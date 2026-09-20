"""Provider interface (PRD section 10). Local mode uses ClaudeProvider,
hosted mode uses RunpodProvider — same schema, swappable by one env var."""

from __future__ import annotations

from typing import Protocol


class ProviderError(Exception):
    """Raised when a provider fails after its retry, carrying the upstream message."""


class Provider(Protocol):
    name: str

    def extract(self, conversation_text: str, schema: dict) -> dict:
        """Run extraction on conversation_text, returning JSON matching schema."""
        ...

    def health(self) -> dict:
        """Return a small dict describing reachability and latency."""
        ...


EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {"type": "string"},
                    "entity": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "projects",
                            "work_school",
                            "people",
                            "interests",
                            "personal",
                            "finances",
                            "health",
                        ],
                    },
                    "sensitive": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string", "maxLength": 120},
                },
                "required": [
                    "subject",
                    "predicate",
                    "value",
                    "entity",
                    "category",
                    "confidence",
                    "evidence",
                ],
            },
        },
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "kind", "description"],
            },
        },
        "episode": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "category": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "entities": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "category"],
        },
    },
    "required": ["facts", "entities"],
}


SYSTEM_PROMPT = """You are the extraction stage of a personal memory system. \
Read the conversation and return ONLY JSON matching this schema, nothing else, \
no markdown fences, no commentary:

{schema}

Rules:
- Extract only durable facts: decisions, preferences, identity, project state. \
Skip small talk and one-off questions.
- "evidence" is a short paraphrase under 120 characters. Never quote the \
conversation verbatim and never include secrets (API keys, passwords, full \
card or account numbers).
- "entity" is the project, person, or topic the fact belongs to; reuse the \
same entity name for the same thing across facts.
- category is one of: projects, work_school, people, interests, personal, \
finances, health. Mark sensitive=true for anything in finances or health.
- If nothing durable is in the conversation, return \
{{"facts": [], "entities": [], "episode": null}}.
"""
