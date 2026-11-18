"""Current-user loading and role checks.

The demo build signs people in by picking a persona (see routes/auth.py);
production deployments put the app behind the corporate SSO proxy, which sets
the same session key.
"""

import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import abort, g, redirect, session, url_for

from lib.db import get_db, now

# Server-side session records make logout effective: a session cookie is only
# honoured while its "sid" still exists (unexpired) in the sessions table.
SESSION_MAX_AGE_DAYS = 31  # matches Flask's default permanent_session_lifetime


def _cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=SESSION_MAX_AGE_DAYS)).isoformat(timespec="seconds")


def create_session(user_id: int) -> None:
    """Start a fresh, server-side revocable session for ``user_id``."""
    db = get_db()
    old_sid = session.get("sid")
    if old_sid:
        db.execute("DELETE FROM sessions WHERE sid = ?", (old_sid,))
    db.execute("DELETE FROM sessions WHERE created_at < ?", (_cutoff(),))
    sid = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions (sid, user_id, created_at) VALUES (?, ?, ?)", (sid, user_id, now()))
    db.commit()
    session.clear()
    session["sid"] = sid
    session["user_id"] = user_id


def destroy_session() -> None:
    """Log out: revoke the server-side record so the cookie dies with it."""
    sid = session.get("sid")
    if sid:
        db = get_db()
        db.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
        db.commit()
    session.clear()


def _session_user_id() -> int | None:
    """Authenticated user id for this request, or None when the cookie's
    session was revoked, expired, or malformed."""
    user_id = session.get("user_id")
    sid = session.get("sid")
    if type(user_id) is not int or not sid:
        return None
    row = get_db().execute(
        "SELECT user_id FROM sessions WHERE sid = ? AND created_at >= ?", (sid, _cutoff())
    ).fetchone()
    if row is None or row["user_id"] != user_id:
        return None
    return user_id


def load_user() -> None:
    g.user = None
    user_id = _session_user_id()
    if user_id is None:
        if session.get("user_id") is not None or session.get("sid") is not None:
            session.clear()  # expire revoked/invalid credentials client-side too
        return
    g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


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
