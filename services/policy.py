"""Expense policy rules.

Finance writes rules as short expressions over an expense, for example::

    category == "Meals" and amount < 25

The first matching rule decides what happens to a newly submitted expense.

Rule conditions are *not* Python code. They are parsed with :mod:`ast` and
evaluated by a small interpreter that understands only the rule grammar:
literals, the expense fields listed in :func:`rule_context`, ``and``/``or``/
``not``, arithmetic, and comparisons. Anything else (attribute access, calls,
comprehensions, subscripts, ...) is rejected, so a stored condition can never
execute code, no matter who wrote it or how it reached the database.
"""

import ast
import operator

import yaml

from lib.db import get_db, now

ACTIONS = {"approve": "approved", "flag": "flagged", "reject": "rejected"}

# Names a rule condition may reference: the fields rule_context exposes.
_CONTEXT_FIELDS = frozenset({"amount", "category", "merchant", "description", "has_receipt"})

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}

_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
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
    """Reject anything outside the rule-expression grammar."""
    if isinstance(node, ast.Expression):
        _check(node.body)
    elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _check(value)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
        _check(node.operand)
    elif isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        _check(node.left)
        _check(node.right)
    elif isinstance(node, ast.Compare):
        _check(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            if type(op) not in _CMP_OPS:
                raise ValueError(f"{type(op).__name__} is not allowed in rule conditions")
            _check(comparator)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            _check(elt)
    elif isinstance(node, ast.Name):
        if node.id not in _CONTEXT_FIELDS:
            raise ValueError(f"Unknown name {node.id!r} in rule condition")
    elif isinstance(node, ast.Constant):
        pass
    else:
        raise ValueError(f"{type(node).__name__} is not allowed in rule conditions")


def _eval(node, context: dict):
    """Interpret a rule-expression AST already vetted by _check()."""
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
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand
    if isinstance(node, ast.BinOp):
        return _BIN_OPS[type(node.op)](_eval(node.left, context), _eval(node.right, context))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, context)
            if not _CMP_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(elt, context) for elt in node.elts]
    raise ValueError(f"{type(node).__name__} is not allowed in rule conditions")


def _parse(condition: str) -> ast.Expression:
    """Parse and validate a rule condition; raises SyntaxError or ValueError."""
    tree = ast.parse(condition, mode="eval")
    _check(tree)
    return tree


def evaluate(condition: str, context: dict) -> bool:
    return bool(_eval(_parse(condition), context))


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
    _parse(condition)  # reject syntax errors and non-grammar expressions up front
    get_db().execute("INSERT INTO rules (name, condition, action) VALUES (?, ?, ?)", (name, condition, action))


def import_rules(stream) -> int:
    """Load rules from a YAML policy file exported by another workspace."""
    document = yaml.safe_load(stream) or {}
    rules = document.get("rules", []) if isinstance(document, dict) else []
    for rule in rules:
        add_rule(str(rule["name"]), str(rule["condition"]), str(rule["action"]))
    return len(rules)
