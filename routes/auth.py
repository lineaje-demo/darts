"""Demo sign-in: pick a persona and enter the demo password."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from lib.db import get_db

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    users = get_db().execute("SELECT * FROM users ORDER BY id").fetchall()
    if request.method == "POST":
        chosen = request.form.get("user_id", type=int)
        password = request.form.get("password", "")
        user = next((u for u in users if u["id"] == chosen), None)
        if user is not None and password and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("expenses.index"))
        flash("Unknown user or wrong password.", "error")
        return render_template("login.html", users=users)
    return render_template("login.html", users=users)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
