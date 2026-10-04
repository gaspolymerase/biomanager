"""Pages for setting up the lab and for notifications.

  /setup            the first-run survey for the first admin, and afterwards
                    the admin's "Lab setup" page: databases, functions,
                    what members may do, who else is an admin
  /welcome          a short tour for anyone signing in for the first time
  /guide            the user guide on the BioManager website (remembers that
                    you opened it, for the Getting started list)
  /milestone/guide  a guide link was opened (sent by base.html)
  /getting-started/hide   hide the Getting started list on your home page
  /databases/<kind>/<key>/audience   share a personal database with the lab,
                    or make a lab database someone's own again
  /notifications    everything you were told, and the bell's data

See app/lab.py for the rules and app/notify.py for what sends notifications.
"""
from __future__ import annotations

from datetime import date, datetime

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)
from sqlalchemy import select

from . import groups, lab, notify, telemetry, whats_new
from .db import SessionLocal
from .i18n import gettext, translate_value
from .models import NotificationRecord, UserAccount
from .services import WEAN_OFFSET_DAYS

bp = Blueprint("lab", __name__)


@bp.app_template_filter("when")
def when(moment) -> str:
    """"just now", "5 min ago", "3 h ago", "yesterday", or the date.
    Times are stored in UTC."""
    if not moment:
        return ""
    seconds = (datetime.utcnow() - moment).total_seconds()
    if seconds < 60:
        return gettext("just now")
    if seconds < 3600:
        return gettext("%(n)s min ago", n=int(seconds // 60))
    if seconds < 86400:
        return gettext("%(n)s h ago", n=int(seconds // 3600))
    if seconds < 2 * 86400:
        return gettext("yesterday")
    # Further back: the date, written the lab's way (Lab setup → Dates).
    return current_app.jinja_env.filters["day"](current_app.jinja_env.filters["local"](moment))


@bp.app_context_processor
def _notification_labels():
    labels = {key: label for key, (label, _hint) in notify.CATEGORIES.items()}
    labels.update({"account": "Accounts", "general": "General"})   # shown through |tr
    return {"notification_labels": labels}


def _admin_or_redirect():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    if g.user.role != "admin":
        flash(gettext("Only a lab admin can change the lab's setup."), "error")
        return redirect(url_for("home_dashboard"))
    return None


# ---------------------------------------------------------------- lab-wide checks on every request

@bp.before_app_request
def refuse_switched_off():
    """A function the lab switched off has no pages; its data is kept."""
    if g.get("user") is None:
        return None
    feature = lab.feature_for_path(request.path)
    if feature is None or lab.request_features().get(feature.key, True):
        return None
    if g.user.role == "admin":
        message = gettext("%(feature)s is switched off for this lab. You can switch it on in Lab setup.",
                          feature=translate_value(feature.label))
    else:
        message = gettext("%(feature)s is switched off for this lab.", feature=translate_value(feature.label))
    if request.method != "GET" or request.headers.get("X-Autosave") == "1":
        return jsonify({"ok": False, "error": message}), 403
    flash(message, "error")
    return redirect(url_for("home_dashboard"))


@bp.before_app_request
def remind_once_a_day():
    """The daily "waiting for genotyping" note, made on someone's first
    page of the day (remembered in their session, so it costs one query)."""
    user = g.get("user")
    if user is None or request.method != "GET" or request.path.startswith(("/static", "/notifications/count", "/api/v1")):
        return None
    today = date.today().isoformat()
    if session.get("reminded_on") == today:
        return None
    session["reminded_on"] = today
    with SessionLocal() as db_session:
        fresh = db_session.get(UserAccount, user.id)
        if fresh is not None:
            reminded = notify.daily_genotyping_reminder(db_session, fresh)
            reminded = notify.daily_experiment_reminder(db_session, fresh) or reminded
            if reminded:
                db_session.commit()
    return None


# ---------------------------------------------------------------- setup survey / lab setup

@bp.route("/setup", methods=["GET", "POST"])
def setup():
    blocked = _admin_or_redirect()
    if blocked:
        return blocked
    with SessionLocal() as db_session:
        first_run = not lab.setup_done(db_session)
        if request.method == "POST":
            if request.form.get("action") == "module":
                module = lab.set_module_enabled(db_session, request.form.get("kind", ""), request.form.get("key", ""),
                                                request.form.get("enabled") == "1")
                if module is None:
                    abort(404)
                db_session.commit()
                flash(gettext("%(name)s switched on.", name=translate_value(module.label)) if module.enabled
                      else gettext("%(name)s switched off.", name=translate_value(module.label)), "success")
                return redirect(url_for("lab.setup") + "#databases")
            zone = (request.form.get("lab_timezone") or "").strip()
            if zone and not lab.valid_timezone(zone):
                flash(gettext("“%(zone)s” is not a time zone BioManager knows; the time zone was left as it was. Pick one from the list, such as America/New_York.", zone=zone), "error")
            switched_on = lab.apply_survey(db_session, request.form, g.user.username)
            if first_run and not telemetry.off_by_env():
                # The first survey asks about the anonymous daily counts; afterwards it's on the Usage report.
                telemetry.set_lab_on(db_session, request.form.get("heartbeat") == "1")
            if switched_on and not first_run:
                notify.tell_lab(db_session, g.user.username, "%(who)s added %(what)s for the lab",
                                link=url_for("home_dashboard"),
                                values={"who": g.user.display_name or g.user.username, "what": ", ".join(switched_on)})
            user = db_session.get(UserAccount, g.user.id)
            if first_run and user.welcomed_at is None:
                user.welcomed_at = datetime.utcnow()
                whats_new.stamp(user)
            db_session.commit()
            flash(gettext("The lab is set up. Change any of it here whenever you like.") if first_run
                  else gettext("Lab setup saved."), "success")
            return redirect(url_for("home_dashboard") if first_run else url_for("lab.setup"))
        state = lab.survey_state(db_session)
        custom = lab.custom_databases(db_session)
        admins = db_session.scalars(select(UserAccount).where(UserAccount.role == "admin",
                                                              UserAccount.disabled.is_(False))
                                    .order_by(UserAccount.username)).all()
        members = db_session.scalars(select(UserAccount).where(UserAccount.role.in_(["member", "care", "facility"]),
                                                               UserAccount.disabled.is_(False))
                                     .order_by(UserAccount.username)).all()
        from .stocks import PRESETS as STOCK_PRESETS
        return render_template(
            "lab/setup.html", first_run=first_run, state=state, custom=custom,
            features=lab.FEATURES, stock_choices=lab.STOCK_CHOICES, inventory_choices=lab.INVENTORY_CHOICES,
            permissions=lab.MEMBER_PERMISSIONS, rack_labels=lab.RACK_LABELS,
            incubator_temps=lab.INCUBATOR_TEMPS, incubator_defaults=lab.INCUBATOR_DEFAULTS,
            stock_default_labels={kind: STOCK_PRESETS[kind]["label"] for kind in lab.STOCK_CHOICES},
            admins=[{"id": a.id, "username": a.username, "name": a.display_name or a.username} for a in admins],
            members=[{"id": m.id, "username": m.username, "name": m.display_name or m.username} for m in members],
            date_styles=lab.DATE_STYLES, timezones=lab.timezone_names(), server_timezone=lab.server_timezone(),
            local_setup=bool(current_app.config.get("LOCAL_SETUP")), wean_offset_days=WEAN_OFFSET_DAYS,
            heartbeat_off_by_server=telemetry.off_by_env())


# ---------------------------------------------------------------- welcome tour

@bp.route("/welcome", methods=["GET", "POST"])
def welcome():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    with SessionLocal() as db_session:
        if request.method == "POST":
            user = db_session.get(UserAccount, g.user.id)
            user.welcomed_at = datetime.utcnow()
            whats_new.stamp(user)          # what's new is for those who knew the version before
            db_session.commit()
            from .app import landing_url
            return redirect(landing_url(user, after_welcome=True))
        name = lab.lab_name(db_session)
        may_create = lab.may_create_database(db_session)
        may_share = lab.may_create_lab_database(db_session)
    return render_template("lab/welcome.html", lab_name=name, may_create=may_create, may_share=may_share)


# ---------------------------------------------------------------- user guide and getting started

# Pages that tick off a Getting started step just by being opened. A cage
# card's QR code opens the cages with ?card=1 (labels.py): that is a scan.
MILESTONE_ENDPOINTS = {"labels.cage_cards": "cage_cards", "colony": "colony"}


@bp.before_app_request
def remember_milestones():
    milestone = MILESTONE_ENDPOINTS.get(request.endpoint or "")
    if milestone == "colony" and request.args.get("card"):
        milestone = "scan"
    user = g.get("user")
    if milestone is None or user is None or session.get(f"did:{milestone}"):
        return None
    session[f"did:{milestone}"] = True
    with SessionLocal() as db_session:
        if not lab.did(db_session, user, milestone):
            lab.mark_did(db_session, user, milestone)
            db_session.commit()
    return None


@bp.route("/guide")
def guide():
    """The user guide lives on the BioManager website, so it is the same for
    every installation and can be read before installing."""
    user = g.get("user")
    if user is not None:
        with SessionLocal() as db_session:
            if not lab.did(db_session, user, "guide"):
                lab.mark_did(db_session, user, "guide")
                db_session.commit()
    anchor = request.args.get("section", "")
    return redirect(lab.guide_url() + (f"#{anchor}" if anchor.replace("-", "").isalnum() else ""))


@bp.route("/milestone/<name>", methods=["POST"])
def milestone(name: str):
    """A guide link was opened (navigator.sendBeacon from base.html)."""
    if g.get("user") is None or name not in ("guide",):
        return "", 204
    with SessionLocal() as db_session:
        if not lab.did(db_session, g.user, name):
            lab.mark_did(db_session, g.user, name)
            db_session.commit()
    return "", 204


@bp.route("/getting-started/hide", methods=["POST"])
def hide_getting_started():
    if g.get("user") is None:
        return redirect(url_for("login"))
    with SessionLocal() as db_session:
        lab.hide_getting_started(db_session, g.user)
        db_session.commit()
    flash(gettext("Getting started is hidden. The user guide is under Help in the sidebar."), "success")
    return redirect(url_for("home_dashboard"))


@bp.app_context_processor
def _getting_started():
    def getting_started_card():
        """For the home page: the steps, or None once they are all done, the
        person hid them, or the lab is not set up yet."""
        user = g.get("user")
        if user is None:
            return None
        with SessionLocal() as db_session:
            if not lab.setup_done(db_session) or lab.getting_started_hidden(db_session, user):
                return None
            steps = lab.getting_started(db_session, user, on_server=not current_app.config.get("LOCAL_SETUP"))
        done = sum(1 for s in steps if s["done"])
        if done == len(steps):
            return None
        return {"steps": steps, "done": done, "total": len(steps)}
    return {"getting_started_card": getting_started_card, "guide_url": lab.guide_url()}


# ---------------------------------------------------------------- a database: mine or the lab's

@bp.route("/databases/<kind>/<key>/audience", methods=["POST"])
def audience(kind: str, key: str):
    if g.get("user") is None:
        return redirect(url_for("login"))
    with SessionLocal() as db_session:
        module = lab.module_for(db_session, kind, key)
        if module is None or not lab.can_see(module):
            abort(404)
        to = request.form.get("to") or ""
        group_id = groups.page_share_group(to)
        if group_id is not None:
            # Your own database, for one of your project groups.
            if not (g.user.role == "admin" or module.private_to == g.user.username):
                flash(gettext("Only its owner or a lab admin can give this database to a group."), "error")
            elif not groups.may_share_with(group_id):
                flash(groups.refusal(group_id), "error")
            else:
                module.private_to, module.share_group_id = "", group_id
                notify.tell_group(db_session, group_id, g.user.username, "%(who)s shared %(name)s with %(group)s",
                                  values={"who": g.user.display_name or g.user.username, "name": module.label,
                                          "group": groups.name_of(group_id)})
                flash(gettext("%(name)s is now %(group)s's: its members see it.", name=translate_value(module.label),
                              group=groups.name_of(group_id)), "success")
                db_session.commit()
            referrer = request.referrer or ""
            return redirect(referrer if referrer.startswith(request.host_url) else url_for("organisms.index"))
        if not lab.can_change_audience(db_session, module):
            flash(gettext("Only a lab admin can change who sees this database."), "error")
            return redirect(url_for("organisms.index"))
        if to == "lab":
            module.private_to, module.share_group_id = "", None
            notify.tell_lab(db_session, g.user.username, "%(who)s shared %(name)s with the lab",
                            values={"who": g.user.display_name or g.user.username, "name": module.label})
            flash(gettext("%(name)s is now a lab database: everyone sees it.", name=translate_value(module.label)),
                  "success")
        elif to == "me":
            owner = request.form.get("owner") or module.created_by or g.user.username
            if g.user.role != "admin":
                owner = g.user.username
            module.private_to, module.share_group_id = owner, None
            if owner == g.user.username:
                flash(gettext("%(name)s is now your own database.", name=translate_value(module.label)), "success")
            else:
                flash(gettext("%(name)s is now %(owner)s's own database.", name=translate_value(module.label),
                              owner=owner), "success")
        db_session.commit()
    referrer = request.referrer or ""
    return redirect(referrer if referrer.startswith(request.host_url) else url_for("organisms.index"))


# ---------------------------------------------------------------- notifications

def _row(n: NotificationRecord) -> dict:
    return {"id": n.id, "title": n.title, "message": n.message, "category": n.category or "general",
            "link": n.link, "actor": n.actor, "is_read": n.is_read, "created_at": n.created_at}


@bp.route("/notifications")
def notifications():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    show = request.args.get("show", "all")
    category = request.args.get("category", "")
    if category not in notify.CATEGORIES and category not in ("account", "general"):
        category = ""
    with SessionLocal() as db_session:
        if g.user.role == "admin":
            notify.settle_signups(db_session)
        rows = [_row(n) for n in notify.recent(db_session, g.user.username, limit=200,
                                               unread_only=(show == "unread"), category=category)]
        unread = notify.unread_count(db_session, g.user.username)
    return render_template("lab/notifications.html", rows=rows, show=show, category=category,
                           categories=notify.CATEGORIES, unread=unread)


@bp.route("/notifications/count")
def notification_count():
    if g.get("user") is None:
        return jsonify({"unread": 0}), 401
    with SessionLocal() as db_session:
        return jsonify({"unread": notify.unread_count(db_session, g.user.username)})


@bp.route("/notifications/panel")
def notification_panel():
    """The bell's dropdown, rendered on the server (fetched when opened)."""
    if g.get("user") is None:
        return "", 401
    with SessionLocal() as db_session:
        rows = [_row(n) for n in notify.recent(db_session, g.user.username, limit=8)]
        unread = notify.unread_count(db_session, g.user.username)
    return render_template("lab/_notification_panel.html", rows=rows, unread=unread)


@bp.route("/notifications/<int:note_id>/open")
def open_notification(note_id: int):
    """Mark it read and go where it points (only ever a page of this app)."""
    if g.get("user") is None:
        return redirect(url_for("login"))
    from .security import safe_next
    with SessionLocal() as db_session:
        note = db_session.get(NotificationRecord, note_id)
        if note is None or note.recipient_username != g.user.username:
            abort(404)
        note.is_read = True
        target = safe_next(note.link) or url_for("lab.notifications")
        db_session.commit()
    return redirect(target)
