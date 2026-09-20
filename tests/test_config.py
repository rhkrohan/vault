"""Tests for .env loading (src/vault/config.py).

PRD section 5 makes environment variables the whole configuration surface
and .env the place a developer puts them, so these pin the two behaviours
that matter: the file is read, and the real environment still wins.
"""

from __future__ import annotations

import os

from vault.config import load_env, parse_env

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_plain_pairs():
    assert parse_env("A=1\nB=two\n") == {"A": "1", "B": "two"}


def test_skips_comments_and_blank_lines():
    assert parse_env("# a comment\n\nA=1\n   \n# another\nB=2\n") == {"A": "1", "B": "2"}


def test_strips_surrounding_quotes():
    parsed = parse_env('A="a value"\nB=\'b value\'\nC=unquoted\n')
    assert parsed == {"A": "a value", "B": "b value", "C": "unquoted"}


def test_accepts_the_export_prefix():
    assert parse_env("export A=1\n") == {"A": "1"}


def test_keeps_equals_signs_inside_a_value():
    """Base64 and JWT-ish values end in '=' padding."""
    assert parse_env("KEY=abc==\n") == {"KEY": "abc=="}


def test_ignores_lines_without_an_equals_sign():
    assert parse_env("A=1\nnonsense\nB=2\n") == {"A": "1", "B": "2"}


def test_empty_value_is_allowed():
    """.env.example ships every key with a blank value."""
    assert parse_env("ANTHROPIC_API_KEY=\n") == {"ANTHROPIC_API_KEY": ""}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_load_env_sets_variables(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("VAULT_TEST_KEY=from-file\n")

    applied = load_env(env_file)

    assert applied == {"VAULT_TEST_KEY": "from-file"}
    assert os.environ["VAULT_TEST_KEY"] == "from-file"


def test_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    """`VAULT_PROVIDER=offline vault ingest ...` must beat .env, and on the
    server the platform's own environment must stay authoritative."""
    monkeypatch.setenv("VAULT_TEST_KEY", "from-environment")
    env_file = tmp_path / ".env"
    env_file.write_text("VAULT_TEST_KEY=from-file\n")

    applied = load_env(env_file)

    assert applied == {}
    assert os.environ["VAULT_TEST_KEY"] == "from-environment"


def test_override_forces_the_file_to_win(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_TEST_KEY", "from-environment")
    env_file = tmp_path / ".env"
    env_file.write_text("VAULT_TEST_KEY=from-file\n")

    load_env(env_file, override=True)

    assert os.environ["VAULT_TEST_KEY"] == "from-file"


def test_a_missing_file_is_not_an_error(tmp_path):
    """Hosted mode has no .env at all, and offline mode needs no config."""
    assert load_env(tmp_path / "does-not-exist") == {}


def test_a_directory_in_place_of_the_file_is_not_an_error(tmp_path):
    assert load_env(tmp_path) == {}


def test_example_file_lists_every_prd_variable():
    """PRD section 5: .env.example lists every variable with a blank value."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    keys = set(parse_env(example.read_text()))
    assert {
        "ANTHROPIC_API_KEY",
        "VAULT_MODEL",
        "VAULT_PROVIDER",
        "RUNPOD_API_KEY",
        "RUNPOD_ENDPOINT_ID",
        "RUNPOD_MODEL",
        "VAULT_HOME",
        "VAULT_BUDGET",
    } <= keys


def test_example_file_ships_no_real_secrets():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    parsed = parse_env(example.read_text())
    for key in ("ANTHROPIC_API_KEY", "RUNPOD_API_KEY"):
        assert parsed[key] == "", f"{key} must ship blank"
