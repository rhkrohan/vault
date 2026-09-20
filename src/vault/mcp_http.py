"""MCP over HTTP (JSON-RPC 2.0), so remote clients can reach the same four
tools ``mcp_server.py`` serves over stdio.

PRD section 7.2 already specifies vault_catalog, vault_search, vault_get and
vault_remember. This adds no new capability -- only a second transport, so
one running Vault can be attached to a desktop client over stdio *and* to a
hosted client over HTTPS at the same time, against the same store.

Why this is hand-rolled rather than using the SDK: ``mcp`` is pinned at 1.0.0
by PRD section 5, and that release ships the stdio transport only. Bumping it
would break the fixed dependency list for a wire format that is, at this
level, plain JSON-RPC 2.0 over POST -- which needs no library.

``mcp_server.TOOLS`` stays the single source of truth for the tool schemas so
the two transports cannot drift. Its *handlers* are deliberately not reused:
``mcp_server.PROJECT_DIR`` is bound to ``Path(".")`` at import time because a
stdio server inherits the client's working directory, whereas a hosted server
must read ``VAULT_PROJECT``. Dispatch here takes the project directory as an
argument instead.
"""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path

from vault import db
from vault.cli import DEFAULT_BUDGET, _build_provider, _remember_text, _run_ask
from vault.providers.base import ProviderError

# The revision of the MCP spec this endpoint speaks.
PROTOCOL_VERSION = "2025-06-18"


def tool_schemas() -> list[dict]:
    """The same tool list stdio advertises, as plain JSON-RPC dicts."""
    from vault.mcp_server import TOOLS

    return [
        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
        for t in TOOLS
    ]


def authorised(auth_header: str | None) -> bool:
    """Bearer check, enforced only when ``VAULT_MCP_TOKEN`` is set.

    ``vault_remember`` writes to the store, so a publicly reachable endpoint
    should not be open to anyone who finds the URL. Any deployment that sets
    the variable gets a check; left unset (a laptop), the endpoint stays open
    and a client needs no extra configuration.
    """
    token = os.environ.get("VAULT_MCP_TOKEN", "").strip()
    if not token:
        return True
    header = (auth_header or "").strip()
    if not header.startswith("Bearer "):
        return False
    # compare_digest, not ==: a plain string compare returns as soon as two
    # bytes differ, so response time leaks how much of the token a caller
    # guessed correctly and the secret can be recovered a character at a time.
    return hmac.compare_digest(header[7:].strip(), token)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _catalog(project: Path) -> str:
    memory = project / "memory"
    parts = []
    for filename in ("profile.md", "catalog.md"):
        path = memory / filename
        parts.append(
            path.read_text().strip()
            if path.exists()
            else f"(no {filename} yet -- ingest a conversation first)"
        )
    return "\n\n".join(parts) + "\n"


def _search(project: Path, args: dict) -> str:
    result = _run_ask(
        args["query"],
        args.get("scope") or None,
        args.get("chat_id") or None,
        int(args.get("budget") or DEFAULT_BUDGET),
        project,
        target="mcp-http",
    )
    footer = json.dumps({"tokens": result["tokens"], "item_ids": result["item_ids"]})
    return f"{result['block']}\n{footer}"


def _get(entity: str) -> str:
    conn = db.connect()
    try:
        row = db.get_entity_by_name(conn, entity)
        if row is None:
            return f"(no entity named '{entity}' in the catalog)"
        facts = db.current_facts_for_entity(conn, row["id"])
        lines = [f"{row['name']} ({row['kind']}, {row['category']}): {row['description']}", ""]
        if not facts:
            lines.append("(no current facts)")
        for fact in facts:
            marker = " (sensitive)" if fact["sensitive"] else ""
            lines.append(f"- {fact['predicate']} = {fact['value']}{marker} — {fact['observed_at']}")
        return "\n".join(lines)
    finally:
        conn.close()


def _remember(project: Path, args: dict) -> str:
    try:
        written = _remember_text(
            args["text"], args.get("entity") or None, project, _build_provider()
        )
    except ProviderError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(written)


def call_tool(name: str, args: dict, project: Path) -> str:
    """Run one tool against `project`. Raises KeyError for an unknown name."""
    if name == "vault_catalog":
        return _catalog(project)
    if name == "vault_search":
        return _search(project, args)
    if name == "vault_get":
        return _get(args["entity"])
    if name == "vault_remember":
        return _remember(project, args)
    raise KeyError(name)


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------


def _error(rpc_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _result(rpc_id: object, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": payload}


def handle(body: object, project: Path, version: str) -> tuple[dict | None, int]:
    """Handle one JSON-RPC message. Returns (response, http_status).

    A response of ``None`` means the message was a notification, which the
    spec says must be answered with no body.
    """
    if isinstance(body, list):
        return _error(None, -32600, "batch requests are not supported"), 400
    if not isinstance(body, dict):
        return _error(None, -32600, "request must be a JSON object"), 400

    rpc_id = body.get("id")
    method = body.get("method") or ""
    params = body.get("params") or {}

    if method.startswith("notifications/"):
        return None, 202

    if method == "initialize":
        return _result(
            rpc_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "vault", "version": version},
                "instructions": (
                    "Vault is a personal memory store. Call vault_catalog once per "
                    "session, vault_search before planning, and vault_remember when "
                    "the user states a durable decision, preference or fact. Never "
                    "paste raw transcript text into memory yourself -- vault_remember "
                    "scrubs secrets and extracts the durable parts."
                ),
            },
        ), 200

    if method == "ping":
        return _result(rpc_id, {}), 200

    if method == "tools/list":
        return _result(rpc_id, {"tools": tool_schemas()}), 200

    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        try:
            text = call_tool(name, args, project)
        except KeyError:
            return _error(rpc_id, -32602, f"unknown tool: {name}"), 200
        except Exception as exc:  # noqa: BLE001 - a failing tool is a result, not a crash
            return _result(
                rpc_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            ), 200
        return _result(
            rpc_id, {"content": [{"type": "text", "text": text}], "isError": False}
        ), 200

    return _error(rpc_id, -32601, f"method not found: {method}"), 200
