"""Ingest parsers: turn a raw export (Markdown transcript, Claude Code
session `.jsonl`, or ChatGPT `conversations.json`) into plain conversation
text ready for `vault.extract.extract`.

Each parser module (`markdown.py`, `claude_code.py`, `chatgpt.py`) exposes a
`parse(path) -> list[ParsedConversation]` function. A single file may contain
more than one conversation (ChatGPT exports always do; Markdown and Claude
Code sessions currently produce exactly one each, but the list return keeps
the three parsers uniform for the CLI).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ParsedConversation:
    """One conversation's worth of plain text, ready to scrub and extract.

    `text` never contains anything beyond what was in the source transcript
    itself -- no synthetic commentary -- since it is the thing `scrub.py`
    and `extract.py` operate on before anything reaches memory/.
    """

    text: str
    chat_id: str | None = None
    started_at: datetime | None = None
