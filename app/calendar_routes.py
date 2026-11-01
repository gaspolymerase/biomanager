"""The lab calendar's events and to-dos: the calendar page itself, its events
feed (/calendar/events.json), adding, changing, ticking and deleting an
event or a to-do, calendar subscriptions (an .ics link) and the Google
Calendar link. Who may see and change a to-do is decided here
(task_visible_clause, access.can_edit_task, task_denied), and the Board, the
devices' sync and Home ask the same functions.

Repeats, protocol timelines, equipment booking and the dates the other
databases put on the calendar are app/lab_calendar.py's, a blueprint under
the same /calendar.

These pages hang on the application object from app/flask_app.py and keep
the endpoint names every page links to ("calendar", "calendar_item_create",
...); tests/test_url_map.py holds them to it. They take what every page
needs from app/web.py and never import app.py, which imports them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import flash, g, has_request_context, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select

from .db import SessionLocal
from . import access, lab
from .i18n import gettext
from .models import (
    CalendarEvent,
    CalendarRepeat,
    CalendarSubscription,
    GoogleCalendarLink,
    Experiment,
    TaskItem,
)
from .services import (
    derive_auto_calendar_items,
    fetch_ics_subscription,
    fetch_google_calendar_items,
    google_client_config,
    google_oauth_configured,
    GOOGLE_OAUTH_SCOPES,
    parse_date,
)
from . import experiment_steps, lab_calendar
from . import groups as project_groups
from .flask_app import app
from .zebrafish_routes import zebrafish_home_summary
from .web import login_required


@app.route("/calendar", methods=["GET"])
@login_required
def calendar():
    """Calendar page — TOAST UI Calendar mount + custom list view.
    Data is fetched async from /calendar/events.json so the page itself is light."""
    with SessionLocal() as db_session:
        cal_data = {
            "me": g.user.username,
            "admin": g.user.role == "admin",
            "people": lab_calendar.people(db_session),
            "equipment": lab_calendar.equipment_list(db_session),
            "experiments": [{"id": e.id, "name": e.name} for e in db_session.scalars(
                select(Experiment).where(Experiment.status.in_(["active", "paused", "planned"]))
                .order_by(Experiment.name))],
            "colors": lab_calendar.COLORS,
        }
    return render_template("calendar.html", cal_data=cal_data)


def _serialize_calendar_event(e: CalendarEvent) -> dict:
    """Translate a CalendarEvent row into TOAST UI Calendar's schedule shape."""
    if e.start_at and e.end_at:
        start_iso, end_iso = e.start_at.isoformat(), e.end_at.isoformat()
        is_all_day = bool(e.is_all_day)
    else:
        d = e.event_date
        start_iso = datetime.combine(d, datetime.min.time()).isoformat()
        end_iso = datetime.combine(d, datetime.max.time()).isoformat()
        is_all_day = True
    return {
        "id": f"event-{e.id}",
        "kind": "event",
        "calendarId": "events",
        "title": e.title or "(untitled)",
        "category": "allday" if is_all_day else "time",
        "isAllday": is_all_day,
        "start": start_iso,
        "end": end_iso,
        "backgroundColor": e.color or "#7c3aed",
        "borderColor": e.color or "#7c3aed",
        "body": e.description or "",
        "raw": {
            "rowId": e.id,
            "owner": e.owner or "",
            "animalId": e.animal_id_fk,
            "eventType": e.event_type or "",
            # Whose event: "0" its owner's alone, "1" the lab's, "g<id>" a group's.
            "audience": project_groups.record_value(e),
            "audienceLabel": project_groups.label(e.is_shared, e.share_group_id, personal="", lab=""),
            "readOnly": has_request_context() and g.get("user") is not None and not lab_calendar.event_can_edit(e),
        },
    }


def task_visible_clause(user=None):
    """The to-dos a person sees: their own, the lab's, and their project
    groups' (app/groups.py)."""
    me = access.username(user)
    mine = sorted(project_groups.ids_of(user))
    lab_wide = TaskItem.is_shared.is_(True) & TaskItem.share_group_id.is_(None)
    clause = (TaskItem.owner == me) | lab_wide
    if mine:
        clause = clause | (TaskItem.is_shared.is_(True) & TaskItem.share_group_id.in_(mine))
    return clause


def task_denied(t) -> str:
    if t.is_shared and t.share_group_id:
        return gettext("That to-do is %(group)s's.",
                       group=project_groups.name_of(t.share_group_id) or gettext("a project group"))
    return gettext("That to-do is %(owner)s's. Ask them, or an admin, to make the change.",
                   owner=t.owner or gettext("someone else"))


def _serialize_task(t: TaskItem) -> dict:
    """Translate a TaskItem row into TOAST UI Calendar's schedule shape, with
    extra metadata our custom renderer uses to draw the done-checkbox."""
    if t.start_at and t.end_at:
        start_iso, end_iso = t.start_at.isoformat(), t.end_at.isoformat()
        is_all_day = False
    elif t.due_date:
        d = t.due_date
        start_iso = datetime.combine(d, datetime.min.time()).isoformat()
        end_iso = datetime.combine(d, datetime.max.time()).isoformat()
        is_all_day = True
    else:
        # No date at all → put it on today, all-day, so it doesn't get lost.
        d = date.today()
        start_iso = datetime.combine(d, datetime.min.time()).isoformat()
        end_iso = datetime.combine(d, datetime.max.time()).isoformat()
        is_all_day = True
    is_done = bool(t.done_at) or (t.status or "").lower() in ("done", "completed", "closed")
    return {
        "id": f"task-{t.id}",
        "kind": "task",
        "calendarId": "tasks",
        "title": t.title or "(untitled)",
        "category": "task",  # TOAST UI native task category — shows in task strip
        "isAllday": is_all_day,
        "start": start_iso,
        "end": end_iso,
        "backgroundColor": t.color or ("#94a3b8" if is_done else "#0ea5e9"),
        "borderColor": t.color or ("#94a3b8" if is_done else "#0ea5e9"),
        "body": t.notes or "",
        "raw": {
            "rowId": t.id,
            "owner": t.owner or "",
            "status": t.status or "todo",
            "done": is_done,
            "priority": t.priority or "medium",
            # Whose to-do it is: "0" its owner's, "1" the lab's, "g<id>" a group's.
            "audience": project_groups.record_value(t),
            "audienceLabel": project_groups.label(t.is_shared, t.share_group_id, personal="", lab="Lab"),
            "readOnly": has_request_context() and g.get("user") is not None and not access.can_edit_task(t),
        },
    }


def calendar_items(db_session, start: date | None, end: date | None, owner: str | None = None,
                   external: bool = True) -> list[dict]:
    """Everything on the calendar between two dates, as TOAST UI schedules:
    events (repeats expanded), to-dos, mouse colony dates, the other
    organisms and supplies, protocol steps, bookings and away days
    (app/lab_calendar.py), then connected calendars. With `owner`, only that
    person's own things (their phone feed can ask for that)."""
    today = date.today()
    start = start or today - timedelta(days=400)
    end = end or today + timedelta(days=400)
    ev_q = select(CalendarEvent).where(
        ((CalendarEvent.event_date >= start) & (CalendarEvent.event_date <= end))
        | ((CalendarEvent.event_date <= end) & CalendarEvent.id.in_(select(CalendarRepeat.event_id_fk))))
    # A to-do with no day shows on today; one kept in a list on the Board
    # (app/task_board.py) stays there.
    tk_q = select(TaskItem).where(
        (TaskItem.due_date.is_(None) & (TaskItem.board_list == ""))
        | ((TaskItem.due_date >= start) & (TaskItem.due_date <= end)))
    if owner is not None:
        ev_q = ev_q.where(CalendarEvent.owner == owner)
        tk_q = tk_q.where(TaskItem.owner == owner)
    else:
        # Someone's own events and to-dos are theirs; the lab's and a
        # group's are shared.
        ev_q = ev_q.where(lab_calendar.event_visible_clause())
        tk_q = tk_q.where(task_visible_clause())
    events = db_session.scalars(ev_q.order_by(CalendarEvent.event_date)).all()
    repeats = lab_calendar.repeats_by_event(db_session, [e.id for e in events])
    items: list[dict] = []
    for e in events:
        item = _serialize_calendar_event(e)
        repeat = repeats.get(e.id)
        if repeat is None:
            items.append(item)
            continue
        item["raw"]["repeat"] = lab_calendar.repeat_summary(repeat, e.event_date)
        for day in lab_calendar.occurrences(e.event_date, repeat, start, end):
            shift = timedelta(days=(day - e.event_date).days)
            copy = dict(item, id=f"{item['id']}@{day.isoformat()}",
                        start=(datetime.fromisoformat(item["start"]) + shift).isoformat(),
                        end=(datetime.fromisoformat(item["end"]) + shift).isoformat(),
                        raw=dict(item["raw"], occurrence=day.isoformat()))
            items.append(copy)
    items += [_serialize_task(t) for t in db_session.scalars(tk_q.order_by(TaskItem.due_date)).all()]

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())
    features = lab.request_features()
    if owner is None and features.get("colony", True):
        # Mouse colony dates carry no owner, so a personal feed leaves them out.
        items += derive_auto_calendar_items(db_session, start_dt, end_dt)
    zebrafish = zebrafish_home_summary() if features.get("zebrafish", True) else None
    items += lab_calendar.agenda_items(db_session, start, end, features, zebrafish, owner=owner)
    items += lab_calendar.protocol_items(db_session, start, end, owner=owner)
    if features.get("colony", True):
        items += experiment_steps.calendar_items(db_session, start, end, owner=owner)
    items += lab_calendar.booking_items(db_session, start, end, owner=owner)
    items += lab_calendar.absence_items(db_session, start, end, owner=owner)

    if external and g.user is not None:
        # Connected calendars are the viewer's own, and read-only here.
        subs = db_session.scalars(
            select(CalendarSubscription)
            .where(CalendarSubscription.owner == g.user.username)
            .where(CalendarSubscription.enabled == True)  # noqa: E712
        ).all()
        for sub in subs:
            items.extend(fetch_ics_subscription(sub, db_session))
        try:
            from .services import fetch_google_calendar_items  # optional, may not exist yet
            for link in db_session.scalars(
                select(GoogleCalendarLink)
                .where(GoogleCalendarLink.owner == g.user.username)
                .where(GoogleCalendarLink.enabled == True)  # noqa: E712
            ).all():
                items.extend(fetch_google_calendar_items(link, db_session, start_dt, end_dt))
        except ImportError:
            pass
        except Exception as exc:  # don't break the feed for transient Google errors
            app.logger.warning("Google Calendar fetch failed: %s", exc)
    return items


@app.route("/calendar/events.json")
@login_required
def calendar_events_json():
    """The calendar page's feed for ?start=&end= (ISO dates or date-times),
    plus who is away soon and what of theirs falls due meanwhile."""
    def _day(v):
        try:
            return date.fromisoformat((v or "")[:10]) if v else None
        except ValueError:
            return None

    with SessionLocal() as db_session:
        items = calendar_items(db_session, _day(request.args.get("start")), _day(request.args.get("end")))
        features = lab.request_features()
        cover = lab_calendar.cover_report(
            db_session, features, zebrafish_home_summary() if features.get("zebrafish", True) else None)
        db_session.commit()
        return jsonify({"ok": True, "items": items, "cover": cover})


# ---------------------------------------------------------------------------
# Calendar subscriptions (ICS) — CRUD
# ---------------------------------------------------------------------------


@app.route("/calendar/subscriptions", methods=["GET", "POST"])
@login_required
def calendar_subscriptions():
    """GET → list current user's subscriptions. POST → create a new one."""
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            name = (payload.get("name") or "").strip() or "Subscription"
            url = (payload.get("url") or "").strip()
            color = (payload.get("color") or "#10b981").strip()
            if not url:
                return jsonify({"ok": False, "error": "url required"}), 400
            # Allow webcal:// URLs — most calendars publish their iCal feed
            # with that scheme; swap to https for urllib.
            if url.startswith("webcal://"):
                url = "https://" + url[len("webcal://"):]
            sub = CalendarSubscription(owner=owner, name=name, url=url, color=color, enabled=True)
            db_session.add(sub)
            db_session.commit()
            # Eagerly fetch once so the user sees instant results.
            items = fetch_ics_subscription(sub, db_session, force=True)
            return jsonify({
                "ok": True,
                "subscription": {
                    "id": sub.id, "name": sub.name, "url": sub.url, "color": sub.color,
                    "enabled": sub.enabled, "last_error": sub.last_error,
                    "last_fetched_at": sub.last_fetched_at.isoformat() if sub.last_fetched_at else None,
                    "event_count": len(items),
                },
            })

        subs = db_session.scalars(
            select(CalendarSubscription).where(CalendarSubscription.owner == owner).order_by(CalendarSubscription.created_at)
        ).all()
        return jsonify({
            "ok": True,
            "subscriptions": [
                {
                    "id": s.id, "name": s.name, "url": s.url, "color": s.color,
                    "enabled": s.enabled, "last_error": s.last_error,
                    "last_fetched_at": s.last_fetched_at.isoformat() if s.last_fetched_at else None,
                }
                for s in subs
            ],
        })


@app.route("/calendar/subscriptions/<int:sub_id>", methods=["POST"])
@login_required
def calendar_subscription_update(sub_id: int):
    """Update a subscription (toggle enabled, rename, recolor, change URL)."""
    owner = g.user.username if g.user else ""
    payload = request.get_json(silent=True) or {}
    with SessionLocal() as db_session:
        sub = db_session.get(CalendarSubscription, sub_id)
        if sub is None or sub.owner != owner:
            return jsonify({"ok": False}), 404
        if "name" in payload: sub.name = (payload["name"] or "").strip() or sub.name
        if "color" in payload: sub.color = payload["color"] or sub.color
        if "url" in payload and payload["url"]:
            new_url = payload["url"].strip()
            if new_url.startswith("webcal://"):
                new_url = "https://" + new_url[len("webcal://"):]
            sub.url = new_url
            sub.cached_payload = ""  # invalidate cache on URL change
        if "enabled" in payload: sub.enabled = bool(payload["enabled"])
        db_session.commit()
        return jsonify({"ok": True})


@app.route("/calendar/subscriptions/<int:sub_id>/refresh", methods=["POST"])
@login_required
def calendar_subscription_refresh(sub_id: int):
    """Force a re-fetch of one subscription, bypassing the 10-min cache."""
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        sub = db_session.get(CalendarSubscription, sub_id)
        if sub is None or sub.owner != owner:
            return jsonify({"ok": False}), 404
        items = fetch_ics_subscription(sub, db_session, force=True)
        return jsonify({"ok": True, "event_count": len(items), "last_error": sub.last_error})


@app.route("/calendar/subscriptions/<int:sub_id>/delete", methods=["POST"])
@login_required
def calendar_subscription_delete(sub_id: int):
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        sub = db_session.get(CalendarSubscription, sub_id)
        if sub is None or sub.owner != owner:
            return jsonify({"ok": False}), 404
        db_session.delete(sub)
        db_session.commit()
        return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Google Calendar OAuth
# ---------------------------------------------------------------------------


def _google_redirect_uri() -> str:
    return url_for("calendar_google_callback", _external=True)


@app.route("/calendar/google/status")
@login_required
def calendar_google_status():
    """Tell the UI whether server-side OAuth is configured and whether THIS
    user has already connected. The status panel in the calendar uses this
    to pick between "Connect", "Connected — refresh/disconnect", and
    "not configured" states."""
    if not google_oauth_configured():
        return jsonify({"ok": True, "configured": False, "connected": False})
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        link = db_session.scalar(select(GoogleCalendarLink).where(GoogleCalendarLink.owner == owner))
        if link is None:
            return jsonify({"ok": True, "configured": True, "connected": False})
        return jsonify({
            "ok": True, "configured": True, "connected": True,
            "email": link.google_email or "",
            "calendar_id": link.calendar_id,
            "last_synced_at": link.last_synced_at.isoformat() if link.last_synced_at else None,
        })


@app.route("/calendar/google/connect")
@login_required
def calendar_google_connect():
    """Kick off the OAuth dance. Redirects the user to Google's consent screen."""
    if not google_oauth_configured():
        flash(gettext("Google Calendar integration isn't configured on this server."), "error")
        return redirect(url_for("calendar"))
    from google_auth_oauthlib.flow import Flow
    redirect_uri = _google_redirect_uri()
    flow = Flow.from_client_config(
        google_client_config(redirect_uri),
        scopes=GOOGLE_OAUTH_SCOPES,
        redirect_uri=redirect_uri,
    )
    # access_type=offline → we get a refresh_token; prompt=consent ensures we
    # always receive a fresh refresh_token even if the user already approved.
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["google_oauth_state"] = state
    return redirect(auth_url)


@app.route("/calendar/google/callback")
@login_required
def calendar_google_callback():
    """OAuth redirect target. Exchange the code → tokens, save the link."""
    if not google_oauth_configured():
        return redirect(url_for("calendar"))
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build

    state = session.pop("google_oauth_state", None)
    redirect_uri = _google_redirect_uri()
    flow = Flow.from_client_config(
        google_client_config(redirect_uri),
        scopes=GOOGLE_OAUTH_SCOPES,
        redirect_uri=redirect_uri,
        state=state,
    )
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:
        app.logger.warning("Google OAuth callback failed: %s", exc)
        flash(gettext("Google sign-in failed: %(error)s", error=exc), "error")
        return redirect(url_for("calendar"))

    creds = flow.credentials
    # Fetch the user's email so we can show "Connected as alice@…".
    email = ""
    try:
        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        email = svc.userinfo().get().execute().get("email", "")
    except Exception:
        pass

    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        link = db_session.scalar(select(GoogleCalendarLink).where(GoogleCalendarLink.owner == owner))
        if link is None:
            link = GoogleCalendarLink(owner=owner)
        link.refresh_token = creds.refresh_token or link.refresh_token
        link.access_token = creds.token or ""
        link.token_expiry = creds.expiry
        link.google_email = email
        link.calendar_id = "primary"
        link.enabled = True
        if not link.refresh_token:
            db_session.expunge(link)
            flash(gettext("Google didn't return a refresh token. Revoke access at myaccount.google.com/permissions and try connecting again."), "warning")
            return redirect(url_for("calendar"))
        db_session.add(link)
        db_session.commit()
    flash(gettext("Connected Google Calendar for %(email)s.", email=email), "success")
    return redirect(url_for("calendar"))


@app.route("/calendar/google/refresh", methods=["POST"])
@login_required
def calendar_google_refresh():
    """Force a fresh pull of events from Google (bypasses any in-memory cache
    on the next /calendar/events.json hit)."""
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        link = db_session.scalar(select(GoogleCalendarLink).where(GoogleCalendarLink.owner == owner))
        if link is None:
            return jsonify({"ok": False, "error": "not connected"}), 404
        try:
            items = fetch_google_calendar_items(link, db_session)
            return jsonify({"ok": True, "event_count": len(items)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/calendar/google/disconnect", methods=["POST"])
@login_required
def calendar_google_disconnect():
    owner = g.user.username if g.user else ""
    with SessionLocal() as db_session:
        link = db_session.scalar(select(GoogleCalendarLink).where(GoogleCalendarLink.owner == owner))
        if link is None:
            return jsonify({"ok": True})
        db_session.delete(link)
        db_session.commit()
    return jsonify({"ok": True})


@app.route("/calendar/items", methods=["POST"])
@login_required
def calendar_item_create():
    """Create either an event or a task. JSON body:
       { kind: 'event'|'task', title, start, end, isAllday, color, description, ... }"""
    from datetime import datetime as _dt
    payload = request.get_json(silent=True) or {}
    kind = (payload.get("kind") or "event").lower()
    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400

    def _parse(v):
        if not v:
            return None
        try:
            return _dt.fromisoformat(v.replace("Z", "+00:00").replace("+00:00", ""))
        except ValueError:
            return None

    start = _parse(payload.get("start"))
    end = _parse(payload.get("end"))
    is_all_day = bool(payload.get("isAllday", True))
    if start and end and end < start:
        # Saved like that, it would vanish from Month and Week (List only).
        return jsonify({"ok": False, "error": gettext("It has to end after it starts.")}), 400
    color = (payload.get("backgroundColor") or payload.get("color") or "").strip()
    owner = g.user.username if g.user else ""

    with SessionLocal() as db_session:
        if kind == "task":
            row = TaskItem(
                title=title,
                due_date=start.date() if start else None,
                start_at=None if is_all_day else start,
                end_at=None if is_all_day else end,
                status=payload.get("status", "todo"),
                priority=payload.get("priority", "medium"),
                color=color,
                notes=payload.get("body", "") or payload.get("description", ""),
                owner=owner,
            )
            # A lab to-do ("1") or a project group's ("g<id>"); else yours.
            refused = project_groups.apply(row, payload.get("audience", "0"))
            if refused:
                return jsonify({"ok": False, "error": refused}), 403
        else:
            row = CalendarEvent(
                title=title,
                event_date=start.date() if start else date.today(),
                start_at=None if is_all_day else start,
                end_at=None if is_all_day else end,
                is_all_day=is_all_day,
                event_type=payload.get("event_type", "experiment"),
                color=color,
                description=payload.get("body", "") or payload.get("description", ""),
                owner=owner,
            )
            # The lab's ("1", the usual), a project group's ("g<id>") or yours ("0").
            refused = project_groups.apply(row, payload.get("audience", "1"))
            if refused:
                return jsonify({"ok": False, "error": refused}), 403
        # "Change this one only": one date of a repeating event becomes an
        # event of its own (this one), and the series skips that date.
        split = payload.get("split_from") if kind != "task" else None
        if isinstance(split, dict):
            series = db_session.get(CalendarEvent, lab_calendar._int(split.get("event_id")))
            day = lab_calendar._date(split.get("date"))
            repeat = series and db_session.scalar(
                select(CalendarRepeat).where(CalendarRepeat.event_id_fk == series.id))
            if repeat is None or day is None:
                return jsonify({"ok": False, "error": gettext("That event does not repeat.")}), 404
            if not lab_calendar.event_can_edit(series):
                return jsonify({"ok": False, "error": gettext("Only the person who added this event, or an admin, can change it.")}), 403
            row.event_type, row.animal_id_fk = series.event_type, series.animal_id_fk
            skip = {d for d in (repeat.skip or "").split(",") if d} | {day.isoformat()}
            repeat.skip = ",".join(sorted(skip))
        db_session.add(row)
        db_session.flush()
        if kind != "task" and not isinstance(split, dict):
            lab_calendar.save_repeat(db_session, row, payload.get("repeat"))
        db_session.commit()
        if kind == "task":
            return jsonify({"ok": True, "item": _serialize_task(row)})
        return jsonify({"ok": True, "item": _serialize_calendar_event(row)})


@app.route("/calendar/items/<string:item_key>", methods=["POST"])
@login_required
def calendar_item_update(item_key: str):
    """Update an existing event or task. item_key is `event-<id>` or `task-<id>`;
    an occurrence of a repeating event (`event-<id>@<date>`) edits the series."""
    from datetime import datetime as _dt
    payload = request.get_json(silent=True) or {}
    kind, _, raw_id = item_key.split("@")[0].partition("-")
    if not raw_id.isdigit():
        return jsonify({"ok": False, "error": "bad id"}), 400
    row_id = int(raw_id)

    def _parse(v):
        if not v:
            return None
        try:
            return _dt.fromisoformat(v.replace("Z", "+00:00").replace("+00:00", ""))
        except ValueError:
            return None

    new_start, new_end = _parse(payload.get("start")), _parse(payload.get("end"))
    if new_start and new_end and new_end < new_start:
        return jsonify({"ok": False, "error": gettext("It has to end after it starts.")}), 400

    with SessionLocal() as db_session:
        if kind == "task":
            row = db_session.get(TaskItem, row_id)
            if row is None:
                return jsonify({"ok": False}), 404
            if not access.can_edit_task(row):
                return jsonify({"ok": False, "error": task_denied(row)}), 403
            if "audience" in payload and project_groups.differs(row, payload["audience"]):
                if not access.can_manage_task(row):
                    return jsonify({"ok": False, "error": gettext(
                        "Only %(owner)s or an admin can change whose to-do it is.",
                        owner=row.owner or gettext("its owner"))}), 403
                refused = project_groups.apply(row, payload["audience"])
                if refused:
                    return jsonify({"ok": False, "error": refused}), 403
            if "title" in payload: row.title = (payload["title"] or "").strip() or row.title
            if "body" in payload: row.notes = payload["body"] or ""
            if "backgroundColor" in payload: row.color = payload["backgroundColor"] or ""
            if "priority" in payload: row.priority = payload["priority"]
            start = _parse(payload.get("start"))
            end = _parse(payload.get("end"))
            is_all_day = payload.get("isAllday")
            if start is not None:
                row.due_date = start.date()
                row.start_at = None if is_all_day else start
            if end is not None:
                row.end_at = None if is_all_day else end
            db_session.commit()
            return jsonify({"ok": True, "item": _serialize_task(row)})
        elif kind == "event":
            row = db_session.get(CalendarEvent, row_id)
            if row is None:
                return jsonify({"ok": False}), 404
            if not lab_calendar.event_can_edit(row):
                return jsonify({"ok": False, "error": gettext("Only the person who added this event, or an admin, can change it.")}), 403
            if "audience" in payload and project_groups.differs(row, payload["audience"]):
                refused = project_groups.apply(row, payload["audience"])
                if refused:
                    return jsonify({"ok": False, "error": refused}), 403
            if "title" in payload: row.title = (payload["title"] or "").strip() or row.title
            if "body" in payload: row.description = payload["body"] or ""
            if "backgroundColor" in payload: row.color = payload["backgroundColor"] or ""
            if "event_type" in payload: row.event_type = payload["event_type"]
            start = _parse(payload.get("start"))
            end = _parse(payload.get("end"))
            is_all_day = payload.get("isAllday")
            if start is not None:
                row.event_date = start.date()
                row.start_at = None if is_all_day else start
                if is_all_day is not None: row.is_all_day = bool(is_all_day)
            if end is not None:
                row.end_at = None if is_all_day else end
            if "repeat" in payload:
                lab_calendar.save_repeat(db_session, row, payload["repeat"])
            db_session.commit()
            return jsonify({"ok": True, "item": _serialize_calendar_event(row)})
        return jsonify({"ok": False, "error": "unknown kind"}), 400


@app.route("/calendar/items/<string:item_key>/toggle", methods=["POST"])
@login_required
def calendar_item_toggle(item_key: str):
    """Flip a task's done state. No-op for events."""
    kind, _, raw_id = item_key.partition("-")
    if kind != "task" or not raw_id.isdigit():
        return jsonify({"ok": False, "error": "tasks only"}), 400
    with SessionLocal() as db_session:
        row = db_session.get(TaskItem, int(raw_id))
        if row is None:
            return jsonify({"ok": False}), 404
        if not access.can_edit_task(row):
            return jsonify({"ok": False, "error": task_denied(row)}), 403
        if row.done_at is None:
            row.done_at = datetime.utcnow()
            row.status = "done"
        else:
            row.done_at = None
            row.status = "todo"
        db_session.commit()
        return jsonify({"ok": True, "item": _serialize_task(row)})


@app.route("/calendar/items/<string:item_key>/delete", methods=["POST"])
@login_required
def calendar_item_delete(item_key: str):
    kind, _, raw_id = item_key.split("@")[0].partition("-")
    if not raw_id.isdigit():
        return jsonify({"ok": False, "error": "bad id"}), 400
    row_id = int(raw_id)
    with SessionLocal() as db_session:
        row = db_session.get(TaskItem if kind == "task" else CalendarEvent, row_id)
        if row is None:
            return jsonify({"ok": False}), 404
        if kind == "task" and not access.can_manage_task(row):
            return jsonify({"ok": False, "error": gettext("Only %(owner)s can delete this to-do.",
                                                          owner=row.owner or gettext("its owner"))}), 403
        if kind != "task" and not lab_calendar.event_can_edit(row):
            return jsonify({"ok": False, "error": gettext("Only the person who added this event, or an admin, can delete it.")}), 403
        if kind != "task":
            lab_calendar.delete_repeat(db_session, row_id)
        db_session.delete(row)
        db_session.commit()
        return jsonify({"ok": True})


@app.route("/tasks", methods=["POST"])
@login_required
def tasks():
    """Legacy endpoint kept for any older form posts."""
    with SessionLocal() as db_session:
        db_session.add(
            TaskItem(
                title=request.form["title"],
                due_date=parse_date(request.form.get("due_date")),
                status=request.form.get("status", "todo"),
                priority=request.form.get("priority", "medium"),
                notes=request.form.get("notes", ""),
                owner=g.user.username if g.user else "",
            )
        )
        db_session.commit()
    return redirect(url_for("calendar"))
