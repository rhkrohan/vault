"""Tests for the qa-owned eval runner (src/vault/eval.py).

src/vault/retrieve (gate/scope/search/pack) belongs to the retriever agent
and may not exist yet in this checkout. These tests only cover the parts
of eval.py that don't depend on it: golden loading, graceful degradation
when the retrieve package is unavailable, and results.md formatting. If
retrieve becomes importable later, run_eval's scored path is exercised
implicitly by `vault eval` in Phase 3 (PRD section 12).
"""

from __future__ import annotations

import json

from vault import eval as vault_eval


def test_golden_file_has_25_rows():
    rows = vault_eval._load_golden(
        vault_eval.Path(vault_eval.DEFAULT_GOLDEN)
    )
    assert len(rows) == 25


def test_golden_rows_have_required_fields():
    rows = vault_eval._load_golden(vault_eval.Path(vault_eval.DEFAULT_GOLDEN))
    required = {"prompt", "scope", "expects_memory", "expected_entity_ids", "expected_keywords"}
    for row in rows:
        assert required.issubset(row.keys())
        assert isinstance(row["prompt"], str) and row["prompt"]
        assert isinstance(row["expects_memory"], bool)
        assert isinstance(row["expected_entity_ids"], list)
        assert isinstance(row["expected_keywords"], list)


def test_golden_covers_required_categories():
    rows = vault_eval._load_golden(vault_eval.Path(vault_eval.DEFAULT_GOLDEN))
    no_memory = [r for r in rows if not r["expects_memory"]]
    acme = [r for r in rows if r.get("scope") == "acme-platform"]
    northstar = [r for r in rows if r.get("scope") == "north-star"]
    finance = [
        r for r in rows if "rent" in r["expected_keywords"] or "budget" in r["expected_keywords"]
    ]
    health = [
        r
        for r in rows
        if "caffeine" in r["expected_keywords"] or "sleep" in r["expected_keywords"]
    ]
    changed_decision = [
        r
        for r in rows
        if "sessions" in r["expected_keywords"] and "Clerk" in r["expected_keywords"]
    ]

    assert no_memory, "need at least one gate=no row"
    assert acme, "need at least one acme-platform scoped row"
    assert northstar, "need at least one north-star scoped row"
    assert finance, "need at least one finance row"
    assert health, "need at least one health row"
    assert changed_decision, "need a row surfacing the transcript 2->4 decision change"


def test_run_eval_degrades_gracefully_without_retrieve(tmp_path):
    """Regardless of whether src/vault/retrieve is importable in this run,
    run_eval must not raise and must always write results.md."""
    results_path = tmp_path / "results.md"
    report = vault_eval.run_eval(
        golden_path=vault_eval.DEFAULT_GOLDEN,
        project_dir=tmp_path,
        vault_home=tmp_path / "home",
        results_path=results_path,
    )
    assert results_path.exists()
    assert report["total_rows"] == 25
    assert "retrieval_available" in report
    if not vault_eval.RETRIEVE_AVAILABLE:
        assert report["gate_accuracy"] is None
        assert any("retrieve" in e for e in report["errors"])


def test_run_eval_handles_missing_golden_file(tmp_path):
    results_path = tmp_path / "results.md"
    report = vault_eval.run_eval(
        golden_path=tmp_path / "does-not-exist.jsonl",
        project_dir=tmp_path,
        vault_home=tmp_path / "home",
        results_path=results_path,
    )
    assert report["total_rows"] == 0
    assert results_path.exists()


def test_percentile_and_mean_helpers():
    assert vault_eval._mean([]) is None
    assert vault_eval._mean([1.0, 3.0]) == 2.0
    assert vault_eval._percentile([], 95) is None
    assert vault_eval._percentile([5], 95) == 5
    assert vault_eval._percentile([1, 2, 3, 4, 5], 100) == 5


def test_split_results_accepts_tuple_and_row_lists():
    facts_in = [{"predicate": "auth", "value": "Clerk", "entity_id": 1}]
    episodes_in = [{"summary": "s", "entities": json.dumps(["acme-platform"])}]

    f, e = vault_eval._split_results((facts_in, episodes_in))
    assert f == facts_in
    assert e == episodes_in

    combined = facts_in + episodes_in
    f2, e2 = vault_eval._split_results(combined)
    assert f2 == facts_in
    assert e2 == episodes_in
