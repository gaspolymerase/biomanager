from __future__ import annotations

import gc
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import Response, abort, flash, g, get_flashed_messages, has_request_context, jsonify, redirect, render_template, request, session, url_for
from markupsafe import Markup, escape
from jinja2 import pass_context, pass_environment
from jinja2.filters import make_attrgetter
from jinja2.utils import htmlsafe_json_dumps
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.security import check_password_hash, generate_password_hash

from .db import SessionLocal
from . import access, cage_rows, cell_text, i18n, lab, mouse_rows, positions, security
from .i18n import gettext, ngettext, pgettext
from .formutil import form_changed
# Importing this registers the SQLAlchemy flush listener that writes
# audit_log rows for every tracked change; nothing here calls into it.
from . import audit  # noqa: F401
from . import sync_clock  # noqa: F401  (numbers each change to what devices keep)
from . import lab_devices
from . import undo as undo_service
from . import custom_fields, feature_library, feature_pack, plannotate, plasmid_lineage, plasmid_versions, primer_records  # noqa: E402
from .models import (
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
    Experiment,
    LitterRecord,
    InventoryItem,
    InventoryModule,
    InventoryRack,
    MouseWeight,
    MouseRecord,
    AuditEntry,
    BatchRecord,
    NotebookEntry,
    NotebookPage,
    NotebookTab,
    NotebookTopicShare,
    NotebookTemplate,
    PlasmidFile,
    PlasmidRecord,
    PlasmidSequenceVersion,
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
from .formutil import arg_int, attr_value_pattern, like_pattern
from .services import (
    csv_text,
    calculate_age_fields,
    add_notification,
    weaning_offset,
    weaning_due,
    weaning_title,
    open_litters,
    set_birth_date,
    refresh_birth_date,
    find_open_litter,
    born_day,
    CAGE_GENO_OFFSET_DAYS,
    derive_auto_calendar_items,
    fetch_ics_subscription,
    fetch_google_calendar_items,
    google_client_config,
    google_oauth_configured,
    GOOGLE_OAUTH_SCOPES,
    current_lab_usernames,
    mouse_is_active,
    sample_source_label,
    sample_sources,
    init_database,
    parse_date,
    save_uploaded_file,
    save_uploaded_image,
)


# The application object itself is in app/flask_app.py, so that a module
# of pages can hang a route on it without importing this file.
from .flask_app import app  # noqa: E402

# Python looks for objects that only point at each other once a few hundred
# new ones have piled up, and every so often walks all it holds. A long
# sheet makes hundreds of thousands (each row's dicts, its words, its
# record): those looks were a fifth of an inventory's page. Once in 10,000,
# they are a handful a page; what is freed is the same.
gc.set_threshold(10_000, 10, 10)
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
from . import rack_layout  # noqa: E402

app.register_blueprint(organism_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(stocks_bp)
app.register_blueprint(labels_bp)
app.register_blueprint(admin_racks_bp)
# A rack's new layout: what happens to what is in it (app/rack_layout.py).
app.register_blueprint(rack_layout.bp)

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


# Uploaded files, wherever the uploads folder is (app/static/uploads, a
# frozen .app/.exe's user-data folder, or BIOMANAGER_UPLOADS_DIR: a server's
# data volume). This rule wins over Flask's own /static/<path>.
# security.guard_uploads sends anyone not signed in to sign in, and
# app/uploads.py serves a file only to someone who may see a record it
# belongs to.
from . import uploads as _uploads  # noqa: E402


@app.route("/static/uploads/<path:filename>")
def _serve_uploads(filename):
    return _uploads.serve(filename)


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


# What a page needs whatever it is a page of (app/web.py): these are
# imported here so that this file's own pages, and anything that still
# reaches for them here, go on working unchanged.
from .web import (  # noqa: E402
    _get_user_directory,
    _name_color,
    admin_required,
    log_delete,
    login_required,
    next_number_retried,
    owner_badge_filter,
    stamp_updated,
)


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
    from . import contained
    acting = contained.acting_user()
    if acting is not None:
        # A proposal running the pages (or the API's own writes) on its owner's
        # behalf (app/contained.py): that person, with no cookie or token, and
        # messages in their language (English unless the proposal says).
        g.lang = contained.acting_lang() or i18n.DEFAULT
        with SessionLocal() as db_session:
            g.user = db_session.scalar(select(UserAccount).where(UserAccount.username == acting,
                                                                 UserAccount.disabled.is_(False)))
        if g.user is not None and (request.path == "/api/v1" or request.path.startswith("/api/v1/")):
            g.api_token = contained.API_TOKEN
        return
    if request.path == "/api/v1" or request.path.startswith("/api/v1/"):
        # The API is signed in by its token alone, never the session cookie (app/api.py),
        # and answers in English whatever the client's language: programs read it.
        from . import api
        g.lang = i18n.DEFAULT
        api.authenticate()
        return
    # A device that joined the lab (the desktop's relay, a phone syncing): its
    # token alone signs it in, as its person (app/lab_devices.py).
    g.device = None
    if lab_devices.authenticate():
        if g.user is not None:
            # Their language from Settings; else the window's own (Accept-Language).
            with SessionLocal() as db_session:
                chosen = i18n.preference(db_session, g.user.username)
            if chosen in i18n.LANGUAGES:
                g.lang = chosen
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
                or security.signed_out(db_session)
                or not lab_devices.browser_ok(db_session, user)):
            session.clear()
            g.user = None
            return
        g.user = user
        # Their language from Settings, read once per sign-in (app/i18n.py).
        # With none chosen there, the one picked on the sign-in page becomes
        # theirs (its own session: committing this one would detach g.user).
        if session.get("lang_for") != user.id:
            chosen, picked = i18n.preference(db_session, user.username), session.get("lang")
            if not chosen and picked in i18n.LANGUAGES:
                with SessionLocal() as pref_session:
                    i18n.set_preference(pref_session, user.username, picked)
                    pref_session.commit()
            else:
                i18n.remember(chosen)
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
# A batch made of a sheet's row saves (Set a field…): registered after
# load_current_user, so its hook knows who is saving.
from . import batch_edit  # noqa: E402
app.register_blueprint(batch_edit.bp)
# A plasmid's virtual digest and gel (app/digest_routes.py).
from . import digest_routes  # noqa: E402
app.register_blueprint(digest_routes.bp)
# Clones checked by sequencing, on a plasmid's Verify tab (app/clone_routes.py).
from . import clone_routes  # noqa: E402
app.register_blueprint(clone_routes.bp)
# The plasmid's own sequence viewer, primer design and PCR (app/sequence_routes.py).
from . import sequence_routes  # noqa: E402
app.register_blueprint(sequence_routes.bp)
# Site-directed mutagenesis from the Sequence tab (app/mutagenesis_routes.py).
from . import mutagenesis_routes  # noqa: E402
app.register_blueprint(mutagenesis_routes.bp)
# Gateway BP/LR in Assemble a plasmid (app/gateway_routes.py).
from . import gateway_routes  # noqa: E402
app.register_blueprint(gateway_routes.bp)
# Compare two plasmids, from the Overview tab (app/compare_routes.py).
from . import compare_routes  # noqa: E402
app.register_blueprint(compare_routes.bp)
# A plasmid's sequence by its Addgene number or GenBank accession (app/sequence_fetch_routes.py).
from . import sequence_fetch_routes  # noqa: E402
app.register_blueprint(sequence_fetch_routes.bp)
app.jinja_env.globals["sequence_fetch_on"] = lambda: not sequence_fetch_routes.sequence_fetch.switched_off()

# Guest passes and the gate in front of internet access (app/guests.py).
app.register_blueprint(guests.bp)

# The way in before signing in: starting or opening a lab, waiting to be
# approved, asking the admins (app/door.py).
from . import door  # noqa: E402
app.register_blueprint(door.bp)
app.jinja_env.filters["initials"] = door.initials
# The whole lab as one file, and moving it to a server (app/lab_move.py).
from . import lab_move  # noqa: E402
app.register_blueprint(lab_move.bp)
# Devices that joined the lab, each signed in on its own (app/lab_devices.py).
app.register_blueprint(lab_devices.bp)
from . import sync as lab_sync  # noqa: E402
app.register_blueprint(lab_sync.bp)
# Whether this request is the desktop app's own window (not a network visitor).
app.jinja_env.globals["this_computer"] = lambda: devices.on_this_computer()

# Copies of the lab's database on every desktop app (app/lab_copy.py).
app.register_blueprint(lab_copy.bp)
# The devices that work on the lab, and which holds its master copy (app/devices.py).
from . import devices  # noqa: E402
app.register_blueprint(devices.bp)
# Project groups: a layer between a person and the lab (app/groups.py).
from . import groups as project_groups  # noqa: E402
app.register_blueprint(project_groups.bp)
# Who may give a record to someone else, or share it (app/stewardship.py).
from . import bulk, stewardship  # noqa: E402
# A Breeding cage's litters: Litter born, Genotyping and Wean (app/litters.py).
from .litters import PUP_AGE_DAYS  # noqa: E402,F401
# What's new: a short note once after an update (app/whats_new.py).
from . import whats_new  # noqa: E402
app.register_blueprint(whats_new.bp)
# Hovering a database in the sidebar shows a look inside it (app/rail_peek.py).
from . import rail_peek  # noqa: E402
app.register_blueprint(rail_peek.bp)

# The lab's own icon at the top of the sidebar (app/lab_icon.py).
from . import lab_icon  # noqa: E402
app.register_blueprint(lab_icon.bp)
# A sheet's saved views, each person's own (app/sheet_views.py).
from . import sheet_views  # noqa: E402
app.register_blueprint(sheet_views.bp)
# The calendar's Board: to-dos as cards in lists (app/task_board.py).
from . import task_board  # noqa: E402
app.register_blueprint(task_board.bp)
# Repeats, protocols, equipment, away days and the phone feed (app/lab_calendar.py).
app.register_blueprint(lab_calendar.bp)
# The calendar in the Mac's own Calendar app, from the desktop app (app/mac_calendar.py).
from . import mac_calendar  # noqa: E402
app.register_blueprint(mac_calendar.bp)
app.jinja_env.globals["mac_calendar_offered"] = mac_calendar.offered
# Setting up a lab server from the desktop app (app/server_setup.py).
from . import server_setup  # noqa: E402
app.register_blueprint(server_setup.bp)
# Experiments on every database's animals (app/experiments.py), and what is
# done to them, planned and recorded (app/experiment_steps.py).
from . import experiments as experiment_pages  # noqa: E402
app.jinja_env.globals["experiment_readouts"] = experiment_pages.new_readout_choices
from . import experiment_steps  # noqa: E402
app.register_blueprint(experiment_pages.bp)
app.register_blueprint(experiment_steps.bp)
# Plate layouts: a plate reader's or qPCR machine's export read onto an experiment's animals (app/plates.py).
from . import plates as experiment_plates  # noqa: E402
app.register_blueprint(experiment_plates.bp)
# Signing a notebook page, which locks it (app/signatures.py).
from . import signatures as record_signatures  # noqa: E402
app.register_blueprint(record_signatures.bp)
# The public API, and Settings → AI assistant & tokens (app/api.py).
from . import api as public_api  # noqa: E402
app.register_blueprint(public_api.bp)
app.register_blueprint(public_api.pages)
# Proposed changes: what an assistant proposed, approved here (app/proposals.py).
from . import proposals as lab_proposals  # noqa: E402
app.register_blueprint(lab_proposals.bp)
# Captures: what someone wrote down or photographed at the bench, for an assistant (app/captures.py).
from . import captures as lab_captures  # noqa: E402
app.register_blueprint(lab_captures.bp)
# Assistant apps signing in to use BioManager as a connector (app/oauth.py).
from . import oauth as lab_oauth  # noqa: E402
app.register_blueprint(lab_oauth.bp)
# Send feedback and the usage report, for a pilot (app/feedback.py).
from . import feedback as lab_feedback  # noqa: E402
app.register_blueprint(lab_feedback.bp)
# Anonymous counts for BioManager's makers, once a day (app/telemetry.py).
from . import telemetry  # noqa: E402
telemetry.init_app(app)
# A newer release for a lab server: told once a day, and Update now (app/server_updates.py).
from . import server_updates  # noqa: E402
server_updates.init_app(app)
# Import from Excel into any database (app/sheet_import.py).
from . import sheet_import  # noqa: E402
app.register_blueprint(sheet_import.bp)
# Sharing, live editing, versions, comments, protocols and meetings (app/lab_notebook.py).
app.register_blueprint(lab_notebook.bp)
# The assembly wizard: digest and ligate, Gibson, Golden Gate (app/cloning.py).
from . import cloning_routes  # noqa: E402
app.register_blueprint(cloning_routes.bp)


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
            message = f"That {field} is already used. Choose another."
        else:
            message = gettext("That %(field)s is already used. Choose another.", field=field)
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
    request, and work offline in the packaged app. Within a page (base.html,
    between icon_sprite_start() and icon_sprite_end()) an icon points to a
    copy of its symbol at the end of the page instead (icons.page_sprite).
    """
    from markupsafe import Markup

    from .icons import page_sprite, resolve

    def icon(name: str, extra: str = "") -> Markup:
        classes = ("icon " + extra).strip()
        ref = resolve(name)
        drawn = g.get("icons_drawn")
        if drawn is not None:
            drawn.add(ref)
            href = f"#i-{ref}"
        else:
            href = f"/static/icons.svg#{ref}"
        return Markup(f'<svg class="{classes}" aria-hidden="true"><use href="{href}"></use></svg>')

    def icon_sprite_start() -> str:
        g.icons_drawn = set()
        return ""

    def icon_sprite_end() -> Markup:
        # Anything drawn after the page (another template in the same
        # request) points to the shared file again.
        drawn = g.pop("icons_drawn", None)
        return Markup(page_sprite(drawn)) if drawn else Markup("")

    from .icons import PICKER_ICONS
    return {"icon": icon, "icon_sprite_start": icon_sprite_start, "icon_sprite_end": icon_sprite_end,
            "picker_icons": PICKER_ICONS}


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
    proposals_menu = None
    if user is not None:
        from . import proposals as lab_proposals
        with SessionLocal() as db_session:
            unread = notify.unread_count(db_session, user.username)
            # Proposed changes is in the account menu once an assistant has sent some.
            if lab_proposals.has_any(db_session, user.username):
                proposals_menu = lab_proposals.pending_count(db_session, user.username)
    return {"current_user": user, "min_password_length": security.MIN_PASSWORD_LENGTH,
            "sign_in_providers": oidc.provider_choices(), "notification_unread": unread,
            "proposals_pending": proposals_menu,
            "lab_features": lab.request_features() if user is not None else {}}


@app.context_processor
def inject_appearance():
    """The person's app icon, and the accent colour that goes with it."""
    user = g.get("user")
    glyph, color = appearance.DEFAULT_GLYPH, appearance.DEFAULT_COLOR
    font = appearance.FONTS[0]
    icon_url, lab_icon_name = "", ""
    if user is not None:
        from . import lab_icon as lab_icons
        with SessionLocal() as db_session:
            glyph, color = appearance.get_choice(db_session, user.username)
            font = appearance.get_font(db_session, user.username)
            # The lab's own icon (app/lab_icon.py) heads the sidebar when it has one.
            icon_url = lab_icons.url(db_session)
            lab_icon_name = lab.lab_name(db_session) if icon_url else ""
    return {"app_icon_url": app_icon_url(glyph, color), "brand_css": appearance.brand_css(color), "ui_font": font,
            "lab_icon_url": icon_url, "lab_icon_name": lab_icon_name}


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
             "endpoint": "notebook", "match": ("notebook", "notebook_templates", "notebook_library")},
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
             "endpoint": "plasmids",
             "match": ("plasmids", "plasmid_detail", "plasmid_page", "feature_library_page", "cloning.wizard")},
            {"key": "new-db", "label": "Add database", "icon": "plus", "needs": "create_db",
             "hint": "Keep another kind of record: an organism, a stock collection or an inventory",
             "endpoint": "organisms.new_module", "match": ("organisms.new_module",)},
        ],
    },
]

# The foot of the sidebar: Settings, which holds the lab's pages too
# (Statistics, General, Databases, People & access, History), and a Help
# menu in the template. Utilities sits with Workspace.
NAV_FOOTER: list[dict] = [
    {"key": "settings", "label": "Settings", "icon": "settings", "endpoint": "settings",
     "match": ("settings", "admin_users", "batches_view", "audit_log_view", "guests.admin", "devices.page",
               "server_setup.page", "oauth.connect_page", "proposals.page")},
]


# The mouse colony's pages, and the helpers behind them, are in
# app/colony_routes.py. These are the names this file and other modules
# still reach for here.
from .colony_routes import (  # noqa: E402
    COLONY_VIEW_META, _merged_choices, _mouse_rack_from_form,
    cage_position_label, cage_purposes, can_edit_mouse, change_mouse,
    populate_mouse_from_form,
)


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


# Plasmids and the databases that belong with them: one area in the sidebar
# with a tab each, in the order the work flows. They are nothing like the
# lab's other inventories — a primer, a glycerol stock and a virus each name
# the plasmid they came from — so they are kept together.
from .inventory_service import PLASMID_TAB_KINDS, get_setting, set_setting  # noqa: E402
# The rail's name for that area; its first tab is still Plasmids.
MOLECULAR_BIOLOGY = "Molecular biology"


def _inventory_module_links() -> list[dict]:
    """Rail entries for the lab inventories (samples, orders, reagents…).
    The ones that go with Plasmids are left out of the rail and kept in
    `g.plasmid_tabs` for the tab strip (PLASMID_TAB_KINDS)."""
    from . import inventory_service as inventories
    from .icons import resolve as resolve_icon

    current_key = request.view_args.get("key") if request.view_args else None
    on_inventory = (request.endpoint or "").startswith("inventory.")
    links, tabs = [], []
    try:
        with SessionLocal() as db_session:
            for module in inventories.list_modules(db_session):
                if not lab.in_sidebar(module):
                    continue
                entry = {
                    "key": f"inventory:{module.key}", "label": module.label, "short": module.label,
                    "icon": resolve_icon(module.icon), "soon": None,
                    "url": url_for("inventory.module", key=module.key),
                    "active": on_inventory and current_key == module.key,
                    "personal": lab.is_personal(module),
                }
                if module.kind in PLASMID_TAB_KINDS:
                    tabs.append((PLASMID_TAB_KINDS.index(module.kind), module.position, entry))
                else:
                    links.append(entry)
    except Exception:
        return []
    g.plasmid_tabs = [entry for _kind, _position, entry in sorted(tabs, key=lambda t: t[:2])]
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


def _database_order() -> list[str]:
    """The lab's arrangement, read once a request."""
    if "database_order" not in g:
        try:
            with SessionLocal() as db_session:
                g.database_order = lab.database_order(db_session)
        except Exception:  # noqa: BLE001 — a database being set up still renders the rail
            g.database_order = []
    return g.database_order


@app.context_processor
def inject_nav():
    if g.get("user") is None:
        return {"nav_sections": [], "nav_footer": [], "tab_icon_rules": [],
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
            # The lab's own order, if it has arranged them (app/lab.py);
            # anything it has not named keeps its place at the end.
            links = lab.in_database_order(_database_order(), head + extras) + \
                [l for l in tail if l["key"] == "new-db"]
            # Plasmids holds the tabs, so it is the active database on them,
            # and once it has primers, glycerol stocks or viruses beside it
            # the rail names the whole area (unless the lab renamed it).
            for link in links:
                if link["key"] == "plasmids":
                    if any(t["active"] for t in g.get("plasmid_tabs", [])):
                        link["active"] = True
                    if g.get("plasmid_tabs") and link["label"] == "Plasmids":
                        link["label"] = link["short"] = MOLECULAR_BIOLOGY
            # Each database's tabs show its own glyph, not the generic one.
            tab_icon_rules += [(l["url"], l["icon"]) for l in extras]

        if links:
            # An admin drags the databases into the lab's order right in the
            # rail (static/shell.js); it saves through organisms.reorder.
            sortable = section["label"] == "Databases" and access.is_admin()
            sections.append({"label": section["label"], "links": links, "sortable": sortable})
    footer = [r for r in (_resolve_nav_item(i, active) for i in NAV_FOOTER) if r]
    return {
        "nav_sections": sections,
        "nav_footer": footer,
        "tab_icon_rules": tab_icon_rules,
        "colony_view_meta": COLONY_VIEW_META,
        "db_labels": g.db_labels,
        "plasmid_tabs": _plasmid_tab_strip(active),
    }


def _plasmid_tab_strip(active_endpoint: str) -> list[dict]:
    """Plasmids and its databases, as the tab strip both pages show."""
    tabs = g.get("plasmid_tabs")
    if not tabs or not lab.request_features().get("plasmids", True):
        return []
    plasmids = {"key": "plasmids", "label": g.db_labels.get("plasmids", "Plasmids"), "icon": "plasmid",
                "url": url_for("plasmids"),
                "active": active_endpoint in ("plasmids", "plasmid_detail", "plasmid_page",
                                              "feature_library_page", "cloning.wizard")}
    return [plasmids] + tabs


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


@app.after_request
def cache_versioned_assets(response):
    """A file the page names with ?v=<its time> never changes at that
    address (a new one has a new time), so the browser keeps it rather than
    asking again on every page: a page names a dozen scripts and styles,
    and a lab server can be far away."""
    if security.versioned_asset() and response.status_code in (200, 304):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


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


from .days import fmt_day  # noqa: E402  (the lab's way of writing a day)

app.jinja_env.filters["day"] = fmt_day

_BETWEEN_TAGS = re.compile(r">[ \t\r\n]+<")


@app.template_filter("tight")
def tight(html) -> Markup:
    """`{% filter tight %}` around a long sheet's rows: the template's
    indentation between one tag and the next, a third of a row's markup,
    left out. Only where it could not show: put it around rows whose cells
    keep side-by-side pieces in flex boxes, never around text, <pre> or a
    <textarea>. Values are escaped, so a > or < here is always markup."""
    return Markup(_BETWEEN_TAGS.sub("><", str(html)))


# A sheet's cells drawn as text until used, in Chromium (cell_text.text_cells_on;
# static/text-cells.js turns one back into its field). Each <td> holding just
# one field: the field's own markup goes in data-field, and the text it shows
# is drawn instead, as the field would show it (a menu's chosen label, else
# its first; an input's placeholder when empty). Gzipped, a page grows by
# about a tenth.
_ONE_FIELD = re.compile(r'<td((?:\s[^>]*)?)>\s*(<input\b[^>]*>|<select\b[^>]*>.*?</select>)\s*</td>', re.S)
# A field's attributes, each with its value: a word in a value ("see the
# form") is part of it, not an attribute of its own.
_ATTR = re.compile(r'\s([\w:-]+)(?:="([^"]*)")?')
_CHOSEN = re.compile(r'<option\b(?=[^>]*\bselected\b)[^>]*?\bvalue="([^"]*)"[^>]*>([^<]*)</option>')
_FIRST = re.compile(r'<option\b[^>]*?\bvalue="([^"]*)"[^>]*>([^<]*)</option>')
# Copied onto the text: what the page's styles and scripts read from the field.
_KEPT = ("title", "data-status", "data-tone", "data-scope", "data-sex", "data-alive-values", "data-dead-values", "data-dead-if-set")
_DISABLED = re.compile(r"\sdisabled\b")
_READONLY = re.compile(r"\sreadonly\b")


def text_cell(td_attrs: str, field: str, attrs: dict | None = None, locked: bool | None = None,
              chosen: tuple[str, str] | None = None) -> str | None:
    """A field's cell (<td{td_attrs}>{field}</td>) drawn as text, as
    as_text_cells draws it; None for a field that stays one (a tick box, a
    hidden field, one without a name). What the caller knows of the field
    it says, rather than have it read back from the tag (app/mouse_rows.py):
    `attrs`, its tag's attributes as written there; `locked`, whether it is
    disabled (then it is not read-only either); `chosen`, a menu's chosen
    option's value and words, as written."""
    tag = "select" if field.startswith("<select") else "input"
    head = field[:field.index(">") + 1] if attrs is None or locked is None else ""
    if attrs is None:
        attrs = dict(_ATTR.findall(head))
    if attrs.get("type") in ("checkbox", "hidden", "radio") or "name" not in attrs:
        return None
    if tag == "select":
        if chosen is None:
            # As the browser shows it: the chosen option, else the first.
            found = _CHOSEN.search(field) or _FIRST.search(field)
            chosen = (found.group(1), found.group(2)) if found else ("", "")
        value, shown = chosen
    else:
        value = shown = attrs.get("value", "")
    empty = not value
    if not shown and "note-cell" not in attrs.get("class", ""):
        shown = attrs.get("placeholder", "")
    readonly = False
    if locked is None:
        locked = _DISABLED.search(head) is not None
        readonly = _READONLY.search(head) is not None
    classes = " ".join(c for c in ["cell-text", attrs.get("class", ""), "is-empty" if empty else "",
                                   "is-date" if attrs.get("type") == "date" else "",
                                   "is-disabled" if locked else "",
                                   # an input's placeholder, shown for an empty one: not its value
                                   "is-placeholder" if tag != "select" and not value and shown else ""] if c)
    extra = "".join(f' {k}="{attrs[k]}"' for k in _KEPT if k in attrs)
    form = f' data-form="{attrs["form"]}"' if attrs.get("form") else ""
    # A field that can't be changed (or only shows what is worked out) stays text.
    reach = "" if locked or readonly else ' tabindex="0"'
    return (f'<td{td_attrs}><span class="{classes}"{reach} data-field="{escape(field)}" '
            f'data-sort-for="{attrs["name"]}" data-sort-value="{value}"{form}'
            f'{extra}>{shown}</span></td>')


@app.template_filter("as_text_cells")
def as_text_cells(html, on: bool = True) -> Markup:
    html = str(html)
    if not on:
        return Markup(html)
    return Markup(_ONE_FIELD.sub(lambda m: text_cell(m.group(1), m.group(2)) or m.group(0), html))


app.jinja_env.globals["text_cells_on"] = cell_text.text_cells_on


def id_url(endpoint: str, arg: str):
    """url_for(endpoint, arg=…) for a sheet's rows: built once, then each
    row's id put in its place (`{% set update_url = id_url('update_mouse',
    'mouse_row_id') %}` … `{{ update_url(row.id) }}`). A route built a row
    at a time, a few on each, was a tenth of drawing a sheet."""
    marker = 918273645
    head, found, tail = url_for(endpoint, **{arg: marker}).partition(str(marker))
    if not found:   # a converter that changed it: build each one
        return lambda value: url_for(endpoint, **{arg: value})
    return lambda value: f"{head}{value}{tail}"


app.jinja_env.globals["id_url"] = id_url


def mouse_payload_json(row: dict, locked: bool, fields: list[dict]) -> Markup:
    """What the mouse dialog opens with (_mouse_sheet.html's mouse_payload):
    the Mice sheet's rows and the cage sheet's sub-rows pass the same, as
    `|tojson` writes it. Made here: as a macro, a row at a time, it was a
    tenth of drawing the sheet. `fields`: the lab's own columns, which travel
    under the names the dialog's fields have."""
    payload = {
        "id": row["id"], "mouse_id": row["mouse_id"], "locked": locked,
        "ear_tag": row["ear_tag"], "strain": row["strain"], "gender": row["gender"],
        "transgene_1": row["transgene_1"], "transgene_2": row["transgene_2"],
        "transgene_3": row["transgene_3"], "transgene_4": row["transgene_4"],
        "cage_id": row["cage_id"], "cage_rack": row["cage_rack_id"] or "", "cage_position": row["cage_position"],
        "cage_location": row["cage_location"], "owner": row["owner"],
        "cage_rack_was": row["cage_rack_id"] or "", "cage_position_was": row["cage_position"],
        "cage_location_was": row["cage_location"],
        "litter_id": row["litter_id"], "date_of_birth": row["date_of_birth"], "status": row["status"],
        "date_of_death": row["date_of_death"], "note": row["note"]}
    for fd in fields:
        payload["attr_" + fd["key"]] = row["attrs"].get(fd["key"], "")
    policies = app.jinja_env.policies
    return htmlsafe_json_dumps(payload, dumps=policies["json.dumps_function"], **policies["json.dumps_kwargs"])


app.jinja_env.globals["mouse_payload_json"] = mouse_payload_json


@app.template_filter("stubs_json")
def stubs_json(stubs: list[dict]) -> Markup:
    """A long sheet's stubs (mouse_stubs, inventory_routes' stubs…) for its
    <script type="application/json" data-dt-stubs>, safe in the page as
    `|tojson` writes them, but with each row's keys in the order made and no
    spaces: sorting thousands of rows' keys took longer than writing them."""
    return htmlsafe_json_dumps(stubs, dumps=json.dumps, default=app.json.default, separators=(",", ":"))


def mouse_rows_html(rows, sheet, custom_columns, custom_columns_all, fragment, as_text, custom_cell,
                    icon) -> Markup:
    """The mouse sheet's rows (app/mouse_rows.py), for _mouse_rows.html,
    which passes its own helpers: _sheet.html's custom_cell and icon().
    `as_text`: cell_text.text_cells_on(), the cells drawn as text."""
    return mouse_rows.html(rows, sheet, custom_columns, custom_columns_all, fragment, as_text,
                           custom_cell=custom_cell, icon=icon, owner_badge=owner_badge_filter,
                           payload=mouse_payload_json,
                           duplicate_url=id_url("duplicate_mouse", "mouse_row_id"),
                           delete_url=id_url("delete_mouse", "mouse_row_id"),
                           text_cell=text_cell, text_cells=as_text_cells)


app.jinja_env.globals["mouse_rows_html"] = mouse_rows_html


@pass_context
def cage_rows_html(ctx, rows, fragment) -> Markup:
    """The cage sheet's rows and their detail rows (app/cage_rows.py), for
    _cage_rows.html, from what the page's context has: the racks, the
    purposes, the mouse sheet's choices and the template's helpers. In
    Chromium their cells are drawn as text (cell_text.text_cells_on), by the
    writer itself, which knows each field's attributes."""
    policies = app.jinja_env.policies
    return cage_rows.html(
        rows, fragment=fragment, racks=ctx["cage_racks"]["racks"], purposes=ctx["cage_sheet"]["purpose_choices"],
        mouse_sheet=ctx["mouse_sheet"], end_statuses=ctx["end_statuses"], custom_columns_all=ctx["custom_columns_all"],
        wean_offset_days=ctx["wean_offset_days"], icon=ctx["icon"], owner_badge=owner_badge_filter,
        payload=mouse_payload_json,
        json_attr=lambda value: htmlsafe_json_dumps(value, dumps=policies["json.dumps_function"],
                                                    **policies["json.dumps_kwargs"]),
        url_for=url_for, id_url=id_url, group_choices=project_groups.choices,
        as_text=cell_text.text_cells_on(), text_cell=text_cell)


app.jinja_env.globals["cage_rows_html"] = cage_rows_html
app.jinja_env.globals["mouse_row_forms_html"] = lambda rows: mouse_rows.forms_html(
    rows, id_url("update_mouse", "mouse_row_id"))


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


@app.template_filter("natural_sort")
@pass_environment
def natural_sort_filter(environment, rows, attribute: str):
    """Like Jinja's sort(attribute=...), with numbers as numbers: tank 2
    before tank 10."""
    get = make_attrgetter(environment, attribute)
    return positions.natural_sorted(rows, key=get)


@app.template_filter("group_in_order")
@pass_environment
def group_in_order_filter(environment, rows, attribute: str) -> list:
    """Like Jinja's groupby, but in the order the rows come (boxes in
    freezer order, −80 before −20), not re-sorted as text."""
    get = make_attrgetter(environment, attribute)
    groups: dict = {}
    for row in rows:
        groups.setdefault(get(row), []).append(row)
    return list(groups.items())


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


# A person's initials in their colour: app/web.py, where the colony's and
# the zebrafish's pages reach it as a function too.
app.template_filter("owner_badge")(owner_badge_filter)


@app.template_filter("owner_with_badge")
def owner_with_badge_filter(name: str) -> Markup:
    """Badge + name, useful when both are needed in compact rows."""
    if not name:
        return Markup("")
    badge = owner_badge_filter(name)
    label = owner_short_filter(name)
    return Markup(f'<span class="owner-cell">{badge}<span class="owner-cell-name">{escape(label)}</span></span>')


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
    """The first page someone sees before signing in (app/door.py): the
    sign-in to this lab, or, before there is any account, starting a lab."""
    return door.front()


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

        # ---- Upcoming weanings: litters whose weaning day (DOB + the lab's
        # weaning day, as the cage cards use) is within ±7 days -------
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

    # The lab's databases as the sidebar lists them (app/home_layouts.py):
    # each brings its own cards and Counts tile.
    from . import organism_service, stock_service
    from .icons import resolve as resolve_icon
    features = lab.request_features()
    with SessionLocal() as db_session:
        databases = home_layouts.lab_databases(db_session, features)
        lab_cards = home_layouts.lab_cards(databases)

        # Each fly or worm database's work due soon: flips, egg collections,
        # shifts, scoring; and each animal database's schedule (wean, retire…).
        db_cards, due_count = {}, {}
        for db in databases:
            module = db["module"]
            if db["kind"] == "stock":
                mv = stock_service.view(module)
                rows = [{"title": item["title"], "due": item["due"], "overdue": item["overdue"],
                         "is_today": item["today"]}
                        for item in stock_service.schedule(db_session, mv, today, horizon=2)]
                rows.sort(key=lambda i: i["due"])
                db_cards[f"stock:{mv.key}"] = {
                    "label": mv.label, "icon": resolve_icon(module.icon), "worms": module.kind == "worm",
                    "url": url_for("stocks.module", key=mv.key, view="schedule"), "rows": rows}
                due_count[db["key"]] = len(rows)
        organism_due = organism_service.home_due(db_session, horizon_days=2)
        for db in databases:
            card = f"org:{db['module'].key}" if db["kind"] == "organism" else None
            if card and any(c[0] == card for c in lab_cards):
                rows = [d for d in organism_due if d["key"] == db["module"].key]
                db_cards[card] = {
                    "label": db["module"].label, "icon": resolve_icon(db["module"].icon or "paw"),
                    "url": url_for("organisms.module", key=db["module"].key, view="schedule"), "rows": rows}
                due_count[db["key"]] = len(rows)
        tiles = home_layouts.count_tiles(
            db_session, databases, g.user.username, builtin_labels(),
            {"total_mice": total_mice, "active_mice": active_mice, "my_mice": my_mice,
             "notebook_pages": notebook_pages}, due_count, bool(features.get("notebook", True)))
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
        setup_needed = g.user.role == "admin" and not lab.setup_done(db_session)
        if g.user.role == "admin":
            notify.settle_signups(db_session)
        for_you = [{"id": n.id, "title": n.title, "created_at": n.created_at, "actor": n.actor or ""}
                   for n in notify.recent(db_session, g.user.username, limit=5, unread_only=True)]

    # Classic (the cards above) or one of the other layouts (app/home_layouts.py),
    # which draw the same work from one agenda.
    zebrafish_due = zebrafish_home_summary()
    with SessionLocal() as db_session:
        home_cards = home_layouts.get_cards(db_session, g.user.username, lab_cards)
        offered = home_layouts.offered_cards(features, {
            "has_orders": has_orders, "has_restock": has_restock,
            "has_animals": features.get("colony", True) or features.get("zebrafish", True)}, lab_cards)
        shown = {c["key"] for c in offered} - set(home_cards["hidden"])
        # Overview's bento always has the databases and the notebook's pages.
        extra = _home_extra_cards(db_session, shown | {"notebook"}, today)
        # Animals that may need a look (app/welfare.py): a member's own by
        # default, the lab's for animal care; ?animals= switches.
        from . import welfare
        welfare_view =(welfare.home_view(db_session, g.user, request.args.get("animals"), today, features)
                        if "welfare" in shown else None)
        home_layout = home_layouts.get_layout(db_session, g.user.username)
        layout_view = None
        span = home_layouts.span_arg(request.args.get("span"))
        # Overview's This week reaches Sunday; By place pulls the next few days.
        horizon = span if home_layout == "tracks" else (7 if home_layout == "classic" else home_layouts.PULL_DAYS)
        agenda, track_meta = home_layouts.build_agenda(
            db_session, today, horizon, lab.request_features(), zebrafish_due,
            colony_label=builtin_labels().get("colony", "Mouse colony"))
        if home_layout == "tracks":
            layout_view = home_layouts.tracks_view(agenda, track_meta, today, span)
        elif home_layout != "classic":
            layout_view = home_layouts.freezer_view(db_session, agenda, today, g.user.username)
        # The daily brief at the top: today's work in a few words.
        brief = home_layouts.daily_brief(agenda, today, welfare_view["count"] if welfare_view else 0)
        week = home_layouts.week_view(agenda, track_meta, today, extra.get("todos"))
        needs = home_layouts.needs_look(welfare_view)
        week_end = today + timedelta(days=6 - today.weekday())
        colony_due = sum(1 for item in agenda if item["track"].startswith("colony:") and item["due"] <= week_end)
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
        setup_needed=setup_needed,
        for_you=for_you,
        zebrafish_due=zebrafish_due,
        db_cards=db_cards,
        tiles=tiles,
        shown_tiles=home_layouts.shown_tiles(tiles, home_cards["tiles"]),
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
        welfare_view=welfare_view,
        brief=brief,
        week=week,
        needs=needs,
        colony_due=colony_due,
        today_kicker=i18n.strftime(today, "%A %d %B"),
        weanings=weanings,
        geno_queue=geno_queue,
        orders_list=orders_list,
        events_list=events_list,
        restock=restock,
        wean_offset_days=weaning_offset(),
    )


# Calculators the Home card links to (static/bench-calcs.js ids).
HOME_CALCULATORS = [("dilute", "Dilute (C₁V₁)"), ("make", "Make a solution"), ("a260", "DNA / RNA concentration"),
                    ("count", "Count cells"), ("seed", "Seed plates"), ("rcf", "rpm ↔ × g"),
                    ("buffer", "Buffer at a pH"), ("mastermix", "Master mix")]


def _home_extra_cards(db_session, shown: set, today: date) -> dict:
    """What Home's optional cards show, read only when they are on."""
    me = g.user.username
    out = {"todos": [], "bookings": [], "recent_pages": [], "recent_plasmids": [], "home_calculators": HOME_CALCULATORS}
    if "plasmids" in shown:
        # Your newest plasmids first, then the lab's; a plasmid with no
        # sequence yet is marked, as the Plasmids page marks it.
        rows = db_session.scalars(select(PlasmidRecord).order_by(
            (PlasmidRecord.owner == me).desc(),
            func.coalesce(PlasmidRecord.updated_at, PlasmidRecord.created_at).desc(),
            PlasmidRecord.id.desc()).limit(6)).all()
        out["recent_plasmids"] = [{"number": p.plasmid_id, "name": p.name, "owner": p.owner,
                                   "url": url_for("plasmid_page", number=p.plasmid_id), "has_sequence": bool(p.full_sequence),
                                   "when": p.updated_at or p.created_at} for p in rows]
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
    """Customize Home: which cards show, their order, which are full width,
    and which Counts tiles there are."""
    with SessionLocal() as db_session:
        if request.form.get("reset") == "1":
            home_layouts.reset_cards(db_session, g.user.username)
        else:
            order = request.form.getlist("order")
            shown = set(request.form.getlist("show"))
            # The tiles only when the dialog listed them: none ticked is a choice too.
            tiles = request.form.getlist("tile") if request.form.get("tiles") == "1" else None
            cards = home_layouts.lab_cards(home_layouts.lab_databases(db_session, lab.request_features()))
            home_layouts.set_cards(db_session, g.user.username, order,
                                   [k for k in order if k not in shown], request.form.getlist("wide"),
                                   tiles, cards)
        db_session.commit()
    return redirect(url_for("home_dashboard"))


ALLOWED_LANDING_ENDPOINTS = {"colony", "notebook", "calendar", "orders", "samples", "plasmids"}


@app.route("/home/layout", methods=["POST"])
@login_required
def set_home_layout():
    """Switch Home between Overview, Timeline and By place; kept per person."""
    with SessionLocal() as db_session:
        home_layouts.set_layout(db_session, g.user.username, request.form.get("layout", ""))
        db_session.commit()
    return redirect(url_for("home_dashboard"))


@app.route("/design")
@admin_required
def design_gallery():
    """Every shared piece of the interface on one page, in its real styles
    (docs/DEVELOPMENT.md → Interface design): to check a change against all
    of them at once, in light, dark and on a phone. For maintainers, so in
    English only."""
    return render_template("design_gallery.html")


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
            return door.signin(429)
        if security.https_required_but_missing():
            flash(gettext("This server only accepts sign-ins over HTTPS. Open it with an https:// address."), "error")
            return door.signin()
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
                if request.form.get("remember") == "1":
                    # Home writes it to this browser's list (templates/base.html), so
                    # the next sign-in here offers the account by name.
                    session["remember_account"] = {"u": user.username, "n": user.display_name or user.username}
                flash(gettext("Welcome, %(name)s.", name=user.display_name or user.username), "success")
                return redirect(security.safe_next(request.args.get("next")) or landing_url(user))
    if request.method == "GET" and no_accounts_yet():
        return redirect(url_for("index"))
    return door.signin()


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
                # Only the fields the form sent: Profile and "When you sign
                # in" are separate forms on the page, each saved as it changes.
                form = request.form
                if "display_name" in form:
                    user.display_name = form.get("display_name", "").strip()
                if "short_name" in form:
                    user.short_name = form.get("short_name", "").strip()[:5]
                if "email" in form:
                    user.email = form.get("email", "").strip()
                if "role_title" in form:
                    user.role_title = form.get("role_title", "").strip()
                if "default_landing" in form:
                    landing = form.get("default_landing", "").strip()
                    user.default_landing = landing if landing in ALLOWED_LANDING_ENDPOINTS else ""
                if "home_layout" in form:
                    home_layouts.set_layout(db_session, user.username, form.get("home_layout", ""))
                if "ui_font" in form:
                    appearance.set_font(db_session, user.username, form.get("ui_font", ""))
                db_session.commit()
                if request.headers.get("X-Autosave") == "1":
                    return jsonify({"ok": True})
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
                    switch = notify.FOLLOWS.get(category, category)
                    notify.set_wants(db_session, user, category, request.form.get(f"notify_{switch}") == "1")
                db_session.commit()
                if request.headers.get("X-Autosave") == "1":
                    return jsonify({"ok": True})
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
            # Back to the pane the form was on (templates/settings.html).
            pane = {"appearance": "appearance", "language": "appearance", "notifications": "notifications",
                    "password": "security"}.get(action, "profile")
            return redirect(url_for("settings", _anchor=pane))
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
            **{f"notify_{c}": notify.wants(db_session, user, c) for c in notify.CATEGORIES},
            "has_password": security.has_password(user),
        }
        linked = db_session.scalars(select(UserIdentity).where(UserIdentity.user_id_fk == user.id)
                                    .order_by(UserIdentity.created_at)).all()
        identities = [{"id": i.id, "provider": oidc.LABELS.get(i.provider, i.provider.title()),
                       "provider_key": i.provider, "email": i.email,
                       "last_used": local_time(i.last_login_at).strftime("%Y-%m-%d") if i.last_login_at else ""}
                      for i in linked]
    from . import mailer, settings_lab

    with SessionLocal() as db_session:
        lab_data = settings_lab.panes(db_session) if not g.user.expires_at else None
        updates = server_updates.state(db_session) if g.user.role == "admin" else None
        heartbeat = telemetry.status(db_session) if lab_data else None
    from . import whats_new
    from .stocks import PRESETS as STOCK_PRESETS
    return render_template(
        "settings.html",
        lab_data=lab_data, updates=updates, heartbeat=heartbeat, version_info=whats_new.version_info(), lab_features=lab.FEATURES, stock_choices=lab.STOCK_CHOICES,
        inventory_choices=lab.INVENTORY_CHOICES, member_permissions=lab.MEMBER_PERMISSIONS,
        date_styles=lab.DATE_STYLES, timezones=lab.timezone_names(), server_timezone=lab.server_timezone(),
        wean_offset_days=weaning_offset(), roles=access.ROLES, group_switches=project_groups.SWITCHES,
        group_parts=project_groups.PARTS, stock_default_labels={k: STOCK_PRESETS[k]["label"] for k in lab.STOCK_CHOICES},
        user_settings=user_data,
        landing_choices=sorted(ALLOWED_LANDING_ENDPOINTS),
        home_layout_choices=home_layouts.LAYOUTS,
        icon_glyphs={key: label for key, (label, _draw) in appearance.GLYPHS.items()},
        icon_palettes=appearance.PALETTES,
        icon_urls={f"{g}/{c}": app_icon_url(g, c) for g in appearance.GLYPHS for c in appearance.PALETTES},
        mail_configured=mailer.is_configured(),
        identities=identities,
        notification_categories=notify.SHOWN,
    )


@app.route("/settings/lab", methods=["POST"])
@admin_required
def settings_lab_save():
    """Settings → Lab: one pane's form (General, Databases, What members may
    do, one other database switched on or off, or the anonymous counts),
    saved as it changes."""
    section = request.form.get("section", "")
    autosave = request.headers.get("X-Autosave") == "1"
    anchor = {"general": "general", "databases": "databases", "permissions": "member-rights",
              "module": "databases", "heartbeat": "general"}.get(section)
    if anchor is None:
        abort(400)
    with SessionLocal() as db_session:
        if section == "heartbeat":
            # The anonymous daily counts (app/telemetry.py), also on the Usage report.
            telemetry.set_lab_on(db_session, request.form.get("enabled") == "1")
            db_session.commit()
            if autosave:
                return jsonify({"ok": True})
            return redirect(url_for("settings", _anchor=anchor))
        if section == "module":
            module = lab.set_module_enabled(db_session, request.form.get("kind", ""), request.form.get("key", ""),
                                            request.form.get("enabled") == "1")
            if module is None:
                abort(404)
            db_session.commit()
            if autosave:
                return jsonify({"ok": True})
            return redirect(url_for("settings", _anchor=anchor))
        zone = (request.form.get("lab_timezone") or "").strip()
        if section == "general" and zone and not lab.valid_timezone(zone):
            message = gettext("“%(zone)s” is not a time zone BioManager knows; the time zone was left as it was. Pick one from the list, such as America/New_York.", zone=zone)
            if autosave:
                return jsonify({"ok": False, "error": message}), 400
            flash(message, "error")
        switched_on = lab.apply_survey(db_session, request.form, g.user.username, sections=(section,))
        if switched_on:
            notify.tell_lab(db_session, g.user.username, "%(who)s added %(what)s for the lab",
                            link=url_for("home_dashboard"),
                            values={"who": g.user.display_name or g.user.username, "what": ", ".join(switched_on)})
        db_session.commit()
    if autosave:
        return jsonify({"ok": True})
    flash(gettext("Lab setup saved."), "success")
    return redirect(url_for("settings", _anchor=anchor))


@app.route("/settings/updates", methods=["POST"])
@admin_required
def settings_updates():
    """Settings → Version → Updates, on a lab server: the daily
    check on or off, Check now, and Update now (app/server_updates.py)."""
    action = request.form.get("action", "")
    autosave = request.headers.get("X-Autosave") == "1"
    with SessionLocal() as db_session:
        if action == "switch":
            server_updates.set_checking(db_session, request.form.get("check") == "1")
            db_session.commit()
            if autosave:
                return jsonify({"ok": True})
        elif action == "check":
            result = server_updates.check(db_session, manual=True)
            if result["state"] == "newer":
                flash(gettext("BioManager %(version)s is available.", version=result["release"]["version"]), "success")
            elif result["state"] == "current":
                flash(gettext("This server runs the newest BioManager."), "success")
            else:
                flash(gettext("BioManager couldn't reach GitHub to check. Try again later."), "error")
        elif action == "install":
            release = server_updates.latest(db_session)
            if release is None:
                flash(gettext("This server runs the newest BioManager."), "success")
            elif not server_updates.request_update(g.user.username, release["version"]):
                flash(gettext("This server can't update itself yet: run sudo host/install.sh on it once."), "error")
            else:
                app.logger.info("%s asked the server to update to BioManager %s", g.user.username, release["version"])
        else:
            abort(400)
    return redirect(url_for("settings", _anchor="updates"))


@app.route("/settings/updates/status")
@admin_required
def settings_updates_status():
    """How far the server's update has got, for the page to follow."""
    return jsonify({"current": server_updates.current(), "progress": server_updates.progress()})


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
                ["mouse_id", "ear_tag", "strain", "gender", "genotype", "status", "owner", "cage_id", "litter_id", "dob", "dod", "note"],
                *([m.mouse_id, m.ear_tag, m.strain, m.gender, m.genotype, m.status, m.owner, m.cage.cage_id if m.cage else "",
                   m.litter.litter_id if m.litter else "", iso(m.litter.date_of_birth) if m.litter else "",
                   iso(m.date_of_death), m.note] for m in mice)]))

            cages = positions.natural_sorted(db_session.scalars(
                select(CageRecord).where(CageRecord.owner == username)).all(), key=lambda c: c.cage_id)
            zf.writestr("cages.csv", csv_text([
                ["cage_id", "purpose", "location", "rack", "row", "column", "shared", "litter_born", "notes"],
                *([c.cage_id, c.purpose, c.cage_location, c.rack.name if c.rack else "", c.rack_row or "",
                   c.rack_col or "", (project_groups.label(True, c.share_group_id, lab="yes") if access.is_shared_cage(c) else "no"),
                   "; ".join(iso(cl.born) for cl in open_litters(c)) or iso(c.date_give_birth), c.notes]
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
            from .sequence_parser import to_genbank
            for p in plasmids:
                if not p.full_sequence:
                    continue
                try:
                    annotations = json.loads(p.features_json) if p.features_json else []
                except json.JSONDecodeError:
                    annotations = []
                safe = _re.sub(r"[^\w\-.]+", "_", p.name or "").strip("_")
                zf.writestr(f"plasmids/{p.plasmid_id}{'-' + safe if safe else ''}.gb",
                            to_genbank(p.name or f"plasmid-{p.plasmid_id}", p.full_sequence, bool(p.is_circular), annotations))
            # The files kept with each of their plasmids: traces, gel photos.
            from pathlib import Path as _Path

            # The folder save_uploaded_file writes to (the tests point it
            # at a temp folder), not paths.uploads_dir().
            from . import services as _services
            for p in plasmids:
                safe = _re.sub(r"[^\w\-.]+", "_", p.name or "").strip("_")
                folder = f"plasmids/{p.plasmid_id}{'-' + safe if safe else ''}-files"
                for kept in db_session.scalars(select(PlasmidFile).where(PlasmidFile.plasmid_row_id == p.id)
                                               .order_by(PlasmidFile.id)):
                    source = _services.UPLOAD_DIR / _Path(kept.path).name
                    if not source.is_file():
                        continue
                    name = _re.sub(r"[^\w\-. ]+", "_", kept.name or source.name).strip() or source.name
                    zf.write(source, f"{folder}/{name}")

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
    """The colony at a glance is Settings → Statistics now."""
    return redirect(url_for("settings", _anchor="stats"))


@app.route("/admin/users")
@admin_required
def admin_users():
    """The lab's accounts are in Settings → People & access."""
    return redirect(url_for("settings", _anchor="people"))


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
@admin_required
def admin_toggle_role(user_id: int):
    with SessionLocal() as db_session:
        target = db_session.get(UserAccount, user_id)
        if target is None:
            flash(gettext("User not found."), "error")
            return redirect(url_for("settings", _anchor="people"))
        if target.id == g.user.id:
            flash(gettext("You cannot change your own role."), "error")
            return redirect(url_for("settings", _anchor="people"))
        if target.role == "pending":
            flash(gettext("Approve %(user)s before changing their role.", user=target.username), "error")
            return redirect(url_for("settings", _anchor="people"))
        wanted = request.form.get("role", "")
        if wanted and wanted not in access.ROLES:
            flash(gettext("That isn't a role."), "error")
            return redirect(url_for("settings", _anchor="people"))
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
                        "You can now change the lab's setup, approve sign-ups and edit any record.",
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
    return redirect(referrer if referrer.startswith(request.host_url) else url_for("settings", _anchor="people"))


@app.route("/admin/users/<int:user_id>/disable", methods=["POST"])
@admin_required
def admin_toggle_disabled(user_id: int):
    with SessionLocal() as db_session:
        target = db_session.get(UserAccount, user_id)
        if target is None:
            flash(gettext("User not found."), "error")
            return redirect(url_for("settings", _anchor="people"))
        if target.id == g.user.id:
            flash(gettext("You cannot disable your own account."), "error")
            return redirect(url_for("settings", _anchor="people"))
        if target.role == "pending":
            # A sign-up waiting for approval: enabling it is the approval.
            target.role = "member"
            target.disabled = False
            db_session.commit()
            notify.settle_signups(db_session)
            flash(gettext("%(user)s approved. They can sign in now.", user=target.username), "success")
            return redirect(url_for("settings", _anchor="people"))
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
    return redirect(url_for("settings", _anchor="people"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_reset_password(user_id: int):
    new_password = (request.form.get("new_password") or "").strip()
    with SessionLocal() as db_session:
        target = db_session.get(UserAccount, user_id)
        if target is None:
            flash(gettext("User not found."), "error")
            return redirect(url_for("settings", _anchor="people"))
        if problem := security.password_problem(new_password, target.username):
            flash(problem, "error")
            return redirect(url_for("settings", _anchor="people"))
        target.password_hash = generate_password_hash(new_password)
        db_session.commit()
        # Their sessions end (session_stamp); an admin resetting their own
        # password stays signed in here.
        if target.id == g.user.id:
            session["auth"] = security.session_stamp(target)
        flash(gettext("Password reset for %(user)s. Their other sessions have been signed out.", user=target.username),
              "success")
    return redirect(url_for("settings", _anchor="people"))


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
                        # The lab named on the way here (app/door.py), and its admin
                        # signed straight in, on to the setup survey.
                        lab_name = (session.pop(door.NEW_LAB_NAME, "") or "").strip()
                        if lab_name:
                            set_setting(db_session, "lab_name", lab_name)
                            db_session.commit()
                        security.clear_setup_code()
                        security.start_session(user)
                        flash(gettext("Admin account created."), "success")
                        return redirect(url_for("lab.setup"))
                    session[door.PENDING] = username
                    flash(gettext("Account created. A lab admin needs to approve it before you can sign in."), "success")
                    return redirect(url_for("door.joined"))
    if first:
        return door.render("door/admin.html", steps=door.setup_steps("admin"), needs_setup_code=needs_code,
                           lab_name=session.get(door.NEW_LAB_NAME, ""), lab_set_aside=lab_set_aside())
    return door.render("door/join.html")


def no_accounts_yet() -> bool:
    with SessionLocal() as db_session:
        return db_session.scalar(select(func.count(UserAccount.id))) == 0


def lab_set_aside() -> str:
    """Where the lab set aside at this start went (paths.set_lab_aside), until
    the new lab has its first account: the sign-in page says so."""
    folder = app.config.get("LAB_SET_ASIDE") or ""
    return folder if folder and no_accounts_yet() else ""


@app.route("/start-new-lab", methods=["GET", "POST"])
def start_new_lab():
    """The desktop app's way out of a lab nobody can sign in to (the admin's
    password is lost, or someone else made it): the next start sets this lab
    aside, whole, in data/old-labs/, and begins an empty one whose first
    account is its admin. Only for the person at this computer."""
    if not devices.on_this_computer():
        abort(404)
    from . import paths
    if request.method == "POST":
        if request.form.get("action") == "keep":
            paths.ask_for_new_lab(False)
            flash(gettext("Nothing changes: this lab stays as it is."), "success")
            return redirect(url_for("login"))
        paths.ask_for_new_lab()
        return redirect(url_for("start_new_lab"))
    return render_template("start_new_lab.html", asked=paths.new_lab_asked_for(),
                           folder=str(paths.old_labs_dir()),
                           restore_url=lab.guide_url().replace("guide.html", "guide/restore-a-backup.html"))


@app.route("/restore-lab", methods=["GET", "POST"])
def restore_lab():
    """The desktop app: bring back a lab set aside by Start a new lab (or by
    an earlier restore). The next start sets the present lab aside first, so
    this can be undone the same way. Only for the person at this computer."""
    if not devices.on_this_computer():
        abort(404)
    from . import paths
    if request.method == "POST":
        if request.form.get("action") == "keep":
            paths.ask_to_restore(None)
            flash(gettext("Nothing changes: this lab stays as it is."), "success")
            return redirect(url_for("index"))
        if not paths.ask_to_restore(request.form.get("folder", "")):
            flash(gettext("That lab is no longer in the folder."), "error")
        return redirect(url_for("restore_lab"))
    return door.render("door/restore.html", labs=paths.old_labs(), asked=paths.restore_asked_for(),
                       folder=str(paths.old_labs_dir()), signed_in=g.user is not None)


@app.route("/logout", methods=["POST"])
def logout():
    lab_devices.forget_browser()
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
    return access.can_edit(t, user, shared=project_groups.record_editable(t, user, what="todos"))


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
    # A to-do with no day shows on today; one kept in a list on the Board
    # (app/task_board.py) stays there.
    tk_q = select(TaskItem).where(
        (TaskItem.due_date.is_(None) & (TaskItem.board_list == ""))
        | ((TaskItem.due_date >= start) & (TaskItem.due_date <= end)))
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


# The notebook's libraries, each a page of its own beside the sidebar:
# key -> (title, icon). The drawer beside a page (/protocol) still lists
# protocols to insert.
NOTEBOOK_LIBRARIES = {
    "protocols": ("Protocols", "protocol"),
    "recipes": ("Recipes", "flask"),
    "meetings": ("Meetings", "users"),
    "reading": ("Reading", "book-open"),
}


@app.route("/notebook")
@login_required
def notebook():
    return _notebook_page()


@app.route("/notebook/<any(protocols, recipes, meetings, reading):library>")
@login_required
def notebook_library(library: str):
    """The lab's protocols or recipes, or the papers and talks read, to look
    through, open, add to and sort into folders. Meetings, their days and
    who presents, are kept in the calendar (static/calendar-meetings.js)."""
    if library == "meetings":
        return redirect(url_for("calendar", meetings="all"))
    return _notebook_page(library)


# How a topic's page lists its pages: ?sort= -> its label.
NOTEBOOK_SORTS = {"edited": "Last edited", "name": "Name", "order": "Your order"}


def _notebook_page(library: str | None = None):
    selected_tab_id = arg_int("tab", None)
    selected_page_id = arg_int("page", None) if library is None else None
    with SessionLocal() as db_session:
        tab_rows = db_session.scalars(
            _notebook_owner_filter(select(NotebookTab)).order_by(NotebookTab.position, NotebookTab.id)
        ).all()
        selected_page = None
        role = None
        if selected_page_id is not None:
            candidate = db_session.get(NotebookPage, selected_page_id)
            role = lab_notebook.role_for(db_session, candidate) if candidate is not None else None
            if role is not None:
                selected_page = candidate
        # Other people's topics shared with this person (lab_notebook.topic_shares).
        shared_rows = lab_notebook.shared_topic_rows(db_session)
        selected_tab = None
        if selected_page is not None:
            selected_tab = next((tab for tab in [*tab_rows, *shared_rows] if tab.id == selected_page.tab_id_fk
                                 and (role == "owner" or tab in shared_rows)), None)
        # No page asked for: a topic's page (?tab=), or the notebook's home.
        if selected_page is None and library is None and selected_tab_id is not None:
            selected_tab = next((tab for tab in [*tab_rows, *shared_rows] if tab.id == selected_tab_id), None)

        side = lab_notebook.sidebar(db_session)
        kinds = side.pop("kinds_by_page")
        parents = lab_notebook.parent_ids([page.id for tab in [*tab_rows, *shared_rows] for page in tab.pages])
        tabs_data = lab_notebook.topic_tree(db_session, tab_rows, kinds, parents)
        shared_data = lab_notebook.topic_tree(db_session, shared_rows, kinds, parents) if shared_rows else []
        shared_roles = lab_notebook.topic_shares(db_session)
        owners = {tab.id: tab.owner_username for tab in shared_rows}
        owner_names = lab_notebook.display_names(db_session, list(owners.values()))
        for node in shared_data:
            node.update(shared=True, role=shared_roles.get(node["id"], "view"),
                        owner_name=owner_names.get(owners[node["id"]], owners[node["id"]]))
        by_id = {node["id"]: node for node in [*tabs_data, *shared_data]}
        # The open topic and the topics it sits in start unfolded.
        open_topics = set()
        here = selected_tab.id if selected_tab is not None else None
        while here is not None and here not in open_topics:
            open_topics.add(here)
            here = by_id[here]["parent_id"] if here in by_id else None
        topic_view = home = None
        sort = request.args.get("sort") if request.args.get("sort") in NOTEBOOK_SORTS else "edited"
        if selected_page is None and library is None and selected_tab is not None:
            node = by_id[selected_tab.id]
            pages = {page.id: page for page in selected_tab.pages}
            top = [pages[p["id"]] for p in node["pages"]]
            if sort == "edited":
                top.sort(key=lambda page: page.updated_at or datetime.min, reverse=True)
            elif sort == "name":
                top.sort(key=lambda page: (page.title or "").casefold())
            topic_view = {**node, "cards": lab_notebook.page_cards(db_session, top), "sort": sort,
                          "trail": [by_id[i] for i in _topic_trail(by_id, node["id"])]}
        elif selected_page is None and library is None:
            favorites = lab_notebook.favorite_ids(db_session)
            recent = db_session.scalars(lab_notebook.accessible_filter(
                select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id))
                .order_by(NotebookPage.updated_at.desc()).limit(8)).all()
            starred = {page.id: page for page in db_session.scalars(
                select(NotebookPage).where(NotebookPage.id.in_(favorites)))} if favorites else {}
            home = {"favorites": lab_notebook.page_cards(db_session, [starred[i] for i in favorites if i in starred]),
                    "recent": lab_notebook.page_cards(db_session, recent),
                    "topics": [node for node in tabs_data if node["depth"] == 0]}
        selected_page_data = (lab_notebook.page_payload(db_session, selected_page, role)
                              if selected_page is not None else None)
        selected_tab_id_value = selected_tab.id if selected_tab else None
        me = {"username": g.user.username, "name": g.user.display_name or g.user.username}
        # Old keys too ("old": the @ menu leaves them out), so a chip written
        # before a rename still reads as one.
        mention_types = [{"key": key, "label": m.label, "noun": m.item_noun, **({"old": True} if key != m.key else {})}
                         for key, m in _mention_modules(db_session, with_old=True).items()]
        from . import zotero
        zotero_state = zotero.state(zotero.link_for(db_session, g.user.username))

    return render_template(
        "notebook.html",
        tabs=tabs_data,
        shared_topics=[node for node in shared_data if node["depth"] == 0],
        open_topics=open_topics,
        topic_view=topic_view,
        home=home,
        sorts=NOTEBOOK_SORTS,
        folder_icons=lab_notebook.FOLDER_ICONS,
        selected_tab_id=selected_tab_id_value,
        # A topic the person opened (?tab= or one of its pages), not the
        # first one shown when nothing was: a new page goes there.
        topic_chosen=selected_tab_id is not None or selected_page_id is not None,
        selected_page=selected_page_data,
        side=side,
        kinds=lab_notebook.KINDS,
        kind_icons=lab_notebook.KIND_ICONS,
        kind_hints=lab_notebook.KIND_HINTS,
        # As [key, label] pairs: tojson sorts a dict's keys, and the rows keep this order.
        kind_fields={kind: list(fields.items()) for kind, fields in lab_notebook.KIND_FIELDS.items()},
        statuses=lab_notebook.STATUSES,
        me=me,
        mention_types=mention_types,
        lab_zone=lab.clock_zone(),
        starters=[{"key": key, "title": st["title"], "kind": st["kind"], "hint": st["hint"]}
                  for key, st in lab_notebook.STARTERS.items()],
        library=library,
        libraries=NOTEBOOK_LIBRARIES,
        zotero=zotero_state,
        # Each person's chosen short name (THX), shown on their round badge here too.
        shorts={u: d["own_short"] for u, d in _get_user_directory().items() if d.get("own_short")},
    )


def _topic_trail(by_id: dict, topic_id: int) -> list[int]:
    """The topics a topic sits in, outermost first, then itself."""
    chain = []
    while topic_id is not None and topic_id in by_id and topic_id not in chain:
        chain.append(topic_id)
        topic_id = by_id[topic_id]["parent_id"]
    return list(reversed(chain))


@app.route("/notebook/tabs/create", methods=["POST"])
@login_required
def notebook_create_tab():
    title = (request.form.get("title") or "New topic").strip() or "New topic"
    with SessionLocal() as db_session:
        # Inside another topic of theirs (?parent_id=), Inbox aside; else at the top.
        parent = db_session.get(NotebookTab, request.form.get("parent_id", type=int) or 0)
        if parent is not None and (parent.owner_username != g.user.username or lab_notebook.is_inbox(parent)):
            parent = None
        max_pos = db_session.scalar(
            _notebook_owner_filter(select(func.max(NotebookTab.position)))
        ) or 0
        tab = NotebookTab(owner_username=g.user.username, title=title, position=max_pos + 1,
                          parent_tab_id=parent.id if parent is not None else None)
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
            # The topics inside it stay, one level up.
            db_session.execute(update(NotebookTab).where(NotebookTab.owner_username == g.user.username,
                                                         NotebookTab.parent_tab_id == tab.id)
                               .values(parent_tab_id=tab.parent_tab_id))
            db_session.execute(delete(NotebookTopicShare).where(NotebookTopicShare.tab_id_fk == tab.id))
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
    q, results = search_results(request.args.get("q") or "")
    return jsonify({"ok": True, "results": results, **({"query": q} if q else {})})


def search_results(q: str, limit: int = 5) -> tuple[str, list[dict]]:
    """The search itself, as the signed-in person: (what was looked for,
    up to `limit` matches per section). Each match also carries `ref`, the
    row it is ({"kind", "id"}), which the assistants' search
    (lookup.search) hands on."""
    q = (q or "").strip()
    # "#44" is how the app writes mouse 44 everywhere; search for the number.
    if re.fullmatch(r"#\s*\d+", q):
        q = q.lstrip("#").strip()
    if not q:
        return "", []
    # % and _ are what they are, not LIKE's wildcards (they matched everything).
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    # A record's number: digits, not a 21-digit "number" and not "0012" (a
    # lot or catalogue number); those are text to look for.
    is_digit = _record_number(q) is not None
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
                | MouseRecord.ear_tag.ilike(like, escape="\\")
                | MouseRecord.strain.ilike(like, escape="\\") | MouseRecord.attrs.ilike(like, escape="\\")
            )
        for m in db_session.scalars(mouse_stmt.order_by(MouseRecord.mouse_id.desc()).limit(limit)).all():
            results.append({
                "type": "mouse",
                "id": m.mouse_id,
                "ref": {"kind": "mouse", "id": m.id},
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
        for cage in positions.natural_sorted(db_session.scalars(cage_stmt).all(), key=lambda c: c.cage_id)[:limit]:
            live = sum(1 for mouse in cage.mice if mouse_is_active(mouse))
            results.append({
                "type": "cage",
                "id": cage.id,
                "ref": {"kind": "cage", "id": cage.id},
                "label": gettext("Cage %(id)s", id=cage.cage_id),
                "sublabel": " · ".join(filter(None, [cage.purpose, gettext("%(n)s live", n=live), cage.owner])),
                "url": url_for("colony", view="cages", scope="all", q=cage.cage_id),
            })
        litter_stmt = select(LitterRecord).where(
            LitterRecord.litter_id.ilike(like, escape="\\") | LitterRecord.cohort_name.ilike(like, escape="\\")
            | LitterRecord.notes.ilike(like, escape="\\"))
        for litter in positions.natural_sorted(db_session.scalars(litter_stmt).all(), key=lambda l: l.litter_id)[:limit]:
            results.append({
                "type": "litter",
                "id": litter.id,
                "ref": {"kind": "litter", "id": litter.id},
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
            if experiment_pages.place_for(db_session, exp.db or "colony") is None:
                continue              # on a database this person can't open (someone's own)
            results.append({
                "type": "experiment",
                "id": exp.id,
                "ref": {"kind": "experiment", "id": exp.id},
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
                "ref": {"kind": "strain", "id": strain.id},
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
                | PlasmidRecord.attrs.ilike(like, escape="\\")
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
                "ref": {"kind": "plasmid", "id": p.id},
                "label": gettext("Plasmid #%(id)s · %(name)s", id=p.plasmid_id, name=p.name or gettext("(no name)")),
                "sublabel": " · ".join([p.backbone or "?", p.resistance or gettext("no resistance"),
                                        p.owner or gettext("no owner")]) + (f" · {where}" if where else ""),
                "url": url_for("plasmid_page", number=p.plasmid_id),
            })

        # Every lab inventory: samples, orders, reagents, antibodies, custom.
        kind_type = {"orders": "order", "samples": "sample", "reagents": "reagent", "chemicals": "chemical",
                     "antibodies": "antibody",
                     "viruses": "virus", "primers": "primer", "cell_lines": "cell-line",
                     "glycerol_stocks": "glycerol-stock"}
        # Only databases this person sees: the lab's and their own (app/lab.py).
        from . import inventory_service as inventories
        modules = {m.id: m for m in inventories.list_modules(db_session)}
        item_stmt = select(InventoryItem).where(InventoryItem.module_id_fk.in_(list(modules)))
        text_match = (
            InventoryItem.name.ilike(like, escape="\\") | InventoryItem.category.ilike(like, escape="\\")
            | InventoryItem.vendor.ilike(like, escape="\\") | InventoryItem.catalog_number.ilike(like, escape="\\")
            | InventoryItem.lot.ilike(like, escape="\\") | InventoryItem.notes.ilike(like, escape="\\")
            | InventoryItem.attrs.ilike(like, escape="\\")
            # What is kept in a cabinet or fridge named so ("explosion-proof").
            | InventoryItem.rack_id_fk.in_(select(InventoryRack.id).where(InventoryRack.place.ilike(like, escape="\\"))))
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
                "ref": {"kind": "inventory_item", "id": item.id},
                "label": f"{i18n.translate_value(module.label)} #{item.number} · "
                         + (item.name or gettext("(unnamed)")),
                "sublabel": " · ".join(filter(None, [item.category, item.status, item.vendor,
                                                     gettext("lab common") if item.is_shared else item.owner,
                                                     inventories.where_label(item)])),
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
                "ref": {"kind": "stock_unit", "id": unit.id},
                "label": f"{mv.code(unit)} · " + (unit.genotype or gettext("(no genotype)")),
                "sublabel": " · ".join(filter(None, [i18n.translate_value(mv.label), mv.purpose_label(unit.purpose),
                                                     unit.rack.name if unit.rack else "", unit.owner])),
                "url": url_for("stocks.module", key=mv.key),
            })

        # Zebrafish tanks, lines and clutches.
        for t in positions.natural_sorted(db_session.scalars(
                select(TankRecord).options(joinedload(TankRecord.line))
                .where(TankRecord.tank_id.ilike(like, escape="\\") | TankRecord.card_id.ilike(like, escape="\\")
                       | TankRecord.attrs.ilike(like, escape="\\")
                       | TankRecord.owner.ilike(like, escape="\\") | TankRecord.notes.ilike(like, escape="\\"))
                ).all(), key=lambda t: t.tank_id)[:limit]:
            results.append({
                "type": "tank", "id": t.id, "ref": {"kind": "tank", "id": t.id}, "label": gettext("Tank %(id)s", id=t.tank_id),
                "sublabel": " · ".join(filter(None, [t.purpose, t.line.name if t.line else "", t.owner])),
                "url": url_for("zebrafish", view="tanks") + f"#tank-{t.id}",
            })
        for ln in db_session.scalars(
                select(FishLine).where(FishLine.name.ilike(like, escape="\\") | FishLine.zfin_name.ilike(like, escape="\\")
                                       | FishLine.allele.ilike(like, escape="\\") | FishLine.transgene_summary.ilike(like, escape="\\"))
                .order_by(FishLine.name).limit(limit)).all():
            results.append({
                "type": "fish-line", "id": ln.id, "ref": {"kind": "fish_line", "id": ln.id}, "label": ln.name,
                "sublabel": " · ".join(filter(None, [gettext("Zebrafish line"), ln.zfin_name, ln.background])),
                "url": url_for("zebrafish_line_detail", line_id=ln.id),
            })
        for c in db_session.scalars(
                select(ClutchRecord).where(ClutchRecord.clutch_id.ilike(like, escape="\\") | ClutchRecord.notes.ilike(like, escape="\\"))
                .order_by(ClutchRecord.date_of_fertilization.desc()).limit(limit)).all():
            results.append({
                "type": "clutch", "id": c.id, "ref": {"kind": "clutch", "id": c.id}, "label": gettext("Clutch %(id)s", id=c.clutch_id),
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
                "ref": {"kind": "notebook_page", "id": page.id},
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
    return q, results


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
            return redirect(_undo_back())
        if not (row.actor == g.user.username or access.is_admin()):
            flash(gettext("Only whoever ran a batch, or an admin, can undo it."), "error")
            return redirect(_undo_back())

        result = undo_service.undo(db_session, row, g.user.username, force=force)
        if not result["ok"]:
            db_session.rollback()
            for problem in result["problems"]:
                flash(problem, "error")
            return redirect(_undo_back())
        db_session.commit()

    if result["skipped"]:
        flash(gettext("Undid %(reverted)s change(s) — %(skipped)s could not be reversed.",
                      reverted=result["reverted"], skipped=result["skipped"]), "success")
    else:
        flash(gettext("Undid %(reverted)s change(s).", reverted=result["reverted"]), "success")
    for note in result["notes"][:5]:
        flash(note, "info")
    return redirect(_undo_back())



def _undo_back() -> str:
    """Undo from Settings → History goes back there; from Batch history, there."""
    return url_for("settings", _anchor="history") if request.form.get("from") == "settings" else url_for("batches_view")

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
        # A folder holds a page with the pages made inside it: they move together.
        lab_notebook.move_page(db_session, page, new_tab)
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
            # The page's details (a paper's DOI, a cover): kept to what a page holds.
            import json as _json
            try:
                parsed = _json.loads(request.form.get("properties") or "[]")
            except _json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                page.properties = _json.dumps(lab_notebook.clean_properties(parsed))
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
    _note_uploader(saved)
    return jsonify({"ok": True, "url": url_for("static", filename=saved)})


def _note_uploader(saved_path: str) -> None:
    """Its uploader may open the picture or file before the page naming it
    is saved (app/uploads.py). Uploaded into a page they may edit (the live
    editor sends page_id), the page owns it at once, so whoever else has the
    page open sees the picture now, not after autosave."""
    with SessionLocal() as db_session:
        _uploads.note_uploader(db_session, saved_path, g.user.username)
        page_id = request.form.get("page_id", "")
        page = db_session.get(NotebookPage, int(page_id)) if page_id.isdigit() else None
        if page is not None and lab_notebook.can_edit_role(lab_notebook.role_for(db_session, page)):
            from . import signatures
            if not signatures.is_signed(db_session, page.id):
                _uploads.note_record(db_session, saved_path, "notebook_pages", page.id)
        db_session.commit()


@app.route("/notebook/upload-file", methods=["POST"])
@login_required
def notebook_upload_file():
    upload = request.files.get("file")
    saved = save_uploaded_file(upload)
    if not saved:
        return jsonify({"ok": False}), 400
    _note_uploader(saved["path"])
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
        # Its age, from its litter's birth date, as the colony sheet counts it.
        ages = calculate_age_fields(mouse.litter.date_of_birth if mouse.litter else None)
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
                "age_days": ages["age_days"],
                "age_weeks": ages["age_weeks"],
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
    typed, or one of whose own columns (a chemical's abbreviation or CAS
    number) starts with it; a record number finds that record too. A lot or
    catalogue number that is what was typed comes first, then the record of
    that number."""
    stmt = select(InventoryItem).where(InventoryItem.module_id_fk == module.id)
    if query:
        like = like_pattern(query)
        found = (InventoryItem.name.ilike(like, escape="\\") | InventoryItem.catalog_number.ilike(like, escape="\\")
                 | InventoryItem.lot.ilike(like, escape="\\") | InventoryItem.vendor.ilike(like, escape="\\")
                 | InventoryItem.attrs.ilike(attr_value_pattern(query), escape="\\"))
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
        where = " · ".join(v for v in (inventories.where_label(item), item.location_note) if v)
        amount = " ".join(v for v in (item.quantity, item.unit) if v)
        fields = [("Name", item.name), ("Category", item.category), ("Status", item.status),
                  ("Owner", "Lab common" if item.is_shared else item.owner), ("Vendor", item.vendor),
                  ("Catalog #", item.catalog_number), ("Lot", item.lot), ("Amount", amount), ("Where", where),
                  ("Expires", item.expires_on.isoformat() if item.expires_on else "")]
        # A chemical's abbreviation, CAS number and molecular weight, after its name.
        if module.kind == "chemicals":
            attrs = item.attrs_dict
            fields[1:1] = [(f["label"], str(attrs.get(f["key"]) or "")) for f in inventories.view(module).fields
                           if f["key"] in lab_notebook.CHEMICAL_KEYS]
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
                stmt = stmt.where(MouseRecord.genotype.ilike(like_pattern(query), escape="\\") | MouseRecord.owner.ilike(like_pattern(query), escape="\\")
                                  | MouseRecord.ear_tag.ilike(like_pattern(query), escape="\\")
                                  | MouseRecord.strain.ilike(like_pattern(query), escape="\\"))
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
                    | MouseRecord.ear_tag.ilike(like, escape="\\")
                    | MouseRecord.strain.ilike(like, escape="\\") | MouseRecord.attrs.ilike(like, escape="\\")
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


def _plasmid_primers(db_session, p) -> dict:
    """The Primers card: records in the Primers database that name this
    plasmid, and where each binds on it (primer_records.binding_sites)."""
    module = primer_records.primers_module(db_session, create=False)
    rows = []
    for item in primer_records.primers_for(db_session, p):
        attrs = item.attrs_dict
        sites = primer_records.binding_sites(p.full_sequence or "", bool(p.is_circular), attrs.get("sequence", ""))
        where = ""
        if sites:
            site = sites[0]
            where = f"{site['start'] + 1}–{site['end'] + 1} {'→' if site['direction'] > 0 else '←'}"
        rows.append({"id": item.id, "name": item.name or f"#{item.number}", "sequence": attrs.get("sequence", ""),
                     "tm": attrs.get("tm", ""), "status": item.status, "where": where, "more_sites": max(len(sites) - 1, 0),
                     "tail": sites[0]["tail"] if sites else 0, "binds": bool(sites),
                     "url": url_for("inventory.module", key=module.key, open=item.id) if module else ""})
    # Find saved primers: a sequence to look along, and primers to look through.
    can_scan = bool(p.full_sequence) and bool(primer_records.visible_primer_databases(db_session))
    return {"rows": rows, "database": module.label if module else "",
            "url": url_for("inventory.module", key=module.key) if module else "",
            "can_scan": can_scan, "scan_url": url_for("plasmid_primer_sites", row_id=p.id)}


FOUND_PRIMER_ID = "found-primer-"   # Find saved primers' marks on the map: shown, never saved


def primer_sites(db_session, p, with_listed: bool = False) -> dict:
    """Every saved primer the person may see that binds plasmid `p`, wherever
    its Plasmid column points (GitHub #62: don't order a primer the lab
    already has). The ones the plasmid's Primers card lists already are
    counted, not repeated, unless `with_listed` (a page without that card).
    Positions are 1-based, as the page shows them; a reverse primer's site
    runs start→end along the top strand."""
    template = p.full_sequence or ""
    index = primer_records.TemplateIndex(template, bool(p.is_circular))
    listed = set() if with_listed else {i.id for i in primer_records.primers_for(db_session, p)}
    number = str(p.plasmid_id)
    hits, scanned, already, found = [], 0, 0, set()
    for module, item in primer_records.saved_primers(db_session):
        attrs = item.attrs_dict
        sequence = re.sub(r"[^A-Za-z]", "", str(attrs.get("sequence") or "")).upper()
        if not sequence:
            continue
        scanned += 1
        sites = index.sites(sequence)
        if not sites:
            continue
        if item.id in listed:
            already += 1
            continue
        found.add(item.id)
        for s in sites:
            forward = s["direction"] > 0
            hits.append({
                "id": item.id, "number": item.number, "name": item.name or f"#{item.number}",
                "database": i18n.translate_value(module.label, "inventory"),
                "url": url_for("inventory.module", key=module.key, open=item.id),
                "sequence": sequence, "length": len(sequence),
                "status": i18n.translate_value(item.status or "", "inventory"),
                "start": s["start"] + 1, "end": s["end"] + 1,
                "strand": 1 if forward else -1, "direction": "forward" if forward else "reverse",
                "where": f"{s['start'] + 1}–{s['end'] + 1} {'→' if forward else '←'}",
                "annealed": s["annealed"], "tail": s["tail"], "exact": s["tail"] == 0,
                # Its Plasmid column names this one (in a database the card doesn't list, unless with_listed).
                "linked": str(attrs.get(primer_records.TEMPLATE_KEY, "")).strip() == number,
            })
    hits.sort(key=lambda h: (h["start"], h["name"].lower()))
    return {"ok": True, "length": len(template), "circular": bool(p.is_circular), "scanned": scanned,
            "primers": len(found), "sites": len(hits), "listed": already, "hits": hits}


def _parent_role_label(role: str) -> str:
    return {"backbone": gettext("backbone"), "insert": gettext("insert"), "template": gettext("template"),
            "donor": gettext("donor"), "other": gettext("parent")}.get(role, role)


def _parent_method_label(method: str) -> str:
    return {"digest_ligate": gettext("Digest and ligate"), "gibson": gettext("Gibson / HiFi assembly"),
            "infusion": gettext("In-Fusion"),
            "golden_gate": gettext("Golden Gate"), "pcr": gettext("PCR"), "mutagenesis": gettext("Mutagenesis"),
            "gateway": gettext("Gateway"), "synthesis": gettext("Synthesised"), "other": gettext("Other")}.get(method, "")


def _tree_rows(family: dict) -> dict:
    """The family tree as the page lists it: each node with its number, link
    and role, children nested."""
    def node(n: dict) -> dict:
        plasmid = n["plasmid"]
        return {"label": n["label"], "number": plasmid.plasmid_id if plasmid else None,
                "url": url_for("plasmid_page", number=plasmid.plasmid_id) if plasmid else "",
                "role": _parent_role_label(n["role"]) if n["role"] else "",
                "children": [node(c) for c in n["children"]]}
    return {"up": [node(n) for n in family["up"]], "down": [node(n) for n in family["down"]]}


def _version_row(v, current: bool) -> dict:
    """One line of a plasmid's version history, as its Sequence tab lists it."""
    if v.how == "upload":
        how = gettext("Loaded from a %(format)s file", format=(v.detail or "sequence").upper())
    elif v.how == "restore":
        try:
            restored = fmt_day(local_time(datetime.fromisoformat(v.detail)), with_time=True)
        except ValueError:
            restored = v.detail
        how = gettext("Restored the version of %(when)s", when=restored)
    else:
        how = {"editor": gettext("Edited on the Sequence tab"), "hand": gettext("Edited by hand"),
               "detect": gettext("Features found from the library"),
               "annotate": gettext("Features found by pLannotate"),
               "assembly": gettext("Assembled from fragments"),
               "mutagenesis": gettext("Made by site-directed mutagenesis"),
               "primers": gettext("Primers the wizard designed, drawn on the map"),
               "clear": gettext("Cleared"), "baseline": gettext("As it was before version history")}.get(v.how, v.how)
    try:
        features = len([f for f in json.loads(v.features_json or "[]") if (f.get("kind") or "feature") == "feature"])
    except (json.JSONDecodeError, AttributeError):
        features = 0
    return {"id": v.id, "how": how, "who": v.saved_by, "when": fmt_day(local_time(v.saved_at), with_time=True),
            "bp": len(v.full_sequence or ""), "features": features, "circular": bool(v.is_circular),
            "current": current}


def _several_records(parsed: dict) -> str:
    """A file of several sequences keeps its first: say so."""
    return gettext("The file holds %(n)s sequences; only the first, %(name)s, was kept.",
                   n=parsed["records"], name=parsed.get("name") or gettext("unnamed"))


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
    held = custom_fields.values(p)
    return {
        "id": p.id, "_label": _plasmid_label(p), "_locked": not editable, "_manage": access.can_manage(p),
        "plasmid_id": p.plasmid_id, **values,
        **{f"attr_{key}": value for key, value in held.items()},
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
        # Each plasmid's newest accepted clone, for the List's cards (app/clone_routes.py).
        from .models import PlasmidClone
        verified_at = dict(db_session.execute(select(PlasmidClone.plasmid_row_id, func.max(PlasmidClone.accepted_at))
                                              .where(PlasmidClone.accepted_at.is_not(None))
                                              .group_by(PlasmidClone.plasmid_row_id)).all())
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
                "mini": _mini_map(p),
                "verified": fmt_day(local_time(verified_at[p.id])) if p.id in verified_at else "",
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
        defined_columns = custom_fields.fields(db_session, "plasmids")
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
            custom_columns=custom_fields.in_table(defined_columns),
            custom_columns_all=defined_columns,
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
    if parsed and parsed.get("records", 1) > 1:
        flash(_several_records(parsed), "warning")
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
                # A SnapGene file's description fills an empty Notes.
                if parsed.get("description") and not (record.notes or "").strip():
                    record.notes = parsed["description"][:2000]
                plasmid_versions.record(db_session, record, "upload", user, parsed["format"])
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
            refused = stewardship.change_sharing(p, form.get("is_shared"), "plasmid", f"#{p.plasmid_id}")
            if refused:
                return _plasmid_answer(db_session, p, error=refused, status=403)
        if owner_changes:
            refused = stewardship.change_owner(db_session, p, form.get("owner"), "plasmid", f"#{p.plasmid_id}")
            if refused:
                db_session.rollback()
                return _plasmid_answer(db_session, p, error=refused, status=400)
        for field, limit in (("name", 200), ("backbone", 200), ("insert_seq", 200),
                             ("resistance", 80), ("location", 120)):
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
        # Columns this lab added to Plasmids: only the ones the form sent.
        custom_fields.apply_form(p, form, custom_fields.fields(db_session, "plasmids"))

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
        # A column the lab added to Plasmids is set like any other.
        own = next((f for f in custom_fields.fields(db_session, "plasmids")
                    if f"attr_{f['key']}" == action), None)
        if own is not None:
            return bulk.set_custom_column(db_session, "plasmids", own, value, url_for("plasmids"),
                                       records, access.can_edit, "plasmids")
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
        members = stewardship.lab_members(db_session) if action == "owner" else set()
        refused = stewardship.batch_owner_refused(members, value) if action == "owner" else None
        if refused:
            flash(refused, "error")
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
                for p in editable:
                    if action == "owner":
                        stewardship.change_owner(db_session, p, value, "plasmid", f"#{p.plasmid_id}", members)
                    else:
                        p.resistance = value[:80]
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
    """New box (anyone), or change one: its creator or an admin. A layout
    that renames the places of plasmids in it, or leaves some out, says
    whether each keeps its name or its cell (app/rack_layout.py); a rename
    follows onto its plasmids."""
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
        # A new layout that renames the places of plasmids in the box, or leaves
        # some out, says whether each keeps its name or its cell (app/rack_layout.py).
        change, refused = rack_layout.check(db_session, rack_layout.kind("plasmid_box"), box,
                                            rack_layout.Layout(rows, cols, rack_layout.form_naming(form, box)),
                                            form.get(rack_layout.FIELD))
        if refused:
            flash(refused, "error")
            return _plasmids_page(box.id)
        created = box is None
        if created:
            box = PlasmidBox(created_by=g.user.username)
            db_session.add(box)
        renamed = not created and box.name != name
        with rack_layout.batch(db_session, change):
            box.name, box.rows, box.cols = name, rows, cols
            box.naming = json.dumps(rack_layout.form_naming(form, box))
            box.location = (form.get("location") or "").strip()[:120]
            if "notes" in form:
                box.notes = (form.get("notes") or "").strip()
            db_session.flush()
            if renamed:
                for p in db_session.scalars(select(PlasmidRecord).where(PlasmidRecord.box_id_fk == box.id)):
                    p.storage_box = name
            rack_layout.apply(db_session, change)
        db_session.commit()
        flash(gettext("Added box %(box)s (%(rows)s × %(cols)s, positions %(span)s).", box=box.name, rows=rows,
                      cols=cols, span=pbox.span(box)) if created else
              gettext("Saved box %(box)s (%(rows)s × %(cols)s, positions %(span)s).", box=box.name, rows=rows,
                      cols=cols, span=pbox.span(box)) + rack_layout.outcome(change), "success")
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
            "versions": [_version_row(v, i == 0) for i, v in enumerate(plasmid_versions.versions(db_session, p))],
            "parents": [{"id": e["link"].id, "label": e["label"], "role": _parent_role_label(e["role"]),
                         "method": _parent_method_label(e["method"]),
                         "number": e["plasmid"].plasmid_id if e["plasmid"] else None,
                         "url": url_for("plasmid_page", number=e["plasmid"].plasmid_id) if e["plasmid"] else ""}
                        for e in plasmid_lineage.parents(db_session, p)],
            "children": [{"label": c.name, "number": c.plasmid_id, "url": url_for("plasmid_page", number=c.plasmid_id)}
                         for c in plasmid_lineage.children(db_session, p)],
            "tree": _tree_rows(plasmid_lineage.tree(db_session, p)),
        }
        # Viruses (or anything with a plasmid column) made from it.
        from . import inventory_service as inventories
        made_from = [{
            "label": m["item"].name or f"#{m['item'].number}", "number": m["item"].number,
            "database": m["module"].label, "status": m["item"].status, "available": m["available"],
            "url": url_for("inventory.module", key=m["module"].key, open=m["item"].id),
        } for m in inventories.made_from_plasmid(db_session, p.plasmid_id)]
        stocks_db = inventories.first_of_kind(db_session, "glycerol_stocks")
        glycerol = {
            "rows": [{"label": m["item"].name or f"#{m['item'].number}", "number": m["item"].number,
                      "status": m["item"].status, "available": m["available"],
                      "where": inventories.where_label(m["item"]) or m["item"].location_note,
                      "url": url_for("inventory.module", key=m["module"].key, open=m["item"].id)}
                     for m in inventories.made_from_plasmid(db_session, p.plasmid_id, kinds=("glycerol_stocks",))],
            "database": stocks_db.label if stocks_db else "",
            "url": url_for("inventory.module", key=stocks_db.key) if stocks_db else "",
            "new_url": url_for("inventory.new_module", preset="glycerol_stocks"),
        }
        files = _plasmid_files(db_session, p)
        kept_reads = _kept_reads(db_session, p)
        primers = _plasmid_primers(db_session, p)
        from . import clone_routes
        clones = {"count": len(clone_routes.clones_of(db_session, p)), "verified": clone_routes.verified(db_session, p),
                  "urls": clone_routes.clone_urls(p.id)}
        lineage_options = [f"{n} · {name}" if name else str(n) for n, name in db_session.execute(
            select(PlasmidRecord.plasmid_id, PlasmidRecord.name).where(PlasmidRecord.id != p.id)
            .order_by(PlasmidRecord.plasmid_id)).all()]
    return render_template("plasmid_detail.html", plasmid=data, boxes=boxes, usernames=usernames,
                           made_from=made_from, lineage_options=lineage_options, primers=primers, files=files,
                           kept_reads=kept_reads, clones=clones,
                           plannotate_here=plannotate.available(),
                           glycerol=glycerol)


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
        plasmid_versions.before_change(db_session, p)
        _apply_parsed_sequence(p, parsed)
        if parsed.get("name") and not p.name:
            p.name = parsed["name"]
        stamp_updated(p)
        plasmid_versions.record(db_session, p, "upload", g.user.username, parsed["format"])
        db_session.commit()
    flash(gettext("Loaded %(format)s · %(bp)s bp · %(features)s features.", format=parsed["format"].upper(),
                  bp=len(parsed["sequence"]), features=len(parsed["features"])), "success")
    if parsed.get("records", 1) > 1:
        flash(_several_records(parsed), "warning")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/parents", methods=["POST"])
@login_required
def plasmid_add_parent(row_id: int):
    """Say what a plasmid was made from: a plasmid in the lab (its number,
    #42, or its name) or one from outside it (anything else, e.g.
    "Addgene #11150"), in a role, by a method."""
    from . import inventory_service as inventories

    raw = (request.form.get("parent") or "").strip()
    role = request.form.get("role") or "other"
    method = request.form.get("method") or ""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if not raw:
            flash(gettext("Name the plasmid it was made from: its number, its name, or where it came from."), "error")
            return redirect(plasmid_page_url(row_id) + "#made-from")
        parent = inventories.resolve_plasmid(db_session, raw)
        try:
            plasmid_lineage.add_parent(db_session, p, parent=parent, label="" if parent else raw, role=role,
                                       method=method, user=g.user.username)
        except ValueError as problem:
            flash(gettext("A plasmid can't be made from itself.") if str(problem) == "self" else
                  gettext("That would make a loop: #%(n)s was made from this plasmid.", n=parent.plasmid_id),
                  "error")
            return redirect(plasmid_page_url(row_id) + "#made-from")
        # The Backbone and Insert columns name it, when they are empty.
        name = parent.name if parent is not None else raw
        if role == "backbone" and not (p.backbone or "").strip():
            p.backbone = name[:200]
        elif role == "insert" and not (p.insert_seq or "").strip():
            p.insert_seq = name[:200]
        stamp_updated(p)
        db_session.commit()
        shown = f"#{parent.plasmid_id} {parent.name}" if parent is not None else raw
    flash(gettext("Made from %(parent)s.", parent=shown), "success")
    return redirect(plasmid_page_url(row_id) + "#made-from")


@app.route("/plasmids/<int:row_id>/parents/<int:link_id>/remove", methods=["POST"])
@login_required
def plasmid_remove_parent(row_id: int, link_id: int):
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if plasmid_lineage.remove_link(db_session, p, link_id):
            stamp_updated(p)
            db_session.commit()
            flash(gettext("Removed it from Made from."), "success")
    return redirect(plasmid_page_url(row_id) + "#made-from")


@app.route("/plasmids/<int:row_id>/primers.csv")
@login_required
def plasmid_primers_csv(row_id: int):
    """An order sheet (Name, Sequence, Scale, Purification) for the plasmid's
    primers, or the ones ticked (?ids=3,7)."""
    wanted = {int(x) for x in (request.args.get("ids") or "").split(",") if x.strip().isdigit()}
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            abort(404)
        items = [i for i in primer_records.primers_for(db_session, p) if not wanted or i.id in wanted]
        body = primer_records.order_sheet(items)
        safe = re.sub(r"[^\w\-.]+", "_", p.name or f"plasmid-{p.plasmid_id}").strip("_")
    return Response(body, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}-primers.csv"'})


@app.route("/plasmids/<int:row_id>/primer-sites")
@login_required
def plasmid_primer_sites(row_id: int):
    """Find saved primers (the Primers card's switch): JSON of every saved
    primer that binds this plasmid (primer_sites). ?all=1 includes the ones
    the plasmid's Primers card lists, for a page without it."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
        return jsonify(primer_sites(db_session, p, with_listed=request.args.get("all") == "1"))


@app.route("/plasmids/<int:row_id>/download.<fmt>")
@login_required
def plasmid_download(row_id: int, fmt: str):
    """The plasmid's sequence as a GenBank (.gb) or FASTA (.fa) file, with
    every feature and qualifier it keeps (GenBank)."""
    from .sequence_parser import to_fasta, to_genbank

    if fmt not in ("gb", "fa"):
        abort(404)
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None or not p.full_sequence:
            abort(404)
        name = p.name or f"plasmid-{p.plasmid_id}"
        try:
            annotations = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            annotations = []
        if fmt == "gb":
            body = to_genbank(name, p.full_sequence, bool(p.is_circular), annotations)
        else:
            body = to_fasta(name, p.full_sequence, f"plasmid #{p.plasmid_id}")
    safe = re.sub(r"[^\w\-.]+", "_", name).strip("_") or f"plasmid-{row_id}"
    return Response(body, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.{fmt}"'})


@app.route("/plasmids/<int:row_id>/versions/<int:version_id>/restore", methods=["POST"])
@login_required
def plasmid_restore_version(row_id: int, version_id: int):
    """Put an earlier version of the sequence back (it becomes the newest
    version; nothing in between is lost)."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        version = db_session.get(PlasmidSequenceVersion, version_id)
        if version is None or version.plasmid_row_id != p.id:
            flash(gettext("That version no longer exists."), "error")
            return redirect(plasmid_page_url(row_id))
        plasmid_versions.restore(db_session, p, version, g.user.username)
        stamp_updated(p)
        db_session.commit()
        when = fmt_day(local_time(version.saved_at), with_time=True)
        bp = len(p.full_sequence or "")
    flash(gettext("Restored the version of %(when)s · %(bp)s bp.", when=when, bp=bp), "success")
    return redirect(plasmid_page_url(row_id))


FEATURE_CATEGORY_LABELS = {
    "viral": "Viral elements", "promoter": "Promoters and enhancers", "coding": "Coding sequences",
    "selection": "Selection markers", "origin": "Origins", "terminator": "PolyA signals and terminators",
    "other": "Other elements",
}


# What a file kept with a plasmid is, from its name: a sequencing read, a
# picture (a gel, a plate), or anything else.
TRACE_SUFFIXES = (".ab1", ".abi", ".scf", ".ztr", ".phd", ".seq")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".heic")


def _file_kind(filename: str) -> str:
    lowered = (filename or "").lower()
    if lowered.endswith(TRACE_SUFFIXES):
        return "trace"
    if lowered.endswith(IMAGE_SUFFIXES):
        return "image"
    return "document"


def _file_kind_label(kind: str) -> str:
    return {"trace": gettext("Sequencing read"), "image": gettext("Photo")}.get(kind, gettext("File"))


def _plasmid_files(db_session, p) -> list[dict]:
    """The files kept with this plasmid, newest first."""
    rows = db_session.scalars(select(PlasmidFile).where(PlasmidFile.plasmid_row_id == p.id)
                              .order_by(PlasmidFile.uploaded_at.desc(), PlasmidFile.id.desc()))
    out = []
    for f in rows:
        size = f.size_bytes or 0
        out.append({"id": f.id, "name": f.name, "kind": f.kind, "kind_label": _file_kind_label(f.kind),
                    "url": url_for("static", filename=f.path), "notes": f.notes,
                    "size": gettext("%(n)s MB", n=f"{size / 1048576:.1f}") if size >= 1048576
                            else gettext("%(n)s KB", n=max(size // 1024, 1)),
                    "when": fmt_day(local_time(f.uploaded_at), with_time=True), "who": f.uploaded_by,
                    "may_remove": g.user.role == "admin" or f.uploaded_by == g.user.username})
    return out


@app.route("/plasmids/<int:row_id>/files", methods=["POST"])
@login_required
def plasmid_add_files(row_id: int):
    """Keep files with a plasmid: sequencing traces, a gel photo, a vendor's
    map. Anyone who may edit the plasmid may add them."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        note = (request.form.get("notes") or "").strip()[:300]
        kept = []
        for upload in request.files.getlist("files"):
            saved = save_uploaded_file(upload)
            if saved is None:
                continue
            db_session.add(PlasmidFile(plasmid_row_id=p.id, kind=_file_kind(saved["original_name"]),
                                       name=saved["original_name"][:200], path=saved["path"],
                                       size_bytes=saved["size_bytes"], notes=note,
                                       uploaded_by=g.user.username))
            kept.append(saved["original_name"])
        if not kept:
            flash(gettext("Choose a file to keep with this plasmid first."), "info")
            return redirect(plasmid_page_url(row_id))
        stamp_updated(p)
        db_session.commit()
    flash(ngettext("Kept %(num)s file with this plasmid: %(names)s.",
                   "Kept %(num)s files with this plasmid: %(names)s.", len(kept), names=", ".join(kept)), "success")
    return redirect(plasmid_page_url(row_id) + "#plasmid-files")


@app.route("/plasmids/<int:row_id>/files/<int:file_id>/remove", methods=["POST"])
@login_required
def plasmid_remove_file(row_id: int, file_id: int):
    """Take a file off a plasmid (whoever added it, or an admin). The file
    itself stays in the uploads folder, as the notebook's do."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        kept = db_session.get(PlasmidFile, file_id)
        if p is None or kept is None or kept.plasmid_row_id != p.id:
            flash(gettext("That file is no longer here."), "info")
            return redirect(plasmid_page_url(row_id))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if g.user.role != "admin" and kept.uploaded_by != g.user.username:
            flash(gettext("Only an admin or whoever added %(name)s can take it off.", name=kept.name), "error")
            return redirect(plasmid_page_url(row_id) + "#plasmid-files")
        name = kept.name
        db_session.delete(kept)
        db_session.commit()
    flash(gettext("Took %(name)s off this plasmid.", name=name), "success")
    return redirect(plasmid_page_url(row_id) + "#plasmid-files")


def _kept_reads(db_session, p) -> list[dict]:
    """The files kept with a plasmid that Verify sequencing can read again."""
    from . import seqverify
    rows = db_session.scalars(select(PlasmidFile).where(PlasmidFile.plasmid_row_id == p.id)
                              .order_by(PlasmidFile.uploaded_at.desc(), PlasmidFile.id.desc()))
    return [{"id": f.id, "name": f.name} for f in rows if (f.name or "").lower().endswith(seqverify.READ_SUFFIXES)]


@app.route("/plasmids/<int:row_id>/verify", methods=["POST"])
@login_required
def plasmid_verify(row_id: int):
    """Verify sequencing: Sanger reads (chosen files, pasted text, or reads
    kept with the plasmid) checked against its map (app/seqverify.py), as
    JSON for the page to draw. With keep, the chosen files are kept with
    the plasmid, each noted with its read's verdict."""
    from io import BytesIO
    from pathlib import Path as _Path

    from werkzeug.datastructures import FileStorage

    from . import seqverify
    from . import services as _services

    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
        if not p.full_sequence:
            return jsonify({"ok": False, "error": gettext("This plasmid has no sequence to check reads against.")}), 400
        uploads = [u for u in request.files.getlist("reads") if u and u.filename]
        files = [(u.filename, u.stream.read(seqverify.MAX_FILE + 1)) for u in uploads]
        wanted = {int(x) for x in request.form.getlist("kept") if x.isdigit()}
        for kept in db_session.scalars(select(PlasmidFile).where(PlasmidFile.plasmid_row_id == p.id,
                                                                 PlasmidFile.id.in_(wanted or {0}))):
            source = _services.UPLOAD_DIR / _Path(kept.path).name
            if source.is_file():
                files.append((kept.name, source.read_bytes()[:seqverify.MAX_FILE + 1]))
        text = request.form.get("reads_text") or ""
        reads, problems = seqverify.reads_from_uploads(files, text)
        if not reads:
            return jsonify({"ok": False, "error": " ".join(problems) or
                            gettext("Choose read files (.ab1, FASTA or text), or paste a read.")}), 400
        try:
            annotations = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            annotations = []
        report = seqverify.verify(p.full_sequence, bool(p.is_circular), annotations, reads, name=p.name or "")
        kept_names = []
        if request.form.get("keep") and access.can_edit(p):
            # Keep what was chosen (and pasted) with the plasmid, each with what its reads showed.
            verdicts: dict = {}
            for r, checked in zip(reads, report["reads"]):
                verdicts.setdefault(r.extra.get("file"), []).append(checked["verdict"])
            day = fmt_day(local_time(datetime.utcnow()))
            sources = [(i, name, data) for i, (name, data) in enumerate(files[:len(uploads)])]
            if text.strip():
                sources.append((None, "pasted-reads.fa", text.encode("utf-8")))
            for index, name, data in sources:
                if index not in verdicts:
                    continue
                saved = save_uploaded_file(FileStorage(BytesIO(data), filename=name))
                if saved is None:
                    continue
                note = gettext("Verified against the map %(day)s: %(verdict)s", day=day,
                               verdict=" ".join(verdicts[index]))
                db_session.add(PlasmidFile(plasmid_row_id=p.id, kind=_file_kind(name), name=name[:200],
                                           path=saved["path"], size_bytes=saved["size_bytes"],
                                           notes=note[:300], uploaded_by=g.user.username))
                kept_names.append(name)
            if kept_names:
                stamp_updated(p)
                db_session.commit()
    return jsonify({"ok": True, "report": report, "problems": problems, "kept": kept_names})


@app.route("/plasmids/<int:row_id>/detect-features", methods=["POST"])
@login_required
def plasmid_detect_features(row_id: int):
    """Annotate the library's elements wherever they are in this sequence
    (app/feature_library.py). A version is kept first, so Restore undoes it."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        library = feature_library.entries(db_session)
        if not library:
            flash(gettext("The feature library is empty. Add a plasmid's features to it first."), "info")
            return redirect(plasmid_page_url(row_id))
        current = json.loads(p.features_json or "[]")
        found = feature_library.detect(library, p.full_sequence or "", bool(p.is_circular), current)
        if not found:
            flash(gettext("None of the library's %(n)s elements is in this sequence, or each is already marked.",
                          n=len(library)), "info")
            return redirect(plasmid_page_url(row_id))
        plasmid_versions.before_change(db_session, p)
        p.features_json = json.dumps(current + found)
        stamp_updated(p)
        names = ", ".join(dict.fromkeys(f["name"] for f in found))
        plasmid_versions.record(db_session, p, "detect", g.user.username, detail=names[:200])
        db_session.commit()
    flash(ngettext("Marked %(num)s feature from the library: %(names)s.",
                   "Marked %(num)s features from the library: %(names)s.", len(found), names=names), "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/annotate", methods=["POST"])
@login_required
def plasmid_annotate(row_id: int):
    """Mark what pLannotate finds (app/plannotate.py), which unlike the
    library's exact matching also finds an element a few bases off. A
    version is kept first, so Restore undoes it."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if not plannotate.available():
            flash(gettext("pLannotate is not set up on this server."), "info")
            return redirect(plasmid_page_url(row_id))
        current = json.loads(p.features_json or "[]")
        try:
            found = plannotate.find(p.full_sequence or "", bool(p.is_circular), current)
        except plannotate.ToolFailed as problem:
            flash(gettext("pLannotate could not annotate this plasmid: %(problem)s.", problem=problem), "error")
            return redirect(plasmid_page_url(row_id))
        if not found:
            flash(gettext("pLannotate found nothing this map does not already mark."), "info")
            return redirect(plasmid_page_url(row_id))
        plasmid_versions.before_change(db_session, p)
        p.features_json = json.dumps(current + found)
        stamp_updated(p)
        names = ", ".join(dict.fromkeys(f["name"] for f in found))
        plasmid_versions.record(db_session, p, "annotate", g.user.username, detail=names[:200])
        db_session.commit()
    flash(ngettext("pLannotate marked %(num)s feature: %(names)s.",
                   "pLannotate marked %(num)s features: %(names)s.", len(found), names=names), "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/features-to-library", methods=["POST"])
@login_required
def plasmid_features_to_library(row_id: int):
    """This plasmid's named features into the lab's feature library."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        made = feature_library.add_from_plasmid(db_session, p, g.user.username)
        db_session.commit()
    if made:
        flash(ngettext("Added %(num)s element to the feature library.",
                       "Added %(num)s elements to the feature library.", made), "success")
    else:
        flash(gettext("The feature library already has every named feature of this plasmid."), "info")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/features")
@login_required
def feature_library_page():
    """The lab's feature library, by kind of element."""
    category = request.args.get("category", "")
    if category not in feature_library.CATEGORIES:
        category = ""
    # With the pack in, a lab has a couple of thousand elements: show a
    # shelf-full at a time and let a search narrow it, rather than sending
    # every one of them to a phone.
    look_for = (request.args.get("q") or "").strip()[:80]
    SHOWN = 100
    with SessionLocal() as db_session:
        everything = feature_library.entries(db_session)
        sources = {p.id: p for p in db_session.scalars(select(PlasmidRecord).where(
            PlasmidRecord.id.in_({e.source_row_id for e in everything if e.source_row_id})))}
        if look_for:
            wanted = look_for.lower()
            everything = [e for e in everything
                          if wanted in e.name.lower() or wanted in e.type.lower()
                          or wanted in e.sequence.lower()]
        groups = []
        for key in feature_library.CATEGORIES:
            rows = [{"id": e.id, "name": e.name, "type": e.type, "length": len(e.sequence), "color": e.color,
                     "sequence": e.sequence[:72], "more": max(len(e.sequence) - 72, 0),
                     "by": e.created_by, "from_pack": e.source_name,
                     "may_delete": g.user.role == "admin" or e.created_by == g.user.username,
                     "source": ({"number": sources[e.source_row_id].plasmid_id, "name": sources[e.source_row_id].name,
                                 "url": url_for("plasmid_page", number=sources[e.source_row_id].plasmid_id)}
                                if e.source_row_id in sources else None)}
                    for e in everything if e.category == key]
            if rows:
                groups.append({"key": key, "label": gettext(FEATURE_CATEGORY_LABELS[key]),
                               "rows": rows[:SHOWN], "all": len(rows)})
        state = feature_pack.status(db_session)
    counts = {g_["key"]: g_["all"] for g_ in groups}
    shown = [g_ for g_ in groups if not category or g_["key"] == category]
    pack = {"name": feature_pack.SOURCE_NAME, "citation": feature_pack.CITATION, "licence": feature_pack.LICENCE,
            "url": feature_pack.ARTICLE_URL, "state": state["state"], "error": state["error"],
            "here": sum(1 for e in everything if e.source_name == feature_pack.SOURCE_NAME)}
    return render_template("feature_library.html", groups=shown, counts=counts, category=category, pack=pack,
                           total=len(everything), look_for=look_for, shown=SHOWN,
                           labels={k: gettext(v) for k, v in FEATURE_CATEGORY_LABELS.items()})


def _names(files: list[str]) -> str:
    """File names in a sentence, as inventory_routes._and words them."""
    from .inventory_routes import _and
    return _and(files) if files else ""


@app.route("/plasmids/features/pack", methods=["POST"])
@admin_required
def feature_library_pack():
    """Fetch the common-features pack (app/feature_pack.py) and put it in the
    lab's library. The file is downloaded from the open archive when asked
    for; the app carries no copy of it."""
    with SessionLocal() as db_session:
        started = feature_pack.start(db_session, g.user.username)
    if started:
        flash(gettext("Downloading the common-features pack. It takes about a minute; this page shows it when it is in."),
              "info")
    else:
        flash(gettext("The pack is already being downloaded."), "info")
    return redirect(url_for("feature_library_page"))


@app.route("/plasmids/features/import", methods=["POST"])
@login_required
def feature_library_import():
    """Fill the library straight from annotated files — the GenBank a
    repository gives you, a SnapGene .dna, a vendor's map — without making a
    plasmid for each. Several files at once; each is reported by name."""
    from .sequence_parser import parse_sequence_bytes

    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        flash(gettext("Choose the annotated files to read the elements from."), "info")
        return redirect(url_for("feature_library_page"))
    made, read, unreadable, nothing = 0, 0, [], []
    with SessionLocal() as db_session:
        for upload in uploads:
            parsed = parse_sequence_bytes(upload.read(), upload.filename)
            if not parsed or not parsed.get("sequence"):
                unreadable.append(upload.filename)
                continue
            read += 1
            added = feature_library.add_from_sequence(db_session, parsed["sequence"], parsed.get("features") or [],
                                                      g.user.username)
            if not added:
                nothing.append(upload.filename)
            made += added
        db_session.commit()
    if made:
        # Two counts, each pluralised on its own, rather than one sentence
        # whose "1 files" follows the other number.
        flash(ngettext("Added %(num)s element to the feature library.",
                       "Added %(num)s elements to the feature library.", made) + " "
              + ngettext("Read from %(num)s file.", "Read from %(num)s files.", read), "success")
    elif read:
        flash(gettext("The library already has every named feature in %(files)s.",
                      files=_names(nothing)), "info")
    if unreadable:
        flash(gettext("%(files)s: not a sequence file this app can read (SnapGene .dna, GenBank or FASTA).",
                      files=_names(unreadable)), "warning")
    return redirect(url_for("feature_library_page"))


@app.route("/plasmids/features/collect", methods=["POST"])
@admin_required
def feature_library_collect():
    """Every plasmid's named features into the library."""
    with SessionLocal() as db_session:
        made = feature_library.collect(db_session, g.user.username)
        db_session.commit()
    flash(ngettext("Added %(num)s element to the feature library.",
                   "Added %(num)s elements to the feature library.", made), "success" if made else "info")
    return redirect(url_for("feature_library_page"))


@app.route("/plasmids/features/<int:entry_id>/delete", methods=["POST"])
@login_required
def feature_library_delete(entry_id: int):
    """Take an element out of the library (its admin or whoever added it).
    Maps it was marked on keep their features."""
    from .models import FeatureLibraryEntry
    with SessionLocal() as db_session:
        entry = db_session.get(FeatureLibraryEntry, entry_id)
        if entry is None:
            flash(gettext("That element is no longer in the library."), "info")
            return redirect(url_for("feature_library_page"))
        if g.user.role != "admin" and entry.created_by != g.user.username:
            flash(gettext("Only an admin or whoever added it can take %(name)s out of the library.", name=entry.name),
                  "error")
            return redirect(url_for("feature_library_page"))
        name = entry.name
        db_session.delete(entry)
        db_session.commit()
    flash(gettext("Took %(name)s out of the feature library.", name=name), "success")
    return redirect(url_for("feature_library_page"))


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
        plasmid_versions.before_change(db_session, p)
        p.full_sequence = ""
        p.features_json = "[]"
        p.sequence_format = ""
        p.sequence_uploaded_at = None
        stamp_updated(p)
        plasmid_versions.record(db_session, p, "clear", g.user.username)
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


from .sequence_parser import NOTE_LIMIT, QUALIFIER_LIMIT  # noqa: E402

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
        # Find saved primers draws them on the map to look at; they stay in Primers.
        if str(f.get("id") or "").startswith(FOUND_PRIMER_ID):
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
        if isinstance(notes, dict):  # the editor's {key: [values]}, every qualifier an import kept
            notes = {str(k)[:60]: [str(v)[:NOTE_LIMIT] for v in (vals if isinstance(vals, list) else [vals])][:20]
                     for k, vals in list(notes.items())[:QUALIFIER_LIMIT]}
        elif isinstance(notes, str):
            notes = notes[:NOTE_LIMIT]
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
        if item_kind == "primer" and str(f.get("inventory_id") or "").isdigit():
            entry["inventory_id"] = int(f["inventory_id"])
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
        for extra in ("locations", "bases", "translationType", "inventory_id"):
            if f.get(extra):
                item[extra] = f[extra]
        groups[group].append(item)
    return groups


def _mini_map(p) -> list:
    """A plasmid's features, as little as its small map in the List needs."""
    if not p.full_sequence or not p.features_json:
        return []
    try:
        stored = json.loads(p.features_json)
    except json.JSONDecodeError:
        return []
    return [{"start": f.get("start", 0), "end": f.get("end", 0), "type": f.get("type", ""), "name": f.get("name", ""),
             "direction": f.get("direction", 1)} for f in _features_only(stored)][:40]


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
        plasmid_versions.before_change(db_session, p)
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
        plasmid_versions.record(db_session, p, "hand", g.user.username)
        db_session.commit()
    flash(gettext("Saved sequence · %(bp)s bp.", bp=len(cleaned)), "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/sequence-save", methods=["POST"])
@login_required
def plasmid_sequence_save_json(row_id: int):
    """A whole map saved as JSON, for scripts (the Sequence tab saves one
    edit at a time through sequence_routes' edit.json). The body is
    `sequenceData`: {sequence, circular, features, primers, translations,
    parts, name}, each annotation {id, name, start, end, type, color,
    forward or strand, notes, locations[]}, translated to our schema.

    An empty or malformed save is refused (400) rather than stored: the
    editor autosaves after every edit, and a blank body used to wipe the
    construct. Clearing a sequence is /clear-sequence."""
    from .sequence_parser import is_iupac

    payload = request.get_json(silent=True)
    sd = payload.get("sequenceData", payload) if isinstance(payload, dict) else None
    raw_sequence = sd.get("sequence") if isinstance(sd, dict) else None
    if not isinstance(raw_sequence, str) or not raw_sequence.strip():
        return jsonify({"ok": False, "error": gettext("Refusing to save an empty sequence. Use Clear sequence to empty it.")}), 400
    sequence = re.sub(r"\s+", "", raw_sequence).upper()
    if not is_iupac(sequence):
        return jsonify({"ok": False, "error": gettext("The sequence has characters that are not IUPAC nucleotide codes.")}), 400
    translated = _clean_annotations(sd, len(sequence))

    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
        if not access.can_edit(p):
            return jsonify({"ok": False, "error": _plasmid_denied(p)}), 403
        plasmid_versions.before_change(db_session, p)
        p.full_sequence = sequence
        p.is_circular = bool(sd.get("circular"))
        # Each primer drawn on the map gets its record in the Primers database.
        primers_added = primer_records.sync_from_map(db_session, p, translated, g.user.username)
        p.features_json = json.dumps(translated)
        if not p.sequence_format:
            p.sequence_format = "json"
        # File > Rename Sequence in the editor renames the plasmid.
        if isinstance(sd.get("name"), str) and sd["name"].strip() and sd["name"].strip() != (p.name or ""):
            p.name = sd["name"].strip()[:200]
        p.sequence_uploaded_at = datetime.utcnow()
        stamp_updated(p)
        plasmid_versions.record(db_session, p, "editor", g.user.username)
        db_session.commit()
    counts = {group: sum(1 for f in translated if (f.get("kind") or "feature") == kind)
              for group, kind in ANNOTATION_KINDS.items()}
    return jsonify({"ok": True, "length": len(sequence), "features": counts["features"], "counts": counts,
                    "primers_added": primers_added})


@app.route("/plasmids/<int:row_id>/sequence.json")
@login_required
def plasmid_sequence_json(row_id: int):
    """The plasmid's sequence and annotations as JSON, grouped as
    sequence-save takes them (for scripts)."""
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
            "circular": bool(p.is_circular),  # the key sequence-save takes
            "features": groups["features"],
            "primers": groups["primers"],
            "translations": groups["translations"],
            "parts": groups["parts"],
            "locked": not access.can_edit(p),
        })


@app.route("/utilities")
@login_required
def utilities():
    """The bench calculators (static/bench-calcs.js); the lab's own
    molecular weights come first in their chemical picker: its Chemicals
    database (each by name, and by abbreviation too), then its older list."""
    with SessionLocal() as db_session:
        chemicals = []
        for c in lab_notebook.chemicals_search(db_session, "", limit=2000):
            if c["mw"]:
                chemicals.append({"name": c["name"], "mw": c["mw"], "notes": c["cas"]})
                if c["abbr"] and c["abbr"].lower() != c["name"].lower():
                    chemicals.append({"name": c["abbr"], "mw": c["mw"], "notes": c["name"]})
        chemicals += [{"name": c.name, "mw": c.molecular_weight, "notes": c.notes}
                      for c in db_session.scalars(select(ChemicalReference).order_by(ChemicalReference.name))]
        rotors = _utility_rotors(db_session)
        lab_tools = _lab_tools(db_session)
    return render_template("utilities.html", chemicals=chemicals, rotors=rotors, lab_tools=lab_tools,
                           rotors_editable=g.user.expires_at is None)


UTILITY_ROTORS_KEY = "utilities_rotors"


def _utility_rotors(db_session) -> list[dict]:
    """The lab's centrifuge rotors for rpm ↔ × g: a name, the radius to the
    bottom of the tube (cm) and the top speed (rpm, 0 when not given)."""
    try:
        rows = json.loads(get_setting(db_session, UTILITY_ROTORS_KEY, "[]") or "[]")
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict)]


@app.route("/utilities/rotors", methods=["POST"])
@login_required
def utilities_rotors():
    """Save the lab's rotors (Utilities, rpm ↔ × g). Anyone in the lab may;
    a guest may not."""
    if g.user.expires_at is not None:
        return jsonify({"error": gettext("Guests can't change the lab's rotors.")}), 403
    data = request.get_json(silent=True) or {}
    rows = data.get("rotors")
    if not isinstance(rows, list) or len(rows) > 40:
        return jsonify({"error": gettext("Up to 40 rotors.")}), 400
    rotors = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()[:80]
        try:
            radius = float(row.get("radius") or 0)
            top = float(row.get("max") or 0)
        except (TypeError, ValueError):
            radius, top = 0.0, 0.0
        if not name or not 0.5 <= radius <= 60 or not 0 <= top <= 300000:
            return jsonify({"error": gettext("Each rotor needs a name and a radius between 0.5 and 60 cm.")}), 400
        rotors.append({"name": name, "radius": round(radius, 2), "max": round(top)})
    with SessionLocal() as db_session:
        set_setting(db_session, UTILITY_ROTORS_KEY, json.dumps(rotors, ensure_ascii=False))
        db_session.commit()
    return jsonify({"rotors": rotors})


# The lab's own tools in Utilities: each a name, inputs (a name used in
# formulas, a label, a unit, an example) and answers (a label, a formula
# over the names, a unit). The formulas are read by static/bench-calcs.js,
# never run as code; here they are only kept to the characters a formula
# may hold.
LAB_TOOLS_KEY = "utilities_lab_tools"
_LAB_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,23}$")
_LAB_FORMULA = re.compile(r"^[A-Za-z0-9_\s.+\-*/^(),×÷−]{1,300}$")


def _lab_tools(db_session) -> list[dict]:
    try:
        rows = json.loads(get_setting(db_session, LAB_TOOLS_KEY, "[]") or "[]")
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def _lab_tool_from(data) -> tuple[dict | None, str]:
    """A tool as typed, cleaned; or None and what is wrong with it."""
    if not isinstance(data, dict):
        return None, gettext("That tool could not be read.")
    text = lambda key, most: str(data.get(key) or "").strip()[:most]  # noqa: E731
    name = text("name", 80)
    if not name:
        return None, gettext("Give the tool a name.")
    inputs, outputs = [], []
    for row in (data.get("inputs") or [])[:12]:
        if not isinstance(row, dict) or not str(row.get("name") or "").strip():
            continue
        key = str(row["name"]).strip()
        if not _LAB_NAME.match(key):
            return None, gettext("An input's name is letters, digits and _, starting with a letter: %(name)s", name=key[:24])
        inputs.append({"name": key, "label": str(row.get("label") or "").strip()[:60],
                       "unit": str(row.get("unit") or "").strip()[:20], "value": str(row.get("value") or "").strip()[:30]})
    for row in (data.get("outputs") or [])[:8]:
        if not isinstance(row, dict) or not str(row.get("formula") or "").strip():
            continue
        formula = str(row["formula"]).strip()
        key = str(row.get("name") or "").strip()
        if not _LAB_FORMULA.match(formula):
            return None, gettext("A formula holds names, numbers, + − × ÷ ^ and brackets: %(formula)s", formula=formula[:40])
        if key and not _LAB_NAME.match(key):
            return None, gettext("An input's name is letters, digits and _, starting with a letter: %(name)s", name=key[:24])
        try:
            digits = max(1, min(8, int(row.get("digits") or 4)))
        except (TypeError, ValueError):
            digits = 4
        outputs.append({"label": str(row.get("label") or "").strip()[:60], "formula": formula,
                        "unit": str(row.get("unit") or "").strip()[:20], "name": key, "digits": digits})
    if not inputs or not outputs:
        return None, gettext("A tool needs at least one input and one answer.")
    return {"name": name, "does": text("does", 160), "note": text("note", 300), "keys": text("keys", 200),
            "inputs": inputs, "outputs": outputs}, ""


def _may_change_lab_tool(tool: dict) -> bool:
    return tool.get("author") == g.user.username or access.is_admin()


@app.route("/utilities/lab-tools", methods=["POST"])
@login_required
def utilities_lab_tool_save():
    """Make a lab tool, or change one (its maker, or an admin)."""
    if g.user.expires_at is not None:
        return jsonify({"error": gettext("Guests can use the lab's tools but not make them.")}), 403
    data = request.get_json(silent=True) or {}
    tool, problem = _lab_tool_from(data.get("tool"))
    if tool is None:
        return jsonify({"error": problem}), 400
    with SessionLocal() as db_session:
        tools = _lab_tools(db_session)
        wanted = str((data.get("tool") or {}).get("id") or "")
        old = next((t for t in tools if t["id"] == wanted), None) if wanted else None
        if wanted and old is None:
            return jsonify({"error": gettext("That tool was deleted.")}), 404
        if old is not None:
            if not _may_change_lab_tool(old):
                return jsonify({"error": gettext("Only the person who made a tool, or an admin, can change it.")}), 403
            tool.update(id=old["id"], author=old.get("author", ""))
            tools[tools.index(old)] = tool
        else:
            if len(tools) >= 200:
                return jsonify({"error": gettext("Up to 200 lab tools.")}), 400
            tool.update(id=f"lab{secrets.token_hex(4)}", author=g.user.username)
            tools.append(tool)
        tool["updated"] = date.today().isoformat()
        set_setting(db_session, LAB_TOOLS_KEY, json.dumps(tools, ensure_ascii=False))
        db_session.commit()
    return jsonify({"tools": tools, "id": tool["id"]})


@app.route("/utilities/lab-tools/<tool_id>/delete", methods=["POST"])
@login_required
def utilities_lab_tool_delete(tool_id):
    with SessionLocal() as db_session:
        tools = _lab_tools(db_session)
        tool = next((t for t in tools if t["id"] == tool_id), None)
        if tool is None:
            return jsonify({"tools": tools})
        if g.user.expires_at is not None or not _may_change_lab_tool(tool):
            return jsonify({"error": gettext("Only the person who made a tool, or an admin, can change it.")}), 403
        tools.remove(tool)
        set_setting(db_session, LAB_TOOLS_KEY, json.dumps(tools, ensure_ascii=False))
        db_session.commit()
    return jsonify({"tools": tools})


# The zebrafish database's pages (app/zebrafish_routes.py), registered on
# this app; Home and the calendar read its summary of what is due.
from . import zebrafish_routes  # noqa: E402,F401
from .zebrafish_routes import zebrafish_home_summary  # noqa: E402


if __name__ == "__main__":
    app.run(debug=True)
