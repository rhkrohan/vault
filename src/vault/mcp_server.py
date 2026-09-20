"""Vault's MCP server (PRD section 7.2): vault_catalog, vault_search,
vault_get, vault_remember, over stdio.

Register with:

    claude mcp add vault -- python -m vault.mcp_server

Every tool here calls into the same pipeline `cli.py` uses (``_run_ask``,
``_remember_text``, ``_build_provider``) rather than reimplementing
retrieval or extraction logic.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from vault import db
from vault.cli import DEFAULT_BUDGET, _build_provider, _remember_text, _run_ask
from vault.providers.base import ProviderError

server = Server("vault")

# The project directory whose memory/ this server reads and writes. MCP
# stdio servers inherit the client's working directory (the XO Space
# project Claude Code was launched from), so this matches `vault ask`'s
# `--project .` default rather than introducing a new env var.
PROJECT_DIR = Path(".")


def _profile_md() -> str:
    path = PROJECT_DIR / "memory" / "profile.md"
    return path.read_text() if path.exists() else "(no profile yet -- run `vault ingest` first)"


def _catalog_md() -> str:
    path = PROJECT_DIR / "memory" / "catalog.md"
    return path.read_text() if path.exists() else "(no catalog yet -- run `vault ingest` first)"


def _vault_catalog() -> str:
    return _profile_md().strip() + "\n\n" + _catalog_md().strip() + "\n"


def _vault_search(
    query: str,
    budget: int = DEFAULT_BUDGET,
    scope: str | None = None,
    chat_id: str | None = None,
) -> str:
    result = _run_ask(query, scope, chat_id, budget, PROJECT_DIR, target="mcp")
    footer = json.dumps({"tokens": result["tokens"], "item_ids": result["item_ids"]})
    return f"{result['block']}\n{footer}"


def _vault_get(entity: str) -> str:
    conn = db.connect()
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


def _vault_remember(text: str, entity: str | None = None) -> str:
    try:
        provider = _build_provider()
        result = _remember_text(text, entity, PROJECT_DIR, provider)
    except ProviderError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(result)


TOOLS: list[types.Tool] = [
    types.Tool(
        name="vault_catalog",
        description="Return the profile block plus the entity catalog. Call once per session.",
        inputSchema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="vault_search",
        description=(
            "Search vault memory for a query and return a token-budgeted context "
            "block. Call before planning."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "budget": {"type": "integer", "default": DEFAULT_BUDGET},
                "scope": {"type": ["string", "null"], "default": None},
                "chat_id": {"type": ["string", "null"], "default": None},
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="vault_get",
        description="Return the current facts for one catalog entity.",
        inputSchema={
            "type": "object",
            "properties": {"entity": {"type": "string"}},
            "required": ["entity"],
        },
    ),
    types.Tool(
        name="vault_remember",
        description=(
            "Extract durable facts/entities/an episode from text and write them "
            "into the vault. Call when the user states a durable decision, "
            "preference or fact. Never pass raw transcript text into memory/ "
            "yourself -- this tool does the scrubbing and extraction."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "entity": {"type": ["string", "null"], "default": None},
            },
            "required": ["text"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    arguments = arguments or {}
    if name == "vault_catalog":
        text = _vault_catalog()
    elif name == "vault_search":
        text = _vault_search(**arguments)
    elif name == "vault_get":
        text = _vault_get(**arguments)
    elif name == "vault_remember":
        text = _vault_remember(**arguments)
    else:
        raise ValueError(f"unknown tool: {name}")
    return [types.TextContent(type="text", text=text)]


async def main() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    os.environ.setdefault("VAULT_PROVIDER", "claude")
    asyncio.run(main())
