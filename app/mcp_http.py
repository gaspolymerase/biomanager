"""BioManager's own MCP endpoint, /api/v1/mcp, for assistants that connect
over the network: claude.ai, Claude's desktop and phone apps and ChatGPT
(as a connector, signed in with OAuth, app/oauth.py), and Claude Code or any
client that sends `Authorization: Bearer <token>`.

MCP's streamable HTTP, kept to what these clients need: one JSON-RPC
message per POST, answered with JSON; no server-sent stream (GET is 405)
and no session. It sits inside /api/v1, so the token, its scope, the rate
limit and switched-off databases are checked as for any API call. The
tools are app/assistant_tools.py's, the same as the local server's, and
each calls the API in-process with the caller's own token: it can do
nothing the API wouldn't.
"""
from __future__ import annotations

import json

from flask import current_app, g, jsonify, request

from . import assistant_tools as tools

# The newest first: an unknown version asked for gets the newest we speak.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
READ_ONLY = {"lab_overview", "resolve", "get", "list", "whats_due", "list_actions", "proposal_status"}
TITLES = {"lab_overview": "Lab overview", "resolve": "Find records", "get": "Read a record",
          "list": "List records", "whats_due": "What's due", "list_actions": "What can be proposed",
          "propose_changes": "Propose changes", "proposal_status": "A proposal's status",
          "discard_proposal": "Withdraw a proposal"}


class InProcessApi(tools.Api):
    """/api/v1 called from inside the app, with the caller's own token, so
    links it makes (the review link) use the address the caller came in on."""

    def __init__(self, token: str):
        super().__init__(request.url_root, token)
        self.client = current_app.test_client()

    def request(self, method: str, path: str, query: dict | None = None, body: dict | None = None):
        r = self.client.open(f"/api/v1/{path.lstrip('/')}", method=method, base_url=request.url_root,
                             query_string={k: v for k, v in (query or {}).items() if v not in (None, "")},
                             json=body, headers={"Authorization": f"Bearer {self.token}"})
        data = r.get_json(silent=True)
        if r.status_code >= 400:
            raise tools.ApiError((data or {}).get("error") or f"BioManager answered {r.status_code}.")
        return data


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _api() -> InProcessApi:
    raw = request.headers.get("Authorization", "")[7:].strip()
    return InProcessApi(raw)


def _tool_list() -> list[dict]:
    api = _api()
    out = []
    for name, (_desc, schema, _fn) in tools.TOOLS.items():
        out.append({"name": name, "title": TITLES.get(name, name), "description": tools.description(api, name),
                    "inputSchema": schema,
                    "annotations": {"title": TITLES.get(name, name), "readOnlyHint": name in READ_ONLY,
                                    "destructiveHint": False, "openWorldHint": False}})
    return out


def _call(params: dict) -> dict:
    name = params.get("name")
    if name not in tools.TOOLS:
        raise LookupError(f"There is no tool called {name!r}.")
    args = dict(params.get("arguments") or {})
    if name == "propose_changes":
        # Proposals are signed with the connection's name ("Claude").
        args["_source"] = (g.api_token.label or "AI assistant") if g.get("api_token") else "AI assistant"
    out = tools.call_tool(_api(), name, args)
    return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}],
            "structuredContent": out, "isError": "error" in out}


def _prompt(params: dict) -> dict:
    name = params.get("name")
    if name not in tools.PROMPTS:
        raise LookupError(f"There is no prompt called {name!r}.")
    desc, _args, render = tools.PROMPTS[name]
    text = render(dict(params.get("arguments") or {}))
    return {"description": desc, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


def handle(message) -> dict | None:
    """One JSON-RPC message: its answer, or None for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid request")
    method, params = message["method"], message.get("params") or {}
    if "id" not in message:
        return None                       # notifications/initialized, cancelled…: nothing to say
    msg_id = message["id"]
    try:
        if method == "initialize":
            asked = str(params.get("protocolVersion") or "")
            from .feedback import app_version
            return _result(msg_id, {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                "serverInfo": {"name": "BioManager", "title": "BioManager", "version": app_version()},
                "instructions": tools.INSTRUCTIONS})
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": _tool_list()})
        if method == "tools/call":
            return _result(msg_id, _call(params))
        if method == "prompts/list":
            return _result(msg_id, {"prompts": [
                {"name": n, "description": d, "arguments": a} for n, (d, a, _r) in tools.PROMPTS.items()]})
        if method == "prompts/get":
            return _result(msg_id, _prompt(params))
        if method in ("resources/list", "resources/templates/list"):
            return _result(msg_id, {"resources" if method == "resources/list" else "resourceTemplates": []})
        return _error(msg_id, -32601, f"Method not found: {method}")
    except LookupError as e:
        return _error(msg_id, -32602, str(e))


def endpoint():
    if request.method != "POST":
        return jsonify({"error": "Send MCP messages by POST; this server has no event stream."}), 405, {
            "Allow": "POST"}
    try:
        body = json.loads(request.get_data(as_text=True) or "null")
    except ValueError:
        return jsonify(_error(None, -32700, "Parse error")), 400
    if isinstance(body, list):           # a batch (2025-03-26)
        answers = [a for a in (handle(m) for m in body) if a is not None]
        return (jsonify(answers), 200) if answers else ("", 202)
    answer = handle(body)
    if answer is None:
        return "", 202
    return jsonify(answer)
