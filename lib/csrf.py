"""Minimal synchronizer-token CSRF protection for form posts."""

import hmac
import secrets

from flask import abort, request, session

SESSION_KEY = "_csrf"
FORM_FIELD = "_csrf"


def csrf_token() -> str:
    token = session.get(SESSION_KEY)
    if token is None:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def protect(app) -> None:
    @app.before_request
    def _verify_csrf_token():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return
        expected = session.get(SESSION_KEY)
        supplied = request.form.get(FORM_FIELD, "")
        if not expected or not hmac.compare_digest(expected, supplied):
            abort(400, description="Invalid or missing CSRF token.")

    app.jinja_env.globals["csrf_token"] = csrf_token
