"""Settings → Appearance (app/appearance.py).

Each person picks a picture, a colour and a background for the app icon, and
a mode, look, font and rows; each is saved at once and kept for them alone.
The colour retints the app; Jade, the default, leaves the shipped accent
alone. A theme sets everything at once."""
from __future__ import annotations

import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

# tests.base first: it points the app at a throwaway database before anything
# imports the app. Importing app first would open data/biomanager.db.
from tests.base import AppTestCase, client_for, make_user, uniq  # isort: skip

from app import appearance  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC_ICON = ROOT / "app" / "static" / "icon.svg"


class DrawingTests(AppTestCase):
    def test_every_picture_in_every_colour_is_valid_svg(self):
        for glyph in appearance.GLYPHS:
            for color in appearance.PALETTES:
                for ground in appearance.GROUNDS:
                    for variant in ("app", "full", "glyph", "background"):
                        with self.subTest(glyph=glyph, color=color, ground=ground, variant=variant):
                            root = ET.fromstring(appearance.render(glyph, color, variant, ground))
                            self.assertEqual(root.get("viewBox"), "0 0 1024 1024")

    def test_the_colour_reaches_the_drawing(self):
        self.assertIn(appearance.PALETTES["rose"].top, appearance.render("mouse", "rose"))
        self.assertNotIn(appearance.PALETTES["rose"].top, appearance.render("mouse", "ocean"))

    def test_on_white_and_black_the_picture_takes_the_colour(self):
        pal = appearance.PALETTES["coral"]
        on_colour = appearance.render("fly", "coral", "app", "color")
        on_white = appearance.render("fly", "coral", "app", "light")
        self.assertIn(f'stop-color="{pal.top}"', on_colour.split('id="bm-bg"')[1].split("</linearGradient>")[0])
        self.assertIn('stop-color="#FFFFFF"', on_white.split('id="bm-bg"')[1].split("</linearGradient>")[0])
        self.assertIn(f'stop-color="{pal.top}"', on_white.split('id="bm-w"')[1].split("</linearGradient>")[0])
        self.assertIn('stop-color="#3B414D"', appearance.render("fly", "coral", "app", "dark"))

    def test_no_painted_bevels(self):
        # Apple's guidance for Liquid Glass icons: the system lights them;
        # the drawing itself carries no lighting or inner shadows.
        for glyph in appearance.GLYPHS:
            svg = appearance.render(glyph, "jade")
            self.assertNotIn("feSpecularLighting", svg)
            self.assertNotIn("feDiffuseLighting", svg)

    def test_ink_on_black_is_drawn_white(self):
        on_black = appearance.render("helix", "ink", "app", "dark")
        self.assertIn('stop-color="#FFFFFF"', on_black.split('id="bm-w"')[1].split("</linearGradient>")[0])

    def test_ink_is_black_and_white_in_dark_mode(self):
        css = appearance.brand_css("ink")
        self.assertIn("--color-brand-600:#0D0D0D;", css)
        self.assertIn("--color-brand-600:#F2F2F2;", css)
        self.assertIn("--color-on-brand:#0D0D0D;", css)
        # White words on the accent become the accent's own: black on white.
        self.assertIn(".btn-primary,", css)
        self.assertIn("--color-white:var(--color-on-brand)", css)
        self.assertNotIn("--color-white", appearance.brand_css("indigo"))

    def test_the_mono_look_sets_every_token_again_for_dark_mode(self):
        # Unlayered and after tailwind.css, a light value missing from the
        # dark block would win in dark mode.
        css = (ROOT / "app" / "static" / "appearance.css").read_text(encoding="utf-8")
        css = css[css.index(':root[data-look="mono"] {'):]
        light = set(re.findall(r"^  (--[a-z0-9-]+):", css[:css.index("\n}\n")], re.M))
        dark_block = css[css.index("@media (prefers-color-scheme: dark)"):]
        dark = set(re.findall(r"^    (--[a-z0-9-]+):", dark_block[:dark_block.index("\n  }\n")], re.M))
        same_in_both = {"--wall", "--glass-light", "--glass-glow", "--glass-sheen", "--glass-hi", "--glass-edge",
                        "--control-hi", "--glass-lift", "--field-shadow", "--scrim-blur"}
        self.assertEqual(light - dark - same_in_both, set())
        self.assertEqual(dark - light, set())

    def test_jade_keeps_the_shipped_accent(self):
        self.assertEqual(appearance.brand_css("jade"), "")
        css = appearance.brand_css("indigo")
        self.assertIn("--color-brand-600:#5A5BD8;", css)
        self.assertIn('prefers-color-scheme: dark', css)
        self.assertIn(':root[data-theme="dark"]', css)

    def test_the_static_icon_is_the_default_drawing(self):
        # scripts/build-app-icon.py writes it; rerun that after changing a drawing.
        self.assertIn(appearance.render("helix", "jade").split("<defs>", 1)[1], STATIC_ICON.read_text(encoding="utf-8"))

    def test_the_forced_dark_stylesheet_is_current(self):
        # scripts/theme-dark-css.py writes it; rerun that after changing a dark-mode rule.
        spec = importlib.util.spec_from_file_location("theme_dark_css", ROOT / "scripts" / "theme-dark-css.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.OUT.read_text(encoding="utf-8"), module.build())
        self.assertIn(':root[data-theme="dark"] {', module.build())


class ChoiceTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.pick(self.m, "helix", "jade")

    def pick(self, client, glyph, color, ground="color"):
        return self.autosave(client, "/settings", {"action": "appearance", "glyph": glyph, "color": color,
                                                   "ground": ground})

    def test_the_default_is_the_helix_in_jade(self):
        fresh = client_for(make_user(uniq("fresh")))
        html = self.get_ok(fresh, "/settings")
        self.assertIn('/static/icon.svg?v=', html)
        self.assertIn('<style id="brand-css"></style>', html)
        self.assertRegex(html, r'value="helix" checked')
        self.assertRegex(html, r'value="jade" checked')
        self.assertRegex(html, r'value="color" checked')

    def test_picking_saves_at_once_and_answers_with_the_new_look(self):
        r = self.pick(self.m, "zebrafish", "rose")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("/app-icon/zebrafish/rose.svg?v=", body["icon"])
        self.assertIn("--color-brand-600:#D4477C;", body["brand_css"])

        html = self.get_ok(self.m, "/settings")
        self.assertIn('href="/app-icon/zebrafish/rose.svg?v=', html)
        self.assertIn("--color-brand-600:#D4477C;", html)
        self.assertRegex(html, r'value="zebrafish" checked')

    def test_on_white_or_black(self):
        body = self.pick(self.m, "petri", "amber", "dark").get_json()
        self.assertIn("/app-icon/petri/amber/dark.svg?v=", body["icon"])
        html = self.get_ok(self.m, "/settings")
        self.assertIn('href="/app-icon/petri/amber/dark.svg?v=', html)
        self.assertRegex(html, r'value="dark" checked')
        # On its colour is the default, at the address it always had.
        self.assertIn("/app-icon/petri/amber.svg?v=", self.pick(self.m, "petri", "amber", "color").get_json()["icon"])

    def test_an_old_colour_becomes_the_nearest_new_one(self):
        self.assertEqual(appearance.parse("worm/lavender"), ("worm", "indigo", "color"))
        self.assertEqual(appearance.parse("fly/mint"), ("fly", "jade", "color"))
        self.assertEqual(appearance.parse("fly/peach/light"), ("fly", "coral", "light"))
        self.assertEqual(appearance.parse(""), ("helix", "jade", "color"))

    def test_the_choice_is_kept_per_person(self):
        self.pick(self.m, "worm", "ocean")
        self.assertIn("/app-icon/worm/ocean.svg", self.get_ok(self.m, "/home"))
        self.assertNotIn("/app-icon/worm/ocean.svg", self.get_ok(self.o, "/home"))

    def test_unknown_values_fall_back_to_the_default(self):
        self.pick(self.m, "unicorn", "tartan", "plaid")
        html = self.get_ok(self.m, "/settings")
        self.assertIn('/static/icon.svg?v=', html)
        self.assertRegex(html, r'value="helix" checked')

    def test_a_plain_form_post_works_without_javascript(self):
        r = self.post(self.m, "/settings", {"action": "appearance", "glyph": "fly", "color": "coral", "ground": "light"})
        self.assertFlash(r, "App icon updated")
        self.assertIn("/app-icon/fly/coral/light.svg", r.get_data(as_text=True))


class FontTests(AppTestCase):
    """Geist by default; Inter or the computer's own font for whoever picks it."""

    def font(self, client, value):
        return self.autosave(client, "/settings", {"action": "profile", "ui_font": value})

    def test_geist_is_the_default(self):
        html = self.get_ok(client_for(make_user(uniq("fresh"))), "/settings")
        self.assertNotIn("data-font=", html[:html.index(">", html.index("<html"))])
        self.assertIn('<option value="geist" selected>', html)

    def test_the_font_is_kept_per_person(self):
        me = client_for(make_user(uniq("sysfont")))
        self.font(me, "system")
        self.assertIn('data-font="system"', self.get_ok(me, "/home"))
        self.assertNotIn('data-font="system"', self.get_ok(client_for(make_user(uniq("other"))), "/home"))
        self.font(me, "inter")
        self.assertIn('data-font="inter"', self.get_ok(me, "/home"))
        self.font(me, "geist")
        home = self.get_ok(me, "/home")
        self.assertNotIn('data-font="inter"', home)
        self.assertNotIn('data-font="system"', home)

    def test_unknown_fonts_fall_back_to_geist(self):
        me = client_for(make_user(uniq("oddfont")))
        self.font(me, "comic-sans")
        home = self.get_ok(me, "/home")
        self.assertNotIn('data-font="system"', home)
        self.assertNotIn('data-font="inter"', home)


class LookTests(AppTestCase):
    """Liquid Glass by default; Solid (no glass, no wallpaper) or Mono (black
    and white) for whoever picks it."""

    def look(self, client, value):
        return self.autosave(client, "/settings", {"action": "profile", "ui_look": value})

    def test_the_look_is_kept_per_person(self):
        me = client_for(make_user(uniq("solid")))
        html = self.get_ok(me, "/settings")
        self.assertNotIn('data-look="solid"', html)
        self.assertIn('<option value="glass" selected>', html)
        self.look(me, "solid")
        self.assertIn('data-look="solid"', self.get_ok(me, "/home"))
        self.assertNotIn('data-look="solid"', self.get_ok(client_for(make_user(uniq("other"))), "/home"))
        self.look(me, "glass")
        self.assertNotIn('data-look="solid"', self.get_ok(me, "/home"))
        self.look(me, "neon")
        self.assertNotIn('data-look=', self.get_ok(me, "/home"))

    def test_mono(self):
        me = client_for(make_user(uniq("mono")))
        self.look(me, "mono")
        self.assertIn('data-look="mono"', self.get_ok(me, "/home"))
        self.assertIn('<option value="mono" selected>', self.get_ok(me, "/settings"))


class ChoiceRowTests(AppTestCase):
    """Mode and the rows of a sheet: marked on the page, kept per person."""

    def choose(self, client, **values):
        return self.autosave(client, "/settings", {"action": "profile", **{f"ui_{k}": v for k, v in values.items()}})

    def html_tag(self, client):
        page = self.get_ok(client, "/home")
        return page[page.index("<html"):page.index(">", page.index("<html"))]

    def test_defaults_leave_the_page_unmarked(self):
        tag = self.html_tag(client_for(make_user(uniq("plain"))))
        for attr in ("data-theme", "data-density", "data-rows"):
            self.assertNotIn(attr, tag)

    def test_each_choice_is_marked_and_kept_per_person(self):
        me = client_for(make_user(uniq("rows")))
        self.choose(me, mode="dark", density="compact", rows="both")
        tag = self.html_tag(me)
        self.assertIn('data-theme="dark"', tag)
        self.assertIn('data-density="compact"', tag)
        self.assertIn('data-rows="both"', tag)
        self.assertNotIn("data-theme", self.html_tag(client_for(make_user(uniq("other")))))
        self.choose(me, mode="auto", density="roomy", rows="lines")
        self.assertNotIn("data-", self.html_tag(me).replace("data-font", "").replace("data-look", ""))

    def test_unknown_values_fall_back(self):
        me = client_for(make_user(uniq("oddrows")))
        self.choose(me, mode="sepia", density="tiny", rows="tartan")
        tag = self.html_tag(me)
        for attr in ("data-theme", "data-density", "data-rows"):
            self.assertNotIn(attr, tag)


class ThemeTests(AppTestCase):
    """A theme sets the icon and every choice at once."""

    def test_a_theme_sets_everything(self):
        me = client_for(make_user(uniq("night")))
        body = self.autosave(me, "/settings", {"action": "theme", "theme": "paper"}).get_json()
        self.assertEqual(body, {"ok": True, "text_size": "default"})
        page = self.get_ok(me, "/settings")
        tag = page[page.index("<html"):page.index(">", page.index("<html"))]
        self.assertIn('data-theme="light"', tag)
        self.assertIn('data-look="solid"', tag)
        self.assertIn('data-font="inter"', tag)
        self.assertIn('data-density="regular"', tag)
        self.assertIn('data-rows="stripes"', tag)
        self.assertIn('href="/app-icon/helix/ocean/light.svg?v=', page)
        self.assertRegex(page, r'class="theme-card is-current"\s+data-theme-name="paper"')

    def test_mono_is_black_and_white(self):
        me = client_for(make_user(uniq("mono")))
        self.autosave(me, "/settings", {"action": "theme", "theme": "mono"})
        page = self.get_ok(me, "/home")
        tag = page[page.index("<html"):page.index(">", page.index("<html"))]
        self.assertIn('data-look="mono"', tag)
        self.assertIn('data-font="system"', tag)
        self.assertIn('href="/app-icon/helix/ink.svg?v=', page)
        self.assertIn("--color-brand-600:#0D0D0D;", page)

    def test_the_themes(self):
        self.assertEqual(list(appearance.THEMES), ["glass", "paper", "night", "mono"])

    def test_changing_anything_after_makes_it_your_own(self):
        me = client_for(make_user(uniq("mine")))
        self.autosave(me, "/settings", {"action": "theme", "theme": "night"})
        self.assertIn("is-current", self.get_ok(me, "/settings"))
        self.autosave(me, "/settings", {"action": "profile", "ui_rows": "stripes"})
        self.assertNotIn("theme-card is-current", self.get_ok(me, "/settings"))

    def test_the_default_matches_the_glass_theme(self):
        page = self.get_ok(client_for(make_user(uniq("fresh"))), "/settings")
        self.assertRegex(page, r'class="theme-card is-current"\s+data-theme-name="glass"')

    def test_an_unknown_theme_changes_nothing(self):
        me = client_for(make_user(uniq("nothing")))
        self.assertEqual(self.autosave(me, "/settings", {"action": "theme", "theme": "disco"}).get_json()["ok"], False)


class IconRouteTests(AppTestCase):
    def test_serves_an_svg_without_signing_in(self):
        client = self.m.application.test_client()
        for path in ("/app-icon/petri/amber.svg", "/app-icon/petri/amber/light.svg"):
            r = client.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.mimetype, "image/svg+xml")
            self.assertIn("immutable", r.headers["Cache-Control"])
            ET.fromstring(r.get_data(as_text=True))

    def test_unknown_pictures_colours_and_backgrounds_are_not_found(self):
        self.assertEqual(self.m.get("/app-icon/unicorn/jade.svg").status_code, 404)
        self.assertEqual(self.m.get("/app-icon/helix/tartan.svg").status_code, 404)
        self.assertEqual(self.m.get("/app-icon/helix/jade/plaid.svg").status_code, 404)
