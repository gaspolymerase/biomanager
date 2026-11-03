"""Who may fetch an uploaded file (app/uploads.py): /static/uploads/<name>
is served only to someone who may see a record it belongs to: a notebook
page's pictures as the page is shared, a plasmid's files to the lab, a
capture's photo to its person, a file nothing names to its uploader and the
admins; and revision 0038 finds what older records named."""
from __future__ import annotations

import base64
import importlib
import io
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import sqlalchemy as sa

from tests.base import (AppTestCase, client_for, count, execute, make_user, one, rows,  # first: a test database
                        uniq, user_id)
from app import uploads  # noqa: E402
from app.db import SessionLocal  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def make_guest() -> str:
    """A guest pass's account: a member whose account ends."""
    name = make_user(uniq("guest"))
    execute("update users set expires_at=? where username=?", datetime.utcnow() + timedelta(days=1), name)
    return name


def a_file(text: str = "a picture") -> str:
    """A file in the uploads folder that no record names; its name."""
    name = uploads.upload_name(uniq("orphan") + ".png")
    (uploads.UPLOAD_DIR / name).write_text(text, encoding="utf-8")
    return name


def url(name: str) -> str:
    return f"/static/uploads/{name}"


class Base(AppTestCase):
    def post_json(self, client, address, body=None):
        return client.post(address, data=json.dumps(body or {}), content_type="application/json")

    def new_page(self, client, title=None) -> int:
        r = self.post_json(client, "/notebook/api/pages/new", {"title": title or uniq("page ")})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["page_id"]

    def save(self, client, page_id, body):
        r = client.post(f"/notebook/pages/{page_id}/update", data={"body": body})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])

    def share(self, client, page_id, username, role="view"):
        r = self.post_json(client, f"/notebook/api/pages/{page_id}/shares", {"username": username, "role": role})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def upload_picture(self, client, page_id=None) -> str:
        """The notebook editor's picture upload; the stored name."""
        data = {"image": (io.BytesIO(PNG), "blot.png")}
        if page_id is not None:
            data["page_id"] = str(page_id)                      # the live editor says which page
        r = client.post("/notebook/upload-image", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        address = r.get_json()["url"]
        self.assertTrue(address.startswith("/static/uploads/"), address)
        return address[len("/static/uploads/"):]

    def assertServed(self, client, name):
        r = client.get(url(name))
        self.assertEqual(r.status_code, 200, f"{name}: {r.status_code}")
        self.assertTrue(r.headers["Cache-Control"].startswith("private"), r.headers["Cache-Control"])
        r.close()

    def assertRefused(self, client, name):
        r = client.get(url(name))
        self.assertEqual(r.status_code, 404, f"{name}: {r.status_code}")
        self.assertEqual(r.headers["Cache-Control"], "no-store")


class NotebookPictures(Base):
    def test_its_uploader_sees_a_picture_before_the_page_is_saved(self):
        name = self.upload_picture(self.m)
        self.assertServed(self.m, name)
        self.assertRefused(self.o, name)
        self.assertEqual(rows("select source, username from upload_owners where name=?", name),
                         [("upload", self.member)])

    def test_someone_editing_the_page_with_its_uploader_sees_the_picture_before_autosave(self):
        page = self.new_page(self.m)
        editor = make_user()
        self.share(self.m, page, editor, role="edit")
        e = client_for(editor)
        name = self.upload_picture(self.m, page)
        self.assertServed(e, name)                               # before the page names it
        self.assertRefused(self.o, name)
        # Uploaded "into" a page they can't edit, it stays only theirs.
        other_page = self.new_page(self.o)
        name = self.upload_picture(self.m, other_page)
        self.assertRefused(self.o, name)
        self.assertEqual(count("upload_owners", "name=? and source='notebook_pages'", name), 0)

    def test_a_private_page_s_picture_is_its_owner_s_until_shared(self):
        name = self.upload_picture(self.m)
        page = self.new_page(self.m)
        self.save(self.m, page, f"Western blot\n\n![blot](/static/uploads/{name})\n")
        self.assertEqual(count("upload_owners", "name=? and source='notebook_pages' and record_id=?", name, page), 1)
        viewer = make_user()
        v = client_for(viewer)
        self.assertRefused(v, name)
        self.assertRefused(self.o, name)
        # Admins don't open someone's private page, nor its pictures.
        self.assertRefused(self.a, name)
        self.share(self.m, page, viewer)
        self.assertServed(v, name)
        self.assertRefused(self.o, name)

    def test_a_page_shared_with_the_lab_shows_its_pictures_to_members_not_guests(self):
        name = self.upload_picture(self.m)
        page = self.new_page(self.m)
        self.save(self.m, page, f'<img src="/static/uploads/{name}">')
        self.share(self.m, page, "*")
        self.assertServed(self.o, name)
        self.assertRefused(client_for(make_guest()), name)

    def test_a_page_shared_with_a_project_group_shows_its_pictures_to_the_group(self):
        from app.models import LabGroup, LabGroupMember
        in_group, outside = make_user(), make_user()
        with SessionLocal() as s:
            group = LabGroup(name=uniq("Group "))
            s.add(group)
            s.flush()
            s.add_all([LabGroupMember(group_id_fk=group.id, username=in_group),
                       LabGroupMember(group_id_fk=group.id, username=self.member)])
            group_id = group.id
            s.commit()
        name = self.upload_picture(self.m)
        page = self.new_page(self.m)
        self.save(self.m, page, f"![x](/static/uploads/{name})")
        self.share(self.m, page, f"group:{group_id}")
        self.assertServed(client_for(in_group), name)
        self.assertRefused(client_for(outside), name)

    def test_a_picture_taken_out_of_the_page_stays_with_its_versions(self):
        viewer = make_user()
        name = self.upload_picture(self.m)
        page = self.new_page(self.m)
        self.save(self.m, page, f"![x](/static/uploads/{name})")
        self.share(self.m, page, viewer)
        self.save(self.m, page, "No picture now")
        self.assertServed(client_for(viewer), name)

    def test_a_deleted_page_s_picture_is_its_uploader_s_and_the_admins(self):
        viewer = make_user()
        name = self.upload_picture(self.m)
        page = self.new_page(self.m)
        self.save(self.m, page, f"![x](/static/uploads/{name})")
        self.share(self.m, page, viewer)
        r = self.m.post(f"/notebook/pages/{page}/delete", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.assertRefused(client_for(viewer), name)
        self.assertServed(self.m, name)
        self.assertServed(self.a, name)

    def test_a_file_named_in_any_form_goes_with_its_page(self):
        """A page whose text names a file in any saved form (here an <img>
        with a query and a link) gives every one of them its page."""
        first, second = self.upload_picture(self.m), self.upload_picture(self.m)
        page = self.new_page(self.m)
        self.save(self.m, page, f'[data](/static/uploads/{first}) and <img src="/static/uploads/{second}?v=2">')
        self.share(self.m, page, self.other)
        self.assertServed(self.o, first)
        self.assertServed(self.o, second)

    def test_the_uploader_s_picture_in_a_shared_page_opens_for_its_readers(self):
        reader = make_user()
        page = self.new_page(self.m)
        self.share(self.m, page, reader)
        name = self.upload_picture(self.m)
        self.assertRefused(client_for(reader), name)
        self.save(self.m, page, f"![x](/static/uploads/{name})")
        self.assertServed(client_for(reader), name)

    def test_a_copied_address_in_one_s_own_page_opens_nothing(self):
        """Someone who learnt the name of a picture in a private page (a
        forwarded link, a screenshot) pastes it into their own page: neither
        they nor their page's readers can open it."""
        name = self.upload_picture(self.m)
        private = self.new_page(self.m)
        self.save(self.m, private, f"![blot](/static/uploads/{name})")
        reader = make_user()
        own = self.new_page(self.o)
        self.share(self.o, own, reader)
        self.save(self.o, own, f"Look: ![blot](/static/uploads/{name})")
        self.assertEqual(count("upload_owners", "name=? and source='notebook_pages' and record_id=?", name, own), 0)
        self.assertRefused(self.o, name)
        self.assertRefused(client_for(reader), name)
        # Shared with them, it opens; their own page still gives it nobody else.
        self.share(self.m, private, self.other)
        self.assertServed(self.o, name)
        self.assertRefused(client_for(reader), name)

    def test_someone_who_may_open_it_may_put_it_in_another_page(self):
        name = self.upload_picture(self.m)
        private = self.new_page(self.m)
        self.save(self.m, private, f"![blot](/static/uploads/{name})")
        self.share(self.m, private, self.other)
        reader = make_user()
        own = self.new_page(self.o)
        self.share(self.o, own, reader)
        self.save(self.o, own, f"From the blot page: ![blot](/static/uploads/{name})")
        self.assertServed(client_for(reader), name)

    def test_another_s_capture_or_plasmid_file_named_in_a_record_opens_nothing(self):
        r = self.m.post("/captures/new", data={"text": "", "file": (io.BytesIO(PNG), "plate.png")},
                        content_type="multipart/form-data")
        self.assertIn(r.status_code, (200, 302))
        name = uploads.stored_name(one("select file_path from captures where owner_username=? order by id desc limit 1",
                                       self.member))
        item = self.make_item(self.o, notes=f"/static/uploads/{name}")
        self.assertTrue(item)
        self.assertRefused(self.o, name)
        self.assertRefused(self.a, name)


class OtherKinds(Base):
    def test_a_capture_s_photo_is_its_person_s_only(self):
        r = self.m.post("/captures/new", data={"text": "", "file": (io.BytesIO(PNG), "plate.png")},
                        content_type="multipart/form-data")
        self.assertIn(r.status_code, (200, 302))
        path = one("select file_path from captures where owner_username=? order by id desc limit 1", self.member)
        name = uploads.stored_name(path)
        self.assertServed(self.m, name)
        self.assertRefused(self.o, name)
        self.assertRefused(self.a, name)

    def test_a_plasmid_s_files_follow_the_plasmid(self):
        rid = self.make_plasmid(self.m, sequence_text="ACGTACGTACGT")
        r = self.m.post(f"/plasmids/{rid}/files", data={"files": [(io.BytesIO(PNG), "gel.png")]},
                        content_type="multipart/form-data")
        self.assertIn(r.status_code, (200, 302))
        name = uploads.stored_name(one("select path from plasmid_files where plasmid_row_id=?", rid))
        # Every plasmid is the lab's to see, guests' too.
        for client in (self.m, self.o, self.a, client_for(make_guest())):
            self.assertServed(client, name)
        execute("delete from plasmids where id=?", rid)
        self.assertRefused(self.o, name)
        self.assertServed(self.a, name)            # nothing names it now: the admins'

    def test_a_personal_database_s_files_are_its_owner_s_and_the_admins(self):
        from app.models import InventoryItem, InventoryModule
        name = a_file()
        with SessionLocal() as s:
            module = InventoryModule(key=uniq("mine-"), label="Mine", private_to=self.member)
            s.add(module)
            s.flush()
            s.add(InventoryItem(module_id_fk=module.id, name=uniq("item"), notes=f"Datasheet: /static/uploads/{name}"))
            s.commit()
        self.assertServed(self.m, name)
        self.assertServed(self.a, name)
        self.assertRefused(self.o, name)

    def test_a_lab_record_s_file_is_the_lab_s(self):
        name = a_file()
        item = self.make_item(self.a, notes=f"See /static/uploads/{name}")
        self.assertTrue(item)
        self.assertServed(self.o, name)

    def test_a_file_nothing_names_is_the_admins_only(self):
        name = a_file()
        self.assertRefused(self.m, name)
        self.assertServed(self.a, name)
        self.assertRefused(self.a, "no-such-file.png")

    def test_signing_in_comes_first(self):
        r = self.app_client().get(url(a_file()))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    @staticmethod
    def app_client():
        from app.app import app
        return app.test_client()

    def test_a_path_outside_the_folder_is_refused(self):
        self.assertEqual(self.a.get("/static/uploads/../../app.py").status_code, 404)


class MembersCopy(Base):
    def test_a_member_s_copy_keeps_only_what_names_their_files_or_their_records(self):
        from app import lab_copy
        from app.models import UserAccount
        mine = self.upload_picture(self.m)
        theirs = self.upload_picture(self.o)
        page = self.new_page(self.o)
        self.save(self.o, page, f"![x](/static/uploads/{theirs})")
        with SessionLocal() as s:
            member = s.get(UserAccount, user_id(self.member))
            s.expunge(member)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "copy.db"
            lab_copy.write_snapshot(path, member)
            with sqlite3.connect(path) as con:
                names = {n for (n,) in con.execute("select name from upload_owners")}
                users = {u for (u,) in con.execute("select username from upload_owners where source='upload'")}
        self.assertIn(mine, names)
        self.assertNotIn(theirs, names)
        self.assertEqual(users, {self.member})


class Backfill(AppTestCase):
    """Revision 0038 finds the files older records name."""

    def run_migration(self, conn):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        migration = importlib.import_module("migrations.versions.0038_upload_owners")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

    def test_what_older_records_name_is_found_once(self):
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.exec_driver_sql("create table notebook_pages (id integer primary key, title varchar(200), body text)")
            conn.exec_driver_sql("create table notebook_versions (id integer primary key, page_id_fk integer, body text)")
            conn.exec_driver_sql("create table captures (id integer primary key, owner_username varchar(80), "
                                 "file_path varchar(400), done boolean)")
            conn.exec_driver_sql("create table plasmid_files (id integer primary key, plasmid_row_id integer, "
                                 "path varchar(300))")
            conn.exec_driver_sql("create table audit_log (id integer primary key, details text)")
            conn.execute(sa.text("insert into notebook_pages (title, body) values ('Blot', :body)"),
                         {"body": "![a](/static/uploads/20250101_a_blot.png) [b](/static/uploads/20250101_b_data%201.csv)"})
            conn.execute(sa.text("insert into notebook_versions (page_id_fk, body) values (1, :body)"),
                         {"body": '<img src="/static/uploads/20250101_c_old.png">'})
            conn.execute(sa.text("insert into captures (owner_username, file_path, done) values ('ann', :p, :done)"),
                         {"p": "uploads/20250101_d_plate.jpg", "done": False})
            conn.execute(sa.text("insert into plasmid_files (plasmid_row_id, path) values (4, :p)"),
                         {"p": "uploads/20250101_e_read.ab1"})
            conn.execute(sa.text("insert into audit_log (details) values (:d)"), {"d": "uploads/20250101_f_gone.png"})
            for _ in range(2):
                self.run_migration(conn)
            found = sorted(conn.exec_driver_sql("select name, source, record_id from upload_owners").fetchall())
        self.assertEqual(found, [("20250101_a_blot.png", "notebook_pages", 1),
                                 ("20250101_b_data 1.csv", "notebook_pages", 1),
                                 ("20250101_c_old.png", "notebook_versions", 1),
                                 ("20250101_d_plate.jpg", "captures", 1),
                                 ("20250101_e_read.ab1", "plasmid_files", 1)])

    def test_a_page_saved_before_is_its_viewers_after_the_backfill(self):
        """Text written straight to the database (as an older version did)
        has no owner rows until the revision runs over it."""
        from app.db import engine
        name = a_file()
        owner, viewer = make_user(), make_user()
        from app.models import NotebookPage, NotebookShare, NotebookTab
        uploads.enabled = False                  # as a version before 0038 saved it
        self.addCleanup(setattr, uploads, "enabled", True)
        with SessionLocal() as s:
            tab = NotebookTab(owner_username=owner, title=uniq("topic "))
            s.add(tab)
            s.flush()
            page = NotebookPage(tab_id_fk=tab.id, title=uniq("old "), body=f"![x](/static/uploads/{name})")
            s.add(page)
            s.flush()
            s.add(NotebookShare(page_id_fk=page.id, username=viewer, role="view"))
            page = page.id
            s.commit()
        uploads.enabled = True
        self.assertEqual(count("upload_owners", "name=?", name), 0)
        self.assertEqual(client_for(viewer).get(url(name)).status_code, 404)
        with engine.begin() as conn:
            self.run_migration(conn)
        self.assertEqual(rows("select source, record_id from upload_owners where name=?", name),
                         [("notebook_pages", page)])
        r = client_for(viewer).get(url(name))
        self.assertEqual(r.status_code, 200)
        r.close()
        self.assertEqual(client_for(make_user()).get(url(name)).status_code, 404)
