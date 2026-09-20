"""Tests for the hosted-mode FastAPI app (PRD section 7.3).

Each test drives the app through Starlette's TestClient against a temporary
VAULT_HOME and VAULT_PROJECT, with the offline provider, so nothing touches
the network or the developer's real store.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from vault.providers.base import ProviderError

TRANSCRIPT = (
    "## User\nBack on acme-platform. Decision: auth = sessions for acme-platform.\n\n"
    "## Assistant\nNoted.\n"
)
REVISION = (
    "## User\nUpdate on acme-platform: we're going with Clerk for acme-platform auth.\n\n"
    "## Assistant\nNoted.\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VAULT_PROJECT", str(tmp_path / "project"))
    monkeypatch.setenv("VAULT_PROVIDER", "offline")

    # web.py reads VAULT_PROVIDER at request time but DEFAULT_BUDGET at
    # import time, so reload both modules under the patched environment.
    import vault.cli
    import vault.web

    importlib.reload(vault.cli)
    web = importlib.reload(vault.web)
    return TestClient(web.app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_reports_provider_db_path_and_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "offline"
    assert body["endpoint"]["ok"] is True
    assert body["db_path"].endswith("vault.db")
    assert body["version"]


def test_health_returns_503_when_the_provider_is_unreachable(client, monkeypatch):
    import vault.web

    class Dead:
        def health(self):
            return {"ok": False, "detail": "endpoint has no workers"}

    monkeypatch.setattr(vault.web, "_build_provider", lambda: Dead())
    response = client.get("/health")
    assert response.status_code == 503
    assert "no workers" in response.json()["endpoint"]["detail"]


# ---------------------------------------------------------------------------
# /ingest
# ---------------------------------------------------------------------------


def test_ingest_returns_the_four_counts(client):
    body = client.post("/ingest", json={"text": TRANSCRIPT}).json()
    assert body["facts_added"] >= 1
    assert set(body) == {"facts_added", "facts_superseded", "episodes", "secrets_redacted"}


def test_ingest_supersedes_a_changed_decision(client):
    client.post("/ingest", json={"text": TRANSCRIPT})
    body = client.post("/ingest", json={"text": REVISION}).json()
    assert body["facts_superseded"] == 1


def test_ingest_redacts_secrets_and_never_stores_them(client, tmp_path):
    secret = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8" * 3
    body = client.post(
        "/ingest", json={"text": f"## User\nDecision: auth = sessions. Key {secret}\n"}
    ).json()
    assert body["secrets_redacted"] >= 1

    leaked = [
        path
        for path in (tmp_path / "project").rglob("*")
        if path.is_file() and secret in path.read_text(errors="ignore")
    ]
    assert not leaked


def test_ingest_rejects_empty_input_with_a_message(client):
    response = client.post("/ingest", json={"text": "   "})
    assert response.status_code == 400
    assert response.json()["error"]


def test_ingest_rejects_an_oversized_document(client):
    import vault.web

    response = client.post("/ingest", json={"text": "x" * (vault.web.MAX_UPLOAD_BYTES + 1)})
    assert response.status_code == 413


def test_provider_failure_becomes_a_502_with_the_upstream_message(client, monkeypatch):
    import vault.web

    def boom(*args, **kwargs):
        raise ProviderError("endpoint ep123 timed out after 90s")

    monkeypatch.setattr(vault.web, "extract", boom)
    response = client.post("/ingest", json={"text": TRANSCRIPT})
    assert response.status_code == 502
    assert "timed out after 90s" in response.json()["error"]


# ---------------------------------------------------------------------------
# /ask
# ---------------------------------------------------------------------------


def test_ask_returns_a_block_within_budget(client):
    client.post("/ingest", json={"text": TRANSCRIPT})
    body = client.post(
        "/ask", json={"prompt": "what did we decide about auth?", "scope": "acme-platform"}
    ).json()
    assert "[Vault context" in body["block"]
    assert body["tokens"] <= 700
    assert body["scope"] == "acme-platform"


def test_ask_respects_a_smaller_budget(client):
    client.post("/ingest", json={"text": TRANSCRIPT})
    body = client.post(
        "/ask", json={"prompt": "what did we decide about auth?", "budget": 60}
    ).json()
    assert body["tokens"] <= 60


def test_ask_rejects_an_empty_prompt(client):
    assert client.post("/ask", json={"prompt": "  "}).status_code == 400


def test_scope_is_a_hard_filter_between_projects(client):
    client.post("/ingest", json={"text": TRANSCRIPT})
    client.post(
        "/ingest",
        json={"text": "## User\nOn north-star: sticking with SQLite for north-star.\n"},
    )
    body = client.post(
        "/ask", json={"prompt": "what is the datastore?", "scope": "north-star"}
    ).json()
    facts_section = body["block"].split("Facts (")[-1].split("Recent:")[0]
    assert "SQLite" in facts_section
    assert "sessions" not in facts_section


# ---------------------------------------------------------------------------
# /audit and /catalog
# ---------------------------------------------------------------------------


def test_every_ask_is_logged_to_the_audit_trail(client):
    client.post("/ingest", json={"text": TRANSCRIPT})
    client.post("/ask", json={"prompt": "what did we decide about auth?"})
    injections = client.get("/audit").json()["injections"]
    assert injections and injections[0]["target"] == "web"


def test_catalog_lists_ingested_entities(client):
    client.post("/ingest", json={"text": TRANSCRIPT})
    names = {row["name"] for row in client.get("/catalog").json()["entities"]}
    assert "acme-platform" in names


# ---------------------------------------------------------------------------
# The page itself
# ---------------------------------------------------------------------------


def test_dashboard_serves_a_self_contained_page(client):
    response = client.get("/")
    assert response.status_code == 200
    page = response.text
    assert "<title>Vault</title>" in page
    # No build step and no third-party origin: everything is inline.
    assert "<script" in page and "src=" not in page.split("<script")[1].split(">")[0]


def test_the_page_never_mentions_a_key_or_the_provider_url(client):
    page = client.get("/").text
    for forbidden in ("api.runpod.ai", "RUNPOD_API_KEY", "ANTHROPIC_API_KEY", "Bearer "):
        assert forbidden not in page
