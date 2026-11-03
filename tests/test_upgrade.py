"""Upgrading a database safely (app/upgrade.py). The whole path, from each
release, is scripts/upgrade-check.py (CI: upgrade-check.yml)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import ON_POSTGRES  # noqa: F401
from app import upgrade  # noqa: E402
from app.db import engine  # noqa: E402
from app.startup import ensure_schema_updates  # noqa: E402


class Upgrade(unittest.TestCase):
    def test_this_database_is_at_the_newest_revision_and_needs_nothing(self):
        self.assertEqual(upgrade.current_revision(engine), upgrade.head_revision())
        self.assertEqual(ensure_schema_updates(apply=False), [])
        self.assertFalse(upgrade.plan(engine).changes)

    def test_the_revisions_form_one_line_from_the_baseline(self):
        from alembic.script import ScriptDirectory
        script = ScriptDirectory.from_config(upgrade.alembic_config())
        self.assertEqual(len(script.get_heads()), 1)
        chain = [r.revision for r in script.walk_revisions()]
        self.assertEqual(chain[-2:], ["0002_v0_8_schema", "0001_baseline"])

    @unittest.skipIf(ON_POSTGRES, "the copy before an upgrade is SQLite's")
    def test_a_copy_is_taken_before_an_upgrade_and_only_ten_kept(self):
        from sqlalchemy import create_engine
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "biomanager.db"
            with sqlite3.connect(db) as c:
                c.execute("create table users (id integer primary key)")
                c.execute("insert into users values (1)")
            eng = create_engine(f"sqlite:///{db}")
            plan = upgrade.Plan(fresh=False, current="0001_baseline", head=upgrade.head_revision())
            for _ in range(12):
                copy = upgrade.backup_before(eng, plan)
            self.assertTrue(copy.exists())
            with sqlite3.connect(copy) as c:
                self.assertEqual(c.execute("select count(*) from users").fetchone()[0], 1)
            self.assertLessEqual(len(list((Path(tmp) / "backups").glob("before-upgrade-*.db"))), upgrade.KEEP_BACKUPS)
            eng.dispose()


class PackagedApp(unittest.TestCase):
    """migrations/ ships in the desktop app as files Alembic runs, so the
    build never sees their imports: each must be bundled some other way
    (v0.10.0's first build lacked logging.config and would not start)."""

    def test_every_import_of_the_migrations_is_bundled(self):
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        spec = (root / "Biomanager.spec").read_text()
        bundled_whole = {"__future__", "alembic", "sqlalchemy", "app"}    # collected, or the app itself
        for path in [root / "migrations" / "env.py", *sorted((root / "migrations" / "versions").glob("*.py"))]:
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
                for name in names:
                    if name.split(".")[0] in bundled_whole:
                        continue
                    self.assertIn(f'"{name}"', spec, f"{path.name} imports {name}: add it to hiddenimports in Biomanager.spec")


class EmptiedDatabase(unittest.TestCase):
    def test_a_zero_byte_database_file_is_not_started_as_a_new_lab(self):
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "biomanager.db"
            db.touch()
            env = {**os.environ, "BIOMANAGER_DATA_DIR": tmp, "DATABASE_URL": f"sqlite:///{db}"}
            env.pop("BIOMANAGER_SEED_DEFAULTS", None)
            r = subprocess.run([sys.executable, "-c", "import app.app"], cwd=Path(__file__).parent.parent,
                               env=env, capture_output=True, text=True, timeout=120)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is an empty file", r.stderr)
            self.assertEqual(db.stat().st_size, 0)
            self.assertFalse((Path(tmp) / "setup-code").exists())

    def test_a_database_from_a_newer_version_is_refused(self):
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "biomanager.db"
            with sqlite3.connect(db) as con:
                con.execute("create table users (id integer primary key)")
                con.execute("create table alembic_version (version_num varchar(32))")
                con.execute("insert into alembic_version values ('9999_from_the_future')")
            env = {**os.environ, "BIOMANAGER_DATA_DIR": tmp, "DATABASE_URL": f"sqlite:///{db}"}
            r = subprocess.run([sys.executable, "-c", "import app.app"], cwd=Path(__file__).parent.parent,
                               env=env, capture_output=True, text=True, timeout=120)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("set up by a newer version", r.stderr)
