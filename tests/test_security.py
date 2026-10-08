"""Running on a network: cross-site requests, sign-in, sign-up approval,
sessions, uploads, the signing key and the production entry points
(app/security.py)."""
import html as html_lib

from tests.base import *  # noqa: F401,F403
from tests.base import AUTOSAVE, AppTestCase, count, flashes, location, make_user, one, uniq, user_id

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Response
from werkzeug.security import generate_password_hash

from app import security, services
from app.app import app
from app.db import SessionLocal
from app.models import UserAccount

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery"


def make_user_with_password(role="member", password=PASSWORD, **fields) -> str:
    username = uniq("pw")
    with SessionLocal() as s:
        s.add(UserAccount(username=username, password_hash=generate_password_hash(password), role=role, **fields))
        s.commit()
    return username


def sign_in(username, password=PASSWORD, next_url=None):
    c = app.test_client()
    url = "/login" + (f"?next={next_url}" if next_url else "")
    return c, c.post(url, data={"username": username, "password": password})


class CrossSiteRequests(AppTestCase):
    def create_cage(self, headers):
        code = uniq("X")
        r = self.m.post("/colony/cages/create", data={"cage_id": code}, headers=headers)
        return r, count("mouse_cages", "cage_id=?", code)

    def test_a_post_from_another_site_is_refused(self):
        r, made = self.create_cage({"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(made, 0)

    def test_a_browser_saying_cross_site_is_refused(self):
        for site in ("cross-site", "same-site"):
            r, made = self.create_cage({"Sec-Fetch-Site": site})
            self.assertEqual((r.status_code, made), (403, 0), site)

    def test_a_referer_from_another_site_is_refused(self):
        r, made = self.create_cage({"Referer": "https://evil.example/page"})
        self.assertEqual((r.status_code, made), (403, 0))

    def test_an_opaque_origin_is_refused(self):
        r, made = self.create_cage({"Origin": "null"})
        self.assertEqual((r.status_code, made), (403, 0))

    def test_the_same_site_is_let_through(self):
        for headers in ({"Origin": "http://localhost"}, {"Sec-Fetch-Site": "same-origin"},
                        {"Referer": "http://localhost/colony"}, {}):
            r, made = self.create_cage(headers)
            self.assertEqual(made, 1, headers)

    def test_sec_fetch_site_wins_over_a_mismatched_host(self):
        """Behind a proxy that rewrites Host, a modern browser still gets in."""
        r, made = self.create_cage({"Sec-Fetch-Site": "same-origin", "Origin": "https://lab.example.edu"})
        self.assertEqual(made, 1)

    def test_a_trusted_origin_is_let_through(self):
        with mock.patch.dict(os.environ, {"BIOMANAGER_TRUSTED_ORIGINS": "https://lab.example.edu"}):
            r, made = self.create_cage({"Origin": "https://lab.example.edu", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(made, 1)

    def test_a_refused_autosave_gets_json(self):
        r = self.m.post("/colony/cages/create", data={"cage_id": uniq("X")},
                        headers={**AUTOSAVE, "Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.get_json()["ok"])

    def test_reading_from_another_site_is_not_affected(self):
        self.assertEqual(self.m.get("/colony", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 200)


class SignIn(AppTestCase):
    def tearDown(self):
        security.login_throttle.reset()

    def test_signing_in_works_and_the_session_is_usable(self):
        c, r = sign_in(make_user_with_password())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get("/settings").status_code, 200)

    def test_the_session_cookie_is_httponly_samesite_and_expires(self):
        _, r = sign_in(make_user_with_password())
        cookie = r.headers["Set-Cookie"]
        for part in ("HttpOnly", "SameSite=Lax", "Expires="):
            self.assertIn(part, cookie)

    def test_next_may_not_leave_the_site(self):
        for target in ("//evil.example", "https://evil.example", "/\\evil.example", "/%09/evil.example"):
            _, r = sign_in(make_user_with_password(), next_url=target)
            self.assertNotIn("evil", r.headers["Location"], target)

    def test_next_within_the_site_is_followed(self):
        _, r = sign_in(make_user_with_password(), next_url="/plasmids")
        self.assertEqual(location(r), "/plasmids")

    def test_safe_next(self):
        self.assertEqual(security.safe_next("/colony?view=cages"), "/colony?view=cages")
        for bad in ("", None, "colony", "//x", "/\\x", "/\t/x", "http://x/"):
            self.assertIsNone(security.safe_next(bad), bad)

    def test_repeated_failures_lock_the_username_out_for_a_while(self):
        username = make_user_with_password()
        for _ in range(security.login_throttle.limit):
            sign_in(username, "wrong password!")
        c, r = sign_in(username)  # the right password, too late
        self.assertEqual(r.status_code, 429)
        self.assertIn("Too many failed sign-in attempts", r.get_data(as_text=True))
        self.assertEqual(c.get("/settings").status_code, 302)

    def test_an_unknown_username_counts_towards_the_limit_too(self):
        for _ in range(security.login_throttle.limit):
            sign_in("nobody-" + uniq(), "wrong password!")
        _, r = sign_in(make_user_with_password())  # same address
        self.assertEqual(r.status_code, 429)

    def test_the_throttle_forgets_after_a_success(self):
        username = make_user_with_password()
        for _ in range(security.login_throttle.limit - 1):
            sign_in(username, "wrong password!")
        sign_in(username)
        sign_in(username, "wrong password!")
        _, r = sign_in(username)
        self.assertEqual(r.status_code, 302)

    def test_a_pending_account_is_told_to_wait(self):
        _, r = sign_in(make_user_with_password(role="pending", disabled=True))
        self.assertIn("waiting for a lab admin", r.get_data(as_text=True))

    def test_https_only_server_says_so_over_http(self):
        with mock.patch.dict(app.config, {"SESSION_COOKIE_SECURE": True}):
            _, r = sign_in(make_user_with_password())
        self.assertEqual(r.status_code, 200)
        self.assertIn("only accepts sign-ins over HTTPS", r.get_data(as_text=True))

    def test_sign_out_is_a_post(self):
        c, _ = sign_in(make_user_with_password())
        self.assertEqual(c.get("/logout").status_code, 405)
        self.assertEqual(c.get("/settings").status_code, 200)
        c.post("/logout")
        self.assertEqual(c.get("/settings").status_code, 302)


class SessionsFollowThePassword(AppTestCase):
    def test_changing_your_password_signs_out_your_other_sessions(self):
        username = make_user_with_password()
        here, _ = sign_in(username)
        there, _ = sign_in(username)
        new = "an even better passphrase"
        r = here.post("/settings", data={"action": "password", "current_password": PASSWORD,
                                         "new_password": new, "confirm_password": new})
        self.assertEqual(here.get("/settings").status_code, 200)   # this one carries on
        self.assertEqual(there.get("/settings").status_code, 302)  # the other is gone

    def test_an_admin_reset_signs_the_person_out(self):
        username = make_user_with_password()
        theirs, _ = sign_in(username)
        self.post(self.a, f"/admin/users/{user_id(username)}/reset-password",
                  data={"new_password": "reset by the admin"})
        self.assertEqual(theirs.get("/settings").status_code, 302)

    def test_sign_out_ends_the_session_even_for_a_copy_of_its_cookie(self):
        c, _ = sign_in(make_user_with_password())
        with c.session_transaction() as sess:
            copied = dict(sess)
        c.post("/logout")
        thief = app.test_client()
        with thief.session_transaction() as sess:
            sess.update(copied)
        self.assertEqual(thief.get("/settings").status_code, 302)

    def test_disabling_then_enabling_does_not_bring_old_sessions_back(self):
        username = make_user_with_password()
        theirs, _ = sign_in(username)
        self.post(self.a, f"/admin/users/{user_id(username)}/disable")
        self.post(self.a, f"/admin/users/{user_id(username)}/disable")
        self.assertEqual(theirs.get("/settings").status_code, 302)
        again, _ = sign_in(username)
        self.assertEqual(again.get("/settings").status_code, 200)

    def register(self, username, password="a long enough passphrase"):
        c = app.test_client()
        r = c.post("/register", data={"username": username, "password": password, "confirm_password": password},
                   follow_redirects=True)
        return html_lib.unescape(r.get_data(as_text=True))

    def test_look_alike_usernames_are_refused(self):
        self.assertIn("A username is 2", self.register("аlex" + uniq("")))            # Cyrillic а
        self.assertIn("A username is 2", self.register("*"))
        self.assertIn("already exists", self.register(self.member.upper()))

    def test_sign_ups_from_one_address_are_limited(self):
        for _ in range(5):
            self.assertIn("needs to approve", self.register(uniq("joiner")))
        self.assertIn("Too many sign-ups", self.register(uniq("joiner")))

    def test_guessing_the_current_password_in_settings_is_limited(self):
        c, _ = sign_in(make_user_with_password())
        wrong = {"action": "password", "current_password": "not it at all", "new_password": "x" * 12,
                 "confirm_password": "x" * 12}
        for _ in range(10):
            c.post("/settings", data=wrong)
        r = c.post("/settings", data={**wrong, "current_password": PASSWORD}, follow_redirects=True)
        self.assertIn("Too many wrong passwords", r.get_data(as_text=True))

    def test_a_session_without_the_stamp_is_not_accepted(self):
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"] = user_id(self.member)
        self.assertEqual(c.get("/settings").status_code, 302)

    def test_short_passwords_are_refused_everywhere(self):
        username = make_user_with_password()
        c, _ = sign_in(username)
        r = self.post(c, "/settings", data={"action": "password", "current_password": PASSWORD,
                                            "new_password": "short", "confirm_password": "short"})
        self.assertFlash(r, "at least 12 characters", "error")
        r = self.post(self.a, f"/admin/users/{user_id(username)}/reset-password", data={"new_password": "short"})
        self.assertFlash(r, "at least 12 characters", "error")
        self.assertIsNotNone(sign_in(username)[1].headers.get("Location"))  # unchanged

    def test_the_password_cannot_be_the_username(self):
        self.assertIn("username", security.password_problem("Somebody-Long-Name", "somebody-long-name"))


class SignUp(AppTestCase):
    def register(self, username=None, password=PASSWORD, confirm=None, **extra):
        username = username or uniq("new")
        r = app.test_client().post("/register", data={
            "username": username, "display_name": "New Person", "password": password,
            "confirm_password": confirm if confirm is not None else password, **extra}, follow_redirects=True)
        return username, r

    def test_a_new_account_waits_for_approval(self):
        username, r = self.register()
        self.assertFlash(r, "needs to approve it", "success")
        self.assertEqual(one("select role from users where username=?", username), "pending")
        self.assertEqual(one("select disabled from users where username=?", username), 1)
        _, r = sign_in(username)
        self.assertEqual(r.status_code, 200)  # not signed in

    def test_admins_are_told_about_it(self):
        username, _ = self.register()
        self.assertTrue(count("notifications", "recipient_username=? and message like ?",
                              self.admin, f"%{username}%"))

    def test_approving_lets_them_in_as_a_member(self):
        username, _ = self.register()
        r = self.post(self.a, f"/admin/users/{user_id(username)}/disable")
        self.assertFlash(r, "approved", "success")
        self.assertEqual(one("select role from users where username=?", username), "member")
        self.assertEqual(sign_in(username)[1].status_code, 302)

    def test_approving_settles_the_admins_notices(self):
        waiting, _ = self.register()
        approved, _ = self.register()
        unread = lambda name: count("notifications", "recipient_username=? and is_read=? and message like ?",
                                    self.admin, False, f"% as {name}. %")
        self.assertEqual((unread(waiting), unread(approved)), (1, 1))
        self.post(self.a, f"/admin/users/{user_id(approved)}/disable")
        self.assertEqual((unread(waiting), unread(approved)), (1, 0))
        # One approved some other way (or before this): Home settles it.
        execute("update users set role='member', disabled=? where username=?", False, waiting)
        self.get_ok(self.a, "/home")
        self.assertEqual(unread(waiting), 0)

    def test_the_admin_page_offers_approve(self):
        username, _ = self.register()
        html = self.get_ok(self.a, "/settings")
        self.assertIn("Waiting to join", html)
        self.assertIn(username, html.split("Waiting to join", 1)[1].split(">Members<", 1)[0])
        self.assertIn("Approve", html)

    def test_a_pending_account_cannot_be_promoted_straight_to_admin(self):
        username, _ = self.register()
        self.post(self.a, f"/admin/users/{user_id(username)}/role")
        self.assertEqual(one("select role from users where username=?", username), "pending")

    def test_a_short_password_is_refused(self):
        username, r = self.register(password="short")
        self.assertFlash(r, "at least 12 characters", "error")
        self.assertEqual(count("users", "username=?", username), 0)

    def test_no_setup_code_is_asked_for_once_there_are_accounts(self):
        html = self.get_ok(app.test_client(), "/register")
        self.assertNotIn("setup_code", html)
        self.assertIn("approves new accounts", html)


class SetupCode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(security, "data_dir", lambda: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_code_is_kept_owner_only_and_matches_until_cleared(self):
        code = security.setup_code()
        path = self.tmp / "setup-code"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(security.setup_code(), code)  # stable across workers/restarts
        with app.test_request_context():
            self.assertTrue(security.setup_code_matches(f"  {code.upper()} "))
            self.assertFalse(security.setup_code_matches("0000-0000-0000"))
            self.assertFalse(security.setup_code_matches(""))
        security.clear_setup_code()
        self.assertFalse(path.exists())

    def test_the_desktop_app_does_not_need_it(self):
        with app.test_request_context():
            self.assertTrue(security.setup_code_required())
            with mock.patch.dict(app.config, {"LOCAL_SETUP": True}):
                self.assertFalse(security.setup_code_required())


class SecretKey(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(security, "data_dir", lambda: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self, **values):
        clean = {k: v for k, v in os.environ.items() if k not in {"SECRET_KEY", "BIOMANAGER_ENV"}}
        return mock.patch.dict(os.environ, {**clean, **values}, clear=True)

    def test_without_one_a_key_is_made_once_and_kept_owner_only(self):
        with self.env():
            first = security.secret_key()
            self.assertEqual(security.secret_key(), first)
        self.assertGreaterEqual(len(first), 48)
        self.assertEqual(stat.S_IMODE((self.tmp / "secret_key").stat().st_mode), 0o600)

    def test_the_published_development_key_is_ignored(self):
        with self.env(SECRET_KEY=security.DEV_SECRET):
            self.assertNotEqual(security.secret_key(), security.DEV_SECRET)

    def test_a_given_key_is_used(self):
        with self.env(SECRET_KEY="k" * 40):
            self.assertEqual(security.secret_key(), "k" * 40)

    def test_production_refuses_a_short_key(self):
        with self.env(SECRET_KEY="short", BIOMANAGER_ENV="production"):
            with self.assertRaises(RuntimeError):
                security.secret_key()


class Uploads(AppTestCase):
    def test_uploads_need_a_login(self):
        r = app.test_client().get("/static/uploads/20260101_note.png")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def headers_for(self, path, mimetype):
        with app.test_request_context(path):
            return security.add_security_headers(Response(b"", mimetype=mimetype)).headers

    def test_an_uploaded_page_is_downloaded_and_sandboxed(self):
        for mimetype in ("text/html", "image/svg+xml", "application/octet-stream"):
            h = self.headers_for("/static/uploads/x", mimetype)
            self.assertEqual(h["Content-Disposition"], "attachment", mimetype)
            self.assertIn("sandbox", h["Content-Security-Policy"], mimetype)

    def test_images_and_pdfs_still_open_in_the_browser(self):
        h = self.headers_for("/static/uploads/x.png", "image/png")
        self.assertNotIn("Content-Disposition", h)
        self.assertIn("sandbox", h["Content-Security-Policy"])
        h = self.headers_for("/static/uploads/x.pdf", "application/pdf")
        self.assertNotIn("Content-Disposition", h)
        self.assertNotIn("Content-Security-Policy", h)

    def test_stored_names_are_unguessable_unique_and_safe(self):
        a = services.upload_name("../../etc/gel image.png")
        b = services.upload_name("../../etc/gel image.png")
        self.assertNotEqual(a, b)
        self.assertTrue(a.endswith("_etc_gel_image.png"))
        self.assertNotIn("/", a)

    def test_a_body_over_the_limit_is_refused_politely(self):
        with mock.patch.dict(app.config, {"MAX_CONTENT_LENGTH": 1024}):
            r = self.m.post("/colony/cages/create", data={"cage_id": uniq("X"), "notes": "x" * 5000},
                            headers=AUTOSAVE)
            self.assertEqual(r.status_code, 413)
            self.assertIn("too large", r.get_json()["error"])
            r = self.post(self.m, "/colony/cages/create", data={"cage_id": uniq("X"), "notes": "x" * 5000})
            self.assertFlash(r, "too large", "error")


class SecurityHeaders(AppTestCase):
    def test_pages_carry_the_basic_headers(self):
        h = self.m.get("/colony").headers
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(h["Referrer-Policy"], "same-origin")


class EntryPoints(unittest.TestCase):
    """In a child process: wsgi sets BIOMANAGER_ENV for the whole process."""

    def run_python(self, code, **env):
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                              env={**os.environ, "SECRET_KEY": "k" * 40, **env}, timeout=120)

    def test_run_py_refuses_the_debugger_on_a_network_address(self):
        p = self.run_python("import runpy; runpy.run_path('run.py', run_name='__main__')",
                            HOST="0.0.0.0", FLASK_DEBUG="1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Refusing to start the debugger", p.stderr)

    def test_wsgi_refuses_debug_mode(self):
        p = self.run_python("import wsgi", FLASK_DEBUG="1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Debug mode is on", p.stderr)

    def test_wsgi_turns_on_secure_cookies(self):
        p = self.run_python("import wsgi; print(wsgi.app.config['SESSION_COOKIE_SECURE'])", FLASK_DEBUG="0")
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        self.assertEqual(p.stdout.strip().splitlines()[-1], "True")


if __name__ == "__main__":
    unittest.main()


class ContentSecurityPolicy(AppTestCase):
    """Scripts run only from the app's own files or with this request's
    nonce, so markup injected into a page cannot run script."""

    TEMPLATES = ROOT / "app" / "templates"
    INLINE_SCRIPT = __import__("re").compile(r'<script(?![^>]*\bsrc=)(?![^>]*type="application/json")[^>]*>')

    def policy(self, response):
        return response.headers.get("Content-Security-Policy", "")

    def test_pages_carry_a_policy_with_a_fresh_nonce(self):
        first, second = self.policy(self.m.get("/colony")), self.policy(self.m.get("/colony"))
        self.assertIn("script-src 'self' 'nonce-", first)
        self.assertIn("object-src 'none'", first)
        self.assertIn("frame-ancestors 'self'", first)
        self.assertNotIn("unsafe-inline' 'nonce", first)
        self.assertNotEqual(first, second)  # a new nonce every response

    def test_every_inline_script_on_a_page_has_that_pages_nonce(self):
        import re
        for url in ("/colony", "/colony?view=breeders", "/notebook", "/calendar", "/plasmids", "/home"):
            r = self.m.get(url)
            nonce = re.search(r"'nonce-([^']+)'", self.policy(r)).group(1)
            for tag in self.INLINE_SCRIPT.findall(r.get_data(as_text=True)):
                self.assertIn(f'nonce="{nonce}"', tag, f"{url}: {tag}")

    def test_no_template_uses_inline_event_handlers_or_unnonced_scripts(self):
        import re
        handler = re.compile(r'\son[a-z]+\s*=\s*["\']', re.I)
        bad = []
        for path in self.TEMPLATES.rglob("*.html"):
            text = path.read_text()
            for i, line in enumerate(text.splitlines(), 1):
                if handler.search(line) or "javascript:" in line:
                    bad.append(f"{path.relative_to(ROOT)}:{i}")
            for tag in self.INLINE_SCRIPT.findall(text):
                if "nonce=" not in tag:
                    bad.append(f"{path.relative_to(ROOT)}: {tag}")
        self.assertEqual(bad, [], "use data-on-click=… (static/actions.js) and nonce=\"{{ csp_nonce() }}\"")

    def test_every_action_named_in_markup_is_registered(self):
        import re
        builtin = {"none", "close-dialog", "open-dialog", "close-dialog-id", "click", "print", "remove-row"}
        named = set()
        for path in self.TEMPLATES.rglob("*.html"):
            named |= set(re.findall(r'data-on-[a-z]+="([^"$]+)"', path.read_text()))
        sources = "\n".join(p.read_text() for p in list(self.TEMPLATES.rglob("*.html"))
                            + list((ROOT / "app" / "static").glob("*.js")))
        registered = set(re.findall(r"BioActions\.register\(\{([^}]*)\}", sources, re.S))
        keys = {k.strip().strip("'\"") for block in registered for k in re.findall(r"['\"]?([\w-]+)['\"]?\s*:", block)}
        self.assertEqual(sorted(named - builtin - keys), [])

    def test_report_only_and_off(self):
        with mock.patch.dict(os.environ, {"BIOMANAGER_CSP": "report-only"}):
            r = self.m.get("/colony")
            self.assertIn("Content-Security-Policy-Report-Only", r.headers)
            self.assertNotIn("Content-Security-Policy", r.headers)
        with mock.patch.dict(os.environ, {"BIOMANAGER_CSP": "off"}):
            r = self.m.get("/colony")
            self.assertNotIn("Content-Security-Policy", r.headers)
            self.assertNotIn("Content-Security-Policy-Report-Only", r.headers)

    def test_violation_reports_are_accepted_without_a_login(self):
        body = '{"csp-report": {"document-uri": "http://localhost/colony", "blocked-uri": "inline", "violated-directive": "script-src-elem"}}'
        r = app.test_client().post("/csp-report", data=body, content_type="application/csp-report",
                                   headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status_code, 204)

    def test_uploads_keep_their_own_sandbox_policy(self):
        with app.test_request_context("/static/uploads/x.html"):
            h = security.add_security_headers(Response(b"", mimetype="text/html")).headers
        self.assertIn("sandbox", h["Content-Security-Policy"])
        self.assertNotIn("nonce", h["Content-Security-Policy"])
