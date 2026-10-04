"""Devices that work on one lab, and which of them holds its master copy.

One database is the lab's master copy. Every other device works on it
through the master's address, so nothing is ever merged and nothing written
in two places can be lost. A lab server is the master unless an admin hands
the role to a desktop; a desktop that was handed it can give it back.

  A lab server         the master by default. Settings → Devices lists the
                       desktops linked to it by a key (app/lab_copy.py).
  A desktop on its own its own lab, private to the computer (the default).
  A desktop sharing    its lab on the lab's network (Share this lab on the
                       network): the master for every device that opens its
                       address. It makes keys for other desktops, as a
                       server does.
  A desktop linked     to a master by its address and a key: it keeps a copy
                       (app/lab_copy.py), can open the lab in its window
                       instead of its own, and says hello every minute, so
                       the master's Devices page shows it.

Handing the master role to a linked desktop (its key an admin's, the same
version of BioManager), from the master's Devices page:
  1. the master marks it asked; the desktop sees that when it next says
     hello (every minute while it is open);
  2. the desktop freezes the master (read only, so no change can be lost),
     takes a last whole copy and its files, keeps its own data as a backup
     (backups/before-taking-over-…db), opens the copy as its database and
     shares it on its network;
  3. it tells the master its address. The master stays read only and sends
     everyone there.
Giving it back: the desktop freezes itself, sends its database and any new
files to the device it came from, which loads them in one transaction
(keeping a copy of what it had in backups/) and is the master again; the
desktop stays read only and opens the master in its window.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   url_for)
from sqlalchemy import Integer, create_engine, func, select, text

from . import lab_copy
from .i18n import gettext
from .db import Base, SessionLocal, engine
from .models import AppSetting, LabCopyKey, UserAccount
from .paths import data_dir, uploads_dir

bp = Blueprint("devices", __name__)

STATE = "devices:state"            # on a master: {"phase", "key_id", "label", "url", "since", "error"}
CAME_FROM = "devices:came_from"    # on a desktop that took the role over: {"url", "label"}
SHARING = "devices:sharing"        # "1" on a desktop sharing its lab on the network
LAST_HELLO = "devices:last_hello"  # on a linked desktop: what the master last said
# Settings that belong to the device, not the lab: kept when a database
# moves between devices, and never carried to the other one.
LOCAL_SETTINGS = ("devices:", "lab_copy_", "telemetry:", "server_setup")

# Phases on a master. "asked": handing over, still writable; the others are
# read only.
ASKED, FROZEN, AWAY, RECEIVING = "asked", "frozen", "away", "receiving"
READ_ONLY = (FROZEN, AWAY, RECEIVING)
SEEN_WITHIN = timedelta(minutes=10)   # a desktop heard from this recently is "open now"
HELLO_EVERY = 60                      # seconds, on a linked desktop
LAN_PORT = 5870
ALLOWED_WHILE_READ_ONLY = ("/static/", "/api/devices/", "/api/lab-copy/", "/login", "/logout", "/app-icon/",
                           "/settings/devices")

_swapping = threading.Event()         # this desktop is opening another database right now


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _settings():
    from .inventory_service import get_setting, set_setting
    return get_setting, set_setting


def _load(s, key: str) -> dict:
    get_setting, _ = _settings()
    try:
        value = json.loads(get_setting(s, key, "") or "{}")
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _save(s, key: str, value: dict) -> None:
    _, set_setting = _settings()
    set_setting(s, key, json.dumps(value) if value else "")


def version() -> str:
    from .feedback import app_version
    return app_version()


def is_desktop() -> bool:
    return bool(current_app.config.get("LOCAL_SETUP"))


def via_network() -> bool:
    """A request that reached a sharing desktop from another device (the
    network listener marks them), not from the person at the computer."""
    return request.environ.get("biomanager.lan") == "1"


def on_this_computer() -> bool:
    """The desktop app's own window, on this computer: where what only the
    computer's owner may do (its server set-up, its copies, its devices) is
    allowed. Never true on a server or from the network."""
    return is_desktop() and not via_network() and (request.remote_addr or "") in ("127.0.0.1", "::1")


def state(s) -> dict:
    return _load(s, STATE)


def phase(s) -> str:
    return state(s).get("phase", "")


def sharing(s) -> bool:
    get_setting, _ = _settings()
    return get_setting(s, SHARING, "") == "1"


def came_from(s) -> dict:
    return _load(s, CAME_FROM)


def is_master(s) -> bool:
    """This device holds the lab's master copy: a server, or a desktop
    sharing its lab, unless it handed the role on."""
    if phase(s) == AWAY:
        return False
    return not is_desktop() or sharing(s)


def is_admin() -> bool:
    user = g.get("user")
    return user is not None and user.role == "admin"


# ---------------------------------------------------------------------------
# Read only while the master copy moves, and once it has moved
# ---------------------------------------------------------------------------


@bp.before_app_request
def refuse_changes_while_read_only():
    """No change while the lab's master copy is moving (it would be left
    behind) or once it lives on another device. Reading stays possible."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        if _swapping.is_set() and not request.path.startswith(ALLOWED_WHILE_READ_ONLY):
            return render_template("devices/busy.html"), 503
        return None
    if request.path.startswith(ALLOWED_WHILE_READ_ONLY):
        return None
    if _swapping.is_set():
        why = gettext("This computer is opening the lab's master copy. Try again in a moment.")
    else:
        with SessionLocal() as s:
            st = state(s)
        if st.get("phase") not in READ_ONLY:
            return None
        why = notice(st)["text"]
    if request.headers.get("X-Autosave") == "1" or request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": False, "error": why}), 409
    flash(why, "error")
    back = request.referrer or ""
    return redirect(back if back.startswith(request.host_url) else url_for("index"))


def notice(st: dict) -> dict:
    """What the banner says about the master copy on this device, if anything."""
    where = st.get("label") or gettext("another computer")
    p = st.get("phase", "")
    if p == ASKED:
        return {"tone": "info", "text": gettext("Handing the lab's master copy to %(where)s: it takes it over the next time it is open, in a minute or so. Until then everything works as usual.", where=where)}
    if p == FROZEN:
        return {"tone": "warn", "text": gettext("Read only: the lab's master copy is moving to %(where)s. Nothing can be changed here until it has, so no change is lost.", where=where)}
    if p == AWAY:
        return {"tone": "warn", "url": st.get("url", ""),
                "text": gettext("Read only: the lab's master copy is now on %(where)s. Work there; what you see here is the lab as it was when it moved.", where=where)}
    if p == RECEIVING:
        return {"tone": "warn", "text": gettext("Read only for a moment: the lab's master copy is coming back from %(where)s.", where=where)}
    if st.get("error"):
        return {"tone": "error", "text": st["error"]}
    return {}


@bp.app_context_processor
def inject():
    def banner():
        try:
            with SessionLocal() as s:
                return notice(state(s))
        except Exception:  # noqa: BLE001 — a page should never fail over its banner
            return {}
    return {"devices_banner": banner}


# ---------------------------------------------------------------------------
# The master: the devices linked to it, and handing the role on
# ---------------------------------------------------------------------------


def linked_devices(s) -> list[dict]:
    """Every desktop with a key to this lab, what it last said about itself,
    and whether it could take the master copy over now."""
    now = datetime.utcnow()
    mine = version()
    owners = {u.id: u for u in s.scalars(select(UserAccount))}
    st = state(s)
    rows = []
    for k in s.scalars(select(LabCopyKey).order_by(LabCopyKey.label)):
        owner = owners.get(k.user_id_fk)
        open_now = k.last_seen_at is not None and now - k.last_seen_at < SEEN_WITHIN
        why_not = ""
        if k.revoked_at is not None:
            why_not = gettext("Its key was revoked.")
        elif owner is None or owner.role != "admin":
            why_not = gettext("Its key is a member's: only a computer with an admin's key can hold the whole lab.")
        elif not k.device_role:
            why_not = gettext("It hasn't been heard from since this version: open BioManager on it.")
        elif not open_now:
            why_not = gettext("It isn't open now: open BioManager on it first.")
        elif k.device_version != mine:
            why_not = gettext("It runs BioManager %(theirs)s, this device %(mine)s: update both to the same version.",
                              theirs=k.device_version or "?", mine=mine)
        rows.append({"key": k, "owner": owner.username if owner else "?", "open_now": open_now,
                     "can_take_over": not why_not and st.get("phase", "") in ("",),
                     "why_not": why_not, "is_target": st.get("key_id") == k.id})
    return rows


def _master_only(s):
    if not is_master(s) and phase(s) != AWAY:
        abort(404)
    if not is_admin() or (is_desktop() and not on_this_computer()):
        abort(403)


@bp.route("/settings/devices")
def page():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    with SessionLocal() as s:
        st = state(s)
        devices = linked_devices(s) if is_admin() else []
        here = desktop_status(s) if is_desktop() and on_this_computer() else None
        master = {"is_master": is_master(s), "phase": st.get("phase", ""), "state": st,
                  "notice": notice(st), "came_from": came_from(s), "sharing_url": share_url(s),
                  # Making this the master again suits a server or a desktop that shared
                  # its lab; a desktop that gave the lab back goes back to its own instead.
                  "can_take_back": not is_desktop() or sharing(s),
                  "own_backup": bool(st.get("own_backup")) and Path(st.get("own_backup", "")).is_file()}
    return render_template("devices/index.html", devices=devices, master=master, here=here,
                           is_admin=is_admin(), is_desktop=is_desktop(), version=version())


@bp.route("/settings/devices/<int:key_id>/make-master", methods=["POST"])
def make_master(key_id: int):
    with SessionLocal() as s:
        _master_only(s)
        row = next((d for d in linked_devices(s) if d["key"].id == key_id), None)
        if row is None:
            abort(404)
        if phase(s):
            flash(gettext("The master copy is already being handed over. Cancel that first."), "error")
            return redirect(url_for("devices.page"))
        label = row["key"].label
        if not row["can_take_over"]:
            flash(gettext("%(device)s can't take the master copy over: %(why)s", device=label, why=row["why_not"]),
                  "error")
            return redirect(url_for("devices.page"))
        _save(s, STATE, {"phase": ASKED, "key_id": key_id, "label": label,
                         "since": datetime.utcnow().isoformat(timespec="seconds")})
        s.commit()
    flash(gettext("%(device)s takes the master copy over in a minute or so, while BioManager is open on it.",
                  device=label), "success")
    return redirect(url_for("devices.page"))


@bp.route("/settings/devices/cancel", methods=["POST"])
def cancel():
    """Stop a hand-over that hasn't finished, or, once it has, make this
    device the master again without what was changed on the other one."""
    with SessionLocal() as s:
        _master_only(s)
        st = state(s)
        if st.get("phase") == RECEIVING:
            flash(gettext("The master copy is arriving right now; wait a moment."), "error")
            return redirect(url_for("devices.page"))
        _save(s, STATE, {})
        s.commit()
    if st.get("phase") == AWAY:
        flash(gettext("This is the lab's master copy again. What was changed on %(device)s since it took over is not here.", device=st.get("label") or gettext("the other computer")), "success")
    else:
        flash(gettext("Hand-over cancelled; this is still the lab's master copy."), "success")
    return redirect(url_for("devices.page"))


# --- what linked desktops call, with their key ----------------------------


def _key_and_state(s):
    k, _user = lab_copy._authorised(s)
    return k, state(s)


@bp.route("/api/devices/hello", methods=["POST"])
def hello():
    """A linked desktop says what it is; the master answers whether it is
    being handed the master copy."""
    body = request.get_json(silent=True) or {}
    with SessionLocal() as s:
        k, st = _key_and_state(s)
        k.device_role = str(body.get("role", ""))[:20]
        k.device_version = str(body.get("version", ""))[:40]
        k.device_url = str(body.get("url", ""))[:300]
        k.last_seen_at = datetime.utcnow()
        s.commit()
        for_me = st.get("key_id") == k.id
        return jsonify({"ok": True, "version": version(), "phase": st.get("phase", "") if for_me else "",
                        "master": is_master(s), "for_me": for_me, "label": k.label})


def _handover_key(s, *phases):
    k, st = _key_and_state(s)
    if st.get("key_id") != k.id or st.get("phase") not in phases:
        abort(409)
    return k, st


@bp.errorhandler(409)
def _conflict(_e):
    return jsonify({"ok": False, "error": "This computer isn't being handed the master copy now."}), 409


@bp.route("/api/devices/handover/freeze", methods=["POST"])
def handover_freeze():
    with SessionLocal() as s:
        _k, st = _handover_key(s, ASKED, FROZEN)
        st["phase"] = FROZEN
        _save(s, STATE, st)
        s.commit()
    return jsonify({"ok": True})


@bp.route("/api/devices/handover/done", methods=["POST"])
def handover_done():
    body = request.get_json(silent=True) or {}
    url = str(body.get("url", "")).strip()[:300]
    with SessionLocal() as s:
        _k, st = _handover_key(s, FROZEN)
        st.update(phase=AWAY, url=url, since=datetime.utcnow().isoformat(timespec="seconds"))
        _save(s, STATE, st)
        s.commit()
    return jsonify({"ok": True})


@bp.route("/api/devices/handover/abort", methods=["POST"])
def handover_abort():
    body = request.get_json(silent=True) or {}
    with SessionLocal() as s:
        _k, st = _handover_key(s, ASKED, FROZEN)
        why = str(body.get("error", ""))[:300]
        _save(s, STATE, {"error": f"{st.get('label', 'The computer')} couldn't take the master copy over"
                                  + (f": {why}" if why else ".") + " This is still the lab's master copy."})
        s.commit()
    return jsonify({"ok": True})


# --- giving the master copy back ------------------------------------------


@bp.route("/api/devices/return/files")
def return_files():
    """The uploaded files this device already has: the desktop sends the rest."""
    with SessionLocal() as s:
        _handover_key(s, AWAY)
    return jsonify({"ok": True, "files": lab_copy._upload_files()})


@bp.route("/api/devices/return/files/<path:name>", methods=["PUT"])
def return_file(name: str):
    with SessionLocal() as s:
        _handover_key(s, AWAY)
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts or any(p.startswith(".") for p in rel.parts):
        abort(400)
    dest = uploads_dir() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with open(part, "wb") as out:
        shutil.copyfileobj(request.stream, out, 1 << 20)
    part.replace(dest)
    return jsonify({"ok": True})


@bp.route("/api/devices/return", methods=["POST"])
def receive():
    """The desktop holding the master copy sends it back: its whole
    database, as a SQLite file. Loaded in the background; the desktop
    asks hello until this device is the master again."""
    digest = request.headers.get("X-BioManager-SHA256", "")
    with SessionLocal() as s:
        _k, st = _handover_key(s, AWAY)
        if request.headers.get("X-BioManager-Version", "") != version():
            return jsonify({"ok": False, "error": f"This device runs BioManager {version()}; update the computer "
                                                  "to the same version first."}), 409
    fd, name = tempfile.mkstemp(prefix="biomanager-return-", suffix=".db")
    with os.fdopen(fd, "wb") as out:
        shutil.copyfileobj(request.stream, out, 1 << 20)
    path = Path(name)
    if not digest or lab_copy._sha256(path) != digest:
        path.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": "The database arrived damaged (its checksum did not match)."}), 400
    with SessionLocal() as s:
        st["phase"] = RECEIVING
        _save(s, STATE, st)
        s.commit()
    app = current_app._get_current_object()
    threading.Thread(target=_receive_in_app, args=(app, path, st), daemon=True).start()
    return jsonify({"ok": True})


def _receive_in_app(app, path: Path, st: dict) -> None:
    with app.app_context():
        try:
            backup = keep_a_copy("before-taking-back")
            load_lab(path)
            with SessionLocal() as s:
                _save(s, STATE, {})
                s.commit()
            app.logger.warning("devices: the master copy is back from %s (what was here is in %s)",
                               st.get("label"), backup)
        except Exception as error:  # noqa: BLE001 — becomes the banner; the data is as it was
            app.logger.exception("devices: taking the master copy back failed")
            with SessionLocal() as s:
                st.update(phase=AWAY, error=f"Taking the master copy back failed: {error}. Nothing here changed.")
                _save(s, STATE, st)
                s.commit()
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Moving a whole database in and out (both sides)
# ---------------------------------------------------------------------------


def _local_settings(conn) -> list[dict]:
    rows = conn.execute(select(AppSetting.__table__)).mappings()
    return [dict(r) for r in rows if str(r["key"]).startswith(LOCAL_SETTINGS)]


def keep_a_copy(why: str) -> Path:
    """This device's whole database, as it is, in <data>/backups/: on the
    desktop the file itself (SQLite's backup, every value kept), on a server
    a whole copy in the desktop app's format (stored secrets blanked, as in
    any copy; the server's own nightly backup has them)."""
    folder = data_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{why}-{datetime.utcnow():%Y%m%d-%H%M%S}.db"
    if engine.dialect.name == "sqlite" and engine.url.database:
        with sqlite3.connect(engine.url.database) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
    else:
        lab_copy.write_snapshot(target)
    return target


def load_lab(path: Path) -> dict[str, int]:
    """Replace this device's records with the lab in `path` (a SQLite file
    of this version's schema), in one transaction: all or nothing. Settings
    that belong to this device (LOCAL_SETTINGS) stay as they are here."""
    tables = list(Base.metadata.sorted_tables)
    source = create_engine(f"sqlite:///{path}")
    counts: dict[str, int] = {}
    try:
        with source.connect() as src, engine.begin() as out:
            if out.dialect.name == "postgresql":
                out.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            else:
                out.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
            keep = _local_settings(out)
            for table in reversed(tables):
                out.execute(table.delete())
            for table in tables:
                rows = [dict(r) for r in src.execute(select(table)).mappings()]
                if table.name == AppSetting.__tablename__:
                    rows = [r for r in rows if not str(r["key"]).startswith(LOCAL_SETTINGS)] + keep
                for start in range(0, len(rows), 1000):
                    out.execute(table.insert(), rows[start:start + 1000])
                counts[table.name] = len(rows)
            if out.dialect.name == "postgresql":
                for table in tables:
                    pk = list(table.primary_key.columns)
                    if len(pk) != 1 or not isinstance(pk[0].type, Integer):
                        continue
                    seq = out.execute(text("SELECT pg_get_serial_sequence(:t, :c)"),
                                      {"t": f'"{table.name}"', "c": pk[0].name}).scalar()
                    highest = out.execute(select(func.coalesce(func.max(pk[0]), 0))).scalar() or 0
                    if seq and highest:
                        out.execute(text("SELECT setval(:s, GREATEST(:v, (SELECT last_value FROM "
                                         + seq + ")))"), {"s": seq, "v": highest})
    finally:
        source.dispose()
    return counts


def install_copy(snapshot: Path, uploads_from: Path | None = None) -> Path:
    """Open a whole copy of the lab as this desktop's database. What this
    computer had is kept first, in backups/before-taking-over-…db; uploaded
    files it lacks are copied in (none are removed). Returns the backup."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Only the desktop app opens a copy as its database.")
    _swapping.set()
    try:
        backup = keep_a_copy("before-taking-over")
        load_lab(snapshot)
        if uploads_from is not None and uploads_from.is_dir():
            for item in uploads_from.rglob("*"):
                if item.is_file() and not item.name.endswith(".part"):
                    dest = uploads_dir() / item.relative_to(uploads_from)
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, dest)
        return backup
    finally:
        _swapping.clear()


# ---------------------------------------------------------------------------
# Desktop: sharing its lab on the network
# ---------------------------------------------------------------------------

_lan: dict = {"server": None, "url": ""}


def lan_address() -> str:
    """This computer's address on the network it is on (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))   # TEST-NET-1: only picks the route
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def share_url(s=None) -> str:
    """Where other devices open this desktop's lab, while it shares it."""
    return _lan["url"] if _lan["server"] is not None else ""


def start_sharing(app, port: int | None = None) -> str:
    """Listen on the network as well as on this computer. Requests that
    arrive that way are marked (via_network), so they never reach what only
    the computer's owner may do."""
    if _lan["server"] is not None:
        return _lan["url"]
    from werkzeug.serving import make_server

    def marked(environ, start_response):
        environ["biomanager.lan"] = "1"
        return app(environ, start_response)

    port = port or _prefs().get("lan_port") or LAN_PORT
    server = None
    for candidate in (port, *range(LAN_PORT + 1, LAN_PORT + 20)):
        try:
            server = make_server("0.0.0.0", int(candidate), marked, threaded=True)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("No free port to share the lab on.")
    threading.Thread(target=server.serve_forever, name="lab-network", daemon=True).start()
    _lan.update(server=server, url=f"http://{lan_address()}:{port}")
    _save_prefs(lan_port=port)
    # Labels' QR codes and keys' instructions name the address others use.
    os.environ.setdefault("BIOMANAGER_BASE_URL", _lan["url"])
    return _lan["url"]


def stop_sharing() -> None:
    server = _lan.get("server")
    if server is not None:
        threading.Thread(target=server.shutdown, daemon=True).start()
    if os.environ.get("BIOMANAGER_BASE_URL") == _lan.get("url"):
        os.environ.pop("BIOMANAGER_BASE_URL", None)
    _lan.update(server=None, url="")


def _prefs() -> dict:
    try:
        import desktop_updates
        return desktop_updates.load_prefs()
    except ImportError:
        return {}


def _save_prefs(**changes) -> None:
    try:
        import desktop_updates
        desktop_updates.save_prefs(**changes)
    except ImportError:
        pass


def window_url() -> str:
    """The lab the desktop window opens instead of this computer's own, if
    any (Open the lab in this window)."""
    return str(_prefs().get("window_url") or "")


# ---------------------------------------------------------------------------
# Desktop: linked to a master — hello, taking over, giving back
# ---------------------------------------------------------------------------


def _call(server: str, key: str, path: str, body: dict | None = None, method: str = "POST", timeout: int = 60):
    """One request to the master, with the key; tests replace it."""
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = Request(f"{server}{path}", data=data, method=method,
                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                           "User-Agent": "BioManager desktop"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def _send(server: str, key: str, path: str, file: Path, headers: dict, method: str = "POST", timeout: int = 600):
    with open(file, "rb") as f:
        req = Request(f"{server}{path}", data=f, method=method,
                      headers={"Authorization": f"Bearer {key}", "Content-Length": str(file.stat().st_size),
                               "Content-Type": "application/octet-stream", "User-Agent": "BioManager desktop",
                               **headers})
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")


def _role(s) -> str:
    if came_from(s) and sharing(s) and phase(s) != AWAY:
        return "master"
    server = lab_copy.config(s)["server"]
    return "window" if server and window_url().rstrip("/") == server.rstrip("/") else "copy"


def _set_status(**fields) -> None:
    with SessionLocal() as s:
        current = _load(s, LAST_HELLO)
        current.update(fields, at=datetime.utcnow().isoformat(timespec="seconds"))
        _save(s, LAST_HELLO, current)
        s.commit()


def say_hello(app) -> dict:
    """Tell the master what this desktop is; take the master copy over if
    it is being handed to it."""
    with SessionLocal() as s:
        cfg = lab_copy.config(s)
        role = _role(s)
        url = share_url() if role == "master" else ""
    if not cfg["server"] or not cfg["key"]:
        return {}
    try:
        answer = _call(cfg["server"], cfg["key"], "/api/devices/hello",
                       {"role": role, "version": version(), "url": url})
    except Exception as error:  # noqa: BLE001
        _set_status(ok=False, error=lab_copy._reason(error))
        return {}
    _set_status(ok=True, error="", master=answer.get("master"), phase=answer.get("phase", ""))
    if answer.get("for_me") and answer.get("phase") in (ASKED, FROZEN) and role != "master":
        take_over(app, cfg)
    return answer


def take_over(app, cfg: dict) -> bool:
    """Become the lab's master copy: freeze the master, take a last copy,
    open it here, share it on this network, and tell the master where."""
    server, key = cfg["server"].rstrip("/"), cfg["key"]
    try:
        _call(server, key, "/api/devices/handover/freeze")
        result = lab_copy.pull()
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "The last copy failed.")
        newest = lab_copy.list_copies(server)[0]
        backup = install_copy(Path(newest["path"]), lab_copy.copies_dir(server) / "uploads")
        with SessionLocal() as s:
            _save(s, CAME_FROM, {"url": server, "label": _master_label(server), "backup": str(backup)})
            _settings()[1](s, SHARING, "1")
            _save(s, STATE, {})
            s.commit()
        url = start_sharing(app)
        _save_prefs(window_url="")
        _call(server, key, "/api/devices/handover/done", {"url": url})
        _set_status(ok=True, error="", took_over=datetime.utcnow().isoformat(timespec="seconds"))
        return True
    except Exception as error:  # noqa: BLE001 — the master goes back to normal
        app.logger.exception("devices: taking the master copy over failed")
        reason = lab_copy._reason(error) if not isinstance(error, RuntimeError) else str(error)
        _set_status(ok=False, error=f"Taking the master copy over failed: {reason}")
        try:
            _call(server, key, "/api/devices/handover/abort", {"error": reason})
        except Exception:  # noqa: BLE001
            pass
        return False


def _master_label(server: str) -> str:
    return server.split("//", 1)[-1].split("/", 1)[0]


def give_back(app) -> dict:
    """Send the master copy back to the device it came from."""
    with SessionLocal() as s:
        origin, cfg = came_from(s), lab_copy.config(s)
    server, key = (origin.get("url") or cfg["server"]).rstrip("/"), cfg["key"]
    if not server or not key:
        return {"ok": False, "error": gettext("This computer didn't take the master copy over from another device.")}
    with SessionLocal() as s:
        _save(s, STATE, {"phase": FROZEN, "label": origin.get("label") or _master_label(server),
                         "since": datetime.utcnow().isoformat(timespec="seconds")})
        s.commit()
    fd, name = tempfile.mkstemp(prefix="biomanager-giving-back-", suffix=".db")
    os.close(fd)
    path = Path(name)
    path.unlink()
    try:
        lab_copy.write_snapshot(path)
        there = {f["path"]: f["size"] for f in _call(server, key, "/api/devices/return/files", method="GET")["files"]}
        for item in lab_copy._upload_files():
            if there.get(item["path"]) != item["size"]:
                _send(server, key, f"/api/devices/return/files/{quote(item['path'])}",
                      uploads_dir() / item["path"], {}, method="PUT")
        _send(server, key, "/api/devices/return", path,
              {"X-BioManager-SHA256": lab_copy._sha256(path), "X-BioManager-Version": version()})
        # It loads in the background: wait until it is the master again.
        for _ in range(120):
            answer = _call(server, key, "/api/devices/hello", {"role": "giving-back", "version": version()})
            if answer.get("master"):
                break
            time.sleep(2)
        else:
            raise RuntimeError("The other device hasn't confirmed it has the master copy.")
    except Exception as error:  # noqa: BLE001
        with SessionLocal() as s:
            _save(s, STATE, {})
            s.commit()
        reason = str(error) if isinstance(error, RuntimeError) else lab_copy._reason(error)
        return {"ok": False, "error": gettext("Giving the master copy back failed: %(reason)s This computer is still the master.", reason=reason)}
    finally:
        path.unlink(missing_ok=True)
    stop_sharing()
    with SessionLocal() as s:
        _save(s, STATE, {"phase": AWAY, "label": origin.get("label") or _master_label(server), "url": server,
                         "since": datetime.utcnow().isoformat(timespec="seconds"),
                         "own_backup": origin.get("backup", "")})
        _settings()[1](s, SHARING, "")
        _save(s, CAME_FROM, {})
        s.commit()
    _save_prefs(window_url=server)
    return {"ok": True, "url": server}


def start_background(app, every: int = HELLO_EVERY) -> threading.Thread:
    """On the desktop: share the lab again if it was shared, then say hello
    to the master every minute while the app is open."""
    with app.app_context():
        with SessionLocal() as s:
            if sharing(s) and phase(s) != AWAY:
                try:
                    start_sharing(app)
                except Exception:  # noqa: BLE001
                    app.logger.exception("devices: sharing the lab on the network failed")

    def loop():
        time.sleep(5)
        while True:
            try:
                with app.app_context():
                    say_hello(app)
            except Exception:  # noqa: BLE001 — never take the app down
                app.logger.exception("devices: hello failed")
            time.sleep(every)

    thread = threading.Thread(target=loop, name="devices", daemon=True)
    thread.start()
    return thread


def desktop_status(s) -> dict:
    cfg = lab_copy.config(s)
    return {"linked": bool(cfg["server"] and cfg["key"]), "server": cfg["server"], "sharing": sharing(s),
            "share_url": share_url(), "came_from": came_from(s), "hello": _load(s, LAST_HELLO),
            "window_url": window_url(), "in_window": bool(window_url()), "role": _role(s)}


# --- what the person at the desktop does ----------------------------------


def _this_computer_only():
    if not on_this_computer():
        abort(404)
    if g.get("user") is None:
        abort(redirect(url_for("login")))


@bp.route("/settings/devices/share", methods=["POST"])
def share():
    _this_computer_only()
    if not is_admin():
        abort(403)
    turn_on = request.form.get("on") == "1"
    with SessionLocal() as s:
        if not turn_on and came_from(s):
            flash(gettext("This computer holds the lab's master copy: give it back instead of stopping."), "error")
            return redirect(url_for("devices.page"))
        if turn_on and window_url():
            flash(gettext("This window opens another device's lab: use this computer's own first."), "error")
            return redirect(url_for("devices.page"))
        _settings()[1](s, SHARING, "1" if turn_on else "")
        s.commit()
    if turn_on:
        try:
            url = start_sharing(current_app._get_current_object())
        except Exception as error:  # noqa: BLE001
            with SessionLocal() as s:
                _settings()[1](s, SHARING, "")
                s.commit()
            flash(gettext("Couldn't share the lab on the network: %(error)s", error=error), "error")
            return redirect(url_for("devices.page"))
        flash(gettext("Shared: other devices on this network open %(url)s. This computer must stay on and awake.",
                      url=url), "success")
    else:
        stop_sharing()
        flash(gettext("No longer shared: only this computer reaches its lab."), "success")
    return redirect(url_for("devices.page"))


@bp.route("/settings/devices/window", methods=["POST"])
def window():
    """Open the linked lab in this window, or this computer's own again."""
    _this_computer_only()
    with SessionLocal() as s:
        cfg = lab_copy.config(s)
        is_sharing = sharing(s)
    if request.form.get("to") == "lab":
        if not cfg["server"]:
            flash(gettext("Link this computer to the lab first: its address and a key, under Keep a copy."), "error")
            return redirect(url_for("devices.page"))
        if is_sharing:
            flash(gettext("This computer shares its own lab: stop sharing it first."), "error")
            return redirect(url_for("devices.page"))
        _save_prefs(window_url=cfg["server"])
        return redirect(cfg["server"].rstrip("/") + "/")
    _save_prefs(window_url="")
    flash(gettext("This window opens this computer's own BioManager again."), "success")
    return redirect(url_for("devices.page"))


@bp.route("/settings/devices/give-back", methods=["POST"])
def give_back_now():
    _this_computer_only()
    if not is_admin():
        abort(403)
    result = give_back(current_app._get_current_object())
    if not result.get("ok"):
        flash(result["error"], "error")
        return redirect(url_for("devices.page"))
    return redirect(result["url"] + "/")


@bp.route("/settings/devices/own-again", methods=["POST"])
def own_again():
    """After giving the master copy back: this computer's own lab again, as
    it was before it took the master copy over (its backup)."""
    _this_computer_only()
    if not is_admin():
        abort(403)
    with SessionLocal() as s:
        st, shared = state(s), sharing(s)
    backup = Path(st.get("own_backup") or "")
    if st.get("phase") != AWAY or shared or not backup.is_file():
        flash(gettext("There is no earlier lab of this computer's to go back to."), "error")
        return redirect(url_for("devices.page"))
    _swapping.set()
    try:
        keep_a_copy("before-going-back")
        load_lab(backup)
    finally:
        _swapping.clear()
    with SessionLocal() as s:
        _save(s, STATE, {})
        s.commit()
    _save_prefs(window_url="")
    flash(gettext("This computer's own lab is back, as it was before it took the master copy over. Sign in with this computer's own account."), "success")
    return redirect(url_for("login"))


@bp.route("/settings/devices/hello", methods=["POST"])
def hello_now():
    _this_computer_only()
    say_hello(current_app._get_current_object())
    return redirect(url_for("devices.page"))
