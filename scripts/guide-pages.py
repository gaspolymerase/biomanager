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
import shutil
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
        ("try-it-first", "Try it first", "先试一遍"),
        ("bring-your-lab-in", "Bring your lab in", "把实验室搬进来"),
        ("import-from-excel", "Import from Excel", "从 Excel 导入"),
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
    (("Your mouse colony", "小鼠鼠群"), [
        ("mouse-colony", "Mouse colony", "小鼠鼠群"),
        ("record-a-litter", "Record a litter", "记录产仔"),
        ("genotype-a-litter", "Genotype a litter", "鉴定一窝的基因型"),
        ("wean-a-litter", "Wean a litter", "断奶分笼"),
        ("print-cage-cards", "Print cage cards", "打印笼卡"),
        ("phones-and-cage-cards", "At the bench: phones and cage cards", "在实验台：手机与笼卡"),
    ]),
    (("Fish, flies, plants and more", "鱼、果蝇、植物及其他"), [
        ("zebrafish", "Zebrafish", "斑马鱼"),
        ("flies-and-worms", "Flies and worms", "果蝇和线虫"),
        ("plants", "Plants and field crops", "植物与大田作物"),
        ("any-organism", "Any other organism", "其他任意物种"),
    ]),
    (("Molecular biology", "分子生物学"), [
        ("plasmids", "Plasmids, primers and stocks", "质粒、引物与菌种"),
    ]),
    (("Samples and ordering", "样本与采购"), [
        ("samples-and-orders", "Samples, reagents, antibodies, viruses", "样本、试剂、抗体、病毒"),
        ("receive-an-order", "Receive an order into stock", "订单到货入库"),
    ]),
    (("Experiments and the notebook", "实验与记录本"), [
        ("experiments", "Experiments", "实验"),
        ("notebook", "Notebook and utilities", "实验记录本与工具"),
        ("sign-a-notebook-page", "Sign a notebook page", "签名实验记录"),
    ]),
    (("Planning the week", "日程安排"), [
        ("calendar", "Calendar", "日历"),
        ("book-equipment", "Book equipment", "预约仪器"),
    ]),
    (("Working as a lab", "实验室协作"), [
        ("who-can-do-what", "Who can do what", "谁能做什么"),
        ("project-groups", "Project groups", "项目组"),
        ("people-and-guests", "People and guests", "成员与访客"),
        ("invite-someone", "Invite someone to the lab", "邀请成员"),
        ("lab-setup", "Lab setup", "实验室设置"),
    ]),
    (("Your data", "你的数据"), [
        ("your-data", "Your data and backups", "数据与备份"),
        ("restore-a-backup", "Restore a backup", "从备份恢复"),
        ("scripts-and-api", "Scripts and the API", "脚本与 API"),
    ]),
    (("AI assistants", "AI 助手"), [
        ("ai-assistants", "AI assistants", "AI 助手"),
        ("connect-an-ai-assistant", "Connect an AI assistant", "连接 AI 助手"),
    ]),
    (("Help", "帮助"), [
        ("troubleshooting", "Troubleshooting", "疑难解答"),
        ("feedback", "Feedback", "反馈"),
    ]),
]

T = {
    "en": dict(lang="en", nav_label="Site", features="Features", server="Run it for your lab", guide="User guide",
               download="Download", github="BioManager on GitHub", other="中文", other_code="zh-CN", other_data="zh", search="Search the guide",
               contents="Contents", on_page="On this page", prev="Previous", next="Next", helpful="Was this page helpful?",
               yes="Yes", no="No", minutes="{n} min read", suffix="BioManager user guide"),
    "zh": dict(lang="zh-CN", nav_label="页面导航", features="功能", server="为实验室部署", guide="用户指南",
               download="下载", github="BioManager 的 GitHub 仓库", other="English", other_code="en", other_data="en", search="搜索用户指南",
               contents="目录", on_page="本页内容", prev="上一页", next="下一页", helpful="这一页对你有帮助吗？",
               yes="有", no="没有", minutes="阅读约 {n} 分钟", suffix="BioManager 用户指南"),
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
    """A page's address as search engines should list it: without .html, which
    Cloudflare Pages redirects away from (GitHub Pages serves both)."""
    rel = "guide" if slug == "index" else f"guide/{slug}"
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


def home(slug: str) -> str:
    """From this page to its language's own pages (features.html, download.html …)."""
    return "" if slug == "index" else "../"


REPO = "https://github.com/gaspolymerase/biomanager"


def header(lang, slug):
    t = T[lang]
    u, h = up(lang, slug), home(slug)
    other = (("zh/" if slug == "index" else "../zh/guide/") if lang == "en" else ("../" if slug == "index" else "../../guide/"))
    other += "guide.html" if slug == "index" else f"{slug}.html"
    return f"""<header class="nav">
  <div class="wrap nav-inner">
    <a class="brand" href="{h or './'}"><img src="{u}assets/icon.svg" alt="" width="28" height="28">BioManager</a>
    <nav class="nav-links" aria-label="{t['nav_label']}">
      <a href="{h}features.html">{t['features']}</a>
      <a href="{h}server.html">{t['server']}</a>
      <a href="{h}guide.html" aria-current="page">{t['guide']}</a>
    </nav>
    <a class="nav-gh" href="{REPO}" aria-label="{t['github']}"><svg aria-hidden="true"><use href="{u}assets/icons.svg#github"/></svg><span>GitHub</span></a>
    <a class="lang-link" href="{other}" hreflang="{t['other_code']}" lang="{t['other_code']}" data-lang="{t['other_data']}">{t['other']}</a>
    <a class="btn btn-small btn-primary" href="{h}download.html">{t['download']}</a>
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


def reading_time(lang, content):
    """Minutes, at 200 English words or 400 Chinese characters a minute."""
    plain = re.sub(r"<[^>]+>", " ", content)
    units = len(re.findall(r"[\u4e00-\u9fff]", plain)) / 400 if lang == "zh" else len(plain.split()) / 200
    return max(1, round(units))


def on_page(lang, content):
    heads = re.findall(r'<h([23]) id="([^"]+)"[^>]*>([\s\S]*?)</h\1>', content)
    if len(heads) < 2:
        return ""
    items = "\n".join(f'        <li class="lvl{lvl}"><a href="#{i}">{html.escape(text(h))}</a></li>' for lvl, i, h in heads)
    return f"""<nav class="docs-rail" aria-label="{T[lang]['on_page']}">
      <p>{T[lang]['on_page']} · {T[lang]['minutes'].format(n=reading_time(lang, content))}</p>
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


FOOT = {
    "en": dict(
        invite=("BioManager is free and open source. If it helps your lab, a star on GitHub helps other labs "
                "find it; an issue there tells us what to fix or build next."),
        star="Star on GitHub", feedback="Send feedback", label="Site map",
        product="Product", learn="Learn", project="Project",
        features="Features", download="Download", server="Run it for your lab", releases="All releases",
        guide="User guide", try_it="Try it first", excel="Coming from Excel", trouble="Troubleshooting",
        source="Source on GitHub", licence="MIT licence", privacy="Privacy", terms="Terms",
        legal=('Released under the <a href="{repo}/blob/master/LICENSE">MIT licence</a>. Icons from '
               '<a href="https://lucide.dev">Lucide</a> (ISC licence), with the lab\'s own drawn for BioManager. Text in '
               '<a href="https://vercel.com/font">Geist</a> (SIL Open Font Licence). '
               'The people and records in the screenshots and clips are made up.')),
    "zh": dict(
        invite=("BioManager 免费开源。如果它帮到了你的实验室，欢迎在 GitHub 上点个星标，让更多实验室找到它；"
                "遇到问题或有想法，也欢迎在 GitHub 上提给我们。"),
        star="在 GitHub 上标星", feedback="提交反馈", label="网站地图",
        product="产品", learn="上手", project="项目",
        features="功能", download="下载", server="为实验室部署", releases="所有版本",
        guide="用户指南", try_it="先试一遍", excel="从 Excel 过来", trouble="常见问题排查",
        source="GitHub 源代码", licence="MIT 许可证", privacy="隐私", terms="使用条款",
        legal=('以 <a href="{repo}/blob/master/LICENSE">MIT 许可证</a>发布。图标来自 '
               '<a href="https://lucide.dev">Lucide</a>（ISC 许可证），实验室用的图标为 BioManager 自绘。文字字体为 '
               '<a href="https://vercel.com/font">Geist</a>（SIL 开源字体许可证）。'
               '截图和视频中的人员和记录均为虚构。')),
}


def footer_html(lang: str, u: str, h: str) -> str:
    """The foot of every page on the site, the hand-written ones too (they
    carry a copy of it): `u` leads to the site's root folder, `h` to this
    language's own pages."""
    f = FOOT[lang]
    return f"""<footer class="footer">
  <div class="wrap">
    <div class="footer-top">
      <div class="footer-about">
        <div class="brand"><img src="{u}assets/icon.svg" alt="" width="22" height="22">BioManager</div>
        <p>{f['invite']}</p>
        <div class="footer-btns">
          <a class="btn btn-quiet" href="{REPO}"><svg class="star" width="16" height="16" aria-hidden="true"><use href="{u}assets/icons.svg#star"/></svg>{f['star']}</a>
          <a class="btn btn-quiet" href="{REPO}/issues/new/choose"><svg width="16" height="16" aria-hidden="true"><use href="{u}assets/icons.svg#github"/></svg>{f['feedback']}</a>
        </div>
      </div>
      <nav class="footer-cols" aria-label="{f['label']}">
        <div>
          <p class="footer-h">{f['product']}</p>
          <ul>
            <li><a href="{h}features.html">{f['features']}</a></li>
            <li><a href="{h}download.html">{f['download']}</a></li>
            <li><a href="{h}server.html">{f['server']}</a></li>
            <li><a href="{REPO}/releases">{f['releases']}</a></li>
          </ul>
        </div>
        <div>
          <p class="footer-h">{f['learn']}</p>
          <ul>
            <li><a href="{h}guide.html">{f['guide']}</a></li>
            <li><a href="{h}guide/try-it-first.html">{f['try_it']}</a></li>
            <li><a href="{h}guide/coming-from-excel.html">{f['excel']}</a></li>
            <li><a href="{h}guide/troubleshooting.html">{f['trouble']}</a></li>
          </ul>
        </div>
        <div>
          <p class="footer-h">{f['project']}</p>
          <ul>
            <li><a href="{REPO}">{f['source']}</a></li>
            <li><a href="{REPO}/blob/master/LICENSE">{f['licence']}</a></li>
            <li><a href="{h}privacy.html">{f['privacy']}</a></li>
            <li><a href="{h}terms.html">{f['terms']}</a></li>
          </ul>
        </div>
      </nav>
    </div>
    <p class="footer-legal">{f['legal'].format(repo=REPO)}</p>
  </div>
</footer>"""


def footer(lang, slug):
    u = up(lang, slug)
    return f"""{footer_html(lang, u, home(slug))}
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
        # Pagefind writes new hashed files and never removes the old ones, so
        # start from an empty folder or every rebuild leaves its index behind.
        shutil.rmtree(SITE / "pagefind", ignore_errors=True)
        subprocess.run(["npx", "--yes", "pagefind@1", "--site", str(SITE)], check=True)


if __name__ == "__main__":
    main()
