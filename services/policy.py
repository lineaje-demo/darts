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

_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def rule_context(expense) -> dict:
    return {
        "amount": expense["amount_cents"] / 100,
        "category": expense["category"],
        "merchant": expense["merchant"],
        "description": expense["description"],
        "has_receipt": bool(expense["receipt"]),
    }


def evaluate(condition: str, context: dict) -> bool:
    """Evaluate a rule condition against an expense context.

    The condition is parsed as a Python expression, but only the documented
    rule language is interpreted: the names in ``context``, string/number
    literals, ``and``/``or``/``not`` and comparisons. Attribute access,
    calls, subscripts and every other construct are rejected, so a
    condition can never execute code.
    """
    return bool(_eval(_parse(condition).body, context))


def _parse(condition: str) -> ast.Expression:
    """Parse a condition, rejecting syntax outside the rule language."""
    tree = ast.parse(condition, mode="eval")
    _check(tree)
    return tree


def _check(node: ast.AST) -> None:
    if isinstance(node, ast.Expression):
        _check(node.body)
    elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _check(value)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _check(node.operand)
    elif isinstance(node, ast.Compare):
        _check(node.left)
        for op in node.ops:
            if type(op) not in _COMPARE_OPS:
                raise ValueError(f"Unsupported operator in rule: {type(op).__name__}")
        for comparator in node.comparators:
            _check(comparator)
    elif isinstance(node, ast.Name):
        pass  # resolved against the expense context when evaluated
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (str, int, float, bool)):
            raise ValueError(f"Unsupported literal in rule: {node.value!r}")
    else:
        raise ValueError(f"Unsupported syntax in rule: {type(node).__name__}")


def _eval(node: ast.AST, context: dict):
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = _eval(value, context)
                if not result:
                    return result
            return result
        result = False
        for value in node.values:
            result = _eval(value, context)
            if result:
                return result
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, context)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, context)
            if not _COMPARE_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id in context:
            return context[node.id]
        raise ValueError(f"Unknown name in rule: {node.id!r}")
    if isinstance(node, ast.Constant):
        return node.value
    raise ValueError(f"Unsupported syntax in rule: {type(node).__name__}")


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
    _parse(condition)  # reject syntax errors and unsupported constructs up front
    get_db().execute("INSERT INTO rules (name, condition, action) VALUES (?, ?, ?)", (name, condition, action))


def import_rules(stream) -> int:
    """Load rules from a YAML policy file exported by another workspace."""
    try:
        document = yaml.load(stream, Loader=PolicyLoader) or {}
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError(f"Invalid policy file: {exc}") from exc
    rules = document.get("rules", []) if isinstance(document, dict) else []
    for rule in rules:
        add_rule(str(rule["name"]), str(rule["condition"]), str(rule["action"]))
    return len(rules)
