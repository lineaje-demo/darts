"""Current-user loading and role checks.

The demo build signs people in by picking a persona (see routes/auth.py);
production deployments put the app behind the corporate SSO proxy, which sets
the same session key.

Each login also creates a server-side record in the ``sessions`` table and the
cookie carries only its opaque random id (``sid``). Deleting that record (see
``destroy_session``) revokes every copy of the cookie immediately, so logout
invalidates stolen or replayed session cookies instead of only clearing the
requesting browser's jar.
"""

import secrets
from functools import wraps

from flask import abort, g, redirect, session, url_for

from lib.db import get_db, now

SESSION_ID_KEY = "sid"


def create_session(user_id: int) -> None:
    """Start a new authenticated session for ``user_id``."""
    sid = secrets.token_urlsafe(32)
    db = get_db()
    db.execute("INSERT INTO sessions (id, user_id, created_at) VALUES (?, ?, ?)", (sid, user_id, now()))
    db.commit()
    session["user_id"] = user_id
    session[SESSION_ID_KEY] = sid


def destroy_session() -> None:
    """Log out: revoke the server-side session record and clear the cookie."""
    sid = session.pop(SESSION_ID_KEY, None)
    if sid is not None:
        db = get_db()
        db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        db.commit()
    session.clear()


def load_user() -> None:
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    sid = session.get(SESSION_ID_KEY)
    row = None
    if sid is not None:
        row = get_db().execute(
            "SELECT users.* FROM users"
            " JOIN sessions ON sessions.user_id = users.id"
            " WHERE users.id = ? AND sessions.id = ?",
            (user_id, sid),
        ).fetchone()
    if row is None:
        # Cookie names a user but has no live server-side session (revoked,
        # unknown, or pre-dating server-side sessions): drop the stale
        # credential and treat the request as anonymous.
        session.clear()
        return
    g.user = row


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
