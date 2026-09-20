import pytest

from vault import db
from vault.models import ExtractionResult
from vault.supersede import apply_extraction


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "home")


@pytest.fixture
def source_id(conn):
    return db.insert_source(conn, "markdown", "inbox/synthetic/t1.md")


def _extraction(facts, entities=None, episode=None) -> ExtractionResult:
    return ExtractionResult.model_validate(
        {"facts": facts, "entities": entities or [], "episode": episode}
    )


def test_new_entity_new_predicate_just_inserts(conn, source_id):
    extraction = _extraction(
        facts=[
            {
                "subject": "user",
                "predicate": "team",
                "value": "infra",
                "entity": "acme-platform",
                "category": "projects",
                "confidence": 0.9,
                "evidence": "moved to infra",
            }
        ],
        entities=[{"name": "acme-platform", "kind": "project", "description": "billing rebuild"}],
    )
    result = apply_extraction(conn, extraction, source_id)
    assert result["added"] == 1
    assert result["superseded"] == 0

    entity = db.get_entity_by_name(conn, "acme-platform")
    assert entity is not None
    current = db.current_facts_for_entity(conn, entity["id"])
    assert len(current) == 1
    assert current[0]["value"] == "infra"


def test_different_value_supersedes_old_fact(conn, source_id):
    entity_id = db.upsert_entity(conn, "acme-platform", "project", "d", "projects")
    db.insert_fact(
        conn, entity_id, "user", "auth", "sessions", "projects", False, 0.8, source_id
    )

    extraction = _extraction(
        facts=[
            {
                "subject": "user",
                "predicate": "auth",
                "value": "Clerk",
                "entity": "acme-platform",
                "category": "projects",
                "confidence": 0.9,
                "evidence": "switched to Clerk for SSO",
            }
        ]
    )
    result = apply_extraction(conn, extraction, source_id)
    assert result["added"] == 1
    assert result["superseded"] == 1

    current = db.current_facts_for_entity(conn, entity_id)
    assert len(current) == 1
    assert current[0]["value"] == "Clerk"

    history = db.history_for_entity(conn, entity_id)
    assert len(history) == 1
    assert history[0]["value"] == "sessions"


def test_same_value_bumps_confidence_no_added_no_superseded(conn, source_id):
    entity_id = db.upsert_entity(conn, "acme-platform", "project", "d", "projects")
    db.insert_fact(
        conn, entity_id, "user", "auth", "Clerk", "projects", False, 0.7, source_id
    )

    extraction = _extraction(
        facts=[
            {
                "subject": "user",
                "predicate": "auth",
                "value": "Clerk",
                "entity": "acme-platform",
                "category": "projects",
                "confidence": 0.95,
                "evidence": "confirmed Clerk again",
            }
        ]
    )
    result = apply_extraction(conn, extraction, source_id)
    assert result["added"] == 0
    assert result["superseded"] == 0

    current = db.current_facts_for_entity(conn, entity_id)
    assert len(current) == 1
    assert current[0]["confidence"] == 0.95
    assert db.history_for_entity(conn, entity_id) == []


def test_episode_is_inserted_when_present(conn, source_id):
    extraction = _extraction(
        facts=[],
        episode={
            "summary": "Decided to use Clerk for SSO.",
            "category": "projects",
            "tags": ["auth"],
            "entities": ["acme-platform"],
        },
    )
    result = apply_extraction(conn, extraction, source_id)
    assert result["episode_id"] is not None
    episodes = db.search_episodes(conn, "Clerk")
    assert len(episodes) == 1


def test_no_episode_returns_none(conn, source_id):
    extraction = _extraction(facts=[])
    result = apply_extraction(conn, extraction, source_id)
    assert result["episode_id"] is None


def test_sensitive_category_defaults_true_and_flows_through(conn, source_id):
    extraction = _extraction(
        facts=[
            {
                "subject": "user",
                "predicate": "rent",
                "value": "$2,400/month",
                "entity": "personal-finances",
                "category": "finances",
                "confidence": 0.8,
                "evidence": "rent increase",
            }
        ]
    )
    apply_extraction(conn, extraction, source_id)
    entity = db.get_entity_by_name(conn, "personal-finances")
    current = db.current_facts_for_entity(conn, entity["id"])
    assert current[0]["sensitive"] == 1
