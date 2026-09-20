---
name: vault
description: Local memory that follows you across models. Use vault_catalog once per session to load who the user is and what projects exist, vault_search before planning any nontrivial task to pull scoped facts and recent episodes, and vault_remember whenever the user states a durable decision, preference or fact. Never paste transcript text into memory/ yourself.
---

# Vault

Vault is a local memory store for one person's projects, people, preferences
and decisions. It is not a transcript log: everything in it is a short,
structured fact or a one-to-three-sentence episode summary, already scrubbed
of secrets. Read it, and add to it, through the four MCP tools below --
never by writing files into `memory/` directly.

## Tools

- **`vault_catalog()`** -- Returns the profile block (hot-tier identity and
  preferences) plus the entity catalog (every known project, person and
  topic, one line each). Call this once per session, near the start, so you
  know what already exists before asking about it.

- **`vault_search(query, budget=700, scope=None, chat_id=None)`** -- Runs
  the gate, scope filter, FTS5 search and token-budget packer, and returns a
  context block (current facts for the scoped entity, then recent episodes)
  plus the token count. Call this before planning any task that might touch
  something the user has told an assistant before -- a named project, "my"
  anything, or a reference to a past decision. Pass `scope` with the
  project/entity name when you already know it; otherwise Vault infers scope
  from the prompt or the chat's pin.

- **`vault_get(entity)`** -- Returns the current facts for one catalog
  entity by name, with no search or budget involved. Use this once you know
  exactly which entity you need (e.g. after `vault_catalog` names it) and
  want everything about it, not just what a query happens to match.

- **`vault_remember(text, entity=None)`** -- Extracts durable facts, any new
  entities, and an episode summary from `text`, scrubs secrets, applies
  supersession (a changed value replaces the old one; the old one moves to
  that entity's History), and writes the result. Call this whenever the user
  states something durable: a decision ("we're going with Clerk"), a
  preference ("I like short variable names"), a fact about their situation
  ("I'm the infra lead now"), or a correction to something Vault already has
  wrong. Do not call it for small talk, one-off questions, or anything that
  isn't worth remembering next session. Pass `entity` when you already know
  which project/person/topic the fact belongs to.

## Rules

- Never write, paste or summarize raw transcript text into `memory/` or any
  project file yourself -- that is exactly what `vault_remember` exists to
  avoid. If something is worth keeping, call `vault_remember` with a short
  paraphrase (or the relevant excerpt) and let Vault's own scrubbing and
  extraction handle it.
- Treat `vault_search`'s block as ground truth for what Vault currently
  believes; if it conflicts with what the user just said, prefer the user
  and call `vault_remember` to update it.
- Sensitive facts (finances, health) only surface when the prompt is
  actually about that topic. Don't go looking for them, and don't repeat
  them back outside that context.
- If `vault_catalog` or `vault_search` come back empty or say memory hasn't
  been built yet, that's normal on a fresh project -- proceed without it and
  let `vault_remember` start filling it in as the conversation happens.
