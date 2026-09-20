"""Tests for the RunPod provider.

No network: every test patches ``httpx.post``. What is being pinned is the
contract PRD section 10 fixes -- the OpenAI-compatible route, bearer auth,
one retry, and a ProviderError that carries the *upstream* message so the
CLI and the web app can show a judge what actually broke.
"""

from __future__ import annotations

import json

import httpx
import pytest

from vault.providers import runpod as runpod_module
from vault.providers.base import EXTRACTION_SCHEMA, ProviderError
from vault.providers.runpod import RunpodProvider

GOOD_EXTRACTION = {
    "facts": [
        {
            "subject": "user",
            "predicate": "auth",
            "value": "Clerk",
            "entity": "acme-platform",
            "category": "projects",
            "sensitive": False,
            "confidence": 0.9,
            "evidence": "user chose Clerk for auth",
        }
    ],
    "entities": [{"name": "acme-platform", "kind": "project", "description": "A project."}],
    "episode": None,
}


@pytest.fixture
def provider() -> RunpodProvider:
    return RunpodProvider(api_key="rpa_test", endpoint_id="ep123", model="some/instruct-model")


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


class FakePost:
    """Stands in for httpx.post, replaying a queued list of outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def __call__(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(payload, status_code=200) -> httpx.Response:
    request = httpx.Request("POST", "https://api.runpod.ai/v2/ep123/openai/v1/chat/completions")
    if isinstance(payload, str):
        return httpx.Response(status_code, text=payload, request=request)
    return httpx.Response(status_code, json=payload, request=request)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_calls_the_openai_compatible_route_with_bearer_auth(provider, monkeypatch):
    fake = FakePost(_response(_completion(json.dumps(GOOD_EXTRACTION))))
    monkeypatch.setattr(runpod_module.httpx, "post", fake)

    provider.extract("## User\nhello", EXTRACTION_SCHEMA)

    call = fake.calls[0]
    assert call["url"] == "https://api.runpod.ai/v2/ep123/openai/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer rpa_test"
    assert call["timeout"] == runpod_module.TIMEOUT_SECONDS == 90
    assert call["json"]["model"] == "some/instruct-model"
    assert call["json"]["messages"][0]["role"] == "system"


def test_unconfigured_provider_names_the_missing_variables():
    with pytest.raises(ProviderError) as excinfo:
        RunpodProvider(api_key=None, endpoint_id=None, model=None).extract("x", EXTRACTION_SCHEMA)
    message = str(excinfo.value)
    assert "RUNPOD_API_KEY" in message and "RUNPOD_ENDPOINT_ID" in message


# ---------------------------------------------------------------------------
# Parsing an open-weight model's looser output
# ---------------------------------------------------------------------------


def test_parses_a_clean_json_reply(provider, monkeypatch):
    monkeypatch.setattr(
        runpod_module.httpx,
        "post",
        FakePost(_response(_completion(json.dumps(GOOD_EXTRACTION)))),
    )
    assert provider.extract("x", EXTRACTION_SCHEMA)["facts"][0]["value"] == "Clerk"


def test_parses_a_fenced_reply(provider, monkeypatch):
    fenced = "```json\n" + json.dumps(GOOD_EXTRACTION) + "\n```"
    monkeypatch.setattr(runpod_module.httpx, "post", FakePost(_response(_completion(fenced))))
    assert provider.extract("x", EXTRACTION_SCHEMA)["facts"][0]["value"] == "Clerk"


def test_parses_a_reply_with_a_prose_preamble(provider, monkeypatch):
    chatty = "Sure! Here is the JSON:\n" + json.dumps(GOOD_EXTRACTION)
    monkeypatch.setattr(runpod_module.httpx, "post", FakePost(_response(_completion(chatty))))
    assert provider.extract("x", EXTRACTION_SCHEMA)["facts"][0]["value"] == "Clerk"


# ---------------------------------------------------------------------------
# One retry, then ProviderError
# ---------------------------------------------------------------------------


def test_unparseable_reply_is_retried_once_and_then_succeeds(provider, monkeypatch):
    fake = FakePost(
        _response(_completion("I'm afraid I can't do that.")),
        _response(_completion(json.dumps(GOOD_EXTRACTION))),
    )
    monkeypatch.setattr(runpod_module.httpx, "post", fake)

    result = provider.extract("x", EXTRACTION_SCHEMA)

    assert result["facts"][0]["value"] == "Clerk"
    assert len(fake.calls) == 2
    # The retry restates the demand rather than repeating the same request.
    assert "JSON object only" in fake.calls[1]["json"]["messages"][-1]["content"]


def test_two_bad_replies_raise_provider_error(provider, monkeypatch):
    monkeypatch.setattr(
        runpod_module.httpx,
        "post",
        FakePost(_response(_completion("nope")), _response(_completion("still nope"))),
    )
    with pytest.raises(ProviderError, match="after one retry"):
        provider.extract("x", EXTRACTION_SCHEMA)


def test_upstream_error_message_reaches_the_caller(provider, monkeypatch):
    body = {"error": {"message": "endpoint ep123 has no workers available"}}
    monkeypatch.setattr(
        runpod_module.httpx,
        "post",
        FakePost(_response(body, status_code=503), _response(body, status_code=503)),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.extract("x", EXTRACTION_SCHEMA)
    assert "no workers available" in str(excinfo.value)


def test_timeout_is_reported_as_a_timeout_not_a_hang(provider, monkeypatch):
    timeout = httpx.TimeoutException("timed out")
    monkeypatch.setattr(runpod_module.httpx, "post", FakePost(timeout, timeout))
    with pytest.raises(ProviderError, match="timed out"):
        provider.extract("x", EXTRACTION_SCHEMA)


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_reports_ok_and_latency(provider, monkeypatch):
    monkeypatch.setattr(
        runpod_module.httpx, "post", FakePost(_response(_completion("pong")))
    )
    health = provider.health()
    assert health["ok"] is True
    assert health["endpoint_id"] == "ep123"
    assert "latency_ms" in health


def test_health_never_raises_when_the_endpoint_is_down(provider, monkeypatch):
    monkeypatch.setattr(
        runpod_module.httpx,
        "post",
        FakePost(httpx.ConnectError("connection refused")),
    )
    health = provider.health()
    assert health["ok"] is False
    assert "connection refused" in health["detail"]


def test_health_sends_only_one_token(provider, monkeypatch):
    fake = FakePost(_response(_completion("pong")))
    monkeypatch.setattr(runpod_module.httpx, "post", fake)
    provider.health()
    assert fake.calls[0]["json"]["max_tokens"] == 1
