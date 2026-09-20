"""Tests for vault.retrieve.scope: precedence order and sensitive gating."""

from __future__ import annotations

import sqlite3

import pytest

from vault import db
from vault.retrieve.scope import allow_sensitive, resolve_scope


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(db.SCHEMA)
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def catalog(conn):
    acme = db.upsert_entity(conn, "acme-platform", "project", "billing rebuild", "projects")
    north = db.upsert_entity(conn, "north-star", "project", "habit tracker", "projects")
    jordan = db.upsert_entity(conn, "Jordan Alvarez", "person", "eng manager", "people")
    conn.commit()
    return {"acme-platform": acme, "north-star": north, "Jordan Alvarez": jordan}


# ---------------------------------------------------------------------------
# Precedence order
# ---------------------------------------------------------------------------


def test_explicit_scope_wins_over_everything(conn, catalog):
    db.set_pin(conn, "chat-1", catalog["north-star"], "user")
    scope = resolve_scope(conn, "tell me about north-star", "acme-platform", "chat-1")
    assert scope == "acme-platform"


def test_explicit_scope_normalizes_case(conn, catalog):
    scope = resolve_scope(conn, "anything", "ACME-PLATFORM", None)
    assert scope == "acme-platform"


def test_entity_named_in_prompt_wins_over_pin(conn, catalog):
    db.set_pin(conn, "chat-1", catalog["north-star"], "user")
    scope = resolve_scope(conn, "What's next for acme-platform?", None, "chat-1")
    assert scope == "acme-platform"


def test_falls_back_to_chat_pin(conn, catalog):
    db.set_pin(conn, "chat-1", catalog["north-star"], "user")
    scope = resolve_scope(conn, "What should I work on today?", None, "chat-1")
    assert scope == "north-star"


def test_no_scope_when_nothing_matches(conn, catalog):
    scope = resolve_scope(conn, "What should I work on today?", None, None)
    assert scope is None


def test_no_scope_when_pin_exists_for_other_chat(conn, catalog):
    db.set_pin(conn, "chat-1", catalog["north-star"], "user")
    scope = resolve_scope(conn, "What should I work on today?", None, "chat-2")
    assert scope is None


def test_entity_named_person(conn, catalog):
    scope = resolve_scope(conn, "Did Jordan Alvarez approve the design?", None, None)
    assert scope == "Jordan Alvarez"


# ---------------------------------------------------------------------------
# Sensitive gating
# ---------------------------------------------------------------------------


def test_allow_sensitive_true_for_finance_keyword():
    assert allow_sensitive("My rent just went up to $2,400.") is True


def test_allow_sensitive_true_for_health_keyword():
    assert allow_sensitive("I've been having trouble with sleep and caffeine.") is True


def test_allow_sensitive_false_for_plain_project_prompt():
    assert allow_sensitive("What's the status of acme-platform?") is False


def test_allow_sensitive_false_for_generic_prompt():
    assert allow_sensitive("What should I work on today?") is False
