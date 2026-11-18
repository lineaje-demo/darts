"""Demo sign-in: pick a persona.

The persona picker verifies no credential, so it is disabled unless the app
is explicitly configured for it (``DEMO_AUTH``, env ``DARTS_DEMO_AUTH=1``).
Production deployments put the app behind the corporate SSO proxy, which
sets the same session key (see lib/auth.py).
"""

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

from lib.db import get_db

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    users = get_db().execute("SELECT * FROM users ORDER BY id").fetchall()
    if request.method == "POST":
        if not current_app.config.get("DEMO_AUTH"):
            # Credential-less persona login is disabled: never issue a session.
            abort(403)
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
