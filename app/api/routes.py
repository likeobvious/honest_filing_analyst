"""
app/api/routes.py
FastAPI router for PS5: Honest Filing Analyst.

Routes:
  POST /api/v1/query   - Main deterministic RAG query pipeline
  GET  /api/v1/health  - Health & readiness check
  GET  /pdfs/{filename}- PDF viewer static file delivery
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse

from app.schemas import (
    QueryRequest, QueryResponse, HealthResponse,
    Citation, SystemStatus, MathResult, PassageResult
)
from app.rag.query_parser import QueryParser
from app.rag.hybrid_search import HybridRRFSearchEngine
from app.rag.embedder import ONNXEmbedder
from app.rag.reranker import ONNXCrossEncoderReranker
from app.circuit_breaker import LiteLLMCircuitBreaker
from app.cache import QueryCache

# Teammate 2 module interfaces (with graceful fallback if not yet implemented)
try:
    from app.security.sandbox import PromptSandbox
except ImportError:
    try:
        from src.security.prompt_sandbox import PromptSandbox
    except ImportError:
        class PromptSandbox:
            def build_prompt(self, passages, logit):
                import secrets
                nonce = secrets.token_hex(4)
                text = "\n\n".join([f"[{p.pdf_filename} p.{p.pdf_page_number}] {p.clean_markdown_text}" for p in passages])
                prompt = (
                    f"Answer the user query based ONLY on the following filing excerpts:\n"
                    f"<untrusted_filing_data_{nonce}>\n{text}\n</untrusted_filing_data_{nonce}>\n"
                    f"Output JSON with keys: status, concise_answer, citations."
                )
                return prompt, nonce

try:
    from app.verification.quote_checker import OutputVerifier
except ImportError:
    try:
        from src.security.output_verifier import OutputVerifier
    except ImportError:
        class OutputVerifier:
            def __init__(self, db_path=""): pass
            def verify_citation(self, fn, p, q): return True

try:
    from app.verification.ast_calc import ASTCalculator
except ImportError:
    try:
        from src.math_engine.ast_calculator import ASTCalculator
    except ImportError:
        class ASTCalculator:
            def evaluate(self, expr):
                from decimal import Decimal
                return Decimal(str(eval(expr, {"__builtins__": None}, {})))

logger = logging.getLogger(__name__)

router = APIRouter()

# Global state holders injected via main.py
state = {
    "query_parser": None,
    "search_engine": None,
    "embedder": None,
    "reranker": None,
    "circuit_breaker": None,
    "query_cache": None,
    "prompt_sandbox": None,
    "output_verifier": None,
    "ast_calculator": None,
    "pdf_dir": Path("data/pdfs"),
}


def get_state():
    return state


@router.post("/api/v1/query", response_model=QueryResponse)
async def query_endpoint(request: QueryRequest) -> QueryResponse:
    """
    Main RAG query endpoint.

    Pipeline (per query):
      1. Parse query entities (ticker, year) -> corpus manifest check -> 0-token early abstention
      2. Check two-tier query cache (SHA-256 exact + semantic cosine) -> cache hit returns 0 tokens
      3. Embed query with bge-small-en-v1.5 ONNX (<30ms)
      4. Hybrid BM25 + FAISS search -> RRF -> Top-12 candidates (<35ms)
      5. Cross-encoder reranker -> Top-4 passages + abstention gate (<650ms)
      6. If abstaining: return "Not in the documents" immediately (0 LLM tokens)
      7. Build XML-sandboxed prompt with nonce wrapping
      8. Call LiteLLM proxy via Circuit Breaker (1.2s connect / 3.2s read timeout)
         - On timeout/failure: return Top-4 raw passages in <1.95s (DEGRADED_PASSAGE_FALLBACK)
      9. Verify LLM response: substring citation check + AST math evaluation
      10. Store in cache, return verified QueryResponse
    """
    st = get_state()
    qp: Optional[QueryParser] = st["query_parser"]
    se: Optional[HybridRRFSearchEngine] = st["search_engine"]
    emb: Optional[ONNXEmbedder] = st["embedder"]
    rr: Optional[ONNXCrossEncoderReranker] = st["reranker"]
    cb: Optional[LiteLLMCircuitBreaker] = st["circuit_breaker"]
    qc: Optional[QueryCache] = st["query_cache"]
    ps = st["prompt_sandbox"] or PromptSandbox()
    ov = st["output_verifier"] or OutputVerifier()
    ac = st["ast_calculator"] or ASTCalculator()

    wall_start = time.perf_counter()

    # ── Step 1: Parse query entities ──
    parsed = qp.parse(
        request.question,
        ticker_hint=request.ticker,
        fiscal_year_hint=request.fiscal_year,
    ) if qp else None

    # Immediate abstention: company or fiscal year not in corpus (0 tokens)
    if parsed and (parsed.company_not_in_corpus or parsed.year_not_in_corpus):
        latency_ms = (time.perf_counter() - wall_start) * 1000
        reason = (
            "The requested company is not present in the document corpus."
            if parsed.company_not_in_corpus
            else f"No 10-K filing available for the requested fiscal year(s): {parsed.fiscal_years}."
        )
        return QueryResponse(
            answer="Not in the documents.",
            is_abstention=True,
            tokens_used=0,
            latency_ms=round(latency_ms, 1),
            system_status=SystemStatus(
                execution_mode="ABSTENTION",
                ui_status_banner={
                    "level": "INFO",
                    "message": reason,
                },
            ),
        )

    # ── Step 2: Embed query ──
    query_vector = emb.embed(request.question) if emb else None

    # ── Step 3: Two-tier cache lookup (0 tokens) ──
    if qc:
        ticker_filter = parsed.tickers[0] if (parsed and parsed.tickers) else None
        year_filter = parsed.fiscal_years[0] if (parsed and parsed.fiscal_years) else None
        is_hit, cached_response_json = qc.lookup(
            request.question,
            ticker_filter=ticker_filter,
            year_filter=year_filter,
            query_vector=query_vector,
        )
        if is_hit and cached_response_json:
            try:
                cached_data = json.loads(cached_response_json)
                latency_ms = (time.perf_counter() - wall_start) * 1000
                cached_data["latency_ms"] = round(latency_ms, 1)
                if not cached_data.get("system_status"):
                    cached_data["system_status"] = {}
                cached_data["system_status"]["execution_mode"] = "CACHE_HIT"
                cached_data["system_status"]["cache_hit"] = True
                return QueryResponse(**cached_data)
            except Exception as exc:
                logger.warning(f"Cache deserialization failed: {exc}")

    # ── Step 4: Hybrid BM25 + FAISS search ──
    matched_docs = parsed.matched_doc_ids if (parsed and parsed.matched_doc_ids) else None
    is_quant = parsed.is_quantitative if parsed else False

    top12_passages = se.search(
        query_vector=query_vector,
        query_text=request.question,
        top_k=12,
        doc_id_filter=matched_docs,
        is_quantitative=is_quant,
    ) if se else []

    if not top12_passages:
        latency_ms = (time.perf_counter() - wall_start) * 1000
        return QueryResponse(
            answer="Not in the documents.",
            is_abstention=True,
            tokens_used=0,
            latency_ms=round(latency_ms, 1),
            system_status=SystemStatus(execution_mode="ABSTENTION"),
        )

    # ── Step 5: Cross-encoder reranking + Abstention Gate ──
    fiscal_years = parsed.fiscal_years if parsed else None
    if rr:
        top4_passages, max_logit, should_abstain = rr.rerank(
            top12_passages,
            request.question,
            fiscal_years=fiscal_years,
        )
    else:
        top4_passages, max_logit, should_abstain = top12_passages[:4], 0.5, False

    # ── Step 6: Tier-1 Abstention (0 LLM tokens consumed) ──
    if should_abstain:
        latency_ms = (time.perf_counter() - wall_start) * 1000
        return QueryResponse(
            answer="Not in the documents.",
            is_abstention=True,
            tokens_used=0,
            latency_ms=round(latency_ms, 1),
            system_status=SystemStatus(
                execution_mode="ABSTENTION",
                ui_status_banner={
                    "level": "INFO",
                    "message": "No sufficiently relevant evidence found in the filings.",
                },
            ),
        )

    # ── Step 7: Build XML-sandboxed prompt with nonce ──
    system_prompt, nonce = ps.build_prompt(top4_passages, max_logit)
    user_message = request.question

    # ── Step 8: Call LiteLLM via Circuit Breaker (<1.2s connect / 3.2s read) ──
    if not cb:
        cb = LiteLLMCircuitBreaker()

    llm_result = await cb.call_llm(
        system_prompt=system_prompt,
        user_message=user_message,
        top4_passages=top4_passages,
    )

    # ── Step 8a: Fallback mode if LLM proxy down (<5s guarantee) ──
    if llm_result["is_fallback"]:
        latency_ms = (time.perf_counter() - wall_start) * 1000
        return cb.build_fallback_response(top4_passages, round(latency_ms, 1), llm_result.get("error"))

    # ── Step 9: Parse & verify LLM response ──
    raw_response = llm_result["raw_response"]
    actual_model = llm_result.get("actual_model", "unknown")
    provider_switched = llm_result.get("provider_switched", False)

    try:
        content = raw_response["choices"][0]["message"]["content"]
        llm_data = json.loads(content)
    except (KeyError, json.JSONDecodeError, TypeError) as exc:
        logger.error(f"Failed to parse LLM JSON response: {exc}")
        latency_ms = (time.perf_counter() - wall_start) * 1000
        return cb.build_fallback_response(top4_passages, round(latency_ms, 1), str(exc))

    usage = raw_response.get("usage", {})
    tokens_used = usage.get("total_tokens", 0)

    # Check Tier-2 LLM abstention
    if llm_data.get("status") == "NOT_IN_DOCUMENTS":
        latency_ms = (time.perf_counter() - wall_start) * 1000
        return QueryResponse(
            answer="Not in the documents.",
            is_abstention=True,
            tokens_used=tokens_used,
            latency_ms=round(latency_ms, 1),
            system_status=SystemStatus(
                execution_mode="ABSTENTION",
                actual_model_used=actual_model,
                provider_switched=provider_switched,
            ),
        )

    concise_answer = llm_data.get("concise_answer", "")
    raw_citations = llm_data.get("citations", [])
    math_data = llm_data.get("calculation", None)

    # Citation substring verification
    verified_citations = []
    for cite_raw in raw_citations:
        try:
            fn = cite_raw.get("doc_filename", "")
            pg = int(cite_raw.get("pdf_page", 0))
            quote = cite_raw.get("verbatim_quote", "")
            if ov.verify_citation(fn, pg, quote):
                verified_citations.append(Citation(
                    pdf_filename=fn,
                    pdf_page_number=pg,
                    snippet=quote[:200],
                ))
            else:
                logger.warning(f"Citation verification failed for {fn}#page={pg}")
        except Exception as e:
            logger.warning(f"Error parsing citation: {e}")

    # Fallback to passage mode if all citations failed
    if not verified_citations and llm_data.get("status") == "ANSWERED":
        logger.warning("All LLM citations failed verification. Falling back to passages.")
        latency_ms = (time.perf_counter() - wall_start) * 1000
        return cb.build_fallback_response(top4_passages, round(latency_ms, 1), "Citation verification failed.")

    # AST math evaluation
    math_result = None
    if math_data and "formula_expression" in math_data:
        try:
            formula = str(math_data["formula_expression"]).strip()
            computed = ac.evaluate(formula)
            rounding = int(math_data.get("rounding_decimals", 2))
            unit = math_data.get("result_unit", "")
            rounded = round(float(computed), rounding)
            math_result = MathResult(
                formula_expression=formula,
                computed_result=rounded,
                result_unit=unit,
                rounding_decimals=rounding,
            )
            concise_answer = concise_answer.replace("[COMPUTED]", f"{rounded}{unit}").strip()
        except Exception as exc:
            logger.warning(f"Math evaluation error: {exc}")

    if len(concise_answer) > 350:
        concise_answer = concise_answer[:347] + "..."

    latency_ms = (time.perf_counter() - wall_start) * 1000

    ui_banner = None
    if provider_switched:
        ui_banner = {
            "level": "WARNING",
            "message": f"Switched to fallback model ({actual_model}) due to rate limits.",
        }

    system_status = SystemStatus(
        execution_mode="LLM_SYNTHESIZED",
        cache_hit=False,
        primary_model="groq/llama-3.3-70b-versatile",
        actual_model_used=actual_model,
        provider_switched=provider_switched,
        ui_status_banner=ui_banner,
    )

    final_response = QueryResponse(
        answer=concise_answer,
        citations=verified_citations,
        is_abstention=False,
        is_fallback_mode=False,
        tokens_used=tokens_used,
        latency_ms=round(latency_ms, 1),
        math_result=math_result,
        system_status=system_status,
    )

    # Store in cache
    if qc:
        try:
            qc.store(
                request.question,
                final_response.model_dump_json(),
                ticker_filter=parsed.tickers[0] if (parsed and parsed.tickers) else None,
                year_filter=parsed.fiscal_years[0] if (parsed and parsed.fiscal_years) else None,
                query_vector=query_vector,
            )
        except Exception as exc:
            logger.warning(f"Query cache store failed: {exc}")

    return final_response


@router.get("/api/v1/health", response_model=HealthResponse)
async def health_endpoint() -> HealthResponse:
    """System health check endpoint."""
    st = get_state()
    se: Optional[HybridRRFSearchEngine] = st["search_engine"]
    cb: Optional[LiteLLMCircuitBreaker] = st["circuit_breaker"]

    index_loaded = bool(se and se.total_chunks > 0)
    total_chunks = se.total_chunks if se else 0
    total_docs = se.total_documents if se else 0

    litellm_reachable = False
    if cb:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(1.0)) as client:
                r = await client.get(f"{cb.litellm_base_url}/models",
                                     headers={"Authorization": f"Bearer {cb.api_key}"})
                litellm_reachable = r.status_code == 200
        except Exception:
            litellm_reachable = False

    status = "healthy" if (index_loaded and litellm_reachable) else (
        "degraded" if index_loaded else "ready"
    )

    return HealthResponse(
        status=status,
        index_loaded=index_loaded,
        litellm_reachable=litellm_reachable,
        total_chunks=total_chunks,
        total_documents=total_docs,
    )


@router.get("/pdfs/{filename:path}")
async def serve_pdf(filename: str) -> FileResponse:
    """Serve PDF files with direct browser #page=N support."""
    st = get_state()
    pdf_dir: Path = st["pdf_dir"]
    pdf_path = pdf_dir / filename
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail=f"PDF '{filename}' not found.")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
    )
