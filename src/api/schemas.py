"""
src/api/schemas.py
Shared Pydantic data models for PS5: Honest Filing Analyst.
Single source of truth for API request/response contracts across Teammate 1, 2, and 3.
Complies with execution_plan.md and RFC-PS5-2026-001.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class Citation(BaseModel):
    """A single evidence citation linking an answer claim to its source page."""
    pdf_filename: str = Field(..., description="Filename of the source 10-K PDF.")
    pdf_page_number: int = Field(..., ge=1, description="1-indexed physical PDF page number (for #page=N browser links).")
    snippet: str = Field(..., description="Short verbatim quote extracted from the cited page (<=25 words).")
    printed_page_label: Optional[str] = Field(None, description="Printed page label (may differ from pdf_page_number).")


class MathResult(BaseModel):
    """Result from AST-based safe arithmetic evaluation."""
    formula_expression: str = Field(..., description="Raw arithmetic formula extracted by LLM (e.g., '(255887 - 242901) / 242901 * 100').")
    computed_result: float = Field(..., description="Deterministically computed result using Python Decimal.")
    result_unit: str = Field(default="", description="Unit of result (e.g., '%', '$M', 'x').")
    rounding_decimals: int = Field(default=2)


class SystemStatus(BaseModel):
    """Telemetry about execution mode, provider failovers, and cache hits."""
    execution_mode: str = Field(
        ...,
        description="One of: LLM_SYNTHESIZED, DEGRADED_PASSAGE_FALLBACK, ABSTENTION, CACHE_HIT."
    )
    cache_hit: bool = False
    primary_model: str = Field(default="groq/llama-3.3-70b-versatile")
    actual_model_used: Optional[str] = None
    provider_switched: bool = False
    ui_status_banner: Optional[Dict[str, str]] = None


class QueryRequest(BaseModel):
    """Incoming query from the analyst's search box."""
    question: str = Field(..., min_length=3, max_length=500, description="The analyst's financial question.")
    ticker: Optional[str] = Field(None, description="Optional company ticker hint (e.g., 'AMZN', 'AAPL').")
    fiscal_year: Optional[int] = Field(None, ge=2000, le=2030, description="Optional fiscal year hint.")


class QueryResponse(BaseModel):
    """
    Full response returned by /api/v1/query.
    is_abstention=True means answer is 'Not in the documents' (0 LLM tokens consumed).
    is_fallback_mode=True means LLM was down; answer contains top-4 raw passages.
    """
    answer: str = Field(..., description="Concise answer (<= 350 chars) or 'Not in the documents'.")
    citations: List[Citation] = Field(default_factory=list)
    is_abstention: bool = Field(default=False, description="True when no evidence meets confidence threshold.")
    is_fallback_mode: bool = Field(default=False, description="True when LLM proxy is offline and passages are shown raw.")
    tokens_used: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    math_result: Optional[MathResult] = None
    system_status: Optional[SystemStatus] = None


class HealthResponse(BaseModel):
    """Response from /api/v1/health endpoint."""
    status: str
    index_loaded: bool
    litellm_reachable: bool
    total_chunks: int
    total_documents: int


class PassageResult(BaseModel):
    """A single retrieved and reranked passage (used in fallback mode and internal pipeline)."""
    chunk_id: str
    doc_id: str
    pdf_filename: str
    pdf_page_number: int
    printed_page_label: Optional[str] = None
    section_item: Optional[str] = None
    clean_markdown_text: str
    reranker_score: float = 0.0
    bm25_rank: Optional[int] = None
    faiss_rank: Optional[int] = None
    rrf_score: Optional[float] = None
    has_financial_table: bool = False
