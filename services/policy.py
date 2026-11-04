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

# Names a rule condition may reference -- the keys rule_context() provides.
_RULE_FIELDS = frozenset({"amount", "category", "merchant", "description", "has_receipt"})

# A rule condition is a small expression language: boolean logic and
# comparisons of expense fields against literals. Calls, attribute access,
# subscripts, comprehensions and arithmetic are all rejected, so evaluating a
# condition can never do more work than its own length.
_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}
_LITERAL_TYPES = (str, int, float, bool, type(None))
_MAX_DEPTH = 100


def rule_context(expense) -> dict:
    return {
        "amount": expense["amount_cents"] / 100,
        "category": expense["category"],
        "merchant": expense["merchant"],
        "description": expense["description"],
        "has_receipt": bool(expense["receipt"]),
    }


def _parse(condition: str) -> ast.Expression:
    try:
        return ast.parse(condition, "<rule>", "eval")
    except RecursionError:
        raise ValueError("Rule is too deeply nested") from None


def _validate(node, fields, depth=0) -> None:
    """Reject any syntax outside the rule language; ``fields`` is the set of
    names the condition may reference."""
    if depth > _MAX_DEPTH:
        raise ValueError("Rule is too deeply nested")
    if isinstance(node, ast.Expression):
        _validate(node.body, fields, depth + 1)
    elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _validate(value, fields, depth + 1)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.UAdd, ast.USub)):
        _validate(node.operand, fields, depth + 1)
    elif isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _COMPARE_OPS:
                raise ValueError(f"Unsupported operator in rule: {type(op).__name__}")
        _validate(node.left, fields, depth + 1)
        for comparator in node.comparators:
            _validate(comparator, fields, depth + 1)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            _validate(element, fields, depth + 1)
    elif isinstance(node, ast.Name):
        if node.id not in fields:
            raise ValueError(f"Unknown field in rule: {node.id!r}")
    elif isinstance(node, ast.Constant) and isinstance(node.value, _LITERAL_TYPES):
        pass
    else:
        raise ValueError(f"Unsupported syntax in rule: {type(node).__name__}")


def _eval(node, context):
    """Evaluate a condition already accepted by _validate."""
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = _eval(value, context)
                if not result:
                    break
            return result
        result = False
        for value in node.values:
            result = _eval(value, context)
            if result:
                break
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
    if isinstance(node, ast.Tuple):
        return tuple(_eval(element, context) for element in node.elts)
    if isinstance(node, ast.List):
        return [_eval(element, context) for element in node.elts]
    if isinstance(node, ast.Name):
        return context[node.id]
    return node.value  # ast.Constant


def evaluate(condition: str, context: dict) -> bool:
    tree = _parse(condition)
    _validate(tree, context.keys())
    return bool(_eval(tree.body, context))


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
    _validate(_parse(condition), _RULE_FIELDS)  # reject unusable rules up front
    get_db().execute("INSERT INTO rules (name, condition, action) VALUES (?, ?, ?)", (name, condition, action))


def import_rules(stream) -> int:
    """Load rules from a YAML policy file exported by another workspace."""
    try:
        document = yaml.safe_load(stream) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid policy file: {exc}") from None
    rules = document.get("rules", []) if isinstance(document, dict) else []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError(f"Rule entry must be a mapping, got {type(rule).__name__}")
        name, condition, action = rule.get("name"), rule.get("condition"), rule.get("action")
        if not all(isinstance(field, str) for field in (name, condition, action)):
            raise ValueError("Rule name, condition, and action must be plain strings")
        add_rule(name, condition, action)
    return len(rules)
