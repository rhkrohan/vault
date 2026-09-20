"""Dedupe and supersession (PRD section 6.2's supersession rule).

Given a validated `ExtractionResult` and an open db connection: each fact's
entity is resolved/inserted via `db.upsert_entity`, then compared against
the current fact (if any) for that `(entity_id, predicate)`. Same value
widens confidence in place; a different value inserts the new fact and
marks the old one superseded; no prior fact just inserts. The episode (if
present) is written via `db.insert_episode`.

Entities carry no `category` in the extraction schema (section 6.1), but
the `entities` table requires one (section 6.2). Judgment call: an entity
referenced by a fact takes that fact's category (facts are the source of
truth for "what kind of thing is this"); an entity that appears only in
`extraction.entities` with no fact falls back to a category guessed from
its `kind` (a "person"/"people" kind maps to the `people` category), else
`projects`.
"""

from __future__ import annotations

import sqlite3

from vault import db
from vault.models import ExtractedEntity, ExtractionResult


def _guess_category(kind: str) -> str:
    k = (kind or "").lower()
    if "person" in k or "people" in k:
        return "people"
    return "projects"


def apply_extraction(
    conn: sqlite3.Connection,
    extraction: ExtractionResult,
    source_id: int,
) -> dict[str, int | None]:
    entity_meta: dict[str, ExtractedEntity] = {e.name: e for e in extraction.entities}
    fact_category_by_entity: dict[str, str] = {}

    added = 0
    superseded = 0

    for fact in extraction.facts:
        fact_category_by_entity.setdefault(fact.entity, fact.category)
        meta = entity_meta.get(fact.entity)
        kind = meta.kind if meta else "topic"
        description = meta.description if meta else ""

        entity_id = db.upsert_entity(conn, fact.entity, kind, description, fact.category)
        current = db.get_current_fact(conn, entity_id, fact.predicate)

        if current is None:
            db.insert_fact(
                conn,
                entity_id=entity_id,
                subject=fact.subject,
                predicate=fact.predicate,
                value=fact.value,
                category=fact.category,
                sensitive=fact.sensitive,
                confidence=fact.confidence,
                source_id=source_id,
            )
            added += 1
        elif current["value"].strip() == fact.value.strip():
            db.bump_confidence(conn, current["id"], fact.confidence)
        else:
            new_id = db.insert_fact(
                conn,
                entity_id=entity_id,
                subject=fact.subject,
                predicate=fact.predicate,
                value=fact.value,
                category=fact.category,
                sensitive=fact.sensitive,
                confidence=fact.confidence,
                source_id=source_id,
            )
            db.supersede_fact(conn, current["id"], new_id)
            added += 1
            superseded += 1

    # Entities mentioned only in extraction.entities (no fact referenced
    # them) still belong in the catalog.
    for name, meta in entity_meta.items():
        if name in fact_category_by_entity:
            continue
        category = _guess_category(meta.kind)
        db.upsert_entity(conn, name, meta.kind, meta.description, category)

    episode_id: int | None = None
    if extraction.episode is not None:
        episode_id = db.insert_episode(
            conn,
            source_id=source_id,
            summary=extraction.episode.summary,
            category=extraction.episode.category,
            tags=extraction.episode.tags,
            entities=extraction.episode.entities,
        )

    return {"added": added, "superseded": superseded, "episode_id": episode_id}
