"""Spend summaries, CSV export and the month-end pack."""

import csv
import os
import re
import shutil
import subprocess
import tempfile

from flask import current_app

from lib.db import get_db
from services import receipts

STATUSES = ("approved", "submitted", "flagged", "rejected", "draft")
CSV_HEADER = ["id", "employee", "date", "merchant", "category", "amount", "status", "description"]
# Cells whose text starts with one of these characters are interpreted as formulas by
# spreadsheet applications; neutralize them when exporting (OWASP CSV injection guidance).
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Prefix a quote on cells a spreadsheet would treat as a formula."""
    if isinstance(value, str) and (
        value[:1] in FORMULA_TRIGGERS or value.lstrip().startswith(FORMULA_TRIGGERS)
    ):
        return "'" + value
    return value


def fetch_expenses(status: str | None = None, month: str | None = None):
    sql = "SELECT e.*, u.name AS employee FROM expenses e JOIN users u ON u.id = e.user_id WHERE 1 = 1"
    params: list = []
    if status:
        sql += " AND e.status = ?"
        params.append(status)
    if month:
        sql += " AND substr(e.spent_on, 1, 7) = ?"
        params.append(month)
    return get_db().execute(sql + " ORDER BY e.spent_on", params).fetchall()


def approved_totals(group_by: str):
    queries = {
        "category": "SELECT category AS label, COUNT(*) AS n, SUM(amount_cents) AS total FROM expenses"
                    " WHERE status = 'approved' GROUP BY category ORDER BY total DESC",
        "employee": "SELECT u.name AS label, COUNT(*) AS n, SUM(e.amount_cents) AS total FROM expenses e"
                    " JOIN users u ON u.id = e.user_id WHERE e.status = 'approved' GROUP BY u.id ORDER BY total DESC",
    }
    return get_db().execute(queries[group_by]).fetchall()


def write_csv(out, expenses) -> None:
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    for e in expenses:
        row = [e["id"], e["employee"], e["spent_on"], e["merchant"], e["category"],
               f"{e['amount_cents'] / 100:.2f}", e["status"], e["description"]]
        writer.writerow([_csv_safe(value) for value in row])


def build_month_end_pack(month: str, pack_name: str) -> str:
    """Bundle the month's approved expenses (CSV + receipts) into ``<pack_name>.tar.gz``."""
    if not PACK_NAME.fullmatch(pack_name):
        raise ValueError(f"invalid pack name: {pack_name!r}")
    reports_dir = current_app.config["REPORTS_DIR"]
    workdir = tempfile.mkdtemp(dir=reports_dir)
    try:
        expenses = fetch_expenses("approved", month)
        with open(os.path.join(workdir, "expenses.csv"), "w", newline="", encoding="utf-8") as fh:
            write_csv(fh, expenses)
        os.makedirs(os.path.join(workdir, "receipts"))
        for e in expenses:
            if e["receipt"] and os.path.isfile(receipts.receipt_path(e["receipt"])):
                shutil.copy(receipts.receipt_path(e["receipt"]), os.path.join(workdir, "receipts"))

        archive = f"{pack_name}.tar.gz"
        subprocess.run(["tar", "-czf", os.path.join(reports_dir, archive), "-C", workdir, "."], check=True)
        return archive
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
