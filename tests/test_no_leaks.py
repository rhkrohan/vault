"""Cross-cutting safety test (PRD section 6.3, never cut per section 13):
no 12-word span from any file under inbox/synthetic/ may appear anywhere
under the memory/ folder mirror.py writes.

extract.py and supersede.py (the librarian's modules) may not be
importable yet when this test runs, so it does not depend on them. Instead
it inserts facts/episodes directly through db.py, using short paraphrased
"evidence" strings that mimic what an LLM extraction step would produce
(under 120 characters, never a verbatim transcript span), then runs
mirror.py exactly as the real ingest pipeline would and checks its output.
"""

from __future__ import annotations

import re
from pathlib import Path

from vault import db, mirror

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DIR = REPO_ROOT / "inbox" / "synthetic"

WORD_RE = re.compile(r"[A-Za-z0-9']+")
SPAN_LEN = 12


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def _spans(tokens: list[str], length: int = SPAN_LEN) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + length]) for i in range(len(tokens) - length + 1)}


def _forbidden_spans() -> set[tuple[str, ...]]:
    forbidden: set[tuple[str, ...]] = set()
    for path in sorted(SYNTHETIC_DIR.glob("*.md")):
        forbidden |= _spans(_tokens(path.read_text()))
    return forbidden


def _leaked_spans(text: str, forbidden: set[tuple[str, ...]]) -> list[tuple[str, ...]]:
    found = _spans(_tokens(text))
    return sorted(found & forbidden)


def _populate_vault(conn, project_dir: Path) -> None:
    """Insert facts/episodes that paraphrase the five synthetic transcripts,
    the way a real extraction pass would -- short evidence strings, never
    verbatim spans -- then run every mirror writer."""

    acme = db.upsert_entity(
        conn, "acme-platform", "project", "Billing dashboard rebuild.", "projects"
    )
    northstar = db.upsert_entity(
        conn, "north-star", "project", "Personal habit tracker side project.", "projects"
    )
    jordan = db.upsert_entity(
        conn, "Jordan Alvarez", "person", "Engineering manager, platform org.", "people"
    )
    finances = db.upsert_entity(
        conn, "personal-finances", "topic", "Personal budget notes.", "finances"
    )
    health = db.upsert_entity(conn, "health", "topic", "Sleep and caffeine tracking.", "health")

    src1 = db.insert_source(conn, "markdown", "inbox/synthetic/01-acme-kickoff.md", "synth-001")
    src2 = db.insert_source(
        conn, "markdown", "inbox/synthetic/02-acme-auth-first-decision.md", "synth-002"
    )
    src3 = db.insert_source(
        conn, "markdown", "inbox/synthetic/03-northstar-and-finance.md", "synth-003"
    )
    src4 = db.insert_source(
        conn, "markdown", "inbox/synthetic/04-acme-auth-revised.md", "synth-004"
    )
    src5 = db.insert_source(
        conn, "markdown", "inbox/synthetic/05-health-and-wrapup.md", "synth-005"
    )

    # team: search -> infra (supersession, mirrors transcript 1/2 -> 4)
    old_team = db.insert_fact(
        conn, acme, "user", "team", "search", "projects", False, 0.8, src1
    )
    new_team = db.insert_fact(
        conn, acme, "user", "team", "infra", "projects", False, 0.9, src4
    )
    db.supersede_fact(conn, old_team, new_team)

    # auth: sessions -> Clerk (the decision that changes between 2 and 4)
    old_auth = db.insert_fact(
        conn, acme, "user", "auth", "sessions", "projects", False, 0.85, src2
    )
    new_auth = db.insert_fact(
        conn, acme, "user", "auth", "Clerk", "projects", False, 0.95, src4
    )
    db.supersede_fact(conn, old_auth, new_auth)

    db.insert_fact(
        conn, acme, "user", "manager", "Jordan Alvarez", "work_school", False, 0.9, src1
    )

    db.insert_fact(conn, northstar, "user", "datastore", "SQLite", "projects", False, 0.85, src1)
    db.insert_fact(
        conn, northstar, "user", "status", "committed weekend project", "projects", False, 0.8, src3
    )
    db.insert_fact(
        conn, northstar, "user", "habit", "no coffee after 2pm", "health", True, 0.8, src5
    )

    db.insert_fact(
        conn,
        jordan,
        "user",
        "role",
        "engineering manager, platform org",
        "people",
        False,
        0.9,
        src1,
    )

    db.insert_fact(
        conn, finances, "user", "rent", "about $2400 per month", "finances", True, 0.9, src3
    )

    db.insert_fact(
        conn,
        health,
        "user",
        "sleep_issue",
        "poor sleep, caffeine timing",
        "health",
        True,
        0.8,
        src5,
    )

    episodes = [
        (src1, "Kicked off acme-platform rebuild; floated north-star as an early side idea.",
         "projects", ["kickoff", "architecture"], ["acme-platform", "north-star"]),
        (src2, "Chose server-side sessions for acme-platform auth while still on search.",
         "projects", ["auth", "decision"], ["acme-platform"]),
        (src3, "Committed to north-star as a real project; noted a rent increase.",
         "projects", ["northstar", "finance"], ["north-star", "personal-finances"]),
        (src4, "Reversed the auth call: acme-platform moves to Clerk for SSO, now on infra.",
         "projects", ["auth", "decision", "sso"], ["acme-platform"]),
        (src5, "Added a caffeine cutoff habit; confirmed Clerk migration approval from manager.",
         "health", ["health", "habit", "status"], ["north-star", "health", "Jordan Alvarez"]),
    ]
    episode_ids = []
    for source_id, summary, category, tags, entities in episodes:
        episode_ids.append(db.insert_episode(conn, source_id, summary, category, tags, entities))

    mirror.write_profile(conn, project_dir)
    mirror.write_catalog(conn, project_dir)
    for entity_id in (acme, northstar, jordan, finances, health):
        mirror.write_semantic(conn, project_dir, entity_id)
    for episode_id in episode_ids:
        mirror.write_episodic(conn, project_dir, episode_id, "markdown")
    mirror.write_procedural(conn, project_dir)
    mirror.write_scope(conn, project_dir, "acme-platform", 100, 1)
    mirror.append_progress(project_dir, "test | synthetic ingest for leak test | n/a")


def test_synthetic_transcripts_exist():
    files = sorted(SYNTHETIC_DIR.glob("*.md"))
    assert len(files) == 5, "expected the five synthetic transcripts from Phase 0"


def test_no_twelve_word_span_leaks_into_memory(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    conn = db.connect(tmp_path / "home")

    _populate_vault(conn, project_dir)

    forbidden = _forbidden_spans()
    assert forbidden, "sanity check: synthetic transcripts should yield some 12-word spans"

    mem_dir = project_dir / "memory"
    assert mem_dir.exists()

    checked_any = False
    for path in sorted(mem_dir.rglob("*")):
        if not path.is_file():
            continue
        checked_any = True
        text = path.read_text()
        leaked = _leaked_spans(text, forbidden)
        rel = path.relative_to(project_dir)
        assert not leaked, f"{rel} leaked transcript span(s): {leaked[:3]}"

    assert checked_any, "expected at least one file under memory/"


def test_no_twelve_word_span_leaks_into_progress(tmp_path):
    """PROGRESS.md is also derived-only per PRD section 6.3."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    conn = db.connect(tmp_path / "home")

    _populate_vault(conn, project_dir)

    forbidden = _forbidden_spans()
    progress = project_dir / "PROGRESS.md"
    assert progress.exists()
    leaked = _leaked_spans(progress.read_text(), forbidden)
    assert not leaked, f"PROGRESS.md leaked transcript span(s): {leaked[:3]}"
