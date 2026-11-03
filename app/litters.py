"""A Breeding cage's litters: Litter born, Genotyping and Wean.

Only a Breeding (mating) cage has litters (services.is_breeding_cage): it
is the only cage with Litter born, Genotyping and Wean, and its litters are
its pups. A mating cage often holds two females, so a second litter can
come before the first is weaned; each is its own CageLitter, with its own
weaning and genotyping days (litter_dates.open_litters).

Every way into a cage's litters asks here: the cage's buttons and dialogs,
its sheet's Litter born cell and New cage, an assistant's proposals
(app/actions.py), Home's suggestions (app/home_layouts.py) and a phone's
offline changes (app/sync.py). Each rule answers with its refusal in words,
or None; what was done comes back as a sentence for the caller to show.
"""
from __future__ import annotations

from datetime import date, timedelta

from .days import fmt_day
from .i18n import gettext, ngettext
from .models import CageLitter
from .services import is_breeding_cage, mouse_is_active
from .litter_dates import (
    MIN_WEAN_AGE_DAYS,
    find_open_litter,
    open_litters,
    refresh_birth_date,
    set_birth_date,
    waiting_litters,
    weaning_offset,
)

MAX_PUPS = 40           # the most pups one litter can have
PUP_AGE_DAYS = 28       # younger than this, mice in a cage are its pups


def not_breeding(cage) -> str | None:
    """Why this cage can't have litters, or None for a Breeding cage. The
    page shows the buttons only there; the server says so too, for the API,
    an assistant's proposals and a phone syncing what was done offline."""
    if cage is None or is_breeding_cage(cage.purpose):
        return None
    return gettext("Cage %(cage)s isn't a Breeding cage: only a mating cage has litters, genotyping and weaning. Set its purpose to Breeding first.", cage=cage.cage_id)


def may_have_litters(cage) -> bool:
    return cage is not None and not_breeding(cage) is None


def pups(raw, when: str = "born") -> tuple[int | None, str | None]:
    """(how many pups `raw` says, or None when it says nothing; the refusal).
    `when`: "born" (Litter born) or "genotyping" (making the litter's mice)."""
    raw = str(raw if raw is not None else "").strip()
    if not raw:
        return None, None
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_PUPS:
        if when == "genotyping":
            return None, gettext("Pups must be a whole number from 1 to 40, not “%(value)s”. No litter was created.",
                                 value=raw)
        return None, gettext("Pups must be a whole number from 1 to 40, not “%(value)s”. Nothing was changed.",
                             value=raw)
    return int(raw), None


def default(cage, what: str) -> CageLitter | None:
    """The litter a change means when it doesn't say: the oldest still to
    wean; for Genotyping, the oldest whose pups aren't mice yet."""
    waiting = waiting_litters(cage) or open_litters(cage)
    if what == "genotyping":
        return next((cl for cl in waiting if cl.litter_id_fk is None), waiting[0] if waiting else None)
    return waiting[0] if waiting else None


def choose(cage, named: date | None, what: str) -> tuple[CageLitter | None, str | None]:
    """(the cage's litter born `named`, or the default; the refusal when it
    has no such litter)."""
    if named is None:
        return default(cage, what), None
    chosen = find_open_litter(cage, named)
    if chosen is None:
        have = ", ".join(fmt_day(cl.born) for cl in open_litters(cage)) or "-"
        return None, gettext("Cage %(cage)s has no litter born %(born)s that isn't weaned (it has %(have)s). Nothing was changed.", cage=cage.cage_id, born=fmt_day(named),
                             have=have)
    return chosen, None


def _fill(cl: CageLitter, count: int | None, dam: str, sire: str) -> None:
    if count:
        cl.pups = count
    if dam:
        cl.dam = dam
    if sire:
        cl.sire = sire


def record_birth(cage, born: date, count: int | None = None, dam: str = "", sire: str = "",
                 choice: str = "", named: date | None = None) -> tuple[str | None, str | None]:
    """Litter born on `cage`: (the refusal; what was recorded, in words).

    On a cage with a litter not yet weaned, `choice` says whether the new
    date is "another" litter (both are kept) or will "correct" the earlier
    one's date (with two, `named`: the birth date of the one to correct).
    Without saying, nothing is changed: guessing either way loses a
    litter's reminders. The same date again only adds the count, the
    father or the mother to that litter."""
    refused = not_breeding(cage)
    if refused:
        return refused, None
    dam, sire, choice = (dam or "")[:120], (sire or "")[:120], (choice or "").strip().lower()
    still = open_litters(cage)
    wean = fmt_day(born + timedelta(days=weaning_offset()))
    same = find_open_litter(cage, born)
    correcting_another = choice == "correct" and named not in (None, born)
    if (same is not None and not correcting_another) or not still or choice == "another":
        # The first litter, another one, or the same one again with its count.
        if same is None:
            same = CageLitter(born=born)
            cage.cage_litters.append(same)
        _fill(same, count, dam, sire)
        refresh_birth_date(cage)
        earlier = [cl for cl in still if cl is not same]
        if earlier and choice == "another":
            return None, gettext("Recorded another litter in cage %(cage)s, born %(born)s: weaning is due %(wean)s. The earlier litter (born %(earlier)s) is kept, with its own reminders.", cage=cage.cage_id, born=fmt_day(born), wean=wean,
                                 earlier=", ".join(fmt_day(cl.born) for cl in earlier))
        if born == date.today():
            return None, gettext("Recorded a litter born today in cage %(cage)s: weaning is due %(wean)s.",
                                 cage=cage.cage_id, wean=wean)
        return None, gettext("Recorded a litter born on %(born)s in cage %(cage)s: weaning is due %(wean)s.",
                             born=fmt_day(born), cage=cage.cage_id, wean=wean)
    days = ", ".join(fmt_day(cl.born) for cl in still)
    if choice != "correct":
        return ngettext("Cage %(cage)s already has a litter born %(born)s that isn't weaned. Say whether this is another litter (a second female: both are kept) or corrects that date. Nothing was changed.",
                        "Cage %(cage)s already has litters born %(born)s that aren't weaned. Say whether this is another litter (both are kept) or corrects one of their dates. Nothing was changed.",
                        len(still), cage=cage.cage_id, born=days), None
    if named is not None:
        wrong = find_open_litter(cage, named)
        if wrong is None:
            return gettext("Cage %(cage)s has no litter born %(born)s that isn't weaned (it has %(have)s). Nothing was changed.", cage=cage.cage_id, born=fmt_day(named),
                           have=days), None
    elif len(still) == 1:
        wrong = still[0]
    else:
        return gettext("Cage %(cage)s has litters born %(born)s: say which one's date to correct. Nothing was changed.", cage=cage.cage_id, born=days), None
    if same is not None and same is not wrong:
        return gettext("Cage %(cage)s already has a litter born %(born)s. Nothing was changed.", cage=cage.cage_id,
                       born=fmt_day(born)), None
    was = wrong.born
    wrong.born = born
    _fill(wrong, count, dam, sire)
    refresh_birth_date(cage)
    return None, gettext("Corrected the litter in cage %(cage)s: born %(born)s, not %(was)s. Weaning is due %(wean)s.",
                         cage=cage.cage_id, born=fmt_day(born), was=fmt_day(was), wean=wean)


def set_born(cage, born: date | None) -> str | None:
    """The cage's Litter born date typed in its sheet cell, its dialog or New
    cage: the oldest litter not yet weaned, so a date corrects that litter
    (or records the first) and clearing it removes that litter. Only a
    Breeding cage takes a date; clearing one is always allowed."""
    if born is not None and born != cage.date_give_birth:
        refused = not_breeding(cage)
        if refused:
            return refused
    set_birth_date(cage, born)
    return None


def pup_litter(cage):
    """The youngest litter among the cage's living mice, if it is still
    pups (born within PUP_AGE_DAYS)."""
    today = date.today()
    found = {m.litter.id: m.litter for m in cage.mice
             if mouse_is_active(m) and m.litter is not None and m.litter.date_of_birth
             and m.litter.weaned_on is None
             and 0 <= (today - m.litter.date_of_birth).days <= PUP_AGE_DAYS}
    return max(found.values(), key=lambda lit: lit.date_of_birth) if found else None


def too_young(cage, weaning: CageLitter | None = None, early: bool = False) -> str | None:
    """Why weaning this cage (its litter `weaning`) now needs a second look,
    or None. `early`: the person has confirmed (colony.html asks)."""
    if early:
        return None
    born = weaning.born if weaning is not None else None
    if born is None:
        litter = pup_litter(cage)
        born = litter.date_of_birth if litter else None
    if born is None:
        return None
    age = (date.today() - born).days
    if age >= min(MIN_WEAN_AGE_DAYS, weaning_offset()):
        return None
    due = born + timedelta(days=weaning_offset())
    if age < 0:
        return gettext("Cage %(cage)s's litter is dated %(born)s, in the future. Correct its date before weaning.",
                       cage=cage.cage_id, born=fmt_day(born))
    return gettext("The pups in cage %(cage)s are %(age)s days old; weaning is due %(due)s (P%(days)s). Nothing was changed. To wean them early anyway, confirm it when asked.", cage=cage.cage_id, age=age,
                   due=fmt_day(due), days=weaning_offset())


def weaned_said(cage, weaned: CageLitter | None) -> str:
    """Cage 12 is weaned; or, when it has another litter still to wean,
    which one was and which is left."""
    left = waiting_litters(cage)
    if weaned is not None and left:
        return gettext("Weaned the litter born %(born)s in cage %(cage)s. Its litter born %(next)s is still to wean (due %(due)s).", born=fmt_day(weaned.born), cage=cage.cage_id,
                       next=fmt_day(left[0].born), due=fmt_day(left[0].born + timedelta(days=weaning_offset())))
    return gettext("Cage %(cage)s is weaned.", cage=cage.cage_id)
