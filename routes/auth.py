"""Demo sign-in: pick a persona.

The persona picker establishes an authenticated session without verifying any
credential, so it is disabled unless the operator explicitly opts in via the
``PERSONA_LOGIN`` config setting. Production deployments authenticate through
the corporate SSO proxy, which sets the same session key.
"""

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

from lib.db import get_db

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not current_app.config.get("PERSONA_LOGIN", False):
        if request.method == "POST":
            abort(403, description="Persona sign-in is disabled on this server.")
        return render_template("login.html", users=[])
    users = get_db().execute("SELECT * FROM users ORDER BY id").fetchall()
    if request.method == "POST":
        chosen = request.form.get("user_id", type=int)
        if chosen in {u["id"] for u in users}:
            session.clear()
            session["user_id"] = chosen
            return redirect(url_for("expenses.index"))
    return render_template("login.html", users=users)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
