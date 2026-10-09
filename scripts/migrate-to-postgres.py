#!/usr/bin/env python3
"""Copy a BioManager SQLite database into an empty PostgreSQL database.

For moving a lab from one laptop (or the desktop app) onto a shared server:

    python scripts/migrate-to-postgres.py data/biomanager.db \\
        postgresql://biomanager:PASSWORD@localhost:5432/biomanager

    --dry-run     do every check and the whole copy, then roll it back

What it does, in order:

1. Copies the SQLite file with SQLite's backup API (consistent even while
   the app is running) and works on the copy. The original is only read.
2. Brings the copy up to the current schema by running the app's own
   start-up on it, exactly as a newer app version would on first launch.
3. Checks every value will fit PostgreSQL, which, unlike SQLite, enforces
   column types and VARCHAR lengths. Problems are listed and nothing is
   written until they are fixed in the app.
4. Copies every table in one transaction. Foreign keys are DEFERRABLE, so
   they are checked once, at commit: a dangling reference aborts the whole
   copy instead of leaving half a database.
5. Sets each id sequence past the highest id the SQLite database ever
   handed out (sqlite_sequence remembers deleted ones), so ids are still
   never reused.
6. Compares row counts table by table.

The target must be empty. Uploaded files are not in the database: copy the
uploads folder (app/static/uploads, or the desktop app's data folder) to the
server's BIOMANAGER_UPLOADS_DIR separately.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BATCH = 1000


def pg_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def snapshot(source: Path, into: Path) -> Path:
    target = into / "snapshot.db"
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    return target


def bring_up_to_date(copy: Path, scratch: Path):
    """Run the app's start-up on the copy. The engine is bound when app.db
    is imported, so the environment is set first."""
    os.environ["DATABASE_URL"] = f"sqlite:///{copy}"
    os.environ["BIOMANAGER_DATA_DIR"] = str(scratch)  # its setup files stay out of the way
    sys.path.insert(0, str(ROOT))
    from app import services
    from app.db import Base, engine

    services.init_database()
    return Base.metadata, engine


def high_water_marks(sqlite_conn) -> dict[str, int]:
    try:
        return dict(sqlite_conn.execute("SELECT name, seq FROM sqlite_sequence"))
    except sqlite3.OperationalError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sqlite_file", type=Path)
    parser.add_argument("postgres_url")
    parser.add_argument("--dry-run", action="store_true", help="copy, verify, then roll back")
    args = parser.parse_args()

    source = args.sqlite_file.expanduser()
    if not source.exists():
        sys.exit(f"No such file: {source}")
    target_url = pg_url(args.postgres_url)
    if not target_url.startswith("postgresql"):
        sys.exit("The target must be a postgresql:// URL.")

    from sqlalchemy import Integer, create_engine, func, inspect, select, text

    with tempfile.TemporaryDirectory(prefix="biomanager-migrate-") as tmp:
        scratch = Path(tmp)
        copy = snapshot(source, scratch)
        print(f"read     : {source} (working on a copy)")
        metadata, sqlite_engine = bring_up_to_date(copy, scratch)
        # The same checks the app makes when it takes in a lab file (app/lab_transfer.py).
        from app.lab_transfer import read_rows
        tables = list(metadata.sorted_tables)
        from app import upgrade
        revision = upgrade.current_revision(sqlite_engine) or upgrade.head_revision()   # the copy is at the newest

        target = create_engine(target_url, future=True)
        existing = set(inspect(target).get_table_names())
        with target.connect() as conn:
            busy = [t.name for t in tables if t.name in existing
                    and conn.execute(select(func.count()).select_from(t)).scalar()]
        if busy:
            sys.exit(f"The target already holds data ({', '.join(busy[:5])}…). "
                     "Migrate into an empty database.")

        sqlite_conn = sqlite3.connect(copy)
        problems: list[str] = []
        data = {t.name: list(read_rows(sqlite_conn, t, problems)) for t in tables}
        if problems:
            print(f"\n{len(problems)} value(s) PostgreSQL would refuse. Fix these in the app "
                  "(or the SQLite file), then run this again. Nothing was written.\n")
            for line in problems[:50]:
                print("  " + line)
            if len(problems) > 50:
                print(f"  … and {len(problems) - 50} more")
            return 1
        marks = high_water_marks(sqlite_conn)
        sqlite_conn.close()

        metadata.create_all(target)
        with target.connect() as conn:
            trans = conn.begin()
            try:
                for table in tables:
                    batch = data[table.name]
                    for start in range(0, len(batch), BATCH):
                        conn.execute(table.insert(), batch[start:start + BATCH])
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
                conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version "
                                  "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
                conn.execute(text("DELETE FROM alembic_version"))
                conn.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})
                # Deferred foreign keys are checked here, all at once.
                conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                mismatched = []
                for table in tables:
                    got = conn.execute(select(func.count()).select_from(table)).scalar()
                    if got != len(data[table.name]):
                        mismatched.append(f"{table.name}: {len(data[table.name])} read, {got} written")
                if mismatched:
                    raise RuntimeError("row counts differ: " + "; ".join(mismatched))
                if args.dry_run:
                    trans.rollback()
                else:
                    trans.commit()
            except Exception as exc:
                trans.rollback()
                print(f"\nThe copy failed and was rolled back; the target is unchanged.\n  {exc}")
                return 1

    total = sum(len(rows) for rows in data.values())
    filled = sum(1 for rows in data.values() if rows)
    print(f"copied   : {total} rows in {filled} of {len(tables)} tables, row counts verified")
    print(f"into     : {target.url.render_as_string(hide_password=True)}")
    if args.dry_run:
        print("dry run  : rolled back, nothing kept")
    else:
        print("\nNext: copy the uploads folder to the server's BIOMANAGER_UPLOADS_DIR, "
              "then start the app on the new database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
