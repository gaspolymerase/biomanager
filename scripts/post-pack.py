#!/usr/bin/env python3
"""Put together one day's launch post, ready to paste, for every platform.

    python scripts/post-pack.py                 # today's post (by the date in Beijing, where it goes out at 08:30)
    python scripts/post-pack.py 2026-10-09      # a given date
    python scripts/post-pack.py --day 3         # by day number
    python scripts/post-pack.py --all           # every day, e.g. to schedule a week ahead
    python scripts/post-pack.py --check         # lengths and placeholders only

Reads promo/posts.json, fills in the release version and each platform's
link (tagged with utm_source so the website can tell where visits came
from), and writes promo/out/packs/<date>-day<N>/:

    post.md               every platform's text, with a checklist
    x.mp4, linkedin.mp4, facebook.mp4    16:9, English (the day's own video if made)
    bilibili.mp4          16:9, Chinese (the day's own video if made)
    xhs.mp4, xhs-cover.png               3:4, Chinese title (the day's own video if made)
    clip.gif              for GitHub and the README

The media come from scripts/feature-clips.py (promo/out/<clip>/); a pack
whose clip isn't recorded yet says so, and --record records it first.
It never posts anything: that is done by a person, or by each platform's
own scheduler.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "promo/posts.json"
OUT = ROOT / "promo/out"

MEDIA = {  # pack file: clip file
    "x.mp4": "landscape-en.mp4", "linkedin.mp4": "landscape-en.mp4", "facebook.mp4": "landscape-en.mp4",
    "bilibili.mp4": "landscape-zh.mp4", "xhs.mp4": "portrait-zh.mp4", "xhs-cover.png": "cover-portrait.png",
    "bilibili-cover.png": "cover-zh.png", "clip.gif": "clip.gif",
}


def version() -> str:
    try:
        tag = subprocess.run(["gh", "release", "view", "--repo", "gaspolymerase/biomanager",
                              "--json", "tagName", "-q", ".tagName"], capture_output=True, text=True, timeout=20)
        if tag.returncode == 0 and tag.stdout.strip():
            ver = tag.stdout.strip().lstrip("v")
            return ver[:-2] if ver.count(".") == 2 and ver.endswith(".0") else ver   # 1.0.0 reads as 1.0
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "(version)"


def tagged(url: str, source: str, day: int) -> str:
    if not url or "biomanager.org" not in url:
        return url
    return f"{url}?{urlencode({'utm_source': source, 'utm_medium': 'social', 'utm_campaign': f'launch-day{day}'})}"


def fill(text: str, links: dict, source: str, day: int, ver: str) -> str:
    return (text.replace("{version}", ver)
                .replace("{site}", tagged(links["site"], source, day))
                .replace("{guide}", tagged(links["guide"], source, day))
                .replace("{github}", links["github"])
                .replace("{download}", links["download"])
                .replace("{arxiv}", links.get("arxiv") or "{arxiv}"))


def x_length(text: str) -> int:
    """X counts every link as 23 characters, and CJK characters as two."""
    n = 0
    for word in text.replace("\n", " \n ").split(" "):
        if word.startswith("http"):
            n += 23
        else:
            n += sum(2 if ord(c) > 0x2E80 else 1 for c in word)
        n += 1
    return n - 1


def problems(post: dict, links: dict, ver: str) -> list[str]:
    out = []
    x = fill(post["x"], links, "x", post["day"], ver)
    if x_length(x) > 280:
        out.append(f"X post is {x_length(x)} characters (limit 280)")
    for i, reply in enumerate(post.get("x_thread", []), 2):
        if x_length(fill(reply, links, "x", post["day"], ver)) > 280:
            out.append(f"X thread post {i} is over 280 characters")
    if len(post["xhs_title"]) > 20:
        out.append(f"Xiaohongshu title is {len(post['xhs_title'])} characters (limit 20)")
    if len(post["xhs_body"]) > 1000:
        out.append(f"Xiaohongshu text is {len(post['xhs_body'])} characters (limit 1000)")
    if len(post["bili_title"].replace("{version}", ver)) > 80:
        out.append(f"Bilibili title is {len(post['bili_title'])} characters (limit 80)")
    if len(fill(post["linkedin"], links, "linkedin", post["day"], ver)) > 3000:
        out.append("LinkedIn text is over 3000 characters")
    for field in ("x", "linkedin", "xhs_body", "bili_desc"):
        if "{arxiv}" in post[field] and not links.get("arxiv"):
            out.append("needs the arXiv link: add \"arxiv\" under links in promo/posts.json")
            break
    return out


def pack(post: dict, data: dict, when: date, ver: str, record: bool) -> Path:
    links, day = data["links"], post["day"]
    folder = OUT / "packs" / f"{when.isoformat()}-day{day}"
    folder.mkdir(parents=True, exist_ok=True)
    clip = post.get("clip") or ""
    source = OUT / clip if clip else None
    if clip and record and not (source / "landscape-en.mp4").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts/feature-clips.py"), str(OUT), clip], check=False)
    missing = []
    for name, src in MEDIA.items():
        if not clip:
            break
        if (source / src).exists():
            shutil.copy2(source / src, folder / name)
        else:
            missing.append(name)
    # The day's own video (scripts/daily-video.py), where it has been made,
    # instead of the plain clip: English for X, LinkedIn and Facebook, Chinese
    # and its cover for Bilibili, and its 3:4 version (scripts/xhs-video.py)
    # for Xiaohongshu.
    daily = OUT / "daily"
    for name, src in (("x.mp4", "en.mp4"), ("linkedin.mp4", "en.mp4"), ("facebook.mp4", "en.mp4"),
                      ("bilibili.mp4", "zh.mp4"), ("bilibili-cover.png", "cover-zh.png"), ("xhs.mp4", "xhs.mp4")):
        made = daily / f"day{day:02d}-{src}"
        if made.exists():
            shutil.copy2(made, folder / name)
            if name in missing:
                missing.remove(name)
    tags_en, tags_zh = " ".join(data["tags_en"]), " ".join(data["tags_zh"])
    notes = problems(post, links, ver)
    li = fill(post["linkedin"], links, "linkedin", day, ver)
    fb = fill(post["linkedin"], links, "facebook", day, ver)
    md = [f"# Day {day} · {when:%a %d %b %Y} · {post['theme']}", "",
          f"Post at {data['post_at']}.", ""]
    if notes:
        md += ["**Fix before posting:**", *[f"- {n}" for n in notes], ""]
    if missing:
        md += [f"**Not recorded yet:** {', '.join(missing)}. Run `python scripts/feature-clips.py promo/out {clip}`.", ""]
    if not clip:
        md += ["No clip for this day: use a cover image or a screenshot.", ""]
    md += [
        "## Checklist", "",
        "- [ ] X: text + x.mp4",
        "- [ ] LinkedIn: text + linkedin.mp4",
        "- [ ] Facebook: text + facebook.mp4",
        "- [ ] Bilibili: title, description, tags + bilibili.mp4 (cover: bilibili-cover.png)",
        "- [ ] Xiaohongshu: title, text + xhs.mp4 (cover: xhs-cover.png). The address is in the text; it isn't clickable there.",
        "- [ ] Reply to comments from yesterday's posts", "",
        "## X", "", "```", fill(post["x"], links, "x", day, ver), "```",
        f"{x_length(fill(post['x'], links, 'x', day, ver))}/280", "",
        *[line for i, reply in enumerate(post.get("x_thread", []), 2) for line in (
            f"Reply {i} (post it as a reply to the one above, once that is out; X can't schedule a thread):",
            "```", fill(reply, links, "x", day, ver), "```", f"{x_length(fill(reply, links, 'x', day, ver))}/280", "")],
        "## LinkedIn", "", "```", li + "\n\n" + tags_en, "```", "",
        "## Facebook", "", "```", fb, "```", "",
        "## Bilibili", "", "Title:", "```", fill(post["bili_title"], links, "bilibili", day, ver), "```",
        "Description:", "```", fill(post["bili_desc"], links, "bilibili", day, ver), "```",
        "Tags: " + ", ".join(t.lstrip("#") for t in data["tags_zh"]) + ", BioManager", "",
        "## Xiaohongshu", "", "Title:", "```", post["xhs_title"], "```",
        "Text:", "```", fill(post["xhs_body"], links, "xhs", day, ver).replace("{site}", "") + "\n\n" + tags_zh, "```", "",
    ]
    (folder / "post.md").write_text("\n".join(md))
    return folder


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD (default today in Beijing)")
    ap.add_argument("--day", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--record", action="store_true", help="record the clip first if it is missing")
    args = ap.parse_args()
    data = json.loads(POSTS.read_text())
    start = date.fromisoformat(data["start"])
    ver = version()
    posts = data["posts"]
    if args.check:
        bad = 0
        for p in posts:
            for n in problems(p, data["links"], ver):
                print(f"day {p['day']}: {n}")
                bad += 1
        print("All posts fit." if not bad else f"{bad} to fix.")
        return
    if args.all:
        chosen = posts
    elif args.day is not None:
        chosen = [p for p in posts if p["day"] == args.day]
    else:
        when = date.fromisoformat(args.date) if args.date else datetime.now(ZoneInfo("Asia/Shanghai")).date()
        chosen = [p for p in posts if start + timedelta(days=p["day"]) == when]
        if not chosen:
            sys.exit(f"No post on {when} (the launch runs {start} to {start + timedelta(days=posts[-1]['day'])}).")
    for p in chosen:
        print(pack(p, data, start + timedelta(days=p["day"]), ver, args.record))


if __name__ == "__main__":
    main()
