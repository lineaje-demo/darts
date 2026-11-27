"""Expense policy rules.

Finance writes rules as short expressions over an expense, for example::

    category == "Meals" and amount < 25

The first matching rule decides what happens to a newly submitted expense.
"""

import ast

import yaml

from lib.db import get_db, now

ACTIONS = {"approve": "approved", "flag": "flagged", "reject": "rejected"}

# Rule conditions may only combine expense fields with literals using boolean
# logic and comparisons. Anything else (calls, attribute access, subscripts,
# comprehensions, ...) could reach the Python object graph and is rejected.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not,
    ast.Compare,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Name, ast.Load,
    ast.Constant,
)


def _parse(condition: str) -> ast.Expression:
    """Parse a rule condition, rejecting anything outside the rule language."""
    tree = ast.parse(condition, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"Rule conditions may not use {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (str, int, float, bool)):
            raise ValueError("Rule conditions may only use string, number and boolean literals")
    return tree


def _eval_node(node, context: dict):
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
    if isinstance(node, ast.UnaryOp):  # Not is the only permitted unary operator
        return not _eval_node(node.operand, context)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context)
            if isinstance(op, ast.Eq):
                matched = left == right
            elif isinstance(op, ast.NotEq):
                matched = left != right
            elif isinstance(op, ast.Lt):
                matched = left < right
            elif isinstance(op, ast.LtE):
                matched = left <= right
            elif isinstance(op, ast.Gt):
                matched = left > right
            elif isinstance(op, ast.GtE):
                matched = left >= right
            elif isinstance(op, ast.In):
                matched = left in right
            elif isinstance(op, ast.NotIn):
                matched = left not in right
            if not matched:
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id not in context:
            raise ValueError(f"Unknown name {node.id!r} in rule condition")
        return context[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    raise ValueError(f"Rule conditions may not use {type(node).__name__}")


def rule_context(expense) -> dict:
    return {
        "amount": expense["amount_cents"] / 100,
        "category": expense["category"],
        "merchant": expense["merchant"],
        "description": expense["description"],
        "has_receipt": bool(expense["receipt"]),
    }


def evaluate(condition: str, context: dict) -> bool:
    tree = _parse(condition)
    return bool(_eval_node(tree.body, context))


def apply_rules(expense_id: int):
    """Run the policy against a submitted expense; return the rule that matched, if any."""
    db = get_db()
    expense = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    context = rule_context(expense)
    for rule in db.execute("SELECT * FROM rules ORDER BY id").fetchall():
        try:
            matched = evaluate(rule["condition"], context)
        except Exception:
            continue  # a broken stored rule must not block expense submission
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
    _parse(condition)  # reject invalid or unsafe conditions up front
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
