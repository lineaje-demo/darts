"""Submitting, browsing and discussing expenses."""

import os
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for

from lib.auth import login_required
from lib.db import CATEGORIES, get_db, now, parse_amount
from services import policy, receipts
from services.access import EXPENSE_QUERY, can_approve, get_expense_or_404, visible_to

bp = Blueprint("expenses", __name__)


@bp.get("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    where, params = visible_to(g.user)
    sql = f"{EXPENSE_QUERY} WHERE {where}"
    if q:
        sql += f" AND (e.merchant LIKE '%{q}%' OR e.description LIKE '%{q}%')"
    sql += " ORDER BY e.spent_on DESC, e.id DESC"
    expenses = get_db().execute(sql, params).fetchall()

    totals = {status: 0 for status in ("submitted", "flagged", "approved", "rejected")}
    for e in expenses:
        if e["status"] in totals:
            totals[e["status"]] += e["amount_cents"]
    return render_template("expenses.html", expenses=expenses, totals=totals, q=q)


@bp.route("/expenses/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "GET":
        return render_template("expense_form.html", categories=CATEGORIES, form={}, today=date.today())

    form = request.form
    try:
        amount_cents = parse_amount(form.get("amount", ""))
        spent_on = date.fromisoformat(form.get("spent_on", "")).isoformat()
        if form.get("category") not in CATEGORIES:
            raise ValueError("Pick a category")
        merchant = form.get("merchant", "").strip()
        if not merchant:
            raise ValueError("Enter the merchant")

        receipt = None
        upload = request.files.get("receipt")
        if upload and upload.filename:
            receipt = receipts.save_upload(upload)
        elif form.get("receipt_url", "").strip():
            receipt = receipts.fetch_from_url(form["receipt_url"].strip())
    except (ValueError, receipts.ReceiptError) as exc:
        flash(str(exc), "error")
        return render_template("expense_form.html", categories=CATEGORIES, form=form, today=date.today())

    db = get_db()
    cur = db.execute(
        "INSERT INTO expenses (user_id, merchant, category, amount_cents, spent_on, description, receipt,"
        " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', ?)",
        (g.user["id"], merchant, form["category"], amount_cents, spent_on,
         form.get("description", "").strip(), receipt, now()),
    )
    db.commit()
    expense_id = cur.lastrowid

    rule = policy.apply_rules(expense_id)
    flash("Expense submitted." + (f" Policy: {rule['name']}." if rule else ""), "ok")
    return redirect(url_for("expenses.detail", expense_id=expense_id))


@bp.post("/expenses/bulk-upload")
@login_required
def bulk_upload():
    upload = request.files.get("bundle")
    if not upload or not upload.filename:
        flash("Choose a .zip of receipts.", "error")
        return redirect(url_for("expenses.index"))
    try:
        names = receipts.extract_bundle(upload.stream)
    except receipts.ReceiptError as exc:
        flash(str(exc), "error")
        return redirect(url_for("expenses.index"))

    db = get_db()
    for name in names:
        merchant = os.path.splitext(os.path.basename(name))[0].replace("_", " ").replace("-", " ").title()
        db.execute(
            "INSERT INTO expenses (user_id, merchant, category, amount_cents, spent_on, receipt, status,"
            " created_at) VALUES (?, ?, 'Other', 0, ?, ?, 'draft', ?)",
            (g.user["id"], merchant or "Receipt", date.today().isoformat(), name, now()),
        )
    db.commit()
    flash(f"Created {len(names)} draft expense(s) from your receipts. Fill in the amounts to submit them.", "ok")
    return redirect(url_for("expenses.index"))


@bp.get("/expenses/<int:expense_id>")
@login_required
def detail(expense_id: int):
    expense = get_expense_or_404(expense_id)
    comments = get_db().execute(
        "SELECT c.*, u.name AS author FROM comments c LEFT JOIN users u ON u.id = c.user_id"
        " WHERE c.expense_id = ? ORDER BY c.id",
        (expense_id,),
    ).fetchall()
    return render_template(
        "expense_detail.html",
        expense=expense,
        comments=comments,
        categories=CATEGORIES,
        can_approve=can_approve(g.user, expense),
    )


@bp.post("/expenses/<int:expense_id>/submit")
@login_required
def submit_draft(expense_id: int):
    expense = get_expense_or_404(expense_id)
    if expense["user_id"] != g.user["id"] or expense["status"] != "draft":
        abort(403)
    try:
        amount_cents = parse_amount(request.form.get("amount", ""))
        if request.form.get("category") not in CATEGORIES:
            raise ValueError("Pick a category")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("expenses.detail", expense_id=expense_id))

    db = get_db()
    db.execute(
        "UPDATE expenses SET merchant = ?, category = ?, amount_cents = ?, description = ?, status = 'submitted'"
        " WHERE id = ?",
        (request.form.get("merchant", expense["merchant"]).strip(), request.form["category"], amount_cents,
         request.form.get("description", "").strip(), expense_id),
    )
    db.commit()
    rule = policy.apply_rules(expense_id)
    flash("Expense submitted." + (f" Policy: {rule['name']}." if rule else ""), "ok")
    return redirect(url_for("expenses.detail", expense_id=expense_id))


@bp.post("/expenses/<int:expense_id>/comments")
@login_required
def add_comment(expense_id: int):
    get_expense_or_404(expense_id)
    body = request.form.get("body", "").strip()
    if body:
        db = get_db()
        db.execute(
            "INSERT INTO comments (expense_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (expense_id, g.user["id"], body, now()),
        )
        db.commit()
    return redirect(url_for("expenses.detail", expense_id=expense_id))


@bp.get("/receipts")
@login_required
def view_receipt():
    path = receipts.receipt_path(request.args.get("file", ""))
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)
