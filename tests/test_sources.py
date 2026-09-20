"""Local chat-history sources (src/vault/sources.py).

Uses a synthetic Claude Code session file. The real ~/.claude/projects is
never touched: it holds the developer's actual conversations, and a test
suite has no business reading them.
"""

from __future__ import annotations

import json

import pytest

from vault import sources


def _session_file(path, lines):
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    (root / "-Users-someone-demo").mkdir(parents=True)
    _session_file(
        root / "-Users-someone-demo" / "s1.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "Decision: auth = Clerk."}},
            {"type": "assistant", "message": {"role": "assistant", "content": "Noted."}},
        ],
    )
    monkeypatch.setattr(sources, "CLAUDE_CODE_ROOT", root)
    return root


def test_detect_reports_sessions_when_present(fake_root):
    claude_code = next(s for s in sources.detect() if s["id"] == "claude_code")
    assert claude_code["available"] is True
    assert claude_code["sessions"] == 1
    assert claude_code["projects"] == 1


def test_detect_reports_unavailable_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "CLAUDE_CODE_ROOT", tmp_path / "nope")
    claude_code = next(s for s in sources.detect() if s["id"] == "claude_code")
    assert claude_code["available"] is False
    assert claude_code["sessions"] == 0


def test_detect_never_returns_conversation_text(fake_root):
    """The listing is counts and labels. Content only moves on ingest."""
    blob = json.dumps(sources.detect())
    assert "Clerk" not in blob
    assert "Decision" not in blob


def test_export_sources_are_upload_only():
    """Neither vendor exposes an API for reading chats, so these cannot be
    pulled -- the UI must send people to the export file instead."""
    for source_id in ("claude_export", "chatgpt_export"):
        entry = next(s for s in sources.detect() if s["id"] == source_id)
        assert entry["available"] is False
        assert entry["upload_only"] is True


def test_unknown_source_is_rejected(tmp_path):
    """Ids are looked up in a fixed table; a path can never be passed in."""
    with pytest.raises(KeyError):
        sources.ingest_source("../../etc/passwd", tmp_path, object())
