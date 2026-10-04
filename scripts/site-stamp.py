#!/usr/bin/env python3
"""Stamp the website's shared files with their content, so browsers fetch a
new copy the moment one changes.

    python scripts/site-stamp.py            # stamp every page
    python scripts/site-stamp.py --check    # list what is out of date (tests use it)

A visitor's browser keeps style.css, site.js and assets/icons.svg for a while.
Without a stamp, a page that changed can arrive with yesterday's stylesheet
and fall apart (new buttons with no styles, icons the old sprite lacks). So
every page links them as style.css?v=<hash of its content>, and style.css
links the pictures it uses (assets/helix/) the same way: change a file, run
this, and every link to it changes too. scripts/guide-pages.py runs it after
writing the guide, and tests/test_site.py fails while anything is stale.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
SHARED = ("style.css", "site.js", "assets/icons.svg")
IN_CSS = re.compile(r'url\("(assets/helix/[\w.-]+\.svg)(?:\?v=[0-9a-f]+)?"\)')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def stamped_css() -> str:
    css = (SITE / "style.css").read_text(encoding="utf-8")
    return IN_CSS.sub(lambda m: f'url("{m.group(1)}?v={digest(SITE / m.group(1))}")', css)


def stamped_page(text: str, hashes: dict[str, str]) -> str:
    for name, h in hashes.items():
        # ../style.css, assets/icons.svg#star, site.js?v=old … → name?v=h
        text = re.sub(r'((?:href|src)="(?:\.\./)*)' + re.escape(name) + r'(?:\?v=[0-9a-f]+)?(?=[#"])',
                      lambda m: f"{m.group(1)}{name}?v={h}", text)
    return text


def run(check: bool) -> list[str]:
    stale = []
    css = stamped_css()
    if css != (SITE / "style.css").read_text(encoding="utf-8"):
        stale.append("style.css")
        if not check:
            (SITE / "style.css").write_text(css, encoding="utf-8")
    hashes = {name: digest(SITE / name) for name in SHARED}
    if check and stale:  # style.css's own hash is not final until it is stamped
        hashes["style.css"] = hashlib.sha256(css.encode("utf-8")).hexdigest()[:10]
    for page in sorted(SITE.rglob("*.html")):
        if "pagefind" in page.parts:
            continue
        text = page.read_text(encoding="utf-8")
        new = stamped_page(text, hashes)
        if new != text:
            stale.append(str(page.relative_to(SITE)))
            if not check:
                page.write_text(new, encoding="utf-8")
    return stale


def main() -> None:
    check = "--check" in sys.argv[1:]
    stale = run(check)
    if check:
        print("\n".join(stale) if stale else "every page is stamped")
        sys.exit(1 if stale else 0)
    print(f"stamped {len(stale)} file(s)")


if __name__ == "__main__":
    main()
