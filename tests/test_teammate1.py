"""
tests/test_teammate1.py
Standard library unittest suite verifying all Teammate 1 modules:
- app/schemas.py
- app/rag/query_parser.py
- app/rag/hybrid_search.py
- app/rag/reranker.py
- app/circuit_breaker.py
"""
import unittest
from app.schemas import Citation, QueryRequest, QueryResponse, PassageResult, SystemStatus
from app.rag.query_parser import QueryParser, ParsedQuery
from app.rag.hybrid_search import finance_tokenize, HybridRRFSearchEngine
from app.rag.reranker import ONNXCrossEncoderReranker, ABSTENTION_LOGIT_THRESHOLD
from app.circuit_breaker import LiteLLMCircuitBreaker, CircuitState


class TestTeammate1(unittest.TestCase):

    # ── 1. Schemas Tests ──────────────────────────────────────────────────────

    def test_citation_schema(self):
        c = Citation(
            pdf_filename="AMZN_2023_10K.pdf",
            pdf_page_number=58,
            snippet="Net product sales were $255,887 million.",
        )
        self.assertEqual(c.pdf_filename, "AMZN_2023_10K.pdf")
        self.assertEqual(c.pdf_page_number, 58)
        self.assertIn("255,887", c.snippet)
        self.assertIsNone(c.printed_page_label)

    def test_query_response_abstention(self):
        resp = QueryResponse(
            answer="Not in the documents.",
            is_abstention=True,
            tokens_used=0,
            latency_ms=12.5,
        )
        self.assertTrue(resp.is_abstention)
        self.assertEqual(resp.tokens_used, 0)
        self.assertEqual(resp.answer, "Not in the documents.")

    def test_query_response_fallback_mode(self):
        resp = QueryResponse(
            answer="LLM Service Offline...",
            is_fallback_mode=True,
            tokens_used=0,
            latency_ms=750.0,
        )
        self.assertTrue(resp.is_fallback_mode)
        self.assertEqual(resp.tokens_used, 0)

    # ── 2. Query Parser Tests ─────────────────────────────────────────────────

    def test_query_parser_fiscal_year_extraction(self):
        qp = QueryParser(db_path="nonexistent.sqlite")
        qp.load()

        parsed = qp.parse("What was Amazon's operating income for FY2023?")
        self.assertIn(2023, parsed.fiscal_years)
        self.assertFalse(parsed.is_multi_year)

        parsed_multi = qp.parse("Compare operating income between FY2022 and FY2023")
        self.assertIn(2022, parsed_multi.fiscal_years)
        self.assertIn(2023, parsed_multi.fiscal_years)
        self.assertTrue(parsed_multi.is_multi_year)
        self.assertTrue(parsed_multi.is_quantitative)

    def test_query_parser_quantitative_keywords(self):
        qp = QueryParser(db_path="nonexistent.sqlite")
        qp.load()

        parsed = qp.parse("What was the gross margin change year-over-year?")
        self.assertTrue(parsed.is_quantitative)

    # ── 3. Hybrid Search & Tokenizer Tests ────────────────────────────────────

    def test_finance_tokenize(self):
        text = "In FY2023, Amazon reported non-GAAP operating margin of 6.4% and $255,887M in sales."
        tokens = finance_tokenize(text)
        self.assertTrue("fy2023" in tokens or "2023" in tokens)
        self.assertIn("non-gaap", tokens)
        self.assertIn("operating", tokens)
        self.assertIn("margin", tokens)

    def test_hybrid_rrf_scoring_logic(self):
        engine = HybridRRFSearchEngine(index_dir="empty_dir")
        self.assertEqual(engine.RRF_K, 60)
        self.assertEqual(engine.TABLE_BOOST, 1.15)

    # ── 4. Reranker & Abstention Gate Tests ────────────────────────────────────

    def test_abstention_gate_threshold(self):
        self.assertEqual(ABSTENTION_LOGIT_THRESHOLD, -1.50)

        reranker = ONNXCrossEncoderReranker(model_dir="models/nonexistent")
        reranker.load()

        passages, max_logit, should_abstain = reranker.rerank([], "some query")
        self.assertTrue(should_abstain)
        self.assertLess(max_logit, ABSTENTION_LOGIT_THRESHOLD)

    def test_multi_year_coverage(self):
        reranker = ONNXCrossEncoderReranker(model_dir="models/nonexistent")
        reranker.load()

        passages = [
            PassageResult(
                chunk_id=f"c_{i}",
                doc_id="d1",
                pdf_filename="AMZN_2022_10K.pdf" if i % 2 == 0 else "AMZN_2023_10K.pdf",
                pdf_page_number=i + 1,
                clean_markdown_text=f"Filing info for year {2022 if i % 2 == 0 else 2023}",
                reranker_score=float(10 - i),
            )
            for i in range(8)
        ]

        selected = reranker._apply_multi_year_coverage(passages, fiscal_years=[2022, 2023])
        self.assertLessEqual(len(selected), 4)
        years_present = {
            2022 if "2022" in p.pdf_filename else 2023 for p in selected
        }
        self.assertIn(2022, years_present)
        self.assertIn(2023, years_present)

    # ── 5. Circuit Breaker Tests ──────────────────────────────────────────────

    def test_circuit_breaker_initial_state(self):
        cb = LiteLLMCircuitBreaker(litellm_base_url="http://localhost:4000/v1", api_key="sk-test")
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.stats.failure_count, 0)

    def test_circuit_breaker_build_fallback_response(self):
        cb = LiteLLMCircuitBreaker()
        passages = [
            PassageResult(
                chunk_id="c1",
                doc_id="d1",
                pdf_filename="AMZN_2023_10K.pdf",
                pdf_page_number=58,
                clean_markdown_text="Net product sales were $255,887 million.",
                reranker_score=2.5,
            )
        ]
        resp = cb.build_fallback_response(passages, latency_ms=850.0)
        self.assertTrue(resp.is_fallback_mode)
        self.assertEqual(resp.tokens_used, 0)
        self.assertEqual(len(resp.citations), 1)
        self.assertEqual(resp.citations[0].pdf_page_number, 58)
        self.assertEqual(resp.citations[0].pdf_filename, "AMZN_2023_10K.pdf")
        self.assertIn("LLM Service Offline", resp.answer)


if __name__ == "__main__":
    unittest.main()
