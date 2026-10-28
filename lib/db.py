"""SQLite access, schema and demo seed data."""

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL,            -- employee | manager | finance
    title       TEXT NOT NULL DEFAULT '',
    manager_id  INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS expenses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    merchant      TEXT NOT NULL,
    category      TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL,
    spent_on      TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    receipt       TEXT,
    status        TEXT NOT NULL DEFAULT 'submitted',  -- draft | submitted | flagged | approved | rejected
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id  INTEGER NOT NULL REFERENCES expenses(id),
    user_id     INTEGER REFERENCES users(id),     -- NULL for policy-engine notes
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    condition  TEXT NOT NULL,
    action     TEXT NOT NULL                -- approve | flag | reject
);
"""

CATEGORIES = ("Travel", "Lodging", "Meals", "Transport", "Office", "Software", "Other")

USERS = [
    (1, "Alice Chen", "alice@darts.test", "employee", "Account Executive", 2),
    (2, "Bob Rivera", "bob@darts.test", "manager", "Sales Director", None),
    (3, "Carol Diaz", "carol@darts.test", "finance", "Finance Controller", None),
    (4, "Dan Okafor", "dan@darts.test", "employee", "Solutions Engineer", 2),
]

# user_id, merchant, category, amount_cents, days_ago, description, status
EXPENSES = [
    (1, "Delta Air Lines", "Travel", 41230, 21, "Flight to Chicago for the Q4 customer summit", "approved"),
    (1, "Hilton Chicago", "Lodging", 68900, 19, "Three nights during the summit", "approved"),
    (1, "Uber", "Transport", 3875, 18, "Airport to hotel", "approved"),
    (1, "Blue Bottle Coffee", "Meals", 1450, 6, "Coffee with **Globex** procurement team", "approved"),
    (1, "The Capital Grille", "Meals", 31200, 4, "Dinner with Initech CIO and two directors", "submitted"),
    (4, "AWS Marketplace", "Software", 9900, 9, "Sandbox licence for the Initech proof of concept", "submitted"),
    (4, "United Airlines", "Travel", 128740, 3, "Last-minute flight to the Umbrella Corp onsite", "flagged"),
    (4, "Staples", "Office", 8999, 2, "Demo kit: HDMI adapters and clicker", "submitted"),
]

RULES = [
    ("Auto-approve small meals", 'category == "Meals" and amount < 25', "approve"),
    ("Send big travel to Finance", 'category == "Travel" and amount > 1000', "flag"),
    ("No receipt over $75", "amount > 75 and not has_receipt", "flag"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(path: str) -> None:
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            _seed(conn)


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO users (id, name, email, role, title, manager_id) VALUES (?, ?, ?, ?, ?, ?)", USERS
    )
    today = date.today()
    for user_id, merchant, category, cents, days_ago, description, status in EXPENSES:
        conn.execute(
            "INSERT INTO expenses (user_id, merchant, category, amount_cents, spent_on, description,"
            " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, merchant, category, cents, (today - timedelta(days=days_ago)).isoformat(),
             description, status, now()),
        )
    conn.executemany("INSERT INTO rules (name, condition, action) VALUES (?, ?, ?)", RULES)


def parse_amount(text: str) -> int:
    """Parse a user-entered amount such as ``1,234.50`` into cents."""
    try:
        value = Decimal(text.replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        raise ValueError("Enter the amount as a number, e.g. 42.50") from None
    if value <= 0 or value > 100000:
        raise ValueError("Amount must be between $0.01 and $100,000")
    return int(value.quantize(Decimal("0.01")) * 100)
