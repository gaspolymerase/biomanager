"""Languages: BioManager in English or Simplified Chinese.

Text is written in English in the code and the templates, wrapped so it can
be translated: `_("Litter born")` in Python and templates,
`{% trans %}…{% endtrans %}` for a template sentence with values in it, and
`t("…")` in page scripts. The Chinese is in `app/translations/zh/*.json`, one
file per area of the app (`"English": "中文"`; page scripts' words in the
`js*.json` files, which are also sent to the browser); text with no entry
stays English. `docs/i18n-glossary.md` lists the words the translations use.

Which language a page is in: the person's choice in Settings (Language), or,
when they have not chosen (and before signing in), the language their
browser or computer asks for first. `session["lang"]` keeps the choice made
for this browser.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path

from flask import g, has_request_context, request, session

LANGUAGES = {"en": "English", "zh": "中文"}
DEFAULT = "en"
TRANSLATIONS = Path(__file__).resolve().parent / "translations"

_catalogs: dict[str, dict[str, str]] = {}


def catalog(lang: str) -> dict[str, str]:
    """Every translation for `lang`, from all its files (read once)."""
    if lang not in _catalogs:
        merged: dict[str, str] = {}
        folder = TRANSLATIONS / lang
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            merged.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if v})
        _catalogs[lang] = merged
    return _catalogs[lang]


_override: list[str] = []


@contextmanager
def using(lang: str):
    """Write text in `lang` for a while: a notification or an email in its
    recipient's language, whoever's page is being made."""
    _override.append(lang if lang in LANGUAGES else DEFAULT)
    try:
        yield
    finally:
        _override.pop()


def current() -> str:
    """The language of the page being made (English outside a request)."""
    if _override:
        return _override[-1]
    if not has_request_context():
        return DEFAULT
    lang = g.get("lang")
    if lang is None:
        lang = g.lang = choose()
    return lang


def from_header(header: str | None) -> str:
    """The first supported language an Accept-Language header asks for."""
    ranked = []
    for i, part in enumerate((header or "").split(",")):
        tag, _, q = part.strip().partition(";q=")
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        ranked.append((-weight, i, tag.strip().lower()))
    for _w, _i, tag in sorted(ranked):
        base = tag.split("-")[0]
        if base in LANGUAGES:
            return base
    return DEFAULT


def choose() -> str:
    """This browser's language: a choice already made, else what it asks for."""
    chosen = session.get("lang")
    if chosen in LANGUAGES:
        return chosen
    return from_header(request.headers.get("Accept-Language"))


def preference_key(username: str) -> str:
    return f"language.{username}"


def preference(db_session, username: str) -> str:
    """A person's choice in Settings: "en", "zh", or "" (follow the browser)."""
    from . import inventory_service
    value = inventory_service.get_setting(db_session, preference_key(username), "")
    return value if value in LANGUAGES else ""


def seen_key(username: str) -> str:
    return f"language.seen.{username}"


def note_seen(db_session, username: str, lang: str) -> None:
    """The language this person's browser last showed BioManager in, for what
    is written to them when they aren't looking (notifications, emails)."""
    from . import inventory_service
    if lang in LANGUAGES and inventory_service.get_setting(db_session, seen_key(username), "") != lang:
        inventory_service.set_setting(db_session, seen_key(username), lang)


def language_for(db_session, username: str) -> str:
    """The language to write to someone in: their choice in Settings, else
    the one their browser last asked for, else English."""
    from . import inventory_service
    chosen = preference(db_session, username)
    if chosen:
        return chosen
    seen = inventory_service.get_setting(db_session, seen_key(username), "")
    return seen if seen in LANGUAGES else DEFAULT


def set_preference(db_session, username: str, lang: str) -> str:
    from . import inventory_service
    lang = lang if lang in LANGUAGES else ""
    inventory_service.set_setting(db_session, preference_key(username), lang)
    remember(lang)
    return lang


def remember(lang: str) -> None:
    """Use `lang` in this browser from now on ("" = follow the browser)."""
    if lang in LANGUAGES:
        session["lang"] = lang
    else:
        session.pop("lang", None)
    g.lang = choose()


def pgettext(context: str, message: str, **values) -> str:
    """`message` as it reads in `context`, when the same English means two
    things ("active" experiment 进行中, "active" account 正常). The catalog
    entry is "context::English"; without one, the plain translation."""
    if current() != DEFAULT:
        words = catalog(current())
        text = words.get(f"{context}::{message}") or words.get(message, message)
    else:
        text = message
    return text % values if values else text


def gettext(message: str, **values) -> str:
    text = catalog(current()).get(message, message) if current() != DEFAULT else message
    return text % values if values else text


def ngettext(singular: str, plural: str, n: int, **values) -> str:
    values.setdefault("num", n)
    if current() != DEFAULT:
        text = catalog(current()).get(singular if n == 1 else plural) or catalog(current()).get(plural)
        if text:
            return text % values
    return (singular if n == 1 else plural) % values


_ = gettext


def _lookup(message: str) -> str:
    """For templates: the translation only. Jinja's newstyle gettext puts the
    values in (`% variables`) itself, so formatting here too would fail."""
    return catalog(current()).get(message, message) if current() != DEFAULT else message


def _nlookup(singular: str, plural: str, n: int) -> str:
    if current() != DEFAULT:
        text = catalog(current()).get(singular if n == 1 else plural) or catalog(current()).get(plural)
        if text:
            return text
    return singular if n == 1 else plural


def js_catalog() -> dict[str, str]:
    """The translations page scripts use (`t("…")`), for the page's language."""
    if current() == DEFAULT:
        return {}
    words: dict[str, str] = {}
    for path in sorted((TRANSLATIONS / current()).glob("js*.json")):
        words.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if v})
    return words


_PLACEHOLDER = re.compile(r"%\((\w+)\)[sd]")


def placeholders(text: str) -> set[str]:
    """The %(name)s values a message carries (a translation must keep them)."""
    return set(_PLACEHOLDER.findall(text))


WEEKDAYS_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
WEEKDAYS_LONG_ZH = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
# English date patterns and how a Chinese reader writes the same thing.
_ZH_PATTERNS = {
    "%b %d": "%-m月%-d日", "%d %b": "%-m月%-d日", "%b %d, %Y": "%Y年%-m月%-d日",
    "%d %b %Y": "%Y年%-m月%-d日", "%A, %b %d, %Y": "%Y年%-m月%-d日 %A",
    "%a %d %b": "%-m月%-d日 %a", "%a %d %b %Y": "%Y年%-m月%-d日 %a",
    "%a %d %b %H:%M": "%-m月%-d日 %a %H:%M", "%b %d, %Y %H:%M": "%Y年%-m月%-d日 %H:%M",
    "%a %d": "%-d日 %a", "%b": "%-m月", "%B": "%-m月", "%b %Y": "%Y年%-m月", "%B %Y": "%Y年%-m月",
    "%a": "%a", "%A": "%A",
}


def strftime(value, pattern: str) -> str:
    """`value.strftime(pattern)` in the page's language. English patterns
    with month or weekday names ("%b %d", "%a %d %b %Y"…) come out as a
    Chinese reader writes them ("10月3日", "2026年10月3日 周六"); a pattern of
    numbers only ("%Y-%m-%d %H:%M") is the same in both."""
    if value is None:
        return ""
    if current() == DEFAULT or not any(code in pattern for code in ("%a", "%A", "%b", "%B")):
        return value.strftime(pattern)
    zh = _ZH_PATTERNS.get(pattern, pattern)
    out = []
    i = 0
    while i < len(zh):
        if zh[i] == "%" and i + 1 < len(zh):
            code = zh[i + 1]
            if code == "-" and i + 2 < len(zh):
                code = zh[i + 2]
                out.append(str({"m": value.month, "d": value.day}.get(code, "")))
                i += 3
                continue
            if code == "a":
                out.append(WEEKDAYS_ZH[value.weekday()])
            elif code == "A":
                out.append(WEEKDAYS_LONG_ZH[value.weekday()])
            elif code in ("b", "B"):
                out.append(f"{value.month}月")
            else:
                out.append(value.strftime("%" + code))
            i += 2
            continue
        out.append(zh[i])
        i += 1
    return "".join(out)


def translate_value(value, context: str = "") -> str:
    """`{{ label|tr }}`: a label that arrives as a value (from Python, or a
    built-in name a lab may have renamed) in the page's language. The text
    of a known label gets its translation; anything else — what a lab typed —
    comes back unchanged. Plain text, so the template still escapes it, and no
    %-formatting, so "GC %" is safe. (`_(value)` would do neither.)
    `{{ status|tr("experiment") }}` prefers the "experiment::active" entry.)"""
    if value is None:
        return ""
    text = str(value)
    if current() == DEFAULT:
        return text
    words = catalog(current())
    if context and f"{context}::{text}" in words:
        return words[f"{context}::{text}"]
    return words.get(text, text)


def init_app(app) -> None:
    # pass_context: the language is the request's, so Jinja must not work a
    # filter on a quoted string out once at compile time ('Rack'|tr would
    # stay in whichever language the template was first shown in).
    from jinja2 import pass_context

    @pass_context
    def tr_filter(_ctx, value, context: str = "") -> str:
        return translate_value(value, context)

    @pass_context
    def date_format_filter(_ctx, value, pattern: str) -> str:
        return strftime(value, pattern)

    app.jinja_env.filters["tr"] = tr_filter
    app.jinja_env.filters["date_format"] = date_format_filter
    app.jinja_env.add_extension("jinja2.ext.i18n")
    app.jinja_env.install_gettext_callables(_lookup, _nlookup, newstyle=True)
    app.jinja_env.globals.update(languages=LANGUAGES, current_language=current, js_catalog=js_catalog,
                                  pgettext=pgettext)
