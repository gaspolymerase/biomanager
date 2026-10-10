"""Project groups: a layer between a person and the lab. Admins make groups
and choose members (leads may add and remove them); whatever can be shared
with the lab can be shared with one group instead, which its members edit
(databases: see), and the calendar has lab and group to-dos."""
from __future__ import annotations

import unittest

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, client_for, flash_text, make_user, one, uniq

from sqlalchemy import select

from app.app import app
from app.db import SessionLocal
from app.models import LabGroup, LabGroupMember


def make_group(name=None, members=(), leads=()) -> int:
    with SessionLocal() as s:
        group = LabGroup(name=name or uniq("Group"))
        s.add(group)
        s.flush()
        for who in members:
            s.add(LabGroupMember(group_id_fk=group.id, username=who, lead=who in leads))
        s.commit()
        return group.id


class GroupCase(AppTestCase):
    """An admin, a member and a colleague in one group; `other` outside it."""

    def setUp(self):
        super().setUp()
        self.colleague = make_user(uniq("colleague"))
        self.c = client_for(self.colleague)
        self.gid = make_group(members=(self.member, self.colleague))


class GroupPageTests(GroupCase):
    def test_everyone_sees_the_groups_and_who_is_in_them(self):
        self.assertTrue(self.o.get("/groups").headers["Location"].endswith("/settings#groups"))
        html = self.get_ok(self.o, "/settings")
        self.assertIn(f'id="group-{self.gid}"', html)
        self.assertIn(self.colleague, html)
        self.assertNotIn('action="/groups/create"', html)

    def test_an_admin_makes_a_group_with_members(self):
        name = uniq("Sleep")
        r = self.a.post("/groups/create", data={"name": name, "members": [self.member, self.other]})
        self.assertEqual(r.status_code, 302)
        gid = one("select id from lab_groups where name=?", name)
        self.assertEqual(sorted(one_col("select username from lab_group_members where group_id_fk=?", gid)),
                         sorted([self.member, self.other]))

    def test_a_member_may_not_make_a_group_or_add_people(self):
        self.assertEqual(self.m.post("/groups/create", data={"name": uniq("G")}).status_code, 403)
        self.assertEqual(self.m.post(f"/groups/{self.gid}/members", data={"username": self.other}).status_code, 403)

    def test_a_lead_adds_and_takes_out_members_but_not_another_lead(self):
        lead = make_user(uniq("lead"))
        gid = make_group(members=(lead, self.colleague), leads=(lead, self.colleague))
        client = client_for(lead)
        self.assertEqual(client.post(f"/groups/{gid}/members", data={"username": self.other}).status_code, 302)
        self.assertEqual(one("select count(*) from lab_group_members where group_id_fk=? and username=?",
                             gid, self.other), 1)
        client.post(f"/groups/{gid}/members/{self.other}/remove")
        self.assertEqual(one("select count(*) from lab_group_members where group_id_fk=? and username=?",
                             gid, self.other), 0)
        r = client.post(f"/groups/{gid}/members/{self.colleague}/remove", follow_redirects=True)
        self.assertIn("Only an admin can take a lead out", flash_text(r))

    def test_a_name_is_needed_and_used_once(self):
        name = one("select name from lab_groups where id=?", self.gid)
        r = self.a.post("/groups/create", data={"name": name}, follow_redirects=True)
        self.assertIn("already a group called", flash_text(r))


class SharedRecordTests(GroupCase):
    """Shared with a group: its members edit it, others in the lab do not."""

    def test_a_breeder_cage_shared_with_a_group_is_its_members_to_edit(self):
        cage = self.make_cage(self.m, purpose="Breeder")
        r = self.autosave(self.m, f"/colony/cages/{cage}/update", {"is_shared": f"g{self.gid}"})
        self.assertSaved(r)
        self.assertEqual(r.get_json()["row"]["values"]["is_shared"], f"g{self.gid}")
        self.assertEqual(one("select share_group_id from mouse_cages where id=?", cage), self.gid)
        self.assertSaved(self.autosave(self.c, f"/colony/cages/{cage}/update", {"notes": "fed"}))
        self.assertNotEqual(self.autosave(self.o, f"/colony/cages/{cage}/update", {"notes": "x"}).status_code, 200)
        self.assertEqual(one("select notes from mouse_cages where id=?", cage), "fed")

    def test_a_mouse_in_a_group_cage_follows_it(self):
        code = uniq("C")
        cage = self.make_cage(self.m, code, purpose="Breeder")
        mouse = self.make_mouse(self.m, self.member, cage=code)
        self.autosave(self.m, f"/colony/cages/{cage}/update", {"is_shared": f"g{self.gid}"})
        from app import access
        from app.models import MouseRecord, UserAccount
        with SessionLocal() as s:
            m = s.get(MouseRecord, mouse)
            colleague = s.scalar(select(UserAccount).where(UserAccount.username == self.colleague))
            other = s.scalar(select(UserAccount).where(UserAccount.username == self.other))
            with app.test_request_context():
                self.assertTrue(access.can_edit_mouse(m, colleague))
                self.assertFalse(access.can_edit_mouse(m, other))

    def test_you_share_only_with_a_group_you_are_in(self):
        cage = self.make_cage(self.o, purpose="Breeder")
        r = self.autosave(self.o, f"/colony/cages/{cage}/update", {"is_shared": f"g{self.gid}"})
        self.assertRefused(r)
        self.assertIn("project group you are in", r.get_json()["error"])
        self.assertIsNone(one("select share_group_id from mouse_cages where id=?", cage))

    def test_the_cage_sheet_offers_your_groups(self):
        self.make_cage(self.m, purpose="Breeder")
        html = self.get_ok(self.m, "/colony?view=cages&scope=all")
        self.assertIn(f'<option value="g{self.gid}"', html)
        self.assertNotIn(f'<option value="g{self.gid}"', self.get_ok(self.o, "/colony?view=cages&scope=all"))

    def test_a_plasmid_shared_with_a_group(self):
        pid = self.make_plasmid(self.m, is_shared=f"g{self.gid}")
        self.assertEqual(one("select is_shared, share_group_id from plasmids where id=?", pid), 1)
        self.assertEqual(one("select share_group_id from plasmids where id=?", pid), self.gid)
        self.assertSaved(self.autosave(self.c, f"/plasmids/{pid}/update", {"notes": "ok"}))
        self.assertNotEqual(self.autosave(self.o, f"/plasmids/{pid}/update", {"notes": "x"}).status_code, 200)

    def test_reagents_shared_with_a_group(self):
        item = self.make_item(self.m, "reagents", is_shared=f"g{self.gid}")
        self.assertEqual(one("select share_group_id from inventory_items where id=?", item), self.gid)
        self.autosave(self.c, "/inventory/reagents/items/save", {"id": item, "notes": "opened"})
        self.autosave(self.o, "/inventory/reagents/items/save", {"id": item, "notes": "nope"})
        self.assertEqual(one("select notes from inventory_items where id=?", item), "opened")

    def test_a_breeding_tank_for_a_group(self):
        tank = self.make_tank(self.m, purpose="breeding")
        r = self.autosave(self.m, f"/zebrafish/tanks/{tank}/update", {"share_group": f"g{self.gid}"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        self.assertEqual(one("select share_group_id from tanks where id=?", tank), self.gid)
        self.autosave(self.c, f"/zebrafish/tanks/{tank}/update", {"notes": "fed"})
        r = self.autosave(self.o, f"/zebrafish/tanks/{tank}/update", {"notes": "x"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(one("select notes from tanks where id=?", tank), "fed")

    def test_a_lab_stock_vial_for_a_group(self):
        key = self.make_stock_module(self.a)
        vial = self.make_vial(self.m, key, purpose="stock")
        r = self.m.post(f"/stocks/{key}/units/{vial}/update", data={"share_group": f"g{self.gid}"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        self.assertEqual(one("select share_group_id from stock_units where id=?", vial), self.gid)
        self.assertEqual(self.c.post(f"/stocks/{key}/units/{vial}/update",
                                     data={"notes": "flip", "notes_was": ""}).status_code, 200)
        self.assertEqual(self.o.post(f"/stocks/{key}/units/{vial}/update",
                                     data={"notes": "x", "notes_was": "flip"}).status_code, 403)


class GroupDatabaseTests(GroupCase):
    def test_a_database_for_a_group_is_seen_by_its_members_only(self):
        r = self.m.post("/stocks/new", data={"kind": "fly", "label": uniq("Sleep flies "), "audience": f"group:{self.gid}"})
        key = r.headers["Location"].split("?")[0].rsplit("/", 1)[1]
        self.assertEqual(one("select private_to, share_group_id from stock_modules where key=?", key), "")
        self.assertEqual(one("select share_group_id from stock_modules where key=?", key), self.gid)
        self.assertEqual(self.c.get(f"/stocks/{key}").status_code, 200)
        self.assertEqual(self.o.get(f"/stocks/{key}").status_code, 404)
        self.assertEqual(self.a.get(f"/stocks/{key}").status_code, 200)
        self.assertIn(f"/stocks/{key}", self.get_ok(self.c, "/home"))
        self.assertNotIn(f"/stocks/{key}", self.get_ok(self.o, "/home"))
        self.assertEqual(one("select count(*) from notifications where recipient_username=? and title like ?",
                             self.colleague, "%for%"), 1)

    def test_a_member_cannot_make_a_database_for_a_group_they_are_not_in(self):
        gid = make_group(members=(self.other,))
        r = self.m.post("/stocks/new", data={"kind": "fly", "label": uniq("Flies "), "audience": f"group:{gid}"})
        key = r.headers["Location"].split("?")[0].rsplit("/", 1)[1]
        self.assertEqual(one("select private_to from stock_modules where key=?", key), self.member)

    def test_your_own_database_can_be_given_to_your_group(self):
        r = self.m.post("/stocks/new", data={"kind": "fly", "label": uniq("Mine "), "audience": "me"})
        key = r.headers["Location"].split("?")[0].rsplit("/", 1)[1]
        self.assertEqual(self.c.get(f"/stocks/{key}").status_code, 404)
        self.m.post(f"/databases/stocks/{key}/audience", data={"to": f"group:{self.gid}"})
        self.assertEqual(self.c.get(f"/stocks/{key}").status_code, 200)

    def test_a_group_database_stays_out_of_the_lab_defaults(self):
        from app import inventory_service
        self.m.post("/inventory/new", data={"preset": "reagents", "label": uniq("Group reagents "),
                                            "audience": f"group:{self.gid}"})
        with SessionLocal() as s:
            module = inventory_service.first_of_kind(s, "reagents")
            self.assertIsNone(module.share_group_id)


class TodoTests(GroupCase):
    def todo(self, client, audience="0", title=None):
        title = title or uniq("todo")
        r = client.post("/calendar/items", json={"kind": "task", "title": title, "start": "2026-10-05T00:00:00",
                                                 "end": "2026-10-05T23:59:59", "isAllday": True,
                                                 "audience": audience})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["item"]["raw"]["rowId"], title

    def titles(self, client):
        r = client.get("/calendar/events.json?start=2026-10-01&end=2026-10-10")
        return {i["title"] for i in r.get_json() if i.get("kind") == "task"} if isinstance(r.get_json(), list) \
            else {i["title"] for i in r.get_json()["items"] if i.get("kind") == "task"}

    def test_a_personal_to_do_is_its_owners(self):
        tid, title = self.todo(self.m)
        self.assertIn(title, self.titles(self.m))
        self.assertNotIn(title, self.titles(self.o))
        self.assertEqual(self.o.post(f"/calendar/items/task-{tid}/toggle").status_code, 403)
        self.assertEqual(self.o.post(f"/calendar/items/task-{tid}/delete").status_code, 403)
        self.assertEqual(self.o.post(f"/calendar/items/task-{tid}", json={"title": "mine now"}).status_code, 403)
        self.assertEqual(one("select title from tasks where id=?", tid), title)

    def test_a_lab_to_do_is_everyones_to_tick_off(self):
        tid, title = self.todo(self.m, "1")
        self.assertIn(title, self.titles(self.o))
        self.assertEqual(self.o.post(f"/calendar/items/task-{tid}/toggle").status_code, 200)
        self.assertEqual(one("select status from tasks where id=?", tid), "done")
        self.assertEqual(self.o.post(f"/calendar/items/task-{tid}/delete").status_code, 403)

    def test_a_group_to_do_is_its_members(self):
        tid, title = self.todo(self.m, f"g{self.gid}")
        self.assertIn(title, self.titles(self.c))
        self.assertNotIn(title, self.titles(self.o))
        self.assertEqual(self.c.post(f"/calendar/items/task-{tid}/toggle").status_code, 200)
        self.assertEqual(self.o.post(f"/calendar/items/task-{tid}/toggle").status_code, 403)

    def test_only_a_group_you_are_in(self):
        gid = make_group(members=(self.other,))
        r = self.m.post("/calendar/items", json={"kind": "task", "title": uniq("t"), "audience": f"g{gid}"})
        self.assertEqual(r.status_code, 403)

    def test_home_lists_your_groups_and_the_labs_to_dos(self):
        from datetime import date
        today = f"{date.today().isoformat()}T00:00:00"
        group_job, lab_job = uniq("group job"), uniq("lab job")
        self.c.post("/calendar/items", json={"kind": "task", "title": group_job, "audience": f"g{self.gid}",
                                             "start": today, "isAllday": True})
        self.o.post("/calendar/items", json={"kind": "task", "title": lab_job, "audience": "1",
                                             "start": today, "isAllday": True})
        for client in (self.m, self.o):
            self.post(client, "/home/cards", {"order": ["todos"], "show": ["todos"]})
        mine = self.get_ok(self.m, "/home")
        self.assertIn(group_job, mine)
        self.assertIn(lab_job, mine)
        self.assertNotIn(group_job, self.get_ok(self.o, "/home"))

    def test_the_dialog_offers_your_groups(self):
        html = self.get_ok(self.m, "/calendar")
        self.assertIn(f'<option value="g{self.gid}">', html)


class EventTests(GroupCase):
    """Calendar events: personal (its owner's alone), the lab's or a group's
    (seen by those it is for, changed by its owner and admins)."""

    def event(self, client, audience=None, title=None):
        title = title or uniq("event")
        body = {"kind": "event", "title": title, "start": "2026-10-05T09:00:00", "end": "2026-10-05T10:00:00",
                "isAllday": False}
        if audience is not None:
            body["audience"] = audience
        r = client.post("/calendar/items", json=body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["item"]["raw"]["rowId"], title

    def titles(self, client):
        data = client.get("/calendar/events.json?start=2026-10-01&end=2026-10-10").get_json()
        items = data if isinstance(data, list) else data["items"]
        return {i["title"] for i in items if i.get("kind") == "event"}

    def test_an_event_is_the_labs_unless_chosen_otherwise(self):
        eid, title = self.event(self.m)
        self.assertEqual(one("select is_shared from calendar_events where id=?", eid), 1)
        self.assertIn(title, self.titles(self.o))

    def test_a_shared_event_is_changed_by_its_owner_and_admins_only(self):
        eid, title = self.event(self.m, "1")
        self.assertEqual(self.o.post(f"/calendar/items/event-{eid}", json={"title": "mine"}).status_code, 403)
        self.assertEqual(self.o.post(f"/calendar/items/event-{eid}/delete").status_code, 403)
        self.assertEqual(self.a.post(f"/calendar/items/event-{eid}", json={"title": "Moved"}).status_code, 200)
        self.assertEqual(one("select title from calendar_events where id=?", eid), "Moved")

    def test_a_personal_event_is_its_owners_alone_not_even_an_admins(self):
        eid, title = self.event(self.m, "0")
        self.assertIn(title, self.titles(self.m))
        self.assertNotIn(title, self.titles(self.o))
        self.assertNotIn(title, self.titles(self.a))
        self.assertEqual(self.a.post(f"/calendar/items/event-{eid}", json={"title": "x"}).status_code, 403)
        self.assertEqual(self.a.post(f"/calendar/items/event-{eid}/delete").status_code, 403)

    def test_a_group_event_is_seen_by_its_members(self):
        eid, title = self.event(self.m, f"g{self.gid}")
        self.assertIn(title, self.titles(self.c))
        self.assertNotIn(title, self.titles(self.o))
        self.assertEqual(self.c.post(f"/calendar/items/event-{eid}", json={"title": "x"}).status_code, 403)

    def test_events_a_lab_already_had_stay_shared(self):
        from app.models import CalendarEvent
        from datetime import date
        title = uniq("old")
        with SessionLocal() as s:     # as older code (meeting rotations) makes them
            s.add(CalendarEvent(title=title, event_date=date(2026, 10, 3), owner=self.member))
            s.commit()
        self.assertEqual(one("select is_shared from calendar_events where title=?", title), 1)


class PersonalTodoTests(GroupCase):
    def test_an_admin_neither_sees_nor_changes_a_personal_to_do(self):
        title = uniq("private job")
        r = self.m.post("/calendar/items", json={"kind": "task", "title": title, "start": "2026-10-05T00:00:00",
                                                 "isAllday": True})
        tid = r.get_json()["item"]["raw"]["rowId"]
        data = self.a.get("/calendar/events.json?start=2026-10-01&end=2026-10-10").get_json()
        items = data if isinstance(data, list) else data["items"]
        self.assertNotIn(title, {i["title"] for i in items})
        for url, body in ((f"/calendar/items/task-{tid}", {"title": "x"}), (f"/calendar/items/task-{tid}/toggle", {}),
                          (f"/calendar/items/task-{tid}/delete", {})):
            self.assertEqual(self.a.post(url, json=body).status_code, 403, url)
        self.assertEqual(one("select title from tasks where id=?", tid), title)


class CageSharingTests(GroupCase):
    def test_an_experiment_cage_can_be_shared_with_a_group(self):
        cage = self.make_cage(self.m, purpose="Experiments")
        self.assertEqual(one("select is_shared from mouse_cages where id=?", cage), 0)
        self.assertSaved(self.autosave(self.m, f"/colony/cages/{cage}/update", {"is_shared": f"g{self.gid}"}))
        self.assertSaved(self.autosave(self.c, f"/colony/cages/{cage}/update", {"notes": "dosed"}))

    def test_the_cage_sheet_filters_by_purpose(self):
        self.make_cage(self.m, purpose="Experiment")
        self.make_cage(self.m, purpose="Breeder")
        html = self.get_ok(self.m, "/colony?view=cages&scope=all")
        self.assertIn('data-dt-filter="purpose:experiment"', html)
        self.assertIn('data-dt-filter="purpose:breeder"', html)
        self.assertIn('data-cage-card-filter="purpose:experiment"', html)
        self.assertIn('data-purpose="experiment"', html)


class NotebookTests(GroupCase):
    def test_a_page_shared_with_a_group_opens_for_its_members(self):
        r = self.m.post("/notebook/api/pages/new", json={"title": uniq("plan")})
        page = r.get_json()["page_id"]
        self.assertEqual(self.c.get(f"/notebook/api/pages/{page}").status_code, 404)
        r = self.m.post(f"/notebook/api/pages/{page}/shares", json={"username": f"group:{self.gid}", "role": "edit"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.c.get(f"/notebook/api/pages/{page}").get_json()["page"]["role"], "edit")
        self.assertEqual(self.o.get(f"/notebook/api/pages/{page}").status_code, 404)
        self.assertEqual(one("select count(*) from notifications where recipient_username=? and category='notebook'",
                             self.colleague), 1)
        shares = self.m.get(f"/notebook/api/pages/{page}/shares").get_json()
        self.assertIn(f"group:{self.gid}", [g["username"] for g in shares["groups"]])

    def test_a_template_for_a_group(self):
        title = uniq("Group protocol")
        r = self.m.post("/notebook/templates/create", data={"title": title, "lab": f"g{self.gid}"})
        self.assertEqual(r.status_code, 200)
        listed = lambda c: [t["title"] for t in c.get("/notebook/templates").get_json()["templates"]]  # noqa: E731
        self.assertIn(title, listed(self.c))
        self.assertNotIn(title, listed(self.o))


class ColonyScopeTests(GroupCase):
    def test_my_groups_shows_the_groups_mice(self):
        theirs = self.make_mouse(self.c, self.colleague, litter=uniq("L"))
        stranger = self.make_mouse(self.o, self.other, litter=uniq("L"))
        html = self.get_ok(self.m, "/colony?view=mice&scope=groups")
        self.assertIn(f'data-id="{theirs}"', html)
        self.assertNotIn(f'data-id="{stranger}"', html)
        self.assertIn("scope=groups", html)
        self.assertNotIn("scope=groups", self.get_ok(self.o, "/colony?view=mice"))


class DeletingAGroupTests(GroupCase):
    def test_what_was_shared_with_it_is_its_owners_again(self):
        cage = self.make_cage(self.m, purpose="Breeder")
        self.autosave(self.m, f"/colony/cages/{cage}/update", {"is_shared": f"g{self.gid}"})
        r = self.m.post("/stocks/new", data={"kind": "fly", "label": uniq("G "), "audience": f"group:{self.gid}"})
        key = r.headers["Location"].split("?")[0].rsplit("/", 1)[1]
        self.assertEqual(self.a.post(f"/groups/{self.gid}/delete").status_code, 302)
        self.assertEqual(one("select is_shared, share_group_id from mouse_cages where id=?", cage), 0)
        self.assertIsNone(one("select share_group_id from mouse_cages where id=?", cage))
        self.assertEqual(one("select private_to from stock_modules where key=?", key), self.member)
        self.assertEqual(one("select count(*) from lab_group_members where group_id_fk=?", self.gid), 0)


class CopyTests(GroupCase):
    """A member's copy of the lab (app/lab_copy.py) keeps their groups'
    databases and pages, not other groups'."""

    def test_a_copy_keeps_your_groups_and_leaves_out_others(self):
        import re
        import sqlite3
        import tempfile
        from pathlib import Path
        from app import lab, lab_copy
        from app.models import InventoryItem, InventoryModule
        with SessionLocal() as s:
            lab._set_flag(s, "members_keep_copies", True)
            s.commit()
        self.addCleanup(self._permission_off)
        lab_copy.key_throttle.reset()
        theirs = make_group(members=(self.other,))
        names = {"ours": uniq("ours"), "theirs": uniq("theirs")}
        with SessionLocal() as s:
            for gid, word in ((self.gid, names["ours"]), (theirs, names["theirs"])):
                module = InventoryModule(key=uniq("grp-"), label=word, share_group_id=gid)
                s.add(module)
                s.flush()
                s.add(InventoryItem(module_id_fk=module.id, name=word))
            s.commit()
        r = self.m.post("/settings/lab-copies", data={"label": uniq("Laptop ")})
        key = re.search(r'id="lab-copy-key"[^>]*>(bmk_[a-z0-9]+)<', r.get_data(as_text=True)).group(1)
        snap = app.test_client().get("/api/lab-copy/snapshot", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(snap.status_code, 200)
        path = Path(tempfile.mkdtemp()) / "copy.db"
        path.write_bytes(snap.get_data())
        with sqlite3.connect(path) as con:
            kept = {r[0] for r in con.execute("select name from inventory_items")}
        self.assertIn(names["ours"], kept)
        self.assertNotIn(names["theirs"], kept)

    @staticmethod
    def _permission_off():
        from app import lab
        with SessionLocal() as s:
            lab._set_flag(s, "members_keep_copies", False)
            s.commit()


def one_col(sql, *args):
    from tests.base import rows
    return [r[0] for r in rows(sql, *args)]


if __name__ == "__main__":
    unittest.main()
