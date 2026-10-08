"""Facility roles (app/access.py) and an institution's own sign-in
(app/oidc.py: any OpenID Connect provider, and CILogon for SAML universities)."""
from __future__ import annotations

import os
from unittest import mock
from urllib.parse import parse_qs, urlparse

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, client_for, make_user, one, uniq
from app import access, oidc  # noqa: E402
from app.app import app  # noqa: E402


class Roles(AppTestCase):
    def setUp(self):
        super().setUp()
        self.care = make_user(uniq("tech"), role="care")
        self.facility = make_user(uniq("fm"), role="facility")

    def mouse_of(self, owner_client, owner):
        return self.make_mouse(owner_client, owner, cage=uniq("C"), status="experiment")

    def test_animal_care_looks_after_every_lab_s_animals(self):
        mouse = self.mouse_of(self.m, self.member)
        tech = client_for(self.care)
        r = self.autosave(tech, f"/colony/mice/{mouse}/update", {"note": "Fight wound, cleaned"})
        self.assertSaved(r)
        self.assertEqual(one("select note from mice where id=?", mouse), "Fight wound, cleaned")
        # …but not someone's experiment, nor a database's settings.
        r = self.m.post("/colony/experiments/create", data={"name": uniq("Exp ")})
        exp = int(r.headers["Location"].rsplit("/", 1)[1])
        self.assertEqual(self.autosave(tech, f"/colony/experiments/{exp}/update", {"name": "x"}).status_code, 403)
        from app.db import SessionLocal
        from app.models import StockModule
        with SessionLocal() as s, app.test_request_context():
            from flask import g
            from app.models import UserAccount
            g.user = s.query(UserAccount).filter_by(username=self.care).one()
            module = StockModule(key="x", label="x", created_by=self.member)
            self.assertFalse(access.can_configure(module))
            g.user = s.query(UserAccount).filter_by(username=self.facility).one()
            self.assertTrue(access.can_configure(module))
            self.assertTrue(access.can_edit_rack(type("R", (), {"created_by": self.member})()))

    def test_a_member_still_can_t(self):
        mouse = self.mouse_of(self.m, self.member)
        r = self.autosave(self.o, f"/colony/mice/{mouse}/update", {"note": "x"})
        self.assertRefused(r)

    def test_an_admin_gives_a_role(self):
        who = make_user(uniq("newtech"))
        uid = one("select id from users where username=?", who)
        self.a.post(f"/admin/users/{uid}/role", data={"role": "care"})
        self.assertEqual(one("select role from users where id=?", uid), "care")
        self.a.post(f"/admin/users/{uid}/role", data={"role": "overlord"})
        self.assertEqual(one("select role from users where id=?", uid), "care")
        self.assertIn("Animal care", self.get_ok(self.a, "/settings"))


CONF = {"issuer": "https://login.example.edu", "authorization_endpoint": "https://login.example.edu/authorize",
        "token_endpoint": "https://login.example.edu/token", "jwks_uri": "https://login.example.edu/keys"}


class InstitutionSignIn(AppTestCase):
    ENV = {"BIOMANAGER_OIDC_ISSUER": "https://login.example.edu/", "BIOMANAGER_OIDC_CLIENT_ID": "bm",
           "BIOMANAGER_OIDC_CLIENT_SECRET": "s", "BIOMANAGER_OIDC_NAME": "ExampleKey",
           "BIOMANAGER_CILOGON_CLIENT_ID": "cilogon:/client_id/1", "BIOMANAGER_CILOGON_CLIENT_SECRET": "s2",
           "BIOMANAGER_CILOGON_IDP": "https://idp.example.edu/idp/shibboleth"}

    def start(self, key):
        oidc._discovery.clear()
        with mock.patch.dict(os.environ, self.ENV), mock.patch.object(oidc, "_get_json", return_value=CONF):
            r = app.test_client().get(f"/auth/{key}/start")
        return r, parse_qs(urlparse(r.headers["Location"]).query)

    def test_both_are_offered_under_their_own_names(self):
        with mock.patch.dict(os.environ, self.ENV):
            found = oidc.providers()
            self.assertEqual(found["institution"].label, "ExampleKey")
            self.assertEqual(found["institution"].discovery_url, "https://login.example.edu/.well-known/openid-configuration")
            self.assertIn("cilogon", found)
            self.assertIn("Sign in with ExampleKey", app.test_client().get("/login").get_data(as_text=True))

    def test_the_institution_gets_a_plain_request_and_cilogon_goes_straight_to_the_university(self):
        r, params = self.start("institution")
        self.assertTrue(r.headers["Location"].startswith("https://login.example.edu/authorize?"))
        self.assertNotIn("prompt", params)
        self.assertEqual(params["redirect_uri"], ["http://localhost/auth/institution/callback"])
        _r, params = self.start("cilogon")
        self.assertEqual(params["idphint"], ["https://idp.example.edu/idp/shibboleth"])

    def test_an_issuer_must_be_https(self):
        with mock.patch.dict(os.environ, {**self.ENV, "BIOMANAGER_OIDC_ISSUER": "http://login.example.edu"}):
            with self.assertRaises(RuntimeError):
                oidc.providers()
