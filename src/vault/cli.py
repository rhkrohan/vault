"""Vault's Typer CLI (PRD section 7.1): ``vault ingest``, ``vault ask``,
``vault eval``, ``vault serve-mcp``, ``vault export``.

This module owns the wiring between the librarian (``ingest/``, ``scrub.py``,
``extract.py``, ``supersede.py``), the retriever (``retrieve/``), the store
(``db.py``, ``mirror.py``) and the eval harness (``eval.py``) -- it does not
reimplement any of their logic. ``mcp_server.py`` imports the private
helpers below (``_run_ask``, ``_run_ingest``, ``_remember_text``,
``_build_provider``) so the MCP tools run the exact same pipeline as the
CLI.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import typer

from vault import db, mirror
from vault.config import load_env
from vault.extract import extract
from vault.ingest import ParsedConversation
from vault.ingest import chatgpt as chatgpt_ingest
from vault.ingest import claude_code as claude_code_ingest
from vault.ingest import markdown as markdown_ingest
from vault.providers.base import Provider, ProviderError
from vault.providers.claude import ClaudeProvider
from vault.retrieve import gate, pack, scope, search
from vault.scrub import scrub
from vault.supersede import apply_extraction

# Read .env before anything below reads os.environ (DEFAULT_BUDGET does, at
# import time). A real environment variable still wins over the file.
load_env()

app = typer.Typer(add_completion=False, help="Local memory that follows you across models.")

SOURCE_MODULES = {
    "claude_code": claude_code_ingest,
    "chatgpt": chatgpt_ingest,
    "markdown": markdown_ingest,
}

_EXT_TO_SOURCE = {
    ".jsonl": "claude_code",
    ".json": "chatgpt",
    ".md": "markdown",
    ".markdown": "markdown",
}

DEFAULT_BUDGET = int(os.environ.get("VAULT_BUDGET", "700"))


# ---------------------------------------------------------------------------
# Provider selection (PRD section 10 / section 5's VAULT_PROVIDER)
# ---------------------------------------------------------------------------


def _build_provider() -> Provider:
    name = os.environ.get("VAULT_PROVIDER", "claude")
    if name == "claude":
        return ClaudeProvider()
    if name == "offline":
        from vault.providers.offline import OfflineProvider

        return OfflineProvider()
    if name == "runpod":
        try:
            from vault.providers.runpod import RunpodProvider
        except ImportError as exc:  # pragma: no cover - hosted branch not merged yet
            raise typer.Exit(
                code=1,
                message=(
                    "VAULT_PROVIDER=runpod but vault.providers.runpod is not "
                    "available in this build (Session B's 'hosted' branch has "
                    "not been merged)."
                ),
            ) from exc
        return RunpodProvider()
    raise typer.BadParameter(
        f"unknown VAULT_PROVIDER: {name!r} (expected claude, offline or runpod)"
    )


# ---------------------------------------------------------------------------
# Path allowlist (SAFETY / PRD section 9): ingest only reads inbox/,
# ~/.claude/projects and VAULT_HOME.
# ---------------------------------------------------------------------------


def _is_allowed_path(path: Path) -> bool:
    resolved = path.resolve()
    if "inbox" in resolved.parts:
        return True
    home_claude = (Path.home() / ".claude" / "projects").resolve()
    vault_home = db.default_vault_home().resolve()
    for allowed_root in (home_claude, vault_home):
        try:
            if resolved.is_relative_to(allowed_root):
                return True
        except ValueError:  # pragma: no cover - defensive, is_relative_to doesn't raise here
            continue
    return False


def _check_allowed(path: Path) -> None:
    if not _is_allowed_path(path):
        raise typer.BadParameter(
            f"{path} is outside the allowed read paths: inbox/, "
            "~/.claude/projects, VAULT_HOME (PRD section 9)"
        )


# ---------------------------------------------------------------------------
# Source detection and file discovery for `vault ingest`
# ---------------------------------------------------------------------------


def _sniff_source(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in _EXT_TO_SOURCE:
        return _EXT_TO_SOURCE[suffix]
    try:
        head = path.read_text(errors="ignore")[:2000].strip()
    except OSError:
        return None
    if not head:
        return None
    if head.startswith("{") or head.startswith("["):
        return "chatgpt" if '"mapping"' in head else "claude_code"
    if head.startswith("---") or head.startswith("#"):
        return "markdown"
    return None


def _iter_ingest_files(path: Path, source: str) -> list[tuple[Path, str]]:
    if path.is_file():
        kind = source if source != "auto" else _sniff_source(path)
        if kind is None:
            raise typer.BadParameter(f"cannot auto-detect a source for {path}; pass --source")
        return [(path, kind)]

    files: list[tuple[Path, str]] = []
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        kind = source if source != "auto" else _sniff_source(child)
        if kind is None:
            continue
        files.append((child, kind))
    return files


# ---------------------------------------------------------------------------
# Ingest pipeline: parse -> scrub (for counting) -> extract -> supersede ->
# mirror. Kept as a plain function (not a typer command) so it's directly
# testable with a fake provider and reusable from mcp_server.py.
# ---------------------------------------------------------------------------


def _mirror_touched(
    conn: db.sqlite3.Connection,
    project_dir: Path,
    touched_entity_names: set[str],
) -> None:
    mirror.write_profile(conn, project_dir)
    mirror.write_catalog(conn, project_dir)
    mirror.write_procedural(conn, project_dir)
    for name in touched_entity_names:
        row = db.get_entity_by_name(conn, name)
        if row is not None:
            mirror.write_semantic(conn, project_dir, row["id"])


def _apply_one_conversation(
    conn: db.sqlite3.Connection,
    convo: ParsedConversation,
    kind: str,
    path_label: str,
    provider: Provider,
    project_dir: Path,
    touched: set[str],
) -> dict[str, int]:
    """Scrub+extract+supersede+mirror one conversation. Returns per-item
    counters; raises ProviderError on an unrecoverable extraction failure."""
    _clean_text, redaction_counts = scrub(convo.text)

    source_id = db.insert_source(conn, kind=kind, path=path_label, chat_id=convo.chat_id)
    extraction = extract(convo.text, provider)
    result = apply_extraction(conn, extraction, source_id)

    touched.update(f.entity for f in extraction.facts)
    touched.update(e.name for e in extraction.entities)

    if result["episode_id"] is not None:
        mirror.write_episodic(conn, project_dir, result["episode_id"], kind)

    return {
        "facts_added": result["added"],
        "facts_superseded": result["superseded"],
        "episodes": 1 if result["episode_id"] is not None else 0,
        "secrets_redacted": sum(redaction_counts.values()),
        "conversations": 1,
    }


def _run_ingest(path: Path, source: str, project_dir: Path, provider: Provider) -> dict[str, int]:
    _check_allowed(path)
    conn = db.connect()

    files = _iter_ingest_files(path, source)
    if not files:
        raise typer.BadParameter(f"no ingestible files found under {path}")

    totals = {
        "facts_added": 0,
        "facts_superseded": 0,
        "episodes": 0,
        "secrets_redacted": 0,
        "conversations": 0,
    }
    touched: set[str] = set()
    failures: list[str] = []

    for file_path, kind in files:
        parser = SOURCE_MODULES[kind]
        conversations = parser.parse(file_path)
        for convo in conversations:
            if not convo.text.strip():
                continue
            try:
                counts = _apply_one_conversation(
                    conn, convo, kind, str(file_path), provider, project_dir, touched
                )
            except ProviderError as exc:
                failures.append(f"{file_path}: {exc}")
                continue
            for key, value in counts.items():
                totals[key] += value

    conn.commit()
    _mirror_touched(conn, project_dir, touched)

    entry = (
        f"vault ingest {path} | facts_added={totals['facts_added']} "
        f"superseded={totals['facts_superseded']} episodes={totals['episodes']} "
        f"secrets_redacted={totals['secrets_redacted']} "
        f"conversations={totals['conversations']}"
    )
    if failures:
        entry += f" failures={len(failures)}"
    mirror.append_progress(project_dir, entry)

    totals["failures"] = len(failures)
    totals["_failure_details"] = failures  # not printed as a count; used for messaging
    return totals


@app.command()
def ingest(
    path: Path = typer.Argument(..., exists=True, help="A file or a folder to ingest."),
    source: str = typer.Option(
        "auto", "--source", help="auto | claude_code | chatgpt | markdown"
    ),
    project: Path = typer.Option(
        Path("."), "--project", help="Project directory whose memory/ gets written."
    ),
) -> None:
    """Ingest one conversation export (or a folder of them) into the vault."""
    allowed_sources = {"auto", *SOURCE_MODULES}
    if source not in allowed_sources:
        raise typer.BadParameter(f"--source must be one of {', '.join(sorted(allowed_sources))}")

    provider = _build_provider()
    totals = _run_ingest(path, source, project, provider)

    typer.echo(f"Facts added: {totals['facts_added']}")
    typer.echo(f"Facts superseded: {totals['facts_superseded']}")
    typer.echo(f"Episodes: {totals['episodes']}")
    typer.echo(f"Secrets redacted: {totals['secrets_redacted']}")
    typer.echo(f"Conversations processed: {totals['conversations']}")
    if totals["failures"]:
        typer.secho(f"Extraction failures: {totals['failures']}", fg="yellow")
        for detail in totals["_failure_details"]:
            typer.secho(f"  - {detail}", fg="yellow")


# ---------------------------------------------------------------------------
# Ask pipeline: gate -> scope -> search -> pack -> log injection -> mirror
# working/scope.md. Also a plain function for the same reasons as _run_ingest.
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "and", "are", "about", "ago", "all", "am", "any", "as", "at",
    "be", "been", "but", "by", "did", "do", "does", "doing", "for", "from",
    "had", "has", "have", "how", "i", "if", "in", "is", "it", "its", "just",
    "like", "me", "mine", "my", "now", "of", "on", "or", "our", "recently",
    "right", "said", "say", "so", "some", "still", "that", "the", "their",
    "there", "these", "they", "this", "time", "to", "was", "we", "were",
    "what", "when", "where", "which", "who", "why", "will", "with", "you",
    "your",
}


def _prep_query(prompt: str) -> str:
    """Turn a free-text prompt into an FTS5 query. facts_fts/episodes_fts'
    bareword MATCH is an implicit AND across every token, so a raw sentence
    (mostly function words no fact/episode text contains) matches nothing;
    drop stopwords and OR the remaining significant terms instead."""
    words = re.findall(r"[A-Za-z0-9']+", prompt)
    significant = [w for w in words if w.lower() not in _STOPWORDS]
    terms = significant or words
    return " OR ".join(terms) if terms else prompt


def _run_ask(
    prompt: str,
    scope_arg: str | None,
    chat_id: str | None,
    budget: int,
    project_dir: Path,
    target: str = "cli",
) -> dict:
    conn = db.connect()
    catalog_names = [row["name"] for row in db.list_entities(conn)]
    fired = gate.needs_memory(prompt, catalog_names, scope_arg)

    profile_path = project_dir / "memory" / "profile.md"
    profile_md = profile_path.read_text() if profile_path.exists() else ""

    resolved_scope: str | None = None
    facts: list[dict] = []
    episodes: list[dict] = []

    if fired:
        resolved_scope = scope.resolve_scope(conn, prompt, scope_arg, chat_id)
        scope_entity_id = None
        if resolved_scope:
            row = db.get_entity_by_name(conn, resolved_scope)
            scope_entity_id = row["id"] if row else None
        allow_sens = scope.allow_sensitive(prompt)
        results = search.search(
            conn,
            _prep_query(prompt),
            scope_entity_id=scope_entity_id,
            allow_sensitive=allow_sens,
        )
        facts = [item for item in results if item["kind"] == "fact"]
        episodes = [item for item in results if item["kind"] == "episode"]

    block, item_ids, tokens = pack.pack(
        conn, profile_md, facts, episodes, budget=budget, chat_id=chat_id
    )

    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    db.insert_injection(
        conn,
        prompt_hash=prompt_hash,
        item_ids=item_ids,
        tokens=tokens,
        target=target,
        chat_id=chat_id,
        scope=resolved_scope,
    )
    conn.commit()

    mirror.write_scope(conn, project_dir, resolved_scope, tokens, len(item_ids))

    return {
        "block": block,
        "tokens": tokens,
        "item_ids": item_ids,
        "scope": resolved_scope,
        "gate_fired": fired,
    }


@app.command()
def ask(
    prompt: str = typer.Argument(..., help="The prompt to fetch context for."),
    scope: str | None = typer.Option(None, "--scope", help="An entity name to scope to."),
    chat: str | None = typer.Option(None, "--chat", help="A chat id for pinning/dedup."),
    budget: int = typer.Option(DEFAULT_BUDGET, "--budget", help="Token budget for the block."),
    project: Path = typer.Option(
        Path("."), "--project", help="Project directory whose memory/ is read/written."
    ),
) -> None:
    """Print the context block for a prompt, then a JSON line with its stats."""
    result = _run_ask(prompt, scope, chat, budget, project)
    typer.echo(result["block"])
    typer.echo(json.dumps({"tokens": result["tokens"], "item_ids": result["item_ids"]}))


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


@app.command()
def eval(  # noqa: A001 - CLI subcommand name from PRD 7.1 ("vault eval"); this
    # function only shadows the builtin `eval` within this module's
    # namespace and never calls it -- no code is evaluated here.
    golden: Path = typer.Option(Path("eval/golden.jsonl"), "--golden", help="Golden set path."),
    project: Path = typer.Option(Path("."), "--project", help="Project directory."),
    budget: int = typer.Option(DEFAULT_BUDGET, "--budget", help="Token budget for pack()."),
) -> None:
    """Run the golden-set eval and print the summary table."""
    from vault.eval import run_eval

    report = run_eval(golden_path=golden, project_dir=project, budget=budget)

    def fmt(value: object) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value)

    typer.echo(f"Rows: {report['total_rows']} (scored: {report['scored_rows']})")
    typer.echo(f"Gate accuracy:  {fmt(report['gate_accuracy'])}")
    typer.echo(f"Recall@5:       {fmt(report['recall_at_5'])}")
    typer.echo(f"Precision@5:    {fmt(report['precision_at_5'])}")
    typer.echo(f"Tokens mean:    {fmt(report['tokens_mean'])}")
    typer.echo(f"Tokens p95:     {fmt(report['tokens_p95'])}")
    typer.echo("Wrote eval/results.md")
    if report["errors"]:
        typer.secho("Notes:", fg="yellow")
        for err in report["errors"]:
            typer.secho(f"  - {err}", fg="yellow")


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------


@app.command(name="serve-mcp")
def serve_mcp() -> None:
    """Run the stdio MCP server (vault_catalog, vault_search, vault_get,
    vault_remember)."""
    import asyncio

    from vault.mcp_server import main as mcp_main

    asyncio.run(mcp_main())


# ---------------------------------------------------------------------------
# Remember: shared by the MCP tool `vault_remember`. Lives here so both
# entry points (CLI and MCP) run the same extract -> supersede -> mirror
# pipeline as `vault ingest`, just against raw text instead of a file.
# ---------------------------------------------------------------------------


def _remember_text(
    text: str, entity: str | None, project_dir: Path, provider: Provider
) -> dict:
    conn = db.connect()
    _clean_text, redaction_counts = scrub(text)

    source_id = db.insert_source(conn, kind="remember", path="mcp:vault_remember", chat_id=None)
    extraction = extract(text, provider)

    if entity:
        for fact in extraction.facts:
            if not fact.entity:
                fact.entity = entity

    result = apply_extraction(conn, extraction, source_id)
    conn.commit()

    touched = {f.entity for f in extraction.facts} | {e.name for e in extraction.entities}
    _mirror_touched(conn, project_dir, touched)

    entry = (
        f"vault_remember | facts_added={result['added']} superseded={result['superseded']} "
        f"secrets_redacted={sum(redaction_counts.values())}"
    )
    mirror.append_progress(project_dir, entry)

    return {
        "facts_added": result["added"],
        "facts_superseded": result["superseded"],
        "episode_id": result["episode_id"],
        "secrets_redacted": sum(redaction_counts.values()),
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@app.command()
def export(
    format: str = typer.Option("json", "--format", help="json | md"),
) -> None:
    """Dump the store."""
    if format not in {"json", "md"}:
        raise typer.BadParameter("--format must be json or md")

    conn = db.connect()

    if format == "json":
        payload = {
            "entities": [dict(row) for row in conn.execute("SELECT * FROM entities ORDER BY name")],
            "facts": [dict(row) for row in conn.execute("SELECT * FROM facts ORDER BY id")],
            "episodes": [dict(row) for row in conn.execute("SELECT * FROM episodes ORDER BY id")],
            "sources": [dict(row) for row in conn.execute("SELECT * FROM sources ORDER BY id")],
            "injections": [
                dict(row) for row in conn.execute("SELECT * FROM injections ORDER BY id")
            ],
        }
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    lines = ["# Vault export", ""]
    for entity in db.list_entities(conn):
        lines.append(f"## {entity['name']} ({entity['kind']}, {entity['category']})")
        if entity["description"]:
            lines.append(entity["description"])
        lines.append("")
        current = db.current_facts_for_entity(conn, entity["id"])
        if current:
            for fact in current:
                lines.append(f"- {fact['predicate']} = {fact['value']}")
        else:
            lines.append("(no current facts)")
        history = db.history_for_entity(conn, entity["id"])
        if history:
            lines.append("")
            lines.append("History:")
            for fact in history:
                lines.append(f"- {fact['predicate']} = {fact['value']} (superseded)")
        lines.append("")
    typer.echo("\n".join(lines))


if __name__ == "__main__":
    app()
