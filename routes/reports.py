"""Finance reports page."""

import io
import re
import subprocess

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, send_from_directory, url_for

from lib.auth import role_required
from services.reports import STATUSES, approved_totals, build_month_end_pack, fetch_expenses, write_csv

bp = Blueprint("reports", __name__)

MONTH = re.compile(r"^\d{4}-\d{2}$")


@bp.get("/reports")
@role_required("finance")
def index():
    return render_template(
        "reports.html",
        by_category=approved_totals("category"),
        by_employee=approved_totals("employee"),
        statuses=STATUSES,
    )


@bp.get("/reports/expenses.csv")
@role_required("finance")
def export_csv():
    status = request.args.get("status")
    out = io.StringIO()
    write_csv(out, fetch_expenses(status if status in STATUSES else None))
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=expenses.csv"})


@bp.post("/reports/month-end")
@role_required("finance")
def month_end_pack():
    month = request.form.get("month", "")
    if not MONTH.match(month):
        flash("Pick a month.", "error")
        return redirect(url_for("reports.index"))
    pack_name = request.form.get("pack_name", "").strip() or f"darts-expenses-{month}"

    try:
        archive = build_month_end_pack(month, pack_name)
    except subprocess.CalledProcessError:
        flash("Could not build the month-end pack.", "error")
        return redirect(url_for("reports.index"))
    return send_from_directory(current_app.config["REPORTS_DIR"], archive, as_attachment=True)
