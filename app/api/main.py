"""
app/api/main.py
Compatibility wrapper delegating to src.api.main (execution_plan.md).
"""
from src.api.main import app, create_app, state, router

__all__ = ["app", "create_app", "state", "router"]
