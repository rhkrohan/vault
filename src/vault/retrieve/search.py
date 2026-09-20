"""FTS5 search over facts and episodes (PRD section 8).

``search`` returns a single list of dicts ranked by bm25, each tagged with
a ``kind`` of ``"fact"`` or ``"episode"``. Callers (the CLI / MCP server,
or ``pack``) split on ``kind`` to build the two lists ``pack.pack`` wants.
Current facts only; scope is a hard SQL filter applied before ranking
(inherited from ``db.search_facts``); ties break on newer ``observed_at``
(facts) / ``started_at`` (episodes) first.
"""

from __future__ import annotations

import json
import sqlite3

from vault import db


def _entity_name(conn: sqlite3.Connection, entity_id: int) -> str:
    row = db.get_entity(conn, entity_id)
    return row["name"] if row else "?"


# bm25 ranks are negative (smaller is better), so an explicitly worse-than-
# any-match sentinel keeps scope-fallback facts below every real FTS hit.
_FALLBACK_RANK = 1e6


def _fact_item(conn: sqlite3.Connection, row: sqlite3.Row, rank: float | None = None) -> dict:
    return {
        "kind": "fact",
        "id": f"fact:{row['id']}",
        "entity_id": row["entity_id"],
        "entity": _entity_name(conn, row["entity_id"]),
        "predicate": row["predicate"],
        "value": row["value"],
        "category": row["category"],
        "sensitive": bool(row["sensitive"]),
        "observed_at": row["observed_at"],
        "rank": row["rank"] if rank is None else rank,
    }


def _episode_item(row: sqlite3.Row) -> dict:
    return {
        "kind": "episode",
        "id": f"episode:{row['id']}",
        "summary": row["summary"],
        "category": row["category"],
        "tags": json.loads(row["tags"] or "[]"),
        "entities": json.loads(row["entities"] or "[]"),
        "started_at": row["started_at"],
        "rank": row["rank"],
    }


def search(
    conn: sqlite3.Connection,
    query: str,
    scope_entity_id: int | None = None,
    limit: int = 20,
    allow_sensitive: bool = False,
) -> list[dict]:
    """Search facts_fts and episodes_fts, merge and rank by bm25 (lower is
    better), scope applied as a hard filter before ranking. Newer
    observed_at/started_at breaks rank ties. Sensitive facts are dropped
    unless allow_sensitive=True (defense in depth on top of scope.py's
    allow_sensitive prompt check)."""
    query = (query or "").strip()
    if not query:
        return []

    try:
        fact_rows = db.search_facts(conn, query, entity_id=scope_entity_id, limit=limit)
    except sqlite3.OperationalError:
        fact_rows = []
    try:
        episode_rows = db.search_episodes(conn, query, limit=limit)
    except sqlite3.OperationalError:
        episode_rows = []

    if scope_entity_id is not None:
        entity = db.get_entity(conn, scope_entity_id)
        entity_name = entity["name"].lower() if entity else None
        if entity_name:
            episode_rows = [
                row
                for row in episode_rows
                if entity_name in [e.lower() for e in json.loads(row["entities"] or "[]")]
            ]
        else:
            episode_rows = []

    items = [_fact_item(conn, row) for row in fact_rows]

    # Scope is a hard filter applied *before* ranking (PRD section 8), so the
    # candidate set for a scoped ask is that entity's current facts and FTS
    # only orders them. When the prompt's terms match none of those values
    # -- "what's the architecture for acme-platform?" shares no token with
    # "auth = Clerk" -- returning nothing would be wrong: the user named the
    # entity, so its current facts are the answer. Ranked below real hits.
    if scope_entity_id is not None and not items:
        items = [
            _fact_item(conn, row, rank=_FALLBACK_RANK)
            for row in db.current_facts_for_entity(conn, scope_entity_id)
        ]

    if not allow_sensitive:
        items = [item for item in items if not item["sensitive"]]
    items += [_episode_item(row) for row in episode_rows]

    def timestamp(item: dict) -> str:
        return item["observed_at"] if item["kind"] == "fact" else item["started_at"]

    # bm25 rank: smaller is better. Two stable passes give rank ascending
    # as the primary key, with newer timestamps breaking ties, since
    # Python's sort is stable and preserves relative order from the first
    # pass among equal-rank items.
    items.sort(key=timestamp, reverse=True)
    items.sort(key=lambda item: item["rank"])
    return items[:limit]
