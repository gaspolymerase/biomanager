"""The calendars a person brings in from outside: subscriptions to a
remote .ics address (read with `icalendar`, kept ten minutes) and a linked
Google Calendar (OAuth, read only), each turned into the calendar page's
item shape. Only the calendar's pages call these (app/calendar_routes.py).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import CalendarSubscription, GoogleCalendarLink


# ---------------------------------------------------------------------------
# Calendar subscriptions (ICS) — fetch a remote .ics URL, parse it with the
# `icalendar` library, cache the parsed events for ~10 min, and surface them
# in TOAST UI Calendar's schedule shape.
# ---------------------------------------------------------------------------

ICS_CACHE_TTL = timedelta(minutes=10)


def fetch_ics_subscription(sub: "CalendarSubscription", session, force: bool = False) -> list[dict]:
    """Fetch + parse `sub.url`. Returns a list of TOAST UI schedule dicts.
    Honors a 10-minute cache so repeated /calendar/events.json hits don't
    hammer the source. Errors are stored on `sub.last_error` (non-fatal —
    we just return whatever the previous cache had)."""
    import json as _json
    now = datetime.utcnow()

    if (
        not force
        and sub.cached_payload
        and sub.last_fetched_at
        and now - sub.last_fetched_at < ICS_CACHE_TTL
    ):
        try:
            return _json.loads(sub.cached_payload)
        except _json.JSONDecodeError:
            pass  # fall through to refetch

    try:
        events = _do_fetch_ics(sub.url, sub.color or "#10b981", sub.id)
        sub.cached_payload = _json.dumps(events)
        sub.last_fetched_at = now
        sub.last_error = ""
        session.add(sub)
        session.commit()
        return events
    except Exception as exc:
        sub.last_error = f"{type(exc).__name__}: {exc}"[:500]
        session.add(sub)
        session.commit()
        # Return whatever's cached if available — better than nothing.
        if sub.cached_payload:
            try:
                return _json.loads(sub.cached_payload)
            except _json.JSONDecodeError:
                return []
        return []


def _do_fetch_ics(url: str, color: str, sub_id: int) -> list[dict]:
    """Fetch one ICS URL and translate VEVENTs into TOAST UI schedule shape."""
    import urllib.request
    from icalendar import Calendar as _Calendar  # local import to keep cold-start fast

    req = urllib.request.Request(url, headers={"User-Agent": "BioManager-Calendar/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()

    cal = _Calendar.from_ical(raw)
    out: list[dict] = []
    for component in cal.walk("VEVENT"):
        try:
            uid = str(component.get("UID") or f"sub-{sub_id}-{len(out)}")
            summary = str(component.get("SUMMARY") or "(no title)")
            description = str(component.get("DESCRIPTION") or "")
            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND") or dtstart
            if dtstart is None:
                continue
            start_val = dtstart.dt
            end_val = dtend.dt if dtend is not None else start_val
            is_all_day = isinstance(start_val, date) and not isinstance(start_val, datetime)
            if is_all_day:
                start_iso = datetime.combine(start_val, datetime.min.time()).isoformat()
                # ICS DTEND for all-day is exclusive; subtract a day for display.
                effective_end = end_val - timedelta(days=1) if isinstance(end_val, date) and end_val > start_val else start_val
                end_iso = datetime.combine(effective_end, datetime.max.time()).isoformat()
            else:
                start_iso = (start_val if isinstance(start_val, datetime) else datetime.combine(start_val, datetime.min.time())).isoformat()
                end_iso = (end_val if isinstance(end_val, datetime) else datetime.combine(end_val, datetime.max.time())).isoformat()
            out.append({
                "id": f"sub-{sub_id}-{uid}",
                "kind": "subscription",
                "calendarId": f"sub-{sub_id}",
                "title": summary,
                "category": "allday" if is_all_day else "time",
                "isAllday": is_all_day,
                "start": start_iso,
                "end": end_iso,
                "backgroundColor": color,
                "borderColor": color,
                "body": description,
                "isReadOnly": True,
                "raw": {"subId": sub_id, "uid": uid},
            })
        except Exception:
            # Skip individual broken VEVENTs but keep the rest.
            continue
    return out


# ---------------------------------------------------------------------------
# Google Calendar OAuth (Pass 3)
# ---------------------------------------------------------------------------

GOOGLE_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def google_oauth_configured() -> bool:
    import os
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def google_client_config(redirect_uri: str) -> dict:
    """The dict that google_auth_oauthlib expects when no client_secrets.json
    file is present — we just synthesize it from env vars."""
    import os
    return {
        "web": {
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def fetch_google_calendar_items(link: "GoogleCalendarLink", session, start_dt=None, end_dt=None) -> list[dict]:
    """Read events from one connected Google Calendar. Returns TOAST UI
    schedule shape. Refresh-token-based — we never need an interactive login
    after the initial OAuth."""
    import os
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=link.access_token or None,
        refresh_token=link.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
        scopes=GOOGLE_OAUTH_SCOPES,
    )
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # Default to ±60 days if no window given.
    if start_dt is None:
        start_dt = datetime.utcnow() - timedelta(days=60)
    if end_dt is None:
        end_dt = datetime.utcnow() + timedelta(days=60)
    time_min = start_dt.isoformat() + "Z" if start_dt.tzinfo is None else start_dt.isoformat()
    time_max = end_dt.isoformat() + "Z" if end_dt.tzinfo is None else end_dt.isoformat()

    resp = service.events().list(
        calendarId=link.calendar_id or "primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=500,
    ).execute()

    # Persist any refreshed access token so we don't refresh on every call.
    if creds.token and creds.token != link.access_token:
        link.access_token = creds.token
        link.token_expiry = creds.expiry
    link.last_synced_at = datetime.utcnow()
    session.add(link)
    session.commit()

    out: list[dict] = []
    for ev in resp.get("items", []):
        try:
            start = ev["start"]
            end = ev.get("end", start)
            is_all_day = "date" in start
            if is_all_day:
                start_iso = datetime.fromisoformat(start["date"]).isoformat()
                # Google's all-day end is exclusive — subtract a day for display.
                end_date = datetime.fromisoformat(end["date"]) - timedelta(days=1)
                end_iso = datetime.combine(end_date.date(), datetime.max.time()).isoformat()
            else:
                start_iso = start["dateTime"]
                end_iso = end.get("dateTime", start["dateTime"])
            out.append({
                "id": f"google-{link.id}-{ev['id']}",
                "kind": "google",
                "calendarId": "google",
                "title": ev.get("summary") or "(no title)",
                "category": "allday" if is_all_day else "time",
                "isAllday": is_all_day,
                "start": start_iso,
                "end": end_iso,
                "backgroundColor": link.color or "#ef4444",
                "borderColor": link.color or "#ef4444",
                "body": ev.get("description") or "",
                "isReadOnly": True,
                "raw": {"linkId": link.id, "googleEventId": ev["id"], "htmlLink": ev.get("htmlLink", "")},
            })
        except Exception:
            continue
    return out
