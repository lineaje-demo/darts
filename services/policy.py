"""Expense policy rules.

Finance writes rules as short expressions over an expense, for example::

    category == "Meals" and amount < 25

The first matching rule decides what happens to a newly submitted expense.
"""

import ast

import yaml

from lib.db import get_db, now

ACTIONS = {"approve": "approved", "flag": "flagged", "reject": "rejected"}

# Rule conditions are interpreted, never eval()'d. Only this fixed set of
# expression nodes is allowed: literals, the names in the expense context,
# boolean operators and comparisons. Attribute access, calls, subscripts,
# comprehensions and every other node type are rejected, so a condition can
# inspect the expense but can never execute code.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.UAdd, ast.USub,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Name, ast.Load,
    ast.Constant, ast.List, ast.Tuple,
)

_MAX_DEPTH = 64


def rule_context(expense) -> dict:
    return {
        "amount": expense["amount_cents"] / 100,
        "category": expense["category"],
        "merchant": expense["merchant"],
        "description": expense["description"],
        "has_receipt": bool(expense["receipt"]),
    }


def _eval_node(node, context: dict, depth: int):
    if depth > _MAX_DEPTH:
        raise ValueError("Rule expression is too complex")
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, context, depth + 1)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in context:
            return context[node.id]
        raise ValueError(f"Unknown name {node.id!r} in rule condition")
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_node(elt, context, depth + 1) for elt in node.elts]
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = _eval_node(value, context, depth + 1)
                if not result:
                    break
            return result
        result = False
        for value in node.values:
            result = _eval_node(value, context, depth + 1)
            if result:
                break
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, context, depth + 1)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context, depth + 1)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context, depth + 1)
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.In):
                ok = left in right
            else:  # ast.NotIn
                ok = left not in right
            if not ok:
                return False
            left = right
        return True
    raise ValueError(f"Unsupported expression in rule condition: {type(node).__name__}")


def _validate_condition(condition: str) -> None:
    """Reject any condition outside the safe rule-language subset."""
    try:
        tree = ast.parse(condition, mode="eval")
    except RecursionError as exc:
        raise ValueError("Rule expression is too complex") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"Unsupported expression in rule condition: {type(node).__name__}")


def evaluate(condition: str, context: dict) -> bool:
    try:
        tree = ast.parse(condition, mode="eval")
        result = _eval_node(tree, context, 0)
    except RecursionError as exc:
        raise ValueError("Rule expression is too complex") from exc
    return bool(result)


def apply_rules(expense_id: int):
    """Run the policy against a submitted expense; return the rule that matched, if any."""
    db = get_db()
    expense = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    context = rule_context(expense)
    for rule in db.execute("SELECT * FROM rules ORDER BY id").fetchall():
        try:
            matched = evaluate(rule["condition"], context)
        except Exception:
            continue  # a rule that cannot be evaluated safely never matches
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
    _validate_condition(condition)  # reject anything outside the safe rule language up front
    get_db().execute("INSERT INTO rules (name, condition, action) VALUES (?, ?, ?)", (name, condition, action))


def import_rules(stream) -> int:
    """Load rules from a YAML policy file exported by another workspace."""
    document = yaml.safe_load(stream) or {}
    rules = document.get("rules", []) if isinstance(document, dict) else []
    for rule in rules:
        add_rule(str(rule["name"]), str(rule["condition"]), str(rule["action"]))
    return len(rules)
