"""Claude provider (PRD section 10): Anthropic SDK, model from VAULT_MODEL."""

from __future__ import annotations

import json
import os
import time

from anthropic import Anthropic

from vault.providers.base import SYSTEM_PROMPT, ProviderError

DEFAULT_MODEL = "claude-sonnet-5"
TIMEOUT_SECONDS = 60


class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or os.environ.get("VAULT_MODEL", DEFAULT_MODEL)
        self._client: Anthropic | None = None

    def _get_client(self) -> Anthropic:
        if self._client is None:
            if not self.api_key:
                raise ProviderError("ANTHROPIC_API_KEY is not set")
            self._client = Anthropic(api_key=self.api_key, timeout=TIMEOUT_SECONDS)
        return self._client

    def extract(self, conversation_text: str, schema: dict) -> dict:
        client = self._get_client()
        system = SYSTEM_PROMPT.format(schema=json.dumps(schema))
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": conversation_text}],
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as ProviderError
            raise ProviderError(f"claude provider request failed: {exc}") from exc

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return _parse_json(text)

    def health(self) -> dict:
        if not self.api_key:
            return {"provider": self.name, "ok": False, "detail": "no api key"}
        try:
            client = self._get_client()
            start = time.monotonic()
            client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"provider": self.name, "ok": True, "latency_ms": latency_ms}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "ok": False, "detail": str(exc)}


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)
