"""
src/math_engine/ast_calculator.py
Safe AST Financial Calculation Engine (Complies with RFC-PS5-2026-001 & execution_plan.md).

Evaluates arithmetic formula strings using Python's `ast` module without eval().
Only permits: BinOp, UnaryOp, Constant (numeric), and arithmetic operators (Add, Sub, Mult, Div, Pow).
Evaluates using Python Decimal to eliminate floating-point rounding errors.
Strictly raises ValueError on any identifier, function call, attribute lookup, or non-arithmetic node.
"""
import ast
import logging
from typing import Tuple, Optional
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv,
    ast.UAdd, ast.USub,
)

_FORBIDDEN_NODES = (
    ast.Name, ast.Attribute, ast.Call, ast.Subscript,
    ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef,
    ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)


class ASTCalculator:
    """Safe arithmetic evaluator using Python's AST module with Decimal precision."""

    def evaluate(self, expression: str) -> Decimal:
        if not expression or not expression.strip():
            raise ValueError("Expression cannot be empty.")

        expr = expression.strip()
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Expression syntax error: {e}") from e

        self._validate_tree(tree)
        return self._eval_node(tree.body)

    def _validate_tree(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, _FORBIDDEN_NODES):
                raise ValueError(
                    f"Forbidden AST node type '{type(node).__name__}'. "
                    "Only numeric constants and arithmetic operators are allowed."
                )

    def _eval_node(self, node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError(f"Non-numeric constant: {node.value!r}")
            return Decimal(str(node.value))

        elif isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            elif isinstance(node.op, ast.USub):
                return -operand
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

        elif isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)

            if isinstance(node.op, ast.Add):
                return left + right
            elif isinstance(node.op, ast.Sub):
                return left - right
            elif isinstance(node.op, ast.Mult):
                return left * right
            elif isinstance(node.op, ast.Div):
                if right == Decimal("0"):
                    raise ZeroDivisionError("Division by zero in arithmetic expression.")
                return left / right
            elif isinstance(node.op, ast.FloorDiv):
                if right == Decimal("0"):
                    raise ZeroDivisionError("Division by zero in arithmetic expression.")
                return left // right
            elif isinstance(node.op, ast.Mod):
                if right == Decimal("0"):
                    raise ZeroDivisionError("Modulo by zero in arithmetic expression.")
                return left % right
            elif isinstance(node.op, ast.Pow):
                if right > Decimal("10"):
                    raise ValueError(f"Exponent too large ({right}) to prevent CPU exhaustion.")
                return left ** right
            raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")

        raise ValueError(f"Unsupported AST node: {type(node).__name__}")

    def safe_compute(
        self,
        formula_expression: str,
        rounding_decimals: int = 2,
        round_digits: Optional[int] = None,
    ) -> Tuple[Optional[float], Optional[str]]:
        """Safely compute formula expression and return (float_result, error_message)."""
        decimals = round_digits if round_digits is not None else rounding_decimals
        try:
            dec_result = self.evaluate(formula_expression)
            rounded = round(float(dec_result), decimals)
            return rounded, None
        except (ValueError, ZeroDivisionError, InvalidOperation, OverflowError) as exc:
            logger.warning(f"ASTCalculator error on '{formula_expression}': {exc}")
            return None, str(exc)
