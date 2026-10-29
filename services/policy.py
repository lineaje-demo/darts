"""Expense policy rules.

Finance writes rules as short expressions over an expense, for example::

    category == "Meals" and amount < 25

The first matching rule decides what happens to a newly submitted expense.
"""

import ast

import yaml

from lib.db import get_db, now

ACTIONS = {"approve": "approved", "flag": "flagged", "reject": "rejected"}

# Conditions are untrusted input, so they are never handed to eval().  They are
# parsed with ast and interpreted against a small allowlist of expression
# nodes: the expense fields, literals, boolean operators and comparisons.  That
# is everything the documented rule language needs, and it makes runaway
# computations such as 9**9**9**9 (or sandbox-breakout expressions using
# attribute access and calls) unrepresentable.
_MAX_CONDITION_LENGTH = 1000
_MAX_CONDITION_NODES = 100

_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Name, ast.Load,
    ast.Constant,
    ast.List, ast.Tuple,
)

_ALLOWED_CONSTANTS = (str, int, float, bool, type(None))

_COMPARE = {
    ast.Eq: lambda left, right: left == right,
    ast.NotEq: lambda left, right: left != right,
    ast.Lt: lambda left, right: left < right,
    ast.LtE: lambda left, right: left <= right,
    ast.Gt: lambda left, right: left > right,
    ast.GtE: lambda left, right: left >= right,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}


def _parse_condition(condition: str) -> ast.Expression:
    """Parse a policy condition, rejecting anything outside the rule language."""
    if not isinstance(condition, str) or not condition.strip():
        raise ValueError("Enter a condition")
    if len(condition) > _MAX_CONDITION_LENGTH:
        raise ValueError("Condition is too long")
    try:
        tree = ast.parse(condition, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise ValueError(f"Invalid condition: {exc}") from None
    nodes = list(ast.walk(tree))  # ast.walk is iterative: safe on deep trees
    if len(nodes) > _MAX_CONDITION_NODES:
        raise ValueError("Condition is too complex")
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"Condition may not use {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, _ALLOWED_CONSTANTS):
            raise ValueError(f"Condition may not use the literal {node.value!r}")
    return tree


def _eval_node(node, context: dict):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, context)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        try:
            return context[node.id]
        except KeyError:
            raise ValueError(f"Unknown field {node.id!r} in condition") from None
    if isinstance(node, ast.BoolOp):
        combine = all if isinstance(node.op, ast.And) else any
        return combine(_eval_node(value, context) for value in node.values)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, context)
        if isinstance(node.op, ast.Not):
            return not operand
        return -operand if isinstance(node.op, ast.USub) else +operand
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context)
            if not _COMPARE[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_node(element, context) for element in node.elts]
    raise ValueError(f"Condition may not use {type(node).__name__}")


def rule_context(expense) -> dict:
    return {
        "amount": expense["amount_cents"] / 100,
        "category": expense["category"],
        "merchant": expense["merchant"],
        "description": expense["description"],
        "has_receipt": bool(expense["receipt"]),
    }


def evaluate(condition: str, context: dict) -> bool:
    return bool(_eval_node(_parse_condition(condition), context))


def apply_rules(expense_id: int):
    """Run the policy against a submitted expense; return the rule that matched, if any."""
    db = get_db()
    expense = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    context = rule_context(expense)
    for rule in db.execute("SELECT * FROM rules ORDER BY id").fetchall():
        if evaluate(rule["condition"], context):
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
    _parse_condition(condition)  # reject anything outside the rule language up front
    get_db().execute("INSERT INTO rules (name, condition, action) VALUES (?, ?, ?)", (name, condition, action))


def import_rules(stream) -> int:
    """Load rules from a YAML policy file exported by another workspace."""
    try:
        document = yaml.safe_load(stream) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid policy file: {exc}") from None
    rules = document.get("rules", []) if isinstance(document, dict) else []
    for rule in rules:
        add_rule(str(rule["name"]), str(rule["condition"]), str(rule["action"]))
    return len(rules)
