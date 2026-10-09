"""The way in: the pages before signing in (templates/door/).

One screen in two halves. The left is the lab: the BioManager mark, the
lab's name, what it keeps, on a quiet helix pattern. The right is the one
thing to do: sign in, ask to join, wait for approval, enter a guest code,
ask the lab's admins for something, or, before there is any account, start
a lab or (in the desktop app) open the lab the person already has.

The sign-in and join forms post to the routes they always did (/login,
/register in app.py); this module holds what is new around them.
"""
from __future__ import annotations

import html
import math
import re
from functools import lru_cache
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from markupsafe import Markup
from sqlalchemy import func, select

from . import lab, security
from .db import SessionLocal
from .i18n import gettext
from .models import UserAccount

bp = Blueprint("door", __name__)

# Someone asking the lab's admins for something, from one address: a few an
# hour (the form is open to anyone who can reach the server).
ask_throttle = security.LoginThrottle(limit=4, window=60 * 60)

ASK_REASONS = ("guest", "password", "account", "other")
NEW_LAB_NAME = "door_new_lab_name"      # the session's name for the lab being started
PENDING = "door_pending_signup"         # the username this browser asked to join with


# ---------------------------------------------------------------- what every page shows

def initials(name: str) -> str:
    words = [w for w in re.split(r"\s+", name or "") if w and w[0].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "B"


def context(**extra) -> dict:
    """The left half: the lab's name and what it keeps (its shared databases
    only, never anyone's personal ones), unless the visitor comes from the
    internet, who sees the name alone."""
    from . import guests
    from . import inventory_service as inventories
    from . import organism_service, stock_service

    with SessionLocal() as db_session:
        accounts = db_session.scalar(select(func.count(UserAccount.id))) or 0
        name = lab.lab_name(db_session)
        databases = []
        if not guests.from_internet():
            labels = inventories.builtin_labels(db_session)
            features = lab.features_on(db_session)
            databases = [{"label": labels[key], "icon": lab.FEATURES[key].icon}
                         for key in ("colony", "zebrafish", "plasmids") if features.get(key)]
            for module in stock_service.list_modules(db_session):
                databases.append({"label": module.label, "icon": stock_service.view(module).icon})
            for module in organism_service.list_modules(db_session):
                databases.append({"label": module.label, "icon": module.icon or "paw"})
            for module in inventories.list_modules(db_session):
                databases.append({"label": module.label, "icon": inventories.view(module).icon})
            for key, icon in (("calendar", "calendar"), ("notebook", "notebook")):
                if features.get(key):
                    databases.append({"label": key.title(), "icon": icon})
    return {"lab_title": name, "lab_initials": initials(name), "databases": databases, "first_account": accounts == 0,
            "host": request.host, "guide_url": lab.guide_url(), "door_pattern": pattern(), **extra}


@lru_cache(maxsize=1)
def pattern() -> Markup:
    """The left half's pattern: two strands of a helix sweeping across, with
    their rungs, and a faint field of dots, as the app's mark is drawn.
    Worked out once; colours come from the page (currentColor)."""
    def strand(phase: float, amp: float, wave: float, x0: float, x1: float, y: float) -> list[tuple[float, float, float]]:
        points = []
        steps = int((x1 - x0) / 4)
        for k in range(steps + 1):
            x = x0 + (x1 - x0) * k / steps
            angle = (x / wave) * 2 * math.pi + phase
            points.append((x, y + amp * math.sin(angle), math.cos(angle)))
        return points

    def path(points) -> str:
        return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y, _ in points)

    parts = []
    for y, amp, wave, opacity, width in ((660, 58, 260, 0.30, 2.4), (830, 30, 180, 0.14, 1.5)):
        front = strand(0, amp, wave, -60, 760, y)
        back = strand(math.pi, amp, wave, -60, 760, y)
        rungs = []
        for k in range(0, len(front), 7):
            (x, ya, depth), (_, yb, _) = front[k], back[k]
            rungs.append(f'<line x1="{x:.1f}" y1="{ya:.1f}" x2="{x:.1f}" y2="{yb:.1f}" '
                         f'stroke-opacity="{opacity * (0.35 + 0.35 * abs(depth)):.3f}"/>')
        parts.append(f'<g stroke="currentColor" stroke-width="{width * 0.6:.1f}">{"".join(rungs)}</g>'
                     f'<path d="{path(back)}" stroke="currentColor" stroke-opacity="{opacity * 0.55:.3f}" stroke-width="{width:.1f}" fill="none"/>'
                     f'<path d="{path(front)}" stroke="currentColor" stroke-opacity="{opacity:.3f}" stroke-width="{width * 1.25:.1f}" fill="none" stroke-linecap="round"/>')
    dots = "".join(f'<circle cx="{18 + 36 * i}" cy="{18 + 36 * j}" r="1.3"/>' for i in range(20) for j in range(26))
    return Markup(
        '<svg class="door-pattern" viewBox="0 0 700 900" preserveAspectRatio="xMidYMax slice" aria-hidden="true" focusable="false">'
        f'<g fill="currentColor" fill-opacity="0.07">{dots}</g>'
        f'<g transform="rotate(-14 350 450)">{"".join(parts)}</g></svg>')


def render(template: str, status: int = 200, **values):
    return render_template(template, **context(**values)), status


def no_accounts_yet() -> bool:
    with SessionLocal() as db_session:
        return db_session.scalar(select(func.count(UserAccount.id))) == 0


def setup_steps(now: str) -> list[dict]:
    """The steps of starting a lab, for the left half: on the desktop app,
    where the lab lives is one of them."""
    from . import devices
    steps = [("name", gettext("Name the lab"))]
    if devices.on_this_computer():
        steps.append(("where", gettext("Where it lives")))
    steps += [("admin", gettext("You, the admin")), ("keeps", gettext("What it keeps")), ("ready", gettext("Invite people"))]
    keys = [k for k, _ in steps]
    at = keys.index(now) if now in keys else 0
    return [{"key": k, "label": label, "done": i < at, "now": i == at} for i, (k, label) in enumerate(steps)]


# ---------------------------------------------------------------- the front page

def front():
    """`/` before signing in: the sign-in, or, before there is any account,
    the first step of starting a lab (or of opening one, in the desktop app)."""
    from . import devices
    from .app import lab_set_aside
    if no_accounts_yet():
        if devices.on_this_computer():
            return render("door/first.html", lab_set_aside=lab_set_aside())
        return render("door/fresh.html")
    return signin()


def signin(status: int = 200, **values):
    from . import devices
    from .app import lab_set_aside
    return render("door/signin.html", status, lab_set_aside=lab_set_aside(),
                  new_lab_offered=devices.on_this_computer() and not no_accounts_yet(),
                  prefill=(request.args.get("u") or request.form.get("username") or "").strip()[:40], **values)


# ---------------------------------------------------------------- starting a lab

@bp.route("/start", methods=["GET", "POST"])
def start():
    """Start a new lab: its name first, so it is plain that a lab is being
    made. Only before there is any account."""
    from . import devices
    if not no_accounts_yet():
        return redirect(url_for("index"))
    if request.method == "POST":
        name = (request.form.get("lab_name") or "").strip()[:80]
        if not name:
            flash(gettext("Give the lab a name. You can change it later."), "error")
        else:
            session[NEW_LAB_NAME] = name
            return redirect(url_for("door.where") if devices.on_this_computer() else url_for("register"))
    return render("door/start.html", steps=setup_steps("name"), lab_name=session.get(NEW_LAB_NAME, ""))


@bp.route("/start/where")
def where():
    """The desktop app: the new lab on this computer, or on a lab server."""
    from . import devices
    if not no_accounts_yet() or not devices.on_this_computer():
        return redirect(url_for("index"))
    if not session.get(NEW_LAB_NAME):
        return redirect(url_for("door.start"))
    return render("door/where.html", steps=setup_steps("where"), lab_name=session[NEW_LAB_NAME])


# ---------------------------------------------------------------- opening a lab that exists

def normalise_address(text: str) -> list[str]:
    """What someone types for their lab's address, as URLs to try: with a
    scheme as given; without one, https first, then http."""
    text = (text or "").strip().rstrip("/")
    if not text or len(text) > 200 or re.search(r"\s", text):
        return []
    if re.match(r"^https?://", text, re.I):
        return [text]
    return [f"https://{text}", f"http://{text}"]


def _fetch(url: str, timeout: float = 6) -> tuple[int, str]:
    req = Request(url, headers={"User-Agent": "BioManager desktop", "Accept-Language": "en"})
    with urlopen(req, timeout=timeout) as response:    # noqa: S310 (an address the person typed, in their own app)
        return response.status, response.read(200_000).decode("utf-8", "replace")


def find_lab(text: str) -> dict | None:
    """The BioManager at that address, if there is one: its address and the
    lab's name (from its page title)."""
    for base in normalise_address(text):
        try:
            status, body = _fetch(f"{base}/healthz")
            if status != 200 or body.strip() != "ok":
                continue
            _, page = _fetch(f"{base}/")
        except Exception:  # noqa: BLE001 — anything that isn't an answer: try the next, then say so
            continue
        title = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
        name = html.unescape(title.group(1)).strip() if title else ""
        name = re.sub(r"\s*·\s*BioManager\s*$", "", name).strip()
        return {"url": base, "name": "" if name == "BioManager" else name, "host": urlparse(base).netloc}
    return None


@bp.route("/open-lab", methods=["GET", "POST"])
def open_lab():
    """The desktop app, before any account: open the lab's BioManager in this
    window instead of making one here. Nothing is created on this computer."""
    from . import devices
    if not devices.on_this_computer() or not no_accounts_yet():
        abort(404)
    found, address = None, (request.form.get("address") or "").strip()
    if request.method == "POST":
        found = find_lab(address)
        if found is None:
            flash(gettext("No BioManager answered at %(address)s. Check the address with whoever runs your lab’s BioManager, and that this computer is on the lab’s network.", address=address or "—"), "error")
        else:
            devices._save_prefs(window_url=found["url"])
    return render("door/open_lab.html", found=found, address=address)


# ---------------------------------------------------------------- after asking to join

@bp.route("/joined")
def joined():
    """The page after asking to join: what happens next, and (while it is
    open) the moment an admin approves."""
    username = session.get(PENDING)
    if not username:
        return redirect(url_for("index"))
    return render("door/waiting.html", username=username)


@bp.route("/joined/status")
def joined_status():
    username = session.get(PENDING)
    if not username:
        return jsonify({"approved": False, "known": False})
    with SessionLocal() as db_session:
        user = db_session.scalar(select(UserAccount).where(UserAccount.username == username))
        approved = user is not None and user.role != "pending" and not user.disabled
    if approved:
        session.pop(PENDING, None)
    return jsonify({"approved": approved, "known": user is not None,
                    "signin": url_for("login", u=username)})


# ---------------------------------------------------------------- asking the admins

@bp.route("/ask", methods=["GET", "POST"])
def ask():
    """Ask the lab's admins for something (a guest code, a new password, an
    account): it reaches each of them as a notification."""
    from .services import add_notification
    reason = request.values.get("why", "")
    reason = reason if reason in ASK_REASONS else "guest"
    if g.get("user") is not None:
        return redirect(url_for("home_dashboard"))
    sent = False
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:80]
        contact = (request.form.get("contact") or "").strip()[:120]
        note = (request.form.get("note") or "").strip()[:600]
        key = ("ask", request.remote_addr or "")
        if not name:
            flash(gettext("Say who you are, so the admins know who is asking."), "error")
        elif ask_throttle.retry_after(key):
            flash(gettext("Several requests have come from here in the last hour. Try again later."), "error")
        else:
            # One English text a reason, so each admin reads it in their own language.
            title = {"guest": "%(who)s asks for a guest code", "password": "%(who)s asks for a new password",
                     "account": "%(who)s asks for an account", "other": "%(who)s asks the lab's admins for help"}[reason]
            message = "They wrote: %(note)s Reach them at %(contact)s." if note else "Reach them at %(contact)s."
            with SessionLocal() as db_session:
                admins = db_session.scalars(select(UserAccount.username).where(
                    UserAccount.role == "admin", UserAccount.disabled.is_(False))).all()
                for admin in admins:
                    add_notification(db_session, admin, title, category="account",
                                     link=url_for("admin_users"),
                                     message=message, values={"who": name},
                                     message_values={"note": note, "contact": contact or gettext("no address given")})
                db_session.commit()
            ask_throttle.failed(key)                 # counts requests, not failures
            sent = True
    return render("door/ask.html", reason=reason, sent=sent)
