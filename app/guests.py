"""Guest passes: let someone outside the lab in for a while, with a code.

An admin makes a pass for a person and a length of time (Guests, from
Manage users). That makes a temporary member account for them and shows a
code once. Entering the code at /guest signs them in as that member: they
can look around and add their own records like anyone in the lab, but not
change Lab setup, approve people or edit others' personal records. When the
pass runs out, or an admin ends it, the account stops working and every
session it had is signed out (load_current_user checks users.expires_at).

Internet access. The server is normally reached only over the lab's
Tailscale network. deploy/host/internet-access.sh puts it on the internet
through Tailscale Funnel, into a Caddy site (deploy/Caddyfile, :8081) that
marks each request with X-BioManager-Entry: internet. Such a request from
someone not signed in only ever gets the guest-code page: no sign-in or
sign-up form, no setup, nothing else. Someone who is signed in (a guest,
or a lab member whose browser already has a session) uses the app as usual.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from . import i18n, security
from .db import SessionLocal
from .i18n import gettext, ngettext
from .models import GuestPass, UserAccount

bp = Blueprint("guests", __name__)

ENTRY_HEADER = "X-BioManager-Entry"
# How long a pass can last, in days, as offered on the Guests page.
DURATIONS = {1: "1 day", 3: "3 days", 7: "1 week", 30: "30 days"}
# No 0/O, 1/I/L or U, so a code read out loud or copied by hand survives.
ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
CODE_LENGTH = 16                     # about 78 bits: guessing is hopeless
# Anyone on the internet may reach these without a session.
OPEN_PATHS = ("/guest", "/healthz", "/logout", security.CSP_REPORT_PATH)
# A calendar feed link carries its own secret (app/lab_calendar.py), so a
# phone or Google Calendar can fetch it from outside the lab's network.
OPEN_PREFIXES = ("/static/", "/calendar/feed/")

# Wrong codes from anywhere, counted together: behind the proxies every
# internet request can look as if it came from the same address.
guest_throttle = security.LoginThrottle(limit=30, window=15 * 60)
THROTTLE_KEY = ("guest-code",)


def from_internet() -> bool:
    return request.headers.get(ENTRY_HEADER, "") == "internet"


def public_url() -> str:
    """Where people outside the lab network open BioManager, if it is on
    the internet (BIOMANAGER_PUBLIC_URL, set by internet-access.sh)."""
    return os.environ.get("BIOMANAGER_PUBLIC_URL", "").strip().rstrip("/")


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i:i + 4] for i in range(0, CODE_LENGTH, 4))


def normalise(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def code_hash(code: str) -> str:
    return hashlib.sha256(normalise(code).encode()).hexdigest()


def is_active(gp: GuestPass, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    return gp.ended_at is None and gp.expires_at > now


def _is_admin() -> bool:
    return g.get("user") is not None and g.user.role == "admin"


# ---------------------------------------------------------------------------
# The gate in front of internet access
# ---------------------------------------------------------------------------


@bp.before_app_request
def internet_gate():
    """From the internet, someone not signed in only sees the code page."""
    if not from_internet() or g.get("user") is not None:
        return None
    path = request.path
    if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
        return None
    if request.method not in ("GET", "HEAD"):
        abort(403)
    return redirect(url_for("guests.enter"))


# ---------------------------------------------------------------------------
# Entering a code
# ---------------------------------------------------------------------------


@bp.route("/guest", methods=["GET", "POST"])
def enter():
    from .app import landing_url

    if g.get("user") is not None and request.method == "GET":
        return redirect(url_for("home_dashboard"))
    status = 200
    if request.method == "POST":
        wait = guest_throttle.retry_after(THROTTLE_KEY)
        if wait:
            minutes = -(-wait // 60)
            flash(ngettext("Too many wrong codes. Try again in %(num)s minute.",
                           "Too many wrong codes. Try again in %(num)s minutes.", minutes), "error")
            return render_template("guests/enter.html"), 429
        code = request.form.get("code", "")
        now = datetime.utcnow()
        with SessionLocal() as s:
            gp = (s.scalar(select(GuestPass).where(GuestPass.code_hash == code_hash(code)))
                  if len(normalise(code)) == CODE_LENGTH else None)
            user = s.get(UserAccount, gp.user_id_fk) if gp is not None and is_active(gp, now) else None
            if user is None or user.disabled:
                guest_throttle.failed(THROTTLE_KEY)
                flash(gettext("That code is not right, or its guest access has ended."), "error")
                status = 401
            else:
                gp.uses = (gp.uses or 0) + 1
                gp.last_used_at = now
                s.commit()
                security.start_session(user)
                from .app import local_time
                until = local_time(gp.expires_at)
                when = (f"{until.day} {until:%b %Y, %H:%M}" if i18n.current() == i18n.DEFAULT
                        else i18n.strftime(until, "%b %d, %Y %H:%M"))
                flash(gettext("Welcome, %(name)s. Your guest access lasts until %(when)s.", name=gp.label, when=when),
                      "success")
                return redirect(landing_url(user))
    return render_template("guests/enter.html"), status


# ---------------------------------------------------------------------------
# Admin: make and end passes
# ---------------------------------------------------------------------------


def _username_for(s, label: str) -> str:
    base = "guest-" + (re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:30] or "visitor")
    name, n = base, 2
    while s.scalar(select(UserAccount.id).where(UserAccount.username == name)) is not None:
        name, n = f"{base}-{n}", n + 1
    return name


def _page(s, new_code_for: dict | None = None, status: int = 200):
    now = datetime.utcnow()
    passes = list(s.scalars(select(GuestPass).order_by(GuestPass.created_at.desc())))
    users = {u.id: u for u in s.scalars(select(UserAccount).where(
        UserAccount.id.in_([p.user_id_fk for p in passes])))} if passes else {}
    rows = [{"pass": p, "user": users.get(p.user_id_fk), "active": is_active(p, now)} for p in passes]
    base = (os.environ.get("BIOMANAGER_BASE_URL") or request.host_url).rstrip("/")
    return render_template("guests/admin.html", rows=rows, durations=DURATIONS, new=new_code_for,
                           public_url=public_url(), lab_url=base), status


@bp.route("/admin/guests", methods=["GET", "POST"])
def admin():
    if not _is_admin():
        abort(403)
    with SessionLocal() as s:
        if request.method == "GET":
            return _page(s)
        label = " ".join((request.form.get("label") or "").split())[:60]
        try:
            days = int(request.form.get("days") or 0)
        except ValueError:
            days = 0
        if not label or days not in DURATIONS:
            flash(gettext("Give the guest a name and pick how long the access lasts."), "error")
            return _page(s, status=400)
        expires = datetime.utcnow() + timedelta(days=days)
        user = UserAccount(username=_username_for(s, label), display_name=f"{label} (guest)",
                           short_name=label[:5], role="member", password_hash=security.NO_PASSWORD,
                           expires_at=expires)
        s.add(user)
        s.flush()
        code = new_code()
        gp = GuestPass(label=label, code_hash=code_hash(code), user_id_fk=user.id, expires_at=expires,
                       created_by=g.user.username)
        s.add(gp)
        s.commit()
        # Shown on this page only, never stored or put in a redirect.
        return _page(s, new_code_for={"code": code, "label": label, "expires_at": expires,
                                      "username": user.username})


@bp.route("/admin/guests/<int:pass_id>/end", methods=["POST"])
def end(pass_id: int):
    if not _is_admin():
        abort(403)
    with SessionLocal() as s:
        gp = s.get(GuestPass, pass_id)
        if gp is None:
            abort(404)
        now = datetime.utcnow()
        if gp.ended_at is None:
            gp.ended_at = now
        user = s.get(UserAccount, gp.user_id_fk)
        if user is not None and (user.expires_at is None or user.expires_at > now):
            user.expires_at = now          # signs out every session it has
        s.commit()
        flash(gettext("Guest access for %(name)s has ended.", name=gp.label), "success")
    return redirect(url_for("guests.admin"))
