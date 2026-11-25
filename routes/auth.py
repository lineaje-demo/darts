"""Sign-in.

Production deployments put the app behind the corporate SSO proxy, which
establishes the session (see lib/auth.py). The persona picker below performs
NO authentication — it hands a session for any user to anyone who asks — so it
is only a local demo convenience and must be enabled explicitly by setting
DARTS_DEMO_LOGIN. When it is not enabled, sign-in fails closed.
"""

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

from lib.db import get_db

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not current_app.config.get("DEMO_LOGIN"):
        abort(403, description="Sign-in is handled by the SSO proxy; the demo persona picker is disabled.")
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
