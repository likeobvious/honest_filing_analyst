"""
app/circuit_breaker.py
Compatibility wrapper delegating to src.llm.circuit_breaker (execution_plan.md).
"""
from src.llm.circuit_breaker import (
    LiteLLMCircuitBreaker, CircuitState, CircuitBreakerStats,
    CB_OPEN_DURATION_SECS, CB_CONNECT_TIMEOUT, CB_READ_TIMEOUT,
    PRIMARY_MODEL_ID, LLM_MAX_TOKENS, LLM_TEMPERATURE
)

__all__ = [
    "LiteLLMCircuitBreaker", "CircuitState", "CircuitBreakerStats",
    "CB_OPEN_DURATION_SECS", "CB_CONNECT_TIMEOUT", "CB_READ_TIMEOUT",
    "PRIMARY_MODEL_ID", "LLM_MAX_TOKENS", "LLM_TEMPERATURE"
]
