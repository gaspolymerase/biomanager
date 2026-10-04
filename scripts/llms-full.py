#!/usr/bin/env python3
"""Write site/llms-full.txt: the whole English user guide as one Markdown file.

    python scripts/llms-full.py            # writes site/llms-full.txt
    python scripts/llms-full.py --stdout   # prints it instead

AI assistants and AI search read Markdown more reliably than a styled page,
and one file lets them take in the whole guide at once (site/llms.txt links
it). It is made from each guide page's own words, between <!-- page --> and
<!-- /page -->, in the order of scripts/guide-pages.py's PAGES, followed by
site/deploy-with-ai.md. It is kept in the repository, because Cloudflare
Pages publishes site/ as it is: run this after editing the guide or
deploy-with-ai.md (tests/test_llms_full.py fails while it is out of date).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = SITE / "llms-full.txt"

_spec = importlib.util.spec_from_file_location("guide_pages", ROOT / "scripts" / "guide-pages.py")
guide_pages = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guide_pages)

# Left out: the app's clips and screenshots, a page's group label, and the
# guide home's cards (every page they link to follows anyway).
SKIP_TAGS = {"video", "img", "script", "style", "svg"}
SKIP_CLASSES = {"eyebrow", "doc-clip", "window-bar", "areas"}
BLOCKS = {"p", "div", "section", "h1", "h2", "h3", "h4", "ul", "ol", "dl", "table", "figure", "figcaption", "details", "summary"}
HEADING = {"h1": "## ", "h2": "### ", "h3": "#### ", "h4": "##### "}
VOID = {"br", "img", "hr", "source", "input", "meta", "link", "wbr"}


def final(link: str) -> str:
    """A link to one of the site's pages, as its address without .html (what
    the sitemap lists; Cloudflare Pages redirects .html there)."""
    u = urlsplit(link)
    if u.netloc != "biomanager.org" or not u.path.endswith(".html"):
        return link
    path = u.path[: -len(".html")]
    if path.endswith("/index"):
        path = path[: -len("index")]
    return urlunsplit((u.scheme, u.netloc, path, u.query, u.fragment))


class ToMarkdown(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.blocks: list[str] = []
        self.buf = ""
        self.stack: list[str] = []     # open tags, to match ends
        self.ends: list[str] = []      # what each open tag writes when it closes
        self.skip = 0                  # depth inside something left out
        self.hold = 0                  # inside a list item, term, cell…: blocks don't break the line
        self.lists: list[list] = []    # [kind, number] per open list
        self.href: list[str | None] = []
        self.link_text: list[str] = []
        self.row: list[str] | None = None
        self.rows: list[list[str]] | None = None
        self.header_rows = 0
        self.callout_title = False     # a note's bold title, ended with a space
        self.in_cell = False

    # -- output ---------------------------------------------------------
    def flush(self):
        text = re.sub(r"[ \t]+", " ", self.buf).strip()
        if text:
            self.blocks.append(text)
        self.buf = ""

    def write(self, s: str):
        if self.link_text:
            self.link_text[-1] += s
        elif self.in_cell and self.row:
            self.row[-1] += s
        else:
            self.buf += s

    # -- parsing ----------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if tag in VOID:
            if tag == "br" and not self.skip:
                self.write(" ")
            return
        self.stack.append(tag)
        self.ends.append("")
        if self.skip or tag in SKIP_TAGS or classes & SKIP_CLASSES:
            self.skip += 1
            return
        if tag in ("ul", "ol"):
            self.flush()
            self.lists.append([tag, 0])
        elif tag == "li":
            self.flush()
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            indent = "  " * (len(self.lists) - 1)
            self.buf = indent + ("- " if kind[0] == "ul" else f"{kind[1]}. ")
            self.hold += 1
        elif tag == "dt":
            self.flush()
            self.buf = "- **"
            self.hold += 1
        elif tag == "dd":
            self.buf = self.buf.rstrip()
            self.buf += ": " if not self.buf.endswith(":") else " "
            self.hold += 1
        elif tag == "table":
            self.flush()
            self.rows, self.header_rows = [], 0
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            if self.row is not None:
                self.row.append("")
                self.in_cell = True
                if tag == "th" and not self.rows:
                    self.header_rows = 1
        elif tag in HEADING:
            self.flush()
            self.buf = HEADING[tag]
        elif tag == "summary":
            self.flush()
            self.buf = "**"
            self.hold += 1
        elif tag == "figcaption":
            self.flush()
            self.buf = "*"
            self.hold += 1
        elif tag in BLOCKS:
            if not self.hold:
                self.flush()
            if "callout" in classes or "note" in classes:
                self.buf = "> "
                self.callout_title = True
            elif "badges" in classes:
                self.buf = "Who and where: "
            elif "sub" in classes:
                self.write(" ")
        elif tag == "span" and "sub" in classes:
            self.write(" ")
        elif tag == "span" and "min" in classes:   # a step's time on the first-day path
            self.write(" (")
            self.ends[-1] = ")"
        elif tag == "small":
            self.write(": ")
        elif tag == "span" and "badge" in classes:
            if not self.buf.endswith(": "):
                self.write(" · ")
        elif tag in ("b", "strong"):
            self.write("**")
        elif tag in ("i", "em"):
            self.write("*")
        elif tag in ("code", "kbd"):
            self.write("`")
        elif tag == "a":
            href = a.get("href")
            self.href.append(final(urljoin(self.base, href)) if href and not href.startswith("#") else
                             (self.base + href if href else None))
            self.link_text.append("")

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        # close up to the matching tag (HTML lets <p> and <li> close themselves)
        while self.stack and self.stack[-1] != tag:
            self.handle_endtag(self.stack[-1])
        if not self.stack:
            return
        self.stack.pop()
        end = self.ends.pop()
        if self.skip:
            self.skip -= 1
            return
        if end:
            self.write(end)
        if tag in ("ul", "ol"):
            self.flush()
            if self.lists:
                self.lists.pop()
        elif tag in ("li", "dd"):
            self.hold -= 1
            self.flush()
        elif tag == "dt":
            self.buf = self.buf.rstrip() + "**"
            self.hold -= 1
        elif tag in ("td", "th"):
            self.in_cell = False
        elif tag == "tr":
            if self.rows is not None and self.row is not None:
                self.rows.append([re.sub(r"\s+", " ", c).strip().replace("|", "\\|") for c in self.row])
            self.row = None
        elif tag == "table":
            self.flush()
            if self.rows:
                width = max(len(r) for r in self.rows)
                rows = [r + [""] * (width - len(r)) for r in self.rows]
                head = rows[0] if self.header_rows else [""] * width
                body = rows[1:] if self.header_rows else rows
                lines = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
                lines += ["| " + " | ".join(r) + " |" for r in body]
                self.blocks.append("\n".join(lines))
            self.rows = None
        elif tag in ("summary", "figcaption"):
            self.buf = self.buf.rstrip() + ("**" if tag == "summary" else "*")
            self.hold -= 1
            self.flush()
        elif tag in BLOCKS:
            self.callout_title = False
            if not self.hold:
                self.flush()
        elif tag in ("b", "strong"):
            self.write("** " if self.callout_title else "**")
            self.callout_title = False
        elif tag in ("i", "em"):
            self.write("*")
        elif tag in ("code", "kbd"):
            self.write("`")
        elif tag == "a" and self.link_text:
            text = re.sub(r"\s+", " ", self.link_text.pop()).strip()
            href = self.href.pop()
            self.write(f"[{text}]({href})" if href and text else text)

    def handle_data(self, data):
        if not self.skip:
            self.write(data.replace("\n", " "))

    def markdown(self) -> str:
        self.flush()
        return "\n\n".join(self.blocks)


def page_markdown(lang: str, slug: str) -> str:
    path = SITE / ("guide.html" if slug == "index" else f"guide/{slug}.html")
    content = path.read_text(encoding="utf-8")
    m = re.search(r"<!-- page -->([\s\S]*?)<!-- /page -->", content)
    if not m:
        raise SystemExit(f"{path}: no <!-- page --> … <!-- /page --> markers")
    parser = ToMarkdown(guide_pages.url(lang, slug))
    parser.feed(m.group(1))
    parser.close()
    body = parser.markdown()
    return f"{body}\n\nSource: {guide_pages.url(lang, slug)}"


def build() -> str:
    parts = [
        "# BioManager user guide\n\n"
        "> BioManager is a free, open-source lab management app for a biology lab's animal colonies "
        "(mice, zebrafish, flies, worms and any other organism), plasmids, samples, orders, reagents, "
        "antibodies and viruses, with a calendar and a lab notebook. Website: https://biomanager.org. "
        "This file is the whole English user guide (https://biomanager.org/guide) in one place, "
        "followed by the guide to deploying a lab server; the Chinese guide is at https://biomanager.org/zh/guide.",
    ]
    for _group, pages in guide_pages.PAGES:
        for slug, _en, _zh in pages:
            parts.append(page_markdown("en", slug))
    deploy = (SITE / "deploy-with-ai.md").read_text(encoding="utf-8").strip()
    parts.append("Source: https://biomanager.org/deploy-with-ai.md\n\n" + deploy)
    return "\n\n---\n\n".join(parts) + "\n"


def main() -> int:
    text = build()
    if "--stdout" in sys.argv:
        sys.stdout.write(text)
    else:
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}: {len(text.split())} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
