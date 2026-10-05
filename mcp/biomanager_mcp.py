#!/usr/bin/env python3
"""BioManager for AI assistants: an MCP server that reads your lab and
proposes changes for you to approve.

    python -m pip install -r mcp/requirements.txt
    BIOMANAGER_URL=https://your-server BIOMANAGER_TOKEN=bmt_… python mcp/biomanager_mcp.py

It speaks MCP over stdio, so an assistant app (Claude Desktop, Cursor,
Cherry Studio…) starts it as a command; mcp/README.md has the setup for each.
It holds no data and changes nothing itself: it calls BioManager's API
(/api/v1) with a token, ideally a *Read and propose* one, and every change
goes in as a proposal that its person approves, whole, on Proposed changes
in BioManager.

    BIOMANAGER_URL        the lab's address (the desktop app: http://127.0.0.1:<port>)
    BIOMANAGER_TOKEN      a token from Settings → API tokens
    BIOMANAGER_ASSISTANT  how proposals are signed in BioManager ("Claude"); default "AI assistant"

The tools are described once, in app/assistant_tools.py (standard library
only), and shared with the lab server's own endpoint (/api/v1/mcp, for
claude.ai and ChatGPT); `build_server()` wraps them for MCP. What may be
proposed comes from the lab's own /api/v1/actions, so a new action reaches
the assistant without editing this file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# The shared tools live in the app (app/assistant_tools.py: standard library only).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.assistant_tools import (INSTRUCTIONS, PROMPTS, Api, ApiError, call_tool, description,  # noqa: E402,F401
                                 describe_actions, discard_proposal, get, lab_overview, list_actions, list_records,
                                 proposal_status, propose_changes, resolve, whats_due)


def build_server(api: Api):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("BioManager", instructions=INSTRUCTIONS)

    @server.tool(name="lab_overview", description=description(api, "lab_overview"))
    def lab_overview_tool() -> dict:
        return call_tool(api, "lab_overview", {})

    @server.tool(name="resolve", description=description(api, "resolve"))
    def resolve_tool(q: str, kinds: str = "") -> dict:
        return call_tool(api, "resolve", {"q": q, "kinds": kinds})

    @server.tool(name="get", description=description(api, "get"))
    def get_tool(path: str) -> dict:
        return call_tool(api, "get", {"path": path})

    @server.tool(name="list", description=description(api, "list"))
    def list_tool(path: str, filters: dict | None = None, limit: int = 100) -> dict:
        return call_tool(api, "list", {"path": path, "filters": filters, "limit": limit})

    @server.tool(name="whats_due", description=description(api, "whats_due"))
    def whats_due_tool(days: int = 7) -> dict:
        return call_tool(api, "whats_due", {"days": days})

    @server.tool(name="list_actions", description=description(api, "list_actions"))
    def list_actions_tool() -> dict:
        return call_tool(api, "list_actions", {})

    @server.tool(name="propose_changes", description=description(api, "propose_changes"))
    def propose_changes_tool(summary: str, changes: list[dict], replaces: int | None = None) -> dict:
        return call_tool(api, "propose_changes", {"summary": summary, "changes": changes, "replaces": replaces})

    @server.tool(name="proposal_status", description=description(api, "proposal_status"))
    def proposal_status_tool(proposal_id: int) -> dict:
        return call_tool(api, "proposal_status", {"proposal_id": proposal_id})

    @server.tool(name="discard_proposal", description=description(api, "discard_proposal"))
    def discard_proposal_tool(proposal_id: int) -> dict:
        return call_tool(api, "discard_proposal", {"proposal_id": proposal_id})

    @server.prompt(name="log_today", description=PROMPTS["log_today"][0])
    def log_today(notes: str) -> str:
        return PROMPTS["log_today"][2]({"notes": notes})

    @server.prompt(name="log_from_photo", description=PROMPTS["log_from_photo"][0])
    def log_from_photo(what_it_shows: str = "") -> str:
        return PROMPTS["log_from_photo"][2]({"what_it_shows": what_it_shows})

    @server.prompt(name="plan_tomorrow", description=PROMPTS["plan_tomorrow"][0])
    def plan_tomorrow() -> str:
        return PROMPTS["plan_tomorrow"][2]({})

    return server


def main() -> None:
    base, token = os.environ.get("BIOMANAGER_URL", "").strip(), os.environ.get("BIOMANAGER_TOKEN", "").strip()
    if not base or not token:
        sys.exit("Set BIOMANAGER_URL (your BioManager's address) and BIOMANAGER_TOKEN (a token from "
                 "Settings → API tokens; Read and propose is the one for an assistant). See mcp/README.md.")
    build_server(Api(base, token)).run("stdio")


if __name__ == "__main__":
    main()
