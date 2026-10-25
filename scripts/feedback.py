#!/usr/bin/env python3
"""The feedback people sent to BioManager's makers, newest first.

    python scripts/feedback.py               # the last 30 days
    python scripts/feedback.py --days 365    # further back
    python scripts/feedback.py --csv out.csv # also as a spreadsheet

A note arrives here when its sender leaves **Also send this to BioManager's
makers** ticked under Help → Send feedback (app/feedback.py). It comes as a
PostHog event, `feedback`, in the project the daily counts go to, and is
read with the personal API key scripts/installs.py uses
(~/.config/biomanager/posthog-personal-key or POSTHOG_PERSONAL_API_KEY).

Each note has its kind, text, the page it was sent from, the version, where
it runs, the language, the installation's id (the same id scripts/installs.py
lists, so you can see what that installation keeps) and, if the sender gave
one, a way to reach them. Never a name: to answer someone who left no
contact, reply on GitHub or in the release notes.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installs import AUTHOR_FILE, ids, matches, query  # noqa: E402

FIELDS = ("sent", "install", "kind", "version", "install_kind", "os", "language", "page", "contact", "text")


def notes(days: int) -> list[dict]:
    sql = f"""
select timestamp, distinct_id, properties.kind, properties.version, properties.install_kind, properties.os,
       properties.language, properties.page, properties.contact, properties.text
from events
where event = 'feedback' and timestamp > now() - interval {int(days)} day
order by timestamp desc
limit 1000
"""
    out = []
    for row in query(sql):
        note = dict(zip(FIELDS, row))
        note["sent"] = str(note["sent"])[:16].replace("T", " ")
        out.append(note)
    return out


def show(found: list[dict]) -> str:
    author = ids(AUTHOR_FILE)
    lines = []
    for n in found:
        whose = " (your own)" if matches(n["install"] or "", author) else ""
        lines += [
            f"{n['sent']}  {n['kind'] or '?'}  ·  {n['version']} {n['install_kind']} on {n['os']}  ·  "
            f"{n['language']}  ·  install {str(n['install'])[:8]}{whose}",
            *(f"    page: {n['page']}" for _ in [0] if n["page"]),
            *(f"    contact: {n['contact']}" for _ in [0] if n["contact"]),
            *("    " + line for line in str(n["text"] or "").splitlines()),
            "",
        ]
    return "\n".join(lines) if lines else "No feedback sent to the makers in that time."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30, help="how far back (default 30)")
    ap.add_argument("--csv", type=Path, help="also write the notes to this file")
    args = ap.parse_args()
    found = notes(args.days)
    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(found)
        print(f"{len(found)} notes written to {args.csv}")
    print(show(found))


if __name__ == "__main__":
    main()
