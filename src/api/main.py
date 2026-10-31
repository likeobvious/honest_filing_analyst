"""
src/api/main.py
FastAPI Application Entrypoint — PS5: Honest Filing Analyst.
Complies with RFC-PS5-2026-001 and execution_plan.md.

Implements:
- POST /api/v1/query   : Full deterministic RAG pipeline (Cache -> Parse -> Hybrid RRF -> Rerank/Gate -> Sandbox -> LLM/Breaker -> Verifier -> AST Math)
- GET  /api/v1/health  : System health and readiness telemetry
- GET  /pdfs/{file}    : Static range-request PDF streaming for PDF.js deep-linking (#page=N)
- GET  /data/pdfs/{file}: Mirror PDF path matching execution_plan.md specification
"""
import os
import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import httpx

from src.api.schemas import (
    QueryRequest, QueryResponse, Citation, SystemStatus,
    MathResult, PassageResult, HealthResponse
)
from src.retrieval.query_parser import QueryParser
from src.retrieval.hybrid_rrf import HybridRRFSearchEngine
from src.retrieval.onnx_reranker import ONNXCrossEncoderReranker, ONNXEmbedder
from src.cache.query_cache import QueryCache
from src.llm.circuit_breaker import LiteLLMCircuitBreaker
from src.security.prompt_sandbox import PromptSandbox
from src.security.output_verifier import OutputVerifier
from src.math_engine.ast_calculator import ASTCalculator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Config from environment
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
INDEX_DIR = Path(os.getenv("INDEX_DIR", str(DATA_DIR / "indices")))
MODELS_DIR = Path(os.getenv("MODELS_DIR", "models"))
PDF_DIR = Path(os.getenv("PDF_DIR", str(DATA_DIR / "pdfs")))
UI_DIR = Path(os.getenv("UI_DIR", "src/ui"))
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://127.0.0.1:4000/v1")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-ps5-team-key")

# Global pipeline state container
state: Dict[str, Any] = {
    "query_parser": None,
    "search_engine": None,
    "embedder": None,
    "reranker": None,
    "circuit_breaker": None,
    "query_cache": None,
    "prompt_sandbox": None,
    "output_verifier": None,
    "ast_calculator": None,
    "pdf_dir": PDF_DIR,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for model loading and pipeline initialization."""
    logger.info("=== Starting PS5 Honest Filing Analyst (execution_plan.md) ===")
    t0 = time.perf_counter()

    db_path = str(INDEX_DIR / "chunks.sqlite")

    # 1. Query Parser
    qp = QueryParser(db_path)
    qp.load()
    state["query_parser"] = qp

    # 2. Hybrid RRF Search Engine
    se = HybridRRFSearchEngine(str(INDEX_DIR))
    se.load()
    state["search_engine"] = se

    # 3. ONNX Embedder
    emb = ONNXEmbedder(str(MODELS_DIR / "bge-small-en-v1.5-onnx-int8"))
    emb.load()
    state["embedder"] = emb

    # 4. ONNX Reranker + Abstention Gate
    rr = ONNXCrossEncoderReranker(str(MODELS_DIR / "bge-reranker-base-onnx-int8"))
    rr.load()
    state["reranker"] = rr

    # 5. Circuit Breaker
    cb = LiteLLMCircuitBreaker(LITELLM_BASE_URL, LITELLM_API_KEY)
    state["circuit_breaker"] = cb

    # 6. Two-tier In-Chat Query Cache
    qc = QueryCache(db_path)
    qc.load()
    state["query_cache"] = qc

    # 7. Security & Math modules
    state["prompt_sandbox"] = PromptSandbox()
    state["output_verifier"] = OutputVerifier(db_path)
    state["ast_calculator"] = ASTCalculator()
    state["pdf_dir"] = PDF_DIR

    logger.info(f"=== Pipeline Startup completed in {time.perf_counter() - t0:.2f}s ===")
    yield
    logger.info("=== Shutting down PS5 Honest Filing Analyst ===")


router = APIRouter()


@router.post("/api/v1/query", response_model=QueryResponse)
async def query_endpoint(request: QueryRequest) -> QueryResponse:
    """
    Deterministic RAG query pipeline:
      1. Parse query entities (manifest filter) -> 0-token immediate abstention if not in corpus.
      2. Embed query vector.
      3. Two-tier in-chat query cache lookup -> 0-token return if exact hash or semantic match.
      4. Hybrid BM25 + FAISS search with Table Boost -> Top-12 chunks.
      5. Cross-Encoder reranker -> Top-4 chunks. Two-Tier Epistemic Abstention Gate (logit < -1.50).
      6. Multi-year balanced representation enforcement.
      7. Nonce XML Prompt Sandbox + Instruction override neutralization.
      8. Fast-Fail Circuit Breaker LLM call -> Top-4 fallback if offline (<1.95s).
      9. Substring Quote Verifier & Canary check against physical PDF pages.
      10. Safe AST Arithmetic Engine (Decimal precision).
      11. Store verified answer in query cache.
    """
    qp: Optional[QueryParser] = state["query_parser"]
    se: Optional[HybridRRFSearchEngine] = state["search_engine"]
    emb: Optional[ONNXEmbedder] = state["embedder"]
    rr: Optional[ONNXCrossEncoderReranker] = state["reranker"]
    cb: Optional[LiteLLMCircuitBreaker] = state["circuit_breaker"]
    qc: Optional[QueryCache] = state["query_cache"]
    ps: PromptSandbox = state["prompt_sandbox"]
    ov: OutputVerifier = state["output_verifier"]
    ac: ASTCalculator = state["ast_calculator"]

    wall_start = time.perf_counter()

    # ── Step 1: Parse Query Entities & Manifest Check ──
    parsed = qp.parse(
        request.question,
        ticker_hint=request.ticker,
        fiscal_year_hint=request.fiscal_year,
    ) if qp else None

    # Immediate 0-token Epistemic Abstention: out-of-corpus company or fiscal year
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
                ui_status_banner={"level": "INFO", "message": reason},
            ),
        )

    # ── Step 2: Dense Embedding Vector ──
    query_vector = emb.embed(request.question) if emb else None

    # ── Step 3: Two-Tier In-Chat Query Cache Lookup (0 tokens) ──
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
                cached_data["system_status"]["ui_status_banner"] = {
                    "level": "SUCCESS",
                    "message": f"In-Chat Cache Hit (0 Tokens, {latency_ms:.1f}ms)",
                }
                return QueryResponse(**cached_data)
            except Exception as exc:
                logger.warning(f"Cache deserialization failed: {exc}")

    # ── Step 4: Hybrid BM25 + FAISS Search with Table Boost ──
    doc_id_filter = parsed.matched_doc_ids if (parsed and parsed.matched_doc_ids) else None
    is_quant = parsed.is_quantitative if parsed else False

    candidates = se.search(
        query_vector=query_vector,
        query_text=request.question,
        top_k=12,
        doc_id_filter=doc_id_filter,
        is_quantitative=is_quant,
    ) if se else []

    if not candidates:
        latency_ms = (time.perf_counter() - wall_start) * 1000
        return QueryResponse(
            answer="Not in the documents.",
            is_abstention=True,
            tokens_used=0,
            latency_ms=round(latency_ms, 1),
            system_status=SystemStatus(
                execution_mode="ABSTENTION",
                ui_status_banner={"level": "INFO", "message": "No relevant evidence found in the corpus."},
            ),
        )

    # ── Step 5 & 6: Cross-Encoder Reranker & Two-Tier Abstention Gate ──
    is_multi_yr = parsed.is_multi_year if parsed else False
    target_years = parsed.fiscal_years if (parsed and parsed.fiscal_years) else None

    if rr:
        top_passages, max_logit, should_abstain = rr.rerank(
            query=request.question,
            candidates=candidates,
            top_k=4,
            is_multi_year=is_multi_yr,
            target_years=target_years,
        )
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
                        "message": f"Abstention gate: evidence confidence ({max_logit:.2f}) below threshold (-1.50).",
                    },
                ),
            )
    else:
        top_passages = candidates[:4]
        max_logit = 0.0

    # ── Step 7: Build XML-Sandboxed Prompt ──
    system_prompt, nonce = ps.build_prompt(top_passages, max_reranker_logit=max_logit)

    # ── Step 8: Call LLM via Fast-Fail Circuit Breaker ──
    llm_result = await cb.call_llm(
        system_prompt=system_prompt,
        user_message=f"Question: {request.question}",
        top4_passages=top_passages,
        max_tokens=250,
    )

    elapsed_ms = (time.perf_counter() - wall_start) * 1000

    if llm_result["is_fallback"]:
        logger.info(f"Circuit breaker fallback triggered ({elapsed_ms:.1f}ms).")
        return cb.build_fallback_response(
            top4_passages=top_passages,
            elapsed_ms=elapsed_ms,
            error_reason=llm_result.get("error", "LLM Offline"),
        )

    # ── Step 9: Parse & Verify LLM JSON Response ──
    raw_resp = llm_result["raw_response"]
    tokens_used = 0
    actual_model = llm_result.get("actual_model", "")
    provider_switched = llm_result.get("provider_switched", False)

    try:
        usage = raw_resp.get("usage", {})
        tokens_used = usage.get("total_tokens", 0)
        content_str = raw_resp["choices"][0]["message"]["content"]
        parsed_llm = json.loads(content_str)
    except Exception as exc:
        logger.warning(f"Failed to parse LLM JSON output: {exc}. Falling back to raw passages.")
        return cb.build_fallback_response(
            top4_passages=top_passages,
            elapsed_ms=elapsed_ms,
            error_reason="Invalid JSON from LLM",
        )

    # Canary token check
    if ov.check_canary(str(parsed_llm)):
        logger.warning("Canary token found in LLM output! Activating emergency fallback.")
        return cb.build_fallback_response(
            top4_passages=top_passages,
            elapsed_ms=elapsed_ms,
            error_reason="Adversarial Canary Triggered",
        )

    # Handle explicit LLM abstention
    if parsed_llm.get("status") == "NOT_IN_DOCUMENTS":
        return QueryResponse(
            answer="Not in the documents.",
            is_abstention=True,
            tokens_used=tokens_used,
            latency_ms=round(elapsed_ms, 1),
            system_status=SystemStatus(
                execution_mode="ABSTENTION",
                primary_model="groq/llama-3.3-70b-versatile",
                actual_model_used=actual_model,
                provider_switched=provider_switched,
                ui_status_banner={"level": "INFO", "message": "The filing does not contain sufficient information."},
            ),
        )

    # Verify Citations
    raw_citations = parsed_llm.get("citations", [])
    verified_citations: List[Citation] = []
    for c in raw_citations:
        fn = c.get("doc_filename", "")
        p_num = c.get("pdf_page", 1)
        quote = c.get("verbatim_quote", "")
        if fn and p_num:
            is_valid = ov.verify_citation(fn, p_num, quote)
            if is_valid:
                verified_citations.append(
                    Citation(
                        pdf_filename=fn,
                        pdf_page_number=p_num,
                        snippet=quote,
                        printed_page_label=str(p_num),
                    )
                )

    if not verified_citations and top_passages:
        # Fall back to top passage citation if LLM omitted valid citation
        top_p = top_passages[0]
        verified_citations.append(
            Citation(
                pdf_filename=top_p.pdf_filename,
                pdf_page_number=top_p.pdf_page_number,
                snippet=top_p.clean_markdown_text[:120].strip(),
                printed_page_label=top_p.printed_page_label,
            )
        )

    # ── Step 10: Safe AST Arithmetic ──
    math_result: Optional[MathResult] = None
    calc_block = parsed_llm.get("calculation")
    concise_answer = parsed_llm.get("concise_answer", "").strip()

    if calc_block and isinstance(calc_block, dict):
        formula_expr = calc_block.get("formula_expression", "").strip()
        unit = calc_block.get("result_unit", "")
        decimals = calc_block.get("rounding_decimals", 2)

        if formula_expr:
            computed_val, err = ac.safe_compute(formula_expr, decimals)
            if computed_val is not None:
                math_result = MathResult(
                    formula_expression=formula_expr,
                    computed_result=computed_val,
                    result_unit=unit,
                    rounding_decimals=decimals,
                )
                if "[COMPUTED]" in concise_answer:
                    concise_answer = concise_answer.replace("[COMPUTED]", f"{computed_val}{unit}")

    # Build telemetry & UI banners
    ui_banner = None
    if provider_switched:
        ui_banner = {
            "level": "WARNING",
            "message": f"Provider Failover Active: routed to {actual_model} (Primary: Groq llama-3.3-70b-versatile).",
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
        latency_ms=round(elapsed_ms, 1),
        math_result=math_result,
        system_status=system_status,
    )

    # Store in QueryCache (never cache fallback mode)
    if qc:
        try:
            qc.store(
                request.question,
                final_response.model_dump_json(),
                ticker_filter=parsed.tickers[0] if (parsed and parsed.tickers) else None,
                year_filter=parsed.fiscal_years[0] if (parsed and parsed.fiscal_years) else None,
                query_vector=query_vector,
                is_fallback_mode=False,
            )
        except Exception as exc:
            logger.warning(f"Query cache store failed: {exc}")

    return final_response


@router.get("/api/v1/health", response_model=HealthResponse)
async def health_endpoint() -> HealthResponse:
    """System health check endpoint."""
    se: Optional[HybridRRFSearchEngine] = state["search_engine"]
    cb: Optional[LiteLLMCircuitBreaker] = state["circuit_breaker"]

    total_chunks = len(se._chunks) if se else 0
    total_docs = len(set(c.doc_id for c in se._chunks)) if se else 0
    index_loaded = total_chunks > 0

    litellm_reachable = False
    if cb:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                res = await client.get(f"{cb.litellm_base_url}/models")
                litellm_reachable = res.status_code == 200
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


def _deliver_pdf(filename: str) -> FileResponse:
    pdf_dir: Path = state["pdf_dir"]
    pdf_path = pdf_dir / filename
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail=f"PDF '{filename}' not found.")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
    )


@router.get("/pdfs/{filename:path}")
async def serve_pdf(filename: str) -> FileResponse:
    """Serve PDF files with direct browser #page=N support."""
    return _deliver_pdf(filename)


@router.get("/data/pdfs/{filename:path}")
async def serve_data_pdf(filename: str) -> FileResponse:
    """Mirror path matching execution_plan.md specification (/data/pdfs/{file})."""
    return _deliver_pdf(filename)


def create_app() -> FastAPI:
    app = FastAPI(
        title="PS5: Honest Filing Analyst",
        description="Zero-cost, adversarial-resistant SEC 10-K RAG system.",
        version="3.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(router)

    # Mount UI
    ui_dir_path = Path("src/ui")
    if not ui_dir_path.exists() or not (ui_dir_path / "index.html").exists():
        ui_dir_path = Path("frontend")

    if ui_dir_path.exists() and (ui_dir_path / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(ui_dir_path), html=True), name="ui")

    return app


app = create_app()
