from __future__ import annotations

import gc
import json
import os
import re
from datetime import date, datetime
from functools import wraps

from flask import Response, abort, flash, g, get_flashed_messages, jsonify, redirect, render_template, request, session, url_for
from markupsafe import Markup, escape
from jinja2 import pass_context, pass_environment
from jinja2.filters import make_attrgetter
from jinja2.utils import htmlsafe_json_dumps
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.security import check_password_hash

from .db import SessionLocal
from . import access, cage_rows, cell_text, i18n, lab, mouse_rows, positions, security
from .i18n import gettext
# Importing this registers the SQLAlchemy flush listener that writes
# audit_log rows for every tracked change; nothing here calls into it.
from . import audit  # noqa: F401
from . import sync_clock  # noqa: F401  (numbers each change to what devices keep)
from . import lab_devices
from .models import (
    FishRack,
    FishRecord,
    FishSacLog,
    TANK_PURPOSE_OPTIONS,
    FISH_SEX_OPTIONS,
    FISH_STATUS_OPTIONS,
    WaterLog,
    WaterSystem,
    NotebookEntry,
    ORDER_STATUS_OPTIONS,
    Order,
    SAMPLE_TYPE_OPTIONS,
    TASK_STATUS_OPTIONS,
    AnimalRecord,
    SampleRecord,
    UserAccount,
)
from .services import sample_source_label, sample_sources, init_database
from .litter_dates import set_birth_date, refresh_birth_date, find_open_litter, born_day, CAGE_GENO_OFFSET_DAYS


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
from . import appearance  # noqa: E402
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
    admin_required,
    app_icon_url,
    builtin_labels,
    local_time,
    owner_badge_filter,
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


# The mouse colony's pages are in app/colony_routes.py; importing it is what registers them.
# COLONY_VIEW_META names the colony's tabs in the navigation below.
from .colony_routes import COLONY_VIEW_META  # noqa: E402


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
from .inventory_service import PLASMID_TAB_KINDS  # noqa: E402
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


# A sheet's cells drawn as text until used (app/text_cells.py).
from .text_cells import as_text_cells, text_cell  # noqa: E402

app.template_filter("as_text_cells")(as_text_cells)


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


# The front door (/) and Home are in app/home_routes.py; importing it is
# what registers them.
from . import home_routes  # noqa: E402,F401


@app.route("/design")
@admin_required
def design_gallery():
    """Every shared piece of the interface on one page, in its real styles
    (docs/DEVELOPMENT.md → Interface design): to check a change against all
    of them at once, in light, dark and on a phone. For maintainers, so in
    English only."""
    return render_template("design_gallery.html")


# Signing in and out, Settings, the admin's pages and starting or restoring a
# lab are in app/account_routes.py; importing it is what registers them.
from . import account_routes  # noqa: E402,F401


# Search across the databases, CSV import, Batch history, the change log and
# the old /orders and /samples are in app/records_routes.py; importing it is
# what registers them.
from . import records_routes  # noqa: E402,F401


# The calendar's events and to-dos are in app/calendar_routes.py; importing it
# is what registers them.
from . import calendar_routes  # noqa: E402,F401


# The notebook's tabs, pages and templates are in app/notebook_routes.py;
# importing it is what registers them.
from . import notebook_routes  # noqa: E402,F401


# The plasmids' pages are in app/plasmid_routes.py; importing it is what
# registers them.
from . import plasmid_routes  # noqa: E402,F401


# Utilities (the bench calculators, rotors and lab tools) are in
# app/utilities_routes.py; importing it is what registers them.
from . import utilities_routes  # noqa: E402,F401


# The zebrafish database's pages are in app/zebrafish_routes.py; importing it
# is what registers them.
from . import zebrafish_routes  # noqa: E402,F401


if __name__ == "__main__":
    app.run(debug=True)
