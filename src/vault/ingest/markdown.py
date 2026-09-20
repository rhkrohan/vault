"""Parse the `## User` / `## Assistant` Markdown transcripts used by
`inbox/synthetic/` (and by anyone hand-writing a transcript) into plain
conversation text.

Expected shape:

    ---
    date: 2026-09-12
    chat_id: synth-002
    ---

    ## User

    ...text...

    ## Assistant

    ...text...
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from vault.ingest import ParsedConversation

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(user|assistant)\s*$", re.IGNORECASE | re.MULTILINE)


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, raw[match.end() :]


def _parse_started_at(meta: dict[str, str]) -> datetime | None:
    raw_date = meta.get("date")
    if not raw_date:
        return None
    try:
        return datetime.fromisoformat(raw_date)
    except ValueError:
        return None


def parse_text(raw: str) -> ParsedConversation:
    """Parse one Markdown transcript's raw text into a ParsedConversation."""
    meta, body = _parse_frontmatter(raw)
    pieces = _HEADING_RE.split(body)
    # pieces[0] is any preamble before the first heading (discarded); after
    # that, re.split with a capturing group alternates [role, content, role, content, ...]
    turns: list[str] = []
    rest = iter(pieces[1:])
    for role, content in zip(rest, rest, strict=False):
        label = "User" if role.strip().lower() == "user" else "Assistant"
        content = content.strip()
        if content:
            turns.append(f"{label}: {content}")

    return ParsedConversation(
        text="\n\n".join(turns),
        chat_id=meta.get("chat_id") or None,
        started_at=_parse_started_at(meta),
    )


def parse(path: Path) -> list[ParsedConversation]:
    raw = Path(path).read_text()
    return [parse_text(raw)]
