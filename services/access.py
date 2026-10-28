"""Who can see and approve which expenses."""

from flask import abort, g

from lib.db import get_db

EXPENSE_QUERY = (
    "SELECT e.*, u.name AS employee, u.email AS employee_email, u.manager_id"
    " FROM expenses e JOIN users u ON u.id = e.user_id"
)


def visible_to(user) -> tuple[str, list]:
    """SQL condition (and its parameters) limiting expenses to those ``user`` may see."""
    if user["role"] == "finance":
        return "1 = 1", []
    if user["role"] == "manager":
        return "(e.user_id = ? OR u.manager_id = ?)", [user["id"], user["id"]]
    return "e.user_id = ?", [user["id"]]


def can_view(user, expense) -> bool:
    return (
        user["role"] == "finance"
        or expense["user_id"] == user["id"]
        or (user["role"] == "manager" and expense["manager_id"] == user["id"])
    )


def can_approve(user, expense) -> bool:
    if expense["user_id"] == user["id"]:
        return False
    return user["role"] == "finance" or (user["role"] == "manager" and expense["manager_id"] == user["id"])


def get_expense_or_404(expense_id: int):
    expense = get_db().execute(f"{EXPENSE_QUERY} WHERE e.id = ?", (expense_id,)).fetchone()
    if expense is None or not can_view(g.user, expense):
        abort(404)
    return expense
