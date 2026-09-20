"""Gate: decide whether a prompt needs memory at all (PRD section 8).

``needs_memory`` returns False for prompts that don't reference anything
durable -- those get the profile block only, nothing else.
"""

from __future__ import annotations

import re

POSSESSIVES = re.compile(r"\b(my|our|mine)\b", re.IGNORECASE)

TRIGGER_PHRASES = ("remember", "recall", "continue", "last time", "we decided")


def _edit_distance(a: str, b: str) -> int:
    """Classic Levenshtein distance, no dependency required."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(
                prev[j] + 1,  # deletion
                cur[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = cur
    return prev[-1]


def _fuzzy_contains(prompt_lower: str, name_lower: str) -> bool:
    """True if name_lower appears in prompt_lower exactly, or within one
    edit distance over a same-length window of words."""
    if not name_lower:
        return False
    if name_lower in prompt_lower:
        return True
    words = re.findall(r"[a-z0-9']+", prompt_lower)
    name_words = re.findall(r"[a-z0-9']+", name_lower)
    n = len(name_words)
    if n == 0 or n > len(words):
        return False
    joined_name = " ".join(name_words)
    for i in range(len(words) - n + 1):
        window = " ".join(words[i : i + n])
        if _edit_distance(window, joined_name) <= 1:
            return True
    return False


def mentions_entity(prompt: str, catalog_names: list[str]) -> str | None:
    """Return the first catalog entity name fuzzy-mentioned in prompt, else
    None. Longer names are checked first so e.g. "acme-platform" wins over
    a shorter coincidental overlap."""
    lower = prompt.lower()
    for name in sorted(catalog_names, key=len, reverse=True):
        if _fuzzy_contains(lower, name.lower()):
            return name
    return None


def needs_memory(prompt: str, catalog_names: list[str], scope: str | None) -> bool:
    """True when the prompt has a possessive, names a catalog entity
    (fuzzy within one edit), contains a recall trigger phrase, or scope is
    explicitly given. Otherwise the caller should return the profile block
    only (PRD section 8)."""
    if scope:
        return True
    lower = prompt.lower()
    if POSSESSIVES.search(lower):
        return True
    if any(phrase in lower for phrase in TRIGGER_PHRASES):
        return True
    if mentions_entity(prompt, catalog_names) is not None:
        return True
    return False
