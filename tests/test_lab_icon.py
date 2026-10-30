"""The lab's own icon at the top of the sidebar (app/lab_icon.py).

An admin picks a picture in Settings → General; everyone then sees it in
the sidebar in place of BioManager's. Only a PNG, JPEG or WebP, never an
SVG; removing it brings BioManager's back."""
from __future__ import annotations

import io

from tests.base import AppTestCase, client_for, make_user  # isort: skip

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


class LabIconTests(AppTestCase):
    def tearDown(self):
        from app import lab_icon
        from app.db import SessionLocal
        with SessionLocal() as s:
            lab_icon.put(s, "", b"")
            s.commit()
        super().tearDown()

    def upload(self, client, data: bytes, name: str = "icon.png"):
        return client.post("/settings/lab-icon", data={"icon": (io.BytesIO(data), name)},
                           content_type="multipart/form-data", headers={"X-Autosave": "1"})

    def test_an_admin_sets_it_and_everyone_sees_it(self):
        admin = client_for(make_user(role="admin"))
        r = self.upload(admin, PNG)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        url = r.get_json()["icon"]
        self.assertTrue(url.startswith("/lab-icon?v="))
        member = client_for(make_user())
        html = self.get_ok(member, "/home")
        self.assertIn(f'src="{url}"', html.replace("&amp;", "&"))
        self.assertIn("data-lab-icon", html)
        got = member.get(url)
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.mimetype, "image/png")
        self.assertEqual(got.headers["X-Content-Type-Options"], "nosniff")

    def test_without_one_the_sidebar_shows_biomanagers(self):
        html = self.get_ok(client_for(make_user()), "/home")
        self.assertNotIn("data-lab-icon", html)
        self.assertIn("data-app-icon", html)

    def test_removing_it_brings_biomanagers_back(self):
        admin = client_for(make_user(role="admin"))
        self.upload(admin, PNG)
        r = admin.post("/settings/lab-icon", data={"remove": "1"}, headers={"X-Autosave": "1"})
        self.assertEqual(r.get_json()["icon"], "")
        self.assertNotIn("data-lab-icon", self.get_ok(admin, "/home"))

    def test_only_pictures_and_only_admins(self):
        admin = client_for(make_user(role="admin"))
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertEqual(self.upload(admin, svg, "icon.svg").status_code, 400)
        self.assertEqual(self.upload(client_for(make_user()), PNG).status_code, 403)

    def test_signed_out_sees_nothing(self):
        from app.app import app
        self.assertEqual(app.test_client().get("/lab-icon").status_code, 404)
