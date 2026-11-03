from __future__ import annotations

from collections import defaultdict, namedtuple
from datetime import date, datetime, timedelta
from urllib.parse import quote


from sqlalchemy import func, select

from .db import SessionLocal
from .i18n import gettext
from .litter_dates import born_day, genotyping_offset, open_litters, pups_all_dead, weaning_due, weaning_title
from .positions import natural_sorted, place_order
from . import custom_fields
from .record_numbers import next_cage_id
from .models import (
    AnimalRecord,
    CageLitter,
    CageRecord,
    CalendarEvent,
    ClutchRecord,
    Experiment,
    FishRecord,
    TankRecord,
    DropdownOption,
    LitterRecord,
    MOUSE_PRESET_FIELDS,
    MouseRecord,
    NotebookEntry,
    Order,
    SampleRecord,
    StrainRecord,
    TaskItem,
    UserAccount,
)


def parse_date(raw_value: str | None) -> date | None:
    if not raw_value:
        return None
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    for fmt in ("%Y-%m-%d", "%y%m%d", "%y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def dashboard_counts() -> dict[str, int]:
    with SessionLocal() as session:
        return {
            "orders": len(session.scalars(select(Order)).all()),
            "animals": len(session.scalars(select(AnimalRecord)).all()),
            "samples": len(session.scalars(select(SampleRecord)).all()),
            "events": len(session.scalars(select(CalendarEvent)).all()),
            "tasks": len(session.scalars(select(TaskItem)).all()),
            "notebook_entries": len(session.scalars(select(NotebookEntry)).all()),
            "mice": len(session.scalars(select(MouseRecord)).all()),
            "cages": len(session.scalars(select(CageRecord)).all()),
            "strains": len(session.scalars(select(StrainRecord)).all()),
        }


def get_or_create_litter(session, litter_code: str, dob: date | None = None) -> LitterRecord:
    litter = session.scalar(select(LitterRecord).where(LitterRecord.litter_id == litter_code))
    if litter is None:
        litter = LitterRecord(litter_id=litter_code, date_of_birth=dob)
        session.add(litter)
        session.flush()
    elif dob and dob != litter.date_of_birth:
        # A litter's date of birth is the age of every mouse in it: only
        # someone who may edit all of them re-dates it (as on the Litters
        # tab). Anyone else's mouse just joins with the litter's date.
        from flask import flash, has_request_context
        from . import access
        if not has_request_context() or access.can_edit_litter(litter):
            litter.date_of_birth = dob
        else:
            flash(gettext("Litter %(litter)s keeps its date of birth (%(dob)s): it has mice you can't edit.",
                          litter=litter.litter_id, dob=litter.date_of_birth or gettext("not set")), "info")
    return litter


def mouse_racks(session) -> list:
    from .models import MouseRack
    return sorted(session.scalars(select(MouseRack)), key=lambda r: (r.position or 0, place_order(r.name)))


def get_or_create_cage(session, cage_code: str) -> CageRecord:
    resolved_code = cage_code.strip()
    if resolved_code.lower() == "new" or resolved_code == "":
        resolved_code = next_cage_id(session)
    cage = session.scalar(select(CageRecord).where(CageRecord.cage_id == resolved_code))
    if cage is None:
        # A cage made by typing its number belongs to whoever made it, like
        # one from New cage; without an owner anyone could change it.
        from flask import g, has_request_context
        owner = g.user.username if has_request_context() and g.get("user") is not None else ""
        cage = CageRecord(cage_id=resolved_code, owner=owner)
        session.add(cage)
        session.flush()
    return cage


def calculate_age_fields(dob: date | None) -> dict[str, int | None]:
    if dob is None:
        return {"age_days": None, "age_weeks": None}
    delta = (date.today() - dob).days
    return {"age_days": max(delta, 0), "age_weeks": max(delta, 0) // 7}


def normalize_status(raw_status: str) -> str:
    return raw_status.strip().lower()


def transgene_values_from_form(form) -> list[str]:
    return [form.get(f"transgene_{index}", "").strip() for index in range(1, 5)]


def genotype_string_from_transgenes(transgenes: list[str]) -> str:
    return "; ".join([value for value in transgenes if value])


def split_genotype(raw_value: str) -> list[str]:
    if not raw_value:
        return []
    normalized = raw_value.replace(",", ";")
    return [part.strip() for part in normalized.split(";") if part.strip()]


def sync_mouse_transgenes(mouse: MouseRecord, transgenes: list[str]) -> None:
    padded = transgenes + [""] * (4 - len(transgenes))
    mouse.transgene_1 = padded[0]
    mouse.transgene_2 = padded[1]
    mouse.transgene_3 = padded[2]
    mouse.transgene_4 = padded[3]
    mouse.genotype = genotype_string_from_transgenes(padded)


# Statuses that mean the mouse has died. Choosing one stamps today as the
# date of death (if none is set); moving a mouse back out of one (a sac
# entered by mistake, say) clears the date so the mouse is alive again.
END_STATUSES = {"sac", "dead", "found dead", "died", "euthanized", "euthanised"}


def is_end_status(status: str | None) -> bool:
    return normalize_status(status or "") in END_STATUSES


def apply_status_rules(mouse: MouseRecord, previous_status: str | None) -> None:
    if is_end_status(mouse.status):
        if mouse.date_of_death is None:
            mouse.date_of_death = date.today()
    elif is_end_status(previous_status):
        mouse.date_of_death = None


_INACTIVE_STATUSES = frozenset(END_STATUSES | {"transfer"})


def mouse_is_active(mouse: MouseRecord) -> bool:
    return mouse.date_of_death is None and normalize_status(mouse.status or "") not in _INACTIVE_STATUSES


def _cage_position(cage) -> str:
    from . import positions
    if cage is None or cage.rack is None:
        return ""
    return positions.label(cage.rack_row, cage.rack_col, cage.rack.naming, cage.rack.cols)


def transgene_parts(mouse: MouseRecord) -> list[str]:
    """A mouse's transgenes as the sheets show them: the filled ones in
    order (a lone third reads as Transgene 1), else its genotype split up."""
    transgenes = [mouse.transgene_1, mouse.transgene_2, mouse.transgene_3, mouse.transgene_4]
    return [value for value in transgenes if value] or split_genotype(mouse.genotype)


def transgene_columns_used(mice) -> list[bool]:
    """Which of the sheets' four transgene columns any of `mice` fills."""
    counts = [len(transgene_parts(mouse)) for mouse in mice]
    return [any(c >= n for c in counts) for n in range(1, 5)]


def _cage_cells(cage) -> tuple:
    """What a mouse's row shows of its cage (mouse_display_row): its number,
    location, rack (id and name), position, and whether it holds breeders."""
    if cage is None:
        return "", "", None, "", "", False
    return (cage.cage_id, cage.cage_location, cage.rack_id_fk, cage.rack.name if cage.rack else "",
            _cage_position(cage), holds_breeders(cage.purpose))


def mouse_display_row(mouse: MouseRecord, current_username: str | None = None, current_role: str | None = None,
                      cages: dict | None = None, today: date | None = None) -> dict[str, object]:
    """A mouse's row on the sheets. For a sheet's thousands: `cages`, a dict
    the caller keeps for what each cage's mice's rows show of it (worked out
    once a cage), and `today`, the day their ages count to."""
    dob = mouse.litter.date_of_birth if mouse.litter else None
    age_days = max(((today or date.today()) - dob).days, 0) if dob is not None else None   # calculate_age_fields
    genotype_parts = transgene_parts(mouse)
    can_edit_breeder = current_role == "admin" or mouse.owner == current_username
    cage = mouse.cage
    if cages is None:
        cage_cells = _cage_cells(cage)
    else:
        key = None if cage is None else cage.id
        cage_cells = cages.get(key)
        if cage_cells is None:
            cage_cells = cages[key] = _cage_cells(cage)
    cage_id, cage_location, cage_rack_id, cage_rack, cage_position, cage_is_breeder = cage_cells
    active = mouse_is_active(mouse)
    return {
        "id": mouse.id,
        "mouse_id": mouse.mouse_id,
        "ear_tag": mouse.ear_tag or "",
        "strain": mouse.strain or "",
        "attrs": custom_fields.values(mouse),
        "active": active,
        "active_label": "Y" if active else "N",
        "age_weeks": age_days // 7 if age_days is not None else None,
        "age_days": age_days,
        "gender": mouse.gender,
        "genotype": mouse.genotype,
        "genotype_full": mouse.genotype or "",
        "genotype_parts": genotype_parts,
        "transgene_1": genotype_parts[0] if len(genotype_parts) > 0 else "",
        "transgene_2": genotype_parts[1] if len(genotype_parts) > 1 else "",
        "transgene_3": genotype_parts[2] if len(genotype_parts) > 2 else "",
        "transgene_4": genotype_parts[3] if len(genotype_parts) > 3 else "",
        "cage_id": cage_id,
        "cage_location": cage_location,
        "cage_rack_id": cage_rack_id,
        "cage_rack": cage_rack,
        "cage_position": cage_position,
        "owner": mouse.owner,
        "litter_id": mouse.litter.litter_id if mouse.litter else "",
        "date_of_birth": dob.isoformat() if dob else "",
        "status": mouse.status,
        "note": mouse.note,
        "date_of_death": mouse.date_of_death.isoformat() if mouse.date_of_death else "",
        "can_edit": not cage_is_breeder or can_edit_breeder,
    }


# What a cage's purpose means. They are two different cages; see "The
# mouse colony's words" in CLAUDE.md before changing either.
# - Breeding: a mating cage, mating now. Only it offers Litter born,
#   Genotyping and Wean, and its litters are its pups.
# - Breeder: the lab's breeding stock, mice kept to be bred. Each person
#   keeps part of the lab's stock in them, so a Breeder cage starts out
#   shared with the lab (app/access.py) and the Breeders tab lists them
#   for anyone to Pick from.
BREEDING_PURPOSE = "breeding"
BREEDER_PURPOSE = "breeder"
BREEDER_PURPOSES = {BREEDER_PURPOSE, BREEDING_PURPOSE}   # either: mice kept for breeding


def is_breeding_cage(purpose: str | None) -> bool:
    """A mating cage: the one with litters, genotyping and weaning."""
    return (purpose or "").strip().lower() == BREEDING_PURPOSE


def is_breeder_cage(purpose: str | None) -> bool:
    """A cage of the lab's breeding stock (the Breeders tab)."""
    return (purpose or "").strip().lower() == BREEDER_PURPOSE


def holds_breeders(purpose: str | None) -> bool:
    """Breeder or Breeding: its mice are breeders (the breeder summary)."""
    return (purpose or "").strip().lower() in BREEDER_PURPOSES


def cage_is_active(cage: CageRecord) -> bool:
    """Active means it holds living mice, and nothing else: an empty cage
    is not active, whatever its purpose (there is no Retired purpose)."""
    return any(mouse_is_active(mouse) for mouse in cage.mice)


# A place in a rack may hold more than one cage: a cage is often set up in a
# place before the one there is ended. The rack shows one of them, and marks
# the place with "!" while two or more there are active.
def placed_when(cage: CageRecord) -> tuple:
    """The later of two cages in one place sorts last: when it was put
    there (placed_at), else, for a cage placed before 2.0.8, when it was
    made; then the later record."""
    return (cage.placed_at or cage.created_at or datetime.min, cage.id or 0)


def slot_order(cages) -> list[CageRecord]:
    """The cages of one place in the order the rack lists them: the one it
    shows first. That is the latest placed of those with living mice, or,
    when none has any, the latest placed."""
    return sorted(cages, key=lambda c: (cage_is_active(c), placed_when(c)), reverse=True)


def slot_cages(cages) -> dict[tuple[int, int, int], list[CageRecord]]:
    """Placed cages by their place (rack id, row, col), each place's in
    slot_order."""
    places: dict[tuple[int, int, int], list[CageRecord]] = defaultdict(list)
    for cage in cages:
        if cage.rack_id_fk and cage.rack_row and cage.rack_col:
            places[(cage.rack_id_fk, cage.rack_row, cage.rack_col)].append(cage)
    return {place: slot_order(here) for place, here in places.items()}


def crowded(cages) -> bool:
    """Whether a place's cages earn its "!": two or more are active."""
    return sum(1 for c in cages if cage_is_active(c)) > 1


def dropdown_options_map(session) -> dict[str, list[str]]:
    options = defaultdict(list)
    rows = session.scalars(select(DropdownOption).order_by(DropdownOption.field_name, DropdownOption.position, DropdownOption.option_value)).all()
    for row in rows:
        options[row.field_name].append(row.option_value)
    for field in MOUSE_PRESET_FIELDS:
        options.setdefault(field, [])
    return dict(options)


def dropdown_records_map(session) -> dict[str, list[DropdownOption]]:
    grouped: dict[str, list[DropdownOption]] = {field: [] for field in MOUSE_PRESET_FIELDS}
    rows = session.scalars(select(DropdownOption).order_by(DropdownOption.field_name, DropdownOption.position, DropdownOption.option_value)).all()
    for row in rows:
        grouped.setdefault(row.field_name, []).append(row)
    return grouped


def current_lab_usernames(session) -> list[str]:
    return [user.username for user in session.scalars(select(UserAccount).order_by(UserAccount.username)).all()]


def breeder_summary(session) -> list[dict[str, object]]:
    today = date.today()
    breeder_buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"f_young": 0, "f_old": 0, "m_young": 0, "m_old": 0, "oldest": 0, "total": 0}
    )

    # Living mice in breeder cages, with their litters' dates of birth, as
    # plain rows: a large colony's breeders run to thousands.
    m, c, l = MouseRecord.__table__, CageRecord.__table__, LitterRecord.__table__
    living_in_breeders = (select(m.c.gender, m.c.genotype, m.c.status, m.c.date_of_death, c.c.purpose,
                                 l.c.date_of_birth)
                          .select_from(m.join(c, m.c.cage_id_fk == c.c.id).outerjoin(l, l.c.id == m.c.litter_id_fk))
                          .where(m.c.date_of_death.is_(None), func.lower(func.trim(c.c.purpose)).in_(BREEDER_PURPOSES)))
    for mouse in session.execute(living_in_breeders):
        if not mouse_is_active(mouse):
            continue
        if not holds_breeders(mouse.purpose):
            continue
        dob = mouse.date_of_birth
        if dob is None:
            continue
        age_weeks = max((today - dob).days, 0) // 7
        genotype = (mouse.genotype or "").strip() or "(no genotype)"
        gender = (mouse.gender or "").strip().upper()

        bucket = breeder_buckets[genotype]
        bucket["total"] += 1
        if age_weeks > bucket["oldest"]:
            bucket["oldest"] = age_weeks
        if 8 <= age_weeks <= 20 and gender == "F":
            bucket["f_young"] += 1
        elif 20 < age_weeks <= 30 and gender == "F":
            bucket["f_old"] += 1
        elif 8 <= age_weeks <= 20 and gender == "M":
            bucket["m_young"] += 1
        elif 20 < age_weeks <= 30 and gender == "M":
            bucket["m_old"] += 1

    rows: list[dict[str, object]] = []
    for genotype in sorted(breeder_buckets.keys()):
        bucket = breeder_buckets[genotype]
        oldest = bucket["oldest"]
        if oldest <= 20:
            urgency = "ok"
        elif oldest <= 30:
            urgency = "warn"
        else:
            urgency = "urgent"
        rows.append(
            {
                "genotype": genotype,
                "f_young": bucket["f_young"],
                "f_old": bucket["f_old"],
                "m_young": bucket["m_young"],
                "m_old": bucket["m_old"],
                "total": bucket["total"],
                "oldest_age_weeks": oldest,
                "urgency": urgency,
            }
        )
    return rows


# A Breeder cage as the Breeders tab reads it (breeder_mice): its own
# columns, its rack (plain_records.SheetRack) and its mice (plain records).
_BreederCage = namedtuple("CageRecord", ("id", "cage_id", "cage_location", "room", "location_detail", "card_id",
                                         "genotype_summary", "created_at", "rack_id_fk", "rack_row", "rack_col",
                                         "rack", "mice"))


def breeder_mice(session, current_username: str | None, current_role: str | None,
                 recent: date | None = None) -> tuple[list[dict[str, object]], dict[str, int]]:
    """The Breeders tab's cards, one a Breeder cage, and what it left out.

    With `recent` (a day), as the cage sheet does: a cage that holds no
    living mouse and had nothing since that day (made, or a mouse died) is
    left out, and so is a mouse that ended before it (a colony keeps every
    cage and mouse it had; years of them made the tab take a minute). The
    card's counts and its search still go by all its mice. The second value
    says how many were left out: {"cages": n, "mice": n}."""
    from . import plain_records
    from .models import MouseRack
    is_breeder = func.lower(func.trim(CageRecord.purpose)) == BREEDER_PURPOSE
    # The lab's breeding stock to pick from: Breeder cages, not the
    # mating (Breeding) cages, whose mice are in use. Read as plain rows
    # (app/plain_records.py), each cage with its rack and its mice (with
    # their litters): the ORM took most of the tab's time.
    chosen = select(CageRecord.id).where(is_breeder)
    if recent is not None:
        # Only cages that may be shown are loaded; the rule itself is below.
        chosen = chosen.where(
            CageRecord.id.in_(select(MouseRecord.cage_id_fk).where(
                MouseRecord.date_of_death.is_(None) | (MouseRecord.date_of_death >= recent)))
            | (CageRecord.created_at >= datetime.combine(recent, datetime.min.time())))
    racks = {r[0]: plain_records.SheetRack(*r) for r in session.execute(
        select(*(MouseRack.__table__.c[name] for name in plain_records.COLUMNS["mouse_racks"])))}
    cage_columns = ("id", "cage_id", "cage_location", "room", "location_detail", "card_id", "genotype_summary",
                    "created_at", "rack_id_fk", "rack_row", "rack_col")
    t = CageRecord.__table__
    cages = [_BreederCage(*row, racks.get(row[8]), []) for row in session.execute(
        select(*(t.c[name] for name in cage_columns)).where(t.c.id.in_(chosen)))]
    by_id = {cage.id: cage for cage in cages}
    # Each cage's mice by number (plain_records.mice sorts them).
    for mouse in plain_records.mice(session, MouseRecord.cage_id_fk.in_(chosen)):
        if mouse.cage_id_fk in by_id:
            by_id[mouse.cage_id_fk].mice.append(mouse)
    cages = natural_sorted(cages, key=lambda c: c.cage_id)
    left_out = {"cages": 0, "mice": 0}
    if recent is not None:
        cages = [c for c in cages if cage_is_active(c) or c.created_at and c.created_at.date() >= recent
                 or any(m.date_of_death and m.date_of_death >= recent for m in c.mice)]
        left_out["cages"] = (session.scalar(select(func.count(CageRecord.id)).where(is_breeder)) or 0) - len(cages)
    grouped: list[dict[str, object]] = []
    cells: dict = {}
    today = date.today()
    for cage in cages:
        sorted_mice = cage.mice
        # Its owner: the first of its mice (in the order they were added) that has one.
        owner_name = min(((mouse.id, mouse.owner) for mouse in sorted_mice if mouse.owner), default=(0, ""))[1]
        listed = sorted_mice if recent is None else [
            mouse for mouse in sorted_mice if mouse.date_of_death is None or mouse.date_of_death >= recent]
        left_out["mice"] += len(sorted_mice) - len(listed)
        mouse_rows = [mouse_display_row(mouse, current_username, current_role, cells, today) for mouse in listed]
        search_parts: list[str] = [
            cage.cage_id or "",
            cage.cage_location or "",
            cage.room or "",
            cage.location_detail or "",
            cage.card_id or "",
            cage.genotype_summary or "",
            owner_name,
        ]
        for mouse in sorted_mice:
            search_parts.extend(
                [
                    str(mouse.mouse_id),
                    mouse.gender or "",
                    mouse.genotype or "",
                    mouse.note or "",
                    mouse.owner or "",
                    mouse.status or "",
                ]
            )
            if mouse.litter and mouse.litter.litter_id:
                search_parts.append(mouse.litter.litter_id)
        rack = cage.rack.name if cage.rack else ""
        position = _cage_position(cage)
        search_parts += [rack, position]
        search_blob = " ".join(part for part in search_parts if part).lower()
        active_count = sum(1 for mouse in sorted_mice if mouse_is_active(mouse))
        living = [mouse for mouse in sorted_mice if mouse_is_active(mouse)]
        females = sum(1 for mouse in living if mouse.gender == "F")
        males = sum(1 for mouse in living if mouse.gender == "M")
        grouped.append(
            {
                "cage_id": cage.cage_id,
                "id": cage.id,
                "rack": rack,
                "position": position,
                "females": females,
                "males": males,
                "cage_location": cage.cage_location,
                "room": cage.room,
                "location_detail": cage.location_detail,
                "card_id": cage.card_id,
                "genotype_summary": cage.genotype_summary,
                "owner": owner_name or "Unassigned",
                "search_blob": search_blob,
                "mice": mouse_rows,
                "active_count": active_count,
                "total_count": len(sorted_mice),
                "editable": current_role == "admin" or owner_name == current_username,
            }
        )
    return grouped, left_out


# ---------------------------------------------------------------------------
# Auto-derived calendar items
# ---------------------------------------------------------------------------
# These come from EXISTING domain rows (litters, mice, experiments) rather
# than from CalendarEvent / TaskItem. They're read-only on the calendar —
# the source of truth is the originating record, so editing happens there.
# The output shape matches what TOAST UI Calendar expects, with `isReadOnly`
# set so drag-to-reschedule is blocked.

# Defaults reflect typical mouse colony practice:
# - weaning at P21 (3 weeks)
# - genotyping ~a week after weaning (P28)
# - "old mouse" sac-threshold reminder at 30 weeks
# weaning_offset and genotyping_offset are defined with the cage dates
# above (one rule each for the home dashboard, cage cards and the calendar).
SAC_THRESHOLD_WEEKS = 30


def _parent_label(session, info: str) -> str:
    """Translate a litter's father_info / mother_info field into a label
    that includes the parent mouse's genotype. The field often holds just a
    mouse_id (e.g. "3") or text containing one (e.g. "M3 ♂"); we extract
    the first integer, look up the mouse, and append its genotype/
    transgenes. Falls back to the raw value when nothing matches."""
    import re as _re

    info = (info or "").strip()
    if not info:
        return ""
    match = _re.search(r"\d+", info)
    if not match:
        return info
    mouse_id_int = int(match.group(0))
    mouse = session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == mouse_id_int))
    if mouse is None:
        return info
    # Prefer the rolled-up `genotype` field if set; otherwise stitch the
    # individual transgene slots so we always have something useful.
    geno = (mouse.genotype or "").strip()
    if not geno:
        parts = [p.strip() for p in (mouse.transgene_1, mouse.transgene_2, mouse.transgene_3, mouse.transgene_4) if p and p.strip()]
        geno = "; ".join(parts)
    return f"M{mouse_id_int} ({geno})" if geno else f"M{mouse_id_int}"


def _auto_item(*, kind_tag: str, anchor_id: int, title: str, color: str, day: date,
               body: str = "", source: str = "", href: str = "") -> dict:
    """Helper to build one auto-derived TOAST UI schedule dict."""
    start_iso = datetime.combine(day, datetime.min.time()).isoformat()
    end_iso = datetime.combine(day, datetime.max.time()).isoformat()
    return {
        "id": f"auto-{source}-{anchor_id}-{kind_tag}",
        "kind": "auto",
        "calendarId": "auto",
        "title": title,
        "category": "allday",
        "isAllday": True,
        "start": start_iso,
        "end": end_iso,
        "backgroundColor": color,
        "borderColor": color,
        "body": body,
        "isReadOnly": True,
        "raw": {"source": source, "anchor_id": anchor_id, "href": href},
    }


def derive_auto_calendar_items(session, start_dt=None, end_dt=None) -> list[dict]:
    """Walk the litters / mice / experiments tables and emit a list of
    auto-derived calendar items in the requested window. Each row keeps a
    link back to its source via raw.href so clicking the item can navigate
    to the originating record."""
    items: list[dict] = []
    # A parent is looked up once, however many litters name it.
    parent_labels: dict[str, str] = {}

    def parent_label(info) -> str:
        if info not in parent_labels:
            parent_labels[info] = _parent_label(session, info)
        return parent_labels[info]

    geno_days = genotyping_offset(session)

    def _in_window(d: date) -> bool:
        if start_dt and d < (start_dt.date() if hasattr(start_dt, "date") else start_dt):
            return False
        if end_dt and d > (end_dt.date() if hasattr(end_dt, "date") else end_dt):
            return False
        return True

    # ----- Cage genotyping ---------------------------------------------
    # Each litter born in a cage and not yet weaned (cage_litters) has its
    # genotyping day (born + the lab's genotyping day); a cage with two
    # litters has two, each titled with its birth date. The body includes
    # the father + mother genotypes from the litter of its pups, when
    # Genotyping made one.
    from sqlalchemy.orm import selectinload
    births = session.scalars(
        select(CageLitter).options(selectinload(CageLitter.cage).selectinload(CageRecord.cage_litters),
                                   selectinload(CageLitter.litter).selectinload(LitterRecord.mice))
        .where(CageLitter.weaned_on.is_(None))).all()
    for cl in births:
        if pups_all_dead(cl):
            continue
        cage, dob = cl.cage, cl.born
        several = len(open_litters(cage)) > 1

        location_label = ((cage.cage_location or cage.cage_id or "").strip()
                          or gettext("Cage %(cage)s", cage=cage.id))

        # The litter of its pups, or one born that day with a mouse in
        # this cage. Fall back gracefully if missing.
        litter = cl.litter or session.scalar(
            select(LitterRecord)
            .where(LitterRecord.date_of_birth == dob)
            .where(LitterRecord.mice.any(MouseRecord.cage_id_fk == cage.id))
            .limit(1)
        )

        # Resolve father_info / mother_info → "M<id> (genotype)" by looking
        # up the parent mouse. This is what the user actually wants to see
        # on the wean / geno reminder, not just the raw ID.
        father = parent_label(litter.father_info) if litter else ""
        mother = parent_label(litter.mother_info) if litter else ""
        if not father and cl.sire:
            father = parent_label(cl.sire)
        if not mother and cl.dam:
            mother = parent_label(cl.dam)
        cage_geno = (cage.genotype_summary or "").strip()
        # Compose the notes line. Always include DOB; include parent geno
        # info when present, otherwise fall back to the cage's own summary.
        body_parts = [gettext("DOB %(date)s", date=dob.isoformat())]
        if father:
            body_parts.append(gettext("Father: %(mouse)s", mouse=father))
        if mother:
            body_parts.append(gettext("Mother: %(mouse)s", mouse=mother))
        if cage_geno and not (father or mother):
            body_parts.append(gettext("Genotype: %(genotype)s", genotype=cage_geno))
        body = " · ".join(body_parts)

        # Weaning comes from weaning_due below, the same list Home shows.
        geno = dob + timedelta(days=geno_days)
        if _in_window(geno):
            title = gettext("Genotype - %(where)s", where=location_label)
            if several:
                title = gettext("%(what)s (born %(born)s)", what=title, born=born_day(dob))
            items.append(_auto_item(
                # One a cage, as before; a second litter's has its own id.
                kind_tag="geno" if not several else f"geno-{dob.isoformat()}", anchor_id=cage.id,
                title=title,
                color="#fcb77e",  # apricot
                day=geno, body=body, source="cage",
                href=f"/colony?view=cages&scope=all&q={quote(str(cage.cage_id))}",
            ))

    # ----- Weaning at P21: cages with a litter-born date, and litters not
    # yet weaned (weaning_due, shared with Home) -------------------------
    def _day(value, default: date) -> date:
        if not value:
            return default
        return value.date() if isinstance(value, datetime) else value

    lo = _day(start_dt, date.today() - timedelta(days=365))
    hi = _day(end_dt, date.today() + timedelta(days=365))
    for wean in weaning_due(session, lo, hi):
        cage, litter = wean["cage"], wean["litter"]
        body_parts = [gettext("DOB %(date)s", date=wean["born"].isoformat())]
        if litter is not None:
            father = parent_label(litter.father_info)
            mother = parent_label(litter.mother_info)
            body_parts += [gettext("Father: %(mouse)s", mouse=father)] if father else []
            body_parts += [gettext("Mother: %(mouse)s", mouse=mother)] if mother else []
        elif wean["cage_litter"] is not None:
            sire, dam = wean["cage_litter"].sire, wean["cage_litter"].dam
            body_parts += [gettext("Father: %(mouse)s", mouse=parent_label(sire))] if sire else []
            body_parts += [gettext("Mother: %(mouse)s", mouse=parent_label(dam))] if dam else []
        if cage is not None and (cage.genotype_summary or "").strip() and len(body_parts) == 1:
            body_parts.append(gettext("Genotype: %(genotype)s", genotype=cage.genotype_summary.strip()))
        where = ""
        if cage is not None:
            where = (cage.cage_location or "").strip()
        items.append(_auto_item(
            kind_tag="wean" if not wean["several"] else f"wean-{wean['born'].isoformat()}",
            anchor_id=(cage.id if cage is not None else litter.id),
            title=(gettext("Wean - %(what)s (%(where)s)", what=weaning_title(wean), where=where) if where
                   else gettext("Wean - %(what)s", what=weaning_title(wean))),
            color="#b6e2a1",  # pistachio
            day=wean["due"], body=" · ".join(body_parts), source="cage" if cage is not None else "litter",
            href=(f"/colony?view=cages&scope=all#cage-{cage.id}" if cage is not None else "/colony?view=litters"),
        ))

    # ----- Old-mouse sac threshold (>= 30 weeks via litter DOB) ----------
    # Mice link to litters; we walk mice whose litter has a DOB. We skip
    # any mouse that's already dead.
    # Only the mice born early enough to reach it in the window, and only
    # the three values used: a colony's living mice would otherwise each
    # load their litter, one query a litter.
    threshold = timedelta(days=SAC_THRESHOLD_WEEKS * 7)
    sac_query = (select(MouseRecord.id, MouseRecord.mouse_id, LitterRecord.date_of_birth)
                 .join(LitterRecord, MouseRecord.litter_id_fk == LitterRecord.id)
                 .where(LitterRecord.date_of_birth.is_not(None))
                 .where(MouseRecord.date_of_death.is_(None))
                 .order_by(MouseRecord.id))
    if start_dt:
        sac_query = sac_query.where(LitterRecord.date_of_birth >= _day(start_dt, date.today()) - threshold)
    if end_dt:
        sac_query = sac_query.where(LitterRecord.date_of_birth <= _day(end_dt, date.today()) - threshold)
    for m_id, m_number, dob in session.execute(sac_query):
        threshold_day = dob + threshold
        if not _in_window(threshold_day):
            continue
        items.append(_auto_item(
            kind_tag="sac", anchor_id=m_id,
            title=gettext("Sac reminder · M%(mouse)s", mouse=m_number),
            color="#f9a8a8",  # rose — gentle warning
            day=threshold_day,
            body=gettext("%(weeks)s weeks since litter DOB (%(date)s)", weeks=SAC_THRESHOLD_WEEKS, date=dob.isoformat()),
            source="mouse",
            href=f"/colony?view=mice&scope=all&q={m_number}",
        ))

    # ----- Experiment start + end --------------------------------------
    experiments = session.scalars(
        select(Experiment).where(
            (Experiment.start_date.is_not(None)) | (Experiment.end_date.is_not(None))
        )
    ).all()
    for ex in experiments:
        if ex.start_date and _in_window(ex.start_date):
            items.append(_auto_item(
                kind_tag="start", anchor_id=ex.id,
                title=gettext("Exp start · %(name)s", name=ex.name),
                color="#c4b5fd",  # lavender
                day=ex.start_date,
                body=(ex.description or "")[:200],
                source="experiment",
                href=f"/colony/experiments/{ex.id}",
            ))
        if ex.end_date and _in_window(ex.end_date):
            items.append(_auto_item(
                kind_tag="end", anchor_id=ex.id,
                title=gettext("Exp end · %(name)s", name=ex.name),
                color="#a4c8f0",  # sky
                day=ex.end_date,
                body=(ex.description or "")[:200],
                source="experiment",
                href=f"/colony/experiments/{ex.id}",
            ))

    # ----- Zebrafish: clutches → tank-up / fin-clip / adult -------------
    clutches = session.scalars(
        select(ClutchRecord).where(ClutchRecord.date_of_fertilization.is_not(None))
    ).all()
    for c in clutches:
        dof = c.date_of_fertilization
        if dof is None:
            continue
        label = c.clutch_id or f"C{c.id}"
        line_name = c.line.name if c.line else ""
        body_parts = [gettext("DOF %(date)s", date=dof.isoformat())]
        if line_name:
            body_parts.append(gettext("Line: %(line)s", line=line_name))
        if c.embryo_count:
            body_parts.append(gettext("Embryos: %(n)s", n=c.embryo_count))
        body = " · ".join(body_parts)

        milestones = [
            ("tank-up", 5,  "#a3e0d8", gettext("Tank up · %(clutch)s", clutch=label)),
            ("fin-clip", 30, "#fcb77e", gettext("Fin clip · %(clutch)s", clutch=label)),
            ("adult", 90, "#c4b5fd", gettext("Adult · %(clutch)s", clutch=label)),
        ]
        for tag, offset, color, title in milestones:
            day = dof + timedelta(days=offset)
            if _in_window(day):
                items.append(_auto_item(
                    kind_tag=tag, anchor_id=c.id,
                    title=title,
                    color=color, day=day, body=body,
                    source="clutch",
                    href=f"/zebrafish?view=clutches#clutch-{c.id}",
                ))

    # ----- Zebrafish: mating return reminders ---------------------------
    mating_tanks = session.scalars(
        select(TankRecord)
        .where(TankRecord.purpose == "mating")
        .where(TankRecord.mating_return_at.is_not(None))
    ).all()
    for t in mating_tanks:
        d = t.mating_return_at
        if d is None or not _in_window(d):
            continue
        items.append(_auto_item(
            kind_tag="mating-return", anchor_id=t.id,
            title=gettext("Return mating · %(tank)s", tank=t.tank_id),
            color="#f4b8d8",  # pink
            day=d,
            body=(t.notes or "")[:200],
            source="mating",
            href=f"/zebrafish?view=tanks#tank-{t.id}",
        ))

    return items


def sample_sources(session, refs: bool = True) -> list[dict]:
    """Where a sample can come from: each colony, with the identifiers it
    uses, for the source picker. Order follows the sidebar. Without `refs`,
    each colony's identifiers are left out (a sheet's rows need only its
    name)."""
    from . import organism_service as organisms
    from .models import Organism, OrgHousing, OrgLine

    sources = [{
        "kind": "mouse", "label": "Mouse",
        "refs": [str(m) for m in session.scalars(select(MouseRecord.mouse_id).order_by(MouseRecord.mouse_id))]
        if refs else [],
    }]
    fish_refs = []
    if refs:
        fish_refs = natural_sorted(session.scalars(select(TankRecord.tank_id)))
        fish_refs += natural_sorted(i for i in session.scalars(select(FishRecord.individual_id)) if i)
    sources.append({"kind": "fish", "label": "Fish", "refs": fish_refs})
    for module in organisms.list_modules(session):
        found = []
        for model in (OrgLine, OrgHousing, Organism) if refs else ():
            found += natural_sorted(c for c in session.scalars(
                select(model.code).where(model.module_id_fk == module.id)) if c)
        sources.append({"kind": f"organism:{module.key}", "label": module.label, "refs": found})
    sources.append({"kind": "other", "label": "Other", "refs": []})
    return sources


def sample_source_label(kind: str, sources: list[dict]) -> str:
    for source in sources:
        if source["kind"] == kind:
            return source["label"]
    return kind.split(":", 1)[-1].replace("_", " ").title() if kind else ""


