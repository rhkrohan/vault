"""Pydantic models for Vault: the extraction schema (section 6.1) and the
stored rows that mirror the SQLite schema (section 6.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "projects",
    "work_school",
    "people",
    "interests",
    "personal",
    "finances",
    "health",
]

SENSITIVE_CATEGORIES: set[str] = {"finances", "health"}

SourceKind = Literal["claude_code", "chatgpt", "markdown", "remember", "web"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def count_tokens(text: str) -> int:
    """Fixed rule from PRD section 5: no tokenizer dependency."""
    return len(text) // 4


# ---------------------------------------------------------------------------
# Extraction schema (section 6.1) — what a provider returns for one
# conversation. Providers must produce JSON matching this shape exactly.
# ---------------------------------------------------------------------------


class ExtractedFact(BaseModel):
    subject: str
    predicate: str
    value: str
    entity: str
    category: Category
    sensitive: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(max_length=120)

    def model_post_init(self, __context) -> None:
        if self.category in SENSITIVE_CATEGORIES:
            object.__setattr__(self, "sensitive", True)


class ExtractedEntity(BaseModel):
    name: str
    kind: str
    description: str


class ExtractedEpisode(BaseModel):
    summary: str
    category: Category
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)
    entities: list[ExtractedEntity] = Field(default_factory=list)
    episode: ExtractedEpisode | None = None


# ---------------------------------------------------------------------------
# Stored rows (section 6.2) — what lives in SQLite, with real integer ids
# and foreign keys resolved.
# ---------------------------------------------------------------------------


class Entity(BaseModel):
    id: int | None = None
    name: str
    kind: str
    description: str = ""
    category: Category
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Fact(BaseModel):
    id: int | None = None
    entity_id: int
    subject: str
    predicate: str
    value: str
    category: Category
    sensitive: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    source_id: int
    observed_at: datetime = Field(default_factory=utcnow)
    superseded_by: int | None = None


class Episode(BaseModel):
    id: int | None = None
    source_id: int
    summary: str
    category: Category
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utcnow)


class Source(BaseModel):
    id: int | None = None
    kind: SourceKind
    path: str
    chat_id: str | None = None
    captured_at: datetime = Field(default_factory=utcnow)


class Injection(BaseModel):
    id: int | None = None
    prompt_hash: str
    item_ids: list[str] = Field(default_factory=list)
    tokens: int
    target: str
    chat_id: str | None = None
    scope: str | None = None
    sent_at: datetime = Field(default_factory=utcnow)


class Pin(BaseModel):
    chat_id: str
    entity_id: int
    set_by: str
    updated_at: datetime = Field(default_factory=utcnow)
