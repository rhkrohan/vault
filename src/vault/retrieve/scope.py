"""Scope resolution and sensitive-topic gating (PRD section 8).

Precedence for ``resolve_scope``: explicit scope argument, then an entity
named in the prompt, then the chat's pin, else None. Sensitive facts
(finances, health) are only allowed through when the prompt itself matches
one of the keyword lists below -- never as a side effect of a plain
project scope.
"""

from __future__ import annotations

import re
import sqlite3

from vault import db
from vault.retrieve.gate import mentions_entity

FINANCE_KEYWORDS = {
    "rent",
    "mortgage",
    "budget",
    "budgeting",
    "salary",
    "paycheck",
    "income",
    "expense",
    "expenses",
    "spending",
    "spend",
    "money",
    "finance",
    "finances",
    "financial",
    "bill",
    "bills",
    "debt",
    "loan",
    "loans",
    "savings",
    "invoice",
    "cost",
    "costs",
    "tax",
    "taxes",
}

HEALTH_KEYWORDS = {
    "sleep",
    "sleeping",
    "insomnia",
    "caffeine",
    "coffee",
    "health",
    "healthy",
    "doctor",
    "medication",
    "medicine",
    "therapy",
    "therapist",
    "exercise",
    "workout",
    "gym",
    "diet",
    "sick",
    "illness",
    "symptom",
    "symptoms",
    "anxiety",
    "stress",
    "mental",
}

_WORD_RE = re.compile(r"[a-z']+")


def allow_sensitive(prompt: str) -> bool:
    """True only when the prompt matches a finance or health keyword."""
    words = set(_WORD_RE.findall(prompt.lower()))
    return bool(words & (FINANCE_KEYWORDS | HEALTH_KEYWORDS))


def _resolve_catalog_name(conn: sqlite3.Connection, name: str) -> str | None:
    row = db.get_entity_by_name(conn, name)
    return row["name"] if row else name


def resolve_scope(
    conn: sqlite3.Connection,
    prompt: str,
    explicit_scope: str | None,
    chat_id: str | None,
) -> str | None:
    """Order: explicit scope, then an entity named in the prompt, then the
    chat's pin, else None."""
    if explicit_scope:
        return _resolve_catalog_name(conn, explicit_scope)

    catalog_names = [row["name"] for row in db.list_entities(conn)]
    mentioned = mentions_entity(prompt, catalog_names)
    if mentioned:
        return mentioned

    if chat_id:
        pin = db.get_pin(conn, chat_id)
        if pin:
            entity = db.get_entity(conn, pin["entity_id"])
            if entity:
                return entity["name"]

    return None
