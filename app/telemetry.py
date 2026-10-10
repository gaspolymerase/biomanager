"""Anonymous counts for BioManager's makers ("a heartbeat").

Once a day an installation that has been set up sends one small message to
PostHog, where BioManager's makers see how many labs use it, on which
version, and which parts. It holds counts and coarse facts only (see
`properties()`):

- the version; whether this is the desktop app or a lab server; the
  operating system's family (Darwin, Windows, Linux); the database
  (sqlite, postgresql);
- how many members, and how many of them changed something in the last
  7 days, each as a range (0, 1, 2-5, 6-15, 16-50, 51+);
- which built-in functions are on (by their keys: colony, zebrafish,
  plasmids, calendar, notebook), and how many configurable databases of
  each kind are on (organism databases; fly and worm stocks; inventories by
  the preset they came from);
- a random id made for this installation (`uuid4`), so a lab counts once.

Never a name, username or email, anything anyone wrote, what a database is
called, the server's address or the computer's name. PostHog is asked not
to work out where the message came from (`$geoip_disable`) nor to keep a
profile (`$process_person_profile`). Admins see exactly what is sent, as
JSON, on the Usage report (/feedback/usage).

When: from a request (`after_request`, checked at most once an hour in
each process, the send in a daemon thread), so the desktop app and every
gunicorn worker behave alike; at most once a day for the installation,
claimed in the database first so two workers never both send. Never under
TESTING, never from a build being worked on (`released()`: a `+dev`
version, which makes a fresh database every run and would count as a new
lab each time), never before the setup survey is
answered, never without a
project key (`PROJECT_KEY`, or `BIOMANAGER_TELEMETRY_KEY`): a build without
one sends nothing at all. A failed send is logged at debug level only and
tried again the next hour; nothing is ever raised into the app.

Turning it off: admins, in the first setup survey or on the Usage report
(`telemetry:enabled` in app_settings, `on` unless switched off); or for the
whole server, `BIOMANAGER_TELEMETRY=0` or `DO_NOT_TRACK=1`.

app_settings keys: `telemetry:enabled`, `telemetry:install_id`,
`telemetry:last_sent` (UTC, ISO 8601).
"""
from __future__ import annotations

import json
import logging
import os
import platform
import threading
import time
import uuid
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

from flask import current_app
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal, engine
from .models import AppSetting, AuditEntry, InventoryModule, OrganismModule, StockModule, UserAccount

log = logging.getLogger(__name__)

HOST = "https://us.i.posthog.com"
# The PostHog project's public key (phc_…): it can only send events, not read
# them. Empty: nothing is ever sent.
PROJECT_KEY = "phc_vHqAVNkAb8HL8n3A8SWkfUmYtFvU4b3gDCTqSe3ohfAb"
EVENT = "heartbeat"
TIMEOUT = 5                        # seconds
EVERY = timedelta(days=1)          # at most one send a day per installation
CHECK_EVERY = 3600.0               # seconds between checks in one process

ENABLED_KEY = "telemetry:enabled"
ID_KEY = "telemetry:install_id"
SENT_KEY = "telemetry:last_sent"

BUCKETS = ((0, "0"), (1, "1"), (5, "2-5"), (15, "6-15"), (50, "16-50"))


# ---------------------------------------------------------------- switches

def project_key() -> str:
    return (os.environ.get("BIOMANAGER_TELEMETRY_KEY") or PROJECT_KEY or "").strip()


def off_by_env() -> bool:
    """BIOMANAGER_TELEMETRY=0 (or off/false/no), DO_NOT_TRACK set to anything
    but 0, or a CI machine (CI=true, as GitHub Actions and most others set):
    a build being checked is not a lab."""
    own = (os.environ.get("BIOMANAGER_TELEMETRY") or "").strip().lower()
    dnt = (os.environ.get("DO_NOT_TRACK") or "").strip().lower()
    ci = (os.environ.get("CI") or "").strip().lower()
    return own in ("0", "off", "false", "no") or dnt not in ("", "0", "false", "no") or ci in ("1", "true", "yes")


def lab_on(session) -> bool:
    from .inventory_service import get_setting
    return get_setting(session, ENABLED_KEY, "") != "off"


def set_lab_on(session, on: bool) -> None:
    from .inventory_service import set_setting
    set_setting(session, ENABLED_KEY, "on" if on else "off")


def last_sent(session) -> datetime | None:
    from .inventory_service import get_setting
    return _parse(get_setting(session, SENT_KEY, ""))


def status(session) -> dict:
    """For the Usage report: what the switch shows and whether anything goes."""
    if off_by_env():
        state = "server-off"
    elif not project_key():
        state = "no-key"
    elif not lab_on(session):
        state = "off"
    else:
        state = "on"
    return {"state": state, "lab_on": lab_on(session), "last_sent": last_sent(session)}


# ---------------------------------------------------------------- what is sent

def bucket(n: int) -> str:
    for top, label in BUCKETS:
        if n <= top:
            return label
    return "51+"


def install_id(session) -> str:
    """This installation's random id, made the first time it is needed."""
    row = session.get(AppSetting, ID_KEY)
    if row is not None and row.value:
        return row.value
    made = str(uuid.uuid4())
    try:
        if row is None:
            session.add(AppSetting(key=ID_KEY, value=made))
        else:
            row.value = made
        session.commit()
        return made
    except IntegrityError:          # another worker made it at the same moment
        session.rollback()
        return session.get(AppSetting, ID_KEY).value


def properties(session) -> dict:
    from . import lab
    from .feedback import app_version
    from .inventory import PRESETS as INVENTORY_PRESETS
    count = lambda stmt: session.scalar(stmt) or 0  # noqa: E731
    members = count(select(func.count(UserAccount.id)).where(UserAccount.disabled.is_(False)))
    lab_accounts = select(UserAccount.username)
    week_ago = datetime.utcnow() - timedelta(days=7)
    active = count(select(func.count(func.distinct(AuditEntry.changed_by)))
                   .where(AuditEntry.changed_at >= week_ago, AuditEntry.changed_by.in_(lab_accounts)))
    stocks = {"fly": 0, "worm": 0}
    for (kind,) in session.execute(select(StockModule.kind).where(StockModule.enabled.is_(True))):
        key = kind if kind in stocks else "other"
        stocks[key] = stocks.get(key, 0) + 1
    inventories = {kind: 0 for kind in INVENTORY_PRESETS}
    for (kind,) in session.execute(select(InventoryModule.kind).where(InventoryModule.enabled.is_(True))):
        key = kind if kind in inventories else "custom"
        inventories[key] = inventories.get(key, 0) + 1
    return {
        "version": app_version(),
        "kind": "desktop" if current_app.config.get("LOCAL_SETUP") else "server",
        "os": platform.system() or "unknown",
        "database": engine.dialect.name,
        "members": bucket(members),
        "active_7_days": bucket(active),
        "functions_on": sorted(key for key in lab.FEATURES if lab.feature_on(session, key)),
        "databases_on": {
            "organisms": count(select(func.count(OrganismModule.id)).where(OrganismModule.enabled.is_(True))),
            "stocks": stocks,
            "inventories": inventories,
        },
        "$geoip_disable": True,
        "$process_person_profile": False,
    }


def payload(session) -> dict:
    """Exactly what is sent (the Usage report shows it)."""
    return {"api_key": project_key(), "event": EVENT, "distinct_id": install_id(session),
            "properties": properties(session)}


# ---------------------------------------------------------------- sending

def _parse(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _due(before: str, now: datetime) -> bool:
    last = _parse(before)
    return last is None or last > now or now - last >= EVERY   # a stamp from the future: the clock was wrong


def _claim(session, now: datetime) -> str | None:
    """Stamp the send time, if nobody else did since we looked. Returns the
    stamp before (to put back if the send fails), or None if not ours."""
    row = session.get(AppSetting, SENT_KEY)
    before = row.value if row is not None else ""
    if not _due(before, now):
        return None
    stamp = now.isoformat(timespec="seconds")
    try:
        if row is None:
            session.add(AppSetting(key=SENT_KEY, value=stamp))
            session.flush()
            won = True
        else:
            won = session.execute(update(AppSetting).where(AppSetting.key == SENT_KEY, AppSetting.value == before)
                                  .values(value=stamp)).rowcount == 1
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    return before if won else None


def _post(body: dict) -> bool:
    request = Request(f"{HOST}/i/v0/e/", data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json", "User-Agent": "BioManager"})
    with urlopen(request, timeout=TIMEOUT) as response:   # noqa: S310 (a fixed https address)
        return 200 <= response.status < 300


def released() -> bool:
    """A build someone is running, not one being worked on. A development
    build (`1.0.6+dev`) makes a fresh database every time it is run — a demo
    lab, the upgrade check, a screenshot pass — and each of those would
    otherwise count as a lab of its own, which is how 230 of 234
    "installations" came to be this machine. "server" is a lab server whose
    image was built from a checkout (`docker compose up -d --build`, as
    deploy/README.md offers), which has no VERSION file: a lab, so it counts."""
    from .feedback import app_version
    version = app_version()
    return bool(version) and "+dev" not in version


def allowed(session) -> bool:
    from . import lab
    return (bool(project_key()) and not off_by_env() and released()
            and lab_on(session) and lab.setup_done(session))


def send_if_due(session, now: datetime | None = None) -> bool:
    """Send today's counts if everything allows it and nobody sent them in
    the last day. True if they were sent."""
    try:
        if not allowed(session):
            return False
        now = now or datetime.utcnow()
        before = _claim(session, now)
        if before is None:
            return False
        stamp = now.isoformat(timespec="seconds")
        try:
            sent = _post(payload(session))
        except Exception:  # noqa: BLE001 — offline, a proxy, PostHog down: try again later
            log.debug("anonymous counts not sent", exc_info=True)
            sent = False
        if not sent:
            session.execute(update(AppSetting).where(AppSetting.key == SENT_KEY, AppSetting.value == stamp)
                            .values(value=before))
            session.commit()
        return sent
    except Exception:  # noqa: BLE001 — never into the app
        log.debug("anonymous counts: check failed", exc_info=True)
        session.rollback()
        return False


# ---------------------------------------------------------------- the request hook

_next_check = 0.0
_lock = threading.Lock()


def _start(work) -> None:
    """Run the send in the background; tests replace it."""
    threading.Thread(target=work, name="biomanager-heartbeat", daemon=True).start()


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
        if app.config.get("TESTING") or off_by_env() or not project_key() or not _time_to_check():
            return response

        def work():
            with app.app_context(), SessionLocal() as session:
                send_if_due(session)
        _start(work)
    except Exception:  # noqa: BLE001
        log.debug("anonymous counts: hook failed", exc_info=True)
    return response


def init_app(app) -> None:
    app.after_request(after_request)
