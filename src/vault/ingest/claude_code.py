"""Parse a Claude Code session `.jsonl` file (one JSON object per line) into
plain conversation text.

Real session files vary in shape across Claude Code versions, so this parser
is deliberately loose: it looks for a role on each line (`type`, `role`, or
`message.role`) and text content under `message.content` or `content`,
handling both a plain string and a list of content blocks (taking `text`
blocks, ignoring `tool_use`/`tool_result` blocks that carry no plain text).
Lines that don't parse as JSON, or that carry no recognizable user/assistant
text, are skipped rather than raised on.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from vault.ingest import ParsedConversation


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for block in content:
            if isinstance(block, str):
                pieces.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                pieces.append(block["text"])
        return "\n".join(p for p in pieces if p)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
    return ""


def _role_and_content(obj: dict) -> tuple[str | None, Any]:
    message = obj.get("message") if isinstance(obj.get("message"), dict) else None
    role = obj.get("role")
    if role is None and message is not None:
        role = message.get("role")
    if role is None:
        role = obj.get("type")

    content = message.get("content") if message is not None else None
    if content is None:
        content = obj.get("content")
    return role, content


def _parse_timestamp(raw: Any) -> datetime | None:
    if isinstance(raw, int | float):
        try:
            return datetime.fromtimestamp(raw)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def parse(path: Path) -> list[ParsedConversation]:
    turns: list[str] = []
    chat_id: str | None = None
    started_at: datetime | None = None

    for raw_line in Path(path).read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        chat_id = chat_id or obj.get("sessionId") or obj.get("session_id") or obj.get("chat_id")
        if started_at is None:
            started_at = _parse_timestamp(obj.get("timestamp") or obj.get("created_at"))

        role, content = _role_and_content(obj)
        if role not in ("user", "assistant"):
            continue
        text = _extract_text(content).strip()
        if not text:
            continue
        label = "User" if role == "user" else "Assistant"
        turns.append(f"{label}: {text}")

    return [ParsedConversation(text="\n\n".join(turns), chat_id=chat_id, started_at=started_at)]
