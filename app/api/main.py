"""
app/api/main.py
FastAPI Application Entrypoint — PS5: Honest Filing Analyst (Teammate 1 Lead).

Initializes:
- QueryParser (deterministic ticker/year manifest extractor)
- HybridRRFSearchEngine (BM25 + FAISS RRF)
- ONNXEmbedder (bge-small-en-v1.5 INT8 ONNX)
- ONNXCrossEncoderReranker (bge-reranker-base INT8 ONNX)
- LiteLLMCircuitBreaker (1.2s connect / 3.2s read timeout, 60s outage fallback)
- QueryCache (two-tier exact SHA-256 + semantic cosine cache)
"""
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router, state
from app.rag.query_parser import QueryParser
from app.rag.hybrid_search import HybridRRFSearchEngine
from app.rag.embedder import ONNXEmbedder
from app.rag.reranker import ONNXCrossEncoderReranker
from app.circuit_breaker import LiteLLMCircuitBreaker
from app.cache import QueryCache

# Optional Teammate 2 modules
try:
    from app.security.sandbox import PromptSandbox
except ImportError:
    PromptSandbox = None

try:
    from app.verification.quote_checker import OutputVerifier
except ImportError:
    OutputVerifier = None

try:
    from app.verification.ast_calc import ASTCalculator
except ImportError:
    ASTCalculator = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Config from environment
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
INDEX_DIR = DATA_DIR / "indices"
MODELS_DIR = Path(os.getenv("MODELS_DIR", "models"))
PDF_DIR = DATA_DIR / "pdfs"
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", "frontend"))
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm-proxy:4000/v1")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-ps5-team-key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for model loading and resource teardown."""
    logger.info("=== Starting PS5 Honest Filing Analyst (Teammate 1 Lead) ===")
    t0 = time.perf_counter()

    db_path = str(INDEX_DIR / "chunks.sqlite")

    # 1. Query Parser
    qp = QueryParser(db_path)
    qp.load()
    state["query_parser"] = qp

    # 2. Hybrid RRF Search
    se = HybridRRFSearchEngine(str(INDEX_DIR))
    se.load()
    state["search_engine"] = se

    # 3. ONNX Embedder
    emb = ONNXEmbedder(str(MODELS_DIR / "bge-small-en-v1.5-onnx-int8"))
    emb.load()
    state["embedder"] = emb

    # 4. ONNX Reranker
    rr = ONNXCrossEncoderReranker(str(MODELS_DIR / "bge-reranker-base-onnx-int8"))
    rr.load()
    state["reranker"] = rr

    # 5. Circuit Breaker
    cb = LiteLLMCircuitBreaker(LITELLM_BASE_URL, LITELLM_API_KEY)
    state["circuit_breaker"] = cb

    # 6. Two-tier Cache
    qc = QueryCache(db_path)
    qc.load()
    state["query_cache"] = qc

    # 7. Security / Verification / Math modules
    if PromptSandbox:
        state["prompt_sandbox"] = PromptSandbox()
    if OutputVerifier:
        state["output_verifier"] = OutputVerifier(db_path)
    if ASTCalculator:
        state["ast_calculator"] = ASTCalculator()

    state["pdf_dir"] = PDF_DIR

    logger.info(f"=== Startup completed in {time.perf_counter() - t0:.2f}s ===")
    yield
    logger.info("=== Shutting down PS5 Honest Filing Analyst ===")


def create_app() -> FastAPI:
    app = FastAPI(
        title="PS5: Honest Filing Analyst",
        description="Zero-cost, adversarial-resistant SEC 10-K RAG system.",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(router)

    if FRONTEND_DIR.exists() and (FRONTEND_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()
