import json
from pathlib import Path

from vault.ingest import chatgpt, claude_code, markdown

MARKDOWN_FIXTURE = """---
date: 2026-09-12
chat_id: synth-002
---

## User

Back on acme-platform. We need to decide on authentication.

## Assistant

Hand-rolled sessions are the least new surface area for now.

## User

Let's go with sessions.
"""


def test_markdown_parses_frontmatter_and_turns(tmp_path):
    path = tmp_path / "t.md"
    path.write_text(MARKDOWN_FIXTURE)
    conversations = markdown.parse(path)
    assert len(conversations) == 1
    convo = conversations[0]
    assert convo.chat_id == "synth-002"
    assert convo.started_at is not None
    assert convo.started_at.year == 2026
    assert "User: Back on acme-platform" in convo.text
    assert "Assistant: Hand-rolled sessions" in convo.text
    assert convo.text.count("User:") == 2
    assert convo.text.count("Assistant:") == 1


def test_markdown_parses_real_synthetic_transcript():
    synthetic_dir = Path(__file__).resolve().parents[1] / "inbox" / "synthetic"
    files = sorted(synthetic_dir.glob("*.md"))
    assert files, "expected synthetic transcripts to exist"
    for f in files:
        conversations = markdown.parse(f)
        assert len(conversations) == 1
        assert conversations[0].text.strip() != ""
        assert "User:" in conversations[0].text


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines))


def test_claude_code_parses_string_content(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "sessionId": "abc-123",
                "timestamp": "2026-09-12T10:00:00Z",
                "message": {"role": "user", "content": "What auth should we use?"},
            },
            {
                "type": "assistant",
                "sessionId": "abc-123",
                "message": {"role": "assistant", "content": "Sessions for now."},
            },
        ],
    )
    conversations = claude_code.parse(path)
    assert len(conversations) == 1
    convo = conversations[0]
    assert convo.chat_id == "abc-123"
    assert "User: What auth should we use?" in convo.text
    assert "Assistant: Sessions for now." in convo.text


def test_claude_code_parses_block_list_content_and_skips_tool_use(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Use sessions."}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
                        {"type": "text", "text": "Done, sessions it is."},
                    ],
                },
            },
        ],
    )
    conversations = claude_code.parse(path)
    convo = conversations[0]
    assert "User: Use sessions." in convo.text
    assert "Assistant: Done, sessions it is." in convo.text


def test_claude_code_skips_malformed_lines(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        'not json at all\n'
        '{"type": "user", "message": {"role": "user", "content": "hello"}}\n'
        '\n'
    )
    conversations = claude_code.parse(path)
    assert "User: hello" in conversations[0].text


CHATGPT_FIXTURE = {
    "conversation_id": "conv-1",
    "create_time": 1700000000,
    "mapping": {
        "n1": {
            "id": "n1",
            "message": {
                "author": {"role": "user"},
                "create_time": 1700000001,
                "content": {"content_type": "text", "parts": ["What auth should we use?"]},
            },
        },
        "n2": {
            "id": "n2",
            "message": {
                "author": {"role": "assistant"},
                "create_time": 1700000002,
                "content": {"content_type": "text", "parts": ["Sessions for now."]},
            },
        },
        "n0": {
            "id": "n0",
            "message": {
                "author": {"role": "system"},
                "create_time": 1699999999,
                "content": {"content_type": "text", "parts": [""]},
            },
        },
    },
}


def test_chatgpt_parses_mapping_in_time_order(tmp_path):
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps([CHATGPT_FIXTURE]))
    conversations = chatgpt.parse(path)
    assert len(conversations) == 1
    convo = conversations[0]
    assert convo.chat_id == "conv-1"
    lines = convo.text.split("\n\n")
    assert lines == ["User: What auth should we use?", "Assistant: Sessions for now."]


def test_chatgpt_skips_empty_and_system_messages(tmp_path):
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps([CHATGPT_FIXTURE]))
    conversations = chatgpt.parse(path)
    assert "system" not in conversations[0].text.lower()
