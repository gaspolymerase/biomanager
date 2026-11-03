"""Accounts and the lab's settings: signing in and out, registering the first
admin, the 中文 / English switch, Settings (a person's profile, the lab's
own settings, updates, and taking a copy of one's data), the admin's
pages for the colony and for people (their role, switching an account off,
a new password), starting a new lab or restoring one on this computer, and
marking notifications read.

Where someone lands after signing in, and the other things every page
needs, are app/web.py's.

These pages hang on the application object from app/flask_app.py and keep
the endpoint names every page links to ("login", "settings", "admin_users",
...); tests/test_url_map.py holds them to it. They never import app.py,
which imports them.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from .db import SessionLocal
from . import access, i18n, lab, positions, security
from .i18n import gettext, ngettext
from . import lab_devices
from .models import (
    CageRecord,
    Experiment,
    MouseWeight,
    MouseRecord,
    NotebookTab,
    PlasmidFile,
    PlasmidRecord,
    NotificationRecord,
    UserAccount,
    UserIdentity,
)
from .services import csv_text, add_notification
from .litter_dates import weaning_offset, open_litters
from . import appearance, devices, door, home_layouts, notify, oidc, server_updates, telemetry
from . import groups as project_groups
from .inventory_service import set_setting
from .flask_app import app
from .web import (
    ALLOWED_LANDING_ENDPOINTS,
    admin_required,
    app_icon_url,
    landing_url,
    local_time,
    login_required,
)


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
