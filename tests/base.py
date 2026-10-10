"""Shared set-up for the BioManager test suite.

Importing this module (every test module does, first) points the app at a
fresh, empty SQLite database in a temporary folder, then imports the app
once for the whole process. The lab's real databases in data/ are never
opened.

To run the same suite on PostgreSQL, name an empty database it may wipe:

    BIOMANAGER_TEST_DATABASE_URL=postgresql://localhost/biomanager_test scripts/test.sh

Its public schema is dropped and recreated first, so never point this at
a database whose data you want.

Isolation: all test modules share that one database, so no test may rely
on another test having run, or on a table being empty. Each TestCase makes
its own uniquely named users and records (see `uniq`) and looks rows up by
the names it gave them. Factories go through the real HTTP routes, so
permissions and validation are exercised too.
"""
from __future__ import annotations

import atexit
import html as html_lib
import itertools
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse

_TMP = tempfile.mkdtemp(prefix="biomanager-tests-")
DB_PATH = os.path.join(_TMP, "test.db")
POSTGRES_URL = os.environ.get("BIOMANAGER_TEST_DATABASE_URL", "").strip()
if POSTGRES_URL:
    import psycopg

    with psycopg.connect(POSTGRES_URL.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as _pg:
        _pg.execute("DROP SCHEMA public CASCADE")
        _pg.execute("CREATE SCHEMA public")
    os.environ["DATABASE_URL"] = POSTGRES_URL
else:
    os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["BIOMANAGER_DATA_DIR"] = _TMP
# The suite expects every default database to exist, as installations did
# before the setup survey made new ones start empty (app/lab.py).
os.environ["BIOMANAGER_SEED_DEFAULTS"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
atexit.register(shutil.rmtree, _TMP, True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.app import app  # noqa: E402  (runs init_database on the temp file)
from app import services  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import UserAccount  # noqa: E402

# Uploaded files go to the temp folder, not app/static/uploads.
services.UPLOAD_DIR = __import__("pathlib").Path(_TMP) / "uploads"
services.UPLOAD_DIR.mkdir(exist_ok=True)

# A view that raises should fail the test with its traceback, not a bare 500.
app.config["TESTING"] = True

ON_POSTGRES = engine.dialect.name == "postgresql"

# The suite's lab lets members add databases for everyone (app/lab.py), as
# the app always did before lab setup: most tests have a member create a
# database that others then use. tests/test_lab.py covers the stricter
# default, where only admins add lab databases.
with SessionLocal() as _s:
    from app.inventory_service import set_setting as _set_setting
    _set_setting(_s, "members_share_databases", "on")
    _s.commit()
only_sqlite = unittest.skipIf(ON_POSTGRES, "tests SQLite-only machinery")

def _wait_out_midnight() -> None:
    """Tests fix "today" once, here (TODAY, T, days_ago), while the app asks
    the clock every time it stamps a date. A run that crosses midnight would
    compare two different days and fail in dozens of places, so a run that
    starts close to midnight waits for it to pass. Runs take a few minutes."""
    import time
    margin = float(os.environ.get("BIOMANAGER_TEST_MIDNIGHT_MARGIN_MIN", "15")) * 60
    now = datetime.now()
    left = (datetime.combine(now.date() + timedelta(days=1), datetime.min.time()) - now).total_seconds()
    if left < margin:
        print(f"tests: {left / 60:.1f} min to midnight; waiting for it to pass so the date "
              "cannot change during the run", file=sys.stderr)
        time.sleep(left + 5)


_wait_out_midnight()
TODAY = date.today()
T = TODAY.isoformat()
AUTOSAVE = {"X-Autosave": "1"}
GRID_NAMING = {"naming_mode": "grid", "naming_rows": "letters", "naming_cols": "numbers",
               "naming_order": "row_col", "naming_separator": "", "naming_start": "1"}
SEQUENTIAL_NAMING = {**GRID_NAMING, "naming_mode": "sequential"}


def iso(d) -> str:
    return d.isoformat()


def days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def days_ahead(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


# ---------------------------------------------------------------- database

def _driver_sql(sql: str) -> str:
    """Tests write `?` placeholders; psycopg wants `%s` (and `%%` for a
    literal percent sign)."""
    return sql.replace("%", "%%").replace("?", "%s") if ON_POSTGRES else sql


def _as_stored_by_sqlite(value):
    """Tests compare against what SQLite hands back — dates as ISO text,
    booleans as 0/1 — so PostgreSQL's typed values are put in that shape."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")  # SQLAlchemy's SQLite format
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def rows(sql: str, *args) -> list[tuple]:
    """Run a read query against the test database (a new transaction, so
    it always sees what the app committed)."""
    with engine.connect() as con:
        found = con.exec_driver_sql(_driver_sql(sql), tuple(args)).fetchall()
    if not ON_POSTGRES:
        return [tuple(r) for r in found]
    return [tuple(_as_stored_by_sqlite(v) for v in r) for r in found]


def row(sql: str, *args):
    """The first row, or None."""
    found = rows(sql, *args)
    return found[0] if found else None


def one(sql: str, *args):
    """The first column of the first row, or None."""
    found = row(sql, *args)
    return found[0] if found else None


def execute(sql: str, *args) -> None:
    """Write straight to the database — only to set up a state the UI can
    no longer produce (legacy data, a migration's input)."""
    with engine.begin() as con:
        con.exec_driver_sql(_driver_sql(sql), tuple(args))


def count(table: str, where: str = "1=1", *args) -> int:
    return one(f'select count(*) from "{table}" where {where}', *args)


def last_batch():
    """(id, description, action, table) of the newest batch."""
    return row("select id, description, action, target_table from batches order by id desc limit 1")


def batch_of(table: str, record_id: int, action: str | None = None):
    """The batch id the newest audit entry for this record belongs to."""
    sql = "select batch_id_fk from audit_log where table_name=? and record_id=?"
    args = [table, record_id]
    if action:
        sql += " and action=?"
        args.append(action)
    return one(sql + " order by id desc limit 1", *args)


# ---------------------------------------------------------------- names/users

_counter = itertools.count(1)


def uniq(prefix: str = "t") -> str:
    """A name no other test uses."""
    return f"{prefix}{os.getpid() % 1000}x{next(_counter)}"


def make_user(username: str | None = None, role: str = "member") -> str:
    """Create a user (straight in the database: signing up is not what is
    under test) and return the username."""
    username = username or uniq("user")
    with SessionLocal() as s:
        s.add(UserAccount(username=username, password_hash="x", role=role))
        s.commit()
    return username


def user_id(username: str) -> int:
    return one("select id from users where username=?", username)


def client_for(username: str):
    """A Flask test client logged in as `username`."""
    from app import security
    c = app.test_client()
    with SessionLocal() as s, app.app_context():
        user = s.get(UserAccount, user_id(username))
        stamp = security.session_stamp(user)
    with c.session_transaction() as sess:
        sess["user_id"] = user.id
        sess["auth"] = stamp
    return c


# ---------------------------------------------------------------- responses

_FLASH = re.compile(r'flash-message flash-(\w+)"[^>]*>(.*?)</div>', re.S)


def flashes(response_or_html) -> list[tuple[str, str]]:
    """[(category, text)] of the flash messages on a rendered page."""
    text = response_or_html if isinstance(response_or_html, str) else response_or_html.get_data(as_text=True)
    return [(kind, re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", body))).strip())
            for kind, body in _FLASH.findall(text)]


def flash_text(response) -> str:
    return " | ".join(text for _, text in flashes(response))


def errors(response) -> list[str]:
    return [text for kind, text in flashes(response) if kind == "error"]


def location(response) -> str:
    """The path (and query) a redirect points to."""
    parsed = urlparse(response.headers.get("Location", ""))
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


# ---------------------------------------------------------------- test case

class AppTestCase(unittest.TestCase):
    """Gives every TestCase an admin and a member of its own, with clients.

    self.admin / self.member are usernames; self.a / self.m are their
    logged-in clients. Make more with make_user + client_for."""

    maxDiff = None

    def setUp(self):
        super().setUp()
        # Rate limits count across the whole run otherwise (one address, one process).
        from app import security
        security.signup_throttle.reset()
        security.password_check_throttle.reset()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = make_user(uniq("admin"), role="admin")
        cls.member = make_user(uniq("member"))
        cls.other = make_user(uniq("other"))
        cls.a = client_for(cls.admin)
        cls.m = client_for(cls.member)
        cls.o = client_for(cls.other)

    # --- request helpers
    @staticmethod
    def post(client, url, data=None, **kwargs):
        """POST and follow the redirect, so flashes are on the page."""
        return client.post(url, data=data or {}, follow_redirects=True, **kwargs)

    @staticmethod
    def autosave(client, url, data=None):
        """A background sheet save: answers JSON, 200 ok or 409 refused."""
        return client.post(url, data=data or {}, headers=AUTOSAVE)

    def get_ok(self, client, url) -> str:
        r = client.get(url)
        self.assertEqual(r.status_code, 200, url)
        return r.get_data(as_text=True)

    def assertFlash(self, response, fragment, kind=None):
        found = flashes(response)
        self.assertTrue(any(fragment in text and (kind is None or k == kind) for k, text in found),
                        f"no {kind or ''} flash containing {fragment!r}; got {found}")

    def assertGoesTo(self, response, url):
        """A form's answer that sends the window to another address: a page
        that goes there by itself (devices.open_in_window), since the CSP's
        form-action refuses a redirect there."""
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(f'<meta http-equiv="refresh" content="0;url={url}">', html)
        self.assertIn(f'href="{url}"', html)

    def assertNoErrors(self, response):
        self.assertEqual(errors(response), [])

    def assertRefused(self, response):
        """An autosave that was refused (409 with an error message)."""
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True)[:300])
        self.assertTrue(response.get_json()["error"])

    def assertSaved(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        self.assertTrue(response.get_json()["ok"], response.get_json())

    # ================================================================ factories
    # --- mouse colony
    @staticmethod
    def make_cage(client, cage_id: str | None = None, **fields) -> int:
        """Create a cage through the colony dialog; returns its row id."""
        code = cage_id or uniq("C")
        client.post("/colony/cages/create", data={"cage_id": code, **fields})
        found = one("select id from mouse_cages where cage_id=?", code)
        assert found, f"cage {code} was not created"
        return found

    @staticmethod
    def make_litter(client, litter_id: str | None = None, date_of_birth: str = "", **fields) -> int:
        code = litter_id or uniq("L")
        client.post("/colony/litters/create", data={"litter_id": code, "date_of_birth": date_of_birth, **fields})
        found = one("select id from litters where litter_id=?", code)
        assert found, f"litter {code} was not created"
        return found

    @staticmethod
    def make_mouse(client, owner: str, cage: str = "", litter: str = "", date_of_birth: str = "",
                   status: str = "experiment", gender: str = "F", **fields) -> int:
        """Create a mouse through the Add mouse form; returns its row id.
        `cage` / `litter` are codes (created when missing)."""
        before = one("select coalesce(max(id), 0) from mice")
        client.post("/colony/mice/create", data={
            "cage_id": cage, "litter_id": litter, "date_of_birth": date_of_birth,
            "status": status, "gender": gender, "owner": owner, **fields})
        found = one("select max(id) from mice where id>?", before)
        assert found, "mouse was not created"
        return found

    def make_colony(self, client, owner: str, n_mice: int = 2, dob: str | None = None, **cage_fields):
        """A cage with a litter of `n_mice` mice. Returns a dict with the
        codes and row ids: cage, cage_id, litter, litter_id, mice."""
        cage = uniq("C")
        litter = uniq("L")
        cage_row = self.make_cage(client, cage, **cage_fields)
        dob = dob if dob is not None else days_ago(60)
        mice = [self.make_mouse(client, owner, cage=cage, litter=litter, date_of_birth=dob) for _ in range(n_mice)]
        return {"cage": cage, "cage_id": cage_row, "litter": litter,
                "litter_id": one("select id from litters where litter_id=?", litter), "mice": mice}

    # --- zebrafish
    @staticmethod
    def make_line(client, name: str | None = None, **fields) -> int:
        name = name or uniq("line")
        client.post("/zebrafish/lines/create", data={"name": name, **fields})
        found = one("select id from fish_lines where name=?", name)
        assert found, f"fish line {name} was not created"
        return found

    @staticmethod
    def make_tank(client, tank_id: str | None = None, **fields) -> int:
        code = tank_id or uniq("TK")
        client.post("/zebrafish/tanks/create", data={"tank_id": code, **fields})
        found = one("select id from tanks where tank_id=?", code)
        assert found, f"tank {code} was not created"
        return found

    # --- plasmids
    @staticmethod
    def make_box(client, name: str | None = None, rows: int = 9, cols: int = 9, **fields) -> int:
        """A plasmid box on the shared storage grid."""
        name = name or uniq("Box")
        client.post("/plasmids/boxes/save", data={"id": "", "name": name, "rows": str(rows), "cols": str(cols),
                                                  **GRID_NAMING, **fields})
        found = one("select id from plasmid_boxes where name=?", name)
        assert found, f"plasmid box {name} was not created"
        return found

    @staticmethod
    def make_plasmid(client, name: str | None = None, box_id: int | None = None, position: str = "", **fields) -> int:
        name = name or uniq("pT")
        data = {"name": name, "count": "1", **fields}
        if box_id is not None:
            data.update(box_id=str(box_id), position=position)
        client.post("/plasmids", data=data)
        found = one("select max(id) from plasmids where name=?", name)
        assert found, f"plasmid {name} was not created"
        return found

    # --- inventory
    @staticmethod
    def make_item(client, key: str = "reagents", name: str | None = None, **fields) -> int:
        name = name or uniq("item")
        if one("select kind from inventory_modules where key=?", key) == "orders":
            # What an order needs before it can be placed (the preset's Required).
            fields = {"vendor": "Acme", "catalog_number": "A-1", "quantity": "1", **fields}
        client.post(f"/inventory/{key}/items/save", data={"id": "", "name": name, **fields})
        found = one("select max(i.id) from inventory_items i join inventory_modules m on m.id=i.module_id_fk "
                    "where m.key=? and i.name=?", key, name)
        assert found, f"{key} item {name} was not created"
        return found

    # --- fly / worm stocks
    @staticmethod
    def make_stock_module(client, kind: str = "fly", label: str | None = None) -> str:
        """A new fly (or worm) stock database of its own; returns its key."""
        label = label or uniq("Flies ")
        r = client.post("/stocks/new", data={"kind": kind, "label": label, "audience": "lab"})
        path = location(r)
        assert path.startswith("/stocks/"), (r.status_code, path)
        return path.split("?")[0].rsplit("/", 1)[1]

    @staticmethod
    def stock_module_id(key: str) -> int:
        return one("select id from stock_modules where key=?", key)

    def make_incubator(self, client, key: str, name: str | None = None, temperature: str = "25") -> int:
        name = name or uniq("Inc")
        client.post(f"/stocks/{key}/incubators/save", data={"name": name, "temperature": temperature})
        found = one("select id from stock_incubators where module_id_fk=? and name=?", self.stock_module_id(key), name)
        assert found, f"incubator {name} was not created"
        return found

    def make_stock_rack(self, client, key: str, name: str | None = None, rows: int = 4, cols: int = 4,
                        incubator_id: int | None = None, last_flipped_on: str = "", **fields) -> int:
        name = name or uniq("R")
        naming = GRID_NAMING if one("select kind from stock_modules where key=?", key) == "fly" else SEQUENTIAL_NAMING
        client.post(f"/stocks/{key}/racks/save", data={
            "name": name, "rows": rows, "cols": cols, "incubator_id": incubator_id or "",
            "last_flipped_on": last_flipped_on, **naming, **fields})
        found = one("select id from stock_racks where module_id_fk=? and name=?", self.stock_module_id(key), name)
        assert found, f"rack {name} was not created"
        return found

    def make_vial(self, client, key: str, genotype: str | None = None, purpose: str = "stock",
                  rack_id: int | None = None, position: str = "", **fields) -> int:
        """One vial (plate); returns its row id."""
        mid = self.stock_module_id(key)
        before = one("select coalesce(max(id), 0) from stock_units")
        client.post(f"/stocks/{key}/units/save", data={
            "id": "", "genotype": genotype if genotype is not None else uniq("w; geno"), "purpose": purpose,
            "count": 1, "rack_id": rack_id or "", "position": position, **fields})
        found = one("select max(id) from stock_units where module_id_fk=? and id>?", mid, before)
        assert found, "vial was not created"
        return found

    def make_fly_setup(self, client, temperature: str = "25", rows: int = 4, cols: int = 4):
        """A fly database with an incubator and a rack in it:
        returns (key, incubator_id, rack_id)."""
        key = self.make_stock_module(client, "fly")
        inc = self.make_incubator(client, key, temperature=temperature)
        rack = self.make_stock_rack(client, key, rows=rows, cols=cols, incubator_id=inc, last_flipped_on=T)
        return key, inc, rack

    # --- custom organism modules
    @staticmethod
    def make_organism_module(client, label: str | None = None, capabilities=None, **fields) -> str:
        """A custom organism database; returns its key."""
        label = label or uniq("Newts ")
        caps = capabilities if capabilities is not None else [
            "housing", "housing_grid", "individuals", "group_counts", "lines", "cohorts", "genotyping", "schedule"]
        r = client.post("/organisms/new", data={"audience": "lab",
            "preset_key": "custom", "label": label, "identity_mode": "individual", "age_unit": "days",
            "capabilities": caps, "organism_noun": "newt", "organism_noun_plural": "newts",
            "housing_noun": "tank", "housing_noun_plural": "tanks", **fields})
        path = location(r)
        assert path.startswith("/organisms/"), (r.status_code, path, flashes(r))
        return path.split("?")[0].rsplit("/", 1)[1]

    @staticmethod
    def organism_module_id(key: str) -> int:
        return one("select id from organism_modules where key=?", key)

    def make_housing(self, client, key: str, code: str | None = None, **fields) -> int:
        code = code or uniq("H")
        client.post(f"/organisms/{key}/housing/save", data={"code": code, "_full": "1", **fields})
        found = one("select id from organism_housing where module_id_fk=? and code=?", self.organism_module_id(key), code)
        assert found, f"housing {code} was not created"
        return found

    def make_animal(self, client, key: str, code: str | None = None, status: str = "alive", **fields) -> int:
        code = code or uniq("A")
        client.post(f"/organisms/{key}/animal/save", data={"code": code, "status": status, "_full": "1", **fields})
        found = one("select id from organisms where module_id_fk=? and code=?", self.organism_module_id(key), code)
        assert found, f"animal {code} was not created"
        return found
