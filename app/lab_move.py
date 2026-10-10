"""The whole lab, from the desktop app: saved as one file, or moved to a new
server (app/lab_transfer.py makes and reads the file; the server takes it in
at /lab/import, app/door.py).

  Save the whole lab     Settings → Your data, for an admin: a `.biomanager`
                         file, in Downloads on the desktop app, downloaded
                         from a server.
  Move this lab to a     Settings → Devices, the desktop app's admin: the
  server                 server's address and setup code. The lab goes there
                         whole; this computer then keeps a daily copy of it,
                         its window opens the server, and its own lab stays,
                         read only, as it was.
  Use this computer's    the way back: this computer's own lab is open and
  own lab again          writable again, exactly as it was when it moved (the
                         server keeps the lab it was given).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file,
                   url_for)

from . import devices, lab_copy, lab_transfer, paths, security
from .db import SessionLocal
from .i18n import gettext

bp = Blueprint("lab_move", __name__)


def _admin():
    if g.get("user") is None:
        abort(redirect(url_for("login")))
    if g.user.role != "admin":
        abort(403)


def _downloads() -> Path:
    folder = Path.home() / "Downloads"
    if not folder.is_dir():
        folder = paths.data_dir() / "exports"
        folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---------------------------------------------------------------- Save the whole lab

@bp.route("/settings/lab-file", methods=["POST"])
def save():
    _admin()
    with tempfile.TemporaryDirectory(prefix="lab-file-", dir=paths.data_dir()) as tmp:
        made = Path(tmp) / "lab.biomanager"
        manifest = lab_transfer.build(made, Path(tmp) / "work")
        name = lab_transfer.file_name(manifest)
        if devices.on_this_computer():
            target = _downloads() / name
            n = 2
            while target.exists():
                target = target.with_name(f"{Path(name).stem} ({n}){lab_transfer.SUFFIX}")
                n += 1
            shutil.move(str(made), str(target))
            flash(gettext("Saved the whole lab: %(path)s", path=str(target)), "success")
            return redirect(url_for("settings", saved_file=target.name) + "#data")
        data = made.read_bytes()
    from io import BytesIO
    return send_file(BytesIO(data), as_attachment=True, download_name=name, mimetype="application/zip")


@bp.route("/settings/lab-file/show", methods=["POST"])
def show():
    """Show a saved lab file in the computer's file manager."""
    _admin()
    if not devices.on_this_computer():
        abort(404)
    name = Path(request.form.get("name", "")).name
    target = _downloads() / name
    if not name.endswith(lab_transfer.SUFFIX) or not target.is_file():
        abort(404)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target.parent)])
    return redirect(url_for("settings") + "#data")


# ---------------------------------------------------------------- Move this lab to a server

def push(server: str, code: str, admin: str) -> dict:
    """Make the lab file and send it to `server`'s /lab/import. Returns the
    server's answer ({"ok", "lab", "key", …} or {"ok": False, "error", "problems"})."""
    with tempfile.TemporaryDirectory(prefix="lab-move-", dir=paths.data_dir()) as tmp:
        made = Path(tmp) / "lab.biomanager"
        lab_transfer.build(made, Path(tmp) / "work")
        size = made.stat().st_size
        with open(made, "rb") as body:
            req = Request(f"{server}/lab/import", data=body, method="POST", headers={
                "Content-Type": "application/zip", "Content-Length": str(size), "Accept": "application/json",
                "X-BioManager-Setup-Code": code, "X-BioManager-Admin": admin, "User-Agent": "BioManager desktop"})
            try:
                with urlopen(req, timeout=1800) as response:    # noqa: S310 (the server the admin typed)
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                try:
                    return json.loads(error.read().decode("utf-8"))
                except ValueError:
                    return {"ok": False, "problems": [],
                            "error": gettext("The server answered %(status)s. Is it a BioManager of the same version?", status=error.code)}
            except (URLError, OSError) as error:
                return {"ok": False, "problems": [], "error": lab_copy._reason(error)}


@bp.route("/settings/devices/move", methods=["GET", "POST"])
def move():
    """The desktop app's admin moves the lab to a new server."""
    from . import door
    _admin()
    if not devices.on_this_computer():
        abort(404)
    with SessionLocal() as s:
        st = devices.state(s)
    if st.get("phase") in devices.READ_ONLY:
        flash(gettext("This lab has already moved, or is moving."), "error")
        return redirect(url_for("devices.page"))
    address = (request.form.get("address") or "").strip()
    result = None
    if request.method == "POST":
        found = door.find_lab(address)
        if found is None:
            result = {"ok": False, "problems": [], "error": gettext("No BioManager answered at %(address)s. Check the address, and that this computer can reach it.", address=address or "—")}
        else:
            # Nothing may change here while the lab is on its way.
            devices._swapping.set()
            try:
                result = push(found["url"], request.form.get("setup_code", "").strip(), g.user.username)
            finally:
                devices._swapping.clear()
            if result.get("ok"):
                return _moved(found["url"], found["host"], result.get("key", ""))
    return render_template("lab_move/move.html", address=address, result=result)


def _moved(server: str, host: str, key: str):
    """After the server took the lab: this computer keeps a copy of it, opens
    it in its window, and its own lab stays as it was, read only."""
    _, set_setting = lab_copy._settings()
    with SessionLocal() as s:
        set_setting(s, lab_copy.SETTING_SERVER, server)
        if key:
            set_setting(s, lab_copy.SETTING_KEY, security.encrypt_text(key))
        set_setting(s, devices.SHARING, "")
        devices._save(s, devices.STATE, {"phase": devices.AWAY, "label": host, "url": server, "moved": True,
                                          "since": datetime.utcnow().isoformat(timespec="seconds")})
        s.commit()
    devices.stop_sharing()
    devices._save_prefs(window_url=server)
    return devices.open_in_window(server)


@bp.route("/settings/devices/move/back", methods=["POST"])
def move_back():
    """The way back after a move: this computer's own lab, open and
    writable again, as it was when it moved. The server keeps its lab."""
    _admin()
    if not devices.on_this_computer():
        abort(404)
    with SessionLocal() as s:
        st = devices.state(s)
        if st.get("phase") != devices.AWAY or not st.get("moved"):
            flash(gettext("This lab hasn't moved anywhere."), "error")
            return redirect(url_for("devices.page"))
        devices._save(s, devices.STATE, {})
        s.commit()
    devices._save_prefs(window_url="")
    flash(gettext("This computer’s own lab is open again, as it was when it moved. The server keeps the lab it was given; this computer still keeps a copy of it."), "success")
    return redirect(url_for("devices.page"))
