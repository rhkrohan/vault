"""Load ``.env`` into the process environment.

PRD section 5 fixes the configuration surface as environment variables and
says ``.env`` is gitignored while ``.env.example`` lists every variable --
which only works if something actually reads ``.env``. Nothing did, so a
filled-in ``.env`` was silently ignored and the CLI still reported
"ANTHROPIC_API_KEY is not set".

This is a ~30 line stdlib parser rather than ``python-dotenv`` because the
dependency list in section 5 is fixed and this does not need a library.

Precedence is the usual one: **a variable already set in the real
environment wins over the file.** That keeps ``VAULT_PROVIDER=offline vault
ingest ...`` working regardless of what ``.env`` says, and it keeps the
server's own environment authoritative in hosted mode, where the RunPod key
is injected by the platform and no ``.env`` should exist at all.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILE = ".env"


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines, skipping blanks and ``#`` comments."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip one matching pair of surrounding quotes, so both
        # KEY=value and KEY="value with spaces" work.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env(path: str | Path = DEFAULT_ENV_FILE, *, override: bool = False) -> dict[str, str]:
    """Read `path` into ``os.environ``. Returns what was applied.

    A missing or unreadable file is not an error: hosted mode has no
    ``.env`` at all, and `VAULT_PROVIDER=offline` needs no configuration.
    """
    env_path = Path(path).expanduser()
    try:
        text = env_path.read_text()
    except (OSError, UnicodeDecodeError):
        return {}

    applied: dict[str, str] = {}
    for key, value in parse_env(text).items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
