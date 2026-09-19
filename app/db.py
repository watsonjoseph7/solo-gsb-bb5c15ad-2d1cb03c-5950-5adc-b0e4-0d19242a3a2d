"""SQLite 持久化：方案按 plan_id 存储，幂等键唯一。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

_DEFAULT_DB_PATH = "./data/blends.db"
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    plan_id         TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    request_json    TEXT NOT NULL,
    response_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""


def db_path() -> str:
    return os.environ.get("DATABASE_PATH", _DEFAULT_DB_PATH)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    path = db_path()
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)


def find_by_key(idempotency_key: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM plans WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
    return dict(row) if row else None


def find_by_id(plan_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
    return dict(row) if row else None


def save_plan(plan_id: str, idempotency_key: str | None,
              request: dict, response: dict, created_at: str) -> None:
    canonical = json.dumps(request, sort_keys=True, ensure_ascii=False)
    payload = json.dumps(response, ensure_ascii=False)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO plans (plan_id, idempotency_key, request_json,"
            " response_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (plan_id, idempotency_key, canonical, payload, created_at),
        )
