"""SQLite connection + schema. Schema lives here in plain SQL — small enough
that a migration framework would be overkill."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS mapping (
    onestop_desc_normalized TEXT PRIMARY KEY,
    onestop_desc            TEXT NOT NULL,
    gm_item_no              INTEGER,
    gm_sheet                TEXT,
    gm_desc                 TEXT,
    confidence              REAL NOT NULL,
    confirmed_by            TEXT NOT NULL,
    confirmed_at            TEXT NOT NULL,
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS gm_catalog (
    item_no                 INTEGER NOT NULL,
    sheet                   TEXT    NOT NULL,
    side                    TEXT    NOT NULL,
    row_index               INTEGER NOT NULL,
    description             TEXT    NOT NULL,
    description_normalized  TEXT    NOT NULL,
    price                   REAL,
    available               INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (item_no, sheet)
);

CREATE INDEX IF NOT EXISTS idx_gm_norm ON gm_catalog(description_normalized);

CREATE TABLE IF NOT EXISTS onestop_template (
    row_index               INTEGER PRIMARY KEY,
    description             TEXT    NOT NULL,
    description_normalized  TEXT    NOT NULL,
    price                   REAL,
    is_header               INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS order_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uploaded_at     TEXT NOT NULL,
    uploaded_by     TEXT NOT NULL,
    filename        TEXT NOT NULL,
    lines_auto      INTEGER NOT NULL,
    lines_reviewed  INTEGER NOT NULL,
    lines_unmatched INTEGER NOT NULL,
    output_path     TEXT
);

-- Single-row table for FreshBooks OAuth tokens (single-tenant app).
-- id is always 1 — enforced by CHECK constraint.
CREATE TABLE IF NOT EXISTS freshbooks_tokens (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    access_token  TEXT    NOT NULL,
    refresh_token TEXT    NOT NULL,
    account_id    TEXT    NOT NULL,
    expires_at    REAL    NOT NULL   -- Unix timestamp (seconds)
);
"""


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
