"""A notebook topic's icon (app/topic_icons.py): one of BioManager's own, an
emoji as the computer's emoji picker types it, or an SVG of one's own,
cleaned to what draws before it is kept."""
from __future__ import annotations

import io
import json
import unittest

from tests.base import AppTestCase, client_for, make_user, one  # first: points the app at a test database
from app import topic_icons as ti  # noqa: E402
from app.lab_notebook import FOLDER_ICONS  # noqa: E402

SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'


def svg(inside: str, attrs: str = 'viewBox="0 0 24 24"') -> bytes:
    return f'<svg {SVG_NS} {attrs}>{inside}</svg>'.encode("utf-8")


class Emoji(unittest.TestCase):
    def test_an_emoji_and_the_ones_made_of_several(self):
        for text in ("🧪", "🐭", "⚗️", "🇨🇳", "👩‍🔬", "❄️"):
            self.assertTrue(ti.is_emoji(text), text)

    def test_letters_markup_and_long_text_are_not(self):
        for text in ("", "a", "ab🧪", "🧪 ", "<b>", "🧪\"", "1️⃣2", "🧪" * 17):
            self.assertFalse(ti.is_emoji(text), text)

    def test_what_the_dialog_sends_is_kept_as_it_is_drawn(self):
        self.assertEqual(ti.from_form("dna", FOLDER_ICONS), "dna")
        self.assertEqual(ti.from_form("folder", FOLDER_ICONS), "")
        self.assertEqual(ti.from_form(None, FOLDER_ICONS), "")
        self.assertEqual(ti.from_form("emoji: 🧫 ", FOLDER_ICONS), "emoji:🧫")
        self.assertEqual(ti.from_form("svg:uploads/ti_0123456789abcdef.svg", FOLDER_ICONS),
                         "svg:uploads/ti_0123456789abcdef.svg")
        for wrong in ("rocket", "emoji:abc", "emoji:", "svg:uploads/../app.db", "svg:https://x/y.svg",
                      "svg:uploads/ti_0123456789abcdef.svg.png"):
            self.assertIsNone(ti.from_form(wrong, FOLDER_ICONS), wrong)


class CleaningAnSvg(unittest.TestCase):
    def clean(self, data: bytes) -> str:
        out = ti.clean_svg(data)
        self.assertIsNotNone(out)
        return out.decode("utf-8")

    def test_a_drawing_is_kept(self):
        out = self.clean(svg('<style>.a{fill:#e5484d}</style><g><circle class="a" cx="12" cy="12" r="10"/>'
                             '<path d="M6 12h12" stroke="#fff" stroke-width="2"/></g>'))
        for kept in ('<style>.a{fill:#e5484d}</style>', 'class="a"', 'r="10"', 'd="M6 12h12"', 'stroke-width="2"'):
            self.assertIn(kept, out)

    def test_script_and_event_attributes_go(self):
        out = self.clean(svg('<script>alert(1)</script><rect width="4" height="4" onclick="x()" ONLOAD="y()"/>',
                             attrs='viewBox="0 0 24 24" onload="alert(2)"'))
        for gone in ("script", "alert", "onclick", "onload", "ONLOAD"):
            self.assertNotIn(gone, out)
        self.assertIn("<rect", out)

    def test_nothing_reaches_outside_the_file(self):
        out = self.clean(svg(
            '<a href="https://example.org"><circle r="3"/></a>'
            '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>'
            '<image href="https://example.org/x.png"/>'
            '<use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://example.org/s.svg#a"/>'
            '<use href="#inside"/>'
            '<rect id="inside" style="fill:url(https://example.org/p)" stroke="url(#grad)"/>'
            '<style>@import url(https://example.org/x.css);</style>'))
        for gone in ("example.org", "foreignObject", "<image", "<a ", "@import"):
            self.assertNotIn(gone, out)
        self.assertIn('href="#inside"', out)
        self.assertIn('stroke="url(#grad)"', out)
        self.assertIn("fill:none", out)

    def test_it_is_given_a_viewbox_to_draw_at_any_size(self):
        self.assertIn('viewBox="0 0 32 16"', self.clean(svg("<rect/>", attrs='width="32px" height="16"')))

    def test_what_is_not_a_drawing_is_refused(self):
        for data in (b"", b"not xml", b"<html><body/></html>", b'<svg xmlns="http://www.w3.org/2000/svg"',
                     b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "lol">]><svg>&x;</svg>',
                     svg("<g/>" * (ti.SVG_ELEMENTS_MAX + 1)),
                     svg("<!--" + "x" * ti.SVG_MAX + "-->")):
            self.assertIsNone(ti.clean_svg(data), data[:60])


class TopicIcons(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.c = client_for(self.user)
        r = self.c.post("/notebook/tabs/create", data={"title": "Cloning"})
        self.tab = r.get_json()["id"]

    def edit(self, client, icon):
        return client.post(f"/notebook/api/topics/{self.tab}", data=json.dumps({"icon": icon}),
                           content_type="application/json")

    def upload(self, client, data: bytes, name="icon.svg"):
        return client.post("/notebook/api/topic-icons", data={"svg": (io.BytesIO(data), name)},
                           content_type="multipart/form-data")

    def test_an_emoji_for_a_topic(self):
        r = self.edit(self.c, "emoji:🧫")
        self.assertEqual((r.status_code, r.get_json()["icon"]), (200, "emoji:🧫"))
        html = self.c.get(f"/notebook?tab={self.tab}").get_data(as_text=True)
        self.assertIn('<span class="ti-emoji" aria-hidden="true">🧫</span>', html)
        self.assertEqual(self.edit(self.c, "emoji:<script>").status_code, 400)

    def test_an_svg_of_one_s_own(self):
        r = self.upload(self.c, svg('<circle cx="12" cy="12" r="10" onload="x()"/><script>y()</script>'))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        icon, address = r.get_json()["icon"], r.get_json()["url"]
        self.assertRegex(icon, r"^svg:uploads/ti_[0-9a-f]{16}\.svg$")
        kept = self.c.get(address)
        self.assertEqual(kept.status_code, 200)
        self.assertNotIn(b"script", kept.data)
        self.assertNotIn(b"onload", kept.data)
        self.assertEqual(self.edit(self.c, icon).get_json()["icon"], icon)
        self.assertEqual(one("select icon from notebook_tabs where id=?", self.tab), icon)
        html = self.c.get(f"/notebook?tab={self.tab}").get_data(as_text=True)
        self.assertIn(f'<img class="ti-svg" src="{address}"', html)
        # The lab's other people may draw it in their sidebar, as any shared record.
        self.assertEqual(client_for(make_user()).get(address).status_code, 200)

    def test_what_isn_t_an_svg_drawing_is_refused(self):
        self.assertEqual(self.upload(self.c, b"<html></html>").status_code, 400)
        self.assertEqual(self.upload(self.c, b"x" * (ti.SVG_MAX + 1)).status_code, 400)
        self.assertEqual(self.c.post("/notebook/api/topic-icons").status_code, 400)


if __name__ == "__main__":
    unittest.main()
