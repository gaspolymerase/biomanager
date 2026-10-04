"""The public API: the lab's records as JSON, for scripts and other tools.

    curl -H "Authorization: Bearer bmt_…" https://SERVER/api/v1/mice?alive=true

**Tokens.** Each person makes their own under Settings → API tokens (a
member only if Lab setup allows it; not a guest). A token acts as its
owner, with exactly their permissions, and is either *read* or *write*
(read and change). Only its SHA-256 is kept; it is shown once. It can
expire, and its owner or an admin can revoke it. Changes made with one are
in the change history as the person, marked "[API: <token's name>]".

**Only the token.** /api/v1 never looks at the session cookie, so another
website can't make a signed-in browser call it; for the same reason the
cross-site check (security.py) does not apply here. No cookie is set.

**Shape.** Lists are `{"data": [...], "next": url | null}`, at most
`limit` (default 100, up to 1000) a page; follow `next` for the rest.
Errors are `{"error": "..."}` with the HTTP status. Dates are ISO 8601.
600 requests a minute per token.

Writes go through the same checks as the pages (app.populate_mouse_from_form,
inventory_routes._item_from_form, stock_routes._unit_from_form,
experiments.set_reading), so the API can't do what the person couldn't.
Every endpoint is in ENDPOINTS, which also makes /api/v1/openapi.json and
the reference page at /api.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from flask import (Blueprint, abort, flash, g, get_flashed_messages, jsonify, redirect, render_template, request,
                   url_for)
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import selectinload
from werkzeug.datastructures import MultiDict

from .formutil import like_pattern
from . import access, groups, lab
from .db import SessionLocal
from .i18n import gettext
from .models import (ApiToken, CageRecord, Experiment, FishRecord, InventoryItem, InventoryRack, LitterRecord,
                     MouseRecord, MouseWeight, PlasmidRecord, StockRack, StockUnit, StrainRecord, TankRecord,
                     UserAccount)

bp = Blueprint("api", __name__, url_prefix="/api/v1")
pages = Blueprint("api_pages", __name__)

VERSION = "1"
TOKEN_PREFIX = "bmt_"
ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PERMISSION = "members_api_tokens"
SCOPES = {"read": "Read", "write": "Read and change"}
EXPIRY_DAYS = {"30": "In 30 days", "90": "In 90 days", "365": "In a year", "": "Never"}
PER_MINUTE = 600
MAX_LIMIT = 1000


# ---------------------------------------------------------------- tokens

def new_token() -> str:
    return TOKEN_PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(40))


def token_hash(raw: str) -> str:
    return hashlib.sha256((raw or "").strip().encode()).hexdigest()


def may_make_tokens(session, user) -> bool:
    if user is None or user.disabled or user.role == "pending" or user.expires_at is not None:
        return False          # a guest's account ends; a token shouldn't outlive it
    return user.role == "admin" or lab.permission(session, PERMISSION)


def authenticate() -> None:
    """For /api/v1 (called from app.load_current_user): the person whose
    token is in the Authorization header, and never a session cookie."""
    g.user = None
    g.api_token = None
    header = request.headers.get("Authorization", "")
    raw = header[7:].strip() if header[:7].lower() == "bearer " else ""
    if not raw.startswith(TOKEN_PREFIX):
        return
    now = datetime.utcnow()
    with SessionLocal() as s:
        tok = s.scalar(select(ApiToken).where(ApiToken.token_hash == token_hash(raw)))
        if tok is None or tok.revoked_at is not None or (tok.expires_at is not None and tok.expires_at <= now):
            return
        user = s.get(UserAccount, tok.user_id_fk)
        if user is None or not may_make_tokens(s, user):
            return
        g.api_token = SimpleNamespace(id=tok.id, label=tok.label, scope=tok.scope)
        s.expunge(user)
        s.execute(update(ApiToken).where(ApiToken.id == tok.id)
                  .values(last_used_at=now, uses=ApiToken.uses + 1))
        s.commit()
    g.user = user
    # The change history says the change came through this token.
    g.audit_batch = f"API: {g.api_token.label}"


class _Rate:
    """At most PER_MINUTE requests in any minute, per token, per worker."""

    def __init__(self):
        self._seen: dict[int, deque] = {}
        self._lock = threading.Lock()

    def wait(self, key: int) -> int:
        now = time.monotonic()
        with self._lock:
            q = self._seen.setdefault(key, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= PER_MINUTE:
                return int(60 - (now - q[0])) + 1
            q.append(now)
            return 0

    def reset(self):
        with self._lock:
            self._seen.clear()


rate = _Rate()


def _error(status: int, message: str, **extra):
    return jsonify({"error": message, **extra}), status


@bp.before_request
def _gate():
    if g.get("user") is None or g.get("api_token") is None:
        return _error(401, "Send an API token: Authorization: Bearer bmt_… Make one in Settings → API tokens.")
    wait = rate.wait(g.api_token.id)
    if wait:
        return _error(429, f"Too many requests: at most {PER_MINUTE} a minute.") + ({"Retry-After": str(wait)},)
    if request.method not in ("GET", "HEAD", "OPTIONS") and g.api_token.scope != "write":
        return _error(403, "This token can only read. Make a read-and-change token to make changes.")
    return None


@bp.after_request
def _no_cookie(response):
    response.headers.pop("Set-Cookie", None)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.errorhandler(400)
def _bad(e):
    return _error(400, getattr(e, "description", "") or "Bad request.")


@bp.errorhandler(403)
def _forbidden(e):
    return _error(403, getattr(e, "description", "") or "Not allowed.")


@bp.errorhandler(404)
def _missing(e):
    return _error(404, getattr(e, "description", "") or "Not found.")


@bp.errorhandler(405)
def _method(_e):
    return _error(405, "That method isn't allowed here.")


# ---------------------------------------------------------------- helpers

def _json() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Send a JSON object (Content-Type: application/json).")
    return data


def _int_arg(name: str, default: int, lo: int, hi: int | None = None) -> int:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        abort(400, f"{name} must be a whole number.")
    return max(lo, min(hi, value) if hi is not None else value)


def _bool_arg(name: str) -> bool | None:
    raw = (request.args.get(name) or "").strip().lower()
    if raw == "":
        return None
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    abort(400, f"{name} must be true or false.")


def _since() -> datetime | None:
    raw = (request.args.get("updated_since") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        abort(400, "updated_since must be a date or a date and time (2026-09-01 or 2026-09-01T08:00:00).")


def _day(value) -> str | None:
    return value.isoformat() if value else None


def _stamp(value) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def _feature(key: str) -> None:
    if not lab.request_features().get(key, True):
        abort(404, f"{lab.FEATURES[key].label} is switched off for this lab.")


def _page(session, stmt, model, serialize):
    """One page of a list, by row id: {"data": [...], "next": url | null}."""
    limit = _int_arg("limit", 100, 1, MAX_LIMIT)
    after = _int_arg("after", 0, 0)
    rows = session.scalars(stmt.where(model.id > after).order_by(model.id).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    following = None
    if more:
        args = {**request.args.to_dict(), "after": rows[-1].id}
        following = url_for(request.endpoint, **(request.view_args or {}), **args, _external=True)
    return jsonify({"data": [serialize(r) for r in rows], "next": following})


def _where(rack, row, col) -> str:
    from . import positions
    if rack is None:
        return ""
    if row and col:
        return positions.label(row, col, rack.naming, rack.cols)
    return ""


def _warnings() -> list[str]:
    return [text for _cat, text in get_flashed_messages(with_categories=True)]


# ---------------------------------------------------------------- who am I

@bp.get("")
def index():
    return jsonify({"api": VERSION, "user": g.user.username, "scope": g.api_token.scope,
                    "openapi": url_for("api.openapi", _external=True),
                    "reference": url_for("api_pages.reference", _external=True),
                    "endpoints": [f"{m} {p}" for m, p, *_ in ENDPOINTS]})


@bp.get("/me")
def me():
    u = g.user
    return jsonify({"username": u.username, "name": u.display_name or u.username, "role": u.role,
                    "token": {"name": g.api_token.label, "scope": g.api_token.scope}})


# ---------------------------------------------------------------- mice

def mouse_json(m: MouseRecord) -> dict:
    return {
        "mouse_id": m.mouse_id,
        "sex": m.gender or "",
        "genotype": m.genotype or "",
        "transgenes": [t for t in (m.transgene_1, m.transgene_2, m.transgene_3, m.transgene_4) if t],
        "status": m.status or "",
        "alive": m.date_of_death is None,
        "date_of_birth": _day(m.litter.date_of_birth) if m.litter else None,
        "date_of_death": _day(m.date_of_death),
        "cage": m.cage.cage_id if m.cage else None,
        "litter": m.litter.litter_id if m.litter else None,
        "owner": m.owner or "",
        "note": m.note or "",
        "created_at": _stamp(m.created_at),
        "updated_at": _stamp(m.updated_at),
        "updated_by": m.updated_by or "",
    }


def weight_json(w: MouseWeight) -> dict:
    return {"date": _day(w.weigh_date), "grams": w.grams, "notes": w.notes or "", "recorded_by": w.recorded_by or ""}


def _mouse(session, mouse_id: int) -> MouseRecord:
    _feature("colony")
    m = session.scalar(select(MouseRecord).options(selectinload(MouseRecord.cage), selectinload(MouseRecord.litter))
                       .where(MouseRecord.mouse_id == mouse_id))
    if m is None:
        abort(404, f"There is no mouse {mouse_id}.")
    return m


@bp.get("/mice")
def mice():
    _feature("colony")
    with SessionLocal() as s:
        stmt = select(MouseRecord).options(selectinload(MouseRecord.cage), selectinload(MouseRecord.litter))
        alive = _bool_arg("alive")
        if alive is not None:
            stmt = stmt.where(MouseRecord.date_of_death.is_(None) if alive else MouseRecord.date_of_death.is_not(None))
        if request.args.get("cage"):
            stmt = stmt.where(MouseRecord.cage_id_fk.in_(select(CageRecord.id).where(CageRecord.cage_id == request.args["cage"])))
        if request.args.get("litter"):
            stmt = stmt.where(MouseRecord.litter_id_fk.in_(
                select(LitterRecord.id).where(LitterRecord.litter_id == request.args["litter"])))
        for name in ("owner", "status"):
            if request.args.get(name):
                stmt = stmt.where(func.lower(getattr(MouseRecord, name)) == request.args[name].strip().lower())
        if request.args.get("sex"):
            stmt = stmt.where(MouseRecord.gender == request.args["sex"].strip().upper())
        if request.args.get("genotype"):
            stmt = stmt.where(MouseRecord.genotype.ilike(like_pattern(request.args['genotype'].strip()), escape="\\"))
        since = _since()
        if since:
            stmt = stmt.where(or_(MouseRecord.updated_at >= since, MouseRecord.created_at >= since))
        return _page(s, stmt, MouseRecord, mouse_json)


@bp.get("/mice/<int:mouse_id>")
def mouse(mouse_id: int):
    with SessionLocal() as s:
        m = _mouse(s, mouse_id)
        weights = s.scalars(select(MouseWeight).where(MouseWeight.mouse_id_fk == m.id).order_by(MouseWeight.weigh_date))
        return jsonify({**mouse_json(m), "weights": [weight_json(w) for w in weights]})


MOUSE_FIELDS = ("sex", "genotype", "transgenes", "status", "owner", "note", "cage", "litter", "date_of_death")


@bp.patch("/mice/<int:mouse_id>")
def mouse_update(mouse_id: int):
    """Change a mouse: any of MOUSE_FIELDS, the rest left as they are."""
    from .app import (can_edit_mouse, create_transfer_copy, current_lab_usernames, populate_mouse_from_form,
                      stamp_updated)
    from .services import mouse_display_row, split_genotype
    data = _json()
    unknown = sorted(set(data) - set(MOUSE_FIELDS))
    if unknown:
        abort(400, f"A mouse has no {', '.join(unknown)} to change; it has {', '.join(MOUSE_FIELDS)}.")
    with SessionLocal() as s:
        m = _mouse(s, mouse_id)
        if not can_edit_mouse(m):
            abort(403, access.reason_denied(m))
        row = mouse_display_row(m)
        form = MultiDict({"gender": row["gender"] or "", "status": row["status"] or "", "owner": row["owner"] or "",
                          "note": row["note"] or "",
                          **{f"transgene_{i}": row[f"transgene_{i}"] or "" for i in range(1, 5)}})
        if "sex" in data:
            sex = str(data["sex"] or "").strip().upper()
            if sex not in ("F", "M", ""):
                abort(400, "sex is F, M or empty.")
            form["gender"] = sex
        if "genotype" in data or "transgenes" in data:
            parts = data["transgenes"] if "transgenes" in data else split_genotype(str(data["genotype"] or ""))
            if not isinstance(parts, list) or len(parts) > 4:
                abort(400, "transgenes is a list of at most four.")
            for i in range(1, 5):
                form[f"transgene_{i}"] = str(parts[i - 1]).strip() if i <= len(parts) else ""
        for key in ("status", "note"):
            if key in data:
                form[key] = str(data[key] or "")
        if "owner" in data:
            owner = str(data["owner"] or "").strip()
            if owner and owner not in set(current_lab_usernames(s)):
                abort(400, f"“{owner}” is not a lab member.")
            form["owner"] = owner
        if "cage" in data:
            form["cage_id"] = str(data["cage"] or "")
        if "litter" in data:
            form["litter_id"] = str(data["litter"] or "")
        if "date_of_death" in data:
            raw = str(data["date_of_death"] or "")
            try:
                if raw and date.fromisoformat(raw[:10]) > date.today():
                    abort(400, "date_of_death can't be in the future.")
            except ValueError:
                abort(400, "date_of_death is a date, 2026-09-28.")
            form["date_of_death"] = raw[:10]
        recipient, sender = populate_mouse_from_form(s, m, form)
        if recipient:
            create_transfer_copy(s, m, recipient, sender)
        stamp_updated(m)
        s.commit()
        m = _mouse(s, mouse_id)
        return jsonify({**mouse_json(m), "warnings": _warnings()})


@bp.get("/mice/<int:mouse_id>/weights")
def weights(mouse_id: int):
    with SessionLocal() as s:
        m = _mouse(s, mouse_id)
        rows = s.scalars(select(MouseWeight).where(MouseWeight.mouse_id_fk == m.id).order_by(MouseWeight.weigh_date))
        return jsonify({"data": [weight_json(w) for w in rows], "next": None})


@bp.post("/mice/<int:mouse_id>/weights")
def weigh(mouse_id: int):
    """{"grams": 24.1, "date": "2026-09-28" (today if left out), "notes": ""}:
    one weight a day, so the same day again replaces it."""
    from .app import can_edit_mouse
    data = _json()
    try:
        grams = float(data.get("grams"))
    except (TypeError, ValueError):
        abort(400, "grams must be a number.")
    if not 0 < grams < 2000:
        abort(400, "grams must be more than 0 and less than 2000.")
    try:
        on = date.fromisoformat(str(data.get("date"))[:10]) if data.get("date") else date.today()
    except ValueError:
        abort(400, "date is a date, 2026-09-28.")
    if on > date.today():
        abort(400, "A weight can't be for a day still to come.")
    notes = str(data.get("notes") or "").strip()[:500]
    with SessionLocal() as s:
        m = _mouse(s, mouse_id)
        if not can_edit_mouse(m):
            abort(403, access.reason_denied(m))
        w = s.scalar(select(MouseWeight).where(MouseWeight.mouse_id_fk == m.id, MouseWeight.weigh_date == on))
        created = w is None
        if created:
            w = MouseWeight(mouse_id_fk=m.id, weigh_date=on, grams=grams, notes=notes, recorded_by=g.user.username)
            s.add(w)
        else:
            w.grams, w.recorded_by = grams, g.user.username
            if notes:
                w.notes = notes
        s.commit()
        return jsonify(weight_json(w)), 201 if created else 200


# ---------------------------------------------------------------- cages, litters, strains

def cage_json(c: CageRecord) -> dict:
    living = sorted(m.mouse_id for m in c.mice if m.date_of_death is None)
    return {
        "cage_id": c.cage_id, "purpose": c.purpose or "", "owner": c.owner or "", "shared": access.is_shared_cage(c),
        "shared_with": (groups.name_of(c.share_group_id) or None)
        if access.is_shared_cage(c) and c.share_group_id else None,
        "room": c.room or "", "rack": c.rack.name if c.rack else None,
        "position": _where(c.rack, c.rack_row, c.rack_col) or None, "location": c.cage_location or "",
        "card_id": c.card_id or "", "notes": c.notes or "", "mice": living, "litter_born": _day(c.date_give_birth),
        "created_at": _stamp(c.created_at),
    }


@bp.get("/cages")
def cages():
    _feature("colony")
    with SessionLocal() as s:
        stmt = select(CageRecord).options(selectinload(CageRecord.mice), selectinload(CageRecord.rack))
        occupied = _bool_arg("occupied")
        living = select(MouseRecord.cage_id_fk).where(MouseRecord.date_of_death.is_(None))
        if occupied is not None:
            stmt = stmt.where(CageRecord.id.in_(living) if occupied else CageRecord.id.not_in(living))
        for name in ("owner", "purpose", "room"):
            if request.args.get(name):
                stmt = stmt.where(func.lower(getattr(CageRecord, name)) == request.args[name].strip().lower())
        return _page(s, stmt, CageRecord, cage_json)


@bp.get("/cages/<cage_id>")
def cage(cage_id: str):
    _feature("colony")
    with SessionLocal() as s:
        c = s.scalar(select(CageRecord).options(selectinload(CageRecord.mice), selectinload(CageRecord.rack))
                     .where(CageRecord.cage_id == cage_id))
        if c is None:
            abort(404, f"There is no cage {cage_id}.")
        return jsonify(cage_json(c))


def litter_json(lt: LitterRecord) -> dict:
    return {"litter_id": lt.litter_id, "date_of_birth": _day(lt.date_of_birth), "weaned_on": _day(lt.weaned_on),
            "cohort": lt.cohort_name or "", "father": lt.father_info or "", "mother": lt.mother_info or "",
            "total_pups": lt.total_pups, "notes": lt.notes or "", "mice": sorted(m.mouse_id for m in lt.mice)}


@bp.get("/litters")
def litters():
    _feature("colony")
    with SessionLocal() as s:
        stmt = select(LitterRecord).options(selectinload(LitterRecord.mice))
        if request.args.get("born_since"):
            try:
                stmt = stmt.where(LitterRecord.date_of_birth >= date.fromisoformat(request.args["born_since"][:10]))
            except ValueError:
                abort(400, "born_since is a date, 2026-09-01.")
        return _page(s, stmt, LitterRecord, litter_json)


@bp.get("/strains")
def strains():
    _feature("colony")
    with SessionLocal() as s:
        return _page(s, select(StrainRecord), StrainRecord, lambda r: {
            "number": r.strain_number or "", "name": r.strain_name or "", "background": r.strain_background or "",
            "supplier": r.supplier or "", "description": r.description or ""})


# ---------------------------------------------------------------- zebrafish

def tank_json(t: TankRecord) -> dict:
    from .app import fish_position_label
    alive = [f for f in t.fish if (f.status or "alive") == "alive"]
    return {"tank_id": t.tank_id, "line": t.line.name if t.line else None, "purpose": t.purpose or "",
            "owner": t.owner or "", "active": bool(t.active), "rack": t.rack.name if t.rack else None,
            "position": (fish_position_label(t) or None) if t.rack else None, "fish": sum(f.count or 0 for f in alive),
            "genotypes": sorted({f.genotype for f in alive if f.genotype}), "notes": t.notes or ""}


@bp.get("/tanks")
def tanks():
    _feature("zebrafish")
    with SessionLocal() as s:
        stmt = select(TankRecord).options(selectinload(TankRecord.fish), selectinload(TankRecord.line),
                                          selectinload(TankRecord.rack))
        active = _bool_arg("active")
        if active is not None:
            stmt = stmt.where(TankRecord.active.is_(active))
        if request.args.get("owner"):
            stmt = stmt.where(TankRecord.owner == request.args["owner"])
        return _page(s, stmt, TankRecord, tank_json)


@bp.get("/fish")
def fish():
    _feature("zebrafish")
    with SessionLocal() as s:
        stmt = select(FishRecord).options(selectinload(FishRecord.tank))
        if request.args.get("status"):
            stmt = stmt.where(FishRecord.status == request.args["status"])
        if request.args.get("tank"):
            stmt = stmt.where(FishRecord.tank_id_fk.in_(select(TankRecord.id).where(TankRecord.tank_id == request.args["tank"])))
        return _page(s, stmt, FishRecord, lambda f: {
            "id": f.id, "tank": f.tank.tank_id if f.tank else None, "individual_id": f.individual_id or "",
            "count": f.count, "sex": f.sex or "", "status": f.status or "", "genotype": f.genotype or "",
            "date_of_fertilization": _day(f.date_of_fertilization), "notes": f.notes or ""})


# ---------------------------------------------------------------- plasmids

def plasmid_json(p: PlasmidRecord, sequence: bool = False) -> dict:
    out = {"plasmid_id": p.plasmid_id, "name": p.name or "", "backbone": p.backbone or "", "insert": p.insert_seq or "",
           "resistance": p.resistance or "", "owner": p.owner or "", "location": p.location or "",
           "concentration": p.concentration or "", "a260_280": p.a260_280 or "", "shared": bool(p.is_shared),
           "shared_with": (groups.name_of(p.share_group_id) or None) if p.is_shared and p.share_group_id else None,
           "box": p.storage_box or "", "notes": p.notes or "", "has_sequence": bool(p.full_sequence),
           "updated_at": _stamp(p.updated_at)}
    if sequence:
        out.update(sequence=p.full_sequence or "", circular=bool(p.is_circular))
    return out


@bp.get("/plasmids")
def plasmids():
    _feature("plasmids")
    with SessionLocal() as s:
        stmt = select(PlasmidRecord)
        if request.args.get("q"):
            q = like_pattern(request.args['q'].strip())
            stmt = stmt.where(or_(PlasmidRecord.name.ilike(q, escape="\\"), PlasmidRecord.insert_seq.ilike(q, escape="\\"),
                                  PlasmidRecord.backbone.ilike(q, escape="\\")))
        return _page(s, stmt, PlasmidRecord, plasmid_json)


@bp.get("/plasmids/<int:plasmid_id>")
def plasmid(plasmid_id: int):
    _feature("plasmids")
    with SessionLocal() as s:
        p = s.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == plasmid_id))
        if p is None:
            abort(404, f"There is no plasmid {plasmid_id}.")
        return jsonify(plasmid_json(p, sequence=True))


# ---------------------------------------------------------------- fly and worm stocks

def _stock_module(session, key: str):
    from . import stock_service as ssvc
    module = ssvc.get_module(session, key)
    if module is None or not lab.can_see(module):
        abort(404, f"There is no stock database {key}.")
    return module, ssvc.view(module)


def unit_json(mv, u: StockUnit) -> dict:
    return {"number": u.number, "code": mv.code(u), "genotype": u.genotype or "", "purpose": u.purpose or "",
            "active": bool(u.active), "owner": u.owner or "", "rack": u.rack.name if u.rack else None,
            "position": _where(u.rack, u.rack_row, u.rack_col) or None, "set_up_on": _day(u.set_up_on),
            "ready_on": _day(u.ready_on), "discarded_on": _day(u.discarded_on), "notes": u.notes or "",
            "female_genotype": u.female_genotype or "", "male_genotype": u.male_genotype or ""}


@bp.get("/stocks")
def stock_modules():
    from . import stock_service as ssvc
    with SessionLocal() as s:
        mods = [m for m in ssvc.list_modules(s) if lab.can_see(m)]
        return jsonify({"data": [{"key": m.key, "label": m.label, "kind": m.kind} for m in mods], "next": None})


@bp.get("/stocks/<key>/units")
def stock_units(key: str):
    with SessionLocal() as s:
        module, mv = _stock_module(s, key)
        stmt = select(StockUnit).options(selectinload(StockUnit.rack)).where(StockUnit.module_id_fk == module.id)
        active = _bool_arg("active")
        if active is not None:
            stmt = stmt.where(StockUnit.active.is_(active))
        for name in ("purpose", "owner"):
            if request.args.get(name):
                stmt = stmt.where(getattr(StockUnit, name) == request.args[name])
        if request.args.get("genotype"):
            stmt = stmt.where(StockUnit.genotype.ilike(like_pattern(request.args['genotype'].strip()), escape="\\"))
        return _page(s, stmt, StockUnit, lambda u: unit_json(mv, u))


UNIT_FIELDS = ("genotype", "female_genotype", "male_genotype", "owner", "purpose", "set_up_on", "ready_on",
               "shift_on", "score_on", "shift_to", "notes", "rack", "position")


@bp.patch("/stocks/<key>/units/<int:number>")
def stock_unit_update(key: str, number: int):
    from .stock_routes import Invalid, can_edit, _unit_from_form
    data = _json()
    unknown = sorted(set(data) - set(UNIT_FIELDS))
    if unknown:
        abort(400, f"A vial has no {', '.join(unknown)} to change; it has {', '.join(UNIT_FIELDS)}.")
    with SessionLocal() as s:
        module, mv = _stock_module(s, key)
        u = s.scalar(select(StockUnit).where(StockUnit.module_id_fk == module.id, StockUnit.number == number))
        if u is None:
            abort(404, f"There is no {mv.unit} {number}.")
        if not can_edit(u):
            abort(403, access.reason_denied(u))
        form = MultiDict({k: str(v if v is not None else "") for k, v in data.items() if k not in ("rack", "position")})
        if "rack" in data or "position" in data:
            rack_name = data.get("rack", u.rack.name if u.rack else "")
            rack = s.scalar(select(StockRack).where(StockRack.module_id_fk == module.id, StockRack.name == rack_name)) \
                if rack_name else None
            if rack_name and rack is None:
                abort(400, f"There is no {mv.rack_noun} {rack_name}.")
            form["rack_id"] = str(rack.id) if rack else ""
            form["position"] = str(data.get("position") or "")
        try:
            problem = _unit_from_form(s, mv, u, form)
        except Invalid as refused:
            s.rollback()
            abort(400, str(refused))
        s.commit()
        s.refresh(u)
        return jsonify({**unit_json(mv, u), "warnings": [problem] if problem else []})


# ---------------------------------------------------------------- inventories

def _inventory(session, key: str):
    from . import inventory_service as isvc
    module = isvc.get_module(session, key)
    if module is None or not lab.can_see(module):
        abort(404, f"There is no inventory {key}.")
    return module, isvc.view(module)


def item_json(mv, i: InventoryItem) -> dict:
    from . import inventory_service as isvc
    attrs = i.attrs_dict
    return {"number": i.number, "name": i.name or "", "category": i.category or "", "status": i.status or "",
            "owner": i.owner or "", "shared": bool(i.is_shared),
            "shared_with": (groups.name_of(i.share_group_id) or None) if i.is_shared and i.share_group_id else None,
            "quantity": i.quantity or "", "unit": i.unit or "",
            "vendor": i.vendor or "", "catalog_number": i.catalog_number or "", "lot": i.lot or "",
            "box": i.rack.name if i.rack else None, "position": (isvc.rack_label(i) or None) if i.rack else None,
            "location_note": i.location_note or "", "received_on": _day(i.received_on),
            "expires_on": _day(i.expires_on), "notes": i.notes or "",
            "fields": {f["key"]: attrs.get(f["key"], "") for f in mv.fields},
            "updated_at": _stamp(i.updated_at), "updated_by": i.updated_by or ""}


@bp.get("/inventories")
def inventories():
    from . import inventory_service as isvc
    with SessionLocal() as s:
        mods = [m for m in isvc.list_modules(s) if lab.can_see(m)]
        out = []
        for m in mods:
            mv = isvc.view(m)
            out.append({"key": m.key, "label": m.label, "kind": m.kind, "noun": mv.item_noun,
                        "statuses": list(mv.statuses),
                        "fields": [{"key": f["key"], "label": f["label"], "type": f["type"]} for f in mv.fields]})
        return jsonify({"data": out, "next": None})


@bp.get("/inventories/<key>/items")
def inventory_items(key: str):
    with SessionLocal() as s:
        module, mv = _inventory(s, key)
        stmt = select(InventoryItem).options(selectinload(InventoryItem.rack)).where(InventoryItem.module_id_fk == module.id)
        for name in ("status", "category", "owner"):
            if request.args.get(name):
                stmt = stmt.where(func.lower(getattr(InventoryItem, name)) == request.args[name].strip().lower())
        if request.args.get("q"):
            stmt = stmt.where(InventoryItem.name.ilike(like_pattern(request.args['q'].strip()), escape="\\"))
        since = _since()
        if since:
            stmt = stmt.where(or_(InventoryItem.updated_at >= since, InventoryItem.created_at >= since))
        return _page(s, stmt, InventoryItem, lambda i: item_json(mv, i))


def _item(session, module, mv, number: int) -> InventoryItem:
    i = session.scalar(select(InventoryItem).options(selectinload(InventoryItem.rack))
                       .where(InventoryItem.module_id_fk == module.id, InventoryItem.number == number))
    if i is None:
        abort(404, f"There is no {mv.item_noun} {number}.")
    return i


@bp.get("/inventories/<key>/items/<int:number>")
def inventory_item(key: str, number: int):
    with SessionLocal() as s:
        module, mv = _inventory(s, key)
        return jsonify(item_json(mv, _item(s, module, mv, number)))


ITEM_FIELDS = ("name", "category", "status", "owner", "shared", "quantity", "unit", "vendor", "catalog_number",
               "lot", "box", "position", "location_note", "received_on", "expires_on", "notes", "fields")


def _item_form(session, module, mv, data: dict, item: InventoryItem | None) -> MultiDict:
    unknown = sorted(set(data) - set(ITEM_FIELDS))
    if unknown:
        abort(400, f"A {mv.item_noun} has no {', '.join(unknown)}; it has {', '.join(ITEM_FIELDS)}.")
    form = MultiDict()
    for k, v in data.items():
        if k in ("box", "position", "fields", "shared"):
            continue
        form[k] = "" if v is None else str(v)
    if "shared" in data:
        # true keeps a project group's stock its group's (app/groups.py).
        keep = item is not None and data["shared"] and item.is_shared
        form["is_shared"] = groups.record_value(item) if keep else ("1" if data["shared"] else "0")
    known = {f["key"]: f for f in mv.fields}
    for k, v in (data.get("fields") or {}).items():
        field = known.get(k)
        if field is None:
            abort(400, f"{module.label} has no field {k}; it has {', '.join(known) or 'none'}.")
        if field["type"] == "source":
            v = v if isinstance(v, dict) else {"kind": "", "ref": str(v or "")}
            form[f"attr_{k}_kind"], form[f"attr_{k}_ref"] = str(v.get("kind") or ""), str(v.get("ref") or "")
        else:
            form[f"attr_{k}"] = "" if v is None else str(v)
    if "box" in data or "position" in data:
        box = data.get("box", item.rack.name if item is not None and item.rack else "")
        rack = session.scalar(select(InventoryRack).where(InventoryRack.module_id_fk == module.id,
                                                          InventoryRack.name == box)) if box else None
        if box and rack is None:
            abort(400, f"{module.label} has no box {box}.")
        form["rack_id"] = str(rack.id) if rack else ""
        form["position"] = str(data.get("position") or "")
    return form


@bp.post("/inventories/<key>/items")
def inventory_create(key: str):
    from . import inventory_service as isvc
    from .inventory_routes import Refused, _item_from_form
    data = _json()
    with SessionLocal() as s:
        module, mv = _inventory(s, key)
        form = _item_form(s, module, mv, data, None)
        item = InventoryItem(module_id_fk=module.id, number=isvc.next_number(s, module.id), owner=g.user.username,
                             status=mv.statuses[0] if mv.statuses else "")
        s.add(item)
        try:
            problem, notes = _item_from_form(s, mv, item, form, creating=True)
        except Refused as refused:
            s.rollback()
            abort(400, str(refused))
        item.updated_at, item.updated_by = datetime.utcnow(), g.user.username
        s.commit()
        number = item.number
        out = item_json(mv, _item(s, module, mv, number))
        return jsonify({**out, "warnings": notes + ([problem] if problem else [])}), 201


@bp.patch("/inventories/<key>/items/<int:number>")
def inventory_update(key: str, number: int):
    from .inventory_routes import Refused, _can_edit, _item_from_form
    data = _json()
    with SessionLocal() as s:
        module, mv = _inventory(s, key)
        item = _item(s, module, mv, number)
        if not _can_edit(item):
            abort(403, access.reason_denied(item, noun=mv.item_noun))
        form = _item_form(s, module, mv, data, item)
        try:
            problem, notes = _item_from_form(s, mv, item, form)
        except Refused as refused:
            s.rollback()
            abort(getattr(refused, "status", 400) if getattr(refused, "status", 400) in (400, 403) else 400,
                  str(refused))
        item.updated_at, item.updated_by = datetime.utcnow(), g.user.username
        s.commit()
        out = item_json(mv, _item(s, module, mv, number))
        return jsonify({**out, "warnings": notes + ([problem] if problem else [])})


# ---------------------------------------------------------------- experiments

def experiment_json(e: Experiment) -> dict:
    return {"id": e.id, "name": e.name or "", "database": e.db or "colony", "readout": e.readout or "",
            "status": e.status or "", "owner": e.owner_username or "", "start_date": _day(e.start_date),
            "end_date": _day(e.end_date), "description": e.description or "", "updated_at": _stamp(e.updated_at)}


@bp.get("/experiments")
def experiments():
    with SessionLocal() as s:
        stmt = select(Experiment)
        for name, column in (("status", Experiment.status), ("owner", Experiment.owner_username),
                             ("database", Experiment.db)):
            if request.args.get(name):
                stmt = stmt.where(column == request.args[name])
        return _page(s, stmt, Experiment, experiment_json)


@bp.get("/experiments/<int:experiment_id>")
def experiment(experiment_id: int):
    """The experiment, its animals, its readout table (with tests by day)
    and its manipulations, as its page shows them."""
    from . import experiments as xp
    with SessionLocal() as s:
        exp = s.get(Experiment, experiment_id)
        place = xp.place_for(s, exp.db or "colony") if exp is not None else None
        if exp is None or place is None:
            abort(404, f"There is no experiment {experiment_id}.")
        return jsonify({**experiment_json(exp), "page": xp.payload(s, exp, place)})


@bp.post("/experiments/<int:experiment_id>/readings")
def experiment_readings(experiment_id: int):
    """{"date": "2026-09-28", "values": {"<animal key or label>": 24.5, ...}}:
    the day's readout (a weight, a count, a score) for some animals."""
    from . import experiments as xp
    data = _json()
    with SessionLocal() as s:
        exp = s.get(Experiment, experiment_id)
        place = xp.place_for(s, exp.db or "colony") if exp is not None else None
        if exp is None or place is None:
            abort(404, f"There is no experiment {experiment_id}.")
        if not access.can_edit_experiment(exp):
            abort(403, access.denied_message("experiment", exp.owner_username))
        try:
            on = date.fromisoformat(str(data.get("date") or date.today().isoformat())[:10])
        except ValueError:
            abort(400, "date is a date, 2026-09-28.")
        if on > date.today():
            abort(400, "A readout can't be for a day still to come.")
        values = data.get("values")
        if not isinstance(values, dict) or not values:
            abort(400, "values maps each animal (its key or label, e.g. \"1043\") to its reading.")
        subjects = xp.subjects(s, exp, place)
        by = {x.key: x for x in subjects}
        by.update({x.label.lstrip("#"): x for x in subjects})
        saved, problems = 0, []
        for name, raw in values.items():
            subject = by.get(str(name).lstrip("#"))
            if subject is None:
                problems.append(f"{name} isn't in this experiment.")
                continue
            problem = xp.set_reading(s, exp, place, subject, on, "" if raw is None else str(raw))
            if problem:
                problems.append(problem)
            else:
                saved += 1
        if not saved:
            s.rollback()
            return _error(400, " ".join(problems[:5]) or "Nothing to save.", problems=problems)
        s.commit()
        return jsonify({"saved": saved, "problems": problems, "date": on.isoformat()})


# ---------------------------------------------------------------- anything else under /api/v1

@bp.route("/<path:_rest>", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
def unknown(_rest):
    return _error(404, "There is no such endpoint. GET /api/v1 lists them.")


# ---------------------------------------------------------------- the reference

# (method, path, token scope, summary, query parameters)
ENDPOINTS = [
    ("GET", "/api/v1", "read", "Who you are, and every endpoint", ()),
    ("GET", "/api/v1/me", "read", "The token's owner and scope", ()),
    ("GET", "/api/v1/mice", "read", "Mice", ("alive", "cage", "litter", "owner", "status", "sex", "genotype", "updated_since")),
    ("GET", "/api/v1/mice/{mouse_id}", "read", "One mouse, with its weights", ()),
    ("PATCH", "/api/v1/mice/{mouse_id}", "write", "Change a mouse: " + ", ".join(MOUSE_FIELDS), ()),
    ("GET", "/api/v1/mice/{mouse_id}/weights", "read", "A mouse's weights", ()),
    ("POST", "/api/v1/mice/{mouse_id}/weights", "write", "Record a weight: {grams, date, notes}", ()),
    ("GET", "/api/v1/cages", "read", "Cages, with their living mice", ("occupied", "owner", "purpose", "room")),
    ("GET", "/api/v1/cages/{cage_id}", "read", "One cage", ()),
    ("GET", "/api/v1/litters", "read", "Litters", ("born_since",)),
    ("GET", "/api/v1/strains", "read", "Strains", ()),
    ("GET", "/api/v1/tanks", "read", "Zebrafish tanks", ("active", "owner")),
    ("GET", "/api/v1/fish", "read", "Zebrafish rows", ("tank", "status")),
    ("GET", "/api/v1/plasmids", "read", "Plasmids", ("q",)),
    ("GET", "/api/v1/plasmids/{plasmid_id}", "read", "One plasmid, with its sequence", ()),
    ("GET", "/api/v1/stocks", "read", "Fly and worm stock databases", ()),
    ("GET", "/api/v1/stocks/{key}/units", "read", "Vials or plates", ("active", "purpose", "owner", "genotype")),
    ("PATCH", "/api/v1/stocks/{key}/units/{number}", "write", "Change a vial: " + ", ".join(UNIT_FIELDS), ()),
    ("GET", "/api/v1/inventories", "read", "Inventories, with their statuses and fields", ()),
    ("GET", "/api/v1/inventories/{key}/items", "read", "Items", ("status", "category", "owner", "q", "updated_since")),
    ("GET", "/api/v1/inventories/{key}/items/{number}", "read", "One item", ()),
    ("POST", "/api/v1/inventories/{key}/items", "write", "Add an item: " + ", ".join(ITEM_FIELDS), ()),
    ("PATCH", "/api/v1/inventories/{key}/items/{number}", "write", "Change an item", ()),
    ("GET", "/api/v1/experiments", "read", "Experiments, in every database", ("status", "owner", "database")),
    ("GET", "/api/v1/experiments/{experiment_id}", "read", "One experiment: animals, readout table, manipulations", ()),
    ("POST", "/api/v1/experiments/{experiment_id}/readings", "write", "Record the readout: {date, values}", ()),
]
PARAM_HELP = {
    "limit": "How many a page (default 100, at most 1000)",
    "after": "Continue after this row (from `next`)",
    "alive": "true or false", "occupied": "true: cages with a living mouse", "active": "true or false",
    "updated_since": "Changed on or after this date or time",
    "born_since": "Born on or after this date",
    "q": "Part of the name", "genotype": "Part of the genotype",
}


@bp.get("/openapi.json")
def openapi():
    paths: dict = {}
    for method, path, scope, summary, params in ENDPOINTS:
        rel = path[len("/api/v1"):] or "/"
        op = {"summary": summary, "security": [{"token": []}],
              "responses": {"200": {"description": "OK"}, "401": {"description": "No valid token"},
                            "403": {"description": "Not allowed"}, "404": {"description": "Not found"}},
              "x-token-scope": scope}
        parameters = [{"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                      for name in _path_names(rel)]
        listing = method == "GET" and not rel.endswith("}") and rel not in ("/", "/me")
        for name in (*params, *(("limit", "after") if listing else ())):
            parameters.append({"name": name, "in": "query", "required": False, "schema": {"type": "string"},
                               "description": PARAM_HELP.get(name, "")})
        if parameters:
            op["parameters"] = parameters
        if method in ("POST", "PATCH"):
            op["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}
        paths.setdefault(rel, {})[method.lower()] = op
    return jsonify({
        "openapi": "3.0.3",
        "info": {"title": "BioManager", "version": VERSION,
                 "description": "The lab's records. Authorization: Bearer <token from Settings → API tokens>."},
        "servers": [{"url": url_for("api.index", _external=True)}],
        "components": {"securitySchemes": {"token": {"type": "http", "scheme": "bearer"}}},
        "paths": paths,
    })


def _path_names(path: str) -> list[str]:
    return [part[1:-1] for part in path.split("/") if part.startswith("{")]


# ---------------------------------------------------------------- Settings → API tokens, and /api

def _signed_in():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


def settings_card() -> dict | None:
    user = g.get("user")
    if user is None or request.path.startswith("/api/v1"):
        return None
    with SessionLocal() as s:
        allowed = may_make_tokens(s, user)
        stmt = select(ApiToken).order_by(ApiToken.created_at.desc())
        if user.role != "admin":
            stmt = stmt.where(ApiToken.user_id_fk == user.id)
        tokens = list(s.scalars(stmt))
        owners = {u.id: u.username for u in s.scalars(select(UserAccount).where(
            UserAccount.id.in_({t.user_id_fk for t in tokens})))} if tokens else {}
        now = datetime.utcnow()
        rows = [{"token": t, "owner": owners.get(t.user_id_fk, "?"),
                 "active": t.revoked_at is None and (t.expires_at is None or t.expires_at > now)} for t in tokens]
    if not allowed and not rows:
        return None
    return {"allowed": allowed, "rows": rows, "is_admin": user.role == "admin", "scopes": SCOPES,
            "expiry": EXPIRY_DAYS}


@pages.app_context_processor
def _inject():
    return {"api_tokens_card": settings_card}


@pages.post("/settings/api-tokens")
def make_token():
    blocked = _signed_in()
    if blocked:
        return blocked
    label = " ".join((request.form.get("label") or "").split())[:80]
    scope = request.form.get("scope") if request.form.get("scope") in SCOPES else "read"
    days = request.form.get("expires") if request.form.get("expires") in EXPIRY_DAYS else "90"
    with SessionLocal() as s:
        if not may_make_tokens(s, g.user):
            abort(403)
        if not label:
            flash(gettext("Say what the token is for, e.g. “Balance in B12” or “My R scripts”."), "error")
            return redirect(url_for("settings") + "#api-tokens")
        raw = new_token()
        s.add(ApiToken(user_id_fk=g.user.id, label=label, token_hash=token_hash(raw), hint=raw[:10], scope=scope,
                       expires_at=datetime.utcnow() + timedelta(days=int(days)) if days else None))
        s.commit()
    # Shown on this page only: never stored, never in a redirect.
    return render_template("api/token.html", token=raw, label=label, scope=SCOPES[scope],
                           base=url_for("api.index", _external=True))


@pages.post("/settings/api-tokens/<int:token_id>/revoke")
def revoke_token(token_id: int):
    blocked = _signed_in()
    if blocked:
        return blocked
    with SessionLocal() as s:
        t = s.get(ApiToken, token_id)
        if t is None or (t.user_id_fk != g.user.id and g.user.role != "admin"):
            abort(404)
        if t.revoked_at is None:
            t.revoked_at = datetime.utcnow()
            s.commit()
        flash(gettext("The token “%(name)s” no longer works.", name=t.label), "success")
    return redirect(url_for("settings") + "#api-tokens")


@pages.get("/api")
def reference():
    blocked = _signed_in()
    if blocked:
        return blocked
    with SessionLocal() as s:
        allowed = may_make_tokens(s, g.user)
    return render_template("api/reference.html", endpoints=ENDPOINTS, param_help=PARAM_HELP, allowed=allowed,
                           base=url_for("api.index", _external=True), per_minute=PER_MINUTE, max_limit=MAX_LIMIT)
