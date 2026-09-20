"""Pack the context block sent to a model (PRD section 7.1 and section 8).

``pack`` takes the (already scoped, already ranked) facts and episodes
produced by ``search.search`` -- split by ``kind`` -- plus the profile
markdown, and assembles the exact block shape from PRD 7.1:

```
[Vault context | scope: acme-platform | 7 items | 412 tokens]
Profile: <profile block>
Facts (acme-platform):
- team = infra (was search, 2026-09-12)
- auth = Clerk, decided 2026-09-18
Recent:
- 2026-09-18: Evaluated JWT vs sessions vs Clerk; chose Clerk for SSO.
```

Order: profile, then up to 10 entity facts, then up to 3 episodes. Item
ids already logged for this chat (``db.logged_item_ids``) are skipped.
When the block is over budget, an episode is truncated before a fact is
dropped.
"""

from __future__ import annotations

import re
import sqlite3

from vault import db
from vault.models import count_tokens

MAX_FACTS = 10
MAX_EPISODES = 3
MIN_EPISODE_LEN = 40


def _condense_profile(profile_md: str | None) -> str:
    """Fold the profile markdown's bullet lines into one line."""
    if not profile_md:
        return "(none)"
    bullets = []
    for line in profile_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
    if not bullets:
        return "(none)"
    return "; ".join(bullets)


def _previous_value(conn: sqlite3.Connection, fact: dict) -> tuple[str, str] | None:
    """The most recently superseded prior value for this fact's predicate,
    if any, as (value, YYYY-MM-DD)."""
    entity_id = fact.get("entity_id")
    if entity_id is None:
        return None
    history = db.history_for_entity(conn, entity_id)
    candidates = [h for h in history if h["predicate"] == fact["predicate"]]
    if not candidates:
        return None
    latest = max(candidates, key=lambda h: h["observed_at"])
    return latest["value"], latest["observed_at"][:10]


def _format_fact(conn: sqlite3.Connection, fact: dict) -> str:
    line = f"- {fact['predicate']} = {fact['value']}"
    previous = _previous_value(conn, fact)
    if previous is not None:
        old_value, old_date = previous
        line += f" (was {old_value}, {old_date})"
    return line


def _format_episode(episode: dict) -> str:
    date = (episode.get("started_at") or "")[:10]
    return f"- {date}: {episode['summary']}"


_EPISODE_PREFIX = re.compile(r"^(- \d{4}-\d{2}-\d{2}: )")


def _shorten(line: str, min_len: int = MIN_EPISODE_LEN) -> str:
    """Shorten one episode line, keeping its date prefix intact. Returns
    the line unchanged once it can't be shortened further."""
    if len(line) <= min_len:
        return line
    match = _EPISODE_PREFIX.match(line)
    prefix = match.group(1) if match else ""
    rest = line[len(prefix) :]
    target = max(min_len - len(prefix), 10)
    if len(rest) <= target or rest.endswith("…"):
        return line
    return prefix + rest[:target].rstrip() + "…"


def _build_body(
    profile_line: str,
    scope_label: str,
    fact_lines: list[str],
    episode_lines: list[str],
) -> str:
    parts = [f"Profile: {profile_line}"]
    if fact_lines:
        parts.append(f"Facts ({scope_label}):")
        parts.extend(fact_lines)
    if episode_lines:
        parts.append("Recent:")
        parts.extend(episode_lines)
    return "\n".join(parts)


def _tokens_and_header(scope_label: str, item_count: int, body: str) -> tuple[int, str]:
    """Fixed-point over the header's own token count, since the header
    includes the token total. Converges in a couple of iterations."""
    tokens = 0
    header = ""
    for _ in range(5):
        header = f"[Vault context | scope: {scope_label} | {item_count} items | {tokens} tokens]"
        new_tokens = count_tokens(header + "\n" + body)
        if new_tokens == tokens:
            break
        tokens = new_tokens
        header = f"[Vault context | scope: {scope_label} | {item_count} items | {tokens} tokens]"
    return tokens, header


def pack(
    conn: sqlite3.Connection,
    profile_md: str,
    facts: list[dict],
    episodes: list[dict],
    budget: int = 700,
    chat_id: str | None = None,
) -> tuple[str, list[str], int]:
    logged = db.logged_item_ids(conn, chat_id) if chat_id else set()

    facts_kept = [f for f in facts if f.get("id") not in logged][:MAX_FACTS]
    episodes_kept = [e for e in episodes if e.get("id") not in logged][:MAX_EPISODES]

    scope_label = facts_kept[0]["entity"] if facts_kept else "none"
    profile_line = _condense_profile(profile_md)

    fact_lines = [_format_fact(conn, f) for f in facts_kept]
    episode_lines = [_format_episode(e) for e in episodes_kept]

    def block_tokens() -> int:
        item_count = len(facts_kept) + len(episodes_kept)
        body = _build_body(profile_line, scope_label, fact_lines, episode_lines)
        tokens, _ = _tokens_and_header(scope_label, item_count, body)
        return tokens

    # Truncate an episode before dropping a fact.
    while block_tokens() > budget and (episodes_kept or facts_kept):
        if episodes_kept:
            lengths = [len(line) for line in episode_lines]
            idx = lengths.index(max(lengths))
            shortened = _shorten(episode_lines[idx])
            if shortened == episode_lines[idx]:
                episodes_kept.pop(idx)
                episode_lines.pop(idx)
            else:
                episode_lines[idx] = shortened
        elif facts_kept:
            facts_kept.pop()
            fact_lines.pop()
        else:
            break

    item_ids = [f["id"] for f in facts_kept] + [e["id"] for e in episodes_kept]
    item_count = len(item_ids)
    body = _build_body(profile_line, scope_label, fact_lines, episode_lines)
    tokens, header = _tokens_and_header(scope_label, item_count, body)

    block = header + "\n" + body
    return block, item_ids, tokens
