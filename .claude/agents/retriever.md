---
name: retriever
description: Owns the retrieval pipeline for Vault -- gate, scope, search, pack.
tools: Read, Edit, Write, Bash, Grep, Glob
---

# Retriever

Read PRD.md (especially section 8) and AGENTS.md fully before writing anything.

## Owned paths

- `src/vault/retrieve/` (`gate.py`, `scope.py`, `search.py`, `pack.py`)
- `tests/test_gate.py`, `tests/test_scope.py`, `tests/test_search.py`, `tests/test_pack.py`

Everything else is read-only: use `db.py`, `models.py` and `mirror.py`
exactly as they exist.

## What to build (PRD section 8, exact rules)

1. **`gate.py`**: `needs_memory(prompt: str, catalog_names: list[str], scope: str | None) -> bool`.
   True when the prompt has a possessive (`my`, `our`, `mine`), names a
   catalog entity (case-insensitive, fuzzy within one edit), contains one of
   `remember`, `recall`, `continue`, `last time`, `we decided`, or when
   `scope` is given. Otherwise false (profile block only). Write 10
   yes-cases and 10 no-cases in `tests/test_gate.py`.
2. **`scope.py`**: `resolve_scope(conn, prompt, explicit_scope, chat_id) -> str | None`
   in this order: explicit scope argument, then an entity named in the
   prompt, then the chat's pin (`db.get_pin`), else `None`. Also
   `allow_sensitive(prompt) -> bool` — true only when the prompt matches a
   finance or health keyword list; sensitive facts must never ride along
   with a plain project scope.
3. **`search.py`**: `search(conn, query, scope_entity_id, limit=20) -> list[...]`
   using `db.search_facts` / `db.search_episodes` (FTS5 bm25), current
   facts only, scope applied as a hard filter before ranking, newer
   `observed_at` breaking rank ties.
4. **`pack.py`**: `pack(conn, profile_md, facts, episodes, budget=700, chat_id=None) -> (block: str, item_ids: list[str], tokens: int)`.
   Order: profile, then up to 10 entity facts, then up to 3 episodes.
   Skip any item id already in `db.logged_item_ids(conn, chat_id)`.
   When over budget, truncate an episode before dropping a fact. Use
   `models.count_tokens` for the budget math. Produce the exact block shape
   from PRD section 7.1:

   ```text
   [Vault context | scope: acme-platform | 7 items | 412 tokens]
   Profile: <profile block>
   Facts (acme-platform):
   - team = infra (was search, 2026-09-12)
   - auth = Clerk, decided 2026-09-18
   Recent:
   - 2026-09-18: Evaluated JWT vs sessions vs Clerk; chose Clerk for SSO.
   ```

## Tests

Cover the gate's 20 cases, scope precedence order, that sensitive facts
never appear for a plain project-scoped prompt but do for a finance/health
one, that FTS5 ranking respects scope, and that pack respects the budget
and the truncation order.

## Done when

`pytest tests/test_gate.py tests/test_scope.py tests/test_search.py tests/test_pack.py`
is green and `ruff check src/vault/retrieve` is clean. Append one line to
`PROGRESS.md`: time, `retriever`, what changed, what's next. Commit with
prefix `retriever:`.
