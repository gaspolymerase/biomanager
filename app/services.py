from __future__ import annotations

import csv
import io
import os
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from sqlalchemy import String, func, inspect, select, text
from sqlalchemy.orm import load_only

from .db import BASE_DIR, Base, SessionLocal, engine
from .i18n import gettext
from .integrity import ensure_integrity
from .paths import uploads_dir
from .models import (
    AnimalRecord,
    CageRecord,
    CalendarEvent,
    CalendarSubscription,
    ClutchRecord,
    Experiment,
    FishLine,
    FishRack,
    FishRecord,
    GoogleCalendarLink,
    TankRecord,
    WaterLog,
    WaterSystem,
    ChemicalReference,
    DropdownOption,
    LitterRecord,
    MOUSE_PRESET_FIELDS,
    MOUSE_STATUS_OPTIONS,
    MouseRecord,
    NotificationRecord,
    NotebookEntry,
    Order,
    SampleRecord,
    StrainRecord,
    TaskItem,
    UserAccount,
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
    "purpose": ["Breeder", "Breeding", "Exp"],
    "status": MOUSE_STATUS_OPTIONS,
    "genotype": ["WT"],
}
RETIRED_PRESET_FIELDS = ("cage_location",)
UPLOAD_DIR = uploads_dir()


def parse_date(raw_value: str | None) -> date | None:
    if not raw_value:
        return None
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    for fmt in ("%Y-%m-%d", "%y%m%d", "%y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


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
    ensure_schema_updates()
    rebuild_samples_table()
    ensure_integrity()  # ids never reused, foreign keys enforced (app/integrity.py)
    migrate_cage_locations()
    backfill_cage_owners()
    upgrade.migrate(engine, the_plan)
    warn_if_database_is_synced()
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


# ---------------------------------------------------------------------------
# Calendar subscriptions (ICS) — fetch a remote .ics URL, parse it with the
# `icalendar` library, cache the parsed events for ~10 min, and surface them
# in TOAST UI Calendar's schedule shape.
# ---------------------------------------------------------------------------

ICS_CACHE_TTL = timedelta(minutes=10)


def fetch_ics_subscription(sub: "CalendarSubscription", session, force: bool = False) -> list[dict]:
    """Fetch + parse `sub.url`. Returns a list of TOAST UI schedule dicts.
    Honors a 10-minute cache so repeated /calendar/events.json hits don't
    hammer the source. Errors are stored on `sub.last_error` (non-fatal —
    we just return whatever the previous cache had)."""
    import json as _json
    now = datetime.utcnow()

    if (
        not force
        and sub.cached_payload
        and sub.last_fetched_at
        and now - sub.last_fetched_at < ICS_CACHE_TTL
    ):
        try:
            return _json.loads(sub.cached_payload)
        except _json.JSONDecodeError:
            pass  # fall through to refetch

    try:
        events = _do_fetch_ics(sub.url, sub.color or "#10b981", sub.id)
        sub.cached_payload = _json.dumps(events)
        sub.last_fetched_at = now
        sub.last_error = ""
        session.add(sub)
        session.commit()
        return events
    except Exception as exc:
        sub.last_error = f"{type(exc).__name__}: {exc}"[:500]
        session.add(sub)
        session.commit()
        # Return whatever's cached if available — better than nothing.
        if sub.cached_payload:
            try:
                return _json.loads(sub.cached_payload)
            except _json.JSONDecodeError:
                return []
        return []


def _do_fetch_ics(url: str, color: str, sub_id: int) -> list[dict]:
    """Fetch one ICS URL and translate VEVENTs into TOAST UI schedule shape."""
    import urllib.request
    from icalendar import Calendar as _Calendar  # local import to keep cold-start fast

    req = urllib.request.Request(url, headers={"User-Agent": "BioManager-Calendar/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()

    cal = _Calendar.from_ical(raw)
    out: list[dict] = []
    for component in cal.walk("VEVENT"):
        try:
            uid = str(component.get("UID") or f"sub-{sub_id}-{len(out)}")
            summary = str(component.get("SUMMARY") or "(no title)")
            description = str(component.get("DESCRIPTION") or "")
            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND") or dtstart
            if dtstart is None:
                continue
            start_val = dtstart.dt
            end_val = dtend.dt if dtend is not None else start_val
            is_all_day = isinstance(start_val, date) and not isinstance(start_val, datetime)
            if is_all_day:
                start_iso = datetime.combine(start_val, datetime.min.time()).isoformat()
                # ICS DTEND for all-day is exclusive; subtract a day for display.
                effective_end = end_val - timedelta(days=1) if isinstance(end_val, date) and end_val > start_val else start_val
                end_iso = datetime.combine(effective_end, datetime.max.time()).isoformat()
            else:
                start_iso = (start_val if isinstance(start_val, datetime) else datetime.combine(start_val, datetime.min.time())).isoformat()
                end_iso = (end_val if isinstance(end_val, datetime) else datetime.combine(end_val, datetime.max.time())).isoformat()
            out.append({
                "id": f"sub-{sub_id}-{uid}",
                "kind": "subscription",
                "calendarId": f"sub-{sub_id}",
                "title": summary,
                "category": "allday" if is_all_day else "time",
                "isAllday": is_all_day,
                "start": start_iso,
                "end": end_iso,
                "backgroundColor": color,
                "borderColor": color,
                "body": description,
                "isReadOnly": True,
                "raw": {"subId": sub_id, "uid": uid},
            })
        except Exception:
            # Skip individual broken VEVENTs but keep the rest.
            continue
    return out


# ---------------------------------------------------------------------------
# Google Calendar OAuth (Pass 3)
# ---------------------------------------------------------------------------

GOOGLE_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def google_oauth_configured() -> bool:
    import os
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def google_client_config(redirect_uri: str) -> dict:
    """The dict that google_auth_oauthlib expects when no client_secrets.json
    file is present — we just synthesize it from env vars."""
    import os
    return {
        "web": {
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def fetch_google_calendar_items(link: "GoogleCalendarLink", session, start_dt=None, end_dt=None) -> list[dict]:
    """Read events from one connected Google Calendar. Returns TOAST UI
    schedule shape. Refresh-token-based — we never need an interactive login
    after the initial OAuth."""
    import os
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=link.access_token or None,
        refresh_token=link.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
        scopes=GOOGLE_OAUTH_SCOPES,
    )
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # Default to ±60 days if no window given.
    if start_dt is None:
        start_dt = datetime.utcnow() - timedelta(days=60)
    if end_dt is None:
        end_dt = datetime.utcnow() + timedelta(days=60)
    time_min = start_dt.isoformat() + "Z" if start_dt.tzinfo is None else start_dt.isoformat()
    time_max = end_dt.isoformat() + "Z" if end_dt.tzinfo is None else end_dt.isoformat()

    resp = service.events().list(
        calendarId=link.calendar_id or "primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=500,
    ).execute()

    # Persist any refreshed access token so we don't refresh on every call.
    if creds.token and creds.token != link.access_token:
        link.access_token = creds.token
        link.token_expiry = creds.expiry
    link.last_synced_at = datetime.utcnow()
    session.add(link)
    session.commit()

    out: list[dict] = []
    for ev in resp.get("items", []):
        try:
            start = ev["start"]
            end = ev.get("end", start)
            is_all_day = "date" in start
            if is_all_day:
                start_iso = datetime.fromisoformat(start["date"]).isoformat()
                # Google's all-day end is exclusive — subtract a day for display.
                end_date = datetime.fromisoformat(end["date"]) - timedelta(days=1)
                end_iso = datetime.combine(end_date.date(), datetime.max.time()).isoformat()
            else:
                start_iso = start["dateTime"]
                end_iso = end.get("dateTime", start["dateTime"])
            out.append({
                "id": f"google-{link.id}-{ev['id']}",
                "kind": "google",
                "calendarId": "google",
                "title": ev.get("summary") or "(no title)",
                "category": "allday" if is_all_day else "time",
                "isAllday": is_all_day,
                "start": start_iso,
                "end": end_iso,
                "backgroundColor": link.color or "#ef4444",
                "borderColor": link.color or "#ef4444",
                "body": ev.get("description") or "",
                "isReadOnly": True,
                "raw": {"linkId": link.id, "googleEventId": ev["id"], "htmlLink": ev.get("htmlLink", "")},
            })
        except Exception:
            continue
    return out


def dashboard_counts() -> dict[str, int]:
    with SessionLocal() as session:
        return {
            "orders": len(session.scalars(select(Order)).all()),
            "animals": len(session.scalars(select(AnimalRecord)).all()),
            "samples": len(session.scalars(select(SampleRecord)).all()),
            "events": len(session.scalars(select(CalendarEvent)).all()),
            "tasks": len(session.scalars(select(TaskItem)).all()),
            "notebook_entries": len(session.scalars(select(NotebookEntry)).all()),
            "mice": len(session.scalars(select(MouseRecord)).all()),
            "cages": len(session.scalars(select(CageRecord)).all()),
            "strains": len(session.scalars(select(StrainRecord)).all()),
        }


def upload_name(original: str) -> str:
    """A stored name nobody can guess and no two uploads share: before this,
    two files with the same name uploaded in the same second overwrote each
    other. The original name stays on the end so a download is recognisable."""
    return f"{datetime.utcnow():%Y%m%d%H%M%S}_{secrets.token_hex(8)}_{secure_filename(original) or 'file'}"


def save_uploaded_image(upload: FileStorage | None) -> str:
    if upload is None or not upload.filename:
        return ""

    output_name = upload_name(upload.filename)
    destination = Path(UPLOAD_DIR / output_name)
    upload.save(destination)
    return f"uploads/{output_name}"


def save_uploaded_file(upload: FileStorage | None) -> dict | None:
    """Save an arbitrary uploaded file (PDF, .docx, .xlsx, etc.) to the
    uploads dir. Returns {path, original_name, size_bytes} or None on failure."""
    if upload is None or not upload.filename:
        return None

    output_name = upload_name(upload.filename)
    destination = Path(UPLOAD_DIR / output_name)
    upload.save(destination)
    try:
        size_bytes = destination.stat().st_size
    except OSError:
        size_bytes = 0
    return {
        "path": f"uploads/{output_name}",
        "original_name": upload.filename,
        "size_bytes": size_bytes,
    }


def latest_mouse_record(session) -> MouseRecord | None:
    return session.scalar(select(MouseRecord).order_by(MouseRecord.created_at.desc(), MouseRecord.mouse_id.desc()).limit(1))


def next_mouse_id(session) -> int:
    """The next free mouse ID (see reserve_mouse_ids).

    Flushes first because the session is created with autoflush=False: a
    caller in a loop (CSV import, batch create) has rows pending that the
    max() would otherwise not see, and every row would be handed the same
    ID. Use reserve_mouse_ids() when creating several at once.
    """
    return reserve_mouse_ids(session, 1)[0]


MOUSE_ID_HIGH = "mouse_id_high"      # app_settings: the highest mouse ID ever handed out


def reserve_mouse_ids(session, count: int) -> list[int]:
    """Allocate `count` consecutive mouse IDs in one go, above the highest
    mouse there is and above any ever handed out: a number is never given
    twice, even after its mouse was deleted or an Add many undone. (Before,
    the newest mouse by date decided, so importing an old #5 made every
    New mouse ask for #6 again, and be refused.)"""
    if count <= 0:
        return []
    from .inventory_service import get_setting, set_setting
    session.flush()
    highest = session.scalar(select(func.max(MouseRecord.mouse_id))) or 0
    stored = get_setting(session, MOUSE_ID_HIGH, "")
    if stored.isdigit():
        highest = max(highest, int(stored))
    ids = [highest + offset for offset in range(1, count + 1)]
    set_setting(session, MOUSE_ID_HIGH, str(ids[-1]))
    return ids


def _cage_number(code: str | None) -> int:
    """The number a cage ID stands for: "12" -> 12, "2A" -> 2, "B-12" -> 12.
    Only the first run of digits counts, so "B12-3" is 12, not 123."""
    match = re.search(r"\d+", code or "")
    return int(match.group()) if match else 0


def reserve_cage_ids(session, count: int) -> list[str]:
    """`count` unused cage IDs above the highest numbered cage.

    Computed from every cage, not the most recently created one: a cage
    typed by hand with a low number ("2A") must not send the next "new"
    cage back into the existing cage 3."""
    if count <= 0:
        return []
    # See next_mouse_id: pending rows are invisible without a flush.
    session.flush()
    codes = set(session.scalars(select(CageRecord.cage_id)).all())
    highest = max((_cage_number(code) for code in codes), default=0)
    ids: list[str] = []
    candidate = highest
    while len(ids) < count:
        candidate += 1
        if str(candidate) not in codes:
            ids.append(str(candidate))
    return ids


def next_cage_id(session) -> str:
    return reserve_cage_ids(session, 1)[0]


def get_or_create_litter(session, litter_code: str, dob: date | None = None) -> LitterRecord:
    litter = session.scalar(select(LitterRecord).where(LitterRecord.litter_id == litter_code))
    if litter is None:
        litter = LitterRecord(litter_id=litter_code, date_of_birth=dob)
        session.add(litter)
        session.flush()
    elif dob and dob != litter.date_of_birth:
        # A litter's date of birth is the age of every mouse in it: only
        # someone who may edit all of them re-dates it (as on the Litters
        # tab). Anyone else's mouse just joins with the litter's date.
        from flask import flash, has_request_context
        from . import access
        if not has_request_context() or access.can_edit_litter(litter):
            litter.date_of_birth = dob
        else:
            flash(gettext("Litter %(litter)s keeps its date of birth (%(dob)s): it has mice you can't edit.",
                          litter=litter.litter_id, dob=litter.date_of_birth or gettext("not set")), "info")
    return litter


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
        racks = {r.name.lower(): r for r in session.scalars(select(MouseRack))}
        if not racks:
            return 0
        # Only the columns it uses: this runs before Alembic adds newer ones.
        for cage in session.scalars(select(CageRecord).options(load_only(
                CageRecord.id, CageRecord.cage_location, CageRecord.rack_id_fk, CageRecord.rack_row,
                CageRecord.rack_col)).where(CageRecord.rack_id_fk.is_(None))):
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


def mouse_racks(session) -> list:
    from .models import MouseRack
    return list(session.scalars(select(MouseRack).order_by(MouseRack.position, MouseRack.name)))


def get_or_create_cage(session, cage_code: str) -> CageRecord:
    resolved_code = cage_code.strip()
    if resolved_code.lower() == "new" or resolved_code == "":
        resolved_code = next_cage_id(session)
    cage = session.scalar(select(CageRecord).where(CageRecord.cage_id == resolved_code))
    if cage is None:
        # A cage made by typing its number belongs to whoever made it, like
        # one from New cage; without an owner anyone could change it.
        from flask import g, has_request_context
        owner = g.user.username if has_request_context() and g.get("user") is not None else ""
        cage = CageRecord(cage_id=resolved_code, owner=owner)
        session.add(cage)
        session.flush()
    return cage


def calculate_age_fields(dob: date | None) -> dict[str, int | None]:
    if dob is None:
        return {"age_days": None, "age_weeks": None}
    delta = (date.today() - dob).days
    return {"age_days": max(delta, 0), "age_weeks": max(delta, 0) // 7}


def normalize_status(raw_status: str) -> str:
    return raw_status.strip().lower()


def transgene_values_from_form(form) -> list[str]:
    return [form.get(f"transgene_{index}", "").strip() for index in range(1, 5)]


def genotype_string_from_transgenes(transgenes: list[str]) -> str:
    return "; ".join([value for value in transgenes if value])


def split_genotype(raw_value: str) -> list[str]:
    if not raw_value:
        return []
    normalized = raw_value.replace(",", ";")
    return [part.strip() for part in normalized.split(";") if part.strip()]


def sync_mouse_transgenes(mouse: MouseRecord, transgenes: list[str]) -> None:
    padded = transgenes + [""] * (4 - len(transgenes))
    mouse.transgene_1 = padded[0]
    mouse.transgene_2 = padded[1]
    mouse.transgene_3 = padded[2]
    mouse.transgene_4 = padded[3]
    mouse.genotype = genotype_string_from_transgenes(padded)


# Statuses that mean the mouse has died. Choosing one stamps today as the
# date of death (if none is set); moving a mouse back out of one (a sac
# entered by mistake, say) clears the date so the mouse is alive again.
END_STATUSES = {"sac", "dead", "found dead", "died", "euthanized", "euthanised"}


def is_end_status(status: str | None) -> bool:
    return normalize_status(status or "") in END_STATUSES


def apply_status_rules(mouse: MouseRecord, previous_status: str | None) -> None:
    if is_end_status(mouse.status):
        if mouse.date_of_death is None:
            mouse.date_of_death = date.today()
    elif is_end_status(previous_status):
        mouse.date_of_death = None


def mouse_is_active(mouse: MouseRecord) -> bool:
    inactive_statuses = END_STATUSES | {"transfer"}
    return mouse.date_of_death is None and normalize_status(mouse.status or "") not in inactive_statuses


def _cage_position(cage) -> str:
    from . import positions
    if cage is None or cage.rack is None:
        return ""
    return positions.label(cage.rack_row, cage.rack_col, cage.rack.naming, cage.rack.cols)


def mouse_display_row(mouse: MouseRecord, current_username: str | None = None, current_role: str | None = None) -> dict[str, object]:
    dob = mouse.litter.date_of_birth if mouse.litter else None
    ages = calculate_age_fields(dob)
    transgenes = [mouse.transgene_1, mouse.transgene_2, mouse.transgene_3, mouse.transgene_4]
    genotype_parts = [value for value in transgenes if value] or split_genotype(mouse.genotype)
    can_edit_breeder = current_role == "admin" or mouse.owner == current_username
    cage_is_breeder = mouse.cage is not None and is_breeder_purpose(mouse.cage.purpose)
    return {
        "id": mouse.id,
        "mouse_id": mouse.mouse_id,
        "active": mouse_is_active(mouse),
        "active_label": "Y" if mouse_is_active(mouse) else "N",
        "age_weeks": ages["age_weeks"],
        "age_days": ages["age_days"],
        "gender": mouse.gender,
        "genotype": mouse.genotype,
        "genotype_full": mouse.genotype or "",
        "genotype_parts": genotype_parts,
        "transgene_1": genotype_parts[0] if len(genotype_parts) > 0 else "",
        "transgene_2": genotype_parts[1] if len(genotype_parts) > 1 else "",
        "transgene_3": genotype_parts[2] if len(genotype_parts) > 2 else "",
        "transgene_4": genotype_parts[3] if len(genotype_parts) > 3 else "",
        "cage_id": mouse.cage.cage_id if mouse.cage else "",
        "cage_location": mouse.cage.cage_location if mouse.cage else "",
        "cage_rack_id": mouse.cage.rack_id_fk if mouse.cage else None,
        "cage_rack": mouse.cage.rack.name if mouse.cage and mouse.cage.rack else "",
        "cage_position": _cage_position(mouse.cage),
        "owner": mouse.owner,
        "litter_id": mouse.litter.litter_id if mouse.litter else "",
        "date_of_birth": dob.isoformat() if dob else "",
        "status": mouse.status,
        "note": mouse.note,
        "date_of_death": mouse.date_of_death.isoformat() if mouse.date_of_death else "",
        "can_edit": not cage_is_breeder or can_edit_breeder,
    }


# Cage purposes that make a cage a breeding cage: the Breeders tab lists
# them and cage cards offer birth / genotyping / weaning on them. Labs
# write it either way, so both spellings mean the same thing here. (A
# "breeder" cage starts out shared with the lab: app/access.py.)
BREEDER_PURPOSES = {"breeder", "breeding"}


def is_breeder_purpose(purpose: str | None) -> bool:
    return (purpose or "").strip().lower() in BREEDER_PURPOSES


def cage_is_active(cage: CageRecord) -> bool:
    return cage.active_override or any(mouse_is_active(mouse) for mouse in cage.mice)


# Weaning at P21 is the standard; the home dashboard, cage cards and the
# calendar all read this one constant, and genotyping (a week after
# weaning, P28) is one constant too: the cage, the calendar and Home must
# never disagree about when it is due.
WEAN_OFFSET_DAYS = 21
GENO_OFFSET_DAYS = 28          # the default; the lab may choose another day (genotyping_offset)
CAGE_GENO_OFFSET_DAYS = GENO_OFFSET_DAYS


def genotyping_offset(session=None) -> int:
    """The day after birth the lab genotypes (Lab setup; P28 unless
    chosen), read once per request."""
    from flask import g, has_request_context
    from . import lab
    if has_request_context() and "genotyping_day" in g:
        return g.genotyping_day
    if session is not None:
        value = lab.genotyping_day(session)
    else:
        from .db import SessionLocal
        with SessionLocal() as own:
            value = lab.genotyping_day(own)
    if has_request_context():
        g.genotyping_day = value
    return value


# Younger than this, weaning asks first: P21 is the usual day, and pups
# weaned before about P18 often do not survive without their mother.
MIN_WEAN_AGE_DAYS = 18


def weaning_due(session, start: date, end: date) -> list[dict]:
    """Every litter waiting to be weaned whose day (born + P21) falls
    between start and end, soonest first.

    The one source for Home, its layouts and the calendar, so they agree
    with each other and with the cage sheet. Two things wait to be weaned:
    a cage's "Litter born" date (the pups may not be entered as mice yet;
    weaning the cage clears it), and a litter with a date of birth that has
    not been weaned (weaned_on) and still has living mice or a pup count.
    A litter whose pups sit in such a cage counts once, as that cage.
    """
    from sqlalchemy.orm import selectinload

    first, last = start - timedelta(days=WEAN_OFFSET_DAYS), end - timedelta(days=WEAN_OFFSET_DAYS)
    items: list[dict] = []
    cages = session.scalars(
        select(CageRecord).options(selectinload(CageRecord.mice).selectinload(MouseRecord.litter),
                                   selectinload(CageRecord.rack))
        .where(CageRecord.date_give_birth.is_not(None),
               CageRecord.date_give_birth >= first, CageRecord.date_give_birth <= last)).all()
    counted: set[int] = set()
    for cage in cages:
        born = cage.date_give_birth
        litter = next((m.litter for m in cage.mice
                       if m.date_of_death is None and m.litter is not None and m.litter.date_of_birth == born), None)
        if litter is not None:
            if litter.weaned_on is not None:
                continue
            counted.add(litter.id)
        items.append({"born": born, "due": born + timedelta(days=WEAN_OFFSET_DAYS), "cage": cage,
                      "litter": litter, "pups": litter.total_pups if litter else 0})
    litters = session.scalars(
        select(LitterRecord).options(selectinload(LitterRecord.mice).selectinload(MouseRecord.cage)
                                     .selectinload(CageRecord.rack))
        .where(LitterRecord.weaned_on.is_(None), LitterRecord.date_of_birth.is_not(None),
               LitterRecord.date_of_birth >= first, LitterRecord.date_of_birth <= last)).all()
    for litter in litters:
        if litter.id in counted:
            continue
        living = [m for m in litter.mice if m.date_of_death is None]
        if litter.mice and not living:
            continue
        cage = next((m.cage for m in living if m.cage is not None), None)
        items.append({"born": litter.date_of_birth, "due": litter.date_of_birth + timedelta(days=WEAN_OFFSET_DAYS),
                      "cage": cage, "litter": litter, "pups": litter.total_pups or len(living)})
    items.sort(key=lambda item: (item["due"], item["litter"].litter_id if item["litter"] else ""))
    return items


def weaning_title(item: dict) -> str:
    """"Litter L-12 · cage 12", or "Cage 12" before the pups are entered
    (in the page's language)."""
    litter, cage = item["litter"], item["cage"]
    if litter is not None and cage is not None:
        return gettext("Litter %(litter)s · cage %(cage)s", litter=litter.litter_id, cage=cage.cage_id)
    if litter is not None:
        return gettext("Litter %(litter)s", litter=litter.litter_id)
    if cage is not None:
        return gettext("Cage %(cage)s", cage=cage.cage_id)
    return gettext("A litter")


def mark_weaned(cage: CageRecord, when: date | None = None) -> list[LitterRecord]:
    """Weaning a cage: its "Litter born" date is cleared and the litters
    of its pups are marked weaned. The pups are the living mice born on
    that date, or, with no date, the youngest litter still under P28."""
    when = when or date.today()
    born = cage.date_give_birth
    litters = {m.litter.id: m.litter for m in cage.mice
               if m.date_of_death is None and m.litter is not None and m.litter.date_of_birth
               and m.litter.weaned_on is None
               and (m.litter.date_of_birth == born if born else 0 <= (when - m.litter.date_of_birth).days <= 28)}
    if not born and litters:
        youngest = max(litters.values(), key=lambda lit: lit.date_of_birth)
        litters = {youngest.id: youngest}
    for litter in litters.values():
        litter.weaned_on = when
    cage.date_give_birth = None
    return list(litters.values())


def cage_derived_dates(cage: CageRecord) -> dict[str, str]:
    if cage.date_give_birth is None:
        return {"genotyping_date": "", "weaning_date": ""}
    return {
        "genotyping_date": (cage.date_give_birth + timedelta(days=genotyping_offset())).isoformat(),
        "weaning_date": (cage.date_give_birth + timedelta(days=WEAN_OFFSET_DAYS)).isoformat(),
    }


def dropdown_options_map(session) -> dict[str, list[str]]:
    options = defaultdict(list)
    rows = session.scalars(select(DropdownOption).order_by(DropdownOption.field_name, DropdownOption.option_value)).all()
    for row in rows:
        options[row.field_name].append(row.option_value)
    for field in MOUSE_PRESET_FIELDS:
        options.setdefault(field, [])
    return dict(options)


def dropdown_records_map(session) -> dict[str, list[DropdownOption]]:
    grouped: dict[str, list[DropdownOption]] = {field: [] for field in MOUSE_PRESET_FIELDS}
    rows = session.scalars(select(DropdownOption).order_by(DropdownOption.field_name, DropdownOption.option_value)).all()
    for row in rows:
        grouped.setdefault(row.field_name, []).append(row)
    return grouped


def current_lab_usernames(session) -> list[str]:
    return [user.username for user in session.scalars(select(UserAccount).order_by(UserAccount.username)).all()]


def add_notification(session, recipient_username: str, title: str, message: str, category: str = "general",
                     link: str = "", actor: str = "", values: dict | None = None,
                     message_values: dict | None = None) -> None:
    """Insert a notification, respecting the recipient's per-category
    preferences (notify_<category> on UserAccount). See app/notify.py, which
    also sends most notifications by itself when records change, and writes
    each in its recipient's language (`values`, `message_values`: see
    notify.send)."""
    from . import notify
    notify.send(session, recipient_username, title, message, category=category, link=link, actor=actor,
                values=values, message_values=message_values)


def recent_notifications(session, recipient_username: str, limit: int = 8) -> list[NotificationRecord]:
    return session.scalars(
        select(NotificationRecord)
        .where(NotificationRecord.recipient_username == recipient_username)
        .order_by(NotificationRecord.created_at.desc())
        .limit(limit)
    ).all()


def breeder_summary(session) -> list[dict[str, object]]:
    today = date.today()
    breeder_buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"f_young": 0, "f_old": 0, "m_young": 0, "m_old": 0, "oldest": 0, "total": 0}
    )

    from sqlalchemy.orm import selectinload
    # Living mice in breeder cages, with their litters, in two queries.
    living_in_breeders = (select(MouseRecord).join(CageRecord, MouseRecord.cage_id_fk == CageRecord.id)
                          .where(MouseRecord.date_of_death.is_(None),
                                 func.lower(func.trim(CageRecord.purpose)).in_(BREEDER_PURPOSES))
                          .options(selectinload(MouseRecord.litter), selectinload(MouseRecord.cage)))
    for mouse in session.scalars(living_in_breeders).all():
        if not mouse_is_active(mouse):
            continue
        if mouse.cage is None or not is_breeder_purpose(mouse.cage.purpose):
            continue
        dob = mouse.litter.date_of_birth if mouse.litter else None
        if dob is None:
            continue
        age_weeks = max((today - dob).days, 0) // 7
        genotype = (mouse.genotype or "").strip() or "(no genotype)"
        gender = (mouse.gender or "").strip().upper()

        bucket = breeder_buckets[genotype]
        bucket["total"] += 1
        if age_weeks > bucket["oldest"]:
            bucket["oldest"] = age_weeks
        if 8 <= age_weeks <= 20 and gender == "F":
            bucket["f_young"] += 1
        elif 20 < age_weeks <= 30 and gender == "F":
            bucket["f_old"] += 1
        elif 8 <= age_weeks <= 20 and gender == "M":
            bucket["m_young"] += 1
        elif 20 < age_weeks <= 30 and gender == "M":
            bucket["m_old"] += 1

    rows: list[dict[str, object]] = []
    for genotype in sorted(breeder_buckets.keys()):
        bucket = breeder_buckets[genotype]
        oldest = bucket["oldest"]
        if oldest <= 20:
            urgency = "ok"
        elif oldest <= 30:
            urgency = "warn"
        else:
            urgency = "urgent"
        rows.append(
            {
                "genotype": genotype,
                "f_young": bucket["f_young"],
                "f_old": bucket["f_old"],
                "m_young": bucket["m_young"],
                "m_old": bucket["m_old"],
                "total": bucket["total"],
                "oldest_age_weeks": oldest,
                "urgency": urgency,
            }
        )
    return rows


def breeder_mice(session, current_username: str | None, current_role: str | None) -> list[dict[str, object]]:
    from sqlalchemy.orm import selectinload
    cages = session.scalars(
        select(CageRecord).where(func.lower(func.trim(CageRecord.purpose)).in_(BREEDER_PURPOSES))
        .options(selectinload(CageRecord.mice).selectinload(MouseRecord.litter), selectinload(CageRecord.rack))
        .order_by(CageRecord.cage_id)
    ).all()
    grouped: list[dict[str, object]] = []
    for cage in cages:
        owner_name = ""
        for mouse in cage.mice:
            if mouse.owner:
                owner_name = mouse.owner
                break
        sorted_mice = sorted(cage.mice, key=lambda item: item.mouse_id)
        mouse_rows = [mouse_display_row(mouse, current_username, current_role) for mouse in sorted_mice]
        search_parts: list[str] = [
            cage.cage_id or "",
            cage.cage_location or "",
            cage.room or "",
            cage.location_detail or "",
            cage.card_id or "",
            cage.genotype_summary or "",
            owner_name,
        ]
        for mouse in sorted_mice:
            search_parts.extend(
                [
                    str(mouse.mouse_id),
                    mouse.gender or "",
                    mouse.genotype or "",
                    mouse.note or "",
                    mouse.owner or "",
                    mouse.status or "",
                ]
            )
            if mouse.litter and mouse.litter.litter_id:
                search_parts.append(mouse.litter.litter_id)
        rack = cage.rack.name if cage.rack else ""
        position = _cage_position(cage)
        search_parts += [rack, position]
        search_blob = " ".join(part for part in search_parts if part).lower()
        active_count = sum(1 for mouse in sorted_mice if mouse_is_active(mouse))
        living = [mouse for mouse in sorted_mice if mouse_is_active(mouse)]
        females = sum(1 for mouse in living if mouse.gender == "F")
        males = sum(1 for mouse in living if mouse.gender == "M")
        grouped.append(
            {
                "cage_id": cage.cage_id,
                "id": cage.id,
                "rack": rack,
                "position": position,
                "females": females,
                "males": males,
                "cage_location": cage.cage_location,
                "room": cage.room,
                "location_detail": cage.location_detail,
                "card_id": cage.card_id,
                "genotype_summary": cage.genotype_summary,
                "owner": owner_name or "Unassigned",
                "search_blob": search_blob,
                "mice": mouse_rows,
                "active_count": active_count,
                "total_count": len(sorted_mice),
                "editable": current_role == "admin" or owner_name == current_username,
            }
        )
    return grouped


def next_litter_id(session) -> str:
    """The next litter ID: L-1, L-2, L-3… one past the highest L-number the
    lab has (litters named another way, imported ones, don't count), and
    never one that is taken."""
    session.flush()
    codes = {(code or "").strip() for code in session.scalars(select(LitterRecord.litter_id))}
    numbers = [int(m.group(1)) for m in (re.fullmatch(r"[Ll]-(\d+)", code) for code in codes) if m]
    n = max(numbers, default=0) + 1
    while f"L-{n}" in codes:
        n += 1
    return f"L-{n}"


def generate_litter_id(session, cage: CageRecord | None) -> str:
    return next_litter_id(session)


_PLAIN_NUMBER = re.compile(r"^-?\d+(?:[.,]\d+)?$")
XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sheet_safe(value):
    """A cell a spreadsheet shows as text rather than runs: someone's note
    that starts with = + - @ (=HYPERLINK(…), say) would otherwise be a
    formula when the export is opened in Excel. A plain number stays one."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") and not _PLAIN_NUMBER.match(value):
        return "'" + value
    return value


def csv_text(rows) -> str:
    """Rows as CSV that Excel opens as UTF-8 (a byte-order mark) with no
    cell taken for a formula (sheet_safe)."""
    output = io.StringIO()
    writer = csv.writer(output)
    for row in rows:
        writer.writerow([sheet_safe(v) for v in row])
    return "\ufeff" + output.getvalue()


def xlsx_bytes(sheets) -> bytes:
    """[(title, rows)] as an .xlsx workbook, header rows bold, every text
    cell kept as text (never a formula)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    book = Workbook()
    for i, (title, rows) in enumerate(sheets):
        sheet = book.active if i == 0 else book.create_sheet()
        sheet.title = title[:31]
        for row in rows:
            sheet.append(row)
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def export_mouse_rows(mouse_rows: list[dict[str, object]], export_format: str) -> tuple[str | bytes, str, str]:
    """The Mice sheet as CSV, or as an Excel workbook (.xlsx) that Import
    from Excel reads back."""
    table = [
        [
            "Mouse_ID",
            "Active",
            "Age_weeks",
            "Age_days",
            "Gender",
            "Transgene_1",
            "Transgene_2",
            "Transgene_3",
            "Transgene_4",
            "Cage_ID",
            "Rack",
            "Position",
            "Cage_Location",
            "Owner",
            "Litter_ID",
            "DOB",
            "Status",
            "Date_of_Death",
            "Note",
        ]
    ]
    for row in mouse_rows:
        table.append(
            [
                row["mouse_id"],
                "Yes" if row["active"] else "No",
                "" if row["age_weeks"] is None else row["age_weeks"],   # a mouse born today is 0, not blank
                "" if row["age_days"] is None else row["age_days"],
                row["gender"],
                row["transgene_1"],
                row["transgene_2"],
                row["transgene_3"],
                row["transgene_4"],
                row["cage_id"],
                row.get("cage_rack", ""),
                row.get("cage_position", ""),
                row["cage_location"],
                row["owner"],
                row["litter_id"],
                row["date_of_birth"],
                row["status"],
                row["date_of_death"],
                row["note"],
            ]
        )
    if export_format == "csv":
        return csv_text(table), "mice_export.csv", "text/csv"
    return xlsx_bytes([("Mice", table)]), "mice_export.xlsx", XLSX_MIMETYPE


# ---------------------------------------------------------------------------
# Auto-derived calendar items
# ---------------------------------------------------------------------------
# These come from EXISTING domain rows (litters, mice, experiments) rather
# than from CalendarEvent / TaskItem. They're read-only on the calendar —
# the source of truth is the originating record, so editing happens there.
# The output shape matches what TOAST UI Calendar expects, with `isReadOnly`
# set so drag-to-reschedule is blocked.

# Defaults reflect typical mouse colony practice:
# - weaning at P21 (3 weeks)
# - genotyping ~a week after weaning (P28)
# - "old mouse" sac-threshold reminder at 30 weeks
# WEAN_OFFSET_DAYS and GENO_OFFSET_DAYS are defined with the cage dates
# above (one rule each for the home dashboard, cage cards and the calendar).
SAC_THRESHOLD_WEEKS = 30


def _parent_label(session, info: str) -> str:
    """Translate a litter's father_info / mother_info field into a label
    that includes the parent mouse's genotype. The field often holds just a
    mouse_id (e.g. "3") or text containing one (e.g. "M3 ♂"); we extract
    the first integer, look up the mouse, and append its genotype/
    transgenes. Falls back to the raw value when nothing matches."""
    import re as _re

    info = (info or "").strip()
    if not info:
        return ""
    match = _re.search(r"\d+", info)
    if not match:
        return info
    mouse_id_int = int(match.group(0))
    mouse = session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == mouse_id_int))
    if mouse is None:
        return info
    # Prefer the rolled-up `genotype` field if set; otherwise stitch the
    # individual transgene slots so we always have something useful.
    geno = (mouse.genotype or "").strip()
    if not geno:
        parts = [p.strip() for p in (mouse.transgene_1, mouse.transgene_2, mouse.transgene_3, mouse.transgene_4) if p and p.strip()]
        geno = "; ".join(parts)
    return f"M{mouse_id_int} ({geno})" if geno else f"M{mouse_id_int}"


def _auto_item(*, kind_tag: str, anchor_id: int, title: str, color: str, day: date,
               body: str = "", source: str = "", href: str = "") -> dict:
    """Helper to build one auto-derived TOAST UI schedule dict."""
    start_iso = datetime.combine(day, datetime.min.time()).isoformat()
    end_iso = datetime.combine(day, datetime.max.time()).isoformat()
    return {
        "id": f"auto-{source}-{anchor_id}-{kind_tag}",
        "kind": "auto",
        "calendarId": "auto",
        "title": title,
        "category": "allday",
        "isAllday": True,
        "start": start_iso,
        "end": end_iso,
        "backgroundColor": color,
        "borderColor": color,
        "body": body,
        "isReadOnly": True,
        "raw": {"source": source, "anchor_id": anchor_id, "href": href},
    }


def derive_auto_calendar_items(session, start_dt=None, end_dt=None) -> list[dict]:
    """Walk the litters / mice / experiments tables and emit a list of
    auto-derived calendar items in the requested window. Each row keeps a
    link back to its source via raw.href so clicking the item can navigate
    to the originating record."""
    items: list[dict] = []

    def _in_window(d: date) -> bool:
        if start_dt and d < (start_dt.date() if hasattr(start_dt, "date") else start_dt):
            return False
        if end_dt and d > (end_dt.date() if hasattr(end_dt, "date") else end_dt):
            return False
        return True

    # ----- Cage wean + genotype ----------------------------------------
    # Driven by CageRecord.date_give_birth (the cage's most recent litter
    # date). For each one we derive a wean event at +21d and a genotype
    # event at +28d. Title uses the cage's location, and the body includes
    # the father + mother genotypes pulled from the linked litter when
    # available.
    cages = session.scalars(select(CageRecord).where(CageRecord.date_give_birth.is_not(None))).all()
    for cage in cages:
        dob = cage.date_give_birth
        if dob is None:
            continue

        location_label = ((cage.cage_location or cage.cage_id or "").strip()
                          or gettext("Cage %(cage)s", cage=cage.id))

        # Try to find the litter belonging to this cage: same DOB AND at
        # least one mouse in this cage. Fall back gracefully if missing.
        litter = session.scalar(
            select(LitterRecord)
            .where(LitterRecord.date_of_birth == dob)
            .where(LitterRecord.mice.any(MouseRecord.cage_id_fk == cage.id))
            .limit(1)
        )

        # Resolve father_info / mother_info → "M<id> (genotype)" by looking
        # up the parent mouse. This is what the user actually wants to see
        # on the wean / geno reminder, not just the raw ID.
        father = _parent_label(session, litter.father_info) if litter else ""
        mother = _parent_label(session, litter.mother_info) if litter else ""
        cage_geno = (cage.genotype_summary or "").strip()
        # Compose the notes line. Always include DOB; include parent geno
        # info when present, otherwise fall back to the cage's own summary.
        body_parts = [gettext("DOB %(date)s", date=dob.isoformat())]
        if father:
            body_parts.append(gettext("Father: %(mouse)s", mouse=father))
        if mother:
            body_parts.append(gettext("Mother: %(mouse)s", mouse=mother))
        if cage_geno and not (father or mother):
            body_parts.append(gettext("Genotype: %(genotype)s", genotype=cage_geno))
        body = " · ".join(body_parts)

        # Weaning comes from weaning_due below, the same list Home shows.
        geno = dob + timedelta(days=genotyping_offset(session))
        if _in_window(geno):
            items.append(_auto_item(
                kind_tag="geno", anchor_id=cage.id,
                title=gettext("Genotype - %(where)s", where=location_label),
                color="#fcb77e",  # apricot
                day=geno, body=body, source="cage",
                href=f"/colony?view=cages&scope=all&q={quote(str(cage.cage_id))}",
            ))

    # ----- Weaning at P21: cages with a litter-born date, and litters not
    # yet weaned (weaning_due, shared with Home) -------------------------
    def _day(value, default: date) -> date:
        if not value:
            return default
        return value.date() if isinstance(value, datetime) else value

    lo = _day(start_dt, date.today() - timedelta(days=365))
    hi = _day(end_dt, date.today() + timedelta(days=365))
    for wean in weaning_due(session, lo, hi):
        cage, litter = wean["cage"], wean["litter"]
        body_parts = [gettext("DOB %(date)s", date=wean["born"].isoformat())]
        if litter is not None:
            father = _parent_label(session, litter.father_info)
            mother = _parent_label(session, litter.mother_info)
            body_parts += [gettext("Father: %(mouse)s", mouse=father)] if father else []
            body_parts += [gettext("Mother: %(mouse)s", mouse=mother)] if mother else []
        if cage is not None and (cage.genotype_summary or "").strip() and len(body_parts) == 1:
            body_parts.append(gettext("Genotype: %(genotype)s", genotype=cage.genotype_summary.strip()))
        where = ""
        if cage is not None:
            where = (cage.cage_location or "").strip()
        items.append(_auto_item(
            kind_tag="wean", anchor_id=(cage.id if cage is not None else litter.id),
            title=(gettext("Wean - %(what)s (%(where)s)", what=weaning_title(wean), where=where) if where
                   else gettext("Wean - %(what)s", what=weaning_title(wean))),
            color="#b6e2a1",  # pistachio
            day=wean["due"], body=" · ".join(body_parts), source="cage" if cage is not None else "litter",
            href=(f"/colony?view=cages&scope=all#cage-{cage.id}" if cage is not None else "/colony?view=litters"),
        ))

    # ----- Old-mouse sac threshold (>= 30 weeks via litter DOB) ----------
    # Mice link to litters; we walk mice whose litter has a DOB. We skip
    # any mouse that's already dead.
    threshold_days = SAC_THRESHOLD_WEEKS * 7
    mice = session.scalars(
        select(MouseRecord)
        .join(LitterRecord, MouseRecord.litter_id_fk == LitterRecord.id)
        .where(LitterRecord.date_of_birth.is_not(None))
        .where(MouseRecord.date_of_death.is_(None))
    ).all()
    for m in mice:
        dob = m.litter.date_of_birth if m.litter else None
        if dob is None:
            continue
        threshold_day = dob + timedelta(days=threshold_days)
        if not _in_window(threshold_day):
            continue
        items.append(_auto_item(
            kind_tag="sac", anchor_id=m.id,
            title=gettext("Sac reminder · M%(mouse)s", mouse=m.mouse_id),
            color="#f9a8a8",  # rose — gentle warning
            day=threshold_day,
            body=gettext("%(weeks)s weeks since litter DOB (%(date)s)", weeks=SAC_THRESHOLD_WEEKS, date=dob.isoformat()),
            source="mouse",
            href=f"/colony?view=mice&scope=all&q={m.mouse_id}",
        ))

    # ----- Experiment start + end --------------------------------------
    experiments = session.scalars(
        select(Experiment).where(
            (Experiment.start_date.is_not(None)) | (Experiment.end_date.is_not(None))
        )
    ).all()
    for ex in experiments:
        if ex.start_date and _in_window(ex.start_date):
            items.append(_auto_item(
                kind_tag="start", anchor_id=ex.id,
                title=gettext("Exp start · %(name)s", name=ex.name),
                color="#c4b5fd",  # lavender
                day=ex.start_date,
                body=(ex.description or "")[:200],
                source="experiment",
                href=f"/colony/experiments/{ex.id}",
            ))
        if ex.end_date and _in_window(ex.end_date):
            items.append(_auto_item(
                kind_tag="end", anchor_id=ex.id,
                title=gettext("Exp end · %(name)s", name=ex.name),
                color="#a4c8f0",  # sky
                day=ex.end_date,
                body=(ex.description or "")[:200],
                source="experiment",
                href=f"/colony/experiments/{ex.id}",
            ))

    # ----- Zebrafish: clutches → tank-up / fin-clip / adult -------------
    clutches = session.scalars(
        select(ClutchRecord).where(ClutchRecord.date_of_fertilization.is_not(None))
    ).all()
    for c in clutches:
        dof = c.date_of_fertilization
        if dof is None:
            continue
        label = c.clutch_id or f"C{c.id}"
        line_name = c.line.name if c.line else ""
        body_parts = [gettext("DOF %(date)s", date=dof.isoformat())]
        if line_name:
            body_parts.append(gettext("Line: %(line)s", line=line_name))
        if c.embryo_count:
            body_parts.append(gettext("Embryos: %(n)s", n=c.embryo_count))
        body = " · ".join(body_parts)

        milestones = [
            ("tank-up", 5,  "#a3e0d8", gettext("Tank up · %(clutch)s", clutch=label)),
            ("fin-clip", 30, "#fcb77e", gettext("Fin clip · %(clutch)s", clutch=label)),
            ("adult", 90, "#c4b5fd", gettext("Adult · %(clutch)s", clutch=label)),
        ]
        for tag, offset, color, title in milestones:
            day = dof + timedelta(days=offset)
            if _in_window(day):
                items.append(_auto_item(
                    kind_tag=tag, anchor_id=c.id,
                    title=title,
                    color=color, day=day, body=body,
                    source="clutch",
                    href=f"/zebrafish?view=clutches#clutch-{c.id}",
                ))

    # ----- Zebrafish: mating return reminders ---------------------------
    mating_tanks = session.scalars(
        select(TankRecord)
        .where(TankRecord.purpose == "mating")
        .where(TankRecord.mating_return_at.is_not(None))
    ).all()
    for t in mating_tanks:
        d = t.mating_return_at
        if d is None or not _in_window(d):
            continue
        items.append(_auto_item(
            kind_tag="mating-return", anchor_id=t.id,
            title=gettext("Return mating · %(tank)s", tank=t.tank_id),
            color="#f4b8d8",  # pink
            day=d,
            body=(t.notes or "")[:200],
            source="mating",
            href=f"/zebrafish?view=tanks#tank-{t.id}",
        ))

    return items


def rebuild_samples_table() -> None:
    """Bring an older `samples` table up to the current model.

    Older databases have animal_id_fk NOT NULL and no source/owner columns.
    SQLite cannot relax NOT NULL in place, so the table is rebuilt: renamed
    aside, recreated from the model, rows copied across, old copy dropped,
    all in one transaction. Idempotent; a no-op once the table is current.
    """
    inspector = inspect(engine)
    if "samples" not in inspector.get_table_names():
        return
    columns = {col["name"]: col for col in inspector.get_columns("samples")}
    wanted = set(SampleRecord.__table__.columns.keys())
    if not columns["animal_id_fk"]["nullable"] or not wanted <= set(columns):
        if engine.dialect.name != "sqlite":
            with engine.begin() as conn:
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
            conn.execute(text("DROP TABLE samples_old"))
            # Rows from the old animal link keep a readable source.
            if "animals" in inspector.get_table_names():
                conn.execute(text(
                    "UPDATE samples SET source_kind = 'other', source_ref = "
                    "(SELECT animal_id FROM animals WHERE animals.id = samples.animal_id_fk) "
                    "WHERE animal_id_fk IS NOT NULL AND source_kind = ''"))


def sample_sources(session) -> list[dict]:
    """Where a sample can come from: each colony, with the identifiers it
    uses, for the source picker. Order follows the sidebar."""
    from . import organism_service as organisms
    from .models import Organism, OrgHousing, OrgLine

    sources = [{
        "kind": "mouse", "label": "Mouse",
        "refs": [str(m) for m in session.scalars(select(MouseRecord.mouse_id).order_by(MouseRecord.mouse_id))],
    }]
    fish_refs = list(session.scalars(select(TankRecord.tank_id).order_by(TankRecord.tank_id)))
    fish_refs += [i for i in session.scalars(select(FishRecord.individual_id).order_by(FishRecord.individual_id)) if i]
    sources.append({"kind": "fish", "label": "Fish", "refs": fish_refs})
    for module in organisms.list_modules(session):
        refs = []
        for model in (OrgLine, OrgHousing, Organism):
            refs += [c for c in session.scalars(
                select(model.code).where(model.module_id_fk == module.id).order_by(model.code)) if c]
        sources.append({"kind": f"organism:{module.key}", "label": module.label, "refs": refs})
    sources.append({"kind": "other", "label": "Other", "refs": []})
    return sources


def sample_source_label(kind: str, sources: list[dict]) -> str:
    for source in sources:
        if source["kind"] == kind:
            return source["label"]
    return kind.split(":", 1)[-1].replace("_", " ").title() if kind else ""


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
        changed = 0
        for cage in cages:
            owners = Counter(
                (mouse.owner or "").strip()
                for mouse in cage.mice
                if (mouse.owner or "").strip()
            )
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
