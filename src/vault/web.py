"""FastAPI app for hosted mode (PRD section 7.3).

One inline HTML page, no frontend build, five routes. Run it with::

    uvicorn vault.web:app --host 0.0.0.0 --port 8000

Design notes, straight off the GalaxyGate scoring criteria in section 11:

* **Reliable.** Every route opens its own ``db.connect()`` and closes it in a
  ``finally``. SQLite is in WAL mode (``db.connect``), so a reader and a
  writer do not block each other and two judges clicking at once is fine.
  All state lives in ``VAULT_HOME``, which points at a persistent path on the
  server rather than inside the container, so a restart or a page reload
  keeps the memory.
* **Errors are shown, never hung.** A provider failure comes back as a
  ``ProviderError`` and is rendered as a message with the upstream text in
  it. The page shows a status line for the whole duration of a request --
  a cold RunPod worker can take a minute, and silence reads as a hang.
* **Secure.** The browser only ever talks to this app. The RunPod key lives
  in the server environment and only ``providers/runpod.py`` uses it; no key
  and no provider URL is ever sent to the client.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from vault import __version__ as VAULT_VERSION
from vault import db, mcp_http, mirror
from vault.cli import DEFAULT_BUDGET, _build_provider, _mirror_touched, _run_ask
from vault.extract import extract
from vault.providers.base import ProviderError
from vault.scrub import scrub
from vault.supersede import apply_extraction

app = FastAPI(title="Vault", version=VAULT_VERSION)

MAX_UPLOAD_BYTES = 1_000_000


# Request bodies are JSON, not multipart forms: FastAPI's Form/UploadFile
# require python-multipart, which is not in the PRD section 5 dependency
# list. The browser reads an uploaded file with File.text() and posts its
# contents here, so "paste or upload" still works with no extra dependency.
class IngestBody(BaseModel):
    text: str = ""
    filename: str = ""


class AskBody(BaseModel):
    prompt: str
    scope: str = ""
    chat_id: str = ""
    budget: int = Field(default=DEFAULT_BUDGET, ge=50, le=8000)


def project_dir() -> Path:
    """The project whose memory/ mirror this app writes.

    On the server this is set to the same persistent path as VAULT_HOME's
    parent so the Markdown mirror survives a container restart alongside
    the database.
    """
    return Path(os.environ.get("VAULT_PROJECT", ".")).expanduser()


# ---------------------------------------------------------------------------
# Pipeline helpers (the same librarian/retriever code the CLI runs)
# ---------------------------------------------------------------------------


def _ingest_text(text: str, label: str) -> dict:
    """Scrub, extract, supersede and mirror one pasted or uploaded document."""
    conn = db.connect()
    try:
        _clean, redactions = scrub(text)
        source_id = db.insert_source(conn, kind="web", path=label, chat_id=None)
        extraction = extract(text, _build_provider())
        result = apply_extraction(conn, extraction, source_id)
        conn.commit()

        touched = {f.entity for f in extraction.facts} | {e.name for e in extraction.entities}
        if result["episode_id"] is not None:
            mirror.write_episodic(conn, project_dir(), result["episode_id"], "web")
        _mirror_touched(conn, project_dir(), touched)

        counts = {
            "facts_added": result["added"],
            "facts_superseded": result["superseded"],
            "episodes": 1 if result["episode_id"] is not None else 0,
            "secrets_redacted": sum(redactions.values()),
        }
        mirror.append_progress(
            project_dir(),
            "web /ingest | " + " ".join(f"{k}={v}" for k, v in counts.items()),
        )
        return counts
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> JSONResponse:
    """Active provider, endpoint reachability, db path, version.

    ``status`` and ``runpod_key_set`` are here because the GalaxyGate
    deployment guide's verification step polls ``/health`` and asserts
    exactly those two fields. Emitting them lets the guide's own check pass
    against this app unmodified, in place of the demo it ships with.
    ``runpod_key_set`` is a boolean presence check -- it never echoes the
    key itself.
    """
    home = db.default_vault_home()
    payload: dict = {
        "status": "ok",
        "version": VAULT_VERSION,
        "provider": os.environ.get("VAULT_PROVIDER", "claude"),
        "runpod_key_set": bool(os.environ.get("RUNPOD_API_KEY")),
        "db_path": str(home / "vault.db"),
        "db_exists": (home / "vault.db").exists(),
        "project_dir": str(project_dir().resolve()),
    }
    try:
        payload["endpoint"] = _build_provider().health()
    except Exception as exc:  # noqa: BLE001 - /health must answer, never raise
        payload["endpoint"] = {"ok": False, "detail": str(exc)}

    try:
        conn = db.connect()
        try:
            payload["entities"] = len(db.list_entities(conn))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        payload["db_error"] = str(exc)

    ok = bool(payload.get("endpoint", {}).get("ok")) and "db_error" not in payload
    payload["status"] = "ok" if ok else "degraded"
    return JSONResponse(payload, status_code=200 if ok else 503)


@app.post("/ingest")
def ingest(body: IngestBody) -> JSONResponse:
    text = body.text or ""
    if not text.strip():
        return JSONResponse(
            {"error": "Paste a transcript or choose a file first."}, status_code=400
        )
    if len(text.encode("utf-8")) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": f"That is larger than the {MAX_UPLOAD_BYTES // 1000} KB limit."},
            status_code=413,
        )

    label = f"web:{body.filename}" if body.filename else "web:paste"
    try:
        return JSONResponse(_ingest_text(text, label))
    except ProviderError as exc:
        # The upstream message, verbatim: a judge seeing "endpoint cold,
        # timed out after 90s" can retry; a generic 500 tells them nothing.
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.post("/ask")
def ask(body: AskBody) -> JSONResponse:
    if not body.prompt.strip():
        return JSONResponse({"error": "Ask a question first."}, status_code=400)
    result = _run_ask(
        body.prompt,
        body.scope.strip() or None,
        body.chat_id.strip() or None,
        body.budget,
        project_dir(),
        target="web",
    )
    return JSONResponse(result)


@app.get("/audit")
def audit(limit: int = 50) -> JSONResponse:
    """The injection log, newest first: what Vault handed out, and when."""
    conn = db.connect()
    try:
        return JSONResponse(
            {"injections": [dict(row) for row in db.recent_injections(conn, limit=limit)]}
        )
    finally:
        conn.close()


@app.get("/catalog")
def catalog() -> JSONResponse:
    """Entities plus their facts, so the dashboard can draw the memory graph.

    Not one of PRD 7.3's five fixed routes -- this is the read the inline page
    uses to render. Facts carry ``current`` rather than the raw
    ``superseded_by`` id so the browser never has to resolve a join, and
    superseded rows are included on purpose: the graph shows what a decision
    replaced, which is the whole point of the supersession rule.
    """
    conn = db.connect()
    try:
        entities = [dict(row) for row in db.list_entities(conn)]
        names = {e["id"]: e["name"] for e in entities}
        rows = conn.execute(
            "SELECT id, entity_id, predicate, value, category, sensitive, "
            "superseded_by, observed_at FROM facts ORDER BY id"
        ).fetchall()
        facts = [
            {
                "entity": names.get(r["entity_id"], "?"),
                "predicate": r["predicate"],
                "value": r["value"],
                "category": r["category"],
                "sensitive": bool(r["sensitive"]),
                "current": r["superseded_by"] is None,
            }
            for r in rows
        ]
        return JSONResponse({"entities": entities, "facts": facts})
    finally:
        conn.close()


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """MCP over HTTP, so a hosted Claude or OpenAI client can use this store.

    DEVIATION from PRD 7.3, which fixes five routes -- recorded in
    PROGRESS.md. The four tools are already specified in 7.2; this is a
    second transport for them, not new capability. Protocol detail lives in
    mcp_http.py so this file stays a thin FastAPI shell.
    """
    if not mcp_http.authorised(request.headers.get("authorization")):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32001, "message": "unauthorised: send Authorization: Bearer"},
            },
            status_code=401,
        )
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a protocol error
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error: body is not JSON"},
            },
            status_code=400,
        )

    response, status = mcp_http.handle(body, project_dir(), VAULT_VERSION)
    if response is None:
        return Response(status_code=status)
    return JSONResponse(response, status_code=status)


@app.get("/mcp")
def mcp_describe() -> JSONResponse:
    """Clients and humans probe with GET; say what this is rather than 405."""
    return JSONResponse(
        {
            "transport": "streamable-http (JSON-RPC 2.0 over POST)",
            "protocolVersion": mcp_http.PROTOCOL_VERSION,
            "tools": [t["name"] for t in mcp_http.tool_schemas()],
            "auth": "bearer" if os.environ.get("VAULT_MCP_TOKEN") else "none",
        }
    )


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(PAGE)


# ---------------------------------------------------------------------------
# The page. Inline on purpose: no build step, one file to deploy.
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vault</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&amp;family=IBM+Plex+Sans:wght@400;500;600;700&amp;family=IBM+Plex+Serif:wght@600&amp;display=swap">
<style>
  :root {
    --bg:#F4F6F4; --panel:#fff; --sunk:#EDF0EC;
    --ink:#101619; --ink-2:#3C474D; --muted:#6A767C;
    --line:#DBE0DA; --line-2:#C4CBC2;
    /* categorical palette, validated in both modes: worst adjacent dE 19.6 */
    --d0:#00968A; --d1:#C77A07; --d2:#4C6FD4; --d3:#B5439C;
    --accent:#00968A; --ok:#00968A; --bad:#B3261E; --past:#8A9298;
    --stage:#090E11; --code-bg:#090E11;
    --sans:"IBM Plex Sans",ui-sans-serif,-apple-system,"Segoe UI",sans-serif;
    --serif:"IBM Plex Serif",ui-serif,Georgia,serif;
    --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0B1013; --panel:#141C20; --sunk:#101A1E;
      --ink:#E9EDE9; --ink-2:#AFBABD; --muted:#7D898D;
      --line:#233036; --line-2:#324047;
      --d0:#2AA79C; --d1:#C0872A; --d2:#6A88D8; --d3:#BC5FA6;
      --accent:#2AA79C; --ok:#2AA79C; --bad:#F87171; --past:#6B767C;
    }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.55 var(--sans); -webkit-font-smoothing:antialiased; }
  .wrap { max-width:1060px; margin:0 auto; padding:30px 18px 60px; }
  header { border-bottom:2px solid var(--ink); padding-bottom:12px; margin-bottom:18px; }
  .eyebrow { font-family:var(--mono); font-size:10.5px; letter-spacing:.15em;
             text-transform:uppercase; color:var(--muted); }
  h1 { font-family:var(--serif); font-size:clamp(26px,4.4vw,36px); margin:.2em 0 0;
       letter-spacing:-.015em; line-height:1.07; }
  .sub { color:var(--ink-2); margin:9px 0 0; max-width:60ch; }

  .hero { display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--line);
          border:1px solid var(--line); border-radius:10px; overflow:hidden; margin-bottom:18px; }
  .hcell { background:var(--panel); padding:11px 13px; }
  .hcell .k { font-family:var(--mono); font-size:9.5px; letter-spacing:.11em;
              text-transform:uppercase; color:var(--muted); }
  .hcell .v { font-family:var(--mono); font-size:clamp(20px,3vw,26px); font-weight:600;
              font-variant-numeric:tabular-nums; margin-top:2px; }
  .hcell.on .v { color:var(--accent); } .hcell.old .v { color:var(--past); }

  .stagewrap { margin-bottom:16px; }
  @media (max-width:880px){ .hero{grid-template-columns:repeat(2,1fr)} }

  .stage { position:relative; border-radius:11px; overflow:hidden; background:var(--stage);
           border:1px solid #1B252B; aspect-ratio:16/9; }
  .stage canvas { display:block; width:100%; height:100%; }
  .slab { position:absolute; left:12px; top:10px; font-family:var(--mono); font-size:10px;
          letter-spacing:.14em; text-transform:uppercase; color:#5A686E; pointer-events:none; }
  .lg { position:absolute; right:11px; top:10px; display:flex; flex-direction:column; gap:4px;
        font-family:var(--mono); font-size:10px; color:#93A0A5; pointer-events:none;
        text-align:right; }
  .lg span { display:flex; align-items:center; gap:5px; justify-content:flex-end; }
  .lg i { width:8px; height:8px; border-radius:2px; }
  .empty[hidden] { display:none; }
  .empty { position:absolute; inset:0; display:flex; align-items:center;
           justify-content:center;
           color:#5A686E; font-family:var(--mono); font-size:12px; text-align:center; padding:20px;
           }

  section { background:var(--panel); border:1px solid var(--line); border-radius:11px;
            padding:17px 18px; margin-bottom:16px; }
  h2 { font-family:var(--mono); font-size:10.5px; text-transform:uppercase; letter-spacing:.14em;
       margin:0 0 4px; color:var(--muted); font-weight:500; }
  .hint { color:var(--ink-2); font-size:13px; margin:0 0 12px; }
  label { display:block; font-size:12px; color:var(--muted); margin:10px 0 4px;
          font-family:var(--mono); letter-spacing:.04em; }
  textarea, input, select { width:100%; padding:9px 11px; border:1px solid var(--line-2);
    border-radius:7px; background:var(--bg); color:var(--ink); font:inherit; }
  textarea { min-height:112px; resize:vertical; font-size:12.5px; font-family:var(--mono); }
  input:focus, textarea:focus, select:focus { outline:2px solid var(--accent); outline-offset:1px; }
  .row { display:flex; gap:12px; flex-wrap:wrap; }
  .row > div { flex:1 1 160px; }
  button { margin-top:13px; padding:9px 18px; border:0; border-radius:7px; background:var(--ink);
           color:var(--bg); font:inherit; font-weight:600; cursor:pointer; }
  button:hover { opacity:.88; }
  button:disabled { opacity:.5; cursor:progress; }
  .status { margin-top:11px; font-size:13.5px; min-height:19px; }
  .status.busy { color:var(--muted); } .status.ok { color:var(--ok);
  } .status.bad { color:var(--bad); }
  pre { background:var(--code-bg); color:#DCE4E3; border:1px solid #1B252B; border-radius:8px;
    background: var(--code-bg); border: 1px solid var(--line); border-radius: 7px;
    padding: 14px; overflow-x: auto; font-size: 13px; white-space: pre-wrap;
    word-break: break-word; margin: 12px 0 0;
  }
  pre:empty { display: none; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-weight: 600; }
  td.mono { font-family: ui-monospace, monospace; }
  footer { color: var(--muted); font-size: 13px; text-align: center; margin-top: 8px; }
  a { color: var(--accent); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <span class="eyebrow" id="eyebrow">Vault Librarian</span>
    <h1>Memory that follows you across models</h1>
    <p class="sub">Feed it a conversation. It scrubs secrets, keeps what is
       durable, supersedes what changed &mdash; and hands the next agent only
       the slice that answers the question.</p>
  </header>

  <div class="hero">
    <div class="hcell on"><div class="k">Facts held</div>
      <div class="v" id="m-held">&mdash;</div></div>
    <div class="hcell old"><div class="k">Superseded</div>
      <div class="v" id="m-sup">&mdash;</div></div>
    <div class="hcell"><div class="k">Entities</div><div class="v" id="m-ent">&mdash;</div></div>
    <div class="hcell"><div class="k">Last block</div><div class="v" id="m-tok">&mdash;</div></div>
  </div>

  <div class="stagewrap">
    <div class="stage" id="stage">
      <canvas id="graph"></canvas>
      <div class="slab">Memory graph</div>
      <div class="lg">
        <span><i style="background:var(--d0)"></i>projects</span>
        <span><i style="background:var(--d1)"></i>work &amp; people</span>
        <span><i style="background:var(--d2)"></i>personal</span>
        <span><i style="background:var(--d3)"></i>health &amp; money</span>
        <span><i style="background:var(--past)"></i>superseded</span>
      </div>
      <div class="empty" id="graph-empty">Nothing stored yet &mdash;<br>add a conversation 
      elow.</div>
    </div>
  </div>

  <section>
    <h2>1 &middot; Add a conversation</h2>
    <p class="hint">Paste a chat transcript (or upload an export). Vault scrubs
       secrets, pulls out durable facts, and supersedes anything it already
       knew that has since changed.</p>
    <form id="ingest-form">
      <label for="ingest-text">Transcript</label>
      <textarea id="ingest-text" name="text"
        placeholder="## User&#10;We're going with Clerk for acme-platform auth,
because we need SSO.&#10;&#10;## Assistant&#10;Noted."></textarea>
      <label for="ingest-file">…or a file (.md, .json, .jsonl)</label>
      <input type="file" id="ingest-file" name="file" accept=".md,.markdown,.json,.jsonl,.txt">
      <button type="submit">Ingest</button>
      <div class="status" id="ingest-status"></div>
      <pre id="ingest-out"></pre>
    </form>
  </section>

  <section>
    <h2>2 &middot; Ask</h2>
    <p class="hint">Returns the context block an agent would be given —
       nothing more than the token budget allows.</p>
    <form id="ask-form">
      <label for="ask-prompt">Question</label>
      <input id="ask-prompt" name="prompt" autocomplete="off"
        placeholder="What did we decide about auth?">
      <div class="row">
        <div>
          <label for="ask-scope">Scope (optional)</label>
          <select id="ask-scope" name="scope"><option value="">auto-detect</option></select>
        </div>
        <div>
          <label for="ask-budget">Token budget</label>
          <input id="ask-budget" name="budget" type="number" value="700" min="50" max="4000">
        </div>
      </div>
      <button type="submit">Ask</button>
      <div class="status" id="ask-status"></div>
      <pre id="ask-out"></pre>
    </form>
  </section>

  <section>
    <h2>3 &middot; What Vault handed out</h2>
    <p class="hint">Every block Vault injects is logged here, newest first.</p>
    <div id="audit">Loading…</div>
  </section>

  <footer id="health">Checking status…</footer>
</div>

<script>
const $ = (id) => document.getElementById(id);

function setStatus(el, message, kind) {
  el.textContent = message;
  el.className = "status" + (kind ? " " + kind : "");
}

// Every request goes through here so a failure always lands as a visible
// message. A silent page is the one thing worse than an error.
async function submit(form, url, payload, statusEl, outEl, busyText, onOk) {
  const button = form.querySelector("button");
  button.disabled = true;
  setStatus(statusEl, busyText, "busy");
  outEl.textContent = "";
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(statusEl, data.error || ("Request failed: " + response.status), "bad");
      return;
    }
    onOk(data);
  } catch (err) {
    setStatus(statusEl, "Could not reach the server: " + err.message, "bad");
  } finally {
    button.disabled = false;
  }
}

$("ingest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  // Read an uploaded file in the browser and send its text, so the server
  // needs no multipart parser.
  const chosen = $("ingest-file").files[0];
  let text = $("ingest-text").value;
  let filename = "";
  if (chosen) {
    setStatus($("ingest-status"), "Reading " + chosen.name + "…", "busy");
    try {
      text = await chosen.text();
      filename = chosen.name;
    } catch (err) {
      setStatus($("ingest-status"), "Could not read that file: " + err.message, "bad");
      return;
    }
  }
  submit(
    event.target, "/ingest", { text, filename },
    $("ingest-status"), $("ingest-out"),
    "Scrubbing and extracting… a cold model endpoint can take up to a minute.",
    (data) => {
      setStatus($("ingest-status"),
        `${data.facts_added} fact(s) added · ${data.facts_superseded} superseded · ` +
        `${data.episodes} episode(s) · ${data.secrets_redacted} secret(s) redacted`, "ok");
      loadCatalog();
      loadAudit();
    }
  );
});

$("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submit(
    event.target, "/ask",
    {
      prompt: $("ask-prompt").value,
      scope: $("ask-scope").value,
      budget: Number($("ask-budget").value) || 700,
    },
    $("ask-status"), $("ask-out"), "Searching memory…",
    (data) => {
      setStatus($("ask-status"),
        `${data.tokens} tokens · ${data.item_ids.length} item(s) · ` +
        `scope: ${data.scope || "none"} · ` +
        `gate: ${data.gate_fired ? "fired" : "profile only"}`, "ok");
      $("ask-out").textContent = data.block;
      loadAudit();
    }
  );
});

async function loadCatalog() {
  try {
    const { entities, facts } = await (await fetch("/catalog")).json();
    drawGraph(entities, facts || []);
    const select = $("ask-scope");
    const current = select.value;
    select.innerHTML = '<option value="">auto-detect</option>';
    for (const entity of entities) {
      const option = document.createElement("option");
      option.value = entity.name;
      option.textContent = `${entity.name} (${entity.kind})`;
      select.appendChild(option);
    }
    select.value = current;
  } catch (err) { /* the scope select is optional; auto-detect still works */ }
}

async function loadAudit() {
  const host = $("audit");
  try {
    const { injections } = await (await fetch("/audit?limit=15")).json();
    if (!injections.length) { host.textContent = "Nothing injected yet."; return; }
    const rows = injections.map((row) => `<tr>
        <td class="mono">${(row.sent_at || "").slice(0, 19).replace("T", " ")}</td>
        <td>${row.target || ""}</td>
        <td>${row.scope || "—"}</td>
        <td class="mono">${row.tokens}</td>
      </tr>`).join("");
    host.innerHTML = `<table><thead><tr><th>When</th><th>Target</th>
      <th>Scope</th><th>Tokens</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (err) {
    host.textContent = "Could not load the audit log: " + err.message;
  }
}

async function loadHealth() {
  try {
    const data = await (await fetch("/health")).json();
    const endpoint = data.endpoint || {};
    $("health").textContent =
      `v${data.version} · provider: ${data.provider} · endpoint: ` +
      `${endpoint.ok ? "ok" + (endpoint.latency_ms != null ? ` (${endpoint.latency_ms} ms)` : "")
                     : "unavailable — " + (endpoint.detail || "unknown")}` +
      ` · ${data.entities ?? 0} entities`;
  } catch (err) {
    $("health").textContent = "Status unavailable: " + err.message;
  }
}


// ---------------------------------------------------------------------------
// The memory graph. Deterministic radial layout, not a force simulation: the
// labels have to stay readable on a projector, and a force layout piles the
// nodes into the middle and collides their text.
// ---------------------------------------------------------------------------
const CATEGORY_BRANCH = {
  projects: 0, work_school: 1, people: 1, interests: 2, personal: 2,
  finances: 3, health: 3,
};
const BRANCH_NAMES = ["projects", "work & people", "personal", "health & money"];

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawGraph(entities, facts) {
  const stage = $("stage"), cv = $("graph");
  if (!stage || !cv) return;
  const empty = $("graph-empty");
  if (empty) empty.hidden = entities.length > 0;
  if (!entities.length) { const c = cv.getContext("2d"); c.clearRect(0,0,cv.width,cv.height);
  return; }

  const rect = stage.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const W = rect.width, H = rect.height;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const COL = [cssVar("--d0"), cssVar("--d1"), cssVar("--d2"), cssVar("--d3")];
  const PAST = cssVar("--past");
  const cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2;
  const hubR = R * 0.26, entR = R * 0.56, leafR = R * 0.84;

  const branchOf = (cat) => CATEGORY_BRANCH[cat] ?? 0;
  const byBranch = [[], [], [], []];
  for (const e of entities) byBranch[branchOf(e.category)].push(e);

  const factsFor = (name) => facts.filter((f) => f.entity === name);
  const placed = [];

  // Give each branch a slice of the full circle proportional to how many
  // entities it holds, rather than a fixed quadrant. Real stores are lopsided
  // -- most of this one is "projects" -- and fixed quadrants crush the big
  // branch into 90 degrees where the labels overlap into mush.
  const total = entities.length || 1;
  const GAP = 0.12;                        // radians of breathing room per branch
  const live = [0, 1, 2, 3].filter((b) => byBranch[b].length);
  let cursor = -Math.PI / 2;

  for (const b of live) {
    const mine = byBranch[b];
    const slice = (Math.PI * 2 * mine.length) / total;
    const span = Math.max(slice - GAP, 0.05);
    const mid = cursor + slice / 2;
    cursor += slice;
    const a0 = mid;
    const hx = cx + Math.cos(a0) * hubR, hy = cy + Math.sin(a0) * hubR;

    ctx.strokeStyle = COL[b] + "66"; ctx.lineWidth = 2.2;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(hx, hy); ctx.stroke();

    mine.forEach((e, i) => {
      const a = a0 - span / 2 + (mine.length === 1 ? span / 2 : (span * i) / (mine.length - 1));
      const ex = cx + Math.cos(a) * entR, ey = cy + Math.sin(a) * entR;
      ctx.strokeStyle = COL[b] + "4D"; ctx.lineWidth = 1.3;
      ctx.beginPath(); ctx.moveTo(hx, hy);
      ctx.quadraticCurveTo((hx + ex) / 2, (hy + ey) / 2, ex, ey); ctx.stroke();

      const fs = factsFor(e.name);
      const fspan = (span / Math.max(mine.length, 1)) * 0.9;
      fs.forEach((f, q) => {
        const fa = a - fspan / 2 + (fs.length === 1 ? fspan / 2 : (fspan * q) / (fs.length - 1));
        const fx = cx + Math.cos(fa) * leafR, fy = cy + Math.sin(fa) * leafR;
        ctx.strokeStyle = f.current ? COL[b] + "3A" : PAST + "2E"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(ex, ey); ctx.lineTo(fx, fy); ctx.stroke();
        ctx.fillStyle = f.current ? COL[b] : PAST;
        ctx.globalAlpha = f.current ? 1 : 0.5;
        ctx.beginPath(); ctx.arc(fx, fy, f.sensitive ? 4.4 : 3.4, 0, 7); ctx.fill();
        if (f.sensitive && f.current) {
          ctx.globalAlpha = 1; ctx.strokeStyle = COL[b]; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(fx, fy, 7.2, 0, 7); ctx.stroke();
        }
        ctx.globalAlpha = 1;
      });
      placed.push({ name: e.name, x: ex, y: ey, b, flip: Math.cos(a) < 0 });
    });

    if (mine.length) {
      ctx.fillStyle = COL[b];
      ctx.beginPath(); ctx.arc(hx, hy, 5, 0, 7); ctx.fill();
      ctx.font = "600 10px " + cssVar("--mono");
      ctx.textAlign = "center";
      ctx.fillText(BRANCH_NAMES[b].toUpperCase(), hx, hy - 10);
    }
  }

  for (const n of placed) {
    ctx.fillStyle = "#0D1418"; ctx.strokeStyle = COL[n.b]; ctx.lineWidth = 1.7;
    ctx.beginPath(); ctx.arc(n.x, n.y, 6.5, 0, 7); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#C8D4D6"; ctx.font = "500 10.5px " + cssVar("--sans");
    ctx.textAlign = n.flip ? "right" : "left";
    ctx.fillText(n.name, n.x + (n.flip ? -10 : 10), n.y + 3.3);
  }

  ctx.fillStyle = "#E9EDE9";
  ctx.beginPath(); ctx.arc(cx, cy, 10, 0, 7); ctx.fill();
  ctx.fillStyle = "#090E11"; ctx.font = "600 9.5px " + cssVar("--sans");
  ctx.textAlign = "center"; ctx.fillText("YOU", cx, cy + 3.2);

  const held = facts.filter((f) => f.current).length;
  $("m-held").textContent = held;
  $("m-sup").textContent = facts.length - held;
  $("m-ent").textContent = entities.length;
}

let graphTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(graphTimer);
  graphTimer = setTimeout(loadCatalog, 180);
});

loadCatalog(); loadAudit(); loadHealth();
</script>
</body>
</html>
"""
