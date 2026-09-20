import pytest
from pydantic import ValidationError

from vault.extract import extract


class _FakeProvider:
    """Fake Provider (never calls the real Claude API) that returns a fixed
    sequence of raw dicts, one per call, so we can drive retry behaviour."""

    name = "fake"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[str] = []

    def extract(self, conversation_text: str, schema: dict) -> dict:
        self.calls.append(conversation_text)
        return self._responses.pop(0)

    def health(self) -> dict:
        return {"provider": self.name, "ok": True}


VALID_RESPONSE = {
    "facts": [
        {
            "subject": "user",
            "predicate": "team",
            "value": "infra",
            "entity": "acme-platform",
            "category": "projects",
            "confidence": 0.9,
            "evidence": "moved to infra team",
        }
    ],
    "entities": [{"name": "acme-platform", "kind": "project", "description": "billing rebuild"}],
    "episode": None,
}

INVALID_RESPONSE = {"facts": [{"subject": "user"}]}  # missing required fields


def test_extract_valid_response_first_try():
    provider = _FakeProvider([VALID_RESPONSE])
    result = extract("User: I moved to the infra team.", provider)
    assert len(result.facts) == 1
    assert result.facts[0].entity == "acme-platform"
    assert len(provider.calls) == 1


def test_extract_retries_once_on_validation_error_then_succeeds():
    provider = _FakeProvider([INVALID_RESPONSE, VALID_RESPONSE])
    result = extract("User: I moved to the infra team.", provider)
    assert len(result.facts) == 1
    assert len(provider.calls) == 2
    # the retry input carries the validation error forward
    assert "error" in provider.calls[1].lower() or "invalid" in provider.calls[1].lower()


def test_extract_raises_after_second_failure():
    provider = _FakeProvider([INVALID_RESPONSE, INVALID_RESPONSE])
    with pytest.raises(ValidationError):
        extract("User: I moved to the infra team.", provider)
    assert len(provider.calls) == 2


def test_extract_scrubs_secrets_before_calling_provider():
    provider = _FakeProvider([VALID_RESPONSE])
    extract("User: my key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789", provider)
    assert "sk-ant" not in provider.calls[0]
    assert "[REDACTED:api_key]" in provider.calls[0]
