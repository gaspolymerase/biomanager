#!/usr/bin/env python3
"""Who uses BioManager: every installation that sent the anonymous daily counts.

    python scripts/installs.py                 # one line per installation, then a summary
    python scripts/installs.py --csv out.csv   # the same as a spreadsheet (for a paper's numbers)
    python scripts/installs.py --mine ID       # an install id of your own, left out from then on

It reads BioManager's PostHog project, where app/telemetry.py sends, with a
personal API key in ~/.config/biomanager/posthog-personal-key (or
POSTHOG_PERSONAL_API_KEY), as scripts/promo-metrics.py does. Only what the
heartbeat holds: version, desktop or server, the operating system's family,
the database, members and the active in the last 7 days as ranges, and which
functions and databases are on. Never who or where.

Left out: "+dev" builds (run from source: a demo lab, the upgrade check),
anything before COUNT_FROM (GitHub's build machines, before CI was switched
off), and the ids in ~/.config/biomanager/own-installs, one per line: your
own computers and servers (the table's first 8 characters are enough). An
installation's id is the "distinct_id" in the JSON on its Usage report
(Help → Feedback → Usage report).

A lab is a server, or a desktop with more than one member. Kept means seen on
days at least 7 (or 30) days apart: someone came back, not one try.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

POSTHOG = os.environ.get("POSTHOG_HOST", "https://us.posthog.com")
PROJECT = os.environ.get("POSTHOG_PROJECT_ID", "638885")   # BioManager's PostHog project
COUNT_FROM = "2026-10-01 04:05:00"   # UTC, as in scripts/promo-metrics.py
CONFIG = Path.home() / ".config/biomanager"
KEY_FILE = CONFIG / "posthog-personal-key"
MINE_FILE = CONFIG / "own-installs"

QUERY = f"""
select distinct_id,
       argMax(properties.kind, timestamp), argMax(properties.os, timestamp),
       argMax(properties.database, timestamp), argMax(properties.version, timestamp),
       min(timestamp), max(timestamp), count(distinct toDate(timestamp)),
       argMax(properties.members, timestamp), argMax(properties.active_7_days, timestamp),
       argMax(properties.functions_on, timestamp), argMax(properties.databases_on, timestamp)
from events
where event = 'heartbeat' and timestamp >= toDateTime('{COUNT_FROM}')
  and not (properties.version like '%+dev%')
group by distinct_id
order by min(timestamp)
limit 10000
"""
FIELDS = ("id", "kind", "os", "database", "version", "first_seen", "last_seen", "days_seen", "span_days",
          "members", "active_7_days", "functions_on", "databases_on")


def personal_key() -> str:
    key = os.environ.get("POSTHOG_PERSONAL_API_KEY", "")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        sys.exit(f"No PostHog personal API key: put one in {KEY_FILE} or POSTHOG_PERSONAL_API_KEY.")
    return key


def mine() -> set[str]:
    if not MINE_FILE.exists():
        return set()
    lines = MINE_FILE.read_text(encoding="utf-8").splitlines()
    return {line.split("#")[0].strip() for line in lines} - {""}


def query(sql: str) -> list:
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": sql}}).encode()
    req = urllib.request.Request(f"{POSTHOG}/api/projects/{PROJECT}/query/", data=body,
                                 headers={"Authorization": f"Bearer {personal_key()}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["results"]


def when(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def databases(raw) -> str:
    """{"organisms": 1, "stocks": {"fly": 1}, "inventories": {"reagents": 1}} → "organisms, fly, reagents"."""
    try:
        on = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return ""
    names = ["organisms"] if on.get("organisms") else []
    for group in ("stocks", "inventories"):
        names += [name for name, n in (on.get(group) or {}).items() if n]
    return ", ".join(names)


def installations() -> list[dict]:
    skip = mine()
    rows = []
    for (iid, kind, os_, db, version, first, last, days, members, active, functions,
         dbs) in query(QUERY):
        if any(iid.startswith(own) for own in skip):   # the table's 8-character id is enough
            continue
        try:
            functions = ", ".join(json.loads(functions)) if isinstance(functions, str) else ", ".join(functions or [])
        except ValueError:
            pass
        rows.append({"id": iid, "kind": kind, "os": os_, "database": db, "version": version,
                     "first_seen": first[:10], "last_seen": last[:10], "days_seen": days,
                     "span_days": (when(last) - when(first)).days, "members": members,
                     "active_7_days": active, "functions_on": functions, "databases_on": databases(dbs)})
    return rows


def is_lab(row: dict) -> bool:
    return row["kind"] == "server" or row["members"] not in ("0", "1")


def summary(rows: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    recent = [r for r in rows if (now - when(r["last_seen"] + "T00:00:00+00:00")).days <= 7]
    count = lambda pred: sum(1 for r in rows if pred(r))   # noqa: E731
    return "\n".join([
        f"Installations: {len(rows)} ({count(lambda r: r['kind'] == 'desktop')} desktop, "
        f"{count(lambda r: r['kind'] == 'server')} server), since {COUNT_FROM[:10]}",
        f"Labs (a server, or more than one member): {count(is_lab)}",
        f"Sent in the last 7 days: {len(recent)}",
        f"Kept 7+ days: {count(lambda r: r['span_days'] >= 7)}; 30+ days: {count(lambda r: r['span_days'] >= 30)}",
        f"Left out as yours: {len(mine())} id(s) in {MINE_FILE}",
    ])


def table(rows: list[dict]) -> str:
    head = f"{'id':8}  {'kind':7} {'os':7} {'version':8} {'first':10} {'last':10} {'days':>4}  {'members':7} {'active':6}  on"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(f"{r['id'][:8]:8}  {r['kind']:7} {r['os']:7} {r['version']:8} {r['first_seen']:10} "
                     f"{r['last_seen']:10} {r['days_seen']:>4}  {r['members']:7} {r['active_7_days']:6}  "
                     f"{r['functions_on']}" + (f"; {r['databases_on']}" if r["databases_on"] else ""))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", type=Path, help="write one row per installation to this file")
    ap.add_argument("--mine", metavar="ID", help="add an install id of your own to the left-out list")
    args = ap.parse_args()
    if args.mine:
        CONFIG.mkdir(parents=True, exist_ok=True)
        with MINE_FILE.open("a", encoding="utf-8") as f:
            f.write(args.mine.strip() + "\n")
        print(f"Left out from now on: {args.mine.strip()}")
        return
    rows = installations()
    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"{len(rows)} installations written to {args.csv}")
    print(table(rows))
    print()
    print(summary(rows))


if __name__ == "__main__":
    main()
