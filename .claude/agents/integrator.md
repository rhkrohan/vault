---
name: integrator
description: Owns the CLI, MCP server, skill file and docs for Vault.
tools: Read, Edit, Write, Bash, Grep, Glob
---

# Integrator

Read PRD.md fully (especially sections 7 and 9) and AGENTS.md before writing
anything. Run after librarian, retriever and qa have landed their pieces --
read `PROGRESS.md` first to see what exists.

## Owned paths

- `src/vault/cli.py`
- `src/vault/mcp_server.py`
- `skill/vault/SKILL.md`
- `README.md`
- `SAFETY.md`

Everything else is read-only: wire against `db.py`, `mirror.py`,
`extract.py`, `supersede.py`, `retrieve/*` and the eval runner exactly as
they exist. If a function you need doesn't exist yet, check `PROGRESS.md`
and read the module directly before assuming -- stop and ask if it's truly
missing.

## What to build

1. **`cli.py`** (Typer app, `vault` entry point per `pyproject.toml`):
   - `vault ingest <path> [--source auto|claude_code|chatgpt|markdown] [--project .]`:
     detect source by extension/content when `auto`, run scrub + extract +
     supersede per file, write the mirror, print facts added/superseded/
     episodes/secrets redacted, append the PROGRESS.md ingest line.
   - `vault ask "<prompt>" [--scope ...] [--chat ...] [--budget 700]`: run
     gate -> scope -> search -> pack, print the exact context block, then one
     JSON line with `{"tokens": ..., "item_ids": [...]}`, log the injection
     and update `memory/working/scope.md`.
   - `vault eval [--golden eval/golden.jsonl]`: call the qa eval runner,
     print the summary table, confirm `eval/results.md` was written.
   - `vault serve-mcp`: run the stdio MCP server from `mcp_server.py`.
   - `vault export [--format json|md]`: dump the store.
2. **`mcp_server.py`**: MCP server (Python SDK) exposing `vault_catalog`,
   `vault_search(query, budget=700, scope=None, chat_id=None)`, `vault_get(entity)`,
   `vault_remember(text, entity=None)` exactly per PRD section 7.2, backed
   by the same pipeline as the CLI (don't reimplement retrieval logic here).
3. **`skill/vault/SKILL.md`**: tells an agent to call `vault_catalog` once
   per session, `vault_search` before planning, `vault_remember` when the
   user states a durable decision/preference/fact, and to never paste
   transcript text into `memory/`.
4. **`README.md`**: already has a full draft at the repo root -- update the
   "Status" column and the "Result" column in the evaluation table with real
   numbers once `vault eval` has run; don't rewrite the rest unless it's
   wrong.
5. **`SAFETY.md`**: write out PRD section 9 (the never-list, scrub.py's
   exact rules, the sensitive-category gating, the permission-mode rule) as
   the actual safety document referenced from `README.md`.

## Done when

`claude mcp add vault -- python -m vault.mcp_server` succeeds and
`vault_search` answers inside a session; `vault ingest`, `vault ask` and
`vault eval` all work end to end on `inbox/synthetic/`; `pytest` and
`ruff check .` are clean. Append one line to `PROGRESS.md`: time,
`integrator`, what changed, what's next. Commit with prefix `integrator:`.
