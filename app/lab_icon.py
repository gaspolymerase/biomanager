"""The lab's own icon, at the top of the sidebar in place of BioManager's.

An admin picks a picture in Settings → Lab → General (a PNG, JPEG or WebP,
never an SVG, which could carry a script); it is kept with the lab's
settings (app_settings), so it goes into backups and lab copies with them.
With Pillow here it is made a square PNG of ICON_SIDE; without, it is kept
as sent if it is small enough. A lab with none shows BioManager's icon.
"""
from __future__ import annotations

import base64
import hashlib
import io

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, request, url_for

from . import inventory_service
from .db import SessionLocal
from .i18n import gettext

bp = Blueprint("lab_icon", __name__)

KEY = "lab_icon"
VERSION_KEY = "lab_icon_version"     # read on every page, so the picture itself is not
ICON_SIDE = 160
MAX_UPLOAD = 4 * 1024 * 1024
MAX_KEPT = 256 * 1024
TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}


def _sniff(data: bytes) -> str:
    """The picture's type from its first bytes, whatever its name says."""
    for magic, mimetype in TYPES.items():
        if data.startswith(magic):
            return mimetype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _square_png(data: bytes) -> bytes | None:
    """The picture centred in a square PNG of ICON_SIDE, or None without
    Pillow (or when Pillow cannot read it)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGBA")
            im.thumbnail((ICON_SIDE, ICON_SIDE), Image.LANCZOS)
            square = Image.new("RGBA", (ICON_SIDE, ICON_SIDE), (0, 0, 0, 0))
            square.paste(im, ((ICON_SIDE - im.width) // 2, (ICON_SIDE - im.height) // 2))
            out = io.BytesIO()
            square.save(out, "PNG", optimize=True)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - any picture Pillow can't read is refused the same way
        return None


def fit(data: bytes) -> tuple[str, bytes] | str:
    """(type, bytes) to keep, or why not, in English for gettext."""
    mimetype = _sniff(data)
    if not mimetype:
        return "Choose a PNG, JPEG or WebP picture."
    if len(data) > MAX_UPLOAD:
        return "That picture is too large. Choose one under 4 MB."
    square = _square_png(data)
    if square is not None:
        return "image/png", square
    if len(data) > MAX_KEPT:
        return "That picture is too large. Choose one under 256 KB."
    return mimetype, data


def stored(session) -> tuple[str, bytes] | None:
    raw = inventory_service.get_setting(session, KEY, "")
    mimetype, _, body = raw.partition(";")
    if not body or mimetype not in (*TYPES.values(), "image/webp"):
        return None
    try:
        return mimetype, base64.b64decode(body)
    except ValueError:
        return None


def url(session) -> str:
    """Where the lab's icon is, or "" when it has none."""
    version = inventory_service.get_setting(session, VERSION_KEY, "")
    return url_for("lab_icon.icon", v=version) if version else ""


def put(session, mimetype: str, data: bytes) -> None:
    """Keep this picture as the lab's icon ("" data removes it)."""
    if not data:
        inventory_service.set_setting(session, KEY, "")
        inventory_service.set_setting(session, VERSION_KEY, "")
        return
    inventory_service.set_setting(session, KEY, f"{mimetype};{base64.b64encode(data).decode('ascii')}")
    inventory_service.set_setting(session, VERSION_KEY, hashlib.sha1(data).hexdigest()[:10])


@bp.get("/lab-icon")
def icon():
    if g.get("user") is None:
        abort(404)
    with SessionLocal() as session:
        found = stored(session)
    if found is None:
        abort(404)
    mimetype, data = found
    return Response(data, mimetype=mimetype, headers={
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff"})


@bp.post("/settings/lab-icon")
def save():
    if g.get("user") is None:
        abort(401)
    if g.user.role != "admin":
        abort(403)
    wants_json = request.headers.get("X-Autosave") == "1"
    with SessionLocal() as session:
        if request.form.get("remove"):
            put(session, "", b"")
            session.commit()
            message, ok = gettext("The lab icon is removed. BioManager's shows instead."), True
        else:
            upload = request.files.get("icon")
            result = fit(upload.read(MAX_UPLOAD + 1)) if upload else "Choose a PNG, JPEG or WebP picture."
            if isinstance(result, str):
                message, ok = gettext(result), False
            else:
                mimetype, data = result
                put(session, mimetype, data)
                session.commit()
                message, ok = gettext("Lab icon saved."), True
        icon_url = url(session)
    if wants_json:
        return jsonify({"ok": ok, "message": message, "icon": icon_url}), (200 if ok else 400)
    flash(message, "success" if ok else "error")
    return redirect(url_for("settings", _anchor="general"))
