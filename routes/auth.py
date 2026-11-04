"""Sign-in.

Production deployments put the app behind the corporate SSO proxy, which
sets the session key this app reads (see lib/auth.py). The persona picker
below performs no credential check, so it is a local-demo convenience only:
it is disabled unless the operator explicitly sets DARTS_DEMO_LOGIN=1, and
/login neither creates sessions nor discloses the user roster otherwise.
"""

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

from lib.db import get_db

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not current_app.config.get("DARTS_DEMO_LOGIN", False):
        if request.method == "POST":
            abort(403)
        return render_template("login.html", users=None)
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
