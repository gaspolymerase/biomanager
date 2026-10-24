"""Animals that may need a look: what the records show, never a diagnosis.

Four checks, each deterministic and read from the records alone:

* weight_loss: a living mouse whose latest weight is at least the lab's
  threshold (Settings → General → Weight loss to flag; 15% unless set)
  below its own peak over the last WINDOW_DAYS days (two weights or more
  there), or below its first weight in an experiment that has started.
* status_experiment: a living mouse whose status is "experiment" while it
  is in no planned, active or paused experiment (checked once the lab keeps
  mouse experiments here at all), and a living mouse in an active
  experiment whose status says something else (breeder, geno, transfer).
* welfare_notes: a mouse, tank or fish row whose notes changed in the last
  NOTES_DAYS days (the change history says when; a weight's own note by its
  day) and use a word from WORDS, in English or Chinese, unless "no" or
  "not" (无, 没有, 未见…) comes just before it. Notes that may need a look,
  never a finding.
* not_weighed: an animal in an active experiment measuring body weight not
  weighed for more than N days: the longest gap between the weighing days
  its regimen plans, or DEFAULT_WEIGH_EVERY when it plans none (or after
  the last of them). A mouse's weights are its own (MouseWeight); any other
  animal's are the experiment's readings. Animals left out of the analysis
  are not counted.

`checks()` gives them all, each {"id", "title", "severity", "items", "fix"}
with an item's `ref`, its numbers and `url` (a path; lab_checks makes it
whole) and, where the action catalogue has one, `propose`. Home's card
(`home_dashboard`), the morning notification (`notify.daily_welfare_reminder`)
and the assistants' lab_checks (app/assistant_compute.py) all read it.

Days: weigh_date is the lab's own day; created_at and the change history's
changed_at are UTC, compared through `day_start()` (the UTC moment the lab's
day starts) and read back with `day_of()`.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from flask import url_for
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

WINDOW_DAYS = 30            # a mouse's peak is its heaviest weight in these days
NOTES_DAYS = 14             # notes changed this recently are read
DEFAULT_WEIGH_EVERY = 7     # days between weighings when the regimen doesn't say
DEFAULT_LOSS_PCT = 15
LOSS_SETTING = "welfare_weight_loss_pct"
LOSS_RANGE = (1, 50)
BUSY = ("planned", "active", "paused")       # an experiment whose animals are taken
STARTED = ("active", "paused")               # one whose day 1 has come

# The words read in notes: English (whole words, any case) and Chinese.
WORDS_EN = ("hunched", "hunching", "lethargic", "lethargy", "moribund", "not eating", "off food",
            "dehydrated", "dehydration", "wound", "wounds", "wounded", "ulcer", "ulcers", "ulcerated",
            "ulceration", "weight loss", "losing weight", "thin", "emaciated", "labored breathing",
            "laboured breathing", "piloerection", "hypothermic", "prolapse")
WORDS_ZH = ("弓背", "消瘦", "精神差", "精神萎靡", "萎靡", "不进食", "拒食", "脱水", "伤口", "溃疡",
            "呼吸困难", "体重下降", "体重减轻", "濒死", "竖毛", "低体温", "脱垂")
WORDS = WORDS_EN + WORDS_ZH
_WORD_RE = re.compile(r"\b(?:" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in
                                          sorted(WORDS_EN, key=len, reverse=True)) + r")\b"
                      "|" + "|".join(re.escape(w) for w in sorted(WORDS_ZH, key=len, reverse=True)),
                      re.IGNORECASE)
# "no wound", "not hunched", "无溃疡", "未见伤口": said not to be there.
_NEGATED_EN = re.compile(r"\b(?:no|not|without|non)[\s-]+$", re.IGNORECASE)
_NEGATED_ZH = re.compile(r"(?:无|没有|未见|未)$")
MAX_LINE = 200


# ---------------------------------------------------------------- the lab's threshold

def loss_pct(session) -> int:
    """The lab's weight loss to flag, in % (Settings → General)."""
    from .inventory_service import get_setting
    raw = (get_setting(session, LOSS_SETTING, "") or "").strip()
    return int(raw) if raw.isdigit() and LOSS_RANGE[0] <= int(raw) <= LOSS_RANGE[1] else DEFAULT_LOSS_PCT


def set_loss_pct(session, raw) -> bool:
    from .inventory_service import set_setting
    raw = str(raw or "").strip()
    if not (raw.isdigit() and LOSS_RANGE[0] <= int(raw) <= LOSS_RANGE[1]):
        return False
    set_setting(session, LOSS_SETTING, raw)
    return True


# ---------------------------------------------------------------- days

def day_start(day: date) -> datetime:
    """The UTC stamp at which the lab's `day` begins."""
    return datetime.combine(day, datetime.min.time()).astimezone(timezone.utc).replace(tzinfo=None)


def day_of(stamp: datetime | None) -> date | None:
    return stamp.replace(tzinfo=timezone.utc).astimezone().date() if stamp else None


# ---------------------------------------------------------------- words

def welfare_words(text: str | None) -> tuple[list[str], str]:
    """The words of WORDS in `text` (each once, as written), and the first
    line that has one; ([], "") when none, or each is said not to be there."""
    found, line = [], ""
    for raw_line in (text or "").splitlines() or [""]:
        for match in _WORD_RE.finditer(raw_line):
            before = raw_line[:match.start()]
            if _NEGATED_EN.search(before) or _NEGATED_ZH.search(before):
                continue
            word = match.group(0)
            if word.lower() not in (w.lower() for w in found):
                found.append(word)
            if not line:
                line = raw_line.strip()
    if len(line) > MAX_LINE:
        line = line[:MAX_LINE - 1] + "…"
    return found, line


# ---------------------------------------------------------------- helpers

def _living(m, transferred: bool = False) -> bool:
    """Alive and, unless `transferred`, still the lab's (not transferred away),
    as the mouse sheet's green dot (services.mouse_is_active)."""
    from .services import END_STATUSES, normalize_status
    if m is None or m.date_of_death is not None:
        return False
    status = normalize_status(m.status or "")
    return status not in END_STATUSES and (transferred or status != "transfer")


def _mouse_path(m) -> str:
    return url_for("colony", view="mice", scope="all", q=m.mouse_id)


def _mouse_ref(m) -> dict:
    return {"kind": "mouse", "id": m.id}


def _is_colony(exp) -> bool:
    return (exp.db or "colony") == "colony"


def _experiments(session, statuses) -> list:
    from .models import Experiment
    return [e for e in session.scalars(select(Experiment).options(selectinload(Experiment.memberships))
                                       .where(Experiment.status.in_(statuses))
                                       .order_by(Experiment.id)) if _is_colony(e)]


def _mine(owner: str | None, m, exp=None) -> bool:
    """In this person's view: their own mouse, or a mouse in their experiment."""
    if not owner:
        return True
    return (m.owner or "") == owner or (exp is not None and (exp.owner_username or "") == owner)


def _busy_by_mouse(session) -> dict[int, list]:
    """Mouse row → the busy experiments it is in (planned, active, paused)."""
    out: dict[int, list] = {}
    for exp in _experiments(session, BUSY):
        for em in exp.memberships:
            out.setdefault(em.mouse_id_fk, []).append((exp, em))
    return out


# ---------------------------------------------------------------- 1. losing weight

def weight_loss(session, today: date, pct: float, owner: str | None = None) -> list[dict]:
    from .models import MouseRecord, MouseWeight
    since = today - timedelta(days=WINDOW_DAYS)
    by_mouse: dict[int, list] = {}
    for w in session.scalars(select(MouseWeight).join(MouseRecord, MouseWeight.mouse_id_fk == MouseRecord.id).where(
            MouseRecord.date_of_death.is_(None), MouseWeight.weigh_date >= since, MouseWeight.weigh_date <= today)
            .order_by(MouseWeight.weigh_date, MouseWeight.id)):
        by_mouse.setdefault(w.mouse_id_fk, []).append(w)
    in_exp = _busy_by_mouse(session)
    # Each started experiment's first weight of its mice, which may be older than the window.
    baselines: dict[int, tuple] = {}
    for mid, pairs in in_exp.items():
        for exp, em in pairs:
            if exp.status not in STARTED or exp.start_date is None or exp.start_date > today:
                continue
            ws = session.scalars(select(MouseWeight).where(
                MouseWeight.mouse_id_fk == mid, MouseWeight.weigh_date >= exp.start_date,
                MouseWeight.weigh_date <= today).order_by(MouseWeight.weigh_date, MouseWeight.id)).all()
            if len(ws) >= 2:
                baselines[mid] = (exp, em, ws[0], ws[-1])
                by_mouse.setdefault(mid, by_mouse.get(mid, []))
    ids, mice = list(by_mouse), {}
    for start in range(0, len(ids), 500):
        mice.update({m.id: m for m in session.scalars(select(MouseRecord).options(selectinload(MouseRecord.cage))
                                                       .where(MouseRecord.id.in_(ids[start:start + 500])))})
    out = []
    for mid, weights in by_mouse.items():
        m = mice.get(mid)
        if not _living(m):
            continue
        pairs = in_exp.get(mid, [])
        exp_now = next((e for e, _em in pairs if e.status in STARTED), pairs[0][0] if pairs else None)
        if not _mine(owner, m, exp_now):
            continue
        best = None
        if len(weights) >= 2:
            last = weights[-1]
            peak = max(weights, key=lambda w: (w.grams, w.weigh_date))
            if peak.grams and peak.weigh_date < last.weigh_date:
                best = ("peak", peak, last, (peak.grams - last.grams) / peak.grams * 100, None, None)
        if mid in baselines:
            exp, em, first, last = baselines[mid]
            if first.grams and first.id != last.id:
                loss = (first.grams - last.grams) / first.grams * 100
                if best is None or loss > best[3] + 1e-9:
                    best = ("experiment_start", first, last, loss, exp, em)
        if best is None or best[3] < pct - 1e-9:
            continue
        basis, high, last, loss, exp, em = best
        exp = exp or exp_now
        em = em or next((e2 for e1, e2 in pairs if e1 is exp), None)
        item = {"mouse": m.mouse_id, "ref": _mouse_ref(m), "sex": m.gender or "", "owner": m.owner or "",
                "cage": m.cage.cage_id if m.cage else "", "basis": basis,
                "peak_g": high.grams, "peak_on": high.weigh_date.isoformat(),
                "latest_g": last.grams, "latest_on": last.weigh_date.isoformat(), "loss_pct": round(loss, 1),
                "url": _mouse_path(m)}
        if exp is not None:
            from . import experiments as ex
            item.update(experiment=exp.name, experiment_ref={"kind": "experiment", "id": exp.id},
                        group=(em.treatment_group if em else "") or "", experiment_url=ex.page_url(exp))
        out.append(item)
    out.sort(key=lambda x: (-x["loss_pct"], x["mouse"]))
    return out


# ---------------------------------------------------------------- 2. status and experiment disagree

def status_experiment(session, owner: str | None = None) -> list[dict]:
    from .models import Experiment, MouseRecord
    from .services import normalize_status
    out = []
    in_exp = _busy_by_mouse(session)
    # A lab that keeps no mouse experiments here uses "experiment" as a plain label.
    keeps = session.scalar(select(Experiment.id).where(
        or_(Experiment.db == "colony", Experiment.db == "", Experiment.db.is_(None))).limit(1)) is not None
    if keeps:
        stmt = select(MouseRecord).options(selectinload(MouseRecord.cage)).where(
            MouseRecord.date_of_death.is_(None), func.lower(func.trim(MouseRecord.status)) == "experiment")
        if owner:
            stmt = stmt.where(MouseRecord.owner == owner)
        for m in session.scalars(stmt.order_by(MouseRecord.mouse_id)):
            if normalize_status(m.status or "") != "experiment" or m.id in in_exp:
                continue
            out.append({"mouse": m.mouse_id, "ref": _mouse_ref(m), "kind": "no_experiment",
                        "status": m.status, "owner": m.owner or "", "cage": m.cage.cage_id if m.cage else "",
                        "url": _mouse_path(m)})
    for exp in _experiments(session, ("active",)):
        from . import experiments as ex
        for em in exp.memberships:
            m = em.mouse
            if not _living(m, transferred=True) or (em.left_out or "").strip():
                continue
            status = normalize_status(m.status or "")
            if not status or status == "experiment" or not _mine(owner, m, exp):
                continue
            ref = _mouse_ref(m)
            out.append({"mouse": m.mouse_id, "ref": ref, "kind": "other_status", "status": m.status,
                        "owner": m.owner or "", "cage": m.cage.cage_id if m.cage else "",
                        "experiment": exp.name, "experiment_ref": {"kind": "experiment", "id": exp.id},
                        "experiment_url": ex.page_url(exp), "url": _mouse_path(m),
                        "propose": {"action": "mouse_change", "target": ref, "fields": {"status": "experiment"}}})
    seen, unique = set(), []
    for item in out:                      # a mouse in two active experiments: once
        key = (item["ref"]["id"], item["kind"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


# ---------------------------------------------------------------- 3. welfare words in recent notes

def welfare_notes(session, today: date, owner: str | None = None, features: dict | None = None) -> list[dict]:
    from .models import AuditEntry, FishRecord, MouseRecord, MouseWeight, TankRecord
    features = features or {}
    since = today - timedelta(days=NOTES_DAYS)
    changed: dict[tuple, datetime] = {}
    for table, record_id, at in session.execute(select(AuditEntry.table_name, AuditEntry.record_id,
                                                       AuditEntry.changed_at).where(
            AuditEntry.table_name.in_(("mice", "tanks", "fish")), AuditEntry.changed_at >= day_start(since),
            or_(AuditEntry.action == "create", AuditEntry.changes_json.like('%"note":[%'),
                AuditEntry.changes_json.like('%"notes":[%')))):
        if at and at > changed.get((table, record_id), datetime.min):
            changed[(table, record_id)] = at
    out = []

    def add(item, text, when):
        words, line = welfare_words(text)
        if words:
            out.append({**item, "words": words, "line": line, "changed_on": when.isoformat() if when else None})

    if features.get("colony", True):
        ids = [rid for (table, rid) in changed if table == "mice"]
        for start in range(0, len(ids), 500):
            for m in session.scalars(select(MouseRecord).where(MouseRecord.id.in_(ids[start:start + 500]))):
                if _living(m) and (not owner or (m.owner or "") == owner):
                    add({"animal": "mouse", "mouse": m.mouse_id, "ref": _mouse_ref(m), "owner": m.owner or "",
                         "where": "note", "url": _mouse_path(m)}, m.note, day_of(changed[("mice", m.id)]))
        stmt = select(MouseWeight).join(MouseRecord, MouseWeight.mouse_id_fk == MouseRecord.id).where(
            MouseWeight.weigh_date >= since, MouseWeight.weigh_date <= today, MouseRecord.date_of_death.is_(None),
            MouseWeight.notes != "")
        if owner:
            stmt = stmt.where(MouseRecord.owner == owner)
        for w in session.scalars(stmt.order_by(MouseWeight.weigh_date.desc(), MouseWeight.id.desc())):
            m = w.mouse
            if _living(m):
                add({"animal": "mouse", "mouse": m.mouse_id, "ref": _mouse_ref(m), "owner": m.owner or "",
                     "where": "weight", "grams": w.grams, "url": _mouse_path(m)}, w.notes, w.weigh_date)
    if features.get("zebrafish", True):
        tank_ids = [rid for (table, rid) in changed if table == "tanks"]
        fish_ids = [rid for (table, rid) in changed if table == "fish"]
        tanks_path = url_for("zebrafish", view="tanks")
        if tank_ids:
            for t in session.scalars(select(TankRecord).where(TankRecord.id.in_(tank_ids),
                                                              TankRecord.active.is_(True))):
                if not owner or (t.owner or "") == owner:
                    add({"animal": "tank", "tank": t.tank_id, "ref": {"kind": "tank", "id": t.id},
                         "owner": t.owner or "", "where": "note", "url": f"{tanks_path}#tank-{t.id}"},
                        t.notes, day_of(changed[("tanks", t.id)]))
        if fish_ids:
            from .zebrafish_routes import fish_alive
            for f in session.scalars(select(FishRecord).options(selectinload(FishRecord.tank)).where(
                    FishRecord.id.in_(fish_ids))):
                t = f.tank
                if t is None or not fish_alive(f) or (owner and (t.owner or "") != owner):
                    continue
                add({"animal": "fish", "tank": t.tank_id, "fish": f.individual_id or "",
                     "ref": {"kind": "fish", "id": f.id}, "owner": t.owner or "", "where": "note",
                     "url": f"{tanks_path}#tank-{t.id}"}, f.notes, day_of(changed[("fish", f.id)]))
    out.sort(key=lambda x: (x["changed_on"] or "", ), reverse=True)
    return out


# ---------------------------------------------------------------- 4. not weighed in an experiment that weighs

def _weighs(exp) -> bool:
    try:
        chosen = json.loads(exp.readout or "{}")
    except ValueError:
        chosen = {}
    key = chosen.get("key") if isinstance(chosen, dict) else None
    return not key or key == "body_weight"


def weigh_every(exp, day_number: int | None = None) -> int:
    """Days allowed between weighings: the longest gap between the weighing
    days the regimen plans, while they last; DEFAULT_WEIGH_EVERY otherwise."""
    from . import experiments as ex
    from .experiment_steps import parse_days
    days: set[int] = set()
    for step in exp.steps:
        if ex.is_reading(step.kind):
            try:
                days.update(parse_days(step.days))
            except ValueError:
                continue
    days = sorted(d for d in days if d >= 0)
    if len(days) < 2 or (day_number is not None and day_number > days[-1]):
        return DEFAULT_WEIGH_EVERY
    return max(1, max(b - a for a, b in zip(days, days[1:])))


def not_weighed(session, today: date, owner: str | None = None) -> list[dict]:
    from . import experiments as ex
    from .models import MouseWeight
    out = _others_not_weighed(session, today, owner)
    for exp in _experiments(session, ("active",)):
        if exp.start_date is None or exp.start_date > today or not _weighs(exp):
            continue
        day_number = (today - exp.start_date).days + 1
        every = weigh_every(exp, day_number)
        members = [em for em in exp.memberships if _living(em.mouse) and not (em.left_out or "").strip()
                   and _mine(owner, em.mouse, exp)]
        if not members:
            continue
        last: dict[int, date] = {}
        for mid, day in session.execute(select(MouseWeight.mouse_id_fk, MouseWeight.weigh_date).where(
                MouseWeight.mouse_id_fk.in_([em.mouse_id_fk for em in members]), MouseWeight.weigh_date <= today)):
            if mid not in last or day > last[mid]:
                last[mid] = day
        for em in members:
            m = em.mouse
            weighed = last.get(m.id)
            since = max(weighed, exp.start_date) if weighed else exp.start_date
            days = (today - since).days
            if days <= every:
                continue
            out.append({"mouse": m.mouse_id, "ref": _mouse_ref(m), "owner": m.owner or "",
                        "experiment": exp.name, "experiment_ref": {"kind": "experiment", "id": exp.id},
                        "group": em.treatment_group or "", "last_weighed": weighed.isoformat() if weighed else None,
                        "days": days, "every_days": every, "url": ex.page_url(exp)})
    from .positions import natural
    out.sort(key=lambda x: (-x["days"], natural(x.get("mouse") or x.get("label") or "")))
    return out


# The ref an action takes for each kind of animal (app/actions.py).
_REF_KIND = {"fish": "fish", "unit": "stock_unit", "organism": "org_animal"}


def _others_not_weighed(session, today: date, owner: str | None) -> list[dict]:
    """not_weighed for the animals of an active experiment in any other
    database (fish, flies, worms, other organisms): weighed when the
    experiment has a body weight reading for them."""
    from . import experiments as ex
    from .models import Experiment, ExperimentReading
    out = []
    for exp in session.scalars(select(Experiment).where(Experiment.status == "active").order_by(Experiment.id)):
        if _is_colony(exp) or exp.start_date is None or exp.start_date > today:
            continue
        place = ex.place_for(session, exp.db)
        if place is None or not ex.weighs(exp, place):
            continue
        members = []
        for sub in ex.subjects(session, exp, place):
            if sub.kind not in _REF_KIND or not sub.alive or sub.left_out.strip():
                continue
            rec = sub.record
            whose = (rec.tank.owner if sub.kind == "fish" and rec.tank else getattr(rec, "owner", "")) or ""
            if owner and whose != owner and (exp.owner_username or "") != owner:
                continue
            members.append((sub, whose))
        if not members:
            continue
        every = weigh_every(exp, (today - exp.start_date).days + 1)
        last: dict[str, date] = {}
        for key, day in session.execute(select(ExperimentReading.subject, ExperimentReading.read_on).where(
                ExperimentReading.experiment_id_fk == exp.id, ExperimentReading.readout_key == "body_weight",
                ExperimentReading.read_on <= today)):
            if key not in last or day > last[key]:
                last[key] = day
        for sub, whose in members:
            weighed = last.get(sub.key)
            since = max(weighed, exp.start_date) if weighed else exp.start_date
            days = (today - since).days
            if days <= every:
                continue
            out.append({"animal": sub.kind, "label": sub.label, "ref": {"kind": _REF_KIND[sub.kind], "id": sub.id},
                        "owner": whose, "experiment": exp.name, "experiment_ref": {"kind": "experiment", "id": exp.id},
                        "group": sub.group, "last_weighed": weighed.isoformat() if weighed else None,
                        "days": days, "every_days": every, "url": ex.page_url(exp)})
    return out


# ---------------------------------------------------------------- all four

def checks(session, today: date | None = None, owner: str | None = None, pct: float | None = None,
           features: dict | None = None) -> list[dict]:
    """The four checks, as lab_checks lists its own: id, title, severity,
    items, fix. `owner`: only that person's animals and experiments."""
    from . import lab
    today = today or date.today()
    features = features if features is not None else lab.features_on(session)
    pct = pct if pct is not None else loss_pct(session)
    out = []

    def check(cid, title, severity, items, fix):
        out.append({"id": cid, "title": title, "severity": severity, "count": len(items), "items": items,
                    "fix": fix})

    if features.get("colony", True):
        check("weight_loss", f"Mice down {pct:g}% or more from their peak weight in the last {WINDOW_DAYS} days, "
                             "or from their first weight in an experiment that started", "high",
              weight_loss(session, today, pct, owner),
              "Say what the records show and check them against the protocol's humane endpoints; nothing is "
              "proposed for this.")
    if features.get("colony", True) or features.get("zebrafish", True):
        check("welfare_notes", f"Notes changed in the last {NOTES_DAYS} days with a welfare word (hunched, "
                               "lethargic, wound…): notes that may need a look", "medium",
              welfare_notes(session, today, owner, features),
              "Quote the line to the person; it is a word in a note, not a finding. Nothing is proposed.")
    check("not_weighed", "Animals in an active experiment measuring body weight not weighed for longer than "
                         "its regimen's weighing days allow (7 days when it plans none)", "medium",
          not_weighed(session, today, owner),
          "Weigh them; a mouse's weight is recorded with mouse_weight, any other animal's as the experiment's "
          "body weight reading, once the person has the number.")
    if features.get("colony", True):
        check("status_experiment", "Mice whose status and experiments disagree: status experiment in no "
                                   "experiment, or another status while in an active experiment", "low",
              status_experiment(session, owner),
              "For one in an active experiment, propose sets its status to experiment; for one in none, ask "
              "the person which experiment it is in or what its status should be.")
    return out


# ---------------------------------------------------------------- Home's card

HOME_LIMIT = 8      # rows of each check on Home's card


def default_scope(user) -> str:
    """Whose animals someone sees first: animal care, facility managers and
    admins the lab's; a member their own (and their experiments')."""
    from . import access
    return "lab" if access.is_care(user) else "mine"


def home_view(session, user, asked: str | None = None, today: date | None = None,
              features: dict | None = None) -> dict:
    """Home's "Animals that may need a look": each check with items, the
    first HOME_LIMIT of each, for this person's scope (or the one asked)."""
    scope = asked if asked in ("mine", "lab") else default_scope(user)
    found = checks(session, today, None if scope == "lab" else user.username, features=features)
    groups = [{"id": c["id"], "count": c["count"], "rows": c["items"][:HOME_LIMIT],
               "more": max(0, c["count"] - HOME_LIMIT)} for c in found if c["items"]]
    return {"scope": scope, "groups": groups, "count": sum(g["count"] for g in groups)}
