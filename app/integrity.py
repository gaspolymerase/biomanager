"""Database integrity: ids are never reused and references stay valid.

Two SQLite defaults let old references silently point at the wrong record:

  * Without AUTOINCREMENT, SQLite hands out max(id) + 1, so deleting the
    newest row frees its id for the next insert. Audit entries, links and
    printed labels that named the deleted row then name the new one.
  * Foreign keys are not enforced unless each connection asks, so a parent
    can be deleted while children still point at it.

New databases get AUTOINCREMENT, and foreign keys checked at commit
(DEFERRABLE INITIALLY DEFERRED, so "unlink the children, then delete the
parent" works whatever order the ORM flushes in), from the settings block
at the end of app/models.py. ``ensure_integrity()`` (called from
``init_database``) brings an existing SQLite database up to that standard
and then decides whether app/db.py turns on ``PRAGMA foreign_keys`` for
every connection (until it has run, connections leave it off, as before):

 1. Tables whose CREATE statement lacks AUTOINCREMENT or a deferred foreign
    key are rebuilt with the model's DDL. Columns the old table has but the
    model lacks are kept; a column the old table allowed NULL in stays
    nullable, so every row is copied unchanged. Indexes are recreated and
    sqlite_sequence is seeded with the highest id the table has ever used
    (the larger of max(id) and the highest record_id in audit_log), so ids
    freed before this migration are not handed out again either.
    All tables are rebuilt in one transaction (all or nothing), row counts
    are compared per table before the old copy is dropped, and a backup of
    the whole file is written first, next to it:
        <database>.pre-integrity-<YYYYmmdd-HHMMSS>.bak
    Success is recorded in app_settings under ``integrity.autoincrement``.
    Each start re-reads the stored schema (cheap) so a table restored or
    added by hand later is caught too; a current database is left alone.
 2. ``PRAGMA foreign_key_check`` runs on every start. A dangling reference
    in a nullable column is cleared to NULL (each one logged). One in a
    NOT NULL column is reported, never deleted; while any remain,
    enforcement stays OFF for this database with a loud warning so the app
    keeps working. The state is recorded under ``integrity.foreign_keys``.

Rolling back: the rebuilt schema works with older code as it is, so going
back to older code needs nothing. To undo the rebuild itself, stop the app
and copy the .bak file over the database (changes made since are lost).
Non-SQLite databases (PostgreSQL) already have sequences and enforced
foreign keys; everything here is skipped for them.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import Column, Date, DateTime, MetaData, text
from sqlalchemy import Boolean, Float, Integer, Numeric
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.types import UserDefinedType

from . import db as _db
from .db import Base, engine

log = logging.getLogger("biomanager")

FLAG_AUTOINCREMENT = "integrity.autoincrement"
FLAG_FOREIGN_KEYS = "integrity.foreign_keys"
_AUTOINCREMENT = re.compile(r"\bAUTOINCREMENT\b", re.I)
_REFERENCES = re.compile(r"\bREFERENCES\b", re.I)
_DEFERRED = re.compile(r"\bDEFERRABLE\s+INITIALLY\s+DEFERRED\b", re.I)


class _AsDeclared(UserDefinedType):
    """A column type written back exactly as the old table declared it."""

    cache_ok = True

    def __init__(self, spec: str = ""):
        self.spec = spec

    def get_col_spec(self, **kw) -> str:
        return self.spec


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def ensure_integrity() -> dict:
    """Upgrade an existing SQLite database; see the module docstring.

    Never stops the app from starting: a failed rebuild is rolled back and
    logged, and the database is left exactly as it was.
    """
    report = {"rebuilt": [], "cleared": [], "unresolved": [], "enforced": True}
    if engine.dialect.name != "sqlite":
        return report
    raw = engine.raw_connection()
    conn = raw.driver_connection
    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # we issue BEGIN/COMMIT ourselves
    try:
        try:
            pending = tables_needing_rebuild(conn)
            if pending:
                backup = _backup(conn)
                report["rebuilt"] = _rebuild(conn, pending)
                _set_flag(conn, FLAG_AUTOINCREMENT,
                          f"{datetime.now():%Y-%m-%d %H:%M:%S}; rebuilt {len(pending)} tables"
                          + (f"; backup {backup}" if backup else ""))
                log.warning("Integrity: rebuilt %d tables (AUTOINCREMENT, deferred foreign keys) so ids are never "
                            "reused.%s", len(pending),
                            f" Backup of the database before the change: {backup}" if backup else "")
            schema_ok = True
        except Exception:
            schema_ok = False
            log.exception("Integrity: rebuilding tables with AUTOINCREMENT failed; the database "
                          "was left unchanged and ids may still be reused.")
        try:
            _check_foreign_keys(conn, report)
        except Exception:
            log.exception("Integrity: foreign key check failed; foreign keys are NOT enforced.")
        if not schema_ok and _db.FOREIGN_KEYS_ENFORCED:
            # Its foreign keys are still checked per statement, which the
            # ORM's flush order does not respect; enforcing them would make
            # ordinary deletes fail at random.
            _db.FOREIGN_KEYS_ENFORCED = False
            log.warning("Integrity: foreign keys NOT enforced for this database until the "
                        "rebuild above succeeds.")
    finally:
        try:
            conn.execute(f"PRAGMA foreign_keys={'ON' if _db.FOREIGN_KEYS_ENFORCED else 'OFF'}")
        finally:
            conn.isolation_level = previous_isolation
            raw.close()
        # Pooled connections were opened before the decision; start afresh.
        engine.dispose()
    report["enforced"] = bool(_db.FOREIGN_KEYS_ENFORCED)
    return report


def _is_current(sql: str) -> bool:
    """AUTOINCREMENT, and every foreign key checked at commit."""
    references = len(_REFERENCES.findall(sql))
    return bool(_AUTOINCREMENT.search(sql)) and len(_DEFERRED.findall(sql)) >= references


def tables_needing_rebuild(conn) -> list[str]:
    """Model tables with a single integer primary key whose stored schema
    lacks AUTOINCREMENT or has a foreign key checked per statement."""
    existing = dict(conn.execute(
        "select name, sql from sqlite_master where type='table'").fetchall())
    pending = []
    for table in Base.metadata.sorted_tables:
        sql = existing.get(table.name)
        if sql is None or table.autoincrement_column is None or _is_current(sql):
            continue
        pk = [row for row in conn.execute(f"PRAGMA table_info({_q(table.name)})") if row[5]]
        if len(pk) != 1 or pk[0][2].upper() != "INTEGER" or pk[0][1] != table.autoincrement_column.name:
            log.warning("Integrity: %s has an unexpected primary key; left as it is.", table.name)
            continue
        pending.append(table.name)
    return pending


def _backup(conn) -> str | None:
    """Copy the whole database next to itself (a consistent online copy)."""
    path = engine.url.database
    if not path or path == ":memory:" or path.startswith("file:"):
        return None
    target = Path(f"{path}.pre-integrity-{datetime.now():%Y%m%d-%H%M%S}.bak")
    out = sqlite3.connect(target)
    try:
        conn.backup(out)
    finally:
        out.close()
    return str(target)


def _default_literal(column) -> str:
    default = column.default
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
    kind = column.type
    if isinstance(kind, DateTime):
        return "CURRENT_TIMESTAMP"
    if isinstance(kind, Date):
        return "CURRENT_DATE"
    if isinstance(kind, (Integer, Float, Numeric, Boolean)):
        return "0"
    return "''"


def _rebuild(conn, names: list[str]) -> list[dict]:
    """Rebuild tables with AUTOINCREMENT, all in one transaction."""
    # A private copy of the schema: loosening a column or adding a legacy
    # one must not touch the live model.
    scratch = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(scratch)
    audit_ok = "record_id" in {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}

    done = []
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for name in names:
            done.append(_rebuild_one(conn, scratch.tables[name]))
        for item in done:
            item["next_id"] = _restart_sequence(conn, item["table"], audit_ok)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return done


def _restart_sequence(conn, name: str, audit_ok: bool) -> int:
    """After a rebuild: the table's next id is past every id it has had,
    including deleted rows the change history remembers. Returns it."""
    high = conn.execute(f"select max(id) from {_q(name)}").fetchone()[0] or 0
    if audit_ok:
        logged = conn.execute("select max(record_id) from audit_log where table_name=?",
                              (name,)).fetchone()[0] or 0
        high = max(high, logged)
    seq = conn.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone()
    high = max(high, seq[0] if seq else 0)
    conn.execute("delete from sqlite_sequence where name=?", (name,))
    conn.execute("insert into sqlite_sequence (name, seq) values (?, ?)", (name, high))
    return high + 1


def _rebuild_one(conn, table, drop: frozenset = frozenset()) -> dict:
    """Rebuild one table as the model has it, copying every row. A column
    the database has and the model doesn't is kept, unless named in `drop`
    (a revision retiring it, as 0056 does)."""
    name = table.name
    temp = f"{name}__integrity_new"
    old = {row[1]: row for row in conn.execute(f"PRAGMA table_info({_q(name)})")}
    # cid, name, type, notnull, dflt_value, pk

    for column in table.columns:
        if column.name in old and not old[column.name][3] and not column.primary_key:
            column.nullable = True  # never tighten: rows copy unchanged
    extra = [col for col in old if col not in table.columns and col not in drop]
    for col in extra:
        _, _, declared, notnull, dflt, _ = old[col]
        table.append_column(Column(col, _AsDeclared(declared or ""), nullable=not notnull,
                                   server_default=text(dflt) if dflt is not None else None))

    ddl = str(CreateTable(table).compile(dialect=engine.dialect))
    ddl, count = re.subn(r"^\s*CREATE TABLE\s+(\"?)" + re.escape(name) + r"\1\s*\(",
                         f"CREATE TABLE {_q(temp)} (", ddl, count=1)
    if count != 1 or not _is_current(ddl):
        raise RuntimeError(f"could not build the new DDL for {name}")

    targets, sources = [], []
    for column in table.columns:
        if column.name in old:
            targets.append(_q(column.name))
            sources.append(_q(column.name))
        elif not column.nullable:
            targets.append(_q(column.name))
            sources.append(_default_literal(column))

    indexes = conn.execute(
        "select name, sql from sqlite_master where type='index' and tbl_name=? and sql is not null",
        (name,)).fetchall()
    before = conn.execute(f"select count(*) from {_q(name)}").fetchone()[0]
    conn.execute(ddl)
    conn.execute(f"INSERT INTO {_q(temp)} ({', '.join(targets)}) "
                 f"SELECT {', '.join(sources)} FROM {_q(name)}")
    after = conn.execute(f"select count(*) from {_q(temp)}").fetchone()[0]
    if before != after:
        raise RuntimeError(f"{name}: {before} rows before, {after} after the copy")
    conn.execute(f"DROP TABLE {_q(name)}")
    conn.execute(f"ALTER TABLE {_q(temp)} RENAME TO {_q(name)}")
    made = set()
    for index_name, sql in indexes:
        if any(re.search(rf"\b{re.escape(col)}\b", sql) for col in drop):
            continue                 # an index on a dropped column goes with it
        conn.execute(sql)
        made.add(index_name)
    for index in table.indexes:
        if index.name not in made:
            conn.execute(str(CreateIndex(index).compile(dialect=engine.dialect)))
    return {"table": name, "rows": after, "kept_columns": extra}


def _check_foreign_keys(conn, report: dict) -> None:
    """Clear dangling nullable references; switch enforcement off if any
    NOT NULL ones remain (they are reported, never deleted)."""
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    unresolved = []
    if violations:
        conn.execute("BEGIN IMMEDIATE")
        try:
            fk_cache: dict[str, dict] = {}
            for table, rowid, parent, fkid in violations:
                if table not in fk_cache:
                    notnull = {row[1]: bool(row[3]) for row in conn.execute(f"PRAGMA table_info({_q(table)})")}
                    keys: dict[int, list] = {}
                    for row in conn.execute(f"PRAGMA foreign_key_list({_q(table)})"):
                        keys.setdefault(row[0], []).append((row[3], row[4]))
                    fk_cache[table] = {"notnull": notnull, "keys": keys}
                info = fk_cache[table]
                columns = [src for src, _ in info["keys"].get(fkid, [])]
                values = conn.execute(
                    f"select {', '.join(_q(c) for c in columns)} from {_q(table)} where rowid=?",
                    (rowid,)).fetchone() if columns and rowid is not None else None
                label = (f"{table} #{rowid} {', '.join(columns)}={values if values is None else ', '.join(map(str, values))}"
                         f" points at a missing {parent} row")
                if columns and rowid is not None and not any(info["notnull"].get(c) for c in columns):
                    conn.execute(f"update {_q(table)} set {', '.join(f'{_q(c)}=NULL' for c in columns)} "
                                 "where rowid=?", (rowid,))
                    report["cleared"].append(label)
                    log.warning("Integrity: %s; cleared the reference.", label)
                else:
                    unresolved.append(label)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    report["unresolved"] = unresolved
    if unresolved:
        _db.FOREIGN_KEYS_ENFORCED = False
        log.warning(
            "\n" + "!" * 72
            + f"\n  Foreign keys are NOT enforced for this database: {len(unresolved)} required\n"
            "  reference(s) point at records that no longer exist. Nothing was deleted.\n"
            + "".join(f"    - {item}\n" for item in unresolved[:20])
            + (f"    ... and {len(unresolved) - 20} more\n" if len(unresolved) > 20 else "")
            + "  Fix or remove those rows, then restart to turn enforcement on.\n"
            + "!" * 72)
        state = f"off; {len(unresolved)} unresolved reference(s)"
    else:
        _db.FOREIGN_KEYS_ENFORCED = True
        state = "on"
    _set_flag(conn, FLAG_FOREIGN_KEYS, state, only_if_changed=True)


def _set_flag(conn, key: str, value: str, only_if_changed: bool = False) -> None:
    if not conn.execute("select 1 from sqlite_master where type='table' and name='app_settings'").fetchone():
        return
    if only_if_changed:
        row = conn.execute("select value from app_settings where key=?", (key,)).fetchone()
        if row and row[0] == value:
            return
    conn.execute("insert or replace into app_settings (key, value) values (?, ?)", (key, value))
