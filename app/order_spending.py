"""What a lab's orders cost: the orders page's Spending view, and the
limits a lab can set on it.

An order costs its Price times its Quantity, or its Price alone where the
lab writes the whole order's price there (the view's *Price is* choice).
An order ordered or received counts as spent, in the month it was
ordered (attrs "ordered_on", stamped by inventory_service.apply_status;
an order from before that date was kept counts in the month it was asked
for); one still requested counts as asked for, in the month it was asked
for (created_at); a cancelled one counts nowhere. Grouped by project group, an order counts in
every group its asker is in now, so the groups can add up to more than the
lab.

A limit is an amount per month or per year (from the month the lab's year
starts), for the lab, a person or a project group, over everything asked
for in that period. The settings are kept per orders database in
app_settings (spending:<key>), as JSON.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from . import inventory_service as svc
from .i18n import gettext
from .models import InventoryItem, InventoryModule, LabGroup, LabGroupMember, UserAccount

BY = ("person", "group", "vendor", "category")
CURRENCIES = ("", "$", "€", "£", "¥", "CHF", "₩", "₹")
STACKED = 5          # the chart's series: the five that cost most, then Other
NEAR = 0.8           # a limit this much used is near


@dataclass
class Order:
    id: int
    number: int
    name: str
    owner: str
    vendor: str
    category: str
    state: str           # "spent" or "asked"
    cost: float | None   # None: no price
    day: date


def _key(module: InventoryModule) -> str:
    return f"spending:{module.key}"


def _amount_or_none(raw) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def settings_of(session, module: InventoryModule) -> dict:
    try:
        raw = json.loads(svc.get_setting(session, _key(module), "") or "{}")
    except ValueError:
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    starts = raw.get("year_starts")
    return {
        "currency": raw.get("currency") if raw.get("currency") in CURRENCIES else "",
        "price_is": "total" if raw.get("price_is") == "total" else "unit",
        "period": "month" if raw.get("period") == "month" else "year",
        "year_starts": starts if isinstance(starts, int) and 1 <= starts <= 12 else 1,
        "lab": _amount_or_none(raw.get("lab")),
        "people": {str(k): v for k, v in (raw.get("people") or {}).items() if _amount_or_none(v) is not None},
        "groups": {str(k): v for k, v in (raw.get("groups") or {}).items() if _amount_or_none(v) is not None},
    }


def save_settings(session, module: InventoryModule, form) -> dict:
    """The view's settings from its dialog: currency, what Price is, the
    limits' period and each limit (limit_lab, limit_person_<username>,
    limit_group_<id>; empty for none)."""
    def limit(name):
        return _amount_or_none(money(form.get(name)))
    starts = form.get("year_starts", "1")
    settings = {
        "currency": form.get("currency", "") if form.get("currency", "") in CURRENCIES else "",
        "price_is": "total" if form.get("price_is") == "total" else "unit",
        "period": "month" if form.get("period") == "month" else "year",
        "year_starts": int(starts) if str(starts).isdigit() and 1 <= int(starts) <= 12 else 1,
        "lab": limit("limit_lab"),
        "people": {name[len("limit_person_"):]: limit(name) for name in form
                   if name.startswith("limit_person_") and limit(name) is not None},
        "groups": {name[len("limit_group_"):]: limit(name) for name in form
                   if name.startswith("limit_group_") and limit(name) is not None},
    }
    svc.set_setting(session, _key(module), json.dumps(settings))
    return settings


def money(raw) -> float | None:
    """A price as a number: "96", "1,250.50", "$96", "¥ 1200", "12,5"
    (a decimal comma); None for none or anything else."""
    text = re.sub(r"[^\d.,]", "", str(raw or ""))
    return svc.amount(text) if text else None


def cost(attrs: dict, quantity, price_is: str = "unit") -> float | None:
    price = money(attrs.get("price"))
    if price is None:
        return None
    if price_is == "total":
        return price
    count = svc.amount(quantity)
    return price * count if count is not None else price


def state_of(status: str, statuses: list[str]) -> str | None:
    """"asked" for an order still at the first step (requested), None for a
    cancelled one, "spent" for the rest."""
    folded = (status or "").strip().casefold()
    if folded in ("cancelled", "canceled"):
        return None
    if statuses and folded == statuses[0].casefold():
        return "asked"
    return "spent"


def orders_of(session, module: InventoryModule, settings: dict) -> list[Order]:
    statuses = svc.view(module).statuses
    rows = session.execute(select(InventoryItem.id, InventoryItem.number, InventoryItem.name, InventoryItem.owner,
                                  InventoryItem.vendor, InventoryItem.category, InventoryItem.status,
                                  InventoryItem.quantity, InventoryItem.attrs, InventoryItem.created_at)
                           .where(InventoryItem.module_id_fk == module.id))
    out = []
    for r in rows:
        state = state_of(r.status, statuses)
        if state is None:
            continue
        try:
            attrs = json.loads(r.attrs or "{}")
        except ValueError:
            attrs = {}
        attrs = attrs if isinstance(attrs, dict) else {}
        day = r.created_at.date() if r.created_at else date.today()
        if state == "spent":
            try:
                day = date.fromisoformat(str(attrs.get(svc.ORDERED_ATTR) or ""))
            except ValueError:
                pass
        out.append(Order(r.id, r.number or 0, r.name or "", (r.owner or "").strip(), (r.vendor or "").strip(),
                         (r.category or "").strip(), state, cost(attrs, r.quantity, settings["price_is"]), day))
    return out


# ------------------------------------------------------------------- periods

def month_of(text, default: date) -> date:
    found = re.fullmatch(r"(\d{4})-(\d{1,2})", str(text or "").strip())
    if found and 1 <= int(found.group(2)) <= 12:
        return date(int(found.group(1)), int(found.group(2)), 1)
    return default


def add_months(day: date, n: int) -> date:
    index = day.year * 12 + day.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def months_between(first: date, last: date) -> list[date]:
    out, at = [], date(first.year, first.month, 1)
    while at <= last and len(out) < 600:
        out.append(at)
        at = add_months(at, 1)
    return out


def limit_period(settings: dict, today: date) -> tuple[date, date]:
    """The first and last month of the limits' period that holds today."""
    this_month = date(today.year, today.month, 1)
    if settings["period"] == "month":
        return this_month, this_month
    start = date(today.year, settings["year_starts"], 1)
    if start > this_month:
        start = date(today.year - 1, settings["year_starts"], 1)
    return start, add_months(start, 11)


# ------------------------------------------------------------------- grouping

@dataclass
class People:
    names: dict[str, str]            # username → display name
    groups: dict[int, str]           # group id → name
    members: dict[int, set[str]]     # group id → usernames

    def groups_of(self, username: str) -> list[int]:
        return [gid for gid, who in self.members.items() if username in who]


def people_of(session) -> People:
    names = {u.username: (u.display_name or u.username) for u in session.scalars(select(UserAccount))}
    groups = {g.id: g.name for g in session.scalars(select(LabGroup).order_by(LabGroup.name))}
    members: dict[int, set[str]] = {gid: set() for gid in groups}
    for m in session.scalars(select(LabGroupMember)):
        members.setdefault(m.group_id_fk, set()).add(m.username)
    return People(names, groups, members)


def keys_of(order: Order, by: str, people: People) -> list[tuple[str, str]]:
    """The (key, label) pairs an order counts under, grouped by `by`."""
    if by == "group":
        groups = people.groups_of(order.owner)
        return ([(f"g{gid}", people.groups[gid]) for gid in groups if gid in people.groups]
                or [("", gettext("Not in a group"))])
    if by == "vendor":
        return [(order.vendor.casefold(), order.vendor or gettext("No vendor"))]
    if by == "category":
        return [(order.category.casefold(), order.category or gettext("No category"))]
    return [(order.owner, people.names.get(order.owner, order.owner) if order.owner else gettext("No one"))]


def summary(orders: list[Order], by: str, first: date, last: date, people: People, settings: dict,
            today: date | None = None) -> dict:
    """The view's numbers for the months first..last, grouped by `by`."""
    today = today or date.today()
    by = by if by in BY else "person"
    months = months_between(first, last)
    index = {(m.year, m.month): i for i, m in enumerate(months)}
    series: dict[str, dict] = {}
    totals = {"spent": 0.0, "asked": 0.0, "orders": 0, "unpriced": 0}
    month_totals = [0.0] * len(months)
    for o in orders:
        at = index.get((o.day.year, o.day.month))
        if at is None:
            continue
        totals["orders"] += 1
        if o.cost is None:
            totals["unpriced"] += 1
        value = o.cost or 0.0
        totals[o.state] += value
        month_totals[at] += value
        for key, label in keys_of(o, by, people):
            s = series.setdefault(key, {"key": key, "label": label, "spent": 0.0, "asked": 0.0, "orders": 0,
                                        "months": [0.0] * len(months)})
            s[o.state] += value
            s["orders"] += 1
            s["months"][at] += value
    ranked = sorted(series.values(), key=lambda s: (-(s["spent"] + s["asked"]), s["label"].casefold()))
    for s in ranked:
        s["total"] = s["spent"] + s["asked"]
    # The chart's stack: the five that cost most, the rest as Other.
    stack = [{"key": s["key"], "label": s["label"], "months": s["months"]} for s in ranked[:STACKED]]
    rest = ranked[STACKED:]
    if rest:
        stack.append({"key": "__other", "label": gettext("Other"),
                      "months": [sum(s["months"][i] for s in rest) for i in range(len(months))],
                      "count": len(rest)})
    return {
        "by": by, "first": first.strftime("%Y-%m"), "last": last.strftime("%Y-%m"),
        "months": [m.strftime("%Y-%m") for m in months],
        "month_totals": month_totals,
        "totals": totals, "series": ranked, "stack": stack,
        "limits": limits(orders, people, settings, today),
        "currency": settings["currency"], "price_is": settings["price_is"],
    }


# ------------------------------------------------------------------- limits

def _state(used: float, limit: float) -> str:
    if used > limit:
        return "over"
    return "near" if limit and used >= NEAR * limit else "ok"


def limits(orders: list[Order], people: People, settings: dict, today: date) -> dict:
    """Where each limit stands in the period that holds today."""
    first, last = limit_period(settings, today)
    now = [o for o in orders if first <= date(o.day.year, o.day.month, 1) <= last]
    rows = []

    def row(kind, key, label, used, limit):
        rows.append({"kind": kind, "key": key, "label": label, "used": used, "limit": limit,
                     "share": used / limit if limit else None, "state": _state(used, limit)})
    if settings["lab"] is not None:
        row("lab", "", gettext("The lab"), sum(o.cost or 0 for o in now), float(settings["lab"]))
    for username, limit in settings["people"].items():
        row("person", username, people.names.get(username, username),
            sum(o.cost or 0 for o in now if o.owner == username), float(limit))
    for gid, limit in settings["groups"].items():
        if gid.isdigit() and int(gid) in people.groups:
            who = people.members.get(int(gid), set())
            row("group", gid, people.groups[int(gid)], sum(o.cost or 0 for o in now if o.owner in who), float(limit))
    return {"period": settings["period"], "first": first.strftime("%Y-%m"), "last": last.strftime("%Y-%m"),
            "rows": rows}


def over_limits(session, module: InventoryModule, owner: str, today: date | None = None) -> list[dict]:
    """The limits an order of `owner` now takes past: the lab's, theirs and
    their project groups'. Asked after an order is saved."""
    settings = settings_of(session, module)
    if settings["lab"] is None and not settings["people"] and not settings["groups"]:
        return []
    people = people_of(session)
    mine = {str(g) for g in people.groups_of(owner)}
    found = limits(orders_of(session, module, settings), people, settings, today or date.today())["rows"]
    return [r for r in found if r["state"] == "over" and (
        r["kind"] == "lab" or (r["kind"] == "person" and r["key"] == owner) or (r["kind"] == "group" and r["key"] in mine))]


def tell_admins(session, module: InventoryModule, crossed: list[dict], actor: str, link: str) -> int:
    """Every admin (but whoever saved) is told of each limit an order has
    just taken past, in their own language, under Orders notifications."""
    from . import notify
    settings = settings_of(session, module)
    admins = session.scalars(select(UserAccount.username).where(UserAccount.role == "admin",
                                                                UserAccount.disabled.is_(False))).all()
    message = ("%(used)s asked for this month, of %(limit)s." if settings["period"] == "month"
               else "%(used)s asked for this year, of %(limit)s.")
    sent = 0
    for r in crossed:
        values = {"used": amount_text(r["used"], settings["currency"]),
                  "limit": amount_text(r["limit"], settings["currency"])}
        for admin in admins:
            sent += notify.send(session, admin, "%(who)s is over the spending limit", message, category="orders",
                                link=link, actor=actor, values={"who": r["label"]}, message_values=values)
    return sent


def amount_text(value: float, currency: str = "") -> str:
    text = f"{value:,.0f}" if abs(value) >= 100 or value == int(value) else f"{value:,.2f}"
    return f"{currency}{text}" if currency else text


def over_message(rows: list[dict], settings: dict) -> str:
    """One sentence per limit passed, for the message after a save."""
    period = gettext("this month") if settings["period"] == "month" else gettext("this year")
    return " ".join(gettext("Over the limit: %(who)s has %(used)s asked for %(period)s, of %(limit)s.",
                            who=r["label"], used=amount_text(r["used"], settings["currency"]), period=period,
                            limit=amount_text(r["limit"], settings["currency"])) for r in rows)


def can_set_limits() -> bool:
    from . import access
    return access.is_admin()

