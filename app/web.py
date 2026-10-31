"""What a page needs whatever it is a page of: who is signed in and where
they land after signing in, how a save answers, how an edit or a delete is
written down, a timestamp in the lab's own time, a person's badge, what
the built-in databases are called here, and the app's icon.

These were in app.py, where a module of pages could only reach them by
importing the file that imports it. They are here so that a module of pages
imports this and app/flask_app.py, and nothing else of the application
itself.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import wraps

from flask import flash, g, get_flashed_messages, jsonify, redirect, request, session, url_for
from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import appearance, lab
from .db import SessionLocal
from .flask_app import app
from .i18n import gettext
from .models import AuditEntry, UserAccount
def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def next_number_retried(view):
    """For a save that hands out the next free number (a mouse, a cage, a
    litter). Two people saving at the same moment both read the same
    highest number, and the database refuses the second: try that save
    again, with a fresh number, rather than tell them an ID they never
    typed "is already used". A number someone typed that is taken is
    refused as before, after the retries."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        import random
        import time
        tries = 6
        for attempt in range(tries):
            flashes = list(session.get("_flashes") or [])
            try:
                return view(*args, **kwargs)
            except IntegrityError:
                if attempt == tries - 1:
                    raise
                session["_flashes"] = flashes        # the failed try's messages go with it
                for upload in request.files.values():
                    upload.stream.seek(0)             # the next try reads the uploaded file again
                time.sleep(random.uniform(0.02, 0.12) * (attempt + 1))

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        if g.user.role != "admin":
            flash(gettext("Admin access required."), "error")
            return redirect(url_for("colony"))
        return view(*args, **kwargs)

    return wrapped_view


def autosave_response(default_view: str):
    if request.headers.get("X-Autosave") == "1":
        # A background save has no page to show a flash on, so errors are
        # returned to the sheet instead of surfacing on the next page load.
        messages = get_flashed_messages(with_categories=True)
        errors = [text for category, text in messages if category == "error"]
        for category, text in messages:
            if category != "error":
                flash(text, category)
        if errors:
            return jsonify({"ok": False, "error": " ".join(errors)}), 409
        return jsonify({"ok": True})
    # A regular form post (a dialog or a detail page) goes back where it
    # came from — the fish line page, or the colony with its scope intact —
    # but only to this site.
    referrer = request.referrer or ""
    if referrer.startswith(request.host_url):
        return redirect(referrer)
    return redirect(url_for("colony", view=default_view))


def log_delete(db_session, table_name: str, record_id: int, record_label: str = "", details: str = "") -> None:
    """Insert an AuditEntry capturing a deletion. The caller is responsible
    for the db_session commit (we just add the entry to the same transaction
    as the delete itself).

    Tables in audit.TRACKED_TABLES are skipped: the flush listener already
    records their deletes, with the full snapshot undo needs, and a second
    bare entry only doubled every delete in the audit log."""
    from .audit import TRACKED_TABLES

    if g.user is None or table_name in TRACKED_TABLES:
        return
    db_session.add(AuditEntry(
        table_name=table_name,
        record_id=record_id,
        record_label=record_label or str(record_id),
        action="delete",
        changed_by=g.user.username,
        details=details,
    ))


def stamp_updated(record, fields_changed: int = 1) -> None:
    """Mark a record as edited by the current user. Skips silently if no
    user is in the request context (e.g. management commands)."""
    if g.user is None or not fields_changed:
        return
    record.updated_at = datetime.utcnow()
    record.updated_by = g.user.username


def local_time(value):
    """A timestamp the app stamped (datetime.utcnow(): naive UTC) as the
    lab's own time (the server's TZ), for showing to people. Without it an
    evening's entry reads as tomorrow."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)



def _get_user_directory():
    """Cache username -> {short_name, display_name} for the current request."""
    cache = g.get("user_directory")
    if cache is not None:
        return cache
    cache = {}
    try:
        with SessionLocal() as db_session:
            for u in db_session.scalars(select(UserAccount)).all():
                cache[u.username] = {"short": u.short_name or u.username[:5], "display": u.display_name or u.username,
                                     "own_short": (u.short_name or "").strip()}
    except Exception:
        pass
    g.user_directory = cache
    return cache



def _name_color(seed: str) -> tuple[int, int, int]:
    """Stable HSL hue from a name string."""
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFF
    return h % 360, 55, 32



def owner_badge_filter(name: str) -> Markup:
    """A person's initials in their colour. Made once a name for the
    request: a long sheet or the Breeders tab shows the same few thousands
    of times."""
    if not name:
        return Markup("")
    made = g.get("owner_badges")
    if made is None:
        made = g.owner_badges = {}
    found = made.get(name)
    if found is None:
        found = made[name] = _owner_badge(name)
    return found



def _owner_badge(name: str) -> Markup:
    info = _get_user_directory().get(name)
    display = (info["display"] if info else "") or name
    tooltip = display
    # The short name they chose (Settings → Profile, up to 5 letters: THX),
    # as on their records in tables. Else two initials, from the person's
    # name as the lab knows it (Jordan Lee: JL), or their username (jordan: JO).
    text = info["own_short"] if info and info.get("own_short") else ""
    if not text:
        parts = [p for p in re.split(r"[\s._-]+", display.strip()) if p]
        if len(parts) >= 2:
            text = (parts[0][0] + parts[-1][0]).upper()
        else:
            text = (parts[0][:2] if parts else name[:2]).upper()
    hue, _sat, _light = _name_color(name)
    # Soft in light and dark alike: the colour tells people apart, the
    # initials stay readable on it.
    bg = f"hsl({hue} 70% 55% / 0.2)"
    fg = f"hsl({hue} 55% 25%);color:light-dark(hsl({hue} 55% 25%), hsl({hue} 75% 78%))"
    # data-tooltip drives the CSS popover; aria-label keeps it accessible.
    # We deliberately avoid the native `title` attribute so the browser doesn't
    # show its own delayed tooltip on top of ours.
    return Markup(
        f'<span class="owner-badge" data-tooltip="{escape(tooltip)}" aria-label="{escape(tooltip)}" '
        f'data-len="{min(len(text), 5)}" style="background:{bg};color:{fg};">{escape(text)}</span>'
    )


def app_icon_url(glyph: str, color: str) -> str:
    # The default is the static icon the build script writes, so the sign-in
    # page and everyone who never chose share one cached file.
    if appearance.is_default(glyph, color):
        return url_for("static", filename="icon.svg", v=appearance.version(glyph, color))
    return url_for("app_icon", glyph=glyph, color=color, v=appearance.version(glyph, color))


def builtin_labels() -> dict[str, str]:
    """Names of the built-in databases (the lab may have renamed them)."""
    from . import inventory_service as inventories
    try:
        with SessionLocal() as db_session:
            return inventories.builtin_labels(db_session)
    except Exception:
        return {key: default for key, (default, _short) in inventories.BUILTIN_DATABASES.items()}


ALLOWED_LANDING_ENDPOINTS = {"colony", "notebook", "calendar", "orders", "samples", "plasmids"}


LANDING_FEATURES = {"colony": "colony", "calendar": "calendar", "notebook": "notebook", "plasmids": "plasmids"}


def landing_url(user, after_welcome: bool = False) -> str:
    """Where someone lands after signing in. The first admin goes to the
    setup survey until the lab is set up, anyone new to the welcome tour
    once, then their chosen start page (or home, if that page's function
    is switched off)."""
    with SessionLocal() as db_session:
        if user.role == "admin" and not lab.setup_done(db_session):
            return url_for("lab.setup")
        features = lab.features_on(db_session)
        # Someone who has not chosen a start page starts at home while
        # their Getting started list has steps left.
        new_to_it = not (user.default_landing or "").strip() and lab.getting_started_pending(
            db_session, user, on_server=not app.config.get("LOCAL_SETUP"))
    if getattr(user, "welcomed_at", None) is None and not after_welcome:
        return url_for("lab.welcome")
    if new_to_it:
        return url_for("home_dashboard")
    landing = (user.default_landing or "").strip()
    if landing not in ALLOWED_LANDING_ENDPOINTS:
        landing = "colony"
    if not features.get(LANDING_FEATURES.get(landing, ""), True):
        landing = "home_dashboard"
    return url_for(landing)
