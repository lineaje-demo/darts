"""Application configuration.

Values come from defaults, then an optional YAML settings file named by
``DARTS_SETTINGS``, then individual ``DARTS_*`` environment variables.
"""

import os
import secrets

import yaml

SETTINGS_KEYS = ("DATA_DIR", "MAX_CONTENT_LENGTH")


def load_config(instance_path: str) -> dict:
    settings: dict = {
        "DATA_DIR": instance_path,
        "MAX_CONTENT_LENGTH": 20 * 1024 * 1024,
    }

    settings_file = os.environ.get("DARTS_SETTINGS")
    if settings_file and os.path.exists(settings_file):
        with open(settings_file, encoding="utf-8") as fh:
            overrides = yaml.safe_load(fh) or {}
        settings.update({k: v for k, v in overrides.items() if k in SETTINGS_KEYS})

    if os.environ.get("DARTS_DATA_DIR"):
        settings["DATA_DIR"] = os.environ["DARTS_DATA_DIR"]

    data_dir = settings["DATA_DIR"]
    return {
        **settings,
        "SECRET_KEY": os.environ.get("DARTS_SECRET_KEY") or secrets.token_hex(32),
        "DATABASE": os.path.join(data_dir, "expenses.db"),
        "RECEIPTS_DIR": os.path.join(data_dir, "receipts"),
        "REPORTS_DIR": os.path.join(data_dir, "reports"),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
    }
