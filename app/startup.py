"""What runs when the app opens a database: the hand-written ALTERs of the
versions before 0.8 (frozen; every change since is an Alembic revision,
migrations/versions/), the one-time moves of data from older shapes, the
built-in databases and choices a new lab starts with, and the checks that
refuse to open a database that looks wrong (app/upgrade.py runs the rest).
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path


from sqlalchemy import String, inspect, select, text
from sqlalchemy.orm import load_only

from .db import Base, SessionLocal, engine
from .integrity import ensure_integrity
from .models import (
    CageRecord,
    ChemicalReference,
    DropdownOption,
    DEFAULT_MOUSE_STRAINS,
    MOUSE_STATUS_OPTIONS,
    MouseRecord,
    SampleRecord,
)


DEFAULT_CHEMICALS = [
    ("NaCl", 58.44, "Sodium chloride"),
    ("KCl", 74.55, "Potassium chloride"),
    ("Tris base", 121.14, "Common buffer component"),
    ("EDTA", 292.24, "Chelating agent"),
    ("Glucose", 180.16, "D-glucose"),
    ("Sucrose", 342.30, "Disaccharide"),
    ("HEPES", 238.30, "Buffering agent"),
    ("CaCl2", 110.98, "Calcium chloride"),
    ("MgCl2", 95.21, "Magnesium chloride"),
    ("PBS tablet equivalent", 0.0, "Enter a custom MW if needed for tablets or mixes"),
]


DEFAULT_MOUSE_OPTIONS = {
    "gender": ["F", "M", "Unknown"],
    "purpose": ["Breeder", "Breeding", "Experiment"],
    "status": MOUSE_STATUS_OPTIONS,
    "genotype": ["WT"],
    "strain": DEFAULT_MOUSE_STRAINS,
}


RETIRED_PRESET_FIELDS = ("cage_location",)


def refuse_emptied_database() -> None:
    """A lab's SQLite file that exists but holds nothing (0 bytes: a failed
    copy, a sync tool, a full disk) is not a new lab. Starting on it would
    make a new empty lab with a new setup code, and the next save would
    bury the chance of getting the old one back. Stop and say so."""
    if engine.dialect.name != "sqlite" or not engine.url.database or engine.url.database == ":memory:":
        return
    path = Path(engine.url.database)
    if path.exists() and path.stat().st_size == 0:
        raise SystemExit(
            f"BioManager won't start: the lab's database {path} is an empty file (0 bytes), so its records "
            "are not in it. Put back a backup (deploy/RUNBOOK.md, or scripts/dbtool.py restore), or delete "
            "the empty file to start a new lab.")


def init_database() -> None:
    # A new installation starts empty: the first admin's setup survey decides
    # which databases exist (app/lab.py). BIOMANAGER_SEED_DEFAULTS=1 keeps
    # the old start with every default database, for the test suite and
    # scripts/demo-data.py.
    # Opening a database an older version made changes it: copy it first
    # (app/upgrade.py), then the frozen start-up steps, then Alembic.
    from . import upgrade
    refuse_emptied_database()
    the_plan = upgrade.plan(engine)
    if the_plan.changes:
        upgrade.backup_before(engine, the_plan)
    fresh = the_plan.fresh
    Base.metadata.create_all(bind=engine)
    if fresh and os.environ.get("BIOMANAGER_SEED_DEFAULTS") != "1":
        from . import lab
        with SessionLocal() as session:
            lab.start_empty(session)
            session.commit()
    # Nothing is numbered for devices (app/sync_clock.py) until Alembic has
    # given the records their sync_version, nor a cage placed_at stamped:
    # the steps before it save records.
    from . import models, sync_clock
    sync_clock.enabled = models.stamp_placements = False
    ensure_schema_updates()
    rebuild_samples_table()
    ensure_integrity()  # ids never reused, foreign keys enforced (app/integrity.py)
    migrate_cage_locations()
    backfill_cage_owners()
    upgrade.migrate(engine, the_plan)
    sync_clock.enabled = models.stamp_placements = True
    warn_if_database_is_synced()
    # A lab server's backup service locks each backup with the lab's key
    # (deploy/compose.yaml: the labkey volume; app/lab_key.py).
    from . import lab_key
    lab_key.export_if_asked()
    seed_organism_modules()
    seed_inventories()
    seed_stocks()
    migrate_plasmid_boxes()
    encrypt_stored_tokens()
    with SessionLocal() as session:
        existing_chemical = session.scalar(select(ChemicalReference.id).limit(1))
        if existing_chemical is None:
            for name, mw, notes in DEFAULT_CHEMICALS:
                session.add(ChemicalReference(name=name, molecular_weight=mw, notes=notes))

        existing_options = session.scalar(select(DropdownOption.id).limit(1))
        if existing_options is None:
            for field_name, values in DEFAULT_MOUSE_OPTIONS.items():
                for value in values:
                    session.add(DropdownOption(field_name=field_name, option_value=value))
            session.flush()   # so the tidy-up below sees them (the built-in statuses aren't stored)

        retired_rows = session.scalars(
            select(DropdownOption).where(DropdownOption.field_name.in_(RETIRED_PRESET_FIELDS))
        ).all()
        for row in retired_rows:
            session.delete(row)

        lowercase_status_rows = session.scalars(
            select(DropdownOption).where(
                DropdownOption.field_name == "status",
                DropdownOption.option_value.in_(MOUSE_STATUS_OPTIONS),
            )
        ).all()
        for row in lowercase_status_rows:
            session.delete(row)
        session.commit()


def encrypt_stored_tokens() -> int:
    """Encrypt Google Calendar tokens saved before they were encrypted at
    rest. Reads the raw column (bypassing EncryptedText, which would hand
    back plain text either way) and rewrites what is still plain."""
    from . import security

    changed = 0
    with engine.begin() as conn:
        found = conn.execute(text("SELECT id, refresh_token, access_token FROM google_calendar_links")).all()
        for row_id, refresh, access in found:
            new_refresh, new_access = security.encrypt_text(refresh), security.encrypt_text(access)
            if (new_refresh, new_access) != (refresh, access):
                conn.execute(text("UPDATE google_calendar_links SET refresh_token = :r, access_token = :a WHERE id = :i"),
                             {"r": new_refresh, "a": new_access, "i": row_id})
                changed += 1
    return changed


def ensure_schema_updates(apply: bool = True) -> list[str]:
    """The hand-written ALTERs of the versions before 0.8, frozen: they bring
    an older database up to 0.8's shape. Every schema change since is an
    Alembic revision (migrations/versions/, app/upgrade.py). With
    `apply=False`, only says which ALTERs an older database still needs."""
    # These ALTERs were written for SQLite. TRUE/FALSE defaults work on both;
    # PostgreSQL has no DATETIME type, so it gets TIMESTAMP.
    timestamp = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
    inspector = inspect(engine)
    table_columns = {table: {col["name"] for col in inspector.get_columns(table)} for table in inspector.get_table_names()}
    alter_statements: list[str] = []

    if "mice" in table_columns:
        if "transgene_1" not in table_columns["mice"]:
            alter_statements.extend(
                [
                    "ALTER TABLE mice ADD COLUMN transgene_1 VARCHAR(200) DEFAULT ''",
                    "ALTER TABLE mice ADD COLUMN transgene_2 VARCHAR(200) DEFAULT ''",
                    "ALTER TABLE mice ADD COLUMN transgene_3 VARCHAR(200) DEFAULT ''",
                    "ALTER TABLE mice ADD COLUMN transgene_4 VARCHAR(200) DEFAULT ''",
                ]
            )
    if "experiment_steps" in table_columns and "reagent_item_id_fk" not in table_columns["experiment_steps"]:
        alter_statements.append("ALTER TABLE experiment_steps ADD COLUMN reagent_item_id_fk INTEGER")
    if "experiment_step_records" in table_columns and "reagent" not in table_columns["experiment_step_records"]:
        alter_statements.extend([
            "ALTER TABLE experiment_step_records ADD COLUMN reagent TEXT DEFAULT ''",
            "ALTER TABLE experiment_step_records ADD COLUMN samples TEXT DEFAULT ''",
        ])
    if "experiments" in table_columns and "db" not in table_columns["experiments"]:
        # Experiments on every database's animals, not only mice (app/experiments.py).
        alter_statements.extend([
            "ALTER TABLE experiments ADD COLUMN db VARCHAR(120) DEFAULT 'colony'",
            "ALTER TABLE experiments ADD COLUMN readout TEXT DEFAULT ''",
        ])
    if "audit_log" in table_columns and "batch_id_fk" not in table_columns["audit_log"]:
        alter_statements.extend([
            "ALTER TABLE audit_log ADD COLUMN batch_id_fk INTEGER",
            "ALTER TABLE audit_log ADD COLUMN changes_json TEXT DEFAULT ''",
        ])
    if "mouse_cages" in table_columns and "owner" not in table_columns["mouse_cages"]:
        alter_statements.extend([
            "ALTER TABLE mouse_cages ADD COLUMN owner VARCHAR(120) DEFAULT ''",
            "ALTER TABLE mouse_cages ADD COLUMN is_shared BOOLEAN DEFAULT FALSE",
        ])
    for table in ("stock_racks", "stock_incubators"):
        if table in table_columns and "created_by" not in table_columns[table]:
            alter_statements.append(f"ALTER TABLE {table} ADD COLUMN created_by VARCHAR(80) DEFAULT ''")
    if "strains" in table_columns and "strain_number" not in table_columns["strains"]:
        alter_statements.append("ALTER TABLE strains ADD COLUMN strain_number VARCHAR(80) DEFAULT ''")
    if "strains" in table_columns and "created_by" not in table_columns["strains"]:
        alter_statements.append("ALTER TABLE strains ADD COLUMN created_by VARCHAR(80) DEFAULT ''")
    if "litters" in table_columns:
        if "father_info" not in table_columns["litters"]:
            alter_statements.extend(
                [
                    "ALTER TABLE litters ADD COLUMN father_info VARCHAR(120) DEFAULT ''",
                    "ALTER TABLE litters ADD COLUMN mother_info VARCHAR(120) DEFAULT ''",
                    "ALTER TABLE litters ADD COLUMN total_pups INTEGER DEFAULT 0",
                ]
            )
    if "users" in table_columns:
        existing = table_columns["users"]
        if "short_name" not in existing:
            alter_statements.append("ALTER TABLE users ADD COLUMN short_name VARCHAR(20) DEFAULT ''")
        if "email" not in existing:
            alter_statements.append("ALTER TABLE users ADD COLUMN email VARCHAR(200) DEFAULT ''")
        if "role_title" not in existing:
            alter_statements.append("ALTER TABLE users ADD COLUMN role_title VARCHAR(80) DEFAULT ''")
        if "default_landing" not in existing:
            alter_statements.append("ALTER TABLE users ADD COLUMN default_landing VARCHAR(40) DEFAULT ''")
        if "disabled" not in existing:
            alter_statements.append("ALTER TABLE users ADD COLUMN disabled BOOLEAN DEFAULT FALSE")
        if "notify_transfer" not in existing:
            alter_statements.append("ALTER TABLE users ADD COLUMN notify_transfer BOOLEAN DEFAULT TRUE")
        if "notify_picked" not in existing:
            alter_statements.append("ALTER TABLE users ADD COLUMN notify_picked BOOLEAN DEFAULT TRUE")
        if "notify_breeder_aging" not in existing:
            alter_statements.append("ALTER TABLE users ADD COLUMN notify_breeder_aging BOOLEAN DEFAULT TRUE")
        for column in ("notify_genotyping", "notify_orders", "notify_lab", "notify_notebook", "notify_experiments"):
            if column not in existing:
                alter_statements.append(f"ALTER TABLE users ADD COLUMN {column} BOOLEAN DEFAULT TRUE")
        if "welcomed_at" not in existing:
            alter_statements.append(f"ALTER TABLE users ADD COLUMN welcomed_at {timestamp}")
        if "expires_at" not in existing:
            alter_statements.append(f"ALTER TABLE users ADD COLUMN expires_at {timestamp}")

    # In-app notifications: what kind, where it points, who caused it (app/notify.py).
    if "notifications" in table_columns:
        existing = table_columns["notifications"]
        if "category" not in existing:
            alter_statements.append("ALTER TABLE notifications ADD COLUMN category VARCHAR(40) DEFAULT 'general'")
        if "link" not in existing:
            alter_statements.append("ALTER TABLE notifications ADD COLUMN link VARCHAR(300) DEFAULT ''")
        if "actor" not in existing:
            alter_statements.append("ALTER TABLE notifications ADD COLUMN actor VARCHAR(80) DEFAULT ''")

    # Personal databases: private_to names the one person a database is for;
    # empty means the whole lab (app/lab.py).
    for module_table in ("organism_modules", "stock_modules", "inventory_modules"):
        if module_table in table_columns and "private_to" not in table_columns[module_table]:
            alter_statements.append(f"ALTER TABLE {module_table} ADD COLUMN private_to VARCHAR(80) DEFAULT ''")

    if "notebook_pages" in table_columns and "entry_date" not in table_columns["notebook_pages"]:
        alter_statements.append("ALTER TABLE notebook_pages ADD COLUMN entry_date DATE")
    if "notebook_pages" in table_columns and "properties" not in table_columns["notebook_pages"]:
        alter_statements.append("ALTER TABLE notebook_pages ADD COLUMN properties TEXT DEFAULT ''")

    for tbl in ("mice", "plasmids", "orders"):
        if tbl in table_columns:
            if "updated_at" not in table_columns[tbl]:
                alter_statements.append(f"ALTER TABLE {tbl} ADD COLUMN updated_at {timestamp}")
            if "updated_by" not in table_columns[tbl]:
                alter_statements.append(f"ALTER TABLE {tbl} ADD COLUMN updated_by VARCHAR(80) DEFAULT ''")

    if "plasmids" in table_columns:
        if "full_sequence" not in table_columns["plasmids"]:
            alter_statements.append("ALTER TABLE plasmids ADD COLUMN full_sequence TEXT DEFAULT ''")
        if "is_circular" not in table_columns["plasmids"]:
            alter_statements.append("ALTER TABLE plasmids ADD COLUMN is_circular BOOLEAN DEFAULT TRUE")
        if "features_json" not in table_columns["plasmids"]:
            alter_statements.append("ALTER TABLE plasmids ADD COLUMN features_json TEXT DEFAULT ''")
        if "sequence_format" not in table_columns["plasmids"]:
            alter_statements.append("ALTER TABLE plasmids ADD COLUMN sequence_format VARCHAR(20) DEFAULT ''")
        if "sequence_uploaded_at" not in table_columns["plasmids"]:
            alter_statements.append(f"ALTER TABLE plasmids ADD COLUMN sequence_uploaded_at {timestamp}")
        if "storage_box" not in table_columns["plasmids"]:
            alter_statements.append("ALTER TABLE plasmids ADD COLUMN storage_box VARCHAR(80) DEFAULT ''")
        if "box_row" not in table_columns["plasmids"]:
            alter_statements.append("ALTER TABLE plasmids ADD COLUMN box_row INTEGER")
        if "box_col" not in table_columns["plasmids"]:
            alter_statements.append("ALTER TABLE plasmids ADD COLUMN box_col INTEGER")
        # Plasmid boxes became their own table (2026-09-25); see migrate_plasmid_boxes.
        if "box_id_fk" not in table_columns["plasmids"]:
            alter_statements.append("ALTER TABLE plasmids ADD COLUMN box_id_fk INTEGER REFERENCES plasmid_boxes(id)")

    if "calendar_events" in table_columns:
        cols = table_columns["calendar_events"]
        if "start_at" not in cols:
            alter_statements.append(f"ALTER TABLE calendar_events ADD COLUMN start_at {timestamp}")
        if "end_at" not in cols:
            alter_statements.append(f"ALTER TABLE calendar_events ADD COLUMN end_at {timestamp}")
        if "is_all_day" not in cols:
            alter_statements.append("ALTER TABLE calendar_events ADD COLUMN is_all_day BOOLEAN DEFAULT TRUE")
        if "color" not in cols:
            alter_statements.append("ALTER TABLE calendar_events ADD COLUMN color VARCHAR(20) DEFAULT ''")
        if "owner" not in cols:
            alter_statements.append("ALTER TABLE calendar_events ADD COLUMN owner VARCHAR(80) DEFAULT ''")

    if "tasks" in table_columns:
        cols = table_columns["tasks"]
        if "start_at" not in cols:
            alter_statements.append(f"ALTER TABLE tasks ADD COLUMN start_at {timestamp}")
        if "end_at" not in cols:
            alter_statements.append(f"ALTER TABLE tasks ADD COLUMN end_at {timestamp}")
        if "done_at" not in cols:
            alter_statements.append(f"ALTER TABLE tasks ADD COLUMN done_at {timestamp}")
        if "color" not in cols:
            alter_statements.append("ALTER TABLE tasks ADD COLUMN color VARCHAR(20) DEFAULT ''")
        if "owner" not in cols:
            alter_statements.append("ALTER TABLE tasks ADD COLUMN owner VARCHAR(80) DEFAULT ''")

    # Rack naming schemes and structured cage placement (2026-09-24).
    if "mouse_racks" in table_columns and "naming" not in table_columns["mouse_racks"]:
        alter_statements.append("ALTER TABLE mouse_racks ADD COLUMN naming TEXT DEFAULT '{}'")
    if "fish_racks" in table_columns and "naming" not in table_columns["fish_racks"]:
        alter_statements.append("ALTER TABLE fish_racks ADD COLUMN naming TEXT DEFAULT '{}'")
    if "fish_racks" in table_columns and "created_by" not in table_columns["fish_racks"]:
        alter_statements.append("ALTER TABLE fish_racks ADD COLUMN created_by VARCHAR(80) DEFAULT ''")
    if "mouse_racks" in table_columns and "created_by" not in table_columns["mouse_racks"]:
        alter_statements.append("ALTER TABLE mouse_racks ADD COLUMN created_by VARCHAR(80) DEFAULT ''")
    if "mouse_cages" in table_columns:
        for column, ddl in (("rack_id_fk", "INTEGER REFERENCES mouse_racks(id)"),
                            ("rack_row", "INTEGER"), ("rack_col", "INTEGER")):
            if column not in table_columns["mouse_cages"]:
                alter_statements.append(f"ALTER TABLE mouse_cages ADD COLUMN {column} {ddl}")

    # Who added each custom database's rack or room (2026-09-27).
    if "organism_locations" in table_columns and "created_by" not in table_columns["organism_locations"]:
        alter_statements.append("ALTER TABLE organism_locations ADD COLUMN created_by VARCHAR(80) DEFAULT ''")

    # When a litter was weaned, so it stops being due everywhere (2026-09-26).
    if "litters" in table_columns and "weaned_on" not in table_columns["litters"]:
        alter_statements.append("ALTER TABLE litters ADD COLUMN weaned_on DATE")

    # Who made each inventory box, for who may resize or delete it (2026-09-25).
    if "inventory_racks" in table_columns and "created_by" not in table_columns["inventory_racks"]:
        alter_statements.append("ALTER TABLE inventory_racks ADD COLUMN created_by VARCHAR(80) DEFAULT ''")
    # Zebrafish sac rule: a sac date on fish rows, and which row a sac-log
    # entry came from (2026-09-25).
    if "fish" in table_columns and "sac_date" not in table_columns["fish"]:
        alter_statements.append("ALTER TABLE fish ADD COLUMN sac_date DATE")
    if "fish_sac_log" in table_columns and "fish_id_fk" not in table_columns["fish_sac_log"]:
        alter_statements.append("ALTER TABLE fish_sac_log ADD COLUMN fish_id_fk INTEGER")
    # Zebrafish line owners and water-system creators (2026-09-25).
    if "fish_lines" in table_columns and "owner" not in table_columns["fish_lines"]:
        alter_statements.append("ALTER TABLE fish_lines ADD COLUMN owner VARCHAR(80) DEFAULT ''")
    if "water_systems" in table_columns and "created_by" not in table_columns["water_systems"]:
        alter_statements.append("ALTER TABLE water_systems ADD COLUMN created_by VARCHAR(80) DEFAULT ''")

    if not apply:
        return alter_statements
    if alter_statements:
        with engine.begin() as connection:
            for statement in alter_statements:
                connection.execute(text(statement))

    # Calendar integration tables — created by Base.metadata.create_all in
    # init_db, but we also assert here so older deployments pick them up.
    if "calendar_subscriptions" not in table_columns:
        Base.metadata.tables["calendar_subscriptions"].create(bind=engine, checkfirst=True)
    if "google_calendar_links" not in table_columns:
        Base.metadata.tables["google_calendar_links"].create(bind=engine, checkfirst=True)

    # Zebrafish module tables — same approach.
    for fish_table in ("water_systems", "fish_racks", "fish_lines", "tanks",
                       "fish", "clutches", "water_logs", "fish_sac_log"):
        if fish_table not in table_columns:
            Base.metadata.tables[fish_table].create(bind=engine, checkfirst=True)
    if "water_logs" in table_columns:
        _water_logs_system_nullable()
    return alter_statements


def _water_logs_system_nullable() -> None:
    """Readings outlive a deleted water system (2026-09-25), so
    water_logs.system_id_fk may be blank. SQLite can't drop NOT NULL in
    place: the table is rebuilt from the model and the rows copied over."""
    inspector = inspect(engine)  # fresh: the ALTERs above may have added columns
    column = next((c for c in inspector.get_columns("water_logs") if c["name"] == "system_id_fk"), None)
    if column is None or column.get("nullable", True):
        return
    if engine.dialect.name != "sqlite":
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE water_logs ALTER COLUMN system_id_fk DROP NOT NULL"))
        return
    table = Base.metadata.tables["water_logs"]
    names = [c["name"] for c in inspector.get_columns("water_logs") if c["name"] in table.c]
    cols = ", ".join(names)
    indexes = [index["name"] for index in inspector.get_indexes("water_logs")]
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE water_logs RENAME TO water_logs_before_nullable"))
        for name in indexes:
            connection.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
        table.create(bind=connection)
        connection.execute(text(f"INSERT INTO water_logs ({cols}) SELECT {cols} FROM water_logs_before_nullable"))
        connection.execute(text("DROP TABLE water_logs_before_nullable"))


def migrate_cage_locations() -> int:
    """Move cage placements written into the location text ("B-D7", the
    format used before racks had structured positions) into the cage's rack
    fields, and clear the text. Text that names no rack ("789", "shelf 2")
    is a free-text location note and is left alone. Idempotent."""
    from .models import MouseRack
    from . import positions

    pattern = re.compile(r"^\s*(?P<rack>.+?)\s*[-/: ]\s*(?P<pos>[A-Za-z]\s*\d{1,2})\s*$")
    moved = 0
    with SessionLocal() as session:
        racks = {r.name.lower(): r for r in session.scalars(select(MouseRack).options(load_only(
            MouseRack.id, MouseRack.name, MouseRack.naming, MouseRack.rows, MouseRack.cols)))}
        if not racks:
            return 0
        # Only the columns it uses: this runs before Alembic adds newer ones.
        # Only cages with location text: it runs at every start, and a big
        # lab's thousands of cages with none can have nothing to move.
        for cage in session.scalars(select(CageRecord).options(load_only(
                CageRecord.id, CageRecord.cage_location, CageRecord.rack_id_fk, CageRecord.rack_row,
                CageRecord.rack_col)).where(CageRecord.rack_id_fk.is_(None),
                                            CageRecord.cage_location.is_not(None),
                                            CageRecord.cage_location != "")):
            match = pattern.match(cage.cage_location or "")
            rack = racks.get(match.group("rack").strip().lower()) if match else None
            cell = positions.parse(match.group("pos"), rack.naming, rack.rows, rack.cols) if rack else None
            if cell:
                cage.rack_id_fk, (cage.rack_row, cage.rack_col) = rack.id, cell
                cage.cage_location = ""
                moved += 1
        if moved:
            session.commit()
    return moved


def rebuild_samples_table() -> None:
    """Bring an older `samples` table up to the current model.

    Older databases have animal_id_fk NOT NULL and no source/owner columns.
    SQLite cannot relax NOT NULL in place, so the table is rebuilt: renamed
    aside, recreated from the model, rows copied across, old copy dropped,
    all in one transaction. Idempotent; a no-op once the table is current.
    A sample linked to the old `animals` table keeps that animal's number
    as its source; revision 0055 then drops the link and the table.
    """
    inspector = inspect(engine)
    if "samples" not in inspector.get_table_names():
        return
    columns = {col["name"]: col for col in inspector.get_columns("samples")}
    wanted = set(SampleRecord.__table__.columns.keys())
    linked = "animal_id_fk" in columns
    if (linked and not columns["animal_id_fk"]["nullable"]) or not wanted <= set(columns):
        if engine.dialect.name != "sqlite":
            with engine.begin() as conn:
                if linked:
                    conn.execute(text("ALTER TABLE samples ALTER COLUMN animal_id_fk DROP NOT NULL"))
                for name in sorted(wanted - set(columns)):
                    column = SampleRecord.__table__.columns[name]
                    conn.execute(text(f"ALTER TABLE samples ADD COLUMN {name} {column.type.compile(engine.dialect)}"))
            return
        table = SampleRecord.__table__
        shared = sorted(set(columns) & wanted)
        # New NOT NULL columns have only Python-side defaults, so the copy
        # supplies them: empty text for strings, 0 for anything else.
        added = [name for name in sorted(wanted - set(columns)) if not table.columns[name].nullable]
        column_list = ", ".join(shared + added)
        select_list = ", ".join(shared + [
            "''" if isinstance(table.columns[name].type, String) else "0" for name in added])
        # Index names are global in SQLite and follow the table on rename,
        # so collect them first and drop them once it has moved aside.
        old_indexes = [index["name"] for index in inspector.get_indexes("samples")]
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE samples RENAME TO samples_old"))
            for name in old_indexes:
                conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
            table.create(conn)
            conn.execute(text(
                f"INSERT INTO samples ({column_list}) SELECT {select_list} FROM samples_old"))
            # Rows from the old animal link keep a readable source.
            if linked and "animals" in inspector.get_table_names():
                conn.execute(text(
                    "UPDATE samples SET source_kind = 'other', source_ref = "
                    "(SELECT a.animal_id FROM samples_old o JOIN animals a ON a.id = o.animal_id_fk "
                    "WHERE o.id = samples.id) "
                    "WHERE source_kind = '' AND id IN (SELECT id FROM samples_old WHERE animal_id_fk IS NOT NULL)"))
            conn.execute(text("DROP TABLE samples_old"))


PLASMID_BOXES_FLAG = "migrated:plasmid_boxes"


def migrate_plasmid_boxes() -> dict | None:
    """Once: turn the free-text box names plasmids used into PlasmidBox rows.

    Before, a box was only a name typed on each plasmid, and its size lived
    in each browser. Every distinct (trimmed) name becomes a box of at least
    9 × 9, grown to hold the furthest row and column used, named letters ×
    numbers ("D7") as the old grid labelled it. Its plasmids are linked to
    it; storage_box / box_row / box_col are left as they were, so the old
    code still reads them after a rollback. Two plasmids in one cell keep
    the lower number there; the other waits unplaced in the box. The boxes
    have no creator, so only an admin may resize or delete them."""
    import json as _json

    from . import positions
    from .inventory_service import get_setting, set_setting
    from .models import PlasmidBox, PlasmidRecord

    with SessionLocal() as session:
        if get_setting(session, PLASMID_BOXES_FLAG):
            return None
        plasmids = session.scalars(select(PlasmidRecord).order_by(PlasmidRecord.plasmid_id)).all()
        by_name: dict[str, list] = defaultdict(list)
        for p in plasmids:
            name = (p.storage_box or "").strip()[:80]
            if name and p.box_id_fk is None:
                by_name[name].append(p)
        existing = {b.name: b for b in session.scalars(select(PlasmidBox))}
        created = linked = doubled = 0
        naming = _json.dumps(positions.scheme({}))
        for name, members in sorted(by_name.items()):
            rows = max([9] + [p.box_row + 1 for p in members if p.box_row is not None and p.box_row >= 0])
            cols = max([9] + [p.box_col + 1 for p in members if p.box_col is not None and p.box_col >= 0])
            box = existing.get(name)
            if box is None:
                box = PlasmidBox(name=name, rows=min(rows, 26), cols=min(cols, 40), naming=naming, created_by="")
                session.add(box)
                session.flush()
                existing[name] = box
                created += 1
            taken = set()
            for p in members:
                p.box_id_fk, p.storage_box = box.id, name
                cell = (p.box_row, p.box_col)
                if p.box_row is None or p.box_col is None or not (0 <= p.box_row < box.rows and 0 <= p.box_col < box.cols) \
                        or cell in taken:
                    doubled += cell in taken
                    p.box_row = p.box_col = None
                else:
                    taken.add(cell)
                linked += 1
        set_setting(session, PLASMID_BOXES_FLAG, datetime.utcnow().isoformat(timespec="seconds"))
        session.commit()
        return {"boxes": created, "plasmids": linked, "unplaced_doubles": doubled}


def seed_stocks() -> None:
    """Create the Drosophila and C. elegans vial/plate databases once,
    moving anything kept in the old organism-engine versions."""
    from . import stock_service
    stock_service.seed_modules()
    stock_service.tidy_genotype_lists()


def seed_inventories() -> None:
    """Create the samples, orders, reagents and antibodies inventories once,
    then move any rows from the old fixed samples/orders tables into them."""
    from . import inventory_service as inventories
    inventories.seed_modules()
    inventories.migrate_legacy()


def seed_organism_modules() -> None:
    """Create the configurable organism modules that ship with the app.

    Only organisms without a hand-written module are seeded (flies, worms) —
    see organisms.AUTO_SEED_PRESETS. Safe to run on every boot; it is a
    no-op once the modules exist.
    """
    from . import organism_service as organisms

    with SessionLocal() as session:
        created = organisms.seed_builtin_modules(session)
        repaired = organisms.repair_icon_names(session)
        gridded = organisms.offer_housing_grid(session)
        if created or repaired or gridded:
            session.commit()


def backfill_cage_owners() -> None:
    """Give every unowned cage the owner of the mice it holds.

    Runs once, after the owner column is added. A cage whose mice disagree
    about ownership is left unowned — that is genuinely shared, and guessing
    would hand it to whoever happens to have the most animals in it.
    """
    from collections import Counter

    with SessionLocal() as session:
        # Only the columns it uses: this runs before Alembic adds newer ones.
        cages = session.scalars(
            select(CageRecord).options(load_only(CageRecord.id, CageRecord.owner)).where(
                (CageRecord.owner.is_(None)) | (CageRecord.owner == "")
            )
        ).all()
        if not cages:
            return
        held: dict[int, list[str]] = {}
        for cage_id, owner in session.execute(select(MouseRecord.cage_id_fk, MouseRecord.owner).where(
                MouseRecord.cage_id_fk.in_([c.id for c in cages]))):
            held.setdefault(cage_id, []).append(owner or "")
        changed = 0
        for cage in cages:
            owners = Counter(owner.strip() for owner in held.get(cage.id, []) if owner.strip())
            if len(owners) == 1:
                cage.owner = next(iter(owners))
                changed += 1
        if changed:
            session.commit()


def warn_if_database_is_synced() -> None:
    """Shout if the SQLite file is sitting in a cloud-synced folder.

    Cloud clients do not honour SQLite's locking: a sync mid-write, or two
    machines with the folder open, corrupts the file. This is the single
    most likely way a lab loses its colony records, and it fails silently
    until the day it does not.
    """
    import logging
    from .db import DATABASE_URL
    from .paths import sync_risk

    if not DATABASE_URL.startswith("sqlite"):
        return
    db_file = DATABASE_URL.split("///", 1)[-1]
    provider = sync_risk(Path(db_file))
    if not provider:
        return
    logging.getLogger("biomanager").warning(
        "\n" + "!" * 72
        + f"\n  The database is inside a {provider} folder:\n    {db_file}\n"
        "  Cloud sync can corrupt a SQLite file. Move it somewhere local:\n"
        "    python scripts/dbtool.py relocate ~/BioManagerData\n"
        + "!" * 72
    )


def stamp_alembic_baseline() -> None:
    """Mark a database as sitting at the Alembic baseline.

    Existing databases were built by create_all() plus the hand-written
    ALTERs above, so there is nothing to replay — they just need a version
    to upgrade *from*. No-op once alembic_version exists.
    """
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            if engine.dialect.name == "sqlite":
                probe = "select 1 from sqlite_master where type='table' and name='alembic_version'"
            else:
                probe = ("select 1 from information_schema.tables"
                         " where table_name='alembic_version'")
            if conn.execute(text(probe)).first():
                return
            conn.execute(text(
                "create table alembic_version (version_num varchar(32) not null)"))
            conn.execute(text(
                "insert into alembic_version (version_num) values ('0001_baseline')"))
    except Exception:
        # Migration bookkeeping must never stop the app from starting.
        pass
