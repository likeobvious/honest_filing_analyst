"""
app/rag/hybrid_search.py
Compatibility wrapper delegating to src.retrieval.hybrid_rrf (execution_plan.md).
"""
from src.retrieval.hybrid_rrf import (
    HybridRRFSearchEngine, finance_tokenize, ChunkMeta
)

__all__ = ["HybridRRFSearchEngine", "finance_tokenize", "ChunkMeta"]
