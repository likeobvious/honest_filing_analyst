"""
app/security/sandbox.py
Compatibility wrapper delegating to src.security.prompt_sandbox (execution_plan.md).
"""
from src.security.prompt_sandbox import PromptSandbox, SYSTEM_PROMPT_TEMPLATE, INJECTION_PATTERNS

__all__ = ["PromptSandbox", "SYSTEM_PROMPT_TEMPLATE", "INJECTION_PATTERNS"]
