"""Signing a notebook page: the record is locked, and later changes are
amendments.

Signing is each person's choice, page by page: nothing asks for it. When
someone makes a page the record of their work, they sign it:

- **Sign** (the owner or an editor): they confirm who they are (their
  password, or for a Google or Microsoft account their user name) and what
  signing means ("I did this work; this is its record"). The page's exact
  title and text are fingerprinted (SHA-256) and kept as a version, any
  live experiment in it is frozen as it is at that moment, and the page is
  locked: no one can change it, live or otherwise.
- **Witness** (anyone else who can open it): they confirm who they are and
  that they have read it, while it is locked.
- **Amend** (the owner): opens it again, with a reason, which stays in the
  record. Signing it again locks it again. Earlier signatures stay, each
  with the fingerprint of what it signed, so anyone can see whether the
  page still reads as it did.

A signed page can't be deleted.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from .db import SessionLocal
from .models import NotebookPage, NotebookVersion, RecordSignature

bp = Blueprint("signatures", __name__, url_prefix="/notebook/api/pages")

MEANINGS = {
    "sign": "I did this work, and this page is its record.",
    "review": "I reviewed this page and it is accurate.",
    "witness": "I have read and understood this page.",
}
_FENCE = re.compile(r"(?P<fence>`{3,})experiment\n(?P<data>.*?)\n(?P=fence)", re.S)


def fingerprint(title: str, body: str) -> str:
    return hashlib.sha256(f"{title or ''}\n{body or ''}".encode("utf-8")).hexdigest()


def history(session, page_id: int) -> list[RecordSignature]:
    return session.scalars(select(RecordSignature).where(RecordSignature.page_id_fk == page_id)
                           .order_by(RecordSignature.signed_at, RecordSignature.id)).all()


def is_locked(session, page_id: int) -> bool:
    """Signed, and not opened again since."""
    state = None
    for row in history(session, page_id):
        if row.action == "sign":
            state = "locked"
        elif row.action == "amend":
            state = None
    return state == "locked"


def is_signed(session, page_id: int) -> bool:
    return any(r.action == "sign" for r in history(session, page_id))


def summary(session, page: NotebookPage) -> dict:
    rows = history(session, page.id)
    now = fingerprint(page.title, page.body)
    return {
        "locked": is_locked(session, page.id),
        "signed": any(r.action == "sign" for r in rows),
        "entries": [{"id": r.id, "action": r.action, "meaning": r.meaning, "reason": r.reason, "username": r.username,
                     "name": r.name or r.username, "at": r.signed_at.isoformat(timespec="seconds") + "Z",
                     "sha256": r.content_sha256, "matches": bool(r.content_sha256) and r.content_sha256 == now,
                     "version_id": r.version_id_fk} for r in rows],
        "sha256": now,
    }


def freeze_experiments(body: str) -> str:
    """Every live experiment block in the page, as it is now: a signed record
    can't change when the experiment does."""
    from . import experiment_steps as xs
    from .models import Experiment

    def freeze(match):
        try:
            data = json.loads(match.group("data"))
        except ValueError:
            return match.group(0)
        if not isinstance(data, dict) or data.get("frozen") or not data.get("id"):
            return match.group(0)
        with SessionLocal() as s:
            exp = s.get(Experiment, int(data["id"]))
            if exp is None:
                return match.group(0)
            data["frozen"] = {"at": datetime.now().strftime("%Y-%m-%d %H:%M") + " (signed)",
                              "experiment": xs.notebook_payload(s, exp)}
        fence = match.group("fence")
        return f"{fence}experiment\n{json.dumps(data)}\n{fence}"

    return _FENCE.sub(freeze, body or "")


def _confirm_identity(user, data) -> str | None:
    """Who is signing, confirmed: their password, or (an account that signs
    in with Google or Microsoft) their user name typed."""
    from . import security
    if user.password_hash and user.password_hash != getattr(security, "NO_PASSWORD", None):
        if not security.check_password(user, str(data.get("password") or "")):
            return "That password isn't right."
        return None
    if str(data.get("confirm") or "").strip().lower() != user.username.lower():
        return f"Type your user name, {user.username}, to sign."
    return None


def _page(session, page_id: int):
    from .lab_notebook import load_page
    return load_page(session, page_id)


@bp.get("/<int:page_id>/signatures")
def show(page_id: int):
    with SessionLocal() as s:
        page, role = _page(s, page_id)
        from . import security
        return jsonify({"ok": True, **summary(s, page), "meanings": MEANINGS,
                        "needs_password": bool(g.user.password_hash) and g.user.password_hash != getattr(security, "NO_PASSWORD", None),
                        "can_sign": role in ("owner", "edit"), "can_amend": role == "owner",
                        "me": g.user.username})


@bp.post("/<int:page_id>/sign")
def sign(page_id: int):
    """Sign (the owner or an editor), witness (anyone else), or amend (the
    owner, with a reason). {action, meaning, reason, password | confirm}."""
    from . import lab_notebook as nb
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "sign")
    if action not in ("sign", "review", "witness", "amend"):
        return jsonify({"ok": False, "error": "Sign, witness or amend."}), 400
    with SessionLocal() as s:
        page, role = _page(s, page_id)
        user = s.get(type(g.user), g.user.id)
        problem = _confirm_identity(user, data)
        if problem:
            return jsonify({"ok": False, "error": problem}), 403
        locked = is_locked(s, page.id)
        name = user.display_name or user.username
        if action in ("sign", "review"):
            if role not in ("owner", "edit"):
                return jsonify({"ok": False, "error": "Only whoever can edit the page signs it; you can witness it."}), 403
            if locked and action == "sign":
                return jsonify({"ok": False, "error": "It is signed already. Amend it first to change it."}), 409
            frozen = freeze_experiments(page.body or "")
            if frozen != (page.body or ""):
                nb.record_edit(s, page)
                page.body = frozen
                nb.reset_collab(s, page.id)      # open editors reload the frozen text
            version = NotebookVersion(page_id_fk=page.id, title=page.title, body=page.body or "", kind="manual",
                                      label=f"Signed by {name}"[:160], saved_by=user.username, saved_at=nb._now())
            s.add(version)
            s.flush()
            s.add(RecordSignature(page_id_fk=page.id, action="sign" if action == "sign" else "review",
                                  meaning=str(data.get("meaning") or MEANINGS[action])[:200], username=user.username,
                                  name=name, content_sha256=fingerprint(page.title, page.body), version_id_fk=version.id))
        elif action == "witness":
            if not locked:
                return jsonify({"ok": False, "error": "Only a signed page can be witnessed."}), 409
            last = next((r for r in reversed(history(s, page.id)) if r.action == "sign"), None)
            if last is not None and last.username == user.username:
                return jsonify({"ok": False, "error": "Someone other than who signed it witnesses it."}), 409
            s.add(RecordSignature(page_id_fk=page.id, action="witness", meaning=str(data.get("meaning") or MEANINGS["witness"])[:200],
                                  username=user.username, name=name, content_sha256=fingerprint(page.title, page.body)))
        else:
            if role != "owner":
                return jsonify({"ok": False, "error": "Only the page's owner can open it again to amend it."}), 403
            if not locked:
                return jsonify({"ok": False, "error": "It isn't locked."}), 409
            reason = str(data.get("reason") or "").strip()
            if len(reason) < 5:
                return jsonify({"ok": False, "error": "Say why it is being amended: that stays in the record."}), 400
            s.add(RecordSignature(page_id_fk=page.id, action="amend", reason=reason[:2000], username=user.username,
                                  name=name, content_sha256=fingerprint(page.title, page.body)))
        s.commit()
        return jsonify({"ok": True, **summary(s, page)})


def refuse_if_locked(session, page_id: int):
    """For every route that changes a page: a JSON 423 while it is locked."""
    if is_locked(session, page_id):
        return jsonify({"ok": False, "locked": True,
                        "error": "This page is signed and locked. Its owner can amend it to change it."}), 423
    return None
