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


def _expenses_query(status: str | None = None, month: str | None = None):
    sql = "SELECT e.*, u.name AS employee FROM expenses e JOIN users u ON u.id = e.user_id WHERE 1 = 1"
    params: list = []
    if status:
        sql += " AND e.status = ?"
        params.append(status)
    if month:
        sql += " AND substr(e.spent_on, 1, 7) = ?"
        params.append(month)
    return sql + " ORDER BY e.spent_on", params


def fetch_expenses(status: str | None = None, month: str | None = None):
    sql, params = _expenses_query(status, month)
    return get_db().execute(sql, params).fetchall()


def iter_expenses(status: str | None = None, month: str | None = None):
    """Cursor over the matching expense rows, fetched lazily.

    Iterating pulls one row into memory at a time instead of materializing
    the whole (attacker-influenced) table.
    """
    sql, params = _expenses_query(status, month)
    return get_db().execute(sql, params)


def approved_totals(group_by: str):
    queries = {
        "category": "SELECT category AS label, COUNT(*) AS n, SUM(amount_cents) AS total FROM expenses"
                    " WHERE status = 'approved' GROUP BY category ORDER BY total DESC",
        "employee": "SELECT u.name AS label, COUNT(*) AS n, SUM(e.amount_cents) AS total FROM expenses e"
                    " JOIN users u ON u.id = e.user_id WHERE e.status = 'approved' GROUP BY u.id ORDER BY total DESC",
    }
    return get_db().execute(queries[group_by]).fetchall()


class _LineBuffer:
    """File-like sink whose ``write`` returns the line it was given.

    Lets ``csv.writer`` hand each serialized row back to a generator instead
    of accumulating the whole CSV in one in-memory buffer.
    """

    def write(self, line: str) -> str:
        return line


def _csv_row(e) -> list:
    return [e["id"], e["employee"], e["spent_on"], e["merchant"], e["category"],
            f"{e['amount_cents'] / 100:.2f}", e["status"], e["description"]]


def write_csv(out, expenses) -> None:
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    for e in expenses:
        writer.writerow(_csv_row(e))


def stream_csv(status: str | None = None):
    """Yield the expenses CSV one row at a time.

    Must be consumed inside ``flask.stream_with_context`` so ``get_db`` stays
    available while the response iterates. Memory stays proportional to a
    single row instead of the whole table.
    """
    writer = csv.writer(_LineBuffer())
    yield writer.writerow(CSV_HEADER)
    for e in iter_expenses(status):
        yield writer.writerow(_csv_row(e))


def build_month_end_pack(month: str, pack_name: str) -> str:
    """Bundle the month's approved expenses (CSV + receipts) into ``<pack_name>.tar.gz``."""
    if not isinstance(pack_name, str) or not PACK_NAME.fullmatch(pack_name):
        raise ValueError("Invalid pack name")
    reports_dir = current_app.config["REPORTS_DIR"]
    workdir = tempfile.mkdtemp(dir=reports_dir)
    try:
        with open(os.path.join(workdir, "expenses.csv"), "w", newline="", encoding="utf-8") as fh:
            write_csv(fh, iter_expenses("approved", month))
        os.makedirs(os.path.join(workdir, "receipts"))
        for e in iter_expenses("approved", month):
            if e["receipt"] and os.path.isfile(receipts.receipt_path(e["receipt"])):
                shutil.copy(receipts.receipt_path(e["receipt"]), os.path.join(workdir, "receipts"))

        archive = f"{pack_name}.tar.gz"
        subprocess.run(["tar", "-czf", os.path.join(reports_dir, archive), "-C", workdir, "."], check=True)
        return archive
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
