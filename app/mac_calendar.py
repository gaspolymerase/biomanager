"""The calendar in the Mac's own Calendar app, kept up to date by the desktop app.

A lab with no server has no address that Google Calendar or a phone can
fetch the phone feed from. On a Mac the desktop app writes the same things
(lab_calendar.feed_items) into a calendar called BioManager, in one of the
accounts the Mac's Calendar holds (iCloud, Google, Exchange, On My Mac), and
the Mac's Calendar carries it on to that account: Google Calendar and the
phone then show it, with no server and no Google sign-in of BioManager's own.

- One way, and the calendar is BioManager's alone: whatever in it isn't on
  BioManager's calendar is taken out. So only a calendar BioManager made, or
  one called BioManager, can be chosen (`dedicated`). A Mac can't add a
  calendar to a Google account, so for Google the person makes one called
  BioManager in Google Calendar on the web and picks it here.
- Matched by what an event says (`Entry`: title, times, notes), not by an id:
  EventKit's identifiers change when an account syncs afresh, and an item
  that changed is simply one event out and another in (`plan`).
- From FEED_BACK_DAYS ago to FEED_AHEAD_DAYS ahead, as the phone feed;
  events that started before that stay as they were.
- When: a minute after the app opens, every UPDATE_EVERY, and SETTLE after
  a change made in the app (`_after`, on each request that changed
  something). Only while the app is open.
- EventKit, Apple's calendar store, through PyObjC (`EventKitStore`); tests
  put a fake in its place (`use_store`). The packaged app says why it asks
  (the NSCalendars… keys in Biomanager.spec) and may ask
  (desktop/entitlements.plist): without them macOS stops the app.
- Kept in app_settings, one per lab (`SETTING`): whose calendar, which
  things, where, and how the last update went.

Only the desktop app's own window, on a Mac, offers it (`offered`).
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections import namedtuple
from contextlib import nullcontext
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, current_app, g, jsonify, request
from sqlalchemy import select

from .db import SessionLocal
from .i18n import gettext

bp = Blueprint("maccal", __name__, url_prefix="/calendar/mac")

SETTING = "mac_calendar"
TITLE = "BioManager"
UPDATE_EVERY = 15 * 60
# After a change, wait this long for the next one before updating.
SETTLE = 20
# EventKit's kinds of account that hold calendars of their own: On My Mac,
# Exchange, CalDAV (iCloud, Google…) and MobileMe; not subscribed calendars
# or birthdays.
WRITABLE_SOURCES = (0, 1, 2, 3)

# An event as both sides see it. All day: start and end are dates, the last
# day included. Otherwise: seconds since 1970.
Entry = namedtuple("Entry", "title allday start end notes")

_lock = threading.RLock()          # EventKit, and the setting, one at a time
_nudge = threading.Event()         # something changed in the app
_started = threading.Event()       # the background updates are running
_store = None


class CalendarError(Exception):
    """Said to the person as it is."""


class StoreError(Exception):
    """EventKit refused; its own reason, often empty."""


def _not_allowed() -> str:
    return gettext("BioManager isn't allowed to use this Mac's calendars. Turn it on in System Settings → Privacy & Security → Calendars, then try again.")  # noqa: E501


# ---------------------------------------------------------------- what goes in

def _clean(text: str) -> str:
    """Notes as both sides keep them: no trailing spaces or Windows line ends."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def entry(item: dict) -> Entry | None:
    """A calendar item (calendar_routes.calendar_items) as an event, or None without times."""
    from .lab_calendar import _dt, shown_title
    start, end = _dt(item.get("start")), _dt(item.get("end"))
    if start is None or end is None:
        return None
    title, notes = shown_title(item).strip(), _clean(item.get("body") or "")
    if item.get("isAllday"):
        return Entry(title, True, start.date(), max(end.date(), start.date()), notes)
    # The app's times are the lab's clock, which is this process's.
    return Entry(title, False, int(start.timestamp()), int(max(end, start).timestamp()), notes)


def _starts_from(e: Entry, day: date) -> bool:
    if e.allday:
        return e.start >= day
    return e.start >= datetime.combine(day, datetime.min.time()).timestamp()


def plan(wanted: list[Entry], existing: list[tuple[object, Entry]]) -> tuple[list[Entry], list[object]]:
    """What to add and which events (their handles) to take out, so the
    calendar holds `wanted` and nothing else. Two identical items are two
    events."""
    pool: dict[Entry, list] = {}
    for handle, e in existing:
        pool.setdefault(e, []).append(handle)
    add = []
    for e in wanted:
        if pool.get(e):
            pool[e].pop(0)   # keep the first, take out any copies
        else:
            add.append(e)
    return add, [handle for handles in pool.values() for handle in handles]


def dedicated(title: str) -> bool:
    """A calendar that may be chosen: everything in it gets replaced."""
    return (title or "").strip().casefold().startswith(TITLE.casefold())


# ---------------------------------------------------------------- the setting

def config(s) -> dict:
    from .inventory_service import get_setting
    try:
        value = json.loads(get_setting(s, SETTING, "") or "{}")
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _save(s, value: dict) -> None:
    from .inventory_service import set_setting
    set_setting(s, SETTING, json.dumps(value) if value else "")
    s.commit()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- where it runs

def store():
    global _store
    if _store is None:
        _store = EventKitStore()
    return _store


def use_store(fake) -> None:
    """Tests: a stand-in for EventKit (None puts the real one back)."""
    global _store
    _store = fake


def available() -> bool:
    if _store is not None:
        return True
    if sys.platform != "darwin":
        return False
    try:
        import EventKit  # noqa: F401
    except ImportError:
        return False
    return True


def offered() -> bool:
    """The desktop app's own window on a Mac: never a server, nor a device
    reaching a desktop that shares its lab on the network."""
    from . import devices
    return bool(current_app.config.get("LOCAL_SETUP")) and devices.on_this_computer() and available()


# ---------------------------------------------------------------- updating

def update(app) -> dict:
    """Bring the Mac's calendar into line with BioManager's, if it is set
    up; what is kept about it afterwards."""
    if not available():
        return {}
    # A context of its own: its g is the calendar owner's, never the page's.
    with _lock, app.app_context(), app.test_request_context("/"), SessionLocal() as s:
        cfg = config(s)
        if not cfg.get("calendar"):
            return cfg
        st = store()
        try:
            with st.pool():
                cfg["count"] = _update(s, st, cfg)
        except CalendarError as error:
            cfg.update(error=str(error), error_at=_now())
        except StoreError as error:
            cfg.update(error=gettext("The Mac's Calendar didn't take the change: %(why)s",
                                     why=str(error) or "?"), error_at=_now())
        except Exception as error:  # noqa: BLE001 — told on the calendar, never takes the app down
            app.logger.exception("mac calendar: the update failed")
            cfg.update(error=gettext("The Mac's Calendar didn't take the change: %(why)s", why=str(error) or "?"),
                       error_at=_now())
        else:
            cfg.update(error="", last_ok=_now())
        _save(s, cfg)
        return cfg


def _window() -> tuple[date, date]:
    from .lab_calendar import FEED_AHEAD_DAYS, FEED_BACK_DAYS
    today = date.today()
    return today - timedelta(days=FEED_BACK_DAYS), today + timedelta(days=FEED_AHEAD_DAYS + 1)


def _update(s, st, cfg: dict) -> int:
    from . import i18n, lab_calendar
    from .models import UserAccount
    user = s.scalar(select(UserAccount).where(UserAccount.username == cfg.get("user")))
    if user is None or user.disabled:
        raise CalendarError(gettext("%(who)s can no longer sign in. Choose Stop, then start it again.",
                                    who=cfg.get("user") or "?"))
    if st.access() != "granted":
        raise CalendarError(_not_allowed())
    found = st.find(cfg["calendar"], cfg.get("source", ""), cfg.get("title") or TITLE)
    if found is None:
        raise CalendarError(gettext("The calendar %(name)s is no longer on this Mac. Choose Stop, then choose another.",
                                    name=cfg.get("title") or TITLE))
    cfg["calendar"] = found   # the same calendar, perhaps under a new identifier
    g.lang = i18n.language_for(s, user.username)
    items, _name = lab_calendar.feed_items(s, user, "lab" if cfg.get("scope") == "lab" else "mine")
    first, last = _window()
    wanted = [e for e in map(entry, items) if e is not None and _starts_from(e, first)]
    existing = [(handle, e) for handle, e in st.events(found, first, last) if _starts_from(e, first)]
    add, remove = plan(wanted, existing)
    for handle in remove:
        st.remove(handle)
    for e in add:
        st.add(found, e)
    if add or remove:
        st.commit()
    return len(wanted)


def _after(response):
    if (_started.is_set() and request.method in ("POST", "PUT", "PATCH", "DELETE")
            and response.status_code < 400 and not request.path.startswith(bp.url_prefix)):
        _nudge.set()
    return response


bp.after_app_request(_after)


def start_background(app, first_delay: int = 60, every: int = UPDATE_EVERY) -> threading.Thread | None:
    """The desktop app's timer (desktop.py); nothing where there's no EventKit."""
    if not available():
        return None

    def loop():
        time.sleep(first_delay)
        while True:
            try:
                update(app)
            except Exception:  # noqa: BLE001 — never take the app down
                app.logger.exception("mac calendar: the update failed")
            if _nudge.wait(every):
                time.sleep(SETTLE)
                _nudge.clear()

    _started.set()
    thread = threading.Thread(target=loop, name="mac-calendar", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------- the dialog

@bp.before_request
def _only_here():
    if not offered():
        abort(404)
    if g.get("user") is None:
        return jsonify({"ok": False, "error": gettext("Sign in first.")}), 401
    return None


def _status(s, st) -> dict:
    from .models import UserAccount
    cfg = config(s)
    with st.pool():
        allowed = st.access()
        out = {"ok": True, "access": allowed, "setup": None, "calendars": [], "accounts": []}
        if allowed == "granted":
            out["calendars"] = [c for c in st.calendars() if dedicated(c["title"])]
            out["accounts"] = st.accounts()
    if cfg.get("calendar"):
        owner = s.scalar(select(UserAccount).where(UserAccount.username == cfg.get("user")))
        out["setup"] = {"scope": cfg.get("scope") or "mine", "title": cfg.get("title") or TITLE,
                        "account": cfg.get("account", ""), "count": cfg.get("count"),
                        "last_ok": cfg.get("last_ok", ""), "error": cfg.get("error", ""),
                        "created": bool(cfg.get("created")), "mine": cfg.get("user") == g.user.username,
                        "who": (owner.display_name or owner.username) if owner else cfg.get("user", "")}
    return out


def _error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


@bp.get("")
def status():
    with _lock, SessionLocal() as s:
        return jsonify(_status(s, store()))


@bp.post("")
def change():
    """allow (ask the Mac), start (a scope and a target: "new:<account>" or
    "cal:<calendar>"), scope, now (update now) or stop."""
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    scope = "lab" if data.get("scope") == "lab" else "mine"
    st = store()
    with _lock:
        with SessionLocal() as s:
            cfg = config(s)
            if action == "allow":
                with st.pool():
                    if st.access() == "ask":
                        st.request_access()
            elif action == "start":
                if cfg.get("calendar"):
                    return _error(gettext("It is already on. Choose Stop first to send it somewhere else."))
                try:
                    with st.pool():
                        if st.access() != "granted":
                            raise CalendarError(_not_allowed())
                        cfg = _start(st, str(data.get("target") or ""), scope)
                except CalendarError as error:
                    return _error(str(error))
                _save(s, cfg)
            elif action == "scope":
                if cfg.get("calendar"):
                    cfg["scope"] = scope
                    _save(s, cfg)
            elif action == "stop":
                if cfg.get("calendar"):
                    try:
                        with st.pool():
                            _stop(st, cfg)
                    except StoreError as error:
                        return _error(gettext("The Mac's Calendar didn't take the change: %(why)s",
                                              why=str(error) or "?"))
                    _save(s, {})
            elif action != "now":
                return _error(gettext("Unknown action."))
        if action in ("start", "scope", "now"):
            update(current_app._get_current_object())
        with SessionLocal() as s:
            return jsonify(_status(s, st))


def _start(st, target: str, scope: str) -> dict:
    kind, _, ident = target.partition(":")
    base = {"user": g.user.username, "scope": scope}
    if kind == "new":
        account = next((a for a in st.accounts() if a["id"] == ident), None)
        if account is not None:
            try:
                calendar = st.create(ident, TITLE)
            except StoreError:
                raise CalendarError(gettext("%(account)s doesn't let this Mac add a calendar. Make one called BioManager there (for Google: in Google Calendar on the web, Settings → Add calendar → Create new calendar), then choose it here once it shows.", account=account["title"])) from None  # noqa: E501
            return {**base, "calendar": calendar, "source": ident, "title": TITLE, "account": account["title"],
                    "created": True}
    elif kind == "cal":
        chosen = next((c for c in st.calendars() if c["id"] == ident), None)
        if chosen is not None and not dedicated(chosen["title"]):
            raise CalendarError(gettext("Only a calendar called BioManager can be chosen: everything in it is replaced by BioManager's."))  # noqa: E501
        if chosen is not None:
            return {**base, "calendar": chosen["id"], "source": chosen["source"], "title": chosen["title"],
                    "account": chosen["account"], "created": False}
    raise CalendarError(gettext("Choose where the calendar goes."))


def _stop(st, cfg: dict) -> None:
    """A calendar BioManager made goes; one the person made is emptied of
    what BioManager put there."""
    found = st.find(cfg["calendar"], cfg.get("source", ""), cfg.get("title") or TITLE)
    if found is None:
        return
    if cfg.get("created"):
        st.remove_calendar(found)
        return
    first, last = _window()
    events = st.events(found, first, last)
    for handle, _e in events:
        st.remove(handle)
    if events:
        st.commit()


# ---------------------------------------------------------------- EventKit

def _why(error) -> str:
    try:
        return str(error.localizedDescription()) if error is not None else ""
    except Exception:  # noqa: BLE001
        return str(error or "")


class EventKitStore:
    """Apple's calendar store, through PyObjC. Calendars are named by their
    identifier; events are EventKit's own objects, only handed back."""

    def __init__(self):
        import EventKit
        import Foundation
        self.ek, self.fd = EventKit, Foundation
        self.store = EventKit.EKEventStore.alloc().init()

    def pool(self):
        # A thread Python started has no autorelease pool of its own.
        try:
            import objc
            return objc.autorelease_pool()
        except (ImportError, AttributeError):
            return nullcontext()

    def access(self) -> str:
        status = int(self.ek.EKEventStore.authorizationStatusForEntityType_(self.ek.EKEntityTypeEvent))
        # Not asked yet, restricted (a managed Mac), denied, allowed; 4 is
        # macOS 14's "add events only", which can't see what it added.
        return {0: "ask", 1: "restricted", 2: "denied", 3: "granted", 4: "ask"}.get(status, "denied")

    def request_access(self, timeout: float = 120.0) -> bool:
        done, answer = threading.Event(), {}

        def handler(granted, _error):
            answer["granted"] = bool(granted)
            done.set()

        if hasattr(self.store, "requestFullAccessToEventsWithCompletion_"):
            self.store.requestFullAccessToEventsWithCompletion_(handler)   # macOS 14 and later
        else:
            self.store.requestAccessToEntityType_completion_(self.ek.EKEntityTypeEvent, handler)
        done.wait(timeout)
        if answer.get("granted"):
            # A store made before access was given sees no calendars.
            self.store = self.ek.EKEventStore.alloc().init()
        return bool(answer.get("granted"))

    def _sources(self) -> list:
        return [src for src in (self.store.sources() or []) if int(src.sourceType()) in WRITABLE_SOURCES]

    @staticmethod
    def _account(src) -> str:
        """An account's name as the Mac's Calendar shows it: EventKit calls
        the Mac's own (EKSourceTypeLocal) "Default"."""
        if int(src.sourceType()) == 0:
            return gettext("On My Mac")
        return str(src.title())

    def accounts(self) -> list[dict]:
        return [{"id": str(src.sourceIdentifier()), "title": self._account(src)} for src in self._sources()]

    def calendars(self) -> list[dict]:
        out = []
        for cal in self.store.calendarsForEntityType_(self.ek.EKEntityTypeEvent) or []:
            if not cal.allowsContentModifications() or cal.isImmutable():
                continue
            src = cal.source()
            out.append({"id": str(cal.calendarIdentifier()), "title": str(cal.title()),
                        "account": self._account(src) if src is not None else "",
                        "source": str(src.sourceIdentifier()) if src is not None else ""})
        return out

    def find(self, calendar: str, source: str, title: str) -> str | None:
        self.store.refreshSourcesIfNecessary()
        if self.store.calendarWithIdentifier_(calendar) is not None:
            return calendar
        return next((c["id"] for c in self.calendars() if c["source"] == source and c["title"] == title), None)

    def create(self, source: str, title: str) -> str:
        src = next((x for x in self._sources() if str(x.sourceIdentifier()) == source), None)
        if src is None:
            raise StoreError("")
        cal = self.ek.EKCalendar.calendarForEntityType_eventStore_(self.ek.EKEntityTypeEvent, self.store)
        cal.setTitle_(title)
        cal.setSource_(src)
        ok, error = self.store.saveCalendar_commit_error_(cal, True, None)
        if not ok:
            raise StoreError(_why(error))
        return str(cal.calendarIdentifier())

    def remove_calendar(self, calendar: str) -> None:
        cal = self.store.calendarWithIdentifier_(calendar)
        if cal is not None:
            ok, error = self.store.removeCalendar_commit_error_(cal, True, None)
            if not ok:
                raise StoreError(_why(error))

    # Days through the Mac's own calendar both ways, as EventKit keeps an
    # all-day event: whatever its time zone, a day goes in and comes back out.
    def _date(self, day: date, end_of_day: bool = False):
        parts = self.fd.NSDateComponents.alloc().init()
        parts.setYear_(day.year)
        parts.setMonth_(day.month)
        parts.setDay_(day.day)
        if end_of_day:
            parts.setHour_(23)
            parts.setMinute_(59)
            parts.setSecond_(59)
        return self.fd.NSCalendar.currentCalendar().dateFromComponents_(parts)

    def _day(self, moment) -> tuple[date, bool]:
        fd = self.fd
        units = (fd.NSCalendarUnitYear | fd.NSCalendarUnitMonth | fd.NSCalendarUnitDay | fd.NSCalendarUnitHour
                 | fd.NSCalendarUnitMinute | fd.NSCalendarUnitSecond)
        parts = fd.NSCalendar.currentCalendar().components_fromDate_(units, moment)
        return (date(parts.year(), parts.month(), parts.day()),
                (parts.hour(), parts.minute(), parts.second()) == (0, 0, 0))

    def _entry(self, ev) -> Entry:
        title, notes = str(ev.title() or "").strip(), _clean(str(ev.notes() or ""))
        if ev.isAllDay():
            start, _ = self._day(ev.startDate())
            end, at_midnight = self._day(ev.endDate())
            if at_midnight and end > start:   # kept as the next day's midnight
                end -= timedelta(days=1)
            return Entry(title, True, start, max(end, start), notes)
        return Entry(title, False, round(ev.startDate().timeIntervalSince1970()),
                     round(ev.endDate().timeIntervalSince1970()), notes)

    def events(self, calendar: str, first: date, last: date) -> list[tuple[object, Entry]]:
        cal = self.store.calendarWithIdentifier_(calendar)
        if cal is None:
            return []
        query = self.store.predicateForEventsWithStartDate_endDate_calendars_(self._date(first), self._date(last), [cal])
        return [(ev, self._entry(ev)) for ev in (self.store.eventsMatchingPredicate_(query) or [])]

    def add(self, calendar: str, e: Entry) -> None:
        ev = self.ek.EKEvent.eventWithEventStore_(self.store)
        ev.setCalendar_(self.store.calendarWithIdentifier_(calendar))
        ev.setTitle_(e.title)
        if e.notes:
            ev.setNotes_(e.notes)
        ev.setAllDay_(e.allday)
        if e.allday:
            ev.setStartDate_(self._date(e.start))
            ev.setEndDate_(self._date(e.end, end_of_day=True))
        else:
            # A moment, kept as one. In the Mac's own zone, macOS 14 gives
            # an event across the night the clocks change back an hour
            # longer than it took (scripts/mac-calendar-check/live.py);
            # the Mac's Calendar still shows it in the Mac's time.
            ev.setTimeZone_(self.fd.NSTimeZone.timeZoneWithName_("UTC"))
            ev.setStartDate_(self.fd.NSDate.dateWithTimeIntervalSince1970_(e.start))
            ev.setEndDate_(self.fd.NSDate.dateWithTimeIntervalSince1970_(e.end))
        ok, error = self.store.saveEvent_span_commit_error_(ev, self.ek.EKSpanThisEvent, False, None)
        if not ok:
            raise StoreError(_why(error))

    def remove(self, ev) -> None:
        ok, error = self.store.removeEvent_span_commit_error_(ev, self.ek.EKSpanThisEvent, False, None)
        if not ok:
            raise StoreError(_why(error))

    def commit(self) -> None:
        ok, error = self.store.commit_(None)
        if not ok:
            raise StoreError(_why(error))
