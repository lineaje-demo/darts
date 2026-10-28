"""Darts: employee expense reporting and approvals."""

import os

from flask import Flask, g

from config import load_config
from lib import auth, csrf, db, filters
from routes import approvals, expenses, policy, reports
from routes import auth as auth_routes

__version__ = "1.3.0"


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(load_config(app.instance_path))
    if overrides:
        app.config.update(overrides)

    for key in ("DATA_DIR", "RECEIPTS_DIR", "REPORTS_DIR"):
        os.makedirs(app.config[key], exist_ok=True)

    db.init_db(app.config["DATABASE"])
    app.teardown_appcontext(db.close_db)
    app.before_request(auth.load_user)
    csrf.protect(app)
    filters.register(app)

    for module in (auth_routes, expenses, approvals, policy, reports):
        app.register_blueprint(module.bp)

    @app.context_processor
    def _inject_globals():
        return {"user": g.get("user"), "version": __version__}

    return app


if __name__ == "__main__":
    create_app().run(
        host=os.environ.get("DARTS_HOST", "127.0.0.1"),
        port=int(os.environ.get("DARTS_PORT", "8080")),
    )
