"""A copy of the lab's database on every computer that runs the desktop app.

The lab server stays the one place people work. But a server can be lost —
a deleted VM, a lapsed account, a disk — so each desktop app can keep its
own copy of the whole lab, refreshed every day while it runs:

  Server   Someone who may (admins; members if Lab setup allows it) makes a
           key for a computer under Settings → Copies of the lab. It is
           shown once and only its hash is kept. With it the computer asks
             GET /api/lab-copy/snapshot     the whole database, as a SQLite file
             GET /api/lab-copy/files        uploaded files: names and sizes
             GET /api/lab-copy/files/<name> one uploaded file
           Keys are refused from the internet (guest access) like any
           request without a session, and are throttled when wrong.

  Desktop  Settings → Keep a copy of your lab server: the address and the
           key. While the app is open it fetches a snapshot a day (or on
           Copy now), checks it arrived whole (its SHA-256 and SQLite's own
           integrity check) and keeps the newest KEEP; uploaded files are
           mirrored, only new ones downloaded, never deleted.

A snapshot is a complete BioManager database in the desktop app's own
format, read in one transaction so it is one moment of the lab, and so it is
also a restore point: scripts/migrate-to-postgres.py loads it into a new
server (see the Run it for your lab guide). Stored secrets (Google Calendar
tokens and the like) are blanked in it: they are encrypted with the server's
own key and would be useless anywhere else.

An admin's copy is the whole lab, the restore point. A member's copy holds
what that member can see in the app (member_view): no one's password,
tokens or keys, no one else's private notebook pages, personal databases,
notifications or calendar links, and not the Audit log or feedback; of the
uploaded files, only those named in what their copy holds.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, url_for)
from sqlalchemy import String, Text, create_engine, func, select, text

from . import lab, security
from .db import Base, SessionLocal, engine
from .models import EncryptedText, LabCopyKey, UserAccount
from .paths import data_dir, uploads_dir

bp = Blueprint("lab_copy", __name__)

PERMISSION = "members_keep_copies"
KEY_PREFIX = "bmk_"
ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
KEEP_DEFAULT = 14
EVERY_HOURS = 20                     # a copy a day, even if the app opens at different times
MIN_SECONDS_BETWEEN = 120            # per key: a snapshot is a whole-database read
key_throttle = security.LoginThrottle(limit=20, window=15 * 60)


# ---------------------------------------------------------------------------
# Server: who may keep copies, and their keys
# ---------------------------------------------------------------------------


def may_keep_copies(session, user) -> bool:
    if user is None or user.disabled or user.role == "pending" or user.expires_at is not None:
        return False          # a guest's account ends; a copy of the lab shouldn't outlive it
    return user.role == "admin" or lab.permission(session, PERMISSION)


def new_key() -> str:
    return KEY_PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(32))


def key_hash(key: str) -> str:
    return hashlib.sha256((key or "").strip().encode()).hexdigest()


def settings_card() -> dict | None:
    """What Settings shows about copies (None: nothing, on the desktop app
    there is the other card)."""
    user = g.get("user")
    if user is None:
        return None
    if current_app.config.get("LOCAL_SETUP"):
        # A desktop makes keys only while it shares its lab on the network
        # (app/devices.py), and only for the person at the computer.
        from . import devices
        if not devices.share_url() or not devices.on_this_computer():
            return None
    with SessionLocal() as s:
        allowed = may_keep_copies(s, user)
        stmt = select(LabCopyKey).order_by(LabCopyKey.created_at.desc())
        if user.role != "admin":
            stmt = stmt.where(LabCopyKey.user_id_fk == user.id)
        keys = list(s.scalars(stmt))
        owners = {u.id: u.username for u in s.scalars(select(UserAccount).where(
            UserAccount.id.in_({k.user_id_fk for k in keys})))} if keys else {}
        now = datetime.utcnow()
        rows = [{"key": k, "owner": owners.get(k.user_id_fk, "?"), "active": k.revoked_at is None,
                 "fresh": k.last_used_at is not None and now - k.last_used_at < timedelta(hours=36)}
                for k in keys]
    return {"allowed": allowed, "rows": rows, "is_admin": user.role == "admin"}


@bp.app_context_processor
def inject():
    return {"lab_copy_card": settings_card}


@bp.route("/settings/lab-copies", methods=["POST"])
def make_key():
    if g.get("user") is None:
        return redirect(url_for("login"))
    label = " ".join((request.form.get("label") or "").split())[:80]
    with SessionLocal() as s:
        if not may_keep_copies(s, g.user):
            abort(403)
        if not label:
            flash("Say which computer the key is for, e.g. “Lab iMac”.", "error")
            return redirect(url_for("settings") + "#lab-copies")
        key = new_key()
        s.add(LabCopyKey(user_id_fk=g.user.id, label=label, key_hash=key_hash(key)))
        s.commit()
    from . import devices
    base = (devices.share_url() or os.environ.get("BIOMANAGER_BASE_URL") or request.host_url).rstrip("/")
    # Shown on this page only: never stored, never in a redirect.
    return render_template("lab_copy/key.html", key=key, label=label, server=base)


@bp.route("/settings/lab-copies/<int:key_id>/revoke", methods=["POST"])
def revoke_key(key_id: int):
    if g.get("user") is None:
        return redirect(url_for("login"))
    with SessionLocal() as s:
        k = s.get(LabCopyKey, key_id)
        if k is None or (k.user_id_fk != g.user.id and g.user.role != "admin"):
            abort(404)
        if k.revoked_at is None:
            k.revoked_at = datetime.utcnow()
            s.commit()
        flash(f"The key for {k.label} no longer works. Copies already on that computer stay there.", "success")
    return redirect(url_for("settings") + "#lab-copies")


def _authorised(s) -> tuple[LabCopyKey, UserAccount]:
    """The key in the Authorization header, if it may take a copy now.

    Only wrong keys are throttled, per address: a key is 160 random bits,
    so the throttle is against noise, and a stranger's wrong guesses must
    not lock out the lab's own computers."""
    throttle_key = ("lab-copy-ip", request.remote_addr or "")
    header = request.headers.get("Authorization", "")
    raw = header[7:].strip() if header.lower().startswith("bearer ") else ""
    k = s.scalar(select(LabCopyKey).where(LabCopyKey.key_hash == key_hash(raw))) if raw.startswith(KEY_PREFIX) else None
    user = s.get(UserAccount, k.user_id_fk) if k is not None and k.revoked_at is None else None
    now = datetime.utcnow()
    if (user is None or user.disabled or user.role == "pending"
            or (user.expires_at is not None and user.expires_at <= now)):
        if key_throttle.retry_after(throttle_key):
            abort(429)
        key_throttle.failed(throttle_key)
        abort(401)
    if not may_keep_copies(s, user):
        abort(403)
    return k, user


def _json_error(code: int, message: str):
    return jsonify({"ok": False, "error": message}), code


@bp.errorhandler(401)
def _unauthorised(_e):
    return _json_error(401, "This key is not valid, or it was revoked. Make a new one in Settings on the server.")


@bp.errorhandler(403)
def _forbidden(_e):
    return _json_error(403, "This account may not keep copies of the lab. An admin can allow it in Lab setup.")


@bp.errorhandler(429)
def _too_many(_e):
    return _json_error(429, "Too many requests. Try again in a few minutes.")


# ---------------------------------------------------------------------------
# Server: the snapshot and the uploaded files
# ---------------------------------------------------------------------------


def write_snapshot(path: Path, user: UserAccount | None = None) -> dict[str, int]:
    """The whole database, as a SQLite file at `path`, in the schema the app
    itself makes (so the desktop app and migrate-to-postgres read it).
    Returns the rows copied per table. Encrypted columns are blanked; for a
    `user` who is not an admin, only what they can see is kept (member_view).

    Every table is read in one transaction (REPEATABLE READ on PostgreSQL,
    one read transaction on SQLite), so a copy taken while people work is
    one moment of the lab: no mouse points at a cage the copy is missing."""
    target = create_engine(f"sqlite:///{path}")
    counts: dict[str, int] = {}
    try:
        Base.metadata.create_all(target)
        with engine.connect() as src, target.begin() as out:
            if src.dialect.name == "postgresql":
                src = src.execution_options(isolation_level="REPEATABLE READ")
            with src.begin():
                if src.dialect.name == "sqlite":
                    src.exec_driver_sql("BEGIN")    # pysqlite only begins before a write
                for table in Base.metadata.sorted_tables:
                    secret = [c.name for c in table.columns if isinstance(c.type, EncryptedText)]
                    result = src.execution_options(stream_results=True).execute(select(table))
                    while True:
                        batch = result.fetchmany(1000)
                        if not batch:
                            break
                        rows = [dict(r._mapping) for r in batch]
                        for row in rows:
                            for column in secret:
                                row[column] = ""
                        out.execute(table.insert(), rows)
            if user is not None and user.role != "admin":
                member_view(out, user.username)
            for table in Base.metadata.sorted_tables:
                counts[table.name] = out.execute(select(func.count()).select_from(table)).scalar_one()
    finally:
        target.dispose()
    return counts


# What a member's copy leaves out (write_snapshot). Everything else is the
# lab's shared records, which every member sees in the app anyway.
# tests/test_lab_copy.py checks that each table holding someone's own rows
# is named here, or in MEMBER_SEES_WHOLE, so a new one is decided on.
ADMIN_ONLY = ("api_tokens", "lab_copy_keys", "guest_passes", "user_identities", "feedback", "audit_log")
OWN_ROWS = {"notifications": "recipient_username", "calendar_subscriptions": "owner",
            "google_calendar_links": "owner", "calendar_feeds": "owner", "notebook_templates": "owner_username",
            # Batch history: a member's own, as the Batches page shows them
            # (a description can name someone's personal database or file).
            "batches": "actor"}
PAGE_ROWS = ("notebook_comments", "notebook_page_info", "notebook_presence", "notebook_shares",
             "notebook_sync_updates", "notebook_versions", "record_signatures")
PERSONAL_DATABASES = ("inventory_modules", "stock_modules", "organism_modules")
ALIAS_KIND = {"inventory_modules": "inventory", "stock_modules": "stocks", "organism_modules": "organisms"}
MEMBER_SEES_WHOLE = ("users", "experiments", "notebook_tabs", "notebook_pages", "lab_groups", "lab_group_members")


def member_view(out, username: str) -> None:
    """Narrow a copy just written (`out`, a connection to it) to what this
    member may see in the app."""
    run = lambda sql, **kw: out.execute(text(sql), {"me": username, **kw})
    run("UPDATE users SET password_hash = ''")
    for table in ADMIN_ONLY:
        run(f"DELETE FROM {table}")
    for table, column in OWN_ROWS.items():
        run(f"DELETE FROM {table} WHERE {column} != :me")
    # Notebook pages: their own and those shared with them or the lab
    # (app/lab_notebook.accessible_filter), and the rows that hang off them.
    run("CREATE TEMP TABLE seen_pages AS SELECT p.id FROM notebook_pages p "
        "JOIN notebook_tabs t ON t.id = p.tab_id_fk WHERE t.owner_username = :me "
        "OR p.id IN (SELECT page_id_fk FROM notebook_shares WHERE username IN (:me, :everyone) "
        "OR username IN (SELECT 'group:' || group_id_fk FROM lab_group_members WHERE username = :me))",
        everyone="*")
    for table in PAGE_ROWS:
        run(f"DELETE FROM {table} WHERE page_id_fk NOT IN (SELECT id FROM seen_pages)")
    run("DELETE FROM notebook_pages WHERE id NOT IN (SELECT id FROM seen_pages)")
    run("DELETE FROM notebook_tabs WHERE owner_username != :me "
        "AND id NOT IN (SELECT tab_id_fk FROM notebook_pages)")
    run("DROP TABLE seen_pages")
    # Someone else's personal databases, and project groups' they are not
    # in (app/groups.py), and everything in them.
    for modules in PERSONAL_DATABASES:
        hidden = (f"SELECT id FROM {modules} WHERE (private_to != '' AND private_to != :me) "
                  "OR (private_to = '' AND share_group_id IS NOT NULL AND share_group_id NOT IN "
                  "(SELECT group_id_fk FROM lab_group_members WHERE username = :me))")
        for table in Base.metadata.sorted_tables:
            if any(fk.parent.name == "module_id_fk" and fk.column.table.name == modules for fk in table.foreign_keys):
                run(f"DELETE FROM {table.name} WHERE module_id_fk IN ({hidden})")
        # The addresses it had before a rename (app/database_keys.py) too.
        run(f"DELETE FROM database_aliases WHERE kind = :kind AND module_id IN ({hidden})", kind=ALIAS_KIND[modules])
        run(f"DELETE FROM {modules} WHERE id IN ({hidden})")


UPLOAD_REF = re.compile(r"uploads/([^\s\"'<>()\[\]\\?#]+)")


def uploads_named_in(path: Path) -> set[str]:
    """The uploaded files a copy's records point at (as uploads/<name>)."""
    names: set[str] = set()
    with sqlite3.connect(path) as con:
        for table in Base.metadata.sorted_tables:
            for column in table.columns:
                if isinstance(column.type, (String, Text)) and not isinstance(column.type, EncryptedText):
                    for (value,) in con.execute(f'SELECT "{column.name}" FROM "{table.name}" '
                                                f'WHERE "{column.name}" LIKE \'%uploads/%\''):
                        names.update(unquote(n) for n in UPLOAD_REF.findall(value or ""))
    return names


_member_files: dict[int, tuple[float, set[str]]] = {}
MEMBER_FILES_FOR = 30 * 60           # seconds a member's list of files is kept


def member_files(user: UserAccount, key_id: int) -> set[str]:
    """The uploaded files a member's copy may hold: those named in it."""
    cached = _member_files.get(key_id)
    if cached is not None and time.monotonic() - cached[0] < MEMBER_FILES_FOR:
        return cached[1]
    with tempfile.TemporaryDirectory(prefix="biomanager-files-") as tmp:
        path = Path(tmp) / "view.db"
        write_snapshot(path, user)
        names = uploads_named_in(path)
    _member_files[key_id] = (time.monotonic(), names)
    return names


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@bp.route("/api/lab-copy/snapshot")
def snapshot():
    with SessionLocal() as s:
        k, user = _authorised(s)
        s.expunge(user)
        now = datetime.utcnow()
        # The last copy of a hand-over (app/devices.py) is never made to wait.
        from . import devices
        handing_over = devices.state(s).get("key_id") == k.id and devices.phase(s) == devices.FROZEN
        if (not handing_over and k.last_used_at is not None
                and (now - k.last_used_at).total_seconds() < MIN_SECONDS_BETWEEN):
            abort(429)
        k.last_used_at = now
        k.uses = (k.uses or 0) + 1
        s.commit()
        key_id = k.id
    fd, name = tempfile.mkstemp(prefix="biomanager-copy-", suffix=".db")
    os.close(fd)
    path = Path(name)
    path.unlink()                   # create_engine makes a fresh file
    try:
        counts = write_snapshot(path, user)
        size, digest = path.stat().st_size, _sha256(path)
    except Exception:
        path.unlink(missing_ok=True)
        current_app.logger.exception("lab copy: the snapshot failed")
        return _json_error(500, "The server could not make a copy. Its log says why.")
    with SessionLocal() as s:
        k = s.get(LabCopyKey, key_id)
        k.last_bytes = size
        s.commit()
    response = send_file(path, mimetype="application/vnd.sqlite3", as_attachment=True,
                         download_name=f"biomanager-{now:%Y%m%d-%H%M%S}Z.db", max_age=0)
    response.headers["X-BioManager-SHA256"] = digest
    response.headers["X-BioManager-Taken"] = now.strftime("%Y%m%d-%H%M%S")
    response.headers["X-BioManager-Rows"] = json.dumps(counts, separators=(",", ":"))
    response.headers["Cache-Control"] = "no-store"
    response.call_on_close(lambda: path.unlink(missing_ok=True))
    return response


def _upload_files() -> list[dict]:
    root = uploads_dir()
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]   # e.g. .before-restore-<time>
        for name in filenames:
            if name.startswith("."):
                continue
            full = Path(dirpath) / name
            files.append({"path": full.relative_to(root).as_posix(), "size": full.stat().st_size})
    return sorted(files, key=lambda f: f["path"])


def _allowed_files(s) -> set[str] | None:
    """None: every uploaded file (an admin's key); else the member's."""
    k, user = _authorised(s)
    if user.role == "admin":
        return None
    s.expunge(user)
    return member_files(user, k.id)


@bp.route("/api/lab-copy/files")
def files():
    with SessionLocal() as s:
        allowed = _allowed_files(s)
    listing = _upload_files()
    if allowed is not None:
        listing = [f for f in listing if f["path"] in allowed]
    return jsonify({"ok": True, "files": listing})


@bp.route("/api/lab-copy/files/<path:name>")
def file(name: str):
    with SessionLocal() as s:
        allowed = _allowed_files(s)
    if allowed is not None and name not in allowed:
        abort(404)
    # send_from_directory refuses anything outside the uploads folder.
    return send_from_directory(uploads_dir(), name, max_age=0)


# ---------------------------------------------------------------------------
# Desktop: fetching and keeping copies
# ---------------------------------------------------------------------------


SETTING_SERVER, SETTING_KEY, SETTING_KEEP, SETTING_STATUS = (
    "lab_copy_server", "lab_copy_key", "lab_copy_keep", "lab_copy_status")
_lock = threading.Lock()
_running = threading.Event()


def _settings():
    from .inventory_service import get_setting, set_setting
    return get_setting, set_setting


def config(s) -> dict:
    get_setting, _ = _settings()
    try:
        keep = max(1, min(365, int(get_setting(s, SETTING_KEEP, "") or KEEP_DEFAULT)))
    except ValueError:
        keep = KEEP_DEFAULT
    return {"server": get_setting(s, SETTING_SERVER, ""), "key": security.decrypt_text(get_setting(s, SETTING_KEY, "")) or "",
            "keep": keep}


def status(s) -> dict:
    get_setting, _ = _settings()
    try:
        return json.loads(get_setting(s, SETTING_STATUS, "") or "{}")
    except ValueError:
        return {}


def _save_status(**fields) -> None:
    _, set_setting = _settings()
    with SessionLocal() as s:
        current = status(s)
        current.update(fields)
        set_setting(s, SETTING_STATUS, json.dumps(current))
        s.commit()


def copies_dir(server: str) -> Path:
    """Where copies of this server go: <data folder>/lab-copies/<host>/."""
    host = re.sub(r"[^A-Za-z0-9.-]", "_", urlsplit(server).netloc or server) or "server"
    target = data_dir() / "lab-copies" / host
    (target / "db").mkdir(parents=True, exist_ok=True)
    (target / "uploads").mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    return target


def list_copies(server: str) -> list[dict]:
    if not server:
        return []
    out = []
    for db in sorted((copies_dir(server) / "db").glob("biomanager-*.db"), reverse=True):
        info = {}
        sidecar = db.with_suffix(".json")
        if sidecar.exists():
            try:
                info = json.loads(sidecar.read_text(encoding="utf-8"))
            except ValueError:
                pass
        out.append({"name": db.name, "path": str(db), "size": db.stat().st_size, **info})
    return out


def _open(url: str, key: str, timeout: int = 300):
    """One request to the server; tests replace it."""
    return urlopen(Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "BioManager desktop"}),
                   timeout=timeout)


def _reason(error: Exception) -> str:
    if isinstance(error, HTTPError):
        try:
            detail = json.loads(error.read().decode() or "{}").get("error", "")
        except ValueError:
            detail = ""
        return detail or f"The server answered {error.code}."
    if isinstance(error, URLError):
        text = str(error.reason)
        if "CERTIFICATE" in text.upper():
            return ("The server's certificate is not trusted on this computer. If the lab uses its own "
                    "certificate, install it here first.")
        return f"Could not reach the server ({text}). Is this computer on the lab's network, VPN or Tailscale?"
    return str(error) or error.__class__.__name__


def pull() -> dict:
    """Fetch a copy now. Returns the new status (also saved)."""
    with SessionLocal() as s:
        cfg = config(s)
    if not cfg["server"] or not cfg["key"]:
        return {"ok": False, "error": "Set the server's address and a key first."}
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "A copy is already being made."}
    _running.set()
    _save_status(running=True, started=datetime.utcnow().isoformat(timespec="seconds"))
    server = cfg["server"].rstrip("/")
    target = copies_dir(server)
    try:
        # 1. The database.
        fd, name = tempfile.mkstemp(dir=target, prefix=".incoming-", suffix=".db")
        with os.fdopen(fd, "wb") as out, _open(f"{server}/api/lab-copy/snapshot", cfg["key"]) as r:
            shutil.copyfileobj(r, out, 1 << 20)
            digest, taken = r.headers.get("X-BioManager-SHA256", ""), r.headers.get("X-BioManager-Taken", "")
            try:
                rows = json.loads(r.headers.get("X-BioManager-Rows", "") or "{}")
            except ValueError:
                rows = {}
        incoming = Path(name)
        if not digest or _sha256(incoming) != digest:
            incoming.unlink(missing_ok=True)
            raise RuntimeError("The copy arrived damaged (its checksum did not match). It was thrown away.")
        with sqlite3.connect(incoming) as con:
            check = con.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            incoming.unlink(missing_ok=True)
            raise RuntimeError(f"The copy failed SQLite's integrity check ({check}). It was thrown away.")
        taken = re.sub(r"[^0-9-]", "", taken) or datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        final = target / "db" / f"biomanager-{taken}Z.db"
        incoming.replace(final)
        final.with_suffix(".json").write_text(json.dumps({"server": server, "taken": taken, "rows": rows,
                                                           "sha256": digest}, indent=1), encoding="utf-8")
        # 2. Keep the newest copies.
        dbs = sorted((target / "db").glob("biomanager-*.db"))
        for old in dbs[:-cfg["keep"]]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
        # 3. Uploaded files: only what is new or changed; nothing is deleted here.
        with _open(f"{server}/api/lab-copy/files", cfg["key"], timeout=60) as r:
            listing = json.loads(r.read().decode() or "{}").get("files", [])
        fetched = 0
        for item in listing:
            rel = Path(item["path"])
            if rel.is_absolute() or ".." in rel.parts:
                continue
            dest = target / "uploads" / rel
            if dest.exists() and dest.stat().st_size == item["size"]:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            with open(part, "wb") as out, _open(f"{server}/api/lab-copy/files/{quote(item['path'])}", cfg["key"]) as r:
                shutil.copyfileobj(r, out, 1 << 20)
            part.replace(dest)
            fetched += 1
        result = {"ok": True, "error": "", "last_ok": datetime.utcnow().isoformat(timespec="seconds"),
                  "last_name": final.name, "last_size": final.stat().st_size,
                  "records": sum(v for v in rows.values() if isinstance(v, int)),
                  "files": len(listing), "files_fetched": fetched}
    except Exception as error:  # noqa: BLE001 — every failure becomes a message on Settings
        result = {"ok": False, "error": _reason(error),
                  "last_error_at": datetime.utcnow().isoformat(timespec="seconds")}
    finally:
        for stray in target.glob(".incoming-*"):
            stray.unlink(missing_ok=True)
        _running.clear()
        _lock.release()
    _save_status(running=False, **result)
    return result


def due() -> bool:
    from . import devices
    with SessionLocal() as s:
        cfg, st = config(s), status(s)
        # A desktop holding the master copy has nothing newer to copy.
        holds_master = bool(devices.came_from(s)) and devices.sharing(s)
    if not cfg["server"] or not cfg["key"] or holds_master:
        return False
    last = st.get("last_ok")
    if not last:
        return True
    try:
        return datetime.utcnow() - datetime.fromisoformat(last) > timedelta(hours=EVERY_HOURS)
    except ValueError:
        return True


def start_background(app, first_delay: int = 60, every: int = 30 * 60) -> threading.Thread:
    """The desktop app's timer: a copy when one is due, checked every half hour."""
    def loop():
        time.sleep(first_delay)
        while True:
            try:
                with app.app_context():
                    if due():
                        pull()
            except Exception:  # noqa: BLE001 — never take the app down
                app.logger.exception("lab copy: the scheduled copy failed")
            time.sleep(every)

    thread = threading.Thread(target=loop, name="lab-copy", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Desktop: its Settings card
# ---------------------------------------------------------------------------


def desktop_card() -> dict | None:
    if g.get("user") is None or not current_app.config.get("LOCAL_SETUP"):
        return None
    with SessionLocal() as s:
        cfg, st = config(s), status(s)
    return {"server": cfg["server"], "has_key": bool(cfg["key"]), "keep": cfg["keep"], "status": st,
            "running": _running.is_set(), "copies": list_copies(cfg["server"]),
            "folder": str(copies_dir(cfg["server"])) if cfg["server"] else ""}


@bp.app_context_processor
def inject_desktop():
    return {"lab_copy_desktop": desktop_card}


def _desktop_only():
    from . import devices
    if not current_app.config.get("LOCAL_SETUP") or not devices.on_this_computer():
        abort(404)
    if g.get("user") is None:
        abort(redirect(url_for("login")))


@bp.route("/lab-copy/configure", methods=["POST"])
def configure():
    _desktop_only()
    server = (request.form.get("server") or "").strip().rstrip("/")
    if server and not server.startswith(("https://", "http://")):
        server = "https://" + server
    key = (request.form.get("key") or "").strip()
    _, set_setting = _settings()
    with SessionLocal() as s:
        set_setting(s, SETTING_SERVER, server)
        if key:
            set_setting(s, SETTING_KEY, security.encrypt_text(key))
        if request.form.get("keep", "").isdigit():
            set_setting(s, SETTING_KEEP, str(max(1, min(365, int(request.form["keep"])))))
        if not server:
            set_setting(s, SETTING_KEY, "")
        s.commit()
    if server and (key or request.form.get("copy_now")):
        threading.Thread(target=_pull_in_app, args=(current_app._get_current_object(),), daemon=True).start()
        flash("Saved. Making the first copy now…", "success")
    else:
        flash("Saved." if server else "This computer no longer keeps copies. Those already here stay.", "success")
    return redirect(url_for("settings") + "#lab-copy")


def _pull_in_app(app) -> None:
    with app.app_context():
        pull()


@bp.route("/lab-copy/now", methods=["POST"])
def copy_now():
    _desktop_only()
    threading.Thread(target=_pull_in_app, args=(current_app._get_current_object(),), daemon=True).start()
    return redirect(url_for("settings") + "#lab-copy")


@bp.route("/lab-copy/status")
def status_json():
    _desktop_only()
    with SessionLocal() as s:
        st = status(s)
    return jsonify({**st, "running": _running.is_set()})
