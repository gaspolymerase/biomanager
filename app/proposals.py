"""Proposed changes: what an assistant sends, and what its person approves.

An assistant (through /api/v1/proposals, or the MCP server in mcp/) sends a
list of changes from app/actions.py. BioManager previews them at once (the
pages run as the person, and everything is rolled back), keeps the proposal
with each change's summary, warnings, errors and before → after, and tells
the person. On Proposed changes they read one summary and press Approve, or
Discard. Approving runs every change again, for real, as one batch in Batch
history; it is refused, with nothing applied, when a change no longer goes
through or a record it touches was changed after the preview.

Only the person whose token sent it may approve or discard a proposal, and
only signed in: no token can approve.

Each change may carry its evidence (`evidence`: the words of the person's
notes it comes from, or what a photo shows, and the capture it is in),
shown beside it; it is kept with the request and taken off before the
change runs. A proposal may name the captures it was made from (`captures`,
app/captures.py): they become used when it is approved. Instead of
approving, the person may **Send back** a proposal with a one-line reason
(status `sent_back`), which the assistant reads to correct it and propose
again with `replaces`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select, update

from . import actions, i18n
from .i18n import gettext
from .models import AuditEntry, Proposal, ProposalChange

EXPIRE_DAYS = 14
MAX_SUMMARY = 2000
STATUSES = ("pending", "approved", "discarded", "superseded", "expired", "invalid", "sent_back")
MAX_EVIDENCE = 300
MAX_REASON = 300
# What a new proposal's `replaces` supersedes.
REPLACEABLE = ("pending", "sent_back", "invalid")
# What each database's changes are listed under on Proposed changes.
AREAS = {"mice": "Mouse colony", "zebrafish": "Zebrafish", "stocks": "Flies and worms",
         "organisms": "Other organisms", "experiments": "Experiments", "notebook": "Notebook",
         "inventory": "Inventories", "plasmids": "Plasmids", "calendar": "Calendar"}


class ProposalError(ValueError):
    """Why a proposal can't be approved or discarded, in words for the person."""


def label(p: Proposal) -> str:
    """What the change history calls an approved proposal's changes."""
    return gettext("Proposal #%(id)s (via %(source)s)", id=p.id, source=p.source or "API")


def description(p: Proposal) -> str:
    """Its batch's description in Batch history."""
    summary = " ".join((p.summary or "").split())
    if len(summary) > 120:
        summary = summary[:119] + "…"
    return gettext("Proposal #%(id)s: %(summary)s (via %(source)s)", id=p.id,
                   summary=summary or gettext("%(n)s changes", n=0), source=p.source or "API")[:200]


def expire_old(session) -> None:
    """Pending proposals past their time are expired (read lazily)."""
    session.execute(update(Proposal).where(Proposal.status == "pending", Proposal.expires_at.is_not(None),
                                           Proposal.expires_at < datetime.utcnow())
                    .values(status="expired", decided_at=datetime.utcnow()))


def evidence_of(raw) -> dict | None:
    """A change's evidence as kept and shown: {"quote", "capture", "where"},
    from a string (the quote) or an object; None when it has none."""
    ev = raw.get("evidence") if isinstance(raw, dict) else None
    if isinstance(ev, str):
        ev = {"quote": ev}
    if not isinstance(ev, dict):
        return None
    quote = " ".join(str(ev.get("quote") or ev.get("text") or "").split())
    if len(quote) > MAX_EVIDENCE:
        quote = quote[:MAX_EVIDENCE - 1] + "…"
    capture = ev.get("capture")
    capture = capture if isinstance(capture, int) and not isinstance(capture, bool) else None
    where = " ".join(str(ev.get("where") or "").split())[:120]
    if not quote and capture is None:
        return None
    return {"quote": quote, "capture": capture, "where": where}


def _runnable(changes) -> list:
    """The changes as actions.run takes them: without their evidence."""
    if not isinstance(changes, list):
        return []
    return [{k: v for k, v in c.items() if k != "evidence"} if isinstance(c, dict) else c for c in changes]


def _capture_ids(session, owner: str, changes, captures) -> list[int]:
    """The captures named, and those the evidence points into: all the
    owner's (ProposalError otherwise)."""
    from . import captures as lab_captures
    named = list(captures) if isinstance(captures, list) else captures
    if isinstance(named, list):
        named += [ev["capture"] for ev in map(evidence_of, changes if isinstance(changes, list) else [])
                  if ev and ev["capture"] is not None]
    try:
        return lab_captures.check_ids(session, owner, named)
    except lab_captures.CaptureError as error:
        raise ProposalError(str(error)) from None


def create(app, session, owner: str, changes, summary: str = "", source: str = "", token_id: int | None = None,
           replaces: int | None = None, captures=None) -> Proposal:
    """Preview `changes` as `owner` and keep the proposal: pending, or
    invalid with the reasons. Commits. A capture that isn't the owner's
    raises ProposalError, with nothing kept."""
    capture_ids = _capture_ids(session, owner, changes, captures if captures is not None else [])
    lang = i18n.language_for(session, owner)
    mark = session.scalar(select(func.max(AuditEntry.id))) or 0
    session.commit()  # the preview runs in a transaction of its own
    outcome = actions.run(app, owner, _runnable(changes), lang=lang,
                          source=(source or "")[:80])
    now = datetime.utcnow()
    p = Proposal(owner_username=owner, token_id_fk=token_id, source=(source or "")[:80],
                 summary=(summary or "").strip()[:MAX_SUMMARY], status="pending" if outcome.ok else "invalid",
                 created_at=now, expires_at=now + timedelta(days=EXPIRE_DAYS), audit_mark=mark, lang=lang,
                 captures_json=json.dumps(capture_ids))
    session.add(p)
    session.flush()
    for i, (raw, out) in enumerate(zip(changes if isinstance(changes, list) else [{}], outcome.changes)):
        act = actions.CATALOGUE.get(out.action)
        session.add(ProposalChange(
            proposal_id_fk=p.id, position=i, action=out.action[:60], area=act.area if act else "",
            request_json=json.dumps(raw if isinstance(raw, dict) else {"invalid": True}, ensure_ascii=False,
                                    default=str),
            summary=out.summary, ok=out.ok, errors_json=json.dumps(out.errors, ensure_ascii=False),
            warnings_json=json.dumps(out.warnings, ensure_ascii=False),
            records_json=json.dumps(out.records, ensure_ascii=False, default=str)))
    if replaces:
        # A corrected proposal replaces one of the same person's not yet
        # decided for good (waiting, sent back or invalid): superseded.
        earlier = session.get(Proposal, replaces)
        if earlier is not None and earlier.owner_username == owner and earlier.id != p.id:
            p.replaces_id = earlier.id
            if earlier.status in REPLACEABLE:
                earlier.status, earlier.decided_at = "superseded", now
    if p.status == "pending":
        from . import notify
        notify.send(session, owner, "Proposed changes from %(source)s", category="general",
                    values={"source": p.source or "API"}, message=p.summary[:300],
                    link=_review_link(p), actor=p.source or "API")
    session.commit()
    return p


def _review_link(p: Proposal) -> str:
    from flask import url_for
    try:
        return url_for("proposals.page") + f"#proposal-{p.id}"
    except RuntimeError:
        return f"/proposals#proposal-{p.id}"


def changes_of(session, p: Proposal) -> list[ProposalChange]:
    return list(session.scalars(select(ProposalChange).where(ProposalChange.proposal_id_fk == p.id)
                                .order_by(ProposalChange.position)))


def as_dict(session, p: Proposal, full: bool = False, records: bool = False) -> dict:
    """A proposal as the API and the page show it. `full` adds each change's
    request and records (before → after); `records` the records alone, as
    the reply to a new proposal, so what else a change does shows at once."""
    rows = changes_of(session, p)
    requests = [json.loads(c.request_json or "{}") for c in rows]
    out = {"id": p.id, "status": p.status, "source": p.source, "summary": p.summary,
           "created_at": p.created_at.isoformat(timespec="seconds") + "Z",
           "expires_at": p.expires_at.isoformat(timespec="seconds") + "Z" if p.expires_at else None,
           "decided_at": p.decided_at.isoformat(timespec="seconds") + "Z" if p.decided_at else None,
           "replaces": p.replaces_id, "batch_id": p.batch_id_fk,
           "errors": [f"{c.position + 1}. {e}" for c in rows for e in json.loads(c.errors_json or "[]")],
           "warnings": [f"{c.position + 1}. {w}" for c in rows for w in json.loads(c.warnings_json or "[]")],
           "changes": [{"action": c.action, "area": c.area, "summary": c.summary, "ok": c.ok,
                        "errors": json.loads(c.errors_json or "[]"), "warnings": json.loads(c.warnings_json or "[]"),
                        **({"evidence": evidence_of(req)} if evidence_of(req) else {}),
                        **({"request": req} if full else {}),
                        **({"records": json.loads(c.records_json or "[]")} if full or records else {})}
                       for c, req in zip(rows, requests)]}
    out["captures"] = json.loads(p.captures_json or "[]")
    out["reason"] = p.reason or None
    if p.status == "sent_back":
        out["next"] = (f"The person sent this back: \"{p.reason}\". Correct it as they say and propose again, "
                       f"with replaces set to {p.id}.")
    return out


def _mine(session, proposal_id: int, username: str) -> Proposal:
    p = session.get(Proposal, proposal_id)
    if p is None or p.owner_username != username:
        raise ProposalError(gettext("There is no proposal #%(id)s of yours.", id=proposal_id))
    return p


def discard(session, proposal_id: int, username: str) -> Proposal:
    expire_old(session)
    p = _mine(session, proposal_id, username)
    if p.status not in ("pending", "invalid"):
        raise ProposalError(gettext("Proposal #%(id)s is already %(status)s.", id=p.id,
                                    status=i18n.translate_value(p.status, "proposal status")))
    p.status, p.decided_at = "discarded", datetime.utcnow()
    session.commit()
    return p


def send_back(session, proposal_id: int, username: str, reason: str) -> Proposal:
    """Not approved, with a one-line reason for the assistant to correct it."""
    expire_old(session)
    p = _mine(session, proposal_id, username)
    reason = " ".join((reason or "").split())[:MAX_REASON]
    if not reason:
        raise ProposalError(gettext("Say in a line what to correct."))
    if p.status not in ("pending", "invalid"):
        raise ProposalError(gettext("Proposal #%(id)s is already %(status)s.", id=p.id,
                                    status=i18n.translate_value(p.status, "proposal status")))
    p.status, p.decided_at, p.reason = "sent_back", datetime.utcnow(), reason
    session.commit()
    return p


def changed_since(session, p: Proposal) -> list[str]:
    """The records the preview changed (not made) that someone changed
    after it, as "Cage 88 (by alex, 10:50)"."""
    touched = {(r["table"], r["id"]) for c in changes_of(session, p)
               for r in json.loads(c.records_json or "[]") if r.get("action") != "create" and r.get("id")}
    out = _pages_changed_since(session, p, {i for t, i in touched if t == "notebook_pages"})
    touched = {(t, i) for t, i in touched if t != "notebook_pages"}
    if not touched:
        return out
    later = session.scalars(select(AuditEntry).where(AuditEntry.id > p.audit_mark,
                                                     AuditEntry.table_name.in_({t for t, _ in touched}))
                            .order_by(AuditEntry.id)).all()
    for e in later:
        if (e.table_name, e.record_id) in touched:
            from .app import local_time
            out.append(gettext("%(record)s (by %(who)s, %(when)s)", record=e.record_label or f"#{e.record_id}",
                               who=e.changed_by, when=local_time(e.changed_at).strftime("%H:%M")))
            touched.discard((e.table_name, e.record_id))
    return out


def _pages_changed_since(session, p: Proposal, page_ids: set) -> list[str]:
    """Notebook pages the preview changed (actions.notebook_page_change) that
    were saved after it: the notebook keeps no change history, so their
    last save tells."""
    if not page_ids:
        return []
    from .lab_notebook import info_for
    from .models import NotebookPage
    out = []
    for page in session.scalars(select(NotebookPage).where(NotebookPage.id.in_(page_ids),
                                                           NotebookPage.updated_at > p.created_at)):
        from .app import local_time
        out.append(gettext("%(record)s (by %(who)s, %(when)s)", record=page.title,
                           who=(info_for(session, page.id) or SimpleNamespace(edited_by="")).edited_by or "-",
                           when=local_time(page.updated_at).strftime("%H:%M")))
    return out


def approve(app, session, proposal_id: int, username: str) -> Proposal:
    """Apply a pending proposal as one batch, or raise ProposalError saying
    why nothing was applied."""
    expire_old(session)
    p = _mine(session, proposal_id, username)
    if p.status == "applying":
        raise ProposalError(gettext("Proposal #%(id)s is being approved already.", id=proposal_id))
    if p.status != "pending":
        raise ProposalError(gettext("Proposal #%(id)s is %(status)s, so it can't be approved.", id=p.id,
                                    status=i18n.translate_value(p.status, "proposal status")))
    stale = changed_since(session, p)
    if stale:
        raise ProposalError(gettext("Nothing was applied: %(records)s changed after this proposal was made. Discard it and ask for a fresh one.",  # noqa: E501
                                    records="; ".join(stale)))
    requests = _runnable([json.loads(c.request_json or "{}") for c in changes_of(session, p)])
    lang = i18n.language_for(session, username)
    # Claimed first, so a second Approve (a double click, another tab) finds it taken.
    claimed = session.execute(update(Proposal).where(Proposal.id == p.id, Proposal.status == "pending")
                              .values(status="applying")).rowcount
    session.commit()  # the outcome runs in its own transaction
    if not claimed:
        raise ProposalError(gettext("Proposal #%(id)s is being approved already.", id=proposal_id))
    try:
        outcome = actions.run(app, username, requests, apply=True, label=label(p), description=description(p),
                              lang=lang, source=p.source)
    except Exception:
        _release(session, proposal_id)
        raise
    if not outcome.ok:
        _release(session, proposal_id)
        raise ProposalError(gettext("Nothing was applied: %(problems)s Discard it and ask for a fresh one.",
                                    problems=" ".join(outcome.errors) or gettext("a change no longer goes through.")))
    p = session.get(Proposal, proposal_id)
    p.status, p.decided_at, p.batch_id_fk = "approved", datetime.utcnow(), outcome.batch_id
    from . import captures as lab_captures
    lab_captures.mark_used(session, json.loads(p.captures_json or "[]"), username, p.id, outcome.batch_id)
    session.commit()
    return p


def _release(session, proposal_id: int) -> None:
    session.rollback()
    session.execute(update(Proposal).where(Proposal.id == proposal_id, Proposal.status == "applying")
                    .values(status="pending"))
    session.commit()


# ---------------------------------------------------------------- Proposed changes (the page)

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for  # noqa: E402

bp = Blueprint("proposals", __name__)


def pending_count(session, username: str) -> int:
    return session.scalar(select(func.count(Proposal.id)).where(
        Proposal.owner_username == username, Proposal.status == "pending",
        (Proposal.expires_at.is_(None)) | (Proposal.expires_at >= datetime.utcnow()))) or 0


def has_any(session, username: str) -> bool:
    return session.scalar(select(Proposal.id).where(Proposal.owner_username == username).limit(1)) is not None


def _card(session, p: Proposal) -> dict:
    from .audit import table_label
    rows = changes_of(session, p)
    groups: dict[str, list[dict]] = {}
    for c in rows:
        records = []
        for r in json.loads(c.records_json or "[]"):
            fields = ((r.get("changes") or {}).get("changes") or {}) if isinstance(r.get("changes"), dict) else {}
            records.append({"table": table_label(r.get("table", "")), "label": r.get("label") or f"#{r.get('id')}",
                            "action": r.get("action", ""),
                            "fields": [(k, _shown(v[0]), _shown(v[1])) for k, v in fields.items()
                                       if isinstance(v, list) and len(v) == 2]})
        groups.setdefault(AREAS.get(c.area, c.area or "Other"), []).append({
            "summary": c.summary or c.action, "ok": c.ok, "errors": json.loads(c.errors_json or "[]"),
            "warnings": json.loads(c.warnings_json or "[]"), "records": records,
            "evidence": evidence_of(json.loads(c.request_json or "{}"))})
    warnings = [w for items in groups.values() for item in items for w in item["warnings"]]
    errors = [e for items in groups.values() for item in items for e in item["errors"]]
    from . import captures as lab_captures
    return {"p": p, "groups": groups, "count": len(rows), "warnings": warnings, "errors": errors,
            "captures": lab_captures.proposal_cards(session, p.owner_username, p.captures_json)}


def _shown(value) -> str:
    """A value as the change history stored it (audit._encode), for reading."""
    if isinstance(value, dict):
        value = value.get("__d__") or (value.get("__dt__") or "").replace("T", " ")[:16] or value
    if value in (None, ""):
        return "–"
    if value is True or value is False:
        return gettext("yes") if value else gettext("no")
    return str(value)


def _signed_in():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


@bp.route("/proposals")
def page():
    blocked = _signed_in()
    if blocked:
        return blocked
    from .db import SessionLocal
    with SessionLocal() as s:
        expire_old(s)
        s.commit()
        mine = select(Proposal).where(Proposal.owner_username == g.user.username)
        waiting = [_card(s, p) for p in s.scalars(mine.where(Proposal.status == "pending")
                                                   .order_by(Proposal.id.desc()).limit(50))]
        decided = [_card(s, p) for p in s.scalars(mine.where(Proposal.status != "pending")
                                                   .order_by(Proposal.id.desc()).limit(20))]
    return render_template("proposals.html", waiting=waiting, decided=decided, areas=AREAS)


@bp.route("/proposals/<int:proposal_id>/approve", methods=["POST"])
def approve_page(proposal_id: int):
    blocked = _signed_in()
    if blocked:
        return blocked
    from .db import SessionLocal
    with SessionLocal() as s:
        try:
            p = approve(current_app._get_current_object(), s, proposal_id, g.user.username)
        except ProposalError as error:
            flash(str(error), "error")
            return redirect(url_for("proposals.page") + f"#proposal-{proposal_id}")
        flash(gettext("Approved proposal #%(id)s: it is in Batch history, where it can be undone as one.",
                      id=p.id), "success")
    return redirect(url_for("proposals.page"))


@bp.route("/proposals/<int:proposal_id>/discard", methods=["POST"])
def discard_page(proposal_id: int):
    blocked = _signed_in()
    if blocked:
        return blocked
    from .db import SessionLocal
    with SessionLocal() as s:
        try:
            discard(s, proposal_id, g.user.username)
        except ProposalError as error:
            flash(str(error), "error")
            return redirect(url_for("proposals.page"))
    flash(gettext("Discarded proposal #%(id)s.", id=proposal_id), "success")
    return redirect(url_for("proposals.page"))


@bp.route("/proposals/<int:proposal_id>/send-back", methods=["POST"])
def send_back_page(proposal_id: int):
    blocked = _signed_in()
    if blocked:
        return blocked
    from .db import SessionLocal
    with SessionLocal() as s:
        try:
            send_back(s, proposal_id, g.user.username, request.form.get("reason", ""))
        except ProposalError as error:
            flash(str(error), "error")
            return redirect(url_for("proposals.page") + f"#proposal-{proposal_id}")
    flash(gettext("Sent proposal #%(id)s back: your assistant sees why when it next checks.", id=proposal_id),
          "success")
    return redirect(url_for("proposals.page"))
