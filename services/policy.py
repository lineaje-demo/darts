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

# The only names a rule condition may reference (see rule_context).
RULE_FIELDS = frozenset({"amount", "category", "merchant", "description", "has_receipt"})

_MAX_CONDITION_LENGTH = 1000

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


def rule_context(expense) -> dict:
    return {
        "amount": expense["amount_cents"] / 100,
        "category": expense["category"],
        "merchant": expense["merchant"],
        "description": expense["description"],
        "has_receipt": bool(expense["receipt"]),
    }


def _validate(node) -> None:
    """Reject anything outside the rule mini-language.

    Conditions may only combine constants, the rule fields, boolean logic
    and comparisons. There are no calls, attribute lookups, subscripts or
    comprehensions, so a condition can never execute code.
    """
    if isinstance(node, ast.Expression):
        _validate(node.body)
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (str, int, float, bool, type(None))):
            raise ValueError(f"Unsupported value in rule condition: {node.value!r}")
    elif isinstance(node, ast.Name):
        if node.id not in RULE_FIELDS:
            raise ValueError(f"Unknown field in rule condition: {node.id!r}")
    elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _validate(value)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _validate(node.operand)
    elif isinstance(node, ast.Compare):
        _validate(node.left)
        for op in node.ops:
            if type(op) not in _COMPARE_OPS:
                raise ValueError("Unsupported operator in rule condition")
        for comparator in node.comparators:
            _validate(comparator)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            _validate(elt)
    else:
        raise ValueError(f"Unsupported syntax in rule condition: {node.__class__.__name__}")


def _parse(condition: str) -> ast.Expression:
    if len(condition) > _MAX_CONDITION_LENGTH:
        raise ValueError("Rule condition is too long")
    try:
        tree = ast.parse(condition, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ValueError(f"Invalid rule condition: {exc}") from None
    _validate(tree)
    return tree


def _eval(node, context):
    """Interpret a validated rule AST against the expense context."""
    if isinstance(node, ast.Expression):
        return _eval(node.body, context)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return context[node.id]
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
    if isinstance(node, ast.UnaryOp):  # only `not` survives validation
        return not _eval(node.operand, context)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, context)
            if not _COMPARE_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.List):
        return [_eval(elt, context) for elt in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(elt, context) for elt in node.elts)
    raise ValueError(f"Unsupported syntax in rule condition: {node.__class__.__name__}")


def evaluate(condition: str, context: dict) -> bool:
    return bool(_eval(_parse(condition), context))


def apply_rules(expense_id: int):
    """Run the policy against a submitted expense; return the rule that matched, if any."""
    db = get_db()
    expense = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    context = rule_context(expense)
    for rule in db.execute("SELECT * FROM rules ORDER BY id").fetchall():
        try:
            matched = evaluate(rule["condition"], context)
        except Exception:
            continue  # a condition that cannot be evaluated never matches
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
    _parse(condition)  # only persist conditions written in the rule mini-language
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
