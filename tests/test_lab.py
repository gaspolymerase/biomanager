"""Lab setup, personal databases and notifications (app/lab.py,
app/lab_routes.py, app/notify.py).

The suite's database is shared by every test, so a test that changes a
lab-wide setting puts it back in tearDown."""
from tests.base import *  # noqa: F401,F403
from tests.base import AUTOSAVE, AppTestCase, client_for, count, execute, make_user, one, rows, uniq, user_id

import os
import unittest
from datetime import datetime, timedelta

from flask import g
from werkzeug.security import generate_password_hash

from app import lab, notify
from app.app import app
from app.db import SessionLocal
from app.inventory_service import get_setting, set_setting
from app.models import MouseRecord, UserAccount

PASSWORD = "correct horse battery"


def real_user(role="member", welcomed=True) -> str:
    """A user who can sign in with PASSWORD."""
    username = uniq("u")
    with SessionLocal() as s:
        s.add(UserAccount(username=username, password_hash=generate_password_hash(PASSWORD), role=role,
                          welcomed_at=datetime.utcnow() if welcomed else None))
        s.commit()
    return username


def notes_for(username, category=None):
    sql = "select title, category, link, actor, is_read from notifications where recipient_username=?"
    args = [username]
    if category:
        sql += " and category=?"
        args.append(category)
    return rows(sql + " order by id", *args)


class LabSettingsCase(AppTestCase):
    """Remembers the lab-wide settings and puts them back afterwards."""
    KEYS = ["lab_setup_done", "lab_name", "members_create_databases", "members_share_databases",
            "lab_timezone", "date_style", "genotyping_day"] + \
           [f"feature:{k}" for k in lab.FEATURES]

    def setUp(self):
        super().setUp()
        with SessionLocal() as s:
            self._saved = {k: get_setting(s, k, None) for k in self.KEYS}
            self._modules = rows("select 'stock', id, enabled from stock_modules union all "
                                 "select 'inventory', id, enabled from inventory_modules")

    def tearDown(self):
        with SessionLocal() as s:
            from app.models import AppSetting
            for key, value in self._saved.items():
                row = s.get(AppSetting, key)
                if value is None:
                    if row is not None:
                        s.delete(row)
                else:
                    set_setting(s, key, value)
            s.commit()
        for kind, mid, enabled in self._modules:
            execute(f"update {'stock_modules' if kind == 'stock' else 'inventory_modules'} "
                    f"set enabled={'true' if enabled else 'false'} where id=?", mid)
        # The process's zone goes back to the server's own.
        lab.apply_timezone("")
        lab._tz_cache["value"] = None
        super().tearDown()

    def set(self, key, value):
        with SessionLocal() as s:
            set_setting(s, key, value)
            s.commit()

    def survey(self, client, **overrides):
        """Everything ticked, except what `overrides` sets to False."""
        data = {f"feature:{k}": "1" for k in lab.FEATURES}
        data.update({f"stock:{k}": "1" for k in lab.STOCK_CHOICES})
        data.update({f"inventory:{k}": "1" for k in lab.INVENTORY_CHOICES})
        data.update({"members_create_databases": "1", "members_share_databases": "1", "lab_name": "Test Lab"})
        for key, value in overrides.items():
            key = key.replace("__", ":")
            if value is False:
                data.pop(key, None)
            else:
                data[key] = value
        return self.post(client, "/setup", data=data)


# ---------------------------------------------------------------- setup survey

class SetupSurvey(LabSettingsCase):
    def test_only_admins_may_open_it(self):
        r = self.post(self.m, "/setup", data={})
        self.assertFlash(r, "Only a lab admin", "error")
        self.assertEqual(self.m.get("/setup").status_code, 302)

    def test_the_first_admin_is_sent_to_it_after_signing_in(self):
        execute("delete from app_settings where key='lab_setup_done'")
        admin = real_user("admin")
        r = app.test_client().post("/login", data={"username": admin, "password": PASSWORD})
        self.assertEqual(r.headers["Location"], "/setup")

    def test_answering_it_marks_the_lab_set_up_and_goes_home(self):
        execute("delete from app_settings where key='lab_setup_done'")
        r = self.survey(self.a)
        self.assertFlash(r, "The lab is set up", "success")
        self.assertTrue(one("select value from app_settings where key='lab_setup_done'"))
        self.assertEqual(one("select value from app_settings where key='lab_name'"), "Test Lab")

    def test_a_switched_off_database_leaves_the_sidebar_home_and_its_pages(self):
        self.survey(self.a, feature__zebrafish=False)
        html = self.get_ok(self.m, "/home")
        self.assertNotIn('href="/zebrafish"', html)
        self.assertEqual(self.m.get("/zebrafish").status_code, 302)
        r = self.m.get("/zebrafish", follow_redirects=True)
        self.assertFlash(r, "Zebrafish is switched off for this lab", "error")
        # Its data is kept: switching it back on brings the page back.
        self.survey(self.a)
        self.assertEqual(self.m.get("/zebrafish").status_code, 200)

    def test_search_leaves_out_switched_off_functions(self):
        name = uniq("pSearch")
        self.make_plasmid(self.a, name)
        found = lambda: [r["type"] for r in self.m.get(f"/search?q={name}").get_json()["results"]]
        self.assertIn("plasmid", found())
        self.survey(self.a, feature__plasmids=False)
        self.assertNotIn("plasmid", found())

    def test_a_write_to_a_switched_off_function_is_refused(self):
        self.survey(self.a, feature__calendar=False)
        r = self.m.post("/calendar/events/create", data={"title": "x"}, headers=AUTOSAVE)
        self.assertEqual(r.status_code, 403)
        self.assertIn("switched off", r.get_json()["error"])

    def test_the_home_page_follows_the_lab(self):
        self.survey(self.a, feature__colony=False, inventory__orders=False)
        html = self.get_ok(self.m, "/home")
        self.assertNotIn("Mice older than 30 weeks", html)
        self.assertNotIn("Recent orders", html)
        self.survey(self.a)
        html = self.get_ok(self.m, "/home")
        self.assertIn("Mice older than 30 weeks", html)
        self.assertIn("Recent orders", html)

    def test_unticking_an_inventory_hides_it_and_ticking_brings_it_back(self):
        self.survey(self.a, inventory__antibodies=False)
        self.assertEqual(one("select count(*) from inventory_modules where kind='antibodies' "
                             "and private_to='' and share_group_id is null and enabled=true"), 0)
        self.assertNotIn("Antibodies", self.get_ok(self.m, "/home").split("home-dbs")[1].split("</section>")[0])
        self.survey(self.a)
        self.assertGreater(one("select count(*) from inventory_modules where kind='antibodies' "
                               "and private_to='' and share_group_id is null and enabled=true"), 0)

    def test_a_database_switched_on_later_is_announced_to_the_lab(self):
        self.survey(self.a, stock__worm=False)
        self.survey(self.a)
        self.assertTrue([n for n in notes_for(self.member, "lab") if "C. elegans" in n[0]])

    def test_signing_in_lands_on_home_when_the_start_page_is_switched_off(self):
        self.survey(self.a, feature__colony=False)
        member = real_user()
        r = app.test_client().post("/login", data={"username": member, "password": PASSWORD})
        self.assertEqual(r.headers["Location"], "/home")

    def test_admins_can_make_a_member_an_admin_from_here(self):
        self.set("lab_setup_done", "2026-01-01T00:00:00")
        promoted = make_user(uniq("promo"))
        html = self.get_ok(self.a, "/setup")
        self.assertIn("Make admin", html)
        self.post(self.a, f"/admin/users/{user_id(promoted)}/role")
        self.assertEqual(one("select role from users where username=?", promoted), "admin")
        self.assertTrue(notes_for(promoted, "lab"))


class WelcomeTour(LabSettingsCase):
    def test_a_new_member_sees_it_once(self):
        self.set("lab_setup_done", "2026-01-01T00:00:00")
        member = real_user(welcomed=False)
        c = app.test_client()
        r = c.post("/login", data={"username": member, "password": PASSWORD})
        self.assertEqual(r.headers["Location"], "/welcome")
        self.assertIn("Your lab's databases", c.get("/welcome").get_data(as_text=True))
        c.post("/welcome")
        self.assertIsNotNone(one("select welcomed_at from users where username=?", member))
        r = app.test_client().post("/login", data={"username": member, "password": PASSWORD})
        self.assertNotEqual(r.headers["Location"], "/welcome")


# ---------------------------------------------------------------- personal databases

class PersonalDatabases(LabSettingsCase):
    def create_inventory(self, client, audience):
        label = uniq("Mine")
        client.post("/inventory/new", data={"preset": "custom", "label": label, "audience": audience})
        return one("select key from inventory_modules where label=?", label)

    def test_a_personal_database_is_seen_by_its_owner_and_admins_only(self):
        key = self.create_inventory(self.m, "me")
        self.assertEqual(one("select private_to from inventory_modules where key=?", key), self.member)
        self.assertEqual(self.m.get(f"/inventory/{key}").status_code, 200)
        self.assertEqual(self.a.get(f"/inventory/{key}").status_code, 200)
        self.assertEqual(self.o.get(f"/inventory/{key}").status_code, 404)
        self.assertIn(f"/inventory/{key}", self.get_ok(self.m, "/home"))
        self.assertNotIn(f"/inventory/{key}", self.get_ok(self.o, "/home"))
        self.assertNotIn(f"/inventory/{key}", self.get_ok(self.o, "/organisms/"))

    def test_by_default_members_cannot_add_lab_databases(self):
        self.set("members_share_databases", "off")
        key = self.create_inventory(self.m, "lab")    # asked for the lab
        self.assertEqual(one("select private_to from inventory_modules where key=?", key), self.member)
        key = self.create_inventory(self.a, "lab")    # an admin may
        self.assertEqual(one("select private_to from inventory_modules where key=?", key), "")

    def test_no_choice_means_each_roles_default(self):
        label = uniq("Plain")
        self.a.post("/inventory/new", data={"preset": "custom", "label": label})
        self.assertEqual(one("select private_to from inventory_modules where label=?", label), "")
        label = uniq("Plain")
        self.m.post("/inventory/new", data={"preset": "custom", "label": label})
        self.assertEqual(one("select private_to from inventory_modules where label=?", label), self.member)

    def test_a_new_lab_database_is_announced(self):
        key = self.create_inventory(self.a, "lab")
        label = one("select label from inventory_modules where key=?", key)
        self.assertTrue([n for n in notes_for(self.member, "lab") if label in n[0]])

    def test_members_can_be_stopped_from_adding_databases(self):
        self.set("members_create_databases", "off")
        r = self.post(self.m, "/inventory/new", data={"preset": "custom", "label": uniq("No"), "audience": "me"})
        self.assertFlash(r, "turned off adding databases", "error")
        # The sidebar's entry, not the words: What's new may mention it.
        self.assertNotIn('data-label="Add database"', self.get_ok(self.m, "/home"))
        self.assertIsNotNone(self.create_inventory(self.a, "me"))

    def test_sharing_with_the_lab(self):
        key = self.create_inventory(self.m, "me")
        self.set("members_share_databases", "off")
        r = self.post(self.m, f"/databases/inventory/{key}/audience", data={"to": "lab"})
        self.assertFlash(r, "Only a lab admin", "error")
        self.set("members_share_databases", "on")
        self.post(self.m, f"/databases/inventory/{key}/audience", data={"to": "lab"})
        self.assertEqual(one("select private_to from inventory_modules where key=?", key), "")
        self.assertEqual(self.o.get(f"/inventory/{key}").status_code, 200)

    def test_search_leaves_out_other_peoples_personal_databases(self):
        key = self.create_inventory(self.m, "me")
        secret = uniq("Secretab")
        self.make_item(self.m, key, secret)
        labels = lambda c: [r["label"] for r in c.get(f"/search?q={secret}").get_json()["results"]]
        self.assertTrue(any(secret in l for l in labels(self.m)))
        self.assertFalse(any(secret in l for l in labels(self.o)))

    def test_nobody_else_can_share_someone_elses_database(self):
        key = self.create_inventory(self.m, "me")
        self.assertEqual(self.o.post(f"/databases/inventory/{key}/audience", data={"to": "lab"}).status_code, 404)

    def test_personal_stock_and_organism_databases_too(self):
        label = uniq("MyFlies")
        self.m.post("/stocks/new", data={"kind": "fly", "label": label, "audience": "me"})
        key = one("select key from stock_modules where label=?", label)
        self.assertEqual(self.o.get(f"/stocks/{key}").status_code, 404)
        self.assertEqual(self.m.get(f"/stocks/{key}").status_code, 200)
        label = uniq("MyFrogs")
        self.m.post("/organisms/new", data={"preset_key": "custom", "label": label, "audience": "me",
                                            "organism_noun": "frog"})
        key = one("select key from organism_modules where label=?", label)
        self.assertEqual(self.o.get(f"/organisms/{key}").status_code, 404)


# ---------------------------------------------------------------- notifications

class Notifications(AppTestCase):
    def test_moving_someones_mouse_tells_them_once_for_several(self):
        colony = self.make_colony(self.m, self.member, n_mice=3)
        dest = self.make_cage(self.a, uniq("C"))
        self.a.post("/colony/mice/bulk-update", data={"field": "cage_id",
                                                      "value": one("select cage_id from mouse_cages where id=?", dest),
                                                      "selected_ids": colony["mice"]})
        moved = [n for n in notes_for(self.member, "transfer") if "moved 3 of your mice" in n[0]]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0][3], self.admin)          # who did it
        self.assertIn("scope=all", moved[0][2])             # a link to them

    def test_putting_a_mouse_in_someone_elses_cage_tells_the_cage_owner(self):
        cage = self.make_cage(self.o, uniq("C"), owner=self.other, purpose="Breeder")
        code = one("select cage_id from mouse_cages where id=?", cage)
        mouse = self.make_mouse(self.m, self.member)
        before = len(notes_for(self.member))
        self.m.post(f"/colony/mice/{mouse}/update", data={"cage_id": code}, headers=AUTOSAVE)
        self.assertTrue([n for n in notes_for(self.other, "transfer") if f"into your cage {code}" in n[0]])
        self.assertEqual(len(notes_for(self.member)), before)   # the mover is not told

    def test_giving_a_mouse_away_tells_both_sides(self):
        mouse = self.make_mouse(self.a, self.member)
        label = one("select mouse_id from mice where id=?", mouse)
        self.a.post("/colony/mice/bulk-update", data={"field": "owner", "value": self.other,
                                                      "selected_ids": [mouse]})
        self.assertTrue([n for n in notes_for(self.other, "transfer") if f"gave you mouse #{label}" in n[0]])
        self.assertTrue([n for n in notes_for(self.member, "transfer") if f"to {self.other}" in n[0]])

    def test_a_genotype_recorded_by_someone_else(self):
        mouse = self.make_mouse(self.m, self.member)
        # The sheet saves the whole row, owner and status included.
        self.a.post(f"/colony/mice/{mouse}/update", headers=AUTOSAVE,
                    data={"transgene_1": "Cre/+", "owner": self.member, "status": "experiment", "gender": "F"})
        self.assertTrue([n for n in notes_for(self.member, "genotyping") if "Cre/+" in n[0]])

    def test_clearing_the_owner_says_so(self):
        mouse = self.make_mouse(self.m, self.member)
        self.a.post(f"/colony/mice/{mouse}/update", headers=AUTOSAVE,
                    data={"owner": "", "status": "experiment", "gender": "F"})
        self.assertEqual(one("select owner from mice where id=?", mouse), "")
        cleared = [n[0] for n in notes_for(self.member, "transfer")]
        self.assertTrue(any("removed you as the owner of mouse" in t for t in cleared), cleared)
        self.assertFalse(any(t.endswith(" to ") for t in cleared), cleared)

    def test_nobody_is_told_about_their_own_changes(self):
        mouse = self.make_mouse(self.m, self.member)
        before = len(notes_for(self.member))
        self.m.post(f"/colony/mice/{mouse}/update", headers=AUTOSAVE,
                    data={"transgene_1": "Flp/+", "owner": self.member, "status": "experiment", "gender": "F"})
        self.assertEqual(len(notes_for(self.member)), before)

    def test_turned_off_categories_stay_quiet(self):
        quiet = make_user(uniq("quiet"))
        execute("update users set notify_genotyping=false where username=?", quiet)
        mouse = self.make_mouse(self.a, quiet)
        self.a.post(f"/colony/mice/{mouse}/update", headers=AUTOSAVE,
                    data={"transgene_1": "Cre/+", "owner": quiet, "status": "experiment", "gender": "F"})
        self.assertEqual(notes_for(quiet, "genotyping"), [])

    def test_picking_from_a_breeder_cage_is_told_once(self):
        colony = self.make_colony(self.m, self.member, n_mice=1, purpose="Breeder")
        self.o.post(f"/colony/mice/{colony['mice'][0]}/pick")
        picked = notes_for(self.member, "picked")
        self.assertEqual(len(picked), 1)
        self.assertIn(f"{self.other} took mouse", picked[0][0])

    def test_an_order_arriving_tells_whoever_placed_it(self):
        orders = one("select key from inventory_modules where kind='orders' and private_to='' "
                     "and share_group_id is null order by id")
        oid = self.make_item(self.m, orders, uniq("Taq"), status="requested")
        name = one("select name from inventory_items where id=?", oid)
        self.autosave(self.a, f"/inventory/{orders}/items/{oid}/update", {"status": "received"})
        got = [n for n in notes_for(self.member, "orders") if name in n[0]]
        self.assertEqual(len(got), 1)
        self.assertIn("was received", got[0][0])
        # It opens that order, not just the list.
        self.assertEqual(got[0][2], f"/inventory/{orders}?open={oid}")

    def test_a_status_change_outside_orders_is_not_an_order_notice(self):
        oid = self.make_item(self.m, "reagents", uniq("Buffer"), status="in stock")
        self.autosave(self.a, f"/inventory/reagents/items/{oid}/update", {"status": "received"})
        self.assertEqual([n for n in notes_for(self.member, "orders") if "Buffer" in n[0]], [])

    def test_flagging_a_tank_for_genotyping(self):
        tank = self.make_tank(self.m, owner=self.member)
        self.autosave(self.a, f"/zebrafish/tanks/{tank}/toggle-geno")
        self.assertTrue([n for n in notes_for(self.member, "genotyping") if "for genotyping" in n[0]])

    def test_an_organism_genotype_call(self):
        key = self.make_organism_module(self.a)
        code = uniq("A")
        self.make_animal(self.a, key, code, owner=self.member)
        self.post(self.a, f"/organisms/{key}/genotype/save", {"subject": code, "assay": "PCR", "result": "tg/+"})
        self.assertTrue([n for n in notes_for(self.member, "genotyping") if "tg/+" in n[0]])

    def test_a_rolled_back_change_sends_nothing(self):
        mouse = self.make_mouse(self.m, self.member)
        before = len(notes_for(self.other))
        with app.test_request_context("/"):
            with SessionLocal() as s:
                g.user = s.scalar(__import__("sqlalchemy").select(UserAccount).where(UserAccount.username == self.admin))
                s.get(MouseRecord, mouse).owner = self.other
                s.flush()
                s.rollback()
        self.assertEqual(len(notes_for(self.other)), before)

    def test_the_daily_genotyping_reminder_is_made_once(self):
        owner = make_user(uniq("geno"))
        self.make_mouse(self.a, owner, status="geno")
        c = client_for(owner)
        c.get("/home")
        c.get("/settings")
        with c.session_transaction() as sess:
            sess.pop("reminded_on", None)   # a new browser session the same day
        c.get("/home")
        reminders = [n for n in notes_for(owner, "genotyping") if n[0].startswith("Waiting for genotyping")]
        self.assertEqual(len(reminders), 1)
        self.assertIn("1 mouse", reminders[0][0])


class Bell(AppTestCase):
    def notify(self, username, title="Hello", link="/plasmids"):
        with SessionLocal() as s:
            notify.send(s, username, title, category="lab", link=link, actor=self.admin)
            s.commit()
        return one("select max(id) from notifications where recipient_username=?", username)

    def test_the_count_and_the_panel(self):
        person = make_user(uniq("bell"))
        c = client_for(person)
        self.assertEqual(c.get("/notifications/count").get_json()["unread"], 0)
        self.notify(person, "A plasmid box was added")
        self.assertEqual(c.get("/notifications/count").get_json()["unread"], 1)
        panel = c.get("/notifications/panel").get_data(as_text=True)
        self.assertIn("A plasmid box was added", panel)
        self.assertIn("Mark all read", panel)
        self.assertIn('data-bell-count', c.get("/home").get_data(as_text=True))

    def test_opening_one_marks_it_read_and_goes_to_its_page(self):
        person = make_user(uniq("bell"))
        nid = self.notify(person)
        r = client_for(person).get(f"/notifications/{nid}/open")
        self.assertEqual(r.headers["Location"], "/plasmids")
        self.assertEqual(one("select is_read from notifications where id=?", nid), 1)

    def test_a_link_off_the_site_is_never_followed(self):
        person = make_user(uniq("bell"))
        nid = self.notify(person, link="https://evil.example/x")
        r = client_for(person).get(f"/notifications/{nid}/open")
        self.assertEqual(r.headers["Location"], "/notifications")

    def test_someone_elses_notification_is_not_found(self):
        nid = self.notify(self.member)
        self.assertEqual(self.o.get(f"/notifications/{nid}/open").status_code, 404)

    def test_mark_all_read(self):
        person = make_user(uniq("bell"))
        self.notify(person)
        self.notify(person)
        r = client_for(person).post("/notifications/mark-read", headers={"Accept": "application/json"})
        self.assertEqual(r.get_json(), {"ok": True, "unread": 0})
        self.assertEqual(count("notifications", "recipient_username=? and is_read=false", person), 0)

    def test_the_notifications_page_filters(self):
        person = make_user(uniq("bell"))
        self.notify(person, "Lab thing")
        c = client_for(person)
        self.assertIn("Lab thing", self.get_ok(c, "/notifications"))
        self.assertNotIn("Lab thing", self.get_ok(c, "/notifications?category=orders"))

    def test_settings_switch_categories_off(self):
        person = make_user(uniq("prefs"))
        c = client_for(person)
        self.post(c, "/settings", data={"action": "notifications", "notify_transfer": "1"})
        self.assertEqual(one("select notify_genotyping from users where username=?", person), 0)
        self.assertEqual(one("select notify_transfer from users where username=?", person), 1)


if __name__ == "__main__":
    unittest.main()


class LabTimeAndDates(LabSettingsCase):
    """Lab setup's time zone, date style and genotyping day."""

    def test_the_time_zone_is_saved_and_becomes_the_processes(self):
        # The server's own zone by name, so "today" cannot move mid-test.
        zone = lab.server_timezone()
        if not lab.valid_timezone(zone):
            self.skipTest("no zone data for this computer's zone")
        self.survey(self.a, lab_timezone=zone)
        self.assertEqual(one("select value from app_settings where key='lab_timezone'"), zone)
        self.assertEqual(os.environ.get("TZ"), zone)

    def test_an_unknown_time_zone_is_refused_and_said(self):
        r = self.survey(self.a, lab_timezone="Mars/Olympus_Mons")
        self.assertIn("is not a time zone", flash_text(r))
        self.assertIn(one("select value from app_settings where key='lab_timezone'"), (None, ""))

    def test_the_date_style_is_how_dates_are_written(self):
        born = TODAY + timedelta(days=2)
        for style, text in (("iso", born.isoformat()), ("day", f"{born.day} {born:%b}"),
                            ("month", f"{born:%b} {born.day}")):
            with self.subTest(style=style):
                self.survey(self.a, date_style=style)
                r = self.post(self.a, "/colony/mice/create", {"date_of_birth": born.isoformat(), "owner": self.admin})
                self.assertIn(f"({text}", flash_text(r))

    def test_the_genotyping_day_moves_the_reminder(self):
        self.survey(self.a, genotyping_day="25")
        born = TODAY - timedelta(days=10)
        cage = self.make_cage(self.a, date_give_birth=born.isoformat())
        r = self.a.get("/calendar/events.json", query_string={"start": days_ago(30), "end": days_ahead(40)})
        genos = [i["start"][:10] for i in r.get_json()["items"] if i["id"] == f"auto-cage-{cage}-geno"]
        self.assertEqual(genos, [(born + timedelta(days=25)).isoformat()])
