"""
app/verification/ast_calc.py
Safe AST Financial Calculation Engine.

Evaluates arithmetic formula strings using Python's `ast` module without eval().
Only permits: BinOp, UnaryOp, Constant (numeric), and basic arithmetic operators.
Raises ValueError on any identifier, function call, or non-arithmetic node.
"""
import ast
import logging
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

        elif isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            op = node.op

            if isinstance(op, ast.Add):
                return left + right
            elif isinstance(op, ast.Sub):
                return left - right
            elif isinstance(op, ast.Mult):
                return left * right
            elif isinstance(op, ast.Div):
                if right == 0:
                    raise ZeroDivisionError("Division by zero in formula expression.")
                return left / right
            elif isinstance(op, ast.Pow):
                if abs(float(right)) > 100:
                    raise ValueError("Exponent too large (max 100).")
                return left ** right
            elif isinstance(op, ast.Mod):
                if right == 0:
                    raise ZeroDivisionError("Modulo by zero.")
                return left % right
            elif isinstance(op, ast.FloorDiv):
                if right == 0:
                    raise ZeroDivisionError("Floor division by zero.")
                return Decimal(int(left // right))
            else:
                raise ValueError(f"Unsupported binary operator: {type(op).__name__}")

        elif isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand)
            if isinstance(node.op, ast.UAdd):
                return operand
            elif isinstance(node.op, ast.USub):
                return -operand
            else:
                raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

        else:
            raise ValueError(f"Unsupported AST node: {type(node).__name__}")

    def evaluate_safe(self, expression: str, default: float = 0.0) -> float:
        try:
            return float(self.evaluate(expression))
        except Exception as exc:
            logger.warning(f"AST evaluate_safe failed for '{expression}': {exc}")
            return default
