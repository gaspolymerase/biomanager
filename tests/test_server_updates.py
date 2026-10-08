"""A lab server hearing of a newer BioManager, and Update now
(app/server_updates.py, deploy/host/update.sh)."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import server_updates
from app.app import app
from app.db import SessionLocal
from tests.base import AppTestCase, execute, one

ROOT = Path(__file__).resolve().parent.parent
APPLIES = server_updates.applies          # before UpdateCase stands in for it
RELEASE = {"version": "1.2.4", "page": "https://github.com/gaspolymerase/biomanager/releases/tag/v1.2.4",
           "notes": "New in 1.2.4\n\nA calmer Settings window.", "published": "2026-10-20"}


class UpdateCase(AppTestCase):
    def setUp(self):
        super().setUp()
        execute("delete from app_settings where key like 'updates:%'")
        execute("delete from notifications where title like ?", "BioManager % is available")
        self.control = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.control, True)
        for patch in (mock.patch.object(server_updates, "current", return_value="1.2.3"),
                      mock.patch.object(server_updates, "applies", return_value=True),
                      mock.patch.dict(os.environ, {"BIOMANAGER_CONTROL_DIR": str(self.control)})):
            patch.start()
            self.addCleanup(patch.stop)

    def check(self, release=RELEASE, manual=False):
        with app.test_request_context(), SessionLocal() as s:
            return server_updates.check(s, manual=manual, fetcher=lambda: dict(release))

    def told(self, who):
        return one("select count(*) from notifications where recipient_username=? and title like ?",
                   who, "BioManager % is available")

    def listening(self):
        (self.control / server_updates.LISTENING).write_text("yes", encoding="utf-8")


class TheDailyCheckTests(UpdateCase):
    def test_a_newer_release_is_kept_and_each_admin_told_once(self):
        self.assertEqual(self.check()["state"], "newer")
        self.assertEqual(self.told(self.admin), 1)
        self.assertEqual(self.told(self.member), 0)
        self.check(manual=True)
        self.assertEqual(self.told(self.admin), 1)          # the same version: not again

    def test_once_a_day(self):
        self.check()
        self.assertEqual(self.check()["state"], "quiet")
        self.assertEqual(self.check(manual=True)["state"], "newer")    # Check now always asks

    def test_the_same_version_is_not_news(self):
        self.assertEqual(self.check(dict(RELEASE, version="1.2.3"))["state"], "current")
        self.assertEqual(self.told(self.admin), 0)

    def test_switched_off_it_does_not_ask(self):
        self.assertTrue(self.autosave(self.a, "/settings/updates", {"action": "switch"}).get_json()["ok"])
        self.assertEqual(one("select value from app_settings where key='updates:check'"), "off")
        self.assertEqual(self.check()["state"], "quiet")

    def test_github_out_of_reach_is_not_an_error_page(self):
        def down():
            raise OSError("offline")
        with app.test_request_context(), SessionLocal() as s:
            self.assertEqual(server_updates.check(s, fetcher=down)["state"], "error")

    def test_the_desktop_app_has_none_of_it(self):
        before = app.config.get("LOCAL_SETUP")
        app.config["LOCAL_SETUP"] = True
        try:
            with app.test_request_context():
                self.assertFalse(APPLIES())
        finally:
            app.config["LOCAL_SETUP"] = before


class SettingsTests(UpdateCase):
    def test_admins_see_the_new_version_and_update_now(self):
        self.check()
        self.listening()
        html = self.get_ok(self.a, "/settings")
        self.assertIn("This server runs BioManager 1.2.3", html)
        self.assertIn("BioManager 1.2.4 is available.", html)
        self.assertIn("A calmer Settings window.", html)
        self.assertIn('value="install"', html)
        self.assertIn('set-link-badge', html)

    def test_without_the_updater_it_says_how_to_set_it_up(self):
        self.check()
        html = self.get_ok(self.a, "/settings")
        self.assertNotIn('value="install"', html)
        self.assertIn("sudo host/install.sh", html)

    def test_members_see_no_updates(self):
        self.check()
        html = self.get_ok(self.m, "/settings")
        self.assertNotIn('id="updates"', html)
        self.listening()
        self.assertNotEqual(self.m.post("/settings/updates", data={"action": "install"}).status_code, 200)
        self.assertFalse((self.control / server_updates.REQUEST).exists())
        self.assertNotEqual(self.m.get("/settings/updates/status").status_code, 200)


class UpdateNowTests(UpdateCase):
    def test_it_leaves_a_request_for_the_machine(self):
        self.check()
        self.listening()
        r = self.a.post("/settings/updates", data={"action": "install"})
        self.assertTrue(r.headers["Location"].endswith("/settings#updates"))
        asked = json.loads((self.control / server_updates.REQUEST).read_text(encoding="utf-8"))
        self.assertEqual((asked["by"], asked["version"]), (self.admin, "1.2.4"))
        self.assertEqual(self.a.get("/settings/updates/status").get_json()["progress"], {"state": "asked"})

    def test_a_request_nobody_takes_says_so_and_can_be_made_again(self):
        self.check()
        self.listening()
        self.a.post("/settings/updates", data={"action": "install"})
        request = self.control / server_updates.REQUEST
        os.utime(request, (0, 0))
        progress = self.a.get("/settings/updates/status").get_json()["progress"]
        self.assertEqual(progress["state"], "failed")
        self.assertIn("biomanager-update.path", progress["detail"])
        self.a.post("/settings/updates", data={"action": "install"})
        self.assertEqual(self.a.get("/settings/updates/status").get_json()["progress"], {"state": "asked"})

    def test_nothing_to_ask_for_without_a_newer_version_or_an_updater(self):
        self.a.post("/settings/updates", data={"action": "install"})
        self.check()
        self.a.post("/settings/updates", data={"action": "install"})       # nobody listening
        self.assertFalse((self.control / server_updates.REQUEST).exists())

    def test_the_page_follows_what_the_machine_writes(self):
        (self.control / server_updates.STATUS).write_text(json.dumps(
            {"state": "running", "step": "backup", "version": "1.2.4", "from": "1.2.3"}), encoding="utf-8")
        body = self.a.get("/settings/updates/status").get_json()
        self.assertEqual((body["current"], body["progress"]["step"]), ("1.2.3", "backup"))


# ---------------------------------------------------------------- host/update.sh

STUB_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$STUB_LOG"
case "$1 $2" in
  "inspect -f") echo healthy ;;
  "compose ps") echo app-container ;;
esac
exit 0
"""
STUB_CURL = """#!/usr/bin/env bash
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out=$2; shift 2 ;; -H|-m|--retry|-d) shift 2 ;; -*) shift ;; *) url=$1; shift ;; esac
done
echo "curl $url" >> "$STUB_LOG"
case "$url" in
  *releases/latest) cp "$STUB_DIR/release.json" "$out" ;;
  *biomanager-server.tar.gz) cp "$STUB_DIR/server.tar.gz" "$out" ;;
  *biomanager-image-*) cp "$STUB_DIR/image.tar.gz" "$out" ;;
  *) exit 22 ;;
esac
"""


def _bundle(version: str) -> bytes:
    """A server bundle whose scripts only say they ran."""
    files = {"Biomanager/deploy/VERSION": f"{version}\n",
             "Biomanager/deploy/compose.yaml": "name: biomanager\n",
             "Biomanager/deploy/host/load-image.sh": 'echo "load-image $1" >> "$STUB_LOG"\n',
             "Biomanager/deploy/host/install.sh": 'echo "install" >> "$STUB_LOG"\n',
             "Biomanager/deploy/host/update.sh": "echo new\n"}
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


@unittest.skipIf(sys.platform.startswith("win") or not shutil.which("bash") or not shutil.which("sha256sum")
                 or not shutil.which("flock") or not shutil.which("python3"), "needs a Linux shell")
class UpdateScriptTests(unittest.TestCase):
    """update.sh against stand-ins for docker and curl."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.deploy = self.tmp / "Biomanager" / "deploy"
        (self.deploy / "host").mkdir(parents=True)
        (self.deploy / "control").mkdir()
        shutil.copy(ROOT / "deploy/host/update.sh", self.deploy / "host/update.sh")
        (self.deploy / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        (self.deploy / ".env").write_text("DOMAIN=lab.example\n", encoding="utf-8")
        (self.deploy / "control/update-request").write_text("{}", encoding="utf-8")
        self.stubs = self.tmp / "stubs"
        self.bin = self.tmp / "bin"
        self.stubs.mkdir()
        self.bin.mkdir()
        for name, text in (("docker", STUB_DOCKER), ("curl", STUB_CURL)):
            (self.bin / name).write_text(text, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        self.log = self.tmp / "log"
        self.log.write_text("", encoding="utf-8")

    def release(self, version="1.2.4", bundle=None, image=b"image", wrong_sum=False):
        bundle = bundle if bundle is not None else _bundle(version)
        (self.stubs / "server.tar.gz").write_bytes(bundle)
        (self.stubs / "image.tar.gz").write_bytes(image)
        digest = lambda data: "sha256:" + hashlib.sha256(data).hexdigest()  # noqa: E731
        assets = [{"name": "biomanager-server.tar.gz", "browser_download_url": "https://x/biomanager-server.tar.gz",
                   "digest": "sha256:" + "0" * 64 if wrong_sum else digest(bundle)}]
        assets += [{"name": f"biomanager-image-{arch}.tar.gz", "digest": digest(image),
                    "browser_download_url": f"https://x/biomanager-image-{arch}.tar.gz"} for arch in ("amd64", "arm64")]
        (self.stubs / "release.json").write_text(json.dumps({"tag_name": f"v{version}", "assets": assets}),
                                                 encoding="utf-8")

    def run_it(self):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", STUB_LOG=str(self.log),
                   STUB_DIR=str(self.stubs), BIOMANAGER_UPDATE_TEST="1")
        result = subprocess.run(["bash", str(self.deploy / "host/update.sh")], env=env, capture_output=True,
                                text=True, timeout=60)
        status = json.loads((self.deploy / "control/update-status.json").read_text(encoding="utf-8"))
        return result, status, self.log.read_text(encoding="utf-8")

    def test_it_backs_up_checks_loads_unpacks_and_restarts(self):
        self.release()
        result, status, log = self.run_it()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((status["state"], status["version"], status["from"]), ("done", "1.2.4", "1.2.3"))
        lines = log.splitlines()
        at = lambda text: next(i for i, line in enumerate(lines) if text in line)  # noqa: E731
        self.assertLess(at("exec -T backup backup.sh"), at("biomanager-server.tar.gz"))
        self.assertLess(at("load-image"), at("up -d --build"))
        self.assertLess(at("up -d --build"), lines.index("install"))
        self.assertEqual((self.deploy / "VERSION").read_text(encoding="utf-8").strip(), "1.2.4")
        self.assertEqual((self.deploy / ".env").read_text(encoding="utf-8"), "DOMAIN=lab.example\n")
        self.assertFalse((self.deploy / "control/update-request").exists())

    def test_a_file_that_does_not_match_its_checksum_changes_nothing(self):
        self.release(wrong_sum=True)
        result, status, log = self.run_it()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((status["state"], status["step"]), ("failed", "download"))
        self.assertIn("checksum", status["detail"])
        self.assertNotIn("up -d", log)
        self.assertEqual((self.deploy / "VERSION").read_text(encoding="utf-8").strip(), "1.2.3")

    def test_nothing_newer_nothing_done(self):
        self.release(version="1.2.3")
        result, status, log = self.run_it()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status["state"], "current")
        self.assertNotIn("docker", log)

    def test_a_release_candidate_is_updated_to_its_release(self):
        (self.deploy / "VERSION").write_text("1.2.4-rc.1\n", encoding="utf-8")
        self.release(version="1.2.4")
        _result, status, _log = self.run_it()
        self.assertEqual(status["state"], "done")

    def test_a_pinned_image_is_left_alone(self):
        (self.deploy / ".env").write_text("BIOMANAGER_IMAGE=ghcr.io/x:1.0\n", encoding="utf-8")
        self.release()
        _result, status, log = self.run_it()
        self.assertEqual(status["state"], "failed")
        self.assertIn("BIOMANAGER_IMAGE", status["detail"])
        self.assertNotIn("curl", log)


class DeployFilesTests(unittest.TestCase):
    def test_the_app_can_reach_the_control_folder_and_install_listens(self):
        compose = (ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")
        self.assertIn("./control:/control", compose)
        install = (ROOT / "deploy/host/install.sh").read_text(encoding="utf-8")
        self.assertIn("biomanager-update.path", install)
        self.assertIn("chmod 1733", install)
        unit = (ROOT / "deploy/host/biomanager-update.path").read_text(encoding="utf-8")
        self.assertIn("deploy/control/update-request", unit)

    def test_the_scripts_parse(self):
        if not shutil.which("bash"):
            self.skipTest("no bash")
        for name in ("update.sh", "install.sh", "maintenance.sh"):
            with self.subTest(name=name):
                self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / "deploy/host" / name)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
