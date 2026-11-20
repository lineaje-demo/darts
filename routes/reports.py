"""Finance reports page."""

import io
import re
import subprocess

from flask import Blueprint, Response, flash, redirect, render_template, request, send_file, url_for

from werkzeug.utils import secure_filename

from lib.auth import role_required
from services.reports import STATUSES, approved_totals, build_month_end_pack, fetch_expenses, write_csv

bp = Blueprint("reports", __name__)

MONTH = re.compile(r"^\d{4}-\d{2}$")
PACK_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


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
    body = stream_with_context(stream_csv(status if status in STATUSES else None))
    return Response(body, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=expenses.csv"})


@bp.post("/reports/month-end")
@role_required("finance")
def month_end_pack():
    month = request.form.get("month", "")
    if not MONTH.match(month):
        flash("Pick a month.", "error")
        return redirect(url_for("reports.index"))
    pack_name = secure_filename(request.form.get("pack_name", "").strip()) or f"darts-expenses-{month}"

    try:
        archive_name, data = build_month_end_pack(month, pack_name)
    except subprocess.CalledProcessError:
        flash("Could not build the month-end pack.", "error")
        return redirect(url_for("reports.index"))
    return send_file(io.BytesIO(data), mimetype="application/gzip",
                     as_attachment=True, download_name=archive_name)
