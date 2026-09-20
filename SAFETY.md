# Safety

This is the safety spec for Vault (PRD section 9), written out as the actual
policy and cross-referenced to where each rule is enforced in code. If any
of this changes, the change goes through PRD.md and PROGRESS.md first, not
silently into a module.

## Never list

Vault never does any of the following, in any mode (local or hosted):

- No global keyboard hook.
- No screen capture or screen recording.
- No HTTPS interception or TLS man-in-the-middling.
- No network calls except to the one configured model provider
  (`VAULT_PROVIDER`: `claude` via `providers/claude.py`, or `runpod` via
  `providers/runpod.py` in hosted mode). Nothing else on the network path --
  no telemetry, no analytics, no third-party calls from the browser in
  hosted mode (the browser only ever talks to the Vault web app; only the
  server calls RunPod).
- No reads outside three roots: `inbox/`, `~/.claude/projects`, and
  `VAULT_HOME`. `vault ingest` checks every path against this allowlist
  (`cli.py`'s `_check_allowed`/`_is_allowed_path`) before it opens a file,
  and refuses anything outside it.
- No chat content into `memory/`. Only extracted facts, entity metadata,
  episode summaries, and evidence fragments capped at 120 characters ever
  reach the store or the Markdown mirror. See "No transcript text" below.

## Secret scrubbing

`scrub.py`'s `scrub(text)` runs before any write to the database or to
`memory/` -- including before the scrubbed text is even sent to the
extraction provider (`extract.py` calls `scrub()` first, so a provider never
sees a secret either). It redacts, in this order, so specific patterns are
caught before the generic catch-all can double-count them:

1. `sk-` and `sk-ant-` API keys.
2. AWS access keys (`AKIA...`).
3. GitHub tokens (`ghp_...`).
4. RunPod API keys (`rpa_...`).
5. Three-part base64 JWTs.
6. Digit runs of 13-19 characters that pass the Luhn checksum (credit
   card numbers).
7. SSNs in `###-##-####` form.
8. Any remaining token of 32 or more characters whose Shannon entropy
   exceeds 4.0 bits/character (catches keys and secrets that don't match a
   known vendor prefix).

Each match is replaced with `[REDACTED:<type>]` and counted. `vault ingest`
prints the total redaction count per run (`cli.py` calls `scrub.scrub()`
directly, separately from `extract.extract()`, specifically to surface this
count -- `extract()`'s return type is the validated `ExtractionResult` and
doesn't expose it). `vault_remember` reports the same count in its MCP
response.

## Sensitive categories

`finances` and `health` are the two sensitive categories (PRD section 6.1);
`ExtractedFact.sensitive` defaults to `True` for both, enforced in
`models.py`.

- **Never in `profile.md`.** `mirror.write_profile` only ever selects facts
  with `sensitive = 0`; a sensitive fact cannot reach the hot tier no matter
  how it was extracted.
- **Gated in `scope.py`.** `scope.allow_sensitive(prompt)` only returns
  `True` when the prompt itself matches a finance or health keyword list --
  never as a side effect of a project scope. `search.search` drops sensitive
  facts unless `allow_sensitive=True` is passed explicitly, as defense in
  depth on top of that gate. `cli.py`'s `_run_ask` and `mcp_server.py`'s
  `vault_search` both compute `allow_sensitive` from the live prompt on
  every call; a project-scoped question about `acme-platform` never pulls in
  a health or finance fact just because one happens to reference the same
  entity.
- They still appear in that entity's `memory/semantic/<entity>.md` (marked
  `(sensitive)`) and in `vault_get`, since those are explicit, targeted
  reads of one named entity, not a general-purpose scan.

## No transcript text

Nothing that reaches `memory/` or `vault.db` is raw transcript text:

- `ExtractedFact.evidence` is capped at 120 characters by the schema
  (`models.py`) and is meant to be a paraphrase, not a quote (enforced by
  the provider's system prompt in `providers/base.py`; not mechanically
  provable from a string alone, which is why the cap and the eval's leak
  test both exist as a backstop).
- `tests/test_no_leaks.py` (qa) asserts that no 12-word span from
  `inbox/synthetic/` appears anywhere under `memory/` or in `PROGRESS.md`.
  This test is part of the standard `pytest` run and must stay green.
- `mirror.py`'s writers only ever read from SQLite rows (facts, entities,
  episodes) -- never from a `ParsedConversation.text` or a raw provider
  response.

## Path and process safety

- `ingest` refuses any path outside `inbox/`, `~/.claude/projects`, or
  `VAULT_HOME` (see above).
- `vault.db` lives at `VAULT_HOME/vault.db` (default `~/.vault/vault.db`),
  never inside the project folder being ingested, so cloning or sharing a
  project never leaks the database.
- Agents building and operating Vault run in the permission mode that asks
  before each action. No `rm -rf`, no force push, no `git reset --hard`, no
  new dependencies beyond what's pinned in `pyproject.toml`/
  `requirements.txt` -- any of those requires asking a human first
  (AGENTS.md rules 1, 5, 7).
- `vault eval` runs before every merge to `main`, and its numbers are
  recorded in `PROGRESS.md` (AGENTS.md rule 10; PRD section 9's last line).

## Hosted mode additions

In hosted mode (`web.py`, `providers/runpod.py`, Session B's `deploy/`):

- Keys (`RUNPOD_API_KEY`, `ANTHROPIC_API_KEY`) live only in the server's
  environment -- never in the repo, a log, a response body, or a
  screenshot. Only the server process calls RunPod; the browser only talks
  to the Vault web app.
- `VAULT_HOME` is a persistent path outside the container so state survives
  a redeploy or reload.
- Upstream provider errors are surfaced to the user as a message, never a
  silent hang or a swallowed exception (`ProviderError` carries the
  upstream message through to the CLI and, in hosted mode, to `/ingest` and
  `/ask`'s responses).
- WAL mode and one SQLite connection per request (`db.connect()`) so two
  users hitting the app at once don't corrupt or block each other.

## What's intentionally out of scope today

Per PRD section 3: macOS hotkey, browser extension, local models on the
laptop, encryption at rest, multi-device sync. None of these are safety
regressions to fix later so much as features not built for this hackathon
window; each has a line in README.md's Roadmap.
