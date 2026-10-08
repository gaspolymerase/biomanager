"""A lab server hearing of a newer BioManager, and updating itself.

The notice. Once a day a lab server asks GitHub for the latest release on
gaspolymerase/biomanager (the request carries only the version, in its
User-Agent, as the desktop app's own check does, desktop_updates.py). A
newer one is kept in app_settings and shown to admins in Settings →
Devices & copies, with what's new and a link to the release notes; each
admin is told once per version by a notification ("lab" news). Admins turn
the daily check off there (`updates:check`), or the whole server with
BIOMANAGER_UPDATE_CHECK=0. It runs from a request (after_request, at most
once an hour in each process, the fetch in a daemon thread) and the day's
check is claimed in the database first, so two workers never both ask.

Only on a lab server run from a release's server bundle, which knows its
version: the desktop app updates itself (desktop_updates.py), the phone
apps open the server, and a server built from a checkout has no version to
compare ("server").

Update now. The app runs in a container and cannot restart its own server,
so it asks the machine: it writes `update-request` into the control folder
(deploy/control, mounted at /control) and a systemd path unit on the host
(deploy/host/biomanager-update.path, put in by host/install.sh, which also
writes `updater` there to say it is listening) runs deploy/host/update.sh.
That takes a backup, downloads the latest release's server bundle and app
image, checks both against the SHA-256 GitHub publishes, loads the image,
unpacks the bundle and restarts, and writes how far it got to
`update-status.json` for the page to follow. The request says nothing the
script acts on: it always installs GitHub's latest release, and only when
it is newer, so the most anyone able to write there could do is update the
server to BioManager's own newest version.

app_settings keys (this machine's, never carried to another, devices.py):
`updates:check` ("off" to stop), `updates:last_check` (UTC, ISO 8601),
`updates:latest` (JSON: version, page, notes, published) and `updates:told`
(the version admins were told of).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import current_app
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal
from .models import AppSetting, UserAccount

log = logging.getLogger(__name__)

CHECK_KEY = "updates:check"
CHECKED_KEY = "updates:last_check"
LATEST_KEY = "updates:latest"
TOLD_KEY = "updates:told"
EVERY = timedelta(days=1)
CHECK_EVERY = 3600.0               # seconds between looks in one process
REQUEST = "update-request"
STATUS = "update-status.json"
LISTENING = "updater"
UNANSWERED = 300                   # seconds a request may wait for host/update.sh
NOTICE_TITLE = "BioManager %(version)s is available"
NOTICE_MESSAGE = "Update the lab's server in Settings → Devices & copies."


# ---------------------------------------------------------------- where this applies

def current() -> str:
    from .feedback import app_version
    return app_version()


def applies() -> bool:
    """A lab server from a release: it knows its version and does not
    update itself the way the desktop app does."""
    from .telemetry import released
    return not current_app.config.get("LOCAL_SETUP") and released()


def off_by_env() -> bool:
    return (os.environ.get("BIOMANAGER_UPDATE_CHECK") or "").strip().lower() in ("0", "off", "false", "no")


def checking(session) -> bool:
    from .inventory_service import get_setting
    return not off_by_env() and get_setting(session, CHECK_KEY, "") != "off"


def set_checking(session, on: bool) -> None:
    from .inventory_service import set_setting
    set_setting(session, CHECK_KEY, "on" if on else "off")


def latest(session) -> dict | None:
    """The newest release heard of, if it is newer than this server."""
    from .releases import is_newer
    from .inventory_service import get_setting
    try:
        release = json.loads(get_setting(session, LATEST_KEY, "") or "null")
    except ValueError:
        return None
    if not isinstance(release, dict) or not is_newer(str(release.get("version") or ""), current()):
        return None
    return release


def last_checked(session) -> datetime | None:
    from .inventory_service import get_setting
    return _parse(get_setting(session, CHECKED_KEY, ""))


# ---------------------------------------------------------------- the check

def _parse(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _claim(session, now: datetime) -> bool:
    """Stamp today's check, if nobody else did since we looked."""
    row = session.get(AppSetting, CHECKED_KEY)
    before = row.value if row is not None else ""
    last = _parse(before)
    if last is not None and last <= now and now - last < EVERY:
        return False
    stamp = now.isoformat(timespec="seconds")
    try:
        if row is None:
            session.add(AppSetting(key=CHECKED_KEY, value=stamp))
            session.flush()
            won = True
        else:
            won = session.execute(update(AppSetting).where(AppSetting.key == CHECKED_KEY, AppSetting.value == before)
                                  .values(value=stamp)).rowcount == 1
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return won


def fetch() -> dict:
    """GitHub's latest release: {"version", "page", "notes", "published"}.
    Raises OSError (no network, GitHub down) or ValueError (an odd answer)."""
    import urllib.request
    from . import releases
    request = urllib.request.Request(releases.LATEST_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"BioManager/{current()}"})
    with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 (a fixed https URL)
        data = json.loads(response.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    if releases.parse_version(tag) is None:
        raise ValueError("GitHub's answer had no version in it.")
    return {"version": tag.lstrip("v"), "page": str(data.get("html_url") or releases.RELEASES_PAGE),
            "notes": releases.summary(str(data.get("body") or "")),
            "published": str(data.get("published_at") or "")[:10]}


def store(session, release: dict) -> bool:
    """Keep what GitHub said; tell each admin once of a version newer than
    this one. True if they were told."""
    from . import notify
    from .inventory_service import get_setting, set_setting
    set_setting(session, LATEST_KEY, json.dumps(release))
    session.commit()
    if latest(session) is None or get_setting(session, TOLD_KEY, "") == release["version"]:
        return False
    told = session.get(AppSetting, TOLD_KEY)
    before = told.value if told is not None else ""
    if told is None:
        session.add(AppSetting(key=TOLD_KEY, value=release["version"]))
    elif session.execute(update(AppSetting).where(AppSetting.key == TOLD_KEY, AppSetting.value == before)
                         .values(value=release["version"])).rowcount != 1:
        session.rollback()
        return False
    for name in session.scalars(select(UserAccount.username).where(
            UserAccount.role == "admin", UserAccount.disabled.is_(False))):
        notify.send(session, name, NOTICE_TITLE, NOTICE_MESSAGE, category="lab", link="/settings#updates",
                    values={"version": release["version"]}, message_values={})
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return True


def check(session, manual: bool = False, now: datetime | None = None, fetcher=None) -> dict:
    """Ask GitHub, once a day (or now, when an admin asks).

    {"state": "newer", "release": {...}} | {"state": "current"} |
    {"state": "quiet"} (not due, or switched off) | {"state": "error"}"""
    try:
        if not applies() or (not manual and not checking(session)):
            return {"state": "quiet"}
        now = now or datetime.utcnow()
        if manual:
            from .inventory_service import set_setting
            set_setting(session, CHECKED_KEY, now.isoformat(timespec="seconds"))
            session.commit()
        elif not _claim(session, now):
            return {"state": "quiet"}
        try:
            release = (fetcher or fetch)()
        except (OSError, ValueError):
            log.debug("update check: GitHub not reached", exc_info=True)
            return {"state": "error"}
        store(session, release)
        return {"state": "newer", "release": release} if latest(session) else {"state": "current"}
    except Exception:  # noqa: BLE001 — never into the app
        log.debug("update check failed", exc_info=True)
        session.rollback()
        return {"state": "error"}


# ---------------------------------------------------------------- Update now

def control_dir() -> Path:
    return Path(os.environ.get("BIOMANAGER_CONTROL_DIR") or "/control")


def can_update() -> bool:
    """The machine listens (host/install.sh was run) and this app may ask."""
    folder = control_dir()
    return (folder / LISTENING).is_file() and os.access(folder, os.W_OK)


def progress() -> dict | None:
    """What host/update.sh last wrote: {"state": "running" | "done" |
    "failed" | "current", "step", "version", "from", "detail", "at"}, or
    a request not yet picked up ({"state": "asked"})."""
    folder = control_dir()
    try:
        raw = (folder / STATUS).read_text(encoding="utf-8")[:4000]
        status = json.loads(raw)
        status = status if isinstance(status, dict) else None
    except (OSError, ValueError):
        status = None
    try:
        waiting = time.time() - (folder / REQUEST).stat().st_mtime
    except OSError:
        return status
    if waiting > UNANSWERED:
        return {"state": "failed", "step": "check", "detail": "the server did not start the update; "
                "check biomanager-update.path is running (sudo host/install.sh sets it up)"}
    return {"state": "asked"}


def request_update(by: str, version: str) -> bool:
    """Ask the machine to update. False if it cannot be asked, or is busy."""
    if not can_update():
        return False
    now = progress()
    if now and now.get("state") in ("asked", "running"):
        return True
    folder = control_dir()
    temp = folder / f".{REQUEST}.{os.getpid()}"
    try:
        temp.write_text(json.dumps({"by": by, "version": version,
                                    "at": datetime.utcnow().isoformat(timespec="seconds")}), encoding="utf-8")
        os.replace(temp, folder / REQUEST)
    except OSError:
        log.warning("could not ask the server to update", exc_info=True)
        return False
    return True


def state(session) -> dict:
    """Everything Settings → Devices & copies shows about updates."""
    on = applies()
    return {"applies": on, "current": current(), "latest": latest(session) if on else None,
            "checking": checking(session), "env_off": off_by_env(), "checked": last_checked(session),
            "can_update": on and can_update(), "progress": progress() if on else None}


# ---------------------------------------------------------------- the request hook

_next_check = 0.0
_lock = threading.Lock()


def _start(work) -> None:
    """Run the check in the background; tests replace it."""
    threading.Thread(target=work, name="biomanager-update-check", daemon=True).start()


def _time_to_check() -> bool:
    global _next_check
    now = time.monotonic()
    with _lock:
        if now < _next_check:
            return False
        _next_check = now + CHECK_EVERY
        return True


def after_request(response):
    try:
        app = current_app._get_current_object()
        if app.config.get("TESTING") or off_by_env() or not applies() or not _time_to_check():
            return response

        def work():
            with app.app_context(), SessionLocal() as session:
                check(session)
        _start(work)
    except Exception:  # noqa: BLE001
        log.debug("update check: hook failed", exc_info=True)
    return response


def init_app(app) -> None:
    app.after_request(after_request)
