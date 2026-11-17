"""A paper's details for a reading note, from what a person pastes.

Zotero copies or exports an item as BibTeX, RIS or CSL JSON (select it,
then Edit → Copy to Clipboard with the Quick Copy format set to one of
them, or right-click → Export Item…). Any of the three is read here, with
nothing fetched. CSL JSON carries the item's Zotero address
(http://zotero.org/users/<n>/items/<KEY>), which becomes a link that opens
the item in Zotero. A DOI alone, or a doi.org link, is looked up at
Crossref (the one site this reaches, with a short wait), and a zotero://
link is kept as the note's Zotero link.

`read(text)` gives the fields a reading note keeps (lab_notebook.KIND_FIELDS)
plus `title`; anything it can't find is left out.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>{}]+)", re.I)
ZOTERO_LINK = re.compile(r"zotero://select/[^\s\"'<>]+", re.I)
ZOTERO_ITEM = re.compile(r"zotero\.org/(users|groups)/(\w+)/items/([A-Z0-9]{8})", re.I)
CROSSREF = "https://api.crossref.org/works/"
WAIT = 8


class CitationError(Exception):
    """Said to the person in words (lab_notebook.cite)."""


def _clean(value) -> str:
    return " ".join(str(value or "").split())[:500]


def _doi(text: str) -> str:
    found = DOI.search(text or "")
    return found.group(1).rstrip(".,;)") if found else ""


def _authors(names: list[str]) -> str:
    names = [n for n in (_clean(x) for x in names) if n]
    return "; ".join(names[:30]) + ("; …" if len(names) > 30 else "")


def _zotero_from(uri: str) -> str:
    """zotero://select/… for an item's zotero.org address."""
    found = ZOTERO_ITEM.search(uri or "")
    if not found:
        return ""
    where, number, key = found.groups()
    if where.lower() == "groups":
        return f"zotero://select/groups/{number}/items/{key}"
    return f"zotero://select/library/items/{key}"


# ---------------------------------------------------------------- the formats

def from_csl(items) -> dict:
    item = items[0] if isinstance(items, list) and items else items
    if not isinstance(item, dict):
        return {}
    people = []
    for a in item.get("author") or []:
        if isinstance(a, dict):
            people.append(a.get("literal") or " ".join(x for x in (a.get("given"), a.get("family")) if x))
    issued = (item.get("issued") or {}).get("date-parts") or [[]]
    year = str(issued[0][0]) if issued and issued[0] else ""
    journal = item.get("container-title") or ""
    if isinstance(journal, list):
        journal = journal[0] if journal else ""
    title = item.get("title") or ""
    if isinstance(title, list):
        title = title[0] if title else ""
    return {"title": _clean(title), "authors": _authors(people), "journal": _clean(journal), "year": year,
            "doi": _clean(item.get("DOI")), "url": _clean(item.get("URL")),
            "zotero": _zotero_from(str(item.get("id") or ""))}


def _bib_value(raw: str) -> str:
    raw = raw.strip().rstrip(",").strip()
    if raw[:1] in "{\"" and raw[-1:] in "}\"":
        raw = raw[1:-1]
    raw = re.sub(r"\\[a-zA-Z]+\s*", "", raw)          # \textit, \&
    return _clean(raw.replace("{", "").replace("}", "").replace("\\&", "&"))


def from_bibtex(text: str) -> dict:
    fields: dict[str, str] = {}
    body = text[text.find("{") + 1:]
    i, n = 0, len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([A-Za-z][\w-]*)\s*=\s*").match(body, i)
        if not m:
            i += 1
            continue
        name, i = m.group(1).lower(), m.end()
        if i < n and body[i] == "{":
            depth, start = 0, i
            while i < n:
                depth += {"{": 1, "}": -1}.get(body[i], 0)
                i += 1
                if depth == 0:
                    break
            fields[name] = _bib_value(body[start:i])
        elif i < n and body[i] == '"':
            end = body.find('"', i + 1)
            end = n if end < 0 else end + 1
            fields[name], i = _bib_value(body[i:end]), end
        else:
            m2 = re.compile(r"[^,}\n]*").match(body, i)
            fields[name], i = _bib_value(m2.group(0)), m2.end()
    people = []
    for one in re.split(r"\s+and\s+", fields.get("author", "")):
        last, _, first = one.partition(",")
        people.append(f"{first.strip()} {last.strip()}".strip() if first else one.strip())
    return {"title": fields.get("title", ""), "authors": _authors(people),
            "journal": fields.get("journal") or fields.get("journaltitle") or fields.get("booktitle", ""),
            "year": (re.search(r"\d{4}", fields.get("year") or fields.get("date", "")) or [""])[0],
            "doi": fields.get("doi", ""), "url": fields.get("url", "")}


def from_ris(text: str) -> dict:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])\s{1,2}-\s?(.*)$", line.strip())
        if m:
            out.setdefault(m.group(1), []).append(m.group(2).strip())
    first = lambda *tags: next((out[t][0] for t in tags if out.get(t)), "")  # noqa: E731
    people = []
    for one in out.get("AU", []) + out.get("A1", []):
        last, _, given = one.partition(",")
        people.append(f"{given.strip()} {last.strip()}".strip() if given else one)
    return {"title": _clean(first("TI", "T1")), "authors": _authors(people),
            "journal": _clean(first("T2", "JO", "JF", "JA")),
            "year": (re.search(r"\d{4}", first("PY", "Y1", "DA")) or [""])[0],
            "doi": _clean(first("DO")), "url": _clean(first("UR"))}


def crossref(doi: str) -> dict:
    req = Request(CROSSREF + quote(doi, safe="/"), headers={
        "Accept": "application/json", "User-Agent": "BioManager (lab notebook; reading notes)"})
    try:
        with urlopen(req, timeout=WAIT) as r:  # noqa: S310 (always api.crossref.org)
            message = json.loads(r.read().decode("utf-8")).get("message") or {}
    except Exception as e:  # noqa: BLE001 — offline, refused, not found: all said the same way
        raise CitationError(str(getattr(e, "code", "") or e)) from e
    fields = from_csl(message)
    fields["doi"] = fields.get("doi") or doi
    fields["url"] = fields.get("url") or f"https://doi.org/{doi}"
    fields.pop("zotero", None)
    return fields


# ---------------------------------------------------------------- what was pasted

def read(text: str, lookup=None) -> dict:
    """The details in `text`, by what it looks like. A DOI with nothing
    else is looked up (`lookup`, Crossref unless given); raises
    CitationError when that fails."""
    text = (text or "").strip()[:200_000]
    if not text:
        return {}
    fields: dict = {}
    if text[:1] in "[{":
        try:
            fields = from_csl(json.loads(text))
        except ValueError:
            fields = {}
    if not fields and text.startswith("@"):
        fields = from_bibtex(text)
    if not fields and re.search(r"^TY\s{1,2}-", text, re.M):
        fields = from_ris(text)
    zotero = ZOTERO_LINK.search(text)
    if zotero:
        fields["zotero"] = zotero.group(0)
    elif not fields.get("zotero") and ZOTERO_ITEM.search(text):
        fields["zotero"] = _zotero_from(text)
    if not fields.get("title"):
        doi = fields.get("doi") or _doi(text)
        if doi:
            found = (lookup or crossref)(doi)
            found.update({k: v for k, v in fields.items() if v})
            fields = found
        elif re.match(r"https?://", text) and "\n" not in text:
            fields.setdefault("url", text)
    return {k: v for k, v in fields.items() if v}


# ------------------------------------------------------------------ citing

CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
PARTICLES = {"van", "von", "de", "der", "den", "del", "della", "di", "da", "du", "dos", "das",
             "le", "la", "ten", "ter", "zu", "af", "bin", "al", "van't"}
SUFFIX = re.compile(r"^(?:jr|sr|ii|iii|iv|v)\.?$", re.I)
INITIALS = re.compile(r"^(?:[A-Z]\.?-?){1,3}$")
NOT_A_NAME = re.compile(r"^(?:…|\.\.\.|et\.? al\.?|others)$", re.I)


def _listing(part: str) -> list[str]:
    """"Wang X, Li Y" lists people; "Wang, Xiaoming" is one person."""
    pieces = part.split(",")
    return pieces if len(pieces) > 1 and all(" " in p.strip() for p in pieces) else [part]


def split_authors(authors) -> list[str]:
    """The people named in an Authors field, in order: as the app keeps them,
    "Xiaoming Wang; Hua Li", or as people type them, "Wang X, Li Y",
    "Wang, Xiaoming", "Wang and Li"."""
    text = " ".join(str(authors or "").split())
    if not text:
        return []
    if ";" in text:
        names = text.split(";")
    else:
        names = re.split(r"\s*(?:,\s*)?\band\b\s*|\s*&\s*|、|，", text)
        names = _listing(text) if len(names) == 1 else [n for part in names for n in _listing(part)]
    names = [n.strip().rstrip(",") for n in names]
    return [n for n in names if n and not NOT_A_NAME.match(n)]


def surname(name) -> str:
    """The name a citation gives one author: "Jane Smith", "Smith, Jane" and
    "Smith J" are Smith, "Ludwig van Beethoven" is van Beethoven, and a
    Chinese name is written whole, family name first (Zotero gives "小明 王")."""
    text = " ".join(str(name or "").split())
    if not text:
        return ""
    if CJK.search(text):
        if "," in text:
            family, given = (s.strip() for s in text.split(",", 1))
            return family + (given if CJK.search(given) else "")
        words = text.split(" ")
        return words[-1] + "".join(words[:-1]) if len(words) > 1 else text
    if "," in text:
        family = text.split(",", 1)[0].strip()
        if family and not SUFFIX.match(family):
            return family
    words = [w for w in text.split(" ") if not SUFFIX.match(w.rstrip(","))]
    if len(words) == 1:
        return words[0]
    if INITIALS.match(words[-1]) and not INITIALS.match(words[0]):
        return " ".join(w for w in words[:-1] if not INITIALS.match(w))
    at = len(words) - 1
    while at > 1 and words[at - 1].lower() in PARTICLES:
        at -= 1
    if at == 1 and words[0].lower() in PARTICLES and len(words) > 2:
        at = 0
    return " ".join(words[at:])


def cite_year(year) -> str:
    found = re.search(r"\d{4}", str(year or ""))
    return found.group(0) if found else "n.d."


def _short_title(title) -> str:
    title = " ".join(str(title or "").split())
    if not title:
        return "Untitled page"
    if CJK.search(title):
        return f"{title[:15].strip()}…" if len(title) > 16 else title
    words = title.split(" ")
    return " ".join(words[:4]) + "…" if len(words) > 4 else title


def short_cite(authors, year="", title="") -> str:
    """How a paper is cited in a sentence: Smith, 2024; Smith & Lee, 2024;
    Smith et al., 2024; a Chinese first author whole, 王小明 等, 2024. No year
    is n.d.; no authors, the start of its title."""
    names = [n for n in (surname(a) for a in split_authors(authors)) if n]
    year = cite_year(year)
    if not names:
        return f"{_short_title(title)}, {year}"
    first = names[0]
    if len(names) == 1:
        return f"{first}, {year}"
    if CJK.search(first):
        return f"{first} 等, {year}"
    if len(names) == 2:
        return f"{first} & {names[1]}, {year}"
    return f"{first} et al., {year}"


def reference(fields: dict, title: str = "") -> str:
    """A paper as a reference list writes it: Authors (Year). Title. Journal.
    https://doi.org/…"""
    authors = str(fields.get("authors") or "").replace("; ", ", ")
    year = str(fields.get("year") or "").strip()
    head = f"{authors} ({year})." if authors and year else (f"{authors}." if authors else (f"({year})." if year else ""))
    doi = str(fields.get("doi") or "").strip()
    link = f"https://doi.org/{doi}" if doi else str(fields.get("url") or "").strip()
    parts = [head, f"{title.strip().rstrip('.')}." if title and title.strip() else "",
             f"{str(fields.get('journal') or '').strip().rstrip('.')}." if fields.get("journal") else "", link]
    return " ".join(p for p in parts if p)

