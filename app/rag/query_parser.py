"""
app/rag/query_parser.py
Compatibility wrapper delegating to src.retrieval.query_parser (execution_plan.md).
"""
from src.retrieval.query_parser import (
    QueryParser, ParsedQuery, FISCAL_YEAR_PATTERN, BARE_YEAR_PATTERN,
    COMMON_FINANCIAL_WORDS, KNOWN_EXTERNAL_COMPANIES, QUANTITATIVE_KEYWORDS
)

__all__ = [
    "QueryParser", "ParsedQuery", "FISCAL_YEAR_PATTERN", "BARE_YEAR_PATTERN",
    "COMMON_FINANCIAL_WORDS", "KNOWN_EXTERNAL_COMPANIES", "QUANTITATIVE_KEYWORDS"
]
