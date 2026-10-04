"""Home page layouts: Classic (the cards), Tracks and Freezer.

Classic is the original dashboard. The other two draw the same work in
another shape:

- Tracks lays everything with a date on one shared day ruler, a track per
  kind of work, like annotations in a genome browser.
- Freezer starts from where things sit: the racks holding something that
  needs doing, and a pull list in the order you would walk the room.

Both read one agenda (`build_agenda`): every dated piece of work across the
lab's databases, with where it lives. Each person's choice is kept in
app_settings as "home_layout:<username>", so it follows them between
browsers and needs no schema change.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from flask import url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import i18n, inventory_service, organism_service, positions, stock_service
from .i18n import gettext, translate_value
from .models import CageRecord, CalendarEvent, InventoryItem, LitterRecord, MouseRack, MouseRecord, StockRack, StockUnit
from .services import WEAN_OFFSET_DAYS, weaning_due, weaning_title

LAYOUTS = {
    "classic": ("Classic", "columns"),
    "tracks": ("Tracks", "dna"),
    "freezer": ("Freezer", "snowflake"),
}
DEFAULT_LAYOUT = "classic"
SPANS = (7, 14, 28)
DEFAULT_SPAN = 14
# Freezer's pull list: overdue work and the next few days.
PULL_DAYS = 3
SAC_AGE_DAYS = 30 * 7

SEVERITY = {"overdue": 0, "today": 1, "soon": 2, "later": 3}


# ---------------------------------------------------------------- the choice

def _key(username: str) -> str:
    return f"home_layout:{username}"


def get_layout(session, username: str) -> str:
    value = inventory_service.get_setting(session, _key(username), DEFAULT_LAYOUT)
    return value if value in LAYOUTS else DEFAULT_LAYOUT


def set_layout(session, username: str, value: str) -> str:
    value = value if value in LAYOUTS else DEFAULT_LAYOUT
    inventory_service.set_setting(session, _key(username), value)
    return value


# ---------------------------------------------------------------- the cards

# Classic's cards, in their first order: key, name, icon, whether it starts
# full width, and what the lab must keep for it to be offered (a feature,
# or one of the has_* flags home_dashboard works out).
CARDS = [
    ("for_you", "For you (unread notices)", "bell", True, None),
    ("databases", "Your databases", "database", True, None),
    ("stats", "Counts", "chart", True, None),
    ("todos", "Your to-dos", "list-check", False, "calendar"),
    ("sac", "Mice older than 30 weeks", "warning", False, "colony"),
    ("weanings", "Upcoming weanings", "baby", False, "colony"),
    ("geno", "Genotyping queue", "microscope", False, "colony"),
    ("stocks", "Flies & worms: due", "fly", False, "has_stocks"),
    ("restock", "Expiring & low stock", "flask", False, "has_restock"),
    ("zebrafish", "Zebrafish", "fish", False, "zebrafish"),
    ("organisms", "Animal databases: due", "paw", False, None),
    ("calendar", "Next 14 days", "calendar", False, "calendar"),
    ("bookings", "Your bookings", "calendar-clock", False, "calendar"),
    ("notebook", "Recent notebook pages", "notebook", False, "notebook"),
    ("utilities", "Calculators", "calculator", False, None),
    ("orders", "Recent orders", "cart", True, "has_orders"),
]
CARD_KEYS = [c[0] for c in CARDS]
# Cards a new person doesn't see until they add them in Customize.
OFF_AT_FIRST = {"todos", "bookings", "notebook", "utilities"}


def _cards_key(username: str) -> str:
    return f"home_cards:{username}"


def get_cards(session, username: str) -> dict:
    """{"order": [...every key...], "hidden": [...], "wide": [...]}, as this
    person arranged Home (kept in app_settings, like the layout); a card
    added to the app since goes in its usual place, off if OFF_AT_FIRST."""
    import json
    try:
        saved = json.loads(inventory_service.get_setting(session, _cards_key(username), "") or "{}")
    except ValueError:
        saved = {}
    order = [k for k in saved.get("order", []) if k in CARD_KEYS]
    known = set(order)
    for i, key in enumerate(CARD_KEYS):          # new cards: after the one before them
        if key not in known:
            before = next((CARD_KEYS[j] for j in range(i - 1, -1, -1) if CARD_KEYS[j] in order), None)
            order.insert(order.index(before) + 1 if before else 0, key)
            known.add(key)
    seen = set(saved.get("seen", [])) if saved else set()
    hidden = [k for k in saved.get("hidden", []) if k in CARD_KEYS] if saved else []
    hidden += [k for k in OFF_AT_FIRST if k not in seen and k not in hidden]
    wide = [k for k in saved.get("wide", []) if k in CARD_KEYS] if "wide" in saved else [c[0] for c in CARDS if c[3]]
    return {"order": order, "hidden": hidden, "wide": wide}


def set_cards(session, username: str, order, hidden, wide) -> dict:
    import json
    clean = lambda keys: [k for k in dict.fromkeys(keys) if k in CARD_KEYS]  # noqa: E731
    value = {"order": clean(order), "hidden": clean(hidden), "wide": clean(wide), "seen": CARD_KEYS}
    inventory_service.set_setting(session, _cards_key(username), json.dumps(value))
    return get_cards(session, username)


def reset_cards(session, username: str) -> None:
    inventory_service.set_setting(session, _cards_key(username), "")


def offered_cards(features: dict, flags: dict) -> list[dict]:
    """The cards this lab can show, for Customize."""
    out = []
    for key, label, icon, _wide, needs in CARDS:
        if needs and not (flags.get(needs) if needs.startswith("has_") else features.get(needs, True)):
            continue
        out.append({"key": key, "label": label, "icon": icon})
    return out


def span_arg(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SPAN
    return value if value in SPANS else DEFAULT_SPAN


# ---------------------------------------------------------------- the agenda

def _status(due: date, today: date) -> str:
    if due < today:
        return "overdue"
    if due == today:
        return "today"
    return "soon" if due <= today + timedelta(days=PULL_DAYS) else "later"


def _item(track, due, title, today, url, *, detail="", loc=None, owner="", status=None):
    return {"track": track, "due": due, "title": title, "detail": detail, "url": url,
            "loc": loc, "owner": owner or "", "status": status or _status(due, today)}


def _cage_loc(cage: CageRecord | None) -> dict | None:
    if cage is None:
        return None
    rack = cage.rack
    if rack is not None and cage.rack_row and cage.rack_col:
        pos = positions.label(cage.rack_row, cage.rack_col, rack.naming, rack.cols)
        return {"kind": "mouse", "rack_id": rack.id, "row": cage.rack_row, "col": cage.rack_col,
                "text": gettext("cage %(cage)s · %(rack)s · %(pos)s", cage=cage.cage_id, rack=rack.name, pos=pos),
                "order": (0, rack.position or 0, rack.name, cage.rack_row, cage.rack_col)}
    text = (gettext("cage %(cage)s · %(where)s", cage=cage.cage_id, where=cage.cage_location) if cage.cage_location
            else gettext("cage %(cage)s", cage=cage.cage_id))
    return {"kind": "mouse", "rack_id": None, "row": None, "col": None,
            "text": text, "order": (3, 0, cage.cage_id, 0, 0)}


def _stock_loc(unit: StockUnit | None, rack: StockRack | None) -> dict | None:
    rack = rack or (unit.rack if unit is not None else None)
    if rack is None:
        return None
    where = rack.name
    if rack.incubator is not None:
        temp = rack.incubator.temperature
        temp = f" {temp} °C" if temp and temp not in rack.incubator.name else ""
        where = f"{rack.incubator.name}{temp} · {rack.name}"
    row = unit.rack_row if unit is not None else None
    col = unit.rack_col if unit is not None else None
    if row and col:
        where += f" · {positions.label(row, col, rack.naming, rack.cols)}"
    return {"kind": "stock", "rack_id": rack.id, "row": row, "col": col, "text": where,
            "order": (1, rack.incubator_id_fk or 0, rack.name, row or 0, col or 0)}


def _mouse_ids(mice) -> str:
    ids = [f"#{m.mouse_id}" for m in sorted(mice, key=lambda m: m.mouse_id)]
    return ", ".join(ids[:4]) + (f" +{len(ids) - 4}" if len(ids) > 4 else "")


def _by_cage(mice):
    groups = defaultdict(list)
    for m in mice:
        groups[m.cage_id_fk].append(m)
    return groups.values()


def _colony_items(session, today, until) -> list[dict]:
    out = []
    mice_url = url_for("colony", view="mice")
    alive = (select(MouseRecord)
             .options(selectinload(MouseRecord.litter),
                      selectinload(MouseRecord.cage).selectinload(CageRecord.rack))
             .join(LitterRecord, MouseRecord.litter_id_fk == LitterRecord.id)
             .where(MouseRecord.date_of_death.is_(None), LitterRecord.date_of_birth.is_not(None)))

    # 30 weeks and older (one entry per cage), and anyone turning 30 weeks
    # inside the window (per cage and day).
    crossing = session.scalars(alive.where(
        LitterRecord.date_of_birth <= until - timedelta(days=SAC_AGE_DAYS))).all()
    by_day_cage = defaultdict(list)
    for m in crossing:
        crosses = m.litter.date_of_birth + timedelta(days=SAC_AGE_DAYS)
        by_day_cage[(None if crosses < today else crosses, m.cage_id_fk)].append(m)
    for (when, _cage), group in by_day_cage.items():
        due = when or min(m.litter.date_of_birth for m in group) + timedelta(days=SAC_AGE_DAYS)
        weeks = max((today - m.litter.date_of_birth).days // 7 for m in group)
        past = due < today
        title = (gettext("Past 30 w: %(mice)s", mice=_mouse_ids(group)) if past
                 else gettext("Turns 30 w: %(mice)s", mice=_mouse_ids(group)))
        out.append(_item("colony:age", due, title, today,
                         mice_url, detail=gettext("oldest %(n)s w", n=weeks) if past else "",
                         loc=_cage_loc(group[0].cage), owner=group[0].owner))

    # Weanings at P21, from a week overdue to the end of the window: the
    # same list as Home's and the calendar's (services.weaning_due).
    for wean in weaning_due(session, today - timedelta(days=7), until):
        cage, litter = wean["cage"], wean["litter"]
        bits = [b for b in (litter.cohort_name if litter else "",
                            gettext("%(n)s pups", n=wean["pups"]) if wean["pups"] else "") if b]
        url = (url_for("colony", view="cages", scope="all") + f"#cage-{cage.id}") if cage is not None \
            else url_for("colony", view="litters")
        out.append(_item("colony:wean", wean["due"], gettext("Wean %(what)s", what=weaning_title(wean)), today, url,
                         detail=" · ".join(bits), loc=_cage_loc(cage),
                         owner=cage.owner if cage is not None else ""))

    # Waiting on genotyping: due now.
    geno = session.scalars(
        select(MouseRecord).options(selectinload(MouseRecord.cage).selectinload(CageRecord.rack))
        .where(MouseRecord.date_of_death.is_(None), MouseRecord.status == "geno")).all()
    for group in _by_cage(geno):
        out.append(_item("colony:geno", today, gettext("Genotype %(mice)s", mice=_mouse_ids(group)), today, mice_url,
                         loc=_cage_loc(group[0].cage), owner=group[0].owner))
    return out


def _calendar_items(session, today, until) -> list[dict]:
    from .lab_calendar import event_visible_clause
    events = session.scalars(select(CalendarEvent).where(
        event_visible_clause(), CalendarEvent.event_date >= today, CalendarEvent.event_date <= until)
        .order_by(CalendarEvent.event_date)).all()
    return [_item("calendar", e.event_date, e.title, today, url_for("calendar"), detail=e.event_type or "")
            for e in events]


def _zebrafish_items(summary: dict, today) -> list[dict]:
    out = []
    for t in summary.get("mating", []):
        out.append(_item("zebrafish", t["due"], gettext("Return mating tank %(tank)s", tank=t["tank_id"]), today,
                         url_for("zebrafish", view="tanks") + f"#tank-{t['id']}", owner=t.get("owner", "")))
    for t in summary.get("geno", []):
        out.append(_item("zebrafish", today, gettext("Genotype tank %(tank)s", tank=t["tank_id"]), today,
                         url_for("zebrafish", view="tanks", mode="geno"), detail=t.get("line", ""),
                         owner=t.get("owner", "")))
    return out


def _stock_items(session, today, horizon) -> tuple[list[dict], dict]:
    out, tracks = [], {}
    for module in stock_service.list_modules(session):
        mv = stock_service.view(module)
        track = f"stock:{mv.key}"
        tracks[track] = (translate_value(mv.label), gettext("Flips & collections"),
                         url_for("stocks.module", key=mv.key, view="schedule"))
        for it in stock_service.schedule(session, mv, today, horizon=horizon):
            unit = it.get("unit")
            out.append(_item(track, it["due"], it["title"], today, tracks[track][2], detail=it.get("detail", ""),
                             loc=_stock_loc(unit, it.get("rack")), owner=unit.owner if unit is not None else ""))
    return out, tracks


def _organism_items(session, horizon, today) -> tuple[list[dict], dict]:
    out, tracks = [], {}
    for it in organism_service.home_due(session, horizon_days=horizon):
        track = f"org:{it['key']}"
        tracks[track] = (translate_value(it["module"]), gettext("Schedule"),
                         url_for("organisms.module", key=it["key"], view="schedule"))
        out.append(_item(track, it["due"], it["title"], today, tracks[track][2]))
    return out, tracks


def _supply_items(session, today, horizon) -> list[dict]:
    attention = inventory_service.attention_items(session, days=horizon, limit=40)
    rows = {i.id: i for i in session.scalars(select(InventoryItem).options(selectinload(InventoryItem.rack))
                                             .where(InventoryItem.id.in_([a["id"] for a in attention])))}
    out = []
    for a in attention:
        item = rows.get(a["id"])
        loc = None
        if item is not None and (item.rack is not None or item.location_note):
            where = item.rack.name if item.rack is not None else ""
            if item.rack is not None and item.rack_row and item.rack_col:
                where += f" · {positions.label(item.rack_row, item.rack_col, item.rack.naming, item.rack.cols)}"
            where = " · ".join(b for b in (where, item.location_note) if b)
            loc = {"kind": "inventory", "rack_id": None, "row": None, "col": None, "text": where,
                   "order": (2, 0, where, 0, 0)}
        url = url_for("inventory.module", key=a["key"], q=a["name"])
        owner = item.owner if item is not None else ""
        if a["expiry"] in ("expired", "soon") and a["expires_on"]:
            title = (gettext("Expired: %(name)s", name=a["name"]) if a["expiry"] == "expired"
                     else gettext("Expires: %(name)s", name=a["name"]))
            module = translate_value(a["module"])
            out.append(_item("supplies", a["expires_on"], title, today, url,
                             detail=gettext("%(module)s · low", module=module) if a["low"] else module,
                             loc=loc, owner=owner))
        else:
            # Low stock has no date of its own: it is on today's list, but it is
            # not as pressing as something due today.
            out.append(_item("supplies", today, gettext("Low: %(name)s", name=a["name"]), today, url,
                             detail=translate_value(a["module"]),
                             loc=loc, owner=owner, status="soon"))
    return out


def build_agenda(session, today: date, horizon: int, features: dict, zebrafish: dict | None,
                 colony_label: str = "Mouse colony") -> tuple[list[dict], dict]:
    """Every dated piece of work up to `horizon` days ahead (and anything
    overdue), with the tracks it belongs to: ({track: (group, name, url)}),
    in the page's language."""
    until = today + timedelta(days=horizon)
    colony_label = translate_value(colony_label)
    tracks: dict[str, tuple[str, str, str]] = {}
    items: list[dict] = []
    if features.get("calendar", True):
        tracks["calendar"] = (gettext("Calendar"), gettext("Events"), url_for("calendar"))
        items += _calendar_items(session, today, until)
    if features.get("colony", True):
        tracks["colony:age"] = (colony_label, gettext("30 weeks and older"), url_for("colony", view="breeders"))
        tracks["colony:wean"] = (colony_label, gettext("Weaning · P%(days)s", days=WEAN_OFFSET_DAYS),
                                 url_for("colony", view="litters"))
        tracks["colony:geno"] = (colony_label, gettext("Genotyping"), url_for("colony", view="mice"))
        items += _colony_items(session, today, until)
    if features.get("zebrafish", True) and zebrafish and zebrafish.get("any"):
        tracks["zebrafish"] = (gettext("Zebrafish"), gettext("Tanks"), url_for("zebrafish", view="tanks"))
        items += _zebrafish_items(zebrafish, today)
    stock_items, stock_tracks = _stock_items(session, today, horizon)
    tracks.update(stock_tracks)
    items += stock_items
    org_items, org_tracks = _organism_items(session, horizon, today)
    tracks.update(org_tracks)
    items += org_items
    if any(m.kind in inventory_service.RESTOCK_KINDS for m in inventory_service.list_modules(session)):
        tracks["supplies"] = (gettext("Supplies"), gettext("Expiry & low stock"), "")
        items += _supply_items(session, today, horizon)
    order = {key: n for n, key in enumerate(tracks)}
    items.sort(key=lambda i: (i["due"], SEVERITY[i["status"]], order.get(i["track"], 99), i["title"]))
    return items, tracks


def _when(due: date, today: date) -> str:
    days = (due - today).days
    if days < 0:
        return gettext("overdue")
    if days == 0:
        return gettext("today")
    if days == 1:
        return gettext("tomorrow")
    return i18n.strftime(due, "%a %d") if days < 7 else i18n.strftime(due, "%b %d")


# ---------------------------------------------------------------- Tracks

# Label widths are estimated to stack features that would overlap: about
# 6.6px per character of the 11px mono label, on a track area near 640px.
_CHAR_PX = 6.6
_TRACK_PX = 640
_LABEL_MAX = 26


def tracks_view(agenda: list[dict], track_meta: dict, today: date, span: int) -> dict:
    day_px = _TRACK_PX / span
    days = []
    for i in range(span):
        d = today + timedelta(days=i)
        # One letter of the weekday ("M"), or its last character in Chinese ("一" of 周一).
        weekday = i18n.strftime(d, "%a")
        days.append({"date": d, "weekday": weekday[0] if i18n.current() == i18n.DEFAULT else weekday[-1],
                     "num": d.day, "month": i18n.strftime(d, "%b") if (i == 0 or d.day == 1) else "",
                     "weekend": d.weekday() >= 5, "today": i == 0, "left": i * 100 / span})

    per_track: dict[str, dict[int, list[dict]]] = {key: defaultdict(list) for key in track_meta}
    for item in agenda:
        if item["track"] not in per_track:
            continue
        slot = max(0, (item["due"] - today).days)
        if slot < span:
            per_track[item["track"]][slot].append(item)

    tracks = []
    for key, (group, name, url) in track_meta.items():
        features, row_ends = [], []
        count = 0
        for slot in sorted(per_track[key]):
            group_items = per_track[key][slot]
            count += len(group_items)
            first = min(group_items, key=lambda i: SEVERITY[i["status"]])
            label = first["title"]
            if len(label) > _LABEL_MAX:
                label = label[:_LABEL_MAX - 1] + "…"
            if len(group_items) > 1:
                label += f" +{len(group_items) - 1}"
            span_days = max(1.0, (len(label) * _CHAR_PX + 14) / day_px)
            # Near the right edge the label reads leftwards instead of running off.
            flip = slot + span_days > span
            start, end = (slot + 1 - span_days, slot + 1) if flip else (slot, slot + span_days)
            row = next((r for r, taken in enumerate(row_ends) if taken <= start), len(row_ends))
            if row == len(row_ends):
                row_ends.append(0)
            row_ends[row] = end
            features.append({
                "left": slot * 100 / span, "width": 100 / span, "row": row, "label": label, "flip": flip,
                "status": first["status"], "over": first["status"] == "overdue",
                "url": first["url"] if len(group_items) == 1 else (url or first["url"]),
                "tooltip": "\n".join(f"{i['title']}" + (f" · {i['detail']}" if i["detail"] else "")
                                     + (f" · {i['loc']['text']}" if i["loc"] else "") for i in group_items),
            })
        tracks.append({"key": key, "group": group, "name": name, "url": url, "count": count,
                       "features": features, "rows": max(1, len(row_ends))})

    now = [i for i in agenda if i["status"] in ("overdue", "today")]
    busy = []
    for i, d in enumerate(days):
        on_day = [a for a in agenda if a["due"] == d["date"]]
        if len(on_day) >= 3:
            busy.append({"date": d["date"], "count": len(on_day), "titles": [a["title"] for a in on_day[:3]]})

    counts = {s: sum(1 for i in agenda if i["status"] == s and (i["due"] - today).days < span) for s in SEVERITY}
    return {"span": span, "spans": SPANS, "days": days, "tracks": tracks, "now": now[:8], "now_total": len(now),
            "counts": counts, "total": sum(counts.values()),
            "busy": busy[:3], "ideogram": _ideogram(today, span), "until": today + timedelta(days=span - 1),
            "list": _day_list(agenda, today, span)}


def _ideogram(today: date, span: int) -> dict:
    """The months around the window drawn like a chromosome: one arm per
    month, weekends as the dark bands, a box over the days on screen."""
    start = today.replace(day=1)
    last = today + timedelta(days=span - 1)
    end = (last.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    total = (end - start).days + 1
    width, left, gap = 1000, 110, 6
    months = 0
    cursor = start
    while cursor <= end:
        months += 1
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    unit = (width - left - 10 - gap * (months - 1)) / total
    arms, bands = [], []
    cursor = start
    x = left
    while cursor <= end:
        nxt = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        n = (nxt - cursor).days
        arms.append({"x": round(x, 2), "w": round(n * unit, 2), "label": i18n.strftime(cursor, "%b"), "start": cursor})
        for k in range(n):
            if (cursor + timedelta(days=k)).weekday() >= 5:
                bands.append({"x": round(x + k * unit, 2), "w": round(unit + 0.3, 2)})
        x += n * unit + gap
        cursor = nxt

    def x_of(d: date) -> float:
        arm = [a for a in arms if a["start"] <= d][-1]
        return arm["x"] + (d - arm["start"]).days * unit

    return {"arms": arms, "bands": bands, "box_x": round(x_of(today), 2),
            "box_w": round(x_of(last) + unit - x_of(today), 2),
            "title": i18n.strftime(start, "%b") + "–" + i18n.strftime(end, "%b %Y")}


def _day_list(agenda: list[dict], today: date, span: int) -> list[dict]:
    """The same agenda as a list of days, for phones."""
    groups: dict[str, dict] = {}
    for item in agenda:
        if (item["due"] - today).days >= span:
            continue
        key = "overdue" if item["status"] == "overdue" else item["due"].isoformat()
        label = gettext("Overdue") if key == "overdue" else _when(item["due"], today).capitalize()
        groups.setdefault(key, {"label": label, "items": []})["items"].append(item)
    return list(groups.values())


# ---------------------------------------------------------------- Freezer

def _hue(name: str) -> int | None:
    if not name:
        return None
    from .app import _name_color
    return _name_color(name)[0]


def freezer_view(session, agenda: list[dict], today: date, username: str = "") -> dict:
    last = today + timedelta(days=PULL_DAYS)
    pull = [i for i in agenda if i["track"] != "calendar" and i["due"] <= last]
    pull.sort(key=lambda i: ((i["loc"] or {}).get("order", (9,)), SEVERITY[i["status"]], i["due"]))

    flags: dict[tuple, tuple[int, str]] = {}
    mine: dict[tuple, int] = defaultdict(int)
    for n, item in enumerate(pull, start=1):
        item["num"] = n
        item["when"] = _when(item["due"], today)
        item["hue"] = _hue(item["owner"])
        loc = item["loc"]
        if loc and loc.get("rack_id"):
            key = (loc["kind"], loc["rack_id"], loc.get("row"), loc.get("col"))
            if username and item["owner"] == username:
                mine[(loc["kind"], loc["rack_id"])] += 1
            if key not in flags or SEVERITY[item["status"]] < SEVERITY[flags[key][1]]:
                flags[key] = (flags.get(key, (n,))[0], item["status"])

    def rack_flags(kind, rack_id):
        return sum(1 for k in flags if k[0] == kind and k[1] == rack_id)

    mouse_racks = session.scalars(select(MouseRack).order_by(MouseRack.position, MouseRack.name)).all()
    stock_racks = session.scalars(select(StockRack).options(selectinload(StockRack.incubator))
                                  .order_by(StockRack.name)).all()
    candidates = [("mouse", r, rack_flags("mouse", r.id)) for r in mouse_racks]
    candidates += [("stock", r, rack_flags("stock", r.id)) for r in stock_racks]
    # The racks with the most to do, your own work first.
    chosen = [c for c in candidates if c[2]]
    chosen.sort(key=lambda c: (-mine[(c[0], c[1].id)], -c[2]))
    if not chosen and mouse_racks:
        chosen = [("mouse", mouse_racks[0], 0)]
    chosen = chosen[:3]

    stock_keys = {m.id: m.key for m in stock_service.list_modules(session)}
    boxes = []
    for kind, rack, _n in chosen:
        cells: dict[tuple[int, int], dict] = {}
        if kind == "mouse":
            for cage in session.scalars(select(CageRecord).where(
                    CageRecord.rack_id_fk == rack.id, CageRecord.rack_row.is_not(None))):
                cells[(cage.rack_row, cage.rack_col)] = {"label": cage.cage_id, "hue": _hue(cage.owner),
                                                         "title": gettext("Cage %(cage)s", cage=cage.cage_id)
                                                         + (f" · {cage.owner}" if cage.owner else "")}
            sub = rack.room or gettext("mouse rack")
            url = url_for("colony", view="cages")
        else:
            for unit in session.scalars(select(StockUnit).where(
                    StockUnit.rack_id_fk == rack.id, StockUnit.active.is_(True), StockUnit.rack_row.is_not(None))):
                cells[(unit.rack_row, unit.rack_col)] = {"label": str(unit.number), "hue": _hue(unit.owner),
                                                         "title": gettext("No. %(n)s · %(genotype)s", n=unit.number,
                                                                          genotype=unit.genotype)}
            sub = rack.incubator.name if rack.incubator is not None else gettext("rack")
            key = stock_keys.get(rack.module_id_fk)
            url = url_for("stocks.module", key=key) if key else ""
        grid = []
        for r in range(1, rack.rows + 1):
            for c in range(1, rack.cols + 1):
                cell = dict(cells.get((r, c), {}))
                cell["pos"] = positions.label(r, c, rack.naming, rack.cols)
                flag = flags.get((kind, rack.id, r, c))
                if flag:
                    cell["num"], cell["sev"] = flag[0], "red" if flag[1] in ("overdue", "today") else "amber"
                grid.append(cell)
        rack_flag = flags.get((kind, rack.id, None, None))
        temp = ""
        if kind == "stock" and rack.incubator is not None and rack.incubator.temperature:
            temp = f"{rack.incubator.temperature} °C"
        boxes.append({"name": rack.name, "sub": sub, "rows": rack.rows, "cols": rack.cols, "cells": grid,
                      "filled": len(cells), "url": url, "temp": temp,
                      "rack_flag": rack_flag[0] if rack_flag else None})

    owners = sorted({i["owner"] for i in pull if i["owner"]})
    return {"pull": pull, "boxes": boxes, "has_racks": bool(mouse_racks or stock_racks),
            "places": len({(i["loc"] or {}).get("order", (9,))[:3] for i in pull if i["loc"]}),
            "owners": [{"name": o, "hue": _hue(o)} for o in owners[:6]], "pull_days": PULL_DAYS}
