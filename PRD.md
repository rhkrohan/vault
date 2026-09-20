# Vault: Project Requirements Document

For the Claude Code orchestrator and its subagents. Read this whole file before doing anything, then read AGENTS.md.

Date: September 20, 2026. Deadline: 6:00 PM EDT today. The contracts in sections 4 to 9 are fixed. Propose changes in PROGRESS.md; do not make them silently.

## 1. Goal

Ship Vault Librarian: a Python CLI plus MCP server plus web app that reads AI conversations, extracts durable facts, writes them into the memory/ folder of an XO Space project, and hands scope-filtered context back to any agent. It runs in two modes from one codebase:

- Local mode: on a laptop, extraction through the Claude API, XO Space showing every run.
- Hosted mode: on a GalaxyGate server, extraction through a RunPod serverless endpoint, a judge-openable URL.

Tracks: Quirq Build It, Bring Your Own Agent, The Code Registry, GalaxyGate + RunPod.

## 2. Definition of done

All of these are checked by a human before submission. Each is a command or a visible state, not an opinion.

| Check | Command or state |
| --- | --- |
| Ingest works | `vault ingest inbox/synthetic --project .` prints facts added, superseded, episodes, secrets redacted; memory/ is populated |
| Supersession works | The decision that changes between synthetic transcript 2 and 4 shows the new value, with the old one under History |
| Ask works | `vault ask "<prompt>" --scope <entity>` prints a context block under 700 tokens and logs an injection |
| Scope holds | A prompt scoped to project A shows no project B facts; a health or finance fact appears only for a prompt about that topic |
| MCP works | `claude mcp add vault -- python -m vault.mcp_server`, then vault_search returns a block inside a Claude Code session |
| Eval runs | `vault eval` prints gate accuracy, recall@5, precision@5, tokens mean and p95, and writes eval/results.md |
| Quality gates | `pytest` and `ruff check .` are clean on main |
| No leaks | The test that no 12-word span from inbox/ appears in memory/ passes |
| Hosted mode | `GET https://<team>.galaxygate.app/health` reports provider runpod and endpoint ok; `POST /ingest` on the server produces facts through RunPod |
| Visible | The xo-space shows the ingest sessions, the memory/ file changes and the costs |

## 3. Scope

Must (core): ingest for Claude Code jsonl, ChatGPT export and Markdown; scrub; extraction with the JSON schema; entities and catalog; supersession; SQLite with FTS5; Markdown mirror; gate, scope, search, pack; injection log; CLI; MCP server; skill file; eval on 25 prompts; tests; README; SAFETY.md.

Should (hosted mode): provider interface with `claude` and `runpod` implementations; FastAPI web app; deployment on GalaxyGate with the RunPod endpoint; `/health`, `/ingest`, `/ask`, `/audit`.

Stretch: RunPod embedding endpoint and hybrid search (bm25 plus cosine, RRF fusion); RunPod network volume for model weights.

Out of scope today: macOS hotkey, browser extension, local models on the laptop, encryption at rest, multi-device sync. Each gets one roadmap line in README.

## 4. Repo layout and ownership

```text
vault/
  PRD.md  CLAUDE.md  AGENTS.md  README.md  SAFETY.md  LICENSE  PROGRESS.md
  pyproject.toml  requirements.txt  .env.example  .gitignore
  .claude/agents/          librarian.md  retriever.md  integrator.md  qa.md
  skill/vault/SKILL.md     skill for agents in an XO Space (also the PR to quirq-ai/xo-space)
  inbox/synthetic/         five generated transcripts, committed
  inbox/real/              your exports, gitignored
  eval/golden.jsonl  eval/results.md
  deploy/                  hosted mode notes and env template (Session B)
  src/vault/
    models.py              pydantic models: Fact, Entity, Episode, Source, Injection, ExtractionResult
    db.py                  SQLite schema, FTS5, queries
    mirror.py              Markdown mirror into memory/
    providers/             base.py  claude.py  runpod.py  (embeddings.py stretch)
    ingest/                claude_code.py  chatgpt.py  markdown.py
    scrub.py               secret scrubbing
    extract.py             calls the active provider with the JSON schema, validates, retries once
    supersede.py           dedupe and supersession
    retrieve/              gate.py  scope.py  search.py  pack.py
    cli.py                 typer: ingest  ask  eval  serve-mcp  export
    mcp_server.py          vault_catalog  vault_search  vault_get  vault_remember
    web.py                 FastAPI app for hosted mode
  tests/
```

| Paths | Owner | Others |
| --- | --- | --- |
| models.py, db.py, mirror.py, providers/base.py, providers/claude.py | orchestrator, Phase 0 | read only |
| ingest/, scrub.py, extract.py, supersede.py | librarian | read only |
| retrieve/ | retriever | read only |
| cli.py, mcp_server.py, skill/, README.md, SAFETY.md | integrator | read only |
| eval/, tests/, pyproject.toml, requirements.txt | qa | librarian and retriever add tests/test_<module>.py for their own modules |
| providers/runpod.py, web.py, deploy/ | deployer (Session B, branch `hosted`) | read only; the orchestrator merges the branch in Phase 3 |
| PROGRESS.md, PRD.md, AGENTS.md | orchestrator | any agent may append one line to PROGRESS.md |

## 5. Dependencies and environment

Dependencies are fixed: anthropic, pydantic, typer, mcp, fastapi, uvicorn, httpx, pytest, ruff. Python 3.12. Pin every version in requirements.txt. Anything else requires asking the human. Token counting is `len(text) // 4`; no tokenizer dependency.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | none | Claude provider |
| `VAULT_MODEL` | `claude-sonnet-5` | Model for the Claude provider |
| `VAULT_PROVIDER` | `claude` | `claude` or `runpod` |
| `RUNPOD_API_KEY` | none | RunPod provider; server environment only |
| `RUNPOD_ENDPOINT_ID` | none | Serverless endpoint id for extraction |
| `RUNPOD_MODEL` | set at deploy | Model name served by the endpoint |
| `VAULT_HOME` | `~/.vault` | Where vault.db lives; on the server, a persistent path outside the container |
| `VAULT_BUDGET` | `700` | Default token budget for ask and vault_search |

`.env` is gitignored. `.env.example` lists every variable with a blank value.

## 6. Data contracts

### 6.1 Extraction schema

Every provider returns this JSON for one conversation. extract.py validates it with pydantic and retries once with the validation error appended to the prompt.

```json
{
  "facts": [
    {"subject": "user", "predicate": "team", "value": "infra",
     "entity": "acme-platform", "category": "projects",
     "sensitive": false, "confidence": 0.9,
     "evidence": "paraphrase under 120 characters"}
  ],
  "entities": [
    {"name": "acme-platform", "kind": "project", "description": "one line"}
  ],
  "episode": {
    "summary": "one to three sentences", "category": "projects",
    "tags": ["auth", "clerk"], "entities": ["acme-platform"]
  }
}
```

Categories: projects, work_school, people, interests, personal, finances, health. `sensitive` defaults to true for finances and health.

### 6.2 SQLite tables

| Table | Columns | Note |
| --- | --- | --- |
| entities | id, name, kind, description, category, created_at, updated_at | The catalog |
| facts | id, entity_id, subject, predicate, value, category, sensitive, confidence, source_id, observed_at, superseded_by | superseded_by NULL means current |
| episodes | id, source_id, summary, category, tags, entities, started_at | One per conversation |
| sources | id, kind, path, chat_id, captured_at | kind: claude_code, chatgpt, markdown, remember, web |
| injections | id, prompt_hash, item_ids, tokens, target, chat_id, scope, sent_at | The audit log |
| pins | chat_id, entity_id, set_by, updated_at | Chat-level scope |
| facts_fts, episodes_fts | FTS5 over value plus predicate, and summary plus tags | Keyword search |
| embeddings (stretch) | item_id, kind, vector | Cosine over the scoped candidate set |

Use WAL mode and one connection per request so the web app survives two users at once.

Supersession rule: same entity and predicate with a different value marks the old row superseded_by the new one; the same value raises confidence and updates observed_at. Both cases are unit-tested.

### 6.3 Markdown mirror

Follows the xo-projects layout (see the xo-space README: memory/ holds semantic, episodic, procedural and working).

| Path in the project | Contents | Rewritten when |
| --- | --- | --- |
| memory/profile.md | Hot tier: identity and preferences, under 300 tokens, no sensitive facts | A hot fact changes |
| memory/catalog.md | One line per entity | An entity changes |
| memory/semantic/<entity>.md | Current facts, then a History section of superseded ones | A fact for that entity changes |
| memory/episodic/<yyyy-mm-dd>-<source>.md | Episode summary, tags, entities | On ingest |
| memory/procedural/preferences.md | Preferences and how-tos | On ingest |
| memory/working/scope.md | Active scope and the last five injections | On ask |
| PROGRESS.md | One appended line per run: time, source, facts added, superseded, tokens | On ingest and eval |

Hard rules: no transcript text enters memory/; only the evidence field, capped at 120 characters. vault.db lives in VAULT_HOME, never inside the project folder. A test asserts that no 12-word span from inbox/ occurs anywhere in memory/.

## 7. Interfaces

### 7.1 CLI

| Command | Arguments | Output |
| --- | --- | --- |
| `vault ingest <path>` | file or folder; `--source auto|claude_code|chatgpt|markdown`; `--project <dir>` | Facts added, superseded, episodes, secrets redacted; db, mirror and one PROGRESS.md line |
| `vault ask "<prompt>"` | `--scope <entity>`, `--chat <id>`, `--budget 700` | The context block, then one JSON line with tokens and item ids; logs to injections and memory/working/scope.md |
| `vault eval` | `--golden eval/golden.jsonl` | Gate accuracy, recall@5, precision@5, tokens mean and p95; writes eval/results.md and a PROGRESS.md line |
| `vault serve-mcp` | none | stdio MCP server |
| `vault export` | `--format json|md` | Dumps the store |

Context block template, exactly this shape:

```text
[Vault context | scope: acme-platform | 7 items | 412 tokens]
Profile: <profile block>
Facts (acme-platform):
- team = infra (was search, 2026-09-12)
- auth = Clerk, decided 2026-09-18
Recent:
- 2026-09-18: Evaluated JWT vs sessions vs Clerk; chose Clerk for SSO.
```

### 7.2 MCP tools

| Tool | Input | Returns |
| --- | --- | --- |
| vault_catalog | none | Profile block plus the catalog |
| vault_search | query, budget=700, scope=null, chat_id=null | Packed block, item ids, tokens |
| vault_get | entity | Current facts for that entity |
| vault_remember | text, entity=null | Runs extraction on the text, writes, returns facts added |

Register with `claude mcp add vault -- python -m vault.mcp_server`. The skill file `skill/vault/SKILL.md` tells agents: call vault_catalog once per session, vault_search before planning, vault_remember when the user states a durable decision, preference or fact, and never paste transcript text into memory/.

### 7.3 Web app (hosted mode)

FastAPI in web.py, one inline HTML page, no frontend build.

| Route | Method | Behavior |
| --- | --- | --- |
| `/` | GET | Dashboard: catalog, recent injections, an ingest form (paste or upload), an ask box with scope and budget |
| `/ingest` | POST | Runs the librarian on the submitted text or file; returns facts added, superseded, redacted |
| `/ask` | POST | Runs gate, scope, search, pack; returns the block and tokens |
| `/audit` | GET | The injections table, newest first |
| `/health` | GET | Active provider, endpoint reachability, db path, version |

Requirements from the GalaxyGate scoring: a first-time user finishes ingest and ask with no instructions; the page says what is happening while a request runs; upstream errors are shown as messages, never a hang; results survive a reload because state is in VAULT_HOME on a persistent path; two users at once do not break it (WAL, per-request connections); the browser never calls RunPod.

## 8. Retrieval spec

| Module | Rule |
| --- | --- |
| gate.py | Needs memory when the prompt has a possessive (my, our, mine), a catalog entity name (case-insensitive, one edit fuzzy), one of remember, recall, continue, last time, we decided, or when scope is given. Otherwise return the profile block only. Tests: 10 yes, 10 no. |
| scope.py | Order: explicit scope, then an entity named in the prompt, then the chat pin, else none. Hard SQL filter on entity_id. Profile always included. Sensitive facts only when the prompt matches the finance or health keyword lists, never as part of a project scope. |
| search.py | FTS5 bm25 over facts_fts and episodes_fts, current facts only, scope applied before ranking, newer observed_at breaks ties. Top 20. Stretch: fuse with cosine over embeddings by RRF. |
| pack.py | Budget default 700 tokens. Order: profile, then up to 10 entity facts, then up to 3 episodes. Skip item ids already logged for this chat_id. Truncate an episode before dropping a fact. |

## 9. Safety spec

Written in SAFETY.md and enforced in code.

- Never list: no keyboard hook, no screen capture, no HTTPS interception, no network calls except the configured provider, no reads outside inbox/, ~/.claude/projects and VAULT_HOME, no chat content into memory/.
- scrub.py runs before any write: `sk-` and `sk-ant-` keys, AWS `AKIA`, GitHub `ghp_`, RunPod `rpa_`, three-part base64 JWTs, 13 to 19 digit runs passing Luhn, SSN 3-2-4, and any token of 32 or more characters with entropy above 4.0 bits per character. Replace with `[REDACTED:<type>]` and count.
- ingest refuses paths outside the allowlist.
- Sensitive categories gated in scope.py; never in profile.md.
- Agents run in the permission mode that asks before each action. No `rm -rf`, no force push, no `git reset --hard`, no new dependencies.
- `vault eval` runs before every merge to main; its numbers go in PROGRESS.md.

## 10. Providers

`providers/base.py` defines one interface:

```python
class Provider(Protocol):
    name: str
    def extract(self, conversation_text: str, schema: dict) -> dict: ...
    def health(self) -> dict: ...
```

`providers/claude.py`: Anthropic SDK, model from VAULT_MODEL, system prompt asks for JSON matching the schema and nothing else, 60 second timeout.

`providers/runpod.py` (Session B): calls the RunPod serverless endpoint for an open-weight instruct model served by the vLLM worker. Use the endpoint's OpenAI-compatible chat completions route (`https://api.runpod.ai/v2/$RUNPOD_ENDPOINT_ID/openai/v1/chat/completions`, bearer RUNPOD_API_KEY); confirm the exact route with the RunPod MCP or docs before relying on it. Same prompt and schema as the Claude provider, 90 second timeout, one retry, then raise ProviderError with the upstream message so the CLI and web app can show it. `health()` sends a one-token request and reports latency.

`providers/embeddings.py` (stretch): a second RunPod endpoint serving an embedding model; embed facts and episodes on ingest, store in the embeddings table, cosine over the scoped candidate set in search.py, fused with FTS5 by RRF.

## 11. Hosted mode on GalaxyGate (Session B)

Run by a teammate in a second Claude Code session on the branch `hosted`, following the hackathon's GalaxyGate guide. Steps:

1. Accounts: GalaxyGate registration with the event coupon, RunPod signup and credit, a RunPod API key named hackathon-app (copy it once, store it only in the server environment).
2. In an empty folder named hackathon: add the galaxygate and runpod MCP servers with `claude mcp add -t http`, start Claude Code with the permission mode that asks before each step, sign in to both with `/mcp`.
3. Paste the guide's block with TEAM_NAME and RUNPOD_API_KEY and follow its AGENT.md to create the server. When it reaches the app step, deploy this repo's web app instead of the demo: clone the `hosted` branch on the server, install, run `uvicorn vault.web:app --host 0.0.0.0 --port 8000` behind the guide's proxy, set VAULT_PROVIDER=runpod, RUNPOD_ENDPOINT_ID, RUNPOD_MODEL, RUNPOD_API_KEY and VAULT_HOME to a persistent path on the server.
4. Using the RunPod MCP, create a serverless endpoint for the chosen open-weight instruct model; put the id in RUNPOD_ENDPOINT_ID. Stretch: a second endpoint for the embedding model and a network volume for weights.
5. Verify: `/health` reports provider runpod and endpoint ok; one ingest and one ask succeed from a phone.
6. Submit with the app URL, one input to try, and the ids from the guide's HACKATHON.md. No keys or environment screenshots.

| GalaxyGate criterion | How Vault meets it |
| --- | --- |
| It works | Ingest a pasted transcript, ask a question, facts and a block come back |
| Simple to use | One page, two forms, status text while requests run, README gets a stranger running |
| Reliable | State in VAULT_HOME survives reload; upstream errors surfaced; WAL and per-request connections |
| Secure | Keys only in the server environment; only the server calls RunPod |
| Scalable | Endpoint scales to zero and adds workers; app state outside the container |
| Agentic | The librarian decides its next step from model output: re-ask on invalid JSON, supersede on conflict, skip on low confidence |
| Creativity | Memory that follows you across models, visible in the xo-space, with the injected block shown to the user |
| Extra credit | Second endpoint (embeddings), network volume, a snapshot of the server |

## 12. The agent team

Session A is one Claude Code session in the repo: the orchestrator. It writes Phase 0 itself, runs three subagents in parallel, then the integrator, then integrates. Session B is the deployer on a teammate's machine.

| Phase | Who | Delivers | Done when |
| --- | --- | --- | --- |
| 0 | orchestrator | pyproject, pinned requirements, .env.example, models.py, db.py, mirror.py, providers/base.py and claude.py, tests/test_db.py, the four subagent files, five synthetic transcripts | `pytest tests/test_db.py` green |
| 1 | librarian, retriever, qa in parallel | ingest, scrub, extract, supersede; retrieve/; golden.jsonl, eval runner, ruff config, leak test | Ingest fills memory/; ask returns a block; 25 golden rows |
| 1b | deployer (Session B, branch hosted) | providers/runpod.py, web.py, deploy/, the GalaxyGate server and RunPod endpoint | `/health` ok on the public URL |
| 2 | integrator | cli.py, mcp_server.py, skill/, README.md, SAFETY.md | `claude mcp add` works; docs written |
| 3 | orchestrator | Merge `hosted`, run everything inside the XO Space, run eval, fix, record numbers | pytest and ruff clean; eval/results.md filled |
| 4 | humans | Video, Devpost, Code Registry sync | Submitted |

Subagent files live in .claude/agents/<name>.md with front matter (name, description, tools: Read, Edit, Write, Bash, Grep, Glob) and a body stating owned paths, the done-when line, and the commit prefix.

Synthetic transcripts must cover two projects, one person, one finance mention, one health mention, and a decision that changes between transcript 2 and transcript 4.

## 13. Clock and cut list

Times assume a 2:00 PM kickoff. Shift the middle rows if you start later; the last three rows do not move.

| Time | Checkpoint |
| --- | --- |
| 2:00 PM | Kickoff prompt pasted in Session A; Session B starts accounts |
| 2:30 PM | Phase 0 green; Session B has the server |
| 3:45 PM | Phase 1 green; Session B has `/health` ok |
| 4:20 PM | Phase 2 done |
| 5:00 PM | Phase 3 done, eval numbers recorded |
| 5:00 to 5:25 PM | Three-minute video |
| 5:25 to 5:40 PM | Devpost submitted, all four tracks opted in |
| 5:30 to 6:00 PM | Code Registry account, sync started, team logged at their table |

If Phase 1 is not green by 3:45 PM, cut in this order: embedding endpoint; ChatGPT parser; vault_remember; procedural mirror; the web dashboard page (keep `/ingest`, `/ask`, `/health` as JSON); golden set from 25 to 15.

Never cut: supersession, the scope filter, secret scrubbing, the injection log, `vault eval`, SAFETY.md, `/health`.

## 14. Kickoff prompts

### Session A, orchestrator

```text
You are the orchestrator for the Vault hackathon build. Read PRD.md fully, then AGENTS.md. The deadline is 6:00 PM EDT today; the contracts in PRD.md sections 4 to 9 are fixed.

Phase 0, do this yourself:
1. Create pyproject.toml (package vault under src/, Python 3.12), requirements.txt pinning anthropic, pydantic, typer, mcp, fastapi, uvicorn, httpx, pytest, ruff, and .env.example with every variable in PRD.md section 5.
2. Write src/vault/models.py, db.py, mirror.py, providers/base.py and providers/claude.py exactly to sections 6 and 10, with tests/test_db.py covering schema creation, FTS5 search, supersession and the mirror layout. Run pytest until green.
3. Create .claude/agents/librarian.md, retriever.md, integrator.md and qa.md as described in section 12.
4. Generate five synthetic transcripts in inbox/synthetic/ as Markdown with ## User and ## Assistant blocks, covering two projects, one person, one finance mention, one health mention, and a decision that changes between transcript 2 and transcript 4.
5. Commit as `orchestrator: phase 0 contracts` and append one line to PROGRESS.md.

Phase 1, run librarian, retriever and qa in parallel with the scopes in section 12, and wait for all three.

Phase 2, run the integrator.

Phase 3, do this yourself: merge the branch hosted if it exists; run `vault ingest inbox/synthetic --project .`, `vault ask` with and without --scope, and `vault eval`; fix failures; write the numbers to eval/results.md and PROGRESS.md; run `claude mcp add vault -- python -m vault.mcp_server` and confirm vault_search answers; run pytest and `ruff check .` on main; report the eval numbers to me.

Rules: ask before any step that installs software, deletes files or touches git history. Never commit .env, *.db or inbox/real/. If a contract is ambiguous, ask instead of guessing. Small commits, prefixed with the role.
```

### Session B, deployer

```text
You are the deployer for the Vault hackathon build. Read PRD.md sections 5, 7.3, 10 and 11, then AGENTS.md. Work on the branch hosted and touch only src/vault/providers/runpod.py, src/vault/web.py and deploy/.

1. Implement providers/runpod.py against providers/base.py, using the RunPod endpoint's OpenAI-compatible chat completions route; confirm the exact route with the RunPod MCP before use. Same prompt and schema as providers/claude.py, 90 second timeout, one retry, then ProviderError with the upstream message.
2. Implement web.py per section 7.3 with one inline HTML page and the five routes. WAL mode, one connection per request, errors shown to the user.
3. Follow the hackathon's GalaxyGate guide to create the server, then deploy this branch's web app instead of the demo, with the environment variables from section 5 and VAULT_HOME on a persistent path.
4. Using the RunPod MCP, create a serverless endpoint for the instruct model I name and put its id in RUNPOD_ENDPOINT_ID. If time allows, a second endpoint for the embedding model and a network volume.
5. Verify /health, one ingest and one ask on the public URL. Write deploy/README.md with the URL, the ids and the exact steps. Commit as `deployer: hosted mode` and append one line to PROGRESS.md.

Rules: ask before every step that creates cloud resources or spends credits. Keys go only in the server environment, never in the repo, and never in a screenshot.
```

### Resume, either session

```text
Read PRD.md, AGENTS.md and PROGRESS.md. Find the last completed phase and continue from there with the same rules. Do not redo finished work.
```
