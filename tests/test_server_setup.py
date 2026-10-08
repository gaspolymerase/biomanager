"""Setting up a lab server from the desktop app (app/server_setup.py).

Nothing here signs in anywhere: the command runner is replaced, and the
script it would run is checked by bash for syntax."""
from __future__ import annotations

import json
import subprocess
import time
from types import SimpleNamespace
from unittest import mock

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, ON_POSTGRES
from app import server_setup as ss  # noqa: E402
from app.app import app  # noqa: E402


ANSWERS = {"target": "cloud-tailscale", "host": "203.0.113.10", "user": "ubuntu", "port": "22", "key_path": "",
           "ts_authkey": "tskey-auth-SECRET123", "ts_hostname": "biomanager", "timezone": "Europe/London"}


class DesktopOnly(AppTestCase):
    def test_a_lab_server_has_no_setup_wizard(self):
        self.assertEqual(self.a.get("/server-setup/").status_code, 404)
        self.assertNotIn("Set up a lab server", self.get_ok(self.a, "/settings"))


class Wizard(AppTestCase):
    def setUp(self):
        super().setUp()
        app.config["LOCAL_SETUP"] = True

    def tearDown(self):
        app.config.pop("LOCAL_SETUP", None)
        ss.JOBS.clear()
        super().tearDown()

    def post_json(self, url, body):
        return self.a.post(url, data=json.dumps(body), content_type="application/json")

    def test_the_page_and_its_way_in(self):
        html = self.get_ok(self.a, "/server-setup/")
        self.assertIn("Where should the lab's BioManager run?", html)
        self.assertIn('href="/server-setup/"', self.get_ok(self.a, "/settings"))

    def test_answers_are_checked(self):
        r = self.post_json("/server-setup/preview", {"target": "cloud-domain", "host": "bad host!", "user": "",
                                                     "address": "", "acme_email": "nope", "timezone": "Mars/Base"})
        errors = " ".join(r.get_json()["errors"])
        for fragment in ("server's address", "user name", "address people will type", "email", "time zone"):
            self.assertIn(fragment, errors)

    def test_the_preview_shows_the_script_but_never_the_tailscale_key(self):
        r = self.post_json("/server-setup/preview", ANSWERS)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertNotIn("SECRET123", body["script"])
        self.assertIn("tailscale up", body["script"])
        self.assertEqual(body["runs_on"], "ubuntu@203.0.113.10")

    def test_every_target_makes_a_valid_script(self):
        for target in ss.TARGETS:
            plan = ss.Plan(target=target, host="10.0.0.5", user="ubuntu", address="bm.example.edu",
                           acme_email="a@b.co", ts_authkey="tskey-x", bring_data=True)
            r = subprocess.run(["bash", "-n"], input=ss.build_script(plan), text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, (target, r.stderr))

    def test_certificates_follow_the_choice(self):
        for target, tls in (("cloud-tailscale", "tailscale"), ("cloud-domain", "acme"), ("university", "internal")):
            self.assertIn(f"TLS={tls}", ss.build_script(ss.Plan(target=target)))

    def test_the_connection_check_reads_the_server(self):
        out = SimpleNamespace(returncode=0, stdout="Linux aarch64\nos=Ubuntu 24.04 LTS\ndocker=no\nsudo=yes\nexisting=no\n",
                              stderr="")
        with mock.patch.object(ss.subprocess, "run", return_value=out) as run:
            got = self.post_json("/server-setup/check", ANSWERS).get_json()
        self.assertEqual(run.call_args.args[0][:2], ["ssh", "-p"])
        self.assertIn("BatchMode=yes", run.call_args.args[0])
        self.assertTrue(got["ok"])
        self.assertEqual((got["system"], got["machine"], got["docker"], got["sudo"]), ("Ubuntu 24.04 LTS", "aarch64", False, True))

    def check_with(self, sudo_line, **answers):
        out = SimpleNamespace(returncode=0, stdout=f"Linux x86_64\nos=Debian GNU/Linux 12\ndocker=yes\n{sudo_line}\nexisting=no\n",
                              stderr="")
        with mock.patch.object(ss.subprocess, "run", return_value=out) as run:
            got = self.post_json("/server-setup/check", {**ANSWERS, **answers}).get_json()
        return got, run.call_args

    def test_an_account_whose_sudo_asks_for_a_password_is_asked_for_it(self):
        # A NAS's admin account: sudo works, but only with its password.
        got, _ = self.check_with("sudo=password")
        self.assertEqual((got["sudo"], got["sudo_password"]), (False, "needed"))

    def test_the_password_goes_to_sudo_as_input_never_on_a_command_line(self):
        got, call = self.check_with("sudo=password-ok", sudo_password="hunter2 x")
        self.assertEqual((got["sudo"], got["sudo_password"]), (True, "needed"))
        self.assertEqual(call.kwargs["input"], "hunter2 x\n")
        self.assertNotIn("hunter2", " ".join(call.args[0]))
        self.assertIn("sudo -S", call.args[0][-1])

    def test_a_wrong_password_says_so(self):
        got, _ = self.check_with("sudo=no", sudo_password="wrong")
        self.assertEqual((got["sudo"], got["sudo_password"]), (False, "wrong"))

    def test_the_script_hands_sudo_the_password_through_a_helper(self):
        plan = ss.Plan(target="lab-linux", host="nas.local", user="admin", address="nas.local", sudo_password="it's $ecret")
        script = ss.build_script(plan)
        self.assertIn('SUDO="sudo -A"', script)
        self.assertIn("SUDO_PASSWORD='it'\"'\"'s $ecret'", script)      # quoted for bash
        r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("$ecret", ss.build_script(plan, show_secrets=False))

    def test_a_password_with_a_line_break_is_refused(self):
        r = self.post_json("/server-setup/preview", {**ANSWERS, "sudo_password": "two\nlines"})
        self.assertIn("line break", " ".join(r.get_json()["errors"]))

    def test_the_run_never_shows_the_password_and_then_forgets_it(self):
        seen = []

        def fake_stream(job, command, stdin_text=None, stdin_file=None):
            seen.append(stdin_text or "")
            for line in ["::step::Checking the machine", "echo hunter2 x", "::value::ADDRESS=nas.local", "::done::"]:
                ss._say(job, line)
            return 0

        with mock.patch.object(ss, "_stream", fake_stream), \
                mock.patch.object(ss, "_check_from_here", return_value="yes"), \
                mock.patch.object(ss, "data_dir", return_value=__import__("pathlib").Path(__import__("tempfile").mkdtemp())):
            job_id = self.post_json("/server-setup/start", {**ANSWERS, "sudo_password": "hunter2 x"}).get_json()["job"]
            for _ in range(50):
                state = self.a.get(f"/server-setup/job/{job_id}").get_json()
                if state["status"] != "running":
                    break
                time.sleep(0.05)
        self.assertEqual(state["status"], "done", state)
        self.assertIn("hunter2 x", seen[-1])                       # the script had it…
        self.assertNotIn("hunter2", "\n".join(state["lines"]))     # …the page never shows it
        self.assertEqual(ss.JOBS[job_id].plan.sudo_password, "")    # and it isn't kept

    def test_a_refused_key_says_so(self):
        out = SimpleNamespace(returncode=255, stdout="", stderr="ubuntu@203.0.113.10: Permission denied (publickey).")
        with mock.patch.object(ss.subprocess, "run", return_value=out):
            got = self.post_json("/server-setup/check", ANSWERS).get_json()
        self.assertFalse(got["ok"])
        self.assertIn("didn't accept this key", got["error"])

    def test_a_run_reports_its_steps_and_results_and_hides_the_key(self):
        lines = ["::step::Checking the machine", "Ubuntu 24.04, aarch64", "using tskey-auth-SECRET123 now",
                 "::step::Starting BioManager", "::value::ADDRESS=biomanager.tail1234.ts.net",
                 "::value::SETUP_CODE=abcd-ef01-2345", "::done::"]

        def fake_stream(job, command, stdin_text=None, stdin_file=None):
            for line in lines:
                ss._say(job, line)
            return 0

        with mock.patch.object(ss, "_stream", fake_stream), \
                mock.patch.object(ss, "_check_from_here", return_value="yes"), \
                mock.patch.object(ss, "data_dir", return_value=__import__("pathlib").Path(__import__("tempfile").mkdtemp())):
            job_id = self.post_json("/server-setup/start", ANSWERS).get_json()["job"]
            for _ in range(50):
                state = self.a.get(f"/server-setup/job/{job_id}").get_json()
                if state["status"] != "running":
                    break
                time.sleep(0.05)
        self.assertEqual(state["status"], "done", state)
        self.assertEqual(state["steps"][:2], ["Checking the machine", "Starting BioManager"])
        self.assertEqual(state["values"]["SETUP_CODE"], "abcd-ef01-2345")
        self.assertNotIn("SECRET123", "\n".join(state["lines"]))

    def test_a_failed_run_says_why(self):
        def fake_stream(job, command, stdin_text=None, stdin_file=None):
            ss._say(job, "::fail::Docker isn't installed here.")
            return 1

        with mock.patch.object(ss, "_stream", fake_stream):
            job_id = self.post_json("/server-setup/start", ANSWERS).get_json()["job"]
            for _ in range(50):
                state = self.a.get(f"/server-setup/job/{job_id}").get_json()
                if state["status"] != "running":
                    break
                time.sleep(0.05)
        self.assertEqual(state["status"], "failed")
        self.assertIn("Docker isn't installed", state["error"])

    def test_bringing_records_exports_a_consistent_copy(self):
        if ON_POSTGRES:
            self.skipTest("the desktop app's database is SQLite")
        import sqlite3
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            files = ss._export_data(Path(tmp))
            with sqlite3.connect(files[0]) as db:
                self.assertGreater(db.execute("select count(*) from users").fetchone()[0], 0)
