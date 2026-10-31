from __future__ import annotations

import gc
import json
import os
import re
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Response, abort, flash, g, get_flashed_messages, jsonify, redirect, render_template, request, session, url_for
from markupsafe import Markup, escape
from jinja2 import pass_context, pass_environment
from jinja2.filters import make_attrgetter
from jinja2.utils import htmlsafe_json_dumps
from sqlalchemy import case, func, select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.security import check_password_hash, generate_password_hash

from .db import SessionLocal
from . import access, cage_rows, cell_text, i18n, lab, mouse_rows, positions, security
from .i18n import gettext, ngettext, pgettext
# Importing this registers the SQLAlchemy flush listener that writes
# audit_log rows for every tracked change; nothing here calls into it.
from . import audit  # noqa: F401
from . import sync_clock  # noqa: F401  (numbers each change to what devices keep)
from . import lab_devices
from . import undo as undo_service
from .models import (
    CageRecord,
    CalendarEvent,
    ClutchRecord,
    FishLine,
    FishRack,
    FishRecord,
    FishSacLog,
    TANK_PURPOSE_OPTIONS,
    FISH_SEX_OPTIONS,
    FISH_STATUS_OPTIONS,
    TankRecord,
    WaterLog,
    WaterSystem,
    Experiment,
    LitterRecord,
    InventoryItem,
    InventoryRack,
    MouseWeight,
    MouseRecord,
    AuditEntry,
    BatchRecord,
    NotebookEntry,
    NotebookPage,
    NotebookTab,
    PlasmidFile,
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
from .formutil import _record_number
from .services import (
    csv_text,
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
    mouse_is_active,
    sample_source_label,
    sample_sources,
    init_database,
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
    ALLOWED_LANDING_ENDPOINTS,
    LANDING_FEATURES,
    _get_user_directory,
    _name_color,
    admin_required,
    app_icon_url,
    builtin_labels,
    landing_url,
    local_time,
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


# Plasmids and the databases that belong with them: one area in the sidebar
# with a tab each, in the order the work flows. They are nothing like the
# lab's other inventories — a primer, a glycerol stock and a virus each name
# the plasmid they came from — so they are kept together.
from .inventory_service import PLASMID_TAB_KINDS, set_setting  # noqa: E402
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


# The calendar's events and to-dos, and who may see and change a to-do, are
# in app/calendar_routes.py. These are the names this file and other
# modules still reach for here.
from .calendar_routes import (  # noqa: E402
    calendar_items,
    task_can_edit,
    task_denied,
    task_visible_clause,
)


# The notebook's tabs, pages and templates are in app/notebook_routes.py;
# importing it is what registers them.
from . import notebook_routes  # noqa: E402,F401


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


# The plasmids' pages, and the helpers behind them, are in
# app/plasmid_routes.py. These are the names this file and other modules
# still reach for here.
from .plasmid_routes import (  # noqa: E402
    _box_of, _clean_features, _file_kind, _plasmid_measure, _plasmid_where,
    primer_sites,
)


# Utilities (the bench calculators, rotors and lab tools) are in
# app/utilities_routes.py; importing it is what registers them.
from . import utilities_routes  # noqa: E402,F401


# The zebrafish database's pages (app/zebrafish_routes.py), registered on
# this app; Home and the calendar read its summary of what is due.
from . import zebrafish_routes  # noqa: E402,F401
from .zebrafish_routes import zebrafish_home_summary  # noqa: E402


if __name__ == "__main__":
    app.run(debug=True)
