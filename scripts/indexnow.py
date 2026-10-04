#!/usr/bin/env python3
"""Tell search engines which website pages changed, through IndexNow.

    python scripts/indexnow.py site/guide.html site/zh/index.html   # these pages
    python scripts/indexnow.py --all                                # every page in the sitemap
    python scripts/indexnow.py --dry-run --all                      # print what would be sent

IndexNow (https://www.indexnow.org) passes the list to Bing, Yandex, Seznam,
Naver and Yep, and through Bing to ChatGPT search and Copilot, so a changed page
is crawled again within minutes instead of whenever they next look. The
website's publish workflow runs this after each deploy with the pages the push
changed. The key is public by design: site/<KEY>.txt holds it, which proves
the site is ours.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
HOST = "biomanager.org"
KEY = "1f444d98d1dd1499e7e92d2224727472"
ENDPOINT = "https://api.indexnow.org/indexnow"


def page_url(path: str) -> str | None:
    """site/guide/zebrafish.html -> https://biomanager.org/guide/zebrafish.html;
    anything that isn't a page (styles, images, clips, the search index) -> None."""
    parts = Path(path).parts       # paths as git gives them, from the repository's root
    if parts[:1] != ("site",) or len(parts) < 2:
        return None
    rel = Path(*parts[1:])
    if rel.suffix != ".html" or rel.parts[0] == "pagefind":
        return None
    tail = rel.as_posix()
    if rel.name == "index.html":
        tail = tail[: -len("index.html")]
    return f"https://{HOST}/{tail}"


def sitemap_urls() -> list[str]:
    import re
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", (SITE / "sitemap.xml").read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    args = [a for a in argv if not a.startswith("--")]
    urls = sitemap_urls() if "--all" in argv else sorted({u for a in args if (u := page_url(a))})
    if not urls:
        print("indexnow: no pages changed, nothing to send")
        return 0
    body = {"host": HOST, "key": KEY, "keyLocation": f"https://{HOST}/{KEY}.txt", "urlList": urls[:10000]}
    if dry:
        print(json.dumps(body, indent=2))
        return 0
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            # 200: received; 202: received, the key is still being checked (first use)
            print(f"indexnow: {r.status} for {len(urls)} page(s)")
            return 0
    except urllib.error.HTTPError as e:
        # 403: the key file isn't reachable; 422: a page isn't on this host; 429: too often
        print(f"indexnow: {e.code} {e.reason}: {e.read()[:300]!r}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
