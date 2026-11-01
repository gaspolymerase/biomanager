"""Write-ups an assistant drafts from the records (the report tools in
app/assistant_tools.py, over app/api.py).

BioManager counts and lists; the model writes. Like app/assistant_compute.py,
everything here only reads, and says what each number is based on and what
the records can't say:

* animal_use(): animals per protocol, species and strain over a period, for
  an IACUC or animal-care annual review: new (born, or added to the records),
  used in experiments, died or euthanized (by reason), and breeding.
* handover(): everything one person owns or looks after, grouped and
  linked, with what is due on it, for when they leave the lab.

The pure parts (finding a protocol column, a death's reason, a strain, the
tally) take plain values; tests/test_reports.py tests them, and the
endpoints and who may ask.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from flask import url_for
from sqlalchemy import or_, select

from . import custom_fields, lab, positions
from .assistant_compute import _base, _cage_url, _day, _mouse_url

# ================================================================ the pure parts

# A column a lab added to hold an animal protocol: its key or its name says so.
PROTOCOL_COLUMN = re.compile(r"protocol|iacuc|licen[cs]e|approval|伦理|批号|许可", re.I)


def protocol_columns(defined: list[dict]) -> list[dict]:
    """The lab's own columns (custom_fields) that hold a protocol."""
    return [f for f in defined
            if PROTOCOL_COLUMN.search(str(f.get("key") or "")) or PROTOCOL_COLUMN.search(str(f.get("label") or ""))]


def protocol_of(values: dict, columns: list[dict]) -> tuple[str, str]:
    """(protocol, the column's name) from a record's own columns; ("", "")
    when none of them says."""
    for f in columns:
        value = " ".join(str((values or {}).get(f["key"]) or "").split())
        if value:
            return value, f["label"]
    return "", ""


def same_protocol(a, b) -> bool:
    """One protocol however it was typed: case and spaces aside."""
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


# Why an animal's life ended, from the words of its notes or a sac log's
# reason, in this order (a humane endpoint reached in an experiment is a
# humane endpoint, not the end of the experiment).
REASONS = (
    ("Found dead", re.compile(r"found dead|died|dead in (the )?(cage|tank)|发现死亡|死亡", re.I)),
    ("Humane endpoint or ill health", re.compile(
        r"humane|moribund|weight loss|lost weight|sick|\bill\b|illness|tumou?r|injur|wound|fight|dermatitis|"
        r"malocclusion|prolapse|hunched|人道终点|体重下降|肿瘤|受伤", re.I)),
    ("End of experiment or tissue collection", re.compile(
        r"end of (the )?(experiment|study)|experimental endpoint|study endpoint|terminal|harvest|perfus|necrops|"
        r"tissue|dissect|实验结束|取材|灌注", re.I)),
    ("Retired breeder", re.compile(r"retired? breeder|breeder retired|retire|old breeder|淘汰种鼠", re.I)),
    ("Surplus or unwanted genotype", re.compile(
        r"surplus|excess|not needed|unneeded|wrong genotype|unwanted genotype|wild[- ]?type littermate|多余|"
        r"基因型不对", re.I)),
)
DIED = {"dead", "found dead", "died"}
EUTHANIZED = {"sac", "euthanized", "euthanised"}
NOT_RECORDED = ("Euthanized, reason not recorded", "Ended, reason not recorded")


def death_reason(status: str = "", note: str = "") -> tuple[str, str]:
    """(category, the words it was read from): from the notes first (where
    a reason is written), then the status. The words are "" when the
    records don't say why."""
    for category, pattern in REASONS:
        found = pattern.search(note or "")
        if found:
            return category, found.group(0)
    said = (status or "").strip().lower()
    if said in DIED:
        return "Found dead", status.strip()
    if said in EUTHANIZED:
        return NOT_RECORDED[0], ""
    return NOT_RECORDED[1], ""


def strain_of(genotype: str, strains: list[str]) -> str:
    """A mouse's strain: the lab's Strain whose name its genotype holds
    (the longest such name), else its genotype as written."""
    text = " ".join((genotype or "").split())
    low = text.lower()
    for name in sorted((s for s in strains if s and s.strip()), key=len, reverse=True):
        if re.search(r"(?<![\w/])" + re.escape(name.strip().lower()) + r"(?![\w/])", low):
            return name.strip()
    return text or "(not recorded)"


COLUMNS = ("born", "added", "new", "used_in_experiments", "died_or_euthanized", "breeding")


def counted(entry: dict, start: date, end: date) -> dict:
    """What one entry adds to each column in [start, end]. An entry is an
    animal (or a zebrafish row, or a sac log event): species, strain,
    protocol, count (1), born and added (the day its record was made), ended
    and reason, used and breeding (each True, or how many)."""
    def within(day) -> bool:
        return day is not None and start <= day <= end

    n = entry.get("count", 1) or 0
    born = entry.get("born")
    out = dict.fromkeys(COLUMNS, 0)
    if within(born):
        out["born"] = n
    elif within(entry.get("added")) and (born is None or born < start):
        out["added"] = n
    out["new"] = out["born"] + out["added"]
    used = entry.get("used")
    out["used_in_experiments"] = used if isinstance(used, int) and not isinstance(used, bool) else (n if used else 0)
    out["died_or_euthanized"] = n if within(entry.get("ended")) else 0
    breeding = entry.get("breeding")
    out["breeding"] = breeding if isinstance(breeding, int) and not isinstance(breeding, bool) else \
        (n if breeding else 0)
    return out


def tally(entries: list[dict], start: date, end: date, protocol: str = "") -> dict:
    """Each column per protocol, species and strain, per protocol, and in
    all; only what the period touches, and with `protocol` only that one."""
    rows: dict[tuple, dict] = {}
    for e in entries:
        if protocol and not same_protocol(e.get("protocol"), protocol):
            continue
        c = counted(e, start, end)
        if not any(c.values()):
            continue
        key = (e.get("protocol") or "", e["species"], e.get("strain") or "(not recorded)")
        row = rows.setdefault(key, {"protocol": key[0] or None, "species": key[1], "strain": key[2],
                                    **dict.fromkeys(COLUMNS, 0), "died_by_reason": {}})
        for col in COLUMNS:
            row[col] += c[col]
        if c["died_or_euthanized"]:
            reason = e.get("reason") or NOT_RECORDED[1]
            row["died_by_reason"][reason] = row["died_by_reason"].get(reason, 0) + c["died_or_euthanized"]
    ordered = sorted(rows.values(), key=lambda r: (r["protocol"] is None, (r["protocol"] or "").lower(),
                                                   r["species"], r["strain"].lower()))
    protocols: dict = {}
    for r in ordered:
        p = protocols.setdefault(r["protocol"], {"protocol": r["protocol"], **dict.fromkeys(COLUMNS, 0),
                                                 "by_species": {}})
        for col in COLUMNS:
            p[col] += r[col]
        species = p["by_species"].setdefault(r["species"], dict.fromkeys(COLUMNS, 0))
        for col in COLUMNS:
            species[col] += r[col]
    totals = {col: sum(r[col] for r in ordered) for col in COLUMNS}
    return {"rows": ordered, "protocols": list(protocols.values()), "totals": totals}


def year_before(end: date) -> date:
    """The first day of the twelve months that end on `end`."""
    try:
        back = end.replace(year=end.year - 1)
    except ValueError:            # 29 February
        back = end.replace(year=end.year - 1, day=28)
    return back + timedelta(days=1)


# ================================================================ against the lab's records

MOUSE = "Mus musculus (mouse)"
FISH = "Danio rerio (zebrafish)"
STARTED = ("active", "paused", "done", "cancelled")   # every status but planned: animals were used
FISH_BREEDING = ("breeder", "mating")


def _day_of(stamp) -> date | None:
    """The lab's own day of a stamp the database keeps in UTC (created_at,
    recorded_at), as web.local_time reads them."""
    return stamp.replace(tzinfo=timezone.utc).astimezone().date() if stamp else None


def _from(day: date) -> datetime:
    """The UTC stamp at which the lab's `day` begins, to compare those stamps with."""
    return datetime.combine(day, datetime.min.time()).astimezone(timezone.utc).replace(tzinfo=None)


def _ran_during(session, start: date, end: date):
    """The experiments with animals used in [start, end]: started on or
    before its end and not ended before its start."""
    from .models import Experiment
    return select(Experiment.id).where(Experiment.status.in_(STARTED), Experiment.start_date.is_not(None),
                                       Experiment.start_date <= end,
                                       or_(Experiment.end_date.is_(None), Experiment.end_date >= start))


def _mouse_entries(session, start: date, end: date) -> tuple[list[dict], dict]:
    from sqlalchemy.orm import selectinload
    from .lookup import _first_number
    from .models import ExperimentMouse, LitterRecord, MouseRecord, StrainRecord
    from .services import holds_breeders, normalize_status, transgene_parts
    columns = protocol_columns(custom_fields.fields(session, "colony"))
    strains = [n for (n,) in session.execute(select(StrainRecord.strain_name))]
    used = {mid for (mid,) in session.execute(select(ExperimentMouse.mouse_id_fk).where(
        ExperimentMouse.experiment_id_fk.in_(_ran_during(session, start, end))))}
    litters = session.scalars(select(LitterRecord).options(selectinload(LitterRecord.mice)).where(
        LitterRecord.date_of_birth >= start, LitterRecord.date_of_birth <= end)).all()
    parents = {n for lt in litters for n in (_first_number(lt.father_info), _first_number(lt.mother_info)) if n}
    # Each mouse's own protocol and strain by its number, for the pups that
    # have none of their own: a pup is under its parents' protocol.
    own: dict[int, tuple[str, str, str]] = {}
    for m in session.scalars(select(MouseRecord)):
        p, column = protocol_of(custom_fields.values(m), columns)
        own[m.mouse_id] = (p, column, strain_of(m.genotype or "; ".join(transgene_parts(m)), strains))

    def from_parents(litter) -> tuple[str, str]:
        for who, info in (("mother", litter.mother_info), ("father", litter.father_info)):
            p, column, _strain = own.get(_first_number(info), ("", "", ""))
            if p:
                return p, f"its {who}'s “{column}”"
        return "", ""
    entries, facts = [], {"no_birth_date": 0, "transferred": 0, "columns": [f["label"] for f in columns],
                          "from_parents": 0}
    stmt = select(MouseRecord).options(selectinload(MouseRecord.cage), selectinload(MouseRecord.litter)).where(
        or_(MouseRecord.date_of_death.is_(None), MouseRecord.date_of_death >= start),
        MouseRecord.created_at < _from(end + timedelta(days=1)))
    for m in session.scalars(stmt):
        born = m.litter.date_of_birth if m.litter else None
        if born is not None and born > end:
            continue
        ended = m.date_of_death
        alive_then = ended is None or ended >= start
        protocol, column = protocol_of(custom_fields.values(m), columns)
        if not protocol and m.litter is not None:
            protocol, column = from_parents(m.litter)
            facts["from_parents"] += bool(protocol)
        reason = death_reason(m.status, m.note)[0] if ended is not None else ""
        entry = {"species": MOUSE, "strain": strain_of(m.genotype or "; ".join(transgene_parts(m)), strains),
                 "protocol": protocol, "protocol_from": column, "born": born,
                 "added": _day_of(m.created_at), "ended": ended, "reason": reason,
                 "used": m.id in used,
                 "breeding": m.mouse_id in parents or (alive_then and m.cage is not None
                                                       and holds_breeders(m.cage.purpose))}
        if any(counted(entry, start, end).values()):
            facts["no_birth_date"] += born is None
            facts["transferred"] += normalize_status(m.status or "") == "transfer"
        entries.append(entry)
    # Pups a litter counted that have no record of their own: born, under
    # their parents' protocol and strain, though what became of them isn't kept.
    unrecorded = 0
    for lt in litters:
        n = (lt.total_pups or 0) - len(lt.mice)
        if n <= 0:
            continue
        protocol, column = from_parents(lt)
        mother = own.get(_first_number(lt.mother_info)) or own.get(_first_number(lt.father_info))
        entries.append({"species": MOUSE, "strain": mother[2] if mother else "(not recorded)", "protocol": protocol,
                        "protocol_from": column, "count": n, "born": lt.date_of_birth, "unrecorded_pups": True})
        unrecorded += n
    facts["litters"] = {"born": len(litters), "pups_born": sum(lt.total_pups or 0 for lt in litters),
                        "pups_with_records": sum(len(lt.mice) for lt in litters), "pups_without_records": unrecorded,
                        "without_a_pup_count": sum(1 for lt in litters if not lt.total_pups)}
    return entries, facts


def _fish_entries(session, start: date, end: date) -> tuple[list[dict], dict]:
    from sqlalchemy.orm import selectinload
    from .zebrafish_routes import FISH_STATUS_SAC_REASON
    from .models import ExperimentSubject, FishLine, FishRecord, FishSacLog, TankRecord
    columns = protocol_columns(custom_fields.fields(session, "zebrafish"))
    lines = {ln.id: ln for ln in session.scalars(select(FishLine))}
    tanks = {t.id: t for t in session.scalars(select(TankRecord))}

    def protocol(tank, line) -> tuple[str, str]:
        found = protocol_of(custom_fields.values(tank), columns) if tank is not None else ("", "")
        if found[0] or line is None:
            return found
        said = " ".join((line.iacuc_protocol or "").split())
        return (said, "IACUC #") if said else ("", "")

    used = {sid: n for sid, n in session.execute(
        select(ExperimentSubject.subject_id, ExperimentSubject.start_count).where(
            ExperimentSubject.subject_kind == "fish",
            ExperimentSubject.experiment_id_fk.in_(_ran_during(session, start, end))))}
    # Fish sac'd from a row by hand were taken off its count (the status
    # turning sac leaves the count as it was): back on, for the row as it began.
    taken: dict[int, int] = {}
    for fid, n, said in session.execute(select(FishSacLog.fish_id_fk, FishSacLog.count, FishSacLog.reason).where(
            FishSacLog.fish_id_fk.is_not(None))):
        if not (said or "").startswith(FISH_STATUS_SAC_REASON):
            taken[fid] = taken.get(fid, 0) + (n or 0)
    entries = []
    for f in session.scalars(select(FishRecord).options(selectinload(FishRecord.tank)).where(
            FishRecord.created_at < _from(end + timedelta(days=1)))):
        tank = f.tank
        line = lines.get(f.line_id_fk) or (lines.get(tank.line_id_fk) if tank is not None else None)
        p, column = protocol(tank, line)
        alive = (f.status or "alive") == "alive"
        entries.append({"species": FISH, "strain": line.name if line else (f.genotype or "(no line)"),
                        "protocol": p, "protocol_from": column, "count": (f.count or 0) + taken.get(f.id, 0),
                        "born": f.date_of_fertilization, "added": _day_of(f.created_at),
                        "used": (used[f.id] if used[f.id] is not None else (f.count or 0)) if f.id in used else 0,
                        "breeding": (f.count or 0) if alive and tank is not None and bool(tank.active)
                                    and (tank.purpose or "").lower() in FISH_BREEDING else 0})
    sac = session.scalars(select(FishSacLog).where(
        FishSacLog.recorded_at >= _from(start),
        FishSacLog.recorded_at < _from(end + timedelta(days=1)))).all()
    for e in sac:
        tank, line = tanks.get(e.tank_id_fk), lines.get(e.line_id_fk)
        p, column = protocol(tank, line)
        said = e.reason or ""
        status = said[len(FISH_STATUS_SAC_REASON):] if said.startswith(FISH_STATUS_SAC_REASON) else "sac"
        reason = death_reason(status, "" if said.startswith(FISH_STATUS_SAC_REASON) else said)[0]
        entries.append({"species": FISH, "strain": line.name if line else "(no line)", "protocol": p,
                        "protocol_from": column, "count": e.count or 0, "ended": _day_of(e.recorded_at),
                        "reason": reason})
    return entries, {"columns": [f["label"] for f in columns]}


def _organism_entries(session, start: date, end: date) -> tuple[list[dict], list[str]]:
    """The lab's own organisms (each database it may see): an animal or a
    group at its count, its protocol its own, else its housing's, else its
    line's."""
    from . import organism_service
    from .models import ExperimentSubject, OrgHousing, OrgLine, Organism
    used = {sid: n for sid, n in session.execute(
        select(ExperimentSubject.subject_id, ExperimentSubject.start_count).where(
            ExperimentSubject.subject_kind == "organism",
            ExperimentSubject.experiment_id_fk.in_(_ran_during(session, start, end))))}
    entries, names = [], []
    for module in organism_service.list_modules(session):
        if not lab.can_see(module):
            continue
        mv = organism_service.view(module)
        species = module.label
        names.append(species)
        lines = {ln.id: ln for ln in session.scalars(select(OrgLine).where(OrgLine.module_id_fk == module.id))}
        housing = {h.id: h for h in session.scalars(select(OrgHousing).where(OrgHousing.module_id_fk == module.id))}
        for o in session.scalars(select(Organism).where(Organism.module_id_fk == module.id,
                                                        Organism.created_at < _from(end + timedelta(days=1)))):
            line, home = lines.get(o.line_id_fk), housing.get(o.housing_id_fk)
            mine, theirs = " ".join((o.protocol or "").split()), " ".join(((home.protocol if home else "") or "").split())
            from_line = " ".join(((line.protocol if line else "") or "").split())
            protocol, column = ((mine, "Protocol") if mine else (theirs, f"its {mv.housing_noun}'s Protocol") if theirs
                                else (from_line, f"its {mv.line_noun}'s Protocol") if from_line else ("", ""))
            n = o.count or 1
            alive = mv.is_alive(o)
            entries.append({"species": species, "strain": (line.code if line else "") or o.genotype or "(no line)",
                            "protocol": protocol, "protocol_from": column, "count": n, "born": o.birth_on,
                            "added": _day_of(o.created_at), "ended": o.death_on,
                            "reason": death_reason(o.status, o.notes)[0] if o.death_on else "",
                            "used": (used[o.id] if used[o.id] is not None else n) if o.id in used else 0,
                            "breeding": n if alive and home is not None and home.active
                            and re.search(r"breed|mating|cross", home.purpose or "", re.I) else 0})
    return entries, names


def animal_use(session, start=None, end=None, protocol: str = "", today: date | None = None) -> dict:
    """Animals per protocol, species and strain over a period (the twelve
    months to today unless asked), for an annual review."""
    today = today or date.today()
    end = _day(end, today)
    start = _day(start, None) or year_before(end)
    if start > end:
        raise ValueError("start is after end.")
    protocol = " ".join((protocol or "").split())
    features = lab.request_features()
    entries: list[dict] = []
    notes, flags = [], []
    mice = fish = None
    if features.get("colony", True):
        found, mice = _mouse_entries(session, start, end)
        entries += found
    else:
        notes.append("The mouse colony is switched off for this lab: no mice are counted.")
    if features.get("zebrafish", True):
        found, fish = _fish_entries(session, start, end)
        entries += found
    else:
        notes.append("Zebrafish is switched off for this lab: no fish are counted.")
    others, other_names = _organism_entries(session, start, end)
    entries += others
    out = tally(entries, start, end, protocol)

    # What the records can't say, over every protocol (some of these may be this one's).
    def unassigned(species: str) -> int:
        return sum(max(counted(e, start, end).values()) for e in entries
                   if e["species"] == species and not e.get("protocol"))
    for species, noun in ((MOUSE, "mice"), (FISH, "zebrafish"), *((name, name) for name in other_names)):
        n = unassigned(species)
        if n:
            flags.append({"what": "no_protocol", "species": species, "count": n,
                          "says": f"No protocol is recorded for {n} {noun} counted in this period"
                                  + (": they may belong to this protocol." if protocol else ".")})
    not_said = sum(n for r in tally(entries, start, end)["rows"] for reason, n in r["died_by_reason"].items()
                   if reason in NOT_RECORDED)
    if not_said:
        flags.append({"what": "no_reason", "count": not_said,
                      "says": f"{not_said} animals died or were euthanized with no reason in their records."})
    if mice:
        if mice["no_birth_date"]:
            flags.append({"what": "no_birth_date", "species": MOUSE, "count": mice["no_birth_date"],
                          "says": f"{mice['no_birth_date']} mice have no date of birth (their litter has none): "
                                  "they count as new only by when their record was made."})
        if mice["transferred"]:
            flags.append({"what": "transferred", "species": MOUSE, "count": mice["transferred"],
                          "says": f"{mice['transferred']} mice are marked transfer (moved out); the records don't "
                                  "say when, so they aren't counted as leaving."})
        lt = mice["litters"]
        if lt["born"]:
            notes.append(f"{lt['born']} litters were born in the period, {lt['pups_born']} pups by their litters' "
                         f"counts; {lt['pups_with_records']} of the pups have a record of their own"
                         + (f", and the other {lt['pups_without_records']} are counted as born under their "
                            "parents' protocol and strain (what became of them isn't recorded)"
                            if lt["pups_without_records"] else "")
                         + (f"; {lt['without_a_pup_count']} litters have no pup count"
                            if lt["without_a_pup_count"] else "") + ".")
        out["litters"] = lt
        if mice["from_parents"]:
            notes.append(f"{mice['from_parents']} mice with no protocol of their own are counted under their "
                         "mother's (else father's), as the litter names them.")
    mouse_columns = (mice or {}).get("columns") or []
    fish_columns = (fish or {}).get("columns") or []
    basis = {
        "protocol": ("A mouse's protocol is in the colony's own column " + ", ".join(f"“{c}”" for c in mouse_columns)
                     if mouse_columns else
                     "The colony has no protocol column, so no mouse has a protocol: add one (Configure → Your own "
                     "columns, e.g. “IACUC #”) and fill it in")
                    + ", else (a pup) its mother's, else its father's, by the litter's parents; a litter's pups with "
                    "no record of their own are under its parents' too. A zebrafish row's is "
                    + (f"its tank's own column ({', '.join(fish_columns)}), else " if fish_columns else "")
                    + "its line's IACUC #, else its tank's line's. Another organism's is its own Protocol, else its "
                    "housing's, else its line's.",
        "strain": "A mouse's strain is the lab's Strain whose name its genotype holds, else its genotype as written; "
                  "a zebrafish row's is its line; another organism's its line's code, else its genotype.",
        "born": "Mice whose litter's date of birth is in the period; zebrafish rows whose date of fertilisation is, "
                "each at its count now plus the fish sac'd from it since.",
        "added": "Animals whose record was made in the period though born before it (or with no birth date): "
                 "bought, imported or transferred in, or entered late.",
        "new": "born + added.",
        "used_in_experiments": "Animals in an experiment that ran during the period (any status but planned: started "
                               "on or before its end and not ended before its start), whether or not left out of the "
                               "analysis; a zebrafish row at its count at the start of the experiment.",
        "died_or_euthanized": "Mice whose date of death is in the period; zebrafish on the sac log in the period. The "
                              "reason is read from the words in the mouse's notes or the sac log's reason, else its "
                              "status: check the categories against the records.",
        "breeding": "Mice named by number as a parent of a litter born in the period, or now in a Breeder or Breeding "
                    "cage and alive during it; zebrafish alive now in a breeder or mating tank. Where a mouse was "
                    "before today isn't kept, so one moved out of a breeding cage counts only as a parent.",
        "columns": "An animal may be in several columns (born, used and euthanized); don't add them up.",
    }
    return {"period": {"start": start.isoformat(), "end": end.isoformat()}, "protocol": protocol or None,
            **out, "basis": basis, "flags": flags, "notes": notes,
            "note": "Only what the records say: a draft for the person to check against their institution's form. "
                    "Never fill in or estimate a number that isn't here."}


# ---------------------------------------------------------------- handover

AGENDA_DAYS = 30
MAX_PAGES = 200


def owner_actions() -> dict[str, str]:
    """For each kind of record, the action that sets its owner (Set field →
    Owner on its sheet), from the action catalogue."""
    from .actions import CATALOGUE
    out = {}
    for act in CATALOGUE.values():
        if act.target and "|" not in act.target and act.name.endswith("_change") \
                and any(f.name == "owner" for f in act.fields):
            out.setdefault(act.target, act.name)
    return out


def _place(rack, row, col) -> str:
    return positions.label(row, col, rack.naming, rack.cols) if rack is not None and row and col else ""


def handover(session, username: str = "", days: int = AGENDA_DAYS, today: date | None = None) -> dict:
    """Everything one person owns or looks after, grouped and linked, and
    what is due on it in the next `days` days. A member asks about
    themselves; an admin about anyone."""
    from . import access
    from .models import UserAccount
    me = access.username()
    who = (username or me or "").strip()
    if who != me and not access.is_admin():
        raise PermissionError("A member may ask for their own handover only; an admin may ask about anyone.")
    today = today or date.today()
    features = lab.request_features()
    account = session.scalar(select(UserAccount).where(UserAccount.username == who))
    out: dict = {"person": {"username": who, "name": (account.display_name or "") if account else "",
                            "role": account.role if account else None,
                            "disabled": bool(account.disabled) if account else None, "has_account": account is not None},
                 "asked_by": me, "today": today.isoformat()}
    notes = []
    if account is None:
        notes.append(f"No account is called {who}: these are the records in that name.")
    from . import lookup
    due = [i for i in lookup.due(session, days) if i.get("owner") == who]
    due_by_ref: dict[tuple, list] = {}
    for item in due:
        ref = item.get("ref") or {}
        if ref.get("kind"):
            due_by_ref.setdefault((ref["kind"], ref.get("id")), []).append(
                {k: item[k] for k in ("due", "status", "title", "detail", "litter") if k in item})

    if features.get("colony", True):
        out.update(_handover_colony(session, who, due_by_ref))
    if features.get("zebrafish", True):
        out["tanks"] = _handover_tanks(session, who, due_by_ref)
    out["vials"] = _handover_vials(session, who)
    out["inventories"], glycerol = _handover_inventory(session, who)
    if features.get("plasmids", True):
        out["plasmids"] = _handover_plasmids(session, who)
    out["organisms"] = _handover_organisms(session, who)
    out["experiments"] = _handover_experiments(session, who, today)
    out["racks_and_boxes"] = _handover_racks(session, who)
    if features.get("notebook", True):
        out["notebook"] = _handover_notebook(session, who, me)
        if who != me:
            notes.append("Notebook pages are private: only the pages shared with you are named.")
    out["proposals"] = _handover_proposals(session, who, me)
    if who != me:
        notes.append("Proposals are their own: only how many are waiting.")
    out["due"] = due
    out["due_days"] = days
    out["summary"] = {
        "mice": sum(len(c["mice"]) for c in out.get("cages", [])) + len(out.get("mice_without_cage", [])),
        "cages": sum(1 for c in out.get("cages", []) if c["yours"]),
        "breeding_cages": len(out.get("breeding_cages", [])),
        "tanks": len(out.get("tanks", [])), "fish": sum(t["fish"] for t in out.get("tanks", [])),
        "vials": sum(len(v["vials"]) for v in out["vials"]),
        "inventory_items": sum(len(i["items"]) for i in out["inventories"]), "glycerol_stocks": glycerol,
        "plasmids": len(out.get("plasmids", [])),
        "organisms": sum(o["animals"] for o in out["organisms"]),
        "experiments": len(out["experiments"]), "racks_and_boxes": len(out["racks_and_boxes"]),
        "unsigned_pages": (out.get("notebook") or {}).get("unsigned", 0),
        "proposals_waiting": out["proposals"]["waiting"], "due": len(due),
        "overdue": sum(1 for i in due if i.get("status") == "overdue")}
    out["owner_changes"] = owner_actions()
    notes.append("Only records in their name are here: unowned and shared records with another owner aren't. "
                 "Records in a database switched off, or one you can't open, aren't listed.")
    out["notes"] = notes
    out["next"] = ("To hand records on, each can go to someone else with owner_changes' action for its kind "
                   "(Set field → Owner on its sheet): propose those only when the person asks, with the new owner "
                   "they name. Racks and boxes are handed on by an admin in Settings → Statistics.")
    return out


def _handover_colony(session, who: str, due_by_ref: dict) -> dict:
    from sqlalchemy.orm import selectinload
    from .models import CageRecord, MouseRecord
    from .services import is_breeding_cage, mouse_is_active
    from .litter_dates import cage_litter_json, open_litters
    theirs_living = select(MouseRecord.cage_id_fk).where(MouseRecord.owner == who, MouseRecord.date_of_death.is_(None))
    cages, breeding = [], []
    for c in session.scalars(select(CageRecord).options(selectinload(CageRecord.mice), selectinload(CageRecord.rack))
                             .where(or_(CageRecord.owner == who, CageRecord.id.in_(theirs_living)))):
        living = [m for m in c.mice if mouse_is_active(m)]
        mine = sorted(m.mouse_id for m in living if m.owner == who)
        yours = c.owner == who
        if not mine and not (yours and (living or c.rack_id_fk or (c.purpose or "").strip())):
            continue
        ref = {"kind": "cage", "id": c.id}
        entry = {"cage": c.cage_id, "ref": ref, "yours": yours, "owner": c.owner or "", "purpose": c.purpose or "",
                 "rack": c.rack.name if c.rack else None, "position": _place(c.rack, c.rack_row, c.rack_col) or None,
                 "room": (c.rack.room if c.rack and c.rack.room else c.room) or "",
                 "where": c.location_detail or c.cage_location or "", "shared": bool(c.is_shared),
                 "mice": mine, "others_mice": len(living) - len(mine), "empty": not living,
                 "due": due_by_ref.get(("cage", c.id), []), "url": _cage_url(c),
                 "_order": (c.rack is None, positions.place_order(c.rack.name if c.rack else ""), c.rack_row or 0,
                            c.rack_col or 0, positions.natural(c.cage_id))}
        cages.append(entry)
        if is_breeding_cage(c.purpose) and yours:
            breeding.append({"cage": c.cage_id, "ref": ref, "rack": entry["rack"], "position": entry["position"],
                             "mice": sorted(m.mouse_id for m in living),
                             "litter_born": c.date_give_birth.isoformat() if c.date_give_birth else None,
                             # Each litter not yet weaned (a trio's two females give two).
                             "litters": [cage_litter_json(cl) for cl in open_litters(c)],
                             "due": entry["due"], "url": entry["url"]})
    cages.sort(key=lambda e: e.pop("_order"))
    breeding.sort(key=lambda b: next(i for i, c in enumerate(cages) if c["ref"] == b["ref"]))
    loose = session.scalars(select(MouseRecord).where(MouseRecord.owner == who, MouseRecord.date_of_death.is_(None),
                                                      MouseRecord.cage_id_fk.is_(None))
                            .order_by(MouseRecord.mouse_id)).all()
    return {"cages": cages, "breeding_cages": breeding,
            "mice_without_cage": [{"mouse": m.mouse_id, "ref": {"kind": "mouse", "id": m.id}, "url": _mouse_url(m)}
                                  for m in loose if mouse_is_active(m)]}


def _handover_tanks(session, who: str, due_by_ref: dict) -> list[dict]:
    from sqlalchemy.orm import selectinload
    from .zebrafish_routes import fish_position_label
    from .models import TankRecord
    out = []
    for t in session.scalars(select(TankRecord).options(selectinload(TankRecord.fish), selectinload(TankRecord.line),
                                                        selectinload(TankRecord.rack))
                             .where(TankRecord.owner == who, TankRecord.active.is_(True))):
        out.append({"tank": t.tank_id, "ref": {"kind": "tank", "id": t.id}, "line": t.line.name if t.line else None,
                    "purpose": t.purpose or "", "rack": t.rack.name if t.rack else None,
                    "position": fish_position_label(t) or None,
                    "fish": sum(f.count or 0 for f in t.fish if (f.status or "alive") == "alive"),
                    "due": due_by_ref.get(("tank", t.id), []),
                    "url": _base() + url_for("zebrafish", view="tanks") + f"#tank-{t.id}"})
    out.sort(key=lambda t: (t["rack"] is None, positions.place_order(t["rack"] or ""), positions.natural(t["tank"])))
    return out


def _handover_vials(session, who: str) -> list[dict]:
    from sqlalchemy.orm import selectinload
    from . import stock_service as ssvc
    from .models import StockFrozen, StockUnit
    out = []
    for module in ssvc.list_modules(session, everyone=True):
        if not lab.can_see(module):
            continue
        mv = ssvc.view(module)
        vials = [{"code": mv.code(u), "ref": {"kind": "stock_unit", "id": u.id}, "genotype": u.genotype or "",
                  "purpose": u.purpose or "", "rack": u.rack.name if u.rack else None,
                  "position": _place(u.rack, u.rack_row, u.rack_col) or None}
                 for u in session.scalars(select(StockUnit).options(selectinload(StockUnit.rack)).where(
                     StockUnit.module_id_fk == module.id, StockUnit.owner == who, StockUnit.active.is_(True))
                     .order_by(StockUnit.number))]
        frozen = [{"ref": {"kind": "stock_frozen", "id": f.id}, "genotype": f.genotype or "",
                   "vials_left": f.vials_left, "location": f.location or "",
                   "frozen_on": f.frozen_on.isoformat() if f.frozen_on else None}
                  for f in session.scalars(select(StockFrozen).where(
                      StockFrozen.module_id_fk == module.id, StockFrozen.owner == who, StockFrozen.vials_left > 0))]
        if vials or frozen:
            out.append({"database": module.label, "key": module.key, "noun": mv.units, "vials": vials,
                        "frozen": frozen, "url": _base() + url_for("stocks.module", key=module.key)})
    return out


def _handover_inventory(session, who: str) -> tuple[list[dict], int]:
    from sqlalchemy.orm import selectinload
    from . import inventory_service as isvc
    from .models import InventoryItem
    out, glycerol = [], 0
    for module in isvc.list_modules(session, everyone=True):
        if not lab.can_see(module):
            continue
        items = []
        for i in session.scalars(select(InventoryItem).options(selectinload(InventoryItem.rack)).where(
                InventoryItem.module_id_fk == module.id, InventoryItem.owner == who).order_by(InventoryItem.number)):
            if (i.status or "").strip().lower() in isvc.TERMINAL_STATUSES:
                continue
            items.append({"number": i.number, "ref": {"kind": "inventory_item", "id": i.id}, "name": i.name or "",
                          "status": i.status or "", "quantity": i.quantity or "", "unit": i.unit or "",
                          "where": isvc.where_label(i) or i.location_note or "",
                          "shared": bool(i.is_shared),
                          "expires_on": i.expires_on.isoformat() if i.expires_on else None,
                          "url": _base() + url_for("inventory.module", key=module.key, open=i.id)})
        if items:
            if module.kind == "glycerol_stocks":
                glycerol += len(items)
            out.append({"database": module.label, "key": module.key, "kind": module.kind, "items": items})
    return out, glycerol


def _handover_plasmids(session, who: str) -> list[dict]:
    from . import plasmid_service
    from .models import PlasmidBox, PlasmidRecord
    boxes = {b.id: b for b in session.scalars(select(PlasmidBox))}
    return [{"plasmid": p.plasmid_id, "ref": {"kind": "plasmid", "id": p.id}, "name": p.name or "",
             "box": p.storage_box or None, "position": plasmid_service.label(p, boxes.get(p.box_id_fk)) or None,
             "location": p.location or "", "shared": bool(p.is_shared),
             "url": _base() + url_for("plasmid_page", number=p.plasmid_id)}
            for p in session.scalars(select(PlasmidRecord).where(PlasmidRecord.owner == who)
                                     .order_by(PlasmidRecord.plasmid_id))]


def _handover_organisms(session, who: str) -> list[dict]:
    from sqlalchemy import func
    from . import organism_service
    from .models import Organism, OrgHousing, OrgLocation
    out = []
    for module in organism_service.list_modules(session, everyone=True):
        if not lab.can_see(module):
            continue
        places = {loc.id: loc.name for loc in session.scalars(select(OrgLocation).where(
            OrgLocation.module_id_fk == module.id))}
        housing = [{"code": h.code, "ref": {"kind": "org_housing", "id": h.id}, "purpose": h.purpose or "",
                    "where": places.get(h.location_id_fk, "")}
                   for h in positions.natural_sorted(session.scalars(select(OrgHousing).where(
                       OrgHousing.module_id_fk == module.id, OrgHousing.owner == who, OrgHousing.active.is_(True))),
                       key=lambda h: h.code)]
        animals = session.scalar(select(func.coalesce(func.sum(Organism.count), 0)).where(
            Organism.module_id_fk == module.id, Organism.owner == who, Organism.death_on.is_(None))) or 0
        if housing or animals:
            out.append({"database": module.label, "key": module.key, "housing": housing, "animals": int(animals),
                        "url": _base() + url_for("organisms.module", key=module.key)})
    return out


def _handover_experiments(session, who: str, today: date) -> list[dict]:
    from . import experiments as xp
    from .experiment_steps import day_date, reagent_snapshot, safe_days
    from .models import Experiment, ExperimentSubject
    out = []
    for exp in session.scalars(select(Experiment).where(Experiment.owner_username == who,
                                                        Experiment.status.in_(("planned", "active", "paused")))
                               .order_by(Experiment.start_date, Experiment.id)):
        place = xp.place_for(session, exp.db or "colony")
        if place is None:
            continue
        animals = len(exp.memberships) if place.subject_kind == "mouse" else len(session.scalars(
            select(ExperimentSubject.id).where(ExperimentSubject.experiment_id_fk == exp.id)).all())
        steps = []
        if exp.status != "planned" and exp.start_date:
            for step in exp.steps:
                done = {r.day for r in step.records}
                # The item it uses, with its lot (experiment_steps.reagent_used).
                item = reagent_snapshot(session, step.reagent_item_id_fk) if step.reagent_item_id_fk else {}
                reagent = " ".join(filter(None, [item.get("name"), f"lot {item['lot']}" if item.get("lot") else ""]))
                for day in safe_days(step):
                    on = day_date(exp.start_date, day)
                    if day in done or on is None or on < today - timedelta(days=7):
                        continue
                    steps.append({"day": day, "date": on.isoformat(), "overdue": on < today,
                                  "what": " ".join(filter(None, [step.agent or step.kind, step.dose, step.route])),
                                  "group": step.treatment_group or "", **({"reagent": reagent} if reagent else {})})
        steps.sort(key=lambda s: (s["date"], s["day"]))
        out.append({"experiment": exp.name, "ref": {"kind": "experiment", "id": exp.id}, "status": exp.status,
                    "database": place.label, "start_date": exp.start_date.isoformat() if exp.start_date else None,
                    "end_date": exp.end_date.isoformat() if exp.end_date else None, "animals": animals,
                    "next_steps": steps[:5], "steps_left": len(steps),
                    **({"note": "Planned: nothing is due until someone presses Start."}
                       if exp.status == "planned" else {}),
                    "url": _base() + xp.page_url(exp)})
    return out


def _handover_racks(session, who: str) -> list[dict]:
    from .admin_racks import CONTAINERS
    out = []
    for kind in CONTAINERS:
        for row in session.scalars(select(kind.model).where(kind.model.created_by == who)):
            out.append({"kind": kind.label, "name": row.name, "database": kind.database(session, row),
                        "where": kind.where(session, row), "holds": f"{kind.count(session, row)} {kind.count_noun}"})
    return out


def _handover_notebook(session, who: str, me: str) -> dict:
    from sqlalchemy import func
    from .lab_notebook import role_for
    from .models import NotebookPage, NotebookTab, RecordSignature
    signed = select(RecordSignature.page_id_fk).where(RecordSignature.action == "sign")
    pages = session.scalars(select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id).where(
        NotebookTab.owner_username == who, func.length(func.coalesce(NotebookPage.body, "")) > 0,
        NotebookPage.id.not_in(signed)).order_by(NotebookPage.updated_at.desc())).all()
    readable = [p for p in pages if who == me or role_for(session, p) is not None]
    signs = session.scalar(select(func.count(RecordSignature.id)).where(
        RecordSignature.username == who, RecordSignature.action == "sign")) or 0
    return {"unsigned": len(pages), "signs_pages": bool(signs),
            "pages": [{"page": p.title or "", "ref": {"kind": "notebook_page", "id": p.id},
                       "changed": p.updated_at.date().isoformat() if p.updated_at else None,
                       "url": _base() + url_for("notebook", page=p.id)} for p in readable[:MAX_PAGES]],
            "not_named": len(pages) - min(len(readable), MAX_PAGES),
            "note": ("Signing is each person's choice; they have signed pages before, so these may want signing "
                     "before they go." if signs else "They have never signed a page: signing may not be how "
                                                      "they work.")}


def _handover_proposals(session, who: str, me: str) -> dict:
    from .models import Proposal
    waiting = session.scalars(select(Proposal).where(
        Proposal.owner_username == who, Proposal.status == "pending",
        or_(Proposal.expires_at.is_(None), Proposal.expires_at > datetime.utcnow())).order_by(Proposal.id)).all()
    out = {"waiting": len(waiting)}
    if who == me:
        out["items"] = [{"id": p.id, "summary": p.summary or "", "source": p.source or "",
                         "made": p.created_at.isoformat(timespec="seconds") + "Z",
                         "url": _base() + url_for("proposals.page") + f"#proposal-{p.id}"} for p in waiting]
    return out
