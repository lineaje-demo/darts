"""Expense policy rules.

Finance writes rules as short expressions over an expense, for example::

    category == "Meals" and amount < 25

The first matching rule decides what happens to a newly submitted expense.
"""

import ast

import yaml

from lib.db import get_db, now

ACTIONS = {"approve": "approved", "flag": "flagged", "reject": "rejected"}

# Rule conditions may only reference these expense fields.
FIELDS = frozenset({"amount", "category", "merchant", "description", "has_receipt"})

_COMPARE_OPS = {
    ast.Eq: lambda left, right: left == right,
    ast.NotEq: lambda left, right: left != right,
    ast.Lt: lambda left, right: left < right,
    ast.LtE: lambda left, right: left <= right,
    ast.Gt: lambda left, right: left > right,
    ast.GtE: lambda left, right: left >= right,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}


def rule_context(expense) -> dict:
    return {
        "amount": expense["amount_cents"] / 100,
        "category": expense["category"],
        "merchant": expense["merchant"],
        "description": expense["description"],
        "has_receipt": bool(expense["receipt"]),
    }


def _check(node) -> None:
    """Reject anything outside the rule language (no calls, attributes, subscripts, ...)."""
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _check(value)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
        _check(node.operand)
    elif isinstance(node, ast.Compare):
        _check(node.left)
        for op in node.ops:
            if type(op) not in _COMPARE_OPS:
                raise ValueError(f"Operator not allowed in rule: {type(op).__name__}")
        for comparator in node.comparators:
            _check(comparator)
    elif isinstance(node, ast.Name):
        if node.id not in FIELDS:
            raise ValueError(f"Unknown name in rule: {node.id!r}")
    elif isinstance(node, ast.Constant):
        pass
    elif isinstance(node, (ast.List, ast.Tuple)):
        for element in node.elts:
            _check(element)
    else:
        raise ValueError(f"Not allowed in rule: {type(node).__name__}")


def _parse(condition: str) -> ast.Expression:
    tree = ast.parse(condition, mode="eval")
    _check(tree.body)
    return tree


def _eval(node, context: dict):
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
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, context)
        if isinstance(node.op, ast.Not):
            return not operand
        return -operand if isinstance(node.op, ast.USub) else +operand
    if isinstance(node, ast.Compare):
        left = _eval(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, context)
            if not _COMPARE_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        return context[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_eval(element, context) for element in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(element, context) for element in node.elts)
    raise ValueError(f"Not allowed in rule: {type(node).__name__}")


def evaluate(condition: str, context: dict) -> bool:
    return bool(_eval(_parse(condition).body, context))


def apply_rules(expense_id: int):
    """Run the policy against a submitted expense; return the rule that matched, if any."""
    db = get_db()
    expense = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    context = rule_context(expense)
    for rule in db.execute("SELECT * FROM rules ORDER BY id").fetchall():
        try:
            matched = evaluate(rule["condition"], context)
        except Exception:
            continue  # a broken stored rule matches nothing; it must not block submissions
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
    try:
        evaluate(condition, _SAMPLE_CONTEXT)  # reject conditions outside the rule language up front
    except (SyntaxError, ValueError):
        raise
    except Exception as exc:  # e.g. RecursionError on pathological nesting
        raise ValueError(f"Invalid rule condition: {exc}") from None
    get_db().execute("INSERT INTO rules (name, condition, action) VALUES (?, ?, ?)", (name, condition, action))


def import_rules(stream) -> int:
    """Load rules from a YAML policy file exported by another workspace."""
    try:
        document = yaml.safe_load(stream) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid policy file: {exc}") from exc
    rules = document.get("rules", []) if isinstance(document, dict) else []
    for rule in rules:
        add_rule(str(rule["name"]), str(rule["condition"]), str(rule["action"]))
    return len(rules)
