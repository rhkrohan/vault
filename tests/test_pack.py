"""Tests for vault.retrieve.pack: block shape, budget, and truncation order."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from vault import db
from vault.models import count_tokens
from vault.retrieve.pack import pack

PROFILE_MD = "# Profile\n\n- team = infra (acme-platform)\n- role = engineer (acme-platform)\n"


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


@pytest.fixture
def acme(conn):
    return db.upsert_entity(conn, "acme-platform", "project", "billing rebuild", "projects")


def _fact(entity_id, entity_name, predicate, value, fact_id=1, observed_at="2026-09-18T00:00:00"):
    return {
        "kind": "fact",
        "id": f"fact:{fact_id}",
        "entity_id": entity_id,
        "entity": entity_name,
        "predicate": predicate,
        "value": value,
        "category": "projects",
        "sensitive": False,
        "observed_at": observed_at,
    }


def _episode(summary, ep_id=1, started_at="2026-09-18T00:00:00"):
    return {
        "kind": "episode",
        "id": f"episode:{ep_id}",
        "summary": summary,
        "category": "projects",
        "tags": ["auth"],
        "entities": ["acme-platform"],
        "started_at": started_at,
    }


def test_block_shape_and_order(conn, acme):
    facts = [_fact(acme, "acme-platform", "team", "infra", fact_id=1)]
    episodes = [_episode("Evaluated JWT vs sessions vs Clerk; chose Clerk for SSO.", ep_id=1)]

    block, item_ids, tokens = pack(conn, PROFILE_MD, facts, episodes)

    lines = block.splitlines()
    assert lines[0].startswith("[Vault context | scope: acme-platform | ")
    assert lines[0].endswith(" tokens]")
    assert lines[1].startswith("Profile: ")
    assert "Facts (acme-platform):" in block
    assert "- team = infra" in block
    assert "Recent:" in block
    assert "- 2026-09-18: Evaluated JWT" in block

    # order: profile before facts before episodes
    assert block.index("Profile:") < block.index("Facts (")
    assert block.index("Facts (") < block.index("Recent:")

    assert item_ids == ["fact:1", "episode:1"]
    assert f"{len(item_ids)} items" in lines[0]
    assert tokens == count_tokens(block)


def test_profile_only_when_no_facts_or_episodes(conn):
    block, item_ids, tokens = pack(conn, PROFILE_MD, [], [])
    assert "Facts (" not in block
    assert "Recent:" not in block
    assert item_ids == []
    assert "scope: none" in block.splitlines()[0]
    assert tokens == count_tokens(block)


def test_caps_facts_at_ten_and_episodes_at_three(conn, acme):
    facts = [_fact(acme, "acme-platform", f"pred{i}", "value", fact_id=i) for i in range(15)]
    episodes = [_episode(f"episode number {i}", ep_id=i) for i in range(6)]

    _, item_ids, _ = pack(conn, PROFILE_MD, facts, episodes, budget=100_000)

    fact_ids = [i for i in item_ids if i.startswith("fact:")]
    episode_ids = [i for i in item_ids if i.startswith("episode:")]
    assert len(fact_ids) == 10
    assert len(episode_ids) == 3


def test_skips_already_logged_item_ids(conn, acme):
    db.insert_injection(conn, "hash1", ["fact:1"], 100, "test", "chat-1", "acme-platform")
    conn.commit()

    facts = [_fact(acme, "acme-platform", "team", "infra", fact_id=1)]
    _, item_ids, _ = pack(conn, PROFILE_MD, facts, [], chat_id="chat-1")
    assert item_ids == []


def test_respects_budget(conn, acme):
    facts = [_fact(acme, "acme-platform", f"pred{i}", "a fairly descriptive value here",
                    fact_id=i) for i in range(10)]
    episodes = [_episode("A very long episode summary " * 10, ep_id=i) for i in range(3)]

    block, item_ids, tokens = pack(conn, PROFILE_MD, facts, episodes, budget=150)
    assert tokens <= 150
    assert tokens == count_tokens(block)


def test_truncates_episode_before_dropping_fact(conn, acme):
    facts = [_fact(acme, "acme-platform", "team", "infra", fact_id=1)]
    long_summary = "A very long episode summary that goes on and on and on. " * 5
    episodes = [_episode(long_summary, ep_id=1)]

    # Budget tight enough to force truncation, but generous enough that the
    # single fact should never need to be dropped.
    block, item_ids, tokens = pack(conn, PROFILE_MD, facts, episodes, budget=90)

    assert "fact:1" in item_ids
    assert tokens <= 90
    # the episode line should have been shortened, not left verbatim
    assert long_summary.strip() not in block


def test_shows_was_history_for_superseded_predicate(conn, acme, source):
    old_id = db.insert_fact(conn, acme, "user", "team", "search", "projects", False, 0.8, source,
                             observed_at=datetime(2026, 9, 12))
    new_id = db.insert_fact(conn, acme, "user", "team", "infra", "projects", False, 0.9, source,
                             observed_at=datetime(2026, 9, 18))
    db.supersede_fact(conn, old_id, new_id)
    conn.commit()

    facts = [_fact(acme, "acme-platform", "team", "infra", fact_id=new_id)]
    block, _, _ = pack(conn, PROFILE_MD, facts, [])
    assert "team = infra (was search, 2026-09-12)" in block


def test_no_history_line_when_no_supersession(conn, acme):
    facts = [_fact(acme, "acme-platform", "auth", "Clerk", fact_id=1)]
    block, _, _ = pack(conn, PROFILE_MD, facts, [])
    assert "- auth = Clerk" in block
    assert "(was" not in block
