"""When a mouse litter is due: the days after birth the lab weans and
genotypes, a Breeding cage's litters not yet weaned, and the weaning list
Home, the calendar, the cage sheet and the reminder emails all read, so
none of them can disagree about when something is due. What a person does
to a litter (Litter born, Wean) and its rules are app/litters.py.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from .i18n import gettext
from .models import CageLitter, CageRecord, LitterRecord, MouseRecord
from .positions import natural


# Weaning at P21 and genotyping at P28 are the defaults; the lab may choose
# other days (Lab setup, Settings). The home dashboard, cage cards and the
# calendar all read weaning_offset and genotyping_offset: the cage, the
# calendar and Home must never disagree about when it is due.
WEAN_OFFSET_DAYS = 21          # the default (weaning_offset)
GENO_OFFSET_DAYS = 28          # the default (genotyping_offset)
CAGE_GENO_OFFSET_DAYS = GENO_OFFSET_DAYS


def _lab_day(name: str, session=None) -> int:
    """A day after birth the lab chose (lab.<name>), read once per request."""
    from flask import g, has_request_context
    from . import lab
    if has_request_context() and name in g:
        return g.get(name)
    if session is not None:
        value = getattr(lab, name)(session)
    else:
        from .db import SessionLocal
        with SessionLocal() as own:
            value = getattr(lab, name)(own)
    if has_request_context():
        setattr(g, name, value)
    return value


def genotyping_offset(session=None) -> int:
    """The day after birth the lab genotypes (P28 unless chosen)."""
    return _lab_day("genotyping_day", session)


def weaning_offset(session=None) -> int:
    """The day after birth the lab weans (P21 unless chosen)."""
    return _lab_day("weaning_day", session)


# Younger than this, weaning asks first: P21 is the usual day, and pups
# weaned before about P18 often do not survive without their mother.
MIN_WEAN_AGE_DAYS = 18


# ----- A cage's litters (models.CageLitter) ------------------------------
# A mating cage often holds two females, so a second litter comes a week or
# two after the first, before the first is weaned. Each birth is its own
# litter with its own weaning and genotyping days; the cage's
# date_give_birth is its oldest litter not yet weaned, kept so by
# refresh_birth_date wherever litters change.

def open_litters(cage: CageRecord) -> list[CageLitter]:
    """The cage's litters not yet weaned, oldest first."""
    return sorted((cl for cl in cage.cage_litters if cl.weaned_on is None), key=lambda cl: (cl.born, cl.id or 0))


def pups_all_dead(cl: CageLitter) -> bool:
    """Its pups were made mice at Genotyping and every one is recorded
    dead: nothing is left to wean or genotype."""
    litter = cl.litter
    return litter is not None and bool(litter.mice) and all(m.date_of_death is not None for m in litter.mice)


def waiting_litters(cage: CageRecord) -> list[CageLitter]:
    """The cage's litters still to be weaned (not weaned, and not all
    recorded dead), oldest first: what the cage, Home and the calendar list."""
    return [cl for cl in open_litters(cage) if not pups_all_dead(cl)]


def refresh_birth_date(cage: CageRecord) -> None:
    """The cage's one birth date, as everything that reads one date sees
    it: its oldest litter not yet weaned."""
    still = open_litters(cage)
    cage.date_give_birth = still[0].born if still else None


def set_birth_date(cage: CageRecord, born: date | None) -> None:
    """The cage's Litter born date typed in the sheet or a cage form: it is
    the oldest litter not yet weaned, so a date there corrects that litter
    (or records the first), and clearing it removes that litter."""
    still = open_litters(cage)
    if born is None:
        if still:
            cage.cage_litters.remove(still[0])
    elif not still:
        cage.cage_litters.append(CageLitter(born=born))
    elif still[0].born != born:
        still[0].born = born
    refresh_birth_date(cage)


def find_open_litter(cage: CageRecord, born: date | None) -> CageLitter | None:
    """The cage's litter not yet weaned born on that day, if any."""
    return next((cl for cl in open_litters(cage) if cl.born == born), None) if born else None


def litter_wean_due(cl: CageLitter) -> date:
    return cl.born + timedelta(days=weaning_offset())


def litter_genotyping_due(cl: CageLitter, session=None) -> date:
    return cl.born + timedelta(days=genotyping_offset(session))


def weaning_due(session, start: date, end: date) -> list[dict]:
    """Every litter waiting to be weaned whose day (born + the lab's weaning
    day, P21 unless chosen) falls
    between start and end, soonest first.

    The one source for Home, its layouts and the calendar, so they agree
    with each other and with the cage sheet. Two things wait to be weaned:
    each litter born in a cage (cage_litters; the pups may not be entered
    as mice yet), until it is weaned or its pups are recorded dead, and a
    litter of mice with a date of birth that has not been weaned (weaned_on)
    and still has living mice or a pup count. A litter of mice whose pups
    are a cage's litter counts once, as that cage's. Each item carries its
    cage's litter ("cage_litter"), and says when the cage has more than one
    waiting ("several"), so titles can tell them apart.
    """
    from sqlalchemy.orm import selectinload

    wean_days = weaning_offset(session)
    first, last = start - timedelta(days=wean_days), end - timedelta(days=wean_days)
    items: list[dict] = []
    births = session.scalars(
        select(CageLitter).options(
            selectinload(CageLitter.cage).selectinload(CageRecord.mice).selectinload(MouseRecord.litter),
            selectinload(CageLitter.cage).selectinload(CageRecord.rack),
            selectinload(CageLitter.cage).selectinload(CageRecord.cage_litters),
            selectinload(CageLitter.litter).selectinload(LitterRecord.mice))
        .where(CageLitter.weaned_on.is_(None), CageLitter.born >= first, CageLitter.born <= last)).all()
    counted: set[int] = set()
    for cl in births:
        cage, born = cl.cage, cl.born
        litter = cl.litter or next((m.litter for m in cage.mice
                                    if m.date_of_death is None and m.litter is not None
                                    and m.litter.date_of_birth == born), None)
        if litter is not None:
            counted.add(litter.id)
            if litter.weaned_on is not None:
                continue
        if pups_all_dead(cl):
            continue
        items.append({"born": born, "due": born + timedelta(days=wean_days), "cage": cage,
                      "litter": litter, "pups": litter.total_pups if litter else (cl.pups or 0),
                      "cage_litter": cl, "several": len(open_litters(cage)) > 1})
    litters = session.scalars(
        select(LitterRecord).options(selectinload(LitterRecord.mice).selectinload(MouseRecord.cage)
                                     .selectinload(CageRecord.rack))
        .where(LitterRecord.weaned_on.is_(None), LitterRecord.date_of_birth.is_not(None),
               LitterRecord.date_of_birth >= first, LitterRecord.date_of_birth <= last)).all()
    for litter in litters:
        if litter.id in counted:
            continue
        living = [m for m in litter.mice if m.date_of_death is None]
        if litter.mice and not living:
            continue
        cage = next((m.cage for m in living if m.cage is not None), None)
        items.append({"born": litter.date_of_birth, "due": litter.date_of_birth + timedelta(days=wean_days),
                      "cage": cage, "litter": litter, "pups": litter.total_pups or len(living),
                      "cage_litter": None, "several": False})
    items.sort(key=lambda item: (item["due"], natural(item["litter"].litter_id if item["litter"] else ""),
                                 natural(item["cage"].cage_id if item["cage"] else "")))
    return items


def born_day(day: date) -> str:
    """A litter's birth day as the lab writes dates ("29 Sep")."""
    from .days import fmt_day
    return fmt_day(day)


def weaning_title(item: dict) -> str:
    """"Litter L-12 · cage 12", or "Cage 12" before the pups are entered
    (in the page's language); "Cage 12 (born 29 Sep)" when the cage has two
    litters waiting, to tell them apart."""
    litter, cage = item["litter"], item["cage"]
    if litter is not None and cage is not None:
        title = gettext("Litter %(litter)s · cage %(cage)s", litter=litter.litter_id, cage=cage.cage_id)
    elif litter is not None:
        title = gettext("Litter %(litter)s", litter=litter.litter_id)
    elif cage is not None:
        title = gettext("Cage %(cage)s", cage=cage.cage_id)
    else:
        title = gettext("A litter")
    if item.get("several"):
        title = gettext("%(what)s (born %(born)s)", what=title, born=born_day(item["born"]))
    return title


def cage_litter_json(cl: CageLitter) -> dict:
    """A cage's litter as the API, the due list and an assistant see it:
    its birth date (what `litter` takes in litter_genotyping and wean), its
    pups, dam and sire if recorded, the litter Genotyping made of its pups, and
    its own days."""
    return {"born": cl.born.isoformat(), "pups": cl.pups, "dam": cl.dam or "", "sire": cl.sire or "",
            "litter": cl.litter.litter_id if cl.litter is not None else None,
            "age_days": (date.today() - cl.born).days,
            "wean_due": litter_wean_due(cl).isoformat(),
            "genotyping_due": litter_genotyping_due(cl).isoformat()}


def mark_weaned(cage: CageRecord, when: date | None = None,
                cage_litter: CageLitter | None = None) -> list[LitterRecord]:
    """Weaning one of a cage's litters (`cage_litter`, else its oldest not
    yet weaned): it is marked weaned, and so are the litters of mice its
    pups are (the living mice born that day). With no litter recorded in
    the cage, the youngest litter of mice in it still under P28. The cage's
    birth date moves on to its next litter, or is cleared."""
    when = when or date.today()
    if cage_litter is None:
        still = open_litters(cage)
        cage_litter = still[0] if still else None
    born = cage_litter.born if cage_litter is not None else None
    litters = {m.litter.id: m.litter for m in cage.mice
               if m.date_of_death is None and m.litter is not None and m.litter.date_of_birth
               and m.litter.weaned_on is None
               and (m.litter.date_of_birth == born if born else 0 <= (when - m.litter.date_of_birth).days <= 28)}
    if not born and litters:
        youngest = max(litters.values(), key=lambda lit: lit.date_of_birth)
        litters = {youngest.id: youngest}
    if cage_litter is not None:
        cage_litter.weaned_on = when
        if cage_litter.litter is not None and cage_litter.litter.weaned_on is None:
            litters[cage_litter.litter.id] = cage_litter.litter
    for litter in litters.values():
        litter.weaned_on = when
    refresh_birth_date(cage)
    return list(litters.values())


def cage_derived_dates(cage: CageRecord) -> dict[str, str]:
    if cage.date_give_birth is None:
        return {"genotyping_date": "", "weaning_date": ""}
    return {
        "genotyping_date": (cage.date_give_birth + timedelta(days=genotyping_offset())).isoformat(),
        "weaning_date": (cage.date_give_birth + timedelta(days=weaning_offset())).isoformat(),
    }
