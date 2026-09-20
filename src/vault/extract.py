"""Runs extraction against the active provider (PRD section 6.1).

Scrubs the conversation text first (secrets never reach a provider, let
alone memory/), calls `provider.extract()` with the fixed
`providers.base.EXTRACTION_SCHEMA`, validates the response against
`ExtractionResult`, and retries once -- with the validation error appended
to the input -- on a bad response. Raises on a second failure.
"""

from __future__ import annotations

from pydantic import ValidationError

from vault.models import ExtractionResult
from vault.providers.base import EXTRACTION_SCHEMA
from vault.scrub import scrub


def extract(conversation_text: str, provider) -> ExtractionResult:
    clean_text, _redaction_counts = scrub(conversation_text)

    raw = provider.extract(clean_text, EXTRACTION_SCHEMA)
    try:
        return ExtractionResult.model_validate(raw)
    except ValidationError as exc:
        retry_text = (
            f"{clean_text}\n\n"
            "Your previous response failed schema validation with this error:\n"
            f"{exc}\n"
            "Return corrected JSON matching the schema exactly, nothing else."
        )
        raw_retry = provider.extract(retry_text, EXTRACTION_SCHEMA)
        return ExtractionResult.model_validate(raw_retry)
