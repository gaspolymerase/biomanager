"""Working offline: a device's copy of the facility, and what was done there.

A phone, or the desktop app's relay, keeps what its person needs in an
animal facility with no signal (a snapshot: the mouse colony, their to-dos,
fly vials and fish tanks), and records what they do there as a queue of
actions (app/actions.py: a weight, a death, a litter, a wean, a move, a
to-do done). Back online it sends the queue, and each action is replayed
here through the page's own route, as its person, with the page's own rules.
The lab keeps one master copy: the queue is a list of intentions, never a
second database to merge.

  GET  /api/sync/snapshot           what this person may see, and a cursor
  GET  /api/sync/changes?since=N    what changed and went since cursor N
  POST /api/sync/push               the queue, in order; a result for each
  GET  /api/sync/held               their held changes
  POST /api/sync/held/<id>          decide one: {"decision": "mine" | "theirs"}
  /decisions                        Needs your decision, on the web

Each change in the queue:

  {"op": "4f9c…",                    made on the device; applied once however often it is sent
   "made_at": "2026-10-11T09:14:05+02:00",
   "cursor": 4120,                   the device's cursor when it was made
   "action": "mice_died", "target": …, "fields": {"mice": [{"kind": "mouse", "id": 1043}]},
   "base": {"mouse:1043": {"v": 17, "status": "breeder"}},     what the device saw
   "creates": {"mouse": ["tmp:9b1e", …]}}                       records it makes, by temporary id

A record that hasn't changed since the device saw it (its sync_version is
"v", app/sync_clock.py) takes the change. One that has is compared field by
field: a different field changed is a merge; the same field changed to
something else is a clash, held under Needs your decision until its person
chooses Keep mine or Keep theirs. Later changes about a held record wait
for the decision ("waiting": the device keeps them and sends them again).
A change the page refuses comes back refused, with the page's own message.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, g, jsonify, make_response, redirect, render_template, request,
                   url_for)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from . import access, actions, lab, sync_clock
from .db import SessionLocal
from .i18n import gettext
from .positions import natural_sorted
from .models import (AuditEntry, CageRecord, FishRack, HeldChange, LabDevice, LitterRecord, MouseRack, MouseRecord,
                     MouseWeight, StockModule, StockRack, StockUnit, SyncDeletion, SyncOp, TankRecord, TaskItem,
                     UserAccount)

bp = Blueprint("sync", __name__)

# What can be done offline: the facility's everyday jobs (the design's
# version 1), then vials and tanks (phase 3).
ACTIONS = ("mouse_weight", "mice_died", "mouse_change", "litter_born", "litter_genotyping", "wean", "mice_move",
           "note_add", "task_done",
           "stock_event", "rack_flipped", "tank_change", "fish_sac", "water_reading", "mating_returned")
MAX_OPS = 200
TASK_DAYS = 14               # to-dos due within this many days (and any overdue)
DEAD_DAYS = 90               # mice that died this recently stay in the snapshot
WEIGHTS_EACH = 5             # the last few weights of each living mouse
MAX_CHANGES = 20000          # more than this since a cursor: take a fresh snapshot instead
REMIND_AFTER = timedelta(days=3)
TMP = re.compile(r"^tmp:[A-Za-z0-9_-]{1,40}$")


# ---------------------------------------------------------------- who is asking

def _user():
    """The person on this device (its token, app/lab_devices.py) or in this
    browser. Guests don't work offline."""
    user = g.get("user")
    if user is None:
        abort(make_response(jsonify({"ok": False, "error": gettext("Not signed in")}), 401))
    if user.expires_at is not None:
        abort(make_response(jsonify({"ok": False, "error": gettext("A guest pass can't work offline.")}), 403))
    return user


# ---------------------------------------------------------------- records as a device keeps them

def _d(value):
    return value.isoformat() if value else None


def mouse_row(m: MouseRecord) -> dict:
    return {"id": m.id, "number": m.mouse_id, "ear_tag": m.ear_tag or "", "strain": m.strain or "",
            "sex": m.gender or "",
            "transgenes": [m.transgene_1 or "", m.transgene_2 or "", m.transgene_3 or "", m.transgene_4 or ""],
            "genotype": m.genotype or "", "status": m.status or "", "owner": m.owner or "", "note": m.note or "",
            "cage": m.cage_id_fk, "litter": m.litter_id_fk, "date_of_death": _d(m.date_of_death),
            "v": m.sync_version}


def cage_row(c: CageRecord, user=None) -> dict:
    from . import services
    return {"id": c.id, "cage_id": c.cage_id, "purpose": c.purpose or "", "owner": c.owner or "",
            "shared": bool(c.is_shared), "group": c.share_group_id, "notes": c.notes or "",
            "date_give_birth": _d(c.date_give_birth), "card_id": c.card_id or "",
            "genotype_summary": c.genotype_summary or "", "room": c.room or "",
            "location_detail": c.location_detail or "", "rack": c.rack_id_fk, "row": c.rack_row, "col": c.rack_col,
            "breeding": services.is_breeding_cage(c.purpose), "litters": cage_litters(c),
            "edit": access.can_edit_cage(c, user) if user is not None else False, "v": c.sync_version}


def cage_litters(c: CageRecord) -> list[dict]:
    """The cage's litters not yet weaned, oldest first: what the phone lists
    and what its Genotyping and Wean ask between (litter: the birth date)."""
    from . import services
    return [{"born": _d(cl.born), "pups": cl.pups, "dam": cl.dam or "", "litter": cl.litter_id_fk,
             "wean_due": _d(services.litter_wean_due(cl)), "genotyping_due": _d(services.litter_genotyping_due(cl))}
            for cl in services.open_litters(c)]


def litter_row(lt: LitterRecord) -> dict:
    return {"id": lt.id, "litter_id": lt.litter_id, "date_of_birth": _d(lt.date_of_birth),
            "father": lt.father_info or "", "mother": lt.mother_info or "", "pups": lt.total_pups or 0,
            "notes": lt.notes or "", "weaned_on": _d(lt.weaned_on), "v": lt.sync_version}


def rack_row(r: MouseRack) -> dict:
    return {"id": r.id, "name": r.name, "rows": r.rows, "cols": r.cols, "room": r.room or "",
            "naming": r.naming or "{}", "v": r.sync_version}


def weight_row(w: MouseWeight) -> dict:
    return {"id": w.id, "mouse": w.mouse_id_fk, "date": _d(w.weigh_date), "grams": w.grams, "notes": w.notes or "",
            "by": w.recorded_by or "", "v": w.sync_version}


def task_row(t: TaskItem, user=None) -> dict:
    from .app import task_can_edit
    return {"id": t.id, "title": t.title, "due": _d(t.due_date), "status": t.status or "", "done": t.done_at is not None,
            "priority": t.priority or "", "owner": t.owner or "", "shared": bool(t.is_shared),
            "notes": t.notes or "", "edit": task_can_edit(t, user) if user is not None else False,
            "v": t.sync_version}


def tank_row(t: TankRecord) -> dict:
    return {"id": t.id, "tank_id": t.tank_id, "purpose": t.purpose or "", "owner": t.owner or "",
            "line": t.line.name if t.line is not None else "", "card_id": t.card_id or "", "rack": t.rack_id_fk,
            "row": t.row, "col": t.col, "active": bool(t.active), "notes": t.notes or "",
            "mating_return_at": _d(t.mating_return_at), "v": t.sync_version}


def unit_row(u: StockUnit, modules: dict) -> dict:
    return {"id": u.id, "module": modules.get(u.module_id_fk, ""), "number": u.number, "genotype": u.genotype or "",
            "purpose": u.purpose or "", "active": bool(u.active), "rack": u.rack_id_fk, "row": u.rack_row,
            "col": u.rack_col, "set_up_on": _d(u.set_up_on), "ready_on": _d(u.ready_on), "owner": u.owner or "",
            "notes": u.notes or "", "v": u.sync_version}


# Each kind of record in a change (actions.KINDS) and how a device keeps it.
ROWS = {"mouse": (MouseRecord, "mice", mouse_row), "cage": (CageRecord, "mouse_cages", cage_row),
        "litter": (LitterRecord, "litters", litter_row), "task": (TaskItem, "tasks", task_row),
        "tank": (TankRecord, "tanks", tank_row)}


def _tank_visible(t: TankRecord, user, groups: set) -> bool:
    return user.role == "admin" or t.share_group_id is None or t.share_group_id in groups or t.owner == user.username


def _stock_modules(s, user) -> dict:
    """The fly and worm databases this person may open: id → key."""
    return {m.id: m.key for m in s.scalars(select(StockModule)) if lab.can_see(m, user)}


def _groups(user) -> set:
    from . import groups as project_groups
    return set(project_groups.ids_of(user))


# ---------------------------------------------------------------- the snapshot

@bp.route("/api/sync/snapshot")
def snapshot():
    """Everything a device keeps, as this person may see it, and the cursor
    to ask for changes from."""
    from . import services
    from .app import task_visible_clause
    user = _user()
    today = date.today()
    with SessionLocal() as s:
        cursor = sync_clock.current(s)          # first: what changes meanwhile comes again with the next changes
        racks = [rack_row(r) for r in services.mouse_racks(s)]
        cages = [cage_row(c, user) for c in natural_sorted(
            s.scalars(select(CageRecord).options(selectinload(CageRecord.cage_litters))), key=lambda c: c.cage_id)]
        mice_rows = list(s.scalars(select(MouseRecord).where(
            MouseRecord.date_of_death.is_(None) | (MouseRecord.date_of_death >= today - timedelta(days=DEAD_DAYS)))
            .order_by(MouseRecord.mouse_id)))
        mice = [mouse_row(m) for m in mice_rows]
        litter_ids = {m.litter_id_fk for m in mice_rows if m.litter_id_fk}
        litters = [litter_row(lt) for lt in s.scalars(select(LitterRecord).where(
            LitterRecord.id.in_(litter_ids) | LitterRecord.weaned_on.is_(None)))]
        living = [m.id for m in mice_rows if m.date_of_death is None]
        weights, seen = [], {}
        for w in s.scalars(select(MouseWeight).where(MouseWeight.mouse_id_fk.in_(living))
                           .order_by(MouseWeight.mouse_id_fk, MouseWeight.weigh_date.desc())) if living else []:
            if seen.get(w.mouse_id_fk, 0) < WEIGHTS_EACH:
                seen[w.mouse_id_fk] = seen.get(w.mouse_id_fk, 0) + 1
                weights.append(weight_row(w))
        tasks = [task_row(t, user) for t in s.scalars(select(TaskItem).where(
            task_visible_clause(user), TaskItem.done_at.is_(None),
            TaskItem.due_date.is_(None) | (TaskItem.due_date <= today + timedelta(days=TASK_DAYS)))
            .order_by(TaskItem.due_date))]
        groups = _groups(user)
        tanks = [tank_row(t) for t in natural_sorted(s.scalars(select(TankRecord).where(TankRecord.active.is_(True))),
                                                     key=lambda t: t.tank_id) if _tank_visible(t, user, groups)]
        fish_racks = [{"id": r.id, "name": r.name} for r in s.scalars(select(FishRack))]
        modules = _stock_modules(s, user)
        units = [unit_row(u, modules) for u in s.scalars(select(StockUnit).where(
            StockUnit.active.is_(True), StockUnit.module_id_fk.in_(list(modules)))
            .order_by(StockUnit.module_id_fk, StockUnit.number))] if modules else []
        stock_racks = [{"id": r.id, "module": modules[r.module_id_fk], "name": r.name}
                       for r in s.scalars(select(StockRack)) if r.module_id_fk in modules]
        due = services.weaning_due(s, today - timedelta(days=7), today + timedelta(days=TASK_DAYS))
        options = services.dropdown_options_map(s)
        lab_name = lab.lab_name(s)
    return jsonify({"ok": True, "cursor": cursor, "taken": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "user": {"username": user.username, "name": user.display_name or user.username, "role": user.role},
                    "lab": lab_name, "options": options, "racks": racks, "cages": cages, "mice": mice,
                    "litters": litters, "weights": weights, "tasks": tasks, "tanks": tanks, "fish_racks": fish_racks,
                    "stock_units": units, "stock_racks": stock_racks,
                    "weaning_due": [_due_row(d) for d in due], "actions": list(ACTIONS)})


def _due_row(d: dict) -> dict:
    return {"born": _d(d.get("born")), "due": _d(d.get("due")), "pups": d.get("pups") or 0,
            "cage": d["cage"].id if d.get("cage") is not None else None,
            "litter": d["litter"].id if d.get("litter") is not None else None}


# ---------------------------------------------------------------- what changed since

@bp.route("/api/sync/changes")
def changes():
    """Records changed since a cursor (each as the snapshot has it), and
    those that went or that this person may no longer see."""
    from .app import task_visible_clause
    user = _user()
    try:
        since = int(request.args.get("since", ""))
    except ValueError:
        return jsonify({"ok": False, "error": gettext("Ask with since=<cursor>, from the snapshot.")}), 400
    with SessionLocal() as s:
        cursor = sync_clock.current(s)
        if since > cursor:
            return jsonify({"ok": True, "reset": True, "cursor": cursor})
        out: dict[str, list] = {}
        gone: list[dict] = [{"table": d.table_name, "id": d.record_id} for d in s.scalars(
            select(SyncDeletion).where(SyncDeletion.sync_version > since).order_by(SyncDeletion.sync_version))]
        count = len(gone)

        def changed(model):
            return list(s.scalars(select(model).where(model.sync_version > since).order_by(model.sync_version)))
        out["racks"] = [rack_row(r) for r in changed(MouseRack)]
        out["cages"] = [cage_row(c, user) for c in changed(CageRecord)]
        out["mice"] = [mouse_row(m) for m in changed(MouseRecord)]
        out["litters"] = [litter_row(lt) for lt in changed(LitterRecord)]
        out["weights"] = [weight_row(w) for w in changed(MouseWeight)]
        visible_tasks = set(s.scalars(select(TaskItem.id).where(TaskItem.sync_version > since,
                                                                 task_visible_clause(user))))
        out["tasks"] = []
        for t in changed(TaskItem):
            if t.id in visible_tasks:
                out["tasks"].append(task_row(t, user))
            else:
                gone.append({"table": "tasks", "id": t.id})
        groups = _groups(user)
        out["tanks"] = []
        for t in changed(TankRecord):
            if _tank_visible(t, user, groups):
                out["tanks"].append(tank_row(t))
            else:
                gone.append({"table": "tanks", "id": t.id})
        modules = _stock_modules(s, user)
        out["stock_units"] = []
        for u in changed(StockUnit):
            if u.module_id_fk in modules:
                out["stock_units"].append(unit_row(u, modules))
            else:
                gone.append({"table": "stock_units", "id": u.id})
        count += sum(len(v) for v in out.values())
        if count > MAX_CHANGES:
            return jsonify({"ok": True, "reset": True, "cursor": cursor})
    return jsonify({"ok": True, "cursor": max(cursor, since), "changes": out, "gone": gone})


# ---------------------------------------------------------------- taking in the queue

class _Push:
    """One push: temporary ids met so far, and what later changes wait for."""

    def __init__(self, user):
        self.user = user
        self.ids: dict[str, dict] = {}          # tmp:… → {"kind", "id"}
        self.held: set = set()                  # records and temporary ids held changes are about
        self.refused: set = set()               # temporary ids a refused change would have made


@bp.route("/api/sync/push", methods=["POST"])
def push():
    """The device's queue, in the order it was made: a result for each."""
    user = _user()
    body = request.get_json(silent=True) or {}
    ops = body.get("ops")
    if not isinstance(ops, list):
        return jsonify({"ok": False, "error": gettext("Send {\"ops\": [...]}.")}), 400
    if len(ops) > MAX_OPS:
        return jsonify({"ok": False, "error": gettext("At most %(max)s changes at once.", max=MAX_OPS)}), 400
    state = _Push(user)
    results = [_take(state, op) for op in ops]
    with SessionLocal() as s:
        cursor = sync_clock.current(s)
        held = s.scalar(select(func.count(HeldChange.id)).where(HeldChange.user_id_fk == user.id,
                                                                HeldChange.status == "open")) or 0
    return jsonify({"ok": True, "results": results, "cursor": cursor, "held": held})


def _answer(op_id: str, result: str, message: str = "", **extra) -> dict:
    return {"op": op_id, "result": result, "message": message, **extra}


def _take(state: _Push, op) -> dict:
    if not isinstance(op, dict):
        return _answer("", "refused", gettext("Each change is an object."))
    op_id = str(op.get("op") or "").strip()
    if not op_id or len(op_id) > 64:
        return _answer(op_id, "refused", gettext("Each change needs its own id (op)."))
    name = str(op.get("action") or "")
    if name not in ACTIONS:
        return _answer(op_id, "refused", gettext("“%(action)s” can't be done offline.", action=name))
    with SessionLocal() as s:
        stored = s.scalar(select(SyncOp).where(SyncOp.op_id == op_id))
        if stored is not None and stored.result != "running":
            if stored.user_id_fk != state.user.id:
                return _answer(op_id, "refused", gettext("Someone else already sent a change with this id."))
            answer = json.loads(stored.result_json or "{}")
            state.ids.update(answer.get("ids") or {})
            _note_blocked(state, op, answer.get("result", ""))
            return answer
        if stored is not None:
            return _answer(op_id, "waiting", gettext("This change is being taken in already."))
    # Temporary ids: from this push, else from what was sent before.
    try:
        change = _resolve(state, {"action": name, "target": op.get("target"), "fields": op.get("fields") or {}})
    except _Blocked as blocked:
        if blocked.result == "refused":
            _note_blocked(state, op, "refused")
        return _answer(op_id, blocked.result, str(blocked))
    with SessionLocal() as s:
        refs = _records(s, change)
        if any(r in state.held for r in refs) or _held_about(s, state.user, refs):
            _note_blocked(state, op, "waiting")
            return _answer(op_id, "waiting", gettext("Waits for an earlier change that needs your decision."))
        # Claim the id, so the same change sent twice at once is taken in once.
        claim = SyncOp(op_id=op_id, user_id_fk=state.user.id, action=name, result="running",
                       device_id_fk=getattr(g.get("device"), "id", None), made_at=_when(op.get("made_at")))
        s.add(claim)
        try:
            s.commit()
        except IntegrityError:
            return _answer(op_id, "waiting", gettext("This change is being taken in already."))
        claim_id = claim.id
    try:
        answer = _apply(state, op, op_id, change)
    except Exception as exc:  # noqa: BLE001 — never leave the claim behind: the device sends it again
        with SessionLocal() as s:
            s.query(SyncOp).filter(SyncOp.id == claim_id).delete()
            s.commit()
        current_app.logger.exception("taking in an offline change failed")
        return _answer(op_id, "waiting", gettext("The server couldn't take this change in just now: %(error)s",
                                                 error=str(exc)))
    with SessionLocal() as s:
        row = s.get(SyncOp, claim_id)
        row.result, row.message = answer["result"], answer.get("message", "")[:2000]
        row.batch_id_fk = answer.get("batch")
        row.result_json = json.dumps(answer)
        if answer["result"] == "held":
            held = s.get(HeldChange, answer["held"])
            held.op_id_fk = claim_id
        s.commit()
    _note_blocked(state, op, answer["result"])
    return answer


def _note_blocked(state: _Push, op: dict, result: str) -> None:
    """Later changes about what a held or refused change touched wait or go too."""
    made = [t for ids in (op.get("creates") or {}).values() if isinstance(ids, list) for t in ids]
    if result in ("held", "waiting"):
        state.held.update(made)
        with SessionLocal() as s:
            try:
                state.held.update(_records(s, {"action": op.get("action"), "target": op.get("target"),
                                               "fields": op.get("fields") or {}}))
            except actions.ActionError:
                pass
    elif result == "refused":
        state.refused.update(made)


class _Blocked(Exception):
    def __init__(self, result: str, message: str):
        super().__init__(message)
        self.result = result


def _resolve(state: _Push, value):
    """`value` with each temporary id ("tmp:…") replaced by the record it became."""
    if isinstance(value, str) and TMP.match(value):
        if value in state.ids:
            return dict(state.ids[value])
        if value in state.held:
            raise _Blocked("waiting", gettext("Waits for an earlier change that needs your decision."))
        if value in state.refused:
            raise _Blocked("refused", gettext("An earlier change this one depends on was refused."))
        found = _sent_before(state.user, value)
        if found is None:
            raise _Blocked("refused", gettext("It names a record (%(id)s) that was never made.", id=value))
        if found.get("result") == "held":
            raise _Blocked("waiting", gettext("Waits for an earlier change that needs your decision."))
        if "kind" not in found:
            raise _Blocked("refused", gettext("An earlier change this one depends on was refused."))
        state.ids[value] = found
        return dict(found)
    if isinstance(value, list):
        return [_resolve(state, v) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(state, v) for k, v in value.items()}
    return value


def _sent_before(user, tmp: str) -> dict | None:
    """What a temporary id became in a change sent before (its result's
    ids), or {"result"} for a change that made nothing; None if unknown."""
    with SessionLocal() as s:
        for row in s.scalars(select(SyncOp).where(SyncOp.user_id_fk == user.id,
                                                  SyncOp.result_json.contains(json.dumps(tmp)))
                             .order_by(SyncOp.id.desc()).limit(5)):
            answer = json.loads(row.result_json or "{}")
            if tmp in (answer.get("ids") or {}):
                return answer["ids"][tmp]
            if tmp in json.dumps(answer.get("creates") or {}):
                return {"result": answer.get("result", "")}
    return None


def _when(text) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is not None:
        moment = (moment - moment.utcoffset()).replace(tzinfo=None)
    return moment


# ---------------------------------------------------------------- which records a change is about

def _records(s, change: dict) -> list[tuple[str, int]]:
    """The records a change touches, as (kind, row id)."""
    act = actions.CATALOGUE[change["action"]]
    out = []
    if act.target is not None and change.get("target") not in (None, "", {}):
        record = actions._target(s, act, change)
        kind = _kind_of(record)
        if kind:
            out.append((kind, record.id))
    fields = change.get("fields") or {}
    for m in fields.get("mice") or []:
        if isinstance(m, (dict, int, str)):
            out.append(("mouse", actions.find(s, "mouse", m).id))
    for row in fields.get("rows") or []:
        if isinstance(row, dict):
            out += [("mouse", actions.find(s, "mouse", m).id) for m in row.get("mice") or []]
    return list(dict.fromkeys(out))


def _kind_of(record) -> str:
    for kind, (model, _table, _row) in ROWS.items():
        if isinstance(record, model):
            return kind
    for kind, spec in actions.KINDS.items():
        if isinstance(record, spec[0]):
            return kind
    return ""


def _held_about(s, user, refs) -> bool:
    if not refs:
        return False
    for held in s.scalars(select(HeldChange).where(HeldChange.user_id_fk == user.id, HeldChange.status == "open")):
        about = {(r["kind"], r["id"]) for r in json.loads(held.clash_json or "{}").get("records", [])}
        if about & set(refs):
            return True
    return False


# ---------------------------------------------------------------- clashes

def _now(kind: str, record, key: str):
    row = ROWS[kind][2](record) if kind in ROWS else {}
    return row.get(key)


def _plain(value):
    """A value as the device and the record compare it."""
    if isinstance(value, dict) and "id" in value:
        return value["id"]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if value == "":
        return None
    return value


# What a change's field is called on the record as a device keeps it.
FIELD_KEYS = {"tank_change": {"new_tank_id": "tank_id"}}


def _who_changed(s, kind: str, record) -> tuple[str, str]:
    """Who last changed a record, and when (for "Jordan set it … at 09:40")."""
    table = ROWS[kind][1] if kind in ROWS else ""
    entry = s.scalar(select(AuditEntry).where(AuditEntry.table_name == table, AuditEntry.record_id == record.id)
                     .order_by(AuditEntry.id.desc()).limit(1)) if table else None
    if entry is None:
        return "", ""
    return entry.changed_by or "", entry.changed_at.isoformat(sep=" ", timespec="minutes") if entry.changed_at else ""


def _label(kind: str, record) -> str:
    return actions._record_label(record) if kind in ("mouse", "cage", "litter") else (
        getattr(record, "title", None) or getattr(record, "tank_id", None) or f"#{record.id}")


def _fields_clash(s, kind, record, saw: dict, fields: dict, keys: dict | None = None) -> dict:
    """Per field: what the device saw, what is there now, what it set; only
    the fields someone else changed to something else meanwhile."""
    out = {}
    for name, new in fields.items():
        key = (keys or {}).get(name, name)
        if key not in saw:
            continue
        before, now = _plain(saw[key]), _plain(_now(kind, record, key))
        if now != before and now != _plain(new):
            out[key] = {"saw": saw[key], "now": _now(kind, record, key), "mine": new}
    return out


def _check(s, user, op: dict, change: dict) -> tuple[str, list[dict], dict, str]:
    """("fits" | "merged" | "clash" | "done", clashes, the change to make, a message)."""
    from . import services
    base = op.get("base") if isinstance(op.get("base"), dict) else {}
    name, fields = change["action"], dict(change.get("fields") or {})
    act = actions.CATALOGUE[name]
    target = actions._target(s, act, change) if act.target is not None else None
    tkind = _kind_of(target) if target is not None else ""
    moved_on = False
    clashes: list[dict] = []

    def saw(kind, record):
        nonlocal moved_on
        seen = base.get(f"{kind}:{record.id}")
        if not isinstance(seen, dict):
            return None
        if seen.get("v") == getattr(record, "sync_version", None):
            return None
        moved_on = True
        return seen

    def clash(kind, record, per_field, message=""):
        by, at = _who_changed(s, kind, record)
        clashes.append({"kind": kind, "id": record.id, "label": _label(kind, record), "fields": per_field,
                        "by": by, "at": at, "message": message})

    if name in ("mouse_change", "tank_change") and target is not None:
        seen = saw(tkind, target)
        if seen is not None:
            per_field = _fields_clash(s, tkind, target, seen, fields, FIELD_KEYS.get(name))
            if per_field:
                clash(tkind, target, per_field)
    elif name == "mice_died":
        keep = []
        for ref in fields.get("mice") or []:
            m = actions.find(s, "mouse", ref)
            seen = saw("mouse", m)
            if (m.status or "").strip().lower() in services.END_STATUSES:
                if seen is not None or m.date_of_death is not None:
                    continue                         # both marked it dead: it stays as it is
            if seen is not None and "status" in seen and _plain(seen["status"]) != _plain(m.status):
                clash("mouse", m, {"status": {"saw": seen["status"], "now": m.status, "mine": "sac"}})
            keep.append(ref)
        if not keep and not clashes:
            return "done", [], change, gettext("Already marked dead or culled.")
        fields["mice"] = keep
    elif name == "mice_move" and target is not None:
        keep = []
        for ref in fields.get("mice") or []:
            m = actions.find(s, "mouse", ref)
            seen = saw("mouse", m)
            if m.cage_id_fk == target.id:
                continue                             # already there
            if seen is not None and "cage" in seen and seen["cage"] != m.cage_id_fk:
                clash("mouse", m, {"cage": {"saw": seen["cage"], "now": m.cage_id_fk, "mine": target.id}},
                      gettext("Two places for one animal."))
            keep.append(ref)
        if not keep and not clashes:
            return "done", [], change, gettext("Already in cage %(cage)s.", cage=target.cage_id)
        fields["mice"] = keep
    elif name == "litter_born" and target is not None:
        seen = saw("cage", target)
        mine = fields.get("date") or date.today().isoformat()
        now = _d(target.date_give_birth)
        # A cage can have two litters (services.open_litters): any of them
        # born that day is this one, recorded already.
        if now == mine or any(_d(cl.born) == mine for cl in services.open_litters(target)):
            return "done", [], change, gettext("That birth date was recorded already.")
        # Another litter keeps the earlier one, whatever it became meanwhile;
        # a correction is of the date the phone saw.
        if seen is not None and "date_give_birth" in seen and now != seen["date_give_birth"] \
                and fields.get("another") is not True:
            clash("cage", target, {"date_give_birth": {"saw": seen["date_give_birth"], "now": now, "mine": mine}},
                  gettext("The cage got another birth date meanwhile."))
    elif name == "litter_genotyping" and target is not None:
        saw("cage", target)
        cursor = op.get("cursor") if isinstance(op.get("cursor"), int) else None
        if cursor is not None:
            newer = s.scalar(select(LitterRecord).join(MouseRecord, MouseRecord.litter_id_fk == LitterRecord.id)
                             .where(MouseRecord.cage_id_fk == target.id, LitterRecord.sync_version > cursor)
                             .order_by(LitterRecord.id.desc()).limit(1))
            if newer is not None and newer.created_at and (_when(op.get("made_at")) or datetime.utcnow()) <= newer.created_at + timedelta(days=1):
                clash("cage", target, {"litter": {"saw": None, "now": newer.litter_id, "mine": fields.get("pups")}},
                      gettext("A litter was recorded in this cage meanwhile; this would make a second set of pups."))
    elif name == "wean" and target is not None:
        saw("cage", target)
        for row in fields.get("rows") or []:
            for ref in (row.get("mice") or []) if isinstance(row, dict) else []:
                m = actions.find(s, "mouse", ref)
                seen = base.get(f"mouse:{m.id}")
                if isinstance(seen, dict) and "cage" in seen and seen["cage"] != m.cage_id_fk:
                    by, at = _who_changed(s, "mouse", m)
                    raise actions.ActionError(gettext("%(mouse)s was moved meanwhile (%(by)s, %(at)s): nothing left to wean there.",
                                                      mouse=actions._mouse_label(m), by=by or "?", at=at or "?"))
    elif name == "mouse_weight" and target is not None:
        cursor = op.get("cursor") if isinstance(op.get("cursor"), int) else None
        day = fields.get("date") or date.today().isoformat()
        there = s.scalar(select(MouseWeight).where(MouseWeight.mouse_id_fk == target.id,
                                                   MouseWeight.weigh_date == date.fromisoformat(day)))
        if there is not None and cursor is not None and there.sync_version > cursor \
                and (there.recorded_by or "") != user.username and _plain(there.grams) != _plain(fields.get("grams")):
            clash("mouse", target, {"grams": {"saw": None, "now": there.grams, "mine": fields.get("grams")}},
                  gettext("Someone else recorded a weight for this mouse that day."))
    else:
        if target is not None:
            saw(tkind, target)
    change = {**change, "fields": fields}
    if clashes:
        return "clash", clashes, change, ""
    return ("merged" if moved_on else "fits"), [], change, ""


def _apply(state: _Push, op: dict, op_id: str, change: dict) -> dict:
    user = state.user
    with SessionLocal() as s:
        try:
            verdict, clashes, change, message = _check(s, user, op, change)
        except actions.ActionError as exc:
            return _answer(op_id, "refused", str(exc))
        if verdict == "done":
            return _answer(op_id, "applied", message)
        if verdict == "clash":
            act = actions.CATALOGUE[change["action"]]
            try:
                target = actions._target(s, act, change) if act.target is not None else None
                summary = act.describe(s, target, actions.clean(act, change))
            except actions.ActionError:
                summary = act.title
            first = clashes[0]
            held = HeldChange(user_id_fk=user.id, action=change["action"], table_name=first["kind"],
                              record_id=first["id"], record_label=first["label"][:200],
                              change_json=json.dumps({**change, "creates": op.get("creates") or {},
                                                      "summary": summary}),
                              clash_json=json.dumps({"records": clashes}), message=first.get("message", ""),
                              made_at=_when(op.get("made_at")))
            s.add(held)
            s.commit()
            return _answer(op_id, "held", gettext("Needs your decision: it was changed meanwhile."), held=held.id,
                           creates=op.get("creates") or {})
    outcome = actions.run(current_app._get_current_object(), user.username, [change], apply=True,
                          label=gettext("Done offline"), description=_described(op, user),
                          lang=g.get("lang") or "en", source="offline")
    result = outcome.changes[0] if outcome.changes else None
    if not outcome.ok:
        errors = (result.errors if result else []) or [gettext("The page refused it.")]
        return _answer(op_id, "refused", " ".join(errors), creates=op.get("creates") or {})
    ids = _made(op, result.records if result else [])
    state.ids.update(ids)
    return _answer(op_id, "merged" if verdict == "merged" else "applied",
                   " ".join((result.warnings if result else []) + (result.said if result else [])),
                   ids=ids, batch=outcome.batch_id)


def _described(op: dict, user) -> str:
    """For Batch history: done offline, when, on which device."""
    device = g.get("device")
    made = _when(op.get("made_at"))
    where = device.name if isinstance(device, LabDevice) and device.name else gettext("a device")
    if made:
        return gettext("Done offline on %(device)s at %(when)s (UTC)", device=where,
                       when=made.isoformat(sep=" ", timespec="minutes"))
    return gettext("Done offline on %(device)s", device=where)


def _made(op: dict, records: list[dict]) -> dict:
    """The temporary ids of what a change made → the records it became, in
    the order they were made."""
    creates = op.get("creates") if isinstance(op.get("creates"), dict) else {}
    ids = {}
    for kind, temps in creates.items():
        if kind not in ROWS or not isinstance(temps, list):
            continue
        table = ROWS[kind][1]
        made = sorted({r["id"] for r in records if r.get("table") == table and r.get("action") == "create"})
        for tmp, row_id in zip(temps, made):
            if isinstance(tmp, str) and TMP.match(tmp):
                ids[tmp] = {"kind": kind, "id": row_id}
    return ids


# ---------------------------------------------------------------- held changes

def _held_rows(s, user, everyone: bool = False) -> list[HeldChange]:
    q = select(HeldChange).where(HeldChange.status == "open").order_by(HeldChange.created_at)
    if not (everyone and user.role == "admin"):
        q = q.where(HeldChange.user_id_fk == user.id)
    return list(s.scalars(q))


# A field's name as the pages call it (Needs your decision).
FIELD_LABELS = {"status": "Status", "date_give_birth": "Litter born", "cage": "Cage", "notes": "Notes", "note": "Note",
                "sex": "Sex", "owner": "Owner", "date_of_death": "Date of death", "grams": "Weight",
                "litter": "Litter", "transgenes": "Transgenes", "purpose": "Purpose", "line": "Line",
                "genotype": "Genotype"}


def held_card(s, held: HeldChange, as_json: bool = False) -> dict:
    change = json.loads(held.change_json or "{}")
    who = s.get(UserAccount, held.user_id_fk)
    when = _d if as_json else (lambda v: v)
    records = json.loads(held.clash_json or "{}").get("records", [])
    if not as_json:
        for r in records:
            r["fields"] = {FIELD_LABELS.get(k, k.replace("_", " ")): f for k, f in r["fields"].items()}
            try:
                r["at"] = datetime.fromisoformat(r["at"]) if r.get("at") else None
            except ValueError:
                r["at"] = None
    return {"id": held.id, "action": held.action, "summary": change.get("summary") or held.action,
            "made_at": when(held.made_at), "received": when(held.created_at), "message": held.message,
            "user": who.username if who else "", "records": records, "link": _link(held)}


def _link(held: HeldChange) -> str:
    if held.table_name == "cage":
        return url_for("colony", view="cages", scope="all") + f"#cage-{held.record_id}"
    if held.table_name == "mouse":
        return url_for("colony", view="mice", scope="all") + f"#mouse-{held.record_id}"
    if held.table_name == "task":
        return url_for("calendar")
    return url_for("home_dashboard")


def decide(held_id: int, decision: str, user) -> tuple[bool, str]:
    """Keep mine (make the change now, as a normal edit), Keep theirs (drop
    it), or Edit (drop it: the person sets the record by hand)."""
    if decision not in ("mine", "theirs", "edit"):
        return False, gettext("Choose Keep mine or Keep theirs.")
    with SessionLocal() as s:
        held = s.get(HeldChange, held_id)
        if held is None or held.status != "open":
            return False, gettext("That change was decided already.")
        if held.user_id_fk != user.id and user.role != "admin":
            return False, gettext("Only its person, or an admin, can decide this.")
        change = json.loads(held.change_json or "{}")
        maker = s.get(UserAccount, held.user_id_fk)
        acting = maker.username if maker is not None and not maker.disabled else user.username
    batch = None
    if decision == "mine":
        outcome = actions.run(current_app._get_current_object(), acting,
                              [{k: change[k] for k in ("action", "target", "fields") if k in change}], apply=True,
                              label=gettext("Done offline"),
                              description=gettext("Kept from a change made offline (decided by %(who)s)",
                                                  who=user.username),
                              lang=g.get("lang") or "en", source="offline")
        if not outcome.ok:
            errors = outcome.changes[0].errors if outcome.changes else []
            return False, " ".join(errors) or gettext("The page refused it.")
        batch = outcome.batch_id
    with SessionLocal() as s:
        held = s.get(HeldChange, held_id)
        held.status = {"mine": "mine", "theirs": "theirs", "edit": "edited"}[decision]
        held.decided_by, held.decided_at = user.username, datetime.utcnow()
        if held.op_id_fk:
            op = s.get(SyncOp, held.op_id_fk)
            if op is not None:
                answer = json.loads(op.result_json or "{}")
                answer["decided"] = held.status
                if batch:
                    answer["batch"] = batch
                op.result_json = json.dumps(answer)
        s.commit()
    return True, {"mine": gettext("Your change was made."), "theirs": gettext("Their change stays; yours was dropped."),
                  "edit": gettext("Set it by hand: your offline change was dropped.")}[decision]


@bp.route("/api/sync/held")
def held_list():
    user = _user()
    with SessionLocal() as s:
        return jsonify({"ok": True, "held": [held_card(s, h, as_json=True) for h in _held_rows(s, user)]})


@bp.route("/api/sync/held/<int:held_id>", methods=["POST"])
def held_decide(held_id: int):
    user = _user()
    decision = str((request.get_json(silent=True) or {}).get("decision") or request.form.get("decision") or "")
    ok, message = decide(held_id, decision, user)
    return jsonify({"ok": ok, "message" if ok else "error": message}), (200 if ok else 409)


# ---------------------------------------------------------------- Needs your decision, on the web

def _signed_in():
    if g.get("user") is None:
        abort(redirect(url_for("login", next=request.path)))


@bp.route("/decisions")
def page():
    _signed_in()
    user = g.user
    everyone = request.args.get("everyone") == "1" and user.role == "admin"
    with SessionLocal() as s:
        cards = [held_card(s, h) for h in _held_rows(s, user, everyone)]
        refused = list(s.scalars(select(SyncOp).where(
            SyncOp.user_id_fk == user.id, SyncOp.result == "refused",
            SyncOp.received_at >= datetime.utcnow() - timedelta(days=30)).order_by(SyncOp.id.desc()).limit(50)))
        others = s.scalar(select(func.count(HeldChange.id)).where(
            HeldChange.status == "open", HeldChange.user_id_fk != user.id)) if user.role == "admin" else 0
    return render_template("sync/decisions.html", cards=cards, refused=refused, everyone=everyone,
                           others=others or 0)


@bp.route("/decisions/<int:held_id>", methods=["POST"])
def page_decide(held_id: int):
    _signed_in()
    decision = request.form.get("decision", "")
    with SessionLocal() as s:
        held = s.get(HeldChange, held_id)
        link = _link(held) if held is not None else ""
    ok, message = decide(held_id, decision, g.user)
    flash(message, "success" if ok else "error")
    if ok and decision == "edit" and link:
        return redirect(link)
    return redirect(url_for("sync.page", everyone=request.form.get("everyone") or None))


@bp.app_context_processor
def inject_held():
    """Needs your decision in the account menu, once something is held."""
    user = g.get("user")
    if user is None:
        return {"held_pending": 0, "held_overdue": False}
    with SessionLocal() as s:
        count, oldest = s.execute(select(func.count(HeldChange.id), func.min(HeldChange.created_at)).where(
            HeldChange.user_id_fk == user.id, HeldChange.status == "open")).one()
    # Home reminds them once one has waited three days.
    return {"held_pending": count or 0,
            "held_overdue": bool(oldest and oldest <= datetime.utcnow() - REMIND_AFTER)}


def pending(s, user) -> int:
    """Held changes waiting for this person (the account menu's badge)."""
    if user is None:
        return 0
    return s.scalar(select(func.count(HeldChange.id)).where(HeldChange.user_id_fk == user.id,
                                                            HeldChange.status == "open")) or 0


def forget_old(s) -> None:
    """Results of changes taken in are kept 90 days, to answer a resend."""
    s.query(SyncOp).filter(SyncOp.received_at < datetime.utcnow() - timedelta(days=90),
                           SyncOp.result != "held").delete(synchronize_session=False)
