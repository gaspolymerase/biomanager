"""The public API: the lab's records as JSON, for scripts and other tools.

    curl -H "Authorization: Bearer bmt_…" https://SERVER/api/v1/mice?alive=true

**Tokens.** Each person makes their own under Settings → AI assistant & tokens (a
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

import json

import hashlib
import re
import secrets
import threading
import time
from collections import Counter, deque
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
from .models import (ApiToken, CageLitter, CageRecord, Experiment, FishRecord, InventoryItem, InventoryRack, LitterRecord,
                     MouseRecord, MouseWeight, PlasmidBox, PlasmidRecord, StockRack, StockUnit, StrainRecord,
                     TankRecord, UserAccount)

bp = Blueprint("api", __name__, url_prefix="/api/v1")
pages = Blueprint("api_pages", __name__)

VERSION = "1"
TOKEN_PREFIX = "bmt_"
ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PERMISSION = "members_api_tokens"
SCOPES = {"read": "Read", "write": "Read and change", "propose": "Read and propose"}
# What a Propose-only token may send besides reading (app/proposals.py): it
# never changes a record itself; its person approves in BioManager.
PROPOSING = {"api.proposal_create", "api.proposal_discard", "api.mcp"}
# POSTs that only work something out from what they are sent and change
# nothing (app/assistant_compute.py): any token may send them.
COMPUTING = {"api.breeding_plan", "api.randomize", "api.power", "api.derive", "api.plasmid_verify",
             "api.experiment_plate"}
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
        # Where an assistant app learns how to sign in (app/oauth.py, RFC 9728).
        pointer = f'Bearer resource_metadata="{request.url_root.rstrip("/")}/.well-known/oauth-protected-resource/api/v1/mcp"'
        return _error(401, "Send an API token: Authorization: Bearer bmt_… Make one in Settings → AI assistant & tokens.") + (
            {"WWW-Authenticate": pointer},)
    wait = rate.wait(g.api_token.id) if g.api_token.id else 0  # 0: a proposal being applied
    if wait:
        return _error(429, f"Too many requests: at most {PER_MINUTE} a minute.") + ({"Retry-After": str(wait)},)
    if request.endpoint == "api.mcp" or request.endpoint in COMPUTING:
        return None                   # MCP's tools check the scope as the API does (app/mcp_http.py)
    if request.method not in ("GET", "HEAD", "OPTIONS") and g.api_token.scope != "write":
        if g.api_token.scope == "propose" and request.endpoint in PROPOSING:
            return None
        if g.api_token.scope == "propose":
            return _error(403, "This token can read and propose changes, not make them: send them to "
                               "POST /api/v1/proposals, and its person approves them in BioManager.")
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


@bp.errorhandler(409)
def _conflict(e):
    return _error(409, getattr(e, "description", "") or "That clashes with a change made meanwhile.")


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
    _only_filters()
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


def _only_filters() -> None:
    """A list takes the filters its ENDPOINTS row names (and limit, after):
    any other is a 400 that names them, not a filter quietly ignored."""
    rule = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", request.url_rule.rule)
    accepted = next((params for method, path, _scope, _summary, params in ENDPOINTS
                     if method == "GET" and path == rule), ())
    unknown = sorted(set(request.args) - set(accepted) - {"limit", "after"})
    if unknown:
        abort(400, f"{rule[len('/api/v1/'):]} has no filter {', '.join(unknown)}; its filters are "
                   f"{', '.join(accepted) or 'none'} (and limit).")


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
        # Raised by every change (app/sync_clock.py): send it back with a change
        # (PATCH "version") and a mouse changed meanwhile is not overwritten.
        "version": m.sync_version,
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
        return jsonify({**mouse_json(m), "weights": [weight_json(w) for w in weights],
                        "experiments": _mouse_experiments(s, m)})


def _mouse_experiments(session, m: MouseRecord) -> list[dict]:
    """The experiments a mouse is in, with its group (and why it is left
    out of the analysis, if it is)."""
    from .experiments import place_for
    from .models import ExperimentMouse
    if place_for(session, "colony") is None:
        return []
    out = []
    for em in session.scalars(select(ExperimentMouse).options(selectinload(ExperimentMouse.experiment))
                              .where(ExperimentMouse.mouse_id_fk == m.id).order_by(ExperimentMouse.id)):
        e = em.experiment
        out.append({"id": e.id, "name": e.name or "", "group": em.treatment_group or "", "status": e.status or "",
                    **({"left_out": em.left_out} if em.left_out else {})})
    return out


MOUSE_FIELDS = ("sex", "genotype", "transgenes", "status", "owner", "note", "cage", "litter", "date_of_death")


@bp.patch("/mice/<int:mouse_id>")
def mouse_update(mouse_id: int):
    """Change a mouse: any of MOUSE_FIELDS, the rest left as they are. With
    "version" (as the mouse was read), a mouse changed since is refused (409)
    instead of overwritten."""
    from .app import (can_edit_mouse, create_transfer_copy, current_lab_usernames, populate_mouse_from_form,
                      stamp_updated)
    from .services import mouse_display_row, split_genotype
    data = dict(_json())
    seen = data.pop("version", None)
    unknown = sorted(set(data) - set(MOUSE_FIELDS))
    if unknown:
        abort(400, f"A mouse has no {', '.join(unknown)} to change; it has {', '.join(MOUSE_FIELDS)}.")
    with SessionLocal() as s:
        m = _mouse(s, mouse_id)
        if not can_edit_mouse(m):
            abort(403, access.reason_denied(m))
        if seen is not None and seen != m.sync_version:
            abort(409, f"Mouse {m.mouse_id} was changed since you read it (by {m.updated_by or 'someone'}). "
                       "Read it again, then make the change.")
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

_CAGE_LITTERS = selectinload(CageRecord.cage_litters).selectinload(CageLitter.litter)


def cage_json(c: CageRecord) -> dict:
    from .services import cage_litter_json, open_litters
    living = sorted(m.mouse_id for m in c.mice if m.date_of_death is None)
    return {
        # Each litter born in it not yet weaned, oldest first (a trio's two
        # females give two); litter_born is the oldest's date, as before.
        "litters": [cage_litter_json(cl) for cl in open_litters(c)],
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
        stmt = select(CageRecord).options(selectinload(CageRecord.mice), selectinload(CageRecord.rack), _CAGE_LITTERS)
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
        c = s.scalar(select(CageRecord).options(selectinload(CageRecord.mice), selectinload(CageRecord.rack), _CAGE_LITTERS)
                     .where(CageRecord.cage_id == cage_id))
        if c is None:
            abort(404, f"There is no cage {cage_id}.")
        return jsonify(cage_json(c))


RACK_FIELDS = ("name", "rows", "cols", "room", "naming", "cages")


def rack_json(session, r) -> dict:
    from . import positions
    held = session.scalar(select(func.count(CageRecord.id)).where(CageRecord.rack_id_fk == r.id)) or 0
    return {"name": r.name, "rows": r.rows, "cols": r.cols, "room": r.room or "",
            "naming": positions.scheme(r.naming), "positions": positions.example(r.naming, r.rows, r.cols),
            "cages": held, "created_by": r.created_by or None}


@bp.get("/racks")
def racks():
    """The colony's cage racks: size, how positions are named, cages in each."""
    _feature("colony")
    from .services import mouse_racks
    with SessionLocal() as s:
        return jsonify({"data": [rack_json(s, r) for r in mouse_racks(s)], "next": None})


@bp.patch("/racks/<name>")
def rack_update(name: str):
    """Change a cage rack: any of RACK_FIELDS. A change that renames the
    places of cages in it, or leaves some out, needs "cages": "keep_names"
    (each cage keeps its position name and moves to the cell that has it
    now) or "keep_cells" (each keeps its row and column, and its name
    changes); without one it is refused (409) with what each would do."""
    from . import rack_layout
    from .app import _mouse_rack_from_form
    from .models import MouseRack
    _feature("colony")
    data = _json()
    unknown = sorted(set(data) - set(RACK_FIELDS))
    if unknown:
        abort(400, f"A rack has no {', '.join(unknown)} to change; it has {', '.join(RACK_FIELDS)}.")
    with SessionLocal() as s:
        r = s.scalar(select(MouseRack).where(func.lower(MouseRack.name) == name.strip().lower()))
        if r is None:
            abort(404, f"There is no rack {name}.")
        if not access.can_edit_rack(r):
            abort(403, f"Only whoever added rack {r.name}, or an admin, can change it.")
        try:
            naming = rack_layout.json_naming(data, r)
        except ValueError:
            abort(400, "naming is {mode, rows, cols, order, separator, start}.")
        form = MultiDict({"name": str(data.get("name", r.name) or ""), "rows": str(data.get("rows", r.rows)),
                          "cols": str(data.get("cols", r.cols)), "room": str(data.get("room", r.room) or ""),
                          **{f"naming_{k}": str(v) for k, v in naming.items()},
                          rack_layout.FIELD: str(data.get("cages") or "")})
        if form[rack_layout.FIELD] and form[rack_layout.FIELD] not in rack_layout.CHOICES:
            abort(400, "cages is keep_names or keep_cells.")
        if not form[rack_layout.FIELD] and form["rows"].isdigit() and form["cols"].isdigit():
            new = rack_layout.Layout(max(1, min(26, int(form["rows"]))), max(1, min(40, int(form["cols"]))), naming)
            preview = rack_layout.plan(s, rack_layout.kind("mouse_rack"), r, new)
            if preview["affected"]:
                return _error(409, rack_layout.refusal(preview), preview=rack_layout.public(preview))
        error, change = _mouse_rack_from_form(s, r, form)
        if error:
            s.rollback()
            abort(400, error)
        s.commit()
        return jsonify({**rack_json(s, r), "warnings": [rack_layout.outcome(change).strip()] if change else []})


def litter_json(lt: LitterRecord) -> dict:
    # A litter is in a cage through its pups (made in the cage at genotyping,
    # moved out at weaning; services.weaning_due reads it the same way):
    # `cage` is where most of its living pups are, `cages` every one.
    held = Counter(m.cage.cage_id for m in lt.mice if m.date_of_death is None and m.cage is not None)
    from .positions import natural, natural_sorted
    cages = sorted(held, key=lambda code: (-held[code], natural(code)))
    return {"litter_id": lt.litter_id, "date_of_birth": _day(lt.date_of_birth), "weaned_on": _day(lt.weaned_on),
            "cage": cages[0] if cages else None, "cages": natural_sorted(held),
            "cohort": lt.cohort_name or "", "father": lt.father_info or "", "mother": lt.mother_info or "",
            "total_pups": lt.total_pups, "notes": lt.notes or "", "mice": sorted(m.mouse_id for m in lt.mice)}


@bp.get("/litters")
def litters():
    _feature("colony")
    with SessionLocal() as s:
        stmt = select(LitterRecord).options(selectinload(LitterRecord.mice).selectinload(MouseRecord.cage))
        if request.args.get("born_since"):
            try:
                stmt = stmt.where(LitterRecord.date_of_birth >= date.fromisoformat(request.args["born_since"][:10]))
            except ValueError:
                abort(400, "born_since is a date, 2026-09-01.")
        if request.args.get("cage"):
            # Litters with a living pup in that cage.
            stmt = stmt.where(LitterRecord.id.in_(select(MouseRecord.litter_id_fk).where(
                MouseRecord.date_of_death.is_(None),
                MouseRecord.cage_id_fk.in_(select(CageRecord.id).where(CageRecord.cage_id == request.args["cage"].strip())))))
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

def plasmid_json(p: PlasmidRecord, sequence: bool = False, box=None) -> dict:
    from . import plasmid_service
    out = {"plasmid_id": p.plasmid_id, "name": p.name or "", "backbone": p.backbone or "", "insert": p.insert_seq or "",
           "resistance": p.resistance or "", "owner": p.owner or "", "location": p.location or "",
           "concentration": p.concentration or "", "a260_280": p.a260_280 or "", "shared": bool(p.is_shared),
           "shared_with": (groups.name_of(p.share_group_id) or None) if p.is_shared and p.share_group_id else None,
           "box": p.storage_box or "", "position": plasmid_service.label(p, box) or None,
           "notes": p.notes or "", "has_sequence": bool(p.full_sequence), "length": len(p.full_sequence or ""),
           "updated_at": _stamp(p.updated_at)}
    if sequence:
        try:
            annotations = json.loads(p.features_json) if p.features_json else []
        except (TypeError, ValueError):
            annotations = []
        out.update(sequence=p.full_sequence or "", circular=bool(p.is_circular),
                   annotations=[a for a in annotations if isinstance(a, dict)])
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
        boxes = {b.id: b for b in s.scalars(select(PlasmidBox))}
        return _page(s, stmt, PlasmidRecord, lambda p: plasmid_json(p, box=boxes.get(p.box_id_fk)))


@bp.get("/plasmids/<int:plasmid_id>")
def plasmid(plasmid_id: int):
    _feature("plasmids")
    _only_filters()
    # The bases and the map's annotations run to kilobytes: only when asked
    # (?sequence=1); has_sequence and length say whether there is one.
    sequence = bool(_bool_arg("sequence"))
    with SessionLocal() as s:
        p = s.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == plasmid_id))
        if p is None:
            abort(404, f"There is no plasmid {plasmid_id}.")
        box = s.get(PlasmidBox, p.box_id_fk) if p.box_id_fk else None
        return jsonify({**plasmid_json(p, sequence=sequence, box=box),
                        "glycerol_stocks": _glycerol_stocks(s, p)})


def _glycerol_stocks(session, p: PlasmidRecord) -> list[dict]:
    """The glycerol stocks that carry it, as its page's Glycerol stocks card
    lists them: from the Glycerol stocks inventories this person can see."""
    from . import inventory_service as isvc
    out = []
    for m in isvc.made_from_plasmid(session, p.plasmid_id, kinds=("glycerol_stocks",)):
        if not lab.can_see(m["module"]):
            continue
        i, key = m["item"], m["module"].key
        out.append({"number": i.number, "name": i.name or "", "box": i.rack.name if i.rack else None,
                    "position": (isvc.rack_label(i) or None) if i.rack else None, "status": i.status or "",
                    "frozen_on": i.attrs_dict.get("frozen_on") or None,
                    "url": url_for("inventory.module", key=key, open=i.id, _external=True)})
    return out


@bp.post("/plasmids/<int:plasmid_id>/verify")
def plasmid_verify(plasmid_id: int):
    """Sanger reads checked against a plasmid's map (app/seqverify.py):
    multipart files (.ab1, FASTA or text), or JSON {"reads": [text or
    {name, sequence}], "captures": [ids]}. Changes nothing."""
    from . import captures, seqverify
    _feature("plasmids")
    files: list[tuple[str, bytes]] = []
    texts: list[str] = []
    problems: list[str] = []
    for key in request.files:
        for upload in request.files.getlist(key):
            if upload and upload.filename:
                files.append((upload.filename, upload.stream.read(seqverify.MAX_FILE + 1)))
    if request.form.get("text") or request.form.get("reads"):
        texts.append(request.form.get("text") or request.form.get("reads") or "")
    data = request.get_json(silent=True) if request.is_json else None
    if request.is_json and not isinstance(data, dict):
        abort(400, "Send a JSON object: {\"reads\": [...], \"captures\": [...]}, or the read files as multipart.")
    data = data or {}
    raw_reads = data.get("reads") or []
    if not isinstance(raw_reads, list):
        abort(400, "reads is a list: each a read's bases (FASTA or plain), or {name, sequence}.")
    for n, item in enumerate(raw_reads, 1):
        if isinstance(item, dict):
            bases = str(item.get("sequence") or item.get("bases") or "")
            title = str(item.get("name") or f"read {n}").strip().split()[0] if str(item.get("name") or "").strip() \
                else f"read-{n}"
            texts.append(bases if bases.lstrip().startswith(">") else f">{title}\n{bases}")
        elif isinstance(item, str):
            texts.append(item if item.lstrip().startswith(">") else f">read-{n}\n{item}")
        else:
            abort(400, "reads is a list: each a read's bases (FASTA or plain), or {name, sequence}.")
    with SessionLocal() as s:
        p = s.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == plasmid_id))
        if p is None:
            abort(404, f"There is no plasmid {plasmid_id}.")
        if not p.full_sequence:
            abort(400, f"Plasmid {plasmid_id} has no sequence to check reads against.")
        ids = data.get("captures") or []
        try:
            ids = captures.check_ids(s, g.user.username, ids)
        except captures.CaptureError as error:
            abort(400 if "list" in str(error) else 404, str(error))
        for capture_id in ids:
            c = captures.mine(s, capture_id, g.user.username)
            path = captures.file_on_disk(c)
            if path is not None:
                files.append((c.file_name or path.name, path.read_bytes()[:seqverify.MAX_FILE + 1]))
            elif (c.text or "").strip():
                texts.append(c.text if c.text.lstrip().startswith(">") else f">capture-{capture_id}\n{c.text}")
            else:
                problems.append(f"Capture {capture_id} has no read in it.")
        reads, unread = seqverify.reads_from_uploads(files, "\n".join(texts))
        problems += unread
        if not reads:
            abort(400, " ".join(problems) or "Send reads: .ab1 or FASTA files, reads as text, or capture ids.")
        try:
            annotations = json.loads(p.features_json) if p.features_json else []
        except (TypeError, ValueError):
            annotations = []
        report = seqverify.without_traces(
            seqverify.verify(p.full_sequence, bool(p.is_circular), annotations, reads, name=p.name or ""))
        report["plasmid"]["plasmid_id"] = p.plasmid_id
        report["problems"] = problems
        return jsonify(report)


# ---------------------------------------------------------------- fly and worm stocks

def _stock_module(session, key: str):
    from . import stock_service as ssvc
    module = ssvc.get_module(session, key)
    if module is None or not lab.can_see(module):
        abort(404, f"There is no stock database {key}.")
    lab.opened(module)   # a group's database: changing it needs the group's leave
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
    lab.opened(module)   # a group's database: changing it needs the group's leave
    return module, isvc.view(module)


def item_json(mv, i: InventoryItem) -> dict:
    from . import inventory_service as isvc
    attrs = i.attrs_dict
    return {"number": i.number, "name": i.name or "", "category": i.category or "", "status": i.status or "",
            "owner": i.owner or "", "shared": bool(i.is_shared),
            "shared_with": (groups.name_of(i.share_group_id) or None) if i.is_shared and i.share_group_id else None,
            "quantity": i.quantity or "", "unit": i.unit or "", "low_at": i.low_at,
            "pack_size": i.pack_size or "",
            "vendor": i.vendor or "", "catalog_number": i.catalog_number or "", "lot": i.lot or "",
            "box": i.rack.name if i.rack else None, "position": (isvc.rack_label(i) or None) if i.rack else None,
            "kept_in": (i.rack.place or None) if i.rack else None,
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


ITEM_FIELDS = ("name", "category", "status", "owner", "shared", "quantity", "unit", "low_at", "pack_size", "vendor",
               "catalog_number", "lot", "box", "position", "location_note", "received_on", "expires_on", "notes", "fields")


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
    from .experiments import STATUSES
    status = (request.args.get("status") or "").strip().lower()
    if status and status != "all" and status not in STATUSES:
        abort(400, f"status is one of {', '.join(STATUSES)}, or all.")
    with SessionLocal() as s:
        stmt = select(Experiment)
        for name, column in (("status", Experiment.status), ("owner", Experiment.owner_username),
                             ("database", Experiment.db)):
            if request.args.get(name) and not (name == "status" and status == "all"):
                stmt = stmt.where(column == (status if name == "status" else request.args[name]))
        return _page(s, stmt, Experiment, experiment_json)


# What the experiment's page needs to offer choices, not facts about the
# experiment: the kinds of step, the lab's regimens, reagents and sample
# inventories, and the statistical tests. The API leaves them out.
PAGE_ONLY = ("kinds", "regimens", "reagents", "sample_inventories", "tests")


@bp.get("/notebook/pages/<int:page_id>")
def notebook_page(page_id: int):
    """A notebook page this person may read: its title, kind, tags, its
    text as it is now (for an assistant to change it from), and whether
    they may change it."""
    from . import lab_notebook, signatures
    from .models import NotebookFolder, NotebookPage
    _feature("notebook")
    with SessionLocal() as s:
        page = s.get(NotebookPage, page_id)
        role = lab_notebook.role_for(s, page) if page is not None else None
        if role is None:
            abort(404, f"There is no notebook page {page_id} that you can open.")
        info = lab_notebook.info_for(s, page.id)
        folder = s.get(NotebookFolder, info.folder_id) if info and info.folder_id else None
        locked = signatures.is_locked(s, page.id)
        return jsonify({"id": page.id, "ref": {"kind": "notebook_page", "id": page.id}, "title": page.title,
                        "kind": info.kind if info else "note", "tags": lab_notebook.tag_list(info.tags if info else ""),
                        "folder": {"ref": {"kind": "protocol_folder", "id": folder.id}, "name": folder.name}
                        if folder else None,
                        "owner": lab_notebook.owner_of(page), "updated_at": _stamp(page.updated_at),
                        "signed": signatures.is_signed(s, page.id), "locked": locked,
                        "can_edit": lab_notebook.can_edit_role(role) and not locked,
                        "url": request.host_url.rstrip("/") + lab_notebook.page_url(page.id),
                        "body": page.body or ""})


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
        # The design kept at Start and the changes from it, with their
        # reasons (app/experiment_plan.py): for ARRIVE-style reporting.
        from . import experiment_plan
        page = {k: v for k, v in xp.payload(s, exp, place).items() if k not in PAGE_ONLY}
        return jsonify({**experiment_json(exp), "page": page,
                        "plan": experiment_plan.plan_of(exp) or None, "planned_test": exp.planned_test or "",
                        "readout_day": exp.readout_day,
                        "deviations": [{"what": d.what, "before": d.before, "after": d.after, "reason": d.reason,
                                        "detail": [x for x in (d.detail or "").split("\n") if x], "who": d.username,
                                        "at": _stamp(d.updated_at or d.created_at)} for d in exp.deviations]})


@bp.post("/experiments/<int:experiment_id>/readings")
def experiment_readings(experiment_id: int):
    """{"date": "2026-09-28", "values": {"<animal key or label>": 24.5, ...}}:
    the day's readout (a weight, a count, a score) for some animals. For a
    readout BioManager works out (app/derived.py), "inputs" gives what was
    measured instead: {"1043": {"length": 12.1, "width": 8.4}}, and the
    value is computed here. "measure" (another measure's name or key, as a
    plate fills it) records that measure instead of the main readout."""
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
        values = {**(data.get("values") if isinstance(data.get("values"), dict) else {}),
                  **(data.get("inputs") if isinstance(data.get("inputs"), dict) else {})}
        if not values:
            abort(400, "values maps each animal (its key or label, e.g. \"1043\") to its reading; for a readout "
                       "BioManager works out, inputs maps each to what was measured.")
        subjects = xp.subjects(s, exp, place)
        by = {x.key: x for x in subjects}
        by.update({x.label.lstrip("#"): x for x in subjects})
        saved, problems = 0, []
        readout = xp.readout_for(exp, place, data.get("measure"))
        if readout is None:
            abort(400, f"This experiment has no measure “{data.get('measure')}”; its other measures: "
                       f"{', '.join(m['label'] for m in xp.measures_of(exp)) or 'none'}.")
        for name, raw in xp.control_first(readout, list(values.items()), lambda item: by.get(str(item[0]).lstrip("#"))):
            subject = by.get(str(name).lstrip("#"))
            if subject is None:
                problems.append(f"{name} isn't in this experiment.")
                continue
            problem = xp.set_reading(s, exp, place, subject, on, raw if isinstance(raw, dict) else
                                     "" if raw is None else str(raw), readout)
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
    if _rest.strip("/") == "mcp":
        # MCP clients may try to open an event stream; this server has none (app/mcp_http.py).
        return _error(405, "Send MCP messages by POST; this server has no event stream.") + ({"Allow": "POST"},)
    return _error(404, "There is no such endpoint. GET /api/v1 lists them.")


# ---------------------------------------------------------------- for assistants (app/lookup.py)

@bp.get("/resolve")
def resolve():
    """The records a phrase could mean, each with the reference a proposal
    names it by. More than one candidate: ask the person which."""
    from . import actions, lookup
    q = (request.args.get("q") or "").strip()
    if not q:
        abort(400, "Send q: what to look for (cage 88, #14, FV12, a line's name).")
    kinds = {k.strip() for k in (request.args.get("kinds") or "").split(",") if k.strip()} or None
    unknown = sorted((kinds or set()) - set(actions.KINDS))
    if unknown:
        abort(400, f"No kind {', '.join(unknown)}; kinds are {', '.join(actions.KINDS)}.")
    with SessionLocal() as s:
        return jsonify({"data": lookup.resolve(s, q, kinds), "next": None})


@bp.get("/vocabulary")
def vocabulary():
    """What this lab calls things: databases, strains and lines, statuses,
    purposes, racks, people, and each organism database's own fields."""
    from . import lookup
    with SessionLocal() as s:
        return jsonify(lookup.vocabulary(s))


@bp.get("/due")
def due():
    """Home's list of what is due, overdue first, up to `days` ahead."""
    from . import lookup
    days = _int_arg("days", 7, 0, 60)
    with SessionLocal() as s:
        data = lookup.due(s, days)
        s.commit()  # an organism schedule rebuilt on the way (as Home does)
    return jsonify({"data": data, "next": None})


@bp.get("/actions")
def action_list():
    """What a proposal may contain: each action, its target and its fields."""
    from . import actions
    return jsonify({"data": actions.catalogue(), "kinds": list(actions.KINDS), "next": None})


# ---------------------------------------------------------------- looked up and worked out (app/assistant_compute.py)

def _answer(fn, *args, **kwargs):
    """What a lookup or computation gives, or its refusal: 404 for a record
    this person can't open, 400 for what it was asked."""
    try:
        return jsonify(fn(*args, **kwargs))
    except KeyError:
        raise
    except LookupError as e:
        abort(404, str(e))
    except (ValueError, TypeError) as e:
        abort(400, str(e))


def _whole(value, default: int | None = None) -> int | None:
    """A whole number sent in JSON (or as text); a ValueError otherwise."""
    if value in (None, ""):
        return default
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{value} isn't a whole number.")
    return int(value)


def _number_arg(name: str, default: float | None = None) -> float | None:
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        abort(400, f"{name} must be a number.")


def _ref_arg():
    """The record asked about: ?kind=mouse&id=412 (a reference's id) or
    ?kind=mouse&code=1043 (what the lab writes on it)."""
    kind = (request.args.get("kind") or "").strip()
    if not kind:
        abort(400, "Send kind (mouse, cage, plasmid…) and id (a reference's) or code (mouse 1043, cage 88).")
    if (request.args.get("id") or "").strip():
        return kind, {"kind": kind, "id": _int_arg("id", 0, 1)}
    code = (request.args.get("code") or "").strip()
    if not code:
        abort(400, "Send id (a reference's) or code (what the lab writes on it).")
    return kind, code


@bp.get("/search")
def search():
    """The app's own search, across records and the notebook pages you may
    read: each match's kind, reference, title, a snippet and its address."""
    from . import lookup
    q = (request.args.get("q") or "").strip()
    if not q:
        abort(400, "Send q: the words to look for.")
    with SessionLocal() as s:
        return jsonify({"data": lookup.search(s, q, _int_arg("limit", 5, 1, 20)), "next": None})


@bp.get("/history")
def history():
    """A record's change history, newest first: everyone's for an admin,
    your own changes for anyone else (as the pages show them)."""
    from . import lookup
    kind, ref = _ref_arg()
    with SessionLocal() as s:
        try:
            row = lookup.record(s, kind, ref)
        except LookupError as e:
            abort(404, str(e))
        return jsonify(lookup.history(s, kind, row, _int_arg("limit", 50, 1, 200)))


@bp.get("/lineage")
def lineage():
    """A plasmid's family tree, or a mouse's parents, litter and offspring."""
    from . import lookup
    kind, ref = _ref_arg()
    if kind not in ("mouse", "plasmid"):
        abort(400, "kind is mouse or plasmid.")
    with SessionLocal() as s:
        try:
            row = lookup.record(s, kind, ref)
        except LookupError as e:
            abort(404, str(e))
        if kind == "mouse":
            return jsonify(lookup.mouse_lineage(s, row, _int_arg("generations", 2, 1, 4)))
        return jsonify(lookup.plasmid_lineage(s, row))


@bp.get("/path")
def record_path():
    """Where GET reads one record a reference names (cages/88 for the cage
    with id 113): for assistants that hold a {kind, id} from resolve."""
    from . import lookup
    kind, ref = _ref_arg()
    with SessionLocal() as s:
        try:
            row = lookup.notebook_page(s, ref) if kind == "notebook_page" else lookup.record(s, kind, ref)
        except LookupError as e:
            abort(404, str(e))
        path = lookup.path_of(s, kind, row)
        if path is None:
            abort(400, f"A {kind.replace('_', ' ')} isn't read on its own: list it (litters, tanks, fish, "
                       "stocks/<key>/units) or ask history about it.")
        return jsonify({"kind": kind, "ref": {"kind": kind, "id": row.id}, "path": path})


@bp.get("/availability")
def availability():
    """Living animals that fit a genotype, sex and age window on a day."""
    from . import assistant_compute as ac
    age_min, age_max = _number_arg("age_min_days"), _number_arg("age_max_days")
    with SessionLocal() as s:
        return _answer(ac.availability, s, species=(request.args.get("species") or "mouse").strip().lower(),
                       genotype=request.args.get("genotype") or "", sex=request.args.get("sex") or "",
                       age_min_days=int(age_min) if age_min is not None else None,
                       age_max_days=int(age_max) if age_max is not None else None, on=request.args.get("on"),
                       include_in_experiments=bool(_bool_arg("include_in_experiments")),
                       include_breeders=bool(_bool_arg("include_breeders")), limit=_int_arg("limit", 200, 1, 1000))


@bp.post("/breeding-plan")
def breeding_plan():
    """{want: {locus: wt|het|hom|carrier}, sex, count, age_min_days,
    age_max_days, by, crosses, confidence, litters_per_mating}: matings to set
    up, and when."""
    from . import assistant_compute as ac
    data = _json()
    with SessionLocal() as s:
        return _answer(lambda: ac.breeding_plan(
            s, data.get("want"), sex=data.get("sex") or "", count=_whole(data.get("count"), 1),
            age_min_days=_whole(data.get("age_min_days")), age_max_days=_whole(data.get("age_max_days")),
            by=data.get("by"), crosses=data.get("crosses"), confidence=float(data.get("confidence") or 0.9),
            litters_per_mating=_whole(data.get("litters_per_mating"))))


@bp.post("/randomize")
def randomize():
    """{animals: [mice], groups: [names or {name, size}], seed, by, experiment}:
    a balanced allocation; nothing changes."""
    from . import assistant_compute as ac
    data = _json()
    with SessionLocal() as s:
        return _answer(ac.randomize, s, data.get("animals"), data.get("groups"), seed=data.get("seed"),
                       by=data.get("by") or "litter", experiment=data.get("experiment"))


@bp.post("/power")
def power():
    """{groups, difference, sd, means, d, alpha, power, attrition, experiment,
    day}: animals per group."""
    from . import assistant_compute as ac
    data = _json()

    def number(key, default=None):
        value = data.get(key)
        return default if value in (None, "") else float(value)
    with SessionLocal() as s:
        return _answer(lambda: ac.power_analysis(
            s, k=_whole(data.get("groups"), 2), difference=number("difference"), sd=number("sd"),
            means=[float(m) for m in data["means"]] if data.get("means") else None, d=number("d"),
            alpha=number("alpha", 0.05), target=number("power", 0.8), attrition=number("attrition", 0.0),
            experiment=_whole(data.get("experiment")), day=data.get("day")))


@bp.post("/experiments/<int:experiment_id>/plate")
def experiment_plate(experiment_id: int):
    """A plate reader's or qPCR machine's export laid on one of the
    experiment's plate layouts (app/plates.py), changing nothing: {layout
    (its id or name) or wells and format, text or capture (a capture's
    file), target, reference, subtract_blank, standard_curve, skip, date}.
    Answers each animal's wells, mean and value, the wells flagged, and the
    experiment_reading change to propose."""
    from . import captures, plates
    from . import experiments as xp
    data = _json()
    with SessionLocal() as s:
        exp = s.get(Experiment, experiment_id)
        place = xp.place_for(s, exp.db or "colony") if exp is not None else None
        if exp is None or place is None:
            abort(404, f"There is no experiment {experiment_id}.")
        export = None
        if data.get("capture") not in (None, ""):
            ident = str(data.get("capture")).strip()
            c = captures.mine(s, int(ident), g.user.username) if ident.isdigit() else None
            path = captures.file_on_disk(c) if c is not None else None
            if c is None or (path is None and not c.text):
                abort(404, f"You have no capture {data.get('capture')} with an export in it.")
            try:
                export = (plates.read_export(filename=c.file_name or path.name, data=path.read_bytes()) if path
                          else plates.read_export(text=c.text))
            except plates.PlateError as e:
                abort(400, str(e))
        try:
            return jsonify(plates.for_assistant(s, exp, place, data, export))
        except plates.PlateError as e:
            abort(400, str(e))
        finally:
            s.rollback()


@bp.route("/derive", methods=["GET", "POST"])
def derive():
    """GET: the formulas (app/derived.py). POST {formula, rows, control}:
    each row's value worked out, or why not; nothing changes."""
    from . import derived
    if request.method == "GET":
        return jsonify({"formulas": derived.catalogue()})
    data = _json()
    return _answer(derived.derive, str(data.get("formula") or ""), data.get("rows"), data.get("control"))


@bp.get("/reorder-forecast")
def reorder_forecast():
    """Stock kept as a number that runs out within `days`, from its use."""
    from . import assistant_compute as ac
    with SessionLocal() as s:
        return _answer(ac.reorder_forecast, s, within_days=_int_arg("days", 30, 1, 365),
                       lead_days=_int_arg("lead_days", 14, 0, 180), window_days=_int_arg("window_days", 180, 14, 730))


@bp.get("/lab-checks")
def lab_checks():
    """The weekly review's checks, each with its items and their fixes."""
    from . import assistant_compute as ac
    with SessionLocal() as s:
        out = _answer(ac.lab_checks, s, weight_loss_pct=_number_arg("weight_loss_pct"),
                      breeder_weeks=_int_arg("breeder_weeks", 0, 0, 200) or None,
                      unsigned_days=_int_arg("unsigned_days", 14, 1, 365),
                      expiring_days=_int_arg("expiring_days", 30, 1, 365),
                      scope=(request.args.get("scope") or "lab").strip())
        s.commit()  # an organism schedule rebuilt on the way (as /due does)
        return out


@bp.get("/experiments/<int:experiment_id>/summary")
def experiment_summary(experiment_id: int):
    """The facts of an experiment for its methods, and ARRIVE's Essential 10."""
    from . import assistant_compute as ac
    with SessionLocal() as s:
        return _answer(ac.experiment_summary, s, experiment_id)


@bp.get("/animal-use")
def animal_use():
    """Animals per protocol, species and strain over a period (the last
    twelve months unless asked), for an annual review; the colony and
    zebrafish are the lab's, so anyone may ask."""
    from . import assistant_reports as ar
    with SessionLocal() as s:
        return _answer(ar.animal_use, s, start=request.args.get("start"), end=request.args.get("end"),
                       protocol=request.args.get("protocol") or "")


@bp.get("/handover")
def handover():
    """Everything a person owns or looks after, and what is due on it: your
    own, or (an admin) anyone's."""
    from . import assistant_reports as ar
    with SessionLocal() as s:
        try:
            out = _answer(ar.handover, s, username=(request.args.get("username") or "").strip(),
                          days=_int_arg("days", ar.AGENDA_DAYS, 1, 90))
        except PermissionError as e:
            abort(403, str(e))
        s.commit()  # an organism schedule rebuilt on the way (as /due does)
        return out


@bp.get("/regimens")
def regimens():
    """The lab's saved regimens (an experiment's manipulations and days)."""
    from . import experiment_steps as xs
    family = (request.args.get("family") or "mouse").strip()
    with SessionLocal() as s:
        return jsonify({"data": xs.regimens_for(s, family), "next": None})


# ---------------------------------------------------------------- proposals (app/proposals.py)

@bp.post("/proposals")
def proposal_create():
    """{"summary", "source", "changes": [{action, target, fields, evidence}], "replaces", "captures"}:
    previewed at once and kept for its person to approve in BioManager."""
    from flask import current_app
    from . import actions, proposals
    data = _json()
    changes = data.get("changes")
    if not isinstance(changes, list) or not changes:
        abort(400, "Send changes: a list of {action, target, fields} (see GET /api/v1/actions).")
    if len(changes) > actions.MAX_CHANGES:
        abort(400, f"At most {actions.MAX_CHANGES} changes in one proposal.")
    replaces = data.get("replaces")
    if replaces is not None and (isinstance(replaces, bool) or not isinstance(replaces, int)):
        abort(400, "replaces is the id of an earlier proposal of yours.")
    source = " ".join(str(data.get("source") or g.api_token.label or "").split())[:80]
    with SessionLocal() as s:
        try:
            p = proposals.create(current_app._get_current_object(), s, g.user.username, changes,
                                 summary=str(data.get("summary") or ""), source=source,
                                 token_id=g.api_token.id or None, replaces=replaces, captures=data.get("captures"))
        except proposals.ProposalError as error:
            abort(400, str(error))
        body = proposals.as_dict(s, p, records=True)
    body["review_url"] = url_for("proposals.page", _external=True) + f"#proposal-{body['id']}"
    return jsonify(body), 201


@bp.get("/proposals")
def proposal_list():
    """Your proposals, newest first."""
    from . import proposals
    from .models import Proposal
    status = (request.args.get("status") or "").strip()
    if status and status not in proposals.STATUSES:
        abort(400, f"status is one of {', '.join(proposals.STATUSES)}.")
    limit = _int_arg("limit", 20, 1, 100)
    with SessionLocal() as s:
        proposals.expire_old(s)
        s.commit()
        stmt = select(Proposal).where(Proposal.owner_username == g.user.username)
        if status:
            stmt = stmt.where(Proposal.status == status)
        rows = s.scalars(stmt.order_by(Proposal.id.desc()).limit(limit)).all()
        return jsonify({"data": [proposals.as_dict(s, p) for p in rows], "next": None})


@bp.get("/proposals/<int:proposal_id>")
def proposal_get(proposal_id: int):
    """One proposal of yours: its status, and each change as previewed."""
    from . import proposals
    from .models import Proposal
    with SessionLocal() as s:
        proposals.expire_old(s)
        s.commit()
        p = s.get(Proposal, proposal_id)
        if p is None or p.owner_username != g.user.username:
            abort(404, f"There is no proposal {proposal_id} of yours.")
        return jsonify(proposals.as_dict(s, p, full=True))


@bp.post("/proposals/<int:proposal_id>/discard")
def proposal_discard(proposal_id: int):
    """Withdraw a proposal of yours that is still waiting (approving is
    only done in BioManager)."""
    from . import proposals
    with SessionLocal() as s:
        try:
            p = proposals.discard(s, proposal_id, g.user.username)
        except proposals.ProposalError as error:
            abort(409 if "already" in str(error) else 404, str(error))
        return jsonify(proposals.as_dict(s, p))


# ---------------------------------------------------------------- captures (app/captures.py)

@bp.get("/captures")
def capture_list():
    """Your captures, newest first: those waiting, unless ?status= says
    used, dismissed or all (waiting first)."""
    from . import captures
    status = (request.args.get("status") or "waiting").strip()
    if status not in (*captures.STATUSES, "all"):
        abort(400, f"status is one of {', '.join(captures.STATUSES)}, all.")
    limit = _int_arg("limit", 50, 1, 200)
    with SessionLocal() as s:
        rows = captures.of(s, g.user.username, status, limit)
        return jsonify({"data": [captures.as_dict(c) for c in rows], "next": None})


@bp.get("/captures/<int:capture_id>")
def capture_get(capture_id: int):
    """One capture of yours, with what is in it: a photo as base64
    (`image`: {mimeType, data}), a CSV or text file's text (`file_text`)."""
    from . import captures
    with SessionLocal() as s:
        c = captures.mine(s, capture_id, g.user.username)
        if c is None:
            abort(404, f"There is no capture {capture_id} of yours.")
        return jsonify(captures.for_assistant(c))


# ---------------------------------------------------------------- MCP (app/mcp_http.py)

@bp.post("/mcp")
def mcp():
    """MCP over streamable HTTP, for assistant apps (claude.ai, ChatGPT,
    Claude Code): the same tools as mcp/biomanager_mcp.py."""
    from . import mcp_http
    return mcp_http.endpoint()


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
    ("GET", "/api/v1/racks", "read", "Cage racks: size, how positions are named, how many cages", ()),
    ("PATCH", "/api/v1/racks/{name}", "write",
     "Change a cage rack: " + ", ".join(RACK_FIELDS) + ". A layout that renames or leaves out cages' places needs "
     "cages: keep_names (each keeps its position name) or keep_cells (each keeps its row and column); without "
     "one it is refused (409) with what each would do", ()),
    ("GET", "/api/v1/litters", "read", "Litters, with the cage their pups are in", ("born_since", "cage")),
    ("GET", "/api/v1/strains", "read", "Strains", ()),
    ("GET", "/api/v1/tanks", "read", "Zebrafish tanks", ("active", "owner")),
    ("GET", "/api/v1/fish", "read", "Zebrafish rows", ("tank", "status")),
    ("GET", "/api/v1/plasmids", "read", "Plasmids", ("q",)),
    ("GET", "/api/v1/plasmids/{plasmid_id}", "read",
     "One plasmid, with its glycerol stocks (its sequence and annotations with sequence=1)", ("sequence",)),
    ("POST", "/api/v1/plasmids/{plasmid_id}/verify", "read",
     "Check Sanger reads against its map: .ab1 or FASTA files (multipart), or {reads, captures}", ()),
    ("GET", "/api/v1/stocks", "read", "Fly and worm stock databases", ()),
    ("GET", "/api/v1/stocks/{key}/units", "read", "Vials or plates", ("active", "purpose", "owner", "genotype")),
    ("PATCH", "/api/v1/stocks/{key}/units/{number}", "write", "Change a vial: " + ", ".join(UNIT_FIELDS), ()),
    ("GET", "/api/v1/inventories", "read", "Inventories, with their statuses and fields", ()),
    ("GET", "/api/v1/inventories/{key}/items", "read", "Items", ("status", "category", "owner", "q", "updated_since")),
    ("GET", "/api/v1/inventories/{key}/items/{number}", "read", "One item", ()),
    ("POST", "/api/v1/inventories/{key}/items", "write", "Add an item: " + ", ".join(ITEM_FIELDS), ()),
    ("PATCH", "/api/v1/inventories/{key}/items/{number}", "write", "Change an item", ()),
    ("GET", "/api/v1/experiments", "read", "Experiments, in every database", ("status", "owner", "database")),
    ("GET", "/api/v1/experiments/{experiment_id}", "read", "One experiment: animals, readout table, manipulations, the plan kept at Start and the changes from it", ()),
    ("POST", "/api/v1/experiments/{experiment_id}/readings", "write",
     "Record the readout: {date, values}, or for a readout BioManager works out {date, inputs}", ()),
    ("GET", "/api/v1/notebook/pages/{page_id}", "read",
     "One notebook page you may read: title, kind, tags, its protocol folder, whether you may change it, and its "
     "text", ()),
    ("GET", "/api/v1/resolve", "read", "The records a phrase could mean, with references", ("q", "kinds")),
    ("GET", "/api/v1/vocabulary", "read", "What this lab calls things", ()),
    ("GET", "/api/v1/due", "read", "What is due (Home's list), with the change that records each done", ("days",)),
    ("GET", "/api/v1/actions", "read", "What a proposal may contain", ()),
    ("GET", "/api/v1/search", "read", "Search records and the notebook pages you may read", ("q", "limit")),
    ("GET", "/api/v1/history", "read", "A record's change history (yours; everyone's for an admin)",
     ("kind", "id", "code", "limit")),
    ("GET", "/api/v1/lineage", "read", "A plasmid's family tree, or a mouse's parents, litter and offspring",
     ("kind", "id", "code", "generations")),
    ("GET", "/api/v1/path", "read", "Where GET reads the record a reference names (cages/88, mice/1043)",
     ("kind", "id", "code")),
    ("GET", "/api/v1/availability", "read", "Living animals that fit a genotype, sex and age on a day",
     ("species", "genotype", "sex", "age_min_days", "age_max_days", "on", "include_in_experiments",
      "include_breeders", "limit")),
    ("POST", "/api/v1/breeding-plan", "read",
     "Work out matings: {want, sex, count, age_min_days, age_max_days, by, crosses, confidence}", ()),
    ("POST", "/api/v1/randomize", "read", "Allocate animals to groups, balanced: {animals, groups, seed, by, experiment}",
     ()),
    ("POST", "/api/v1/power", "read",
     "Animals per group: {groups, difference, sd, means, d, alpha, power, attrition, experiment, day}", ()),
    ("POST", "/api/v1/experiments/{experiment_id}/plate", "read",
     "Lay a plate reader's or qPCR export on one of the experiment's plate layouts, changing nothing: {layout, "
     "text or capture, target, reference, subtract_blank, standard_curve, skip, date}", ()),
    ("GET", "/api/v1/derive", "read", "The formulas BioManager works readouts out with", ()),
    ("POST", "/api/v1/derive", "read", "Work out a derived readout for rows, changing nothing: {formula, rows, control}",
     ()),
    ("GET", "/api/v1/reorder-forecast", "read", "Stock that runs out soon, from its use", ("days", "lead_days",
                                                                                           "window_days")),
    ("GET", "/api/v1/lab-checks", "read", "The weekly review's checks",
     ("scope", "weight_loss_pct", "breeder_weeks", "unsigned_days", "expiring_days")),
    ("GET", "/api/v1/experiments/{experiment_id}/summary", "read",
     "An experiment's facts for its methods, and the ARRIVE Essential 10", ()),
    ("GET", "/api/v1/animal-use", "read", "Animals used per protocol, species and strain over a period",
     ("start", "end", "protocol")),
    ("GET", "/api/v1/handover", "read", "Everything a person owns or looks after, and what is due on it",
     ("username", "days")),
    ("GET", "/api/v1/regimens", "read", "The lab's saved regimens", ("family",)),
    ("POST", "/api/v1/proposals", "propose",
     "Propose changes for your approval: {summary, source, changes: [{action, target, fields, evidence}], replaces, "
     "captures}", ()),
    ("GET", "/api/v1/proposals", "read", "Your proposals, newest first", ("status",)),
    ("GET", "/api/v1/proposals/{proposal_id}", "read", "One proposal: its status and each change as previewed", ()),
    ("POST", "/api/v1/proposals/{proposal_id}/discard", "propose", "Withdraw a proposal still waiting", ()),
    ("GET", "/api/v1/captures", "read", "Your captures from the bench, waiting to be turned into records",
     ("status",)),
    ("GET", "/api/v1/captures/{capture_id}", "read", "One capture: its text, and its photo (base64) or file's text", ()),
    ("POST", "/api/v1/mcp", "propose", "MCP (streamable HTTP) for assistant apps: the tools of mcp/biomanager_mcp.py", ()),
]
NOT_PAGED = {"/", "/me", "/racks", "/resolve", "/vocabulary", "/due", "/actions", "/proposals", "/mcp", "/captures", "/search",
             "/history", "/lineage", "/path", "/availability", "/reorder-forecast", "/lab-checks", "/regimens",
             "/animal-use", "/handover"}
PARAM_HELP = {
    "limit": "How many a page (default 100, at most 1000)",
    "after": "Continue after this row (from `next`)",
    "alive": "true or false", "occupied": "true: cages with a living mouse", "active": "true or false",
    "updated_since": "Changed on or after this date or time",
    "born_since": "Born on or after this date",
    "cage": "The cage's code, as the lab writes it (88); for litters, those with a living pup in it",
    "sequence": "true: with its DNA sequence and map annotations (left out otherwise)",
    "q": "Part of the name", "genotype": "Part of the genotype",
    "kinds": "Only these kinds, comma-separated (cage,mouse; see /actions)",
    "days": "How many days ahead (default 7, at most 60)",
    "kind": "What the record is: mouse, cage, plasmid, stock_unit, inventory_item… (a reference's kind)",
    "id": "A reference's id (from resolve or search)", "code": "What the lab writes on it: 1043, 88, FV12",
    "generations": "How many generations of parents (default 2)",
    "species": "mouse (default) or zebrafish", "sex": "F or M",
    "age_min_days": "At least this many days old on the day", "age_max_days": "At most this many days old on the day",
    "on": "The day (default today)", "include_in_experiments": "true: count animals in active experiments too",
    "include_breeders": "true: count mice in Breeder and Breeding cages too",
    "lead_days": "Days an order takes (default 14)", "window_days": "Days of history to read (default 180)",
    "scope": "lab (default) or mine", "weight_loss_pct": "Flag a loss from peak weight this big (default 15)",
    "breeder_weeks": "Flag breeders this old (default 30)", "unsigned_days": "Flag pages unsigned this long (default 14)",
    "expiring_days": "Flag reagents expiring within (default 30)",
    "family": "mouse, fish, fly, worm or organism",
    "start": "The first day (default: twelve months before end)", "end": "The last day (default today)",
    "protocol": "Only this protocol (as the records write it)",
    "username": "Whose (default you; an admin may ask about anyone)",
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
        listing = method == "GET" and not rel.endswith("}") and rel not in NOT_PAGED
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
                 "description": "The lab's records. Authorization: Bearer <token from Settings → AI assistant & tokens>."},
        "servers": [{"url": url_for("api.index", _external=True)}],
        "components": {"securitySchemes": {"token": {"type": "http", "scheme": "bearer"}}},
        "paths": paths,
    })


def _path_names(path: str) -> list[str]:
    return [part[1:-1] for part in path.split("/") if part.startswith("{")]


# ---------------------------------------------------------------- Settings → AI assistant & tokens, and /api

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
    from . import lab
    return render_template("api/token.html", token=raw, label=label, scope=SCOPES[scope], scope_key=scope,
                           base=url_for("api.index", _external=True), server=request.host_url.rstrip("/"),
                           assistants_guide=lab.guide_url().replace("guide.html", "guide/ai-assistants.html"))


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
