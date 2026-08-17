"""Histórico das conversas, num SQLite.

Para 1-2 usuários isso é mais do que suficiente e não exige servidor nenhum.
O banco é um arquivo só: data/livia.db. Apagar o arquivo = esquecer as
conversas (as memórias e skills continuam, porque são arquivos separados).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT 'Nova conversa',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def create_conversation(title: str = "Nova conversa") -> int:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        return int(cur.lastrowid)


def list_conversations(limit: int = 50) -> list[dict[str, object]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n
            FROM conversations c
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(conversation_id: int, role: str, content: str) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )


def get_messages(conversation_id: int, limit: int | None = None) -> list[dict[str, str]]:
    """Mensagens em ordem cronológica. Com `limit`, devolve as N mais recentes."""
    with _connect() as conn:
        if limit is None:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
            rows = list(reversed(rows))
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def conversation_exists(conversation_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return row is not None


def set_title(conversation_id: int, title: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title.strip()[:80] or "Nova conversa", conversation_id),
        )


def delete_conversation(conversation_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
