#!/usr/bin/env python3
"""How the launch is doing: stars, downloads, visits, and labs using it.

    python scripts/promo-metrics.py            # today's numbers, added to promo/out/metrics.csv
    python scripts/promo-metrics.py --review   # the week-1 review: keep going, improve, or pay

From GitHub (through `gh`, signed in as the repository's owner): stars,
forks and watchers, downloads of every release asset (on this repository
and on biomanager-app, where desktop apps from before the move get 0.10.1),
and the last 14 days' visits to the repository's GitHub pages, clones and
referring sites (traffic needs push access; the website on GitHub Pages
isn't counted by GitHub). From PostHog, with a personal API key in
~/.config/biomanager/posthog-personal-key (or POSTHOG_PERSONAL_API_KEY): how many installations sent the daily
heartbeat (app/telemetry.py) in the last day and week, desktop or server.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "promo/out/metrics.csv"
REPO, OLD = "gaspolymerase/biomanager", "gaspolymerase/biomanager-app"   # OLD: 0.10.1, for apps from before the move
POSTHOG = os.environ.get("POSTHOG_HOST", "https://us.posthog.com")
PROJECT = "638885"   # BioManager's PostHog project (app/telemetry.py's key belongs to it)
# A personal API key that may read the project ("Query: read"), kept on this
# computer only: never in the repository.
KEY_FILE = Path.home() / ".config/biomanager/posthog-personal-key"
COUNT_FROM = "2026-10-01 04:05:00"   # UTC: after the v1.0.0 tag's upgrade check (04:01-04:03), the last CI run with heartbeats on

# The week-1 bar. Below it on most lines: change something before carrying on.
# Stars and downloads count from the last numbers taken before the launch day.
TARGETS = {"stars": 30, "downloads": 50, "installs_7d": 10, "repo_visitors_14d": 300}


def gh(path: str):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout) if r.returncode == 0 else None


def github() -> dict:
    info = gh(f"repos/{REPO}") or {}
    old = gh(f"repos/{OLD}") or {}
    views = gh(f"repos/{REPO}/traffic/views") or {}
    clones = gh(f"repos/{REPO}/traffic/clones") or {}
    out = {"stars": info.get("stargazers_count", 0) + old.get("stargazers_count", 0),
           "forks": info.get("forks_count", 0), "watchers": info.get("subscribers_count", 0),
           "repo_visitors_14d": views.get("uniques", 0), "repo_views_14d": views.get("count", 0),
           "cloners_14d": clones.get("uniques", 0)}
    referrers = {r["referrer"]: r["uniques"] for r in gh(f"repos/{REPO}/traffic/popular/referrers") or []}
    # A release is on both repositories while old apps are moved over; count every download.
    by_tag: dict[str, int] = {}
    for repo in (REPO, OLD):
        for rel in gh(f"repos/{repo}/releases?per_page=100") or []:
            by_tag[rel["tag_name"]] = by_tag.get(rel["tag_name"], 0) + sum(
                a.get("download_count", 0) for a in rel.get("assets", []))
    out["downloads"] = sum(by_tag.values())
    newest = max(by_tag, key=lambda t: [int(x) for x in t.lstrip("v").split(".") if x.isdigit()], default=None)
    out["downloads_latest"] = by_tag.get(newest, 0)
    return out, referrers


def posthog() -> dict:
    key, project = os.environ.get("POSTHOG_PERSONAL_API_KEY"), os.environ.get("POSTHOG_PROJECT_ID", PROJECT)
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if not key or not project:
        return {}
    def q(sql):
        body = json.dumps({"query": {"kind": "HogQLQuery", "query": sql}}).encode()
        req = urllib.request.Request(f"{POSTHOG}/api/projects/{project}/query/", data=body,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["results"]
    # Heartbeats before COUNT_FROM came from GitHub's build machines, before CI was switched off;
    # a "+dev" version runs from source (demo labs for the clips, tests), never someone's install.
    beat = (f"from events where event = 'heartbeat' and timestamp >= toDateTime('{COUNT_FROM}') "
            "and not (properties.version like '%+dev%') and timestamp > now() - interval")
    try:
        out = {"installs_1d": q(f"select count(distinct distinct_id) {beat} 1 day")[0][0],
               "installs_7d": q(f"select count(distinct distinct_id) {beat} 7 day")[0][0]}
        for kind, n in q(f"select properties.kind, count(distinct distinct_id) {beat} 7 day group by 1"):
            out[f"installs_7d_{kind}"] = n
        return out
    except Exception as e:  # noqa: BLE001
        print(f"PostHog: {e}", file=sys.stderr)
        return {}


def save(row: dict):
    CSV.parent.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(CSV.open())) if CSV.exists() else []
    rows = [r for r in rows if r["date"] != row["date"]] + [{k: str(v) for k, v in row.items()}]
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k != "date", k))
    with CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return rows


def review(row: dict, referrers: dict, rows: list) -> str:
    start = json.loads((ROOT / "promo/posts.json").read_text())["start"]
    before = [r for r in rows if r["date"] < start]
    row = dict(row)
    if before:
        for k in ("stars", "downloads"):
            row[k] = int(row[k]) - int(before[-1].get(k) or 0)
    lines = ["## Week-1 review", "", "| | Now | Target | |", "|---|---:|---:|---|"]
    met = 0
    for k, target in TARGETS.items():
        have = row.get(k)
        if have is None or have == "":
            lines.append(f"| {k} | n/a | {target} | not measured |")
            continue
        ok = int(have) >= target
        met += ok
        lines.append(f"| {k} | {have} | {target} | {'met' if ok else 'below'} |")
    measured = sum(1 for k in TARGETS if row.get(k) not in (None, ""))
    lines += ["", "Where visits came from (14 days): " + (", ".join(
        f"{r} {n}" for r, n in sorted(referrers.items(), key=lambda x: -x[1])[:8]) or "nothing yet"), ""]
    if measured and met >= measured / 2:
        lines.append("**Keep going.** Most targets met: carry on with the calendar, and put more into the "
                     "platform that sent the most visitors.")
    elif row.get("installs_7d") not in (None, "") and int(row["downloads"]) >= 20 \
            and int(row["installs_7d"]) < int(row["downloads"]) // 5:
        lines.append("**Improve the software first.** People download it but don't keep it running: look at "
                     "Feedback notes and GitHub issues, and try the first ten minutes yourself on a clean machine.")
    else:
        lines.append("**Reach more people.** Few people are arriving. Options: boost the best post on the "
                     "platform that did best (Xiaohongshu 薯条, Bilibili 起飞, a promoted post on X or LinkedIn), "
                     "post in r/labrats and lab-manager mailing lists, or write to a few labs directly.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--review", action="store_true")
    args = ap.parse_args()
    gh_row, referrers = github()
    row = {"date": date.today().isoformat(), **gh_row, **posthog()}
    rows = save(row)
    prev = rows[-2] if len(rows) > 1 else {}
    print(f"# Launch numbers, {row['date']}\n")
    for k in ("stars", "downloads", "downloads_latest", "repo_visitors_14d", "cloners_14d",
              "installs_1d", "installs_7d", "installs_7d_desktop", "installs_7d_server"):
        if k in row:
            change = f" ({int(row[k]) - int(prev[k]):+d})" if prev.get(k) not in (None, "") else ""
            print(f"- {k}: {row[k]}{change}")
    if "installs_7d" not in row:
        print(f"- installs: not measured (put a PostHog personal API key in {KEY_FILE})")
    if referrers:
        print("- referrers: " + ", ".join(f"{r} {n}" for r, n in sorted(referrers.items(), key=lambda x: -x[1])[:8]))
    if args.review:
        print("\n" + review(row, referrers, rows))


if __name__ == "__main__":
    main()
