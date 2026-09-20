"""SQLite schema, FTS5 index maintenance and query primitives.

One SQLite file at VAULT_HOME/vault.db. WAL mode plus one connection per
request so the web app survives two users at once (section 6.2).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    chat_id TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    category TEXT NOT NULL,
    sensitive INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    observed_at TEXT NOT NULL,
    superseded_by INTEGER REFERENCES facts(id)
);

CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts(entity_id);
CREATE INDEX IF NOT EXISTS idx_facts_current ON facts(entity_id, predicate, superseded_by);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    summary TEXT NOT NULL,
    category TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    entities TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS injections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_hash TEXT NOT NULL,
    item_ids TEXT NOT NULL DEFAULT '[]',
    tokens INTEGER NOT NULL,
    target TEXT NOT NULL,
    chat_id TEXT,
    scope TEXT,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pins (
    chat_id TEXT PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    set_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    value, predicate, content=''
);

CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    summary, tags, content=''
);
"""


def default_vault_home() -> Path:
    raw = os.environ.get("VAULT_HOME", "~/.vault")
    return Path(raw).expanduser()


def connect(vault_home: Path | None = None) -> sqlite3.Connection:
    """Open one connection with WAL mode and the schema ensured."""
    home = vault_home or default_vault_home()
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "vault.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


def get_entity_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM entities WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()


def get_entity(conn: sqlite3.Connection, entity_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()


def upsert_entity(
    conn: sqlite3.Connection,
    name: str,
    kind: str,
    description: str,
    category: str,
) -> int:
    existing = get_entity_by_name(conn, name)
    now = _iso(datetime.now().astimezone())
    if existing:
        conn.execute(
            "UPDATE entities SET description = ?, kind = ?, updated_at = ? WHERE id = ?",
            (description or existing["description"], kind, now, existing["id"]),
        )
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO entities (name, kind, description, category, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, kind, description, category, now, now),
    )
    return cur.lastrowid


def list_entities(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM entities ORDER BY name").fetchall()


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def insert_source(
    conn: sqlite3.Connection, kind: str, path: str, chat_id: str | None = None
) -> int:
    now = _iso(datetime.now().astimezone())
    cur = conn.execute(
        "INSERT INTO sources (kind, path, chat_id, captured_at) VALUES (?, ?, ?, ?)",
        (kind, path, chat_id, now),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def get_current_fact(
    conn: sqlite3.Connection, entity_id: int, predicate: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM facts WHERE entity_id = ? AND predicate = ? "
        "AND superseded_by IS NULL ORDER BY observed_at DESC LIMIT 1",
        (entity_id, predicate),
    ).fetchone()


def insert_fact(
    conn: sqlite3.Connection,
    entity_id: int,
    subject: str,
    predicate: str,
    value: str,
    category: str,
    sensitive: bool,
    confidence: float,
    source_id: int,
    observed_at: datetime | None = None,
) -> int:
    observed = _iso((observed_at or datetime.now()).astimezone())
    cur = conn.execute(
        "INSERT INTO facts (entity_id, subject, predicate, value, category, sensitive, "
        "confidence, source_id, observed_at, superseded_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            entity_id,
            subject,
            predicate,
            value,
            category,
            1 if sensitive else 0,
            confidence,
            source_id,
            observed,
        ),
    )
    fact_id = cur.lastrowid
    conn.execute(
        "INSERT INTO facts_fts (rowid, value, predicate) VALUES (?, ?, ?)",
        (fact_id, value, predicate),
    )
    return fact_id


def supersede_fact(conn: sqlite3.Connection, old_fact_id: int, new_fact_id: int) -> None:
    conn.execute(
        "UPDATE facts SET superseded_by = ? WHERE id = ?", (new_fact_id, old_fact_id)
    )


def bump_confidence(conn: sqlite3.Connection, fact_id: int, confidence: float) -> None:
    now = _iso(datetime.now().astimezone())
    conn.execute(
        "UPDATE facts SET confidence = MAX(confidence, ?), observed_at = ? WHERE id = ?",
        (confidence, now, fact_id),
    )


def current_facts_for_entity(
    conn: sqlite3.Connection, entity_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM facts WHERE entity_id = ? AND superseded_by IS NULL "
        "ORDER BY observed_at DESC",
        (entity_id,),
    ).fetchall()


def history_for_entity(conn: sqlite3.Connection, entity_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM facts WHERE entity_id = ? AND superseded_by IS NOT NULL "
        "ORDER BY observed_at DESC",
        (entity_id,),
    ).fetchall()


def search_facts(
    conn: sqlite3.Connection, query: str, entity_id: int | None = None, limit: int = 20
) -> list[sqlite3.Row]:
    sql = (
        "SELECT f.*, bm25(facts_fts) AS rank FROM facts_fts "
        "JOIN facts f ON f.id = facts_fts.rowid "
        "WHERE facts_fts MATCH ? AND f.superseded_by IS NULL"
    )
    params: list = [query]
    if entity_id is not None:
        sql += " AND f.entity_id = ?"
        params.append(entity_id)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------


def insert_episode(
    conn: sqlite3.Connection,
    source_id: int,
    summary: str,
    category: str,
    tags: list[str],
    entities: list[str],
    started_at: datetime | None = None,
) -> int:
    started = _iso((started_at or datetime.now()).astimezone())
    cur = conn.execute(
        "INSERT INTO episodes (source_id, summary, category, tags, entities, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, summary, category, json.dumps(tags), json.dumps(entities), started),
    )
    episode_id = cur.lastrowid
    conn.execute(
        "INSERT INTO episodes_fts (rowid, summary, tags) VALUES (?, ?, ?)",
        (episode_id, summary, " ".join(tags)),
    )
    return episode_id


def recent_episodes(
    conn: sqlite3.Connection, entity_name: str | None = None, limit: int = 3
) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM episodes ORDER BY started_at DESC LIMIT ?", (limit * 5,)
    ).fetchall()
    if entity_name is None:
        return rows[:limit]
    out = []
    for row in rows:
        entities = json.loads(row["entities"] or "[]")
        if any(e.lower() == entity_name.lower() for e in entities):
            out.append(row)
        if len(out) >= limit:
            break
    return out


def search_episodes(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT e.*, bm25(episodes_fts) AS rank FROM episodes_fts "
        "JOIN episodes e ON e.id = episodes_fts.rowid "
        "WHERE episodes_fts MATCH ? ORDER BY rank LIMIT ?",
        (query, limit),
    ).fetchall()


# ---------------------------------------------------------------------------
# Injections and pins
# ---------------------------------------------------------------------------


def insert_injection(
    conn: sqlite3.Connection,
    prompt_hash: str,
    item_ids: list[str],
    tokens: int,
    target: str,
    chat_id: str | None,
    scope: str | None,
) -> int:
    now = _iso(datetime.now().astimezone())
    cur = conn.execute(
        "INSERT INTO injections (prompt_hash, item_ids, tokens, target, chat_id, scope, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (prompt_hash, json.dumps(item_ids), tokens, target, chat_id, scope, now),
    )
    return cur.lastrowid


def recent_injections(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM injections ORDER BY sent_at DESC LIMIT ?", (limit,)
    ).fetchall()


def logged_item_ids(conn: sqlite3.Connection, chat_id: str | None, limit: int = 5) -> set[str]:
    if not chat_id:
        return set()
    rows = conn.execute(
        "SELECT item_ids FROM injections WHERE chat_id = ? ORDER BY sent_at DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    seen: set[str] = set()
    for row in rows:
        seen.update(json.loads(row["item_ids"] or "[]"))
    return seen


def set_pin(conn: sqlite3.Connection, chat_id: str, entity_id: int, set_by: str) -> None:
    now = _iso(datetime.now().astimezone())
    conn.execute(
        "INSERT INTO pins (chat_id, entity_id, set_by, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET entity_id = excluded.entity_id, "
        "set_by = excluded.set_by, updated_at = excluded.updated_at",
        (chat_id, entity_id, set_by, now),
    )


def get_pin(conn: sqlite3.Connection, chat_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM pins WHERE chat_id = ?", (chat_id,)).fetchone()
