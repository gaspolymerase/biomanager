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

The tool logic is plain functions over an `Api` (no MCP needed), so
tests/test_mcp.py runs them against a test server; `build_server()` wraps
them for MCP. What may be proposed comes from the lab's own /api/v1/actions,
so a new action reaches the assistant without editing this file.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

INSTRUCTIONS = """\
You help a lab member record their work in BioManager, their lab's database of animals, \
experiments and notebook. They tell you what they did in their own words (or show you a photo \
of their notes); you turn it into one proposal of changes, which they approve in BioManager. \
You never change anything yourself.

How to work:
1. Call lab_overview once to learn this lab's databases and its own words (strains, statuses, \
racks, people, each database's fields).
2. For every record the person mentions, call resolve and use the reference it returns. If a \
phrase matches several records, or none, ask the person; never guess.
3. Call list_actions to see what can be proposed and the fields each takes. Use the lab's own \
values (from lab_overview) for statuses, purposes and the like.
4. Put everything from one conversation in one propose_changes call, with a short summary in the \
person's words. If BioManager reports errors, fix them and propose again with replaces set to the \
earlier proposal's id.
5. End by telling the person what you proposed and giving them the review link: nothing has \
changed until they press Approve there.
Dates are YYYY-MM-DD; "today" is in lab_overview. Never invent animals, numbers or results."""


class ApiError(Exception):
    pass


class Api:
    """BioManager's /api/v1, with a token."""

    def __init__(self, base: str, token: str, timeout: float = 60):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, query: dict | None = None, body: dict | None = None):
        url = f"{self.base}/api/v1/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read().decode()).get("error")
            except (ValueError, AttributeError):
                message = None
            raise ApiError(message or f"BioManager answered {e.code}.") from None
        except urllib.error.URLError as e:
            raise ApiError(f"Can't reach BioManager at {self.base}: {e.reason}. Is it running, and is "
                           "BIOMANAGER_URL right?") from None


# ---------------------------------------------------------------- the tools, as plain functions

# What `get` and `list_records` may read: the API's own read paths.
READABLE = ("mice", "cages", "litters", "strains", "tanks", "fish", "plasmids", "stocks", "inventories",
            "experiments", "proposals", "me")


def _readable(path: str) -> str:
    path = (path or "").strip().strip("/")
    if path.startswith("api/v1/"):
        path = path[len("api/v1/"):]
    if ".." in path or not path or path.split("/")[0] not in READABLE:
        raise ApiError(f"Read one of: {', '.join(READABLE)} (e.g. mice/1043, cages, experiments/12).")
    return path


def lab_overview(api: Api) -> dict:
    me = api.request("GET", "me")
    return {"you": me, "lab": api.request("GET", "vocabulary")}


def resolve(api: Api, q: str, kinds: str = "") -> list:
    return api.request("GET", "resolve", {"q": q, "kinds": kinds})["data"]


def get(api: Api, path: str):
    return api.request("GET", _readable(path))


def list_records(api: Api, path: str, filters: dict | None = None, limit: int = 100) -> dict:
    return api.request("GET", _readable(path), {**(filters or {}), "limit": limit})


def whats_due(api: Api, days: int = 7) -> list:
    return api.request("GET", "due", {"days": days})["data"]


def list_actions(api: Api) -> list:
    return api.request("GET", "actions")["data"]


def propose_changes(api: Api, summary: str, changes: list, replaces: int | None = None,
                    source: str | None = None) -> dict:
    body = {"summary": summary, "changes": changes,
            "source": source or os.environ.get("BIOMANAGER_ASSISTANT") or "AI assistant"}
    if replaces is not None:
        body["replaces"] = replaces
    out = api.request("POST", "proposals", body=body)
    out["next"] = ("Nothing has changed yet. Give the person the review_url: they approve or discard it there."
                   if out.get("status") == "pending" else
                   "BioManager can't apply this as it is: fix the errors and propose again, with replaces set "
                   "to this id.")
    return out


def proposal_status(api: Api, proposal_id: int) -> dict:
    return api.request("GET", f"proposals/{int(proposal_id)}")


def discard_proposal(api: Api, proposal_id: int) -> dict:
    return api.request("POST", f"proposals/{int(proposal_id)}/discard", body={})


def describe_actions(actions: list) -> str:
    """The catalogue, briefly, for propose_changes' description."""
    lines = []
    for a in actions:
        fields = ", ".join(f"{name}{'*' if name in a['fields'].get('required', []) else ''}"
                           for name in a["fields"].get("properties", {}))
        target = f" target: {a['target']};" if a.get("target") else ""
        lines.append(f"- {a['name']} ({a['area']}):{target} fields: {fields or '-'}. {a['help']}")
    return "\n".join(lines)


PROPOSE_HELP = """\
Send changes to BioManager as one proposal for the person to approve. Nothing changes until they \
press Approve in BioManager. changes is a list of {"action", "target", "fields"}: target is a \
reference from resolve ({"kind": "cage", "id": 412}) or what the lab writes on the record (cage \
88, mouse 1043, FV12); fields as below (* required; list_actions has each field's details). The \
reply has each change's summary, warnings and errors, and the review_url to give the person."""


# ---------------------------------------------------------------- MCP

def build_server(api: Api):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("BioManager", instructions=INSTRUCTIONS)

    def safe(fn, *args, **kwargs):
        try:
            return fn(api, *args, **kwargs)
        except ApiError as e:
            return {"error": str(e)}

    @server.tool(name="lab_overview", description="This lab's databases and its own words (strains, statuses, purposes, racks, people, "
                             "each database's fields), today's date, and who you are acting for. Call it first.")
    def lab_overview_tool() -> dict:
        return safe(lab_overview)

    @server.tool(name="resolve", description="Find the records a phrase means (\"cage 88\", \"#1043\", \"FV12\", a line or "
                             "experiment's name), each with the reference to use in a proposal. kinds narrows it "
                             "(comma-separated: cage, mouse, litter, tank, stock_unit, experiment, notebook_page…). "
                             "Several matches: ask the person which.")
    def resolve_tool(q: str, kinds: str = "") -> dict:
        found = safe(resolve, q, kinds)
        return found if isinstance(found, dict) else {"matches": found}

    @server.tool(name="get", description="Read one record or list from BioManager's API, e.g. mice/1043, cages/88, "
                             "experiments/12 (with its steps, animals and readouts), stocks.")
    def get_tool(path: str) -> dict:
        return safe(get, path)

    @server.tool(name="list", description="List records with filters, e.g. path=mice, filters={\"alive\": \"true\", "
                             "\"cage\": \"88\"}; path=experiments, filters={\"status\": \"active\"}.")
    def list_tool(path: str, filters: dict | None = None, limit: int = 100) -> dict:
        return safe(list_records, path, filters, limit)

    @server.tool(name="whats_due", description="What is due, as BioManager's Home lists it (weanings, genotyping, flips, scheduled "
                             "jobs, experiment steps), overdue first, up to `days` ahead. Each item may carry "
                             "`propose`: the change that records it done.")
    def whats_due_tool(days: int = 7) -> dict:
        due = safe(whats_due, days)
        return due if isinstance(due, dict) else {"due": due}

    @server.tool(name="list_actions", description="Every action a proposal may contain, with its target and fields (JSON Schema).")
    def list_actions_tool() -> dict:
        found = safe(list_actions)
        return found if isinstance(found, dict) else {"actions": found}

    try:
        catalogue = describe_actions(list_actions(api))
    except ApiError as e:
        catalogue = f"(The list of actions couldn't be read now: {e} Call list_actions.)"

    @server.tool(name="propose_changes", description=f"{PROPOSE_HELP}\n\nActions:\n{catalogue}")
    def propose_changes_tool(summary: str, changes: list[dict], replaces: int | None = None) -> dict:
        return safe(propose_changes, summary, changes, replaces)

    @server.tool(name="proposal_status", description="A proposal's status (pending, approved, discarded, superseded, expired, invalid) and "
                             "each change as BioManager previewed it.")
    def proposal_status_tool(proposal_id: int) -> dict:
        return safe(proposal_status, proposal_id)

    @server.tool(name="discard_proposal", description="Withdraw a proposal that is still waiting (the person asked, or you sent a wrong one).")
    def discard_proposal_tool(proposal_id: int) -> dict:
        return safe(discard_proposal, proposal_id)

    @server.prompt(description="Log today's work from my notes")
    def log_today(notes: str) -> str:
        return ("Here is what I did today. Record it in BioManager: resolve every animal, cage and experiment I "
                "mention, ask me about anything unclear, then send it all as one proposal and give me the link "
                "to approve it. Add a short line to today's daily log too.\n\n" + notes)

    @server.prompt(description="Log from a photo of my notebook, whiteboard or cage card")
    def log_from_photo(what_it_shows: str = "") -> str:
        return ("Read the photo I attached (my notebook, a whiteboard or cage cards) and record what it says in "
                "BioManager: resolve every record, read numbers carefully and ask me about any you can't read, "
                "then send one proposal and give me the link to approve it." +
                (f"\n\nIt shows: {what_it_shows}" if what_it_shows else ""))

    @server.prompt(description="Plan tomorrow from what's due")
    def plan_tomorrow() -> str:
        return ("Look at what is due in BioManager over the next two days and plan my tomorrow: group the work by "
                "room and time, say what each needs, and flag anything overdue. Don't propose changes unless I "
                "ask.")

    return server


def main() -> None:
    base, token = os.environ.get("BIOMANAGER_URL", "").strip(), os.environ.get("BIOMANAGER_TOKEN", "").strip()
    if not base or not token:
        sys.exit("Set BIOMANAGER_URL (your BioManager's address) and BIOMANAGER_TOKEN (a token from "
                 "Settings → API tokens; Read and propose is the one for an assistant). See mcp/README.md.")
    build_server(Api(base, token)).run("stdio")


if __name__ == "__main__":
    main()
