"""Routes for the configurable organism modules.

A blueprint rather than more of app.py, because none of this is
species-specific: one set of views serves every module, driven by the
module's capabilities.

Login is enforced for the whole blueprint in `before_request`; app.py's own
`before_request` has already resolved `g.user` by then.

Write rules every handler here follows:

  * Records follow access.can_edit (yours, unowned, or you are an admin);
    the database's definition — fields, rules, racks, vocabulary — follows
    access.can_configure (its creator or an admin).
  * A save only writes the inputs the form actually sent, so a dialog that
    did not render a field (capability off) or a one-cell inline edit never
    blanks the rest. Checkboxes are read only from full dialog forms, which
    carry `_full=1`.
  * Anything that can move a due date recomputes the schedule.
  * Inline sheet edits send `X-Autosave: 1` and get JSON back (see
    static/sheet.js for the reply shape).
"""
from __future__ import annotations

import json
from datetime import date, datetime

from flask import (
    Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for,
)
from sqlalchemy import func, select

from .db import SessionLocal
from . import i18n, lab
from .i18n import gettext, ngettext
from .lab import lab_audience
from .formutil import form_changed
from .models import (
    ModuleField,
    OrgCohort,
    OrgCross,
    OrgGenotype,
    OrgHousing,
    OrgLine,
    OrgLocation,
    OrgMeasurement,
    OrgPreservationLot,
    Organism,
    OrganismModule,
)
from . import access
from . import audit, database_keys
from . import organism_service as svc
from . import inventory as inventory_presets
from . import stocks as stock_presets
from . import positions
from .icons import housing_icon
from .organisms import (
    AGE_UNITS,
    FIELD_ENTITIES,
    FIELD_TYPE_BY_KEY,
    FIELD_TYPES,
    IDENTITY_MODES,
    SCHEDULE_ANCHORS,
    anchor_allowed,
    capability_groups,
    pluralise,
)

bp = Blueprint("organisms", __name__, url_prefix="/organisms")


# The sub-views a module can show, in order, each gated on a capability.
# `None` means always available. Labels come from the module's own nouns
# (see _views_for), so a fly database says "Vials" and a mouse one "Cages".
MODULE_VIEWS = [
    ("animals", None, None),
    ("housing", None, "housing"),
    ("lines", "sitemap", "lines"),
    ("crosses", "heart", "crosses"),
    ("cohorts", "baby", "cohorts"),
    ("experiments", "flask", None),
    ("genotyping", "microscope", "genotyping"),
    ("schedule", "calendar-clock", "schedule"),
    ("environment", "droplet", "environment"),
    ("preservation", "snowflake", "preservation"),
    ("settings", "sliders", None),
]


# Header glyph for a custom field, by what kind of value it holds.
FIELD_ICONS = {
    "text": "type", "textarea": "note", "mono": "dna", "number": "count", "date": "calendar",
    "select": "tag", "checkbox": "check", "user": "user", "line": "sitemap", "url": "link",
}


def field_icon(field) -> str:
    return FIELD_ICONS.get(getattr(field, "field_type", ""), "type")


def _word(noun: str) -> str:
    """One of a module's own words ("vial", "stocks") in the page's
    language when it is a preset's; what a lab typed stays as typed."""
    return i18n.translate_value(noun, "noun")


@bp.app_context_processor
def inject_helpers():
    """Helpers the module templates need. `age_label` renders an age in the
    unit the organism's community uses, which only the module knows."""
    return {"age_label": svc.age_label, "field_icon": field_icon}


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login"))
    return None


def _parse_date(raw: str | None) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _int(raw, default=0):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _ref(session, model, module_id: int, raw) -> int | None:
    """Resolve a submitted foreign key, but only within this module.

    Every relation in the engine is module-local, so an id belonging to a
    different module is not a valid reference — it is either a stale form or
    a crafted request, and in both cases the right answer is None rather
    than silently linking two species' records together.
    """
    row_id = _int(raw, 0)
    if not row_id:
        return None
    row = session.get(model, row_id)
    if row is None or getattr(row, "module_id_fk", None) != module_id:
        return None
    return row_id


def _module_or_404(session, key: str) -> OrganismModule:
    module = svc.get_module(session, key)
    # Someone else's personal database does not exist, as far as this
    # person can tell (app/lab.py).
    if module is None or not lab.can_see(module):
        abort(404)
    database_keys.to_current(module, key)   # an address it had before a rename
    return module


def _views_for(mv: svc.ModuleView) -> list[dict]:
    labels = {
        "animals": _word(mv.organism_noun_plural), "housing": _word(mv.housing_noun_plural),
        "lines": _word(mv.line_noun_plural), "crosses": _word(mv.cross_noun_plural),
        "cohorts": _word(mv.cohort_noun_plural), "genotyping": gettext("Genotyping"),
        "schedule": gettext("Schedule"), "environment": gettext("Environment"),
        "preservation": gettext("Cryo"), "settings": gettext("Configure"),
        "experiments": gettext("Experiments"),
    }
    out = []
    for key, icon, capability in MODULE_VIEWS:
        if capability and capability not in mv.capabilities:
            continue
        # "Animals" is meaningless if the module tracks neither, and so are
        # experiments on them.
        if key in ("animals", "experiments") and not mv.any_of("individuals", "group_counts"):
            continue
        if key == "animals":
            icon = mv.icon
        elif key == "housing":
            icon = housing_icon(mv.housing_noun)
        label = (labels.get(key) or key).strip()
        out.append({"key": key, "label": label[:1].upper() + label[1:], "icon": icon})
    return out


# ---------------------------------------------------------------------------
# Small write helpers
# ---------------------------------------------------------------------------


def _autosave() -> bool:
    return request.headers.get("X-Autosave") == "1"


def _redirect_back(key: str, view: str):
    return redirect(url_for("organisms.module", key=key, view=view))


def _fail(key: str, view: str, message: str, status: int = 400):
    """Refuse a write: JSON for the sheet, a flash for a form."""
    if _autosave():
        return jsonify({"ok": False, "error": message}), status
    flash(message, "error")
    return _redirect_back(key, view)


def _set_text(row, form, name: str, attr: str | None = None, limit: int | None = None) -> None:
    if name in form:
        value = (form.get(name) or "").strip()
        setattr(row, attr or name, value[:limit] if limit else value)


def _set_date(row, form, name: str) -> None:
    if name in form:
        setattr(row, name, _parse_date(form.get(name)))


def _set_ref(session, row, form, name: str, model, module_id: int) -> None:
    if name in form:
        setattr(row, name, _ref(session, model, module_id, form.get(name)))


def _set_flag(row, form, name: str, attr: str | None = None, invert: bool = False) -> None:
    """Checkboxes submit nothing when unticked, so they are only read from a
    form that renders them (the dialog, `_full=1`) or that sends them."""
    if form.get("_full") == "1" or name in form:
        value = bool(form.get(name))
        setattr(row, attr or name, (not value) if invert else value)


def _deny_configure(module: OrganismModule, key: str):
    if access.can_configure(module):
        return None
    return _fail(key, "settings",
                 gettext("Only an admin or whoever created this database can change its setup."), 403)


def _load_owned(session, model, module: OrganismModule, row_id: int, key: str, view: str):
    """(row, error_response) for an edit of an existing record."""
    row = session.get(model, row_id)
    if row is None or row.module_id_fk != module.id:
        if _autosave():
            return None, (jsonify({"ok": False, "error": gettext("That record no longer exists.")}), 404)
        abort(404)
    if not access.can_edit(row):
        return None, _fail(key, view, access.reason_denied(row), 403)
    return row, None


def _iso(value) -> str:
    return value.isoformat() if value else ""


# ---------------------------------------------------------------------------
# Index and builder
# ---------------------------------------------------------------------------


@bp.route("/")
def index():
    """Every database in one place: the built-in pages, the organism
    modules and the lab inventories, each renameable."""
    from . import inventory_service as inventories
    from .models import InventoryItem, MouseRecord, PlasmidRecord, TankRecord
    from .services import END_STATUSES

    from . import stock_service as stocks
    from .models import StockUnit

    with SessionLocal() as session:
        # Admins look after every database, personal ones included; members
        # see the lab's and their own (app/lab.py).
        everyone = access.is_admin()
        stock_modules = stocks.list_modules(session, include_disabled=True, everyone=everyone)
        # (by any key a stock database has had: a renamed Drosophila too)
        moved = {m.key for m in stock_modules} | set(database_keys.aliases_by_key(session, "stocks"))
        # Old fly/worm modules that moved to the stock pages are not listed.
        modules = [m for m in svc.list_modules(session, include_disabled=True, everyone=everyone)
                   if m.enabled or m.key not in moved]
        share = lambda row: lab.can_change_audience(session, row)
        unit_counts = dict(session.execute(select(StockUnit.module_id_fk, func.count())
                                           .where(StockUnit.active.is_(True))
                                           .group_by(StockUnit.module_id_fk)).all())
        stock_cards = [{"module": stocks.view(m), "count": unit_counts.get(m.id, 0), "row": m,
                        "can_share": share(m)} for m in stock_modules]
        cards = []
        for module in modules:
            cards.append({
                "module": svc.view(module),
                "census": svc.census(session, module),
                "capabilities": svc.capability_labels(module),
                "can_configure": access.can_configure(module),
                "row": module,
                "can_share": share(module),
            })
        names = inventories.builtin_labels(session)
        count = lambda model: session.scalar(select(func.count()).select_from(model)) or 0
        # Alive mice, the way the colony page counts them: no date of death
        # and no end-of-life (or transferred) status.
        inactive = sorted(END_STATUSES | {"transfer", "transferred"})
        alive_mice = session.scalar(select(func.count(MouseRecord.id)).where(
            MouseRecord.date_of_death.is_(None),
            func.lower(func.trim(func.coalesce(MouseRecord.status, ""))).notin_(inactive))) or 0
        active_tanks = session.scalar(select(func.count(TankRecord.id)).where(
            TankRecord.active.is_(True))) or 0
        builtins = [
            {"key": "colony", "icon": "mouse", "url": url_for("colony", view="mice"),
             "count": alive_mice, "noun": "alive mice"},
            {"key": "zebrafish", "icon": "fish", "url": url_for("zebrafish"),
             "count": active_tanks, "noun": "active tanks"},
            {"key": "plasmids", "icon": "plasmid", "url": url_for("plasmids"),
             "count": count(PlasmidRecord), "noun": "plasmids"},
        ]
        features = lab.features_on(session)
        for b in builtins:
            b["label"] = names[b["key"]]
            b["default"] = inventories.BUILTIN_DATABASES[b["key"]][0]
            b["on"] = features.get(b["key"], True)
        # No database is there by default: these three show once the lab has
        # them (the setup survey, or New database), like any other.
        builtins = [b for b in builtins if b["on"]]
        for b in builtins:
            b["configure_url"] = url_for("organisms.configure_builtin", key=b["key"])
            b["blurb"] = lab.FEATURES[b["key"]].blurb
        item_counts = dict(session.execute(select(InventoryItem.module_id_fk, func.count())
                                           .group_by(InventoryItem.module_id_fk)).all())
        inventory_cards = [{"module": inventories.view(m), "count": item_counts.get(m.id, 0), "row": m,
                            "can_share": share(m)}
                           for m in inventories.list_modules(session, include_disabled=True, everyone=everyone)]
        return render_template("organisms/index.html", cards=cards, builtins=builtins,
                               inventory_cards=inventory_cards, stock_cards=stock_cards,
                               is_admin=access.is_admin())


@bp.route("/builtin/<key>/rename", methods=["POST"])
def rename_builtin(key: str):
    """Rename the mouse colony, zebrafish or plasmid pages (admins only).
    An empty name goes back to the default."""
    from . import inventory_service as inventories

    if key not in inventories.BUILTIN_DATABASES:
        abort(404)
    if not access.is_admin():
        flash(gettext("Only an admin can rename the built-in databases."), "error")
        return redirect(url_for("organisms.index"))
    label = (request.form.get("label") or "").strip()[:80]
    with SessionLocal() as session:
        clash = database_keys.name_clash(session, label, builtin=key)
        if clash:
            flash(gettext("There is already a database called %(name)s; give this one a name of its own.",
                          name=clash), "error")
            return redirect(url_for("organisms.configure_builtin", key=key))
        inventories.set_setting(session, f"db_label:{key}", label)
        session.commit()
    flash(gettext("Renamed to %(name)s.",
                  name=label or i18n.translate_value(inventories.BUILTIN_DATABASES[key][0])), "success")
    return redirect(url_for("organisms.configure_builtin", key=key))


@bp.route("/builtin/<key>/configure")
def configure_builtin(key: str):
    """Rename the mouse colony, zebrafish or plasmid pages, or take one out
    of the lab (admins). Their records and columns are set on their own
    pages; this is what every other database's Configure also offers."""
    from . import inventory_service as inventories

    if key not in inventories.BUILTIN_DATABASES:
        abort(404)
    if not access.is_admin():
        flash(gettext("Only an admin can configure this database."), "error")
        return redirect(url_for("organisms.index"))
    feature = lab.FEATURES[key]
    with SessionLocal() as session:
        label = inventories.builtin_labels(session)[key]
        on = lab.feature_on(session, key)
    return render_template("organisms/builtin_configure.html", key=key, feature=feature, label=label,
                           default=inventories.BUILTIN_DATABASES[key][0], on=on,
                           open_url={"colony": url_for("colony", view="mice"), "zebrafish": url_for("zebrafish"),
                                     "plasmids": url_for("plasmids")}[key])


@bp.route("/builtin/<key>/switch", methods=["POST"])
def switch_builtin(key: str):
    """Add the mouse colony, zebrafish or plasmid pages to the lab, or take
    one out. Taking out hides it and refuses its pages; nothing is deleted,
    and adding it back brings every record back."""
    from . import inventory_service as inventories

    if key not in inventories.BUILTIN_DATABASES:
        abort(404)
    if not access.is_admin():
        flash(gettext("Only an admin can add or remove this database."), "error")
        return redirect(url_for("organisms.index"))
    on = request.form.get("on") == "1"
    with SessionLocal() as session:
        lab.set_feature(session, key, on)
        name = inventories.builtin_labels(session)[key]
        session.commit()
    if on:
        flash(gettext("%(name)s added to the lab.", name=i18n.translate_value(name)), "success")
        return redirect({"colony": url_for("colony", view="mice"), "zebrafish": url_for("zebrafish"),
                         "plasmids": url_for("plasmids")}[key])
    flash(gettext("%(name)s taken out of the lab. Nothing was deleted: add it back from New database and every record is there.", name=i18n.translate_value(name)), "success")
    return redirect(url_for("organisms.index"))


# Builder inputs a person types; kept across a preset switch.
TYPED_INPUTS = ("label", "label_plural", "blurb")


@bp.route("/new", methods=["GET", "POST"])
def new_module():
    with SessionLocal() as session:
        if not lab.may_create_database(session):
            flash(gettext("An admin has turned off adding databases for members. Ask a lab admin."), "error")
            return redirect(url_for("organisms.index"))
        if request.method == "POST":
            spec = _spec_from_form(request.form)
            if not spec["label"]:
                flash(gettext("Give the database a name."), "error")
                return redirect(url_for("organisms.new_module"))
            clash = database_keys.name_clash(session, spec["label"])
            if clash:
                flash(gettext("There is already a database called %(name)s; give this one a name of its own.",
                              name=clash), "error")
                return redirect(url_for("organisms.new_module"))
            spec["key"] = svc.unique_key(session, spec["label"])
            module = svc.create_module(session, spec, created_by=g.user.username)
            lab.set_audience_for_new(session, module, request.form.get("audience", ""))
            svc.recompute_due(session, module)
            session.commit()
            flash(gettext("Created the %(name)s database.", name=module.label), "success")
            return redirect(url_for("organisms.module", key=module.key, view="settings"))

        preset_key = request.args.get("preset", "")
        prefill = svc.preset_spec(preset_key) if preset_key else {}
        # A name typed before picking a preset survives the switch.
        for name in TYPED_INPUTS:
            typed = (request.args.get(name) or "").strip()
            if typed:
                prefill[name] = typed[:120]
        return render_template(
            "organisms/new.html",
            presets=svc.available_presets(),
            prefill=prefill,
            preset_key=preset_key,
            capability_groups=capability_groups(),
            identity_modes=IDENTITY_MODES,
            age_units=AGE_UNITS,
            inventory_presets=inventory_presets.PRESETS,
            stock_presets=stock_presets.PRESETS,
            audience=lab_audience(session),
            # The mouse colony, zebrafish and plasmid pages the lab has not got
            # yet: an admin adds them from here, as they would any database.
            builtin_choices=[f for f in lab.FEATURES.values()
                             if f.kind == "database" and not lab.feature_on(session, f.key)]
            if access.is_admin() else [],
        )


def _spec_from_form(form) -> dict:
    """Build a module spec from the builder form.

    A preset can be used as the base, in which case the form only overrides
    what the user actually changed — that keeps the preset's schedule rules
    and field definitions, which the form does not expose.
    """
    base = svc.preset_spec(form.get("preset_key", "")) or {}
    label = (form.get("label") or "").strip()

    spec = dict(base)
    spec.pop("key", None)
    spec["label"] = label
    spec["label_plural"] = (form.get("label_plural") or label).strip()
    spec["icon"] = (form.get("icon") or base.get("icon") or "paw").strip()
    spec["blurb"] = (form.get("blurb") or "").strip()
    spec["identity_mode"] = form.get("identity_mode") or base.get("identity_mode") or "hybrid"
    spec["age_unit"] = form.get("age_unit") or base.get("age_unit") or "days"
    spec["capabilities"] = form.getlist("capabilities")

    for noun in ("organism_noun", "organism_noun_plural", "housing_noun",
                 "housing_noun_plural", "container_noun", "line_noun",
                 "line_noun_plural", "cohort_noun", "cohort_noun_plural",
                 "cross_noun"):
        value = (form.get(noun) or "").strip()
        if value:
            spec[noun] = value
    cross_plural = (form.get("cross_noun_plural") or "").strip()
    if cross_plural:
        spec["settings"] = dict(spec.get("settings") or {}, cross_noun_plural=cross_plural)

    # Keep the preset's rules only for capabilities that survived.
    caps = set(svc.normalize_capabilities(spec["capabilities"])) if spec["capabilities"] else set()
    if "schedule" not in caps:
        spec["schedule_rules"] = []
    return spec


# ---------------------------------------------------------------------------
# Module workbench
# ---------------------------------------------------------------------------


@bp.route("/<key>")
def module(key: str):
    from . import stock_service
    with SessionLocal() as session:
        row = _module_or_404(session, key)
        # Flies and worms moved to their own vial/plate pages.
        if not row.enabled and stock_service.get_module(session, key) is not None:
            return redirect(url_for("stocks.module", key=key))
        mv = svc.view(row)
        views = _views_for(mv)
        active = request.args.get("view") or (views[0]["key"] if views else "settings")
        if active not in {v["key"] for v in views}:
            active = views[0]["key"] if views else "settings"

        me = access.username()
        fields = svc.fields_by_entity(session, row.id)
        ctx = {
            "module": mv,
            "module_row": row,
            "views": views,
            "active_view": active,
            "census": svc.census(session, row),
            "fields": fields,
            "usernames": _usernames(session),
            "field_types": FIELD_TYPES,
            "field_entities": FIELD_ENTITIES,
            "capability_groups": capability_groups(),
            "identity_modes": IDENTITY_MODES,
            "age_units": AGE_UNITS,
            "schedule_anchors": SCHEDULE_ANCHORS,
            "today": date.today().isoformat(),
            "today_date": date.today(),
            "metrics_text": svc.metrics_to_text(svc.measurement_metrics(mv)),
            "housing_icon": housing_icon(mv.housing_noun),
            "can_configure": access.can_configure(row),
            "me": me,
        }
        # A new record's custom fields start at their defaults.
        ctx["attr_defaults"] = {
            entity: {f.key: f.default_value for f in rows if f.default_value}
            for entity, rows in fields.items()
        }

        lines = session.scalars(
            select(OrgLine).where(OrgLine.module_id_fk == row.id)
            .order_by(OrgLine.retired, OrgLine.code)
        ).all()
        ctx["lines"] = lines

        housing = session.scalars(
            select(OrgHousing).where(OrgHousing.module_id_fk == row.id)
            .order_by(OrgHousing.active.desc(), OrgHousing.code)
        ).all()
        ctx["housing_units"] = housing

        if active == "animals":
            organisms = session.scalars(
                select(Organism).where(Organism.module_id_fk == row.id)
                .order_by(Organism.death_on.is_(None).desc(), Organism.id.desc())
            ).all()
            ctx["animal_rows"] = [{
                "o": o, "editable": access.can_edit(o), "alive": mv.is_alive(o),
                "mine": bool(me) and o.owner == me,
            } for o in organisms]
            ctx["chip_counts"] = {
                "all": len(organisms),
                "alive": sum(1 for r in ctx["animal_rows"] if r["alive"]),
                "mine": sum(1 for r in ctx["animal_rows"] if r["mine"]),
            }
            ctx["cohorts"] = session.scalars(
                select(OrgCohort).where(OrgCohort.module_id_fk == row.id)
                .order_by(OrgCohort.code)
            ).all()
            ctx["next_code"] = svc.next_code(session, row, "organism")
            if mv.has("genotyping"):
                ctx["latest_calls"] = svc.latest_calls(session, row.id)
                ctx["assays"] = svc.genotype_assays(session, row.id)
                ctx["zygosities"] = svc.ZYGOSITIES

        elif active == "housing":
            ctx["locations"] = svc.location_tree(session, row.id)
            ctx["occupancy"] = _occupancy(session, row)
            ctx["next_code"] = svc.next_code(session, row, "housing")
            ctx["unit_positions"] = {u.id: housing_position_label(u) for u in housing}
            ctx["housing_rows"] = [{
                "u": u, "editable": access.can_edit(u), "mine": bool(me) and u.owner == me,
            } for u in housing]
            ctx["chip_counts"] = {
                "all": len(housing),
                "active": sum(1 for u in housing if u.active),
                "mine": sum(1 for r in ctx["housing_rows"] if r["mine"]),
            }
            if mv.has("housing_grid"):
                ctx["housing_racks"] = housing_rack_payload(
                    mv, ctx["locations"], housing, ctx["occupancy"], ctx["next_code"], ctx["today"])

        elif active == "lines":
            ctx["line_counts"] = _line_counts(session, row)
            ctx["next_code"] = svc.next_code(session, row, "line")

        elif active == "crosses":
            ctx["crosses"] = session.scalars(
                select(OrgCross).where(OrgCross.module_id_fk == row.id)
                .order_by(OrgCross.collected_on.is_(None).desc(), OrgCross.set_up_on.desc())
            ).all()
            ctx["next_code"] = svc.next_code(session, row, "cross")

        elif active == "cohorts":
            ctx["cohorts"] = session.scalars(
                select(OrgCohort).where(OrgCohort.module_id_fk == row.id)
                .order_by(OrgCohort.birth_on.desc())
            ).all()
            ctx["crosses"] = session.scalars(
                select(OrgCross).where(OrgCross.module_id_fk == row.id).order_by(OrgCross.code)
            ).all()
            ctx["locations"] = svc.location_tree(session, row.id)
            ctx["next_code"] = svc.next_code(session, row, "cohort")

        elif active == "genotyping":
            ctx.update(_genotyping_context(session, row, mv, me))

        elif active == "schedule":
            svc.recompute_due(session, row)
            session.commit()
            due = svc.due_items(session, row, horizon_days=max(1, min(_int(request.args.get("horizon"), 21), 366)))
            for item in due:
                subject = svc.RULE_SUBJECTS.get(item["subject_kind"])
                subject_row = session.get(subject, item["subject_id"]) if subject else None
                item["editable"] = access.can_edit(subject_row) if subject_row is not None else False
            ctx["due"] = due
            ctx["horizon"] = max(1, min(_int(request.args.get("horizon"), 21), 366))

        elif active == "environment":
            ctx["locations"] = svc.location_tree(session, row.id)
            ctx["metrics"] = svc.measurement_metrics(mv)
            ctx["readings"] = session.scalars(
                select(OrgMeasurement)
                .where(OrgMeasurement.module_id_fk == row.id,
                       OrgMeasurement.subject_kind == "location")
                .order_by(OrgMeasurement.recorded_at.desc()).limit(200)
            ).all()

        elif active == "preservation":
            ctx["lots"] = session.scalars(
                select(OrgPreservationLot).where(OrgPreservationLot.module_id_fk == row.id)
                .order_by(OrgPreservationLot.frozen_on.desc())
            ).all()
            ctx["methods"] = mv.settings.get("preservation_methods") or [
                "-80 °C", "liquid nitrogen", "sperm", "embryo"
            ]

        if active == "experiments":
            from . import experiments as experiment_pages
            place = experiment_pages.place_for(session, f"organisms:{row.key}")
            ctx["experiments_tab"] = experiment_pages.tab_context(session, place) if place else None

        ctx["today_iso"] = date.today().isoformat()
        return render_template("organisms/module.html", **ctx)


def _genotyping_context(session, module: OrganismModule, mv, me: str) -> dict:
    """Rows for the Genotyping sheet: every call, newest first, then one row
    per living record that has never been called (the Pending chip)."""
    organisms = session.scalars(
        select(Organism).where(Organism.module_id_fk == module.id)
        .order_by(Organism.death_on.is_(None).desc(), Organism.code, Organism.id)).all()
    by_id = {o.id: o for o in organisms}
    units = {u.id: u for u in session.scalars(
        select(OrgHousing).where(OrgHousing.module_id_fk == module.id))}
    calls = session.scalars(
        select(OrgGenotype).where(OrgGenotype.module_id_fk == module.id)
        .order_by(OrgGenotype.called_on.desc(), OrgGenotype.id.desc())).all()

    others: dict[tuple[str, int], object] = {}
    for kind in ("line", "cohort"):
        ids = {c.subject_id for c in calls if c.subject_kind == kind}
        if ids:
            model = GENOTYPE_SUBJECTS[kind]
            for subject in session.scalars(select(model).where(model.id.in_(ids))):
                others[(kind, subject.id)] = subject

    rows = []
    called = set()
    for call in calls:
        if call.subject_kind == "organism":
            subject = by_id.get(call.subject_id)
            called.add(call.subject_id)
            label = svc.organism_label(subject) if subject else f"#{call.subject_id}"
            unit = units.get(subject.housing_id_fk) if subject and subject.housing_id_fk else None
        elif call.subject_kind == "housing":
            subject = units.get(call.subject_id)
            label = subject.code if subject else f"#{call.subject_id}"
            unit = subject
        else:
            subject = others.get((call.subject_kind, call.subject_id))
            label = getattr(subject, "code", None) or f"#{call.subject_id}"
            unit = None
        rows.append({
            "call": call, "subject": subject, "kind": call.subject_kind, "label": label,
            "housing": unit.code if unit else "",
            "editable": subject is not None and access.can_edit(subject),
            "mine": bool(me) and call.called_by == me,
        })
    pending = [{
        "subject": o, "label": svc.organism_label(o),
        "housing": units[o.housing_id_fk].code if o.housing_id_fk in units else "",
        "editable": access.can_edit(o),
    } for o in organisms if o.id not in called and mv.is_alive(o)]

    return {
        "genotype_rows": rows,
        "pending_rows": pending,
        "chip_counts": {
            "calls": len(rows), "pending": len(pending),
            "mine": sum(1 for r in rows if r["mine"]),
        },
        "assays": svc.genotype_assays(session, module.id),
        "zygosities": svc.ZYGOSITIES,
        # What the subject box offers: living records first.
        "subject_choices": [{
            "value": svc.organism_label(o),
            "hint": " · ".join(filter(None, [
                units[o.housing_id_fk].code if o.housing_id_fk in units else "",
                o.line.code if o.line else "", o.genotype,
                "" if mv.is_alive(o) else gettext("gone")])),
        } for o in sorted(organisms, key=lambda o: not mv.is_alive(o))],
    }


def _usernames(session) -> list[str]:
    from .models import UserAccount
    return list(session.scalars(
        select(UserAccount.username).where(UserAccount.disabled.is_(False))
        .order_by(UserAccount.username)
    ).all())


def _occupancy(session, module: OrganismModule) -> dict[int, int]:
    rows = session.execute(
        select(Organism.housing_id_fk, func.coalesce(func.sum(Organism.count), 0))
        .where(Organism.module_id_fk == module.id, svc.alive_clause(module))
        .group_by(Organism.housing_id_fk)
    ).all()
    return {hid: total for hid, total in rows if hid is not None}


def _line_counts(session, module: OrganismModule) -> dict[int, int]:
    rows = session.execute(
        select(Organism.line_id_fk, func.coalesce(func.sum(Organism.count), 0))
        .where(Organism.module_id_fk == module.id, svc.alive_clause(module))
        .group_by(Organism.line_id_fk)
    ).all()
    return {lid: total for lid, total in rows if lid is not None}


# ---------------------------------------------------------------------------
# Entity write handlers
#
# One route per entity, shared by the dialog (full form) and the sheet's
# inline cells (a few fields, X-Autosave). Custom fields go through
# svc.read_attrs_checked so they are handled identically everywhere.
# ---------------------------------------------------------------------------


def _read_attrs(session, module, entity: str, form, row, creating: bool):
    field_rows = svc.fields_for(session, module.id, entity)
    return svc.read_attrs_checked(form, field_rows, svc.load_dict(row.attrs),
                                  creating=creating, full=form.get("_full") == "1")


@bp.route("/<key>/line/save", methods=["POST"])
def save_line(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        form = request.form
        row_id = _int(form.get("id"), 0)

        if row_id:
            row, denied = _load_owned(session, OrgLine, module, row_id, key, "lines")
            if denied:
                return denied
        else:
            row = OrgLine(module_id_fk=module.id)
            session.add(row)

        if "code" in form or not row_id:
            row.code = (form.get("code") or "").strip() or svc.next_code(session, module, "line")
        for name in ("name", "genotype", "owner", "protocol", "source", "external_ref", "notes"):
            _set_text(row, form, name)
        if "parent_line_id_fk" in form:
            parent = _ref(session, OrgLine, module.id, form.get("parent_line_id_fk"))
            row.parent_line_id_fk = parent if parent != row.id else None
        _set_date(row, form, "last_refreshed_on")
        _set_date(row, form, "last_frozen_on")
        _set_flag(row, form, "retired")
        attrs, errors = _read_attrs(session, module, "line", form, row, creating=not row_id)
        if errors:
            session.rollback()
            return _fail(key, "lines", " ".join(errors))
        row.attrs = svc.dump(attrs)

        session.flush()
        svc.log_event(session, module, "line", row.id,
                      "update" if row_id else "create", recorded_by=g.user.username)
        svc.recompute_due(session, module)
        session.commit()
        if _autosave():
            return jsonify({"ok": True, "row": {"active": not row.retired,
                                                "values": {"code": row.code}}})
        flash(gettext("Saved %(name)s.", name=row.code), "success")
        return _redirect_back(key, "lines")


def _fill_housing(session, module, row, form, row_id: int) -> str | None:
    """Write the submitted fields (not the code or position) onto a housing
    unit; an error message when the form cannot be saved."""
    for name in ("purpose", "owner", "protocol", "card_id", "notes"):
        _set_text(row, form, name)
    _set_ref(session, row, form, "line_id_fk", OrgLine, module.id)
    _set_date(row, form, "established_on")
    _set_date(row, form, "last_serviced_on")
    _set_flag(row, form, "retired", attr="active", invert=True)
    _set_flag(row, form, "needs_attention")
    attrs, errors = _read_attrs(session, module, "housing", form, row, creating=not row_id)
    if errors:
        return " ".join(errors)
    row.attrs = svc.dump(attrs)
    return None


def _free_cells(session, loc, count: int, start: tuple[int, int] | None = None) -> list[tuple[int, int]]:
    """Up to `count` empty cells of a rack, reading along its rows from
    `start` (or the first cell)."""
    taken = {(r, c) for r, c in session.execute(select(OrgHousing.row, OrgHousing.col).where(
        OrgHousing.location_id_fk == loc.id, OrgHousing.row.is_not(None),
        OrgHousing.col.is_not(None))).all()}
    begin = (start[0] - 1) * loc.cols + start[1] - 1 if start else 0
    cells = []
    for index in range(begin, loc.rows * loc.cols):
        cell = (index // loc.cols + 1, index % loc.cols + 1)
        if cell not in taken:
            cells.append(cell)
            if len(cells) == count:
                break
    return cells


def _create_housing_units(session, module, mv, form, how_many: int):
    """"How many" above 1: consecutive codes, the same fields, placed side
    by side in the chosen rack from the typed position (or its first free
    cell). Units that do not fit stay in it without a position, and the
    flash says which. One audit batch."""
    key = module.key
    first = (form.get("code") or "").strip() or svc.next_code(session, module, "housing")
    codes = svc.code_sequence(first, how_many)
    clash = svc.codes_in_use(session, OrgHousing, module.id, codes)
    if clash:
        shown = ", ".join(clash[:5]) + (" …" if len(clash) > 5 else "")
        return _fail(key, "housing", ngettext(
            "%(codes)s already exists in %(db)s. Start the numbering somewhere free.",
            "%(codes)s already exist in %(db)s. Start the numbering somewhere free.",
            len(clash), codes=shown, db=mv.label))
    location_id = _ref(session, OrgLocation, module.id, form.get("location_id_fk"))
    loc = session.get(OrgLocation, location_id) if location_id else None
    typed = (form.get("position") or "").strip() if mv.has("housing_grid") else ""
    cells: list[tuple[int, int]] = []
    gridded = loc is not None and mv.has("housing_grid") and bool(loc.rows and loc.cols)
    if typed and not gridded:
        return _fail(key, "housing", gettext("Pick a %(noun)s with rows and columns before giving a position (“%(typed)s”).",
                                             noun=_word(mv.container_noun), typed=typed))
    if gridded:
        naming = location_naming(loc)
        start = None
        if typed:
            start = positions.parse(typed, naming, loc.rows, loc.cols)
            if start is None:
                return _fail(key, "housing", gettext(
                    "“%(typed)s” is not a position in %(rack)s (%(first)s–%(last)s).",
                    typed=typed, rack=loc.name, first=positions.label(1, 1, naming, loc.cols),
                    last=positions.label(loc.rows, loc.cols, naming, loc.cols)))
            holder = session.scalar(select(OrgHousing).where(
                OrgHousing.location_id_fk == loc.id, OrgHousing.row == start[0], OrgHousing.col == start[1]))
            if holder is not None:
                return _fail(key, "housing", gettext("%(rack)s · %(typed)s already holds %(code)s.",
                                                     rack=loc.name, typed=typed, code=holder.code))
        cells = _free_cells(session, loc, how_many, start)

    error = None
    created = []
    with audit.batch(session, "create", f"New {mv.housing_noun_plural} ×{how_many} ({mv.label})",
                     "organism_housing"):
        for index, code in enumerate(codes):
            row = OrgHousing(module_id_fk=module.id, code=code, location_id_fk=location_id)
            session.add(row)
            error = _fill_housing(session, module, row, form, 0)
            if error:
                break
            if index < len(cells):
                row.row, row.col = cells[index]
            row.updated_at, row.updated_by = datetime.utcnow(), g.user.username
            created.append(row)
        if not error:
            session.flush()
            for row in created:
                svc.log_event(session, module, "housing", row.id, "create", recorded_by=g.user.username,
                              notes=f"Added as one of {how_many}")
    if error:
        session.rollback()
        return _fail(key, "housing", error)
    svc.recompute_due(session, module)
    session.commit()

    made = {"n": how_many, "nouns": _word(mv.housing_noun_plural), "codes": _range_label(codes)}
    if cells:
        naming = location_naming(loc)
        at = (positions.label(*cells[0], naming, loc.cols)
              + (f"–{positions.label(*cells[-1], naming, loc.cols)}" if len(cells) > 1 else ""))
        flash(gettext("Created %(n)s %(nouns)s: %(codes)s in %(place)s at %(cells)s.",
                      place=loc.name, cells=at, **made), "success")
    elif loc:
        flash(gettext("Created %(n)s %(nouns)s: %(codes)s in %(place)s.", place=loc.name, **made), "success")
    else:
        flash(gettext("Created %(n)s %(nouns)s: %(codes)s.", **made), "success")
    if gridded and len(cells) < how_many:
        left = codes[len(cells):]
        values = {"rack": loc.name, "room": len(cells), "total": how_many, "codes": _range_label(left)}
        if typed:
            message = ngettext(
                "%(rack)s had room for %(room)s of %(total)s from %(start)s; %(codes)s is in it without a position. Drag them onto the grid to place them.",
                "%(rack)s had room for %(room)s of %(total)s from %(start)s; %(codes)s are in it without a position. Drag them onto the grid to place them.", len(left), start=typed, **values)
        else:
            message = ngettext(
                "%(rack)s had room for %(room)s of %(total)s from its first free cell; %(codes)s is in it without a position. Drag them onto the grid to place them.",
                "%(rack)s had room for %(room)s of %(total)s from its first free cell; %(codes)s are in it without a position. Drag them onto the grid to place them.", len(left), **values)
        flash(message, "error")
    return _redirect_back(key, "housing")


@bp.route("/<key>/housing/save", methods=["POST"])
def save_housing(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        mv = svc.view(module)
        form = request.form
        row_id = _int(form.get("id"), 0)
        how_many, error = _how_many(form, row_id, MAX_NEW_HOUSING)
        if error:
            return _fail(key, "housing", error)
        if how_many > 1:
            return _create_housing_units(session, module, mv, form, how_many)

        if row_id:
            row, denied = _load_owned(session, OrgHousing, module, row_id, key, "housing")
            if denied:
                return denied
        else:
            row = OrgHousing(module_id_fk=module.id)
            session.add(row)

        if "code" in form or not row_id:
            row.code = (form.get("code") or "").strip() or svc.next_code(session, module, "housing")
        error = _fill_housing(session, module, row, form, row_id)
        if error:
            session.rollback()
            return _fail(key, "housing", error)

        # Where it sits. A move is validated as one change: the position is
        # read against the rack it is moving to, and a unit that cannot be
        # placed there is left unplaced rather than keeping coordinates
        # that belong to its old rack.
        position_error = None
        if form_changed(form, "location_id_fk", "position") or not row_id:
            old_location = row.location_id_fk
            if "location_id_fk" in form:
                row.location_id_fk = _ref(session, OrgLocation, module.id, form.get("location_id_fk"))
            if "position" in form and mv.has("housing_grid"):
                position_error = _apply_position(session, row, form.get("position"))
                if position_error:
                    if _autosave():
                        session.rollback()
                        return _fail(key, "housing", position_error, 409)
                    row.row = row.col = None
            elif row.location_id_fk != old_location:
                row.row = row.col = None
        row.updated_at = datetime.utcnow()
        row.updated_by = g.user.username

        session.flush()
        svc.log_event(session, module, "housing", row.id,
                      "update" if row_id else "create", recorded_by=g.user.username)
        svc.recompute_due(session, module)
        session.commit()
        if _autosave():
            return jsonify({"ok": True, "row": {"active": bool(row.active), "values": {
                "code": row.code, "position": housing_position_label(row),
                "location_id_fk": row.location_id_fk or ""}}})
        if position_error:
            if row.location:
                flash(gettext("%(code)s is in %(rack)s with no position: %(problem)s",
                              code=row.code, rack=row.location.name, problem=position_error), "error")
            else:
                flash(gettext("%(code)s is in no %(noun)s with no position: %(problem)s",
                              code=row.code, noun=_word(mv.container_noun), problem=position_error), "error")
        else:
            flash(gettext("Saved %(name)s.", name=row.code), "success")
        return _redirect_back(key, "housing")


def location_naming(location) -> dict:
    return positions.scheme(svc.load_dict(location.settings).get("naming") if location else None)


def housing_position_label(unit) -> str:
    loc = unit.location
    if loc is None or not loc.cols or not unit.row or not unit.col:
        return ""
    return positions.label(unit.row, unit.col, location_naming(loc), loc.cols)


def _apply_position(session, unit, raw) -> str | None:
    """Set a unit's position from what was typed ("D7"), read with its
    rack's naming scheme; an error message instead of a guess."""
    raw = (raw or "").strip()
    if not raw:
        unit.row = unit.col = None
        return None
    loc = session.get(OrgLocation, unit.location_id_fk) if unit.location_id_fk else None
    if loc is None or not loc.rows or not loc.cols:
        return gettext("pick a rack with rows and columns before giving a position (“%(typed)s” was not saved).",
                       typed=raw)
    cell = positions.parse(raw, location_naming(loc), loc.rows, loc.cols)
    if cell is None:
        n = location_naming(loc)
        return gettext("“%(typed)s” is not a position in %(rack)s (%(first)s–%(last)s).",
                       typed=raw, rack=loc.name, first=positions.label(1, 1, n, loc.cols),
                       last=positions.label(loc.rows, loc.cols, n, loc.cols))
    holder = session.scalar(select(OrgHousing).where(
        OrgHousing.location_id_fk == loc.id, OrgHousing.row == cell[0],
        OrgHousing.col == cell[1], OrgHousing.id != (unit.id or 0)))
    if holder is not None:
        return gettext("%(rack)s · %(typed)s already holds %(code)s. Drag on the rack grid to swap.",
                       rack=loc.name, typed=raw, code=holder.code)
    unit.row, unit.col = cell
    return None


@bp.route("/<key>/housing/<int:unit_id>/place", methods=["POST"])
def place_housing(key: str, unit_id: int):
    """Move a unit on the rack grid; answers JSON. An occupied cell swaps;
    an empty rack id takes it out of its position (it stays where it is)."""
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        unit = session.get(OrgHousing, unit_id)
        if unit is None or unit.module_id_fk != module.id:
            return jsonify({"ok": False, "error": gettext("That record no longer exists.")}), 404
        if not access.can_edit(unit):
            return jsonify({"ok": False, "error": access.reason_denied(unit)}), 403
        before = housing_position_label(unit)
        rack_id = _int(request.form.get("rack_id"), 0)
        if not rack_id:
            unit.row = unit.col = None
            svc.log_event(session, module, "housing", unit.id, "move", recorded_by=g.user.username,
                          notes=f"Taken out of {before or 'its position'}")
            session.commit()
            return jsonify({"ok": True})
        rack = session.get(OrgLocation, rack_id)
        row, col = _int(request.form.get("row"), 0), _int(request.form.get("col"), 0)
        if (rack is None or rack.module_id_fk != module.id or not rack.rows or not rack.cols
                or not (1 <= row <= rack.rows and 1 <= col <= rack.cols)):
            return jsonify({"ok": False, "error": gettext("That position is not in the rack.")}), 400
        occupant = session.scalar(select(OrgHousing).where(
            OrgHousing.location_id_fk == rack.id, OrgHousing.row == row,
            OrgHousing.col == col, OrgHousing.id != unit.id))
        if occupant is not None:
            if not access.can_edit(occupant):
                return jsonify({"ok": False, "error": gettext("That cell holds %(code)s, which you may not move.",
                                                              code=occupant.code)}), 403
            occupant.location_id_fk, occupant.row, occupant.col = unit.location_id_fk, unit.row, unit.col
            svc.log_event(session, module, "housing", occupant.id, "move", recorded_by=g.user.username,
                          notes=f"Swapped with {unit.code}")
        unit.location_id_fk, unit.row, unit.col = rack.id, row, col
        unit.updated_at, unit.updated_by = datetime.utcnow(), g.user.username
        session.flush()
        after = positions.label(row, col, location_naming(rack), rack.cols)
        svc.log_event(session, module, "housing", unit.id, "move", recorded_by=g.user.username,
                      notes=f"Moved to {rack.name} · {after}" + (f" from {before}" if before else ""))
        session.commit()
    return jsonify({"ok": True})


def housing_rack_payload(mv, locations, units, occupancy, next_code, today) -> dict:
    """Racks (locations with rows and columns) and the housing units, for
    the rack grid. Clicking a tile or an empty cell opens the housing
    dialog, the same one the table uses."""
    names = {loc.id: loc.name for loc in locations}
    racks = [loc for loc in locations if loc.rows and loc.cols]
    grid_ids = {loc.id for loc in racks}
    items = []
    for unit in units:
        payload = housing_payload(unit)
        in_grid = unit.location_id_fk in grid_ids
        count = occupancy.get(unit.id, 0)
        items.append({
            "id": unit.id, "label": unit.code,
            "sub": (unit.line.code if unit.line else "") or i18n.translate_value(unit.purpose, "organism"),
            "badge": str(count) if count else "",
            "tone": unit.purpose if unit.active else "inactive",
            "flag": unit.needs_attention,
            "rack": unit.location_id_fk if in_grid else None,
            "row": unit.row if in_grid else None, "col": unit.col if in_grid else None,
            "title": " · ".join(filter(None, [unit.code, unit.line.code if unit.line else "",
                                             i18n.translate_value(unit.purpose, "organism"),
                                             names.get(unit.location_id_fk, ""),
                                             gettext("%(n)s held", n=count) if count else ""])),
            "search": " ".join(filter(None, [unit.code, unit.purpose, unit.owner, unit.card_id,
                                             unit.line.code if unit.line else "",
                                             unit.line.name if unit.line else ""])).lower(),
            "edit": {"data-org-edit": "housing-dialog", "data-org-payload": json.dumps(payload)},
        })
    return {
        "racks": [{
            "id": loc.id,
            "name": f"{names[loc.parent_id_fk]} › {loc.name}" if loc.parent_id_fk in names else loc.name,
            "rows": loc.rows, "cols": loc.cols, "naming": location_naming(loc),
            "edit": {"data-record-payload": json.dumps({
                "id": loc.id, "_label": loc.name, "name": loc.name, "rows": loc.rows,
                "cols": loc.cols, "kind": loc.kind, "parent_id_fk": loc.parent_id_fk or "",
                **{f"naming_{k}": v for k, v in location_naming(loc).items()}})},
        } for loc in racks],
        "items": items,
        "create": {"attrs": {"data-org-edit": "housing-dialog"},
                   "payload": {"code": next_code, "established_on": today},
                   "rack_field": "location_id_fk", "text_field": "position"},
    }


def housing_payload(unit) -> dict:
    """What the housing dialog is filled with for an existing unit."""
    return {
        "id": unit.id, "code": unit.code, "location_id_fk": unit.location_id_fk,
        "position": housing_position_label(unit), "purpose": unit.purpose,
        "line_id_fk": unit.line_id_fk, "owner": unit.owner,
        "protocol": unit.protocol, "card_id": unit.card_id,
        "established_on": _iso(unit.established_on),
        "last_serviced_on": _iso(unit.last_serviced_on),
        "retired": not unit.active, "needs_attention": unit.needs_attention,
        "notes": unit.notes, "attrs": unit.attrs_dict,
        "locked": not access.can_edit(unit),
    }


MAX_NEW_ANIMALS = 50
MAX_NEW_HOUSING = 30


def _how_many(form, row_id: int, limit: int) -> tuple[int, str | None]:
    """The dialog's "How many" (new records only), or an error."""
    raw = (form.get("how_many") or "").strip()
    if row_id or not raw:
        return 1, None
    n = _int(raw, 0)
    if not 1 <= n <= limit:
        return 0, gettext("How many must be a whole number from 1 to %(limit)s (got “%(typed)s”).",
                          limit=limit, typed=raw)
    return n, None


def _range_label(codes: list[str]) -> str:
    codes = [c for c in codes if c]
    if not codes:
        return ""
    return codes[0] if len(codes) == 1 else f"{codes[0]}–{codes[-1]}"


def _fill_animal(session, module, mv, row, form, row_id: int) -> str | None:
    """Write the submitted fields onto an animal record; an error message
    when the form cannot be saved. The code is set by the caller."""
    previous_status, previous_death = row.status, row.death_on
    if "count" in form:
        raw = (form.get("count") or "").strip()
        count = _int(raw, None) if raw else (1 if not row_id else None)
        if count is None or count < 1:
            if not raw:
                return gettext("Count must be a whole number of 1 or more (got nothing). To record that a group is gone, set its status or date removed.")
            return gettext("Count must be a whole number of 1 or more (got “%(typed)s”). To record that a group is gone, set its status or date removed.", typed=raw)
        row.count = count
    elif not row_id:
        row.count = 1
    for name, model in (("housing_id_fk", OrgHousing), ("line_id_fk", OrgLine),
                        ("cohort_id_fk", OrgCohort), ("parent_a_id_fk", Organism),
                        ("parent_b_id_fk", Organism)):
        _set_ref(session, row, form, name, model, module.id)
    for name in ("sex", "status", "genotype", "owner", "protocol", "notes"):
        _set_text(row, form, name)
    _set_date(row, form, "birth_on")
    _set_date(row, form, "death_on")
    _set_date(row, form, "last_procedure_on")
    attrs, errors = _read_attrs(session, module, "organism", form, row, creating=not row_id)
    if errors:
        return " ".join(errors)
    row.attrs = svc.dump(attrs)
    death_given = "death_on" in form and row.death_on != previous_death
    svc.apply_status_rules(mv, row, previous_status, death_given=death_given)
    row.updated_at = datetime.utcnow()
    row.updated_by = g.user.username
    return None


@bp.route("/<key>/animal/save", methods=["POST"])
def save_animal(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        mv = svc.view(module)
        form = request.form
        row_id = _int(form.get("id"), 0)
        how_many, error = _how_many(form, row_id, MAX_NEW_ANIMALS)
        if error:
            return _fail(key, "animals", error)
        if how_many > 1:
            return _create_animals(session, module, mv, form, how_many)

        if row_id:
            row, denied = _load_owned(session, Organism, module, row_id, key, "animals")
            if denied:
                return denied
        else:
            row = Organism(module_id_fk=module.id)
            session.add(row)

        if "code" in form or not row_id:
            code = (form.get("code") or "").strip()
            # Individually tracked records need an ID; anonymous groups do not.
            if not code and mv.identity_mode == "individual":
                code = svc.next_code(session, module, "organism")
            row.code = code or None
        error = _fill_animal(session, module, mv, row, form, row_id)
        if error:
            session.rollback()
            return _fail(key, "animals", error)

        session.flush()
        svc.log_event(session, module, "organism", row.id,
                      "update" if row_id else "create", count=row.count,
                      recorded_by=g.user.username)
        svc.recompute_due(session, module)
        session.commit()
        if _autosave():
            return jsonify({"ok": True, "row": {"active": mv.is_alive(row), "values": {
                "code": row.code or "", "count": row.count, "status": row.status,
                "death_on": _iso(row.death_on), "birth_on": _iso(row.birth_on)}}})
        flash(gettext("Saved %(name)s.", name=row.code or _word(mv.organism_noun)), "success")
        return _redirect_back(key, "animals")


def _create_animals(session, module, mv, form, how_many: int):
    """"How many" above 1: that many records with the same fields, in the
    chosen housing. Individually tracked ones get consecutive IDs from the
    one typed (or the next free one); groups follow a typed code's
    numbering, or stay anonymous. One audit batch, so Batch history can
    undo it."""
    key = module.key
    first = (form.get("code") or "").strip()
    if not first and mv.identity_mode == "individual":
        first = svc.next_code(session, module, "organism")
    codes = svc.code_sequence(first, how_many)
    clash = svc.codes_in_use(session, Organism, module.id, codes)
    if clash:
        shown = ", ".join(clash[:5]) + (" …" if len(clash) > 5 else "")
        return _fail(key, "animals", ngettext(
            "%(codes)s already exists in %(db)s. Start the numbering somewhere free.",
            "%(codes)s already exist in %(db)s. Start the numbering somewhere free.",
            len(clash), codes=shown, db=mv.label))
    error = None
    created = []
    with audit.batch(session, "create", f"New {mv.organism_noun_plural} ×{how_many} ({mv.label})",
                     "organisms"):
        for code in codes:
            row = Organism(module_id_fk=module.id, code=code or None)
            session.add(row)
            error = _fill_animal(session, module, mv, row, form, 0)
            if error:
                break
            created.append(row)
        if not error:
            session.flush()
            for row in created:
                svc.log_event(session, module, "organism", row.id, "create", count=row.count,
                              recorded_by=g.user.username, notes=f"Added as one of {how_many}")
    if error:
        session.rollback()
        return _fail(key, "animals", error)
    svc.recompute_due(session, module)
    session.commit()
    unit = session.get(OrgHousing, created[0].housing_id_fk) if created[0].housing_id_fk else None
    span = _range_label(codes)
    made = {"n": how_many, "nouns": _word(mv.organism_noun_plural), "codes": span}
    if span and unit:
        message = gettext("Created %(n)s %(nouns)s: %(codes)s in %(place)s.", place=unit.code, **made)
    elif span:
        message = gettext("Created %(n)s %(nouns)s: %(codes)s.", **made)
    elif unit:
        message = gettext("Created %(n)s %(nouns)s in %(place)s.", place=unit.code, **made)
    else:
        message = gettext("Created %(n)s %(nouns)s.", **made)
    flash(message, "success")
    return _redirect_back(key, "animals")


def animal_payload(o, mv) -> dict:
    return {
        "id": o.id, "code": o.code, "count": o.count,
        "housing_id_fk": o.housing_id_fk, "line_id_fk": o.line_id_fk,
        "cohort_id_fk": o.cohort_id_fk, "sex": o.sex, "status": o.status,
        "birth_on": _iso(o.birth_on), "death_on": _iso(o.death_on),
        "genotype": o.genotype, "owner": o.owner, "protocol": o.protocol,
        "parent_a_id_fk": o.parent_a_id_fk, "parent_b_id_fk": o.parent_b_id_fk,
        "notes": o.notes, "attrs": o.attrs_dict, "locked": not access.can_edit(o),
    }


@bp.app_template_global()
def org_animal_payload(o, mv):
    return animal_payload(o, mv)


@bp.app_template_global()
def org_housing_payload(unit):
    return housing_payload(unit)


@bp.route("/<key>/cross/save", methods=["POST"])
def save_cross(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        form = request.form
        row_id = _int(form.get("id"), 0)

        if row_id:
            row, denied = _load_owned(session, OrgCross, module, row_id, key, "crosses")
            if denied:
                return denied
        else:
            row = OrgCross(module_id_fk=module.id, cross_type="pair")
            session.add(row)

        if "code" in form or not row_id:
            row.code = (form.get("code") or "").strip() or svc.next_code(session, module, "cross")
        if "cross_type" in form:
            row.cross_type = (form.get("cross_type") or "pair").strip()
        for name, model in (("housing_id_fk", OrgHousing), ("sire_line_id_fk", OrgLine),
                            ("dam_line_id_fk", OrgLine)):
            _set_ref(session, row, form, name, model, module.id)
        for name in ("sire_label", "dam_label", "owner", "notes"):
            _set_text(row, form, name)
        for name in ("set_up_on", "expected_on", "collected_on"):
            _set_date(row, form, name)
        attrs, errors = _read_attrs(session, module, "cross", form, row, creating=not row_id)
        if errors:
            session.rollback()
            return _fail(key, "crosses", " ".join(errors))
        row.attrs = svc.dump(attrs)

        session.flush()
        svc.log_event(session, module, "cross", row.id,
                      "update" if row_id else "create", recorded_by=g.user.username)
        session.commit()
        if _autosave():
            return jsonify({"ok": True, "row": {"values": {"code": row.code}}})
        flash(gettext("Saved %(name)s.", name=row.code), "success")
        return _redirect_back(key, "crosses")


@bp.route("/<key>/cohort/save", methods=["POST"])
def save_cohort(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        form = request.form
        row_id = _int(form.get("id"), 0)

        if row_id:
            row, denied = _load_owned(session, OrgCohort, module, row_id, key, "cohorts")
            if denied:
                return denied
        else:
            row = OrgCohort(module_id_fk=module.id, count_initial=0, count_current=0)
            session.add(row)

        if "code" in form or not row_id:
            row.code = (form.get("code") or "").strip() or svc.next_code(session, module, "cohort")
        for name, model in (("line_id_fk", OrgLine), ("cross_id_fk", OrgCross),
                            ("location_id_fk", OrgLocation)):
            _set_ref(session, row, form, name, model, module.id)
        _set_date(row, form, "birth_on")
        for name in ("stage", "owner", "notes"):
            _set_text(row, form, name)
        counts = {}
        for name in ("count_initial", "count_current"):
            raw = (form.get(name) or "").strip()
            if raw:
                value = _int(raw, None)
                if value is None or value < 0:
                    session.rollback()
                    return _fail(key, "cohorts", gettext("Counts must be whole numbers of 0 or more (got “%(typed)s”).",
                                                         typed=raw))
                counts[name] = value
        if "count_initial" in form:
            row.count_initial = counts.get("count_initial", 0)
        if "count_current" in counts:
            row.count_current = counts["count_current"]
        elif not row_id:
            # A new cohort with no current count still has everyone.
            row.count_current = row.count_initial or 0
        attrs, errors = _read_attrs(session, module, "cohort", form, row, creating=not row_id)
        if errors:
            session.rollback()
            return _fail(key, "cohorts", " ".join(errors))
        row.attrs = svc.dump(attrs)

        session.flush()
        svc.log_event(session, module, "cohort", row.id,
                      "update" if row_id else "create", count=row.count_current,
                      recorded_by=g.user.username)
        svc.recompute_due(session, module)
        session.commit()
        if _autosave():
            return jsonify({"ok": True, "row": {"values": {"code": row.code}}})
        flash(gettext("Saved %(name)s.", name=row.code), "success")
        return _redirect_back(key, "cohorts")


def _deny_location(module: OrganismModule, key: str, row: OrgLocation | None):
    """Adding a rack or room is setting the database up; changing or
    deleting one is also for whoever added it (access.can_edit_rack)."""
    if row is not None and access.can_edit_rack(row):
        return None
    return _deny_configure(module, key)


@bp.route("/<key>/location/save", methods=["POST"])
def save_location(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        form = request.form
        row_id = _int(form.get("id"), 0)
        row = None
        if row_id:
            row = session.get(OrgLocation, row_id)
            if row is None or row.module_id_fk != module.id:
                abort(404)
        denied = _deny_location(module, key, row)
        if denied:
            return denied
        if row is None:
            row = OrgLocation(module_id_fk=module.id, created_by=g.user.username)
            session.add(row)

        row.name = (form.get("name") or "").strip() or row.name or "Unnamed"
        if "kind" in form or not row_id:
            row.kind = (form.get("kind") or "rack").strip()
        if "parent_id_fk" in form:
            parent = _ref(session, OrgLocation, module.id, form.get("parent_id_fk"))
            row.parent_id_fk = parent if parent != row.id else None
        if "rows" in form or "cols" in form:
            rows, cols = _int(form.get("rows"), 0) or None, _int(form.get("cols"), 0) or None
            if row_id and (rows != row.rows or cols != row.cols):
                # Units outside a shrunken grid lose their position rather
                # than pointing at cells that no longer exist.
                for unit in session.scalars(select(OrgHousing).where(OrgHousing.location_id_fk == row.id)):
                    if unit.row and (not rows or not cols or unit.row > rows or unit.col > cols):
                        unit.row = unit.col = None
            row.rows, row.cols = rows, cols
        _set_text(row, form, "notes")
        if "naming_mode" in form:
            settings = svc.load_dict(row.settings)
            settings["naming"] = positions.scheme_from_form(form)
            row.settings = svc.dump(settings)
        session.commit()
        flash(gettext("Saved %(name)s.", name=row.name), "success")
        return _redirect_back(key, "housing")


@bp.route("/<key>/reading/save", methods=["POST"])
def save_reading(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        mv = svc.view(module)
        form = request.form
        location_id = _ref(session, OrgLocation, module.id, form.get("location_id_fk"))
        if location_id is None:
            flash(gettext("Pick a %(noun)s to log against.", noun=_word(mv.container_noun)), "error")
            return _redirect_back(key, "environment")

        recorded, skipped = 0, []
        for metric in svc.measurement_metrics(mv):
            raw = (form.get(f"metric_{metric['key']}") or "").strip()
            if raw == "":
                continue
            try:
                value = float(raw)
            except ValueError:
                skipped.append(i18n.translate_value(metric.get("label") or metric["key"], "field"))
                continue
            session.add(OrgMeasurement(
                module_id_fk=module.id,
                subject_kind="location",
                subject_id=location_id,
                metric=metric["key"],
                value_num=value,
                unit=metric.get("unit", ""),
                recorded_by=g.user.username,
                alarm=bool(form.get(f"alarm_{metric['key']}")),
                notes=(form.get("notes") or "").strip(),
            ))
            recorded += 1
        session.commit()
        flash(ngettext("Logged %(num)s reading.", "Logged %(num)s readings.", recorded), "success")
        if skipped:
            flash(gettext("Not a number, so not logged: %(names)s.", names=", ".join(skipped)), "error")
        return _redirect_back(key, "environment")


@bp.route("/<key>/lot/save", methods=["POST"])
def save_lot(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        mv = svc.view(module)
        form = request.form
        row_id = _int(form.get("id"), 0)
        if row_id:
            row, denied = _load_owned(session, OrgPreservationLot, module, row_id, key, "preservation")
            if denied:
                return denied
        else:
            row = OrgPreservationLot(module_id_fk=module.id)
            session.add(row)

        line_id = _ref(session, OrgLine, module.id, form.get("line_id_fk"))
        if line_id is None:
            session.rollback()
            return _fail(key, "preservation", gettext("Pick the %(noun)s this lot was frozen from.",
                                                      noun=_word(mv.line_noun)))
        vial_count = _int(form.get("vial_count"), None) if (form.get("vial_count") or "").strip() else 0
        raw_remaining = (form.get("vials_remaining") or "").strip()
        remaining = _int(raw_remaining, None) if raw_remaining else vial_count
        if vial_count is None or remaining is None or vial_count < 0 or remaining < 0:
            session.rollback()
            return _fail(key, "preservation", gettext("Vial counts must be whole numbers of 0 or more."))
        if remaining > vial_count:
            session.rollback()
            return _fail(key, "preservation",
                         gettext("Vials remaining (%(left)s) cannot be more than vials frozen (%(frozen)s).",
                                 left=remaining, frozen=vial_count))

        row.line_id_fk = line_id
        row.code = (form.get("code") or "").strip()
        row.method = (form.get("method") or "").strip()
        row.frozen_on = _parse_date(form.get("frozen_on"))
        row.frozen_by = (form.get("frozen_by") or g.user.username).strip()
        row.vial_count = vial_count
        row.vials_remaining = remaining
        row.storage_text = (form.get("storage_text") or "").strip()
        row.position = (form.get("position") or "").strip()
        row.recovery_tested_on = _parse_date(form.get("recovery_tested_on"))
        recovery = form.get("recovery_ok")
        row.recovery_ok = None if recovery in (None, "") else recovery == "yes"
        row.notes = (form.get("notes") or "").strip()

        # Freezing a line resets its re-freeze clock.
        if row.frozen_on:
            line = session.get(OrgLine, line_id)
            if line is not None and (line.last_frozen_on is None or row.frozen_on > line.last_frozen_on):
                line.last_frozen_on = row.frozen_on

        session.flush()
        svc.log_event(session, module, "line", line_id, "freeze",
                      occurred_on=row.frozen_on, count=row.vial_count,
                      recorded_by=g.user.username)
        svc.recompute_due(session, module)
        session.commit()
        flash(gettext("Saved frozen lot."), "success")
        return _redirect_back(key, "preservation")


GENOTYPE_SUBJECTS = {"organism": Organism, "housing": OrgHousing, "line": OrgLine, "cohort": OrgCohort}


GENOTYPE_VIEWS = ("animals", "genotyping", "housing")


def _genotype_back(form) -> str:
    back = (form.get("return_view") or "animals").strip()
    return back if back in GENOTYPE_VIEWS else "animals"


def _read_call(form) -> tuple[dict, str | None]:
    """The call's own fields from a form, or an error message."""
    values = {
        "assay": (form.get("assay") or "").strip()[:120],
        "result": (form.get("result") or "").strip()[:200],
        "zygosity": (form.get("zygosity") or "").strip()[:40],
        "notes": (form.get("notes") or "").strip(),
    }
    if not values["result"] and not values["zygosity"]:
        return values, gettext("Give the call a result or a zygosity.")
    raw_date = (form.get("called_on") or "").strip()
    called_on = _parse_date(raw_date)
    if raw_date and called_on is None:
        return values, gettext("“%(typed)s” is not a date.", typed=raw_date)
    values["called_on"] = called_on or date.today()
    link = svc.safe_link(form.get("image_path"))
    if link is None:
        return values, gettext("The gel image link must start with http:// or https://.")
    values["image_path"] = link
    return values, None


@bp.route("/<key>/genotype/save", methods=["POST"])
def save_genotype(key: str):
    """Record one genotype call. The subject arrives either as a kind + id
    (a Record button on a row) or as what a person typed in the dialog: an
    animal's code, "#id" for an uncoded group, or a housing unit's code.
    Either way it must be a record of this module that you may edit."""
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        mv = svc.view(module)
        form = request.form
        back = _genotype_back(form)
        typed = (form.get("subject") or "").strip()
        if (form.get("subject_id") or "").strip():
            kind = (form.get("subject_kind") or "organism").strip()
            model = GENOTYPE_SUBJECTS.get(kind)
            subject = session.get(model, _int(form.get("subject_id"), 0)) if model else None
            if subject is None or subject.module_id_fk != module.id:
                return _fail(key, back, gettext("That record no longer exists, so no genotype was recorded."), 404)
        elif typed:
            kind, subject = svc.resolve_genotype_subject(session, module, typed)
            if subject is None:
                return _fail(key, back, gettext(
                    "No %(noun)s or %(unit)s “%(typed)s” in %(db)s, so no genotype was recorded.",
                    noun=_word(mv.organism_noun), unit=_word(mv.housing_noun), typed=typed, db=mv.label), 404)
        else:
            return _fail(key, back, gettext("Pick the %(noun)s this call is for.", noun=_word(mv.organism_noun)))
        if not access.can_edit(subject):
            return _fail(key, back, access.reason_denied(subject), 403)
        values, error = _read_call(form)
        if error:
            return _fail(key, back, error)
        session.add(OrgGenotype(module_id_fk=module.id, subject_kind=kind, subject_id=subject.id,
                                called_by=g.user.username, **values))
        label = getattr(subject, "code", None) or f"#{subject.id}"
        svc.log_event(session, module, kind, subject.id, "genotype", occurred_on=values["called_on"],
                      recorded_by=g.user.username,
                      notes=" ".join(filter(None, [values["assay"], values["result"], values["zygosity"]])))
        session.commit()
        flash(gettext("Recorded genotype for %(name)s.", name=label), "success")
        return _redirect_back(key, back)


@bp.route("/<key>/animals/genotype", methods=["POST"])
def genotype_batch(key: str):
    """One call (assay, result, zygosity) for every ticked record, as one
    audit batch so it can be undone from Batch history."""
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        mv = svc.view(module)
        back = _genotype_back(request.form)
        rows = _selected(session, Organism, module)
        if not rows:
            flash(gettext("No %(nouns)s selected, so nothing was recorded.",
                          nouns=_word(mv.organism_noun_plural)), "info")
            return _redirect_back(key, back)
        values, error = _read_call(request.form)
        if error:
            return _fail(key, back, error)
        editable = [r for r in rows if access.can_edit(r)]
        skipped = len(rows) - len(editable)
        n = len(editable)
        if editable:
            with audit.batch(session, "create", f"genotype ×{n} {mv.organism_noun_plural} ({mv.label})",
                             "organism_genotypes"):
                for row in editable:
                    session.add(OrgGenotype(module_id_fk=module.id, subject_kind="organism",
                                            subject_id=row.id, called_by=g.user.username, **values))
                    svc.log_event(session, module, "organism", row.id, "genotype",
                                  occurred_on=values["called_on"], recorded_by=g.user.username,
                                  notes="Batch: " + " ".join(filter(None, [
                                      values["assay"], values["result"], values["zygosity"]])))
            session.commit()
        noun = mv.organism_noun if n == 1 else mv.organism_noun_plural
        message = gettext("Recorded a genotype for %(n)s %(nouns)s.", n=n, nouns=_word(noun))
        if skipped:
            message += " " + gettext("%(n)s belong to someone else and were left alone.", n=skipped)
    flash(message, "success" if editable else "error")
    return _redirect_back(key, back)


@bp.route("/<key>/genotype/<int:call_id>/delete", methods=["POST"])
def delete_genotype(key: str, call_id: int):
    """Remove a call made in error: for whoever may edit its subject (or
    made the call, if the subject is gone)."""
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        call = session.get(OrgGenotype, call_id)
        if call is None or call.module_id_fk != module.id:
            abort(404)
        model = GENOTYPE_SUBJECTS.get(call.subject_kind)
        subject = session.get(model, call.subject_id) if model else None
        allowed = (access.can_edit(subject) if subject is not None
                   else access.is_admin() or call.called_by == access.username())
        if not allowed:
            return _fail(key, "genotyping", access.reason_denied(subject) if subject is not None
                         else gettext("Only an admin or whoever made this call can remove it."), 403)
        session.delete(call)
        if subject is not None:
            svc.log_event(session, module, call.subject_kind, call.subject_id, "genotype-removed",
                          recorded_by=g.user.username,
                          notes=" ".join(filter(None, [call.assay, call.result, call.zygosity])))
        session.commit()
        flash(gettext("Removed the genotype call."), "success")
        return _redirect_back(key, "genotyping")


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@bp.route("/<key>/due/<int:due_id>/done", methods=["POST"])
def complete_due(key: str, due_id: int):
    from .models import OrgDue
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        due = session.get(OrgDue, due_id)
        if due is None or due.module_id_fk != module.id:
            # Rebuilt since the page loaded (its subject was deleted or no
            # longer qualifies): nothing to mark.
            flash(gettext("That item is no longer on the schedule, so nothing was marked."), "info")
            return _redirect_back(key, "schedule")
        subject = svc.due_subject(session, due)
        if subject is not None and not access.can_edit(subject):
            return _fail(key, "schedule", access.reason_denied(subject), 403)
        done_on = None
        raw = (request.form.get("done_on") or "").strip()
        if raw:
            try:
                done_on = date.fromisoformat(raw)
            except ValueError:
                return _fail(key, "schedule", gettext("“%(typed)s” is not a date.", typed=raw), 400)
            if done_on > date.today():
                return _fail(key, "schedule",
                             gettext("It can't be done in the future: pick today or an earlier day."), 400)
        row = svc.complete_due(session, module, due_id, g.user.username, done_on=done_on)
        session.flush()
        svc.recompute_due(session, module)
        session.commit()
        if not row:
            flash(gettext("Already done."), "info")
        elif not done_on or done_on == date.today():
            flash(gettext("Marked done."), "success")
        else:
            flash(gettext("Marked done on %(day)s.", day=i18n.strftime(done_on, "%a %d %b")), "success")
        return _redirect_back(key, "schedule")


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


DELETABLE = {
    "line": OrgLine, "housing": OrgHousing, "animal": Organism,
    "cross": OrgCross, "cohort": OrgCohort, "location": OrgLocation,
    "lot": OrgPreservationLot,
}
DELETE_RETURN = {
    "line": "lines", "housing": "housing", "animal": "animals",
    "cross": "crosses", "cohort": "cohorts", "location": "housing",
    "lot": "preservation",
}


def _plural_count(n: int, noun: str) -> str:
    return f"{n} {_word(noun if n == 1 else pluralise(noun))}"


def _unassign_residents(session, module, unit, user: str) -> int:
    """Take everything out of a housing unit that is about to go. The
    animals stay (they may be someone else's); they just have no unit."""
    moved = 0
    for resident in session.scalars(select(Organism).where(Organism.housing_id_fk == unit.id)):
        resident.housing_id_fk = None
        svc.log_event(session, module, "organism", resident.id, "move", recorded_by=user,
                      notes=f"Unassigned: {unit.code} was deleted")
        moved += 1
    for cross in session.scalars(select(OrgCross).where(OrgCross.housing_id_fk == unit.id)):
        cross.housing_id_fk = None
    return moved


def _delete_one(session, module, mv, entity: str, row, user: str) -> tuple[bool, str]:
    """Delete one record with the clean-up its kind needs. Returns
    (deleted, message)."""
    label = getattr(row, "code", None) or getattr(row, "name", None) or f"#{row.id}"
    extra = ""
    if entity == "line":
        refs = svc.line_references(session, row)
        if refs:
            listed = ", ".join(_plural_count(n, noun) for noun, n in refs.items())
            return False, gettext("%(name)s is still used by %(uses)s. Move or delete those first, or mark the %(noun)s retired.",
                                  name=label, uses=listed, noun=_word(mv.line_noun))
    elif entity == "housing":
        moved = _unassign_residents(session, module, row, user)
        if moved:
            extra = " " + ngettext("%(num)s record that was in it is now without a %(noun)s.",
                                   "%(num)s records that were in it are now without a %(noun)s.",
                                   moved, noun=_word(mv.housing_noun))
    elif entity == "location":
        # Whatever sat in it stays, just unplaced; children move up a level.
        for unit in session.scalars(select(OrgHousing).where(OrgHousing.location_id_fk == row.id)):
            unit.location_id_fk, unit.row, unit.col = row.parent_id_fk, None, None
        for child in session.scalars(select(OrgLocation).where(OrgLocation.parent_id_fk == row.id)):
            child.parent_id_fk = row.parent_id_fk
        for cohort in session.scalars(select(OrgCohort).where(OrgCohort.location_id_fk == row.id)):
            cohort.location_id_fk = None
    elif entity == "animal":
        for child in session.scalars(select(Organism).where(
                (Organism.parent_a_id_fk == row.id) | (Organism.parent_b_id_fk == row.id))):
            if child.parent_a_id_fk == row.id:
                child.parent_a_id_fk = None
            if child.parent_b_id_fk == row.id:
                child.parent_b_id_fk = None
    elif entity == "cross":
        for cohort in session.scalars(select(OrgCohort).where(OrgCohort.cross_id_fk == row.id)):
            cohort.cross_id_fk = None
    elif entity == "cohort":
        for member in session.scalars(select(Organism).where(Organism.cohort_id_fk == row.id)):
            member.cohort_id_fk = None
    session.flush()
    session.delete(row)
    svc.log_event(session, module, entity, row.id, "delete", recorded_by=user, notes=f"Deleted {label}")
    return True, gettext("Deleted %(name)s.", name=label) + extra


@bp.route("/<key>/<entity>/<int:row_id>/delete", methods=["POST"])
def delete_row(key: str, entity: str, row_id: int):
    model = DELETABLE.get(entity)
    if model is None:
        abort(404)
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        mv = svc.view(module)
        back = DELETE_RETURN.get(entity, "animals")
        row = session.get(model, row_id)
        if row is None or row.module_id_fk != module.id:
            abort(404)
        if entity == "location":
            denied = _deny_location(module, key, row)
            if denied:
                return denied
        elif not access.can_edit(row):
            flash(access.reason_denied(row), "error")
            return _redirect_back(key, back)
        deleted, message = _delete_one(session, module, mv, entity, row, g.user.username)
        if not deleted:
            session.rollback()
            flash(message, "error")
            return _redirect_back(key, back)
        session.flush()
        svc.recompute_due(session, module)
        session.commit()
        flash(message, "success")
        return _redirect_back(key, back)


# ---------------------------------------------------------------------------
# Batch actions from the selection bar
# ---------------------------------------------------------------------------


def _selected(session, model, module) -> list:
    ids = [int(i) for i in request.form.getlist("selected_ids") if str(i).isdigit()]
    if not ids:
        return []
    return list(session.scalars(select(model).where(
        model.module_id_fk == module.id, model.id.in_(ids)).order_by(model.id)))


def _custom_field(session, module, entity: str, field: str):
    """The custom field a bulk "attr_<key>" names, for this entity."""
    if not field.startswith("attr_"):
        return None
    return next((f for f in svc.fields_for(session, module.id, entity) if f"attr_{f.key}" == field), None)


def _set_custom(row, custom, value: str) -> str | None:
    """One custom field's value on one record, checked as the dialog checks
    it; the problem in words when it can't be taken."""
    attrs, errors = svc.read_attrs_checked({f"attr_{custom.key}": value}, [custom], svc.load_dict(row.attrs))
    if errors:
        return errors[0] + " " + gettext("Nothing was changed.")
    row.attrs = json.dumps(attrs)
    return None


@bp.route("/<key>/animals/bulk", methods=["POST"])
def bulk_animals(key: str):
    """Set status, housing or owner on the ticked records, or delete them.
    One audit batch, so it can be undone from Batch history."""
    action = request.form.get("action", "")
    field = request.form.get("field", "")
    value = (request.form.get("value") or "").strip()
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        mv = svc.view(module)
        rows = _selected(session, Organism, module)
        if not rows:
            flash(gettext("No %(nouns)s selected, so nothing changed.", nouns=_word(mv.organism_noun_plural)), "info")
            return _redirect_back(key, "animals")
        editable = [r for r in rows if access.can_edit(r)]
        skipped = len(rows) - len(editable)
        noun = lambda n: mv.organism_noun if n == 1 else mv.organism_noun_plural
        custom = _custom_field(session, module, "organism", field) if action == "set" else None
        if action == "set" and field not in ("status", "housing_id_fk", "owner") and custom is None:
            return _fail(key, "animals", gettext("Pick what to set."))
        if custom is not None and not value and request.form.get("clear") != "1":
            return _fail(key, "animals", gettext(
                "Type what to set %(field)s to. (To empty it on those rows, leave it blank and confirm.)",
                field=i18n.translate_value(custom.label, "field")))
        if action not in ("set", "delete"):
            return _fail(key, "animals", gettext("Unknown action."))
        housing_id = None
        if action == "set" and field == "housing_id_fk" and value:
            housing_id = _ref(session, OrgHousing, module.id, value)
            if housing_id is None:
                return _fail(key, "animals", gettext("That %(noun)s no longer exists.", noun=_word(mv.housing_noun)))
        with audit.batch(session, "delete" if action == "delete" else "update",
                         f"{'delete' if action == 'delete' else 'set ' + field} ×{len(editable)} "
                         f"{mv.organism_noun_plural} ({mv.label})", "organisms"):
            for row in editable:
                if action == "delete":
                    _delete_one(session, module, mv, "animal", row, g.user.username)
                    continue
                if field == "status":
                    previous = row.status
                    row.status = value
                    svc.apply_status_rules(mv, row, previous)
                elif field == "housing_id_fk":
                    row.housing_id_fk = housing_id
                elif custom is not None:
                    problem = _set_custom(row, custom, value)
                    if problem:
                        return _fail(key, "animals", problem)
                else:
                    row.owner = value
                row.updated_at, row.updated_by = datetime.utcnow(), g.user.username
                svc.log_event(session, module, "organism", row.id, "update", count=row.count,
                              recorded_by=g.user.username, notes=f"Batch: {field} → {value or '—'}")
        session.flush()
        svc.recompute_due(session, module)
        session.commit()
        n = len(editable)
        if action == "delete":
            message = gettext("Deleted %(n)s %(nouns)s.", n=n, nouns=_word(noun(n)))
        else:
            label = (i18n.translate_value(custom.label, "field") if custom is not None else
                     {"status": gettext("status"), "housing_id_fk": _word(mv.housing_noun),
                      "owner": gettext("owner")}[field])
            message = gettext("Set %(field)s on %(n)s %(nouns)s.", field=label, n=n, nouns=_word(noun(n)))
        if skipped:
            message += " " + gettext("%(n)s belong to someone else and were left alone.", n=skipped)
    flash(message, "success" if editable else "error")
    return _redirect_back(key, "animals")


@bp.route("/<key>/housing/bulk", methods=["POST"])
def bulk_housing(key: str):
    """Set purpose, owner or location on the ticked units, or delete them
    (their residents are unassigned, not deleted). One audit batch."""
    action = request.form.get("action", "")
    field = request.form.get("field", "")
    value = (request.form.get("value") or "").strip()
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        mv = svc.view(module)
        units = _selected(session, OrgHousing, module)
        if not units:
            flash(gettext("No %(nouns)s selected, so nothing changed.", nouns=_word(mv.housing_noun_plural)), "info")
            return _redirect_back(key, "housing")
        editable = [u for u in units if access.can_edit(u)]
        skipped = len(units) - len(editable)
        noun = lambda n: mv.housing_noun if n == 1 else mv.housing_noun_plural
        custom = _custom_field(session, module, "housing", field) if action == "set" else None
        if action == "set" and field not in ("purpose", "owner", "location_id_fk") and custom is None:
            return _fail(key, "housing", gettext("Pick what to set."))
        if custom is not None and not value and request.form.get("clear") != "1":
            return _fail(key, "housing", gettext(
                "Type what to set %(field)s to. (To empty it on those rows, leave it blank and confirm.)",
                field=i18n.translate_value(custom.label, "field")))
        if action not in ("set", "delete"):
            return _fail(key, "housing", gettext("Unknown action."))
        location_id = None
        if action == "set" and field == "location_id_fk" and value:
            location_id = _ref(session, OrgLocation, module.id, value)
            if location_id is None:
                return _fail(key, "housing", gettext("That %(noun)s no longer exists.", noun=_word(mv.container_noun)))
        unassigned = 0
        with audit.batch(session, "delete" if action == "delete" else "update",
                         f"{'delete' if action == 'delete' else 'set ' + field} ×{len(editable)} "
                         f"{mv.housing_noun_plural} ({mv.label})", "organism_housing"):
            for unit in editable:
                if action == "delete":
                    unassigned += _unassign_residents(session, module, unit, g.user.username)
                    _delete_one(session, module, mv, "housing", unit, g.user.username)
                    continue
                if field == "location_id_fk":
                    if unit.location_id_fk != location_id:
                        unit.location_id_fk = location_id
                        unit.row = unit.col = None
                elif custom is not None:
                    problem = _set_custom(unit, custom, value)
                    if problem:
                        return _fail(key, "housing", problem)
                else:
                    setattr(unit, field, value)
                unit.updated_at, unit.updated_by = datetime.utcnow(), g.user.username
                svc.log_event(session, module, "housing", unit.id, "update",
                              recorded_by=g.user.username, notes=f"Batch: {field} → {value or '—'}")
        session.flush()
        svc.recompute_due(session, module)
        session.commit()
        n = len(editable)
        if action == "delete":
            message = gettext("Deleted %(n)s %(nouns)s.", n=n, nouns=_word(noun(n)))
            if unassigned:
                message += " " + ngettext("%(num)s record that was in them is now without a %(noun)s.",
                                          "%(num)s records that were in them are now without a %(noun)s.",
                                          unassigned, noun=_word(mv.housing_noun))
        elif field == "location_id_fk":
            message = gettext("Moved %(n)s %(nouns)s. They are unplaced there: drag them onto the %(noun)s grid to give them a position.",
                              n=n, nouns=_word(noun(n)), noun=_word(mv.container_noun))
        else:
            label = (i18n.translate_value(custom.label, "field") if custom is not None else
                     {"purpose": gettext("purpose"), "owner": gettext("owner")}.get(field, field))
            message = gettext("Set %(field)s on %(n)s %(nouns)s.", field=label, n=n, nouns=_word(noun(n)))
        if skipped:
            message += " " + gettext("%(n)s belong to someone else and were left alone.", n=skipped)
    flash(message, "success" if editable else "error")
    return _redirect_back(key, "housing")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@bp.route("/<key>/configure", methods=["POST"])
def configure(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        denied = _deny_configure(module, key)
        if denied:
            return denied
        form = request.form

        label = (form.get("label") or module.label).strip()
        clash = database_keys.name_clash(session, label, "organisms", module)
        if clash and label.casefold() != (module.label or "").casefold():
            flash(gettext("There is already a database called %(name)s; give this one a name of its own.",
                          name=clash), "error")
            return _redirect_back(module.key, "settings")
        module.label = label
        module.label_plural = (form.get("label_plural") or module.label).strip()
        module.icon = (form.get("icon") or module.icon).strip()
        if "blurb" in form:
            module.blurb = (form.get("blurb") or "").strip()
        module.identity_mode = form.get("identity_mode") or module.identity_mode
        module.age_unit = form.get("age_unit") or module.age_unit
        for noun in ("organism_noun", "organism_noun_plural", "housing_noun",
                     "housing_noun_plural", "container_noun", "line_noun",
                     "line_noun_plural", "cohort_noun", "cohort_noun_plural",
                     "cross_noun"):
            value = (form.get(noun) or "").strip()
            if value:
                setattr(module, noun, value)

        if "capabilities" in form or form.get("_full") == "1":
            capabilities = svc.normalize_capabilities(form.getlist("capabilities"))
            if module.identity_mode == "individual":
                capabilities = [c for c in capabilities if c != "group_counts"] + ["individuals"]
            elif module.identity_mode == "group":
                capabilities = [c for c in capabilities if c != "individuals"] + ["group_counts"]
            module.capabilities = svc.dump(svc.normalize_capabilities(capabilities))

        for name in ("housing_purposes", "statuses", "sexes"):
            raw = form.get(name)
            if raw is not None:
                values = [v.strip() for v in raw.split(",") if v.strip()]
                setattr(module, name, svc.dump(values))

        settings = svc.load_dict(module.settings)
        raw_metrics = form.get("environment_metrics")
        if raw_metrics is not None:
            settings["environment_metrics"] = svc.metrics_from_text(raw_metrics)
        if "dead_statuses" in form:
            settings["dead_statuses"] = [v.strip() for v in form.get("dead_statuses", "").split(",") if v.strip()]
        if "cross_noun_plural" in form:
            plural = (form.get("cross_noun_plural") or "").strip()
            if plural and plural != pluralise(module.cross_noun):
                settings["cross_noun_plural"] = plural
            else:
                settings.pop("cross_noun_plural", None)
        module.settings = svc.dump(settings)

        if form.get("_full") == "1" or "disabled" in form:
            module.enabled = not form.get("disabled")
        session.flush()
        svc.recompute_due(session, module)
        moved = database_keys.rekey(session, "organisms", module)    # its address follows its name
        session.commit()
        flash(gettext("Configuration saved.") + (
            " " + gettext("Its address is now %(address)s; links to the old one still work.",
                          address=f"/organisms/{moved}") if moved else ""), "success")
        return _redirect_back(module.key, "settings")


@bp.route("/<key>/field/add", methods=["POST"])
def add_field(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        denied = _deny_configure(module, key)
        if denied:
            return denied
        form = request.form
        entity = form.get("entity") or "organism"
        field_type = form.get("field_type")
        label = (form.get("label") or "").strip()
        field_key = svc.slugify(form.get("key") or label)
        if entity not in dict(FIELD_ENTITIES) or field_type not in FIELD_TYPE_BY_KEY or not label:
            return _fail(key, "settings", gettext("Give the field a label and pick its type."))
        existing = session.scalar(select(ModuleField).where(
            ModuleField.module_id_fk == module.id, ModuleField.entity == entity,
            ModuleField.key == field_key))
        if existing is not None:
            return _fail(key, "settings",
                         gettext("%(entity)s already has a field “%(name)s” (%(key)s). Remove it first, or pick another name.",
                                 entity=i18n.translate_value(dict(FIELD_ENTITIES)[entity]),
                                 name=i18n.translate_value(existing.label, "field"), key=field_key))
        options = [v.strip() for v in (form.get("options") or "").split(",") if v.strip()]
        default = (form.get("default_value") or "").strip()
        if field_type == "select" and not options:
            return _fail(key, "settings", gettext("A choice field needs its choices, separated by commas."))
        if default and field_type == "number":
            try:
                float(default)
            except ValueError:
                return _fail(key, "settings", gettext("The default for a number field must be a number (got “%(typed)s”).", typed=default))
        if default and field_type == "select" and default not in options:
            return _fail(key, "settings", gettext("The default “%(typed)s” is not one of the choices.",
                                                  typed=default))
        row = svc.add_field(session, module, {
            "entity": entity, "key": field_key, "label": label, "field_type": field_type,
            "options": options, "default_value": default,
            "help_text": form.get("help_text"),
            "required": bool(form.get("required")),
            "show_in_table": bool(form.get("show_in_table")),
        })
        session.commit()
        flash(gettext("Added field %(name)s.", name=row.label), "success")
        return _redirect_back(key, "settings")


@bp.route("/<key>/field/<int:field_id>/delete", methods=["POST"])
def delete_field(key: str, field_id: int):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        denied = _deny_configure(module, key)
        if denied:
            return denied
        row = session.get(ModuleField, field_id)
        if row is None or row.module_id_fk != module.id:
            abort(404)
        label = row.label
        session.delete(row)
        session.commit()
        flash(gettext("Removed field %(name)s. Stored values are kept.",
                      name=i18n.translate_value(label, "field")), "success")
        return _redirect_back(key, "settings")


@bp.route("/<key>/rule/save", methods=["POST"])
def save_rule(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        denied = _deny_configure(module, key)
        if denied:
            return denied
        form = request.form
        rules = svc.load_list(module.schedule_rules)
        rule_key = svc.slugify(form.get("key") or form.get("label") or "")
        if not (form.get("key") or form.get("label") or "").strip():
            return _fail(key, "settings", gettext("Give the rule a name."))
        applies_to = form.get("applies_to") or "housing"
        anchor = form.get("anchor") or ""
        if applies_to not in SCHEDULE_ANCHORS:
            return _fail(key, "settings", gettext("Pick what the rule applies to."))
        if not anchor_allowed(applies_to, anchor):
            choices = ", ".join(i18n.translate_value(label) for _v, label in SCHEDULE_ANCHORS[applies_to])
            return _fail(key, "settings",
                         gettext("A %(subject)s rule counts from one of: %(choices)s.",
                                 subject=i18n.translate_value(applies_to, "subject"), choices=choices))
        replacing = (form.get("replace") or "").strip()
        existing = next((r for r in rules if r.get("key") == rule_key), None)
        if existing is not None and replacing != rule_key:
            return _fail(key, "settings",
                         gettext("There is already a rule “%(name)s”. Remove it first, or give this one another name.",
                                 name=i18n.translate_value(existing.get("label", rule_key), "rule")))
        offset = _int(form.get("offset_days"), None)
        if offset is None or offset < 0:
            return _fail(key, "settings", gettext("Days after must be a whole number of 0 or more."))

        rule = {
            "key": rule_key,
            "label": (form.get("label") or rule_key).strip(),
            "applies_to": applies_to,
            "anchor": anchor,
            "offset_days": offset,
            "icon": (form.get("icon") or "calendar-clock").strip(),
            "recurring": bool(form.get("recurring")),
            "temp_offsets": {},
        }
        # "18:28, 25:14" -> {"18": 28, "25": 14}
        for pair in (form.get("temp_offsets") or "").split(","):
            if ":" not in pair:
                continue
            temp, days = pair.split(":", 1)
            if temp.strip() and days.strip().isdigit():
                rule["temp_offsets"][temp.strip()] = int(days.strip())

        rules = [r for r in rules if r.get("key") != rule_key] + [rule]
        module.schedule_rules = svc.dump(rules)
        session.flush()
        svc.recompute_due(session, module)
        session.commit()
        flash(gettext("Saved rule %(name)s.", name=rule["label"]), "success")
        return _redirect_back(key, "settings")


@bp.route("/<key>/rule/<rule_key>/delete", methods=["POST"])
def delete_rule(key: str, rule_key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        denied = _deny_configure(module, key)
        if denied:
            return denied
        rules = [r for r in svc.load_list(module.schedule_rules) if r.get("key") != rule_key]
        module.schedule_rules = svc.dump(rules)
        session.flush()
        svc.recompute_due(session, module)
        session.commit()
        flash(gettext("Removed rule."), "success")
        return _redirect_back(key, "settings")


@bp.route("/<key>/delete", methods=["POST"])
def delete_module(key: str):
    with SessionLocal() as session:
        module = _module_or_404(session, key)
        if not access.can_configure(module):
            abort(403)
        if (request.form.get("confirm") or "").strip() != module.label:
            flash(gettext("Type the database name exactly to confirm deletion."), "error")
            return _redirect_back(key, "settings")
        label = module.label
        # Child rows are removed explicitly: the soft subject_kind/subject_id
        # references in the log tables have no FK to cascade from.
        for model in (Organism, OrgHousing, OrgCohort, OrgCross, OrgLine,
                      OrgLocation, OrgPreservationLot):
            session.query(model).filter(model.module_id_fk == module.id).delete(
                synchronize_session=False)
        for table in ("organism_events", "organism_due", "organism_measurements",
                      "organism_genotypes", "organism_module_fields"):
            session.execute(
                __import__("sqlalchemy").text(
                    f"DELETE FROM {table} WHERE module_id_fk = :mid"),
                {"mid": module.id},
            )
        session.execute(__import__("sqlalchemy").text("DELETE FROM app_settings WHERE key = :k"),
                        {"k": svc.SCHEDULE_KEY.format(module.id)})
        session.delete(module)
        session.commit()
        flash(gettext("Deleted the %(name)s database.", name=label), "success")
        return redirect(url_for("organisms.index"))
