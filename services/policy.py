"""Expense policy rules.

Finance writes rules as short expressions over an expense, for example::

    category == "Meals" and amount < 25

The first matching rule decides what happens to a newly submitted expense.
"""

import ast
import operator

import yaml

from lib.db import get_db, now

ACTIONS = {"approve": "approved", "flag": "flagged", "reject": "rejected"}


class RuleError(ValueError):
    """Raised when a rule uses syntax outside the policy expression language."""


_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}

# Node types the policy language is allowed to use. Anything else (attribute
# access, calls, subscripts, comprehensions, ...) is rejected before the rule
# is ever evaluated, so a rule can only read the values handed to it in the
# context dict — never the Python object graph.
_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple,
)

_ALLOWED_LITERALS = (str, int, float, bool, type(None))


def _parse_rule(condition: str) -> ast.Expression:
    """Parse a rule condition, rejecting anything outside the policy language."""
    tree = ast.parse(condition, mode="eval")  # raises SyntaxError, as before
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise RuleError(f"Unsupported syntax in policy rule: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise RuleError(f"Unknown name {node.id!r}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, _ALLOWED_LITERALS):
            raise RuleError(f"Unsupported literal in policy rule: {node.value!r}")
    return tree


def _eval_node(node, context: dict):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, context)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = _eval_node(value, context)
                if not result:
                    break
            return result
        result = False
        for value in node.values:
            result = _eval_node(value, context)
            if result:
                break
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, context)
        if isinstance(node.op, ast.Not):
            return not operand
        if not isinstance(operand, (int, float)):
            raise RuleError("Unary +/- is only allowed on numbers")
        return -operand if isinstance(node.op, ast.USub) else +operand
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context)
            if not _COMPARE_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, context)
        right = _eval_node(node.right, context)
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            raise RuleError("Arithmetic is only allowed on numbers")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.Name):
        if node.id not in context:
            raise RuleError(f"Unknown name {node.id!r}")
        return context[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        elements = [_eval_node(element, context) for element in node.elts]
        return elements if isinstance(node, ast.List) else tuple(elements)
    raise RuleError(f"Unsupported syntax in policy rule: {type(node).__name__}")


def rule_context(expense) -> dict:
    return {
        "amount": expense["amount_cents"] / 100,
        "category": expense["category"],
        "merchant": expense["merchant"],
        "description": expense["description"],
        "has_receipt": bool(expense["receipt"]),
    }


def evaluate(condition: str, context: dict) -> bool:
    """Return True when the expense described by ``context`` matches ``condition``.

    Conditions are a small, side-effect-free expression language: the names in
    ``context``, literals, comparisons (including ``in``), boolean logic and
    arithmetic on numbers. Anything else — attribute access, calls, indexing —
    is not part of the language, and such a rule simply does not match.
    """
    try:
        return bool(_eval_node(_parse_rule(condition), context))
    except (SyntaxError, RuleError):
        return False


def apply_rules(expense_id: int):
    """Run the policy against a submitted expense; return the rule that matched, if any."""
    db = get_db()
    expense = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    context = rule_context(expense)
    for rule in db.execute("SELECT * FROM rules ORDER BY id").fetchall():
        try:
            matched = evaluate(rule["condition"], context)
        except (SyntaxError, ValueError):
            continue  # ignore stored rules outside the supported language
        if matched:
            db.execute("UPDATE expenses SET status = ? WHERE id = ?", (ACTIONS[rule["action"]], expense_id))
            db.execute(
                "INSERT INTO comments (expense_id, user_id, body, created_at) VALUES (?, NULL, ?, ?)",
                (expense_id, f"Policy rule **{rule['name']}** matched: {rule['action']}.", now()),
            )
            db.commit()
            return rule
    return None


def add_rule(name: str, condition: str, action: str) -> None:
    if action not in ACTIONS:
        raise ValueError(f"Unknown action {action!r}")
    _parse_rule(condition)  # reject unsupported syntax up front
    get_db().execute("INSERT INTO rules (name, condition, action) VALUES (?, ?, ?)", (name, condition, action))


def import_rules(stream) -> int:
    """Load rules from a YAML policy file exported by another workspace."""
    document = yaml.safe_load(stream) or {}
    rules = document.get("rules", []) if isinstance(document, dict) else []
    for rule in rules:
        add_rule(str(rule["name"]), str(rule["condition"]), str(rule["action"]))
    return len(rules)
