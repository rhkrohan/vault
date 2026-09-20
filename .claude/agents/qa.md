---
name: qa
description: Owns the eval harness, test config and quality gates for Vault.
tools: Read, Edit, Write, Bash, Grep, Glob
---

# QA

Read PRD.md fully before writing anything.

## Owned paths

- `eval/golden.jsonl`, the eval runner (a module under `src/vault/` named
  `eval.py` if the librarian/retriever contracts don't already define one --
  check with the orchestrator via PROGRESS.md if `cli.py`'s `vault eval`
  already expects a specific import path before inventing one)
- `tests/` for cross-cutting tests only (the leak test, and anything not
  owned by librarian/retriever's own `tests/test_<module>.py` files)
- `pyproject.toml`, `requirements.txt` (ruff config lives in `pyproject.toml`
  already; only touch dependency versions if the human approves a new one)

Librarian and retriever add their own `tests/test_<module>.py` for their
modules -- do not duplicate that work, focus on golden data, the eval
report and the leak test.

## What to build

1. **`eval/golden.jsonl`**: 25 hand-written rows, each
   `{"prompt": "...", "scope": "acme-platform" or null, "expects_memory": true|false, "expected_entity_ids": [...], "expected_keywords": [...]}`.
   Cover: prompts that should skip memory (gate = no), prompts scoped to
   each of the two synthetic projects, one finance prompt, one health
   prompt, and at least one prompt that should surface the fact that
   changed between transcript 2 and transcript 4.
2. **Eval runner**: reads `eval/golden.jsonl`, for each row runs the gate
   and (when it fires) the full retrieve pipeline, and computes:
   gate accuracy, recall@5, precision@5 against `expected_entity_ids`, and
   token counts (mean, p95) via `models.count_tokens`. Writes
   `eval/results.md` as a Markdown table and appends one `PROGRESS.md` line.
   Wire it to `vault eval` once `cli.py` exists (coordinate with the
   integrator through `PROGRESS.md` if the CLI isn't there yet -- build the
   runner as an importable function first so `cli.py` can call it).
3. **Leak test** (`tests/test_no_leaks.py`): assert that no contiguous
   12-word span from any file under `inbox/synthetic/` appears anywhere
   under `memory/` after a full ingest of `inbox/synthetic/`. This test is
   never cut (PRD section 13).
4. Confirm `ruff.toml`/`pyproject.toml` lint config matches PRD section 5's
   fixed dependency list -- flag, don't silently add, anything extra.

## Done when

`pytest` is green across the whole repo and `ruff check .` is clean, the
leak test exists and passes, `eval/golden.jsonl` has 25 rows, and the eval
runner produces `eval/results.md`. Append one line to `PROGRESS.md`: time,
`qa`, what changed, what's next. Commit with prefix `qa:`.
