"""Markdown mirror into the project's memory/ folder (PRD section 6.3).

Every write here is derived from stored facts/entities/episodes — never
from raw transcript text. Evidence fragments are already capped at 120
characters by the extraction schema before they reach this module.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from vault import db
from vault.models import count_tokens

PROFILE_BUDGET = 300


def _memory_dir(project_dir: Path) -> Path:
    return project_dir / "memory"


def ensure_layout(project_dir: Path) -> None:
    mem = _memory_dir(project_dir)
    for sub in ("semantic", "episodic", "procedural", "working"):
        (mem / sub).mkdir(parents=True, exist_ok=True)


def write_profile(conn: sqlite3.Connection, project_dir: Path) -> None:
    """Hot tier: identity and preferences for subject 'user', never sensitive."""
    ensure_layout(project_dir)
    rows = conn.execute(
        "SELECT f.* FROM facts f WHERE f.subject = 'user' AND f.sensitive = 0 "
        "AND f.superseded_by IS NULL ORDER BY f.observed_at DESC"
    ).fetchall()

    lines = ["# Profile", ""]
    budget_text = ""
    for row in rows:
        entity = db.get_entity(conn, row["entity_id"])
        entity_name = entity["name"] if entity else "?"
        line = f"- {row['predicate']} = {row['value']} ({entity_name})"
        candidate = budget_text + line + "\n"
        if count_tokens(candidate) > PROFILE_BUDGET:
            break
        lines.append(line)
        budget_text = candidate

    path = _memory_dir(project_dir) / "profile.md"
    path.write_text("\n".join(lines) + "\n")


def write_catalog(conn: sqlite3.Connection, project_dir: Path) -> None:
    ensure_layout(project_dir)
    entities = db.list_entities(conn)
    lines = ["# Catalog", ""]
    for e in entities:
        lines.append(f"- **{e['name']}** ({e['kind']}, {e['category']}): {e['description']}")
    path = _memory_dir(project_dir) / "catalog.md"
    path.write_text("\n".join(lines) + "\n")


def write_semantic(conn: sqlite3.Connection, project_dir: Path, entity_id: int) -> None:
    ensure_layout(project_dir)
    entity = db.get_entity(conn, entity_id)
    if entity is None:
        return
    current = db.current_facts_for_entity(conn, entity_id)
    history = db.history_for_entity(conn, entity_id)

    lines = [f"# {entity['name']}", "", entity["description"], "", "## Current facts", ""]
    if not current:
        lines.append("(none)")
    for f in current:
        marker = " (sensitive)" if f["sensitive"] else ""
        lines.append(f"- {f['predicate']} = {f['value']}{marker} — {f['observed_at']}")

    if history:
        lines += ["", "## History", ""]
        for f in history:
            lines.append(
                f"- {f['predicate']} = {f['value']} — superseded, was observed {f['observed_at']}"
            )

    path = _memory_dir(project_dir) / "semantic" / f"{entity['name']}.md"
    path.write_text("\n".join(lines) + "\n")


def write_episodic(
    conn: sqlite3.Connection, project_dir: Path, episode_id: int, source_kind: str
) -> Path:
    ensure_layout(project_dir)
    row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
    tags = json.loads(row["tags"] or "[]")
    entities = json.loads(row["entities"] or "[]")
    date = row["started_at"][:10]

    lines = [
        f"# Episode {date} ({source_kind})",
        "",
        row["summary"],
        "",
        f"Tags: {', '.join(tags) if tags else '(none)'}",
        f"Entities: {', '.join(entities) if entities else '(none)'}",
    ]
    path = _memory_dir(project_dir) / "episodic" / f"{date}-{source_kind}.md"
    existing = path.read_text() if path.exists() else ""
    block = "\n".join(lines) + "\n"
    separator = "\n---\n\n" if existing else ""
    path.write_text(existing + separator + block)
    return path


def write_procedural(conn: sqlite3.Connection, project_dir: Path) -> None:
    """Preferences and how-tos: facts whose predicate signals a preference."""
    ensure_layout(project_dir)
    rows = conn.execute(
        "SELECT f.* FROM facts f WHERE f.superseded_by IS NULL "
        "AND (f.predicate LIKE '%prefer%' OR f.predicate LIKE '%workflow%' "
        "OR f.predicate LIKE '%how_to%') ORDER BY f.observed_at DESC"
    ).fetchall()
    lines = ["# Preferences", ""]
    if not rows:
        lines.append("(none yet)")
    for row in rows:
        entity = db.get_entity(conn, row["entity_id"])
        entity_name = entity["name"] if entity else "?"
        lines.append(f"- {row['predicate']} = {row['value']} ({entity_name})")
    path = _memory_dir(project_dir) / "procedural" / "preferences.md"
    path.write_text("\n".join(lines) + "\n")


def write_scope(
    conn: sqlite3.Connection,
    project_dir: Path,
    scope: str | None,
    tokens: int,
    item_count: int,
) -> None:
    ensure_layout(project_dir)
    injections = db.recent_injections(conn, limit=5)
    lines = [
        "# Working scope",
        "",
        f"Active scope: {scope or '(none)'}",
        f"Last injection: {item_count} items, {tokens} tokens",
        "",
        "## Last five injections",
        "",
    ]
    for inj in injections:
        lines.append(
            f"- {inj['sent_at']} | scope={inj['scope'] or '(none)'} | "
            f"target={inj['target']} | {inj['tokens']} tokens"
        )
    path = _memory_dir(project_dir) / "working" / "scope.md"
    path.write_text("\n".join(lines) + "\n")


def append_progress(project_dir: Path, entry: str) -> None:
    path = project_dir / "PROGRESS.md"
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    line = f"- {timestamp} | {entry}\n"
    if not path.exists():
        path.write_text("# Progress log\n\n")
    with path.open("a") as fh:
        fh.write(line)
