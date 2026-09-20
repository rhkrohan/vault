from pathlib import Path

import pytest

from vault import db, mirror


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "home")


def test_schema_creates_all_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    for expected in ["entities", "sources", "facts", "episodes", "injections", "pins"]:
        assert expected in tables


def test_entity_upsert_is_idempotent(conn):
    id1 = db.upsert_entity(conn, "acme-platform", "project", "the platform", "projects")
    id2 = db.upsert_entity(conn, "acme-platform", "project", "updated desc", "projects")
    assert id1 == id2
    entity = db.get_entity(conn, id1)
    assert entity["description"] == "updated desc"


def test_fact_insert_and_fts5_search(conn):
    entity_id = db.upsert_entity(conn, "acme-platform", "project", "d", "projects")
    source_id = db.insert_source(conn, "markdown", "inbox/synthetic/t1.md")
    db.insert_fact(
        conn,
        entity_id=entity_id,
        subject="user",
        predicate="team",
        value="infra",
        category="projects",
        sensitive=False,
        confidence=0.9,
        source_id=source_id,
    )
    results = db.search_facts(conn, "infra")
    assert len(results) == 1
    assert results[0]["value"] == "infra"


def test_supersession_marks_old_row_and_current_query_returns_new(conn):
    entity_id = db.upsert_entity(conn, "acme-platform", "project", "d", "projects")
    source_id = db.insert_source(conn, "markdown", "inbox/synthetic/t1.md")

    old_id = db.insert_fact(
        conn, entity_id, "user", "team", "search", "projects", False, 0.8, source_id
    )
    new_id = db.insert_fact(
        conn, entity_id, "user", "team", "infra", "projects", False, 0.9, source_id
    )
    db.supersede_fact(conn, old_id, new_id)

    current = db.current_facts_for_entity(conn, entity_id)
    assert len(current) == 1
    assert current[0]["value"] == "infra"

    history = db.history_for_entity(conn, entity_id)
    assert len(history) == 1
    assert history[0]["value"] == "search"


def test_same_value_bumps_confidence_without_creating_history(conn):
    entity_id = db.upsert_entity(conn, "acme-platform", "project", "d", "projects")
    source_id = db.insert_source(conn, "markdown", "inbox/synthetic/t1.md")
    fact_id = db.insert_fact(
        conn, entity_id, "user", "team", "infra", "projects", False, 0.7, source_id
    )
    db.bump_confidence(conn, fact_id, 0.95)

    current = db.current_facts_for_entity(conn, entity_id)
    assert len(current) == 1
    assert current[0]["confidence"] == 0.95
    assert db.history_for_entity(conn, entity_id) == []


def test_episode_insert_and_fts5_search(conn):
    source_id = db.insert_source(conn, "markdown", "inbox/synthetic/t1.md")
    db.insert_episode(
        conn,
        source_id=source_id,
        summary="Evaluated JWT vs sessions vs Clerk; chose Clerk for SSO.",
        category="projects",
        tags=["auth", "clerk"],
        entities=["acme-platform"],
    )
    results = db.search_episodes(conn, "Clerk")
    assert len(results) == 1


def test_wal_mode_enabled(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_mirror_writes_expected_layout(tmp_path, conn):
    project_dir: Path = tmp_path / "project"
    project_dir.mkdir()

    entity_id = db.upsert_entity(conn, "acme-platform", "project", "d", "projects")
    source_id = db.insert_source(conn, "markdown", "inbox/synthetic/t1.md")
    db.insert_fact(
        conn, entity_id, "user", "team", "infra", "projects", False, 0.9, source_id
    )
    episode_id = db.insert_episode(
        conn, source_id, "Summary text.", "projects", ["auth"], ["acme-platform"]
    )

    mirror.write_profile(conn, project_dir)
    mirror.write_catalog(conn, project_dir)
    mirror.write_semantic(conn, project_dir, entity_id)
    mirror.write_episodic(conn, project_dir, episode_id, "markdown")
    mirror.write_procedural(conn, project_dir)
    mirror.write_scope(conn, project_dir, "acme-platform", 100, 1)
    mirror.append_progress(project_dir, "test | did a thing | next step")

    mem = project_dir / "memory"
    assert (mem / "profile.md").exists()
    assert (mem / "catalog.md").exists()
    assert "acme-platform" in (mem / "catalog.md").read_text()
    assert (mem / "semantic" / "acme-platform.md").exists()
    assert "infra" in (mem / "semantic" / "acme-platform.md").read_text()
    episodic_files = list((mem / "episodic").glob("*.md"))
    assert len(episodic_files) == 1
    assert (mem / "procedural" / "preferences.md").exists()
    assert (mem / "working" / "scope.md").exists()
    assert (project_dir / "PROGRESS.md").exists()


def test_no_sensitive_facts_in_profile(tmp_path, conn):
    project_dir: Path = tmp_path / "project"
    project_dir.mkdir()
    entity_id = db.upsert_entity(conn, "me", "person", "d", "health")
    source_id = db.insert_source(conn, "markdown", "inbox/synthetic/t1.md")
    db.insert_fact(
        conn, entity_id, "user", "condition", "secret-health-fact", "health", True, 0.9, source_id
    )
    mirror.write_profile(conn, project_dir)
    profile_text = (project_dir / "memory" / "profile.md").read_text()
    assert "secret-health-fact" not in profile_text
