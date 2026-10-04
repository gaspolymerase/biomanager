from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import Flask, Response, abort, flash, g, get_flashed_messages, has_request_context, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from markupsafe import Markup, escape
from werkzeug.datastructures import ImmutableMultiDict
from sqlalchemy import case, func, select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.security import check_password_hash, generate_password_hash

from .db import SessionLocal
from . import access, i18n, lab, positions, security
from .i18n import gettext, ngettext, pgettext
from .formutil import form_changed
# Importing this registers the SQLAlchemy flush listener that writes
# audit_log rows for every tracked change; nothing here calls into it.
from . import audit  # noqa: F401
from . import undo as undo_service
from .models import (
    COLONY_VIEWS,
    CageRecord,
    CalendarEvent,
    CalendarRepeat,
    CalendarSubscription,
    ClutchRecord,
    FishLine,
    FishRack,
    FishRecord,
    FishSacLog,
    GoogleCalendarLink,
    TANK_PURPOSE_OPTIONS,
    FISH_SEX_OPTIONS,
    FISH_STATUS_OPTIONS,
    TankRecord,
    WaterLog,
    WaterSystem,
    ChemicalReference,
    DropdownOption,
    Experiment,
    ExperimentMouse,
    LitterRecord,
    InventoryItem,
    InventoryModule,
    MouseRack,
    MouseWeight,
    MOUSE_STATUS_OPTIONS,
    MouseRecord,
    AuditEntry,
    BatchRecord,
    NotebookEntry,
    NotebookPage,
    NotebookTab,
    NotebookTemplate,
    PlasmidRecord,
    ORDER_STATUS_OPTIONS,
    Order,
    SAMPLE_TYPE_OPTIONS,
    StrainRecord,
    TASK_STATUS_OPTIONS,
    AnimalRecord,
    NotificationRecord,
    SampleRecord,
    TaskItem,
    UserAccount,
    UserIdentity,
)
from .formutil import arg_int, like_pattern
from .services import (
    csv_text,
    add_notification,
    breeder_mice,
    is_breeder_purpose,
    WEAN_OFFSET_DAYS,
    MIN_WEAN_AGE_DAYS,
    mark_weaned,
    weaning_due,
    weaning_title,
    CAGE_GENO_OFFSET_DAYS,
    genotyping_offset,
    split_genotype,
    derive_auto_calendar_items,
    fetch_ics_subscription,
    fetch_google_calendar_items,
    google_client_config,
    google_oauth_configured,
    GOOGLE_OAUTH_SCOPES,
    breeder_summary,
    cage_derived_dates,
    cage_is_active,
    current_lab_usernames,
    mouse_is_active,
    apply_status_rules,
    END_STATUSES,
    mouse_racks,
    normalize_status,
    sample_source_label,
    sample_sources,
    dropdown_options_map,
    dropdown_records_map,
    export_mouse_rows,
    generate_litter_id,
    get_or_create_cage,
    get_or_create_litter,
    init_database,
    mouse_display_row,
    next_cage_id,
    next_litter_id,
    next_mouse_id,
    reserve_cage_ids,
    reserve_mouse_ids,
    parse_date,
    recent_notifications,
    save_uploaded_file,
    save_uploaded_image,
    sync_mouse_transgenes,
    transgene_values_from_form,
)


app = Flask(__name__)
# English or Chinese: _() in templates, gettext() in Python (app/i18n.py).
i18n.init_app(app)
# See app/security.py: a key from SECRET_KEY, or one made once and kept in
# the data folder, never a published default.
app.config["SECRET_KEY"] = security.secret_key()
init_database()

# Configurable organism modules (flies, worms, and anything a lab adds) live
# in their own blueprint — none of it is species-specific, so it does not
# belong in this file. See app/organisms.py for the capability vocabulary.
from .organism_routes import bp as organism_bp  # noqa: E402
from .inventory_routes import bp as inventory_bp  # noqa: E402
from .stock_routes import bp as stocks_bp  # noqa: E402
from .labels import bp as labels_bp  # noqa: E402
from .admin_racks import bp as admin_racks_bp  # noqa: E402

app.register_blueprint(organism_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(stocks_bp)
app.register_blueprint(labels_bp)
app.register_blueprint(admin_racks_bp)

# Sign in with Google or Microsoft (app/oidc.py); offered only when configured.
from . import oidc  # noqa: E402

app.register_blueprint(oidc.bp)

# Importing app.notify registers the listener that sends notifications.
from . import guests, lab_copy, lab_routes, notify  # noqa: E402,F401
from . import appearance, home_layouts  # noqa: E402
from . import lab_calendar  # noqa: E402
from . import lab_notebook  # noqa: E402

with SessionLocal() as _db_session:
    if _db_session.scalar(select(func.count(UserAccount.id))) == 0:
        security.announce_setup_code(app.logger)


# When running as a frozen .app/.exe, or with BIOMANAGER_UPLOADS_DIR set (a
# server keeps uploads on its data volume), uploads live outside app/static/.
# Flask's default /static/ handler only sees files inside app/static/, so an
# explicit route resolves /static/uploads/<name> against the uploads dir; the
# more specific rule wins over /static/<path>. security.guard_uploads keeps
# both behind a login.
from .paths import is_frozen, uploads_dir as _uploads_dir  # noqa: E402

if is_frozen() or os.environ.get("BIOMANAGER_UPLOADS_DIR", "").strip():
    @app.route("/static/uploads/<path:filename>")
    def _serve_uploads(filename):
        return send_from_directory(_uploads_dir(), filename)


@app.route("/favicon.ico")
def favicon():
    """Browsers ask for this on pages that name no icon (a JSON answer, a
    download); the app's icon, rather than a 404 in the server's log."""
    return app.send_static_file("icon-192.png"), 200, {"Cache-Control": "public, max-age=604800"}


@app.route("/healthz")
def healthz():
    """For a load balancer or container health check: is the app up and
    can it reach its database? Says nothing else, and needs no login."""
    try:
        with SessionLocal() as db_session:
            db_session.execute(select(1))
    except Exception:  # noqa: BLE001 — any failure means "not healthy"
        app.logger.exception("health check could not reach the database")
        return "database unavailable\n", 503, {"Content-Type": "text/plain"}
    return "ok\n", 200, {"Content-Type": "text/plain", "Cache-Control": "no-store"}


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


@app.before_request
def follow_lab_timezone():
    # The lab's zone from Lab setup (lab.apply_timezone), before anything
    # asks what day it is.
    try:
        lab.refresh_timezone(SessionLocal)
    except Exception:  # noqa: BLE001 — a missing setting table on first start
        app.logger.debug("lab time zone not read", exc_info=True)


@app.before_request
def load_current_user():
    if request.path == "/api/v1" or request.path.startswith("/api/v1/"):
        # The API is signed in by its token alone, never the session cookie (app/api.py),
        # and answers in English whatever the client's language: programs read it.
        from . import api
        g.lang = i18n.DEFAULT
        api.authenticate()
        return
    user_id = session.get("user_id")
    g.user = None
    if user_id is None:
        return
    with SessionLocal() as db_session:
        user = db_session.get(UserAccount, user_id)
        # A password change or reset ends every session signed in with the
        # old password (see security.session_stamp).
        # So does the end of a temporary account (a guest pass, app/guests.py).
        if (user is None or getattr(user, "disabled", False)
                or (user.expires_at is not None and user.expires_at <= datetime.utcnow())
                or not security.session_matches(session.get("auth"), user, db_session)
                or security.signed_out(db_session)):
            session.clear()
            g.user = None
            return
        g.user = user
        # Their language from Settings, read once per sign-in (app/i18n.py).
        if session.get("lang_for") != user.id:
            i18n.remember(i18n.preference(db_session, user.username))
            session["lang_for"] = user.id
        # Remember the language they see, to write their notifications in it
        # (its own session: committing this one would detach g.user).
        if session.get("lang_seen") != i18n.current():
            with SessionLocal() as seen_session:
                i18n.note_seen(seen_session, user.username, i18n.current())
                seen_session.commit()
            session["lang_seen"] = i18n.current()


# Cookies, upload limits, the cross-site check and security headers. After
# load_current_user: the uploads check needs g.user.
security.init_app(app)

# Lab setup survey, welcome tour and notifications (app/lab_routes.py).
# Registered after load_current_user too: its checks (a switched-off
# function, the daily reminder) need to know who is signed in.
app.register_blueprint(lab_routes.bp)

# Guest passes and the gate in front of internet access (app/guests.py).
app.register_blueprint(guests.bp)

# Copies of the lab's database on every desktop app (app/lab_copy.py).
app.register_blueprint(lab_copy.bp)
# The devices that work on the lab, and which holds its master copy (app/devices.py).
from . import devices  # noqa: E402
app.register_blueprint(devices.bp)
# Project groups: a layer between a person and the lab (app/groups.py).
from . import groups as project_groups  # noqa: E402
app.register_blueprint(project_groups.bp)
# What's new: a short note once after an update (app/whats_new.py).
from . import whats_new  # noqa: E402
app.register_blueprint(whats_new.bp)
# Repeats, protocols, equipment, away days and the phone feed (app/lab_calendar.py).
app.register_blueprint(lab_calendar.bp)
# Setting up a lab server from the desktop app (app/server_setup.py).
from . import server_setup  # noqa: E402
app.register_blueprint(server_setup.bp)
# Experiments on every database's animals (app/experiments.py), and what is
# done to them, planned and recorded (app/experiment_steps.py).
from . import experiments as experiment_pages  # noqa: E402
from . import experiment_steps  # noqa: E402
app.register_blueprint(experiment_pages.bp)
app.register_blueprint(experiment_steps.bp)
# Signing a notebook page, which locks it (app/signatures.py).
from . import signatures as record_signatures  # noqa: E402
app.register_blueprint(record_signatures.bp)
# The public API, and Settings → API tokens (app/api.py).
from . import api as public_api  # noqa: E402
app.register_blueprint(public_api.bp)
app.register_blueprint(public_api.pages)
# Send feedback and the usage report, for a pilot (app/feedback.py).
from . import feedback as lab_feedback  # noqa: E402
app.register_blueprint(lab_feedback.bp)
# Anonymous counts for BioManager's makers, once a day (app/telemetry.py).
from . import telemetry  # noqa: E402
telemetry.init_app(app)
# Import from Excel into any database (app/sheet_import.py).
from . import sheet_import  # noqa: E402
app.register_blueprint(sheet_import.bp)
# Sharing, live editing, versions, comments, protocols and meetings (app/lab_notebook.py).
app.register_blueprint(lab_notebook.bp)


@app.route("/app-icon/<glyph>/<color>.svg")
def app_icon(glyph: str, color: str):
    """A picture and colour from Settings (app/appearance.py). Needs no
    login: it is the same for everyone and says nothing about the lab."""
    if glyph not in appearance.GLYPHS or color not in appearance.PALETTES:
        abort(404)
    return Response(appearance.render(glyph, color), mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


def app_icon_url(glyph: str, color: str) -> str:
    # The default is the static icon the build script writes, so the sign-in
    # page and everyone who never chose share one cached file.
    if appearance.is_default(glyph, color):
        return url_for("static", filename="icon.svg", v=appearance.version(glyph, color))
    return url_for("app_icon", glyph=glyph, color=color, v=appearance.version(glyph, color))


def _said(message: str) -> str:
    """An error handler's message in the page's language; the JSON API
    (/api/…, read by programs) keeps the English."""
    return message if request.path.startswith("/api/") else gettext(message)


@app.errorhandler(403)
def handle_forbidden(_error):
    """A page someone may not open: say so in the app, with a way back,
    rather than a bare "Forbidden". Background saves get JSON."""
    message = ("That page is for lab admins, or for whoever owns what it shows. "
               "If you need it, ask an admin.")
    if request.headers.get("X-Autosave") == "1" or request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": False, "error": _said(message)}), 403
    return render_template("error.html", title="You don't have access to that", message=message), 403


def _plain_error_page(title: str, message: str) -> str:
    """error_plain.html rendered straight from the template, without the
    app's context processors: they read the database, which may be what
    just failed."""
    return app.jinja_env.get_template("error_plain.html").render(
        title=title, message=message, request=request, url_for=url_for)


def _wants_json() -> bool:
    """A background save, the API, or a page script's fetch: answer JSON,
    never a redirect to a page (which a script reads as a broken reply)."""
    return (request.headers.get("X-Autosave") == "1" or request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") == "fetch")


@app.errorhandler(OperationalError)
def handle_database_unavailable(error: OperationalError):
    """The database is down, locked by another program for too long, or its
    disk is full. Nothing was saved; say so and that it's worth retrying,
    rather than a bare "Internal Server Error"."""
    detail = str(getattr(error, "orig", error))
    app.logger.error("database unavailable on %s: %s", request.path, detail)
    full = "full" in detail.lower()
    message = ("The server's disk is full, so nothing was saved. Tell whoever runs the server."
               if full else "The database didn't answer in time, so nothing was saved. Try again in a moment; "
               "if it keeps happening, tell whoever runs the server.")
    if _wants_json():
        return jsonify({"ok": False, "error": _said(message)}), 503
    return _plain_error_page("The database is not answering", message), 503


@app.errorhandler(StaleDataError)
def handle_stale_data(_error):
    """The record was removed (an Undo, a colleague) while this save ran."""
    message = _said("That record was changed or removed by someone else just now, so nothing was saved. Reload the page.")
    if _wants_json():
        return jsonify({"ok": False, "error": message}), 409
    flash(message, "error")
    referrer = request.referrer or ""
    return redirect(referrer if referrer.startswith(request.host_url) else url_for("home_dashboard"))


@app.errorhandler(OverflowError)
def handle_overflow(error: OverflowError):
    """A number in the address too big for the database or a date."""
    app.logger.warning("overflow on %s: %s", request.path, error)
    message = "A number or date in that request is out of range."
    if _wants_json():
        return jsonify({"ok": False, "error": _said(message)}), 400
    return render_template("error.html", title="That's out of range", message=message), 400


@app.errorhandler(404)
def handle_not_found(_error):
    if _wants_json():
        return jsonify({"ok": False, "error": _said("Not found.")}), 404
    return render_template("error.html", title="There's nothing here",
                           message="That page doesn't exist, or what it showed was deleted."), 404


@app.errorhandler(500)
def handle_server_error(_error):
    message = "Something went wrong on the server, so that wasn't done. Try again; if it keeps happening, " \
              "use Send feedback to say what you were doing."
    if _wants_json():
        return jsonify({"ok": False, "error": _said(message)}), 500
    return _plain_error_page("Something went wrong", message), 500


@app.errorhandler(IntegrityError)
def handle_integrity_error(error: IntegrityError):
    """A duplicate ID (or similar) is a message for the person, not a 500.

    Routes open their session in a `with` block, so by the time this runs
    the failed transaction has already been rolled back and closed.
    """
    detail = str(getattr(error, "orig", error))
    # SQLite: "UNIQUE constraint failed: organism_lines.module_id_fk, organism_lines.code"
    # PostgreSQL: "duplicate key value ... DETAIL:  Key (module_id_fk, code)=(3, X) already exists."
    # The last column is the one the person typed (the others scope it).
    match = (re.search(r"UNIQUE constraint failed: ([\w.]+(?:,\s*[\w.]+)*)", detail)
             or re.search(r"Key \(([\w\s,\"]+)\)=\(.*\) already exists", detail))
    if match:
        column = match.group(1).split(",")[-1].strip().strip('"').split(".")[-1]
        field = column.replace("_id", " ID").replace("_", " ")
        if request.path.startswith("/api/"):
            message = ("That place in the rack has a cage already (someone may have just put one there), "
                       "so nothing was saved." if column in ("rack_col", "rack_row")
                       else f"That {field} is already used. Choose another.")
        else:
            message = (gettext("That place in the rack has a cage already (someone may have just put one there), so nothing was saved.") if column in ("rack_col", "rack_row")
                       else gettext("That %(field)s is already used. Choose another.", field=field))
    else:
        message = _said("That change conflicts with an existing record, so it was not saved.")
    app.logger.warning("integrity error on %s: %s", request.path, detail)
    if _wants_json():
        return jsonify({"ok": False, "error": message}), 409
    flash(message, "error")
    referrer = request.referrer or ""
    return redirect(referrer if referrer.startswith(request.host_url) else url_for("home_dashboard"))


@app.errorhandler(DataError)
def handle_data_error(error: DataError):
    """PostgreSQL enforces column sizes (SQLite ignores them), so a value
    longer than its column is refused there. Say so rather than 500."""
    detail = str(getattr(error, "orig", error))
    size = re.search(r"character varying\((\d+)\)", detail)
    if request.path.startswith("/api/"):
        message = (f"One of the values is longer than its field allows ({size.group(1)} characters), "
                   "so nothing was saved." if size else "One of the values is not valid for its field, so nothing was saved.")
    else:
        message = (gettext("One of the values is longer than its field allows (%(size)s characters), so nothing was saved.", size=size.group(1)) if size
                   else gettext("One of the values is not valid for its field, so nothing was saved."))
    app.logger.warning("data error on %s: %s", request.path, detail)
    if _wants_json():
        return jsonify({"ok": False, "error": message}), 409
    flash(message, "error")
    referrer = request.referrer or ""
    return redirect(referrer if referrer.startswith(request.host_url) else url_for("home_dashboard"))


@app.context_processor
def inject_icon():
    """`{{ icon('mouse') }}` renders one symbol from the sprite.

    Icons live in a single static/icons.svg sprite referenced with <use>,
    so they inherit currentColor, need no JavaScript, cost one cached
    request, and work offline in the packaged app.
    """
    from markupsafe import Markup

    from .icons import resolve

    def icon(name: str, extra: str = "") -> Markup:
        classes = ("icon " + extra).strip()
        return Markup(
            f'<svg class="{classes}" aria-hidden="true">'
            f'<use href="/static/icons.svg#{resolve(name)}"></use></svg>'
        )

    from .icons import PICKER_ICONS
    return {"icon": icon, "picker_icons": PICKER_ICONS}


@app.context_processor
def inject_life_stage():
    """`life_stage('mouse', days)` and `life_stage_title('mouse', stage)`:
    the colour of an animal's dot and what it means (app/life_stage.py)."""
    from . import life_stage
    return {"life_stage": life_stage.stage, "life_stage_title": life_stage.describe}


@app.context_processor
def inject_user():
    user = g.get("user")
    unread = 0
    if user is not None:
        with SessionLocal() as db_session:
            unread = notify.unread_count(db_session, user.username)
    return {"current_user": user, "min_password_length": security.MIN_PASSWORD_LENGTH,
            "sign_in_providers": oidc.provider_choices(), "notification_unread": unread,
            "lab_features": lab.request_features() if user is not None else {}}


@app.context_processor
def inject_appearance():
    """The person's app icon, and the accent colour that goes with it."""
    user = g.get("user")
    glyph, color = appearance.DEFAULT_GLYPH, appearance.DEFAULT_COLOR
    if user is not None:
        with SessionLocal() as db_session:
            glyph, color = appearance.get_choice(db_session, user.username)
    return {"app_icon_url": app_icon_url(glyph, color), "brand_css": appearance.brand_css(color)}


# ---------------------------------------------------------------------------
# Navigation model
#
# The rail, the "+" quick-launch menu and the workspace tab strip all need the
# same list of destinations (label + lucide icon + URL), so it is defined once
# here rather than repeated in base.html. `tab_icon_rules` is handed to
# tab-bar.js so a restored tab can pick the right glyph from its URL alone.
# ---------------------------------------------------------------------------

NAV_SECTIONS: list[dict] = [
    {
        "label": "Workspace",
        "links": [
            {"key": "home", "label": "Home", "icon": "home",
             "endpoint": "home_dashboard", "match": ("home_dashboard", "index")},
            {"key": "calendar", "label": "Calendar", "icon": "calendar",
             "endpoint": "calendar", "feature": "calendar"},
            {"key": "notebook", "label": "Notebook", "icon": "notebook", "feature": "notebook",
             "endpoint": "notebook", "match": ("notebook", "notebook_templates")},
            {"key": "utilities", "label": "Utilities", "icon": "calculator", "endpoint": "utilities",
             "hint": "Bench calculators and reference data"},
        ],
    },
    {
        "label": "Databases",
        "links": [
            {"key": "colony", "label": "Mouse colony", "short": "Mouse", "icon": "mouse",
             "endpoint": "colony", "args": {"view": "mice"}, "feature": "colony",
             "match": ("colony", "experiment_detail")},
            {"key": "zebrafish", "label": "Zebrafish", "short": "Fish", "icon": "fish",
             "endpoint": "zebrafish", "feature": "zebrafish", "match": ("zebrafish", "zebrafish_line_detail")},
            {"key": "plasmids", "label": "Plasmids", "icon": "plasmid", "feature": "plasmids",
             "endpoint": "plasmids", "match": ("plasmids", "plasmid_detail", "plasmid_page")},
            {"key": "new-db", "label": "Add database", "icon": "plus", "needs": "create_db",
             "hint": "Keep another kind of record: an organism, a stock collection or an inventory",
             "endpoint": "organisms.new_module", "match": ("organisms.new_module",)},
        ],
    },
]

# The foot of the sidebar: Settings, and one More menu for the pages not
# used every day (batch history for everyone; the admin's pages). Help and
# Feedback are a Help menu in the template. Utilities sits with Workspace.
NAV_FOOTER: list[dict] = [
    {"key": "settings", "label": "Settings", "icon": "settings", "endpoint": "settings",
     "match": ("settings", "admin_users")},
]
NAV_MORE: list[dict] = [
    {"key": "batches", "label": "Batch history", "icon": "layers", "endpoint": "batches_view",
     "hint": "Changes made many records at a time (Add many, bulk edits, imports), each with Undo"},
    {"key": "groups", "label": "Project groups", "icon": "users", "endpoint": "groups.page",
     "hint": "Who works together: groups share animals, stock, databases, to-dos and notebook pages"},
    {"key": "lab-setup", "label": "Lab setup", "icon": "sliders",
     "hint": "What the lab keeps, its name, and what members may do",
     "endpoint": "lab.setup", "admin_only": True},
    {"key": "admin-colony", "label": "Colony overview", "hint": "Every member's mice and cages at a glance",
     "icon": "list", "endpoint": "admin_colony_overview", "admin_only": True, "feature": "colony"},
    {"key": "audit", "label": "Audit log", "icon": "history", "endpoint": "audit_log_view",
     "hint": "Who changed what, and when", "admin_only": True},
    {"key": "users", "label": "Manage users", "icon": "users", "endpoint": "admin_users", "admin_only": True,
     "hint": "Approve people, roles, passwords"},
    {"key": "guests", "label": "Guests", "icon": "user", "endpoint": "guests.admin", "admin_only": True,
     "hint": "A pass for someone outside the lab"},
    {"key": "racks", "label": "Racks & boxes", "icon": "box", "endpoint": "admin_racks.index", "admin_only": True,
     "hint": "Who may change each rack, box and incubator"},
]

# Colony sub-views: label, icon and one-line description for the segmented
# tab row on the colony page. Keyed by the view keys in models.COLONY_VIEWS.
COLONY_VIEW_META: dict[str, dict[str, str]] = {
    "mice":        {"label": "Mice", "icon": "mouse",
                    "blurb": "Every mouse record, editable in place"},
    "cages":       {"label": "Cages", "icon": "cage",
                    "blurb": "Every cage, editable in place, with its mice and breeding actions"},
    "litters":     {"label": "Litters", "icon": "baby",
                    "blurb": "Cohorts and the dates derived from their DOB"},
    "breeders":    {"label": "Breeders", "icon": "heart",
                    "blurb": "Breeding cages and their productivity"},
    "experiments": {"label": "Experiments", "icon": "flask",
                    "blurb": "Cohorts assembled for a specific experiment"},
    "strains":     {"label": "Strains", "icon": "sitemap",
                    "blurb": "Strain and allele reference list"},
    "settings":    {"label": "Dropdowns", "icon": "sliders",
                    "blurb": "The choices offered in the colony's dropdowns (statuses, purposes, strains…)"},
}


# URL prefix -> lucide icon, longest prefix wins. Used for workspace tabs.
TAB_ICON_RULES: list[tuple[str, str]] = [
    ("/", "home"),
    ("/home", "home"),
    ("/calendar", "calendar"),
    ("/notebook", "notebook"),
    ("/colony", "mouse"),
    ("/colony/experiments", "flask"),
    ("/zebrafish", "fish"),
    ("/plasmids", "plasmid"),
    ("/samples", "vial"),
    ("/orders", "cart"),
    ("/utilities", "calculator"),
    ("/audit", "history"),
    ("/batches", "layers"),
    ("/admin/colony", "list"),
    ("/settings", "settings"),
    ("/admin/users", "users"),
    ("/groups", "users"),
    ("/organisms", "database"),
]


def _resolve_nav_item(item: dict, active_endpoint: str) -> dict | None:
    """Turn a NAV_SECTIONS entry into something the template can render, or
    None when the current user may not see it."""
    if item.get("admin_only") and getattr(g.get("user"), "role", None) != "admin":
        return None
    if item.get("feature") and not lab.request_features().get(item["feature"], True):
        return None
    if item.get("needs") == "create_db" and not _may_create_db():
        return None

    resolved = {
        "key": item["key"],
        "label": item["label"],
        "short": item.get("short", item["label"]),
        "icon": item["icon"],
        "hint": item.get("hint", ""),
        "soon": item.get("soon"),
        "url": None,
        "active": False,
    }
    endpoint = item.get("endpoint")
    if endpoint:
        resolved["url"] = url_for(endpoint, **item.get("args", {}))
        resolved["active"] = active_endpoint in item.get("match", (endpoint,))
    return resolved


def _may_create_db() -> bool:
    if "may_create_db" not in g:
        with SessionLocal() as db_session:
            g.may_create_db = lab.may_create_database(db_session)
    return g.may_create_db


def builtin_labels() -> dict[str, str]:
    """Names of the built-in databases (the lab may have renamed them)."""
    from . import inventory_service as inventories
    try:
        with SessionLocal() as db_session:
            return inventories.builtin_labels(db_session)
    except Exception:
        return {key: default for key, (default, _short) in inventories.BUILTIN_DATABASES.items()}


def _inventory_module_links() -> list[dict]:
    """Rail entries for the lab inventories (samples, orders, reagents…)."""
    from . import inventory_service as inventories
    from .icons import resolve as resolve_icon

    current_key = request.view_args.get("key") if request.view_args else None
    on_inventory = (request.endpoint or "").startswith("inventory.")
    links = []
    try:
        with SessionLocal() as db_session:
            for module in inventories.list_modules(db_session):
                if not lab.in_sidebar(module):
                    continue
                links.append({
                    "key": f"inventory:{module.key}", "label": module.label, "short": module.label,
                    "icon": resolve_icon(module.icon), "soon": None,
                    "url": url_for("inventory.module", key=module.key),
                    "active": on_inventory and current_key == module.key,
                    "personal": lab.is_personal(module),
                })
    except Exception:
        return []
    return links


def _stock_module_links() -> list[dict]:
    """Rail entries for the fly and worm vial/plate databases."""
    from . import stock_service as stocks
    from .icons import resolve as resolve_icon

    current_key = request.view_args.get("key") if request.view_args else None
    on_stocks = (request.endpoint or "").startswith("stocks.")
    links = []
    try:
        with SessionLocal() as db_session:
            for module in stocks.list_modules(db_session):
                if not lab.in_sidebar(module):
                    continue
                links.append({
                    "key": f"stock:{module.key}", "label": module.label, "short": module.label,
                    "icon": resolve_icon(module.icon), "soon": None,
                    "url": url_for("stocks.module", key=module.key),
                    "active": on_stocks and current_key == module.key,
                    "personal": lab.is_personal(module),
                })
    except Exception:
        return []
    return links


def _organism_module_links() -> list[dict]:
    """Rail entries for the configurable organism modules.

    These are rows in the database rather than entries in NAV_SECTIONS, so a
    lab that adds a species sees it in the sidebar immediately.
    """
    from . import organism_service as organisms
    from .icons import resolve as resolve_icon

    current_key = request.view_args.get("key") if request.view_args else None
    on_organisms = (request.endpoint or "").startswith("organisms.")
    links = []
    try:
        with SessionLocal() as db_session:
            for module in organisms.list_modules(db_session):
                if not lab.in_sidebar(module):
                    continue
                links.append({
                    "key": f"organism:{module.key}",
                    "label": module.label,
                    "short": module.label,
                    "icon": resolve_icon(module.icon),
                    "soon": None,
                    "url": url_for("organisms.module", key=module.key),
                    "active": on_organisms and current_key == module.key,
                    "personal": lab.is_personal(module),
                })
    except Exception:
        # A brand-new database may not have the tables yet; the rail should
        # still render.
        return []
    return links


@app.context_processor
def inject_nav():
    if g.get("user") is None:
        return {"nav_sections": [], "nav_footer": [], "nav_more": [], "tab_icon_rules": [],
                "colony_view_meta": COLONY_VIEW_META, "db_labels": {}}

    active = request.endpoint or ""
    g.db_labels = builtin_labels()
    sections = []
    tab_icon_rules = list(TAB_ICON_RULES)
    for section in NAV_SECTIONS:
        links = [r for r in (_resolve_nav_item(i, active) for i in section["links"]) if r]
        if section["label"] == "Databases":
            # Configurable modules sit with the built-in ones, above "Add".
            renamed = g.db_labels
            for link in links:
                if link["key"] in renamed and renamed[link["key"]] != link["label"]:
                    link["label"] = link["short"] = renamed[link["key"]]
            extras = _stock_module_links() + _organism_module_links() + _inventory_module_links()
            tail = [l for l in links if l["key"] in ("drosophila", "new-db")]
            head = [l for l in links if l["key"] not in ("drosophila", "new-db")]
            links = head + extras + [l for l in tail if l["key"] == "new-db"]
            # Each database's tabs show its own glyph, not the generic one.
            tab_icon_rules += [(l["url"], l["icon"]) for l in extras]

        if links:
            sections.append({"label": section["label"], "links": links})
    footer = [r for r in (_resolve_nav_item(i, active) for i in NAV_FOOTER) if r]
    more = [r for r in (_resolve_nav_item(i, active) for i in NAV_MORE) if r]
    return {
        "nav_sections": sections,
        "nav_footer": footer,
        "nav_more": more,
        "tab_icon_rules": tab_icon_rules,
        "colony_view_meta": COLONY_VIEW_META,
        "db_labels": g.db_labels,
    }


@app.context_processor
def inject_asset_version():
    """Expose `asset_version('relative/path')` to templates so we can bust the
    browser cache whenever a built JS bundle changes on disk."""
    import os as _os

    static_root = _os.path.join(_os.path.dirname(__file__), "static")

    def asset_version(rel_path: str) -> str:
        try:
            return str(int(_os.path.getmtime(_os.path.join(static_root, rel_path))))
        except OSError:
            return "0"

    return {"asset_version": asset_version}


def _get_user_directory():
    """Cache username -> {short_name, display_name} for the current request."""
    cache = g.get("user_directory")
    if cache is not None:
        return cache
    cache = {}
    try:
        with SessionLocal() as db_session:
            for u in db_session.scalars(select(UserAccount)).all():
                cache[u.username] = {"short": u.short_name or u.username[:5], "display": u.display_name or u.username}
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


def local_time(value):
    """A timestamp the app stamped (datetime.utcnow(): naive UTC) as the
    lab's own time (the server's TZ), for showing to people. Without it an
    evening's entry reads as tomorrow."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


app.jinja_env.filters["local"] = local_time


@app.template_filter("relative_day")
def relative_day_filter(value) -> str:
    """"today", "tomorrow", "in 3 d", "2 d ago", or a date further out."""
    if not value:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    days = (value - date.today()).days
    if days == 0:
        return gettext("today")
    if days == 1:
        return gettext("tomorrow")
    if days == -1:
        return gettext("yesterday")
    if 1 < days <= 13:
        return gettext("in %(n)s d", n=days)
    if -13 <= days < -1:
        return gettext("%(n)s d ago", n=-days)
    return fmt_day(value)


def _lab_date_style() -> str:
    if has_request_context():
        if "date_style" not in g:
            with SessionLocal() as db_session:
                g.date_style = lab.date_style(db_session)
        return g.date_style
    return "month"


def fmt_day(value, with_time: bool = False) -> str:
    """A date as the lab writes it (Lab setup → Dates): "Sep 26", "26 Sep"
    or "2026-09-26"; the year is added when it is not this year. Takes a
    date, a datetime or an ISO string; with_time adds "· 14:05"."""
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value) if "T" in value or " " in value else date.fromisoformat(value)
        except ValueError:
            return value
    moment = value if isinstance(value, datetime) else None
    day = value.date() if isinstance(value, datetime) else value
    style = _lab_date_style()
    if style == "iso":
        text = day.isoformat()
    elif i18n.current() == "zh":
        # A Chinese reader writes the month first, whatever the lab's style.
        text = f"{day.month}月{day.day}日" if day.year == date.today().year else f"{day.year}年{day.month}月{day.day}日"
    else:
        this_year = day.year == date.today().year
        if style == "day":
            text = f"{day.day} {day:%b}" + ("" if this_year else f" {day.year}")
        else:
            text = f"{day:%b} {day.day}" + ("" if this_year else f", {day.year}")
    if with_time and moment is not None:
        text += f" · {moment:%H:%M}"
    return text


app.jinja_env.filters["day"] = fmt_day


@app.template_filter("head")
def column_head(label) -> Markup:
    """A column heading, which the sheet writes in capitals: a micro sign
    kept small (capitalised, "µL" reads "ΜL", as if it were mL)."""
    text = str(escape(label or ""))
    if "µ" not in text:
        return Markup(text)
    # One span, so a flex heading keeps it as one piece of text.
    return Markup("<span>" + text.replace("µ", '<span class="normal-case">µ</span>') + "</span>")
app.jinja_env.filters["day_time"] = lambda value: fmt_day(value, with_time=True)


@app.template_filter("owner_short")
def owner_short_filter(name: str) -> str:
    if not name:
        return ""
    info = _get_user_directory().get(name)
    if info and info["short"]:
        return info["short"]
    return name[:5]


@app.template_filter("table_label")
def table_label_filter(name: str) -> str:
    return audit.table_label(name)


@app.template_filter("owner_badge")
def owner_badge_filter(name: str) -> Markup:
    if not name:
        return Markup("")
    info = _get_user_directory().get(name)
    if info and info["short"]:
        text = info["short"][:3].upper() if len(info["short"]) > 3 else info["short"].upper()
        tooltip = info["display"] or name
    else:
        cleaned = name.strip()
        parts = cleaned.split()
        if len(parts) >= 2:
            text = (parts[0][0] + parts[1][0]).upper()
        else:
            text = cleaned[:2].upper()
        tooltip = name
    hue, sat, light = _name_color(name)
    bg = f"hsl({hue}, {sat}%, 90%)"
    fg = f"hsl({hue}, {sat}%, {light}%)"
    # data-tooltip drives the CSS popover; aria-label keeps it accessible.
    # We deliberately avoid the native `title` attribute so the browser doesn't
    # show its own delayed tooltip on top of ours.
    return Markup(
        f'<span class="owner-badge" data-tooltip="{escape(tooltip)}" aria-label="{escape(tooltip)}" style="background:{bg};color:{fg};">{escape(text)}</span>'
    )


@app.template_filter("owner_with_badge")
def owner_with_badge_filter(name: str) -> Markup:
    """Badge + name, useful when both are needed in compact rows."""
    if not name:
        return Markup("")
    badge = owner_badge_filter(name)
    label = owner_short_filter(name)
    return Markup(f'<span class="owner-cell">{badge}<span class="owner-cell-name">{escape(label)}</span></span>')


def can_edit_mouse(mouse: MouseRecord) -> bool:
    """Delegates to the single access policy in app/access.py."""
    return access.can_edit_mouse(mouse)


def can_edit_cage(cage: CageRecord) -> bool:
    return access.can_edit_cage(cage)


def deny(record, view: str = "mice"):
    """Return a response when the user may not edit `record`, else None.

    Routes call this right after loading the record:

        blocked = deny(cage, "cages")
        if blocked:
            return blocked

    Keeping it a returned response rather than an abort lets the colony's
    autosave endpoints answer in the shape their caller expects.
    """
    if record is None:
        return None
    if isinstance(record, MouseRecord):
        allowed = access.can_edit_mouse(record)
    elif isinstance(record, CageRecord):
        allowed = access.can_edit_cage(record)
    else:
        allowed = access.can_edit(record)
    if allowed:
        return None
    flash(access.reason_denied(record), "error")
    return autosave_response(view)


def set_mouse_cage(mouse: MouseRecord, cage: CageRecord | None) -> None:
    """Put `mouse` in `cage` through the foreign-key column as well as the
    relationship. The audit listener records column changes only, so a
    move made through the relationship alone would be missing from /audit
    and batch undo could not put the mouse back."""
    mouse.cage = cage
    mouse.cage_id_fk = cage.id if cage is not None else None


def set_mouse_litter(mouse: MouseRecord, litter: LitterRecord | None) -> None:
    """As set_mouse_cage, for the litter (and so the date of birth)."""
    mouse.litter = litter
    mouse.litter_id_fk = litter.id if litter is not None else None


def automatic_litter(db_session, dob: date) -> LitterRecord:
    """A litter for mice given a date of birth and no litter: one per date
    per request, numbered like any other."""
    made = g.setdefault("_automatic_litters", {})
    litter = made.get(dob)
    if litter is None or litter not in db_session:
        litter = get_or_create_litter(db_session, next_litter_id(db_session), dob)
        made[dob] = litter
    return litter


def future_birth(form, field: str = "date_of_birth", what: str = "A date of birth") -> str | None:
    """The refusal for a birth date after today, or None. Nothing is born
    tomorrow; a future date is a typo (2062 for 2026, a month and day
    swapped) that would put the weaning, genotyping and age reminders on
    the wrong days."""
    born = parse_date(form.get(field)) if field in form else None
    if born is not None and born > date.today():
        if what == "A litter's birth date":
            return gettext("A litter's birth date can't be in the future (%(day)s). Nothing was saved.",
                           day=fmt_day(born))
        return gettext("A date of birth can't be in the future (%(day)s). Nothing was saved.", day=fmt_day(born))
    return None


def _keep_lines(stored: str | None, sent: str) -> str:
    """What a one-line cell sends back for a value with line breaks is the
    value without them (browsers drop them from an <input>): that is the
    stored value unchanged, not an edit. Saving another cell of the row
    must not squash a multi-line note into one line."""
    if stored and ("\n" in stored or "\r" in stored) and sent == re.sub(r"[\r\n]", "", stored).strip():
        return stored
    return sent


def populate_mouse_from_form(db_session, mouse: MouseRecord, form, preserve_owner_on_transfer: bool = True) -> tuple[str | None, str | None]:
    # A sheet row sends every cell, and with each a "<name>_was" copy of
    # what the row showed (form_changed): a cell it didn't change is left as
    # the database has it, so saving one cell of a row open since before a
    # colleague's edit doesn't quietly undo that edit. Dialogs send no
    # copies, so everything they show counts.
    original_owner = mouse.owner
    transfer_recipient = None
    litter_code = form.get("litter_id", "").strip()
    dob = parse_date(form.get("date_of_birth"))
    if not form_changed(form, "litter_id", "date_of_birth"):
        pass
    elif litter_code:
        set_mouse_litter(mouse, get_or_create_litter(db_session, litter_code, dob))
    elif dob is not None:
        # A mouse's date of birth lives on its litter. Given a date and no
        # litter (Add many, a spreadsheet with a dob column but no litter),
        # the mouse gets an automatic litter of that date, shared by the
        # mice born that day in this one save, so the date is never lost.
        set_mouse_litter(mouse, automatic_litter(db_session, dob))
    elif "litter_id" in form or "date_of_birth" in form:
        set_mouse_litter(mouse, None)

    # A form that does not carry the cage (a cage card's mouse row, which
    # only shows the mouse's own fields) leaves the mouse where it is.
    if ("cage_id" in form and form_changed(form, "cage_id")) or form.get("auto_new_cage") == "1":
        cage_input = form.get("cage_id", "").strip()
        existing_cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == cage_input)) if cage_input else None
        if existing_cage is not None and existing_cage is not mouse.cage and not access.can_edit_cage(existing_cage):
            # The same rule as "Add existing mouse" on the cage: a private
            # cage takes mice only from its owner (or an admin).
            flash(gettext("Cage %(cage)s is %(owner)s’s private cage, so the mouse stays where it is. Ask them, or have the cage marked shared.", cage=existing_cage.cage_id,
                          owner=existing_cage.owner or gettext("someone else")), "error")
        elif cage_input:
            set_mouse_cage(mouse, get_or_create_cage(db_session, cage_input))
        elif form.get("auto_new_cage") == "1":
            set_mouse_cage(mouse, get_or_create_cage(db_session, "new"))
        else:
            set_mouse_cage(mouse, None)

    if mouse.cage is not None:
        # Rack, position and location note belong to the cage; see form_changed.
        if form_changed(form, "cage_location"):
            mouse.cage.cage_location = form.get("cage_location", "").strip()
        if "cage_rack" in form and form_changed(form, "cage_rack", "cage_position"):
            error = apply_cage_position(db_session, mouse.cage, form.get("cage_rack"), form.get("cage_position"))
            if error:
                flash(error, "error")

    stored = [mouse.transgene_1, mouse.transgene_2, mouse.transgene_3, mouse.transgene_4]
    if form_changed(form, *(f"transgene_{n}" for n in range(1, 5))):
        transgenes = [_keep_lines(old, new) for old, new in zip(stored, transgene_values_from_form(form))]
        sync_mouse_transgenes(mouse, transgenes)
    if form_changed(form, "gender"):
        mouse.gender = form.get("gender", "").strip()
    previous_status = mouse.status
    if form_changed(form, "status"):
        mouse.status = form.get("status", "").strip()
    status_normalized = (mouse.status or "").lower()
    requested_owner = form.get("owner", "").strip() if form_changed(form, "owner") else (mouse.owner or "")
    if form_changed(form, "note"):
        mouse.note = _keep_lines(mouse.note, form.get("note", "").strip())
    # Absent (a cage card does not show it) or unchanged from its `_was`
    # copy, the stored date stands; the status rules below still stamp or
    # clear it when the status crosses into or out of an end status.
    if form_changed(form, "date_of_death"):
        mouse.date_of_death = parse_date(form.get("date_of_death"))

    lab_users = set(current_lab_usernames(db_session))
    apply_status_rules(mouse, previous_status)

    if status_normalized == "transfer" and requested_owner and requested_owner in lab_users and requested_owner != original_owner:
        transfer_recipient = requested_owner
        if preserve_owner_on_transfer:
            mouse.owner = original_owner
            transfer_note = f"Transferred to {requested_owner} on {date.today().isoformat()}."
            mouse.note = f"{mouse.note} {transfer_note}".strip()
        else:
            mouse.owner = requested_owner
    else:
        mouse.owner = requested_owner

    return transfer_recipient, original_owner


def create_transfer_copy(db_session, source_mouse: MouseRecord, recipient_username: str, sender_username: str | None) -> MouseRecord:
    copied_mouse = MouseRecord(
        mouse_id=next_mouse_id(db_session),
        gender=source_mouse.gender,
        status="experiment",
        owner=recipient_username,
        note=f"Transferred from {sender_username or source_mouse.owner} on {date.today().isoformat()}.",
        date_of_death=None,
    )
    set_mouse_litter(copied_mouse, source_mouse.litter)
    set_mouse_cage(copied_mouse, get_or_create_cage(db_session, "new"))
    sync_mouse_transgenes(
        copied_mouse,
        [source_mouse.transgene_1, source_mouse.transgene_2, source_mouse.transgene_3, source_mouse.transgene_4],
    )
    db_session.add(copied_mouse)
    add_notification(
        db_session,
        recipient_username,
        title="Mouse transfer received",
        message="Mouse %(mouse)s was transferred to you. A new record %(copy)s was created in your colony.",
        message_values={"mouse": source_mouse.mouse_id, "copy": copied_mouse.mouse_id},
        category="transfer",
        link=url_for("colony", view="mice", scope="mine"),
        actor=sender_username or "",
    )
    return copied_mouse


def _merged_choices(*groups) -> list[str]:
    """Unique non-empty values in first-seen order, compared without case,
    so the built-in values lead and a lab's own presets follow."""
    seen, out = set(), []
    for group in groups:
        for value in group:
            value = (value or "").strip()
            if value and value.lower() not in seen:
                seen.add(value.lower())
                out.append(value)
    return out


def mouse_sheet_meta(mouse_rows: list[dict], dropdowns: dict, racks=()) -> dict:
    """What the mouse sheet needs beyond the rows themselves: the choices
    for its dropdown cells, and which transgene columns hold anything (an
    empty TG3/TG4 starts hidden, and one click brings it back)."""
    return {
        "status_choices": _merged_choices(
            MOUSE_STATUS_OPTIONS, dropdowns.get("status", []),
            (r["status"] for r in mouse_rows)),
        "gender_choices": _merged_choices(
            ["F", "M", "Unknown"], dropdowns.get("gender", []),
            (r["gender"] for r in mouse_rows)),
        "tg_used": [any(r[f"transgene_{n}"] for r in mouse_rows) for n in range(1, 5)],
        "active_count": sum(1 for r in mouse_rows if r["active"]),
        "racks": [{"id": r.id, "name": r.name} for r in racks],
        "location_notes_used": any(r["cage_location"] for r in mouse_rows),
    }


# Cage purposes offered on the cage sheet before the lab's own presets.
CAGE_PURPOSE_CHOICES = ["Experiments", "Breeding", "Breeder", "Stock", "Retired"]
# A litter still counts as the cage's pups up to this age (days).
PUP_AGE_DAYS = 28


def _sex_label(females: int, males: int, other: int) -> str:
    """"2♀ 1♂" for the living mice in a cage; unknown sex shown as "?"."""
    parts = [f"{n}{sign}" for n, sign in ((females, "♀"), (males, "♂"), (other, "?")) if n]
    return " ".join(parts)


def cage_pup_litter(cage) -> LitterRecord | None:
    """The youngest litter among the cage's living mice, if it is still
    pups (born within PUP_AGE_DAYS)."""
    today = date.today()
    litters = {m.litter.id: m.litter for m in cage.mice
               if mouse_is_active(m) and m.litter is not None and m.litter.date_of_birth
               and m.litter.weaned_on is None
               and 0 <= (today - m.litter.date_of_birth).days <= PUP_AGE_DAYS}
    return max(litters.values(), key=lambda lit: lit.date_of_birth) if litters else None


def cage_wean_due(cage) -> tuple[str, str]:
    """(date, state): the P21 weaning day from the cage's litter-born date,
    else from its pups' litter; state is "overdue", "soon" (within 3 days)
    or ""."""
    born = cage.date_give_birth
    if born is None:
        litter = cage_pup_litter(cage)
        born = litter.date_of_birth if litter else None
    if born is None:
        return "", ""
    due = born + timedelta(days=WEAN_OFFSET_DAYS)
    left = (due - date.today()).days
    return due.isoformat(), ("overdue" if left < 0 else "soon" if left <= 3 else "")


def cage_genotype_auto(cage) -> str:
    """What the living mice carry, most common first: "Ai14; Cre ×2 · Ai14"."""
    counts: dict[str, int] = {}
    for mouse in cage.mice:
        if mouse_is_active(mouse):
            key = (mouse.genotype or "").strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return " · ".join(f"{name} ×{n}" if n > 1 else name for name, n in ordered)


def share_lock(cage) -> str:
    """Why this person can't make the cage shared or personal ("" if they
    can): its owner or an admin decides."""
    if not access.can_set_sharing(cage):
        return gettext("Only %(owner)s or an admin can change this", owner=cage.owner or gettext("its owner"))
    return ""


def cage_sheet_values(cage) -> dict:
    """The cage's cells as the sheet shows them. The update route returns
    these after a save; static/sheet.js writes them back into the row and
    refreshes each `<name>_was` copy."""
    wean, wean_state = cage_wean_due(cage)
    return {
        "cage_id": cage.cage_id,
        "rack_id": cage.rack_id_fk or "",
        "position": cage_position_label(cage),
        "purpose": cage.purpose or "",
        "owner": cage.owner or "",
        "is_shared": project_groups.record_value(cage) if access.is_shared_cage(cage) else "0",
        "cage_location": cage.cage_location or "",
        "genotype_summary": cage.genotype_summary or "",
        "notes": cage.notes or "",
        "date_give_birth": cage.date_give_birth.isoformat() if cage.date_give_birth else "",
        "wean_due": wean,
        "wean_state": wean_state,
        # Why the Shared cell can't be changed here, if it can't: only its
        # owner or an admin shares a cage.
        "share_lock": share_lock(cage),
        "breeding": "1" if is_breeder_purpose(cage.purpose) else "0",
    }


def cage_purpose_chips(cage_rows) -> list[tuple[str, str, int]]:
    """(key, label, how many) for each purpose the listed cages have, most
    used first; the key is the purpose in lower case, as rows carry it."""
    counts: dict[str, list] = {}
    for r in cage_rows:
        label = (r.get("purpose") or "").strip()
        if label:
            entry = counts.setdefault(label.lower(), [label, 0])
            entry[1] += 1
    return sorted(((key, label, n) for key, (label, n) in counts.items()), key=lambda c: (-c[2], c[1].lower()))


def cage_sheet_row(cage) -> dict:
    """One row of the cage sheet, with the cage's mice for its sub-row."""
    derived = cage_derived_dates(cage)
    living = [m for m in cage.mice if mouse_is_active(m)]
    females = sum(1 for m in living if m.gender == "F")
    males = sum(1 for m in living if m.gender == "M")
    ordered = sorted(cage.mice, key=lambda item: item.mouse_id)
    pups = cage_pup_litter(cage)
    values = cage_sheet_values(cage)
    # What the Wean dialog starts from: the pups' birth date (it asks
    # before weaning under P18) and the pups themselves, by sex.
    wean_born = cage.date_give_birth or (pups.date_of_birth if pups else None)
    wean_pups: dict[str, list[str]] = {"F": [], "M": [], "": []}
    for m in ordered:
        if (mouse_is_active(m) and wean_born and m.litter is not None
                and m.litter.date_of_birth == wean_born and m.litter.weaned_on is None):
            wean_pups[m.gender if m.gender in ("F", "M") else ""].append(str(m.mouse_id))
    role = getattr(g.user, "role", None) if g.user else None
    return {
        **values,
        "id": cage.id,
        "cage_id": cage.cage_id,
        "active": cage_is_active(cage),
        "rack_name": cage.rack.name if cage.rack else "",
        "genotyping_date": derived["genotyping_date"],
        "weaning_date": derived["weaning_date"],
        "card_id": cage.card_id,
        "genotype_auto": cage_genotype_auto(cage),
        "location_detail": cage.location_detail,
        "room": cage.room,
        "shared": access.is_shared_cage(cage),
        "share_value": project_groups.record_value(cage) if access.is_shared_cage(cage) else "0",
        "share_group_id": cage.share_group_id,
        "mine": access.owns(cage),
        "can_edit": access.can_edit_cage(cage),
        # Giving it to someone else is its owner's, an admin's or animal care's.
        "can_reassign": access.can_manage(cage) or access.is_care(),
        "can_breed": is_breeder_purpose(cage.purpose),
        "live_count": len(living),
        "total_count": len(cage.mice),
        "sex_label": _sex_label(females, males, len(living) - females - males),
        "pup_litter": pups.litter_id if pups else "",
        "pup_dob": pups.date_of_birth.isoformat() if pups else "",
        "wean_born": wean_born.isoformat() if wean_born else "",
        "wean_age": (date.today() - wean_born).days if wean_born else "",
        "wean_pups": {sex: ", ".join(ids) for sex, ids in wean_pups.items()},
        "default_father": next((str(m.mouse_id) for m in ordered if m.gender == "M"), ""),
        "default_mother": next((str(m.mouse_id) for m in ordered if m.gender == "F"), ""),
        "mice": [dict(mouse_display_row(m, access.username(), role), editable=can_edit_mouse(m))
                 for m in ordered],
        "payload": {
            "id": cage.id, "_label": cage.cage_id, "_locked": not access.can_edit_cage(cage),
            "rack_id": cage.rack_id_fk or "", "position": values["position"],
            "cage_location": cage.cage_location, "purpose": cage.purpose, "room": cage.room,
            "card_id": cage.card_id, "genotype_summary": cage.genotype_summary,
            "location_detail": cage.location_detail, "notes": cage.notes,
            "date_give_birth": values["date_give_birth"],
        },
    }


RECENT_DAYS = 90   # ended mice, empty cages and old litters shown without "Show all"


def colony_context(active_view: str, scope: str = access.DEFAULT_SCOPE, show_ended: bool = False) -> dict[str, object]:
    """Build the colony page context.

    `scope` filters which slice of the colony is listed — your own animals,
    the shared cages, or everything. It is a view filter only: what
    you may *edit* is decided per record by app/access.py, and is the same
    whichever scope you are looking at.

    Only the open tab's rows are built, with their cages, racks and litters
    loaded in a few queries rather than one per row. A colony keeps every
    mouse it ever had, so the Mice, Cages and Litters tabs show the living
    and what ended in the last RECENT_DAYS days; `show_ended` shows it all.
    """
    from sqlalchemy.orm import selectinload
    me = g.user.username if g.user else ""
    recent = date.today() - timedelta(days=RECENT_DAYS)
    with SessionLocal() as db_session:
        count_of = lambda stmt: db_session.scalar(stmt) or 0  # noqa: E731
        totals = {
            "all_mice": count_of(select(func.count(MouseRecord.id))),
            "all_cages": count_of(select(func.count(CageRecord.id))),
            "my_mice": count_of(select(func.count(MouseRecord.id)).where(MouseRecord.owner == me)),
            "my_cages": count_of(select(func.count(CageRecord.id)).where(CageRecord.owner == me)),
            "shared_cages": count_of(select(func.count(CageRecord.id)).where(CageRecord.is_shared.is_(True))),
        }
        my_groups = project_groups.ids_of()
        if my_groups:
            group_cages = select(CageRecord.id).where(
                CageRecord.is_shared.is_(True), CageRecord.share_group_id.in_(sorted(my_groups)))
            totals["group_mice"] = count_of(select(func.count(MouseRecord.id)).where(
                MouseRecord.owner.in_(sorted(project_groups.colleagues())) | MouseRecord.cage_id_fk.in_(group_cages)))
        mice, hidden_mice = [], 0
        if active_view == "mice":
            query = select(MouseRecord).options(selectinload(MouseRecord.cage).selectinload(CageRecord.rack),
                                                selectinload(MouseRecord.litter)).order_by(MouseRecord.mouse_id)
            if not show_ended:
                hidden_mice = count_of(select(func.count(MouseRecord.id)).where(MouseRecord.date_of_death < recent))
                query = query.where(MouseRecord.date_of_death.is_(None) | (MouseRecord.date_of_death >= recent))
            mice = [m for m in db_session.scalars(query).all()
                    if access.in_scope(m, scope, shared=access.cage_shared_with(m.cage),
                                       group_id=access.cage_group(m.cage))]
        cages, hidden_cages = [], 0
        if active_view == "cages":
            all_cages = db_session.scalars(select(CageRecord).options(
                selectinload(CageRecord.mice).selectinload(MouseRecord.litter),
                selectinload(CageRecord.rack)).order_by(CageRecord.cage_id)).all()
            if not show_ended:
                keep = [c for c in all_cages if cage_is_active(c) or c.created_at and c.created_at.date() >= recent
                        or any(m.date_of_death and m.date_of_death >= recent for m in c.mice)]
                hidden_cages = len(all_cages) - len(keep)
                all_cages = keep
            cages = [c for c in all_cages if access.in_scope(c, scope, shared=access.cage_shared_with(c),
                                                             group_id=access.cage_group(c))]
        litters, hidden_litters = [], 0
        if active_view == "litters":
            litter_query = select(LitterRecord).options(
                selectinload(LitterRecord.mice).selectinload(MouseRecord.cage)).order_by(LitterRecord.litter_id)
            if not show_ended:
                year_ago = date.today() - timedelta(days=365)
                with_living = select(MouseRecord.litter_id_fk).where(MouseRecord.date_of_death.is_(None))
                visible = (LitterRecord.date_of_birth.is_(None) | (LitterRecord.date_of_birth >= year_ago)
                           | LitterRecord.id.in_(with_living))
                hidden_litters = count_of(select(func.count(LitterRecord.id)).where(~visible))
                litter_query = litter_query.where(visible)
            # L-2 before L-10: by number, not letter by letter.
            litters = sorted(db_session.scalars(litter_query).all(), key=lambda l: positions.place_order(l.litter_id or ""))
        strains = db_session.scalars(select(StrainRecord).order_by(StrainRecord.strain_name)).all()
        dropdowns = dropdown_options_map(db_session)
        dropdown_records = dropdown_records_map(db_session)
        usernames = current_lab_usernames(db_session)
        notifications = recent_notifications(db_session, g.user.username if g.user else "", limit=10) if g.user else []

        mouse_rows = [mouse_display_row(mouse, g.user.username if g.user else None, g.user.role if g.user else None) for mouse in mice]
        cage_racks = mouse_rack_payload(db_session, cages) if active_view == "cages" else None
        sheet_racks = mouse_racks(db_session)
        # The sheet renders rows you may not edit as read-only, using the
        # same rule the update routes enforce, rather than letting an edit
        # appear to save and then be refused.
        for mouse, row in zip(mice, mouse_rows):
            row["editable"] = can_edit_mouse(mouse)
        cage_rows = [cage_sheet_row(cage) for cage in cages]
        # The Experiments tab's "start with a cage" only lists cage numbers.
        cage_ids = list(db_session.scalars(select(CageRecord.cage_id).where(
            CageRecord.id.in_(select(MouseRecord.cage_id_fk).where(MouseRecord.date_of_death.is_(None))))
            .order_by(CageRecord.cage_id))) if active_view == "experiments" else []
        litter_rows = []
        for litter in litters:
            litter_rows.append(
                {
                    "id": litter.id,
                    "litter_id": litter.litter_id,
                    "date_of_birth": litter.date_of_birth.isoformat() if litter.date_of_birth else "",
                    "cohort_name": litter.cohort_name,
                    "father_info": litter.father_info,
                    "mother_info": litter.mother_info,
                    "total_pups": litter.total_pups,
                    "notes": litter.notes,
                    "editable": access.can_edit_litter(litter),
                    "mice": [mouse_display_row(mouse, g.user.username, g.user.role) for mouse in sorted(litter.mice, key=lambda item: item.mouse_id)],
                }
            )
        strain_rows = [
            {
                "id": strain.id,
                "strain_number": strain.strain_number,
                "strain_name": strain.strain_name,
                "strain_background": strain.strain_background,
                "supplier": strain.supplier,
                "description": strain.description,
                "created_by": strain.created_by,
                "editable": access.can_edit_strain(strain),
            }
            for strain in strains
        ]
        breeder_rows, breeder_summary_rows = [], []
        if active_view == "breeders":
            breeder_rows = breeder_mice(db_session, g.user.username if g.user else None, g.user.role if g.user else None)
            breeder_summary_rows = breeder_summary(db_session)
        next_mouse_id_value = next_mouse_id(db_session)
        next_cage_id_value = next_cage_id(db_session)
        next_litter_id_value = next_litter_id(db_session)
        # The mice view needs the open experiments too: the selection bar
        # offers "add to experiment", so the list has to be there even when
        # you are not on the experiments tab.
        open_experiments = []
        if active_view in ("mice", "cages", "breeders"):
            open_experiments = [
                {"id": e.id, "name": e.name}
                for e in db_session.scalars(
                    select(Experiment)
                    .where(Experiment.status.in_(["active", "paused"]), Experiment.db == "colony")
                    .order_by(Experiment.name)
                ).all()
            ]

        experiments_list = []
        if active_view == "experiments":
            experiments = db_session.scalars(
                select(Experiment).where(Experiment.db == "colony")
                .order_by(Experiment.status, Experiment.created_at.desc())
            ).all()
            for exp in experiments:
                experiments_list.append({
                    "id": exp.id,
                    "name": exp.name,
                    "description": exp.description,
                    "status": exp.status,
                    "owner": exp.owner_username,
                    "editable": access.can_edit_experiment(exp),
                    "member_count": len(exp.memberships),
                    "start_date": i18n.strftime(exp.start_date, "%b %d, %Y") if exp.start_date else "",
                    "end_date": i18n.strftime(exp.end_date, "%b %d, %Y") if exp.end_date else "",
                })

    return {
        "active_view": active_view,
        "colony_views": COLONY_VIEWS,
        "mouse_rows": mouse_rows,
        "mouse_sheet": mouse_sheet_meta(mouse_rows, dropdowns, sheet_racks),
        "cage_racks": cage_racks,
        "cage_rows": cage_rows,
        "cage_sheet": {
            "purpose_choices": _merged_choices(CAGE_PURPOSE_CHOICES, dropdowns.get("purpose", []),
                                               (r["purpose"] for r in cage_rows)),
            "active_count": sum(1 for r in cage_rows if r["active"]),
            "breeding_count": sum(1 for r in cage_rows if r["can_breed"]),
            "mine_count": sum(1 for r in cage_rows if r["mine"]),
            # A chip for each purpose the cages have: all the experiment
            # cages, all the breeder ones…
            "purposes": cage_purpose_chips(cage_rows),
            "location_notes_used": any(r["cage_location"] for r in cage_rows),
        },
        "can_edit_presets": access.can_edit_presets(),
        "litter_rows": litter_rows,
        "cage_ids": cage_ids,
        "hidden": {"mice": hidden_mice, "cages": hidden_cages, "litters": hidden_litters,
                   "show_ended": show_ended, "days": RECENT_DAYS},
        "strain_rows": strain_rows,
        "dropdowns": dropdowns,
        "dropdown_records": dropdown_records,
        "next_mouse_id_value": next_mouse_id_value,
        "next_cage_id_value": next_cage_id_value,
        "next_litter_id_value": next_litter_id_value,
        "usernames": usernames,
        "notifications": notifications,
        "status_options": MOUSE_STATUS_OPTIONS,
        "breeder_rows": breeder_rows,
        "breeder_summary": breeder_summary_rows,
        "experiments_list": experiments_list,
        "open_experiments": open_experiments,
        "totals": totals,
        "wean_offset_days": WEAN_OFFSET_DAYS,
        "min_wean_age_days": MIN_WEAN_AGE_DAYS,
        "cage_geno_offset_days": genotyping_offset(),
    }


@app.route("/")
def index():
    if g.user is None:
        return hello()
    # If the user picked a default landing page (settings → default_landing),
    # honor it. Otherwise show the dashboard.
    default_landing = (g.user.default_landing or "").strip()
    if default_landing and default_landing in ALLOWED_LANDING_ENDPOINTS \
            and lab.request_features().get(LANDING_FEATURES.get(default_landing, ""), True):
        return redirect(url_for(default_landing))
    return redirect(url_for("home_dashboard"))


def hello():
    """The first page someone sees before signing in: what BioManager is,
    what this lab keeps in it, where the guide is, and the way in. On a
    brand-new installation it leads to creating the first (admin) account."""
    from . import inventory_service as inventories
    from . import organism_service
    from . import stock_service

    with SessionLocal() as db_session:
        first_account = db_session.scalar(select(func.count(UserAccount.id))) == 0
        labels = inventories.builtin_labels(db_session)
        features = lab.features_on(db_session)
        # The lab's databases only: personal ones stay out of sight (app/lab.py).
        databases = [{"label": labels[key], "icon": lab.FEATURES[key].icon}
                     for key in ("colony", "zebrafish", "plasmids") if features.get(key)]
        for module in stock_service.list_modules(db_session):
            databases.append({"label": module.label, "icon": stock_service.view(module).icon})
        for module in organism_service.list_modules(db_session):
            databases.append({"label": module.label, "icon": module.icon or "paw"})
        for module in inventories.list_modules(db_session):
            databases.append({"label": module.label, "icon": inventories.view(module).icon})
        lab_title = lab.lab_name(db_session)
    return render_template(
        "hello.html", first_account=first_account, databases=databases, lab_title=lab_title,
        functions=[key for key in ("calendar", "notebook") if features.get(key)],
        needs_setup_code=first_account and security.setup_code_required(),
        guide_url=lab.guide_url(),
    )


@app.route("/home")
@login_required
def home_dashboard():
    """Home dashboard: counts, sac reminders, upcoming weanings, genotyping
    queue, recent orders. Read-only — every card links into the relevant
    page for actual edits."""
    today = date.today()
    week_ahead = today + timedelta(days=7)

    with SessionLocal() as db_session:
        # ---- Counts ------------------------------------------------------
        total_mice = db_session.scalar(select(func.count(MouseRecord.id))) or 0
        # "Active" by the same rule as the mouse sheet's green dot: no date
        # of death and not in an end status (sac, dead…) or transferred.
        active_mice = sum(
            1 for mouse in db_session.scalars(
                select(MouseRecord).where(MouseRecord.date_of_death.is_(None))).all()
            if mouse_is_active(mouse))
        my_mice = db_session.scalar(
            select(func.count(MouseRecord.id)).where(MouseRecord.owner == g.user.username)
        ) or 0
        from . import inventory_service as _inv
        order_modules = [m.id for m in _inv.list_modules(db_session) if m.kind == "orders"]
        # Each orders inventory's own open statuses, not a hard-coded pair.
        from . import inventory_service as inventories
        pending_orders = inventories.open_order_count(db_session)
        restock = inventories.attention_items(db_session)
        notebook_pages = db_session.scalar(
            select(func.count(NotebookPage.id))
            .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
            .where(NotebookTab.owner_username == g.user.username)
        ) or 0

        # ---- Mice older than 30 weeks (sac candidates) -------------------
        # Mouse age comes from its litter's date_of_birth. Active mice only.
        old_mice = db_session.scalars(
            select(MouseRecord)
            .join(LitterRecord, MouseRecord.litter_id_fk == LitterRecord.id)
            .where(MouseRecord.date_of_death.is_(None))
            .where(LitterRecord.date_of_birth.is_not(None))
            .where(LitterRecord.date_of_birth <= today - timedelta(days=210))
            .order_by(LitterRecord.date_of_birth.asc())
            .limit(15)
        ).all()
        sac_candidates = []
        for mouse in old_mice:
            dob = mouse.litter.date_of_birth if mouse.litter else None
            if not dob:
                continue
            age_weeks = (today - dob).days // 7
            sac_candidates.append({
                "mouse_id": mouse.mouse_id,
                "gender": mouse.gender,
                "genotype": mouse.genotype,
                "owner": mouse.owner,
                "status": mouse.status,
                "age_weeks": age_weeks,
                "cage_id": mouse.cage.cage_id if mouse.cage else "",
            })

        # ---- Upcoming weanings: litters whose weaning day (DOB + P21, the
        # same WEAN_OFFSET_DAYS the cage cards use) is within ±7 days -------
        weanings = []
        for item in weaning_due(db_session, today - timedelta(days=7), today + timedelta(days=7))[:10]:
            days_to_wean = (item["due"] - today).days
            weanings.append({
                "title": weaning_title(item),
                "cage_row_id": item["cage"].id if item["cage"] else None,
                "dob": fmt_day(item["born"]),
                "wean_date": fmt_day(item["due"]),
                "days_to_wean": days_to_wean,
                "overdue": days_to_wean < 0,
                "pups": item["pups"],
                "cohort": item["litter"].cohort_name if item["litter"] else "",
            })

        # ---- Genotyping queue: mice with status='geno' OR recently born
        # (over 2 weeks old, no genotype set) ------------------------------
        geno_status_mice = db_session.scalars(
            select(MouseRecord)
            .where(MouseRecord.date_of_death.is_(None))
            .where(MouseRecord.status == "geno")
            .order_by(MouseRecord.mouse_id.desc())
            .limit(10)
        ).all()
        geno_queue = [{
            "mouse_id": m.mouse_id,
            "gender": m.gender,
            "owner": m.owner,
            "cage_id": m.cage.cage_id if m.cage else "",
            "genotype": m.genotype,
            "reason": "status=geno",
        } for m in geno_status_mice]

        # ---- Recent orders ------------------------------------------------
        recent_orders = db_session.scalars(
            select(InventoryItem).where(InventoryItem.module_id_fk.in_(order_modules))
            .order_by(InventoryItem.created_at.desc()).limit(6)
        ).all()
        orders_list = [{
            "id": o.number,
            "vendor": o.vendor,
            "item": o.name,
            "status": o.status,
            "qty": o.quantity,
            "created_at": i18n.strftime(local_time(o.created_at), "%b %d, %Y"),
        } for o in recent_orders]

        # ---- Upcoming calendar events ------------------------------------
        upcoming_events = db_session.scalars(
            select(CalendarEvent)
            .where(lab_calendar.event_visible_clause())
            .where(CalendarEvent.event_date >= today)
            .where(CalendarEvent.event_date <= today + timedelta(days=14))
            .order_by(CalendarEvent.event_date.asc())
            .limit(6)
        ).all()
        events_list = [{
            "title": e.title,
            "event_date": e.event_date,
            "days_until": (e.event_date - today).days,
            "event_type": e.event_type,
        } for e in upcoming_events]

    # Fly / worm work due soon: flips, egg collections, shifts, scoring.
    from . import stock_service
    stock_due = []
    with SessionLocal() as db_session:
        for module in stock_service.list_modules(db_session):
            mv = stock_service.view(module)
            for item in stock_service.schedule(db_session, mv, today, horizon=2):
                stock_due.append({"module": mv.label, "key": mv.key, "icon": mv.icon, "title": item["title"],
                                  "due": item["due"], "overdue": item["overdue"], "is_today": item["today"], "kind": item["kind"]})
    stock_due.sort(key=lambda i: i["due"])

    # Schedule items from the configurable organism databases (wean, retire…).
    from . import organism_service
    with SessionLocal() as db_session:
        organism_due = organism_service.home_due(db_session, horizon_days=2)
        db_session.commit()

    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")

    # Which cards the lab's databases call for (app/lab.py): only what the
    # lab uses, plus this person's own databases.
    from . import inventory_service as _inv
    with SessionLocal() as db_session:
        visible_inventories = _inv.list_modules(db_session)
        has_orders = any(m.kind == "orders" for m in visible_inventories)
        has_restock = any(m.kind in _inv.RESTOCK_KINDS for m in visible_inventories)
        has_stocks = bool(stock_service.list_modules(db_session))
        setup_needed = g.user.role == "admin" and not lab.setup_done(db_session)
        if g.user.role == "admin":
            notify.settle_signups(db_session)
        for_you = [{"id": n.id, "title": n.title, "created_at": n.created_at}
                   for n in notify.recent(db_session, g.user.username, limit=5, unread_only=True)]

    # Classic (the cards above) or one of the other layouts (app/home_layouts.py),
    # which draw the same work from one agenda.
    zebrafish_due = zebrafish_home_summary()
    with SessionLocal() as db_session:
        home_cards = home_layouts.get_cards(db_session, g.user.username)
        offered = home_layouts.offered_cards(lab.request_features(), {
            "has_orders": has_orders, "has_restock": has_restock, "has_stocks": has_stocks})
        shown = {c["key"] for c in offered} - set(home_cards["hidden"])
        extra = _home_extra_cards(db_session, shown, today)
        home_layout = home_layouts.get_layout(db_session, g.user.username)
        layout_view = None
        if home_layout != "classic":
            span = home_layouts.span_arg(request.args.get("span"))
            horizon = span if home_layout == "tracks" else home_layouts.PULL_DAYS
            agenda, track_meta = home_layouts.build_agenda(
                db_session, today, horizon, lab.request_features(), zebrafish_due,
                colony_label=builtin_labels().get("colony", "Mouse colony"))
            if home_layout == "tracks":
                layout_view = home_layouts.tracks_view(agenda, track_meta, today, span)
            else:
                layout_view = home_layouts.freezer_view(db_session, agenda, today, g.user.username)
            db_session.commit()

    return render_template(
        "home.html",
        home_cards=home_cards,
        offered_cards=offered,
        shown_cards=shown,
        **extra,
        home_layout=home_layout,
        layout_choices=home_layouts.LAYOUTS,
        layout_view=layout_view,
        has_orders=has_orders,
        has_restock=has_restock,
        has_stocks=has_stocks,
        setup_needed=setup_needed,
        for_you=for_you,
        zebrafish_due=zebrafish_due,
        stock_due=stock_due[:12],
        stock_due_total=len(stock_due),
        organism_due=organism_due[:12],
        organism_due_total=len(organism_due),
        greeting=greeting,
        today_str=i18n.strftime(today, "%A, %b %d, %Y"),
        counts={
            "total_mice": total_mice,
            "active_mice": active_mice,
            "my_mice": my_mice,
            "pending_orders": pending_orders,
            "notebook_pages": notebook_pages,
        },
        sac_candidates=sac_candidates,
        weanings=weanings,
        geno_queue=geno_queue,
        orders_list=orders_list,
        events_list=events_list,
        restock=restock,
        wean_offset_days=WEAN_OFFSET_DAYS,
    )


# Calculators the Home card links to (static/bench-calcs.js ids).
HOME_CALCULATORS = [("dilution", "Dilution"), ("molarity", "Molarity"), ("a260", "DNA / RNA from A260"),
                    ("count", "Cell count"), ("seeding", "Seeding plates"), ("rcf", "rpm ↔ × g"),
                    ("buffer", "Buffer pH"), ("pcrmix", "PCR master mix")]


def _home_extra_cards(db_session, shown: set, today: date) -> dict:
    """What Home's optional cards show, read only when they are on."""
    me = g.user.username
    out = {"todos": [], "bookings": [], "recent_pages": [], "home_calculators": HOME_CALCULATORS}
    if "todos" in shown:
        # Yours, and the lab's and your project groups' to-dos.
        rows = db_session.scalars(select(TaskItem).where(
            task_visible_clause(), TaskItem.status != "done",
            TaskItem.due_date.is_(None) | (TaskItem.due_date <= today + timedelta(days=7)))
            .order_by(TaskItem.due_date.is_(None), TaskItem.due_date).limit(8)).all()
        out["todos"] = [{"id": t.id, "title": t.title, "due": t.due_date,
                         "overdue": bool(t.due_date and t.due_date < today),
                         "whose": project_groups.label(t.is_shared, t.share_group_id, personal="", lab="Lab"),
                         "can_tick": task_can_edit(t)} for t in rows]
    if "bookings" in shown:
        from .models import EquipmentBooking
        start = datetime.combine(today, datetime.min.time())
        rows = db_session.scalars(select(EquipmentBooking).options(selectinload(EquipmentBooking.equipment)).where(
            EquipmentBooking.owner == me, EquipmentBooking.end_at >= start,
            EquipmentBooking.start_at < start + timedelta(days=8)).order_by(EquipmentBooking.start_at).limit(8)).all()
        out["bookings"] = [{"what": b.equipment.name, "start": b.start_at, "end": b.end_at, "purpose": b.purpose}
                           for b in rows]
    if "notebook" in shown:
        rows = db_session.scalars(lab_notebook.accessible_filter(
            select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id))
            .order_by(NotebookPage.updated_at.desc()).limit(6)).all()
        out["recent_pages"] = [{"id": p.id, "tab_id": p.tab_id_fk, "title": p.title or "Untitled page",
                                "updated_at": p.updated_at} for p in rows]
    return out


@app.route("/home/cards", methods=["POST"])
@login_required
def set_home_cards():
    """Customize Home: which cards show, their order, which are full width."""
    with SessionLocal() as db_session:
        if request.form.get("reset") == "1":
            home_layouts.reset_cards(db_session, g.user.username)
        else:
            order = request.form.getlist("order")
            shown = set(request.form.getlist("show"))
            home_layouts.set_cards(db_session, g.user.username, order,
                                   [k for k in order if k not in shown], request.form.getlist("wide"))
        db_session.commit()
    return redirect(url_for("home_dashboard"))


ALLOWED_LANDING_ENDPOINTS = {"colony", "notebook", "calendar", "orders", "samples", "plasmids"}


@app.route("/home/layout", methods=["POST"])
@login_required
def set_home_layout():
    """Switch Home between Classic, Tracks and Freezer; kept per person."""
    with SessionLocal() as db_session:
        home_layouts.set_layout(db_session, g.user.username, request.form.get("layout", ""))
        db_session.commit()
    return redirect(url_for("home_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        keys = security.login_keys(username)
        wait = security.login_throttle.retry_after(*keys)
        if wait:
            minutes = -(-wait // 60)
            flash(ngettext("Too many failed sign-in attempts. Try again in %(num)s minute.",
                           "Too many failed sign-in attempts. Try again in %(num)s minutes.", minutes), "error")
            return render_template("auth.html", mode="login"), 429
        if security.https_required_but_missing():
            flash(gettext("This server only accepts sign-ins over HTTPS. Open it with an https:// address."), "error")
            return render_template("auth.html", mode="login")
        with SessionLocal() as db_session:
            user = db_session.scalar(select(UserAccount).where(UserAccount.username == username))
            if not security.check_password(user, password):
                security.login_throttle.failed(*keys)
                flash(gettext("Incorrect username or password."), "error")
            elif user.role == "pending":
                flash(gettext("Your account is waiting for a lab admin to approve it."), "error")
            elif getattr(user, "disabled", False):
                flash(gettext("This account is disabled. Contact an admin."), "error")
            else:
                security.login_throttle.succeeded(*keys)
                security.start_session(user)
                flash(gettext("Welcome, %(name)s.", name=user.display_name or user.username), "success")
                return redirect(security.safe_next(request.args.get("next")) or landing_url(user))
    return render_template("auth.html", mode="login")


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


@app.post("/language")
def choose_language():
    """The 中文 / English switch (the sign-in page, Settings): this browser's
    language from now on, and the person's, when they are signed in."""
    lang = request.form.get("language", "")
    if g.get("user") is not None:
        with SessionLocal() as db_session:
            i18n.set_preference(db_session, g.user.username, lang)
            db_session.commit()
    else:
        i18n.remember(lang)
    return redirect(security.safe_next(request.form.get("next")) or url_for("index"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    profile_error = None
    with SessionLocal() as db_session:
        user = db_session.get(UserAccount, g.user.id)
        if user is None:
            session.clear()
            return redirect(url_for("login"))
        if request.method == "POST":
            action = request.form.get("action", "profile")
            if action == "profile":
                user.display_name = request.form.get("display_name", "").strip()
                short = request.form.get("short_name", "").strip()
                user.short_name = short[:5]
                user.email = request.form.get("email", "").strip()
                user.role_title = request.form.get("role_title", "").strip()
                landing = request.form.get("default_landing", "").strip()
                user.default_landing = landing if landing in ALLOWED_LANDING_ENDPOINTS else ""
                if "home_layout" in request.form:
                    home_layouts.set_layout(db_session, user.username, request.form.get("home_layout", ""))
                db_session.commit()
                flash(gettext("Profile updated."), "success")
            elif action == "appearance":
                glyph, color = appearance.set_choice(db_session, user.username,
                                                     request.form.get("glyph", ""), request.form.get("color", ""))
                db_session.commit()
                if request.headers.get("X-Autosave") == "1":
                    # Picked in Settings and saved at once: the page swaps the
                    # icon and the accent in place.
                    return jsonify({"ok": True, "icon": app_icon_url(glyph, color),
                                    "brand_css": appearance.brand_css(color)})
                flash(gettext("App icon updated."), "success")
            elif action == "language":
                i18n.set_preference(db_session, user.username, request.form.get("language", ""))
                db_session.commit()
                flash(gettext("Language saved."), "success")
            elif action == "notifications":
                for category in notify.CATEGORIES:
                    setattr(user, f"notify_{category}", request.form.get(f"notify_{category}") == "1")
                db_session.commit()
                flash(gettext("Notification preferences updated."), "success")
            elif action == "password":
                current = request.form.get("current_password", "")
                new_pw = request.form.get("new_password", "")
                confirm = request.form.get("confirm_password", "")
                # An account made by signing in with Google or Microsoft has
                # no password yet; it may set one without a current one.
                check_key = ("current-password", user.id)
                if security.password_check_throttle.retry_after(check_key):
                    flash(gettext("Too many wrong passwords. Try again in 15 minutes."), "error")
                elif security.has_password(user) and not security.check_password(user, current):
                    security.password_check_throttle.failed(check_key)   # a stolen session can't guess on
                    flash(gettext("Current password is incorrect."), "error")
                elif problem := security.password_problem(new_pw, user.username):
                    flash(problem, "error")
                elif new_pw != confirm:
                    flash(gettext("New passwords do not match."), "error")
                else:
                    user.password_hash = generate_password_hash(new_pw)
                    db_session.commit()
                    # Other sessions (another browser, a lost laptop) end;
                    # this one carries on under the new password.
                    session["auth"] = security.session_stamp(user)
                    flash(gettext("Password updated. Any other signed-in sessions have been signed out."), "success")
            return redirect(url_for("settings"))
        user_data = {
            "username": user.username,
            "display_name": user.display_name,
            "short_name": user.short_name,
            "email": user.email,
            "role_title": user.role_title,
            "default_landing": user.default_landing,
            "home_layout": home_layouts.get_layout(db_session, user.username),
            "app_icon": appearance.get_choice(db_session, user.username),
            "language": i18n.preference(db_session, user.username),
            "role": user.role,
            "created_at": local_time(user.created_at).strftime("%Y-%m-%d") if user.created_at else "",
            "notify_transfer": user.notify_transfer,
            "notify_picked": user.notify_picked,
            "notify_breeder_aging": user.notify_breeder_aging,
            **{f"notify_{c}": getattr(user, f"notify_{c}", True) for c in notify.CATEGORIES},
            "has_password": security.has_password(user),
        }
        linked = db_session.scalars(select(UserIdentity).where(UserIdentity.user_id_fk == user.id)
                                    .order_by(UserIdentity.created_at)).all()
        identities = [{"id": i.id, "provider": oidc.LABELS.get(i.provider, i.provider.title()),
                       "provider_key": i.provider, "email": i.email,
                       "last_used": local_time(i.last_login_at).strftime("%Y-%m-%d") if i.last_login_at else ""}
                      for i in linked]
    from . import mailer

    return render_template(
        "settings.html",
        user_settings=user_data,
        landing_choices=sorted(ALLOWED_LANDING_ENDPOINTS),
        home_layout_choices=home_layouts.LAYOUTS,
        icon_glyphs={key: label for key, (label, _draw) in appearance.GLYPHS.items()},
        icon_palettes=appearance.PALETTES,
        icon_urls={f"{g}/{c}": app_icon_url(g, c) for g in appearance.GLYPHS for c in appearance.PALETTES},
        mail_status=mailer.status_line(),
        mail_configured=mailer.is_configured(),
        identities=identities,
        notification_categories=notify.CATEGORIES,
    )


@app.route("/settings/export")
@login_required
def export_my_data():
    import csv as _csv
    import io as _io
    import json as _json
    import re as _re
    import zipfile

    username = g.user.username
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with SessionLocal() as db_session:
            user = db_session.get(UserAccount, g.user.id)

            mice = db_session.scalars(
                select(MouseRecord).where(MouseRecord.owner == username).order_by(MouseRecord.mouse_id)
            ).all()
            iso = lambda d: d.isoformat() if d else ""
            zf.writestr("mice.csv", csv_text([
                ["mouse_id", "gender", "genotype", "status", "owner", "cage_id", "litter_id", "dob", "dod", "note"],
                *([m.mouse_id, m.gender, m.genotype, m.status, m.owner, m.cage.cage_id if m.cage else "",
                   m.litter.litter_id if m.litter else "", iso(m.litter.date_of_birth) if m.litter else "",
                   iso(m.date_of_death), m.note] for m in mice)]))

            cages = db_session.scalars(
                select(CageRecord).where(CageRecord.owner == username).order_by(CageRecord.cage_id)
            ).all()
            zf.writestr("cages.csv", csv_text([
                ["cage_id", "purpose", "location", "rack", "row", "column", "shared", "litter_born", "notes"],
                *([c.cage_id, c.purpose, c.cage_location, c.rack.name if c.rack else "", c.rack_row or "",
                   c.rack_col or "", (project_groups.label(True, c.share_group_id, lab="yes") if access.is_shared_cage(c) else "no"),
                   iso(c.date_give_birth), c.notes]
                  for c in cages)]))

            weights = db_session.scalars(
                select(MouseWeight).join(MouseRecord, MouseWeight.mouse_id_fk == MouseRecord.id)
                .where(MouseRecord.owner == username).order_by(MouseWeight.weigh_date)
            ).all()
            zf.writestr("mouse_weights.csv", csv_text([
                ["mouse_id", "date", "grams", "notes", "recorded_by"],
                *([w.mouse.mouse_id, iso(w.weigh_date), w.grams, w.notes, w.recorded_by] for w in weights)]))

            experiments = db_session.scalars(
                select(Experiment).where(Experiment.owner_username == username).order_by(Experiment.id)
            ).all()
            zf.writestr("experiments.csv", csv_text([
                ["name", "status", "start", "end", "database", "description", "treatment_plan", "readout"],
                *([e.name, e.status, iso(e.start_date), iso(e.end_date), e.db, e.description, e.treatment_plan,
                   e.readout] for e in experiments)]))

            plasmids = db_session.scalars(
                select(PlasmidRecord).where(PlasmidRecord.owner == username).order_by(PlasmidRecord.plasmid_id)
            ).all()
            zf.writestr("plasmids.csv", csv_text([
                ["plasmid_id", "name", "backbone", "insert", "resistance", "owner", "location",
                 "concentration", "a260_280", "lab_common", "notes"],
                *([p.plasmid_id, p.name, p.backbone, p.insert_seq, p.resistance, p.owner, p.location,
                   p.concentration, p.a260_280, project_groups.label(p.is_shared, p.share_group_id, personal="", lab="yes"), p.notes]
                  for p in plasmids)]))

            tabs = db_session.scalars(
                select(NotebookTab).where(NotebookTab.owner_username == username).order_by(NotebookTab.position, NotebookTab.id)
            ).all()
            for tab in tabs:
                safe_tab = _re.sub(r"[^\w\-_. ]", "_", tab.title or f"tab_{tab.id}").strip() or f"tab_{tab.id}"
                for page in tab.pages:
                    safe_page = _re.sub(r"[^\w\-_. ]", "_", page.title or f"page_{page.id}").strip() or f"page_{page.id}"
                    path = f"notebook/{safe_tab}/{safe_page}.md"
                    body = page.body or ""
                    header = f"# {page.title}\n\n_Created: {page.created_at.isoformat()} · Updated: {page.updated_at.isoformat()}_\n\n"
                    zf.writestr(path, header + body)

            profile = {
                "username": user.username,
                "display_name": user.display_name,
                "short_name": user.short_name,
                "email": user.email,
                "role_title": user.role_title,
                "role": user.role,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "exported_at": datetime.utcnow().isoformat(),
                "counts": {
                    "mice": len(mice),
                    "cages": len(cages),
                    "mouse_weights": len(weights),
                    "experiments": len(experiments),
                    "plasmids": len(plasmids),
                    "notebook_pages": sum(len(t.pages) for t in tabs),
                },
            }
            zf.writestr("profile.json", _json.dumps(profile, indent=2))

    buf.seek(0)
    filename = f"biomanager_{username}_{datetime.utcnow().strftime('%Y%m%d')}.zip"
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/admin/colony")
@admin_required
def admin_colony_overview():
    """Everyone's cages on one page, grouped by who manages them.

    The colony views are scoped to the person looking at them, which is what
    you want day to day but useless when someone leaves and their animals
    need reassigning. This is the whole-facility picture: who holds what,
    how full it is, and what has gone quiet.
    """
    today = date.today()
    with SessionLocal() as db_session:
        cages = db_session.scalars(
            select(CageRecord).options(selectinload(CageRecord.mice)).order_by(CageRecord.cage_id)
        ).all()
        unhoused = db_session.scalars(
            select(MouseRecord).where(MouseRecord.cage_id_fk.is_(None),
                                      MouseRecord.date_of_death.is_(None))
        ).all()

        groups: dict[str, dict] = {}
        for cage in cages:
            living = [m for m in cage.mice if m.date_of_death is None]
            shared = access.is_shared_cage(cage)
            # A shared cage belongs to the lab or a group, not to one person.
            key = "__shared__" if shared else (cage.owner or "").strip() or "__unowned__"
            group = groups.setdefault(key, {
                "owner": key, "cages": [], "mice": 0, "active_cages": 0,
            })
            last_touch = max(
                [m.updated_at for m in cage.mice if m.updated_at] or [cage.created_at]
            )
            group["cages"].append({
                "id": cage.id,
                "cage_id": cage.cage_id,
                "purpose": cage.purpose,
                "room": cage.room or cage.cage_location,
                "count": len(living),
                "total": len(cage.mice),
                "shared": shared,
                "active": cage_is_active(cage),
                "owner": cage.owner,
                "idle_days": (today - local_time(last_touch).date()).days if last_touch else None,
            })
            group["mice"] += len(living)
            group["active_cages"] += 1 if cage_is_active(cage) else 0

        def sort_key(item):
            name = item[0]
            return (name in ("__shared__", "__unowned__"), name)

        ordered = [
            {
                "label": {"__shared__": "Shared cages",
                          "__unowned__": "Unassigned"}.get(name, name),
                "owner": "" if name.startswith("__") else name,
                "is_pool": name.startswith("__"),
                **data,
            }
            for name, data in sorted(groups.items(), key=sort_key)
        ]

        return render_template(
            "admin_colony.html",
            groups=ordered,
            unhoused=unhoused,
            totals={
                "cages": len(cages),
                "mice": sum(g["mice"] for g in ordered),
                "owners": sum(1 for g in ordered if not g["is_pool"]),
                "shared": sum(len(g["cages"]) for g in ordered if g["label"].startswith("Shared")),
            },
        )


@app.route("/admin/users")
@admin_required
def admin_users():
    with SessionLocal() as db_session:
        users = db_session.scalars(select(UserAccount).order_by(UserAccount.created_at)).all()
        rows = [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "short_name": u.short_name,
                "email": u.email,
                "role_title": u.role_title,
                "role": u.role,
                "disabled": u.disabled,
                "created_at": local_time(u.created_at).strftime("%Y-%m-%d") if u.created_at else "",
                # A guest's account (app/guests.py): when it stops working.
                "expires_at": u.expires_at,
                "expired": u.expires_at is not None and u.expires_at <= datetime.utcnow(),
            }
            for u in users
        ]
    return render_template("admin_users.html", users=rows, roles=access.ROLES)


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
@admin_required
def admin_toggle_role(user_id: int):
    with SessionLocal() as db_session:
        target = db_session.get(UserAccount, user_id)
        if target is None:
            flash(gettext("User not found."), "error")
            return redirect(url_for("admin_users"))
        if target.id == g.user.id:
            flash(gettext("You cannot change your own role."), "error")
            return redirect(url_for("admin_users"))
        if target.role == "pending":
            flash(gettext("Approve %(user)s before changing their role.", user=target.username), "error")
            return redirect(url_for("admin_users"))
        wanted = request.form.get("role", "")
        if wanted and wanted not in access.ROLES:
            flash(gettext("That isn't a role."), "error")
            return redirect(url_for("admin_users"))
        target.role = wanted or ("member" if target.role == "admin" else "admin")
        who = g.user.display_name or g.user.username
        if target.role in ("care", "facility"):
            notify.send(db_session, target.username,
                        ("%(who)s made you animal care" if target.role == "care"
                         else "%(who)s made you facility manager"),
                        access.ROLES[target.role][1], category="lab", actor=g.user.username,
                        values={"who": who}, message_values={})
        if target.role == "admin":
            notify.send(db_session, target.username, "%(who)s made you a lab admin",
                        "You can now change Lab setup, approve sign-ups and edit any record.",
                        category="lab", link=url_for("lab.setup"), actor=g.user.username,
                        values={"who": who}, message_values={})
        db_session.commit()
        if target.role == "member":
            flash(gettext("%(user)s is now member.", user=target.username), "success")
        elif target.role == "care":
            flash(gettext("%(user)s is now animal care.", user=target.username), "success")
        elif target.role == "facility":
            flash(gettext("%(user)s is now facility manager.", user=target.username), "success")
        elif target.role == "admin":
            flash(gettext("%(user)s is now admin.", user=target.username), "success")
        else:
            flash(f"{target.username} is now {target.role}.", "success")
    referrer = request.referrer or ""
    return redirect(referrer if referrer.startswith(request.host_url) else url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/disable", methods=["POST"])
@admin_required
def admin_toggle_disabled(user_id: int):
    with SessionLocal() as db_session:
        target = db_session.get(UserAccount, user_id)
        if target is None:
            flash(gettext("User not found."), "error")
            return redirect(url_for("admin_users"))
        if target.id == g.user.id:
            flash(gettext("You cannot disable your own account."), "error")
            return redirect(url_for("admin_users"))
        if target.role == "pending":
            # A sign-up waiting for approval: enabling it is the approval.
            target.role = "member"
            target.disabled = False
            db_session.commit()
            notify.settle_signups(db_session)
            flash(gettext("%(user)s approved. They can sign in now.", user=target.username), "success")
            return redirect(url_for("admin_users"))
        target.disabled = not target.disabled
        if target.disabled:
            security.end_sessions(db_session, target)   # enabling it again won't bring them back
            from .models import LabCopyKey
            for key in db_session.scalars(select(LabCopyKey).where(LabCopyKey.user_id_fk == target.id,
                                                                     LabCopyKey.revoked_at.is_(None))):
                key.revoked_at = datetime.utcnow()      # their computers' copy keys too
        db_session.commit()
        flash(gettext("%(user)s disabled.", user=target.username) if target.disabled
              else gettext("%(user)s enabled.", user=target.username), "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_reset_password(user_id: int):
    new_password = (request.form.get("new_password") or "").strip()
    with SessionLocal() as db_session:
        target = db_session.get(UserAccount, user_id)
        if target is None:
            flash(gettext("User not found."), "error")
            return redirect(url_for("admin_users"))
        if problem := security.password_problem(new_password, target.username):
            flash(problem, "error")
            return redirect(url_for("admin_users"))
        target.password_hash = generate_password_hash(new_password)
        db_session.commit()
        # Their sessions end (session_stamp); an admin resetting their own
        # password stays signed in here.
        if target.id == g.user.id:
            session["auth"] = security.session_stamp(target)
        flash(gettext("Password reset for %(user)s. Their other sessions have been signed out.", user=target.username),
              "success")
    return redirect(url_for("admin_users"))


@app.route("/register", methods=["GET", "POST"])
def register():
    """Sign up. The first account is the lab's admin, and on a server needs
    the setup code printed at start-up, so nobody else on the network can
    claim it first. Everyone after that waits for an admin's approval."""
    with SessionLocal() as db_session:
        first = db_session.scalar(select(func.count(UserAccount.id))) == 0
    needs_code = first and security.setup_code_required()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        signup_key = ("signup", request.remote_addr or "")
        if not username or not password:
            flash(gettext("Username and password are required."), "error")
        elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,39}", username):
            # Plain letters and digits: "аlex" in Cyrillic looks like "alex" in the lab's lists.
            flash(gettext("A username is 2–40 letters (a–z), digits, dots, dashes or underscores. Your full name, in any alphabet, goes in Name."), "error")
        elif not first and security.signup_throttle.retry_after(signup_key):
            flash(gettext("Too many sign-ups from here in the last hour. Try again later, or ask a lab admin."), "error")
        elif needs_code and not security.setup_code_matches(request.form.get("setup_code")):
            flash(gettext("That setup code is not right. It is printed in the server log when BioManager starts."), "error")
        elif problem := security.password_problem(password, username):
            flash(problem, "error")
        elif password != confirm_password:
            flash(gettext("Passwords do not match."), "error")
        else:
            with SessionLocal() as db_session:
                existing = db_session.scalar(select(UserAccount).where(
                    func.lower(UserAccount.username) == username.lower()))
                if existing is not None:
                    flash(gettext("That username already exists."), "error")
                else:
                    user = UserAccount(
                        username=username,
                        display_name=display_name,
                        password_hash=generate_password_hash(password),
                        role="admin" if first else "pending",
                        disabled=not first,
                    )
                    db_session.add(user)
                    if not first:
                        admins = db_session.scalars(select(UserAccount.username).where(
                            UserAccount.role == "admin", UserAccount.disabled.is_(False))).all()
                        for admin_name in admins:
                            add_notification(db_session, admin_name, notify.SIGNUP_TITLE, category="account",
                                             link=url_for("admin_users"),
                                             message=notify.SIGNUP_MESSAGE,
                                             message_values={"who": display_name or username,
                                                             "username": username})
                    db_session.commit()
                    if not first:
                        security.signup_throttle.failed(signup_key)     # counts sign-ups, not failures
                    if first:
                        security.clear_setup_code()
                        flash(gettext("Admin account created. You can sign in now."), "success")
                    else:
                        flash(gettext("Account created. A lab admin needs to approve it before you can sign in."), "success")
                    return redirect(url_for("login"))
    return render_template("auth.html", mode="register", first_account=first, needs_setup_code=needs_code)


@app.route("/logout", methods=["POST"])
def logout():
    with SessionLocal() as db_session:
        security.sign_out(db_session)
    flash(gettext("You have been signed out."), "success")
    return redirect(url_for("login"))


@app.route("/notifications/mark-read", methods=["POST"])
@login_required
def mark_notifications_read():
    with SessionLocal() as db_session:
        rows = db_session.scalars(select(NotificationRecord).where(NotificationRecord.recipient_username == g.user.username, NotificationRecord.is_read.is_(False))).all()
        for row in rows:
            row.is_read = True
        db_session.commit()
    if request.headers.get("X-Autosave") == "1" or request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": True, "unread": 0})
    referrer = request.referrer or ""
    return redirect(referrer if referrer.startswith(request.host_url) else url_for("lab.notifications"))


@app.route("/colony")
@login_required
def colony():
    active_view = request.args.get("view", "mice")
    if active_view not in COLONY_VIEW_META:
        # An unknown or stale tab name lands on the mouse sheet rather than
        # on whichever tab happens to be the template's fallback.
        active_view = "mice"
    scope = access.resolve_scope(request.args.get("scope"))
    context = colony_context(active_view, scope, show_ended=request.args.get("ended") == "all")
    context["scope"] = scope
    context["scopes"] = access.scopes_for()
    context["scope_hints"] = access.SCOPE_HINTS
    context["end_statuses"] = sorted(END_STATUSES)
    return render_template("colony.html", **context)


# ---------------------------------------------------------------------------
# Experiments: a cohort of mice under a shared treatment plan + timeline,
# with longitudinal body-weight tracking per mouse.
# ---------------------------------------------------------------------------


EXPERIMENT_STATUSES = ("active", "paused", "done", "cancelled")


def _experiment_refusal(exp):
    """A response when the current user may not change `exp`, else None.
    Background saves get JSON; form posts go back to the experiment."""
    if access.can_edit_experiment(exp):
        return None
    message = access.denied_message("experiment", exp.owner_username)
    if request.headers.get("X-Autosave") == "1":
        return jsonify({"ok": False, "error": message}), 403
    flash(message, "error")
    return redirect(url_for("experiment_detail", experiment_id=exp.id))


@app.route("/colony/experiments/create", methods=["POST"])
@login_required
def create_experiment():
    name = (request.form.get("name") or "").strip() or "Untitled experiment"
    description = (request.form.get("description") or "").strip()
    treatment = (request.form.get("treatment_plan") or "").strip()
    start_date = parse_date(request.form.get("start_date"))
    cage_id_raw = (request.form.get("from_cage_id") or "").strip()

    with SessionLocal() as db_session:
        exp = Experiment(
            name=name,
            description=description,
            treatment_plan=treatment,
            status="active",
            owner_username=g.user.username,
            start_date=start_date,
        )
        db_session.add(exp)
        db_session.flush()

        # Optional: seed members from a cage's living mice that you may edit.
        if cage_id_raw:
            cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == cage_id_raw))
            if cage is None:
                flash(gettext("There is no cage %(cage)s, so the experiment starts with no mice. Add a cage or single mice below.", cage=cage_id_raw), "error")
            else:
                added = skipped = 0
                for mouse in cage.mice:
                    if not mouse_is_active(mouse):
                        continue
                    if not can_edit_mouse(mouse):
                        skipped += 1
                        continue
                    db_session.add(ExperimentMouse(
                        experiment_id_fk=exp.id,
                        mouse_id_fk=mouse.id,
                        treatment_group="",
                    ))
                    added += 1
                if skipped:
                    flash(gettext("Added %(added)s mice from cage %(cage)s; %(skipped)s skipped — not yours to edit.",
                                  added=added, cage=cage.cage_id, skipped=skipped), "error")
        db_session.commit()
        return redirect(url_for("experiment_detail", experiment_id=exp.id))


@app.route("/colony/experiments/<int:experiment_id>")
@login_required
def experiment_detail(experiment_id: int):
    """An experiment's page: the same for every database's animals
    (app/experiments.py)."""
    return experiment_pages.render_page(experiment_id)


@app.route("/colony/experiments/<int:experiment_id>/update", methods=["POST"])
@login_required
def update_experiment(experiment_id: int):
    autosave = request.headers.get("X-Autosave") == "1"
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, experiment_id)
        if exp is None:
            return jsonify({"ok": False, "error": gettext("That experiment no longer exists.")}), 404
        refused = _experiment_refusal(exp)
        if refused:
            return refused
        form = request.form
        start = parse_date(form.get("start_date")) if "start_date" in form else exp.start_date
        end = parse_date(form.get("end_date")) if "end_date" in form else exp.end_date
        status = (form.get("status") or "").strip().lower() if "status" in form else exp.status
        error = None
        if start and end and end < start:
            error = gettext("The end date (%(end)s) is before the start date (%(start)s).",
                            end=end.isoformat(), start=start.isoformat())
        elif status and status not in EXPERIMENT_STATUSES:
            error = gettext("“%(status)s” is not an experiment status.", status=status)
        if error:
            if autosave:
                return jsonify({"ok": False, "error": error}), 409
            flash(error, "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        if "name" in form:
            exp.name = (form.get("name") or "").strip() or exp.name
        if "description" in form:
            exp.description = form.get("description", exp.description)
        if "treatment_plan" in form:
            exp.treatment_plan = form.get("treatment_plan", exp.treatment_plan)
        exp.status = status or "active"
        exp.start_date, exp.end_date = start, end
        exp.updated_at = datetime.utcnow()
        db_session.commit()
    if autosave:
        return jsonify({"ok": True})
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.route("/colony/experiments/<int:experiment_id>/delete", methods=["POST"])
@login_required
def delete_experiment(experiment_id: int):
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, experiment_id)
        if exp is not None:
            refused = _experiment_refusal(exp)
            if refused:
                return refused
            log_delete(
                db_session, "experiments", exp.id,
                record_label=f"Experiment: {exp.name}",
                details=f"members={len(exp.memberships)}",
            )
            name = exp.name
            db_session.delete(exp)
            db_session.commit()
            flash(gettext("Deleted experiment %(name)s. Its mice are unchanged.", name=name), "success")
    return redirect(url_for("colony", view="experiments"))


@app.route("/colony/experiments/<int:experiment_id>/add-cage", methods=["POST"])
@login_required
def experiment_add_cage(experiment_id: int):
    cage_id_raw = (request.form.get("cage_id") or "").strip()
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, experiment_id)
        if exp is None:
            return redirect(url_for("colony", view="experiments"))
        refused = _experiment_refusal(exp)
        if refused:
            return refused
        if not cage_id_raw:
            flash(gettext("Enter a cage to add its mice."), "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == cage_id_raw))
        if cage is None:
            flash(gettext("Cage '%(cage)s' not found.", cage=cage_id_raw), "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        existing = {em.mouse_id_fk for em in exp.memberships}
        added = skipped = 0
        for mouse in cage.mice:
            if mouse.id in existing or not mouse_is_active(mouse):
                continue
            if not can_edit_mouse(mouse):
                skipped += 1
                continue
            db_session.add(ExperimentMouse(
                experiment_id_fk=exp.id,
                mouse_id_fk=mouse.id,
                treatment_group="",
            ))
            added += 1
        db_session.commit()
    if added or skipped:
        if skipped:
            message = ngettext("Added %(num)s mouse from cage %(cage)s. %(skipped)s skipped — not yours to edit.",
                               "Added %(num)s mice from cage %(cage)s. %(skipped)s skipped — not yours to edit.",
                               added, cage=cage_id_raw, skipped=skipped)
        else:
            message = ngettext("Added %(num)s mouse from cage %(cage)s.", "Added %(num)s mice from cage %(cage)s.",
                               added, cage=cage_id_raw)
        flash(message, "success" if added else "error")
    else:
        flash(gettext("Cage %(cage)s has no living mice that are not already in the experiment.", cage=cage_id_raw),
              "info")
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.route("/colony/experiments/<int:experiment_id>/add-mouse", methods=["POST"])
@login_required
def experiment_add_mouse(experiment_id: int):
    mouse_row_id = request.form.get("mouse_row_id", type=int)
    treatment_group = (request.form.get("treatment_group") or "").strip()
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, experiment_id)
        if exp is None:
            return redirect(url_for("colony", view="experiments"))
        refused = _experiment_refusal(exp)
        if refused:
            return refused
        mouse = db_session.get(MouseRecord, mouse_row_id) if mouse_row_id else None
        if mouse is None:
            flash(gettext("Pick a mouse to add."), "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        if not can_edit_mouse(mouse):
            flash(access.reason_denied(mouse), "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        if not any(em.mouse_id_fk == mouse.id for em in exp.memberships):
            db_session.add(ExperimentMouse(
                experiment_id_fk=exp.id,
                mouse_id_fk=mouse.id,
                treatment_group=treatment_group,
            ))
            db_session.commit()
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.route("/colony/experiments/<int:experiment_id>/members/<int:membership_id>/update", methods=["POST"])
@login_required
def experiment_member_update(experiment_id: int, membership_id: int):
    with SessionLocal() as db_session:
        em = db_session.get(ExperimentMouse, membership_id)
        if em is None or em.experiment_id_fk != experiment_id:
            return jsonify({"ok": False, "error": gettext("That mouse is no longer in the experiment.")}), 404
        if not access.can_edit_experiment(em.experiment):
            return jsonify({"ok": False, "error": access.denied_message(
                "experiment", em.experiment.owner_username)}), 403
        # Present but blank clears the group; absent leaves it alone.
        if "treatment_group" in request.form:
            em.treatment_group = (request.form.get("treatment_group") or "").strip()
        if "note" in request.form:
            em.note = (request.form.get("note") or "").strip()
        db_session.commit()
        return jsonify({"ok": True})


@app.route("/colony/experiments/<int:experiment_id>/members/<int:membership_id>/remove", methods=["POST"])
@login_required
def experiment_member_remove(experiment_id: int, membership_id: int):
    with SessionLocal() as db_session:
        em = db_session.get(ExperimentMouse, membership_id)
        if em is not None and em.experiment_id_fk == experiment_id:
            refused = _experiment_refusal(em.experiment)
            if refused:
                return refused
            db_session.delete(em)
            db_session.commit()
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.route("/colony/mice/<int:mouse_row_id>/weights/create", methods=["POST"])
@login_required
def mouse_weight_create(mouse_row_id: int):
    raw_date = request.form.get("weigh_date") or ""
    raw_grams = request.form.get("grams") or ""
    notes = (request.form.get("notes") or "").strip()
    try:
        grams = float(raw_grams)
    except ValueError:
        return jsonify({"ok": False, "error": gettext("grams must be a number")}), 400
    weigh_date = parse_date(raw_date) or date.today()
    with SessionLocal() as db_session:
        mouse = db_session.get(MouseRecord, mouse_row_id)
        if mouse is None:
            return jsonify({"ok": False, "error": gettext("mouse not found")}), 404
        if not can_edit_mouse(mouse):
            return jsonify({"ok": False, "error": access.reason_denied(mouse)}), 403
        # Upsert: if a weight for this mouse + date exists, update it.
        existing = db_session.scalar(
            select(MouseWeight)
            .where(MouseWeight.mouse_id_fk == mouse.id)
            .where(MouseWeight.weigh_date == weigh_date)
        )
        if existing is not None:
            existing.grams = grams
            if notes:
                existing.notes = notes
            existing.recorded_by = g.user.username
        else:
            db_session.add(MouseWeight(
                mouse_id_fk=mouse.id,
                weigh_date=weigh_date,
                grams=grams,
                notes=notes,
                recorded_by=g.user.username,
            ))
        db_session.commit()
    if request.headers.get("X-Autosave") == "1":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("colony", view="experiments"))


@app.route("/colony/mice/<int:mouse_row_id>/weights/<int:weight_id>/delete", methods=["POST"])
@login_required
def mouse_weight_delete(mouse_row_id: int, weight_id: int):
    with SessionLocal() as db_session:
        w = db_session.get(MouseWeight, weight_id)
        if w is not None and w.mouse_id_fk == mouse_row_id:
            mouse = db_session.get(MouseRecord, mouse_row_id)
            if not can_edit_mouse(mouse):
                flash(access.reason_denied(mouse), "error")
                return redirect(request.referrer or url_for("colony", view="experiments"))
            db_session.delete(w)
            db_session.commit()
    return redirect(request.referrer or url_for("colony", view="experiments"))


@app.route("/colony/mice/create", methods=["POST"])
@login_required
@next_number_retried
def create_mouse():
    refused = future_birth(request.form)
    if refused:
        flash(refused, "error")
        return redirect(url_for("colony", view="mice"))
    with SessionLocal() as db_session:
        mouse = MouseRecord(mouse_id=next_mouse_id(db_session), owner=request.form.get("owner", "").strip())
        transfer_recipient, sender_username = populate_mouse_from_form(db_session, mouse, request.form, preserve_owner_on_transfer=False)
        if not mouse.owner and g.user is not None:
            mouse.owner = g.user.username
        db_session.add(mouse)
        if transfer_recipient:
            create_transfer_copy(db_session, mouse, transfer_recipient, sender_username)
        db_session.commit()
    return redirect(url_for("colony", view="mice"))


@app.route("/colony/mice/new-record", methods=["POST"])
@login_required
@next_number_retried
def create_blank_mouse():
    """An empty mouse, the sheet's bottom New mouse button: back to the
    sheet it came from (its scope kept), where it is the row to type in."""
    with SessionLocal() as db_session:
        mouse = MouseRecord(mouse_id=next_mouse_id(db_session), owner=g.user.username)
        db_session.add(mouse)
        db_session.commit()
    return autosave_response("mice")


@app.route("/colony/mice/<int:mouse_row_id>/update", methods=["POST"])
@login_required
def update_mouse(mouse_row_id: int):
    with SessionLocal() as db_session:
        mouse = db_session.get(MouseRecord, mouse_row_id)
        if mouse is None:
            return autosave_response("mice")
        if not can_edit_mouse(mouse):
            flash(access.reason_denied(mouse), "error")
            return autosave_response("mice")

        refused = future_birth(request.form)
        if refused:
            flash(refused, "error")
            return autosave_response("mice")
        original_litter_id = mouse.litter.litter_id if mouse.litter else ""
        original_dob = mouse.litter.date_of_birth if mouse.litter else None
        form_litter_id = request.form.get("litter_id", "").strip()
        confirm_cohort_change = request.form.get("confirm_cohort_dob_change") == "1"
        requested_dob = parse_date(request.form.get("date_of_birth"))
        keeping_same_litter = bool(original_litter_id) and form_litter_id == original_litter_id

        if keeping_same_litter and requested_dob and requested_dob != original_dob and not confirm_cohort_change:
            flash(gettext("DOB belongs to the cohort. Confirm to remove this mouse from the cohort before changing DOB."), "error")
            return autosave_response("mice")

        transfer_recipient, sender_username = populate_mouse_from_form(db_session, mouse, request.form)

        if keeping_same_litter and requested_dob and requested_dob != original_dob and confirm_cohort_change:
            if mouse.litter is not None and mouse.litter.litter_id == original_litter_id:
                mouse.litter.date_of_birth = original_dob
            new_litter = get_or_create_litter(db_session, next_litter_id(db_session), requested_dob)
            set_mouse_litter(mouse, new_litter)
        if transfer_recipient:
            create_transfer_copy(db_session, mouse, transfer_recipient, sender_username)
        stamp_updated(mouse)
        db_session.commit()
        state = cage_state(mouse.cage)
        litter = mouse.litter
        mouse_state = {"id": mouse.id, "active": mouse_is_active(mouse), "status": mouse.status,
                       "date_of_death": mouse.date_of_death.isoformat() if mouse.date_of_death else "",
                       "litter_id": litter.litter_id if litter else "",
                       "date_of_birth": litter.date_of_birth.isoformat() if litter and litter.date_of_birth else ""}
        mouse_owner = mouse.owner
    result = autosave_response("mice")
    if request.headers.get("X-Autosave") == "1" and not isinstance(result, tuple):
        return jsonify({"ok": True, "cage": state, "mouse": mouse_state,
                        "row": {"active": mouse_state["active"],
                                "values": {"status": mouse_state["status"], "owner": mouse_owner,
                                           "litter_id": mouse_state["litter_id"]}}})
    return result


@app.route("/colony/mice/<int:mouse_row_id>/duplicate", methods=["POST"])
@login_required
@next_number_retried
def duplicate_mouse(mouse_row_id: int):
    with SessionLocal() as db_session:
        source_mouse = db_session.get(MouseRecord, mouse_row_id)
        if source_mouse is None:
            return redirect(url_for("colony", view="mice"))
        blocked = deny(source_mouse, "mice")
        if blocked:
            return blocked
        duplicate = MouseRecord(
            mouse_id=next_mouse_id(db_session),
            gender=source_mouse.gender,
            status=source_mouse.status,
            owner=source_mouse.owner,
            note=source_mouse.note,
            date_of_death=None,
        )
        set_mouse_cage(duplicate, source_mouse.cage)
        set_mouse_litter(duplicate, source_mouse.litter)
        sync_mouse_transgenes(duplicate, [source_mouse.transgene_1, source_mouse.transgene_2, source_mouse.transgene_3, source_mouse.transgene_4])
        db_session.add(duplicate)
        db_session.commit()
    return redirect(url_for("colony", view="mice"))


@app.route("/colony/mice/<int:mouse_row_id>/delete", methods=["POST"])
@login_required
def delete_mouse(mouse_row_id: int):
    with SessionLocal() as db_session:
        mouse = db_session.get(MouseRecord, mouse_row_id)
        if mouse is not None and can_edit_mouse(mouse):
            log_delete(
                db_session, "mice", mouse.id,
                record_label=f"Mouse #{mouse.mouse_id}",
                details=f"gender={mouse.gender} genotype={mouse.genotype} owner={mouse.owner}",
            )
            # Its weights and experiment places belong to it and go with it;
            # left behind they would block the delete (or, before foreign
            # keys were enforced, attach to whichever mouse got the id next).
            for child in (MouseWeight, ExperimentMouse):
                for row in db_session.scalars(select(child).where(child.mouse_id_fk == mouse.id)):
                    db_session.delete(row)
            db_session.delete(mouse)
            db_session.commit()
    return redirect(url_for("colony", view="mice"))


@app.route("/colony/mice/<int:mouse_row_id>/pick", methods=["POST"])
@login_required
def pick_mouse(mouse_row_id: int):
    with SessionLocal() as db_session:
        mouse = db_session.get(MouseRecord, mouse_row_id)
        if mouse is None:
            return redirect(url_for("colony", view="breeders"))
        blocked = deny(mouse, "breeders")
        if blocked:
            return blocked
        # The previous owner is told by app/notify.py ("… took mouse #12 from you").
        mouse.owner = g.user.username
        db_session.commit()
    return redirect(url_for("colony", view="breeders"))


@app.route("/colony/mice/bulk-sac", methods=["POST"])
@login_required
def bulk_sac_mice():
    ids = [int(value) for value in request.form.getlist("selected_ids") if value.isdigit()]
    with SessionLocal() as db_session, audit.batch(
            db_session, "update", "mark as sac", "mice") as batch_row:
        mice = db_session.scalars(select(MouseRecord).where(MouseRecord.id.in_(ids))).all() if ids else []
        done = 0
        for mouse in mice:
            if can_edit_mouse(mouse):
                mouse.status = "sac"
                # A mouse that died earlier keeps its day; only the living die today.
                mouse.date_of_death = mouse.date_of_death or date.today()
                stamp_updated(mouse)
                done += 1
        batch_row.record_count = done
        db_session.commit()
    # Say what happened, like every other batch action, and go back to the
    # view (and scope) it was done from.
    _report(done, len(mice) - done, gettext("Recorded sac"))
    return _back_to_colony("mice")


def _back_to_colony(view: str):
    """The colony page an action came from, scope and all, or the view."""
    referrer = request.referrer or ""
    return redirect(referrer if referrer.startswith(request.host_url) and "/colony" in referrer
                    else url_for("colony", view=view))


# ---------------------------------------------------------------------------
# Batch actions on a selection of mice.
#
# All of these take repeated `selected_ids` fields (see
# static/selection-bar.js) and share one rule: apply to every record the
# user may edit, skip the rest, and say how many were skipped rather than
# failing the whole operation.
# ---------------------------------------------------------------------------


def _selected_mice(db_session, form) -> list:
    ids = [int(v) for v in form.getlist("selected_ids") if v.isdigit()]
    if not ids:
        return []
    return list(db_session.scalars(
        select(MouseRecord).where(MouseRecord.id.in_(ids))
    ).all())


def _report(changed: int, skipped: int, what: str) -> None:
    """`what` is already in the page's language ("Set owner", "Recorded sac")."""
    if changed and skipped:
        flash(ngettext("%(what)s on %(num)s mouse. %(skipped)s skipped — not yours to edit.",
                       "%(what)s on %(num)s mice. %(skipped)s skipped — not yours to edit.",
                       changed, what=what, skipped=skipped), "success")
    elif changed:
        flash(ngettext("%(what)s on %(num)s mouse.", "%(what)s on %(num)s mice.", changed, what=what), "success")
    elif skipped:
        flash(gettext("Nothing changed — %(skipped)s record(s) are not yours to edit.", skipped=skipped), "error")
    else:
        flash(gettext("Nothing was selected."), "error")


# Fields the bulk editor may set, and how to apply each one.
BULK_FIELDS = {
    "owner": "Owner",
    "status": "Status",
    "genotype": "Transgenes",
    "cage_id": "Cage",
    "note": "Note",
    "date_of_death": "Date of death",
}
# What _report says was done, for each field.
BULK_DONE = {
    "owner": "Set owner",
    "status": "Set status",
    "genotype": "Set transgenes",
    "cage_id": "Set cage",
    "note": "Set note",
    "date_of_death": "Set date of death",
}


def new_owned_cage(db_session, **fields) -> CageRecord:
    """A fresh cage with the next free ID, owned by whoever made it."""
    cage = CageRecord(cage_id=next_cage_id(db_session),
                      owner=g.user.username if g.user else "", **fields)
    db_session.add(cage)
    db_session.flush()
    return cage


@app.route("/colony/mice/bulk-update", methods=["POST"])
@login_required
def bulk_update_mice():
    """Set one field to one value across the selection."""
    field = (request.form.get("field") or "").strip()
    value = (request.form.get("value") or "").strip()
    back = request.referrer or url_for("colony", view="mice")
    if field not in BULK_FIELDS:
        flash(gettext("Pick a field to set."), "error")
        return redirect(back)

    changed = skipped = 0
    with SessionLocal() as db_session:
        if field == "owner" and value not in current_lab_usernames(db_session):
            flash(gettext("“%(value)s” is not a lab member, so no owner was changed. Pick a username from the list.",
                          value=value or gettext("(blank)")), "error")
            return redirect(back)
        if field == "date_of_death" and value and parse_date(value) is None:
            flash(gettext("“%(value)s” is not a date (use YYYY-MM-DD).", value=value), "error")
            return redirect(back)
        if field == "cage_id" and value and value.lower() != "new":
            existing_cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == value))
            if existing_cage is not None and not access.can_edit_cage(existing_cage):
                flash(gettext("Cage %(cage)s is %(owner)s’s private cage, so no mouse was moved.", cage=value,
                              owner=existing_cage.owner or gettext("someone else")), "error")
                return redirect(back)
        # "new" is one new cage for the whole selection — the mice were
        # picked together to be housed together — made only when at least
        # one selected mouse may move.
        target_cage = None
        with audit.batch(db_session, "update",
                         f"set {BULK_FIELDS[field].lower()} = {value or '(blank)'}", "mice") as batch_row:
            for mouse in _selected_mice(db_session, request.form):
                if not can_edit_mouse(mouse):
                    skipped += 1
                    continue
                if field == "cage_id":
                    if value and target_cage is None:
                        # Made inside the batch, so undo removes it too.
                        target_cage = (new_owned_cage(db_session) if value.lower() == "new"
                                       else get_or_create_cage(db_session, value))
                    # The column, not the relationship: the audit listener
                    # records column changes, and undo needs this one.
                    mouse.cage_id_fk = target_cage.id if target_cage else None
                elif field == "genotype":
                    # The sheet shows transgene columns, so "genotype" fills
                    # them ("Ai14; Cre" -> transgene 1 and 2) and keeps the
                    # combined genotype string in step.
                    sync_mouse_transgenes(mouse, split_genotype(value)[:4])
                elif field == "date_of_death":
                    mouse.date_of_death = parse_date(value)
                elif field == "status":
                    previous_status = mouse.status
                    mouse.status = value
                    apply_status_rules(mouse, previous_status)
                else:
                    setattr(mouse, field, value)
                stamp_updated(mouse)
                changed += 1
            batch_row.record_count = changed
        db_session.commit()
    _report(changed, skipped, gettext(BULK_DONE[field]))
    return redirect(back)


@app.route("/colony/mice/bulk-experiment", methods=["POST"])
@login_required
def bulk_add_to_experiment():
    """Add the selection to an experiment, all in one treatment group.

    This is the "set up an experiment with N mice under the same
    manipulation" case: pick the mice, pick the experiment, name the group.
    """
    experiment_id = request.form.get("experiment_id")
    group = (request.form.get("treatment_group") or "").strip()
    if not (experiment_id or "").isdigit():
        flash(gettext("Pick an experiment."), "error")
        return redirect(request.referrer or url_for("colony", view="mice"))

    added = skipped = already = 0
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, int(experiment_id))
        if exp is None:
            flash(gettext("That experiment no longer exists."), "error")
            return redirect(request.referrer or url_for("colony", view="mice"))
        batch_ctx = audit.batch(db_session, "create",
                                f"add to experiment {exp.name}"
                                + (f" as {group}" if group else ""),
                                "experiment_mice")
        batch_row = batch_ctx.__enter__()
        existing = {em.mouse_id_fk for em in exp.memberships}
        for mouse in _selected_mice(db_session, request.form):
            if not can_edit_mouse(mouse):
                skipped += 1
                continue
            if mouse.id in existing:
                already += 1
                continue
            db_session.add(ExperimentMouse(
                experiment_id_fk=exp.id,
                mouse_id_fk=mouse.id,
                treatment_group=group,
            ))
            added += 1
        batch_row.record_count = added
        batch_ctx.__exit__(None, None, None)
        db_session.commit()
        name = exp.name
        exp_id = exp.id

    if group:
        said = ngettext("Added %(num)s mouse to %(name)s as “%(group)s”", "Added %(num)s mice to %(name)s as “%(group)s”",
                        added, name=name, group=group)
    else:
        said = ngettext("Added %(num)s mouse to %(name)s", "Added %(num)s mice to %(name)s", added, name=name)
    extras = []
    if already:
        extras.append(gettext("(%(n)s already in it)", n=already))
    if skipped:
        extras.append(gettext("— %(n)s skipped, not yours to edit", n=skipped))
    flash(gettext("%(said)s%(extras)s.", said=said, extras="".join(" " + extra for extra in extras)),
          "success" if added else "error")
    return redirect(url_for("experiment_detail", experiment_id=exp_id))


# ---------------------------------------------------------------------------
# Batch creation: one specification, many mice.
#
# Two ways in — type a prototype and a count, or upload a CSV — and both land
# in the same editable preview before anything is written. One confirm step,
# one validation path, one place where IDs are allocated.
#
# IDs are shown in the preview but allocated again at save time: the preview
# can sit on screen while someone else adds mice, and a stale block would
# collide on the unique index.
# ---------------------------------------------------------------------------

# Columns the preview grid understands, in display order.
BATCH_COLUMNS = [
    ("gender", "Sex", 70),
    ("transgene_1", "Transgene 1", 130),
    ("transgene_2", "Transgene 2", 130),
    ("transgene_3", "Transgene 3", 130),
    ("transgene_4", "Transgene 4", 130),
    ("genotype", "Genotype", 150),
    ("cage_id", "Cage", 90),
    ("cage_location", "Location", 110),
    ("owner", "Owner", 110),
    ("litter_id", "Litter", 90),
    ("date_of_birth", "DOB", 130),
    ("status", "Status", 110),
    ("note", "Note", 170),
]
BATCH_FIELDS = [key for key, _label, _width in BATCH_COLUMNS]
MAX_BATCH = 200


def _blank_row() -> dict:
    return {key: "" for key in BATCH_FIELDS}


def _rows_from_prototype(form) -> list[dict]:
    """N copies of the prototype, optionally split by sex.

    The sex split exists because "6 females and 6 males" is how litters and
    orders actually arrive, and making someone retype the prototype twice to
    get it would defeat the point.
    """
    prototype = {key: (form.get(key) or "").strip() for key in BATCH_FIELDS}
    females = max(0, _batch_int(form.get("count_female")))
    males = max(0, _batch_int(form.get("count_male")))
    plain = max(0, _batch_int(form.get("count")))

    rows: list[dict] = []
    if females or males:
        # Females and males in one fresh cage breed. Unless asked to keep
        # them together, "new" becomes one new cage per sex.
        split = (females and males and _new_cage_token(prototype["cage_id"]) is not None
                 and not form.get("one_new_cage"))
        cage_f = f"{prototype['cage_id']}-F" if split else prototype["cage_id"]
        cage_m = f"{prototype['cage_id']}-M" if split else prototype["cage_id"]
        for _ in range(females):
            rows.append({**prototype, "gender": "F", "cage_id": cage_f})
        for _ in range(males):
            rows.append({**prototype, "gender": "M", "cage_id": cage_m})
    else:
        rows = [dict(prototype) for _ in range(plain or 1)]
    return rows[:MAX_BATCH]


def _rows_from_csv(upload) -> tuple[list[dict], list[str]]:
    """Parse an uploaded CSV into preview rows.

    Unknown columns are ignored rather than rejected — vendor exports carry
    all sorts of extra fields, and refusing the file over a stray column
    helps nobody.
    """
    import csv as _csv, io as _io

    raw = upload.read()
    try:
        text_data = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_data = raw.decode("latin-1")

    reader = _csv.DictReader(_io.StringIO(text_data))
    rows, warnings = [], []
    aliases = {"sex": "gender", "dob": "date_of_birth", "cage": "cage_id",
               "litter": "litter_id", "notes": "note"}

    for index, raw_row in enumerate(reader, start=2):
        if index - 1 > MAX_BATCH:
            warnings.append(gettext("Only the first %(max)s rows were loaded.", max=MAX_BATCH))
            break
        row = _blank_row()
        for header, value in (raw_row or {}).items():
            if header is None:
                continue
            key = aliases.get(header.strip().lower(), header.strip().lower())
            if key in row:
                row[key] = (value or "").strip()
        if any(row.values()):
            rows.append(row)
    if not rows:
        warnings.append(gettext("No usable rows found in that file."))
    warnings.extend(_normalise_csv_dates(rows))
    return rows, warnings


# The ways a spreadsheet writes a date into a CSV. Excel uses the computer's
# own format: 3/14/2026 in the US, 14/03/2026 or 14.03.2026 in much of the
# world, and 3/14/26 when the year column is narrow.
_SLASH_DATE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})$")


def _normalise_csv_dates(rows: list[dict]) -> list[str]:
    """Rewrite each row's date of birth as YYYY-MM-DD, in place.

    The preview's date field holds only that form, so anything else would
    arrive blank and be lost without a word. Whether 03/04/2026 is 3 April
    or 4 March is decided once for the whole file: a date whose first number
    is over 12 means day first, one whose second number is over 12 means
    month first, and with neither the file is read month first and says so.
    """
    warnings: list[str] = []
    parsed: dict[int, tuple[int, int, int]] = {}
    unreadable: list[int] = []
    day_first = month_first = False
    for index, row in enumerate(rows):
        raw = (row.get("date_of_birth") or "").strip()
        if not raw or parse_date(raw):
            if raw:
                row["date_of_birth"] = parse_date(raw).isoformat()
            continue
        match = _SLASH_DATE.match(raw)
        if not match:
            unreadable.append(index)
            continue
        a, b, year = (int(part) for part in match.groups())
        if year < 100:
            year += 2000
        parsed[index] = (a, b, year)
        day_first = day_first or a > 12
        month_first = month_first or b > 12
    guessed = False
    with SessionLocal() as s:
        lab_day_first = lab.date_style(s) == "day"      # Lab setup's "26 Sep 2026" decides a 03/04 file
    read_day_first = (day_first and not month_first) or (lab_day_first and not day_first and not month_first)
    for index, (a, b, year) in parsed.items():
        month, day = (b, a) if read_day_first else (a, b)
        guessed = guessed or (not day_first and not month_first and a != b)
        try:
            rows[index]["date_of_birth"] = date(year, month, day).isoformat()
        except ValueError:
            unreadable.append(index)
    for index in sorted(unreadable):
        warnings.append(gettext("Row %(row)s: “%(value)s” is not a date; that date of birth is left blank.",
                                row=index + 2, value=rows[index]["date_of_birth"]))
        rows[index]["date_of_birth"] = ""
    if day_first and month_first:
        warnings.append(gettext("The dates of birth mix day-first and month-first; check them."))
    elif guessed and read_day_first:
        warnings.append(gettext("Dates of birth were read day first (03/04/2026 as 3 April), as Lab setup's date style says. If the file is month first, correct them here."))
    elif guessed:
        warnings.append(gettext("Dates of birth were read month first (03/04/2026 as 4 March). If the file is day first, correct them here, or save it with YYYY-MM-DD dates."))
    return warnings


def _new_cage_token(value) -> str | None:
    """The key of a "new" cage in the Cage column, or None.

    "new" is one fresh cage; "new-F", "new 2" and so on are each another, so
    a batch can be split into as many new cages as it needs.
    """
    text = (value or "").strip().lower()
    if text == "new" or re.match(r"^new[\s\-_#]+\S", text):
        return re.sub(r"[\s\-_#]+", "-", text)
    return None


def _mixed_new_cages(rows: list[dict]) -> list[str]:
    """Warnings for new cages that would hold both sexes."""
    sexes: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        token = _new_cage_token(row.get("cage_id"))
        sex = (row.get("gender") or "").strip().upper()[:1]
        if token and sex in ("F", "M"):
            sexes.setdefault(token, set()).add(sex)
            labels.setdefault(token, row["cage_id"].strip())
    return [
        gettext("Cage “%(cage)s” would get females and males together. Give the males another new cage (e.g. “%(cage)s-M”) unless they are meant to breed.", cage=labels[token])
        for token, found in sexes.items() if len(found) > 1
    ]


def _rows_from_grid(form) -> list[dict]:
    """Read back the edited preview grid (fields named rows-<i>-<key>)."""
    indexes = set()
    for key in form.keys():
        if key.startswith("rows-") and key.count("-") >= 2:
            _, index, _field = key.split("-", 2)
            if index.isdigit():
                indexes.add(int(index))

    rows = []
    for index in sorted(indexes):
        if form.get(f"rows-{index}-drop"):
            continue
        row = {key: (form.get(f"rows-{index}-{key}") or "").strip() for key in BATCH_FIELDS}
        if any(row.values()):
            rows.append(row)
    return rows[:MAX_BATCH]


def _batch_int(raw) -> int:
    try:
        return int(str(raw or "").strip())
    except ValueError:
        return 0


@app.route("/colony/mice/batch", methods=["GET"])
@login_required
def batch_mice():
    """The setup step: prototype + count, or a CSV."""
    with SessionLocal() as db_session:
        return render_template(
            "batch_mice.html",
            stage="setup",
            columns=BATCH_COLUMNS,
            rows=[],
            warnings=[],
            prototype=_blank_row() | {"owner": g.user.username, "status": "experiment"},
            template_headers=BATCH_TEMPLATE_HEADERS,
            dropdowns=dropdown_options_map(db_session),
            usernames=current_lab_usernames(db_session),
            strain_rows=db_session.scalars(
                select(StrainRecord).order_by(StrainRecord.strain_name)).all(),
            next_id=next_mouse_id(db_session),
            max_batch=MAX_BATCH,
        )


# The file "Download a template" gives: the headers the importer reads, in
# the short spelling the page shows, and two example rows to overwrite.
BATCH_TEMPLATE_HEADERS = ["sex", "transgene_1", "transgene_2", "transgene_3", "transgene_4",
                          "cage", "cage_location", "owner", "litter", "dob", "status", "note"]


@app.route("/colony/mice/batch/template.csv", methods=["GET"])
@login_required
def batch_mice_template():
    import csv as _csv, io as _io

    buffer = _io.StringIO()
    writer = _csv.writer(buffer)
    writer.writerow(BATCH_TEMPLATE_HEADERS)
    example = {"transgene_1": "DAT-IRES-Cre/+", "cage": "new-F", "owner": g.user.username,
               "dob": (date.today() - timedelta(days=56)).isoformat(), "status": "experiment"}
    writer.writerow([{**example, "sex": "F"}.get(h, "") for h in BATCH_TEMPLATE_HEADERS])
    writer.writerow([{**example, "sex": "M", "cage": "new-M"}.get(h, "") for h in BATCH_TEMPLATE_HEADERS])
    # A byte-order mark, so Excel opens the file as UTF-8.
    return Response("\ufeff" + buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="mice-template.csv"'})


@app.route("/colony/mice/batch/preview", methods=["POST"])
@login_required
def batch_mice_preview():
    """Turn a prototype, a CSV or an edited grid into the preview."""
    warnings: list[str] = []
    upload = request.files.get("file")

    if upload is not None and upload.filename:
        rows, warnings = _rows_from_csv(upload)
    elif request.form.get("source") == "grid":
        rows = _rows_from_grid(request.form)
    else:
        rows = _rows_from_prototype(request.form)

    if not rows:
        flash(gettext("Nothing to preview — set a count or choose a file."), "error")
        return redirect(url_for("batch_mice"))
    warnings.extend(_mixed_new_cages(rows))
    warnings.extend(_future_births(rows))
    return _batch_preview(rows, warnings)


def _future_births(rows: list[dict]) -> list[str]:
    today_ = date.today()
    late = [str(index + 1) for index, row in enumerate(rows)
            if (parse_date(row.get("date_of_birth")) or today_) > today_]
    if not late:
        return []
    return [ngettext("Row %(rows)s: the date of birth is in the future. Correct it before creating.",
                     "Rows %(rows)s: the date of birth is in the future. Correct it before creating.",
                     len(late), rows=", ".join(late))]


def _batch_preview(rows: list[dict], warnings: list[str]):
    with SessionLocal() as db_session:
        # Shown so you can see what you will get; re-allocated on save.
        proposed = reserve_mouse_ids(db_session, len(rows))
        return render_template(
            "batch_mice.html",
            stage="preview",
            columns=BATCH_COLUMNS,
            rows=rows,
            proposed_ids=proposed,
            warnings=warnings,
            prototype=_blank_row(),
            dropdowns=dropdown_options_map(db_session),
            usernames=current_lab_usernames(db_session),
            strain_rows=db_session.scalars(
                select(StrainRecord).order_by(StrainRecord.strain_name)).all(),
            next_id=proposed[0] if proposed else 0,
            max_batch=MAX_BATCH,
        )


@app.route("/colony/mice/batch/create", methods=["POST"])
@login_required
@next_number_retried
def batch_mice_create():
    """Write the previewed rows, with one contiguous block of IDs."""
    rows = _rows_from_grid(request.form)
    if not rows:
        flash(gettext("Nothing to create."), "error")
        return redirect(url_for("batch_mice"))
    late = _future_births(rows)
    if late:
        # Back to the grid as it was typed, not to an empty form.
        return _batch_preview(rows, late + _mixed_new_cages(rows))

    created = 0
    with SessionLocal() as db_session, audit.batch(
            db_session, "create", f"add {len(rows)} mice in bulk", "mice") as batch_row:
        ids = reserve_mouse_ids(db_session, len(rows))

        # Resolve each "new" once for the whole batch. Six littermates
        # arriving together belong in one cage; allocating a cage per row
        # would make the common case the wrong one. "new-F", "new-M",
        # "new 2"… are each a cage of their own, and explicit numbers per
        # row still split them however you like.
        new_cages: dict[str, str] = {}
        for row, mouse_id in zip(rows, ids):
            token = _new_cage_token(row.get("cage_id"))
            if token:
                if token not in new_cages:
                    new_cages[token] = new_owned_cage(db_session).cage_id
                row = {**row, "cage_id": new_cages[token]}
            mouse = MouseRecord(mouse_id=mouse_id, owner=row.get("owner", ""))
            # Reuse the single-record path so cage allocation, litter
            # linking and DOB inheritance behave identically.
            populate_mouse_from_form(
                db_session, mouse, ImmutableMultiDict(row),
                preserve_owner_on_transfer=False)
            if not mouse.owner and g.user is not None:
                mouse.owner = g.user.username
            transgenes = [row.get(f"transgene_{i}", "") for i in (1, 2, 3, 4)]
            # A mouse's genotype is its transgenes joined ("Ai14; Cre"). An
            # old spreadsheet's genotype column, given no transgenes, becomes
            # Transgene 1, so it shows in the sheet and can be edited there.
            if not any(t.strip() for t in transgenes) and (row.get("genotype") or "").strip():
                transgenes[0] = row["genotype"].strip()
            sync_mouse_transgenes(mouse, transgenes)
            db_session.add(mouse)
            created += 1
        batch_row.record_count = created
        batch_id = batch_row.id
        db_session.commit()
        first, last = (ids[0], ids[-1]) if ids else (0, 0)

    undo = Markup('<a href="{}">{}</a>').format(url_for("batches_view"), gettext("Undo"))
    flash(Markup(gettext("Created %(count)s mice — #%(first)s to #%(last)s. %(undo)s", count=created, first=first,
                         last=last, undo=undo)), "success")
    return redirect(url_for("colony", view="mice"))


@app.route("/colony/mice/export")
@login_required
def export_mice():
    export_format = request.args.get("format", "csv")
    # The page passes the scope it is showing, so the file holds the mice
    # you were looking at rather than always your own.
    context = colony_context("mice", access.resolve_scope(request.args.get("scope")), show_ended=request.args.get("ended") != "recent")
    if export_format == "pdf":
        return render_template("print_mice.html", mouse_rows=context["mouse_rows"], printed_on=date.today().isoformat())
    payload, filename, mimetype = export_mouse_rows(context["mouse_rows"], export_format)
    return Response(
        payload,
        mimetype=mimetype,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


MAX_NEW_CAGES = 20


def _free_rack_cells(db_session, rack, count: int, start: tuple[int, int] | None) -> list[tuple[int, int]]:
    """Up to `count` empty positions of `rack` in reading order, from
    `start` (row, col) on, or from the first cell."""
    taken = {(r, c) for r, c in db_session.execute(
        select(CageRecord.rack_row, CageRecord.rack_col).where(
            CageRecord.rack_id_fk == rack.id, CageRecord.rack_row.is_not(None)))}
    cells = []
    first = start or (1, 1)
    for row in range(1, rack.rows + 1):
        for col in range(1, rack.cols + 1):
            if (row, col) < first or (row, col) in taken:
                continue
            cells.append((row, col))
            if len(cells) == count:
                return cells
    return cells


@app.route("/colony/cages/create", methods=["POST"])
@login_required
@next_number_retried
def create_cage():
    """Create a cage, or several ("How many", 1–20) with consecutive IDs.
    A blank ID takes the next free one(s); a typed ID starts the run. An ID
    that is already a cage is refused — creating never edits an existing
    cage (a stale form would otherwise overwrite it)."""
    form = request.form
    requested = form.get("cage_id", "").strip()
    raw_count = (form.get("count") or "1").strip() or "1"
    if not raw_count.isdigit() or not 1 <= int(raw_count) <= MAX_NEW_CAGES:
        flash(gettext("Make between 1 and %(max)s cages at a time (asked for “%(asked)s”).", max=MAX_NEW_CAGES,
                      asked=raw_count), "error")
        return autosave_response("cages")
    count = int(raw_count)
    refused = future_birth(form, "date_give_birth", "A litter's birth date")
    if refused:
        flash(refused, "error")
        return autosave_response("cages")
    with SessionLocal() as db_session:
        if requested and requested.lower() != "new":
            if count > 1 and not requested.isdigit():
                flash(gettext("To make %(count)s cages, leave the ID blank or type the first number of the run.",
                              count=count), "error")
                return autosave_response("cages")
            codes = [requested] if count == 1 else [str(int(requested) + i) for i in range(count)]
            taken = db_session.scalars(select(CageRecord.cage_id).where(CageRecord.cage_id.in_(codes))).all()
            if taken:
                flash(gettext("Cage %(cage)s already exists; nothing was changed. Leave the ID blank to take the next free one.", cage=", ".join(sorted(taken))), "error")
                return autosave_response("cages")
        else:
            codes = reserve_cage_ids(db_session, count)

        rack = cells = None
        placement_error = None
        if count > 1 and (form.get("rack_id") or "").strip():
            rack_raw = form.get("rack_id").strip()
            rack = db_session.get(MouseRack, int(rack_raw)) if rack_raw.isdigit() else None
            if rack is None:
                placement_error = gettext("There is no rack called %(rack)s.", rack=rack_raw)
            else:
                start = None
                position = (form.get("position") or "").strip()
                if position:
                    start = positions.parse(position, rack.naming, rack.rows, rack.cols)
                    if start is None:
                        placement_error = gettext("“%(position)s” is not a position in rack %(rack)s.",
                                                  position=position, rack=rack.name)
                if not placement_error and (start or position == ""):
                    cells = _free_rack_cells(db_session, rack, count, start) if start else []
                    if start and len(cells) < count:
                        placement_error = gettext("%(rack)s had room for %(room)s of %(count)s from %(position)s; the rest are in it without a position.", rack=rack.name,
                                                  room=len(cells), count=count, position=position)

        batch_ctx = audit.batch(db_session, "create", f"add {count} cages", "mouse_cages") if count > 1 else None
        batch_row = batch_ctx.__enter__() if batch_ctx is not None else None
        created = []
        for index, code in enumerate(codes):
            cage = CageRecord(cage_id=code, owner=g.user.username)
            db_session.add(cage)
            cage.cage_location = form.get("cage_location", "").strip()
            cage.purpose = form.get("purpose", "").strip()
            cage.notes = form.get("notes", "").strip()
            cage.date_give_birth = parse_date(form.get("date_give_birth"))
            cage.card_id = form.get("card_id", "").strip()
            cage.genotype_summary = form.get("genotype_summary", "").strip()
            cage.location_detail = form.get("location_detail", "").strip()
            cage.room = form.get("room", "").strip()
            if count == 1:
                if "rack_id" in form:
                    error = apply_cage_position(db_session, cage, form.get("rack_id"), form.get("position"))
                    if error:
                        placement_error = error
            elif rack is not None:
                cage.rack_id_fk = rack.id
                if cells is not None and index < len(cells):
                    cage.rack_row, cage.rack_col = cells[index]
            db_session.flush()
            created.append(code)
        if batch_ctx is not None:
            batch_row.record_count = len(created)
            batch_ctx.__exit__(None, None, None)
        db_session.commit()
        if placement_error:
            flash(gettext("Created cage %(cage)s, but not placed: %(error)s", cage=created[0], error=placement_error)
                  if count == 1 else
                  gettext("Created %(count)s cages, %(first)s to %(last)s. %(error)s", count=count, first=created[0],
                          last=created[-1], error=placement_error), "error")
        elif request.headers.get("X-Autosave") != "1":
            flash(gettext("Created cage %(cage)s.", cage=created[0]) if count == 1 else
                  gettext("Created %(count)s cages, %(first)s to %(last)s.", count=count, first=created[0],
                          last=created[-1]), "success")
    return autosave_response("cages")


# ---- Cage racks -------------------------------------------------------------
# A cage sits at (rack_row, rack_col) of its rack; the rack's naming scheme
# (app/positions.py) decides what that position is called: D7, 4-7, 37…


def rack_naming_payload(rack) -> dict:
    """What rack-grid.js needs to label a rack's rows, columns and cells."""
    return positions.scheme(rack.naming)


def _mouse_rack_from_form(db_session, rack, form) -> str | None:
    name = (form.get("name") or "").strip()
    if not name:
        return gettext("A rack needs a name.")
    clash = db_session.scalar(select(MouseRack.id).where(
        func.lower(MouseRack.name) == name.lower(), MouseRack.id != (rack.id or 0)))
    if clash:
        return gettext("There is already a rack called %(name)s.", name=name)
    try:
        rows = int(form.get("rows") or 8)
        cols = int(form.get("cols") or 10)
    except ValueError:
        return gettext("Rows and columns must be whole numbers.")
    rack.name = name
    rack.rows = max(1, min(26, rows))
    rack.cols = max(1, min(40, cols))
    rack.room = (form.get("room") or "").strip()
    rack.naming = json.dumps(positions.scheme_from_form(form))
    return None


def cage_state(cage) -> dict | None:
    """A cage's shared fields, returned after a save so the sheet can update
    every row of that cage at once."""
    if cage is None:
        return None
    return {"cage_id": cage.cage_id, "rack_id": cage.rack_id_fk or "",
            "rack_name": cage.rack.name if cage.rack else "",
            "position": cage_position_label(cage), "location": cage.cage_location}


def apply_cage_position(db_session, cage, rack_raw, position_raw) -> str | None:
    """Put `cage` in the rack and position a person typed; return an error
    message instead of guessing. An empty rack takes the cage out of racks;
    a rack with no position keeps it in that rack but unplaced. Typing a
    position that another cage holds is refused rather than moving that
    cage (dragging on the grid is how you swap)."""
    rack_raw = str(rack_raw or "").strip()
    position_raw = str(position_raw or "").strip()
    if not rack_raw:
        cage.rack_id_fk = cage.rack_row = cage.rack_col = None
        return None
    rack = db_session.get(MouseRack, int(rack_raw)) if rack_raw.isdigit() else db_session.scalar(
        select(MouseRack).where(func.lower(MouseRack.name) == rack_raw.lower()))
    if rack is None:
        return gettext("There is no rack called %(rack)s.", rack=rack_raw)
    if not position_raw:
        cage.rack_id_fk, cage.rack_row, cage.rack_col = rack.id, None, None
        return None
    cell = positions.parse(position_raw, rack.naming, rack.rows, rack.cols)
    if cell is None:
        first = positions.label(1, 1, rack.naming, rack.cols)
        last = positions.label(rack.rows, rack.cols, rack.naming, rack.cols)
        return gettext("“%(position)s” is not a position in rack %(rack)s (%(first)s–%(last)s).",
                       position=position_raw, rack=rack.name, first=first, last=last)
    holder = db_session.scalar(select(CageRecord).where(
        CageRecord.rack_id_fk == rack.id, CageRecord.rack_row == cell[0],
        CageRecord.rack_col == cell[1], CageRecord.id != (cage.id or 0)))
    if holder is not None:
        name = positions.label(*cell, rack.naming, rack.cols)
        return gettext("%(rack)s · %(position)s already holds cage %(cage)s. Drag on the rack grid to swap.",
                       rack=rack.name, position=name, cage=holder.cage_id)

    cage.rack_id_fk, (cage.rack_row, cage.rack_col) = rack.id, cell
    return None


def cage_position_label(cage) -> str:
    """"D7" under the cage's rack scheme, or "" when not placed."""
    if cage is None or cage.rack is None:
        return ""
    return positions.label(cage.rack_row, cage.rack_col, cage.rack.naming, cage.rack.cols)


@app.route("/colony/racks/save", methods=["POST"])
@login_required
def save_mouse_rack():
    with SessionLocal() as db_session:
        rack_id = request.form.get("id", "").strip()
        rack = db_session.get(MouseRack, int(rack_id)) if rack_id.isdigit() else MouseRack(name="", created_by=g.user.username)
        if rack is None:
            flash(gettext("That rack no longer exists."), "error")
            return redirect(url_for("colony", view="cages"))
        if rack.id is not None and not access.can_edit_rack(rack):
            flash(gettext("Only whoever added rack %(rack)s, or an admin, can change it.", rack=rack.name), "error")
            return redirect(url_for("colony", view="cages"))
        error = _mouse_rack_from_form(db_session, rack, request.form)
        if error:
            flash(error, "error")
            return redirect(url_for("colony", view="cages"))
        if rack.id is None:
            db_session.add(rack)
        db_session.commit()
        flash(gettext("Saved rack %(rack)s.", rack=rack.name), "success")
    return redirect(url_for("colony", view="cages"))


@app.route("/colony/racks/<int:rack_id>/delete", methods=["POST"])
@login_required
def delete_mouse_rack(rack_id: int):
    """Delete a rack. Its cages are kept, just unplaced."""
    with SessionLocal() as db_session:
        rack = db_session.get(MouseRack, rack_id)
        if rack is not None and not access.can_edit_rack(rack):
            flash(gettext("Only whoever added rack %(rack)s, or an admin, can delete it.", rack=rack.name), "error")
            return redirect(url_for("colony", view="cages"))
        if rack is not None:
            name = rack.name
            for cage in db_session.scalars(select(CageRecord).where(CageRecord.rack_id_fk == rack.id)):
                cage.rack_id_fk = cage.rack_row = cage.rack_col = None
            db_session.delete(rack)
            db_session.commit()
            flash(gettext("Deleted rack %(rack)s. Its cages are now unplaced.", rack=name), "success")
    return redirect(url_for("colony", view="cages"))


@app.route("/colony/cages/<int:cage_row_id>/place", methods=["POST"])
@login_required
def place_cage(cage_row_id: int):
    """Move a cage on the rack grid; answers JSON. Dropping onto an occupied
    position swaps the two cages; an empty rack id unplaces the cage."""
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        if cage is None:
            return jsonify({"ok": False, "error": gettext("That cage no longer exists.")}), 404
        if not can_edit_cage(cage):
            return jsonify({"ok": False, "error": access.reason_denied(cage)}), 403
        rack_id = request.form.get("rack_id", "").strip()
        if not rack_id:
            # Dropped on the Unplaced tray: out of the rack altogether.
            cage.rack_id_fk = cage.rack_row = cage.rack_col = None
            db_session.commit()
            return jsonify({"ok": True})
        rack = db_session.get(MouseRack, int(rack_id)) if rack_id.isdigit() else None
        try:
            row, col = int(request.form.get("row", "")), int(request.form.get("col", ""))
        except ValueError:
            return jsonify({"ok": False, "error": gettext("Missing row or column.")}), 400
        if rack is None or not (1 <= row <= rack.rows and 1 <= col <= rack.cols):
            return jsonify({"ok": False, "error": gettext("That position is not in the rack.")}), 400
        holder = db_session.scalar(select(CageRecord).where(
            CageRecord.rack_id_fk == rack.id, CageRecord.rack_row == row,
            CageRecord.rack_col == col, CageRecord.id != cage.id))
        if holder is not None:
            if not can_edit_cage(holder):
                return jsonify({"ok": False, "error": gettext("That position holds cage %(cage)s, which you may not move.",
                                                              cage=holder.cage_id)}), 403
            # A swap, in steps: one cage per place holds at every moment
            # (the unique index on the place), so the dropped cage leaves first.
            old = (cage.rack_id_fk, cage.rack_row, cage.rack_col)
            cage.rack_id_fk = cage.rack_row = cage.rack_col = None
            db_session.flush()
            holder.rack_id_fk, holder.rack_row, holder.rack_col = old
            db_session.flush()
        cage.rack_id_fk, cage.rack_row, cage.rack_col = rack.id, row, col
        db_session.commit()
    return jsonify({"ok": True})


def mouse_rack_payload(db_session, cages) -> dict:
    """Racks and the cages in scope, for the rack grid. Cages outside the
    scope that sit in a rack are drawn too, dimmed (and locked if you may
    not move them): a cell that looks empty must be empty, or a drop there
    would move someone else's cage without anyone seeing it."""
    racks = mouse_racks(db_session)
    items = []
    in_scope = {cage.id for cage in cages}
    others = [c for c in db_session.scalars(select(CageRecord).where(CageRecord.rack_id_fk.is_not(None)))
              if c.id not in in_scope and c.rack_row]
    for cage in [*cages, *others]:
        outside = cage.id not in in_scope
        live = [m for m in cage.mice if mouse_is_active(m)]
        strains = sorted({(m.transgene_1 or "").strip() for m in live if (m.transgene_1 or "").strip()})
        sexes = "".join(m.gender[:1] for m in live if m.gender in ("F", "M"))
        where = f"{cage.rack.name} · {cage_position_label(cage)}" if cage.rack and cage.rack_row else ""
        items.append({
            "id": cage.id,
            "label": cage.cage_id,
            "sub": cage.genotype_summary or ", ".join(strains[:2]) or cage.purpose or "",
            "badge": f"{len(live)}" if live else "",
            "tone": "other" if outside else (normalize_status(cage.purpose) if live else "inactive"),
            "locked": outside and not can_edit_cage(cage),
            "owner": cage.owner or "",
            "rack": cage.rack_id_fk,
            "row": cage.rack_row,
            "col": cage.rack_col,
            "title": "\n".join(filter(None, [
                f"Cage {cage.cage_id}" + (f" · {where}" if where else ""),
                f"{cage.owner or 'Nobody'}'s cage, not in this view" if outside else "",
                cage.purpose, f"{len(live)} live ({sexes.count('F')}F {sexes.count('M')}M)" if live else "empty",
                ", ".join(str(m.mouse_id) for m in live[:12]),
            ])),
            "search": " ".join([cage.cage_id, cage.cage_location, cage.purpose, cage.owner,
                                cage.genotype_summary, *strains, *(str(m.mouse_id) for m in live)]).lower(),
            "edit": {"data-record-edit": "cage-dialog", "data-record-payload": json.dumps({
                "id": cage.id, "_label": cage.cage_id, "_locked": not can_edit_cage(cage),
                "rack_id": cage.rack_id_fk or "", "position": cage_position_label(cage),
                "cage_location": cage.cage_location, "purpose": cage.purpose, "room": cage.room,
                "card_id": cage.card_id, "genotype_summary": cage.genotype_summary,
                "location_detail": cage.location_detail, "notes": cage.notes,
                "date_give_birth": cage.date_give_birth.isoformat() if cage.date_give_birth else "",
            })},
        })
    return {
        "racks": [{"id": r.id, "name": r.name, "rows": r.rows, "cols": r.cols,
                   "naming": rack_naming_payload(r),
                   "can_edit": access.can_edit_rack(r),
                   "edit": {"data-record-payload": json.dumps({
                       "id": r.id, "_label": r.name, "_locked": not access.can_edit_rack(r),
                       "name": r.name, "rows": r.rows,
                       "cols": r.cols, "room": r.room,
                       **{f"naming_{k}": v for k, v in rack_naming_payload(r).items()}})}} for r in racks],
        "items": items,
        "create": {"attrs": {"data-record-edit": "cage-dialog"}, "payload": {"purpose": "Experiments"},
                   "rack_field": "rack_id", "text_field": "position"},
    }


# Plain text fields a cage form may carry. A form only changes what it
# sends: the cage sheet posts its row's cells, the dialog every field.
CAGE_TEXT_FIELDS = ("purpose", "notes", "card_id", "genotype_summary", "location_detail", "room")


def renumber_cage(db_session, cage, raw) -> str | None:
    """Give `cage` the number typed over it in the cage sheet. Its mice
    follow (they point at the cage, not at its number); a number another
    cage has, or none at all, is refused."""
    code = (raw or "").strip()
    if code == cage.cage_id:
        return None
    if not code:
        return gettext("Cage %(cage)s needs a number; it was left as it was.", cage=cage.cage_id)
    if code.lower() == "new":
        return gettext("“new” takes the next free number for a new cage; type the number you want instead.")
    if len(code) > 80:
        return gettext("A cage number can be at most 80 characters.")
    taken = db_session.scalar(select(CageRecord.id).where(CageRecord.cage_id == code, CageRecord.id != cage.id))
    if taken:
        return gettext("There is already a cage %(code)s; cage %(cage)s kept its number.", code=code, cage=cage.cage_id)
    cage.cage_id = code
    return None


def apply_cage_form(db_session, cage, form) -> str | None:
    """Write a cage form onto `cage`; return an error message instead of
    saving something wrong. Rack, position and location note also change
    from the mouse sheet and the rack grid, so they are only written when
    they differ from the row's `_was` copy (app/formutil.py)."""
    if "cage_id" in form and form_changed(form, "cage_id"):
        refused = renumber_cage(db_session, cage, form.get("cage_id"))
        if refused:
            return refused
    for field in CAGE_TEXT_FIELDS:
        if field in form:
            setattr(cage, field, (form.get(field) or "").strip())
    if "cage_location" in form and form_changed(form, "cage_location"):
        cage.cage_location = (form.get("cage_location") or "").strip()
    if "date_give_birth" in form:
        refused = future_birth(form, "date_give_birth", "A litter's birth date")
        if refused:
            return refused
        cage.date_give_birth = parse_date(form.get("date_give_birth"))
    # Sharing is for a breeder cage only, and is its owner's (or an
    # admin's) to decide; on any other cage the field means nothing.
    # Shared with the lab ("1") or with one project group ("g<id>").
    if "is_shared" in form and access.can_be_shared(cage):
        if project_groups.differs(cage, form.get("is_shared")):
            if not access.can_set_sharing(cage):
                return gettext("Only %(owner)s or an admin can make cage %(cage)s shared or personal.",
                               owner=cage.owner or gettext("its owner"), cage=cage.cage_id)
            refused = project_groups.apply(cage, form.get("is_shared"))
            if refused:
                return refused
    if "owner" in form and form_changed(form, "owner"):
        owner = (form.get("owner") or "").strip()
        if owner != (cage.owner or "") and not (access.can_manage(cage) or access.is_care()):
            # Else anyone could take a shared cage and then make it personal.
            return gettext("Only %(owner)s or an admin can give cage %(cage)s to someone else.",
                           owner=cage.owner or gettext("its owner"), cage=cage.cage_id)
        if owner and owner not in current_lab_usernames(db_session):
            return gettext("“%(owner)s” is not a lab member. Pick a username from the list.", owner=owner)
        cage.owner = owner
    if "rack_id" in form and form_changed(form, "rack_id", "position"):
        return apply_cage_position(db_session, cage, form.get("rack_id"), form.get("position"))
    return None


@app.route("/colony/cages/<int:cage_row_id>/update", methods=["POST"])
@login_required
def update_cage(cage_row_id: int):
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        if cage is None:
            flash(gettext("That cage no longer exists."), "error")
            return autosave_response("cages")
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        error = apply_cage_form(db_session, cage, request.form)
        if error:
            db_session.rollback()
            flash(error, "error")
            return autosave_response("cages")
        db_session.commit()
        state = cage_state(cage)
        row = {"active": cage_is_active(cage), "values": cage_sheet_values(cage)}
    result = autosave_response("cages")
    if request.headers.get("X-Autosave") == "1" and not isinstance(result, tuple):
        # "cage" is what the mouse sheet syncs its rows from; "row" is the
        # static/sheet.js shape the cage sheet reads.
        return jsonify({"ok": True, "cage": state, "row": row})
    return result


# ---- Batch actions on ticked cages -----------------------------------------

CAGE_BULK_LABELS = {"purpose": "Set purpose", "owner": "Set owner", "rack": "Moved",
                    "shared": "Sharing set", "retire": "Retired"}


@app.route("/colony/cages/bulk", methods=["POST"])
@login_required
def bulk_cages():
    """Batch actions on ticked cages: set purpose or owner, move to a rack
    (unplaced there), mark shared or not, retire. Applied to the cages you
    may edit, skipping the rest; one batch, so /batches can undo it."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    back = url_for("colony", view="cages", scope=access.resolve_scope(form.get("scope")))
    if action not in CAGE_BULK_LABELS:
        flash(gettext("Pick an action."), "error")
        return redirect(back)
    ids = [int(v) for v in form.getlist("selected_ids") if v.isdigit()]
    changed = skipped = unshareable = 0
    blocked: list[str] = []
    with SessionLocal() as db_session:
        rack = None
        if action == "owner" and value not in current_lab_usernames(db_session):
            flash(gettext("“%(value)s” is not a lab member, so no owner was changed. Pick a username from the list.",
                          value=value or gettext("(blank)")), "error")
            return redirect(back)
        share_group = project_groups.parse(value)[1] if action == "shared" else None
        if share_group is not None and not project_groups.may_share_with(share_group):
            flash(project_groups.refusal(share_group), "error")
            return redirect(back)
        if action == "rack" and value:
            rack = db_session.get(MouseRack, int(value)) if value.isdigit() else None
            if rack is None:
                flash(gettext("That rack no longer exists."), "error")
                return redirect(back)
        what = {"purpose": f"set cage purpose = {value or '(blank)'}",
                "owner": f"set cage owner = {value}",
                "rack": f"move cages to {rack.name if rack else '(no rack)'}",
                "shared": (f"share cages with {project_groups.name_of(share_group)}" if share_group
                           else "mark cages shared" if value == "1" else "mark cages not shared"),
                "retire": "retire cages"}[action]
        cages = db_session.scalars(select(CageRecord).where(CageRecord.id.in_(ids))).all() if ids else []
        with audit.batch(db_session, "update", what, "mouse_cages") as batch_row:
            for cage in cages:
                if not can_edit_cage(cage):
                    skipped += 1
                    continue
                if action == "purpose":
                    cage.purpose = value
                elif action == "owner":
                    if value != (cage.owner or "") and not (access.can_manage(cage) or access.is_care()):
                        skipped += 1
                        continue
                    cage.owner = value
                elif action == "shared":
                    # Only by its owner or an admin.
                    if not access.can_set_sharing(cage):
                        unshareable += 1
                        continue
                    project_groups.apply(cage, value)
                elif action == "rack":
                    if rack is not None and cage.rack_id_fk == rack.id:
                        continue
                    # Into the rack without a position: dragging on the
                    # rack grid places each one.
                    cage.rack_id_fk = rack.id if rack else None
                    cage.rack_row = cage.rack_col = None
                elif action == "retire":
                    living = [m for m in cage.mice if mouse_is_active(m)]
                    if living:
                        blocked.append(ngettext("cage %(cage)s still holds %(num)s living mouse",
                                                "cage %(cage)s still holds %(num)s living mice",
                                                len(living), cage=cage.cage_id))
                        continue
                    # Retired: not a breeding or shared cage any more, and
                    # its rack position is free for the next one.
                    cage.purpose = "Retired"
                    cage.is_shared = False
                    cage.share_group_id = None
                    cage.active_override = False
                    cage.rack_id_fk = cage.rack_row = cage.rack_col = None
                changed += 1
            batch_row.record_count = changed
        db_session.commit()
    what = gettext(CAGE_BULK_LABELS[action])
    if changed and skipped:
        flash(ngettext("%(what)s on %(num)s cage. %(skipped)s skipped — not yours to edit.",
                       "%(what)s on %(num)s cages. %(skipped)s skipped — not yours to edit.",
                       changed, what=what, skipped=skipped), "success")
    elif changed:
        flash(ngettext("%(what)s on %(num)s cage.", "%(what)s on %(num)s cages.", changed, what=what), "success")
    elif skipped:
        flash(ngettext("Nothing changed — %(num)s cage are not yours to edit.",
                       "Nothing changed — %(num)s cages are not yours to edit.", skipped), "error")
    elif not blocked and not unshareable:
        flash(gettext("Nothing was changed."), "error")
    if unshareable:
        flash(ngettext("%(num)s cage left as they were: only a cage's owner or an admin can share it or make it personal.",
                       "%(num)s cages left as they were: only a cage's owner or an admin can share it or make it personal.", unshareable), "error")
    if blocked:
        flash(gettext("Not retired: %(cages)s. Move or end its mice first.", cages="; ".join(blocked)), "error")
    return redirect(back)


@app.route("/colony/cages/<int:cage_row_id>/add-mouse", methods=["POST"])
@login_required
def add_mouse_from_cage(cage_row_id: int):
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        if cage is None:
            return redirect(url_for("colony", view="cages"))
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        requested_mouse_id = request.form.get("mouse_id", "").strip()
        if not requested_mouse_id.isdigit():
            flash(gettext("Enter the number of an existing mouse."), "error")
            return redirect(url_for("colony", view="cages"))
        mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == int(requested_mouse_id)))
        if mouse is None:
            flash(gettext("Mouse %(mouse)s was not found.", mouse=requested_mouse_id), "error")
            return redirect(url_for("colony", view="cages"))
        # Moving a mouse changes the mouse, not just the cage.
        if not can_edit_mouse(mouse):
            flash(gettext("Mouse %(mouse)s was not moved. %(reason)s", mouse=mouse.mouse_id,
                          reason=access.reason_denied(mouse)), "error")
            return redirect(url_for("colony", view="cages"))
        mouse.cage_id_fk = cage.id  # the column, so the audit log records the move
        db_session.commit()
        flash(gettext("Moved mouse %(mouse)s into cage %(cage)s.", mouse=mouse.mouse_id, cage=cage.cage_id), "success")
    return redirect(url_for("colony", view="cages"))


@app.route("/colony/cages/<int:cage_row_id>/give-birth", methods=["POST"])
@login_required
def cage_give_birth(cage_row_id: int):
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        if cage is not None:
            # The date the person confirmed in the Litter born dialog (today
            # when a form sends none).
            refused = future_birth(request.form, "date_give_birth", "A litter's birth date")
            if refused:
                flash(refused, "error")
                return _back_to_colony("cages")
            born = parse_date(request.form.get("date_give_birth")) or date.today()
            before = cage.date_give_birth
            cage.date_give_birth = born
            db_session.commit()
            wean = fmt_day(born + timedelta(days=WEAN_OFFSET_DAYS))
            if born == date.today():
                flash(gettext("Recorded a litter born today in cage %(cage)s: weaning is due %(wean)s.",
                              cage=cage.cage_id, wean=wean), "success")
            else:
                flash(gettext("Recorded a litter born on %(born)s in cage %(cage)s: weaning is due %(wean)s.",
                              born=fmt_day(born), cage=cage.cage_id, wean=wean), "success")
            if before and before != born:
                # The cage holds one litter date: say what the new one replaced,
                # so a litter still waiting to be weaned isn't forgotten.
                flash(gettext("It replaces the litter born %(born)s (weaning was due %(wean)s). If those pups are still in the cage, wean them first.", born=fmt_day(before),
                              wean=fmt_day(before + timedelta(days=WEAN_OFFSET_DAYS))), "warning")
    return _back_to_colony("cages")


@app.route("/colony/cages/<int:cage_row_id>/genotyping", methods=["POST"])
@login_required
def cage_genotyping(cage_row_id: int):
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        if cage is None:
            return redirect(url_for("colony", view="cages"))
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        raw_pups = (request.form.get("total_pups") or "").strip()
        if not raw_pups.isdigit() or not 1 <= int(raw_pups) <= 40:
            flash(gettext("Pups must be a whole number from 1 to 40, not “%(value)s”. No litter was created.",
                          value=raw_pups or gettext("(blank)")), "error")
            return redirect(url_for("colony", view="cages"))
        total_pups = int(raw_pups)
        father_info = request.form.get("father_info", "").strip()
        mother_info = request.form.get("mother_info", "").strip()
        litter = LitterRecord(litter_id=generate_litter_id(db_session, cage),
                              date_of_birth=cage.date_give_birth or date.today())
        db_session.add(litter)
        litter.father_info = father_info
        litter.mother_info = mother_info
        litter.total_pups = total_pups
        litter.cohort_name = f"Cage {cage.cage_id}"
        litter.notes = f"Generated by genotyping for cage {cage.cage_id}."
        db_session.flush()  # the litter's id, for the pups' column
        for _ in range(total_pups):
            mouse = MouseRecord(
                mouse_id=next_mouse_id(db_session),
                status="geno",
                owner=g.user.username,
            )
            set_mouse_cage(mouse, cage)
            set_mouse_litter(mouse, litter)
            db_session.add(mouse)
            db_session.flush()
        db_session.commit()
        flash(gettext("Created litter %(litter)s with %(pups)s pups in cage %(cage)s.", litter=litter.litter_id,
                      pups=total_pups, cage=cage.cage_id), "success")
    return redirect(url_for("colony", view="cages"))


@app.route("/colony/cages/<int:cage_row_id>/wean", methods=["POST"])
@login_required
def cage_wean(cage_row_id: int):
    # A batch, so Batch history can undo it like any other change to many records.
    with SessionLocal() as db_session, audit.batch(db_session, "update", "wean a cage", "mouse_cages") as batch_row:
        cage = db_session.get(CageRecord, cage_row_id)
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        if cage is not None:
            young = _too_young_to_wean(cage)
            if young:
                flash(young, "error")
                return _back_to_colony("cages")
            mark_weaned(cage)
            batch_row.description, batch_row.record_count = f"wean cage {cage.cage_id}"[:200], 1
            db_session.commit()
            flash(gettext("Cage %(cage)s is weaned.", cage=cage.cage_id), "success")
    return _back_to_colony("cages")


def _too_young_to_wean(cage) -> str | None:
    """Why weaning this cage now needs a second look, or None. The form
    sends early=1 once the person has confirmed (colony.html asks)."""
    if request.form.get("early"):
        return None
    born = cage.date_give_birth
    if born is None:
        litter = cage_pup_litter(cage)
        born = litter.date_of_birth if litter else None
    if born is None:
        return None
    age = (date.today() - born).days
    if age >= MIN_WEAN_AGE_DAYS:
        return None
    due = born + timedelta(days=WEAN_OFFSET_DAYS)
    if age < 0:
        return gettext("Cage %(cage)s's litter is dated %(born)s, in the future. Correct its date before weaning.",
                       cage=cage.cage_id, born=fmt_day(born))
    return gettext("The pups in cage %(cage)s are %(age)s days old; weaning is due %(due)s (P%(days)s). Nothing was changed. To wean them early anyway, confirm it when asked.", cage=cage.cage_id, age=age,
                   due=fmt_day(due), days=WEAN_OFFSET_DAYS)


@app.route("/colony/cages/<int:cage_row_id>/wean-distribute", methods=["POST"])
@login_required
@next_number_retried
def cage_wean_distribute(cage_row_id: int):
    """Wean the source cage and distribute its pups to new or existing cages.

    Form arrays (one entry per UI row):
      mouse_ids[]   : comma-separated mouse IDs to move
      gender[]      : M / F / Unknown — applied to those mice
      cage_id[]     : existing cage ID; if filled, mice go there (card_id ignored)
      card_id[]     : if cage_id[] empty, a fresh cage is created with this card label
    Empty rows are skipped. Mice not listed stay in the source cage.

    Only mice that are in the source cage and that you may edit move; new
    cages are yours, and an existing destination must be a cage you may
    edit. Everything else is reported, not silently done.
    """
    mouse_ids_field = request.form.getlist("mouse_ids[]")
    genders_field = request.form.getlist("gender[]")
    cage_ids_field = request.form.getlist("cage_id[]")
    card_ids_field = request.form.getlist("card_id[]")
    rows = list(zip(mouse_ids_field, genders_field, cage_ids_field, card_ids_field))

    moved_count = 0
    problems: list[str] = []
    source_cage_label = ""

    with SessionLocal() as db_session, audit.batch(db_session, "update", "wean and distribute a cage",
                                                   "mouse_cages") as batch_row:
        source_cage = db_session.get(CageRecord, cage_row_id)
        if source_cage is None:
            flash(gettext("Cage not found."), "error")
            return redirect(url_for("colony", view="cages"))
        blocked = deny(source_cage, "cages")
        if blocked:
            return blocked
        source_cage_label = source_cage.cage_id
        young = _too_young_to_wean(source_cage)
        if young:
            flash(young, "error")
            return redirect(url_for("colony", view="cages"))
        # Before the pups move out: which litter they are is read from the cage.
        mark_weaned(source_cage)

        for mouse_ids_str, gender_raw, cage_id_input, card_id_input in rows:
            mouse_ids_str = (mouse_ids_str or "").strip()
            if not mouse_ids_str:
                continue
            movers: list[MouseRecord] = []
            for token in mouse_ids_str.replace(";", ",").split(","):
                token = token.strip()
                if not token:
                    continue
                if not token.isdigit():
                    problems.append(gettext("“%(token)s” is not a mouse number", token=token))
                    continue
                mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == int(token)))
                if mouse is None:
                    problems.append(gettext("mouse %(mouse)s does not exist", mouse=token))
                elif mouse.cage_id_fk != source_cage.id:
                    problems.append(gettext("mouse %(mouse)s is not in cage %(cage)s", mouse=token,
                                            cage=source_cage_label))
                elif not can_edit_mouse(mouse):
                    problems.append(gettext("mouse %(mouse)s is %(owner)s’s", mouse=token,
                                            owner=mouse.owner or gettext("someone else")))
                else:
                    movers.append(mouse)
            if not movers:
                continue

            cage_id_input = (cage_id_input or "").strip()
            card_id_input = (card_id_input or "").strip()
            gender_value = (gender_raw or "").strip()

            if cage_id_input:
                target_cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == cage_id_input))
                if target_cage is None:
                    target_cage = CageRecord(cage_id=cage_id_input, owner=g.user.username, card_id=card_id_input)
                    db_session.add(target_cage)
                    db_session.flush()
                elif not can_edit_cage(target_cage):
                    problems.append(gettext("cage %(cage)s is %(owner)s’s, so %(mice)s stayed", cage=cage_id_input,
                                            owner=target_cage.owner or gettext("someone else"),
                                            mice=", ".join(str(m.mouse_id) for m in movers)))
                    continue
            else:
                target_cage = new_owned_cage(db_session, card_id=card_id_input)

            for mouse in movers:
                mouse.cage_id_fk = target_cage.id  # the column, so the audit log records the move
                if gender_value:
                    mouse.gender = gender_value
                moved_count += 1

        source_cage.date_give_birth = None
        batch_row.description = f"wean cage {source_cage_label} ({moved_count} mice moved)"[:200]
        batch_row.record_count = moved_count + 1
        db_session.commit()

    if moved_count:
        flash(gettext("Distributed %(count)s mice and weaned cage %(cage)s.", count=moved_count,
                      cage=source_cage_label), "success")
    else:
        flash(gettext("Cage %(cage)s weaned (no mice were moved).", cage=source_cage_label), "success")
    if problems:
        flash(gettext("Not moved: %(problems)s.", problems="; ".join(problems)), "error")
    return redirect(url_for("colony", view="cages"))


def _litter_refusal(litter, view: str = "litters"):
    if access.can_edit_litter(litter):
        return None
    owners = sorted({m.owner for m in litter.mice if m.owner and not can_edit_mouse(m)})
    flash(gettext("Litter %(litter)s has mice owned by %(owners)s, so only they or an admin can change it.",
                  litter=litter.litter_id, owners=", ".join(owners) or gettext("someone else")), "error")
    return autosave_response(view)


@app.route("/colony/litters/create", methods=["POST"])
@login_required
@next_number_retried
def create_litter():
    """Create a litter. A blank ID takes the next free number; an ID that is
    already a litter is refused rather than overwriting that litter."""
    requested = (request.form.get("litter_id") or "").strip()
    refused = future_birth(request.form)
    if refused:
        flash(refused, "error")
        return redirect(url_for("colony", view="litters", scope=request.form.get("scope") or None))
    with SessionLocal() as db_session:
        if requested and db_session.scalar(select(LitterRecord.id).where(LitterRecord.litter_id == requested)):
            flash(gettext("Litter %(litter)s already exists; nothing was changed. Leave the ID blank to take the next free one.", litter=requested), "error")
            return redirect(url_for("colony", view="litters", scope=request.form.get("scope") or None))
        litter = LitterRecord(litter_id=requested or next_litter_id(db_session),
                              date_of_birth=parse_date(request.form.get("date_of_birth")))
        litter.cohort_name = request.form.get("cohort_name", "").strip()
        litter.notes = request.form.get("notes", "").strip()
        db_session.add(litter)
        db_session.commit()
        flash(gettext("Created litter %(litter)s.", litter=litter.litter_id), "success")
    return redirect(url_for("colony", view="litters", scope=request.form.get("scope") or None))


@app.route("/colony/litters/<int:litter_row_id>/update", methods=["POST"])
@login_required
def update_litter(litter_row_id: int):
    with SessionLocal() as db_session:
        litter = db_session.get(LitterRecord, litter_row_id)
        if litter is None:
            return autosave_response("litters")
        refused = _litter_refusal(litter)
        if refused:
            return refused
        form = request.form
        refused = future_birth(form)
        if refused:
            flash(refused, "error")
            return autosave_response("litters")
        if "date_of_birth" in form:
            litter.date_of_birth = parse_date(form.get("date_of_birth"))
        for field in ("cohort_name", "notes", "father_info", "mother_info"):
            if field in form:
                setattr(litter, field, (form.get(field) or "").strip())
        if "total_pups" in form:
            raw = (form.get("total_pups") or "").strip()
            if raw and not raw.isdigit():
                flash(gettext("Pups must be a whole number, not “%(value)s”.", value=raw), "error")
                return autosave_response("litters")
            litter.total_pups = int(raw or 0)
        db_session.commit()
    return autosave_response("litters")


@app.route("/colony/litters/<int:litter_row_id>/add-existing-mouse", methods=["POST"])
@login_required
def add_existing_mouse_to_litter(litter_row_id: int):
    with SessionLocal() as db_session:
        litter = db_session.get(LitterRecord, litter_row_id)
        if litter is None:
            return redirect(url_for("colony", view="litters"))
        refused = _litter_refusal(litter)
        if refused:
            return refused
        requested_mouse_id = request.form.get("mouse_id", "").strip()
        if not requested_mouse_id.isdigit():
            flash(gettext("Enter the number of an existing mouse."), "error")
            return redirect(url_for("colony", view="litters"))
        mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == int(requested_mouse_id)))
        if mouse is None:
            flash(gettext("Mouse %(mouse)s was not found.", mouse=requested_mouse_id), "error")
            return redirect(url_for("colony", view="litters"))
        # Joining a litter changes the mouse's date of birth.
        if not can_edit_mouse(mouse):
            flash(gettext("Mouse %(mouse)s was not added. %(reason)s", mouse=mouse.mouse_id,
                          reason=access.reason_denied(mouse)), "error")
            return redirect(url_for("colony", view="litters"))
        mouse.litter_id_fk = litter.id  # the column, so the audit log records it
        db_session.commit()
        flash(gettext("Mouse %(mouse)s is now in litter %(litter)s.", mouse=mouse.mouse_id, litter=litter.litter_id),
              "success")
    return redirect(url_for("colony", view="litters"))


def strain_denied(strain) -> str:
    who = (strain.created_by or "").strip()
    if who:
        return gettext("Strain %(strain)s was added by %(who)s; only they or an admin can change or remove it.",
                       strain=strain.strain_name, who=who)
    return gettext("Strain %(strain)s predates recorded creators; only an admin can change or remove it.",
                   strain=strain.strain_name)


PRESETS_DENIED = "Only an admin can add, rename or remove dropdown choices. You can still pick them everywhere."


@app.route("/colony/strains/create", methods=["POST"])
@login_required
def create_strain():
    strain_name = (request.form.get("strain_name") or "").strip()
    with SessionLocal() as db_session:
        if not strain_name:
            flash(gettext("A strain needs a name."), "error")
            return redirect(url_for("colony", view="strains"))
        existing = db_session.scalar(select(StrainRecord).where(
            func.lower(StrainRecord.strain_name) == strain_name.lower()))
        if existing is not None:
            flash(gettext("There is already a strain called %(strain)s.", strain=existing.strain_name), "error")
            return redirect(url_for("colony", view="strains"))
        db_session.add(
            StrainRecord(
                strain_number=request.form.get("strain_number", "").strip(),
                strain_name=strain_name,
                strain_background=request.form.get("strain_background", "").strip(),
                supplier=request.form.get("supplier", "").strip(),
                description=request.form.get("description", "").strip(),
                created_by=g.user.username,
            )
        )
        db_session.commit()
        flash(gettext("Added strain %(strain)s.", strain=strain_name), "success")
    return redirect(url_for("colony", view="strains"))


@app.route("/colony/strains/<int:strain_row_id>/update", methods=["POST"])
@login_required
def update_strain(strain_row_id: int):
    with SessionLocal() as db_session:
        strain = db_session.get(StrainRecord, strain_row_id)
        if strain is None:
            flash(gettext("That strain no longer exists."), "error")
            return autosave_response("strains")
        if not access.can_edit_strain(strain):
            flash(strain_denied(strain), "error")
            return autosave_response("strains")
        new_name = request.form.get("strain_name", strain.strain_name).strip()
        if not new_name:
            flash(gettext("A strain needs a name."), "error")
            return autosave_response("strains")
        if new_name != strain.strain_name:
            conflict = db_session.scalar(select(StrainRecord).where(
                func.lower(StrainRecord.strain_name) == new_name.lower(), StrainRecord.id != strain.id))
            if conflict is not None:
                flash(gettext("There is already a strain called %(strain)s; this one was not renamed.",
                              strain=conflict.strain_name), "error")
                return autosave_response("strains")
            strain.strain_name = new_name
        for field in ("strain_number", "strain_background", "supplier", "description"):
            if field in request.form:
                setattr(strain, field, request.form.get(field, "").strip())
        db_session.commit()
    return autosave_response("strains")


@app.route("/colony/strains/<int:strain_row_id>/delete", methods=["POST"])
@login_required
def delete_strain(strain_row_id: int):
    """Remove a strain from the reference list. Mice keep their transgene
    text; the strain only stops being offered as a suggestion."""
    with SessionLocal() as db_session:
        strain = db_session.get(StrainRecord, strain_row_id)
        if strain is not None and not access.can_edit_strain(strain):
            flash(strain_denied(strain), "error")
            return redirect(url_for("colony", view="strains"))
        if strain is not None:
            name = strain.strain_name
            db_session.delete(strain)
            db_session.commit()
            flash(gettext("Removed strain %(strain)s. Mice that carry it are unchanged.", strain=name), "success")
    return redirect(url_for("colony", view="strains"))


@app.route("/colony/options/create", methods=["POST"])
@login_required
def create_option():
    field_name = (request.form.get("field_name") or "").strip()
    option_value = (request.form.get("option_value") or "").strip()
    if not access.can_edit_presets():
        flash(gettext(PRESETS_DENIED), "error")
        return redirect(url_for("colony", view="settings"))
    with SessionLocal() as db_session:
        if not field_name or not option_value:
            flash(gettext("Pick a column and type a value."), "error")
            return redirect(url_for("colony", view="settings"))
        existing = db_session.scalar(
            select(DropdownOption).where(
                DropdownOption.field_name == field_name,
                func.lower(DropdownOption.option_value) == option_value.lower(),
            )
        )
        if existing is not None:
            flash(gettext("“%(value)s” is already a %(column)s preset.", value=existing.option_value,
                          column=i18n.translate_value(field_name)), "error")
            return redirect(url_for("colony", view="settings"))
        db_session.add(DropdownOption(field_name=field_name, option_value=option_value))
        db_session.commit()
        flash(gettext("Saved “%(value)s” as a %(column)s preset.", value=option_value,
                      column=i18n.translate_value(field_name)), "success")
    return redirect(url_for("colony", view="settings"))


@app.route("/colony/options/<int:option_id>/update", methods=["POST"])
@login_required
def update_option(option_id: int):
    new_value = request.form.get("option_value", "").strip()
    if not access.can_edit_presets():
        flash(gettext(PRESETS_DENIED), "error")
        return autosave_response("settings")
    with SessionLocal() as db_session:
        option = db_session.get(DropdownOption, option_id)
        if option is None:
            flash(gettext("That preset no longer exists."), "error")
            return autosave_response("settings")
        if not new_value:
            flash(gettext("A preset cannot be blank; delete it instead."), "error")
            return autosave_response("settings")
        duplicate = db_session.scalar(
            select(DropdownOption).where(
                DropdownOption.field_name == option.field_name,
                func.lower(DropdownOption.option_value) == new_value.lower(),
                DropdownOption.id != option.id,
            )
        )
        if duplicate is not None:
            flash(gettext("“%(value)s” is already a %(column)s preset.", value=duplicate.option_value,
                          column=i18n.translate_value(option.field_name)), "error")
            return autosave_response("settings")
        option.option_value = new_value
        db_session.commit()
    return autosave_response("settings")


@app.route("/colony/options/<int:option_id>/delete", methods=["POST"])
@login_required
def delete_option(option_id: int):
    if not access.can_edit_presets():
        flash(gettext(PRESETS_DENIED), "error")
        return redirect(url_for("colony", view="settings"))
    with SessionLocal() as db_session:
        option = db_session.get(DropdownOption, option_id)
        if option is not None:
            column, value = i18n.translate_value(option.field_name), option.option_value
            db_session.delete(option)
            db_session.commit()
            flash(gettext("Removed the %(column)s preset “%(value)s”.", column=column, value=value), "success")
    return redirect(url_for("colony", view="settings"))


# ---------------------------------------------------------------------------
# Orders and samples now live in the lab inventory engine (inventory_routes).
# The old addresses keep working and land on the first inventory of that kind.
# ---------------------------------------------------------------------------


def _inventory_redirect(kind: str):
    from . import inventory_service as inventories
    with SessionLocal() as db_session:
        module = inventories.first_of_kind(db_session, kind)
        key = module.key if module else None
    if key is None:
        flash(gettext("There is no orders inventory yet. Add one from Add database.") if kind == "orders" else
              gettext("There is no samples inventory yet. Add one from Add database.") if kind == "samples" else
              f"There is no {kind} inventory yet. Add one from Add database.", "info")
        return redirect(url_for("organisms.index"))
    return redirect(url_for("inventory.module", key=key))


@app.route("/orders")
@login_required
def orders():
    return _inventory_redirect("orders")


@app.route("/samples")
@login_required
def samples():
    return _inventory_redirect("samples")


@app.route("/calendar", methods=["GET"])
@login_required
def calendar():
    """Calendar page — TOAST UI Calendar mount + custom list view.
    Data is fetched async from /calendar/events.json so the page itself is light."""
    with SessionLocal() as db_session:
        cal_data = {
            "me": g.user.username,
            "admin": g.user.role == "admin",
            "people": lab_calendar.people(db_session),
            "equipment": lab_calendar.equipment_list(db_session),
            "experiments": [{"id": e.id, "name": e.name} for e in db_session.scalars(
                select(Experiment).where(Experiment.status.in_(["active", "paused", "planned"]))
                .order_by(Experiment.name))],
            "colors": lab_calendar.COLORS,
        }
    return render_template("calendar.html", cal_data=cal_data)


def _serialize_calendar_event(e: CalendarEvent) -> dict:
    """Translate a CalendarEvent row into TOAST UI Calendar's schedule shape."""
    if e.start_at and e.end_at:
        start_iso, end_iso = e.start_at.isoformat(), e.end_at.isoformat()
        is_all_day = bool(e.is_all_day)
    else:
        d = e.event_date
        start_iso = datetime.combine(d, datetime.min.time()).isoformat()
        end_iso = datetime.combine(d, datetime.max.time()).isoformat()
        is_all_day = True
    return {
        "id": f"event-{e.id}",
        "kind": "event",
        "calendarId": "events",
        "title": e.title or "(untitled)",
        "category": "allday" if is_all_day else "time",
        "isAllday": is_all_day,
        "start": start_iso,
        "end": end_iso,
        "backgroundColor": e.color or "#7c3aed",
        "borderColor": e.color or "#7c3aed",
        "body": e.description or "",
        "raw": {
            "rowId": e.id,
            "owner": e.owner or "",
            "animalId": e.animal_id_fk,
            "eventType": e.event_type or "",
            # Whose event: "0" its owner's alone, "1" the lab's, "g<id>" a group's.
            "audience": project_groups.record_value(e),
            "audienceLabel": project_groups.label(e.is_shared, e.share_group_id, personal="", lab=""),
            "readOnly": has_request_context() and g.get("user") is not None and not lab_calendar.event_can_edit(e),
        },
    }


def task_visible_clause(user=None):
    """The to-dos a person sees: their own, the lab's, and their project
    groups' (app/groups.py)."""
    me = access.username(user)
    mine = sorted(project_groups.ids_of(user))
    lab_wide = TaskItem.is_shared.is_(True) & TaskItem.share_group_id.is_(None)
    clause = (TaskItem.owner == me) | lab_wide
    if mine:
        clause = clause | (TaskItem.is_shared.is_(True) & TaskItem.share_group_id.in_(mine))
    return clause


def task_can_edit(t, user=None) -> bool:
    """A lab to-do is anyone's to tick off and change, a group's its
    members' (and admins'); a personal one its owner's alone, not even an
    admin's."""
    if lab_calendar.task_is_personal(t):
        return (t.owner or "") == access.username(user)
    return access.can_edit(t, user, shared=project_groups.record_shared_with(t, user))


def task_can_manage(t, user=None) -> bool:
    """Delete it or change whose it is: its owner (a shared one also an admin)."""
    if lab_calendar.task_is_personal(t):
        return (t.owner or "") == access.username(user)
    return access.can_manage(t, user)


def task_denied(t) -> str:
    if t.is_shared and t.share_group_id:
        return gettext("That to-do is %(group)s's.",
                       group=project_groups.name_of(t.share_group_id) or gettext("a project group"))
    return gettext("That to-do is %(owner)s's. Ask them, or an admin, to make the change.",
                   owner=t.owner or gettext("someone else"))


def _serialize_task(t: TaskItem) -> dict:
    """Translate a TaskItem row into TOAST UI Calendar's schedule shape, with
    extra metadata our custom renderer uses to draw the done-checkbox."""
    if t.start_at and t.end_at:
        start_iso, end_iso = t.start_at.isoformat(), t.end_at.isoformat()
        is_all_day = False
    elif t.due_date:
        d = t.due_date
        start_iso = datetime.combine(d, datetime.min.time()).isoformat()
        end_iso = datetime.combine(d, datetime.max.time()).isoformat()
        is_all_day = True
    else:
        # No date at all → put it on today, all-day, so it doesn't get lost.
        d = date.today()
        start_iso = datetime.combine(d, datetime.min.time()).isoformat()
        end_iso = datetime.combine(d, datetime.max.time()).isoformat()
        is_all_day = True
    is_done = bool(t.done_at) or (t.status or "").lower() in ("done", "completed", "closed")
    return {
        "id": f"task-{t.id}",
        "kind": "task",
        "calendarId": "tasks",
        "title": t.title or "(untitled)",
        "category": "task",  # TOAST UI native task category — shows in task strip
        "isAllday": is_all_day,
        "start": start_iso,
        "end": end_iso,
        "backgroundColor": t.color or ("#94a3b8" if is_done else "#0ea5e9"),
        "borderColor": t.color or ("#94a3b8" if is_done else "#0ea5e9"),
        "body": t.notes or "",
        "raw": {
            "rowId": t.id,
            "owner": t.owner or "",
            "status": t.status or "todo",
            "done": is_done,
            "priority": t.priority or "medium",
            # Whose to-do it is: "0" its owner's, "1" the lab's, "g<id>" a group's.
            "audience": project_groups.record_value(t),
            "audienceLabel": project_groups.label(t.is_shared, t.share_group_id, personal="", lab="Lab"),
            "readOnly": has_request_context() and g.get("user") is not None and not task_can_edit(t),
        },
    }


def calendar_items(db_session, start: date | None, end: date | None, owner: str | None = None,
                   external: bool = True) -> list[dict]:
    """Everything on the calendar between two dates, as TOAST UI schedules:
    events (repeats expanded), to-dos, mouse colony dates, the other
    organisms and supplies, protocol steps, bookings and away days
    (app/lab_calendar.py), then connected calendars. With `owner`, only that
    person's own things (their phone feed can ask for that)."""
    today = date.today()
    start = start or today - timedelta(days=400)
    end = end or today + timedelta(days=400)
    ev_q = select(CalendarEvent).where(
        ((CalendarEvent.event_date >= start) & (CalendarEvent.event_date <= end))
        | ((CalendarEvent.event_date <= end) & CalendarEvent.id.in_(select(CalendarRepeat.event_id_fk))))
    tk_q = select(TaskItem).where(
        TaskItem.due_date.is_(None) | ((TaskItem.due_date >= start) & (TaskItem.due_date <= end)))
    if owner is not None:
        ev_q = ev_q.where(CalendarEvent.owner == owner)
        tk_q = tk_q.where(TaskItem.owner == owner)
    else:
        # Someone's own events and to-dos are theirs; the lab's and a
        # group's are shared.
        ev_q = ev_q.where(lab_calendar.event_visible_clause())
        tk_q = tk_q.where(task_visible_clause())
    events = db_session.scalars(ev_q.order_by(CalendarEvent.event_date)).all()
    repeats = lab_calendar.repeats_by_event(db_session, [e.id for e in events])
    items: list[dict] = []
    for e in events:
        item = _serialize_calendar_event(e)
        repeat = repeats.get(e.id)
        if repeat is None:
            items.append(item)
            continue
        item["raw"]["repeat"] = lab_calendar.repeat_summary(repeat, e.event_date)
        for day in lab_calendar.occurrences(e.event_date, repeat, start, end):
            shift = timedelta(days=(day - e.event_date).days)
            copy = dict(item, id=f"{item['id']}@{day.isoformat()}",
                        start=(datetime.fromisoformat(item["start"]) + shift).isoformat(),
                        end=(datetime.fromisoformat(item["end"]) + shift).isoformat(),
                        raw=dict(item["raw"], occurrence=day.isoformat()))
            items.append(copy)
    items += [_serialize_task(t) for t in db_session.scalars(tk_q.order_by(TaskItem.due_date)).all()]

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())
    features = lab.request_features()
    if owner is None and features.get("colony", True):
        # Mouse colony dates carry no owner, so a personal feed leaves them out.
        items += derive_auto_calendar_items(db_session, start_dt, end_dt)
    zebrafish = zebrafish_home_summary() if features.get("zebrafish", True) else None
    items += lab_calendar.agenda_items(db_session, start, end, features, zebrafish, owner=owner)
    items += lab_calendar.protocol_items(db_session, start, end, owner=owner)
    if features.get("colony", True):
        items += experiment_steps.calendar_items(db_session, start, end, owner=owner)
    items += lab_calendar.booking_items(db_session, start, end, owner=owner)
    items += lab_calendar.absence_items(db_session, start, end, owner=owner)

    if external and g.user is not None:
        # Connected calendars are the viewer's own, and read-only here.
        subs = db_session.scalars(
            select(CalendarSubscription)
            .where(CalendarSubscription.owner == g.user.username)
            .where(CalendarSubscription.enabled == True)  # noqa: E712
        ).all()
        for sub in subs:
            items.extend(fetch_ics_subscription(sub, db_session))
        try:
            from .services import fetch_google_calendar_items  # optional, may not exist yet
            for link in db_session.scalars(
                select(GoogleCalendarLink)
                .where(GoogleCalendarLink.owner == g.user.username)
                .where(GoogleCalendarLink.enabled == True)  # noqa: E712
            ).all():
                items.extend(fetch_google_calendar_items(link, db_session, start_dt, end_dt))
        except ImportError:
            pass
        except Exception as exc:  # don't break the feed for transient Google errors
            app.logger.warning("Google Calendar fetch failed: %s", exc)
    return items


@app.route("/calendar/events.json")
@login_required
def calendar_events_json():
    """The calendar page's feed for ?start=&end= (ISO dates or date-times),
    plus who is away soon and what of theirs falls due meanwhile."""
    def _day(v):
        try:
            return date.fromisoformat((v or "")[:10]) if v else None
        except ValueError:
            return None

    with SessionLocal() as db_session:
        items = calendar_items(db_session, _day(request.args.get("start")), _day(request.args.get("end")))
        features = lab.request_features()
        cover = lab_calendar.cover_report(
            db_session, features, zebrafish_home_summary() if features.get("zebrafish", True) else None)
        db_session.commit()
        return jsonify({"ok": True, "items": items, "cover": cover})


# ---------------------------------------------------------------------------
# Calendar subscriptions (ICS) — CRUD
# ---------------------------------------------------------------------------


@app.route("/calendar/subscriptions", methods=["GET", "POST"])
@login_required
def calendar_subscriptions():
    """GET → list current user's subscriptions. POST → create a new one."""
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            name = (payload.get("name") or "").strip() or "Subscription"
            url = (payload.get("url") or "").strip()
            color = (payload.get("color") or "#10b981").strip()
            if not url:
                return jsonify({"ok": False, "error": "url required"}), 400
            # Allow webcal:// URLs — most calendars publish their iCal feed
            # with that scheme; swap to https for urllib.
            if url.startswith("webcal://"):
                url = "https://" + url[len("webcal://"):]
            sub = CalendarSubscription(owner=owner, name=name, url=url, color=color, enabled=True)
            db_session.add(sub)
            db_session.commit()
            # Eagerly fetch once so the user sees instant results.
            items = fetch_ics_subscription(sub, db_session, force=True)
            return jsonify({
                "ok": True,
                "subscription": {
                    "id": sub.id, "name": sub.name, "url": sub.url, "color": sub.color,
                    "enabled": sub.enabled, "last_error": sub.last_error,
                    "last_fetched_at": sub.last_fetched_at.isoformat() if sub.last_fetched_at else None,
                    "event_count": len(items),
                },
            })

        subs = db_session.scalars(
            select(CalendarSubscription).where(CalendarSubscription.owner == owner).order_by(CalendarSubscription.created_at)
        ).all()
        return jsonify({
            "ok": True,
            "subscriptions": [
                {
                    "id": s.id, "name": s.name, "url": s.url, "color": s.color,
                    "enabled": s.enabled, "last_error": s.last_error,
                    "last_fetched_at": s.last_fetched_at.isoformat() if s.last_fetched_at else None,
                }
                for s in subs
            ],
        })


@app.route("/calendar/subscriptions/<int:sub_id>", methods=["POST"])
@login_required
def calendar_subscription_update(sub_id: int):
    """Update a subscription (toggle enabled, rename, recolor, change URL)."""
    owner = g.user.username if g.user else ""
    payload = request.get_json(silent=True) or {}
    with SessionLocal() as db_session:
        sub = db_session.get(CalendarSubscription, sub_id)
        if sub is None or sub.owner != owner:
            return jsonify({"ok": False}), 404
        if "name" in payload: sub.name = (payload["name"] or "").strip() or sub.name
        if "color" in payload: sub.color = payload["color"] or sub.color
        if "url" in payload and payload["url"]:
            new_url = payload["url"].strip()
            if new_url.startswith("webcal://"):
                new_url = "https://" + new_url[len("webcal://"):]
            sub.url = new_url
            sub.cached_payload = ""  # invalidate cache on URL change
        if "enabled" in payload: sub.enabled = bool(payload["enabled"])
        db_session.commit()
        return jsonify({"ok": True})


@app.route("/calendar/subscriptions/<int:sub_id>/refresh", methods=["POST"])
@login_required
def calendar_subscription_refresh(sub_id: int):
    """Force a re-fetch of one subscription, bypassing the 10-min cache."""
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        sub = db_session.get(CalendarSubscription, sub_id)
        if sub is None or sub.owner != owner:
            return jsonify({"ok": False}), 404
        items = fetch_ics_subscription(sub, db_session, force=True)
        return jsonify({"ok": True, "event_count": len(items), "last_error": sub.last_error})


@app.route("/calendar/subscriptions/<int:sub_id>/delete", methods=["POST"])
@login_required
def calendar_subscription_delete(sub_id: int):
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        sub = db_session.get(CalendarSubscription, sub_id)
        if sub is None or sub.owner != owner:
            return jsonify({"ok": False}), 404
        db_session.delete(sub)
        db_session.commit()
        return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Google Calendar OAuth
# ---------------------------------------------------------------------------


def _google_redirect_uri() -> str:
    return url_for("calendar_google_callback", _external=True)


@app.route("/calendar/google/status")
@login_required
def calendar_google_status():
    """Tell the UI whether server-side OAuth is configured and whether THIS
    user has already connected. The status panel in the calendar uses this
    to pick between "Connect", "Connected — refresh/disconnect", and
    "not configured" states."""
    if not google_oauth_configured():
        return jsonify({"ok": True, "configured": False, "connected": False})
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        link = db_session.scalar(select(GoogleCalendarLink).where(GoogleCalendarLink.owner == owner))
        if link is None:
            return jsonify({"ok": True, "configured": True, "connected": False})
        return jsonify({
            "ok": True, "configured": True, "connected": True,
            "email": link.google_email or "",
            "calendar_id": link.calendar_id,
            "last_synced_at": link.last_synced_at.isoformat() if link.last_synced_at else None,
        })


@app.route("/calendar/google/connect")
@login_required
def calendar_google_connect():
    """Kick off the OAuth dance. Redirects the user to Google's consent screen."""
    if not google_oauth_configured():
        flash(gettext("Google Calendar integration isn't configured on this server."), "error")
        return redirect(url_for("calendar"))
    from google_auth_oauthlib.flow import Flow
    redirect_uri = _google_redirect_uri()
    flow = Flow.from_client_config(
        google_client_config(redirect_uri),
        scopes=GOOGLE_OAUTH_SCOPES,
        redirect_uri=redirect_uri,
    )
    # access_type=offline → we get a refresh_token; prompt=consent ensures we
    # always receive a fresh refresh_token even if the user already approved.
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["google_oauth_state"] = state
    return redirect(auth_url)


@app.route("/calendar/google/callback")
@login_required
def calendar_google_callback():
    """OAuth redirect target. Exchange the code → tokens, save the link."""
    if not google_oauth_configured():
        return redirect(url_for("calendar"))
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build

    state = session.pop("google_oauth_state", None)
    redirect_uri = _google_redirect_uri()
    flow = Flow.from_client_config(
        google_client_config(redirect_uri),
        scopes=GOOGLE_OAUTH_SCOPES,
        redirect_uri=redirect_uri,
        state=state,
    )
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:
        app.logger.warning("Google OAuth callback failed: %s", exc)
        flash(gettext("Google sign-in failed: %(error)s", error=exc), "error")
        return redirect(url_for("calendar"))

    creds = flow.credentials
    # Fetch the user's email so we can show "Connected as alice@…".
    email = ""
    try:
        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        email = svc.userinfo().get().execute().get("email", "")
    except Exception:
        pass

    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        link = db_session.scalar(select(GoogleCalendarLink).where(GoogleCalendarLink.owner == owner))
        if link is None:
            link = GoogleCalendarLink(owner=owner)
        link.refresh_token = creds.refresh_token or link.refresh_token
        link.access_token = creds.token or ""
        link.token_expiry = creds.expiry
        link.google_email = email
        link.calendar_id = "primary"
        link.enabled = True
        if not link.refresh_token:
            db_session.expunge(link)
            flash(gettext("Google didn't return a refresh token. Revoke access at myaccount.google.com/permissions and try connecting again."), "warning")
            return redirect(url_for("calendar"))
        db_session.add(link)
        db_session.commit()
    flash(gettext("Connected Google Calendar for %(email)s.", email=email), "success")
    return redirect(url_for("calendar"))


@app.route("/calendar/google/refresh", methods=["POST"])
@login_required
def calendar_google_refresh():
    """Force a fresh pull of events from Google (bypasses any in-memory cache
    on the next /calendar/events.json hit)."""
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        link = db_session.scalar(select(GoogleCalendarLink).where(GoogleCalendarLink.owner == owner))
        if link is None:
            return jsonify({"ok": False, "error": "not connected"}), 404
        try:
            items = fetch_google_calendar_items(link, db_session)
            return jsonify({"ok": True, "event_count": len(items)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/calendar/google/disconnect", methods=["POST"])
@login_required
def calendar_google_disconnect():
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        link = db_session.scalar(select(GoogleCalendarLink).where(GoogleCalendarLink.owner == owner))
        if link is None:
            return jsonify({"ok": True})
        db_session.delete(link)
        db_session.commit()
    return jsonify({"ok": True})


@app.route("/calendar/items", methods=["POST"])
@login_required
def calendar_item_create():
    """Create either an event or a task. JSON body:
       { kind: 'event'|'task', title, start, end, isAllday, color, description, ... }"""
    from datetime import datetime as _dt
    payload = request.get_json(silent=True) or {}
    kind = (payload.get("kind") or "event").lower()
    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400

    def _parse(v):
        if not v:
            return None
        try:
            return _dt.fromisoformat(v.replace("Z", "+00:00").replace("+00:00", ""))
        except ValueError:
            return None

    start = _parse(payload.get("start"))
    end = _parse(payload.get("end"))
    is_all_day = bool(payload.get("isAllday", True))
    if start and end and end < start:
        # Saved like that, it would vanish from Month and Week (List only).
        return jsonify({"ok": False, "error": gettext("It has to end after it starts.")}), 400
    color = (payload.get("backgroundColor") or payload.get("color") or "").strip()
    owner = g.user.username if g.user else ""

    with SessionLocal() as db_session:
        if kind == "task":
            row = TaskItem(
                title=title,
                due_date=start.date() if start else None,
                start_at=None if is_all_day else start,
                end_at=None if is_all_day else end,
                status=payload.get("status", "todo"),
                priority=payload.get("priority", "medium"),
                color=color,
                notes=payload.get("body", "") or payload.get("description", ""),
                owner=owner,
            )
            # A lab to-do ("1") or a project group's ("g<id>"); else yours.
            refused = project_groups.apply(row, payload.get("audience", "0"))
            if refused:
                return jsonify({"ok": False, "error": refused}), 403
        else:
            row = CalendarEvent(
                title=title,
                event_date=start.date() if start else date.today(),
                start_at=None if is_all_day else start,
                end_at=None if is_all_day else end,
                is_all_day=is_all_day,
                event_type=payload.get("event_type", "experiment"),
                color=color,
                description=payload.get("body", "") or payload.get("description", ""),
                owner=owner,
            )
            # The lab's ("1", the usual), a project group's ("g<id>") or yours ("0").
            refused = project_groups.apply(row, payload.get("audience", "1"))
            if refused:
                return jsonify({"ok": False, "error": refused}), 403
        # "Change this one only": one date of a repeating event becomes an
        # event of its own (this one), and the series skips that date.
        split = payload.get("split_from") if kind != "task" else None
        if isinstance(split, dict):
            series = db_session.get(CalendarEvent, lab_calendar._int(split.get("event_id")))
            day = lab_calendar._date(split.get("date"))
            repeat = series and db_session.scalar(
                select(CalendarRepeat).where(CalendarRepeat.event_id_fk == series.id))
            if repeat is None or day is None:
                return jsonify({"ok": False, "error": gettext("That event does not repeat.")}), 404
            if not lab_calendar.event_can_edit(series):
                return jsonify({"ok": False, "error": gettext("Only the person who added this event, or an admin, can change it.")}), 403
            row.event_type, row.animal_id_fk = series.event_type, series.animal_id_fk
            skip = {d for d in (repeat.skip or "").split(",") if d} | {day.isoformat()}
            repeat.skip = ",".join(sorted(skip))
        db_session.add(row)
        db_session.flush()
        if kind != "task" and not isinstance(split, dict):
            lab_calendar.save_repeat(db_session, row, payload.get("repeat"))
        db_session.commit()
        if kind == "task":
            return jsonify({"ok": True, "item": _serialize_task(row)})
        return jsonify({"ok": True, "item": _serialize_calendar_event(row)})


@app.route("/calendar/items/<string:item_key>", methods=["POST"])
@login_required
def calendar_item_update(item_key: str):
    """Update an existing event or task. item_key is `event-<id>` or `task-<id>`;
    an occurrence of a repeating event (`event-<id>@<date>`) edits the series."""
    from datetime import datetime as _dt
    payload = request.get_json(silent=True) or {}
    kind, _, raw_id = item_key.split("@")[0].partition("-")
    if not raw_id.isdigit():
        return jsonify({"ok": False, "error": "bad id"}), 400
    row_id = int(raw_id)

    def _parse(v):
        if not v:
            return None
        try:
            return _dt.fromisoformat(v.replace("Z", "+00:00").replace("+00:00", ""))
        except ValueError:
            return None

    new_start, new_end = _parse(payload.get("start")), _parse(payload.get("end"))
    if new_start and new_end and new_end < new_start:
        return jsonify({"ok": False, "error": gettext("It has to end after it starts.")}), 400

    with SessionLocal() as db_session:
        if kind == "task":
            row = db_session.get(TaskItem, row_id)
            if row is None:
                return jsonify({"ok": False}), 404
            if not task_can_edit(row):
                return jsonify({"ok": False, "error": task_denied(row)}), 403
            if "audience" in payload and project_groups.differs(row, payload["audience"]):
                if not task_can_manage(row):
                    return jsonify({"ok": False, "error": gettext(
                        "Only %(owner)s or an admin can change whose to-do it is.",
                        owner=row.owner or gettext("its owner"))}), 403
                refused = project_groups.apply(row, payload["audience"])
                if refused:
                    return jsonify({"ok": False, "error": refused}), 403
            if "title" in payload: row.title = (payload["title"] or "").strip() or row.title
            if "body" in payload: row.notes = payload["body"] or ""
            if "backgroundColor" in payload: row.color = payload["backgroundColor"] or ""
            if "priority" in payload: row.priority = payload["priority"]
            start = _parse(payload.get("start"))
            end = _parse(payload.get("end"))
            is_all_day = payload.get("isAllday")
            if start is not None:
                row.due_date = start.date()
                row.start_at = None if is_all_day else start
            if end is not None:
                row.end_at = None if is_all_day else end
            db_session.commit()
            return jsonify({"ok": True, "item": _serialize_task(row)})
        elif kind == "event":
            row = db_session.get(CalendarEvent, row_id)
            if row is None:
                return jsonify({"ok": False}), 404
            if not lab_calendar.event_can_edit(row):
                return jsonify({"ok": False, "error": gettext("Only the person who added this event, or an admin, can change it.")}), 403
            if "audience" in payload and project_groups.differs(row, payload["audience"]):
                refused = project_groups.apply(row, payload["audience"])
                if refused:
                    return jsonify({"ok": False, "error": refused}), 403
            if "title" in payload: row.title = (payload["title"] or "").strip() or row.title
            if "body" in payload: row.description = payload["body"] or ""
            if "backgroundColor" in payload: row.color = payload["backgroundColor"] or ""
            if "event_type" in payload: row.event_type = payload["event_type"]
            start = _parse(payload.get("start"))
            end = _parse(payload.get("end"))
            is_all_day = payload.get("isAllday")
            if start is not None:
                row.event_date = start.date()
                row.start_at = None if is_all_day else start
                if is_all_day is not None: row.is_all_day = bool(is_all_day)
            if end is not None:
                row.end_at = None if is_all_day else end
            if "repeat" in payload:
                lab_calendar.save_repeat(db_session, row, payload["repeat"])
            db_session.commit()
            return jsonify({"ok": True, "item": _serialize_calendar_event(row)})
        return jsonify({"ok": False, "error": "unknown kind"}), 400


@app.route("/calendar/items/<string:item_key>/toggle", methods=["POST"])
@login_required
def calendar_item_toggle(item_key: str):
    """Flip a task's done state. No-op for events."""
    kind, _, raw_id = item_key.partition("-")
    if kind != "task" or not raw_id.isdigit():
        return jsonify({"ok": False, "error": "tasks only"}), 400
    with SessionLocal() as db_session:
        row = db_session.get(TaskItem, int(raw_id))
        if row is None:
            return jsonify({"ok": False}), 404
        if not task_can_edit(row):
            return jsonify({"ok": False, "error": task_denied(row)}), 403
        if row.done_at is None:
            row.done_at = datetime.utcnow()
            row.status = "done"
        else:
            row.done_at = None
            row.status = "todo"
        db_session.commit()
        return jsonify({"ok": True, "item": _serialize_task(row)})


@app.route("/calendar/items/<string:item_key>/delete", methods=["POST"])
@login_required
def calendar_item_delete(item_key: str):
    kind, _, raw_id = item_key.split("@")[0].partition("-")
    if not raw_id.isdigit():
        return jsonify({"ok": False, "error": "bad id"}), 400
    row_id = int(raw_id)
    with SessionLocal() as db_session:
        row = db_session.get(TaskItem if kind == "task" else CalendarEvent, row_id)
        if row is None:
            return jsonify({"ok": False}), 404
        if kind == "task" and not task_can_manage(row):
            return jsonify({"ok": False, "error": gettext("Only %(owner)s can delete this to-do.",
                                                          owner=row.owner or gettext("its owner"))}), 403
        if kind != "task" and not lab_calendar.event_can_edit(row):
            return jsonify({"ok": False, "error": gettext("Only the person who added this event, or an admin, can delete it.")}), 403
        if kind != "task":
            lab_calendar.delete_repeat(db_session, row_id)
        db_session.delete(row)
        db_session.commit()
        return jsonify({"ok": True})


@app.route("/tasks", methods=["POST"])
@login_required
def tasks():
    """Legacy endpoint kept for any older form posts."""
    with SessionLocal() as db_session:
        db_session.add(
            TaskItem(
                title=request.form["title"],
                due_date=parse_date(request.form.get("due_date")),
                status=request.form.get("status", "todo"),
                priority=request.form.get("priority", "medium"),
                notes=request.form.get("notes", ""),
                owner=g.user.username if g.user else "",
            )
        )
        db_session.commit()
    return redirect(url_for("calendar"))


def _notebook_owner_filter(query):
    return query.where(NotebookTab.owner_username == g.user.username)


def _serialize_page(page: NotebookPage) -> dict:
    import json as _json
    try:
        props = _json.loads(page.properties) if page.properties else []
    except Exception:
        props = []
    return {
        "id": page.id,
        "tab_id": page.tab_id_fk,
        "title": page.title,
        "body": page.body,
        "entry_date": page.entry_date.isoformat() if page.entry_date else "",
        "properties": props,
        "created_at": page.created_at.isoformat(),
        "updated_at": page.updated_at.isoformat(),
    }


@app.route("/notebook")
@login_required
def notebook():
    selected_tab_id = arg_int("tab", None)
    selected_page_id = arg_int("page", None)
    with SessionLocal() as db_session:
        tabs = db_session.scalars(
            _notebook_owner_filter(select(NotebookTab)).order_by(NotebookTab.position, NotebookTab.id)
        ).all()
        selected_page = None
        role = None
        if selected_page_id is not None:
            candidate = db_session.get(NotebookPage, selected_page_id)
            role = lab_notebook.role_for(db_session, candidate) if candidate is not None else None
            if role is not None:
                selected_page = candidate
        selected_tab = None
        if selected_page is not None and role == "owner":
            selected_tab = next((tab for tab in tabs if tab.id == selected_page.tab_id_fk), None)
        if selected_tab is None and selected_page is None:
            if selected_tab_id is not None:
                selected_tab = next((tab for tab in tabs if tab.id == selected_tab_id), None)
            if selected_tab is None and tabs:
                selected_tab = tabs[0]
            if selected_tab is not None and selected_tab.pages:
                selected_page = selected_tab.pages[0]
                role = "owner"

        side = lab_notebook.sidebar(db_session)
        kinds = side.pop("kinds_by_page")
        tabs_data = [
            {
                "id": tab.id,
                "title": tab.title,
                "pages": [
                    {"id": page.id, "title": page.title, "tab_id": tab.id, "updated_at": page.updated_at.isoformat(),
                     "kind": kinds.get(page.id, "note")}
                    for page in tab.pages
                ],
            }
            for tab in tabs
        ]
        selected_page_data = (lab_notebook.page_payload(db_session, selected_page, role)
                              if selected_page is not None else None)
        selected_tab_id_value = selected_tab.id if selected_tab else None
        me = {"username": g.user.username, "name": g.user.display_name or g.user.username}
        # Old keys too ("old": the @ menu leaves them out), so a chip written
        # before a rename still reads as one.
        mention_types = [{"key": key, "label": m.label, "noun": m.item_noun, **({"old": True} if key != m.key else {})}
                         for key, m in _mention_modules(db_session, with_old=True).items()]

    return render_template(
        "notebook.html",
        tabs=tabs_data,
        selected_tab_id=selected_tab_id_value,
        # A topic the person opened (?tab= or one of its pages), not the
        # first one shown when nothing was: a new page goes there.
        topic_chosen=selected_tab_id is not None or selected_page_id is not None,
        selected_page=selected_page_data,
        side=side,
        kinds=lab_notebook.KINDS,
        kind_icons=lab_notebook.KIND_ICONS,
        statuses=lab_notebook.STATUSES,
        me=me,
        mention_types=mention_types,
        lab_zone=lab.clock_zone(),
        starters=[{"key": key, "title": st["title"], "kind": st["kind"], "hint": st["hint"]}
                  for key, st in lab_notebook.STARTERS.items()],
    )


@app.route("/notebook/tabs/create", methods=["POST"])
@login_required
def notebook_create_tab():
    title = (request.form.get("title") or "New topic").strip() or "New topic"
    with SessionLocal() as db_session:
        max_pos = db_session.scalar(
            _notebook_owner_filter(select(func.max(NotebookTab.position)))
        ) or 0
        tab = NotebookTab(owner_username=g.user.username, title=title, position=max_pos + 1)
        db_session.add(tab)
        db_session.commit()
        return jsonify({"ok": True, "id": tab.id, "title": tab.title})


@app.route("/notebook/tabs/<int:tab_id>/rename", methods=["POST"])
@login_required
def notebook_rename_tab(tab_id: int):
    new_title = (request.form.get("title") or "").strip() or "Untitled topic"
    with SessionLocal() as db_session:
        tab = db_session.get(NotebookTab, tab_id)
        if tab is None or tab.owner_username != g.user.username:
            return jsonify({"ok": False}), 404
        tab.title = new_title
        db_session.commit()
        return jsonify({"ok": True, "id": tab.id, "title": tab.title})


@app.route("/notebook/tabs/<int:tab_id>/delete", methods=["POST"])
@login_required
def notebook_delete_tab(tab_id: int):
    with SessionLocal() as db_session:
        tab = db_session.get(NotebookTab, tab_id)
        if tab is not None and tab.owner_username == g.user.username:
            lab_notebook.delete_page_rows(db_session, [page.id for page in tab.pages])
            db_session.delete(tab)
            db_session.commit()
    return redirect(url_for("notebook"))


@app.route("/notebook/pages/create", methods=["POST"])
@login_required
def notebook_create_page():
    tab_id = request.form.get("tab_id", type=int)
    if not tab_id:
        return jsonify({"ok": False, "error": "tab_id required"}), 400
    with SessionLocal() as db_session:
        tab = db_session.get(NotebookTab, tab_id)
        if tab is None or tab.owner_username != g.user.username:
            return jsonify({"ok": False}), 404
        max_pos = db_session.scalar(
            select(func.max(NotebookPage.position)).where(NotebookPage.tab_id_fk == tab_id)
        ) or 0
        page = NotebookPage(
            tab_id_fk=tab_id,
            title=(request.form.get("title") or "Untitled page").strip() or "Untitled page",
            body="",
            position=max_pos + 1,
        )
        db_session.add(page)
        db_session.commit()
        return jsonify({"ok": True, "page": _serialize_page(page)})


@app.route("/notebook/pages/create-quick", methods=["POST"])
@login_required
def notebook_create_page_quick():
    with SessionLocal() as db_session:
        first_tab = db_session.scalar(
            _notebook_owner_filter(select(NotebookTab)).order_by(NotebookTab.position, NotebookTab.id).limit(1)
        )
        if first_tab is None:
            first_tab = NotebookTab(owner_username=g.user.username, title="Inbox", position=0)
            db_session.add(first_tab)
            db_session.flush()
        max_pos = db_session.scalar(
            select(func.max(NotebookPage.position)).where(NotebookPage.tab_id_fk == first_tab.id)
        ) or 0
        page = NotebookPage(
            tab_id_fk=first_tab.id,
            title="Untitled page",
            body="",
            position=max_pos + 1,
        )
        db_session.add(page)
        db_session.commit()
        return jsonify({"ok": True, "page": _serialize_page(page), "tab_id": first_tab.id})


@app.route("/search")
@login_required
def global_search():
    """Cross-section search used by the Cmd+K palette.

    Returns up to 5 matches per section: mice, cages, litters, experiments,
    strains, plasmids, inventory items, vials, notebook pages. Each result has { type, id, label, sublabel, url }.
    Empty `q` returns nothing — the palette only fires on non-empty input.
    """
    q = (request.args.get("q") or "").strip()
    # "#44" is how the app writes mouse 44 everywhere; search for the number.
    if re.fullmatch(r"#\s*\d+", q):
        q = q.lstrip("#").strip()
    if not q:
        return jsonify({"ok": True, "results": []})
    # % and _ are what they are, not LIKE's wildcards (they matched everything).
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    # A record's number: digits, not a 21-digit "number" and not "0012" (a
    # lot or catalogue number); those are text to look for.
    is_digit = _record_number(q) is not None
    limit = 5
    starts = like[1:]                               # "S20%": a name that starts with it

    def by_name(column, *then):
        """A name that is the search, then one that starts with it, then one
        that has it, then the rest (a match in the notes): "S20" finds the
        S20-* tubes before a note that says "S200"."""
        return (case((func.lower(column) == q.lower(), 0), (column.ilike(starts, escape="\\"), 1),
                     (column.ilike(like, escape="\\"), 2), else_=3), *then)
    results: list[dict] = []
    with SessionLocal() as db_session:
        mouse_stmt = select(MouseRecord)
        if is_digit:
            mouse_stmt = mouse_stmt.where(MouseRecord.mouse_id == int(q))
        else:
            mouse_stmt = mouse_stmt.where(
                MouseRecord.genotype.ilike(like, escape="\\") | MouseRecord.owner.ilike(like, escape="\\") | MouseRecord.note.ilike(like, escape="\\")
            )
        for m in db_session.scalars(mouse_stmt.order_by(MouseRecord.mouse_id.desc()).limit(limit)).all():
            results.append({
                "type": "mouse",
                "id": m.mouse_id,
                "label": gettext("Mouse #%(id)s", id=m.mouse_id),
                "sublabel": " · ".join([m.gender or "?", m.genotype or gettext("(no genotype)"),
                                        m.owner or gettext("no owner")]),
                # Everyone's scope, so the mouse is on the sheet whoever
                # owns it; the sheet puts ?q= in its search box.
                "url": url_for("colony", view="mice", scope="all", q=m.mouse_id),
            })

        # The rest of the colony: cages, litters, experiments, strains.
        cage_stmt = select(CageRecord).where(
            CageRecord.cage_id.ilike(like, escape="\\") | CageRecord.purpose.ilike(like, escape="\\")
            | CageRecord.genotype_summary.ilike(like, escape="\\") | CageRecord.card_id.ilike(like, escape="\\")
            | CageRecord.notes.ilike(like, escape="\\") | CageRecord.room.ilike(like, escape="\\"))
        for cage in db_session.scalars(cage_stmt.order_by(CageRecord.cage_id).limit(limit)).all():
            live = sum(1 for mouse in cage.mice if mouse_is_active(mouse))
            results.append({
                "type": "cage",
                "id": cage.id,
                "label": gettext("Cage %(id)s", id=cage.cage_id),
                "sublabel": " · ".join(filter(None, [cage.purpose, gettext("%(n)s live", n=live), cage.owner])),
                "url": url_for("colony", view="cages", scope="all", q=cage.cage_id),
            })
        litter_stmt = select(LitterRecord).where(
            LitterRecord.litter_id.ilike(like, escape="\\") | LitterRecord.cohort_name.ilike(like, escape="\\")
            | LitterRecord.notes.ilike(like, escape="\\"))
        for litter in db_session.scalars(litter_stmt.order_by(LitterRecord.litter_id).limit(limit)).all():
            results.append({
                "type": "litter",
                "id": litter.id,
                "label": gettext("Litter %(id)s", id=litter.litter_id),
                "sublabel": " · ".join(filter(None, [
                    gettext("born %(date)s", date=litter.date_of_birth.isoformat()) if litter.date_of_birth else "",
                    litter.cohort_name, gettext("%(n)s mice", n=len(litter.mice))])),
                "url": url_for("colony", view="litters", q=litter.litter_id),
            })
        exp_stmt = select(Experiment).where(
            Experiment.name.ilike(like, escape="\\") | Experiment.description.ilike(like, escape="\\")
            | Experiment.treatment_plan.ilike(like, escape="\\"))
        for exp in db_session.scalars(exp_stmt.order_by(Experiment.created_at.desc()).limit(limit)).all():
            results.append({
                "type": "experiment",
                "id": exp.id,
                "label": exp.name,
                "sublabel": " · ".join(filter(None, [pgettext("experiment", exp.status) if exp.status else "",
                                                     gettext("%(n)s mice", n=len(exp.memberships))
                                                     if (exp.db or "colony") == "colony" else "", exp.owner_username])),
                "url": experiment_pages.page_url(exp),
            })
        strain_stmt = select(StrainRecord).where(
            StrainRecord.strain_name.ilike(like, escape="\\") | StrainRecord.strain_number.ilike(like, escape="\\")
            | StrainRecord.strain_background.ilike(like, escape="\\") | StrainRecord.description.ilike(like, escape="\\"))
        for strain in db_session.scalars(strain_stmt.order_by(StrainRecord.strain_name).limit(limit)).all():
            results.append({
                "type": "strain",
                "id": strain.id,
                "label": strain.strain_name,
                "sublabel": " · ".join(filter(None, [strain.strain_number, strain.strain_background, strain.supplier])),
                "url": url_for("colony", view="strains", q=strain.strain_name),
            })

        plasmid_stmt = select(PlasmidRecord)
        if is_digit:
            plasmid_stmt = plasmid_stmt.where(PlasmidRecord.plasmid_id == int(q))
        else:
            plasmid_stmt = plasmid_stmt.where(
                PlasmidRecord.name.ilike(like, escape="\\") | PlasmidRecord.backbone.ilike(like, escape="\\")
                | PlasmidRecord.insert_seq.ilike(like, escape="\\") | PlasmidRecord.resistance.ilike(like, escape="\\")
                | PlasmidRecord.owner.ilike(like, escape="\\") | PlasmidRecord.storage_box.ilike(like, escape="\\")
                | PlasmidRecord.location.ilike(like, escape="\\") | PlasmidRecord.notes.ilike(like, escape="\\")
            )
        for p in db_session.scalars(plasmid_stmt.order_by(*by_name(PlasmidRecord.name, PlasmidRecord.plasmid_id.desc())).limit(limit)).all():
            # Where it is, in its box's own position names ("Box A · D7").
            where = _plasmid_where(p, _box_of(db_session, p))
            results.append({
                "type": "plasmid",
                "id": p.plasmid_id,
                "label": gettext("Plasmid #%(id)s · %(name)s", id=p.plasmid_id, name=p.name or gettext("(no name)")),
                "sublabel": " · ".join([p.backbone or "?", p.resistance or gettext("no resistance"),
                                        p.owner or gettext("no owner")]) + (f" · {where}" if where else ""),
                "url": url_for("plasmid_page", number=p.plasmid_id),
            })

        # Every lab inventory: samples, orders, reagents, antibodies, custom.
        kind_type = {"orders": "order", "samples": "sample", "reagents": "reagent", "antibodies": "antibody",
                     "viruses": "virus", "primers": "primer", "cell_lines": "cell-line"}
        # Only databases this person sees: the lab's and their own (app/lab.py).
        from . import inventory_service as inventories
        modules = {m.id: m for m in inventories.list_modules(db_session)}
        item_stmt = select(InventoryItem).where(InventoryItem.module_id_fk.in_(list(modules)))
        text_match = (
            InventoryItem.name.ilike(like, escape="\\") | InventoryItem.category.ilike(like, escape="\\")
            | InventoryItem.vendor.ilike(like, escape="\\") | InventoryItem.catalog_number.ilike(like, escape="\\")
            | InventoryItem.lot.ilike(like, escape="\\") | InventoryItem.notes.ilike(like, escape="\\")
            | InventoryItem.attrs.ilike(like, escape="\\"))
        # Digits are a record's number and a lot or catalogue number too
        # ("0012" is lot 0012M4817V, not record 12); a lot or catalogue
        # number that starts with it comes first.
        number = _record_number(q)
        item_stmt = item_stmt.where(text_match | (InventoryItem.number == number) if number is not None else text_match)
        code = (InventoryItem.lot.ilike(starts, escape="\\") | InventoryItem.catalog_number.ilike(starts, escape="\\"))
        order = [case((code, 0), else_=1)] if q.isdigit() else []
        if number is not None:
            order.append(InventoryItem.number != number)
        for item in db_session.scalars(item_stmt.order_by(*order, *by_name(InventoryItem.name, InventoryItem.id.desc())).limit(limit * 2)).all():
            module = modules.get(item.module_id_fk)
            if module is None:
                continue
            results.append({
                "type": kind_type.get(module.kind, "item"),
                "id": item.number,
                "label": f"{i18n.translate_value(module.label)} #{item.number} · "
                         + (item.name or gettext("(unnamed)")),
                "sublabel": " · ".join(filter(None, [item.category, item.status, item.vendor,
                                                     gettext("lab common") if item.is_shared else item.owner])),
                # Open the inventory already searched down to this item.
                "url": url_for("inventory.module", key=module.key, q=item.name or str(item.number)),
            })

        # Fly vials and worm plates, by genotype (or either cross parent).
        from . import stock_service
        from .models import StockModule, StockUnit
        stock_modules = {m.id: stock_service.view(m) for m in stock_service.list_modules(db_session)}
        unit_stmt = select(StockUnit).where(StockUnit.active.is_(True))
        if is_digit:
            unit_stmt = unit_stmt.where(StockUnit.number == int(q))
        else:
            unit_stmt = unit_stmt.where(
                StockUnit.genotype.ilike(like, escape="\\") | StockUnit.female_genotype.ilike(like, escape="\\")
                | StockUnit.male_genotype.ilike(like, escape="\\") | StockUnit.notes.ilike(like, escape="\\"))
        for unit in db_session.scalars(unit_stmt.order_by(StockUnit.id.desc()).limit(limit * 2)).all():
            mv = stock_modules.get(unit.module_id_fk)
            if mv is None:
                continue
            results.append({
                "type": "vial",
                "id": unit.number,
                "label": f"{mv.code(unit)} · " + (unit.genotype or gettext("(no genotype)")),
                "sublabel": " · ".join(filter(None, [i18n.translate_value(mv.label), mv.purpose_label(unit.purpose),
                                                     unit.rack.name if unit.rack else "", unit.owner])),
                "url": url_for("stocks.module", key=mv.key),
            })

        # Zebrafish tanks, lines and clutches.
        for t in db_session.scalars(
                select(TankRecord).options(joinedload(TankRecord.line))
                .where(TankRecord.tank_id.ilike(like, escape="\\") | TankRecord.card_id.ilike(like, escape="\\")
                       | TankRecord.owner.ilike(like, escape="\\") | TankRecord.notes.ilike(like, escape="\\"))
                .order_by(TankRecord.tank_id).limit(limit)).all():
            results.append({
                "type": "tank", "id": t.id, "label": gettext("Tank %(id)s", id=t.tank_id),
                "sublabel": " · ".join(filter(None, [t.purpose, t.line.name if t.line else "", t.owner])),
                "url": url_for("zebrafish", view="tanks") + f"#tank-{t.id}",
            })
        for ln in db_session.scalars(
                select(FishLine).where(FishLine.name.ilike(like, escape="\\") | FishLine.zfin_name.ilike(like, escape="\\")
                                       | FishLine.allele.ilike(like, escape="\\") | FishLine.transgene_summary.ilike(like, escape="\\"))
                .order_by(FishLine.name).limit(limit)).all():
            results.append({
                "type": "fish-line", "id": ln.id, "label": ln.name,
                "sublabel": " · ".join(filter(None, [gettext("Zebrafish line"), ln.zfin_name, ln.background])),
                "url": url_for("zebrafish_line_detail", line_id=ln.id),
            })
        for c in db_session.scalars(
                select(ClutchRecord).where(ClutchRecord.clutch_id.ilike(like, escape="\\") | ClutchRecord.notes.ilike(like, escape="\\"))
                .order_by(ClutchRecord.date_of_fertilization.desc()).limit(limit)).all():
            results.append({
                "type": "clutch", "id": c.id, "label": gettext("Clutch %(id)s", id=c.clutch_id),
                "sublabel": " · ".join(filter(None, [c.date_of_fertilization.isoformat() if c.date_of_fertilization else "",
                                                     gettext("%(n)s embryos", n=c.embryo_count) if c.embryo_count else "",
                                                     c.owner])),
                "url": url_for("zebrafish", view="clutches") + f"#clutch-{c.id}",
            })
        # Animals, housing units and lines in the configurable organism databases.
        from . import organism_service
        results.extend(organism_service.search(db_session, q, limit))

        # Notebook pages — the person's own and those shared with them.
        page_stmt = (
            lab_notebook.accessible_filter(
                select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id))
            .where(NotebookPage.title.ilike(like, escape="\\") | NotebookPage.body.ilike(like, escape="\\"))
            .order_by(*by_name(NotebookPage.title, NotebookPage.updated_at.desc()))
            .limit(limit)
        )
        for page in db_session.scalars(page_stmt).all():
            results.append({
                "type": "page",
                "id": page.id,
                "label": page.title or gettext("Untitled page"),
                "sublabel": (gettext("Notebook · %(tab)s", tab=page.tab.title)
                             if page.tab and page.tab.owner_username == g.user.username
                             else gettext("Notebook · shared by %(owner)s",
                                          owner=page.tab.owner_username if page.tab else "")),
                "url": url_for("notebook", page=page.id),
            })

    # Nothing from a function the lab switched off (app/lab.py).
    features = lab.request_features()
    off_types = set()
    if not features.get("colony", True):
        off_types |= {"mouse", "cage", "litter", "experiment", "strain"}
    if not features.get("zebrafish", True):
        off_types |= {"tank", "clutch", "fish-line", "fish"}
    if not features.get("plasmids", True):
        off_types |= {"plasmid"}
    if not features.get("notebook", True):
        off_types |= {"notebook", "page", "notebook-page"}
    results = [r for r in results if r.get("type") not in off_types]
    return jsonify({"ok": True, "results": results, "query": q})


@app.route("/import/<entity>", methods=["POST"])
@login_required
@next_number_retried
def csv_import(entity: str):
    """Import a CSV into one of the data tables.

    Form fields:
      file (uploaded CSV)
      dry_run=1  → returns parsed rows without committing

    Supported entities: mouse, plasmid, order.
    Returns { ok, count, errors, preview } where preview is the first 6 rows.
    """
    if entity not in ("mouse", "plasmid", "order"):
        return jsonify({"ok": False, "error": f"unknown entity: {entity}"}), 400
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": gettext("missing file")}), 400
    dry_run = (request.form.get("dry_run") or "0") == "1"

    import csv as _csv, io as _io
    raw = upload.read()
    try:
        text_data = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_data = raw.decode("latin-1")
    reader = _csv.DictReader(_io.StringIO(text_data))
    rows = [r for r in reader]
    if not rows:
        return jsonify({"ok": False, "error": gettext("empty CSV")}), 400

    errors: list[str] = []
    created = 0
    preview: list[dict] = []
    with SessionLocal() as db_session:
        if entity == "mouse":
            # Numbers come from one counter that skips every number already
            # used, in the database or earlier in this file, and the check
            # is the same in the dry run: a file whose ID repeats, or takes
            # a number a blank row was about to get, is caught row by row
            # instead of failing whole at the end.
            from .inventory_service import get_setting, set_setting
            from .services import MOUSE_ID_HIGH
            taken = set(db_session.scalars(select(MouseRecord.mouse_id)))
            high = get_setting(db_session, MOUSE_ID_HIGH, "")
            next_free = max(max(taken, default=0), int(high) if high.isdigit() else 0) + 1
            typed = {int(v) for r in rows if (v := (r.get("mouse_id") or "").strip()).isdigit()}
            if not dry_run:
                batch_ctx = audit.batch(db_session, "create", f"import mice from {upload.filename}"[:200], "mice")
                batch_ctx.__enter__()
            for idx, row in enumerate(rows, start=2):
                try:
                    mouse_id_raw = (row.get("mouse_id") or "").strip()
                    if mouse_id_raw:
                        if not mouse_id_raw.isdigit() or int(mouse_id_raw) < 1:
                            raise ValueError(f"mouse_id “{mouse_id_raw}” is not a whole number above zero")
                        mouse_id_value = int(mouse_id_raw)
                    else:
                        while next_free in taken or next_free in typed:
                            next_free += 1
                        mouse_id_value = next_free
                    if mouse_id_value in taken:
                        errors.append(f"row {idx}: mouse_id {mouse_id_value} already exists")
                        continue
                    taken.add(mouse_id_value)
                    mouse = MouseRecord(
                        mouse_id=mouse_id_value,
                        gender=(row.get("gender") or "").strip(),
                        genotype=(row.get("genotype") or "").strip(),
                        owner=(row.get("owner") or "").strip(),
                        status=(row.get("status") or "").strip(),
                        note=(row.get("note") or "").strip(),
                    )
                    if not dry_run:
                        db_session.add(mouse)
                    preview.append({"mouse_id": mouse_id_value, "genotype": mouse.genotype})
                    created += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"row {idx}: {exc}")
            if not dry_run:
                if taken:
                    set_setting(db_session, MOUSE_ID_HIGH, str(max(taken)))
                db_session.flush()
                batch_ctx.__exit__(None, None, None)
        elif entity == "plasmid":
            # Numbers for rows without one come from a counter that skips
            # numbers already used (in the database or earlier in the file);
            # re-reading max() per row double-counted rows already added.
            taken = set(db_session.scalars(select(PlasmidRecord.plasmid_id)))
            next_free = max(taken, default=0) + 1
            if not dry_run:
                batch_ctx = audit.batch(db_session, "create", f"import plasmids from {upload.filename}", "plasmids")
                batch_ctx.__enter__()
            for idx, row in enumerate(rows, start=2):
                try:
                    pid_raw = (row.get("plasmid_id") or "").strip()
                    if pid_raw:
                        if not pid_raw.isdigit() or int(pid_raw) < 1:
                            raise ValueError(f"plasmid_id “{pid_raw}” is not a whole number above zero")
                        pid_value = int(pid_raw)
                    else:
                        while next_free in taken:
                            next_free += 1
                        pid_value = next_free
                    if pid_value in taken:
                        errors.append(f"row {idx}: plasmid_id {pid_value} already exists")
                        continue
                    if not (row.get("name") or "").strip():
                        raise ValueError("no name")
                    taken.add(pid_value)
                    p = PlasmidRecord(
                        plasmid_id=pid_value,
                        name=(row.get("name") or "").strip(),
                        backbone=(row.get("backbone") or "").strip(),
                        insert_seq=(row.get("insert_seq") or row.get("insert") or "").strip(),
                        resistance=(row.get("resistance") or "").strip(),
                        owner=(row.get("owner") or g.user.username).strip(),
                        location=(row.get("location") or "").strip(),
                        concentration=_plasmid_measure(row, "concentration"),
                        a260_280=_plasmid_measure(row, "a260_280"),
                        notes=(row.get("notes") or "").strip(),
                    )
                    if not dry_run:
                        db_session.add(p)
                    preview.append({"plasmid_id": pid_value, "name": p.name})
                    created += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"row {idx}: {exc}")
            if not dry_run:
                batch_ctx.__exit__(None, None, None)
        elif entity == "order":
            # Orders are an inventory now; import into the one the page named.
            # Statuses follow the inventory's own list (any case; unknown →
            # its first), and the import is one batch, so it can be undone.
            from . import inventory_service as inventories
            named = request.args.get("module", "")
            module = (inventories.get_module(db_session, named) if named
                      else inventories.first_of_kind(db_session, "orders"))
            if module is None or module.kind != "orders":
                return jsonify({"ok": False, "error": gettext("Orders can only be imported into an orders inventory.")}), 400
            mv = inventories.view(module)
            number = inventories.next_number(db_session, module.id)
            cell = lambda row, *names: next((str(row.get(n) or "").strip() for n in names if str(row.get(n) or "").strip()), "")
            from contextlib import nullcontext
            batch = (nullcontext() if dry_run
                     else audit.batch(db_session, "create", f"import orders into {module.label}", "inventory_items"))
            with batch:
                for idx, row in enumerate(rows, start=2):
                    try:
                        owner = cell(row, "requester_name", "requester", "owner") or g.user.username
                        if owner != g.user.username and not access.is_admin():
                            owner = g.user.username
                        received_raw = cell(row, "received_on", "received", "date_received")
                        o = InventoryItem(
                            module_id_fk=module.id, number=number, owner=owner[:80],
                            vendor=cell(row, "vendor_name", "vendor")[:120],
                            name=cell(row, "item_name", "item", "name")[:200],
                            catalog_number=cell(row, "catalog_number", "catalog")[:120],
                            quantity=(cell(row, "quantity") or "1")[:60],
                            unit=cell(row, "unit", "units")[:30],
                            notes=cell(row, "notes"),
                        )
                        if received_raw:
                            try:
                                o.received_on = date.fromisoformat(received_raw)
                            except ValueError:
                                raise ValueError(f"received date “{received_raw}” is not YYYY-MM-DD") from None
                        if not o.name:
                            raise ValueError("no item name")
                        price = cell(row, "price", "unit_price", "cost")
                        if price:
                            o.attrs = json.dumps({"price": price})
                        status = cell(row, "status")
                        inventories.apply_status(mv, o, inventories.match_status(mv, status)
                                                 or (mv.statuses[0] if mv.statuses else status))
                        if not dry_run:
                            db_session.add(o)
                        number += 1
                        preview.append({"item": o.name, "vendor": o.vendor, "status": o.status})
                        created += 1
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"row {idx}: {exc}")
        if not dry_run:
            db_session.commit()

    return jsonify({
        "ok": True,
        "count": created,
        "errors": errors[:20],
        "preview": preview[:6],
        "dry_run": dry_run,
    })


@app.route("/batches")
@login_required
def batches_view():
    """Recent batch operations, with what undoing each would do.

    Everyone sees their own; admins see the whole lab's, because the
    "someone bulk-edited the colony this morning" question is theirs to
    answer.
    """
    show_all = access.is_admin() and request.args.get("scope") != "mine"
    with SessionLocal() as db_session:
        rows = undo_service.recent(
            db_session, limit=60,
            actor=None if show_all else g.user.username)
        batches = []
        for row in rows:
            summary = undo_service.describe(db_session, row)
            batches.append({
                "id": row.id,
                "action": row.action,
                "description": row.description,
                "table": row.target_table,
                "actor": row.actor,
                "count": row.record_count or summary["entries"],
                "created_at": row.created_at,
                "undone_at": row.undone_at,
                "undone_by": row.undone_by,
                "mine": row.actor == g.user.username,
                "can_undo": (not row.is_undone
                             and (row.actor == g.user.username or access.is_admin())),
                **summary,
            })
    return render_template("batches.html", batches=batches, show_all=show_all)


@app.route("/batches/<int:batch_id>/undo", methods=["POST"])
@login_required
def undo_batch(batch_id: int):
    force = request.form.get("force") == "1"
    with SessionLocal() as db_session:
        row = db_session.get(BatchRecord, batch_id)
        if row is None:
            flash(gettext("That batch no longer exists."), "error")
            return redirect(url_for("batches_view"))
        if not (row.actor == g.user.username or access.is_admin()):
            flash(gettext("Only whoever ran a batch, or an admin, can undo it."), "error")
            return redirect(url_for("batches_view"))

        result = undo_service.undo(db_session, row, g.user.username, force=force)
        if not result["ok"]:
            db_session.rollback()
            for problem in result["problems"]:
                flash(problem, "error")
            return redirect(url_for("batches_view"))
        db_session.commit()

    if result["skipped"]:
        flash(gettext("Undid %(reverted)s change(s) — %(skipped)s could not be reversed.",
                      reverted=result["reverted"], skipped=result["skipped"]), "success")
    else:
        flash(gettext("Undid %(reverted)s change(s).", reverted=result["reverted"]), "success")
    for note in result["notes"][:5]:
        flash(note, "info")
    return redirect(url_for("batches_view"))


@app.route("/audit")
@admin_required
def audit_log_view():
    """Admin-only change history across the app.

    Every create, edit and delete on a tracked table lands here via the
    flush listener in app/audit.py, with a field-level diff — so the
    question is no longer just "who deleted that mouse?" but "what was its
    genotype before someone changed it?"."""
    with SessionLocal() as db_session:
        rows = db_session.scalars(
            select(AuditEntry).order_by(AuditEntry.changed_at.desc()).limit(200)
        ).all()
        entries = [
            {
                "id": e.id,
                "table_name": e.table_name,
                "record_id": e.record_id,
                "record_label": e.record_label or f"#{e.record_id}",
                "action": e.action,
                "changed_by": e.changed_by,
                "changed_at": local_time(e.changed_at).strftime("%Y-%m-%d %H:%M:%S"),
                "details": e.details or "",
            }
            for e in rows
        ]
    return render_template("audit.html", entries=entries)


def _templates_visible(me: str):
    """Your own templates, the lab's, and your project groups'."""
    lab_wide = NotebookTemplate.lab.is_(True) & NotebookTemplate.share_group_id.is_(None)
    mine = sorted(project_groups.ids_of())
    clause = (NotebookTemplate.owner_username == me) | lab_wide
    if mine:
        clause = clause | (NotebookTemplate.lab.is_(True) & NotebookTemplate.share_group_id.in_(mine))
    return clause


def _template_open(template) -> bool:
    """May this person start a page from it: theirs, the lab's, or a group's
    they are in."""
    return template is not None and (template.owner_username == g.user.username
                                     or project_groups.shared_with(template.lab, template.share_group_id))


@app.route("/notebook/templates")
@login_required
def notebook_templates_list():
    """Your templates, then the lab's, each with its page type."""
    me = g.user.username
    with SessionLocal() as db_session:
        rows = db_session.scalars(
            select(NotebookTemplate).where(_templates_visible(me))
            .order_by((NotebookTemplate.owner_username != me), NotebookTemplate.updated_at.desc())
        ).all()
        names = lab_notebook.display_names(db_session, list({t.owner_username for t in rows}))
        return jsonify({"ok": True, "templates": [
            {
                "id": t.id,
                "title": t.title,
                "icon": t.icon,
                "kind": t.kind or "note",
                "kind_label": lab_notebook.KINDS.get(t.kind or "note", "Note"),
                "lab": bool(t.lab),
                "group": project_groups.name_of(t.share_group_id) if t.lab and t.share_group_id else "",
                "mine": t.owner_username == me,
                "can_delete": t.owner_username == me or (bool(t.lab) and access.is_admin()),
                "owner_name": names.get(t.owner_username, t.owner_username),
                "body_preview": (t.body or "")[:160],
                "updated_at": t.updated_at.isoformat(),
            }
            for t in rows
        ]})


@app.route("/notebook/templates/<int:template_id>")
@login_required
def notebook_template_get(template_id: int):
    with SessionLocal() as db_session:
        template = db_session.get(NotebookTemplate, template_id)
        if not _template_open(template):
            return jsonify({"ok": False}), 404
        return jsonify({"ok": True, "template": {"id": template.id, "title": template.title, "icon": template.icon,
                                                 "kind": template.kind or "note", "body": template.body or ""}})


@app.route("/notebook/templates/create", methods=["POST"])
@login_required
def notebook_template_create():
    """Create a template either from scratch or from an existing page.

    Form fields:
      title (str, required)
      icon  (str, optional — emoji/single-character)
      body  (str, optional)
      kind  (str, optional — the page type pages made from it get)
      from_page_id (int, optional — copies the page's text and its type)
      structure_only (1, optional — with from_page_id: headings, steps and
        table headers, without the results; lab_notebook.structure_only)
      lab (1, optional — everyone in the lab can start pages from it)
    """
    title = (request.form.get("title") or "").strip()[:160] or "Untitled template"
    icon = (request.form.get("icon") or "").strip()
    body = request.form.get("body", "")
    kind = request.form.get("kind", "")
    from_page_id = request.form.get("from_page_id", type=int)

    with SessionLocal() as db_session:
        if from_page_id:
            page = db_session.get(NotebookPage, from_page_id)
            if page is None or lab_notebook.role_for(db_session, page) is None:
                return jsonify({"ok": False, "error": gettext("page not found")}), 404
            body = page.body or body
            info = lab_notebook.info_for(db_session, page.id)
            kind = info.kind if info is not None else kind
            if request.form.get("structure_only") == "1":
                body = lab_notebook.structure_only(body)
        # One of theirs by that name already: asked first, then saved over
        # (replace=1), not a second one of the same name in the list.
        template = db_session.scalar(select(NotebookTemplate).where(
            NotebookTemplate.owner_username == g.user.username,
            func.lower(NotebookTemplate.title) == title.lower()).limit(1))
        if template is not None and request.form.get("replace") != "1":
            return jsonify({"ok": False, "exists": True,
                            "error": gettext("You already have a template called “%(title)s”.",
                                             title=template.title)}), 409
        if template is None:
            template = NotebookTemplate(owner_username=g.user.username)
            db_session.add(template)
        template.title, template.icon, template.body = title, icon, body
        template.kind = kind if kind in lab_notebook.KINDS and kind != "daily" else "note"
        # "1": the lab's; "g<id>": a project group's (app/groups.py).
        shared, group_id = project_groups.parse(request.form.get("lab"))
        if group_id is not None and not project_groups.may_share_with(group_id):
            return jsonify({"ok": False, "error": project_groups.refusal(group_id)}), 403
        template.lab, template.share_group_id = shared, group_id if shared else None
        db_session.commit()
        return jsonify({
            "ok": True,
            "template": {"id": template.id, "title": template.title, "icon": template.icon,
                         "kind": template.kind, "lab": template.lab},
        })


@app.route("/notebook/templates/<int:template_id>/delete", methods=["POST"])
@login_required
def notebook_template_delete(template_id: int):
    """Its maker's to delete (a lab template also an admin's)."""
    with SessionLocal() as db_session:
        template = db_session.get(NotebookTemplate, template_id)
        if template is None or not (template.owner_username == g.user.username
                                    or (template.lab and access.is_admin())):
            return jsonify({"ok": False}), 404
        db_session.delete(template)
        db_session.commit()
        return jsonify({"ok": True})


@app.route("/notebook/pages/create-from-template", methods=["POST"])
@login_required
def notebook_create_page_from_template():
    """Create a new page seeded from a template. Optionally targets a specific
    tab; defaults to the first available (or creates an Inbox like
    create-quick)."""
    template_id = request.form.get("template_id", type=int)
    tab_id = request.form.get("tab_id", type=int)
    if not template_id:
        return jsonify({"ok": False, "error": "template_id required"}), 400

    with SessionLocal() as db_session:
        template = db_session.get(NotebookTemplate, template_id)
        if not _template_open(template):
            return jsonify({"ok": False, "error": gettext("template not found")}), 404

        if tab_id:
            tab = db_session.get(NotebookTab, tab_id)
            if tab is None or tab.owner_username != g.user.username:
                return jsonify({"ok": False}), 404
        else:
            tab = db_session.scalar(
                _notebook_owner_filter(select(NotebookTab)).order_by(NotebookTab.position, NotebookTab.id).limit(1)
            )
            if tab is None:
                tab = NotebookTab(owner_username=g.user.username, title="Inbox", position=0)
                db_session.add(tab)
                db_session.flush()

        max_pos = db_session.scalar(
            select(func.max(NotebookPage.position)).where(NotebookPage.tab_id_fk == tab.id)
        ) or 0
        page = NotebookPage(
            tab_id_fk=tab.id,
            title=template.title or "Untitled page",
            body=template.body or "",
            position=max_pos + 1,
        )
        db_session.add(page)
        db_session.commit()
        return jsonify({"ok": True, "page": _serialize_page(page), "tab_id": tab.id})


@app.route("/notebook/pages/<int:page_id>/move", methods=["POST"])
@login_required
def notebook_move_page(page_id: int):
    new_tab_id = request.form.get("tab_id", type=int)
    if not new_tab_id:
        return jsonify({"ok": False, "error": "tab_id required"}), 400
    with SessionLocal() as db_session:
        page = db_session.get(NotebookPage, page_id)
        if page is None or page.tab.owner_username != g.user.username:
            return jsonify({"ok": False}), 404
        new_tab = db_session.get(NotebookTab, new_tab_id)
        if new_tab is None or new_tab.owner_username != g.user.username:
            return jsonify({"ok": False}), 404
        page.tab_id_fk = new_tab_id
        max_pos = db_session.scalar(
            select(func.max(NotebookPage.position)).where(NotebookPage.tab_id_fk == new_tab_id)
        ) or 0
        page.position = max_pos + 1
        db_session.commit()
        return jsonify({"ok": True, "tab_id": new_tab_id, "page_id": page.id})


@app.route("/notebook/pages/<int:page_id>/update", methods=["POST"])
@login_required
def notebook_update_page(page_id: int):
    """Save a page's title, text, date or properties; anyone it is shared
    with for editing may. The live editor sends X-Collab-Gen with the text:
    a save from an editor on an older editing state is refused (it would put
    back what a restore replaced), and a save without it (the plain-text
    fallback) starts the live editors again from the new text."""
    with SessionLocal() as db_session:
        page = db_session.get(NotebookPage, page_id)
        role = lab_notebook.role_for(db_session, page) if page is not None else None
        if role is None:
            return jsonify({"ok": False}), 404
        if not lab_notebook.can_edit_role(role):
            return jsonify({"ok": False, "error": gettext("This page is view only.")}), 403
        from . import signatures
        refused = signatures.refuse_if_locked(db_session, page.id)
        if refused:
            return refused
        changed = False
        if "title" in request.form:
            title = (request.form.get("title") or "").strip() or "Untitled page"
            changed = changed or title != page.title
            page.title = title
        if "body" in request.form:
            body = request.form.get("body", "")
            collab = request.headers.get("X-Collab-Gen")
            generation = lab_notebook.collab_generation(db_session, page.id)
            if collab is not None and collab != str(generation):
                return jsonify({"ok": False, "reset": True, "gen": generation}), 409
            # From the live editor, with the edits it holds: a tab that has
            # fallen behind (a background tab saving late) does not put an
            # older text over a newer one; its own edits are already in it.
            state = request.form.get("collab_state", "")
            info = lab_notebook.info_for(db_session, page.id, create=True) if collab is not None else None
            if info is not None and state and lab_notebook.behind(info.body_state, state):
                db_session.commit()
                return jsonify({"ok": True, "behind": True, "updated_at": page.updated_at.isoformat()})
            if body != (page.body or ""):
                page.body = body
                changed = True
                if collab is None:
                    lab_notebook.reset_collab(db_session, page.id)
            if info is not None and state:
                info.body_state = state[:200_000]
        if "entry_date" in request.form:
            raw_date = (request.form.get("entry_date") or "").strip()
            page.entry_date = parse_date(raw_date) if raw_date else None
        if "properties" in request.form:
            # JSON blob from the client — validated/normalized server-side.
            import json as _json
            try:
                parsed = _json.loads(request.form.get("properties") or "[]")
                if isinstance(parsed, list):
                    page.properties = _json.dumps(parsed)
            except _json.JSONDecodeError:
                pass
        page.updated_at = datetime.utcnow()
        if changed:
            live = request.headers.get("X-Collab-Gen") is not None
            lab_notebook.record_edit(db_session, page, lab_notebook.last_editor(db_session, page.id) if live else None)
        db_session.commit()
        return jsonify({"ok": True, "updated_at": page.updated_at.isoformat()})


@app.route("/notebook/pages/<int:page_id>/delete", methods=["POST"])
@login_required
def notebook_delete_page(page_id: int):
    with SessionLocal() as db_session:
        page = db_session.get(NotebookPage, page_id)
        if page is None or page.tab.owner_username != g.user.username:
            return redirect(url_for("notebook"))
        from . import signatures
        if signatures.is_signed(db_session, page.id):
            message = gettext("A signed page is a record: it can't be deleted.")
            if request.headers.get("X-Requested-With") == "fetch":
                return jsonify({"ok": False, "error": message}), 409
            flash(message, "error")
            return redirect(url_for("notebook", page=page.id))
        tab_id = page.tab_id_fk
        lab_notebook.delete_page_rows(db_session, [page.id])
        db_session.delete(page)
        db_session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "tab_id": tab_id})
    return redirect(url_for("notebook", tab=tab_id))


@app.route("/notebook/upload-image", methods=["POST"])
@login_required
def notebook_upload_image():
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return jsonify({"ok": False}), 400
    saved = save_uploaded_image(upload)
    if not saved:
        return jsonify({"ok": False}), 400
    return jsonify({"ok": True, "url": url_for("static", filename=saved)})


@app.route("/notebook/upload-file", methods=["POST"])
@login_required
def notebook_upload_file():
    upload = request.files.get("file")
    saved = save_uploaded_file(upload)
    if not saved:
        return jsonify({"ok": False}), 400
    return jsonify({
        "ok": True,
        "url": url_for("static", filename=saved["path"]),
        "name": saved["original_name"],
        "size": saved["size_bytes"],
    })


@app.route("/notebook/lookup/mouse/<int:mouse_id>")
@login_required
def notebook_lookup_mouse(mouse_id: int):
    with SessionLocal() as db_session:
        mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == mouse_id))
        if mouse is None:
            return jsonify({"ok": False}), 404
        cage_id = mouse.cage.cage_id if mouse.cage else ""
        label = f"Mouse #{mouse.mouse_id} · {mouse.gender or '?'} · {mouse.genotype or '(no genotype)'} · cage {cage_id or '—'} · {mouse.owner or 'no owner'}"
        return jsonify(
            {
                "ok": True,
                "label": label,
                "mouse_id": mouse.mouse_id,
                "gender": mouse.gender,
                "genotype": mouse.genotype,
                "status": mouse.status,
                "owner": mouse.owner,
                "cage_id": cage_id,
            }
        )


@app.route("/notebook/lookup/plasmid/<int:plasmid_id>")
@login_required
def notebook_lookup_plasmid(plasmid_id: int):
    with SessionLocal() as db_session:
        plasmid = db_session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == plasmid_id))
        if plasmid is None:
            return jsonify({"ok": False}), 404
        label = f"Plasmid #{plasmid.plasmid_id} · {plasmid.name or '(no name)'} · {plasmid.backbone or '?'} · {plasmid.resistance or 'no resistance'} · {plasmid.owner or 'no owner'}"
        return jsonify(
            {
                "ok": True,
                "label": label,
                "plasmid_id": plasmid.plasmid_id,
                "name": plasmid.name,
                "backbone": plasmid.backbone,
                "insert_seq": plasmid.insert_seq,
                "resistance": plasmid.resistance,
                "owner": plasmid.owner,
                "location": plasmid.location,
            }
        )


# @links to inventory records: "@antibodies 12" is item #12 of the database
# whose key is "antibodies". Mice, plasmids and orders keep their own words.
MENTION_BUILTINS = ("mouse", "plasmid", "order", "all")


def _mention_modules(db_session, with_old: bool = False) -> dict[str, InventoryModule]:
    """The inventories the signed-in person can see and @link, by key; with
    `with_old`, also by the keys they had before a rename, so "@antibodies 5"
    written before Antibodies became Primary antibodies still finds it."""
    from . import database_keys
    from . import inventory_service as inventories
    modules = {m.key: m for m in inventories.list_modules(db_session)
               if m.kind != "orders" and m.key not in MENTION_BUILTINS}
    if with_old:
        by_id = {m.id: m for m in modules.values()}
        for old, module_id in database_keys.aliases_by_key(db_session, "inventory").items():
            if module_id in by_id and old not in modules and old not in MENTION_BUILTINS:
                modules[old] = by_id[module_id]
    return modules


def _record_number(query: str) -> int | None:
    """What typed after @ is a record's number: digits, and not "0012", which
    is a lot or catalogue number (record numbers have no leading zero)."""
    if query.isdigit() and len(query) <= 12 and not (len(query) > 1 and query.startswith("0")):
        return int(query)
    return None


def _mention_items(db_session, module: InventoryModule, query: str, limit: int) -> list[InventoryItem]:
    """Records whose name, catalogue number, lot or vendor holds what was
    typed; a record number finds that record too. A lot or catalogue number
    that is what was typed comes first, then the record of that number."""
    stmt = select(InventoryItem).where(InventoryItem.module_id_fk == module.id)
    if query:
        like = like_pattern(query)
        found = (InventoryItem.name.ilike(like, escape="\\") | InventoryItem.catalog_number.ilike(like, escape="\\")
                 | InventoryItem.lot.ilike(like, escape="\\") | InventoryItem.vendor.ilike(like, escape="\\"))
        exact = (func.lower(InventoryItem.lot) == query.lower()) | (func.lower(InventoryItem.catalog_number) == query.lower())
        number = _record_number(query)
        if number is not None:
            found = found | (InventoryItem.number == number)
        stmt = stmt.where(found).order_by(case((exact, 0), else_=1))
        if number is not None:
            stmt = stmt.order_by(InventoryItem.number != number)
    return list(db_session.scalars(stmt.order_by(InventoryItem.number.desc()).limit(limit)))


def _mention_label(module: InventoryModule, item: InventoryItem, with_noun: bool = False) -> str:
    parts = [f"{module.item_noun.capitalize()} #{item.number}" if with_noun else f"#{item.number}",
             item.name or "(no name)"]
    parts += [v for v in (item.vendor, item.lot and f"lot {item.lot}") if v]
    return " · ".join(parts)


@app.route("/notebook/lookup/<key>/<int:number>")
@login_required
def notebook_lookup_item(key: str, number: int):
    """What the popover on an @<inventory> <n> chip shows."""
    from . import inventory_service as inventories
    with SessionLocal() as db_session:
        module = _mention_modules(db_session, with_old=True).get(key)
        item = module and db_session.scalar(select(InventoryItem).where(
            InventoryItem.module_id_fk == module.id, InventoryItem.number == number))
        if not item:
            return jsonify({"ok": False}), 404
        where = " · ".join(v for v in ((item.rack.name if item.rack else ""), inventories.rack_label(item),
                                       item.location_note) if v)
        amount = " ".join(v for v in (item.quantity, item.unit) if v)
        fields = [("Name", item.name), ("Category", item.category), ("Status", item.status),
                  ("Owner", "Lab common" if item.is_shared else item.owner), ("Vendor", item.vendor),
                  ("Catalog #", item.catalog_number), ("Lot", item.lot), ("Amount", amount), ("Where", where),
                  ("Expires", item.expires_on.isoformat() if item.expires_on else "")]
        return jsonify({"ok": True, "label": _mention_label(module, item, True), "type_label": module.label,
                        "name": item.name or "", "fields": [[k, v] for k, v in fields if v]})


def _order_items_query(db_session, query: str, limit: int, by_number: bool = False):
    """Orders for @order mentions: items of the first orders inventory,
    where an order's number is what @order <n> refers to. `by_number`: that
    order only; otherwise what was typed is looked for in the item, vendor
    and catalogue number too (@order 2947 finds catalogue ab2947)."""
    from . import inventory_service as inventories
    module = inventories.first_of_kind(db_session, "orders")
    if module is None:
        return []
    stmt = select(InventoryItem).where(InventoryItem.module_id_fk == module.id)
    if by_number:
        stmt = stmt.where(InventoryItem.number == int(query))
    elif query:
        like = like_pattern(query)
        found = (InventoryItem.name.ilike(like, escape="\\") | InventoryItem.vendor.ilike(like, escape="\\")
                 | InventoryItem.catalog_number.ilike(like, escape="\\"))
        number = _record_number(query)
        if number is not None:
            stmt = stmt.where(found | (InventoryItem.number == number)).order_by(InventoryItem.number != number)
        else:
            stmt = stmt.where(found)
    return db_session.scalars(stmt.order_by(InventoryItem.number.desc()).limit(limit)).all()


@app.route("/notebook/lookup/order/<int:order_id>")
@login_required
def notebook_lookup_order(order_id: int):
    with SessionLocal() as db_session:
        found = _order_items_query(db_session, str(order_id), 1, by_number=True)
        if not found:
            return jsonify({"ok": False}), 404
        order = found[0]
        label = (
            f"Order #{order.number} · {order.vendor or '(no vendor)'} · "
            f"{order.name or '(no item)'} · cat# {order.catalog_number or '—'} · "
            f"qty {order.quantity or '?'} · {order.status or 'requested'} · "
            f"requested by {order.owner or '—'}"
        )
        return jsonify(
            {
                "ok": True,
                "label": label,
                "order_id": order.number,
                "vendor_name": order.vendor,
                "item_name": order.name,
                "catalog_number": order.catalog_number,
                "quantity": order.quantity,
                "status": order.status,
                "requester_name": order.owner,
            }
        )


@app.route("/notebook/open/<entity_type>/<int:number>")
@login_required
def notebook_open_mention(entity_type: str, number: int):
    """Where an @mention (@mouse 12, @plasmid 4, @order 7) leads: the record
    itself, not its list. The number is the one people see."""
    with SessionLocal() as db_session:
        if entity_type == "mouse":
            mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == number))
            if mouse is not None:
                return redirect(url_for("colony", view="mice", scope="all") + f"#mouse-update-{mouse.id}")
            flash(gettext("There is no mouse #%(number)s.", number=number), "warning")
            return redirect(url_for("colony", view="mice"))
        if entity_type == "plasmid":
            plasmid = db_session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == number))
            if plasmid is not None:
                return redirect(url_for("plasmid_page", number=plasmid.plasmid_id))
            flash(gettext("There is no plasmid #%(number)s.", number=number), "warning")
            return redirect(url_for("plasmids"))
        if entity_type == "order":
            found = _order_items_query(db_session, str(number), 1, by_number=True)
            if found:
                module = db_session.get(InventoryModule, found[0].module_id_fk)
                return redirect(url_for("inventory.module", key=module.key, open=found[0].id))
            flash(gettext("There is no order #%(number)s.", number=number), "warning")
            return redirect(url_for("home_dashboard"))
        module = _mention_modules(db_session, with_old=True).get(entity_type)
        if module is not None:
            item = db_session.scalar(select(InventoryItem).where(
                InventoryItem.module_id_fk == module.id, InventoryItem.number == number))
            if item is not None:
                return redirect(url_for("inventory.module", key=module.key, open=item.id))
            flash(gettext("There is no %(noun)s #%(number)s in %(database)s.", noun=i18n.translate_value(module.item_noun),
                          number=number, database=i18n.translate_value(module.label)), "warning")
            return redirect(url_for("inventory.module", key=module.key))
    abort(404)


@app.route("/notebook/backlinks/<entity_type>/<int:entity_id>")
@login_required
def notebook_backlinks(entity_type: str, entity_id: int):
    """Return notebook pages whose body mentions @<type> <id>.

    Scoped to pages the current user may open (theirs and shared). Returns a list of
    {page_id, page_title, tab_id, tab_title, snippet, updated_at}.
    """
    with SessionLocal() as db_session:
        # An inventory is found by every key it has had: "@antibodies 5" from
        # before a rename is the same record as "@primary_antibodies 5".
        keys = [entity_type]
        if entity_type not in ("mouse", "plasmid", "order"):
            known = _mention_modules(db_session, with_old=True)
            if entity_type not in known:
                return jsonify({"ok": False, "error": "bad type"}), 400
            keys = [k for k, m in known.items() if m.id == known[entity_type].id]
        found = None
        for key in keys:
            clause = NotebookPage.body.ilike(like_pattern(f"@{key} {entity_id}"), escape="\\")
            found = clause if found is None else (found | clause)
        stmt = (
            lab_notebook.accessible_filter(
                select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id))
            .where(found)
            .order_by(NotebookPage.updated_at.desc())
            .limit(25)
        )
        rows = db_session.scalars(stmt).all()
        items = []
        # Use a word-boundary check to avoid `@mouse 12` matching `@mouse 123`.
        import re
        pattern = re.compile(rf"@(?:{'|'.join(re.escape(k) for k in keys)})\s+{entity_id}(?!\d)")
        for page in rows:
            body = page.body or ""
            m = pattern.search(body)
            if not m:
                continue
            start = max(0, m.start() - 40)
            end = min(len(body), m.end() + 60)
            snippet = body[start:end].replace("\n", " ").strip()
            if start > 0:
                snippet = "…" + snippet
            if end < len(body):
                snippet = snippet + "…"
            items.append({
                "page_id": page.id,
                "page_title": page.title or "Untitled page",
                "tab_id": page.tab_id_fk,
                "tab_title": page.tab.title if page.tab else "",
                "snippet": snippet,
                "updated_at": page.updated_at.isoformat(),
            })
        return jsonify({"ok": True, "count": len(items), "items": items})


@app.route("/notebook/search/<entity_type>")
@login_required
def notebook_search_entity(entity_type: str):
    query = (request.args.get("q") or "").strip()
    limit = max(1, min(arg_int("limit", 8), 25))
    with SessionLocal() as db_session:
        if entity_type == "mouse":
            stmt = select(MouseRecord)
            if _record_number(query) is not None:
                stmt = stmt.where(MouseRecord.mouse_id == int(query))
            elif query:
                stmt = stmt.where(MouseRecord.genotype.ilike(like_pattern(query), escape="\\") | MouseRecord.owner.ilike(like_pattern(query), escape="\\"))
            stmt = stmt.order_by(MouseRecord.mouse_id.desc()).limit(limit)
            rows = db_session.scalars(stmt).all()
            return jsonify({"ok": True, "items": [
                {"id": m.mouse_id, "label": f"#{m.mouse_id} · {m.gender or '?'} · {m.genotype or '(no genotype)'}"}
                for m in rows
            ]})
        if entity_type == "plasmid":
            stmt = select(PlasmidRecord)
            if _record_number(query) is not None:
                stmt = stmt.where(PlasmidRecord.plasmid_id == int(query))
            elif query:
                stmt = stmt.where(PlasmidRecord.name.ilike(like_pattern(query), escape="\\") | PlasmidRecord.backbone.ilike(like_pattern(query), escape="\\"))
            stmt = stmt.order_by(PlasmidRecord.plasmid_id.desc()).limit(limit)
            rows = db_session.scalars(stmt).all()
            return jsonify({"ok": True, "items": [
                {"id": p.plasmid_id, "label": f"#{p.plasmid_id} · {p.name or '(no name)'} · {p.backbone or '?'}"}
                for p in rows
            ]})
        if entity_type == "order":
            rows = _order_items_query(db_session, query, limit)
            return jsonify({"ok": True, "items": [
                {"id": o.number, "label": f"#{o.number} · {o.vendor or '?'} · {o.name or '(no item)'} · {o.status or 'requested'}"}
                for o in rows
            ]})
        if entity_type == "all":
            # Unified search across mouse + plasmid + order. Each result includes
            # `type` so the editor can build the right `@<type> <id>` chip.
            per_type_limit = max(2, limit // 3)
            items: list[dict] = []
            number = _record_number(query)
            # People first, for "@jordan" and action items ("- [ ] @jordan …"):
            # a name that starts with what was typed.
            if query and not query.isdigit():
                q = query.lower()
                for person in lab_notebook.people(db_session):
                    names = [person["username"].lower(), *person["name"].lower().split()]
                    if not person["guest"] and any(n.startswith(q) for n in names):
                        items.append({"type": "person", "type_label": gettext("Person"), "id": person["username"],
                                      "label": f"{person['name']} · @{person['username']}"})
                items = items[:3]

            # A database by its name ("@prim" → Primary antibodies): picking
            # it writes "@primary_antibodies " and the menu lists its records.
            if query and _record_number(query) is None:
                q = query.lower()
                for key, module in _mention_modules(db_session).items():
                    if any(w.startswith(q) for w in (key, *module.label.lower().split())):
                        items.append({"type": "database", "type_label": gettext("Database"), "id": key,
                                      "label": f"{module.label} · @{key} <number>"})

            # A lot or catalogue number typed as it is (@0012, @ab2947) is what
            # was meant, before any record that happens to have that number.
            codes = []
            if query:
                for module in _mention_modules(db_session).values():
                    for i in _mention_items(db_session, module, query, per_type_limit):
                        if query.lower() in ((i.lot or "").lower(), (i.catalog_number or "").lower()):
                            codes.append({"type": module.key, "type_label": module.label, "id": i.number,
                                          "label": _mention_label(module, i, True)})
            items += codes

            mouse_stmt = select(MouseRecord)
            if number is not None:
                mouse_stmt = mouse_stmt.where(MouseRecord.mouse_id == number)
            elif query:
                like = like_pattern(query)
                mouse_stmt = mouse_stmt.where(
                    MouseRecord.genotype.ilike(like, escape="\\") | MouseRecord.owner.ilike(like, escape="\\")
                )
            mouse_stmt = mouse_stmt.order_by(MouseRecord.mouse_id.desc()).limit(per_type_limit)
            for m in db_session.scalars(mouse_stmt).all():
                items.append({
                    "type": "mouse",
                    "id": m.mouse_id,
                    "label": f"Mouse #{m.mouse_id} · {m.gender or '?'} · {m.genotype or '(no genotype)'}",
                })

            plasmid_stmt = select(PlasmidRecord)
            if number is not None:
                plasmid_stmt = plasmid_stmt.where(PlasmidRecord.plasmid_id == number)
            elif query:
                like = like_pattern(query)
                plasmid_stmt = plasmid_stmt.where(
                    PlasmidRecord.name.ilike(like, escape="\\") | PlasmidRecord.backbone.ilike(like, escape="\\")
                )
            plasmid_stmt = plasmid_stmt.order_by(PlasmidRecord.plasmid_id.desc()).limit(per_type_limit)
            for p in db_session.scalars(plasmid_stmt).all():
                items.append({
                    "type": "plasmid",
                    "id": p.plasmid_id,
                    "label": f"Plasmid #{p.plasmid_id} · {p.name or '(no name)'} · {p.backbone or '?'}",
                })

            for o in _order_items_query(db_session, query, per_type_limit):
                items.append({
                    "type": "order",
                    "id": o.number,
                    "label": f"Order #{o.number} · {o.vendor or '?'} · {o.name or '(no item)'}",
                })

            # Every other inventory, most recent first; a bare "@" keeps to
            # the three above so the list stays short.
            if query:
                # Each database's matches, taken in turn, so one with many
                # (six SO-RNA tubes) fills the list when the others have few.
                found = [[{"type": module.key, "type_label": module.label, "id": i.number,
                           "label": _mention_label(module, i, True)}
                          for i in _mention_items(db_session, module, query, limit)]
                         for module in _mention_modules(db_session).values()]
                room = limit - len(items)
                while room > 0 and any(found):
                    for hits in found:
                        if hits and room > 0:
                            hit = hits.pop(0)
                            if hit not in codes:
                                items.append(hit)
                                room -= 1

            return jsonify({"ok": True, "items": items[:limit]})
        module = _mention_modules(db_session, with_old=True).get(entity_type)
        if module is not None:
            return jsonify({"ok": True, "items": [
                {"id": i.number, "label": _mention_label(module, i)}
                for i in _mention_items(db_session, module, query, limit)]})
        return jsonify({"ok": False, "error": f"Unknown entity type: {entity_type}"}), 400


# ---------------------------------------------------------------------------
# Plasmids: the sheet, box grid and list (/plasmids), the detail page with
# the sequence editor (/plasmid/<number>), and the writes behind them
# (/plasmids/<row id>/…, where the page's own script sends them).
#
# Every write checks access.can_edit: a plasmid is its owner's (or anyone's
# while unowned); admins can change anything. Boxes are PlasmidBox rows
# (app/plasmid_service.py): each has a size and a naming scheme, and every
# position is shown and typed in it ("D7"). Resizing, renaming or deleting a
# box is access.can_edit_rack (its creator or an admin); putting a plasmid
# in any box is open to whoever may edit the plasmid.
# ---------------------------------------------------------------------------

from . import plasmid_service as pbox  # noqa: E402
from .models import PlasmidBox  # noqa: E402


def _plasmid_denied(p) -> str:
    owner = (p.owner or "").strip() or gettext("someone else")
    return gettext("Plasmid #%(id)s belongs to %(owner)s. Ask them, or an admin, to change it.", id=p.plasmid_id,
                   owner=owner)


def _plasmid_label(p) -> str:
    return f"#{p.plasmid_id}" + (f" {p.name}" if p.name else "")


def _next_plasmid_id(db_session) -> int:
    return (db_session.scalar(select(func.max(PlasmidRecord.plasmid_id))) or 0) + 1


def _box_of(db_session, p):
    return db_session.get(PlasmidBox, p.box_id_fk) if p.box_id_fk else None


def _plasmid_where(p, box) -> str:
    """"Box A · D7", "Box A" (no cell yet), or ""."""
    if box is None:
        return ""
    position = pbox.label(p, box)
    return f"{box.name} · {position}" if position else box.name


def _submitted_sequence(file_field: str) -> tuple[dict | None, str]:
    """The sequence a form sent, as (parsed, problem). Both are empty when
    nothing was sent; a file or text that is not a sequence gives a problem
    instead of being quietly ignored."""
    from .sequence_parser import parse_sequence_bytes, parse_sequence_text

    upload = request.files.get(file_field)
    if upload is not None and upload.filename:
        parsed = parse_sequence_bytes(upload.read(), upload.filename)
        if not parsed or not parsed.get("sequence"):
            return None, gettext("%(file)s is not a sequence file this app can read (SnapGene .dna, GenBank or FASTA).",
                                 file=upload.filename)
        return parsed, ""
    raw_text = (request.form.get("sequence_text") or "").strip()
    if raw_text:
        parsed = parse_sequence_text(raw_text)
        if not parsed or not parsed.get("sequence"):
            return None, gettext("The pasted text is not a sequence. Paste FASTA, GenBank, or bases only (IUPAC letters).")
        return parsed, ""
    return None, ""


def _apply_parsed_sequence(p, parsed: dict) -> None:
    p.full_sequence = parsed["sequence"]
    p.is_circular = bool(parsed.get("is_circular"))
    p.features_json = json.dumps(parsed.get("features") or [])
    p.sequence_format = parsed.get("format", "")
    p.sequence_uploaded_at = datetime.utcnow()


def _plasmid_values(p, box) -> dict:
    """What the sheet shows for a row, as the server now has it."""
    return {
        "name": p.name, "backbone": p.backbone, "insert_seq": p.insert_seq,
        "resistance": p.resistance, "owner": p.owner, "location": p.location,
        "concentration": p.concentration, "a260_280": p.a260_280, "is_shared": project_groups.record_value(p),
        "notes": p.notes, "box_id": str(p.box_id_fk or ""), "storage_box": box.name if box else "",
        "position": pbox.label(p, box),
    }


def _plasmid_payload(p, box, editable: bool) -> dict:
    """The record dialog's view of a plasmid (see static/record-dialog.js)."""
    values = _plasmid_values(p, box)
    return {
        "id": p.id, "_label": _plasmid_label(p), "_locked": not editable, "_manage": access.can_manage(p),
        "plasmid_id": p.plasmid_id, **values,
        "box_id_was": values["box_id"], "position_was": values["position"],
    }


def _plasmid_grid(rows, boxes, new_payload) -> dict:
    """The rack grid's payload (static/rack-grid.js): boxes grouped by their
    freezer, and every plasmid as a tile. Rows and columns are 1-based
    here; the page posts moves 0-based (data-index-base="0")."""
    counts = {}
    for r in rows:
        if r["p"].box_id_fk:
            counts[r["p"].box_id_fk] = counts.get(r["p"].box_id_fk, 0) + 1
    items = []
    for r in rows:
        p = r["p"]
        placed = pbox.is_stored(p)
        items.append({
            "id": p.id, "label": f"#{p.plasmid_id}", "sub": p.name or "",
            "badge": (p.resistance or "")[:5], "rack": p.box_id_fk,
            "row": p.box_row + 1 if placed else None, "col": p.box_col + 1 if placed else None,
            "locked": not r["editable"],
            "title": " · ".join(filter(None, [f"#{p.plasmid_id}", p.name, p.backbone, p.resistance, p.owner,
                                              "" if r["editable"] else "read only"])),
            "search": " ".join(filter(None, [f"#{p.plasmid_id}", p.name, p.backbone, p.insert_seq,
                                             p.resistance, p.owner, r["position"]])).lower(),
            "edit": {"data-record-edit": "plasmid-dialog", "data-record-payload": json.dumps(r["payload"])},
        })
    return {
        "racks": [{"id": b.id, "name": b.name, "rows": b.rows, "cols": b.cols, "group": b.location or "",
                   "naming": positions.scheme(b.naming),
                   "edit": {"data-record-edit": "plasmid-box-dialog",
                            "data-record-payload": json.dumps(pbox.box_payload(b, counts.get(b.id, 0)))}}
                  for b in boxes],
        "items": items,
        "create": {"attrs": {"data-record-edit": "plasmid-dialog"}, "payload": new_payload,
                   "rack_field": "box_id", "row_field": None, "col_field": None, "text_field": "position"},
    }


def _plasmid_back(row_id: int | None = None):
    """Back to the page the form was on (this site only), else the plasmid."""
    referrer = request.referrer or ""
    if referrer.startswith(request.host_url):
        return redirect(referrer)
    if row_id:
        return redirect(plasmid_page_url(row_id))
    return redirect(url_for("plasmids"))


PLASMID_MEASURES = (("concentration", "Concentration (ng/µL)", 40), ("a260_280", "260/280", 20))


def _plasmid_measure(form, field: str) -> str:
    """A plasmid's concentration or 260/280 as typed, if it is a number
    ("412", "1.86"); ValueError with the message to show otherwise."""
    label, limit = next((lb, lim) for key, lb, lim in PLASMID_MEASURES if key == field)
    value = (form.get(field) or "").strip().replace(",", ".")[:limit]
    if value:
        try:
            float(value)
        except ValueError:
            raise ValueError(gettext("%(field)s is a number: “%(value)s” isn't one.", field=gettext(label),
                                     value=value)) from None
    return value


def _plasmid_int(raw) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


@app.route("/plasmids", methods=["GET", "POST"])
@login_required
def plasmids():
    if request.method == "POST":
        return _create_plasmid()
    me = g.user.username
    with SessionLocal() as db_session:
        records = db_session.scalars(select(PlasmidRecord).order_by(PlasmidRecord.plasmid_id.desc())).all()
        boxes = pbox.all_boxes(db_session)
        box_by_id = {b.id: b for b in boxes}
        rows = []
        for p in records:
            editable = access.can_edit(p)
            box = box_by_id.get(p.box_id_fk)
            rows.append({
                "p": p, "box": box, "editable": editable, "manageable": access.can_manage(p), "mine": p.owner == me,
                "stored": pbox.is_stored(p), "position": pbox.label(p, box),
                "where": _plasmid_where(p, box),
                "length": len(p.full_sequence or ""),
                "payload": _plasmid_payload(p, box, editable),
            })
        next_id = _next_plasmid_id(db_session)
        usernames = current_lab_usernames(db_session)
        new_payload = {"owner": me, "plasmid_id": next_id, "count": 1}
        grid = _plasmid_grid(rows, boxes, new_payload)
        counts = {
            "all": len(rows),
            "mine": sum(1 for r in rows if r["mine"]),
            "sequence": sum(1 for r in rows if r["length"]),
            "stored": sum(1 for r in rows if r["stored"]),
            "lab": sum(1 for r in rows if r["p"].is_shared),
        }
        box_list = [{"id": b.id, "name": b.name, "location": b.location or ""} for b in boxes]
        return render_template(
            "plasmids.html",
            rows=rows,
            counts=counts,
            usernames=usernames,
            next_plasmid_id=next_id,
            new_payload=new_payload,
            boxes=box_list,
            grid=grid,
            max_batch=pbox.MAX_BATCH,
        )


def _create_plasmid():
    """New plasmids from the dialog: one, or up to 50 alike ("How many"),
    or one per line of a pasted list of names. They get consecutive
    numbers and sit side by side in the chosen box from the given position
    (or its first free cells); what does not fit waits in the box without a
    cell, and is reported. The number comes from the server when the one
    the dialog offered was taken meanwhile. A sequence that cannot be read,
    or a cell that is taken, is reported rather than dropped in silence
    (the plasmids are still created). One batch, so it can be undone."""
    form = request.form
    user = g.user.username
    parsed, seq_problem = _submitted_sequence("sequence_file")

    raw_id = (form.get("plasmid_id") or "").strip()
    if raw_id and (not raw_id.isdigit() or int(raw_id) < 1):
        flash(gettext("“%(value)s” is not a plasmid number. Leave it blank for the next free one.", value=raw_id),
              "error")
        return redirect(url_for("plasmids"))
    requested = int(raw_id) if raw_id else None

    names = [line.strip()[:200] for line in (form.get("names") or "").splitlines() if line.strip()]
    raw_count = str(len(names)) if names else (form.get("count") or "1").strip()
    count = int(raw_count) if raw_count.isdigit() else 0
    if not 1 <= count <= pbox.MAX_BATCH:
        flash(gettext("Make between 1 and %(max)s plasmids at a time (asked for %(asked)s).", max=pbox.MAX_BATCH,
                      asked=raw_count), "error")
        return redirect(url_for("plasmids"))

    name = (form.get("name") or "").strip()[:200] or ((parsed or {}).get("name") or "")[:200]
    if not names and not name:
        flash(gettext("Give the plasmid a name."), "error")
        return redirect(url_for("plasmids"))

    for _attempt in range(3):
        with SessionLocal() as db_session:
            try:
                made = _make_plasmids(db_session, form, user, count, name, names, requested, parsed)
                db_session.commit()
            except IntegrityError:
                # Someone took a number between our check and the commit.
                db_session.rollback()
                requested = None
                continue
            break
    else:
        flash(gettext("Couldn’t allocate plasmid numbers. Try again."), "error")
        return redirect(url_for("plasmids"))

    ids = made["ids"]
    first, last = ids[0], ids[-1]
    if count == 1:
        if raw_id and first != int(raw_id):
            made["notes"].insert(0, gettext("Plasmid #%(asked)s was taken by the time you saved, so this one is #%(first)s.", asked=raw_id, first=first))
        if parsed:
            flash(gettext("Added plasmid #%(first)s with a %(format)s sequence (%(bp)s bp · %(features)s features).",
                          first=first, format=parsed["format"].upper(), bp=len(parsed["sequence"]),
                          features=len(parsed.get("features") or [])), "success")
        else:
            flash(gettext("Added plasmid #%(first)s %(name)s.", first=first, name=made["names"][0]), "success")
    else:
        if raw_id and first != int(raw_id):
            made["notes"].insert(0, gettext("Some of #%(asked)s–#%(asked_last)s were taken, so these are #%(first)s–#%(last)s.", asked=raw_id, asked_last=int(raw_id) + count - 1,
                                            first=first, last=last))
        where = ""
        cells = made["cells"]
        if made["box"] and len(cells) > 1:
            where = gettext(" in %(box)s (%(first)s–%(last)s)", box=made["box"], first=cells[0], last=cells[-1])
        elif made["box"] and cells:
            where = gettext(" in %(box)s (%(cell)s)", box=made["box"], cell=cells[0])
        elif made["box"]:
            where = gettext(" in %(box)s", box=made["box"])
        seq = gettext(" with a %(format)s sequence", format=parsed["format"].upper()) if parsed else ""
        flash(gettext("Added %(count)s plasmids, #%(first)s–#%(last)s%(seq)s%(where)s. Undo it from Batch history.",
                      count=count, first=first, last=last, seq=seq, where=where), "success")
    for note in made["notes"]:
        flash(note, "warning")
    if seq_problem:
        flash(gettext("%(problem)s The plasmids were saved without a sequence; add one on their pages.",
                      problem=seq_problem) if count > 1 else
              gettext("%(problem)s The plasmid was saved without a sequence; add one on its page.",
                      problem=seq_problem), "warning")
    return redirect(url_for("plasmids"))


def _make_plasmids(db_session, form, user, count, name, names, requested, parsed) -> dict:
    """The writes behind _create_plasmid, inside one batch."""
    notes: list[str] = []
    label = (f"New plasmid {name or names[0]}" if count == 1
             else f"New plasmids ×{count}" + (f" ({names[0]}…)" if names else f" ({name})"))
    with audit.batch(db_session, "create", label, "plasmids") as batch_row:
        batch_row.record_count = count
        _given, box, problem = pbox.box_from_form(db_session, form, user)
        if problem:
            notes.append(gettext("%(problem)s The plasmids are not in a box.", problem=problem) if count > 1
                         else gettext("%(problem)s The plasmid is not in a box.", problem=problem))
        # Where the first one goes: "position" (D7) from the dialog, or the
        # older 0-based box_row / box_col pair; blank takes the first free cell.
        position = (form.get("position") or "").strip()
        legacy = (form.get("box_row") or "").strip() or (form.get("box_col") or "").strip()
        start, mode = None, "next"
        try:
            if position or legacy:
                if box is None:
                    raise ValueError(gettext("A position needs a box; the plasmid was saved without one.")
                                     if count == 1 else
                                     gettext("A position needs a box; the plasmids were saved without one."))
                if position:
                    start = pbox.parse(position, box)
                else:
                    start = (_plasmid_int(form.get("box_row")), _plasmid_int(form.get("box_col")))
                    if None in start:
                        raise ValueError(gettext("A box position needs both a row and a column."))
                    if not (0 <= start[0] < box.rows and 0 <= start[1] < box.cols):
                        raise ValueError(gettext("That cell is outside the box %(box)s (%(span)s).", box=box.name,
                                                 span=pbox.span(box)))
                mode = "exact" if count == 1 else "from"
        except ValueError as exc:
            if box is None:
                notes.append(f"{exc}")
            elif count > 1:
                notes.append(gettext("%(problem)s The new plasmids are in %(box)s but not placed.", problem=exc,
                                     box=box.name))
            else:
                notes.append(gettext("%(problem)s The new plasmid is in %(box)s but not placed.", problem=exc,
                                     box=box.name))
            mode = "none"

        cells = []
        if box is not None and mode in ("next", "from"):
            cells = pbox.free_cells(db_session, box, count, start if mode == "from" else None)
            if len(cells) < count:
                missing = count - len(cells)
                values = {"box": box.name, "room": len(cells), "count": count, "num": missing}
                if mode == "from":
                    values["start"] = pbox.cell_label(box, *start)
                    notes.append(ngettext("%(box)s had room for %(room)s of %(count)s from %(start)s; the other %(num)s is in it without a position.",
                                          "%(box)s had room for %(room)s of %(count)s from %(start)s; the other %(num)s are in it without a position.", missing, **values)
                                 if cells else
                                 ngettext("%(box)s had room for %(room)s of %(count)s from %(start)s; %(num)s is in it without a position.",
                                          "%(box)s had room for %(room)s of %(count)s from %(start)s; %(num)s are in it without a position.", missing, **values))
                else:
                    notes.append(ngettext("%(box)s had room for %(room)s of %(count)s; the other %(num)s is in it without a position.",
                                          "%(box)s had room for %(room)s of %(count)s; the other %(num)s are in it without a position.", missing, **values)
                                 if cells else
                                 ngettext("%(box)s had room for %(room)s of %(count)s; %(num)s is in it without a position.",
                                          "%(box)s had room for %(room)s of %(count)s; %(num)s are in it without a position.", missing, **values))

        top = _next_plasmid_id(db_session)
        first = top
        if requested:
            clash = db_session.scalar(select(func.count(PlasmidRecord.id)).where(
                PlasmidRecord.plasmid_id.between(requested, requested + count - 1)))
            if not clash:
                first = requested
        owner = form.get("owner", user).strip()[:120]
        measures = {}
        for field, _label, _limit in PLASMID_MEASURES:
            try:
                measures[field] = _plasmid_measure(form, field)
            except ValueError as exc:
                measures[field] = ""
                notes.append(gettext("%(problem)s It was left empty.", problem=exc))
        made = []
        for i in range(count):
            record = PlasmidRecord(
                plasmid_id=first + i,
                name=names[i] if names else name,
                backbone=(form.get("backbone") or "").strip()[:200],
                insert_seq=(form.get("insert_seq") or "").strip()[:200],
                resistance=(form.get("resistance") or "").strip()[:80],
                owner=owner,
                location=(form.get("location") or "").strip()[:120],
                concentration=measures["concentration"], a260_280=measures["a260_280"],
                notes=(form.get("notes") or "").strip(),
            )
            if project_groups.apply(record, form.get("is_shared")):
                record.is_shared, record.share_group_id = False, None
            pbox.put_in(record, box)
            if mode == "exact":
                refused, _ = pbox.place(db_session, record, box, *start)
                if refused:
                    pbox.put_in(record, box)
                    notes.append(gettext("%(problem)s The new plasmid is in %(box)s but not placed.", problem=refused,
                                         box=box.name))
            elif i < len(cells):
                record.box_row, record.box_col = cells[i]
            db_session.add(record)
            if parsed:
                _apply_parsed_sequence(record, parsed)
            stamp_updated(record)
            made.append(record)
    return {
        "ids": [r.plasmid_id for r in made], "names": [r.name for r in made],
        "box": box.name if box is not None else "",
        "cells": [pbox.label(r, box) for r in made if box is not None and pbox.label(r, box)],
        "notes": notes,
    }


def _plasmid_answer(db_session, p, message: str = "", error: str = "", status: int = 409):
    """Answer a plasmid save: JSON for the sheet's autosave, else a flash
    and a redirect back to the page the form was on."""
    if request.headers.get("X-Autosave") == "1":
        if error:
            return jsonify({"ok": False, "error": error}), status
        box = _box_of(db_session, p)
        return jsonify({"ok": True, "row": {"active": pbox.is_stored(p), "values": _plasmid_values(p, box)},
                        "payload": _plasmid_payload(p, box, access.can_edit(p))})
    if error:
        flash(error, "error")
    elif message:
        flash(message, "success")
    return _plasmid_back(p.id if p is not None else None)


@app.route("/plasmids/<int:row_id>/update", methods=["POST"])
@login_required
def update_plasmid(row_id: int):
    """Inline edits from the sheet (X-Autosave, JSON back) and the record
    dialog / detail page forms (redirect back). Only fields the form sent
    change; box and position follow the `_was` stale-form guard, because
    the box grid can move a plasmid while its sheet row is on screen.
    The box comes as `box_id` (or, from older forms, a `storage_box`
    name); the position is typed in the box's own naming ("D7")."""
    form = request.form
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            if request.headers.get("X-Autosave") == "1":
                return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            return _plasmid_answer(db_session, p, error=_plasmid_denied(p), status=403)
        if "name" in form and not form["name"].strip() and (p.name or "").strip():
            return _plasmid_answer(db_session, p, error=gettext("A plasmid needs a name."), status=400)
        # Lab common lets anyone edit it; whose it is stays its owner's call.
        # "1": the lab's, "g<id>": a project group's (app/groups.py).
        sharing_changes = "is_shared" in form and project_groups.differs(p, form.get("is_shared"))
        owner_changes = "owner" in form and (form.get("owner") or "").strip()[:120] != (p.owner or "")
        if (owner_changes or sharing_changes) and not access.can_manage(p):
            owner = (p.owner or "").strip() or gettext("its owner")
            return _plasmid_answer(db_session, p, status=403, error=gettext(
                "Plasmid #%(id)s is lab common, so you can edit it, but only %(owner)s or an admin can change whose it is.", id=p.plasmid_id, owner=owner))
        if sharing_changes:
            refused = project_groups.apply(p, form.get("is_shared"))
            if refused:
                return _plasmid_answer(db_session, p, error=refused, status=403)
        for field, limit in (("name", 200), ("backbone", 200), ("insert_seq", 200),
                             ("resistance", 80), ("owner", 120), ("location", 120)):
            if field in form:
                setattr(p, field, (form.get(field) or "").strip()[:limit])
        for field, _label, _limit in PLASMID_MEASURES:
            if field in form:
                try:
                    setattr(p, field, _plasmid_measure(form, field))
                except ValueError as exc:
                    db_session.rollback()
                    return _plasmid_answer(db_session, p, error=str(exc), status=400)
        if "notes" in form:
            p.notes = (form.get("notes") or "").strip()

        problem = None
        if (("box_id" in form or "storage_box" in form or "position" in form)
                and form_changed(form, "box_id", "storage_box", "position")):
            current = _box_of(db_session, p)
            _given, box, problem = pbox.box_from_form(db_session, form, g.user.username, current)
            if not problem:
                typed = form.get("position") if "position" in form else pbox.label(p, current)
                if box is None:
                    # Clearing the box takes the plasmid out of it; typing a
                    # position with no box is a mistake worth saying so.
                    if (typed or "").strip() and form_changed(form, "position"):
                        problem = gettext("A position needs a box. Pick the box first.")
                    else:
                        pbox.place(db_session, p, None, None, None)
                else:
                    try:
                        row, col = pbox.parse(typed, box)
                    except ValueError as exc:
                        problem = str(exc)
                    else:
                        if row is None and (current is None or current.id != box.id):
                            # Into another box with no position: its next free cell.
                            cells = pbox.free_cells(db_session, box, 1)
                            row, col = cells[0] if cells else (None, None)
                        problem, _ = pbox.place(db_session, p, box, row, col)
        if problem and request.headers.get("X-Autosave") == "1":
            db_session.rollback()
            return jsonify({"ok": False, "error": problem}), 409
        # A dialog save keeps its other fields; only the move is refused
        # (a refused move changed nothing).
        stamp_updated(p)
        db_session.commit()
        if problem:
            return _plasmid_answer(db_session, p, error=gettext(
                "Saved plasmid #%(id)s, but not the box position: %(problem)s", id=p.plasmid_id, problem=problem))
        return _plasmid_answer(db_session, p, message=gettext("Saved plasmid #%(id)s.", id=p.plasmid_id))


@app.route("/plasmids/<int:row_id>/delete", methods=["POST"])
@login_required
def delete_plasmid(row_id: int):
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_manage(p):
            flash(_plasmid_denied(p) if not access.can_edit(p) else
                  gettext("Plasmid #%(id)s is lab common: only %(owner)s or an admin can delete it.", id=p.plasmid_id,
                          owner=(p.owner or "").strip() or gettext("its owner")), "error")
            return redirect(url_for("plasmids"))
        label = _plasmid_label(p)
        # A batch of one, so Batch history can bring it back.
        with audit.batch(db_session, "delete", f"delete plasmid {label}", "plasmids"):
            db_session.delete(p)
        db_session.commit()
    flash(gettext("Deleted plasmid %(plasmid)s. Undo it from Batch history.", plasmid=label), "success")
    return redirect(url_for("plasmids"))


@app.route("/plasmids/<int:row_id>/duplicate", methods=["POST"])
@login_required
def duplicate_plasmid(row_id: int):
    """A copy to start a derivative from: same construct and sequence, the
    next number, owned by you, not in a box."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        copy = PlasmidRecord(
            plasmid_id=_next_plasmid_id(db_session), name=f"{p.name} (copy)"[:200],
            backbone=p.backbone, insert_seq=p.insert_seq, resistance=p.resistance,
            owner=g.user.username, location="", notes=p.notes,
            full_sequence=p.full_sequence, is_circular=p.is_circular, features_json=p.features_json,
            sequence_format=p.sequence_format, sequence_uploaded_at=p.sequence_uploaded_at,
        )
        db_session.add(copy)
        stamp_updated(copy)
        db_session.commit()
        flash(gettext("Duplicated plasmid #%(id)s as #%(copy)s.", id=p.plasmid_id, copy=copy.plasmid_id), "success")
    return redirect(url_for("plasmids"))


@app.route("/plasmids/bulk", methods=["POST"])
@login_required
def bulk_plasmids():
    """Batch actions on ticked plasmids: set owner or resistance, move to a
    box, delete. One audit batch, so /batches can undo it; plasmids the user
    may not edit are skipped and counted. Move takes `box_id` (blank takes
    them out of their boxes), or a box name in `value` from older forms."""
    action = request.form.get("action", "")
    value = (request.form.get("value") or "").strip()
    ids = [int(i) for i in request.form.getlist("selected_ids") if i.isdigit()]
    with SessionLocal() as db_session:
        records = db_session.scalars(
            select(PlasmidRecord).where(PlasmidRecord.id.in_(ids)).order_by(PlasmidRecord.plasmid_id)
        ).all() if ids else []
        if not records:
            flash(gettext("Tick the plasmids to change first."), "info")
            return redirect(url_for("plasmids"))
        if action not in ("owner", "resistance", "move", "delete", "shared"):
            flash(gettext("Unknown batch action."), "error")
            return redirect(url_for("plasmids"))
        target, target_name = None, ""
        if action == "move":
            if "box_id" in request.form:
                raw = (request.form.get("box_id") or "").strip()
                target = db_session.get(PlasmidBox, int(raw)) if raw.isdigit() else None
                if raw and target is None:
                    flash(gettext("That box no longer exists. Reload the page."), "error")
                    return redirect(url_for("plasmids"))
                target_name = target.name if target else ""
            else:
                target_name = value[:80]
                target = pbox.by_name(db_session, target_name)
                target_name = target.name if target else target_name
        # Whose it is, lab common or not, and deleting: the owner's; the rest:
        # anyone who may edit it.
        allowed = access.can_manage if action in ("owner", "shared", "delete") else access.can_edit
        share_group = project_groups.parse(value)[1] if action == "shared" else None
        if share_group is not None and not project_groups.may_share_with(share_group):
            flash(project_groups.refusal(share_group), "error")
            return redirect(url_for("plasmids"))
        common = (f"shared with {project_groups.name_of(share_group)}" if share_group
                  else "lab common" if value == "1" else "personal")
        editable = [p for p in records if allowed(p)]
        skipped = len(records) - len(editable)
        done, unplaced = 0, 0
        noun = lambda n: "plasmid" if n == 1 else "plasmids"  # noqa: E731
        descriptions = {
            "owner": f"set owner to {value or 'nobody'} on {len(editable)} {noun(len(editable))}",
            "resistance": f"set resistance to {value or 'none'} on {len(editable)} {noun(len(editable))}",
            "move": f"move {len(editable)} {noun(len(editable))} to {target_name or 'no box'}",
            "delete": f"delete {len(editable)} {noun(len(editable))}",
            "shared": f"make {len(editable)} {noun(len(editable))} {common}",
        }
        with audit.batch(db_session, "delete" if action == "delete" else "update",
                         descriptions[action], "plasmids") as batch_row:
            batch_row.record_count = len(editable)
            if action in ("owner", "resistance"):
                limit = 120 if action == "owner" else 80
                for p in editable:
                    setattr(p, action, value[:limit])
                    stamp_updated(p)
                    done += 1
                message = (ngettext("Set owner on %(num)s plasmid.", "Set owner on %(num)s plasmids.", done)
                           if action == "owner" else
                           ngettext("Set resistance on %(num)s plasmid.", "Set resistance on %(num)s plasmids.", done))
            elif action == "shared":
                for p in editable:
                    project_groups.apply(p, value)
                    stamp_updated(p)
                    done += 1
                if share_group:
                    message = ngettext("Made %(num)s plasmid shared with %(group)s.",
                                       "Made %(num)s plasmids shared with %(group)s.", done,
                                       group=project_groups.name_of(share_group))
                elif value == "1":
                    message = ngettext("Made %(num)s plasmid lab common.", "Made %(num)s plasmids lab common.", done)
                else:
                    message = ngettext("Made %(num)s plasmid personal.", "Made %(num)s plasmids personal.", done)
            elif action == "move":
                box = target
                if box is None and target_name:
                    box = pbox.box_for_name(db_session, target_name, g.user.username)
                moving = [p for p in editable if not (box is not None and p.box_id_fk == box.id and pbox.is_stored(p))]
                free = (pbox.free_cells(db_session, box, len(moving),
                                        taken=pbox.occupied(db_session, box.id, [p.id for p in moving]))
                        if box is not None else [])
                for p in moving:
                    pbox.put_in(p, box)
                    if box is not None and free:
                        p.box_row, p.box_col = free.pop(0)
                    else:
                        p.box_row = p.box_col = None
                        unplaced += 1 if box is not None else 0
                    stamp_updated(p)
                done = len(editable)
                message = (ngettext("Moved %(num)s plasmid to %(box)s.", "Moved %(num)s plasmids to %(box)s.", done,
                                    box=box.name) if box is not None
                           else ngettext("Took %(num)s plasmid out of their boxes.",
                                         "Took %(num)s plasmids out of their boxes.", done))
                if unplaced:
                    message += " " + gettext("%(n)s did not fit and wait in %(box)s without a position.", n=unplaced,
                                             box=box.name)
            else:
                for p in editable:
                    db_session.delete(p)
                    done += 1
                message = ngettext("Deleted %(num)s plasmid. Undo it from Batch history.",
                                   "Deleted %(num)s plasmids. Undo it from Batch history.", done)
        db_session.commit()
    if skipped:
        message += " " + gettext("%(n)s belong to someone else and were left alone.", n=skipped)
    flash(message, "success" if done else "warning")
    return redirect(url_for("plasmids"))


# ---- Boxes ------------------------------------------------------------------


def _plasmids_page(box_id: int | None = None):
    return redirect(url_for("plasmids", box=box_id) if box_id else url_for("plasmids"))


@app.route("/plasmids/boxes/save", methods=["POST"])
@login_required
def save_plasmid_box():
    """New box (anyone), or change one: its creator or an admin. A box
    cannot shrink past a plasmid; a rename follows onto its plasmids."""
    form = request.form

    def number(name, default, top):
        raw = (form.get(name) or "").strip()
        return max(1, min(top, int(raw))) if raw.isdigit() else default

    with SessionLocal() as db_session:
        raw_id = (form.get("id") or "").strip()
        box = db_session.get(PlasmidBox, int(raw_id)) if raw_id.isdigit() else None
        if raw_id and box is None:
            flash(gettext("That box no longer exists."), "error")
            return _plasmids_page()
        if box is not None and not access.can_edit_rack(box):
            flash(pbox.denied_box(box), "error")
            return _plasmids_page(box.id)
        name = (form.get("name") or "").strip()[:80]
        if not name:
            flash(gettext("Give the box a name."), "error")
            return _plasmids_page(box.id if box else None)
        clash = pbox.by_name(db_session, name)
        if clash is not None and (box is None or clash.id != box.id):
            flash(gettext("There is already a box called %(box)s.", box=clash.name), "error")
            return _plasmids_page(clash.id)
        rows = number("rows", box.rows if box else pbox.DEFAULT_ROWS, pbox.MAX_ROWS)
        cols = number("cols", box.cols if box else pbox.DEFAULT_COLS, pbox.MAX_COLS)
        if box is not None and (rows < box.rows or cols < box.cols):
            outside = db_session.scalars(select(PlasmidRecord).where(
                PlasmidRecord.box_id_fk == box.id,
                (PlasmidRecord.box_row >= rows) | (PlasmidRecord.box_col >= cols))
                .order_by(PlasmidRecord.plasmid_id)).all()
            if outside:
                n = len(outside)
                listed = ", ".join("#" + str(p.plasmid_id) for p in outside[:5]) + ("…" if n > 5 else "")
                flash(ngettext("%(box)s cannot shrink to %(rows)s × %(cols)s: %(num)s plasmid sits outside that (%(listed)s). Move them first.",
                               "%(box)s cannot shrink to %(rows)s × %(cols)s: %(num)s plasmids sit outside that (%(listed)s). Move them first.", n, box=box.name, rows=rows, cols=cols,
                               listed=listed), "error")
                return _plasmids_page(box.id)
        created = box is None
        if created:
            box = PlasmidBox(created_by=g.user.username)
            db_session.add(box)
        renamed = not created and box.name != name
        box.name, box.rows, box.cols = name, rows, cols
        box.naming = json.dumps(positions.scheme_from_form(form))
        box.location = (form.get("location") or "").strip()[:120]
        if "notes" in form:
            box.notes = (form.get("notes") or "").strip()
        db_session.flush()
        if renamed:
            for p in db_session.scalars(select(PlasmidRecord).where(PlasmidRecord.box_id_fk == box.id)):
                p.storage_box = name
        db_session.commit()
        flash(gettext("Added box %(box)s (%(rows)s × %(cols)s, positions %(span)s).", box=box.name, rows=rows,
                      cols=cols, span=pbox.span(box)) if created else
              gettext("Saved box %(box)s (%(rows)s × %(cols)s, positions %(span)s).", box=box.name, rows=rows,
                      cols=cols, span=pbox.span(box)), "success")
        return _plasmids_page(box.id)


@app.route("/plasmids/boxes/<int:box_id>/delete", methods=["POST"])
@login_required
def delete_plasmid_box(box_id: int):
    """Delete a box: its plasmids stay, no longer in a box. One batch, so
    Batch history can bring the box back with its plasmids in place."""
    with SessionLocal() as db_session:
        box = db_session.get(PlasmidBox, box_id)
        if box is None:
            flash(gettext("That box no longer exists."), "error")
            return _plasmids_page()
        if not access.can_edit_rack(box):
            flash(pbox.denied_box(box), "error")
            return _plasmids_page(box.id)
        name = box.name
        with audit.batch(db_session, "mixed", f"delete plasmid box {name}", "plasmid_boxes") as batch_row:
            members = db_session.scalars(select(PlasmidRecord).where(PlasmidRecord.box_id_fk == box.id)).all()
            batch_row.record_count = len(members) + 1
            for p in members:
                pbox.put_in(p, None)
                stamp_updated(p)
            db_session.flush()
            db_session.delete(box)
        db_session.commit()
    n = len(members)
    if n:
        flash(ngettext("Deleted box %(box)s. Its %(num)s plasmid is no longer in a box. Undo it from Batch history.",
                       "Deleted box %(box)s. Its %(num)s plasmids are no longer in a box. Undo it from Batch history.",
                       n, box=name), "success")
    else:
        flash(gettext("Deleted box %(box)s. Undo it from Batch history.", box=name), "success")
    return _plasmids_page()


def plasmid_page_url(row_id: int) -> str:
    """The page of the plasmid with this row: /plasmid/<its number>, the
    number the list, the labels and the API use."""
    with SessionLocal() as db_session:
        number = db_session.scalar(select(PlasmidRecord.plasmid_id).where(PlasmidRecord.id == row_id))
    return url_for("plasmid_page", number=number) if number is not None else url_for("plasmids")


@app.route("/plasmids/<int:row_id>")
@login_required
def plasmid_detail(row_id: int):
    """Addresses from before the page went by the plasmid's number (saved
    tabs, bookmarks, old notifications): on to /plasmid/<number>."""
    with SessionLocal() as db_session:
        number = db_session.scalar(select(PlasmidRecord.plasmid_id).where(PlasmidRecord.id == row_id))
    if number is None:
        flash(gettext("That plasmid isn't here any more. If it was deleted, More → Batch history can undo that."), "error")
        return redirect(url_for("plasmids"))
    return redirect(url_for("plasmid_page", number=number), code=301)


@app.route("/plasmid/<int:number>")
@login_required
def plasmid_page(number: int):
    with SessionLocal() as db_session:
        p = db_session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == number))
        if p is None:
            flash(gettext("There is no plasmid #%(number)s. If it was deleted, More → Batch history can undo that.",
                          number=number), "error")
            return redirect(url_for("plasmids"))
        try:
            features = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            features = []
        box = _box_of(db_session, p)
        boxes = [{"id": b.id, "name": b.name, "location": b.location or ""} for b in pbox.all_boxes(db_session)]
        usernames = current_lab_usernames(db_session)
        data = {
            "row_id": p.id,
            "plasmid_id": p.plasmid_id,
            "name": p.name,
            "backbone": p.backbone,
            "insert_seq": p.insert_seq,
            "resistance": p.resistance,
            "owner": p.owner,
            "location": p.location,
            "concentration": p.concentration, "a260_280": p.a260_280,
            "is_shared": bool(p.is_shared), "share_value": project_groups.record_value(p),
            "share_group_id": p.share_group_id, "can_manage": access.can_manage(p),
            "notes": p.notes,
            "box_id": p.box_id_fk or "",
            "storage_box": box.name if box else "",
            "box_location": box.location if box else "",
            "box_span": pbox.span(box) if box else "",
            "position": pbox.label(p, box),
            "where": _plasmid_where(p, box),
            "stored": pbox.is_stored(p),
            "sequence": p.full_sequence or "",
            "is_circular": bool(p.is_circular),
            "features": _features_only(features),
            "sequence_format": p.sequence_format or "",
            "sequence_uploaded_at": (i18n.strftime(local_time(p.sequence_uploaded_at), "%b %d, %Y %H:%M")
                                     if p.sequence_uploaded_at else ""),
            "length_bp": len(p.full_sequence or ""),
            "updated_at": i18n.strftime(local_time(p.updated_at), "%b %d, %Y") if p.updated_at else "",
            "updated_by": p.updated_by or "",
            "locked": not access.can_edit(p),
            "denied": _plasmid_denied(p),
        }
        # Viruses (or anything with a plasmid column) made from it.
        from . import inventory_service as inventories
        made_from = [{
            "label": m["item"].name or f"#{m['item'].number}", "number": m["item"].number,
            "database": m["module"].label, "status": m["item"].status, "available": m["available"],
            "url": url_for("inventory.module", key=m["module"].key, open=m["item"].id),
        } for m in inventories.made_from_plasmid(db_session, p.plasmid_id)]
    return render_template("plasmid_detail.html", plasmid=data, boxes=boxes, usernames=usernames,
                           made_from=made_from)


@app.route("/plasmids/<int:row_id>/upload-sequence", methods=["POST"])
@login_required
def plasmid_upload_sequence(row_id: int):
    """Accept a FASTA / GenBank / SnapGene .dna file (or pasted text) and
    parse it into the plasmid's sequence + features. Replaces any existing
    sequence."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        parsed, problem = _submitted_sequence("file")
        if problem:
            # Nothing was changed, so a warning (as on create), not an error.
            flash(gettext("%(problem)s The sequence was not changed.", problem=problem), "warning")
            return redirect(plasmid_page_url(row_id))
        if not parsed:
            flash(gettext("Choose a file or paste a sequence first."), "info")
            return redirect(plasmid_page_url(row_id))
        _apply_parsed_sequence(p, parsed)
        if parsed.get("name") and not p.name:
            p.name = parsed["name"]
        stamp_updated(p)
        db_session.commit()
    flash(gettext("Loaded %(format)s · %(bp)s bp · %(features)s features.", format=parsed["format"].upper(),
                  bp=len(parsed["sequence"]), features=len(parsed["features"])), "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/clear-sequence", methods=["POST"])
@login_required
def plasmid_clear_sequence(row_id: int):
    """The one way to empty a sequence: an explicit, confirmed action. The
    editor's autosave refuses an empty sequence, so a stray select-all and
    delete cannot wipe a construct."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if request.form.get("confirm") != "1":
            flash(gettext("Confirm clearing the sequence first."), "error")
            return redirect(plasmid_page_url(row_id))
        p.full_sequence = ""
        p.features_json = "[]"
        p.sequence_format = ""
        p.sequence_uploaded_at = None
        stamp_updated(p)
        db_session.commit()
        flash(gettext("Cleared the sequence of plasmid #%(id)s. Its audit history keeps the old one.", id=p.plasmid_id),
              "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/move", methods=["POST"])
@login_required
def plasmid_move_in_box(row_id: int):
    """The box grid's drag and drop (static/rack-grid.js): `box_id` with a
    0-based `box_row` / `box_col`. An occupied cell swaps, when you may
    move both plasmids; a blank box_id and cell (the Unplaced tray) keeps
    the plasmid in its box without a cell. Older callers send a box name
    as `storage_box` instead (blank: out of any box)."""
    form = request.form
    row_raw = (form.get("box_row") or "").strip()
    col_raw = (form.get("box_col") or "").strip()
    new_row, new_col = _plasmid_int(row_raw), _plasmid_int(col_raw)
    if (row_raw or col_raw) and (new_row is None or new_col is None):
        return jsonify({"ok": False, "error": gettext("A box position needs both a row and a column.")}), 400

    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
        if not access.can_edit(p):
            return jsonify({"ok": False, "error": _plasmid_denied(p)}), 403
        current = _box_of(db_session, p)
        if "box_id" in form and not (form.get("box_id") or "").strip():
            box, problem = current, None
            new_row = new_col = None
        else:
            _given, box, problem = pbox.box_from_form(db_session, form, g.user.username, current)
        if problem:
            db_session.rollback()
            return jsonify({"ok": False, "error": problem}), 404
        problem, holder = pbox.place(db_session, p, box, new_row, new_col, swap=True)
        if problem:
            db_session.rollback()
            return jsonify({"ok": False, "error": problem}), 409
        stamp_updated(p)
        if holder is not None:
            stamp_updated(holder)
        db_session.commit()
        # Where each plasmid that moved now sits, so the page can update the
        # grid and the sheet rows (and their _was guards) without a reload.
        moved = []
        for q in (p, holder):
            if q is None:
                continue
            qbox = _box_of(db_session, q)
            moved.append({"id": q.id, "box_id": q.box_id_fk or "", "storage_box": qbox.name if qbox else "",
                          "box_row": q.box_row if q.box_row is not None else -1,
                          "box_col": q.box_col if q.box_col is not None else -1,
                          "position": pbox.label(q, qbox), "stored": pbox.is_stored(q)})
        return jsonify({"ok": True, "moved": moved})


# What the plasmid editor annotates, as stored in features_json. Each entry
# carries its "kind"; an entry without one is a feature (how every stored
# annotation looked before primers, translations and parts were kept).
ANNOTATION_KINDS = {"features": "feature", "primers": "primer", "translations": "translation", "parts": "part"}


def _annotation_list(raw) -> list:
    """The editor sends each annotation group as an object keyed by id;
    older callers and GenBank uploads send a list. Either way, a list."""
    if isinstance(raw, dict):
        return list(raw.values())
    return raw if isinstance(raw, list) else []


def _clean_features(raw_features, length: int, kind: str = "feature") -> list[dict]:
    """Stored annotations from the editor's, dropping any outside the
    sequence. An annotation crossing the origin keeps start > end; a joined
    feature keeps its pieces in `locations`."""
    translated = []
    for f in _annotation_list(raw_features):
        if not isinstance(f, dict):
            continue
        try:
            start = int(f.get("start", 0) or 0)
            end = int(f.get("end", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not (0 <= start < length and 0 <= end < length):
            continue
        if "forward" in f:
            direction = 1 if f.get("forward") else -1
        else:
            try:
                direction = 1 if int(f.get("strand", f.get("direction", 1)) or 0) >= 0 else -1
            except (TypeError, ValueError):
                direction = 1
        notes = f.get("notes")
        if isinstance(notes, dict):  # the editor's {key: [values]}
            notes = {str(k)[:60]: [str(v)[:400] for v in (vals if isinstance(vals, list) else [vals])][:20]
                     for k, vals in list(notes.items())[:30]}
        elif isinstance(notes, str):
            notes = notes[:400]
        else:
            notes = ""
        item_kind = f.get("kind") if f.get("kind") in ANNOTATION_KINDS.values() else kind
        # The editor lists a translation for every CDS feature (and ORF) as
        # well; those follow their feature, so only translations a person
        # made are kept.
        if item_kind == "translation" and f.get("translationType") not in (None, "", "User Created"):
            continue
        entry = {
            "name": str(f.get("name") or "")[:120],
            "type": str(f.get("type") or ("primer_bind" if item_kind == "primer" else "misc_feature"))[:40],
            "start": start,
            "end": end,
            "direction": direction,
            "color": str(f.get("color") or "#cbd5e1")[:20],
            "notes": notes,
        }
        if item_kind != "feature":
            entry["kind"] = item_kind
        locations = []
        for loc in f.get("locations") or []:
            try:
                ls, le = int(loc.get("start")), int(loc.get("end"))
            except (AttributeError, TypeError, ValueError):
                continue
            if 0 <= ls < length and 0 <= le < length:
                locations.append({"start": ls, "end": le})
        if len(locations) > 1:
            entry["locations"] = locations
        if item_kind == "primer" and isinstance(f.get("bases"), str) and f["bases"].strip():
            entry["bases"] = re.sub(r"[^A-Za-z]", "", f["bases"])[:500]
        if item_kind == "translation" and isinstance(f.get("translationType"), str):
            entry["translationType"] = f["translationType"][:40]
        translated.append(entry)
    return translated


def _clean_annotations(sequence_data: dict, length: int) -> list[dict]:
    """Every annotation group the editor keeps, cleaned, in one list."""
    out = []
    for group, kind in ANNOTATION_KINDS.items():
        out.extend(_clean_features(sequence_data.get(group), length, kind))
    return out


def _editor_annotations(stored: list) -> dict[str, list]:
    """Stored annotations in the editor's shape, by group: forward and
    strand for direction, and a stable id so the editor can diff renders."""
    groups = {group: [] for group in ANNOTATION_KINDS}
    for i, f in enumerate(stored if isinstance(stored, list) else []):
        if not isinstance(f, dict):
            continue
        kind = f.get("kind") or "feature"
        group = next((g for g, k in ANNOTATION_KINDS.items() if k == kind), "features")
        forward = int(f.get("direction", 1) or 1) >= 0
        item = {
            "id": f.get("id") or f"{kind}-{i}",
            "name": f.get("name") or "",
            "type": f.get("type") or "misc_feature",
            "start": int(f.get("start", 0) or 0),
            "end": int(f.get("end", 0) or 0),
            "forward": forward,
            "strand": 1 if forward else -1,
            "color": f.get("color") or "#cbd5e1",
            "notes": f.get("notes") or "",
        }
        for extra in ("locations", "bases", "translationType"):
            if f.get(extra):
                item[extra] = f[extra]
        groups[group].append(item)
    return groups


def _features_only(stored: list) -> list:
    return [f for f in stored if isinstance(f, dict) and (f.get("kind") or "feature") == "feature"]


@app.route("/plasmids/<int:row_id>/edit-sequence", methods=["POST"])
@login_required
def plasmid_edit_sequence(row_id: int):
    """Save a hand-edited raw sequence. Strips whitespace/digits and FASTA
    headers; keeps the IUPAC letters. Features that no longer fit in the
    new length are dropped. An empty result is refused: clearing a
    sequence is its own confirmed action."""
    from .sequence_parser import clean_bases

    raw = request.form.get("sequence_text", "")
    # Drop FASTA header lines before flattening.
    lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith(">")]
    cleaned = clean_bases("\n".join(lines))

    is_circular_raw = (request.form.get("is_circular") or "").lower()
    new_circular = is_circular_raw in ("1", "true", "on", "yes")

    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if not cleaned:
            flash(gettext("That leaves no sequence, so nothing was saved. Use Clear sequence to empty it."), "error")
            return redirect(plasmid_page_url(row_id))
        p.full_sequence = cleaned
        p.is_circular = new_circular
        try:
            features = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            features = []
        new_features = _clean_features(features, len(cleaned))
        if len(new_features) != len(features):
            flash(
                gettext("Dropped %(n)s feature(s) that fell beyond the new sequence length.",
                        n=len(features) - len(new_features)),
                "warning",
            )
        p.features_json = json.dumps(new_features)
        if not p.sequence_format:
            p.sequence_format = "manual"
        p.sequence_uploaded_at = datetime.utcnow()
        stamp_updated(p)
        db_session.commit()
    flash(gettext("Saved sequence · %(bp)s bp.", bp=len(cleaned)), "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/sequence-save", methods=["POST"])
@login_required
def plasmid_sequence_save_json(row_id: int):
    """JSON endpoint hit by the Open Vector Editor onSave callback. The body
    contains OVE's `sequenceData` shape: {sequence, circular, features, name}.
    OVE features look like {id, name, start, end, type, color, forward,
    strand, notes, locations[]}. We translate to our schema and persist.

    An empty or malformed save is refused (400) rather than stored: the
    editor autosaves after every edit, and a blank body used to wipe the
    construct. Clearing a sequence is /clear-sequence."""
    from .sequence_parser import looks_like_bases

    payload = request.get_json(silent=True)
    sd = payload.get("sequenceData", payload) if isinstance(payload, dict) else None
    raw_sequence = sd.get("sequence") if isinstance(sd, dict) else None
    if not isinstance(raw_sequence, str) or not raw_sequence.strip():
        return jsonify({"ok": False, "error": gettext("Refusing to save an empty sequence. Use Clear sequence to empty it.")}), 400
    if not looks_like_bases(raw_sequence):
        return jsonify({"ok": False, "error": gettext("The sequence has letters that are not IUPAC nucleotide codes.")}), 400
    sequence = re.sub(r"\s+", "", raw_sequence).upper()
    translated = _clean_annotations(sd, len(sequence))

    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
        if not access.can_edit(p):
            return jsonify({"ok": False, "error": _plasmid_denied(p)}), 403
        p.full_sequence = sequence
        p.is_circular = bool(sd.get("circular"))
        p.features_json = json.dumps(translated)
        if not p.sequence_format:
            p.sequence_format = "ove"
        # File > Rename Sequence in the editor renames the plasmid.
        if isinstance(sd.get("name"), str) and sd["name"].strip() and sd["name"].strip() != (p.name or ""):
            p.name = sd["name"].strip()[:200]
        p.sequence_uploaded_at = datetime.utcnow()
        stamp_updated(p)
        db_session.commit()
    counts = {group: sum(1 for f in translated if (f.get("kind") or "feature") == kind)
              for group, kind in ANNOTATION_KINDS.items()}
    return jsonify({"ok": True, "length": len(sequence), "features": counts["features"], "counts": counts})


@app.route("/plasmids/<int:row_id>/sequence.json")
@login_required
def plasmid_sequence_json(row_id: int):
    """Return the plasmid's sequence + features as JSON for the editor."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False}), 404
        try:
            features = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            features = []
        groups = _editor_annotations(features)
        return jsonify({
            "ok": True,
            "name": p.name or f"Plasmid #{p.plasmid_id}",
            "sequence": p.full_sequence or "",
            "is_circular": bool(p.is_circular),
            "circular": bool(p.is_circular),  # OVE uses this key
            "features": groups["features"],
            "primers": groups["primers"],
            "translations": groups["translations"],
            "parts": groups["parts"],
            "locked": not access.can_edit(p),
        })


@app.route("/utilities")
@login_required
def utilities():
    """The bench calculators (static/bench-calcs.js); the lab's own list of
    molecular weights comes first in their chemical picker."""
    with SessionLocal() as db_session:
        chemicals = [{"name": c.name, "mw": c.molecular_weight, "notes": c.notes}
                     for c in db_session.scalars(select(ChemicalReference).order_by(ChemicalReference.name))]
    return render_template("utilities.html", chemicals=chemicals)


# ---------------------------------------------------------------------------
# ZEBRAFISH MODULE — parallel to the mouse colony.
# ---------------------------------------------------------------------------

ZEBRAFISH_VIEWS = ("tanks", "fish", "clutches", "experiments", "lines", "water")
# Older view names, still in bookmarks and reminders, and where they live now.
ZEBRAFISH_VIEW_ALIASES = {"racks": "grid", "genotyping": "geno", "sac": "table"}

# Statuses that mean the fish are gone. Setting one stamps the sac date and
# writes a sac-log entry; going back to a living status undoes both, the
# way a mouse's date of death follows its status.
FISH_DEAD_STATUSES = {"sac", "dead"}
# How a sac-log entry written by a status change begins; reviving the row
# removes that entry, not ones logged by hand.
FISH_STATUS_SAC_REASON = "Status set to "
# Tank purposes the whole lab works out of, like the mouse breeder cages.
FISH_SHARED_PURPOSES = {"breeding", "shared"}


def _fish_age_label(dof):
    """dpf (≤30 d) → wpf (≤12 w) → mpf (≤24 mo) → ypf. dof = date_of_fertilization."""
    if not dof:
        return ""
    days = (date.today() - dof).days
    if days < 0:
        return ""
    if days <= 30:
        return f"{days}dpf"
    weeks = days // 7
    if weeks <= 12:
        return f"{weeks}wpf"
    months = days // 30
    if months <= 24:
        return f"{months}mpf"
    years = days // 365
    return f"{years}ypf"


# ---- Rules shared by every zebrafish write ---------------------------------


def zf_tank_shared(tank) -> bool:
    return (getattr(tank, "purpose", "") or "").strip().lower() in FISH_SHARED_PURPOSES


def zf_can_edit(record) -> bool:
    """access.can_edit for fish records. A tank is editable when it is
    yours, unowned or a shared breeding tank; a fish row follows its tank.
    A line is its owner's (lines from before owners stay open). Racks carry
    no owner and stay open to the lab; deleting a water system is
    access.can_edit_rack (its creator or an admin)."""
    if isinstance(record, FishRecord):
        record = record.tank or record
    if isinstance(record, TankRecord):
        # A breeding tank shared with a project group is its members'.
        return access.can_edit(record, shared=zf_tank_shared(record)
                               and project_groups.record_shared_with(record, shared=True))
    return access.can_edit(record)


def zf_denied(record) -> str:
    """Why a record is read only, in words worth showing."""
    if isinstance(record, FishRecord) and record.tank is not None:
        record = record.tank
    owner = (getattr(record, "owner", "") or "").strip() or gettext("someone else")
    if isinstance(record, TankRecord) and zf_tank_shared(record) and record.share_group_id:
        return gettext("Tank %(tank)s is shared with %(group)s, which you are not in. Ask %(owner)s, or an admin, to make the change.", tank=record.tank_id,
                       group=project_groups.name_of(record.share_group_id), owner=owner)
    if isinstance(record, TankRecord):
        return gettext("Tank %(tank)s and its fish belong to %(owner)s. Ask them, or an admin, to make the change.",
                       tank=record.tank_id, owner=owner)
    if isinstance(record, ClutchRecord):
        return gettext("Clutch %(clutch)s belongs to %(owner)s. Ask them, or an admin, to make the change.",
                       clutch=record.clutch_id, owner=owner)
    if isinstance(record, FishLine):
        return gettext("Line %(line)s belongs to %(owner)s. Ask them, or an admin, to make the change.",
                       line=record.name, owner=owner)
    return gettext("That record belongs to %(owner)s. Ask them, or an admin, to make the change.", owner=owner)


def fish_alive(fish) -> bool:
    return (fish.status or "alive").strip().lower() not in FISH_DEAD_STATUSES and fish.sac_date is None


def zf_reply(view: str, error: str = "", row=None, status: int = 409, **args):
    """Answer a zebrafish write. Autosave (sheet.js, the rack grid) gets
    JSON; a form post goes back where it came from, or to the zebrafish
    view it belongs to — never the colony."""
    if request.headers.get("X-Autosave") == "1":
        if error:
            return jsonify({"ok": False, "error": error}), status
        return jsonify({"ok": True, **({"row": row} if row is not None else {})})
    if error:
        flash(error, "error")
    referrer = request.referrer or ""
    if referrer.startswith(request.host_url):
        return redirect(referrer)
    return redirect(url_for("zebrafish", view=view, **args))


class ZfInputError(ValueError):
    """A typed value that cannot be saved; the message says why."""


def zf_int(form, name: str, label: str, default=None, lo=None, hi=None):
    raw = (form.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ZfInputError(gettext("%(field)s must be a whole number, not “%(value)s”.",
                                   field=pgettext("zebrafish", label), value=raw)) from None
    if lo is not None and value < lo:
        raise ZfInputError(gettext("%(field)s can’t be less than %(n)s.", field=pgettext("zebrafish", label), n=lo))
    if hi is not None and value > hi:
        raise ZfInputError(gettext("%(field)s can’t be more than %(n)s.", field=pgettext("zebrafish", label), n=hi))
    return value


def zf_float(form, name: str, label: str):
    raw = (form.get(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ZfInputError(gettext("%(field)s must be a number, not “%(value)s”.", field=pgettext("zebrafish", label),
                                   value=raw)) from None


def zf_date(form, name: str, label: str):
    raw = (form.get(name) or "").strip()
    if not raw:
        return None
    value = parse_date(raw)
    if value is None:
        raise ZfInputError(gettext("%(field)s: “%(value)s” is not a date.", field=pgettext("zebrafish", label),
                                   value=raw))
    return value


def zf_ref(s, model, raw, label: str):
    """The id of an existing row; None for blank. A missing row is an
    error rather than a dangling id."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    row = s.get(model, int(raw)) if raw.isdigit() else None
    if row is None:
        raise ZfInputError(gettext("That %(what)s no longer exists. Reload the page.",
                                   what=pgettext("zebrafish", label)))
    return row.id


def zf_choice(raw, allowed, label: str, current=None) -> str:
    """One of the allowed values (any case), or the record's own existing
    value unchanged, so a custom status from before is not lost."""
    raw = (raw or "").strip()
    for option in allowed:
        if raw.lower() == option.lower():
            return option
    if current is not None and raw == (current or ""):
        return raw
    raise ZfInputError(gettext("%(field)s must be one of: %(options)s.", field=pgettext("zebrafish", label),
                               options=", ".join(allowed)))


def zf_next_code(s, column, prefix: str, width: int = 0) -> str:
    """The next code after the highest one in use, so deleting a record
    never hands its ID to the next one (a printed tank card still says it)."""
    pattern = re.compile(re.escape(prefix) + r"(\d+)$")
    numbers = [int(m.group(1)) for code in s.scalars(select(column).where(column.like(f"{prefix}%")))
               if (m := pattern.match(code or ""))]
    return f"{prefix}{max(numbers, default=0) + 1:0{width}d}"


def zf_unique(s, column, raw, noun: str, own_id=None, max_len: int = 80) -> str:
    """A required, unique identifier (tank ID, clutch ID, line name)."""
    value = (raw or "").strip()
    if not value:
        raise ZfInputError(gettext("A line needs a name.") if noun == "line" else
                           gettext("A tank needs an ID.") if noun == "tank" else
                           gettext("A clutch needs an ID.") if noun == "clutch" else f"A {noun} needs an ID.")
    if len(value) > max_len:
        raise ZfInputError(gettext("Keep the line name under %(max)s characters.", max=max_len) if noun == "line" else
                           gettext("Keep the tank ID under %(max)s characters.", max=max_len) if noun == "tank" else
                           gettext("Keep the clutch ID under %(max)s characters.", max=max_len) if noun == "clutch"
                           else f"Keep the {noun} ID under {max_len} characters.")
    model = column.class_
    clash = s.scalar(select(model.id).where(column == value, model.id != (own_id or 0)))
    if clash is not None:
        raise ZfInputError(gettext("%(value)s is already used by another line.", value=value) if noun == "line" else
                           gettext("%(value)s is already used by another tank.", value=value) if noun == "tank" else
                           gettext("%(value)s is already used by another clutch.", value=value) if noun == "clutch"
                           else f"{value} is already used by another {noun}.")
    return value


def zf_selected(s, model, form) -> list:
    ids = [int(v) for v in form.getlist("selected_ids") if v.isdigit()]
    return list(s.scalars(select(model).where(model.id.in_(ids)))) if ids else []


def _zf_count(n: int, noun: str, plural: str) -> str:
    """"3 tanks", in the page's language."""
    if noun == "tank":
        return ngettext("%(num)s tank", "%(num)s tanks", n)
    if noun == "fish row":
        return ngettext("%(num)s fish row", "%(num)s fish rows", n)
    if noun == "line":
        return ngettext("%(num)s line", "%(num)s lines", n)
    if noun == "clutch":
        return ngettext("%(num)s clutch", "%(num)s clutches", n)
    if noun == "child line":
        return ngettext("%(num)s child line", "%(num)s child lines", n)
    return f"{n} {noun if n == 1 else plural}"


def zf_report(changed: int, skipped: int, what: str, noun: str, plural: str, blocked: list | None = None) -> None:
    """Flash a batch result: applied to what you may edit, the rest counted."""
    blocked = blocked or []
    what = gettext(what)
    if changed and skipped:
        flash(gettext("%(what)s: %(things)s. %(skipped)s skipped — not yours to edit.", what=what,
                      things=_zf_count(changed, noun, plural), skipped=skipped), "success")
    elif changed:
        flash(gettext("%(what)s: %(things)s.", what=what, things=_zf_count(changed, noun, plural)), "success")
    elif skipped:
        flash(gettext("Nothing changed — %(things)s are not yours to edit.", things=_zf_count(skipped, noun, plural)),
              "error")
    elif not blocked:
        flash(gettext("Nothing was selected."), "error")
    for reason in blocked[:5]:
        flash(reason, "error")


# ---- Page ------------------------------------------------------------------


def _zebrafish_context(active_view: str):
    me = access.username()
    today = date.today()
    with SessionLocal() as s:
        systems = s.scalars(select(WaterSystem).order_by(WaterSystem.name)).all()
        racks = s.scalars(select(FishRack).order_by(FishRack.name)).all()
        lines = s.scalars(select(FishLine).order_by(FishLine.name)).all()
        # Eager-load what the template reads after the session closes.
        tanks = s.scalars(
            select(TankRecord)
            .options(joinedload(TankRecord.line),
                     joinedload(TankRecord.rack),
                     selectinload(TankRecord.fish))
            .order_by(TankRecord.tank_id)
        ).unique().all()
        fish = s.scalars(
            select(FishRecord).options(joinedload(FishRecord.line), joinedload(FishRecord.tank))
            .order_by(FishRecord.id.desc()).limit(500)
        ).all()
        clutches = s.scalars(
            select(ClutchRecord).options(joinedload(ClutchRecord.line))
            .order_by(ClutchRecord.date_of_fertilization.desc()).limit(200)
        ).all()
        tank_by_id = {t.id: t for t in tanks}

        # What refers to what, for delete confirmations that say what goes.
        parent_refs, clutch_line_refs = {}, {}
        for fa, mo, ln in s.execute(select(ClutchRecord.father_tank_id, ClutchRecord.mother_tank_id,
                                           ClutchRecord.line_id_fk)):
            for tid in {fa, mo} - {None}:
                parent_refs[tid] = parent_refs.get(tid, 0) + 1
            if ln:
                clutch_line_refs[ln] = clutch_line_refs.get(ln, 0) + 1
        sac_refs = dict(s.execute(select(FishSacLog.tank_id_fk, func.count(FishSacLog.id))
                                  .group_by(FishSacLog.tank_id_fk)).all())
        fish_line_refs = dict(s.execute(select(FishRecord.line_id_fk, func.count(FishRecord.id))
                                        .group_by(FishRecord.line_id_fk)).all())
        clutch_fish_refs = dict(s.execute(select(FishRecord.clutch_id_fk, func.count(FishRecord.id))
                                          .group_by(FishRecord.clutch_id_fk)).all())
        live_by_line = {}
        for fl, tl, n in s.execute(select(FishRecord.line_id_fk, TankRecord.line_id_fk, FishRecord.count)
                                   .join(TankRecord, FishRecord.tank_id_fk == TankRecord.id)
                                   .where(FishRecord.sac_date.is_(None))
                                   .where(func.lower(func.coalesce(FishRecord.status, "alive")).not_in(sorted(FISH_DEAD_STATUSES)))):
            key = fl or tl
            if key:
                live_by_line[key] = live_by_line.get(key, 0) + (n or 0)
        mating_refs = {}
        for t in tanks:
            for tid in {t.mating_father_tank_id, t.mating_mother_tank_id} - {None}:
                mating_refs[tid] = mating_refs.get(tid, 0) + 1

        # ---- Tanks ----
        tank_rows = []
        for t in tanks:
            total = sum(f.count or 0 for f in t.fish if fish_alive(f))
            editable = zf_can_edit(t)
            position = fish_position_label(t)
            refs = []
            if parent_refs.get(t.id, 0):
                refs.append(ngettext("%(num)s clutch will no longer name it as a parent",
                                     "%(num)s clutches will no longer name it as a parent", parent_refs[t.id]))
            if mating_refs.get(t.id, 0):
                refs.append(ngettext("%(num)s mating tank will lose it as a parent",
                                     "%(num)s mating tanks will lose it as a parent", mating_refs[t.id]))
            if sac_refs.get(t.id, 0):
                refs.append(ngettext("%(num)s sac-log entry will lose its tank",
                                     "%(num)s sac-log entries will lose its tank", sac_refs[t.id]))
            affected = "; ".join(refs)
            # A mating tank still out: where each live group goes back to.
            returnable = zf_is_mating(t) and t.active
            plan = [{"fish": f, "home": home if home in tank_by_id else None,
                     "home_code": tank_by_id[home].tank_id if home in tank_by_id else ""}
                    for f in t.fish if fish_alive(f) for home in [zf_mating_home(f, t)]] if returnable else []
            tank_rows.append({
                "row": t, "total_fish": total, "position": position, "editable": editable,
                "mine": t.owner == me, "shared": zf_tank_shared(t), "fish_rows": len(t.fish),
                "share_group": project_groups.name_of(t.share_group_id) if zf_tank_shared(t) else "",
                "returnable": returnable, "return_plan": plan,
                "return_ready": all(p["home"] for p in plan),
                "return_confirm": zf_return_confirm(t, [(p["fish"].count or 0, p["home_code"]) for p in plan]),
                "confirm": (gettext("Delete tank %(tank)s? %(affected)s.", tank=t.tank_id,
                                    affected=affected[0].upper() + affected[1:]) if affected
                            else gettext("Delete tank %(tank)s?", tank=t.tank_id)),
                "payload": {
                    "id": t.id, "_label": t.tank_id, "_locked": not editable, "tank_id": t.tank_id,
                    "purpose": t.purpose, "line_id_fk": t.line_id_fk or "", "owner": t.owner,
                    "rack_id_fk": t.rack_id_fk or "", "position": position, "card_id": t.card_id,
                    "notes": t.notes, "active": "1" if t.active else "0",
                    "share_group": project_groups.value_of(True, t.share_group_id),
                    "rack_id_fk_was": t.rack_id_fk or "", "position_was": position, "owner_was": t.owner},
            })

        # ---- Fish ----
        fish_rows = []
        for f in fish:
            tank = f.tank
            editable = zf_can_edit(f)
            days = (today - f.date_of_fertilization).days if f.date_of_fertilization else None
            fish_rows.append({
                "row": f, "tank": tank, "alive": fish_alive(f), "editable": editable,
                "mine": bool(tank and tank.owner == me), "owner": tank.owner if tank else "",
                "age": _fish_age_label(f.date_of_fertilization), "age_days": days if days is not None and days >= 0 else "",
                "payload": {
                    "id": f.id, "_label": f.individual_id or f"#{f.id}", "_locked": not editable,
                    "tank_id_fk": f.tank_id_fk, "line_id_fk": f.line_id_fk or "", "count": f.count,
                    "individual_id": f.individual_id, "sex": f.sex, "status": f.status,
                    "date_of_fertilization": f.date_of_fertilization.isoformat() if f.date_of_fertilization else "",
                    "sac_date": f.sac_date.isoformat() if f.sac_date else "",
                    "genotype": f.genotype, "notes": f.notes},
            })

        # ---- Clutches: derived dates + age ----
        clutch_rows = []
        for c in clutches:
            dof = c.date_of_fertilization
            editable = zf_can_edit(c)
            n_fish = clutch_fish_refs.get(c.id, 0)
            clutch_rows.append({
                "row": c,
                "age": _fish_age_label(dof),
                "age_days": (today - dof).days if dof else "",
                "tank_up": dof + timedelta(days=5) if dof else None,
                "fin_clip": dof + timedelta(days=30) if dof else None,
                "adult": dof + timedelta(days=90) if dof else None,
                "father": tank_by_id[c.father_tank_id].tank_id if c.father_tank_id in tank_by_id else "",
                "mother": tank_by_id[c.mother_tank_id].tank_id if c.mother_tank_id in tank_by_id else "",
                "editable": editable, "mine": c.owner == me,
                "confirm": (ngettext("Delete clutch %(clutch)s? %(num)s fish row will no longer name it.",
                                     "Delete clutch %(clutch)s? %(num)s fish rows will no longer name it.", n_fish,
                                     clutch=c.clutch_id) if n_fish
                            else gettext("Delete clutch %(clutch)s?", clutch=c.clutch_id)),
                "payload": {
                    "id": c.id, "_label": c.clutch_id, "_locked": not editable, "clutch_id": c.clutch_id,
                    "date_of_fertilization": dof.isoformat() if dof else "", "line_id_fk": c.line_id_fk or "",
                    "father_tank_id": c.father_tank_id or "", "mother_tank_id": c.mother_tank_id or "",
                    "embryo_count": c.embryo_count, "larvae_count": c.larvae_count, "adults_count": c.adults_count,
                    "owner": c.owner, "notes": c.notes},
            })

        # ---- Lines ----
        line_rows = []
        children = {}
        for ln in lines:
            if ln.parent_line_id_fk:
                children[ln.parent_line_id_fk] = children.get(ln.parent_line_id_fk, 0) + 1
        line_by_id = {ln.id: ln for ln in lines}
        for ln in lines:
            n_tanks = sum(1 for t in tanks if t.line_id_fk == ln.id)
            uses = [(n_tanks, "tank", "tanks"), (fish_line_refs.get(ln.id, 0), "fish row", "fish rows"),
                    (clutch_line_refs.get(ln.id, 0), "clutch", "clutches"),
                    (children.get(ln.id, 0), "child line", "child lines")]
            used = ", ".join(_zf_count(n, one, many) for n, one, many in uses if n)
            live = live_by_line.get(ln.id, 0)
            line_editable = zf_can_edit(ln)
            line_rows.append({
                "row": ln, "tanks": n_tanks, "live": live, "active": live > 0, "used": used,
                "editable": line_editable, "mine": ln.owner == me,
                "parent": line_by_id[ln.parent_line_id_fk].name if ln.parent_line_id_fk in line_by_id else "",
                "payload": {
                    "id": ln.id, "_label": ln.name, "_locked": not line_editable, "name": ln.name,
                    "owner": ln.owner, "zfin_name": ln.zfin_name,
                    "background": ln.background, "transgene_summary": ln.transgene_summary,
                    "allele": ln.allele, "iacuc_protocol": ln.iacuc_protocol, "founder_info": ln.founder_info,
                    "parent_line_id_fk": ln.parent_line_id_fk or "", "notes": ln.notes},
            })

        # Per-system most recent water log.
        latest_logs = {}
        for sys_ in systems:
            latest_logs[sys_.id] = s.scalar(
                select(WaterLog).where(WaterLog.system_id_fk == sys_.id)
                .order_by(WaterLog.recorded_at.desc()).limit(1))
        # What a system delete would say, or why it is refused.
        system_info = {}
        for sys_ in systems:
            n_logs = s.scalar(select(func.count(WaterLog.id)).where(WaterLog.system_id_fk == sys_.id)) or 0
            system_info[sys_.id] = {
                "can_delete": access.can_edit_rack(sys_),
                "in_use": zf_system_uses(s, sys_),
                "confirm": (ngettext("Delete water system %(system)s? Its %(num)s reading is kept, listed under deleted systems.",
                                     "Delete water system %(system)s? Its %(num)s readings are kept, listed under deleted systems.", n_logs, system=sys_.name) if n_logs
                            else gettext("Delete water system %(system)s?", system=sys_.name)),
            }
        orphan_logs = s.scalars(select(WaterLog).where(WaterLog.system_id_fk.is_(None))
                                .order_by(WaterLog.recorded_at.desc()).limit(50)).all()

        geno_queue = [tr for tr in tank_rows if tr["row"].needs_genotyping]
        sac_log = s.scalars(select(FishSacLog).order_by(FishSacLog.recorded_at.desc()).limit(100)).all()
        next_tank_id = zf_next_code(s, TankRecord.tank_id, "T", 3)
        next_clutch_id = zf_next_code(s, ClutchRecord.clutch_id, f"C{today.strftime('%y%m%d')}-")
        usernames = current_lab_usernames(s)
        census = {
            "tanks": sum(1 for t in tanks if t.active),
            "fish": sum(f.count or 0 for t in tanks for f in t.fish if fish_alive(f)),
            "clutches": s.scalar(select(func.count(ClutchRecord.id))) or 0,
            "lines": len(lines),
            "experiments": experiment_pages.tab_count(s, "zebrafish"),
        }
        experiments_tab = None
        if active_view == "experiments":
            place = experiment_pages.place_for(s, "zebrafish")
            experiments_tab = experiment_pages.tab_context(s, place) if place else None

    # Rack grid: tanks store 0-based rows/columns; the grid payload is
    # 1-based and the page tells rack-grid.js to convert back.
    fish_racks = {
        "racks": [{"id": r.id, "name": r.name, "rows": r.rows, "cols": r.cols,
                   "naming": positions.scheme(r.naming),
                   "edit": {"data-record-payload": json.dumps({
                       "id": r.id, "_label": r.name, "_locked": not access.can_edit_rack(r),
                       "name": r.name, "rows": r.rows,
                       "cols": r.cols, "system_id_fk": r.system_id_fk or "",
                       **{f"naming_{k}": v for k, v in positions.scheme(r.naming).items()}})}} for r in racks],
        "items": [{
            "id": tr["row"].id, "label": tr["row"].tank_id,
            "sub": tr["row"].line.name if tr["row"].line else tr["row"].purpose,
            "badge": str(tr["total_fish"]) if tr["total_fish"] else "",
            "tone": tr["row"].purpose if tr["row"].active else "inactive",
            "flag": tr["row"].needs_genotyping,
            "rack": tr["row"].rack_id_fk,
            "row": tr["row"].row + 1 if tr["row"].row is not None else None,
            "col": tr["row"].col + 1 if tr["row"].col is not None else None,
            "title": " · ".join(filter(None, [tr["row"].tank_id, tr["row"].line.name if tr["row"].line else "",
                                             tr["row"].purpose, f'{tr["total_fish"]} fish' if tr["total_fish"] else "",
                                             "needs genotyping" if tr["row"].needs_genotyping else ""])),
            "search": " ".join(filter(None, [tr["row"].tank_id, tr["row"].purpose, tr["row"].owner, tr["row"].card_id,
                                             tr["row"].line.name if tr["row"].line else ""])).lower(),
            "edit": {"data-record-edit": "tank-dialog", "data-record-payload": json.dumps(tr["payload"])},
        } for tr in tank_rows],
        "create": {"attrs": {"data-record-edit": "tank-dialog"},
                   "payload": {"tank_id": next_tank_id, "purpose": "stock", "owner": me, "active": "1"},
                   "rack_field": "rack_id_fk", "text_field": "position"},
    }

    return {
        "active_view": active_view,
        "zebrafish_views": ZEBRAFISH_VIEWS,
        "fish_racks": fish_racks,
        "tank_purpose_options": TANK_PURPOSE_OPTIONS,
        "fish_sex_options": FISH_SEX_OPTIONS,
        "fish_status_options": FISH_STATUS_OPTIONS,
        "fish_dead_statuses": sorted(FISH_DEAD_STATUSES),
        "systems": systems,
        "racks": racks,
        "lines": lines,
        "line_rows": line_rows,
        "tanks": tanks,
        "tank_rows": tank_rows,
        "fish_rows": fish_rows,
        "clutch_rows": clutch_rows,
        "latest_logs": latest_logs,
        "system_info": system_info,
        "orphan_logs": orphan_logs,
        "geno_queue": geno_queue,
        "sac_log": sac_log,
        "census": census,
        "experiments_tab": experiments_tab,
        "usernames": usernames,
        "me": me,
        "next_tank_id": next_tank_id,
        "next_clutch_id": next_clutch_id,
        "now_date": today,
        "today_iso": today.isoformat(),
    }


@app.route("/zebrafish")
@login_required
def zebrafish():
    view = request.args.get("view", "tanks")
    if view in ZEBRAFISH_VIEW_ALIASES:
        return redirect(url_for("zebrafish", view="tanks", mode=ZEBRAFISH_VIEW_ALIASES[view]))
    if view not in ZEBRAFISH_VIEWS:
        view = "tanks"
    return render_template("zebrafish.html", **_zebrafish_context(view))


def zebrafish_home_summary() -> dict:
    """The home page's zebrafish card: mating tanks due back, and tanks
    waiting to be genotyped."""
    horizon = date.today() + timedelta(days=1)
    with SessionLocal() as s:
        mating = s.scalars(
            select(TankRecord).options(selectinload(TankRecord.fish))
            .where(TankRecord.active.is_(True), TankRecord.purpose == "mating",
                   TankRecord.mating_return_at.is_not(None),
                   TankRecord.mating_return_at <= horizon)
            .order_by(TankRecord.mating_return_at).limit(8)).all()
        codes = dict(s.execute(select(TankRecord.id, TankRecord.tank_id)).all())
        geno = s.scalars(
            select(TankRecord).options(joinedload(TankRecord.line))
            .where(TankRecord.needs_genotyping.is_(True)).order_by(TankRecord.tank_id)).all()
        return {
            "any": bool(s.scalar(select(func.count(TankRecord.id)))),
            "mating": [{"id": t.id, "tank_id": t.tank_id, "due": t.mating_return_at, "owner": t.owner,
                        "overdue": t.mating_return_at < date.today(), "editable": zf_can_edit(t),
                        # Returned straight from here when every group has a home tank;
                        # otherwise the tanks page asks where they go.
                        "ready": all(zf_mating_home(f, t) in codes for f in live),
                        "confirm": zf_return_confirm(t, [(f.count or 0, codes.get(zf_mating_home(f, t), ""))
                                                         for f in live])}
                       for t in mating for live in [[f for f in t.fish if fish_alive(f)]]],
            "geno": [{"id": t.id, "tank_id": t.tank_id, "line": t.line.name if t.line else "", "owner": t.owner}
                     for t in geno[:6]],
            "geno_total": len(geno),
        }


# ---- Tanks -----------------------------------------------------------------


def fish_position_label(tank) -> str:
    """A tank's position under its rack's naming scheme ("C3"). Tanks store
    0-based rows and columns."""
    rack = tank.rack
    if rack is None or tank.row is None or tank.col is None:
        return ""
    return positions.label(tank.row + 1, tank.col + 1, rack.naming, rack.cols)


def zf_free_cell(s, rack, exclude_id=None):
    """The first empty (row, col) of a rack, 0-based, or None when full."""
    taken = {(r, c) for r, c in s.execute(
        select(TankRecord.row, TankRecord.col).where(
            TankRecord.rack_id_fk == rack.id, TankRecord.id != (exclude_id or 0)))}
    for r in range(rack.rows):
        for c in range(rack.cols):
            if (r, c) not in taken:
                return r, c
    return None


def zf_place(tank, rack, row=None, col=None) -> None:
    """Put a tank in a rack cell (0-based), or no rack. The column is set
    directly as well as the relationship: the change history (and so
    batch undo) only sees columns written by hand."""
    tank.rack_id_fk = rack.id if rack is not None else None
    tank.rack = rack
    tank.row, tank.col = (row, col) if rack is not None else (None, None)


def apply_fish_position(s, tank, rack_raw, position_raw) -> str | None:
    """Place a tank from a rack and a typed position; an error message
    instead of a guess. No rack unplaces the tank whatever the position
    says. A rack with a blank position keeps the tank's cell if it is
    already there, otherwise takes the first free one."""
    rack_raw = str(rack_raw or "").strip()
    raw = str(position_raw or "").strip()
    if not rack_raw:
        zf_place(tank, None)
        return None
    rack = s.get(FishRack, int(rack_raw)) if rack_raw.isdigit() else None
    if rack is None:
        return gettext("That rack no longer exists. Reload the page.")
    if not raw:
        if tank.rack_id_fk == rack.id and tank.row is not None:
            return None
        cell = zf_free_cell(s, rack, tank.id)
        if cell is None:
            return gettext("%(rack)s is full. Pick another rack, or make room first.", rack=rack.name)
        zf_place(tank, rack, *cell)
        return None
    cell = positions.parse(raw, rack.naming, rack.rows, rack.cols)
    if cell is None:
        return gettext("“%(position)s” is not a position in %(rack)s (%(first)s–%(last)s).", position=raw,
                       rack=rack.name, first=positions.label(1, 1, rack.naming, rack.cols),
                       last=positions.label(rack.rows, rack.cols, rack.naming, rack.cols))
    row, col = cell[0] - 1, cell[1] - 1
    holder = s.scalar(select(TankRecord).where(
        TankRecord.rack_id_fk == rack.id, TankRecord.row == row, TankRecord.col == col,
        TankRecord.id != (tank.id or 0)))
    if holder is not None:
        return gettext("%(rack)s · %(position)s already holds tank %(tank)s. Drag on the rack grid to swap.",
                       rack=rack.name, position=raw, tank=holder.tank_id)
    zf_place(tank, rack, row, col)
    return None


def zf_tank_state(t) -> dict:
    """What sheet.js refreshes on a tank row after a save."""
    return {"active": bool(t.active), "values": {
        "tank_id": t.tank_id, "purpose": t.purpose, "owner": t.owner,
        "rack_id_fk": str(t.rack_id_fk or ""), "position": fish_position_label(t)}}


def zf_active_flag(raw) -> bool:
    return str(raw if raw is not None else "1").strip().lower() not in ("0", "false", "no", "off")


def zf_code_run(s, first: str, n: int) -> list[str]:
    """n consecutive tank IDs: from a typed first one ending in a number
    (T050 → T050, T051…, keeping its width), or the next free T-codes.
    Refused when any of them is taken."""
    if not first:
        start = zf_next_code(s, TankRecord.tank_id, "T", 3)
        prefix, digits = "T", start[1:]
    else:
        m = re.match(r"^(.*?)(\d+)$", first)
        if m is None:
            raise ZfInputError(gettext("To add several tanks, give a first tank ID that ends in a number (T050), or leave it blank for the next free ones."))
        prefix, digits = m.group(1), m.group(2)
    codes = [f"{prefix}{int(digits) + i:0{len(digits)}d}" for i in range(n)]
    if any(len(c) > 80 for c in codes):
        raise ZfInputError(gettext("Keep the tank ID under %(max)s characters.", max=80))
    taken = sorted(set(s.scalars(select(TankRecord.tank_id).where(TankRecord.tank_id.in_(codes)))))
    if taken:
        raise ZfInputError(ngettext("%(tanks)s is already used. Start from another tank ID.",
                                    "%(tanks)s are already used. Start from another tank ID.", len(taken),
                                    tanks=", ".join(taken[:5]) + ("…" if len(taken) > 5 else "")))
    return codes


def zf_free_cells_from(s, rack, start, n: int) -> list[tuple[int, int]]:
    """Up to n empty cells (0-based) of a rack, reading along its rows from
    start (0-based (row, col), or the first cell)."""
    taken = {(r, c) for r, c in s.execute(
        select(TankRecord.row, TankRecord.col).where(TankRecord.rack_id_fk == rack.id))}
    begin = start[0] * rack.cols + start[1] if start else 0
    out = []
    for index in range(begin, rack.rows * rack.cols):
        cell = (index // rack.cols, index % rack.cols)
        if cell not in taken:
            out.append(cell)
            if len(out) == n:
                break
    return out


def zf_create_tanks(s, template: dict, codes: list[str], rack_id, position: str) -> tuple[str, bool]:
    """The New tank dialog's "How many": tanks with the same fields and
    consecutive IDs, side by side in the rack from the given position
    (the next free cells). One batch. Returns the message to show, and
    whether every tank found a place."""
    rack = s.get(FishRack, rack_id) if rack_id else None
    start = None
    if rack is not None and position:
        cell = positions.parse(position, rack.naming, rack.rows, rack.cols)
        if cell is None:
            raise ZfInputError(gettext("“%(position)s” is not a position in %(rack)s.", position=position,
                                       rack=rack.name))
        start = (cell[0] - 1, cell[1] - 1)
    cells = zf_free_cells_from(s, rack, start, len(codes)) if rack is not None else []
    with audit.batch(s, "create", f"new tanks ×{len(codes)} ({codes[0]}–{codes[-1]})", "tanks") as batch_row:
        made = []
        for i, code in enumerate(codes):
            tank = TankRecord(tank_id=code, **template)
            s.add(tank)
            if i < len(cells):
                zf_place(tank, rack, *cells[i])
            made.append(tank)
        batch_row.record_count = len(made)
    values = {"count": len(codes), "first": codes[0], "last": codes[-1]}
    if rack is None:
        return gettext("Created %(count)s tanks, %(first)s–%(last)s.", **values), True
    if cells:
        message = gettext("Created %(count)s tanks, %(first)s–%(last)s, in %(rack)s from %(start)s to %(end)s.",
                          rack=rack.name, start=positions.label(cells[0][0] + 1, cells[0][1] + 1, rack.naming, rack.cols),
                          end=positions.label(cells[-1][0] + 1, cells[-1][1] + 1, rack.naming, rack.cols), **values)
    else:
        message = gettext("Created %(count)s tanks, %(first)s–%(last)s, in %(rack)s.", rack=rack.name, **values)
    if len(cells) < len(codes):
        left = codes[len(cells):]
        message += " " + ngettext("%(rack)s had room for %(room)s; %(left)s is not placed.",
                                  "%(rack)s had room for %(room)s; %(left)s are not placed.", len(left),
                                  rack=rack.name, room=len(cells),
                                  left=left[0] if len(left) == 1 else left[0] + "–" + left[-1])
        return message, False
    return message, True


@app.route("/zebrafish/tanks/create", methods=["POST"])
@login_required
def zebrafish_create_tank():
    form = request.form
    with SessionLocal() as s:
        try:
            code = (form.get("tank_id") or "").strip()
            how_many = zf_int(form, "how_many", "How many", default=1, lo=1, hi=30)
            tank = TankRecord(
                tank_id=zf_unique(s, TankRecord.tank_id, code, "tank") if code and how_many == 1
                else code or zf_next_code(s, TankRecord.tank_id, "T", 3),
                purpose=zf_choice(form.get("purpose") or "stock", TANK_PURPOSE_OPTIONS, "Purpose"),
                line_id_fk=zf_ref(s, FishLine, form.get("line_id_fk"), "line"),
                owner=(form.get("owner", access.username()) or "").strip(),
                card_id=(form.get("card_id") or "").strip(),
                notes=(form.get("notes") or "").strip(),
                active=zf_active_flag(form.get("active")),
            )
            rack_id = zf_ref(s, FishRack, form.get("rack_id_fk"), "rack")
            position = (form.get("position") or "").strip()
            # A 0-based row/col (older clients) stands in for a typed position.
            row = zf_int(form, "row", "Row", lo=0)
            col = zf_int(form, "col", "Column", lo=0)
            if rack_id and not position and row is not None and col is not None:
                rack = s.get(FishRack, rack_id)
                if row >= rack.rows or col >= rack.cols:
                    raise ZfInputError(gettext("Row %(row)s, column %(col)s is outside %(rack)s (%(rows)s × %(cols)s).",
                                               row=row, col=col, rack=rack.name, rows=rack.rows, cols=rack.cols))
                position = positions.label(row + 1, col + 1, rack.naming, rack.cols)
            if how_many > 1:
                template = {k: getattr(tank, k) for k in ("purpose", "line_id_fk", "owner", "card_id", "notes", "active")}
                message, all_placed = zf_create_tanks(s, template, zf_code_run(s, code, how_many), rack_id, position)
        except ZfInputError as error:
            s.rollback()
            return zf_reply("tanks", str(error))
        if how_many > 1:
            s.commit()
            flash(message, "success" if all_placed else "warning")
            return zf_reply("tanks")
        s.add(tank)
        if rack_id:
            error = apply_fish_position(s, tank, rack_id, position)
            if error:
                zf_place(tank, None)
                flash(gettext("Tank %(tank)s was created but not placed: %(error)s", tank=tank.tank_id, error=error),
                      "error")
        s.commit()
    return zf_reply("tanks")


@app.route("/zebrafish/tanks/<int:tank_row_id>/update", methods=["POST"])
@login_required
def zebrafish_update_tank(tank_row_id: int):
    form = request.form
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return zf_reply("tanks", gettext("That tank no longer exists. Reload the page."), status=404)
        if not zf_can_edit(t):
            return zf_reply("tanks", zf_denied(t), status=403)
        try:
            if "tank_id" in form:
                t.tank_id = zf_unique(s, TankRecord.tank_id, form.get("tank_id"), "tank", t.id)
            if "purpose" in form:
                t.purpose = zf_choice(form.get("purpose"), TANK_PURPOSE_OPTIONS, "Purpose", t.purpose)
            if "line_id_fk" in form:
                t.line_id_fk = zf_ref(s, FishLine, form.get("line_id_fk"), "line")
            # Owner, rack and position also show on the rack grid and the
            # dialog, so a stale row only writes what it actually changed.
            if "owner" in form and form_changed(form, "owner"):
                t.owner = (form.get("owner") or "").strip()
            for fld in ("card_id", "notes"):
                if fld in form:
                    setattr(t, fld, (form.get(fld) or "").strip())
            if "active" in form:
                t.active = zf_active_flag(form.get("active"))
            # Which group a breeding or shared tank is for ("1": the lab's).
            if "share_group" in form and project_groups.differs(t, form.get("share_group"), shared=True):
                if not access.can_manage(t):
                    raise ZfInputError(gettext("Only %(owner)s or an admin can change whom tank %(tank)s is shared with.",
                                               owner=t.owner or gettext("its owner"), tank=t.tank_id))
                refused = project_groups.apply(t, form.get("share_group") or "1", set_shared=False)
                if refused:
                    raise ZfInputError(refused)
        except ZfInputError as error:
            s.rollback()
            return zf_reply("tanks", str(error))
        if ("rack_id_fk" in form or "position" in form) and form_changed(form, "rack_id_fk", "position"):
            rack_raw = form.get("rack_id_fk") if "rack_id_fk" in form else t.rack_id_fk
            position = form.get("position", "")
            if "position_was" in form and not form_changed(form, "position"):
                # Only the rack changed: the cell text belongs to the old rack.
                position = ""
            error = apply_fish_position(s, t, rack_raw, position)
            if error:
                s.rollback()
                return zf_reply("tanks", error)
        s.commit()
        state = zf_tank_state(t)
    return zf_reply("tanks", row=state)


@app.route("/zebrafish/tanks/<int:tank_row_id>/move", methods=["POST"])
@login_required
def zebrafish_move_tank(tank_row_id: int):
    """Drag-and-drop on the rack grid (0-based row/col). Dropping on an
    occupied cell swaps the two tanks; a blank rack unplaces."""
    form = request.form
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return jsonify({"ok": False, "error": gettext("That tank no longer exists.")}), 404
        if not zf_can_edit(t):
            return jsonify({"ok": False, "error": zf_denied(t)}), 403
        try:
            rack_id = zf_ref(s, FishRack, form.get("rack_id_fk"), "rack")
            row = zf_int(form, "row", "Row")
            col = zf_int(form, "col", "Column")
        except ZfInputError as error:
            return jsonify({"ok": False, "error": str(error)}), 409
        if rack_id is None:
            zf_place(t, None)
            s.commit()
            return jsonify({"ok": True})
        rack = s.get(FishRack, rack_id)
        if row is None or col is None or not (0 <= row < rack.rows and 0 <= col < rack.cols):
            return jsonify({"ok": False, "error": gettext("That cell is outside %(rack)s (%(rows)s × %(cols)s).",
                                                          rack=rack.name, rows=rack.rows, cols=rack.cols)}), 409
        occupant = s.scalar(select(TankRecord).where(
            TankRecord.rack_id_fk == rack.id, TankRecord.row == row, TankRecord.col == col,
            TankRecord.id != t.id))
        if occupant is not None:
            if not zf_can_edit(occupant):
                return jsonify({"ok": False, "error": zf_denied(occupant)}), 403
            occupant.rack_id_fk, occupant.row, occupant.col = t.rack_id_fk, t.row, t.col
        t.rack_id_fk, t.row, t.col = rack.id, row, col
        s.commit()
    return jsonify({"ok": True})


def zf_delete_tank(s, t) -> str | None:
    """Delete a tank, or say why not. Its fish rows would go with it, so a
    tank holding any is refused. Clutches, mating tanks and sac-log entries
    naming it keep everything else and lose the link — left in place, the
    id would point at whichever tank reuses it."""
    if t.fish:
        n = len(t.fish)
        return ngettext("Tank %(tank)s still holds %(num)s fish row. Move or delete them first.",
                        "Tank %(tank)s still holds %(num)s fish rows. Move or delete them first.", n, tank=t.tank_id)
    for c in s.scalars(select(ClutchRecord).where(
            (ClutchRecord.father_tank_id == t.id) | (ClutchRecord.mother_tank_id == t.id))):
        if c.father_tank_id == t.id:
            c.father_tank_id = None
        if c.mother_tank_id == t.id:
            c.mother_tank_id = None
    for m in s.scalars(select(TankRecord).where(
            (TankRecord.mating_father_tank_id == t.id) | (TankRecord.mating_mother_tank_id == t.id))):
        if m.mating_father_tank_id == t.id:
            m.mating_father_tank_id = None
        if m.mating_mother_tank_id == t.id:
            m.mating_mother_tank_id = None
    for entry in s.scalars(select(FishSacLog).where(FishSacLog.tank_id_fk == t.id)):
        entry.tank_id_fk = None
    s.delete(t)
    return None


@app.route("/zebrafish/tanks/<int:tank_row_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_tank(tank_row_id: int):
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return redirect(url_for("zebrafish", view="tanks"))
        if not zf_can_edit(t):
            flash(zf_denied(t), "error")
            return redirect(url_for("zebrafish", view="tanks"))
        code = t.tank_id
        # A batch, so the delete and the links it clears undo together.
        with audit.batch(s, "delete", f"delete tank {code}", "tanks"):
            error = zf_delete_tank(s, t)
        if error:
            s.rollback()
            flash(error, "error")
        else:
            s.commit()
            flash(gettext("Deleted tank %(tank)s. Undo it from Batch history if that was a mistake.", tank=code),
                  "success")
    return redirect(url_for("zebrafish", view="tanks"))


@app.route("/zebrafish/tanks/<int:tank_row_id>/duplicate", methods=["POST"])
@login_required
def zebrafish_duplicate_tank(tank_row_id: int):
    """A new tank like this one — same purpose, line and rack (first free
    cell) — with the next free ID, owned by whoever made the copy."""
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return redirect(url_for("zebrafish", view="tanks"))
        copy = TankRecord(tank_id=zf_next_code(s, TankRecord.tank_id, "T", 3), purpose=t.purpose,
                          line_id_fk=t.line_id_fk, owner=access.username(), card_id="", notes=t.notes)
        s.add(copy)
        note = ""
        if t.rack is not None:
            cell = zf_free_cell(s, t.rack)
            if cell:
                zf_place(copy, t.rack, *cell)
            else:
                note = " " + gettext("%(rack)s is full, so it is not placed.", rack=t.rack.name)
        s.commit()
        flash(gettext("Copied tank %(tank)s to %(copy)s.", tank=t.tank_id, copy=copy.tank_id) + note, "success")
    return redirect(url_for("zebrafish", view="tanks"))


@app.route("/zebrafish/tanks/<int:tank_row_id>/toggle-geno", methods=["POST"])
@login_required
def zebrafish_toggle_geno(tank_row_id: int):
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return jsonify({"ok": False, "error": gettext("That tank no longer exists.")}), 404
        if not zf_can_edit(t):
            return jsonify({"ok": False, "error": zf_denied(t)}), 403
        t.needs_genotyping = not bool(t.needs_genotyping)
        s.commit()
        return jsonify({"ok": True, "needs_genotyping": t.needs_genotyping})


@app.route("/zebrafish/tanks/bulk", methods=["POST"])
@login_required
def zebrafish_bulk_tanks():
    """Batch actions on ticked tanks: set purpose or owner, move to a rack,
    flag for genotyping, return mating tanks, delete. Applied to the tanks
    you may edit."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    labels = {"purpose": "Set purpose", "owner": "Set owner", "rack": "Moved",
              "geno": "Genotyping flag", "return": "Returned", "delete": "Deleted"}
    if action not in labels:
        flash(gettext("Pick an action."), "error")
        return redirect(url_for("zebrafish", view="tanks"))
    changed = skipped = 0
    blocked: list[str] = []
    with SessionLocal() as s:
        try:
            if action == "purpose":
                value = zf_choice(value, TANK_PURPOSE_OPTIONS, "Purpose")
            rack = s.get(FishRack, zf_ref(s, FishRack, value, "rack")) if action == "rack" and value else None
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="tanks"))
        what = {"purpose": f"set purpose = {value}", "owner": f"set owner = {value or '(blank)'}",
                "rack": f"move to {rack.name if rack else '(no rack)'}", "geno": f"genotyping flag {'on' if value != '0' else 'off'}",
                "return": "return mating tanks", "delete": "delete tanks"}[action]
        with audit.batch(s, "delete" if action == "delete" else "update", what, "tanks") as batch_row:
            for t in zf_selected(s, TankRecord, form):
                if not zf_can_edit(t):
                    skipped += 1
                    continue
                if action == "purpose":
                    t.purpose = value
                elif action == "owner":
                    t.owner = value
                elif action == "geno":
                    t.needs_genotyping = value != "0"
                elif action == "rack":
                    if rack is not None and t.rack_id_fk == rack.id and t.row is not None:
                        continue
                    error = apply_fish_position(s, t, rack.id if rack else "", "")
                    if error:
                        blocked.append(f"{t.tank_id}: {error}")
                        continue
                    s.flush()
                elif action == "return":
                    error, _ = zf_return_mating(s, t, {})
                    if error:
                        # Messages that already begin with the tank are not prefixed with it again.
                        names_it = (gettext("Tank %(tank)s is not a mating tank.", tank=t.tank_id),
                                    gettext("Mating tank %(tank)s was already returned.", tank=t.tank_id),
                                    zf_denied(t))
                        blocked.append(error if error in names_it else f"{t.tank_id}: {error}")
                        continue
                    s.flush()
                elif action == "delete":
                    error = zf_delete_tank(s, t)
                    if error:
                        blocked.append(error)
                        continue
                changed += 1
            batch_row.record_count = changed
        s.commit()
    zf_report(changed, skipped, labels[action], "tank", "tanks", blocked)
    return redirect(url_for("zebrafish", view="tanks"))


# ---- Fish (group rows) -----------------------------------------------------


def zf_apply_fish_status(s, f, previous_status: str | None) -> None:
    """The mouse rule for fish. Turning sac (or dead) stamps today as the
    sac date unless one was given, and logs the fish on the sac log;
    turning back to a living status clears the date and that log entry."""
    dead_now = (f.status or "").strip().lower() in FISH_DEAD_STATUSES
    dead_before = (previous_status or "").strip().lower() in FISH_DEAD_STATUSES
    if dead_now and not dead_before:
        f.sac_date = f.sac_date or date.today()
        if f.count:
            s.flush()  # a new row needs its id for the log link
            s.add(FishSacLog(tank_id_fk=f.tank_id_fk, line_id_fk=f.line_id_fk or (f.tank.line_id_fk if f.tank else None),
                             fish_id_fk=f.id, count=f.count, reason=f"{FISH_STATUS_SAC_REASON}{f.status}",
                             recorded_by=access.username()))
    elif dead_before and not dead_now:
        f.sac_date = None
        # Only the entry the status change wrote: fish logged by hand from
        # this row were sac'd and stay on the log.
        for entry in s.scalars(select(FishSacLog).where(FishSacLog.fish_id_fk == f.id)) if f.id else []:
            if (entry.reason or "").startswith(FISH_STATUS_SAC_REASON):
                s.delete(entry)


def zf_fish_state(f) -> dict:
    return {"active": fish_alive(f), "values": {
        "status": f.status, "count": f.count, "sex": f.sex, "tank_id_fk": str(f.tank_id_fk or ""),
        "sac_date": f.sac_date.isoformat() if f.sac_date else ""}}


def zf_fish_from_form(s, f, form) -> None:
    """Write the posted fields onto a fish row. Raises ZfInputError."""
    if "individual_id" in form:
        f.individual_id = (form.get("individual_id") or "").strip()
    for fld in ("genotype", "notes"):
        if fld in form:
            setattr(f, fld, (form.get(fld) or "").strip())
    if "count" in form:
        f.count = zf_int(form, "count", "Count", default=0, lo=0, hi=100000)
    if "sex" in form:
        f.sex = zf_choice(form.get("sex"), FISH_SEX_OPTIONS, "Sex", f.sex)
    if "status" in form:
        f.status = zf_choice(form.get("status"), FISH_STATUS_OPTIONS + ["dead"], "Status", f.status)
    if "line_id_fk" in form:
        f.line_id_fk = zf_ref(s, FishLine, form.get("line_id_fk"), "line")
    if "date_of_fertilization" in form:
        f.date_of_fertilization = zf_date(form, "date_of_fertilization", "Fertilised")
    if "sac_date" in form:
        f.sac_date = zf_date(form, "sac_date", "Sac date")


@app.route("/zebrafish/fish/create", methods=["POST"])
@login_required
def zebrafish_create_fish():
    form = request.form
    with SessionLocal() as s:
        try:
            tank_id = zf_ref(s, TankRecord, form.get("tank_id_fk"), "tank")
            if tank_id is None:
                raise ZfInputError(gettext("Choose the tank these fish are in."))
            tank = s.get(TankRecord, tank_id)
            if not zf_can_edit(tank):
                raise ZfInputError(zf_denied(tank))
            f = FishRecord(tank=tank, count=1, sex="mixed", status="alive")
            zf_fish_from_form(s, f, form)
        except ZfInputError as error:
            return zf_reply("fish", str(error))
        s.add(f)
        zf_apply_fish_status(s, f, "alive")
        s.commit()
    return zf_reply("fish")


@app.route("/zebrafish/fish/<int:fish_row_id>/update", methods=["POST"])
@login_required
def zebrafish_update_fish(fish_row_id: int):
    form = request.form
    with SessionLocal() as s:
        f = s.get(FishRecord, fish_row_id)
        if f is None:
            return zf_reply("fish", gettext("That fish row no longer exists. Reload the page."), status=404)
        if not zf_can_edit(f):
            return zf_reply("fish", zf_denied(f), status=403)
        previous_status = f.status
        try:
            if "tank_id_fk" in form:
                tank_id = zf_ref(s, TankRecord, form.get("tank_id_fk"), "tank")
                if tank_id is None:
                    raise ZfInputError(gettext("Fish have to be in a tank."))
                if tank_id != f.tank_id_fk:
                    tank = s.get(TankRecord, tank_id)
                    if not zf_can_edit(tank):
                        raise ZfInputError(gettext("Can’t move fish into %(tank)s: %(reason)s", tank=tank.tank_id,
                                                   reason=zf_denied(tank)))
                    f.tank_id_fk, f.tank = tank.id, tank  # the column too, for the change history
            zf_fish_from_form(s, f, form)
        except ZfInputError as error:
            s.rollback()
            return zf_reply("fish", str(error))
        zf_apply_fish_status(s, f, previous_status)
        s.commit()
        state = zf_fish_state(f)
    return zf_reply("fish", row=state)


@app.route("/zebrafish/fish/<int:fish_row_id>/duplicate", methods=["POST"])
@login_required
def zebrafish_duplicate_fish(fish_row_id: int):
    with SessionLocal() as s:
        f = s.get(FishRecord, fish_row_id)
        if f is None:
            return redirect(url_for("zebrafish", view="fish"))
        if not zf_can_edit(f):
            flash(zf_denied(f), "error")
            return redirect(url_for("zebrafish", view="fish"))
        s.add(FishRecord(tank_id_fk=f.tank_id_fk, line_id_fk=f.line_id_fk, individual_id="", count=f.count,
                         sex=f.sex, status=f.status, date_of_fertilization=f.date_of_fertilization,
                         sac_date=f.sac_date, clutch_id_fk=f.clutch_id_fk, genotype=f.genotype, notes=f.notes))
        s.commit()
        flash(gettext("Copied the fish row."), "success")
    return redirect(url_for("zebrafish", view="fish"))


@app.route("/zebrafish/fish/<int:fish_row_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_fish(fish_row_id: int):
    with SessionLocal() as s:
        f = s.get(FishRecord, fish_row_id)
        if f is not None:
            if not zf_can_edit(f):
                flash(zf_denied(f), "error")
                return redirect(url_for("zebrafish", view="fish"))
            # The sac log is history: it keeps the entry, not the link.
            for entry in s.scalars(select(FishSacLog).where(FishSacLog.fish_id_fk == f.id)):
                entry.fish_id_fk = None
            s.delete(f)
            s.commit()
    return redirect(url_for("zebrafish", view="fish"))


@app.route("/zebrafish/fish/bulk", methods=["POST"])
@login_required
def zebrafish_bulk_fish():
    """Batch actions on ticked fish rows: set status, move to a tank, sac."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    labels = {"status": "Set status", "tank": "Moved", "sac": "Sac’d"}
    if action not in labels:
        flash(gettext("Pick an action."), "error")
        return redirect(url_for("zebrafish", view="fish"))
    changed = skipped = 0
    blocked: list[str] = []
    with SessionLocal() as s:
        try:
            if action == "status":
                value = zf_choice(value, FISH_STATUS_OPTIONS + ["dead"], "Status")
            tank = s.get(TankRecord, zf_ref(s, TankRecord, value, "tank") or 0) if action == "tank" else None
            if action == "tank" and tank is None:
                raise ZfInputError(gettext("Choose the tank to move them to."))
            if tank is not None and not zf_can_edit(tank):
                raise ZfInputError(gettext("Can’t move fish into %(tank)s: %(reason)s", tank=tank.tank_id,
                                           reason=zf_denied(tank)))
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="fish"))
        what = {"status": f"set status = {value}", "tank": f"move to tank {tank.tank_id if tank else ''}",
                "sac": "sac fish"}[action]
        with audit.batch(s, "update", what, "fish") as batch_row:
            for f in zf_selected(s, FishRecord, form):
                if not zf_can_edit(f):
                    skipped += 1
                    continue
                previous = f.status
                if action == "tank":
                    f.tank_id_fk, f.tank = tank.id, tank  # the column too, so undo can move them back
                else:
                    if action == "sac" and (f.status or "").lower() in FISH_DEAD_STATUSES:
                        continue
                    f.status = "sac" if action == "sac" else value
                    zf_apply_fish_status(s, f, previous)
                changed += 1
            batch_row.record_count = changed
        s.commit()
    zf_report(changed, skipped, labels[action], "fish row", "fish rows", blocked)
    return redirect(url_for("zebrafish", view="fish"))


# ---- Lines -----------------------------------------------------------------


def zf_line_parent(s, line_id, raw):
    """A parent line that keeps the lineage a tree: not the line itself,
    and not one of its own descendants."""
    parent_id = zf_ref(s, FishLine, raw, "parent line")
    if parent_id is None or line_id is None:
        return parent_id
    seen = set()
    cursor = parent_id
    while cursor is not None and cursor not in seen:
        if cursor == line_id:
            raise ZfInputError(gettext("A line can’t descend from itself. Pick a parent that is not this line or one of its descendants."))
        seen.add(cursor)
        cursor = s.scalar(select(FishLine.parent_line_id_fk).where(FishLine.id == cursor))
    return parent_id


LINE_TEXT_FIELDS = ("zfin_name", "background", "transgene_summary", "allele", "iacuc_protocol", "founder_info", "notes")


@app.route("/zebrafish/lines/create", methods=["POST"])
@login_required
def zebrafish_create_line():
    form = request.form
    with SessionLocal() as s:
        try:
            ln = FishLine(name=zf_unique(s, FishLine.name, form.get("name"), "line", max_len=200),
                          parent_line_id_fk=zf_line_parent(s, None, form.get("parent_line_id_fk")),
                          # Whoever makes a line owns it unless they name someone.
                          owner=(form.get("owner") or "").strip()[:80] or access.username(),
                          **{fld: (form.get(fld) or "").strip() for fld in LINE_TEXT_FIELDS})
        except ZfInputError as error:
            return zf_reply("lines", str(error))
        s.add(ln)
        s.commit()
    return zf_reply("lines")


@app.route("/zebrafish/lines/<int:line_id>/update", methods=["POST"])
@login_required
def zebrafish_update_line(line_id: int):
    form = request.form
    with SessionLocal() as s:
        ln = s.get(FishLine, line_id)
        if ln is None:
            return zf_reply("lines", gettext("That line no longer exists. Reload the page."), status=404)
        if not zf_can_edit(ln):
            return zf_reply("lines", zf_denied(ln), status=403)
        try:
            if "name" in form:
                ln.name = zf_unique(s, FishLine.name, form.get("name"), "line", ln.id, max_len=200)
            for fld in LINE_TEXT_FIELDS:
                if fld in form:
                    setattr(ln, fld, (form.get(fld) or "").strip())
            if "parent_line_id_fk" in form:
                ln.parent_line_id_fk = zf_line_parent(s, ln.id, form.get("parent_line_id_fk"))
            if "owner" in form:
                ln.owner = (form.get("owner") or "").strip()[:80]
        except ZfInputError as error:
            s.rollback()
            return zf_reply("lines", str(error))
        s.commit()
        state = {"values": {"name": ln.name, "owner": ln.owner, "parent_line_id_fk": str(ln.parent_line_id_fk or "")}}
    return zf_reply("lines", row=state)


def zf_line_uses(s, ln) -> str:
    """What still uses a line, in words; empty when nothing does."""
    uses = [
        (s.scalar(select(func.count(TankRecord.id)).where(TankRecord.line_id_fk == ln.id)), "tank", "tanks"),
        (s.scalar(select(func.count(FishRecord.id)).where(FishRecord.line_id_fk == ln.id)), "fish row", "fish rows"),
        (s.scalar(select(func.count(ClutchRecord.id)).where(ClutchRecord.line_id_fk == ln.id)), "clutch", "clutches"),
        (s.scalar(select(func.count(FishLine.id)).where(FishLine.parent_line_id_fk == ln.id)), "child line", "child lines"),
    ]
    return ", ".join(_zf_count(n, one, many) for n, one, many in uses if n)


def zf_delete_line(s, ln) -> str | None:
    """Delete a line, or say what still uses it. Sac-log entries are
    history and only lose the link."""
    used = zf_line_uses(s, ln)
    if used:
        return gettext("Line %(line)s is still used by %(used)s. Reassign or delete those first.", line=ln.name,
                       used=used)
    for entry in s.scalars(select(FishSacLog).where(FishSacLog.line_id_fk == ln.id)):
        entry.line_id_fk = None
    s.delete(ln)
    return None


@app.route("/zebrafish/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_line(line_id: int):
    with SessionLocal() as s:
        ln = s.get(FishLine, line_id)
        if ln is None:
            return redirect(url_for("zebrafish", view="lines"))
        if not zf_can_edit(ln):
            flash(zf_denied(ln), "error")
            return redirect(url_for("zebrafish", view="lines"))
        name = ln.name
        with audit.batch(s, "delete", f"delete line {name}", "fish_lines"):
            error = zf_delete_line(s, ln)
        if error:
            s.rollback()
            flash(error, "error")
        else:
            s.commit()
            flash(gettext("Deleted line %(line)s.", line=name), "success")
    return redirect(url_for("zebrafish", view="lines"))


@app.route("/zebrafish/lines/<int:line_id>/duplicate", methods=["POST"])
@login_required
def zebrafish_duplicate_line(line_id: int):
    with SessionLocal() as s:
        ln = s.get(FishLine, line_id)
        if ln is None:
            return redirect(url_for("zebrafish", view="lines"))
        taken = set(s.scalars(select(FishLine.name)))
        name, n = f"{ln.name} copy", 2
        while name in taken:
            name, n = f"{ln.name} copy {n}", n + 1
        s.add(FishLine(name=name, parent_line_id_fk=ln.parent_line_id_fk, owner=access.username(),
                       **{fld: getattr(ln, fld) for fld in LINE_TEXT_FIELDS}))
        s.commit()
        flash(gettext("Copied line %(line)s to %(copy)s.", line=ln.name, copy=name), "success")
    return redirect(url_for("zebrafish", view="lines"))


@app.route("/zebrafish/lines/bulk", methods=["POST"])
@login_required
def zebrafish_bulk_lines():
    """Batch actions on ticked lines: set background, IACUC protocol or
    owner, delete (lines still in use are kept, and named). Applied to the
    lines you may edit."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    labels = {"background": "Set background", "iacuc_protocol": "Set IACUC #", "owner": "Set owner",
              "delete": "Deleted"}
    if action not in labels:
        flash(gettext("Pick an action."), "error")
        return redirect(url_for("zebrafish", view="lines"))
    changed = skipped = 0
    blocked: list[str] = []
    with SessionLocal() as s:
        what = "delete lines" if action == "delete" else f"{labels[action].lower()} = {value or '(blank)'}"
        with audit.batch(s, "delete" if action == "delete" else "update", what, "fish_lines") as batch_row:
            for ln in zf_selected(s, FishLine, form):
                if not zf_can_edit(ln):
                    skipped += 1
                    continue
                if action == "delete":
                    error = zf_delete_line(s, ln)
                    if error:
                        blocked.append(error)
                        continue
                    s.flush()
                else:
                    setattr(ln, action, value)
                changed += 1
            batch_row.record_count = changed
        s.commit()
    zf_report(changed, skipped, labels[action], "line", "lines", blocked)
    return redirect(url_for("zebrafish", view="lines"))


@app.route("/zebrafish/lines/<int:line_id>")
@login_required
def zebrafish_line_detail(line_id: int):
    """Per-line detail page with founder lineage tree."""
    with SessionLocal() as s:
        ln = s.get(FishLine, line_id)
        if ln is None:
            flash(gettext("Line not found."), "error")
            return redirect(url_for("zebrafish", view="lines"))

        # The ancestor chain. The seen set stops at a cycle written before
        # parents were checked, rather than looping forever.
        ancestors = []
        seen = {ln.id}
        cursor = ln.parent_line
        while cursor is not None and cursor.id not in seen and len(ancestors) < 20:
            ancestors.insert(0, cursor)
            seen.add(cursor.id)
            cursor = cursor.parent_line

        # Direct children.
        children = s.scalars(select(FishLine).where(FishLine.parent_line_id_fk == ln.id,
                                                    FishLine.id != ln.id)).all()

        # Tanks currently holding this line.
        # Racks are read after the session closes, so load them now.
        line_tanks = s.scalars(
            select(TankRecord).options(joinedload(TankRecord.rack))
            .where(TankRecord.line_id_fk == ln.id).order_by(TankRecord.tank_id)
        ).all()
        tank_positions = {t.id: fish_position_label(t) for t in line_tanks}

        all_lines = s.scalars(select(FishLine).order_by(FishLine.name)).all()
        editable = zf_can_edit(ln)
        denied = "" if editable else zf_denied(ln)
        usernames = current_lab_usernames(s)

    return render_template(
        "zebrafish_line_detail.html",
        line=ln, ancestors=ancestors, children=children,
        line_tanks=line_tanks, tank_positions=tank_positions, all_lines=all_lines,
        editable=editable, denied=denied, usernames=usernames,
    )


# ---- Racks -----------------------------------------------------------------


def zf_rack_fields(s, r, form) -> None:
    """Name, size, water system and naming from the rack dialog. Raises
    ZfInputError. Sizes are clamped to what the grid can draw."""
    if "name" in form or r.id is None:
        name = (form.get("name") or "").strip() or r.name or "Rack"
        clash = s.scalar(select(FishRack.id).where(FishRack.name == name, FishRack.id != (r.id or 0)))
        if clash is not None:
            raise ZfInputError(gettext("There is already a rack called %(name)s.", name=name))
        r.name = name
    r.rows = max(1, min(26, zf_int(form, "rows", "Rows", default=r.rows or 8)))
    r.cols = max(1, min(40, zf_int(form, "cols", "Columns", default=r.cols or 10)))
    if "system_id_fk" in form:
        r.system_id_fk = zf_ref(s, WaterSystem, form.get("system_id_fk"), "water system")
    if "naming_mode" in form or r.id is None:
        r.naming = json.dumps(positions.scheme_from_form(form))


@app.route("/zebrafish/racks/create", methods=["POST"])
@login_required
def zebrafish_create_rack():
    with SessionLocal() as s:
        r = FishRack(notes=(request.form.get("notes") or "").strip(), created_by=g.user.username)
        try:
            zf_rack_fields(s, r, request.form)
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        s.add(r)
        s.commit()
    return redirect(url_for("zebrafish", view="tanks", mode="grid"))


@app.route("/zebrafish/racks/<int:rack_id>/update", methods=["POST"])
@login_required
def zebrafish_update_rack(rack_id: int):
    """Rename or resize a rack. Tanks outside a smaller grid keep their
    numbers and show as unplaced until moved."""
    with SessionLocal() as s:
        r = s.get(FishRack, rack_id)
        if r is None:
            flash(gettext("That rack no longer exists."), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        if not access.can_edit_rack(r):
            # Renaming or resizing a rack moves every tank in it: as for
            # mouse and fly racks, only whoever added it or an admin.
            flash(gettext("Only whoever added rack %(rack)s, or an admin, can change it.", rack=r.name), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        try:
            zf_rack_fields(s, r, request.form)
        except ZfInputError as error:
            s.rollback()
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        s.commit()
        flash(gettext("Saved rack %(rack)s.", rack=r.name), "success")
    return redirect(url_for("zebrafish", view="tanks", mode="grid"))


@app.route("/zebrafish/racks/<int:rack_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_rack(rack_id: int):
    """Delete a rack; its tanks stay, unplaced. Refused when that would
    move tanks you may not edit."""
    with SessionLocal() as s:
        r = s.get(FishRack, rack_id)
        if r is not None and not access.can_edit_rack(r):
            flash(gettext("Only whoever added rack %(rack)s, or an admin, can delete it.", rack=r.name), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        if r is not None:
            theirs = [t.tank_id for t in r.tanks if not zf_can_edit(t)]
            if theirs:
                flash(gettext("%(rack)s holds tanks you can’t edit (%(tanks)s). Ask their owners, or an admin.",
                              rack=r.name, tanks=", ".join(theirs[:5]) + ("…" if len(theirs) > 5 else "")), "error")
                return redirect(url_for("zebrafish", view="tanks", mode="grid"))
            with audit.batch(s, "delete", f"delete rack {r.name}", "fish_racks"):
                for t in r.tanks:
                    t.rack_id_fk = None
                    t.row = None
                    t.col = None
                s.delete(r)
            s.commit()
    return redirect(url_for("zebrafish", view="tanks", mode="grid"))


# ---- Water systems + logs --------------------------------------------------


@app.route("/zebrafish/systems/create", methods=["POST"])
@login_required
def zebrafish_create_system():
    form = request.form
    with SessionLocal() as s:
        try:
            name = (form.get("name") or "").strip()
            if not name:
                raise ZfInputError(gettext("A water system needs a name."))
            if s.scalar(select(WaterSystem.id).where(WaterSystem.name == name)) is not None:
                raise ZfInputError(gettext("There is already a water system called %(name)s.", name=name))
            s.add(WaterSystem(
                name=name,
                room=(form.get("room") or "").strip(),
                target_temp_c=zf_float(form, "target_temp_c", "Target °C"),
                target_ph=zf_float(form, "target_ph", "Target pH"),
                target_conductivity=zf_float(form, "target_conductivity", "Target µS/cm"),
                notes=(form.get("notes") or "").strip(),
                created_by=access.username(),
            ))
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="water"))
        s.commit()
    return redirect(url_for("zebrafish", view="water"))


def zf_system_uses(s, system) -> str:
    """What still uses a water system, in words; empty when nothing does."""
    rack_ids = list(s.scalars(select(FishRack.id).where(FishRack.system_id_fk == system.id)))
    if not rack_ids:
        return ""
    n_tanks = s.scalar(select(func.count(TankRecord.id)).where(TankRecord.rack_id_fk.in_(rack_ids))) or 0
    racks = ngettext("%(num)s rack", "%(num)s racks", len(rack_ids))
    if not n_tanks:
        return racks
    return gettext("%(racks)s holding %(tanks)s", racks=racks, tanks=_zf_count(n_tanks, "tank", "tanks"))


@app.route("/zebrafish/systems/<int:system_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_system(system_id: int):
    """Delete a water system: its creator or an admin (systems from before
    creators were recorded are admin-only, like racks). Refused while racks
    use it. Its readings are history: they stay, listed under deleted
    systems."""
    from sqlalchemy import update as sa_update

    back = url_for("zebrafish", view="water")
    with SessionLocal() as s:
        system = s.get(WaterSystem, system_id)
        if system is None:
            flash(gettext("That water system no longer exists."), "error")
            return redirect(back)
        if not access.can_edit_rack(system):
            flash(access.denied_message("water system", system.created_by)
                  if system.created_by else
                  gettext("Only an admin can delete a water system added before creators were recorded."), "error")
            return redirect(back)
        used = zf_system_uses(s, system)
        if used:
            flash(gettext("%(system)s is still used by %(used)s. Move the racks to another system first (rack settings on the rack grid).", system=system.name, used=used), "error")
            return redirect(back)
        name = system.name
        with audit.batch(s, "delete", f"delete water system {name}", "water_systems"):
            # Unlink the readings in the database first: the relationship
            # would otherwise delete them along with the system.
            s.execute(sa_update(WaterLog).where(WaterLog.system_id_fk == system.id).values(system_id_fk=None))
            s.expire(system, ["water_logs"])
            s.delete(system)
        s.commit()
    flash(gettext("Deleted water system %(system)s. Its readings are kept under deleted systems.", system=name),
          "success")
    return redirect(back)


@app.route("/zebrafish/water/log", methods=["POST"])
@login_required
def zebrafish_log_water():
    form = request.form
    with SessionLocal() as s:
        try:
            system_id = zf_ref(s, WaterSystem, form.get("system_id_fk"), "water system")
            if system_id is None:
                raise ZfInputError(gettext("Pick the water system this reading is for."))
            log = WaterLog(
                system_id_fk=system_id,
                ph=zf_float(form, "ph", "pH"),
                conductivity=zf_float(form, "conductivity", "Conductivity"),
                temperature_c=zf_float(form, "temperature_c", "Temperature"),
                salinity=zf_float(form, "salinity", "Salinity"),
                alarm=form.get("alarm") in ("1", "on", "true", "yes"),
                recorded_by=access.username(),
                notes=(form.get("notes") or "").strip(),
            )
            if (all(v is None for v in (log.ph, log.conductivity, log.temperature_c, log.salinity))
                    and not log.alarm and not log.notes):
                raise ZfInputError(gettext("Enter at least one reading, a note or an alarm."))
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="water"))
        s.add(log)
        s.commit()
    return redirect(url_for("zebrafish", view="water"))


@app.route("/zebrafish/water/<int:system_id>/series.json")
@login_required
def zebrafish_water_series(system_id: int):
    """Return last 30 days of readings for one system — used by the trend chart."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    with SessionLocal() as s:
        logs = s.scalars(
            select(WaterLog)
            .where(WaterLog.system_id_fk == system_id)
            .where(WaterLog.recorded_at >= cutoff)
            .order_by(WaterLog.recorded_at.asc())
        ).all()
        return jsonify({
            "ok": True,
            "series": [
                {
                    "t": log.recorded_at.isoformat(),
                    "ph": log.ph, "cond": log.conductivity,
                    "temp": log.temperature_c, "salinity": log.salinity,
                    "alarm": bool(log.alarm), "notes": log.notes or "",
                }
                for log in logs
            ],
        })


# ---- Clutches + mating wizard ---------------------------------------------


def zf_clutch_from_form(s, c, form) -> None:
    """Write the posted fields onto a clutch. Raises ZfInputError."""
    if "clutch_id" in form:
        c.clutch_id = zf_unique(s, ClutchRecord.clutch_id, form.get("clutch_id"), "clutch", c.id)
    if "date_of_fertilization" in form:
        c.date_of_fertilization = zf_date(form, "date_of_fertilization", "Fertilised") or c.date_of_fertilization
    if "line_id_fk" in form:
        c.line_id_fk = zf_ref(s, FishLine, form.get("line_id_fk"), "line")
    if "father_tank_id" in form:
        c.father_tank_id = zf_ref(s, TankRecord, form.get("father_tank_id"), "male tank")
    if "mother_tank_id" in form:
        c.mother_tank_id = zf_ref(s, TankRecord, form.get("mother_tank_id"), "female tank")
    for fld, label in (("embryo_count", "Embryos"), ("larvae_count", "Larvae"), ("adults_count", "Adults")):
        if fld in form:
            setattr(c, fld, zf_int(form, fld, label, default=0, lo=0, hi=1000000))
    if "owner" in form:
        c.owner = (form.get("owner") or "").strip()
    if "notes" in form:
        c.notes = (form.get("notes") or "").strip()


@app.route("/zebrafish/clutches/create", methods=["POST"])
@login_required
def zebrafish_create_clutch():
    form = request.form
    with SessionLocal() as s:
        c = ClutchRecord(date_of_fertilization=date.today(), owner=access.username(),
                         embryo_count=0, larvae_count=0, adults_count=0, notes="")
        try:
            if not (form.get("clutch_id") or "").strip():
                c.clutch_id = zf_next_code(s, ClutchRecord.clutch_id, f"C{date.today().strftime('%y%m%d')}-")
            zf_clutch_from_form(s, c, {k: v for k, v in form.items() if k != "clutch_id" or v.strip()})
        except ZfInputError as error:
            return zf_reply("clutches", str(error))
        s.add(c)
        s.commit()
    return zf_reply("clutches")


@app.route("/zebrafish/clutches/<int:clutch_row_id>/update", methods=["POST"])
@login_required
def zebrafish_update_clutch(clutch_row_id: int):
    with SessionLocal() as s:
        c = s.get(ClutchRecord, clutch_row_id)
        if c is None:
            return zf_reply("clutches", gettext("That clutch no longer exists. Reload the page."), status=404)
        if not zf_can_edit(c):
            return zf_reply("clutches", zf_denied(c), status=403)
        try:
            zf_clutch_from_form(s, c, request.form)
        except ZfInputError as error:
            s.rollback()
            return zf_reply("clutches", str(error))
        s.commit()
        state = {"values": {"clutch_id": c.clutch_id, "owner": c.owner,
                            "date_of_fertilization": c.date_of_fertilization.isoformat()}}
    return zf_reply("clutches", row=state)


def zf_delete_clutch(s, c) -> None:
    """Fish raised from a clutch keep everything but the link."""
    for f in s.scalars(select(FishRecord).where(FishRecord.clutch_id_fk == c.id)):
        f.clutch_id_fk = None
    s.delete(c)


@app.route("/zebrafish/clutches/<int:clutch_row_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_clutch(clutch_row_id: int):
    with SessionLocal() as s:
        c = s.get(ClutchRecord, clutch_row_id)
        if c is not None:
            if not zf_can_edit(c):
                flash(zf_denied(c), "error")
                return redirect(url_for("zebrafish", view="clutches"))
            with audit.batch(s, "delete", f"delete clutch {c.clutch_id}", "clutches"):
                zf_delete_clutch(s, c)
            s.commit()
    return redirect(url_for("zebrafish", view="clutches"))


@app.route("/zebrafish/clutches/<int:clutch_row_id>/duplicate", methods=["POST"])
@login_required
def zebrafish_duplicate_clutch(clutch_row_id: int):
    """Another clutch from the same cross, dated today, owned by whoever
    made the copy."""
    with SessionLocal() as s:
        c = s.get(ClutchRecord, clutch_row_id)
        if c is None:
            return redirect(url_for("zebrafish", view="clutches"))
        copy = ClutchRecord(clutch_id=zf_next_code(s, ClutchRecord.clutch_id, f"C{date.today().strftime('%y%m%d')}-"),
                            date_of_fertilization=date.today(), line_id_fk=c.line_id_fk,
                            father_tank_id=c.father_tank_id, mother_tank_id=c.mother_tank_id,
                            embryo_count=0, larvae_count=0, adults_count=0, owner=access.username(), notes=c.notes)
        s.add(copy)
        s.commit()
        flash(gettext("Copied clutch %(clutch)s to %(copy)s.", clutch=c.clutch_id, copy=copy.clutch_id), "success")
    return redirect(url_for("zebrafish", view="clutches"))


@app.route("/zebrafish/clutches/bulk", methods=["POST"])
@login_required
def zebrafish_bulk_clutches():
    """Batch actions on ticked clutches: set owner, delete."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    labels = {"owner": "Set owner", "delete": "Deleted"}
    if action not in labels:
        flash(gettext("Pick an action."), "error")
        return redirect(url_for("zebrafish", view="clutches"))
    changed = skipped = 0
    with SessionLocal() as s:
        what = "delete clutches" if action == "delete" else f"set owner = {value or '(blank)'}"
        with audit.batch(s, "delete" if action == "delete" else "update", what, "clutches") as batch_row:
            for c in zf_selected(s, ClutchRecord, form):
                if not zf_can_edit(c):
                    skipped += 1
                    continue
                if action == "delete":
                    zf_delete_clutch(s, c)
                else:
                    c.owner = value
                changed += 1
            batch_row.record_count = changed
        s.commit()
    zf_report(changed, skipped, labels[action], "clutch", "clutches")
    return redirect(url_for("zebrafish", view="clutches"))


@app.route("/zebrafish/mate", methods=["POST"])
@login_required
def zebrafish_set_up_mating():
    """Create a mating tank from a ♂×♀ pair of tanks, with a return date
    (default tomorrow). The fish come out of both tanks, so both have to
    be yours to edit (or shared breeding tanks). Optional male and female
    counts move that many fish (rows marked M / F) into the mating tank;
    Returned on the tank puts them back."""
    form = request.form
    with SessionLocal() as s:
        try:
            father_id = zf_ref(s, TankRecord, form.get("father_tank_id"), "male tank")
            mother_id = zf_ref(s, TankRecord, form.get("mother_tank_id"), "female tank")
            if not father_id or not mother_id:
                raise ZfInputError(gettext("Pick both a male and a female tank."))
            if father_id == mother_id:
                raise ZfInputError(gettext("Pick two different tanks for the male and the female."))
            return_days = zf_int(form, "return_days", "Return in (days)", default=1, lo=1, hi=14)
            males = zf_int(form, "males", "Males", default=0, lo=0, hi=1000)
            females = zf_int(form, "females", "Females", default=0, lo=0, hi=1000)
            for tid in (father_id, mother_id):
                parent = s.get(TankRecord, tid)
                if not zf_can_edit(parent):
                    raise ZfInputError(zf_denied(parent))
            father, mother = s.get(TankRecord, father_id), s.get(TankRecord, mother_id)
            for source, sex, wanted, noun in ((father, "M", males, "male"), (mother, "F", females, "female")):
                have = zf_available(source, sex)
                if wanted > have:
                    if noun == "male":
                        message = ngettext("Tank %(tank)s has %(num)s live male (fish rows marked %(sex)s); you asked for %(wanted)s.",
                                           "Tank %(tank)s has %(num)s live males (fish rows marked %(sex)s); you asked for %(wanted)s.", have, tank=source.tank_id, sex=sex, wanted=wanted)
                    else:
                        message = ngettext("Tank %(tank)s has %(num)s live female (fish rows marked %(sex)s); you asked for %(wanted)s.",
                                           "Tank %(tank)s has %(num)s live females (fish rows marked %(sex)s); you asked for %(wanted)s.", have, tank=source.tank_id, sex=sex, wanted=wanted)
                    raise ZfInputError(message)
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="clutches"))
        tank = TankRecord(
            tank_id=zf_next_code(s, TankRecord.tank_id, "MT", 3),
            purpose="mating",
            mating_father_tank_id=father_id,
            mating_mother_tank_id=mother_id,
            mating_return_at=date.today() + timedelta(days=return_days),
            owner=access.username(),
            notes=(form.get("notes") or "").strip(),
        )
        if males or females:
            # One batch, so undo puts the fish back and removes the tank.
            with audit.batch(s, "create", f"set up mating tank {tank.tank_id}", "tanks"):
                s.add(tank)
                s.flush()
                zf_take_fish(s, father, "M", males, tank)
                zf_take_fish(s, mother, "F", females, tank)
        else:
            s.add(tank)
        s.commit()
        if males or females:
            flash(gettext("Mating tank %(tank)s set up with %(males)s ♂ and %(females)s ♀ · return by %(day)s.",
                          tank=tank.tank_id, males=males, females=females, day=fmt_day(tank.mating_return_at)),
                  "success")
        else:
            flash(gettext("Mating tank %(tank)s set up · return by %(day)s.", tank=tank.tank_id,
                          day=fmt_day(tank.mating_return_at)), "success")
    return redirect(url_for("zebrafish", view="tanks"))


def zf_available(tank, sex: str) -> int:
    """Live fish of one sex ("M"/"F") in a tank."""
    return sum(f.count or 0 for f in tank.fish if fish_alive(f) and (f.sex or "").strip().upper() == sex)


def zf_take_fish(s, source, sex: str, n: int, dest) -> None:
    """Move n live fish of one sex from a tank into another, splitting a
    group row when only part of it goes. Check zf_available first."""
    rows = sorted((f for f in source.fish if fish_alive(f) and (f.sex or "").strip().upper() == sex and (f.count or 0) > 0),
                  key=lambda f: -(f.count or 0))
    for f in rows:
        if n <= 0:
            break
        take = min(n, f.count)
        if take == f.count:
            f.tank_id_fk, f.tank = dest.id, dest  # the column too, for undo
        else:
            f.count -= take
            s.add(FishRecord(tank=dest, line_id_fk=f.line_id_fk, individual_id="", count=take, sex=f.sex,
                             status=f.status, date_of_fertilization=f.date_of_fertilization,
                             clutch_id_fk=f.clutch_id_fk, genotype=f.genotype, notes=""))
        n -= take


# ---- Mating tank returned --------------------------------------------------


def zf_is_mating(tank) -> bool:
    return (getattr(tank, "purpose", "") or "").strip().lower() == "mating"


def zf_mating_home(f, mating_tank):
    """The tank a mating-tank group goes back to, as the wizard recorded
    them: males to the male tank, females to the female tank. None for a
    mixed or unknown group, or a mating tank from before the wizard."""
    sex = (f.sex or "").strip().upper()
    return {"M": mating_tank.mating_father_tank_id, "F": mating_tank.mating_mother_tank_id}.get(sex)


def zf_return_confirm(tank, groups) -> str:
    """The Returned prompt. groups: (count, home tank code) per live group."""
    if not groups:
        return gettext("Mark mating tank %(tank)s returned? It holds no fish; it is retired and kept in the history.",
                       tank=tank.tank_id)
    homes = {}
    for n, code in groups:
        homes[code or "?"] = homes.get(code or "?", 0) + n
    where = ", ".join(gettext("%(n)s to %(tank)s", n=n, tank=code) for code, n in homes.items())
    return gettext("Return mating tank %(tank)s? Fish go back: %(where)s. %(tank)s is then retired.",
                   tank=tank.tank_id, where=where)


def zf_matching_row(s, home, f):
    """A live row in the home tank that the returning group is the same
    fish as (same line, sex, age, genotype, clutch), to fold it back into:
    the other half of a row the wizard split."""
    if (f.individual_id or "").strip():
        return None
    if s.scalar(select(FishSacLog.id).where(FishSacLog.fish_id_fk == f.id).limit(1)) is not None:
        return None
    for other in home.fish:
        if (other.id != f.id and fish_alive(other) and not (other.individual_id or "").strip()
                and other.line_id_fk == f.line_id_fk and (other.sex or "") == (f.sex or "")
                and other.date_of_fertilization == f.date_of_fertilization
                and (other.genotype or "") == (f.genotype or "") and other.clutch_id_fk == f.clutch_id_fk):
            return other
    return None


def zf_return_choices(form) -> dict:
    """home_<fish row id> = tank id, from the Returned dialog."""
    out = {}
    for key, value in form.items():
        if key.startswith("home_") and key[5:].isdigit() and str(value).strip().isdigit():
            out[int(key[5:])] = int(value)
    return out


def zf_return_mating(s, mt, choices: dict):
    """Put a mating tank's live fish back in their home tanks and retire
    it. Everything is checked before anything moves. (error, summary)."""
    if not zf_is_mating(mt):
        return gettext("Tank %(tank)s is not a mating tank.", tank=mt.tank_id), ""
    if not mt.active:
        return gettext("Mating tank %(tank)s was already returned.", tank=mt.tank_id), ""
    if not zf_can_edit(mt):
        return zf_denied(mt), ""
    moves = []
    for f in [f for f in mt.fish if fish_alive(f)]:
        home_id = choices.get(f.id) or zf_mating_home(f, mt)
        home = s.get(TankRecord, home_id) if home_id else None
        what = (gettext("the %(n)s %(sex)s fish", n=f.count or 0,
                        sex=i18n.translate_value(f.sex) if f.sex else gettext("unsexed"))
                + (f" ({f.line.name})" if f.line else ""))
        if home is None:
            return gettext("Choose which tank %(what)s in %(tank)s go back to: use Returned on the tank's row.",
                           what=what, tank=mt.tank_id), ""
        if home.id == mt.id:
            return gettext("Pick a home tank for %(what)s other than %(tank)s itself.", what=what,
                           tank=mt.tank_id), ""
        if not zf_can_edit(home):
            return gettext("Can’t move fish into %(tank)s: %(reason)s", tank=home.tank_id, reason=zf_denied(home)), ""
        moves.append((f, home))
    homes = {}
    for f, home in moves:
        n = f.count or 0
        target = zf_matching_row(s, home, f)
        if target is not None:
            target.count = (target.count or 0) + n
            s.delete(f)
        else:
            f.tank_id_fk, f.tank = home.id, home  # the column too, for undo
        homes[home.tank_id] = homes.get(home.tank_id, 0) + n
    summary = ", ".join(f"{n} fish to {code}" for code, n in homes.items())
    mt.active = False
    stamp = f"Returned {date.today().isoformat()}" + (f": {summary}" if summary else "")
    mt.notes = f"{mt.notes} · {stamp}" if (mt.notes or "").strip() else stamp
    # The note above is the record's (kept in English); what is shown now
    # is in the page's language.
    return None, ", ".join(gettext("%(n)s fish to %(tank)s", n=n, tank=code) for code, n in homes.items())


@app.route("/zebrafish/tanks/<int:tank_row_id>/return", methods=["POST"])
@login_required
def zebrafish_return_mating(tank_row_id: int):
    """Returned: the mating tank's fish go home and the tank is retired
    (kept, inactive, for the record). One batch, so it can be undone."""
    with SessionLocal() as s:
        mt = s.get(TankRecord, tank_row_id)
        if mt is None:
            return zf_reply("tanks", gettext("That tank no longer exists. Reload the page."), status=404)
        code = mt.tank_id
        with audit.batch(s, "update", f"return mating tank {code}", "tanks"):
            error, summary = zf_return_mating(s, mt, zf_return_choices(request.form))
        if error:
            s.rollback()
            return zf_reply("tanks", error)
        s.commit()
    flash(gettext("Returned %(tank)s: %(summary)s. %(tank)s is retired; undo it from Batch history if that was a mistake.", tank=code, summary=summary) if summary else
          gettext("Returned %(tank)s. %(tank)s is retired; undo it from Batch history if that was a mistake.",
                  tank=code), "success")
    return zf_reply("tanks")


# ---- Sac log ---------------------------------------------------------------


@app.route("/zebrafish/sac/create", methods=["POST"])
@login_required
def zebrafish_create_sac_log():
    """Log a sacrifice by hand. Picking the fish row (tank + line/sex
    group) takes the fish off its count, refused when more than are
    there; a row left with none turns sac on the sac date, the same rule
    as setting its status. Without a row it only logs, for fish that
    never had one. One batch, so undo puts the count back."""
    form = request.form
    back = url_for("zebrafish", view="tanks") + "#sac-log"
    with SessionLocal() as s:
        fish = None
        try:
            count = zf_int(form, "count", "Count", default=1, lo=1, hi=100000)
            when = zf_date(form, "sac_date", "Sac date") or date.today()
            if when > date.today():
                raise ZfInputError(gettext("The sac date can’t be in the future."))
            fish_id = zf_ref(s, FishRecord, form.get("fish_id_fk"), "fish row")
            if fish_id is not None:
                fish = s.get(FishRecord, fish_id)
                if not zf_can_edit(fish):
                    raise ZfInputError(zf_denied(fish))
                in_tank = fish.tank.tank_id if fish.tank else gettext("no tank")
                if not fish_alive(fish):
                    raise ZfInputError(gettext("The fish row #%(row)s in %(tank)s is already %(status)s.", row=fish.id,
                                               tank=in_tank, status=i18n.translate_value(fish.status or "sac")))
                if count > (fish.count or 0):
                    raise ZfInputError(gettext("The fish row #%(row)s in %(tank)s has %(n)s fish; you can’t sac %(count)s.", row=fish.id, tank=in_tank, n=fish.count or 0,
                                               count=count))
                tank_id = fish.tank_id_fk
                line_id = fish.line_id_fk or (fish.tank.line_id_fk if fish.tank else None)
            else:
                tank_id = zf_ref(s, TankRecord, form.get("tank_id_fk"), "tank")
                if tank_id is not None and not zf_can_edit(s.get(TankRecord, tank_id)):
                    raise ZfInputError(zf_denied(s.get(TankRecord, tank_id)))
                line_id = zf_ref(s, FishLine, form.get("line_id_fk"), "line")
            entry = FishSacLog(
                tank_id_fk=tank_id,
                line_id_fk=line_id,
                fish_id_fk=fish.id if fish is not None else None,
                count=count,
                reason=(form.get("reason") or "").strip()[:200],
                recorded_by=access.username(),
            )
            if when != date.today():
                entry.recorded_at = datetime.combine(when, datetime.utcnow().time())
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(back)
        if fish is None:
            s.add(entry)
            s.commit()
            return redirect(back)
        with audit.batch(s, "update", f"sac {count} from fish row #{fish.id}", "fish"):
            s.add(entry)
            previous = fish.status
            fish.count = (fish.count or 0) - count
            if fish.count == 0:
                # None left: the row itself is sac'd. With a count of 0
                # the status rule stamps the date and logs nothing more.
                fish.status = "sac"
                fish.sac_date = when
                zf_apply_fish_status(s, fish, previous)
        s.commit()
        left = fish.count
        code = fish.tank.tank_id if fish.tank else ""
        flash(gettext("Logged %(count)s sac’d from %(tank)s (row #%(row)s): %(left)s left.", count=count, tank=code,
                      row=fish.id, left=left) if left else
              gettext("Logged %(count)s sac’d from %(tank)s (row #%(row)s): none left, so the row is marked sac.",
                      count=count, tank=code, row=fish.id), "success")
    return redirect(back)


@app.route("/zebrafish/tanks/<int:tank_row_id>/card")
@login_required
def zebrafish_tank_card(tank_row_id: int):
    """Printable tank card — one tank, single page."""
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return redirect(url_for("zebrafish", view="tanks"))
        return render_template("zebrafish_tank_card.html", tank=t, position=fish_position_label(t),
                               age_for_clutch=lambda dof: _fish_age_label(dof))


if __name__ == "__main__":
    app.run(debug=True)
