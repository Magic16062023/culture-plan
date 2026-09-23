"""Small SQL compatibility layer for the existing SQLite-backed application."""

import re

import psycopg
from psycopg.rows import dict_row


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
    password_salt TEXT NOT NULL, password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
    vk_id TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower ON users(LOWER(username));
CREATE UNIQUE INDEX IF NOT EXISTS users_vk_id_unique ON users(vk_id) WHERE vk_id <> '';
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, data TEXT NOT NULL,
    owner_id TEXT REFERENCES users(id), direction TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    creator_id TEXT NOT NULL REFERENCES users(id),
    recipient_user_id TEXT NOT NULL REFERENCES users(id),
    days_before INTEGER NOT NULL, sent_at TEXT, sent_for_date TEXT,
    created_at TEXT NOT NULL, UNIQUE(event_id, recipient_user_id, days_before)
);
CREATE TABLE IF NOT EXISTS calendar_feeds (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_states (
    state_hash TEXT PRIMARY KEY, code_verifier TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def translate(sql):
    sql = re.sub(r"(\w+)\s*=\s*\?\s+COLLATE NOCASE", r"LOWER(\1) = LOWER(?)", sql)
    sql = sql.replace("json_extract(data, '$.date')", "data::jsonb ->> 'date'")
    sql = sql.replace("json_extract(data, '$.time')", "data::jsonb ->> 'time'")
    if sql.startswith("INSERT OR IGNORE INTO notifications"):
        sql = sql.replace("INSERT OR IGNORE", "INSERT", 1) + " ON CONFLICT DO NOTHING"
    if sql.startswith("INSERT OR REPLACE INTO events"):
        sql = sql.replace("INSERT OR REPLACE", "INSERT", 1)
        sql += " ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data, owner_id=EXCLUDED.owner_id, direction=EXCLUDED.direction, created_at=EXCLUDED.created_at, updated_at=EXCLUDED.updated_at"
    return sql.replace("?", "%s")


class Connection:
    def __init__(self, url):
        self.connection = psycopg.connect(url, row_factory=dict_row)

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            if error_type:
                self.connection.rollback()
            else:
                self.connection.commit()
        finally:
            self.connection.close()

    def execute(self, sql, parameters=()):
        if sql == "PRAGMA foreign_keys = ON":
            return None
        if sql.startswith("PRAGMA table_info("):
            table = sql.removeprefix("PRAGMA table_info(").removesuffix(")")
            return self.connection.execute(
                "SELECT column_name AS name FROM information_schema.columns WHERE table_name = %s", (table,)
            )
        return self.connection.execute(translate(sql), parameters)

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.connection.execute(statement)
