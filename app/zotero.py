"""Paper notes made from a Zotero library: each paper added to it (or to one
of its collections) becomes a Reading page in its owner's notebook.

A person connects with a read-only API key from zotero.org (Settings →
Security → Create new private key, with "Allow library access"). The key
says whose it is (/keys/current), so it is all they type; they then pick
the library (their own or a group's), a collection or the whole library,
and the topic the notes go in. Zotero has to sync to zotero.org: the app
reads its web API (api.zotero.org, the only host it reaches here), never
the Zotero program on the computer.

A check asks only for what changed since the library version last read
(`since`), so a check with nothing new is one short request. It runs when
the person opens the notebook, at most every few minutes, and on **Check
now**. A paper gets one note, ever: its item key is kept in `seen` once a
note is made, so editing the paper in Zotero, or deleting the note, does
not make another.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import select, update

from .i18n import gettext
from .models import NotebookPage, NotebookTab, ZoteroLink

API = "https://api.zotero.org"
WAIT = 10
PAGE = 100
MOST_AT_ONCE = 200          # notes made in one check; the rest come in the next
EVERY = timedelta(minutes=5)
SKIP_TYPES = {"note", "attachment", "annotation"}
SOURCE_FIELDS = ("publicationTitle", "proceedingsTitle", "bookTitle", "websiteTitle", "repository",
                 "university", "publisher")


class ZoteroError(Exception):
    """Said to the person in words."""


def _get(path: str, key: str, params: dict | None = None):
    """(JSON, headers) of a GET to Zotero's web API."""
    url = API + path + ("?" + urlencode(params) if params else "")
    req = Request(url, headers={"Zotero-API-Key": key, "Zotero-API-Version": "3",
                                "User-Agent": "BioManager (lab notebook; paper notes)"})
    try:
        with urlopen(req, timeout=WAIT) as r:  # noqa: S310 (always api.zotero.org)
            return json.loads(r.read().decode("utf-8") or "null"), dict(r.headers)
    except HTTPError as e:
        if e.code in (401, 403):
            raise ZoteroError(gettext("Zotero refused that key. Make a new one with library access.")) from e
        if e.code == 404:
            raise ZoteroError(gettext("Zotero has no such library or collection any more.")) from e
        raise ZoteroError(gettext("Zotero answered %(code)s. Try again in a while.", code=e.code)) from e
    except (OSError, ValueError) as e:
        raise ZoteroError(gettext("Could not reach Zotero. Check the internet connection.")) from e


def _all(path: str, key: str, params: dict | None = None, most: int = 1000) -> list:
    out, start = [], 0
    while len(out) < most:
        page, _headers = _get(path, key, {**(params or {}), "limit": PAGE, "start": start})
        out += page or []
        if not page or len(page) < PAGE:
            break
        start += PAGE
    return out


def libraries(key: str) -> dict:
    """Whose key it is, and the libraries it may read with their collections."""
    me, _ = _get("/keys/current", key)
    user_id = str((me or {}).get("userID") or "")
    access = (me or {}).get("access") or {}
    if not user_id:
        raise ZoteroError(gettext("Zotero refused that key. Make a new one with library access."))
    found = []
    if (access.get("user") or {}).get("library"):
        found.append({"library": f"users/{user_id}", "name": gettext("My library")})
    if access.get("groups"):
        for group in _all(f"/users/{user_id}/groups", key):
            found.append({"library": f"groups/{group.get('id')}", "name": (group.get("data") or {}).get("name") or str(group.get("id"))})
    if not found:
        raise ZoteroError(gettext("That key can't read any library. Allow library access when you make it."))
    for lib in found:
        cols = _all(f"/{lib['library']}/collections", key)
        names = {c["key"]: (c.get("data") or {}).get("name", "") for c in cols}
        parents = {c["key"]: (c.get("data") or {}).get("parentCollection") or None for c in cols}

        def path(k, depth=0):
            up = parents.get(k)
            return (path(up, depth + 1) + " › " if up and up in names and depth < 8 else "") + names.get(k, "")
        lib["collections"] = sorted(({"key": k, "name": path(k)} for k in names), key=lambda c: c["name"].lower())
    return {"user_id": user_id, "username": (me or {}).get("username", ""), "libraries": found}


def latest_version(link: ZoteroLink) -> int:
    _items, headers = _get(f"/{link.library}/items/top", link.api_key, {"limit": 1, "format": "json"})
    return int(headers.get("Last-Modified-Version") or 0)


# ---------------------------------------------------------------- an item as a paper note

def zotero_link(library: str, key: str) -> str:
    where, number = library.split("/", 1)
    return f"zotero://select/groups/{number}/items/{key}" if where == "groups" else f"zotero://select/library/items/{key}"


def fields(data: dict, library: str) -> dict:
    """A Zotero item's data as a reading note's fields (lab_notebook.KIND_FIELDS)."""
    people = [c for c in data.get("creators") or [] if c.get("creatorType") == "author"] or data.get("creators") or []
    names = [c.get("name") or " ".join(x for x in (c.get("firstName"), c.get("lastName")) if x) for c in people]
    doi = data.get("DOI") or ""
    if not doi:
        found = re.search(r"^DOI:\s*(\S+)", data.get("extra") or "", re.I | re.M)
        doi = found.group(1) if found else ""
    year = re.search(r"\d{4}", data.get("date") or "")
    source = next((data[f] for f in SOURCE_FIELDS if data.get(f)), "")
    out = {"title": " ".join((data.get("title") or "").split())[:200],
           "authors": "; ".join(n for n in names if n)[:2000],
           "journal": source, "year": year.group(0) if year else "", "doi": doi, "url": data.get("url") or "",
           "zotero": zotero_link(library, data.get("key") or "")}
    return {k: v for k, v in out.items() if v}


def note_body(data: dict, starter_body: str) -> str:
    abstract = " ".join((data.get("abstractNote") or "").split())
    return (f"## Abstract\n\n{abstract}\n\n" if abstract else "") + starter_body


# ---------------------------------------------------------------- a check

def due(link: ZoteroLink | None, now: datetime | None = None) -> bool:
    return link is not None and (link.checked_at is None or (now or datetime.utcnow()) - link.checked_at >= EVERY)


def claim(session, link: ZoteroLink, now: bool = False) -> bool:
    """Take the next check for this request, or not: two notebook tabs
    opened at once must not both make a note of the same paper. The
    check's time moves only if nobody else moved it since it was read
    (committed at once), so one of them goes ahead. Without `now`, only
    once a check is due."""
    if not now and not due(link):
        return False
    read_at = link.checked_at
    same = ZoteroLink.checked_at.is_(None) if read_at is None else ZoteroLink.checked_at == read_at
    took = session.execute(update(ZoteroLink).where(ZoteroLink.id == link.id, same)
                           .values(checked_at=datetime.utcnow()))
    session.commit()
    if not took.rowcount:
        return False
    session.refresh(link)
    return True


def check(session, link: ZoteroLink) -> list[dict]:
    """Make a note for each paper new to the library (or collection) since
    the last check; the notes made, as {id, title, url}. Errors are kept
    on the link (`error`) and raised."""
    from . import lab_notebook
    path = f"/{link.library}" + (f"/collections/{link.collection}" if link.collection else "") + "/items/top"
    seen = set(filter(None, (link_seen(link))))
    made, start, newest, finished = [], 0, link.version or 0, False
    link.checked_at = datetime.utcnow()
    try:
        while len(made) < MOST_AT_ONCE:
            items, headers = _get(path, link.api_key, {"format": "json", "since": link.version or 0, "limit": PAGE,
                                                        "start": start, "sort": "dateAdded", "direction": "asc"})
            newest = max(newest, int(headers.get("Last-Modified-Version") or 0))
            for item in items or []:
                data = item.get("data") or {}
                key = item.get("key") or data.get("key") or ""
                if not key or key in seen or data.get("itemType") in SKIP_TYPES or data.get("deleted"):
                    continue
                seen.add(key)
                page = _make_note(session, link, data, lab_notebook)
                made.append({"id": page.id, "title": page.title, "url": lab_notebook.page_url(page.id)})
                if len(made) >= MOST_AT_ONCE:
                    break
            if not items or len(items) < PAGE:
                finished = True
                break
            start += PAGE
    except ZoteroError as e:
        link.error = str(e)[:300]
        raise
    finally:
        link.seen = ",".join(sorted(seen))
    if finished:
        link.version = newest
    link.error = ""
    link.made = (link.made or 0) + len(made)
    return made


def link_seen(link: ZoteroLink) -> list[str]:
    return (link.seen or "").split(",")


def _make_note(session, link: ZoteroLink, data: dict, lab_notebook) -> NotebookPage:
    tab = session.get(NotebookTab, link.tab_id_fk) if link.tab_id_fk else None
    if tab is None or tab.owner_username != link.owner:
        tab = lab_notebook.folder_for(session, link.owner)
    found = fields(data, link.library)
    starter = lab_notebook.STARTERS["reading"]
    page = lab_notebook.new_page(session, tab, found.pop("title", "") or starter["title"],
                                 note_body(data, starter["body"]), kind="reading")
    page.properties = json.dumps(lab_notebook.clean_properties([{"key": k, "value": v} for k, v in found.items()]))
    info = lab_notebook.info_for(session, page.id, create=True)
    info.edited_by = link.owner
    info.tags = lab_notebook.normalize_tags([t.get("tag") for t in data.get("tags") or [] if t.get("tag")][:10])
    lab_notebook.record_edit(session, page, link.owner)
    return page


def link_for(session, username: str) -> ZoteroLink | None:
    return session.scalar(select(ZoteroLink).where(ZoteroLink.owner == username))


def state(link: ZoteroLink | None) -> dict:
    """What the page shows of a connection (never the key)."""
    if link is None:
        return {"connected": False}
    return {"connected": True, "library": link.library, "library_name": link.library_name,
            "collection": link.collection, "collection_name": link.collection_name, "tab_id": link.tab_id_fk,
            "checked_at": link.checked_at.isoformat() if link.checked_at else "", "made": link.made or 0,
            "error": link.error or ""}
