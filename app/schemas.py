"""
app/schemas.py
Compatibility wrapper delegating to src.api.schemas (execution_plan.md).
"""
from src.api.schemas import (
    Citation, MathResult, SystemStatus, QueryRequest,
    QueryResponse, HealthResponse, PassageResult
)

__all__ = [
    "Citation", "MathResult", "SystemStatus", "QueryRequest",
    "QueryResponse", "HealthResponse", "PassageResult"
]
