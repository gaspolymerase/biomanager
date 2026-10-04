"""The app in English or Chinese (app/i18n.py): who gets which language, and
that every translation keeps the values its English carries."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, app, client_for, make_user, uniq

from app import i18n

ROOT = Path(__file__).resolve().parent.parent
ZH = "zh-CN,zh;q=0.9,en;q=0.8"


class WhichLanguage(unittest.TestCase):
    def test_the_first_language_the_browser_asks_for_that_the_app_has(self):
        self.assertEqual(i18n.from_header(ZH), "zh")
        self.assertEqual(i18n.from_header("en-US,en;q=0.9,zh;q=0.8"), "en")
        self.assertEqual(i18n.from_header("fr-FR,zh-TW;q=0.5"), "zh")
        self.assertEqual(i18n.from_header("en;q=0.1,zh;q=0.9"), "zh")
        self.assertEqual(i18n.from_header(""), "en")
        self.assertEqual(i18n.from_header("de"), "en")


class TheSignInPage(AppTestCase):
    def test_a_chinese_browser_gets_chinese_and_others_english(self):
        c = app.test_client()
        zh = c.get("/login", headers={"Accept-Language": ZH}).get_data(as_text=True)
        self.assertIn('<html lang="zh-CN">', zh)
        self.assertIn("登录", zh)
        en = app.test_client().get("/login", headers={"Accept-Language": "en-US"}).get_data(as_text=True)
        self.assertIn('<html lang="en">', en)
        self.assertIn("Sign in", en)
        self.assertNotIn("登录", en)

    def test_the_switch_is_remembered_by_this_browser(self):
        c = app.test_client()
        r = c.post("/language", data={"language": "zh", "next": "/login"},
                   headers={"Accept-Language": "en-US"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/login"))
        self.assertIn("登录", c.get("/login", headers={"Accept-Language": "en-US"}).get_data(as_text=True))

    def test_the_switch_goes_back_only_to_this_site(self):
        r = app.test_client().post("/language", data={"language": "zh", "next": "//evil.example/x"})
        self.assertNotIn("evil.example", r.headers["Location"])


class APersonsChoice(AppTestCase):
    def test_settings_keeps_the_language_for_that_person(self):
        who = make_user(uniq("lang"))
        c = client_for(who)
        self.assertEqual(c.post("/settings", data={"action": "language", "language": "zh"},
                                headers={"Accept-Language": "en-US"}).status_code, 302)
        page = c.get("/settings", headers={"Accept-Language": "en-US"}).get_data(as_text=True)
        self.assertIn('<html lang="zh-CN">', page)
        # A new sign-in elsewhere (an English browser) still gets their choice.
        other = client_for(who)
        self.assertIn('<html lang="zh-CN">', other.get("/settings", headers={"Accept-Language": "en-US"}).get_data(as_text=True))
        # Automatic again: the browser decides.
        c.post("/settings", data={"action": "language", "language": ""})
        self.assertIn('<html lang="en">', client_for(who).get("/settings", headers={"Accept-Language": "en-US"}).get_data(as_text=True))

    def test_the_guide_link_follows_the_language(self):
        who = make_user(uniq("guide"))
        c = client_for(who)
        r = c.get("/guide", headers={"Accept-Language": ZH})
        self.assertIn("/zh/guide.html", r.headers.get("Location", ""))


class TheTranslations(unittest.TestCase):
    """Every catalog is valid JSON, and each translation keeps the %(name)s
    values of its English: a missing one would show the raw placeholder."""

    def test_each_translation_keeps_its_values(self):
        for path in sorted((ROOT / "app" / "translations").glob("*/*.json")):
            words = json.loads(path.read_text(encoding="utf-8"))
            for english, translated in words.items():
                if translated:
                    self.assertEqual(i18n.placeholders(english), i18n.placeholders(translated),
                                     f"{path.name}: {english!r}")

    def test_no_english_appears_twice_with_different_chinese(self):
        seen: dict[str, tuple[str, str]] = {}
        for path in sorted((ROOT / "app" / "translations" / "zh").glob("*.json")):
            for english, translated in json.loads(path.read_text(encoding="utf-8")).items():
                if english in seen and seen[english][1] != translated:
                    self.fail(f"{english!r}: {seen[english][0]} says {seen[english][1]!r}, "
                              f"{path.name} says {translated!r}")
                seen[english] = (path.name, translated)

    def test_every_wrapped_text_in_a_translated_template_has_its_chinese(self):
        """A template that uses _() is translated whole: each of its texts has
        an entry (a template not yet translated has no _() and stays English)."""
        zh = i18n.catalog("zh")
        env = app.jinja_env
        missing, unsafe = [], []
        for path in sorted((ROOT / "app" / "templates").rglob("*.html")):
            source = path.read_text(encoding="utf-8")
            for lineno, _func, message in env.extract_translations(source):
                texts = [message] if isinstance(message, str) else list(message or ())
                if not texts or texts[0] is None:
                    # _(some_value): unescaped, and a % in it fails. Use |tr.
                    unsafe.append(f"{path.relative_to(ROOT)}:{lineno}")
                for text in (m for m in texts if m):
                    if text not in zh:
                        missing.append(f"{path.relative_to(ROOT)}: {text!r}")
        self.assertEqual(unsafe, [], "_() of a value; use {{ value|tr }} instead:\n" + "\n".join(unsafe[:40]))
        self.assertEqual(missing, [], "\n".join(missing[:40]))

    def test_every_gettext_in_python_has_its_chinese(self):
        zh = i18n.catalog("zh")
        lit = r'"((?:[^"\\]|\\.)*)"'
        plain = re.compile(r'(?<![\w.])gettext\(\s*' + lit)
        plural = re.compile(r'\bngettext\(\s*' + lit + r'\s*,\s*' + lit)
        context = re.compile(r'\bpgettext\(\s*' + lit + r'\s*,\s*' + lit)

        def clean(text):
            return text.encode().decode("unicode_escape") if "\\" in text else text

        missing = []
        for path in sorted((ROOT / "app").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            wanted = [clean(t) for t in plain.findall(source)]
            for one, many in plural.findall(source):
                wanted += [clean(one), clean(many)]
            for ctx, text in context.findall(source):
                wanted.append(f"{clean(ctx)}::{clean(text)}" if f"{clean(ctx)}::{clean(text)}" in zh else clean(text))
            missing += [f"{path.name}: {text!r}" for text in wanted if text not in zh]
        self.assertEqual(missing, [], "\n".join(missing[:40]))


class LabelsThatArriveAsValues(AppTestCase):
    """`|tr`: a built-in label is translated; what a lab typed comes back as
    typed, still escaped, and a % in it is harmless."""

    def render(self, value, lang="zh"):
        with app.test_request_context(headers={"Accept-Language": "zh-CN" if lang == "zh" else "en"}):
            return app.jinja_env.from_string("{{ v|tr }}").render(v=value)

    def test_a_built_in_label_is_translated(self):
        self.assertEqual(self.render("Mouse"), "小鼠")
        self.assertEqual(self.render("Mouse", lang="en"), "Mouse")

    def test_a_labs_own_text_stays_escaped_and_intact(self):
        self.assertEqual(self.render("<b>Lab mice</b>"), "&lt;b&gt;Lab mice&lt;/b&gt;")
        self.assertEqual(self.render("GC %"), "GC %")
        self.assertEqual(self.render(None), "")


class Plurals(AppTestCase):
    def test_a_plural_block_takes_its_own_values(self):
        source = "{% trans count=n, who=w %}{{ who }} has one cage{% pluralize %}{{ who }} has {{ count }} cages{% endtrans %}"
        with app.test_request_context(headers={"Accept-Language": "en"}):
            tpl = app.jinja_env.from_string(source)
            self.assertEqual(tpl.render(n=1, w="Sam"), "Sam has one cage")
            self.assertEqual(tpl.render(n=3, w="Sam"), "Sam has 3 cages")

    def test_python_plurals_fill_in_their_values(self):
        with app.test_request_context(headers={"Accept-Language": "en"}):
            self.assertEqual(i18n.ngettext("%(num)s mouse", "%(num)s mice", 2), "2 mice")
            self.assertEqual(i18n.ngettext("%(num)s mouse in %(cage)s", "%(num)s mice in %(cage)s", 1, cage="C1"),
                             "1 mouse in C1")


class DatesAndNotes(AppTestCase):
    def test_dates_read_as_a_chinese_reader_writes_them(self):
        from datetime import date, datetime
        d = date(2026, 10, 3)
        with app.test_request_context(headers={"Accept-Language": "zh-CN"}):
            self.assertEqual(i18n.strftime(d, "%b %d"), "10月3日")
            self.assertEqual(i18n.strftime(d, "%A, %b %d, %Y"), "2026年10月3日 星期六")
            self.assertEqual(i18n.strftime(d, "%a %d %b %Y"), "2026年10月3日 周六")
            self.assertEqual(i18n.strftime(datetime(2026, 10, 3, 9, 5), "%Y-%m-%d %H:%M"), "2026-10-03 09:05")
        with app.test_request_context(headers={"Accept-Language": "en"}):
            self.assertEqual(i18n.strftime(d, "%b %d"), "Oct 03")

    def test_a_word_can_mean_two_things(self):
        with app.test_request_context(headers={"Accept-Language": "zh-CN"}):
            i18n.catalog("zh")["experiment::Active"] = "进行中"
            try:
                self.assertEqual(i18n.pgettext("experiment", "Active"), "进行中")
                self.assertEqual(app.jinja_env.from_string("{{ s|tr('experiment') }}").render(s="Active"), "进行中")
            finally:
                del i18n.catalog("zh")["experiment::Active"]

    def test_every_release_note_line_has_its_chinese(self):
        from app import whats_new
        zh = i18n.catalog("zh")
        missing = [line for note in whats_new.NOTES.values() for key in ("new", "changed", "fixed")
                   for line in note.get(key, []) if line not in zh]
        self.assertEqual(missing, [])


class NotificationsInTheRecipientsLanguage(AppTestCase):
    def test_each_recipient_reads_it_in_their_language(self):
        from app import notify
        from app.db import SessionLocal
        from app.models import NotificationRecord
        zh_reader, en_reader = make_user(uniq("zhread")), make_user(uniq("enread"))
        i18n.catalog("zh")["%(who)s shared a page with you"] = "%(who)s 和你共享了一个页面"
        try:
            with SessionLocal() as s:
                from app import inventory_service
                inventory_service.set_setting(s, i18n.preference_key(zh_reader), "zh")
                s.commit()
                for who in (zh_reader, en_reader):
                    notify.send(s, who, "%(who)s shared a page with you", "Notes typed by Sam",
                                category="notebook", values={"who": "Sam"})
                s.commit()
                titles = {n.recipient_username: (n.title, n.message) for n in s.query(NotificationRecord)
                          .filter(NotificationRecord.recipient_username.in_([zh_reader, en_reader]))}
        finally:
            del i18n.catalog("zh")["%(who)s shared a page with you"]
        self.assertEqual(titles[zh_reader], ("Sam 和你共享了一个页面", "Notes typed by Sam"))
        self.assertEqual(titles[en_reader], ("Sam shared a page with you", "Notes typed by Sam"))

    def test_the_language_last_seen_is_remembered(self):
        who = make_user(uniq("seen"))
        client_for(who).get("/settings", headers={"Accept-Language": ZH})
        from app.db import SessionLocal
        with SessionLocal() as s:
            self.assertEqual(i18n.language_for(s, who), "zh")


class FiltersOnQuotedText(AppTestCase):
    """{{ 'Rack'|tr }} is worked out per page, not once when the template is
    compiled: a Chinese page first must not leave English pages in Chinese."""

    def test_each_page_gets_its_own_language(self):
        tpl = app.jinja_env.from_string("{{ 'Mouse'|tr }} {{ d|date_format('%b') }}")
        from datetime import date
        with app.test_request_context(headers={"Accept-Language": "zh-CN"}):
            self.assertEqual(tpl.render(d=date(2026, 10, 3)), "小鼠 10月")
        with app.test_request_context(headers={"Accept-Language": "en"}):
            self.assertEqual(tpl.render(d=date(2026, 10, 3)), "Mouse Oct")


class TheApiStaysEnglish(AppTestCase):
    def test_a_chinese_client_still_gets_english_from_the_api(self):
        r = app.test_client().get("/api/v1/mice", headers={"Accept-Language": ZH})
        self.assertNotIn("登录", r.get_data(as_text=True))
        with app.test_request_context("/api/v1/mice", headers={"Accept-Language": ZH}):
            app.preprocess_request()
            self.assertEqual(i18n.current(), "en")
