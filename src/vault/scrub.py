"""Secret scrubbing (PRD section 9). Runs before any write to the db or
memory/: `scrub(text)` redacts, in order, `sk-`/`sk-ant-` API keys, AWS
`AKIA...` keys, GitHub `ghp_...` tokens, RunPod `rpa_...` keys, three-part
base64 JWTs, 13-19 digit runs that pass Luhn, `###-##-####` SSNs, and any
32+ character token with Shannon entropy above 4.0 bits/char. Each match is
replaced with `[REDACTED:<type>]` and counted by type.

Order matters: earlier, more specific patterns are redacted first, so by the
time the generic high-entropy pattern runs, already-redacted placeholders
(`[REDACTED:api_key]`, ...) no longer look like bare tokens and are not
double-counted.
"""

from __future__ import annotations

import math
import re

# --- specific patterns, most specific first -------------------------------

_API_KEY_RE = re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b")
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GITHUB_TOKEN_RE = re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")
_RUNPOD_KEY_RE = re.compile(r"\brpa_[A-Za-z0-9]{20,}\b")
_JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_DIGIT_RUN_RE = re.compile(r"\b\d{13,19}\b")
_GENERIC_TOKEN_RE = re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b")
# Checked against a token the generic pattern already matched, so it only has
# to recognise the alphabet, not re-find the boundaries.
_HEX_RUN_RE = re.compile(r"[0-9a-fA-F]{32,}")
_HEX_ENTROPY_FLOOR = 3.0

_ENTROPY_THRESHOLD = 4.0

# Order in which the specific (non-entropy) patterns are applied. Each is a
# (label, pattern) pair; the label becomes `[REDACTED:<label>]`.
_SPECIFIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("api_key", _API_KEY_RE),
    ("aws_key", _AWS_KEY_RE),
    ("github_token", _GITHUB_TOKEN_RE),
    ("runpod_key", _RUNPOD_KEY_RE),
    ("jwt", _JWT_RE),
    ("ssn", _SSN_RE),
]


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(s)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def scrub(text: str) -> tuple[str, dict[str, int]]:
    """Redact secrets in `text`. Returns (clean_text, counts_by_type)."""
    counts: dict[str, int] = {}
    clean = text

    for label, pattern in _SPECIFIC_PATTERNS:

        def _replace(match: re.Match, _label: str = label) -> str:
            counts[_label] = counts.get(_label, 0) + 1
            return f"[REDACTED:{_label}]"

        clean = pattern.sub(_replace, clean)

    def _luhn_replace(match: re.Match) -> str:
        digits = match.group(0)
        if _luhn_valid(digits):
            counts["credit_card"] = counts.get("credit_card", 0) + 1
            return "[REDACTED:credit_card]"
        return digits

    clean = _DIGIT_RUN_RE.sub(_luhn_replace, clean)

    def _entropy_replace(match: re.Match) -> str:
        token = match.group(0)
        # A pure hex run is structurally incapable of tripping the entropy
        # rule below: 16 symbols cap Shannon entropy at 4.0 bits/char, and a
        # finite sample measures ~3.7-3.8, always under the "> 4.0" threshold
        # PRD section 9 specifies. That silently exempted the single most
        # common secret shape there is -- 32/48/64-character hex session and
        # API tokens, which is exactly what a pasted Claude Code or ChatGPT
        # transcript is full of. Length alone is the signal here; 32+ hex
        # characters is never prose.
        # The entropy floor matters: "aaaa...' is a valid hex run but is not a
        # token, and redacting repetitive text would be a false positive. A
        # random hex token measures ~3.7-4.0 bits/char, a repeated pattern
        # well under 2.5, so 3.0 separates them cleanly while still sitting
        # below anything the "> 4.0" generic rule could ever catch.
        if _HEX_RUN_RE.fullmatch(token) and _shannon_entropy(token) >= _HEX_ENTROPY_FLOOR:
            counts["hex_token"] = counts.get("hex_token", 0) + 1
            return "[REDACTED:hex_token]"
        if _shannon_entropy(token) > _ENTROPY_THRESHOLD:
            counts["high_entropy_token"] = counts.get("high_entropy_token", 0) + 1
            return "[REDACTED:high_entropy_token]"
        return token

    clean = _GENERIC_TOKEN_RE.sub(_entropy_replace, clean)

    return clean, counts
