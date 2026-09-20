"""RunPod provider (PRD section 10): extraction through a RunPod serverless
endpoint running an open-weight instruct model behind the vLLM worker.

The worker exposes an OpenAI-compatible chat completions route::

    https://api.runpod.ai/v2/<RUNPOD_ENDPOINT_ID>/openai/v1/chat/completions

authenticated with ``Authorization: Bearer $RUNPOD_API_KEY``. Confirm that
route against the RunPod console for the worker image you actually deploy
before relying on it -- ``deploy/README.md`` records which image this was
verified against.

Contract, identical to ``providers/claude.py`` so the two are swappable by
one env var: same system prompt, same schema, 90 second timeout, one retry,
then ``ProviderError`` carrying the upstream message so the CLI and the web
app can show the user what actually went wrong instead of hanging.
"""

from __future__ import annotations

import json
import os
import re
import time

import httpx

from vault.providers.base import SYSTEM_PROMPT, ProviderError

TIMEOUT_SECONDS = 90
MAX_TOKENS = 4096
API_ROOT = "https://api.runpod.ai/v2"


class RunpodProvider:
    name = "runpod"

    def __init__(
        self,
        api_key: str | None = None,
        endpoint_id: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("RUNPOD_API_KEY")
        self.endpoint_id = endpoint_id or os.environ.get("RUNPOD_ENDPOINT_ID")
        self.model = model or os.environ.get("RUNPOD_MODEL", "")

    # -- wiring ------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"{API_ROOT}/{self.endpoint_id}/openai/v1"

    def _require_config(self) -> None:
        missing = [
            name
            for name, value in (
                ("RUNPOD_API_KEY", self.api_key),
                ("RUNPOD_ENDPOINT_ID", self.endpoint_id),
                ("RUNPOD_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise ProviderError(f"runpod provider is not configured: {', '.join(missing)} not set")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict, timeout: int) -> dict:
        """One request. Raises ProviderError with the upstream message."""
        url = f"{self.base_url}/chat/completions"
        try:
            response = httpx.post(url, json=payload, headers=self._headers(), timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"runpod endpoint {self.endpoint_id} timed out after {timeout}s "
                "(a cold serverless worker can take a minute to boot)"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"runpod endpoint {self.endpoint_id} unreachable: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"runpod endpoint {self.endpoint_id} returned "
                f"{response.status_code}: {_upstream_detail(response)}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                f"runpod endpoint {self.endpoint_id} returned non-JSON: {response.text[:200]}"
            ) from exc

    # -- Provider protocol -------------------------------------------------

    def extract(self, conversation_text: str, schema: dict) -> dict:
        self._require_config()
        system = SYSTEM_PROMPT.format(schema=json.dumps(schema))
        payload = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            # Deterministic: extraction is not a creative task, and a stable
            # result keeps re-ingesting the same transcript idempotent.
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": conversation_text},
            ],
        }

        # One retry, per the contract. An open-weight model is likelier than
        # Claude to wrap the JSON in prose, so the retry restates the demand
        # rather than repeating an identical request.
        try:
            return _parse_json(_message_text(self._post(payload, TIMEOUT_SECONDS)))
        except (ProviderError, ValueError) as first_error:
            retry = dict(payload)
            retry["messages"] = [
                *payload["messages"],
                {
                    "role": "user",
                    "content": (
                        "That response could not be parsed as JSON "
                        f"({first_error}). Reply with the JSON object only: "
                        "no prose, no markdown fences."
                    ),
                },
            ]
            try:
                return _parse_json(_message_text(self._post(retry, TIMEOUT_SECONDS)))
            except (ProviderError, ValueError) as second_error:
                raise ProviderError(
                    f"runpod extraction failed after one retry: {second_error}"
                ) from second_error

    def health(self) -> dict:
        """One-token request; reports reachability and latency (section 7.3's
        /health needs this to say 'endpoint ok' without a real extraction)."""
        base = {"provider": self.name, "endpoint_id": self.endpoint_id, "model": self.model}
        try:
            self._require_config()
        except ProviderError as exc:
            return {**base, "ok": False, "detail": str(exc)}

        payload = {
            "model": self.model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        start = time.monotonic()
        try:
            self._post(payload, TIMEOUT_SECONDS)
        except ProviderError as exc:
            return {**base, "ok": False, "detail": str(exc)}
        return {**base, "ok": True, "latency_ms": int((time.monotonic() - start) * 1000)}


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _upstream_detail(response: httpx.Response) -> str:
    """The upstream error message, so the user sees the real cause."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
        if body.get("message"):
            return str(body["message"])
    return json.dumps(body)[:200]


def _message_text(body: dict) -> str:
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected chat completion shape: {json.dumps(body)[:200]}") from exc


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_json(text: str) -> dict:
    """Parse the model's reply, tolerating the fences and stray prose an
    open-weight model adds more often than Claude does."""
    text = (text or "").strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost {...} span, which survives a leading
    # "Here is the JSON:" preamble.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError(f"no JSON object in response: {text[:200]}")
