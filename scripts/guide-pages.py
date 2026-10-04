#!/usr/bin/env python3
"""Keep the user guide's pages in step: one list of pages, one look.

    python scripts/guide-pages.py            # rebuild every guide page's shared parts
    python scripts/guide-pages.py --search   # and rebuild the search index (needs npx)

The guide is site/guide.html (its home, which the app opens) and one page per
topic in site/guide/, each with its Chinese twin under site/zh/. A page's own
words sit between <!-- page --> and <!-- /page -->, and are edited by hand.
Everything around them (the head, the header, the sidebar in its groups, "On
this page", Previous / Next and "Was this helpful?") is written by this
script from PAGES below, so adding a page is: add it to PAGES, make its file
with the two markers, and run this.

The search box uses Pagefind (https://pagefind.app): --search indexes the
built site into site/pagefind/, which is published with it.
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

# (group, [(slug, English title, Chinese title)]), in reading order. The home
# page is guide.html itself; every other page is guide/<slug>.html.
PAGES = [
    (("Get started", "入门"), [
        ("index", "User guide", "用户指南"),
        ("what-it-is", "What BioManager is", "BioManager 是什么"),
        ("coming-from-excel", "Coming from Excel", "从 Excel 过来"),
        ("install", "Install and set up", "安装与设置"),
        ("bring-your-lab-in", "Bring your lab in", "把实验室搬进来"),
        ("home-page", "Home", "首页"),
    ]),
    (("Everyday skills", "日常操作"), [
        ("sheets", "Working in a sheet", "在表格里工作"),
        ("many-at-once", "Many at once", "批量操作"),
        ("finding-things", "Finding things", "查找"),
        ("undo-and-history", "Undo and history", "撤销与历史"),
        ("notifications", "Notifications", "通知"),
        ("keyboard-shortcuts", "Keyboard shortcuts", "快捷键"),
    ]),
    (("How to…", "怎么做"), [
        ("record-a-litter", "Record a litter", "记录产仔"),
        ("genotype-a-litter", "Genotype a litter", "鉴定一窝的基因型"),
        ("wean-a-litter", "Wean a litter", "断奶分笼"),
        ("print-cage-cards", "Print cage cards", "打印笼卡"),
        ("import-from-excel", "Import from Excel", "从 Excel 导入"),
        ("receive-an-order", "Receive an order into stock", "订单到货入库"),
        ("book-equipment", "Book equipment", "预约仪器"),
        ("sign-a-notebook-page", "Sign a notebook page", "签名实验记录"),
        ("invite-someone", "Invite someone to the lab", "邀请成员"),
        ("restore-a-backup", "Restore a backup", "从备份恢复"),
    ]),
    (("Your databases", "数据库"), [
        ("mouse-colony", "Mouse colony", "小鼠鼠群"),
        ("zebrafish", "Zebrafish", "斑马鱼"),
        ("flies-and-worms", "Flies and worms", "果蝇和线虫"),
        ("any-organism", "Any other organism", "其他任意物种"),
        ("experiments", "Experiments", "实验"),
        ("plasmids", "Plasmids", "质粒"),
        ("samples-and-orders", "Samples, orders, reagents, antibodies, viruses", "样本、采购、试剂、抗体、病毒"),
        ("calendar", "Calendar", "日历"),
        ("notebook", "Notebook and utilities", "实验记录本与工具"),
    ]),
    (("Working as a lab", "实验室协作"), [
        ("who-can-do-what", "Who can do what", "谁能做什么"),
        ("project-groups", "Project groups", "项目组"),
        ("people-and-guests", "People and guests", "成员与访客"),
        ("lab-setup", "Lab setup", "实验室设置"),
        ("phones-and-cage-cards", "Phones and cage cards", "手机与笼卡"),
    ]),
    (("Data and help", "数据与帮助"), [
        ("your-data", "Your data and backups", "数据与备份"),
        ("scripts-and-api", "Scripts, instruments and other tools", "脚本、仪器和其他工具"),
        ("troubleshooting", "Troubleshooting", "故障排除"),
        ("feedback", "Feedback", "反馈"),
    ]),
]

T = {
    "en": dict(lang="en", nav_label="Site", features="Features", server="Run it for your lab", guide="User guide",
               download="Download", other="中文", other_code="zh-CN", other_data="zh", search="Search the guide",
               contents="Contents", on_page="On this page", prev="Previous", next="Next", helpful="Was this page helpful?",
               yes="Yes", no="No", suffix="BioManager user guide"),
    "zh": dict(lang="zh-CN", nav_label="页面导航", features="功能", server="为实验室部署", guide="用户指南",
               download="下载", other="English", other_code="en", other_data="en", search="搜索用户指南",
               contents="目录", on_page="本页内容", prev="上一页", next="下一页", helpful="这一页对你有帮助吗？",
               yes="有", no="没有", suffix="BioManager 用户指南"),
}


def flat():
    return [p for _, pages in PAGES for p in pages]


def path_of(lang: str, slug: str) -> Path:
    base = SITE if lang == "en" else SITE / "zh"
    return base / "guide.html" if slug == "index" else base / "guide" / f"{slug}.html"


def href(from_slug: str, to_slug: str) -> str:
    """A link from one guide page to another, in the same language."""
    if from_slug == "index":
        return "guide.html" if to_slug == "index" else f"guide/{to_slug}.html"
    return "../guide.html" if to_slug == "index" else f"{to_slug}.html"


def up(lang: str, slug: str) -> str:
    """From this page to the site's root folder (where style.css lives)."""
    depth = (0 if lang == "en" else 1) + (0 if slug == "index" else 1)
    return "../" * depth


def url(lang: str, slug: str) -> str:
    rel = "guide.html" if slug == "index" else f"guide/{slug}.html"
    return "https://biomanager.org/" + ("" if lang == "en" else "zh/") + rel


def title_of(lang, slug):
    for s, en, zh in flat():
        if s == slug:
            return en if lang == "en" else zh
    raise KeyError(slug)


def text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def head(lang, slug, summary):
    t = T[lang]
    u = up(lang, slug)
    title = t["guide"] if slug == "index" else title_of(lang, slug)
    full = f"{title} — BioManager" if slug == "index" else f"{title} — {t['suffix']}"
    redirect = ""
    if lang == "en":
        # Someone whose browser asks for Chinese first, and who hasn't picked a
        # language here, gets the Chinese page (as on the rest of the site).
        target = "zh/guide.html" if slug == "index" else f"../zh/guide/{slug}.html"
        redirect = ("  <script>try{if(!localStorage.getItem('bm-lang')&&/^zh/i.test((navigator.languages&&navigator.languages[0])"
                    f"||navigator.language||''))location.replace('{target}'+location.hash)}}catch(e){{}}</script>\n")
    zh_meta = '  <meta http-equiv="Content-Language" content="zh-CN">\n' if lang == "zh" else ""
    return f"""<!doctype html>
<html lang="{t['lang']}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
{zh_meta}  <title>{html.escape(full)}</title>
  <meta name="description" content="{html.escape(summary)}">
  <meta name="color-scheme" content="light dark">
  <meta name="theme-color" content="#fbfdfc" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#0f1413" media="(prefers-color-scheme: dark)">
  <link rel="canonical" href="{url(lang, slug)}">
  <link rel="alternate" hreflang="en" href="{url('en', slug)}">
  <link rel="alternate" hreflang="zh-CN" href="{url('zh', slug)}">
  <link rel="alternate" hreflang="x-default" href="{url('en', slug)}">
{redirect}  <meta property="og:type" content="article">
  <meta property="og:site_name" content="BioManager">
  <meta property="og:url" content="{url(lang, slug)}">
  <meta property="og:title" content="{html.escape(full)}">
  <meta property="og:description" content="{html.escape(summary)}">
  <meta property="og:image" content="https://biomanager.org/assets/social-card.png">
  <meta name="twitter:card" content="summary_large_image">
  <link rel="icon" href="{u}assets/icon.svg" type="image/svg+xml">
  <link rel="icon" href="{u}assets/icon-192.png" sizes="192x192" type="image/png">
  <link rel="apple-touch-icon" href="{u}assets/apple-touch-icon.png">
  <link rel="preload" href="{u}assets/fonts/Geist-latin.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="{u}style.css">
</head>"""


def header(lang, slug):
    t = T[lang]
    u = up(lang, slug)
    other = (("zh/" if slug == "index" else "../zh/guide/") if lang == "en" else ("../" if slug == "index" else "../../guide/"))
    other += "guide.html" if slug == "index" else f"{slug}.html"
    return f"""<header class="nav">
  <div class="wrap nav-inner">
    <a class="brand" href="{u or './'}"><img src="{u}assets/icon.svg" alt="" width="28" height="28">BioManager</a>
    <nav class="nav-links" aria-label="{t['nav_label']}">
      <a href="{u}features.html">{t['features']}</a>
      <a href="{u}server.html">{t['server']}</a>
      <a class="nav-guide" href="{u}guide.html" aria-current="page">{t['guide']}</a>
    </nav>
    <a class="lang-link" href="{other}" hreflang="{t['other_code']}" lang="{t['other_code']}" data-lang="{t['other_data']}">{t['other']}</a>
    <a class="btn btn-small btn-primary" href="{u}download.html">{t['download']}</a>
  </div>
</header>"""


def sidebar(lang, slug):
    t = T[lang]
    groups = []
    for (en, zh), pages in PAGES:
        items = []
        for s, ten, tzh in pages:
            name = ten if lang == "en" else tzh
            if s == "index":
                name = "Guide home" if lang == "en" else "指南首页"
            cur = ' aria-current="page"' if s == slug else ""
            items.append(f'          <li><a href="{href(slug, s)}"{cur}>{html.escape(name)}</a></li>')
        groups.append(f'        <li class="docs-group"><span>{en if lang == "en" else zh}</span>\n          <ol>\n'
                      + "\n".join("  " + i for i in items) + "\n          </ol></li>")
    return f"""<aside class="docs-nav" aria-label="{t['contents']}">
    <button type="button" class="docs-search" data-search data-pagefind="{up(lang, slug)}pagefind/">
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <span>{t['search']}</span><kbd>/</kbd>
    </button>
    <details class="docs-toc" open>
      <summary>{t['contents']}</summary>
      <ol>
{chr(10).join(groups)}
      </ol>
    </details>
  </aside>"""


def on_page(lang, content):
    heads = re.findall(r'<h([23]) id="([^"]+)"[^>]*>([\s\S]*?)</h\1>', content)
    if len(heads) < 2:
        return ""
    items = "\n".join(f'        <li class="lvl{lvl}"><a href="#{i}">{html.escape(text(h))}</a></li>' for lvl, i, h in heads)
    return f"""<nav class="docs-rail" aria-label="{T[lang]['on_page']}">
      <p>{T[lang]['on_page']}</p>
      <ol>
{items}
      </ol>
    </nav>"""


def pager(lang, slug):
    t = T[lang]
    order = [s for s, _, _ in flat()]
    i = order.index(slug)
    parts = []
    if i > 0:
        p = order[i - 1]
        parts.append(f'<a class="prev" href="{href(slug, p)}"><small>{t["prev"]}</small>{html.escape(title_of(lang, p))}</a>')
    if i < len(order) - 1:
        n = order[i + 1]
        parts.append(f'<a class="next" href="{href(slug, n)}"><small>{t["next"]}</small>{html.escape(title_of(lang, n))}</a>')
    issue = ("https://github.com/gaspolymerase/biomanager/issues/new?title="
             + ("Guide: " if lang == "en" else "用户指南：") + html.escape(title_of(lang, slug)).replace(" ", "%20")
             + "&amp;body=" + ("Page: " if lang == "en" else "页面：") + url(lang, slug)
             + "%0A%0A" + ("What was unclear or missing:" if lang == "en" else "哪里不清楚或缺了什么：").replace(" ", "%20"))
    return f"""<nav class="pager" aria-label="{t['prev']} / {t['next']}">{''.join(parts)}</nav>
      <div class="helpful" data-pagefind-ignore>
        <span>{t['helpful']}</span>
        <button type="button" data-helpful="yes" aria-pressed="false">{t['yes']}</button>
        <a class="btn btn-small" data-helpful="no" href="{issue}" target="_blank" rel="noopener">{t['no']}</a>
      </div>"""


def footer(lang, slug):
    u = up(lang, slug)
    repo = "https://github.com/gaspolymerase/biomanager"
    if lang == "en":
        invite = ("BioManager is free and open source. If it helps your lab, a star on GitHub helps other labs "
                  "find it; an issue there tells us what to fix or build next.")
        star, feedback = "Star on GitHub", "Send feedback"
        body = f'<p><a href="{u}download.html">Download</a> · <a href="{repo}/releases">All releases and what changed</a></p>'
    else:
        invite = ("BioManager 免费开源。如果它帮到了你的实验室，欢迎在 GitHub 上点个星标，让更多实验室找到它；"
                  "遇到问题或有想法，也欢迎在 GitHub 上提给我们。")
        star, feedback = "在 GitHub 上标星", "提交反馈"
        body = f'<p><a href="{u}download.html">下载</a> · <a href="{repo}/releases">所有版本和更新内容</a></p>'
    return f"""<footer class="footer">
  <div class="wrap footer-inner">
    <div class="footer-gh">
      <p>{invite}</p>
      <a class="btn btn-quiet" href="{repo}"><svg class="star" width="16" height="16" aria-hidden="true"><use href="{u}assets/icons.svg#star"/></svg>{star}</a>
      <a class="btn btn-quiet" href="{repo}/issues/new/choose"><svg width="16" height="16" aria-hidden="true"><use href="{u}assets/icons.svg#github"/></svg>{feedback}</a>
    </div>
    <div class="brand"><img src="{u}assets/icon.svg" alt="" width="22" height="22">BioManager</div>
    {body}
  </div>
</footer>
<script src="{u}site.js"></script>"""


def build(lang, slug):
    p = path_of(lang, slug)
    old = p.read_text(encoding="utf-8")
    m = re.search(r"<!-- page -->\n([\s\S]*?)\n\s*<!-- /page -->", old)
    if not m:
        sys.exit(f"{p}: no <!-- page --> … <!-- /page --> block")
    content = m.group(1)
    sm = re.search(r'<p class="summary">([\s\S]*?)</p>', content)
    summary = text(sm.group(1)) if sm else title_of(lang, slug)
    keep = ""
    km = re.search(r"<!-- keep -->\n([\s\S]*?)<!-- /keep -->\n", old)   # page-specific scripts, kept as they are
    if km:
        keep = km.group(0)
    out = f"""{head(lang, slug, summary)}
<body class="docs-page">

{header(lang, slug)}

<div class="wrap docs">
  {sidebar(lang, slug)}
  <main class="docs-main" data-pagefind-body>
    <!-- page -->
{content}
    <!-- /page -->
      {pager(lang, slug)}
  </main>
  {on_page(lang, content)}
</div>

{footer(lang, slug)}
{keep}</body>
</html>
"""
    p.write_text(out, encoding="utf-8")


def main():
    for lang in ("en", "zh"):
        for slug, _, _ in flat():
            build(lang, slug)
    print(f"{2 * len(flat())} guide pages written")
    # the pages link style.css, site.js and the icons by their content
    subprocess.run([sys.executable, str(Path(__file__).with_name("site-stamp.py"))], check=True)
    if "--search" in sys.argv:
        subprocess.run(["npx", "--yes", "pagefind@1", "--site", str(SITE)], check=True)


if __name__ == "__main__":
    main()
