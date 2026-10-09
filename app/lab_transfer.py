"""A whole lab in one file, and putting it on a new server.

The file (`<lab>.biomanager`, a zip) holds:

  manifest.json   what it is: the format, the BioManager version and schema
                  revision that made it, the lab's name, when, the row and
                  file counts, and the database's SHA-256
  lab.db          the lab's database as a SQLite file (lab_copy.write_snapshot:
                  one moment of the lab; stored secrets blanked, since they are
                  encrypted with the key of the machine that made it)
  uploads/…       the uploaded files

The desktop app makes one (Settings → Your data → Save the whole lab), or
sends one straight to a new server (Move this lab to a server,
app/devices.py). A server with no account yet takes one in (`/lab/import`,
with its setup code), in one transaction: what it had is replaced, or, if
anything is wrong, nothing changes. Settings that belong to a machine rather
than to the lab (its links, copies, update checks, counts) are neither
carried nor overwritten.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import zipfile
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, func, select, text

from . import lab, paths
from .db import Base, engine
from .i18n import gettext

FORMAT = "biomanager-lab"
FORMAT_VERSION = 1
SUFFIX = ".biomanager"
BATCH = 1000
# app_settings keys that are the machine's, not the lab's.
MACHINE_KEYS = ("devices:", "lab_copy_", "updates:", "telemetry:", "signed_out:")


class LabFileError(ValueError):
    """The file can't be taken in; the message says why, for a person."""


def _machine_key(key: str) -> bool:
    return str(key or "").startswith(MACHINE_KEYS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _revision() -> str:
    from . import upgrade
    return upgrade.current_revision(engine) or upgrade.head_revision()


def _version() -> str:
    from .feedback import app_version
    return app_version()


# ---------------------------------------------------------------- making one

def build(target: Path, work: Path) -> dict:
    """Write the whole lab to `target` (a zip); `work` is a scratch folder.
    Returns the manifest."""
    from . import lab_copy
    from .db import SessionLocal

    work.mkdir(parents=True, exist_ok=True)
    database = work / "lab.db"
    database.unlink(missing_ok=True)
    counts = lab_copy.write_snapshot(database)
    with sqlite3.connect(database) as conn:
        for prefix in MACHINE_KEYS:
            conn.execute("DELETE FROM app_settings WHERE key LIKE ?", (prefix + "%",))
    uploads = paths.uploads_dir()
    files = sorted(p for p in uploads.rglob("*") if p.is_file()) if uploads.is_dir() else []
    with SessionLocal() as s:
        name = lab.lab_name(s)
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "version": _version(), "revision": _revision(),
                "lab": name, "made": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "rows": sum(counts.values()), "accounts": counts.get("users", 0), "files": len(files),
                "sha256": _sha256(database)}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.write(database, "lab.db")
        for f in files:
            zf.write(f, "uploads/" + f.relative_to(uploads).as_posix(), compress_type=zipfile.ZIP_STORED)
    return manifest


def file_name(manifest: dict) -> str:
    stem = "".join(c if c.isalnum() or c in " -_" else "-" for c in (manifest.get("lab") or "lab")).strip() or "lab"
    return f"{stem} {date.today().isoformat()}{SUFFIX}"


# ---------------------------------------------------------------- reading one

def open_file(source: Path, work: Path) -> tuple[dict, Path, Path]:
    """Check and unpack a lab file into `work`: (manifest, database, uploads)."""
    try:
        zf = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as error:
        raise LabFileError(gettext("This is not a BioManager lab file.")) from error
    with zf:
        names = zf.namelist()
        if "manifest.json" not in names or "lab.db" not in names:
            raise LabFileError(gettext("This is not a BioManager lab file."))
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except ValueError as error:
            raise LabFileError(gettext("This is not a BioManager lab file.")) from error
        if manifest.get("format") != FORMAT or int(manifest.get("format_version") or 0) > FORMAT_VERSION:
            raise LabFileError(gettext("This lab file was made by a newer BioManager. Update this server first."))
        uploads = work / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        for member in zf.infolist():
            name = member.filename
            if member.is_dir() or name in ("manifest.json",):
                continue
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise LabFileError(gettext("This lab file holds a path outside it; it was not taken in."))
            if name == "lab.db":
                dest = work / "lab.db"
            elif pure.parts[0] == "uploads" and len(pure.parts) > 1:
                dest = uploads.joinpath(*pure.parts[1:])
            else:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
    database = work / "lab.db"
    if manifest.get("sha256") and _sha256(database) != manifest["sha256"]:
        raise LabFileError(gettext("The lab file arrived damaged (its database doesn't match its checksum). Make it again."))
    return manifest, database, uploads


# ---------------------------------------------------------------- checking it

def _parse_date(value, kind):
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value
    text_value = str(value).strip().replace("T", " ")
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError:
        parsed = datetime.strptime(text_value, "%Y/%m/%d") if "/" in text_value else None
        if parsed is None:
            raise
    return parsed.date() if kind == "date" else parsed


def read_rows(sqlite_conn, table, problems: list[str], strict_lengths: bool = True):
    """Rows of `table` as dicts ready for the target database. Values are
    read raw (SQLite keeps whatever was stored) and converted by the model's
    type; what the target would refuse goes into `problems`."""
    columns = list(table.columns)
    present = {row[1] for row in sqlite_conn.execute(f'PRAGMA table_info("{table.name}")')}
    selected = [c for c in columns if c.name in present]
    names = ", ".join(f'"{c.name}"' for c in selected)
    for raw in sqlite_conn.execute(f'SELECT {names} FROM "{table.name}"'):
        out = {}
        for column, value in zip(selected, raw):
            kind = column.type
            where = f"{table.name}.{column.name}"
            try:
                if value is None:
                    pass
                elif isinstance(kind, DateTime):
                    value = _parse_date(value, "datetime")
                elif isinstance(kind, Date):
                    value = _parse_date(value, "date")
                elif isinstance(kind, Boolean):
                    value = bool(int(value)) if not isinstance(value, bool) else value
                elif isinstance(kind, Integer):
                    value = None if value == "" else int(value)
                elif isinstance(kind, Float):
                    value = None if value == "" else float(value)
                elif isinstance(kind, String) and not isinstance(value, str):
                    value = str(value)
            except (TypeError, ValueError):
                problems.append(f"{where} id={raw[0]!r}: {value!r} is not a valid {type(kind).__name__}")
                continue
            length = getattr(kind, "length", None)
            if strict_lengths and isinstance(value, str) and length and len(value) > length:
                problems.append(f"{where} id={raw[0]!r}: {len(value)} characters, the column holds {length}")
            if value is None and not column.nullable and not column.primary_key:
                # SQLite let an old row keep NULL where the model now wants a
                # value; the model's default is what the app would have written.
                if column.default is not None:
                    arg = column.default.arg
                    value = arg(None) if callable(arg) else arg
                elif column.server_default is None:
                    problems.append(f"{where} id={raw[0]!r}: empty, but the column requires a value")
            out[column.name] = value
        yield out


def check(manifest: dict, database: Path) -> tuple[dict, list[str]]:
    """Everything that would stop the lab going in, before anything changes:
    a different version, and values this database would refuse. Returns the
    rows by table, ready to write, and the problems (empty: it can go in)."""
    problems: list[str] = []
    here = _revision()
    if manifest.get("revision") != here:
        problems.append(gettext("The lab file was made by BioManager %(theirs)s; this one runs %(ours)s. Update both to the same version, then make the file again.",
                                theirs=manifest.get("version") or "?", ours=_version()))
        return {}, problems
    strict = engine.dialect.name == "postgresql"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        # A record pointing at one the file doesn't hold would be refused by
        # the database at the very end; named here first, on any database.
        dangling = conn.execute("PRAGMA foreign_key_check").fetchall()
        for table, rowid, parent, _ in dangling[:20]:
            problems.append(gettext("%(table)s, row %(row)s: points to a record in %(parent)s that isn’t in the file.",
                                    table=table, row=rowid, parent=parent))
        if len(dangling) > 20:
            problems.append(gettext("… and %(n)s more like these.", n=len(dangling) - 20))
        have = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        data = {}
        for table in Base.metadata.sorted_tables:
            rows = list(read_rows(conn, table, problems, strict)) if table.name in have else []
            if table.name == "app_settings":
                rows = [r for r in rows if not _machine_key(r.get("key"))]
            data[table.name] = rows
        try:
            marks = dict(conn.execute("SELECT name, seq FROM sqlite_sequence"))
        except sqlite3.Error:
            marks = {}
    data["__marks__"] = marks
    return data, problems


# ---------------------------------------------------------------- taking it in

def apply(data: dict, uploads: Path) -> int:
    """Replace this database's lab with `data` (from check), in one
    transaction, and copy the uploaded files in. Only for a database with no
    account yet (the caller checks). Returns the rows written."""
    marks = data.get("__marks__", {})
    tables = list(Base.metadata.sorted_tables)
    total = 0
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            settings = Base.metadata.tables["app_settings"]
            kept = [dict(r._mapping) for r in conn.execute(select(settings))
                    if _machine_key(r._mapping["key"])]
            if conn.dialect.name == "postgresql":
                conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            for table in reversed(tables):
                conn.execute(table.delete())
            for table in tables:
                rows = data.get(table.name) or []
                if table.name == "app_settings":
                    rows = rows + kept
                for start in range(0, len(rows), BATCH):
                    conn.execute(table.insert(), rows[start:start + BATCH])
                total += len(rows)
            if conn.dialect.name == "postgresql":
                for table in tables:
                    pk = list(table.primary_key.columns)
                    if len(pk) != 1 or not isinstance(pk[0].type, Integer):
                        continue
                    seq = conn.execute(text("SELECT pg_get_serial_sequence(:t, :c)"),
                                       {"t": f'"{table.name}"', "c": pk[0].name}).scalar()
                    if not seq:
                        continue
                    highest = max(conn.execute(select(func.coalesce(func.max(pk[0]), 0))).scalar() or 0,
                                  marks.get(table.name, 0))
                    if highest:
                        conn.execute(text("SELECT setval(:s, :v)"), {"s": seq, "v": highest})
                conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            for table in tables:
                got = conn.execute(select(func.count()).select_from(table)).scalar()
                want = len(data.get(table.name) or []) + (len(kept) if table.name == "app_settings" else 0)
                if got != want:
                    raise RuntimeError(f"{table.name}: {want} read, {got} written")
            trans.commit()
        except Exception:
            trans.rollback()
            raise
    target = paths.uploads_dir()
    for f in sorted(p for p in uploads.rglob("*") if p.is_file()):
        dest = target / f.relative_to(uploads)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
    return total
