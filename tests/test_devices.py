"""The devices that work on one lab, and which holds its master copy
(app/devices.py): linked desktops saying hello, handing the master copy to
one, the read-only master while it moves and once it has, giving it back,
and a desktop sharing its lab on the network."""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest import mock

from tests.base import AppTestCase, app, execute, flash_text, one, uniq
from tests.test_lab_copy import CopyCase

AUTOSAVE = {"X-Autosave": "1"}


def reset_devices() -> None:
    execute("delete from app_settings where key like 'devices:%'")


def device_state() -> dict:
    from app import devices
    from app.db import SessionLocal
    with SessionLocal() as s:
        return devices.state(s)


def put_state(value: dict) -> None:
    from app import devices
    from app.db import SessionLocal
    with SessionLocal() as s:
        devices._save(s, devices.STATE, value)
        s.commit()


class DeviceCase(CopyCase):
    def setUp(self):
        super().setUp()
        reset_devices()
        self.addCleanup(reset_devices)

    def call(self, path, key, body=None, method="POST", **kw):
        return app.test_client().open(path, method=method, json=body if method == "POST" else None,
                                      headers={"Authorization": f"Bearer {key}", **kw.pop("headers", {})}, **kw)

    def hello(self, key, **body):
        from app import devices
        body = {"role": "copy", "version": devices.version(), **body}
        return self.call("/api/devices/hello", key, body)

    def key_id(self, key) -> int:
        from app.lab_copy import key_hash
        return one("select id from lab_copy_keys where key_hash=?", key_hash(key))


# ================================================================ the master

class MasterTests(DeviceCase):

    def test_the_devices_page_says_where_the_master_copy_is(self):
        html = self.get_ok(self.a, "/settings/devices")
        self.assertIn("This server.", html)
        self.assertIn("Computers linked to this lab", html)
        self.assertNotIn("Computers linked to this lab", self.get_ok(self.m, "/settings/devices"))

    def test_a_desktop_says_hello_and_the_master_remembers_it(self):
        key = self.make_key(label=uniq("Lab iMac "))
        r = self.hello(key, role="window")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.get_json()["master"], r.get_json()["for_me"]), (True, False))
        self.assertEqual(one("select device_role from lab_copy_keys where id=?", self.key_id(key)), "window")
        self.assertIsNotNone(one("select last_seen_at from lab_copy_keys where id=?", self.key_id(key)))
        self.assertIn("in its window", self.get_ok(self.a, "/settings/devices"))

    def test_no_key_no_hello(self):
        self.assertEqual(self.call("/api/devices/hello", "bmk_nope", {}).status_code, 401)

    def test_only_an_admin_s_open_desktop_of_the_same_version_can_be_made_the_master(self):
        from tests.test_lab_copy import set_permission
        set_permission(True)
        member_key = self.make_key(self.m, uniq("Member laptop "))
        self.hello(member_key)
        old_key = self.make_key(label=uniq("Old iMac "))
        self.hello(old_key, version="0.1.0")
        quiet_key = self.make_key(label=uniq("Quiet iMac "))
        for k in (member_key, old_key, quiet_key):
            r = self.post(self.a, f"/settings/devices/{self.key_id(k)}/make-master")
            self.assertIn("can't take the master copy over", flash_text(r))
        self.assertEqual(device_state(), {})
        html = self.get_ok(self.a, "/settings/devices")
        self.assertIn("only a computer with an admin", html)
        self.assertIn("update both to the same version", html)

    def test_handing_over_freezes_the_master_and_then_sends_everyone_on(self):
        key = self.make_key(label=uniq("Bench PC "))
        self.hello(key)
        kid = self.key_id(key)
        self.post(self.a, f"/settings/devices/{kid}/make-master")
        self.assertEqual(device_state()["phase"], "asked")
        answer = self.hello(key).get_json()
        self.assertEqual((answer["for_me"], answer["phase"]), (True, "asked"))
        # Asked, it still takes changes.
        cage = self.make_cage(self.a)
        self.assertEqual(self.autosave(self.a, f"/colony/cages/{cage}/update", {"notes": "before"}).status_code, 200)

        self.assertEqual(self.call("/api/devices/handover/freeze", key).status_code, 200)
        r = self.autosave(self.a, f"/colony/cages/{cage}/update", {"notes": "lost?"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Read only", r.get_json()["error"])
        r = self.post(self.a, "/colony/cages/create", {"count": 1})
        self.assertIn("Read only", flash_text(r))
        self.assertEqual(one("select notes from mouse_cages where id=?", cage), "before")
        self.assertIn("Read only: the lab", self.get_ok(self.m, "/home"))
        # The last copy is never made to wait for the copies' throttle.
        self.fresh(key)
        self.assertEqual(self.api("/api/lab-copy/snapshot", key).status_code, 200)
        self.assertEqual(self.api("/api/lab-copy/snapshot", key).status_code, 200)

        url = "http://192.168.1.20:5870"
        self.assertEqual(self.call("/api/devices/handover/done", key, {"url": url}).status_code, 200)
        self.assertEqual((device_state()["phase"], device_state()["url"]), ("away", url))
        home = self.get_ok(self.m, "/home")
        self.assertIn("now on", home)
        self.assertIn(f'href="{url}"', home)
        self.assertFalse(self.hello(key).get_json()["master"])
        self.assertEqual(self.autosave(self.a, f"/colony/cages/{cage}/update", {"notes": "x"}).status_code, 409)

    def test_only_the_desktop_being_handed_it_can_freeze_the_master(self):
        key, other = self.make_key(label=uniq("A ")), self.make_key(label=uniq("B "))
        self.hello(key)
        self.post(self.a, f"/settings/devices/{self.key_id(key)}/make-master")
        self.assertEqual(self.call("/api/devices/handover/freeze", other).status_code, 409)
        self.assertEqual(device_state()["phase"], "asked")

    def test_a_desktop_that_fails_gives_the_master_back_at_once(self):
        key = self.make_key(label=uniq("Flaky "))
        self.hello(key)
        self.post(self.a, f"/settings/devices/{self.key_id(key)}/make-master")
        self.call("/api/devices/handover/freeze", key)
        self.call("/api/devices/handover/abort", key, {"error": "disk full"})
        self.assertEqual(device_state().get("phase", ""), "")
        self.assertIn("disk full", self.get_ok(self.a, "/home"))
        self.assertNotIn("disk full", self.get_ok(self.m, "/home"))     # admins only
        cage = self.make_cage(self.a)
        self.assertEqual(self.autosave(self.a, f"/colony/cages/{cage}/update", {"notes": "ok"}).status_code, 200)

    def test_an_admin_can_cancel_or_take_the_role_back_from_a_lost_computer(self):
        key = self.make_key(label=uniq("Lost laptop "))
        self.hello(key)
        self.post(self.a, f"/settings/devices/{self.key_id(key)}/make-master")
        self.assertIn("Hand-over cancelled", flash_text(self.post(self.a, "/settings/devices/cancel")))
        put_state({"phase": "away", "key_id": self.key_id(key), "label": "Lost laptop", "url": "http://x"})
        self.assertEqual(self.post(self.m, "/settings/devices/cancel").status_code, 403)
        r = self.post(self.a, "/settings/devices/cancel")
        self.assertIn("master copy again", flash_text(r))
        self.assertEqual(device_state(), {})


# ============================================================== giving it back

class GiveBackTests(DeviceCase):

    def setUp(self):
        super().setUp()
        self.key = self.make_key(label=uniq("Holder "))
        self.other = self.make_key(label=uniq("Other "))
        self.cage = self.make_cage(self.a, notes="on the server")
        put_state({"phase": "away", "key_id": self.key_id(self.key), "label": "Holder", "url": "http://10.0.0.9:5870"})

    def database_file(self, change=None) -> Path:
        """A whole copy of this lab, as the desktop would send it back."""
        from app import lab_copy
        tmp = Path(tempfile.mkdtemp()) / "back.db"
        with app.app_context():
            lab_copy.write_snapshot(tmp)
        if change:
            with sqlite3.connect(tmp) as con:
                change(con)
        return tmp

    def send(self, path: Path, **headers):
        from app import devices, lab_copy
        with app.app_context():
            defaults = {"X-BioManager-SHA256": lab_copy._sha256(path), "X-BioManager-Version": devices.version()}
        return app.test_client().post("/api/devices/return", data=path.read_bytes(),
                                      headers={"Authorization": f"Bearer {self.key}", **defaults, **headers})

    def wait_back(self):
        for _ in range(100):
            if device_state().get("phase") != "receiving":
                return
            time.sleep(0.1)
        self.fail("still receiving")

    def test_the_master_copy_comes_back_with_what_was_changed_on_the_desktop(self):
        cage = self.cage
        execute("insert into app_settings (key, value) values ('devices:mine', 'kept') "
                "on conflict (key) do update set value = 'kept'")
        changed = uniq("changed on the desktop ")

        def change(con):
            con.execute("update mouse_cages set notes=? where id=?", (changed, cage))
            con.execute("delete from app_settings where key like 'devices:%'")
            con.execute("insert into app_settings (key, value) values ('devices:mine', 'theirs')")
        r = self.send(self.database_file(change))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.wait_back()
        self.assertEqual(device_state(), {})
        self.assertEqual(one("select notes from mouse_cages where id=?", cage), changed)
        self.assertEqual(one("select value from app_settings where key='devices:mine'"), "kept")
        from app.paths import data_dir
        self.assertTrue(list((data_dir() / "backups").glob("before-taking-back-*.db")))
        # The master again: it takes changes.
        self.assertEqual(self.autosave(self.a, f"/colony/cages/{cage}/update", {"notes": "n"}).status_code, 200)
        # New records get new ids, never one the copy already used.
        new_cage = self.make_cage(self.a)
        self.assertGreater(new_cage, cage)

    def test_a_damaged_or_different_version_database_is_refused(self):
        path = self.database_file()
        self.assertEqual(self.send(path, **{"X-BioManager-SHA256": "0" * 64}).status_code, 400)
        r = self.send(path, **{"X-BioManager-Version": "0.0.1"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("same version", r.get_json()["error"])
        self.assertEqual(device_state()["phase"], "away")

    def test_only_the_holder_may_give_it_back(self):
        r = app.test_client().post("/api/devices/return", data=b"x", headers={"Authorization": f"Bearer {self.other}"})
        self.assertEqual(r.status_code, 409)

    def test_new_uploaded_files_come_back_too(self):
        from app.paths import uploads_dir
        name = f"{uniq('gel')}.png"
        listing = self.call("/api/devices/return/files", self.key, method="GET").get_json()["files"]
        self.assertNotIn(name, [f["path"] for f in listing])
        r = self.call(f"/api/devices/return/files/{name}", self.key, method="PUT", data=b"PNG")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((uploads_dir() / name).read_bytes(), b"PNG")
        bad = self.call("/api/devices/return/files/..%2Fescape.txt", self.key, method="PUT", data=b"x")
        self.assertIn(bad.status_code, (400, 404))


# ================================================================ the desktop

class DesktopDeviceTests(DeviceCase):

    def setUp(self):
        super().setUp()
        app.config["LOCAL_SETUP"] = True
        self.addCleanup(app.config.pop, "LOCAL_SETUP", None)
        execute("delete from app_settings where key like 'lab_copy_%'")
        self.server = f"https://{uniq('lab')}.example.ts.net"
        self.key = self.make_key()
        with mock.patch("app.lab_copy.threading.Thread"):
            self.post(self.a, "/lab-copy/configure", data={"server": self.server, "key": self.key})
        self.prefs = {}
        patcher = mock.patch("app.devices._prefs", lambda: dict(self.prefs))
        patcher.start()
        self.addCleanup(patcher.stop)
        saver = mock.patch("app.devices._save_prefs", lambda **kw: self.prefs.update(kw))
        saver.start()
        self.addCleanup(saver.stop)

    def test_taking_over_freezes_the_master_opens_its_copy_and_shares_it(self):
        from app import devices
        calls = []

        def fake_call(server, key, path, body=None, method="POST", timeout=60):
            calls.append((path, body))
            return {"ok": True}
        newest = [{"path": "/tmp/biomanager-x.db"}]
        with mock.patch.object(devices, "_call", fake_call), \
                mock.patch("app.lab_copy.pull", return_value={"ok": True}), \
                mock.patch("app.lab_copy.list_copies", return_value=newest), \
                mock.patch.object(devices, "install_copy", return_value=Path("/tmp/backup.db")) as install, \
                mock.patch.object(devices, "start_sharing", return_value="http://10.1.2.3:5870"):
            with app.app_context():
                self.assertTrue(devices.take_over(app, {"server": self.server, "key": self.key}))
        self.assertEqual([c[0] for c in calls], ["/api/devices/handover/freeze", "/api/devices/handover/done"])
        self.assertEqual(calls[1][1], {"url": "http://10.1.2.3:5870"})
        self.assertEqual(install.call_args[0][0], Path("/tmp/biomanager-x.db"))
        from app.db import SessionLocal
        with SessionLocal() as s:
            self.assertEqual(devices.came_from(s)["url"], self.server)
            self.assertTrue(devices.sharing(s))

    def test_a_failed_take_over_tells_the_master(self):
        from app import devices
        calls = []

        def fake_call(server, key, path, body=None, method="POST", timeout=60):
            calls.append(path)
            return {"ok": True}
        with mock.patch.object(devices, "_call", fake_call), \
                mock.patch("app.lab_copy.pull", return_value={"ok": False, "error": "no network"}):
            with app.app_context():
                self.assertFalse(devices.take_over(app, {"server": self.server, "key": self.key}))
        self.assertEqual(calls, ["/api/devices/handover/freeze", "/api/devices/handover/abort"])
        from app.db import SessionLocal
        with SessionLocal() as s:
            self.assertFalse(devices.sharing(s))
            self.assertIn("no network", devices._load(s, devices.LAST_HELLO)["error"])

    def test_hello_takes_over_when_it_is_handed_the_master(self):
        from app import devices
        with mock.patch.object(devices, "_call", return_value={"ok": True, "for_me": True, "phase": "asked"}), \
                mock.patch.object(devices, "take_over") as take:
            with app.app_context():
                devices.say_hello(app)
        take.assert_called_once()

    def test_open_the_lab_in_this_window_and_come_back(self):
        r = self.a.post("/settings/devices/window", data={"to": "lab"})
        self.assertGoesTo(r, self.server + "/")
        self.assertEqual(self.prefs["window_url"], self.server)
        self.post(self.a, "/settings/devices/window", data={"to": "here"})
        self.assertEqual(self.prefs["window_url"], "")

    def test_sharing_on_the_network_and_the_pages_it_never_offers_there(self):
        from app import devices
        with mock.patch.object(devices, "start_sharing", return_value="http://10.9.8.7:5870"):
            r = self.post(self.a, "/settings/devices/share", data={"on": "1"})
        self.assertIn("http://10.9.8.7:5870", flash_text(r))
        from app.db import SessionLocal
        with SessionLocal() as s:
            self.assertTrue(devices.sharing(s))
        lan = {"biomanager.lan": "1"}
        for path in ("/lab-copy/status", "/server-setup/", "/settings/devices/share"):
            got = self.a.get(path, environ_base=lan) if path != "/settings/devices/share" \
                else self.a.post(path, data={"on": "0"}, environ_base=lan)
            self.assertEqual(got.status_code, 404, path)
        self.assertNotIn("This computer</h2>", self.a.get("/settings/devices", environ_base=lan).get_data(as_text=True))
        with mock.patch.object(devices, "stop_sharing"):
            self.post(self.a, "/settings/devices/share", data={"on": "0"})
        with SessionLocal() as s:
            self.assertFalse(devices.sharing(s))

    def test_giving_back_sends_the_database_and_files_then_opens_the_lab_there(self):
        from app import devices
        from app.db import SessionLocal
        with SessionLocal() as s:
            devices._save(s, devices.CAME_FROM, {"url": self.server, "label": "the server"})
            devices._settings()[1](s, devices.SHARING, "1")
            s.commit()
        sent = []

        def fake_call(server, key, path, body=None, method="POST", timeout=60):
            if path.endswith("/return/files"):
                return {"ok": True, "files": []}
            return {"ok": True, "master": True}

        def fake_send(server, key, path, file, headers, method="POST", timeout=600):
            sent.append((path, headers))
            return {"ok": True}
        with mock.patch.object(devices, "_call", fake_call), mock.patch.object(devices, "_send", fake_send), \
                mock.patch.object(devices, "stop_sharing"):
            with app.app_context():
                result = devices.give_back(app)
        self.assertTrue(result["ok"], result)
        self.assertEqual(sent[-1][0], "/api/devices/return")
        self.assertIn("X-BioManager-SHA256", sent[-1][1])
        with SessionLocal() as s:
            self.assertEqual(devices.phase(s), "away")
            self.assertFalse(devices.sharing(s))
        self.assertEqual(self.prefs["window_url"], self.server)

    def test_the_desktop_page_offers_sharing_and_its_window(self):
        html = self.get_ok(self.a, "/settings/devices")
        self.assertIn("This computer, for itself.", html)
        self.assertIn("Share this lab on the network", html)
        self.assertIn("Open the lab in this window", html)

    def test_after_giving_back_it_can_go_back_to_its_own_lab(self):
        from app import devices
        from app.db import SessionLocal
        backup = Path(tempfile.mkdtemp()) / "own.db"
        backup.write_bytes(b"")
        with SessionLocal() as s:
            devices._save(s, devices.STATE, {"phase": "away", "label": "the server", "url": self.server,
                                             "own_backup": str(backup)})
            s.commit()
        html = self.get_ok(self.a, "/settings/devices")
        self.assertIn("Go back to this computer", html)
        self.assertNotIn("the other computer is lost", html)
        with mock.patch.object(devices, "load_lab") as load, mock.patch.object(devices, "keep_a_copy"):
            r = self.a.post("/settings/devices/own-again")
        load.assert_called_once_with(backup)
        self.assertIn("/login", r.headers["Location"])
        self.assertEqual(device_state(), {})
