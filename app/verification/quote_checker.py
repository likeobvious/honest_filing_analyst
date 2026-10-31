"""
app/verification/quote_checker.py
Compatibility wrapper delegating to src.security.output_verifier (execution_plan.md).
"""
from src.security.output_verifier import OutputVerifier, normalize_for_comparison, CANARY_TOKENS

__all__ = ["OutputVerifier", "normalize_for_comparison", "CANARY_TOKENS"]
