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


def rule_context(expense) -> dict:
    return {
        "amount": expense["amount_cents"] / 100,
        "category": expense["category"],
        "merchant": expense["merchant"],
        "description": expense["description"],
        "has_receipt": bool(expense["receipt"]),
    }


# Conditions are data, not code: boolean combinations of comparisons between
# the context fields above and literal values.  No calls, attribute access,
# subscripts or arithmetic ever reach the evaluator.
_FIELDS = frozenset({"amount", "category", "merchant", "description", "has_receipt"})

_COMPARATORS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}

# Bounds keep the validator and interpreter immune to pathological inputs on
# top of the syntax restrictions (deep nesting, huge literals, ...).
_MAX_CONDITION_LENGTH = 4096
_MAX_CONDITION_NODES = 200

_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Tuple,
    ast.List,
)


def _parse_condition(condition: str) -> ast.Expression:
    """Parse and statically validate a rule condition.

    Raises ValueError for anything outside the safe condition language, so
    stored rules can never execute code or blow up the evaluator.
    """
    if len(condition) > _MAX_CONDITION_LENGTH:
        raise ValueError("Rule condition is too long")
    tree = ast.parse(condition, "<rule>", "eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_CONDITION_NODES:
        raise ValueError("Rule condition is too complex")
    for node in nodes:
        if not isinstance(node, (*_ALLOWED_NODES, *_COMPARATORS)):
            raise ValueError(f"Unsupported syntax in rule condition: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in _FIELDS:
            raise ValueError(f"Unknown field {node.id!r} in rule condition")
    return tree


def _eval_condition(tree: ast.Expression, context: dict):
    """Interpret a validated condition tree against the expense context."""
    node = tree
    if isinstance(node, ast.Expression):
        return _eval_condition(node.body, context)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in context:
            raise ValueError(f"Unknown field {node.id!r} in rule condition")
        return context[node.id]
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = _eval_condition(value, context)
                if not result:
                    return result
            return result
        result = False
        for value in node.values:
            result = _eval_condition(value, context)
            if result:
                return result
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_condition(node.operand, context)
    if isinstance(node, ast.Compare):
        left = _eval_condition(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_condition(comparator, context)
            compare = _COMPARATORS.get(type(op))
            if compare is None:
                raise ValueError(f"Unsupported comparison in rule condition: {type(op).__name__}")
            if not compare(left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval_condition(element, context) for element in node.elts]
    raise ValueError(f"Unsupported syntax in rule condition: {type(node).__name__}")


def evaluate(condition: str, context: dict) -> bool:
    return bool(_eval_condition(_parse_condition(condition), context))


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
    compile(condition, "<rule>", "eval")  # reject syntax errors up front
    get_db().execute("INSERT INTO rules (name, condition, action) VALUES (?, ?, ?)", (name, condition, action))


def import_rules(stream) -> int:
    """Load rules from a YAML policy file exported by another workspace."""
    document = yaml.safe_load(stream) or {}
    rules = document.get("rules", []) if isinstance(document, dict) else []
    for rule in rules:
        add_rule(str(rule["name"]), str(rule["condition"]), str(rule["action"]))
    return len(rules)
