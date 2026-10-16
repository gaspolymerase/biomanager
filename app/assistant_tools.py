"""What an AI assistant can do with BioManager, whichever way it connects.

The local MCP server (mcp/biomanager_mcp.py, stdio, run on the person's
computer) and the server's own MCP endpoint (app/mcp_http.py, /api/v1/mcp,
for claude.ai, Claude's apps and ChatGPT) offer the same tools, described
here once: the instructions, each tool's description and input schema, the
prompts, and the tool functions over an `Api`, which is BioManager's
/api/v1 with the person's token (over HTTP, or in-process on the server).
Standard library only: the local server imports this without the app's
dependencies.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

INSTRUCTIONS = """\
You help a lab member record their work in BioManager, their lab's database of animals, \
experiments and notebook. They tell you what they did in their own words (or show you a photo \
of their notes); you turn it into one proposal of changes, which they approve in BioManager. \
You never change anything yourself.

When the person only says they want to record or change something ("I want to change something in \
BioManager"), don't explain BioManager or ask how to begin: call lab_overview, show the person what is due \
today from its due_today (overdue first), and ask which of those they did, or what else they did, in their \
own words or as a photo of their notes.

When the person says "process my captures", call list_captures and read each with get_capture: they are \
what the person wrote down or photographed at the bench, waiting to become records.

How to work:
1. Call lab_overview once to learn this lab's databases and its own words (strains, statuses, \
racks, people, each database's fields).
2. For every record the person mentions, call resolve and use the reference it returns. If a \
phrase matches several records, or none, ask the person; never guess.
3. Call list_actions to see what can be proposed, then list_actions with the names you will use for \
their help and each field's details. Use the lab's own values (from lab_overview) for statuses, \
purposes and the like.
4. Put everything from one conversation in one propose_changes call, with a short summary in the \
person's words, and give each change its evidence: the words of their notes it comes from, or what \
the photo shows (and the capture's id). If BioManager reports errors, or the person sends it back \
with a reason, fix it and propose again with replaces set to the earlier proposal's id.
5. End by telling the person what you proposed and giving them the review link: nothing has \
changed until they press Approve there.
Dates are YYYY-MM-DD; "today" is in lab_overview. Never invent animals, numbers or results.
A question that only reads ("what's in cage 88?") needs no proposal: just answer it, reading each record \
with get at the path resolve gives it.
To correct something already there, change it rather than making it again: a notebook page or protocol with \
notebook_page_change (read its text first with get, at the path resolve gives it), a protocol's folder with \
protocol_file, an inventory's numbering with inventory_renumber.

BioManager computes; you talk. Never work out a number yourself that a BioManager tool can: how many \
animals fit (availability), breeding odds and matings (breeding_plan), group allocation (randomize), \
animals per group (power_analysis), when stock runs out (reorder_forecast), the week's checks \
(lab_checks), an experiment's facts (experiment_summary), derived readouts (derive), a plate \
reader's or qPCR export on a plate layout (plate_readout), animals used per \
protocol for a review (animal_use), what someone leaving looks after (handover). Call the tool, give its \
numbers with the \
assumptions it states, and say so when it can't answer rather than estimating."""


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
            "experiments", "proposals", "me", "regimens", "notebook")


def _readable(path: str) -> str:
    path = (path or "").strip().strip("/")
    if path.startswith("api/v1/"):
        path = path[len("api/v1/"):]
    if ".." in path or not path or path.split("/")[0] not in READABLE:
        raise ApiError(f"Read one of: {', '.join(READABLE)} (e.g. mice/1043, cages/88, experiments/12, "
                       "inventories/reagents/items/1), or give ref from resolve.")
    return path


# How many of today's jobs lab_overview shows; whats_due has the rest.
DUE_TODAY = 15


def lab_overview(api: Api) -> dict:
    """Who the assistant acts for, the lab's own words, what is due today,
    and how to work: in a tool result, which every app hands to its model,
    as not every app passes on the server's instructions."""
    me = api.request("GET", "me")
    try:
        due = [{**i, "mine": i.get("owner") == me.get("username")} for i in whats_due(api, 0)
               if i.get("status") in ("overdue", "today")]
    except ApiError:
        due = []
    # The person's own jobs first (an admin's list is the whole lab's), each
    # half overdue first.
    due.sort(key=lambda i: (not i["mine"], i.get("status") != "overdue"))
    return {"you": me, "lab": api.request("GET", "vocabulary"), "due_today": due[:DUE_TODAY],
            "due_today_more": max(0, len(due) - DUE_TODAY), "how_to_work": INSTRUCTIONS}


def resolve(api: Api, q: str, kinds: str = "") -> list:
    return api.request("GET", "resolve", {"q": q, "kinds": kinds})["data"]


def get(api: Api, path: str = "", ref: dict | None = None):
    """A record or list by its path, or the record a reference ({"kind",
    "id"}, from resolve or search) names, read at the path BioManager gives
    for it (a cage's path has its code, not the reference's id)."""
    if ref and not path:
        if not isinstance(ref, dict) or not ref.get("kind") or ref.get("id") in (None, ""):
            raise ValueError("ref is {\"kind\", \"id\"}, as resolve gives it")
        path = api.request("GET", "path", {"kind": ref["kind"], "id": ref["id"]})["path"]
    # plasmids/14?sequence=1: the query goes as query parameters, which every
    # Api sends the same way.
    path, _, query = (path or "").partition("?")
    return api.request("GET", _readable(path), dict(urllib.parse.parse_qsl(query)) or None)


def list_records(api: Api, path: str, filters: dict | None = None, limit: int = 100) -> dict:
    return api.request("GET", _readable(path), {**(filters or {}), "limit": limit})


def whats_due(api: Api, days: int = 7) -> list:
    return api.request("GET", "due", {"days": days})["data"]


def list_actions(api: Api) -> list:
    return api.request("GET", "actions")["data"]


def _fields_brief(a: dict) -> str:
    return ", ".join(f"{name}{'*' if name in a['fields'].get('required', []) else ''}"
                     for name in a["fields"].get("properties", {}))


def actions_tool(api: Api, names=None, area: str = "") -> dict:
    """list_actions: every action briefly, or the ones asked for in full. The whole catalogue in full is
    some 86,000 characters, more than an assistant app shows of one reply (Claude Code saves it to a
    file), so it comes in two steps."""
    actions = list_actions(api)
    if isinstance(names, str):
        names = [names]
    wanted = {str(n).strip() for n in names or [] if str(n).strip()}
    area = (area or "").strip().lower()
    if not wanted and not area:
        return {"actions": [{"name": a["name"], "area": a["area"], "title": a["title"], "target": a.get("target"),
                             "fields": _fields_brief(a)} for a in actions],
                "details": "Call list_actions with names (or an area) for an action's help and each field's "
                           "details. * marks a required field."}
    out = {"actions": [a for a in actions if a["name"] in wanted or a["area"] == area]}
    unknown = sorted(wanted - {a["name"] for a in actions})
    if unknown:
        from .actions import nearest_actions
        out["unknown"] = {n: nearest_actions(n) for n in unknown}
    if area and not any(a["area"] == area for a in actions):
        out["areas"] = sorted({a["area"] for a in actions})
    return out


def propose_changes(api: Api, summary: str, changes: list, replaces: int | None = None,
                    source: str | None = None, captures: list | None = None) -> dict:
    body = {"summary": summary, "changes": changes,
            "source": source or os.environ.get("BIOMANAGER_ASSISTANT") or "AI assistant"}
    if replaces is not None:
        body["replaces"] = replaces
    if captures:
        body["captures"] = captures
    out = api.request("POST", "proposals", body=body)
    out["next"] = ("Nothing has changed yet. Give the person the review_url: they approve or discard it there."
                   if out.get("status") == "pending" else
                   "BioManager can't apply this as it is: fix the errors and propose again, with replaces set "
                   "to this id.")
    return out


def list_captures(api: Api, status: str = "waiting") -> dict:
    out = api.request("GET", "captures", {"status": status or "waiting"})
    return {"captures": out["data"],
            "next": ("Read each with get_capture, resolve its records, then send one proposal with captures set to "
                     "their ids and each change's evidence." if out["data"] else "Nothing is waiting.")}


def get_capture(api: Api, capture_id: int) -> dict:
    """A capture; a photo comes back as MCP image content (`_content`,
    which both servers send after the text) rather than in the JSON."""
    out = api.request("GET", f"captures/{int(capture_id)}")
    image = out.pop("image", None)
    if isinstance(image, dict) and image.get("data"):
        out["photo"] = "Attached below as an image."
        out["_content"] = [{"type": "image", "data": image["data"], "mimeType": image.get("mimeType") or "image/jpeg"}]
    return out


def proposal_status(api: Api, proposal_id: int) -> dict:
    return api.request("GET", f"proposals/{int(proposal_id)}")


def discard_proposal(api: Api, proposal_id: int) -> dict:
    return api.request("POST", f"proposals/{int(proposal_id)}/discard", body={})


def describe_actions(actions: list) -> str:
    """The catalogue's names by area, for propose_changes' description. Names only: assistant apps cut a
    tool's description short (Claude Code at about 4,000 characters), and the actions past the cut would
    be unknown to the assistant; list_actions gives their fields."""
    areas: dict[str, list[str]] = {}
    for a in actions:
        areas.setdefault(a["area"], []).append(a["name"])
    return "\n".join(f"- {area}: {', '.join(names)}" for area, names in areas.items())


PROPOSE_HELP = """\
Send changes to BioManager as one proposal for the person to approve. Nothing changes until they \
press Approve in BioManager. changes is a list of {"action", "target", "fields", "evidence"}: target is a \
reference from resolve ({"kind": "cage", "id": 412}) or what the lab writes on the record (cage \
88, mouse 1043, FV12); fields as list_actions gives them (call it with the names you will use). Give each \
change its evidence, shown to the person beside it: {"quote": the words of their notes it comes from, or \
what the photo shows (at most 300 characters), "capture": the capture's id, "where": where in it}. Made \
from captures? Set captures to their ids: they are marked used when the person approves. The \
reply has each change's summary, warnings, errors and records (each record it would change, field: \
before → after; check them for what else a change does), and the review_url to give the person. \
Correcting an earlier proposal (sent back, invalid or still waiting)? Set replaces to its id."""


# ---------------------------------------------------------------- tools and prompts, described once

def _obj(properties: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TOOLS = {
    "lab_overview": (
        "Start here whenever the person mentions BioManager, their lab, colony, mice, cages, litters, fish, tanks, "
        "vials, plates, experiments or notebook, or wants to record, log, update, change or look up anything there, "
        "even if they only say \"I want to change something in BioManager\". Returns how to work with BioManager, "
        "this lab's databases and its own words (strains, statuses, purposes, racks, people, each database's "
        "fields), today's date, what is due today (the person's own first, each marked mine), and who you are "
        "acting for.",
        _obj({}), lambda api, a: lab_overview(api)),
    "resolve": (
        "Find the records a phrase means (\"cage 88\", \"#1043\", \"FV12\", a line or experiment's name), each with "
        "the reference to use in a proposal, and (for a mouse, cage, plasmid, inventory item, experiment or notebook "
        "page) the path to read it with get. kinds narrows it (comma-separated: cage, mouse, litter, tank, "
        "stock_unit, experiment, notebook_page, protocol_folder…). Several matches: ask the person which.",
        _obj({"q": {"type": "string", "description": "What the person called it"},
              "kinds": {"type": "string", "description": "Only these kinds, comma-separated"}}, ("q",)),
        lambda api, a: {"matches": resolve(api, a["q"], a.get("kinds", ""))}),
    "get": (
        "Read one record or list from BioManager's API. Paths take what the lab writes on the record, not a "
        "reference's id: mice/1043 (the mouse number; with its weights and experiments), cages/88 (the cage's code), "
        "plasmids/14 (its number; with its box, position and glycerol stocks, and its DNA sequence and map "
        "annotations only as plasmids/14?sequence=1), "
        "inventories/reagents/items/1 (the inventory's key, then the item's number), "
        "experiments/12 (its id; with its plan, steps, animals and groups, and readouts), "
        "notebook/pages/31 (its id; with its text as it is now: read it before changing a page with "
        "notebook_page_change); lists such as stocks, "
        "inventories, inventories/reagents/items. Easiest: the path resolve gives, or ref instead of path.",
        _obj({"path": {"type": "string", "description": "e.g. mice/1043, cages/88, inventories/reagents/items/1"},
              "ref": {"type": "object", "description": "Instead of path: a reference {\"kind\", \"id\"} from resolve "
                                                       "or search"}}),
        lambda api, a: get(api, a.get("path") or "", a.get("ref"))),
    "list": (
        "List records with filters (anything else is refused, naming the ones the path takes). mice: alive, cage, "
        "litter, owner, status, sex, genotype, updated_since; cages: occupied, owner, purpose, room; litters: "
        "born_since, cage (those with a living pup in it); experiments: status (planned, active, paused, done, cancelled or all), owner, database; tanks: "
        "active, owner; fish: tank, status; plasmids: q; stocks/<key>/units: active, purpose, owner, genotype; "
        "inventories/<key>/items (e.g. inventories/reagents/items): status, category, owner, q, updated_since. "
        "E.g. path=mice, filters={\"alive\": \"true\", \"cage\": \"88\"}.",
        _obj({"path": {"type": "string"}, "filters": {"type": "object", "description": "Query filters"},
              "limit": {"type": "integer", "description": "At most this many (default 100)"}}, ("path",)),
        lambda api, a: list_records(api, a["path"], a.get("filters"), a.get("limit", 100))),
    "whats_due": (
        "What is due, as BioManager's Home lists it (weanings, genotyping, flips, scheduled jobs, experiment steps), "
        "overdue first, up to `days` ahead. Each item may carry `propose`: the change that records it done.",
        _obj({"days": {"type": "integer", "description": "How many days ahead (default 7)"}}),
        lambda api, a: {"due": whats_due(api, a.get("days", 7))}),
    "list_actions": (
        "What a proposal may contain. With nothing given: every action, briefly (its area, title, target and "
        "fields, * required). With names, or an area (mice, inventory…): those actions in full, with their "
        "help and each field's details (JSON Schema). Read the ones you will use before proposing.",
        _obj({"names": {"type": "array", "items": {"type": "string"}, "description": "Actions to give in full"},
              "area": {"type": "string", "description": "Give every action of this area in full"}}),
        lambda api, a: actions_tool(api, a.get("names"), a.get("area", ""))),
    "propose_changes": (
        None,  # PROPOSE_HELP and the lab's own actions: propose_description()
        _obj({"summary": {"type": "string", "description": "What this proposal records, in the person's words"},
              "changes": {"type": "array", "items": {"type": "object"},
                          "description": "Each {action, target, fields, evidence}"},
              "replaces": {"type": "integer", "description": "The id of an earlier proposal this corrects"},
              "captures": {"type": "array", "items": {"type": "integer"},
                           "description": "The ids of the captures this was made from"}},
             ("summary", "changes")),
        lambda api, a: propose_changes(api, a["summary"], a["changes"], a.get("replaces"), a.get("_source"),
                                       a.get("captures"))),
    "proposal_status": (
        "A proposal's status (pending, approved, discarded, superseded, expired, invalid, sent_back) and each "
        "change as BioManager previewed it. Sent back: its reason says what to correct; propose again with "
        "replaces.",
        _obj({"proposal_id": {"type": "integer"}}, ("proposal_id",)),
        lambda api, a: proposal_status(api, a["proposal_id"])),
    "discard_proposal": (
        "Withdraw a proposal that is still waiting (the person asked, or you sent a wrong one).",
        _obj({"proposal_id": {"type": "integer"}}, ("proposal_id",)),
        lambda api, a: discard_proposal(api, a["proposal_id"])),
    "list_captures": (
        "The person's captures: what they wrote down or photographed at the bench (text, a photo or a small "
        "file, and the record it was taken at), waiting to be turned into records. Call it when they say "
        "\"process my captures\".",
        _obj({"status": {"type": "string", "description": "waiting (default), used, dismissed or all"}}),
        lambda api, a: list_captures(api, a.get("status") or "waiting")),
    "get_capture": (
        "Read one capture: its text, its photo (as an image) or its file's text (a CSV), and where it was taken.",
        _obj({"capture_id": {"type": "integer"}}, ("capture_id",)),
        lambda api, a: get_capture(api, a["capture_id"])),
}


# ---------------------------------------------------------------- looking up and working out
#
# BioManager computes; the model talks. Each of these reads, or works
# something out from the lab's records (app/assistant_compute.py), and
# changes nothing.

def _record(a: dict) -> dict:
    """A record as asked for: ref ({"kind", "id"}), or kind and code."""
    ref = a.get("ref") or {}
    if ref:
        return {"kind": ref.get("kind"), "id": ref.get("id")}
    if not a.get("kind") or a.get("code") in (None, ""):
        raise ValueError("give ref (from resolve or search), or kind and code (kind mouse, code 1043)")
    return {"kind": a["kind"], "code": a["code"]}


def search(api: Api, q: str, limit: int = 5) -> list:
    return api.request("GET", "search", {"q": q, "limit": limit})["data"]


def history(api: Api, record: dict, limit: int = 50) -> dict:
    return api.request("GET", "history", {**record, "limit": limit})


def lineage(api: Api, record: dict) -> dict:
    return api.request("GET", "lineage", record)


def _flag(value) -> str | None:
    return None if value is None else ("true" if value else "false")


def availability(api: Api, a: dict) -> dict:
    query = {k: a.get(k) for k in ("species", "genotype", "sex", "age_min_days", "age_max_days", "on", "limit")}
    query.update(include_in_experiments=_flag(a.get("include_in_experiments")),
                 include_breeders=_flag(a.get("include_breeders")))
    return api.request("GET", "availability", query)


def breeding_plan(api: Api, a: dict) -> dict:
    return api.request("POST", "breeding-plan", body=a)


def randomize(api: Api, a: dict) -> dict:
    return api.request("POST", "randomize", body=a)


def power_analysis(api: Api, a: dict) -> dict:
    return api.request("POST", "power", body=a)


def reorder_forecast(api: Api, days: int = 30, lead_days: int = 14) -> dict:
    return api.request("GET", "reorder-forecast", {"days": days, "lead_days": lead_days})


def lab_checks(api: Api, a: dict) -> dict:
    return api.request("GET", "lab-checks", {k: a.get(k) for k in (
        "scope", "weight_loss_pct", "breeder_weeks", "unsigned_days", "expiring_days")})


def experiment_summary(api: Api, experiment_id: int) -> dict:
    return api.request("GET", f"experiments/{int(experiment_id)}/summary")


def animal_use(api: Api, a: dict) -> dict:
    return api.request("GET", "animal-use", {k: a.get(k) for k in ("start", "end", "protocol")})


def handover(api: Api, a: dict) -> dict:
    return api.request("GET", "handover", {k: a.get(k) for k in ("username", "days")})


def derive(api: Api, a: dict) -> dict:
    if not a.get("formula"):
        return api.request("GET", "derive")
    return api.request("POST", "derive", body={k: a.get(k) for k in ("formula", "rows", "control")})


PLATE_KEYS = ("layout", "wells", "format", "text", "capture", "target", "reference", "subtract_blank",
              "standard_curve", "curve", "measure", "skip", "date")


def plate_readout(api: Api, a: dict) -> dict:
    """An export laid on an experiment's plate layout (app/plates.py), changing nothing."""
    return api.request("POST", f"experiments/{int(a['experiment_id'])}/plate",
                       body={k: a[k] for k in PLATE_KEYS if a.get(k) is not None})


def verify_sequencing(api: Api, a: dict) -> dict:
    """Sanger reads (capture ids, or text) against a plasmid's map: the
    plasmid by its number, or a reference from resolve."""
    ref, number = a.get("ref"), a.get("plasmid")
    if ref and number in (None, ""):
        if not isinstance(ref, dict) or ref.get("id") in (None, ""):
            raise ValueError("ref is {\"kind\": \"plasmid\", \"id\"}, as resolve gives it")
        path = api.request("GET", "path", {"kind": ref.get("kind") or "plasmid", "id": ref["id"]})["path"]
        if not path.startswith("plasmids/"):
            raise ValueError("ref is not a plasmid")
        number = path.split("/")[1]
    number = str(number or "").strip().lstrip("#")
    if not number.isdigit():
        raise ValueError("give plasmid (its number, e.g. 14) or ref (from resolve)")
    body = {"captures": a.get("captures") or [], "reads": a.get("reads") or []}
    if not body["captures"] and not body["reads"]:
        raise ValueError("give captures (ids of reads uploaded as captures) or reads (the bases, FASTA or plain)")
    return api.request("POST", f"plasmids/{int(number)}/verify", body=body)


_REF = {"type": "object", "description": "The record's reference, {\"kind\", \"id\"}, from resolve or search"}
_KIND = {"type": "string", "description": "Instead of ref: what it is (mouse, cage, plasmid, stock_unit…)"}
_CODE = {"type": "string", "description": "With kind: what the lab writes on it (1043, 88, FV12)"}
_WANT = {"type": "object", "description": "The genotype wanted. Each key is a locus (an allele or transgene as the lab "
                                          "writes it in genotypes: Cre, Foxp3, Ai14) → wt, het, hom or carrier "
                                          "(het or hom). A strain or background (C57BL/6J, BALB/c; lab_overview's "
                                          "strains and their backgrounds) is not a locus, though transgenes_in_use "
                                          "may list it: {\"C57BL/6J\": \"hom\"} means both parents of that strain, "
                                          "and it has no het or wt."}

TOOLS.update({
    "search": (
        "Search BioManager as its search box does: records in every database the person can open, and the notebook "
        "pages they may read, by words in names, genotypes, notes and page text. Each match has its kind, ref (for "
        "get, history and proposals), title, a snippet and its link. To pin down a record the person names "
        "(cage 88, #1043), use resolve instead.",
        _obj({"q": {"type": "string", "description": "The words to look for"},
              "limit": {"type": "integer", "description": "At most this many of each kind (default 5)"}}, ("q",)),
        lambda api, a: {"matches": search(api, a["q"], a.get("limit", 5))}),
    "history": (
        "A record's change history, newest first: who changed what and when (field: before → after). It lists "
        "changes to that record's own fields only: a cage's history has its purpose, rack and notes, not mice "
        "moving in or out (each mouse's own history has its cage changing). An admin sees everyone's changes; "
        "anyone else only their own (the full history is for admins).",
        _obj({"ref": _REF, "kind": _KIND, "code": _CODE,
              "limit": {"type": "integer", "description": "At most this many (default 50)"}}),
        lambda api, a: history(api, _record(a), a.get("limit", 50))),
    "lineage": (
        "Family: a plasmid's parents and what was made from it (the tree both ways), or a mouse's parents and "
        "grandparents, its litter and littermates, and the litters it had.",
        _obj({"ref": _REF, "kind": {"type": "string", "enum": ["mouse", "plasmid"],
                                    "description": "Instead of ref: mouse or plasmid"}, "code": _CODE}),
        lambda api, a: lineage(api, _record(a))),
    "availability": (
        "How many living animals fit, and which, as BioManager counts them: genotype (words that must all be in "
        "it, ';'-separated), sex, and an age window in days on a day (default today). Mice in an active or paused "
        "experiment, and in Breeder or Breeding cages, are left out unless asked. Use its numbers; don't count.",
        _obj({"species": {"type": "string", "enum": ["mouse", "zebrafish"], "description": "Default mouse"},
              "genotype": {"type": "string", "description": "e.g. Cre; Ai14"},
              "sex": {"type": "string", "enum": ["F", "M"]},
              "age_min_days": {"type": "integer"}, "age_max_days": {"type": "integer"},
              "on": {"type": "string", "description": "The day, YYYY-MM-DD (default today)"},
              "include_in_experiments": {"type": "boolean"}, "include_breeders": {"type": "boolean"},
              "limit": {"type": "integer", "description": "List at most this many (default 200)"}}),
        lambda api, a: availability(api, a)),
    "breeding_plan": (
        "Plan breeding for a genotype, worked out by BioManager: Mendelian odds for each mating type among the "
        "lab's own breeders (or the crosses given), the lab's own litter size, weaning at P21, and how many matings "
        "to set up by which date for `count` mice of that genotype and sex, aged within the window on `by`; up to "
        "5 loci. Each mating counts the litters born in time (or litters_per_mating); Breeding cages already "
        "pairing a cross count as matings under way, and crosses that would need the same breeders are listed in "
        "clashes. Tell the person the assumptions it lists, which breeders it was unsure how to read, and any "
        "clashes or breeders in experiments.",
        _obj({"want": _WANT, "sex": {"type": "string", "enum": ["F", "M"]},
              "count": {"type": "integer", "description": "How many mice are wanted"},
              "age_min_days": {"type": "integer"}, "age_max_days": {"type": "integer"},
              "by": {"type": "string", "description": "When they are needed, YYYY-MM-DD"},
              "crosses": {"type": "array", "items": {"type": "object"},
                          "description": "Instead of the lab's breeders: [{female: {locus: state}, male: {…}}]"},
              "confidence": {"type": "number", "description": "How sure to be of having enough (default 0.9)"},
              "litters_per_mating": {"type": "integer", "description": "Litters to count from each mating, 1 to 6 "
                                                                       "(default: as many as are born in time for "
                                                                       "`by`, else 1)"}},
             ("want",)),
        lambda api, a: breeding_plan(api, a)),
    "randomize": (
        "Allocate mice to named groups, balanced by sex, litter (or cage) and weight, from a seed that gives the "
        "same allocation again; returns the allocation, the seed and a balance table. Changes nothing. For a new "
        "design it returns groups in experiment_new's shape: pass groups and seed to experiment_new as they are. "
        "With experiment (an existing one) it also returns the experiment_add_animals changes to propose.",
        _obj({"animals": {"type": "array", "items": {}, "description": "The mice: numbers or references"},
              "groups": {"type": "array", "items": {},
                         "description": "Group names, or {name, size} for each when the sizes differ"},
              "seed": {"type": "integer", "description": "To repeat an allocation; left out, BioManager picks one"},
              "by": {"type": "string", "enum": ["litter", "cage"], "description": "Balance across (default litter)"},
              "experiment": {"type": "integer", "description": "The experiment's id, for the changes to propose"}},
             ("animals", "groups")),
        lambda api, a: randomize(api, a)),
    "power_analysis": (
        "Animals per group, computed exactly by BioManager: two groups (a t-test planned with one SD; the "
        "experiment's Welch's t-test has the same power then) or more (one-way ANOVA), from the "
        "smallest difference worth detecting and the SD (or each group's expected mean, or Cohen's d), alpha, "
        "power and the share expected to be lost. With experiment (and day), the SD is that experiment's own "
        "readouts'. Never work out a sample size yourself.",
        _obj({"groups": {"type": "integer", "description": "How many groups (default 2)"},
              "difference": {"type": "number", "description": "The smallest difference worth detecting, in the "
                                                              "readout's units"},
              "sd": {"type": "number"}, "means": {"type": "array", "items": {"type": "number"}},
              "d": {"type": "number", "description": "Cohen's d, instead of difference and sd"},
              "alpha": {"type": "number", "description": "Default 0.05"},
              "power": {"type": "number", "description": "Default 0.8"},
              "attrition": {"type": "number", "description": "Share lost before the end, e.g. 0.1"},
              "experiment": {"type": "integer", "description": "A past experiment whose readouts give the SD"},
              "day": {"type": "string", "description": "Its day number or date (default: the day with most readings)"}}),
        lambda api, a: power_analysis(api, a)),
    "reorder_forecast": (
        "Inventory kept as a number that runs out within `days`: how much is used a week (from the changes to its "
        "quantity), days left, when it runs out and the date to reorder by, given how long an order takes. "
        "without_enough_history names the items it can't forecast yet.",
        _obj({"days": {"type": "integer", "description": "Running out within this many days (default 30)"},
              "lead_days": {"type": "integer", "description": "Days an order takes to arrive (default 14)"}}),
        lambda api, a: reorder_forecast(api, a.get("days", 30), a.get("lead_days", 14))),
    "lab_checks": (
        "The weekly review's checks, done by BioManager: overdue jobs; the animals that may need a look (living mice "
        "down the lab's threshold, 15% unless set, from their peak weight of the last 30 days or their first weight "
        "in an experiment; mice, tanks and fish whose notes changed in the last 14 days use a welfare word such as "
        "hunched, lethargic, wound or 弓背; animals in an active experiment measuring body weight not weighed "
        "for longer than its regimen allows, 7 days if it plans none; mice whose status and experiments disagree); "
        "empty cages still set up, breeders 30 weeks or older, your notebook pages unsigned "
        "for 14 days (once you sign pages), reagents expired, expiring or low, and your proposals waiting. Say what "
        "the records show, with their numbers and the note's own line: never a diagnosis. Each check "
        "has a severity, a count and items with links; an item's propose is the change that fixes it, where one "
        "exists. summary counts each record once per severity, though it may be in two checks. scope mine: only "
        "your own records. It finds problems, not the week ahead: for what is coming, call whats_due too.",
        _obj({"scope": {"type": "string", "enum": ["lab", "mine"]},
              "weight_loss_pct": {"type": "number"}, "breeder_weeks": {"type": "integer"},
              "unsigned_days": {"type": "integer"}, "expiring_days": {"type": "integer"}}),
        lambda api, a: lab_checks(api, a)),
    "experiment_summary": (
        "What the records say about an experiment, for its methods: the animals (species, genotypes, sex, age and "
        "weight at the start, n per group), procedures (agent, dose, route, days, lots used and any used after its expiry), the main readout (and "
        "other_measures, secondary ones a plate filled beside it, each with its tests by day), the tests "
        "used and the last day's comparison, dates; and arrive, the ARRIVE 2.0 Essential 10, each with what is "
        "recorded and what is missing. Never fill in what is missing yourself.",
        _obj({"experiment_id": {"type": "integer", "description": "The experiment's id (resolve finds it by name)"}},
             ("experiment_id",)),
        lambda api, a: experiment_summary(api, a["experiment_id"])),
    "derive": (
        "Work out a derived readout from raw measurements, as BioManager does when they are recorded, changing "
        "nothing: tumour volume from caliper length and width (tumour_lw, L × W² / 2) or three diameters "
        "(tumour_lwh, π/6 × L × W × H), ΔCt (delta_ct), ΔΔCt (delta_delta_ct) and fold change 2^−ΔΔCt "
        "(fold_change) against a control group's mean ΔCt for the same day, % of a baseline "
        "(percent_of_baseline) and blank-subtracted absorbance (blank_subtracted). Without formula it lists them, "
        "each with its inputs and unit. Each row gives its value, or why it can't (a missing, negative or "
        "impossible input). Use it on a caliper sheet, qPCR export or plate-reader file to show the person the "
        "numbers before proposing; for an experiment whose readout is one of these (readout key derived:…), "
        "propose experiment_reading with inputs, not values. Never work these out yourself.",
        _obj({"formula": {"type": "string", "enum": ["tumour_lw", "tumour_lwh", "delta_ct", "delta_delta_ct",
                                                     "fold_change", "percent_of_baseline", "blank_subtracted"],
                          "description": "Left out: the list of formulas"},
              "rows": {"type": "array", "items": {"type": "object"},
                       "description": "One per animal or well: its inputs ({\"length\": 12.1, \"width\": 8.4}; "
                                      "{\"ct_target\", \"ct_reference\"}; {\"value\", \"baseline\"}; "
                                      "{\"od\", \"blank\"}), with animal, group and day if you have them"},
              "control": {"type": "string", "description": "For delta_delta_ct and fold_change: the control group "
                                                           "(each row's group)"}}),
        lambda api, a: derive(api, a)),
    "plate_readout": (
        "Read a plate reader's or qPCR machine's export onto an experiment's plate layout (which animal is in which "
        "well, the blanks and standards), as the experiment page's Plate layout does, changing nothing. Give the export as "
        "text (CSV or tab-separated: an 8 × 12 grid with row letters and column numbers, or a list with Well and a "
        "value or Ct column, and Target for several genes) or as capture (a capture's id), and the layout by its id "
        "or name (a name it doesn't know answers with the layouts the experiment has). It "
        "averages each animal's replicate wells, takes off the blank or reads a standard curve, or for a ΔCt, ΔΔCt "
        "or fold change readout takes the target and reference genes' Cts; it flags replicates far from the others "
        "and wells past what the instrument reads. Show the person the values and flags, then propose the change it "
        "returns (experiment_reading) as it is. Never average or subtract the wells yourself. A plate fills the "
        "experiment's main readout, or with measure another measure it has (e.g. an IL-6 ELISA beside tumour "
        "volume; GET /api/v1/experiments/{id} lists page.measures). With standards, curve chooses the standard "
        "curve: linear, loglog or 4pl (four-parameter logistic); left out, 4PL for five or more concentrations "
        "over a tenfold range, linear otherwise.",
        _obj({"experiment_id": {"type": "integer", "description": "The experiment's id (resolve finds it by name)"},
              "layout": {"type": "string", "description": "The plate layout's id or name"},
              "wells": {"type": "object", "description": "Instead of a saved layout: {\"A1\": \"mouse:9\", "
                                                         "\"H12\": \"blank\", \"G1\": {\"role\": \"standard\", "
                                                         "\"conc\": 10}}, with format"},
              "format": {"type": "integer", "enum": [24, 48, 96, 384]},
              "text": {"type": "string", "description": "The export, as text"},
              "capture": {"type": "integer", "description": "A capture whose file or text is the export"},
              "target": {"type": "string", "description": "Which read or gene to use, when the export has several"},
              "reference": {"type": "string", "description": "For ΔCt, ΔΔCt and fold change: the reference gene"},
              "subtract_blank": {"type": "boolean", "description": "Take the blank wells' mean off (default true)"},
              "standard_curve": {"type": "boolean", "description": "Read each value off the standard curve"},
              "curve": {"type": "string", "enum": ["linear", "loglog", "4pl"],
                        "description": "The standard curve's shape (left out: chosen from the standards)"},
              "measure": {"type": "string", "description": "Another measure of the experiment to fill (its name), "
                                                           "instead of its main readout"},
              "skip": {"type": "array", "items": {"type": "string"}, "description": "Wells to leave out, e.g. [\"A3\"]"},
              "date": {"type": "string", "description": "The day the readings are for (today if left out)"}},
             ("experiment_id",)),
        lambda api, a: plate_readout(api, a)),
})

# The write-ups (app/assistant_reports.py): BioManager counts and lists, the assistant writes.
TOOLS.update({
    "animal_use": (
        "Animal use for an IACUC or animal-care annual review, counted by BioManager: for a period (the last twelve "
        "months unless start and end are given) and optionally one protocol, the mice, zebrafish and the lab's own "
        "organisms per protocol, "
        "species and strain: born, added to the records, new (both), used in experiments, died or euthanized (with "
        "each reason, died_by_reason), and breeding; each per protocol and in all. basis says what each count is "
        "based on, flags what the records can't say (animals with no protocol, deaths with no reason). Use its "
        "numbers as they are: an animal may be in several columns, so never add them up or estimate one.",
        _obj({"start": {"type": "string", "description": "The first day, YYYY-MM-DD (default twelve months before end)"},
              "end": {"type": "string", "description": "The last day, YYYY-MM-DD (default today)"},
              "protocol": {"type": "string", "description": "Only this protocol, as the records write it"}}),
        lambda api, a: animal_use(api, a)),
    "handover": (
        "Everything one person owns or looks after, for when they leave the lab: their cages and mice by rack and "
        "position, breeding cages and what's due on them, fish tanks, fly and worm vials, inventory items (glycerol "
        "stocks among them) with where each is, plasmids and their boxes, other animals, planned and running "
        "experiments with their next steps, the racks and boxes they made, notebook pages not signed, proposals "
        "waiting, and everything of theirs due in the next `days` (30) days; each with its link and reference. "
        "username: whose (you if left out); only an admin may ask about someone else. owner_changes names, for "
        "each kind of record, the action that hands it to someone else.",
        _obj({"username": {"type": "string", "description": "Whose (default: you)"},
              "days": {"type": "integer", "description": "What is due within this many days (default 30)"}}),
        lambda api, a: handover(api, a)),
    "verify_sequencing": (
        "Check Sanger sequencing reads of a miniprep or clone against a plasmid's map, as BioManager does it: each "
        "read placed on the plasmid (either strand, across the origin), its ends trimmed by quality, and every "
        "mismatch, insertion and deletion with its position, the read's quality there, the map features it falls "
        "in and what it does to a coding sequence's amino acid; then a verdict for each read and for the clone. "
        "Give the reads as captures (ids from list_captures: an .ab1 chromatogram or a FASTA the person uploaded) "
        "or as reads (their bases, FASTA or plain). Changes nothing. Give its verdict and differences as they are; "
        "a difference marked not confident is at low quality and likely the read's error. A read's mixed says "
        "its chromatogram has second peaks under many good bases (a mix of two clones; from_plasmid, when they "
        "start at one place: two clones differing by an indel there): tell the person to pick a single colony.",
        _obj({"plasmid": {"type": "string", "description": "The plasmid's number, e.g. 14"},
              "ref": {"type": "object", "description": "Instead of plasmid: its reference from resolve"},
              "captures": {"type": "array", "items": {"type": "integer"},
                           "description": "Ids of captures holding the reads (.ab1 or FASTA)"},
              "reads": {"type": "array", "items": {},
                        "description": "Reads as text: each its bases (FASTA or plain) or {name, sequence}"}}),
        lambda api, a: verify_sequencing(api, a)),
})


def propose_description(api: Api) -> str:
    try:
        catalogue = describe_actions(list_actions(api))
    except ApiError as e:
        catalogue = f"(The list of actions couldn't be read now: {e} Call list_actions.)"
    return f"{PROPOSE_HELP}\n\nActions, by area:\n{catalogue}"


def description(api: Api, name: str) -> str:
    return TOOLS[name][0] or propose_description(api)


def call_tool(api: Api, name: str, arguments: dict | None) -> dict:
    """Run a tool; what it returns, or {"error": …} in words for the assistant."""
    if name not in TOOLS:
        raise KeyError(name)
    args = dict(arguments or {})
    try:
        return TOOLS[name][2](api, args)
    except ApiError as e:
        return {"error": str(e)}
    except (KeyError, TypeError, ValueError) as e:
        return {"error": f"Check the arguments: {e}"}


PROMPTS = {
    "log_today": (
        "Log today's work from my notes",
        [{"name": "notes", "description": "What you did today", "required": True}],
        lambda a: ("Here is what I did today. Record it in BioManager: resolve every animal, cage and experiment I "
                   "mention, ask me about anything unclear, then send it all as one proposal and give me the link "
                   "to approve it. Add a short line to today's daily log too.\n\n" + a.get("notes", ""))),
    "log_from_photo": (
        "Log from a photo of my notebook, whiteboard or cage card",
        [{"name": "what_it_shows", "description": "What the photo is of", "required": False}],
        lambda a: ("Read the photo I attached (my notebook, a whiteboard or cage cards) and record what it says in "
                   "BioManager: resolve every record, read numbers carefully and ask me about any you can't read, "
                   "then send one proposal and give me the link to approve it." +
                   (f"\n\nIt shows: {a['what_it_shows']}" if a.get("what_it_shows") else ""))),
    "process_captures": (
        "Turn my captures from the bench into records", [],
        lambda a: ("Process my captures in BioManager: read each one that is waiting (my notes and photos from the "
                   "bench), resolve every record it mentions, read numbers carefully and ask me about any you can't "
                   "read, then send one proposal made from them, each change with its evidence, and give me the "
                   "link to approve it.")),
    "plan_tomorrow": (
        "Plan tomorrow from what's due", [],
        lambda a: ("Look at what is due in BioManager over the next two days and plan my tomorrow: group the work by "
                   "room and time, say what each needs, and flag anything overdue. Don't propose changes unless I "
                   "ask.")),
}

# The prompts over the compute tools: BioManager works the numbers out.
PROMPTS.update({
    "weekly_review": (
        "Review the lab this week", [],
        lambda a: ("Review the lab this week with BioManager's weekly checks (lab_checks). Summarise what needs "
                   "attention by severity, highest first, naming each record with its link, and say which checks "
                   "found nothing. Then offer one proposal that clears the items that come with a fix (their "
                   "propose changes); send it only if I say yes, and give me the link to approve it.")),
    "design_experiment": (
        "Design an experiment",
        [{"name": "goal", "description": "What the experiment should find out", "required": True}],
        lambda a: ("Help me design this experiment from the lab's records in BioManager. Find the animals we have "
                   "(availability) and, if there aren't enough, how to breed them in time (breeding_plan); the "
                   "animals per group (power_analysis, with the SD from a past experiment's readouts if we have "
                   "one); a balanced, seeded allocation (randomize); and the procedures from the lab's saved "
                   "regimens (list regimens). Take doses only from the lab's regimens or a source you cite, and "
                   "leave the check against our animal protocol (IACUC) to me. Give me the design with every number "
                   "as BioManager worked it out and every assumption it made. Don't propose any change unless I ask."
                   "\n\nThe goal: " + a.get("goal", ""))),
    "write_methods": (
        "Write the methods for an experiment",
        [{"name": "experiment", "description": "The experiment's name or id", "required": True}],
        lambda a: ("Write the methods for this experiment from BioManager's records: resolve it, call "
                   "experiment_summary, and write the animals, procedures, outcome measure and statistics as a "
                   "methods paragraph in the past tense, using only what it returns. Then give the ARRIVE 2.0 "
                   "Essential 10 checklist, each item with what the records say and what is missing, and ask me for "
                   "the missing facts. Never invent a fact, number, supplier or lot.\n\nThe experiment: "
                   + a.get("experiment", ""))),
    "animal_use_report": (
        "Draft the animal-use report for an annual review",
        [{"name": "protocol", "description": "Only this protocol (all if left out)", "required": False},
         {"name": "period", "description": "The dates it covers (the last twelve months if left out)",
          "required": False}],
        lambda a: ("Draft our animal-use report for the IACUC or animal-care annual review from BioManager's records: "
                   "call animal_use for the period and protocol below. Give a table per protocol with the species and "
                   "strains, and for each the animals born, added, used in experiments, died or euthanized (with the "
                   "reasons) and breeding, exactly as animal_use counts them, then the totals. Under it, say in plain "
                   "words what each count is based on (its basis) and what the records can't say (its flags), such as "
                   "animals with no protocol recorded. Never invent, estimate or add up a number yourself. Say it is a "
                   "draft for me to check against our institution's form, and don't propose any change."
                   + (f"\n\nThe protocol: {a['protocol']}" if a.get("protocol") else "")
                   + (f"\n\nThe period: {a['period']}" if a.get("period") else ""))),
    "handover_report": (
        "Write a handover for someone leaving the lab",
        [{"name": "person", "description": "Who is leaving (you if left out)", "required": False}],
        lambda a: ("Write a handover document from BioManager's records for someone leaving the lab: "
                   + (a["person"] if a.get("person") else "me, unless I name someone else")
                   + ". Find their username among lab_overview's people if it isn't mine, and call handover. "
                   "Group it as it comes: animals and cages by rack and position, breeding cages and what is due on "
                   "them, fish tanks, vials and plates, inventory items and glycerol stocks with where each is, "
                   "plasmids, experiments with their next steps, racks and boxes, notebook pages not signed and "
                   "proposals waiting, each record with its link; then everything due in the next 30 days, overdue "
                   "first. Leave out empty groups and invent nothing. At the end, offer to propose handing the "
                   "records to someone else (owner_changes' actions); propose them only if I ask and name who.")),
})
