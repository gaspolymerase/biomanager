"""The numbers the colony hands out: a new mouse's ID, a new cage's code,
a new litter's (L-1, L-2…). Each is one past the highest the lab has ever
used, never one that is taken, so a deleted record's number isn't given to
the next one (a printed cage card or an ear tag still says it).
"""
from __future__ import annotations

import re

from sqlalchemy import func, select

from .models import CageRecord, LitterRecord, MouseRecord


def latest_mouse_record(session) -> MouseRecord | None:
    return session.scalar(select(MouseRecord).order_by(MouseRecord.created_at.desc(), MouseRecord.mouse_id.desc()).limit(1))


def next_mouse_id(session) -> int:
    """The next free mouse ID (see reserve_mouse_ids).

    Flushes first because the session is created with autoflush=False: a
    caller in a loop (CSV import, batch create) has rows pending that the
    max() would otherwise not see, and every row would be handed the same
    ID. Use reserve_mouse_ids() when creating several at once.
    """
    return reserve_mouse_ids(session, 1)[0]


MOUSE_ID_HIGH = "mouse_id_high"      # app_settings: the highest mouse ID ever handed out


def reserve_mouse_ids(session, count: int) -> list[int]:
    """Allocate `count` consecutive mouse IDs in one go, above the highest
    mouse there is and above any ever handed out: a number is never given
    twice, even after its mouse was deleted or an Add many undone. (Before,
    the newest mouse by date decided, so importing an old #5 made every
    New mouse ask for #6 again, and be refused.)"""
    if count <= 0:
        return []
    from .inventory_service import get_setting, set_setting
    session.flush()
    highest = session.scalar(select(func.max(MouseRecord.mouse_id))) or 0
    stored = get_setting(session, MOUSE_ID_HIGH, "")
    if stored.isdigit():
        highest = max(highest, int(stored))
    ids = [highest + offset for offset in range(1, count + 1)]
    set_setting(session, MOUSE_ID_HIGH, str(ids[-1]))
    return ids


_DIGITS = re.compile(r"\d+")


_FIRST_NUMBERS = re.compile(r"^[^\d\n]*(\d+)", re.M)


def _cage_number(code: str | None) -> int:
    """The number a cage ID stands for: "12" -> 12, "2A" -> 2, "B-12" -> 12.
    Only the first run of digits counts, so "B12-3" is 12, not 123."""
    if code and code.isdigit():
        return int(code)   # most are: a lab's thousands, asked for at once
    match = _DIGITS.search(code or "")
    return int(match.group()) if match else 0


def reserve_cage_ids(session, count: int) -> list[str]:
    """`count` unused cage IDs above the highest numbered cage.

    Computed from every cage, not the most recently created one: a cage
    typed by hand with a low number ("2A") must not send the next "new"
    cage back into the existing cage 3."""
    if count <= 0:
        return []
    # See next_mouse_id: pending rows are invisible without a flush.
    session.flush()
    # The column alone, not through the ORM: a lab has tens of thousands,
    # and their numbers read in one pass over them all (_FIRST_NUMBERS: each
    # line's first run of digits, as _cage_number reads one).
    codes = set(session.scalars(select(CageRecord.__table__.c.cage_id)))
    one_line = [code for code in codes if code and "\n" not in code]
    highest = max(map(int, _FIRST_NUMBERS.findall("\n".join(one_line))), default=0)
    highest = max([highest, *(_cage_number(code) for code in codes if code and "\n" in code)])
    ids: list[str] = []
    candidate = highest
    while len(ids) < count:
        candidate += 1
        if str(candidate) not in codes:
            ids.append(str(candidate))
    return ids


def next_cage_id(session) -> str:
    return reserve_cage_ids(session, 1)[0]


_LITTER_NUMBER = re.compile(r"[Ll]-(\d+)")


def next_litter_id(session) -> str:
    """The next litter ID: L-1, L-2, L-3… one past the highest L-number the
    lab has (litters named another way, imported ones, don't count), and
    never one that is taken."""
    session.flush()
    codes = {(code or "").strip() for code, in session.execute(select(LitterRecord.__table__.c.litter_id))}
    numbers = [int(m.group(1)) for m in map(_LITTER_NUMBER.fullmatch, codes) if m]
    n = max(numbers, default=0) + 1
    while f"L-{n}" in codes:
        n += 1
    return f"L-{n}"


def generate_litter_id(session, cage: CageRecord | None) -> str:
    return next_litter_id(session)
