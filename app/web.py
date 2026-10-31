"""What a page needs whatever it is a page of: who is signed in, how a save
answers, and how an edit or a delete is written down.

These were in app.py, where a module of pages could only reach them by
importing the file that imports it. They are here so that a module of pages
imports this and app/flask_app.py, and nothing else of the application
itself.
"""
from __future__ import annotations

from datetime import datetime
from functools import wraps

from flask import flash, g, get_flashed_messages, jsonify, redirect, request, session, url_for
from sqlalchemy.exc import IntegrityError

from .i18n import gettext
from .models import AuditEntry
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


