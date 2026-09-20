"""Parse a ChatGPT `conversations.json` export: a list of conversation
objects, each with a `mapping` of message-node-id -> node, into plain
conversation text per conversation.

Nodes are walked by `message.create_time` rather than the parent/children
tree, since floating branches (edited messages, regenerations) still sort
correctly by time and this avoids assuming a single linear path through the
tree.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from vault.ingest import ParsedConversation


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        parts = content.get("parts") or []
        pieces = [p for p in parts if isinstance(p, str) and p.strip()]
        return "\n".join(pieces)
    if isinstance(content, str):
        return content
    return ""


def _started_at(conversation: dict) -> datetime | None:
    create_time = conversation.get("create_time")
    if isinstance(create_time, int | float):
        try:
            return datetime.fromtimestamp(create_time)
        except (ValueError, OSError, OverflowError):
            return None
    return None


def _conversation_to_parsed(conversation: dict) -> ParsedConversation:
    mapping: dict[str, Any] = conversation.get("mapping") or {}
    ordered: list[tuple[float, str, str]] = []

    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author = message.get("author") or {}
        role = author.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _message_text(message).strip()
        if not text:
            continue
        create_time = message.get("create_time")
        sort_key = float(create_time) if isinstance(create_time, int | float) else 0.0
        ordered.append((sort_key, role, text))

    ordered.sort(key=lambda item: item[0])
    turns = [f"{'User' if role == 'user' else 'Assistant'}: {text}" for _, role, text in ordered]

    chat_id = conversation.get("conversation_id") or conversation.get("id")
    return ParsedConversation(
        text="\n\n".join(turns),
        chat_id=str(chat_id) if chat_id else None,
        started_at=_started_at(conversation),
    )


def parse(path: Path) -> list[ParsedConversation]:
    data = json.loads(Path(path).read_text())
    conversations = data if isinstance(data, list) else [data]
    return [
        _conversation_to_parsed(conv) for conv in conversations if isinstance(conv, dict)
    ]
