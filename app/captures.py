"""Captures: what a person wrote down or photographed at the bench, waiting
to be turned into records.

Capture first, structure later: writing something down at the rack must
never wait for an AI. On Capture (`/captures/new`, from Bench mode, the
account menu or Captures) a person types or dictates a few lines and/or
takes a photo (the cage card, the whiteboard, their notebook) or picks a
small file (a balance's CSV, or a Sanger read: an .ab1, which only
verify_sequencing reads), and saves it, with the record it was taken
at when they came from one (Bench mode's experiment, or a card scanned on
the page). Later an assistant reads their waiting captures (`list_captures`,
`get_capture` in app/assistant_tools.py, over `GET /api/v1/captures`) and
proposes the records with `captures: [ids]` (app/proposals.py); approving
that proposal marks them used, linked to it and its batch. Dismiss sets
one aside unused. A capture is its person's only: nobody else sees it,
admins included.

The file is kept with the other uploads (uploads.save_uploaded_file) and
served only to its person, by `/captures/<id>/file`.
"""
from __future__ import annotations

import base64
import io
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import (Blueprint, Response, abort, flash, g, redirect, render_template, request, send_file,
                   url_for)
from sqlalchemy import case, func, select

from .i18n import gettext
from .models import Capture

STATUSES = ("waiting", "used", "dismissed")
MAX_TEXT = 10_000
MAX_FILE = 20 * 1024 * 1024          # what may be saved
SEND_IMAGE_MAX = 5 * 1024 * 1024     # what an assistant is sent, once made smaller where it can be
IMAGE_SIDE = 1600                    # a photo's longer side, as an assistant is sent it
TEXT_FILE_CHARS = 20_000             # how much of a CSV or text file an assistant is sent
# Photos an assistant can read, and that the page shows inline.
SENDABLE = {"image/jpeg", "image/png", "image/gif", "image/webp"}
SHOWN_INLINE = SENDABLE | {"image/heic", "image/heif"}
TEXT_FILES = {".csv": "text/csv", ".tsv": "text/tab-separated-values", ".txt": "text/plain",
              ".md": "text/markdown", ".json": "application/json",
              # Sanger reads as text, for verify_sequencing (app/seqverify.py)
              ".fa": "text/x-fasta", ".fasta": "text/x-fasta", ".seq": "text/plain"}
# A Sanger chromatogram: kept as it is, read only by verify_sequencing.
TRACE_FILES = {".ab1": "application/x-abi", ".abi": "application/x-abi"}


class CaptureError(ValueError):
    """Why a capture can't be saved or used, in words for the person."""


# ---------------------------------------------------------------- the record it was taken at

def _experiment_label(session, record_id: int) -> str:
    from .experiments import place_for
    from .models import Experiment
    exp = session.get(Experiment, record_id)
    if exp is None or place_for(session, exp.db or "colony") is None:
        return ""
    return f"Experiment {exp.name}"


def _row_label(session, kind: str, record_id: int) -> str:
    from . import lab
    from .models import CageRecord, MouseRecord, OrgHousing, StockUnit, TankRecord
    features = lab.request_features()
    if kind == "cage" and features.get("colony", True):
        row = session.get(CageRecord, record_id)
        return f"Cage {row.cage_id}" if row else ""
    if kind == "mouse" and features.get("colony", True):
        row = session.get(MouseRecord, record_id)
        return f"Mouse #{row.mouse_id}" if row else ""
    if kind == "tank" and features.get("zebrafish", True):
        row = session.get(TankRecord, record_id)
        return f"Tank {row.tank_id}" if row else ""
    if kind == "stock_unit":
        from . import stock_service
        row = session.get(StockUnit, record_id)
        visible = {m.id: m for m in stock_service.list_modules(session)}
        if row is None or row.module_id_fk not in visible:
            return ""
        return stock_service.view(visible[row.module_id_fk]).code(row)
    if kind == "org_housing":
        from . import organism_service
        row = session.get(OrgHousing, record_id)
        visible = {m.id: m for m in organism_service.list_modules(session)}
        if row is None or row.module_id_fk not in visible:
            return ""
        noun = organism_service.view(visible[row.module_id_fk]).housing_noun
        return f"{noun.capitalize()} {row.code or '#' + str(row.id)}"
    return ""


def at_label(session, kind: str, record_id) -> str:
    """What the record a capture was taken at is called ("Cage 88"), or ""
    when there is no such record this person can open."""
    if not isinstance(record_id, int) or isinstance(record_id, bool):
        return ""
    if kind == "experiment":
        return _experiment_label(session, record_id)
    return _row_label(session, kind, record_id)


def from_card(link: str) -> tuple[str, int | None]:
    """The record a scanned card (or Bench mode's code) is the link to: a
    card opens its sheet at #cage-12, #tank-3 or #unit-7 (app/labels.py)."""
    try:
        url = urlparse((link or "").strip())
    except ValueError:
        return "", None
    path, fragment = url.path or "", url.fragment or ""
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "experiments" and parts[1].isdigit() and parts[2] == "bench":
        return "experiment", int(parts[1])
    name, _, number = fragment.rpartition("-")
    if not number.isdigit():
        return "", None
    if name == "cage":
        return "cage", int(number)
    if name == "tank":
        return "tank", int(number)
    if name == "unit" and path.startswith("/stocks/"):
        return "stock_unit", int(number)
    if name == "unit" and path.startswith("/organisms/"):
        return "org_housing", int(number)
    return "", None


# ---------------------------------------------------------------- keeping and reading them

def _kind_of(filename: str, mimetype: str) -> str:
    """The file's type, from its name first (a phone may say octet-stream)."""
    suffix = Path(filename or "").suffix.lower()
    if suffix in TEXT_FILES:
        return TEXT_FILES[suffix]
    if suffix in TRACE_FILES:
        return TRACE_FILES[suffix]
    guessed = mimetypes.guess_type(filename or "")[0] or ""
    for candidate in (guessed, (mimetype or "").split(";")[0].strip().lower()):
        if candidate.startswith("image/") and candidate != "image/svg+xml":
            return candidate
    if suffix in (".heic", ".heif"):
        return "image/heic"
    return ""


def create(session, owner: str, text: str = "", upload=None, at_kind: str = "", at_id: int | None = None) -> Capture:
    """Keep a capture (commits), or raise CaptureError saying why not."""
    from . import uploads
    text = (text or "").strip()
    if len(text) > MAX_TEXT:
        raise CaptureError(gettext("That is more than %(n)s characters: save it in two.", n=MAX_TEXT))
    has_file = upload is not None and bool(upload.filename)
    if not text and not has_file:
        raise CaptureError(gettext("Write something or add a photo first."))
    c = Capture(owner_username=owner, text=text, status="waiting", created_at=datetime.utcnow())
    if has_file:
        kind = _kind_of(upload.filename, upload.mimetype)
        if not kind:
            raise CaptureError(gettext("Add a photo, or a CSV or text file."))
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > MAX_FILE:
            raise CaptureError(gettext("That file is over %(mb)s MB.", mb=MAX_FILE // (1024 * 1024)))
        saved = uploads.save_uploaded_file(upload)
        if not saved:
            raise CaptureError(gettext("Add a photo, or a CSV or text file."))
        c.file_path, c.file_name = saved["path"], (upload.filename or "")[:255]
        c.file_type, c.file_size = kind, saved["size_bytes"]
    label = at_label(session, at_kind, at_id) if at_kind else ""
    if label:
        c.at_kind, c.at_id, c.at_label = at_kind, at_id, label[:200]
    session.add(c)
    session.commit()
    return c


def mine(session, capture_id: int, username: str) -> Capture | None:
    c = session.get(Capture, capture_id)
    return c if c is not None and c.owner_username == username else None


def of(session, username: str, status: str = "waiting", limit: int = 50) -> list[Capture]:
    """A person's captures: one status, or "all" (waiting first), newest first."""
    stmt = select(Capture).where(Capture.owner_username == username)
    if status in STATUSES:
        stmt = stmt.where(Capture.status == status).order_by(Capture.id.desc())
    else:
        stmt = stmt.order_by(case((Capture.status == "waiting", 0), else_=1), Capture.id.desc())
    return list(session.scalars(stmt.limit(limit)))


def waiting_count(session, username: str) -> int:
    return session.scalar(select(func.count(Capture.id)).where(Capture.owner_username == username,
                                                               Capture.status == "waiting")) or 0


def has_any(session, username: str) -> bool:
    return session.scalar(select(Capture.id).where(Capture.owner_username == username).limit(1)) is not None


def is_image(c: Capture) -> bool:
    return (c.file_type or "").startswith("image/")


def is_trace(c: Capture) -> bool:
    return (c.file_type or "") in TRACE_FILES.values()


def is_text(c: Capture) -> bool:
    return bool(c.file_path) and not is_image(c) and not is_trace(c)


def file_on_disk(c: Capture) -> Path | None:
    from . import uploads
    if not c.file_path:
        return None
    path = Path(uploads.UPLOAD_DIR) / Path(c.file_path).name
    return path if path.is_file() else None


def _iso(when: datetime | None) -> str | None:
    return when.isoformat(timespec="seconds") + "Z" if when else None


def as_dict(c: Capture) -> dict:
    """A capture as the API lists it."""
    return {"id": c.id, "status": c.status, "text": c.text, "created_at": _iso(c.created_at),
            "at": {"kind": c.at_kind, "id": c.at_id, "label": c.at_label} if c.at_kind else None,
            "file": {"name": c.file_name, "type": c.file_type, "size": c.file_size} if c.file_path else None,
            "used_by_proposal": c.proposal_id_fk, "batch_id": c.batch_id_fk, "decided_at": _iso(c.decided_at)}


def _pil():
    """Pillow, when this install has it (it isn't required)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    return Image, ImageOps


def _fit_image(data: bytes, mimetype: str) -> tuple[bytes | None, str, str]:
    """A photo as an assistant is sent it: no longer than IMAGE_SIDE when
    Pillow is here, never more than SEND_IMAGE_MAX. (data, type, why not)."""
    pil = _pil()
    if pil is not None:
        Image, ImageOps = pil
        try:
            with Image.open(io.BytesIO(data)) as im:
                im = ImageOps.exif_transpose(im)
                if max(im.size) > IMAGE_SIDE or len(data) > SEND_IMAGE_MAX or mimetype not in SENDABLE:
                    im.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
                    out = io.BytesIO()
                    im.convert("RGB").save(out, "JPEG", quality=85)
                    data, mimetype = out.getvalue(), "image/jpeg"
        except Exception:  # noqa: BLE001 — a photo Pillow can't open is sent as it is, if it can be
            pass
    if mimetype not in SENDABLE:
        return None, mimetype, ("This photo's format (%s) can't be sent to an assistant: ask the person what it "
                                "shows, or to save it again as a JPEG." % mimetype)
    if len(data) > SEND_IMAGE_MAX:
        return None, mimetype, ("This photo is too big to send (%.1f MB; at most %d MB): ask the person what it "
                                "shows, or to take it again smaller." % (len(data) / 1048576,
                                                                         SEND_IMAGE_MAX // 1048576))
    return data, mimetype, ""


def _read_text(path: Path) -> tuple[str, bool]:
    raw = path.read_bytes()[:TEXT_FILE_CHARS * 4]
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return text[:TEXT_FILE_CHARS], len(text) > TEXT_FILE_CHARS or path.stat().st_size > len(raw)


def for_assistant(c: Capture) -> dict:
    """A capture with what is in it: a photo as base64 (`image`), a CSV or
    text file's text (`file_text`), or `file_note` saying why not."""
    out = as_dict(c)
    path = file_on_disk(c)
    if c.file_path and path is None:
        out["file_note"] = "The file of this capture is missing."
    elif path is not None and is_trace(c):
        out["file_note"] = ("A Sanger sequencing read (an ABI chromatogram): check it against a plasmid's map with "
                            f"verify_sequencing, captures [{c.id}].")
    elif path is not None and is_image(c):
        data, mimetype, why = _fit_image(path.read_bytes(), c.file_type)
        if data is None:
            out["file_note"] = why
        else:
            out["image"] = {"mimeType": mimetype, "data": base64.b64encode(data).decode("ascii")}
    elif path is not None:
        text, cut = _read_text(path)
        out["file_text"] = text
        if cut:
            out["file_note"] = f"Only the first {TEXT_FILE_CHARS} characters of the file are here."
    return out


def check_ids(session, owner: str, ids) -> list[int]:
    """The captures a proposal names, all the owner's; or CaptureError."""
    if ids in (None, ""):
        return []
    if not isinstance(ids, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
        raise CaptureError("captures is a list of capture ids (from list_captures).")
    out = []
    for capture_id in dict.fromkeys(ids):
        if mine(session, capture_id, owner) is None:
            raise CaptureError(f"There is no capture {capture_id} of yours.")
        out.append(capture_id)
    return out


def mark_used(session, ids: list[int], owner: str, proposal_id: int, batch_id: int | None) -> None:
    """A proposal made from these was approved (doesn't commit)."""
    now = datetime.utcnow()
    for capture_id in ids:
        c = mine(session, capture_id, owner)
        if c is not None:
            c.status, c.decided_at, c.proposal_id_fk, c.batch_id_fk = "used", now, proposal_id, batch_id


def card(session, c: Capture) -> dict:
    """A capture as the pages show it (Captures, Proposed changes)."""
    return {"c": c, "image": is_image(c) and c.file_type in SHOWN_INLINE, "file": bool(c.file_path),
            "url": url_for("captures.file", capture_id=c.id) if c.file_path else ""}


# ---------------------------------------------------------------- the pages

bp = Blueprint("captures", __name__)


@bp.app_context_processor
def _inject():
    """Captures in the account menu, with how many are waiting."""
    def captures_waiting():
        user = g.get("user")
        if user is None or request.path.startswith("/api/"):
            return 0
        from .db import SessionLocal
        with SessionLocal() as s:
            return waiting_count(s, user.username)
    return {"captures_waiting": captures_waiting}


def _signed_in():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
    return None


@bp.route("/captures")
def page():
    blocked = _signed_in()
    if blocked:
        return blocked
    from .db import SessionLocal
    with SessionLocal() as s:
        rows = [card(s, c) for c in of(s, g.user.username, "all", 60)]
        has_assistant = _has_assistant(s, g.user)
    waiting = [r for r in rows if r["c"].status == "waiting"]
    decided = [r for r in rows if r["c"].status != "waiting"][:30]
    return render_template("captures.html", waiting=waiting, decided=decided, has_assistant=has_assistant)


def _has_assistant(session, user) -> bool:
    from . import proposals
    from .models import ApiToken
    return proposals.has_any(session, user.username) or session.scalar(
        select(ApiToken.id).where(ApiToken.user_id_fk == user.id, ApiToken.revoked_at.is_(None)).limit(1)) is not None


def _safe_back(raw: str) -> str:
    raw = (raw or "").strip()
    return raw if raw.startswith("/") and not raw.startswith("//") else ""


@bp.route("/captures/new", methods=["GET", "POST"])
def new():
    blocked = _signed_in()
    if blocked:
        return blocked
    from .db import SessionLocal
    source = request.form if request.method == "POST" else request.args
    kind = (source.get("kind") or "").strip()[:40]
    record_id = int(source.get("id")) if (source.get("id") or "").isdigit() else None
    card_link = (source.get("card") or "").strip()
    if card_link:
        kind, record_id = from_card(card_link)
    back = _safe_back(source.get("back", ""))
    with SessionLocal() as s:
        label = at_label(s, kind, record_id) if kind else ""
        if request.method == "POST":
            try:
                c = create(s, g.user.username, request.form.get("text", ""),
                           request.files.get("photo") if request.files.get("photo") and request.files["photo"].filename
                           else request.files.get("file"), kind, record_id)
            except CaptureError as error:
                flash(str(error), "error")
                return render_template("capture_new.html", text=request.form.get("text", ""), kind=kind,
                                       record_id=record_id, label=label, back=back), 400
            flash(gettext("Saved as capture #%(id)s. Ask your assistant to process your captures when you're ready.",
                          id=c.id), "success")
            return redirect(back or url_for("captures.page"))
    return render_template("capture_new.html", text="", kind=kind, record_id=record_id, label=label, back=back)


@bp.post("/captures/<int:capture_id>/dismiss")
def dismiss(capture_id: int):
    blocked = _signed_in()
    if blocked:
        return blocked
    from .db import SessionLocal
    with SessionLocal() as s:
        c = mine(s, capture_id, g.user.username)
        if c is None:
            abort(404)
        if c.status == "waiting":
            c.status, c.decided_at = "dismissed", datetime.utcnow()
            s.commit()
            flash(gettext("Dismissed capture #%(id)s.", id=capture_id), "success")
    return redirect(url_for("captures.page"))


@bp.post("/captures/<int:capture_id>/restore")
def restore(capture_id: int):
    """A dismissed capture waits again."""
    blocked = _signed_in()
    if blocked:
        return blocked
    from .db import SessionLocal
    with SessionLocal() as s:
        c = mine(s, capture_id, g.user.username)
        if c is None:
            abort(404)
        if c.status == "dismissed":
            c.status, c.decided_at = "waiting", None
            s.commit()
    return redirect(url_for("captures.page") + f"#capture-{capture_id}")


@bp.get("/captures/<int:capture_id>/file")
def file(capture_id: int):
    """A capture's photo or file, for its person only."""
    blocked = _signed_in()
    if blocked:
        return blocked
    from .db import SessionLocal
    with SessionLocal() as s:
        c = mine(s, capture_id, g.user.username)
        path = file_on_disk(c) if c is not None else None
        if path is None:
            abort(404)
        inline = c.file_type in SHOWN_INLINE
        name, mimetype = c.file_name or path.name, (c.file_type if inline else "text/plain")
    response: Response = send_file(path, mimetype=mimetype, as_attachment=not inline and request.args.get("view") != "1",
                                   download_name=name, max_age=0)
    response.headers["Cache-Control"] = "private, max-age=3600"
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'"
    return response


def proposal_cards(session, owner: str, ids_json: str) -> list[dict]:
    """The captures a proposal was made from, for Proposed changes."""
    try:
        ids = [i for i in json.loads(ids_json or "[]") if isinstance(i, int)]
    except ValueError:
        return []
    return [card(session, c) for c in (mine(session, i, owner) for i in ids) if c is not None]
