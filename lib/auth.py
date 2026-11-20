"""Current-user loading, server-side session lifecycle and role checks.

The demo build signs people in by picking a persona (see routes/auth.py).
Authentication state is kept server-side in the ``sessions`` table; the
client cookie only carries an opaque ``session_token``, so logging out can
revoke the session and previously issued cookies stop authenticating.
"""

import secrets
from functools import wraps

from flask import abort, g, redirect, session, url_for

from lib.db import get_db, now


def load_user() -> None:
    token = session.get("session_token")
    g.user = None
    if token is not None:
        g.user = get_db().execute(
            "SELECT u.* FROM users u JOIN sessions s ON s.user_id = u.id WHERE s.token = ?",
            (token,),
        ).fetchone()


def create_session(user_id: int) -> None:
    """Start a server-side session for ``user_id`` and point the cookie at it."""
    token = secrets.token_urlsafe(32)
    db = get_db()
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user_id, now()),
    )
    db.commit()
    session["session_token"] = token


def revoke_session() -> None:
    """Invalidate the current server-side session, if any."""
    token = session.pop("session_token", None)
    if token is not None:
        db = get_db()
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        db.commit()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("auth.login"))
            if g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator
