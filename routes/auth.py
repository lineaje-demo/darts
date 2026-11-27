"""Demo sign-in: pick a persona."""

from flask import Blueprint, redirect, render_template, request, url_for

from lib.auth import create_session, destroy_session
from lib.db import get_db

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    users = get_db().execute("SELECT * FROM users ORDER BY id").fetchall()
    if request.method == "POST":
        chosen = request.form.get("user_id", type=int)
        if chosen in {u["id"] for u in users}:
            destroy_session()
            create_session(chosen)
            return redirect(url_for("expenses.index"))
    return render_template("login.html", users=users)


@bp.post("/logout")
def logout():
    destroy_session()
    return redirect(url_for("auth.login"))
