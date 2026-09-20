"""A health or finance fact must be marked sensitive even when the extraction
model files it under a non-sensitive category (PRD section 9: sensitive
categories are gated in scope.py and never reach profile.md).

Qwen2.5-7B returned "sleep = badly" as category "personal" during the hosted
run, which left it unflagged and put it in the profile block.
"""

from vault.models import ExtractedFact, names_sensitive_topic


def _fact(predicate: str, value: str, category: str = "personal") -> ExtractedFact:
    return ExtractedFact(
        subject="user",
        predicate=predicate,
        value=value,
        entity="personal",
        category=category,
        confidence=0.9,
        evidence="paraphrase",
    )


def test_health_topic_is_sensitive_despite_personal_category():
    assert _fact("sleep", "badly").sensitive is True


def test_finance_topic_is_sensitive_despite_personal_category():
    assert _fact("rent", "1800 a month").sensitive is True


def test_value_alone_can_trigger_the_backstop():
    assert _fact("note", "started therapy in March").sensitive is True


def test_ordinary_project_fact_is_not_marked_sensitive():
    assert _fact("auth", "Clerk", category="projects").sensitive is False
    assert _fact("database", "Postgres", category="projects").sensitive is False


def test_declared_sensitive_category_still_wins():
    assert _fact("anything", "at all", category="health").sensitive is True


def test_names_sensitive_topic_matches_whole_words_only():
    assert names_sensitive_topic("sleep") is True
    # "billing" must not match the "bill" keyword.
    assert names_sensitive_topic("billing system") is False


def test_entity_name_alone_can_trigger_the_backstop():
    """The model files money facts under an entity named for the topic:
    entity "rent" with predicate "amount" and value "$2,400" mentions no
    keyword in either field, so the entity name has to be checked too."""
    fact = ExtractedFact(
        subject="user",
        predicate="amount",
        value="$2,400",
        entity="rent",
        category="personal",
        confidence=0.9,
        evidence="paraphrase",
    )
    assert fact.sensitive is True
