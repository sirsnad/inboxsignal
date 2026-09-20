"""SQLite schema and helpers.

Schema follows SPEC.md section 8, extended with the display columns the UI
needs (subject/snippet on messages and threads, recipient info for the
cc-only and waiting-on-them tests) and a sync_state table for historyId.
"""

import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS senders (
    address TEXT PRIMARY KEY,
    display_name TEXT,
    domain TEXT,
    tier TEXT,                -- person|service|feed|promo|bot|notes|split
    route TEXT,               -- people|needs_you|heads_up|money|on_its_way|lane:<Name>|promotions|notebook|muted
    evidence_json TEXT,
    created_at TEXT,
    confirmed_by_user INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sender_subrules (
    sender_address TEXT REFERENCES senders(address),
    email_type TEXT,
    route TEXT,
    PRIMARY KEY (sender_address, email_type)
);

CREATE TABLE IF NOT EXISTS threads (
    gmail_thread_id TEXT PRIMARY KEY,
    subject TEXT,
    last_message_at TEXT,
    section TEXT,
    state TEXT DEFAULT 'open',    -- open|done|snoozed|sent|following
    snooze_until TEXT,
    ask_summary TEXT,             -- JSON: {ask, age_note, related, one_line}
    age_days INTEGER,
    is_cc_only INTEGER DEFAULT 0,
    last_from_me INTEGER DEFAULT 0,
    counterpart TEXT              -- address of the main human on the other end
);

CREATE TABLE IF NOT EXISTS messages (
    gmail_message_id TEXT PRIMARY KEY,
    thread_id TEXT REFERENCES threads(gmail_thread_id),
    from_address TEXT,
    from_name TEXT,
    to_addresses TEXT,            -- JSON list
    cc_addresses TEXT,            -- JSON list
    subject TEXT,
    snippet TEXT,
    body_text TEXT,
    list_unsubscribe TEXT,
    email_type TEXT,
    extracted_json TEXT,
    received_at TEXT,
    opened_at TEXT,               -- set when UNREAD label absent (proxy, SPEC 8)
    is_from_me INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lanes (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    cadence TEXT DEFAULT 'daily',
    digest_time TEXT DEFAULT '07:00',
    snoozed_until TEXT,
    snooze_reason TEXT
);

CREATE TABLE IF NOT EXISTS lane_sources (
    lane_id INTEGER REFERENCES lanes(id),
    sender_address TEXT REFERENCES senders(address),
    PRIMARY KEY (lane_id, sender_address)
);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY,
    lane_id INTEGER REFERENCES lanes(id),
    date TEXT,
    items_json TEXT
);

CREATE TABLE IF NOT EXISTS money (
    id INTEGER PRIMARY KEY,
    message_id TEXT REFERENCES messages(gmail_message_id),
    merchant TEXT,
    amount_cents INTEGER,
    source TEXT,
    tag TEXT
);

CREATE TABLE IF NOT EXISTS tracking (
    id INTEGER PRIMARY KEY,
    message_id TEXT REFERENCES messages(gmail_message_id),
    carrier TEXT,
    status TEXT,
    eta TEXT,
    delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    at TEXT,
    kind TEXT,
    target TEXT,
    description TEXT,
    gmail_effect TEXT,
    undone_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_address);
CREATE INDEX IF NOT EXISTS idx_messages_received ON messages(received_at);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    own = conn is None
    conn = conn or connect()
    conn.executescript(SCHEMA)
    for name in config.DEFAULT_LANES:
        conn.execute("INSERT OR IGNORE INTO lanes(name) VALUES (?)", (name,))
    conn.commit()
    return conn


@contextmanager
def session():
    conn = init()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_state(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def loads(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default
