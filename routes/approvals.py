"""Manager and finance approval queue."""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from lib.auth import role_required
from lib.db import get_db, now
from services.access import EXPENSE_QUERY, can_approve, get_expense_or_404
from services.notify import build_reminder

bp = Blueprint("approvals", __name__)

DECISIONS = {"approve": "approved", "reject": "rejected"}


@bp.get("/approvals")
@role_required("manager", "finance")
def index():
    if g.user["role"] == "finance":
        where, params = "e.status IN ('flagged', 'submitted')", []
    else:
        where, params = "e.status = 'submitted' AND u.manager_id = ?", [g.user["id"]]
    pending = get_db().execute(f"{EXPENSE_QUERY} WHERE {where} ORDER BY e.spent_on", params).fetchall()
    return render_template("approvals.html", pending=pending)


@bp.post("/approvals/<int:expense_id>")
@role_required("manager", "finance")
def decide(expense_id: int):
    expense = get_expense_or_404(expense_id)
    decision = request.form.get("decision")
    if not can_approve(g.user, expense) or decision not in DECISIONS:
        abort(403)

    db = get_db()
    db.execute("UPDATE expenses SET status = ? WHERE id = ?", (DECISIONS[decision], expense_id))
    note = request.form.get("note", "").strip()
    db.execute(
        "INSERT INTO comments (expense_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
        (expense_id, g.user["id"], f"**{DECISIONS[decision].title()}**" + (f" — {note}" if note else ""), now()),
    )
    db.commit()
    flash(f"{expense['merchant']} expense {DECISIONS[decision]}.", "ok")
    if request.form.get("back") == "detail":
        return redirect(url_for("expenses.detail", expense_id=expense_id))
    return redirect(url_for("approvals.index"))


@bp.route("/approvals/<int:expense_id>/reminder", methods=["GET", "POST"])
@role_required("manager", "finance")
def reminder(expense_id: int):
    expense = get_expense_or_404(expense_id)
    if not can_approve(g.user, expense):
        abort(403)
    employee = get_db().execute("SELECT * FROM users WHERE id = ?", (expense["user_id"],)).fetchone()
    subject, body = build_reminder(expense, employee, g.user)

    if request.method == "POST":
        flash(f"Reminder sent to {employee['email']}.", "ok")
        return redirect(url_for("expenses.detail", expense_id=expense_id))
    return render_template("reminder.html", expense=expense, employee=employee, subject=subject, body=body)
