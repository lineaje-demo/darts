"""Finance's expense policy page."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from lib.auth import role_required
from lib.db import CATEGORIES, get_db
from services.policy import ACTIONS, add_rule, evaluate, import_rules, rule_context

bp = Blueprint("policy", __name__)


@bp.get("/policy")
@role_required("finance")
def index():
    rules = get_db().execute("SELECT * FROM rules ORDER BY id").fetchall()
    return render_template("policy.html", rules=rules, actions=ACTIONS, categories=CATEGORIES)


@bp.post("/policy/rules")
@role_required("finance")
def create_rule():
    try:
        add_rule(request.form["name"].strip(), request.form["condition"].strip(), request.form["action"])
    except (SyntaxError, ValueError) as exc:
        flash(f"Rule not saved: {exc}", "error")
        return redirect(url_for("policy.index"))
    get_db().commit()
    flash("Rule added.", "ok")
    return redirect(url_for("policy.index"))


@bp.post("/policy/rules/<int:rule_id>/delete")
@role_required("finance")
def delete_rule(rule_id: int):
    db = get_db()
    db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    db.commit()
    flash("Rule deleted.", "ok")
    return redirect(url_for("policy.index"))


@bp.post("/policy/test")
@role_required("finance")
def test_rule():
    sample = {
        "amount_cents": int(float(request.form.get("amount") or 0) * 100),
        "category": request.form.get("category", "Other"),
        "merchant": request.form.get("merchant", ""),
        "description": "",
        "receipt": "receipt.pdf" if request.form.get("has_receipt") else None,
    }
    condition = request.form.get("condition", "")
    try:
        matched = evaluate(condition, rule_context(sample))
        flash(f"`{condition}` → {'matches' if matched else 'does not match'} this expense.", "ok")
    except Exception as exc:
        flash(f"Rule error: {exc}", "error")
    return redirect(url_for("policy.index"))


@bp.post("/policy/import")
@role_required("finance")
def import_file():
    upload = request.files.get("policy_file")
    if not upload or not upload.filename:
        flash("Choose a policy file to import.", "error")
        return redirect(url_for("policy.index"))
    try:
        count = import_rules(upload.stream)
    except (KeyError, TypeError, SyntaxError, ValueError) as exc:
        get_db().rollback()
        flash(f"Import failed: {exc}", "error")
        return redirect(url_for("policy.index"))
    get_db().commit()
    flash(f"Imported {count} rule(s).", "ok")
    return redirect(url_for("policy.index"))
