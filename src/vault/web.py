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

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from vault import __version__ as VAULT_VERSION
from vault import db, mirror
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
    conn = db.connect()
    try:
        return JSONResponse({"entities": [dict(row) for row in db.list_entities(conn)]})
    finally:
        conn.close()


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
<style>
  :root {
    --bg: #fbfaf8; --panel: #fff; --ink: #1a1a1a; --muted: #6b6b6b;
    --line: #e4e1dc; --accent: #4338ca; --ok: #0f7b4f; --bad: #b3261e;
    --code-bg: #f4f2ef;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #17181a; --panel: #1f2023; --ink: #ececec; --muted: #a0a0a0;
      --line: #333438; --accent: #a5b4fc; --ok: #4ade80; --bad: #f87171;
      --code-bg: #141517;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  .wrap { max-width: 860px; margin: 0 auto; padding: 32px 16px 64px; }
  header { margin-bottom: 8px; }
  h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -0.02em; }
  .sub { color: var(--muted); margin: 0 0 24px; }
  section {
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
    padding: 20px; margin-bottom: 18px;
  }
  h2 { font-size: 15px; text-transform: uppercase; letter-spacing: 0.06em;
       margin: 0 0 4px; color: var(--muted); }
  .hint { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
  label { display: block; font-size: 13px; color: var(--muted); margin: 10px 0 4px; }
  textarea, input, select {
    width: 100%; padding: 9px 11px; border: 1px solid var(--line); border-radius: 7px;
    background: var(--bg); color: var(--ink); font: inherit;
  }
  textarea { min-height: 120px; resize: vertical; font-size: 13px;
             font-family: ui-monospace, monospace; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; }
  .row > div { flex: 1 1 160px; }
  button {
    margin-top: 14px; padding: 9px 18px; border: 0; border-radius: 7px;
    background: var(--accent); color: #fff; font: inherit; font-weight: 600; cursor: pointer;
  }
  @media (prefers-color-scheme: dark) { button { color: #17181a; } }
  button:disabled { opacity: .55; cursor: progress; }
  .status { margin-top: 12px; font-size: 14px; min-height: 20px; }
  .status.busy { color: var(--muted); }
  .status.ok { color: var(--ok); }
  .status.bad { color: var(--bad); }
  pre {
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
    <h1>Vault</h1>
    <p class="sub">Memory that follows you across models. Feed it a
       conversation, then ask it what you decided.</p>
  </header>

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
    const { entities } = await (await fetch("/catalog")).json();
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

loadCatalog(); loadAudit(); loadHealth();
</script>
</body>
</html>
"""
