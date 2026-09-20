"""Tests for vault.retrieve.gate (PRD section 8): 10 yes-cases, 10 no-cases."""

from __future__ import annotations

from vault.retrieve.gate import needs_memory

CATALOG = ["acme-platform", "north-star", "Jordan Alvarez"]


# ---------------------------------------------------------------------------
# Yes -- needs memory
# ---------------------------------------------------------------------------


def test_yes_possessive_my():
    assert needs_memory("What did I say about my rent?", CATALOG, None) is True


def test_yes_possessive_our():
    assert needs_memory("What's our plan for the demo?", CATALOG, None) is True


def test_yes_possessive_mine():
    assert needs_memory("Is this project mine or the team's?", CATALOG, None) is True


def test_yes_catalog_entity_exact():
    assert needs_memory("How's acme-platform coming along?", CATALOG, None) is True


def test_yes_catalog_entity_fuzzy_one_edit():
    # "pltform" is a single deletion (missing the 'a') from "platform"
    assert needs_memory("Give me a status update on acme pltform.", CATALOG, None) is True


def test_yes_catalog_entity_person():
    assert needs_memory("Has Jordan Alvarez reviewed the doc yet?", CATALOG, None) is True


def test_yes_trigger_remember():
    assert needs_memory("Remember what we talked about last week?", CATALOG, None) is True


def test_yes_trigger_recall():
    assert needs_memory("Can you recall the auth decision?", CATALOG, None) is True


def test_yes_trigger_last_time():
    assert needs_memory("Last time we spoke about the schema.", CATALOG, None) is True


def test_yes_trigger_we_decided():
    assert needs_memory("I think we decided something about this already.", CATALOG, None) is True


def test_yes_scope_given():
    assert needs_memory("What's the status?", CATALOG, "acme-platform") is True


# ---------------------------------------------------------------------------
# No -- profile block only
# ---------------------------------------------------------------------------


def test_no_generic_question():
    assert needs_memory("What's the capital of France?", CATALOG, None) is False


def test_no_code_help():
    assert needs_memory("How do I write a for loop in Python?", CATALOG, None) is False


def test_no_math():
    assert needs_memory("What's 17 times 23?", CATALOG, None) is False


def test_no_unrelated_entity_name():
    assert needs_memory("Tell me about the history of Rome.", CATALOG, None) is False


def test_no_definition():
    assert needs_memory("Define eventual consistency.", CATALOG, None) is False


def test_no_recipe():
    assert needs_memory("Give me a recipe for banana bread.", CATALOG, None) is False


def test_no_weather():
    assert needs_memory("Is it going to rain tomorrow?", CATALOG, None) is False


def test_no_generic_advice():
    assert needs_memory("What's a good book on distributed systems?", CATALOG, None) is False


def test_no_similar_but_unrelated_word():
    # "star" alone shouldn't fuzzy-match "north-star" (too far, missing a word)
    assert needs_memory("I saw a shooting star last night.", CATALOG, None) is False


def test_no_empty_catalog():
    assert needs_memory("What time is it?", [], None) is False
