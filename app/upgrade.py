"""Upgrading a lab's database, safely.

When a new version of BioManager opens a database an older one made, it
changes the database to fit: new tables, new columns. This module makes
that safe:

- Before anything changes, it copies the database. On SQLite (the desktop
  app, a small server) the copy is `backups/before-upgrade-<from>-to-<to>-<time>.db`
  in the data folder; the last ten are kept. On PostgreSQL the server's own
  backup service takes one before every update (deploy/host/maintenance.sh
  and the runbook), and the log says so.
- Schema changes are Alembic revisions (migrations/versions/), applied in
  order and recorded in `alembic_version`. The hand-written ALTERs of the
  versions before 0.8 (startup.ensure_schema_updates) are frozen: they
  still bring an old database up to 0.8's shape, and revision 0002 marks
  that point. Every change since is a revision, written with the helpers
  in migrations/helpers.py so it can run on a database that already has it.
- A new database is created at the newest shape and stamped with the
  newest revision, so nothing is replayed.
- scripts/upgrade-check.py builds a demo lab with every earlier release,
  upgrades it with this code, and checks the schema matches a new one and
  no row was lost. CI runs it (.github/workflows/upgrade-check.yml).

To undo an upgrade: close BioManager, put the backup copy in place of
biomanager.db, and open the version it came from.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

log = logging.getLogger(__name__)
KEEP_BACKUPS = 10


def _root() -> Path:
    """Where migrations/ is: the source tree, or the unpacked app."""
    import sys
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "migrations"
    if bundled.is_dir():
        return bundled.parent
    return Path(__file__).resolve().parent.parent


def alembic_config():
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", str(_root() / "migrations"))
    cfg.set_main_option("prepend_sys_path", str(_root()))   # revisions import migrations.helpers
    return cfg


def head_revision() -> str:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine) -> str | None:
    with engine.connect() as conn:
        if not inspect(conn).has_table("alembic_version"):
            return None
        row = conn.execute(text("select version_num from alembic_version")).first()
        return row[0] if row else None


@dataclass
class Plan:
    """What opening this database will change."""
    fresh: bool
    current: str | None
    head: str
    legacy: list[str] = field(default_factory=list)   # the frozen ALTERs still to run

    @property
    def changes(self) -> bool:
        return not self.fresh and (bool(self.legacy) or self.current != self.head)


def known_revisions() -> set[str]:
    from alembic.script import ScriptDirectory
    return {r.revision for r in ScriptDirectory.from_config(alembic_config()).walk_revisions()}


def plan(engine) -> Plan:
    from .startup import ensure_schema_updates
    fresh = not inspect(engine).has_table("users")
    current = None if fresh else current_revision(engine)
    if current is not None and current not in known_revisions():
        # A newer BioManager made this database. Running on it anyway would
        # write records without the columns and rules it expects.
        raise SystemExit(
            f"BioManager won't start: this database was set up by a newer version (its schema is {current}; "
            f"this version knows up to {head_revision()}). Install that version or newer, or put back the "
            "backup made before it upgraded the database (deploy/RUNBOOK.md: Updating went wrong).")
    legacy = [] if fresh else ensure_schema_updates(apply=False)
    return Plan(fresh=fresh, current=current, head=head_revision(), legacy=legacy)


def backup_before(engine, the_plan: Plan) -> Path | None:
    """A copy of the database before it is upgraded (SQLite). None when
    there is nothing to copy, or it is PostgreSQL (backed up by the server)."""
    if engine.dialect.name != "sqlite":
        log.warning("Upgrading the database from %s to %s. PostgreSQL is backed up by the server's backup "
                    "service before an update; see deploy/RUNBOOK.md.", the_plan.current or "before 0.8", the_plan.head)
        return None
    source = Path(engine.url.database or "")
    if not source.is_file():
        return None
    folder = source.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = folder / f"before-upgrade-{the_plan.current or 'pre-0.8'}-to-{the_plan.head}-{stamp}.db"
    # SQLite's own backup: a consistent copy even if something else has it open.
    with sqlite3.connect(str(source)) as src, sqlite3.connect(str(target)) as dst:
        src.backup(dst)
    copies = sorted(folder.glob("before-upgrade-*.db"))
    for old in copies[:-KEEP_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass
    log.warning("Upgrading the database: a copy of it as it was is %s", target)
    return target


def migrate(engine, the_plan: Plan) -> None:
    """After the frozen start-up steps: a new database is stamped at the
    newest revision; an existing one is upgraded to it."""
    from alembic import command
    cfg = alembic_config()
    if the_plan.fresh:
        command.stamp(cfg, "head")
        return
    if current_revision(engine) is None:
        command.stamp(cfg, "0001_baseline")
    command.upgrade(cfg, "head")
