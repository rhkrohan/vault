"""Tests for vault.retrieve.search: FTS5 ranking, scope as a hard filter,
current-facts-only, and tie-breaking by newer observed_at."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from vault import db
from vault.retrieve.search import search


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
def source(conn):
    return db.insert_source(conn, "markdown", "inbox/synthetic/x.md")


def test_scope_is_a_hard_filter(conn, source):
    acme = db.upsert_entity(conn, "acme-platform", "project", "", "projects")
    north = db.upsert_entity(conn, "north-star", "project", "", "projects")
    db.insert_fact(conn, acme, "user", "auth", "Clerk", "projects", False, 0.9, source)
    db.insert_fact(conn, north, "user", "auth", "Clerk", "projects", False, 0.9, source)
    conn.commit()

    results = search(conn, "Clerk", scope_entity_id=acme)
    assert results
    assert all(r["kind"] != "fact" or r["entity_id"] == acme for r in results)


def test_current_facts_only(conn, source):
    acme = db.upsert_entity(conn, "acme-platform", "project", "", "projects")
    old_id = db.insert_fact(conn, acme, "user", "auth", "sessions", "projects", False, 0.8, source)
    new_id = db.insert_fact(conn, acme, "user", "auth", "Clerk", "projects", False, 0.9, source)
    db.supersede_fact(conn, old_id, new_id)
    conn.commit()

    results = search(conn, "sessions OR Clerk", scope_entity_id=acme)
    fact_ids = {r["id"] for r in results if r["kind"] == "fact"}
    assert f"fact:{new_id}" in fact_ids
    assert f"fact:{old_id}" not in fact_ids


def test_ties_broken_by_newer_observed_at(conn, source):
    acme = db.upsert_entity(conn, "acme-platform", "project", "", "projects")
    north = db.upsert_entity(conn, "north-star", "project", "", "projects")
    older = datetime(2026, 9, 10)
    newer = datetime(2026, 9, 18)
    old_id = db.insert_fact(
        conn, acme, "user", "note", "identical text here", "projects", False, 0.9, source,
        observed_at=older,
    )
    new_id = db.insert_fact(
        conn, north, "user", "note", "identical text here", "projects", False, 0.9, source,
        observed_at=newer,
    )
    conn.commit()

    results = [r for r in search(conn, "identical text here") if r["kind"] == "fact"]
    ids_in_order = [r["id"] for r in results]
    assert ids_in_order.index(f"fact:{new_id}") < ids_in_order.index(f"fact:{old_id}")


def test_sensitive_facts_excluded_by_default(conn, source):
    acme = db.upsert_entity(conn, "acme-platform", "project", "", "projects")
    db.insert_fact(conn, acme, "user", "rent", "2400", "finances", True, 0.9, source)
    conn.commit()

    results = search(conn, "2400", scope_entity_id=acme)
    assert results == []

    results_allowed = search(conn, "2400", scope_entity_id=acme, allow_sensitive=True)
    assert any(r["kind"] == "fact" and r["value"] == "2400" for r in results_allowed)


def test_episode_scope_filter(conn, source):
    acme = db.upsert_entity(conn, "acme-platform", "project", "", "projects")
    db.upsert_entity(conn, "north-star", "project", "", "projects")
    db.insert_episode(conn, source, "Chose Clerk for SSO on acme-platform", "projects",
                       ["auth"], ["acme-platform"])
    db.insert_episode(conn, source, "Sketched the north-star schema with Clerk mention",
                       "projects", ["schema"], ["north-star"])
    conn.commit()

    results = search(conn, "Clerk", scope_entity_id=acme)
    episodes = [r for r in results if r["kind"] == "episode"]
    assert len(episodes) == 1
    assert "acme-platform" in episodes[0]["entities"]


def test_empty_query_returns_empty(conn, source):
    assert search(conn, "", scope_entity_id=None) == []
    assert search(conn, "   ", scope_entity_id=None) == []


def test_limit_is_respected(conn, source):
    acme = db.upsert_entity(conn, "acme-platform", "project", "", "projects")
    base = datetime(2026, 9, 1)
    for i in range(25):
        db.insert_fact(
            conn, acme, "user", f"pred{i}", "widget", "projects", False, 0.9, source,
            observed_at=base + timedelta(days=i),
        )
    conn.commit()
    results = search(conn, "widget", scope_entity_id=acme, limit=5)
    assert len(results) == 5
