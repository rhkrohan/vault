"""Tests for the offline (rule-based) provider.

These pin the behaviour the demo and the no-key test runs depend on: the
five synthetic transcripts must yield the entities and the decision changes
PRD section 12 requires, and nothing the provider emits may be a verbatim
slice of the transcript (PRD section 6.3's hard rule).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vault.models import ExtractionResult
from vault.providers.base import EXTRACTION_SCHEMA
from vault.providers.offline import OfflineProvider

SYNTHETIC = Path(__file__).resolve().parents[1] / "inbox" / "synthetic"


@pytest.fixture
def provider() -> OfflineProvider:
    return OfflineProvider()


def _extract(provider: OfflineProvider, name: str) -> ExtractionResult:
    text = (SYNTHETIC / name).read_text()
    return ExtractionResult.model_validate(provider.extract(text, EXTRACTION_SCHEMA))


def _facts(result: ExtractionResult) -> dict[tuple[str, str], str]:
    return {(f.entity, f.predicate): f.value for f in result.facts}


# ---------------------------------------------------------------------------
# Schema conformance
# ---------------------------------------------------------------------------


def test_every_synthetic_transcript_validates(provider):
    for path in sorted(SYNTHETIC.glob("*.md")):
        result = ExtractionResult.model_validate(
            provider.extract(path.read_text(), EXTRACTION_SCHEMA)
        )
        assert result.entities, f"{path.name} produced no entities"


def test_health_reports_ok_without_network(provider):
    health = provider.health()
    assert health["ok"] is True
    assert health["provider"] == "offline"


def test_empty_text_yields_nothing(provider):
    result = ExtractionResult.model_validate(provider.extract("", EXTRACTION_SCHEMA))
    assert result.facts == []
    assert result.entities == []
    assert result.episode is None


# ---------------------------------------------------------------------------
# The facts PRD section 12 requires the synthetic set to cover
# ---------------------------------------------------------------------------


def test_transcript_1_finds_both_projects_and_the_person(provider):
    result = _extract(provider, "01-acme-kickoff.md")
    names = {e.name for e in result.entities}
    assert {"acme-platform", "north-star", "Jordan Alvarez"} <= names
    kinds = {e.name: e.kind for e in result.entities}
    assert kinds["Jordan Alvarez"] == "person"


def test_decision_changes_between_transcript_2_and_4(provider):
    """The supersession the definition of done checks by hand."""
    assert _facts(_extract(provider, "02-acme-auth-first-decision.md"))[
        ("acme-platform", "auth")
    ] == "sessions"
    assert _facts(_extract(provider, "04-acme-auth-revised.md"))[
        ("acme-platform", "auth")
    ] == "Clerk"


def test_team_change_also_supersedes(provider):
    assert _facts(_extract(provider, "02-acme-auth-first-decision.md"))[
        ("acme-platform", "team")
    ] == "search"
    assert _facts(_extract(provider, "04-acme-auth-revised.md"))[
        ("acme-platform", "team")
    ] == "infra"


def test_finance_fact_is_marked_sensitive(provider):
    result = _extract(provider, "03-northstar-and-finance.md")
    rent = [f for f in result.facts if f.predicate == "rent"]
    assert rent and rent[0].category == "finances" and rent[0].sensitive


def test_health_facts_are_marked_sensitive(provider):
    result = _extract(provider, "05-health-and-wrapup.md")
    health = [f for f in result.facts if f.category == "health"]
    assert health, "no health fact extracted"
    assert all(f.sensitive for f in health)


# ---------------------------------------------------------------------------
# Attribution: a fact belongs to the project actually under discussion
# ---------------------------------------------------------------------------


def test_facts_attach_to_the_project_in_context_not_the_most_mentioned(provider):
    """Transcript 1 discusses both projects; each fact goes to its own."""
    facts = _facts(_extract(provider, "01-acme-kickoff.md"))
    assert facts[("acme-platform", "team")] == "search"
    assert facts[("north-star", "datastore")] == "SQLite"


def test_compound_adjectives_are_not_mistaken_for_project_names(provider):
    result = _extract(provider, "02-acme-auth-first-decision.md")
    names = {e.name for e in result.entities}
    assert not names & {"hand-rolled", "third-party", "server-side", "part-time"}


# ---------------------------------------------------------------------------
# The no-leak guarantee, at the provider boundary (PRD section 6.3)
# ---------------------------------------------------------------------------


def _twelve_word_spans(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(words[i : i + 12]) for i in range(len(words) - 11)}


def test_no_twelve_word_span_of_a_transcript_survives_extraction(provider):
    for path in sorted(SYNTHETIC.glob("*.md")):
        source_spans = _twelve_word_spans(path.read_text())
        result = ExtractionResult.model_validate(
            provider.extract(path.read_text(), EXTRACTION_SCHEMA)
        )
        emitted = [f.evidence for f in result.facts]
        emitted += [e.description for e in result.entities]
        if result.episode:
            emitted.append(result.episode.summary)
        for chunk in emitted:
            assert not (_twelve_word_spans(chunk) & source_spans), (
                f"{path.name}: emitted text overlaps the transcript: {chunk!r}"
            )


def test_evidence_respects_the_120_character_cap(provider):
    for path in sorted(SYNTHETIC.glob("*.md")):
        result = ExtractionResult.model_validate(
            provider.extract(path.read_text(), EXTRACTION_SCHEMA)
        )
        assert all(len(f.evidence) <= 120 for f in result.facts)


def test_assistant_suggestions_are_not_recorded_as_user_facts(provider):
    """The assistant proposes SQLite in transcript 1; only the user's own
    turns may become facts, so nothing is attributed from its suggestion."""
    convo = (
        "## User\nJust saying hello.\n\n"
        "## Assistant\nI'd lean toward keeping Postgres for acme-platform.\n"
    )
    result = ExtractionResult.model_validate(provider.extract(convo, EXTRACTION_SCHEMA))
    assert result.facts == []
