"""
app/cache.py
Compatibility wrapper delegating to src.cache.query_cache (execution_plan.md).
"""
from src.cache.query_cache import QueryCache, normalize_query_text, compute_query_hash

__all__ = ["QueryCache", "normalize_query_text", "compute_query_hash"]
