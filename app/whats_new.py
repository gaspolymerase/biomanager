"""What's new: a short note, once, after BioManager is updated.

The first page someone opens after their lab's app or server moves to a new
version shows a small dialog: what is new in it, what works differently,
what was fixed, and a link to the full release notes. **Got it** keeps it
from showing again for that person (`users.whats_new_seen`); Help → What's
new opens it again.

The notes are written here, a few lines a version, alongside the release's
full notes in `docs/release-notes/<version>.md` (tests/test_whats_new.py
fails when the newest release has no entry). Someone who skipped a version
sees the notes of each one since the last they saw; someone who never saw
one sees only the current version's. A brand-new account sees the welcome
tour instead, and starts from the version it joined on.
"""
from __future__ import annotations

import re

from flask import Blueprint, g, has_request_context, jsonify
from markupsafe import Markup, escape

from .db import SessionLocal
from .models import UserAccount

bp = Blueprint("whats_new", __name__)

RELEASES_URL = "https://github.com/gaspolymerase/biomanager/releases/tag/v{version}"

# Newest first. Each line is plain text; **bold** names a button or a page.
# Each line's Chinese goes in app/translations/zh/whats_new.json (a test checks).
NOTES: dict[str, dict[str, list[str]]] = {
    "1.1.0": {
        "new": [
            "**AI assistants**: tell Claude, ChatGPT or Cursor what you did and it proposes the records; they wait "
            "for you on **Proposed changes**. **Help → Connect an AI assistant** sets it up.",
            "**Plasmids**: **Versions** you can restore, **Made from** and a family tree, a **Feature library** "
            "that marks elements on any map, **Assemble a plasmid**, and **Files** for reads and gel photos.",
            "**Glycerol stocks**: a database for the bacteria carrying each plasmid.",
            "The rest of BioManager is in Chinese: the other databases, experiments, the notebook and the messages.",
        ],
        "changed": [
            "**Primers**, **Glycerol stocks** and **Viruses** are tabs at the top of **Plasmids**, not separate "
            "entries in the sidebar.",
        ],
    },
    "1.0.6": {
        "new": [
            "**BioManager in Chinese (中文)**: choose it in **Settings → Language**, or 中文 / English on the sign-in "
            "page. The colony, sheets, calendar, Home and Settings are translated so far.",
        ],
        "fixed": [
            "On a Windows PC set to Chinese, Japanese or Korean, the desktop app starts again.",
        ],
    },
    "1.0.5": {
        "changed": [
            "BioManager's website is now **biomanager.org**: **Help → User guide** and the app's other links go "
            "there. The old address still works.",
        ],
    },
    "1.0.4": {
        "new": [
            "**What's new**: this note, once after each update. **Help → What's new** opens it again.",
        ],
        "changed": [
            "**Litter born** asks for the date of birth (today unless you change it) before recording the litter.",
            "Any shared cage now says **Shared**, not only breeder cages.",
        ],
        "fixed": [
            "In an open cage, the mouse table's header no longer covers the first mouse.",
            "On a Windows 10 PC without Microsoft Edge WebView2, the desktop app offers to download it and opens in the web browser meanwhile, instead of a blank window.",
        ],
    },
    "1.0.3": {
        "new": [
            "**Project groups** (More → Project groups): share cages, stock, databases, to-dos and notebook "
            "pages with the people on your project.",
            "**Who sees it** on calendar events and to-dos: only you, everyone in the lab, or one of your groups.",
            "**Any cage can be shared**, not only breeder cages, and the Cages tab has a chip for each purpose.",
            "**Settings → Devices**: open the lab's server in the desktop app, or share a desktop's lab on the "
            "lab's network.",
        ],
        "changed": [
            "Your own to-dos and events are yours alone, admins included. A shared event is changed only by "
            "whoever added it and admins.",
        ],
        "fixed": [
            "A sample from a mouse number too big for the database no longer stops the samples sheet opening.",
        ],
    },
}
HEADINGS = (("new", "New"), ("changed", "Works differently"), ("fixed", "Fixed"))


def parse(version: str | None) -> tuple[int, ...] | None:
    """(1, 0, 3) for "1.0.3", "v1.0.3", "1.0.3+dev" or "1.0.3-rc.1"; None for
    anything that names no release ("server", "")."""
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", (version or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def running() -> tuple[int, ...] | None:
    from .feedback import app_version
    return parse(app_version())


def _label(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def due(seen: str | None, now: tuple[int, ...] | None = None) -> list[dict]:
    """The notes someone who last saw `seen` has not seen yet, newest
    first: every version after it up to the running one, or only the running
    one when they have never seen any."""
    now = now if now is not None else running()
    if now is None:
        return []
    last = parse(seen)
    out = []
    for version, note in NOTES.items():
        v = parse(version)
        if v is None or v > now:
            continue
        if (last is None and v == now) or (last is not None and v > last):
            out.append({"version": version, "url": RELEASES_URL.format(version=version),
                        "sections": [(title, note.get(key, [])) for key, title in HEADINGS if note.get(key)]})
    return sorted(out, key=lambda n: parse(n["version"]), reverse=True)


def current_note(now: tuple[int, ...] | None = None) -> list[dict]:
    """The running version's note (for Help → What's new), or the newest one
    before it."""
    now = now if now is not None else running()
    if now is None:
        return []
    earlier = [v for v in NOTES if parse(v) is not None and parse(v) <= now]
    if not earlier:
        return []
    newest = max(earlier, key=parse)
    return [n for n in due(None, parse(newest)) if n["version"] == newest]


def stamp(user) -> None:
    """Mark the running version as seen by this person (a new account, or
    Got it)."""
    now = running()
    if now is not None and user is not None:
        user.whats_new_seen = _label(now)


def rich(text: str) -> Markup:
    """A note line in the page's language (its Chinese is in
    app/translations/zh/whats_new.json): escaped, with **bold** made bold."""
    from .i18n import translate_value
    return Markup(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", str(escape(translate_value(text)))))


@bp.app_context_processor
def inject():
    if not has_request_context():
        return {}
    user = g.get("user")
    # A new account sees the welcome tour first, and is stamped when it ends.
    notes = due(user.whats_new_seen) if user is not None and user.welcomed_at is not None else []
    return {"whats_new_due": notes, "whats_new_rich": rich,
            "whats_new_current": (lambda: current_note()) if user is not None else (lambda: [])}


@bp.post("/whats-new/seen")
def seen():
    """Got it: this person has read the notes up to the running version."""
    if g.get("user") is None:
        return jsonify({"ok": False}), 401
    with SessionLocal() as s:
        user = s.get(UserAccount, g.user.id)
        stamp(user)
        s.commit()
    return jsonify({"ok": True})
