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

import json
import os
import sys
from pathlib import Path

# The shared tools live in the app (app/assistant_tools.py: standard library only).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.assistant_tools import (INSTRUCTIONS, PROMPTS, Api, ApiError, call_tool, description,  # noqa: E402,F401
                                 describe_actions, discard_proposal, get, get_capture, lab_overview, list_actions,
                                 list_captures, list_records, proposal_status, propose_changes, resolve, whats_due)
from app.assistant_tools import (availability, breeding_plan, derive, experiment_summary, history,  # noqa: E402,F401
                                 lab_checks, lineage, power_analysis, randomize, reorder_forecast, search)
from app.assistant_tools import animal_use, handover  # noqa: E402,F401
from app.assistant_tools import plate_readout, verify_sequencing  # noqa: E402,F401


def with_content(out: dict):
    """A tool's answer with MCP content of its own (get_capture's photo,
    `_content`): the text, then each image as the SDK's ImageContent. An SDK
    without it gets the text, saying the photo couldn't be attached."""
    extra = out.pop("_content", None) if isinstance(out, dict) else None
    if not extra:
        return out
    try:
        from mcp.types import ImageContent, TextContent
    except ImportError:
        out["photo"] = "This MCP server can't attach the photo: ask the person what it shows."
        return out
    return [TextContent(type="text", text=json.dumps(out, ensure_ascii=False, default=str)),
            *(ImageContent(type="image", data=c["data"], mimeType=c["mimeType"]) for c in extra
              if c.get("type") == "image")]


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
    def get_tool(path: str = "", ref: dict | None = None) -> dict:
        return call_tool(api, "get", {"path": path, "ref": ref})

    @server.tool(name="list", description=description(api, "list"))
    def list_tool(path: str, filters: dict | None = None, limit: int = 100) -> dict:
        return call_tool(api, "list", {"path": path, "filters": filters, "limit": limit})

    @server.tool(name="whats_due", description=description(api, "whats_due"))
    def whats_due_tool(days: int = 7) -> dict:
        return call_tool(api, "whats_due", {"days": days})

    @server.tool(name="list_actions", description=description(api, "list_actions"))
    def list_actions_tool(names: list[str] | None = None, area: str = "") -> dict:
        return call_tool(api, "list_actions", {"names": names, "area": area})

    @server.tool(name="propose_changes", description=description(api, "propose_changes"))
    def propose_changes_tool(summary: str, changes: list[dict], replaces: int | None = None,
                             captures: list[int] | None = None) -> dict:
        return call_tool(api, "propose_changes", {"summary": summary, "changes": changes, "replaces": replaces,
                                                  "captures": captures})

    @server.tool(name="proposal_status", description=description(api, "proposal_status"))
    def proposal_status_tool(proposal_id: int) -> dict:
        return call_tool(api, "proposal_status", {"proposal_id": proposal_id})

    @server.tool(name="discard_proposal", description=description(api, "discard_proposal"))
    def discard_proposal_tool(proposal_id: int) -> dict:
        return call_tool(api, "discard_proposal", {"proposal_id": proposal_id})

    # Looking up and working out: BioManager computes (app/assistant_compute.py).
    def given(**kwargs) -> dict:
        return {k: v for k, v in kwargs.items() if v is not None}

    @server.tool(name="search", description=description(api, "search"))
    def search_tool(q: str, limit: int = 5) -> dict:
        return call_tool(api, "search", {"q": q, "limit": limit})

    @server.tool(name="history", description=description(api, "history"))
    def history_tool(ref: dict | None = None, kind: str | None = None, code: str | None = None,
                     limit: int = 50) -> dict:
        return call_tool(api, "history", given(ref=ref, kind=kind, code=code, limit=limit))

    @server.tool(name="lineage", description=description(api, "lineage"))
    def lineage_tool(ref: dict | None = None, kind: str | None = None, code: str | None = None) -> dict:
        return call_tool(api, "lineage", given(ref=ref, kind=kind, code=code))

    @server.tool(name="availability", description=description(api, "availability"))
    def availability_tool(species: str = "mouse", genotype: str = "", sex: str | None = None,
                          age_min_days: int | None = None, age_max_days: int | None = None, on: str | None = None,
                          include_in_experiments: bool = False, include_breeders: bool = False,
                          limit: int = 200) -> dict:
        return call_tool(api, "availability", given(
            species=species, genotype=genotype, sex=sex, age_min_days=age_min_days, age_max_days=age_max_days, on=on,
            include_in_experiments=include_in_experiments, include_breeders=include_breeders, limit=limit))

    @server.tool(name="breeding_plan", description=description(api, "breeding_plan"))
    def breeding_plan_tool(want: dict, sex: str | None = None, count: int = 1, age_min_days: int | None = None,
                           age_max_days: int | None = None, by: str | None = None, crosses: list[dict] | None = None,
                           confidence: float = 0.9) -> dict:
        return call_tool(api, "breeding_plan", given(
            want=want, sex=sex, count=count, age_min_days=age_min_days, age_max_days=age_max_days, by=by,
            crosses=crosses, confidence=confidence))

    @server.tool(name="randomize", description=description(api, "randomize"))
    def randomize_tool(animals: list, groups: list, seed: int | None = None, by: str = "litter",
                       experiment: int | None = None) -> dict:
        return call_tool(api, "randomize", given(animals=animals, groups=groups, seed=seed, by=by,
                                                 experiment=experiment))

    @server.tool(name="power_analysis", description=description(api, "power_analysis"))
    def power_analysis_tool(groups: int = 2, difference: float | None = None, sd: float | None = None,
                            means: list[float] | None = None, d: float | None = None, alpha: float = 0.05,
                            power: float = 0.8, attrition: float = 0.0, experiment: int | None = None,
                            day: str | None = None) -> dict:
        return call_tool(api, "power_analysis", given(
            groups=groups, difference=difference, sd=sd, means=means, d=d, alpha=alpha, power=power,
            attrition=attrition, experiment=experiment, day=day))

    @server.tool(name="reorder_forecast", description=description(api, "reorder_forecast"))
    def reorder_forecast_tool(days: int = 30, lead_days: int = 14) -> dict:
        return call_tool(api, "reorder_forecast", {"days": days, "lead_days": lead_days})

    @server.tool(name="lab_checks", description=description(api, "lab_checks"))
    def lab_checks_tool(scope: str = "lab", weight_loss_pct: float | None = None, breeder_weeks: int | None = None,
                        unsigned_days: int | None = None, expiring_days: int | None = None) -> dict:
        return call_tool(api, "lab_checks", given(scope=scope, weight_loss_pct=weight_loss_pct,
                                                  breeder_weeks=breeder_weeks, unsigned_days=unsigned_days,
                                                  expiring_days=expiring_days))

    @server.tool(name="experiment_summary", description=description(api, "experiment_summary"))
    def experiment_summary_tool(experiment_id: int) -> dict:
        return call_tool(api, "experiment_summary", {"experiment_id": experiment_id})

    # The write-ups: BioManager counts and lists (app/assistant_reports.py).
    @server.tool(name="animal_use", description=description(api, "animal_use"))
    def animal_use_tool(start: str | None = None, end: str | None = None, protocol: str | None = None) -> dict:
        return call_tool(api, "animal_use", given(start=start, end=end, protocol=protocol))

    @server.tool(name="handover", description=description(api, "handover"))
    def handover_tool(username: str | None = None, days: int = 30) -> dict:
        return call_tool(api, "handover", given(username=username, days=days))

    @server.tool(name="derive", description=description(api, "derive"))
    def derive_tool(formula: str | None = None, rows: list[dict] | None = None, control: str | None = None) -> dict:
        return call_tool(api, "derive", given(formula=formula, rows=rows, control=control))

    @server.tool(name="plate_readout", description=description(api, "plate_readout"))
    def plate_readout_tool(experiment_id: int, layout: str | None = None, wells: dict | None = None,
                           format: int | None = None, text: str | None = None, capture: int | None = None,
                           target: str | None = None, reference: str | None = None,
                           subtract_blank: bool | None = None, standard_curve: bool | None = None,
                           curve: str | None = None, measure: str | None = None,
                           skip: list[str] | None = None, date: str | None = None) -> dict:
        return call_tool(api, "plate_readout", given(
            experiment_id=experiment_id, layout=layout, wells=wells, format=format, text=text, capture=capture,
            target=target, reference=reference, subtract_blank=subtract_blank, standard_curve=standard_curve,
            curve=curve, measure=measure, skip=skip, date=date))

    @server.tool(name="verify_sequencing", description=description(api, "verify_sequencing"))
    def verify_sequencing_tool(plasmid: str | None = None, ref: dict | None = None, captures: list[int] | None = None,
                               reads: list | None = None) -> dict:
        return call_tool(api, "verify_sequencing", given(plasmid=plasmid, ref=ref, captures=captures, reads=reads))

    @server.tool(name="list_captures", description=description(api, "list_captures"))
    def list_captures_tool(status: str = "waiting") -> dict:
        return call_tool(api, "list_captures", {"status": status})

    # No return annotation: its answer is text and an image, not one JSON object.
    @server.tool(name="get_capture", description=description(api, "get_capture"))
    def get_capture_tool(capture_id: int):
        return with_content(call_tool(api, "get_capture", {"capture_id": capture_id}))

    @server.prompt(name="log_today", description=PROMPTS["log_today"][0])
    def log_today(notes: str) -> str:
        return PROMPTS["log_today"][2]({"notes": notes})

    @server.prompt(name="log_from_photo", description=PROMPTS["log_from_photo"][0])
    def log_from_photo(what_it_shows: str = "") -> str:
        return PROMPTS["log_from_photo"][2]({"what_it_shows": what_it_shows})

    @server.prompt(name="process_captures", description=PROMPTS["process_captures"][0])
    def process_captures() -> str:
        return PROMPTS["process_captures"][2]({})

    @server.prompt(name="plan_tomorrow", description=PROMPTS["plan_tomorrow"][0])
    def plan_tomorrow() -> str:
        return PROMPTS["plan_tomorrow"][2]({})

    @server.prompt(name="weekly_review", description=PROMPTS["weekly_review"][0])
    def weekly_review() -> str:
        return PROMPTS["weekly_review"][2]({})

    @server.prompt(name="design_experiment", description=PROMPTS["design_experiment"][0])
    def design_experiment(goal: str) -> str:
        return PROMPTS["design_experiment"][2]({"goal": goal})

    @server.prompt(name="write_methods", description=PROMPTS["write_methods"][0])
    def write_methods(experiment: str) -> str:
        return PROMPTS["write_methods"][2]({"experiment": experiment})

    @server.prompt(name="animal_use_report", description=PROMPTS["animal_use_report"][0])
    def animal_use_report(protocol: str = "", period: str = "") -> str:
        return PROMPTS["animal_use_report"][2]({"protocol": protocol, "period": period})

    @server.prompt(name="handover_report", description=PROMPTS["handover_report"][0])
    def handover_report(person: str = "") -> str:
        return PROMPTS["handover_report"][2]({"person": person})

    return server


def main() -> None:
    base, token = os.environ.get("BIOMANAGER_URL", "").strip(), os.environ.get("BIOMANAGER_TOKEN", "").strip()
    if not base or not token:
        sys.exit("Set BIOMANAGER_URL (your BioManager's address) and BIOMANAGER_TOKEN (a token from "
                 "Settings → API tokens; Read and propose is the one for an assistant). See mcp/README.md.")
    build_server(Api(base, token)).run("stdio")


if __name__ == "__main__":
    main()
