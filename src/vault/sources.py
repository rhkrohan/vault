"""Detect and ingest the chat histories already sitting on this machine.

What this is *not*
------------------
There is no API that reads a person's claude.ai or chatgpt.com conversation
history on their behalf. Neither vendor offers an OAuth scope for it, so a
"connect my account and pull my chats" button could only be a scraper
driving a logged-in session -- against both products' terms, brittle, and
out of scope per PRD section 3 ("no browser extension").

What is actually available, and what this module wires up:

``claude_code``
    ``~/.claude/projects/**/*.jsonl`` -- every Claude Code session on this
    machine, already on disk in a documented format. PRD section 9 names
    this directory in the read allowlist, and ``ingest/claude_code.py``
    already parses it. This is a genuine one-click source.

``claude_export`` / ``chatgpt_export``
    The ``conversations.json`` each vendor emails you from
    Settings -> Export data. The sanctioned way to get web chat history,
    and the only one. Handled by the existing file upload rather than here,
    since the file lives wherever the person saved it.

Callers name a source by id. A path never crosses the wire: the roots are
fixed constants below, so this cannot be turned into an arbitrary file read
by a crafted request, and ``cli._run_ingest`` re-checks the allowlist anyway.
"""

from __future__ import annotations

from pathlib import Path

from vault.providers.base import Provider

# Fixed roots. Deliberately module constants, never request input.
CLAUDE_CODE_ROOT = Path.home() / ".claude" / "projects"


def _claude_code_files() -> list[Path]:
    if not CLAUDE_CODE_ROOT.is_dir():
        return []
    return sorted(CLAUDE_CODE_ROOT.rglob("*.jsonl"))


def detect() -> list[dict]:
    """Describe the sources present on this machine.

    Returns counts and labels only -- never conversation content. A source
    that is not present is reported with ``available: false`` and a reason,
    so the UI can explain itself instead of hiding a dead button.
    """
    files = _claude_code_files()
    projects = sorted({p.parent.name for p in files})
    return [
        {
            "id": "claude_code",
            "label": "Claude Code",
            "available": bool(files),
            "sessions": len(files),
            "projects": len(projects),
            "detail": (
                f"{len(files)} session files across {len(projects)} projects "
                f"in ~/.claude/projects"
                if files
                else "No ~/.claude/projects on this machine. This reads local "
                "Claude Code sessions, so it only appears where Claude Code runs."
            ),
        },
        {
            "id": "claude_export",
            "label": "Claude export",
            "available": False,
            "detail": (
                "claude.ai has no API for reading your chats. Request an export "
                "from Settings > Privacy > Export data, then upload the "
                "conversations.json above."
            ),
            "upload_only": True,
        },
        {
            "id": "chatgpt_export",
            "label": "ChatGPT export",
            "available": False,
            "detail": (
                "Same for ChatGPT: Settings > Data controls > Export data, then "
                "upload the conversations.json above."
            ),
            "upload_only": True,
        },
    ]


def ingest_source(source_id: str, project_dir: Path, provider: Provider) -> dict:
    """Ingest one named source. Raises KeyError for an unknown id."""
    from vault.cli import _run_ingest

    if source_id != "claude_code":
        raise KeyError(source_id)
    if not _claude_code_files():
        return {
            "facts_added": 0,
            "facts_superseded": 0,
            "episodes": 0,
            "secrets_redacted": 0,
            "conversations": 0,
            "note": "no Claude Code sessions found on this machine",
        }
    return _run_ingest(CLAUDE_CODE_ROOT, "claude_code", project_dir, provider)
