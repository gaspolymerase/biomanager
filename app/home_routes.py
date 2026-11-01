"""The front door and Home: / (the welcome page before anyone signs in, else
the start page a person chose, else Home) and Home itself, with its daily
brief, its cards and calculators, and the cards and layout a person picks.

These pages hang on the application object from app/flask_app.py and keep
the endpoint names every page links to ("index", "home_dashboard", ...);
tests/test_url_map.py holds them to it. They take what every page needs
from app/web.py and never import app.py, which imports them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .db import SessionLocal
from . import access, i18n, lab
from .models import (
    CalendarEvent,
    LitterRecord,
    InventoryItem,
    MouseRecord,
    NotebookPage,
    NotebookTab,
    PlasmidRecord,
    TaskItem,
)
from .services import weaning_offset, weaning_due, weaning_title
from . import door, home_layouts, lab_calendar, lab_notebook, notify
from . import groups as project_groups
from .calendar_routes import task_visible_clause
from .days import fmt_day
from .flask_app import app
from .zebrafish_routes import zebrafish_home_summary
from .web import ALLOWED_LANDING_ENDPOINTS, LANDING_FEATURES, builtin_labels, local_time, login_required


@app.route("/")
def index():
    if g.user is None:
        return hello()
    # If the user picked a default landing page (settings → default_landing),
    # honor it. Otherwise show the dashboard.
    default_landing = (g.user.default_landing or "").strip()
    if default_landing and default_landing in ALLOWED_LANDING_ENDPOINTS \
            and lab.request_features().get(LANDING_FEATURES.get(default_landing, ""), True):
        return redirect(url_for(default_landing))
    return redirect(url_for("home_dashboard"))


def hello():
    """The first page someone sees before signing in (app/door.py): the
    sign-in to this lab, or, before there is any account, starting a lab."""
    return door.front()


@app.route("/home")
@login_required
def home_dashboard():
    """Home dashboard: counts, sac reminders, upcoming weanings, genotyping
    queue, recent orders. Read-only — every card links into the relevant
    page for actual edits."""
    today = date.today()

    with SessionLocal() as db_session:
        # ---- Counts ------------------------------------------------------
        # The active mice only, and how many are yours (home_layouts.colony_counts).
        mouse_counts = home_layouts.colony_counts(db_session, g.user.username)
        from . import inventory_service as _inv
        order_modules = [m.id for m in _inv.list_modules(db_session) if m.kind == "orders"]
        # Each orders inventory's own open statuses, not a hard-coded pair.
        from . import inventory_service as inventories
        pending_orders = inventories.open_order_count(db_session)
        restock = inventories.attention_items(db_session)
        notebook_pages = db_session.scalar(
            select(func.count(NotebookPage.id))
            .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
            .where(NotebookTab.owner_username == g.user.username)
        ) or 0

        # ---- Mice older than 30 weeks (sac candidates) -------------------
        # Mouse age comes from its litter's date_of_birth. Active mice only.
        old_mice = db_session.scalars(
            select(MouseRecord)
            .join(LitterRecord, MouseRecord.litter_id_fk == LitterRecord.id)
            .where(MouseRecord.date_of_death.is_(None))
            .where(LitterRecord.date_of_birth.is_not(None))
            .where(LitterRecord.date_of_birth <= today - timedelta(days=210))
            .order_by(LitterRecord.date_of_birth.asc())
            .limit(15)
        ).all()
        sac_candidates = []
        for mouse in old_mice:
            dob = mouse.litter.date_of_birth if mouse.litter else None
            if not dob:
                continue
            age_weeks = (today - dob).days // 7
            sac_candidates.append({
                "mouse_id": mouse.mouse_id,
                "gender": mouse.gender,
                "genotype": mouse.genotype,
                "owner": mouse.owner,
                "status": mouse.status,
                "age_weeks": age_weeks,
                "cage_id": mouse.cage.cage_id if mouse.cage else "",
            })

        # ---- Upcoming weanings: litters whose weaning day (DOB + the lab's
        # weaning day, as the cage cards use) is within ±7 days -------
        weanings = []
        for item in weaning_due(db_session, today - timedelta(days=7), today + timedelta(days=7))[:10]:
            days_to_wean = (item["due"] - today).days
            weanings.append({
                "title": weaning_title(item),
                "cage_row_id": item["cage"].id if item["cage"] else None,
                "dob": fmt_day(item["born"]),
                "wean_date": fmt_day(item["due"]),
                "days_to_wean": days_to_wean,
                "overdue": days_to_wean < 0,
                "pups": item["pups"],
                "cohort": item["litter"].cohort_name if item["litter"] else "",
            })

        # ---- Genotyping queue: mice with status='geno' OR recently born
        # (over 2 weeks old, no genotype set) ------------------------------
        geno_status_mice = db_session.scalars(
            select(MouseRecord)
            .where(MouseRecord.date_of_death.is_(None))
            .where(MouseRecord.status == "geno")
            .order_by(MouseRecord.mouse_id.desc())
            .limit(10)
        ).all()
        geno_queue = [{
            "mouse_id": m.mouse_id,
            "gender": m.gender,
            "owner": m.owner,
            "cage_id": m.cage.cage_id if m.cage else "",
            "genotype": m.genotype,
            "reason": "status=geno",
        } for m in geno_status_mice]

        # ---- Recent orders ------------------------------------------------
        recent_orders = db_session.scalars(
            select(InventoryItem).where(InventoryItem.module_id_fk.in_(order_modules))
            .order_by(InventoryItem.created_at.desc()).limit(6)
        ).all()
        orders_list = [{
            "id": o.number,
            "vendor": o.vendor,
            "item": o.name,
            "status": o.status,
            "qty": o.quantity,
            "created_at": i18n.strftime(local_time(o.created_at), "%b %d, %Y"),
        } for o in recent_orders]

        # ---- Upcoming calendar events ------------------------------------
        upcoming_events = db_session.scalars(
            select(CalendarEvent)
            .where(lab_calendar.event_visible_clause())
            .where(CalendarEvent.event_date >= today)
            .where(CalendarEvent.event_date <= today + timedelta(days=14))
            .order_by(CalendarEvent.event_date.asc())
            .limit(6)
        ).all()
        events_list = [{
            "title": e.title,
            "event_date": e.event_date,
            "days_until": (e.event_date - today).days,
            "event_type": e.event_type,
        } for e in upcoming_events]

    # The lab's databases as the sidebar lists them (app/home_layouts.py):
    # each brings its own cards and Counts tile.
    from . import organism_service, stock_service
    from .icons import resolve as resolve_icon
    features = lab.request_features()
    with SessionLocal() as db_session:
        databases = home_layouts.lab_databases(db_session, features)
        lab_cards = home_layouts.lab_cards(databases)

        # Each fly or worm database's work due soon: flips, egg collections,
        # shifts, scoring; and each animal database's schedule (wean, retire…).
        db_cards, due_count = {}, {}
        for db in databases:
            module = db["module"]
            if db["kind"] == "stock":
                mv = stock_service.view(module)
                rows = [{"title": item["title"], "due": item["due"], "overdue": item["overdue"],
                         "is_today": item["today"]}
                        for item in stock_service.schedule(db_session, mv, today, horizon=2)]
                rows.sort(key=lambda i: i["due"])
                db_cards[f"stock:{mv.key}"] = {
                    "label": mv.label, "icon": resolve_icon(module.icon), "worms": module.kind == "worm",
                    "url": url_for("stocks.module", key=mv.key, view="schedule"), "rows": rows}
                due_count[db["key"]] = len(rows)
        organism_due = organism_service.home_due(db_session, horizon_days=2)
        for db in databases:
            card = f"org:{db['module'].key}" if db["kind"] == "organism" else None
            if card and any(c[0] == card for c in lab_cards):
                rows = [d for d in organism_due if d["key"] == db["module"].key]
                db_cards[card] = {
                    "label": db["module"].label, "icon": resolve_icon(db["module"].icon or "paw"),
                    "url": url_for("organisms.module", key=db["module"].key, view="schedule"), "rows": rows}
                due_count[db["key"]] = len(rows)
        tiles = home_layouts.count_tiles(
            db_session, databases, g.user.username, builtin_labels(),
            {**mouse_counts, "notebook_pages": notebook_pages}, due_count, bool(features.get("notebook", True)))
        db_session.commit()

    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")

    # Which cards the lab's databases call for (app/lab.py): only what the
    # lab uses, plus this person's own databases.
    from . import inventory_service as _inv
    with SessionLocal() as db_session:
        visible_inventories = _inv.list_modules(db_session)
        has_orders = any(m.kind == "orders" for m in visible_inventories)
        has_restock = any(m.kind in _inv.RESTOCK_KINDS for m in visible_inventories)
        setup_needed = g.user.role == "admin" and not lab.setup_done(db_session)
        if g.user.role == "admin":
            notify.settle_signups(db_session)
        for_you = [{"id": n.id, "title": n.title, "created_at": n.created_at, "actor": n.actor or ""}
                   for n in notify.recent(db_session, g.user.username, limit=5, unread_only=True)]

    # Classic (the cards above) or one of the other layouts (app/home_layouts.py),
    # which draw the same work from one agenda.
    zebrafish_due = zebrafish_home_summary()
    with SessionLocal() as db_session:
        home_cards = home_layouts.get_cards(db_session, g.user.username, lab_cards)
        offered = home_layouts.offered_cards(features, {
            "has_orders": has_orders, "has_restock": has_restock,
            "has_animals": features.get("colony", True) or features.get("zebrafish", True)}, lab_cards)
        shown = {c["key"] for c in offered} - set(home_cards["hidden"])
        # Overview's bento always has the databases and the notebook's pages.
        extra = _home_extra_cards(db_session, shown | {"notebook"}, today)
        # Animals that may need a look (app/welfare.py): a member's own by
        # default, the lab's for animal care; ?animals= switches.
        from . import welfare
        welfare_view =(welfare.home_view(db_session, g.user, request.args.get("animals"), today, features)
                        if "welfare" in shown else None)
        home_layout = home_layouts.get_layout(db_session, g.user.username)
        layout_view = None
        span = home_layouts.span_arg(request.args.get("span"))
        # Overview's This week reaches Sunday; By place pulls the next few days.
        horizon = span if home_layout == "tracks" else (7 if home_layout == "classic" else home_layouts.PULL_DAYS)
        agenda, track_meta = home_layouts.build_agenda(
            db_session, today, horizon, lab.request_features(), zebrafish_due,
            colony_label=builtin_labels().get("colony", "Mouse colony"))
        if home_layout == "tracks":
            layout_view = home_layouts.tracks_view(agenda, track_meta, today, span)
        elif home_layout != "classic":
            layout_view = home_layouts.freezer_view(db_session, agenda, today, g.user.username)
        # The daily brief at the top: today's work in a few words.
        brief = home_layouts.daily_brief(agenda, today, welfare_view["count"] if welfare_view else 0)
        week = home_layouts.week_view(agenda, track_meta, today, extra.get("todos"))
        needs = home_layouts.needs_look(welfare_view)
        colony_due = home_layouts.colony_due(agenda, today)
        db_session.commit()

    return render_template(
        "home.html",
        home_cards=home_cards,
        offered_cards=offered,
        shown_cards=shown,
        **extra,
        home_layout=home_layout,
        layout_choices=home_layouts.LAYOUTS,
        layout_view=layout_view,
        has_orders=has_orders,
        has_restock=has_restock,
        setup_needed=setup_needed,
        for_you=for_you,
        zebrafish_due=zebrafish_due,
        db_cards=db_cards,
        tiles=tiles,
        shown_tiles=home_layouts.shown_tiles(tiles, home_cards["tiles"]),
        greeting=greeting,
        today_str=i18n.strftime(today, "%A, %b %d, %Y"),
        counts={
            **mouse_counts,
            "pending_orders": pending_orders,
            "notebook_pages": notebook_pages,
        },
        sac_candidates=sac_candidates,
        welfare_view=welfare_view,
        brief=brief,
        week=week,
        needs=needs,
        colony_due=colony_due,
        today_kicker=i18n.strftime(today, "%A %d %B"),
        weanings=weanings,
        geno_queue=geno_queue,
        orders_list=orders_list,
        events_list=events_list,
        restock=restock,
        wean_offset_days=weaning_offset(),
    )


# Calculators the Home card links to (static/bench-calcs.js ids).
HOME_CALCULATORS = [("dilute", "Dilute (C₁V₁)"), ("make", "Make a solution"), ("a260", "DNA / RNA concentration"),
                    ("count", "Count cells"), ("seed", "Seed plates"), ("rcf", "rpm ↔ × g"),
                    ("buffer", "Buffer at a pH"), ("mastermix", "Master mix")]


def _home_extra_cards(db_session, shown: set, today: date) -> dict:
    """What Home's optional cards show, read only when they are on."""
    me = g.user.username
    out = {"todos": [], "bookings": [], "recent_pages": [], "recent_plasmids": [], "home_calculators": HOME_CALCULATORS}
    if "plasmids" in shown:
        # Your newest plasmids first, then the lab's; a plasmid with no
        # sequence yet is marked, as the Plasmids page marks it.
        rows = db_session.scalars(select(PlasmidRecord).order_by(
            (PlasmidRecord.owner == me).desc(),
            func.coalesce(PlasmidRecord.updated_at, PlasmidRecord.created_at).desc(),
            PlasmidRecord.id.desc()).limit(6)).all()
        out["recent_plasmids"] = [{"number": p.plasmid_id, "name": p.name, "owner": p.owner,
                                   "url": url_for("plasmid_page", number=p.plasmid_id), "has_sequence": bool(p.full_sequence),
                                   "when": p.updated_at or p.created_at} for p in rows]
    if "todos" in shown:
        # Yours, and the lab's and your project groups' to-dos.
        rows = db_session.scalars(select(TaskItem).where(
            task_visible_clause(), TaskItem.status != "done",
            TaskItem.due_date.is_(None) | (TaskItem.due_date <= today + timedelta(days=7)))
            .order_by(TaskItem.due_date.is_(None), TaskItem.due_date).limit(8)).all()
        out["todos"] = [{"id": t.id, "title": t.title, "due": t.due_date,
                         "overdue": bool(t.due_date and t.due_date < today),
                         "whose": project_groups.label(t.is_shared, t.share_group_id, personal="", lab="Lab"),
                         "can_tick": access.can_edit_task(t)} for t in rows]
    if "bookings" in shown:
        from .models import EquipmentBooking
        start = datetime.combine(today, datetime.min.time())
        rows = db_session.scalars(select(EquipmentBooking).options(selectinload(EquipmentBooking.equipment)).where(
            EquipmentBooking.owner == me, EquipmentBooking.end_at >= start,
            EquipmentBooking.start_at < start + timedelta(days=8)).order_by(EquipmentBooking.start_at).limit(8)).all()
        out["bookings"] = [{"what": b.equipment.name, "start": b.start_at, "end": b.end_at, "purpose": b.purpose}
                           for b in rows]
    if "notebook" in shown:
        rows = db_session.scalars(lab_notebook.accessible_filter(
            select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id))
            .order_by(NotebookPage.updated_at.desc()).limit(6)).all()
        out["recent_pages"] = [{"id": p.id, "tab_id": p.tab_id_fk, "title": p.title or "Untitled page",
                                "updated_at": p.updated_at} for p in rows]
    return out


@app.route("/home/cards", methods=["POST"])
@login_required
def set_home_cards():
    """Customize Home: which cards show, their order, which are full width,
    and which Counts tiles there are."""
    with SessionLocal() as db_session:
        if request.form.get("reset") == "1":
            home_layouts.reset_cards(db_session, g.user.username)
        else:
            order = request.form.getlist("order")
            shown = set(request.form.getlist("show"))
            # The tiles only when the dialog listed them: none ticked is a choice too.
            tiles = request.form.getlist("tile") if request.form.get("tiles") == "1" else None
            cards = home_layouts.lab_cards(home_layouts.lab_databases(db_session, lab.request_features()))
            home_layouts.set_cards(db_session, g.user.username, order,
                                   [k for k in order if k not in shown], request.form.getlist("wide"),
                                   tiles, cards)
        db_session.commit()
    return redirect(url_for("home_dashboard"))


@app.route("/home/layout", methods=["POST"])
@login_required
def set_home_layout():
    """Switch Home between Overview, Timeline and By place; kept per person."""
    with SessionLocal() as db_session:
        home_layouts.set_layout(db_session, g.user.username, request.form.get("layout", ""))
        db_session.commit()
    return redirect(url_for("home_dashboard"))
