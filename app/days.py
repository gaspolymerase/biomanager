"""A day as the lab writes it (Lab setup → Dates), in the page's language.

Everything that says a date to a person goes through fmt_day: pages (the
`day` filter), flashed messages and the rules that word them (app/litters.py).
"""
from __future__ import annotations

from datetime import date, datetime

from flask import g, has_request_context

from . import i18n, lab
from .db import SessionLocal


def _lab_date_style() -> str:
    if has_request_context():
        if "date_style" not in g:
            with SessionLocal() as db_session:
                g.date_style = lab.date_style(db_session)
        return g.date_style
    return "month"


def fmt_day(value, with_time: bool = False) -> str:
    """A date as the lab writes it (Lab setup → Dates): "Sep 26", "26 Sep"
    or "2026-09-26"; the year is added when it is not this year. Takes a
    date, a datetime or an ISO string; with_time adds "· 14:05"."""
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value) if "T" in value or " " in value else date.fromisoformat(value)
        except ValueError:
            return value
    moment = value if isinstance(value, datetime) else None
    day = value.date() if isinstance(value, datetime) else value
    style = _lab_date_style()
    if style == "iso":
        text = day.isoformat()
    elif i18n.current() == "zh":
        # A Chinese reader writes the month first, whatever the lab's style.
        text = f"{day.month}月{day.day}日" if day.year == date.today().year else f"{day.year}年{day.month}月{day.day}日"
    else:
        this_year = day.year == date.today().year
        if style == "day":
            text = f"{day.day} {day:%b}" + ("" if this_year else f" {day.year}")
        else:
            text = f"{day:%b} {day.day}" + ("" if this_year else f", {day.year}")
    if with_time and moment is not None:
        text += f" · {moment:%H:%M}"
    return text
