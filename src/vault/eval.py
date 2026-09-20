"""Eval harness for Vault (PRD section 7.1, `vault eval`).

Reads `eval/golden.jsonl`, runs the gate and (when it fires) the full
retrieve pipeline for each row, and reports gate accuracy, recall@5,
precision@5 and token counts (mean, p95). Writes `eval/results.md` and
appends one `PROGRESS.md` line.

This module is deliberately import-light against `vault.retrieve`: that
package is owned by the retriever agent and may not exist yet, or may be
mid-edit, at the time this file is written or even at the time this
function first runs. Every call into it is wrapped so a missing module or
an unexpected return shape degrades a single row (or the whole run) into a
reported "not available" state instead of raising.

Contracts assumed from PRD section 8 / .claude/agents/retriever.md:
    gate.needs_memory(prompt, catalog_names, scope) -> bool
    scope.resolve_scope(conn, prompt, explicit_scope, chat_id) -> str | None
    scope.allow_sensitive(prompt) -> bool
    search.search(conn, query, scope_entity_id, limit=20, allow_sensitive=False)
        -> list[dict] each tagged with kind "fact" or "episode" (a
        (facts, episodes) tuple return is also accepted defensively)
    pack.pack(conn, profile_md, facts, episodes, budget=700, chat_id=None)
        -> (block: str, item_ids: list[str], tokens: int)

`expected_entity_ids` in the golden rows are entity *names* (e.g.
"acme-platform"), not raw autoincrement ids -- those aren't stable across
runs since they depend on the order the librarian's LLM-driven extraction
first creates entities. This module resolves names to ids at eval time via
`db.get_entity_by_name` and skips (does not crash on) any name not found in
the catalog yet.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any

from vault import db, mirror

try:
    from vault.retrieve import gate as _gate
    from vault.retrieve import pack as _pack
    from vault.retrieve import scope as _scope
    from vault.retrieve import search as _search

    RETRIEVE_AVAILABLE = True
    _IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - degrade gracefully, report instead of raising
    RETRIEVE_AVAILABLE = False
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


DEFAULT_GOLDEN = "eval/golden.jsonl"
DEFAULT_RESULTS = "eval/results.md"


def _load_golden(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


_STOPWORDS = {
    "a", "again", "am", "an", "and", "are", "about", "again", "ago", "all",
    "any", "as", "at", "be", "been", "but", "by", "did", "do", "does",
    "doing", "for", "from", "had", "has", "have", "how", "i", "if", "in",
    "is", "it", "its", "just", "like", "look", "me", "mine", "my", "now",
    "of", "on", "or", "our", "recently", "right", "said", "say", "so",
    "some", "still", "that", "the", "their", "there", "these", "they",
    "this", "time", "to", "was", "we", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "you", "your",
}


def _clean_query(prompt: str) -> str:
    """Turn a free-text prompt into an FTS5 query. FTS5's bareword MATCH is
    an implicit AND over every token, so passing the raw sentence (which is
    mostly function words no fact/episode text contains) matches nothing.
    Drop stopwords and OR the remaining significant terms instead, keeping
    the original words as a fallback for a very short/all-stopword prompt.
    """
    words = re.findall(r"[A-Za-z0-9']+", prompt)
    significant = [w for w in words if w.lower() not in _STOPWORDS]
    terms = significant or words
    return " OR ".join(terms)


def _row_keys(row: Any) -> set[str]:
    if hasattr(row, "keys"):
        return set(row.keys())
    return set()


def _split_results(results: Any) -> tuple[list[Any], list[Any]]:
    """Split search.search's combined, kind-tagged list into (facts,
    episodes). Also accepts a (facts, episodes) tuple, or untagged rows
    (falling back to a predicate/summary key heuristic), so this keeps
    working if the exact return shape shifts."""
    if isinstance(results, tuple) and len(results) == 2:
        a, b = results
        return list(a), list(b)
    facts: list[Any] = []
    episodes: list[Any] = []
    for row in results or []:
        keys = _row_keys(row)
        kind = row.get("kind") if hasattr(row, "get") else None
        if kind == "fact" or (kind is None and "predicate" in keys):
            facts.append(row)
        elif kind == "episode" or (kind is None and "summary" in keys):
            episodes.append(row)
    return facts, episodes


def _resolve_entity_id(conn, value: Any) -> int | None:
    if isinstance(value, int):
        return value
    row = db.get_entity_by_name(conn, str(value))
    return row["id"] if row else None


def _retrieved_entity_ids(conn, facts: list[Any], episodes: list[Any]) -> list[int]:
    ids: list[int] = []
    for f in facts:
        keys = _row_keys(f)
        if "entity_id" in keys and f["entity_id"] is not None:
            ids.append(f["entity_id"])
    for e in episodes:
        keys = _row_keys(e)
        if "entities" not in keys:
            continue
        ents = e["entities"]
        if isinstance(ents, str):
            try:
                ents = json.loads(ents)
            except (json.JSONDecodeError, TypeError):
                ents = []
        for name in ents or []:
            row = db.get_entity_by_name(conn, name)
            if row:
                ids.append(row["id"])
    return ids


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    frac = k - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def _safe_div(num: int, denom: int) -> float | None:
    return (num / denom) if denom else None


def run_eval(
    golden_path: str | Path = DEFAULT_GOLDEN,
    project_dir: str | Path = ".",
    vault_home: Path | None = None,
    budget: int = 700,
    results_path: str | Path = DEFAULT_RESULTS,
) -> dict[str, Any]:
    """Run the golden set and return the report dict (also written to
    `results_path` as Markdown and appended to PROGRESS.md)."""
    golden_path = Path(golden_path)
    project_dir = Path(project_dir)
    results_path = Path(results_path)

    rows = _load_golden(golden_path)
    report: dict[str, Any] = {
        "total_rows": len(rows),
        "retrieval_available": RETRIEVE_AVAILABLE,
        "gate_accuracy": None,
        "recall_at_5": None,
        "precision_at_5": None,
        "keyword_hit_rate": None,
        "tokens_mean": None,
        "tokens_p95": None,
        "scored_rows": 0,
        "errors": [],
    }
    if not RETRIEVE_AVAILABLE:
        report["errors"].append(
            f"src/vault/retrieve is not importable yet ({_IMPORT_ERROR}); "
            "gate/scope/search/pack were not exercised."
        )

    if not rows:
        report["errors"].append(f"no rows found in {golden_path}")
        _write_results(results_path, report)
        return report

    try:
        conn = db.connect(vault_home)
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"could not open vault db: {exc}")
        _write_results(results_path, report)
        return report

    if not RETRIEVE_AVAILABLE:
        _write_results(results_path, report)
        return report

    try:
        catalog_names = [row["name"] for row in db.list_entities(conn)]
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"could not read catalog: {exc}")
        catalog_names = []

    profile_md = ""
    profile_path = project_dir / "memory" / "profile.md"
    if profile_path.exists():
        profile_md = profile_path.read_text()

    gate_correct = 0
    gate_total = 0
    recalls: list[float] = []
    precisions: list[float] = []
    tokens: list[float] = []
    keyword_hits = 0
    keyword_total = 0
    scored_rows = 0

    for i, row in enumerate(rows):
        prompt = row.get("prompt", "")
        scope_arg = row.get("scope")
        expects_memory = bool(row.get("expects_memory"))
        expected_ids_raw = row.get("expected_entity_ids") or []
        expected_keywords = row.get("expected_keywords") or []

        try:
            fired = bool(_gate.needs_memory(prompt, catalog_names, scope_arg))
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"row {i}: gate.needs_memory failed: {exc}")
            continue

        gate_total += 1
        if fired == expects_memory:
            gate_correct += 1
        scored_rows += 1

        if not fired:
            continue

        try:
            resolved_scope = _scope.resolve_scope(conn, prompt, scope_arg, None)
            scope_entity_id = _resolve_entity_id(conn, resolved_scope) if resolved_scope else None
            allow_sens = _scope.allow_sensitive(prompt)
            results = _search.search(
                conn, _clean_query(prompt), scope_entity_id, allow_sensitive=allow_sens
            )
            facts, episodes = _split_results(results)
            block, _item_ids, used_tokens = _pack.pack(
                conn, profile_md, facts, episodes, budget=budget, chat_id=None
            )
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"row {i}: retrieve pipeline failed: {exc}")
            continue

        tokens.append(used_tokens)

        if expected_keywords:
            keyword_total += 1
            if any(kw.lower() in block.lower() for kw in expected_keywords):
                keyword_hits += 1

        if expected_ids_raw:
            expected_ids = {
                eid
                for eid in (_resolve_entity_id(conn, name) for name in expected_ids_raw)
                if eid is not None
            }
            if expected_ids:
                retrieved = _retrieved_entity_ids(conn, facts, episodes)
                top5 = set(retrieved[:5])
                hits = expected_ids & top5
                recalls.append(len(hits) / len(expected_ids))
                precisions.append(len(hits) / len(top5) if top5 else 0.0)

    report["gate_accuracy"] = _safe_div(gate_correct, gate_total)
    report["recall_at_5"] = _mean(recalls)
    report["precision_at_5"] = _mean(precisions)
    report["keyword_hit_rate"] = _safe_div(keyword_hits, keyword_total)
    report["tokens_mean"] = _mean(tokens)
    report["tokens_p95"] = _percentile(tokens, 95)
    report["scored_rows"] = scored_rows

    _write_results(results_path, report)
    _append_progress(project_dir, report)
    return report


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _write_results(results_path: Path, report: dict[str, Any]) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Eval results",
        "",
        f"Rows in golden set: {report['total_rows']}",
        f"Retrieval pipeline available: {report['retrieval_available']}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Gate accuracy | {_fmt(report['gate_accuracy'])} |",
        f"| Recall@5 | {_fmt(report['recall_at_5'])} |",
        f"| Precision@5 | {_fmt(report['precision_at_5'])} |",
        f"| Keyword hit rate (bonus) | {_fmt(report['keyword_hit_rate'])} |",
        f"| Tokens mean | {_fmt(report['tokens_mean'])} |",
        f"| Tokens p95 | {_fmt(report['tokens_p95'])} |",
        "",
    ]
    if report["errors"]:
        lines.append("## Notes / errors")
        lines.append("")
        for err in report["errors"]:
            lines.append(f"- {err}")
        lines.append("")
    results_path.write_text("\n".join(lines))


def _append_progress(project_dir: Path, report: dict[str, Any]) -> None:
    entry = (
        f"qa | eval: gate_accuracy={_fmt(report['gate_accuracy'])} "
        f"recall@5={_fmt(report['recall_at_5'])} precision@5={_fmt(report['precision_at_5'])} "
        f"tokens_mean={_fmt(report['tokens_mean'])} tokens_p95={_fmt(report['tokens_p95'])}"
    )
    try:
        mirror.append_progress(project_dir, entry)
    except Exception:  # noqa: BLE001 - eval results still stand without this
        pass
