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
from pathlib import Path
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
FOUND_LAB = "door_found_lab"            # the lab /open-lab found, until Open is pressed

# Someone asking the lab's admins for something, from one address: a few an
# hour (the form is open to anyone who can reach the server).
ask_throttle = security.LoginThrottle(limit=4, window=60 * 60)
# Wrong setup codes with a lab file, from one address.
import_throttle = security.LoginThrottle(limit=8, window=60 * 60)
LAB_FILE_MAX = 16 * 1024 ** 3            # a lab with many uploaded files is large

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
            from . import paths
            return render("door/first.html", lab_set_aside=lab_set_aside(), old_labs=len(paths.old_labs()))
        return render("door/fresh.html")
    return signin()


def signin(status: int = 200, **values):
    from . import devices
    from .app import lab_set_aside
    from . import paths
    here = devices.on_this_computer()
    return render("door/signin.html", status, lab_set_aside=lab_set_aside(),
                  new_lab_offered=here and not no_accounts_yet(),
                  old_labs=len(paths.old_labs()) if here else 0,
                  lab_restored=current_app.config.get("LAB_RESTORED", "") if here else "",
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
    if request.method == "POST" and request.form.get("action") == "open":
        # Only now, on Open: until then Back changes nothing.
        found = session.pop(FOUND_LAB, None)
        if not found:
            return redirect(url_for("door.open_lab"))
        devices._save_prefs(window_url=found["url"])
        return devices.open_in_window(found["url"])
    if request.method == "POST":
        found = find_lab(address)
        if found is None:
            flash(gettext("No BioManager answered at %(address)s. Check the address with whoever runs your lab’s BioManager, and that this computer is on the lab’s network.", address=address or "—"), "error")
        else:
            session[FOUND_LAB] = found
    else:
        session.pop(FOUND_LAB, None)
    return render("door/open_lab.html", found=found, address=address)


@bp.route("/lab-elsewhere", methods=["GET", "POST"])
def elsewhere():
    """The desktop app in a web browser (no Go menu), when its window opens
    another device's lab: open that lab, or this computer's own instead."""
    from . import devices
    if not devices.on_this_computer():
        abort(404)
    url = devices.window_url()
    if request.method == "POST" or not url:
        devices._save_prefs(window_url="")
        return redirect(url_for("index"))
    return render("door/elsewhere.html", lab_url=url, lab_host=urlparse(url).netloc)


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


# ---------------------------------------------------------------- bringing a lab here

def _may_take_a_lab(code: str) -> str:
    """Why a lab file can't be taken in here now, or "" when it can: only
    before there is any account, and on a server with its setup code."""
    from . import devices
    if not no_accounts_yet():
        return gettext("This BioManager already has a lab. A lab can only be brought to one that has none yet.")
    if devices.on_this_computer() or not security.setup_code_required():
        return ""
    key = ("import", request.remote_addr or "")
    if import_throttle.retry_after(key):
        return gettext("Too many wrong setup codes from here. Try again in an hour.")
    if not security.setup_code_matches(code):
        import_throttle.failed(key)
        return gettext("That setup code is not right. It is printed in the server log when BioManager starts.")
    return ""


def take_in(source: Path, admin: str = "") -> dict:
    """Check and take in the lab file at `source`. Returns {"ok", "lab",
    "accounts", "files", "rows"} or {"ok": False, "error", "problems"}."""
    import tempfile

    from . import lab_transfer, paths
    with tempfile.TemporaryDirectory(prefix="lab-import-", dir=paths.data_dir()) as tmp:
        try:
            manifest, database, uploads = lab_transfer.open_file(source, Path(tmp))
        except lab_transfer.LabFileError as error:
            return {"ok": False, "error": str(error), "problems": []}
        data, problems = lab_transfer.check(manifest, database)
        if problems:
            return {"ok": False, "error": gettext("The lab can't go in as it is; nothing was changed."), "problems": problems[:50]}
        if not no_accounts_yet():
            return {"ok": False, "error": gettext("This BioManager already has a lab. A lab can only be brought to one that has none yet."), "problems": []}
        try:
            rows = lab_transfer.apply(data, uploads)
        except Exception as error:  # noqa: BLE001 — rolled back: say what, and that nothing changed
            current_app.logger.exception("taking in a lab file failed")
            from sqlalchemy.exc import IntegrityError
            if isinstance(error, IntegrityError):
                # Named plainly; the database's own words are for the log.
                where = getattr(getattr(error.orig, "diag", None), "table_name", "") or ""
                return {"ok": False, "problems": [str(error.orig).splitlines()[0][:300]],
                        "error": gettext("The lab file’s records don’t fit together (a record in %(table)s points to one that isn’t in the file), so nothing was changed. Save the lab again in the desktop app and bring the new file.", table=where or "?")}
            return {"ok": False, "error": gettext("Taking the lab in failed, and nothing was changed: %(reason)s", reason=str(error)[:300]), "problems": []}
    security.clear_setup_code()
    result = {"ok": True, "lab": manifest.get("lab") or "", "accounts": manifest.get("accounts", 0),
              "files": manifest.get("files", 0), "rows": rows}
    if admin:
        result["key"] = _copy_key_for(admin)
    return result


def _copy_key_for(username: str) -> str:
    """A copy key for the desktop the lab came from, for its admin: so it
    keeps a copy of the lab here and opens it in its window (app/lab_copy.py)."""
    from . import lab_copy
    from .models import LabCopyKey
    with SessionLocal() as s:
        user = s.scalar(select(UserAccount).where(UserAccount.username == username))
        if user is None or user.role != "admin":
            return ""
        key = lab_copy.new_key()
        s.add(LabCopyKey(user_id_fk=user.id, label=gettext("The desktop app it came from"), key_hash=lab_copy.key_hash(key)))
        s.commit()
    return key


@bp.route("/lab/bring", methods=["GET", "POST"])
def bring():
    """Before any account: bring a lab here from a desktop app, as a lab file
    (Save the whole lab), or from the desktop app itself (Move this lab to a
    server, which sends it to /lab/import)."""
    import tempfile

    from . import devices, paths
    if not no_accounts_yet():
        return redirect(url_for("index"))
    if request.method == "POST":
        request.max_content_length = LAB_FILE_MAX
        problem = _may_take_a_lab(request.form.get("setup_code", ""))
        upload = request.files.get("lab_file")
        if not problem and (upload is None or not upload.filename):
            problem = gettext("Choose the lab file to bring.")
        if problem:
            flash(problem, "error")
            return render("door/bring.html", needs_setup_code=_needs_code(), problems=[])
        with tempfile.TemporaryDirectory(prefix="lab-upload-", dir=paths.data_dir()) as tmp:
            source = Path(tmp) / "lab.biomanager"
            upload.save(source)
            result = take_in(source)
        if not result["ok"]:
            flash(result["error"], "error")
            return render("door/bring.html", needs_setup_code=_needs_code(), problems=result["problems"])
        flash(gettext("%(lab)s is here: sign in as you did in the desktop app.", lab=result["lab"] or gettext("The lab")), "success")
        return redirect(url_for("login"))
    return render("door/bring.html", needs_setup_code=_needs_code(), problems=[], here=devices.on_this_computer())


def _needs_code() -> bool:
    from . import devices
    return not devices.on_this_computer() and security.setup_code_required()


@bp.route("/lab/import", methods=["POST"])
def import_lab():
    """A desktop app's Move this lab to a server: the lab file as the body,
    the setup code and the desktop's admin in headers. Answers in JSON."""
    import tempfile

    from . import paths
    request.max_content_length = LAB_FILE_MAX
    problem = _may_take_a_lab(request.headers.get("X-BioManager-Setup-Code", ""))
    if problem:
        return jsonify({"ok": False, "error": problem, "problems": []}), 409
    with tempfile.TemporaryDirectory(prefix="lab-upload-", dir=paths.data_dir()) as tmp:
        source = Path(tmp) / "lab.biomanager"
        with open(source, "wb") as out:
            while True:
                chunk = request.stream.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        result = take_in(source, admin=request.headers.get("X-BioManager-Admin", "")[:40])
    return jsonify(result), (200 if result["ok"] else 409)
