# Hosted mode: GalaxyGate + RunPod

Vault runs from one codebase in two modes (PRD section 1). This directory
covers the hosted one: the FastAPI app in `src/vault/web.py` on a GalaxyGate
server, with extraction served by a RunPod serverless endpoint instead of the
Claude API.

```
browser ──► GalaxyGate server ──► RunPod serverless endpoint (vLLM worker)
            uvicorn vault.web:app        open-weight instruct model
            VAULT_HOME on a persistent
            path (SQLite + WAL)
```

The browser never talks to RunPod. The RunPod key lives only in the server
environment, so the only process holding it is `providers/runpod.py`.

## Status

| Piece | State |
| --- | --- |
| `providers/runpod.py` | Written and unit-tested against a mocked transport (`tests/test_runpod.py`) |
| `web.py`, the five routes, the dashboard | Written and verified locally end to end (ingest → ask → audit, in a real browser) |
| GalaxyGate server + RunPod endpoint | **Not provisioned.** Needs the accounts, the event coupon and the API keys below. |

Everything except the last row is done and merged. The last row is account
work, not code: follow the steps below with your own credentials.

## 1. Accounts and keys

1. Register on GalaxyGate with the hackathon coupon.
2. Sign up for RunPod, add credit, and create an API key named
   `hackathon-app`. **Copy it once** — it is shown exactly once. It goes into
   the server environment and nowhere else: not into this repo, not into a
   screenshot, not into a commit.

## 2. The RunPod endpoint

In an empty folder, add the two MCP servers and sign in:

```bash
claude mcp add -t http galaxygate <galaxygate-mcp-url>
claude mcp add -t http runpod     <runpod-mcp-url>
claude            # then /mcp to authenticate both
```

Using the RunPod MCP (or the console), create a **serverless endpoint** with
the vLLM worker, serving an open-weight instruct model. Note the endpoint id.

`providers/runpod.py` calls the worker's OpenAI-compatible route:

```
POST https://api.runpod.ai/v2/<RUNPOD_ENDPOINT_ID>/openai/v1/chat/completions
Authorization: Bearer <RUNPOD_API_KEY>
```

> Confirm that path against the RunPod console for the worker image you
> actually deploy before the demo. It is the documented route for the vLLM
> worker, but the provider is written so only `RunpodProvider.base_url` has
> to change if your image differs.

Endpoints scale to zero, so the **first** request after an idle period pays a
cold start. `providers/runpod.py` allows 90 seconds and the dashboard says
"a cold model endpoint can take up to a minute" while it waits, rather than
looking hung.

## 3. The server

Follow the GalaxyGate guide to create the server, then deploy this repo
instead of the guide's demo app:

```bash
git clone -b main <this-repo> vault && cd vault
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .
.venv/bin/uvicorn vault.web:app --host 0.0.0.0 --port 8000
```

behind the guide's proxy. Set the environment from `deploy/.env.template`.

`VAULT_HOME` and `VAULT_PROJECT` must be **outside the container's writable
layer** — a host volume — or every redeploy wipes the memory. That is what
makes "results survive a reload" true.

## 4. Verify

```bash
curl -s https://<team>.galaxygate.app/health | jq
```

Expect `"provider": "runpod"` and `"endpoint": {"ok": true, ...}`. The route
returns **503** when the endpoint is unreachable, so it is a usable uptime
check and not just decoration.

Then, from a phone:

1. Open `/`, paste a short transcript, press **Ingest** → counts come back.
2. Ask a question → a context block comes back.
3. Scroll to **What Vault handed out** → both requests are logged.

## 5. Falling back

If RunPod is down or credit runs out mid-demo, the app keeps working:

```bash
VAULT_PROVIDER=offline .venv/bin/uvicorn vault.web:app --host 0.0.0.0 --port 8000
```

`offline` is the deterministic rule-based provider — no key, no network. The
demo degrades from "a model extracted this" to "rules extracted this" rather
than to a stack trace. `VAULT_PROVIDER=claude` with `ANTHROPIC_API_KEY` set
is the other fallback.

## 6. Submission checklist

- [ ] App URL
- [ ] One input for a judge to try (a short `## User` / `## Assistant` transcript
      with a decision in it, then "what did we decide?")
- [ ] The ids from the guide's `HACKATHON.md`
- [ ] No keys, no environment screenshots

## How the GalaxyGate criteria are met

| Criterion | Where |
| --- | --- |
| It works | `/ingest` then `/ask`; facts and a context block come back |
| Simple to use | One page, two numbered forms, no instructions needed |
| Reliable | WAL + one connection per request; state in `VAULT_HOME`; upstream errors rendered as messages |
| Secure | Keys only in the server environment; only the server calls RunPod; `scrub.py` redacts secrets before any write |
| Scalable | Endpoint scales to zero and adds workers; app state outside the container |
| Agentic | `extract.py` re-asks on invalid JSON; `supersede.py` supersedes on conflict |
| Creativity | Memory that follows you across models, with the injected block shown to the user in `/audit` |
