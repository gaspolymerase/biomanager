"""What this lab uses, and who sees which database.

Four questions, answered in one place so the sidebar, the home page, the
setup survey and the routes all agree:

- **Is a lab function switched on?** The three hand-written databases
  (mouse colony, zebrafish, plasmids) and the calendar and notebook are on
  unless an admin turned them off (`feature:<key>` in app_settings). The
  configurable databases (fly and worm stocks, organism databases,
  inventories) use their own `enabled` flag.
- **Has the lab been set up?** The first admin answers a short survey
  (/setup) choosing what the lab keeps, with a few details for each (its
  name; racks for the mouse colony; incubator temperatures for flies and
  worms). A new installation starts empty: no database exists until the
  survey asks for it (`start_empty`). An older one that predates the survey
  keeps everything on until its admin answers.
- **Who may add databases?** Admins always. Members may add their own
  (personal) databases unless an admin turned that off, and may add
  databases for the whole lab only if an admin allowed it.
- **Who sees a database?** A lab database: everyone. A personal one
  (`private_to` = its owner): its owner, and admins, who look after the lab.
  In the sidebar and on the home page each person sees the lab's databases
  and their own, not other people's personal ones.

Turning something off hides it and refuses its pages; nothing is deleted,
and turning it back on brings everything back.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime

from flask import g

from .i18n import gettext


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    blurb: str
    icon: str
    kind: str                  # "database" | "function"
    path_prefixes: tuple[str, ...]


# The hand-written parts of the app that can be switched off lab-wide.
FEATURES: dict[str, Feature] = {f.key: f for f in (
    Feature("colony", "Mouse colony", "Mice, cages, litters, breeders, experiments and strains.",
            "mouse", "database", ("/colony",)),
    Feature("zebrafish", "Zebrafish", "Tanks on racks, fish, lines, clutches and matings.",
            "fish", "database", ("/zebrafish",)),
    Feature("plasmids", "Plasmids", "Plasmids with sequence maps, in boxes.",
            "plasmid", "database", ("/plasmids",)),
    Feature("calendar", "Calendar", "Lab events, to-dos and colony dates in one calendar.",
            "calendar", "function", ("/calendar",)),
    Feature("notebook", "Notebook", "Protocols and lab notes, linked to animals.",
            "notebook", "function", ("/notebook",)),
)}

# Configurable databases the survey offers, by the preset they are made from.
STOCK_CHOICES = {
    "fly": ("Fruit flies", "Drosophila vials in racks and incubators, with flip schedules.", "fly"),
    "worm": ("C. elegans", "Worm plates, chunking and freezing.", "worm"),
}
INVENTORY_CHOICES = {
    "samples": ("Samples", "Tissue, DNA and other samples, in boxes.", "vial"),
    "orders": ("Orders", "What the lab has asked to buy, and when it arrived.", "cart"),
    "reagents": ("Reagents", "Chemicals and kits, with lots, expiry and low-stock warnings.", "flask"),
    "antibodies": ("Antibodies", "Antibodies with host, target and dilution.", "antibody"),
    "viruses": ("Viruses", "AAV, lentivirus and other vectors: titer, serotype, the plasmid each came from.", "virus"),
    "primers": ("Primers & oligos", "Primers and probes by sequence, with length, GC and Tm worked out.", "dna"),
    "cell_lines": ("Cell lines", "Frozen vials of each line: passage, mycoplasma tests, LN₂ boxes.", "petri"),
}

# Member permissions (app_settings), with their defaults.
MEMBER_PERMISSIONS = {
    "members_create_databases": ("Members may create their own databases",
                                 "A personal database only its owner (and admins) sees.", True),
    "members_share_databases": ("Members may add databases for the whole lab",
                                "Otherwise only admins add lab databases, and members share theirs through an admin.", False),
    "members_keep_copies": ("Members may keep a copy of the lab on their computers",
                            "The desktop app keeps a copy of the whole database, refreshed daily, as a backup. "
                            "Admins always may; a laptop with a copy holds every record.", False),
    "members_api_tokens": ("Members may make API tokens",
                           "A token lets a script, a balance or another tool read the lab's records, or change them, "
                           "as that person. Admins always may.", True),
}

SETUP_DONE_KEY = "lab_setup_done"


# ---------------------------------------------------------------- settings

def _settings():
    from .inventory_service import get_setting, set_setting
    return get_setting, set_setting


def _flag(session, key: str, default: bool) -> bool:
    get_setting, _ = _settings()
    raw = get_setting(session, key, "")
    return default if raw == "" else raw == "on"


def _set_flag(session, key: str, on: bool) -> None:
    _, set_setting = _settings()
    set_setting(session, key, "on" if on else "off")


def feature_on(session, key: str) -> bool:
    return _flag(session, f"feature:{key}", True)


def set_feature(session, key: str, on: bool) -> None:
    _set_flag(session, f"feature:{key}", on)


def features_on(session) -> dict[str, bool]:
    return {key: feature_on(session, key) for key in FEATURES}


def permission(session, key: str) -> bool:
    return _flag(session, key, MEMBER_PERMISSIONS[key][2])


def setup_done(session) -> bool:
    get_setting, _ = _settings()
    return bool(get_setting(session, SETUP_DONE_KEY, ""))


def mark_setup_done(session) -> None:
    _, set_setting = _settings()
    set_setting(session, SETUP_DONE_KEY, datetime.utcnow().isoformat(timespec="seconds"))


STARTED_EMPTY_KEY = "lab_started_empty"

# Offered in the survey for a new fly or worm database: which incubators the
# lab runs, one made for each. Values match the presets' timing tables.
INCUBATOR_TEMPS = {"fly": ("18", "22", "25", "29"), "worm": ("15", "20", "25")}
INCUBATOR_DEFAULTS = {"fly": ("18", "25"), "worm": ("20",)}

# Mouse rack position labels the survey offers, as rack naming schemes.
RACK_LABELS = {
    "letters": ("A1, A2 … B1", {"naming_mode": "grid", "naming_rows": "letters", "naming_cols": "numbers",
                                "naming_order": "row_col", "naming_separator": "", "naming_start": "1"}),
    "numbers": ("1-1, 1-2 … 2-1", {"naming_mode": "grid", "naming_rows": "numbers", "naming_cols": "numbers",
                                   "naming_order": "row_col", "naming_separator": "-", "naming_start": "1"}),
    "sequential": ("1, 2, 3 …", {"naming_mode": "sequential", "naming_rows": "letters", "naming_cols": "numbers",
                                 "naming_order": "row_col", "naming_separator": "", "naming_start": "1"}),
}


def start_empty(session) -> None:
    """A brand-new installation: create nothing until the lab says what it
    keeps. Marks the default databases as already seeded, so they are made
    only when the survey asks for them, and switches the built-in databases
    off until then. Calendar and notebook stay on."""
    _, set_setting = _settings()
    for key in ("inventory_seeded", "stocks_seeded"):
        set_setting(session, key, "1")
    for key in ("colony", "zebrafish", "plasmids"):
        set_feature(session, key, False)
    set_setting(session, STARTED_EMPTY_KEY, "1")


def started_empty(session) -> bool:
    get_setting, _ = _settings()
    return bool(get_setting(session, STARTED_EMPTY_KEY, ""))


def lab_name(session) -> str:
    get_setting, _ = _settings()
    return get_setting(session, "lab_name", "")


def request_features() -> dict[str, bool]:
    """features_on for this request, read once."""
    if "lab_features" not in g:
        from .db import SessionLocal
        with SessionLocal() as session:
            g.lab_features = features_on(session)
    return g.lab_features


def feature_for_path(path: str) -> Feature | None:
    for feature in FEATURES.values():
        if any(path == p or path.startswith(p + "/") for p in feature.path_prefixes):
            return feature
    return None


# ---------------------------------------------------------------- who sees what

def is_personal(module) -> bool:
    return bool(getattr(module, "private_to", "") or "")


def group_of(module):
    """The project group a database is for (app/groups.py), or None."""
    return None if is_personal(module) else getattr(module, "share_group_id", None)


def can_see(module, user=None) -> bool:
    """May this person open the database at all?

    A database switched off in Lab setup is hidden "for everyone": its
    pages refuse members too, not only the sidebar. Admins still reach it,
    to look before switching it back on (the page says it is off)."""
    user = user if user is not None else g.get("user")
    if user is not None and not getattr(module, "enabled", True) and user.role != "admin":
        return False
    if group_of(module):
        from . import groups
        return user is not None and (user.role == "admin" or groups.in_group(module.share_group_id, user))
    if not is_personal(module):
        return True
    if user is None:
        return False
    return user.role == "admin" or module.private_to == user.username


def in_sidebar(module, user=None) -> bool:
    """Is it one of *their* databases: the lab's, their own, or one of their
    project groups'?"""
    user = user if user is not None else g.get("user")
    if group_of(module):
        from . import groups
        return user is not None and groups.in_group(module.share_group_id, user)
    if not is_personal(module):
        return True
    return user is not None and module.private_to == user.username


def visible_list(modules):
    """The databases the signed-in person should see listed: the lab's and
    their own. Outside a request (a script, a reminder email): all."""
    from flask import has_request_context
    if not has_request_context():
        return modules
    return [m for m in modules if in_sidebar(m)]


def may_create_database(session, user=None) -> bool:
    user = user if user is not None else g.get("user")
    if user is None:
        return False
    return user.role == "admin" or permission(session, "members_create_databases")


def may_create_lab_database(session, user=None) -> bool:
    user = user if user is not None else g.get("user")
    if user is None:
        return False
    return user.role == "admin" or permission(session, "members_share_databases")


def audience_for_new(session, requested: str, user=None) -> str:
    """The private_to value for a database this person is creating:
    "" for the lab when they asked for it and may, else their username.
    No choice at all (a script, an older form) means each role's default:
    the lab's for an admin, their own for a member."""
    user = user if user is not None else g.get("user")
    if requested not in ("lab", "me"):
        requested = "lab" if user.role == "admin" else "me"
    if requested == "lab" and may_create_lab_database(session, user):
        return ""
    return user.username


def set_audience_for_new(session, module, requested: str, user=None) -> None:
    """Who a new database is for: the person ("me"), the lab ("lab", when
    they may) or one of their project groups ("group:<id>"); then tell those
    it is for. A member who asked for the lab and may not gets their own."""
    from flask import flash
    from . import groups, notify
    user = user if user is not None else g.get("user")
    group_id = groups.page_share_group(requested or "")
    who = getattr(user, "display_name", "") or user.username
    if group_id is not None and groups.may_share_with(group_id, user):
        module.private_to, module.share_group_id = "", group_id
        notify.tell_group(session, group_id, user.username, "%(who)s added %(db)s for %(group)s",
                          values={"who": who, "db": module.label, "group": groups.name_of(group_id)})
        return
    module.private_to = audience_for_new(session, requested, user)
    module.share_group_id = None
    if module.private_to and requested == "lab":
        flash(gettext("It is yours for now: only lab admins add databases for everyone. Ask one to share it with the lab."), "info")
    if not module.private_to:
        notify.tell_lab(session, user.username, "%(who)s added %(db)s for the lab",
                        values={"who": who, "db": module.label})


def lab_audience(session, user=None) -> dict:
    """For a "new database" form: may this person choose the whole lab, and
    which choice starts selected."""
    from . import groups
    user = user if user is not None else g.get("user")
    may_lab = may_create_lab_database(session, user)
    return {"may_lab": may_lab, "default": "lab" if (user is not None and user.role == "admin") else "me",
            "groups": groups.choices(user)}


def can_change_audience(session, module, user=None) -> bool:
    user = user if user is not None else g.get("user")
    if user is None:
        return False
    if user.role == "admin":
        return True
    mine = (is_personal(module) and module.private_to == user.username) \
        or (bool(group_of(module)) and module.created_by == user.username)
    return mine and permission(session, "members_share_databases")


# ---------------------------------------------------------------- the survey

def _stock_by_kind(session, kind):
    from .models import StockModule
    from sqlalchemy import select
    return session.scalars(select(StockModule).where(StockModule.kind == kind, StockModule.private_to == "",
                                                     StockModule.share_group_id.is_(None))
                           .order_by(StockModule.position, StockModule.id)).all()


def _inventory_by_kind(session, kind):
    from .models import InventoryModule
    from sqlalchemy import select
    return session.scalars(select(InventoryModule).where(InventoryModule.kind == kind,
                                                         InventoryModule.private_to == "",
                                                         InventoryModule.share_group_id.is_(None))
                           .order_by(InventoryModule.position, InventoryModule.id)).all()


# ---------------------------------------------------------------- the lab's time, dates and colony days

# How the app writes a date in text (a sheet's date fields are the
# browser's own and follow the computer's language instead).
DATE_STYLES = {
    "month": "Sep 26, 2026",
    "day": "26 Sep 2026",
    "iso": "2026-09-26",
}
DEFAULT_GENOTYPING_DAY = 28
_ORIGINAL_TZ = os.environ.get("TZ")
_tz_cache: dict = {"at": 0.0, "value": None}


def lab_timezone(session) -> str:
    get_setting, _ = _settings()
    return (get_setting(session, "lab_timezone", "") or "").strip()


def valid_timezone(name: str) -> bool:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:  # noqa: BLE001 — unknown name, or no zone data
        return False


def apply_timezone(name: str) -> None:
    """Make the lab's zone this process's: date.today(), "overdue" and the
    times shown (local_time) then follow the lab, not the server's clock.
    Blank puts back whatever the server started with (TZ in deploy/.env)."""
    if not hasattr(time, "tzset"):
        return   # Windows: the desktop app already runs on the computer's own zone
    wanted = name or _ORIGINAL_TZ
    if os.environ.get("TZ") == wanted:
        return
    if wanted:
        os.environ["TZ"] = wanted
    else:
        os.environ.pop("TZ", None)
    time.tzset()


def refresh_timezone(session_factory, every: float = 30.0) -> None:
    """Pick up a zone changed in Lab setup, in every worker, within `every`
    seconds (called before each request; the setting is read at most that
    often)."""
    now = time.monotonic()
    if _tz_cache["value"] is not None and now - _tz_cache["at"] < every:
        return
    with session_factory() as session:
        name = lab_timezone(session)
    _tz_cache.update(at=now, value=name)
    if not name or valid_timezone(name):
        apply_timezone(name)


def server_timezone() -> str:
    """The zone the server (or computer) runs on by itself, by name."""
    if _ORIGINAL_TZ:
        return _ORIGINAL_TZ
    link = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in link:
        return link.split("zoneinfo/", 1)[1]
    return time.tzname[0] or "UTC"


def clock_zone() -> str:
    """The zone the app's clock runs on now, by name, so a page shows times
    on the same clock as the lines the app writes ("Started: 16:00"), not the
    browser's. Blank on Windows, where the app runs on the computer's zone."""
    if not hasattr(time, "tzset"):
        return ""
    return os.environ.get("TZ") or server_timezone()


def timezone_names() -> list[str]:
    """Region/City names to pick from (Lab setup's list)."""
    try:
        from zoneinfo import available_timezones
    except ImportError:
        return []
    return sorted(z for z in available_timezones()
                  if "/" in z and not z.startswith(("Etc/", "SystemV/", "US/", "posix/", "right/")))


def date_style(session) -> str:
    get_setting, _ = _settings()
    style = get_setting(session, "date_style", "") or "month"
    return style if style in DATE_STYLES else "month"


def genotyping_day(session) -> int:
    """The day after birth the lab genotypes pups (P28 unless chosen)."""
    get_setting, _ = _settings()
    raw = (get_setting(session, "genotyping_day", "") or "").strip()
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= 120 else DEFAULT_GENOTYPING_DAY


def survey_state(session) -> dict:
    """What the survey form should show as chosen right now."""
    return {
        "lab_timezone": lab_timezone(session),
        "date_style": date_style(session),
        "genotyping_day": genotyping_day(session),
        "features": features_on(session),
        "stocks": {kind: any(m.enabled for m in _stock_by_kind(session, kind)) for kind in STOCK_CHOICES},
        "inventories": {kind: any(m.enabled for m in _inventory_by_kind(session, kind)) for kind in INVENTORY_CHOICES},
        "permissions": {key: permission(session, key) for key in MEMBER_PERMISSIONS},
        "lab_name": lab_name(session),
    }


def apply_survey(session, form, actor: str) -> list[str]:
    """Save the survey. Returns the labels of databases and functions it
    switched on that were off, for telling the lab. Nothing is deleted:
    a database the lab stops using is only switched off."""
    from . import database_keys, inventory_service, stock_service
    _, set_setting = _settings()
    before = survey_state(session)
    switched_on: list[str] = []

    for key, feature in FEATURES.items():
        on = form.get(f"feature:{key}") == "1"
        if on and not before["features"][key]:
            switched_on.append(feature.label)
        set_feature(session, key, on)
        name = _name(form, f"name:{key}")
        if on and name and key in inventory_service.BUILTIN_DATABASES:
            name = _own_name(session, name, inventory_service.builtin_labels(session).get(key, ""), builtin=key)
            if name:
                set_setting(session, f"db_label:{key}", name)
    if form.get("feature:colony") == "1":
        _make_mouse_racks(session, form, actor)

    for kind, (label, _blurb, _icon) in STOCK_CHOICES.items():
        wanted = form.get(f"stock:{kind}") == "1"
        existing = _stock_by_kind(session, kind)
        name = _name(form, f"name:stock:{kind}")
        if wanted and name:
            name = _own_name(session, name, existing[0].label if existing else "",
                             kind="stocks", module=existing[0] if existing else None)
        if wanted and not existing:
            from .stocks import PRESETS as STOCK_PRESETS
            module = stock_service.create_module(session, kind, name or STOCK_PRESETS[kind]["label"], actor)
            _make_incubators(session, module, kind, form, actor)
            switched_on.append(module.label)
        for module in existing:
            if wanted and not module.enabled:
                switched_on.append(module.label)
            module.enabled = wanted
            if wanted and name:
                module.label = name
                database_keys.rekey(session, "stocks", module)

    for kind, (label, _blurb, _icon) in INVENTORY_CHOICES.items():
        wanted = form.get(f"inventory:{kind}") == "1"
        existing = _inventory_by_kind(session, kind)
        name = _name(form, f"name:inventory:{kind}")
        if wanted and name:
            name = _own_name(session, name, existing[0].label if existing else "",
                             kind="inventory", module=existing[0] if existing else None)
        if wanted and not existing:
            inventory_service.create_module(session, kind, name or label, actor)
            switched_on.append(name or label)
        for module in existing:
            if wanted and not module.enabled:
                switched_on.append(module.label)
            module.enabled = wanted
            if wanted and name:
                module.label = name
                database_keys.rekey(session, "inventory", module)

    for key in MEMBER_PERMISSIONS:
        _set_flag(session, key, form.get(key) == "1")
    set_setting(session, "lab_name", (form.get("lab_name") or "").strip()[:80])
    if "lab_timezone" in form:
        zone = (form.get("lab_timezone") or "").strip()
        if not zone or valid_timezone(zone):
            set_setting(session, "lab_timezone", zone)
            _tz_cache["value"] = None
            apply_timezone(zone)
    if form.get("date_style") in DATE_STYLES:
        set_setting(session, "date_style", form["date_style"])
    raw_day = (form.get("genotyping_day") or "").strip()
    if raw_day.isdigit() and 1 <= int(raw_day) <= 120:
        set_setting(session, "genotyping_day", raw_day)
    mark_setup_done(session)
    return switched_on


def _name(form, field: str) -> str:
    return (form.get(field) or "").strip()[:80]


def _own_name(session, name: str, current: str = "", **whose) -> str:
    """`name`, unless another database is already called that (every
    database has its own name, database_keys.name_clash): then "" (the
    database keeps the name it has) and a note saying why."""
    from flask import flash
    from . import database_keys
    if not name or name.casefold() == (current or "").casefold():
        return name
    clash = database_keys.name_clash(session, name, **whose)
    if clash:
        flash(gettext("“%(name)s” was not used: there is already a database called %(clash)s.", name=name,
                      clash=clash), "error")
        return ""
    return name


def _make_mouse_racks(session, form, actor: str) -> int:
    """The survey's "racks" answer: that many racks, all the same size and
    labelled the same way. Only for a colony with no racks yet."""
    from sqlalchemy import func, select
    from . import positions
    from .models import MouseRack
    try:
        count = max(0, min(20, int(form.get("racks:count") or 0)))
        rows = max(1, min(26, int(form.get("racks:rows") or 5)))
        cols = max(1, min(40, int(form.get("racks:cols") or 7)))
    except ValueError:
        return 0
    if not count or session.scalar(select(func.count(MouseRack.id))):
        return 0
    _label, naming = RACK_LABELS.get(form.get("racks:labels"), RACK_LABELS["letters"])
    scheme = json.dumps(positions.scheme_from_form(naming))
    for n in range(1, count + 1):
        session.add(MouseRack(name=f"Rack {n}", rows=rows, cols=cols, naming=scheme, created_by=actor))
    return count


def _make_incubators(session, module, kind: str, form, actor: str) -> None:
    """One incubator for each temperature the lab ticked, and the most
    usual of them as the database's default temperature."""
    from .models import StockIncubator
    from .stock_service import norm_temp
    chosen = [t for t in INCUBATOR_TEMPS[kind] if t in form.getlist(f"temps:{kind}")]
    for temp in chosen:
        session.add(StockIncubator(module_id_fk=module.id, name=f"Incubator {temp} °C",
                                   temperature=norm_temp(temp), created_by=actor))
    if chosen:
        settings = json.loads(module.settings or "{}")
        if settings.get("default_temperature") not in chosen:
            settings["default_temperature"] = chosen[0]
            module.settings = json.dumps(settings)


def custom_databases(session) -> list[dict]:
    """Every configurable database the survey does not cover by kind: made
    from the organism builder, a custom inventory, or someone's own."""
    from . import inventory_service, organism_service, stock_service
    out = []
    for kind_label, service, prefix in (("Animals", organism_service, "organisms"),
                                        ("Stocks", stock_service, "stocks"),
                                        ("Inventory", inventory_service, "inventory")):
        for module in service.list_modules(session, include_disabled=True, everyone=True):
            covered = ((prefix == "stocks" and module.kind in STOCK_CHOICES and not is_personal(module))
                       or (prefix == "inventory" and module.kind in INVENTORY_CHOICES and not is_personal(module)))
            if covered:
                continue
            out.append({"kind": prefix, "kind_label": kind_label, "key": module.key, "label": module.label,
                        "enabled": module.enabled, "private_to": module.private_to or "",
                        "created_by": module.created_by or ""})
    return out


def set_module_enabled(session, kind: str, key: str, on: bool):
    module = _module(session, kind, key)
    if module is not None:
        module.enabled = on
    return module


def _module(session, kind: str, key: str):
    from . import inventory_service, organism_service, stock_service
    service = {"organisms": organism_service, "stocks": stock_service, "inventory": inventory_service}.get(kind)
    return service.get_module(session, key) if service else None


def module_for(session, kind: str, key: str):
    return _module(session, kind, key)


def everyone_but(session, username: str) -> list[str]:
    from sqlalchemy import select
    from .models import UserAccount
    return list(session.scalars(select(UserAccount.username).where(
        UserAccount.username != username, UserAccount.disabled.is_(False),
        UserAccount.role != "pending")).all())



# ---------------------------------------------------------------- getting started

GUIDE_URL = "https://biomanager.org/guide.html"


def guide_url() -> str:
    """The user guide in the page's language (the Chinese one is under /zh/)."""
    from . import i18n
    return GUIDE_URL.replace("/guide.html", "/zh/guide.html") if i18n.current() == "zh" else GUIDE_URL


def did(session, user, milestone: str) -> bool:
    """Has this person done a one-off thing the data does not show (printed
    cage cards, opened the guide)?"""
    get_setting, _ = _settings()
    return bool(get_setting(session, f"did:{user.id}:{milestone}", ""))


def mark_did(session, user, milestone: str) -> None:
    _, set_setting = _settings()
    set_setting(session, f"did:{user.id}:{milestone}", datetime.utcnow().isoformat(timespec="seconds"))


def getting_started_hidden(session, user) -> bool:
    get_setting, _ = _settings()
    return bool(get_setting(session, f"getting_started_hidden:{user.id}", ""))


def hide_getting_started(session, user) -> None:
    _, set_setting = _settings()
    set_setting(session, f"getting_started_hidden:{user.id}", "1")


def getting_started(session, user, on_server: bool) -> list[dict]:
    """The first steps, each ticked off by the data itself.

    An admin sets the lab up: a rack exists, mice exist, cage cards were
    printed. Those are lab-wide, so a step one admin did is done for the
    others. A member joins a lab that is already set up, so theirs are their
    own (member_getting_started); a guest, here for a short visit, has none.
    """
    if getattr(user, "expires_at", None) is not None:
        return []
    if user.role != "admin":
        return member_getting_started(session, user)
    from flask import url_for
    from sqlalchemy import func, select
    from . import inventory_service, stock_service
    from .models import (FishRecord, InventoryItem, MouseRack, MouseRecord, PlasmidRecord, StockRack,
                         StockUnit, UserAccount)

    def exists(stmt) -> bool:
        return session.scalar(stmt.limit(1)) is not None

    def step(title, hint, url, done, external=False):
        return {"title": title, "hint": hint, "url": url, "done": bool(done), "external": external}

    features = features_on(session)
    steps: list[dict] = []
    if features["colony"]:
        steps.append(step("Add a rack", "Cages → Rack grid → New rack, labelled like the stickers on your racks.",
                          url_for("colony", view="cages"), exists(select(MouseRack.id))))
        steps.append(step("Bring in your mice", "Mice → Import from Excel brings in the sheet you keep them in; Add many describes a new group.",
                          url_for("colony", view="mice"), exists(select(MouseRecord.id))))
        steps.append(step("Print cage cards", "Cages → Cage cards. Scanning a card's QR code opens its cage.",
                          url_for("labels.cage_cards"), did(session, user, "cage_cards")))
    if features["zebrafish"]:
        steps.append(step("Add a tank and its fish", "New tank, then New fish to put a group in it.",
                          url_for("zebrafish"), exists(select(FishRecord.id))))
    for module in stock_service.list_modules(session):
        mv = stock_service.view(module)
        unit = getattr(mv, "unit", "vial")
        steps.append(step(f"Set up {module.label}", "Incubators & racks: add a rack inside an incubator.",
                          url_for("stocks.module", key=module.key),
                          exists(select(StockRack.id).where(StockRack.module_id_fk == module.id))))
        steps.append(step(f"Add your first {unit}", f"New {unit}: its genotype and purpose.",
                          url_for("stocks.module", key=module.key),
                          exists(select(StockUnit.id).where(StockUnit.module_id_fk == module.id))))
    if features["plasmids"]:
        steps.append(step("Add a plasmid", "Upload its GenBank or FASTA file to see the map.",
                          url_for("plasmids"), exists(select(PlasmidRecord.id))))
    for module in inventory_service.list_modules(session):
        steps.append(step(f"Add to {module.label}", "One item, or several with Add many.",
                          url_for("inventory.module", key=module.key),
                          exists(select(InventoryItem.id).where(InventoryItem.module_id_fk == module.id))))
    if on_server and user.role == "admin":
        others = session.scalar(select(func.count(UserAccount.id)).where(
            UserAccount.id != user.id, UserAccount.disabled.is_(False))) or 0
        steps.append(step("Invite your lab", "Send members this address. They sign up, and you approve them in Manage users.",
                          url_for("admin_users"), others > 0))
    steps.append(step("Read the user guide", "Ten minutes on everything BioManager does. Help in the sidebar opens it too.",
                      guide_url(), did(session, user, "guide"), external=True))
    return steps


def member_getting_started(session, user) -> list[dict]:
    """A member's own first steps: find what is theirs, do one thing of their
    own, scan a card. Ticked off by what this person did, not the lab."""
    from flask import url_for
    from sqlalchemy import select
    from . import inventory_service
    from .models import InventoryItem, MouseRecord, PlasmidRecord

    def exists(stmt) -> bool:
        return session.scalar(stmt.limit(1)) is not None

    def step(title, hint, url, done, external=False):
        return {"title": title, "hint": hint, "url": url, "done": bool(done), "external": external}

    me = user.username
    features = features_on(session)
    steps: list[dict] = []
    if features["colony"]:
        steps.append(step("Find your mice", "Mice, then the Mine chip: the mice that are yours. "
                          "Ask whoever had them to hand over any that should be.",
                          url_for("colony", view="mice", scope="mine"),
                          did(session, user, "colony") or exists(select(MouseRecord.id).where(MouseRecord.owner == me))))
        steps.append(step("Scan a cage card", "Point your phone's camera at the QR code on a cage card: "
                          "it opens that cage here, ready to edit.",
                          url_for("colony", view="cages"), did(session, user, "scan")))
    for module in inventory_service.list_modules(session):
        if module.kind != "orders":
            continue
        steps.append(step(f"Request something in {module.label}",
                          "New order: what, from whom, how many. You are told when it arrives.",
                          url_for("inventory.module", key=module.key),
                          exists(select(InventoryItem.id).where(InventoryItem.module_id_fk == module.id,
                                                                InventoryItem.owner == me))))
        break
    if features["plasmids"]:
        steps.append(step("Add a plasmid of yours", "Upload its GenBank or FASTA file to see the map.",
                          url_for("plasmids"), exists(select(PlasmidRecord.id).where(PlasmidRecord.owner == me))))
    steps.append(step("Read the user guide", "Ten minutes on everything BioManager does. Help in the sidebar opens it too.",
                      guide_url(), did(session, user, "guide"), external=True))
    return steps


def getting_started_pending(session, user, on_server: bool) -> bool:
    """Should this person start on the home page, where Getting started is?"""
    if not setup_done(session) or getting_started_hidden(session, user):
        return False
    return not all(s["done"] for s in getting_started(session, user, on_server))
