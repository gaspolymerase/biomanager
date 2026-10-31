"""The lab notebook's tabs, pages and templates: the notebook page and its
libraries (protocols, recipes, meetings, reading), making, renaming,
moving and deleting tabs and pages, templates and pages made from them,
images and files uploaded into a page, and what a page links to: looking a
record up by its number, opening it, the pages that mention it
(backlinks), and the records an @ finds as it is typed.

Kinds of page, sharing, editing together, version history, comments, tags
and the libraries' contents are app/lab_notebook.py's, a blueprint under
the same /notebook.

These pages hang on the application object from app/flask_app.py and keep
the endpoint names every page links to ("notebook", "notebook_templates_list",
...); tests/test_url_map.py holds them to it. They take what every page
needs from app/web.py and never import app.py, which imports them.
"""
from __future__ import annotations

from datetime import datetime

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import case, delete, func, select, update

from .db import SessionLocal
from . import access, i18n, lab
from .i18n import gettext
from .models import (
    InventoryItem,
    InventoryModule,
    MouseRecord,
    NotebookPage,
    NotebookTab,
    NotebookTopicShare,
    NotebookTemplate,
    PlasmidRecord,
)
from .formutil import _record_number, arg_int, attr_value_pattern, like_pattern
from .services import calculate_age_fields, parse_date, save_uploaded_file, save_uploaded_image
from . import lab_notebook
from . import groups as project_groups
from . import uploads as _uploads
from .flask_app import app
from .web import _get_user_directory, login_required


def _notebook_owner_filter(query):
    return query.where(NotebookTab.owner_username == g.user.username)


def _serialize_page(page: NotebookPage) -> dict:
    import json as _json
    try:
        props = _json.loads(page.properties) if page.properties else []
    except Exception:
        props = []
    return {
        "id": page.id,
        "tab_id": page.tab_id_fk,
        "title": page.title,
        "body": page.body,
        "entry_date": page.entry_date.isoformat() if page.entry_date else "",
        "properties": props,
        "created_at": page.created_at.isoformat(),
        "updated_at": page.updated_at.isoformat(),
    }


# The notebook's libraries, each a page of its own beside the sidebar:
# key -> (title, icon). The drawer beside a page (/protocol) still lists
# protocols to insert.
NOTEBOOK_LIBRARIES = {
    "protocols": ("Protocols", "protocol"),
    "recipes": ("Recipes", "flask"),
    "meetings": ("Meetings", "users"),
    "reading": ("Reading", "book-open"),
}


@app.route("/notebook")
@login_required
def notebook():
    return _notebook_page()


@app.route("/notebook/<any(protocols, recipes, meetings, reading):library>")
@login_required
def notebook_library(library: str):
    """The lab's protocols or recipes, or the papers and talks read, to look
    through, open, add to and sort into folders. Meetings, their days and
    who presents, are kept in the calendar (static/calendar-meetings.js)."""
    if library == "meetings":
        return redirect(url_for("calendar", meetings="all"))
    return _notebook_page(library)


# How a topic's page lists its pages: ?sort= -> its label.
NOTEBOOK_SORTS = {"edited": "Last edited", "name": "Name", "order": "Your order"}


def _notebook_page(library: str | None = None):
    selected_tab_id = arg_int("tab", None)
    selected_page_id = arg_int("page", None) if library is None else None
    with SessionLocal() as db_session:
        tab_rows = db_session.scalars(
            _notebook_owner_filter(select(NotebookTab)).order_by(NotebookTab.position, NotebookTab.id)
        ).all()
        selected_page = None
        role = None
        if selected_page_id is not None:
            candidate = db_session.get(NotebookPage, selected_page_id)
            role = lab_notebook.role_for(db_session, candidate) if candidate is not None else None
            if role is not None:
                selected_page = candidate
        # Other people's topics shared with this person (lab_notebook.topic_shares).
        shared_rows = lab_notebook.shared_topic_rows(db_session)
        selected_tab = None
        if selected_page is not None:
            selected_tab = next((tab for tab in [*tab_rows, *shared_rows] if tab.id == selected_page.tab_id_fk
                                 and (role == "owner" or tab in shared_rows)), None)
        # No page asked for: a topic's page (?tab=), or the notebook's home.
        if selected_page is None and library is None and selected_tab_id is not None:
            selected_tab = next((tab for tab in [*tab_rows, *shared_rows] if tab.id == selected_tab_id), None)

        side = lab_notebook.sidebar(db_session)
        kinds = side.pop("kinds_by_page")
        parents = lab_notebook.parent_ids([page.id for tab in [*tab_rows, *shared_rows] for page in tab.pages])
        tabs_data = lab_notebook.topic_tree(db_session, tab_rows, kinds, parents)
        shared_data = lab_notebook.topic_tree(db_session, shared_rows, kinds, parents) if shared_rows else []
        shared_roles = lab_notebook.topic_shares(db_session)
        owners = {tab.id: tab.owner_username for tab in shared_rows}
        owner_names = lab_notebook.display_names(db_session, list(owners.values()))
        for node in shared_data:
            node.update(shared=True, role=shared_roles.get(node["id"], "view"),
                        owner_name=owner_names.get(owners[node["id"]], owners[node["id"]]))
        by_id = {node["id"]: node for node in [*tabs_data, *shared_data]}
        # The open topic and the topics it sits in start unfolded.
        open_topics = set()
        here = selected_tab.id if selected_tab is not None else None
        while here is not None and here not in open_topics:
            open_topics.add(here)
            here = by_id[here]["parent_id"] if here in by_id else None
        topic_view = home = None
        sort = request.args.get("sort") if request.args.get("sort") in NOTEBOOK_SORTS else "edited"
        if selected_page is None and library is None and selected_tab is not None:
            node = by_id[selected_tab.id]
            pages = {page.id: page for page in selected_tab.pages}
            top = [pages[p["id"]] for p in node["pages"]]
            if sort == "edited":
                top.sort(key=lambda page: page.updated_at or datetime.min, reverse=True)
            elif sort == "name":
                top.sort(key=lambda page: (page.title or "").casefold())
            topic_view = {**node, "cards": lab_notebook.page_cards(db_session, top), "sort": sort,
                          "trail": [by_id[i] for i in _topic_trail(by_id, node["id"])]}
        elif selected_page is None and library is None:
            favorites = lab_notebook.favorite_ids(db_session)
            recent = db_session.scalars(lab_notebook.accessible_filter(
                select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id))
                .order_by(NotebookPage.updated_at.desc()).limit(8)).all()
            starred = {page.id: page for page in db_session.scalars(
                select(NotebookPage).where(NotebookPage.id.in_(favorites)))} if favorites else {}
            home = {"favorites": lab_notebook.page_cards(db_session, [starred[i] for i in favorites if i in starred]),
                    "recent": lab_notebook.page_cards(db_session, recent),
                    "topics": [node for node in tabs_data if node["depth"] == 0]}
        selected_page_data = (lab_notebook.page_payload(db_session, selected_page, role)
                              if selected_page is not None else None)
        selected_tab_id_value = selected_tab.id if selected_tab else None
        me = {"username": g.user.username, "name": g.user.display_name or g.user.username}
        # Old keys too ("old": the @ menu leaves them out), so a chip written
        # before a rename still reads as one.
        mention_types = [{"key": key, "label": m.label, "noun": m.item_noun, **({"old": True} if key != m.key else {})}
                         for key, m in _mention_modules(db_session, with_old=True).items()]
        from . import zotero
        zotero_state = zotero.state(zotero.link_for(db_session, g.user.username))

    return render_template(
        "notebook.html",
        tabs=tabs_data,
        shared_topics=[node for node in shared_data if node["depth"] == 0],
        open_topics=open_topics,
        topic_view=topic_view,
        home=home,
        sorts=NOTEBOOK_SORTS,
        folder_icons=lab_notebook.FOLDER_ICONS,
        selected_tab_id=selected_tab_id_value,
        # A topic the person opened (?tab= or one of its pages), not the
        # first one shown when nothing was: a new page goes there.
        topic_chosen=selected_tab_id is not None or selected_page_id is not None,
        selected_page=selected_page_data,
        side=side,
        kinds=lab_notebook.KINDS,
        kind_icons=lab_notebook.KIND_ICONS,
        kind_hints=lab_notebook.KIND_HINTS,
        # As [key, label] pairs: tojson sorts a dict's keys, and the rows keep this order.
        kind_fields={kind: list(fields.items()) for kind, fields in lab_notebook.KIND_FIELDS.items()},
        statuses=lab_notebook.STATUSES,
        me=me,
        mention_types=mention_types,
        lab_zone=lab.clock_zone(),
        starters=[{"key": key, "title": st["title"], "kind": st["kind"], "hint": st["hint"]}
                  for key, st in lab_notebook.STARTERS.items()],
        library=library,
        libraries=NOTEBOOK_LIBRARIES,
        zotero=zotero_state,
        # Each person's chosen short name (THX), shown on their round badge here too.
        shorts={u: d["own_short"] for u, d in _get_user_directory().items() if d.get("own_short")},
    )


def _topic_trail(by_id: dict, topic_id: int) -> list[int]:
    """The topics a topic sits in, outermost first, then itself."""
    chain = []
    while topic_id is not None and topic_id in by_id and topic_id not in chain:
        chain.append(topic_id)
        topic_id = by_id[topic_id]["parent_id"]
    return list(reversed(chain))


@app.route("/notebook/tabs/create", methods=["POST"])
@login_required
def notebook_create_tab():
    title = (request.form.get("title") or "New topic").strip() or "New topic"
    with SessionLocal() as db_session:
        # Inside another topic of theirs (?parent_id=), Inbox aside; else at the top.
        parent = db_session.get(NotebookTab, request.form.get("parent_id", type=int) or 0)
        if parent is not None and (parent.owner_username != g.user.username or lab_notebook.is_inbox(parent)):
            parent = None
        max_pos = db_session.scalar(
            _notebook_owner_filter(select(func.max(NotebookTab.position)))
        ) or 0
        tab = NotebookTab(owner_username=g.user.username, title=title, position=max_pos + 1,
                          parent_tab_id=parent.id if parent is not None else None)
        db_session.add(tab)
        db_session.commit()
        return jsonify({"ok": True, "id": tab.id, "title": tab.title})


@app.route("/notebook/tabs/<int:tab_id>/rename", methods=["POST"])
@login_required
def notebook_rename_tab(tab_id: int):
    new_title = (request.form.get("title") or "").strip() or "Untitled topic"
    with SessionLocal() as db_session:
        tab = db_session.get(NotebookTab, tab_id)
        if tab is None or tab.owner_username != g.user.username:
            return jsonify({"ok": False}), 404
        tab.title = new_title
        db_session.commit()
        return jsonify({"ok": True, "id": tab.id, "title": tab.title})


@app.route("/notebook/tabs/<int:tab_id>/delete", methods=["POST"])
@login_required
def notebook_delete_tab(tab_id: int):
    with SessionLocal() as db_session:
        tab = db_session.get(NotebookTab, tab_id)
        if tab is not None and tab.owner_username == g.user.username:
            lab_notebook.delete_page_rows(db_session, [page.id for page in tab.pages])
            # The topics inside it stay, one level up.
            db_session.execute(update(NotebookTab).where(NotebookTab.owner_username == g.user.username,
                                                         NotebookTab.parent_tab_id == tab.id)
                               .values(parent_tab_id=tab.parent_tab_id))
            db_session.execute(delete(NotebookTopicShare).where(NotebookTopicShare.tab_id_fk == tab.id))
            db_session.delete(tab)
            db_session.commit()
    return redirect(url_for("notebook"))


@app.route("/notebook/pages/create", methods=["POST"])
@login_required
def notebook_create_page():
    tab_id = request.form.get("tab_id", type=int)
    if not tab_id:
        return jsonify({"ok": False, "error": "tab_id required"}), 400
    with SessionLocal() as db_session:
        tab = db_session.get(NotebookTab, tab_id)
        if tab is None or tab.owner_username != g.user.username:
            return jsonify({"ok": False}), 404
        max_pos = db_session.scalar(
            select(func.max(NotebookPage.position)).where(NotebookPage.tab_id_fk == tab_id)
        ) or 0
        page = NotebookPage(
            tab_id_fk=tab_id,
            title=(request.form.get("title") or "Untitled page").strip() or "Untitled page",
            body="",
            position=max_pos + 1,
        )
        db_session.add(page)
        db_session.commit()
        return jsonify({"ok": True, "page": _serialize_page(page)})


@app.route("/notebook/pages/create-quick", methods=["POST"])
@login_required
def notebook_create_page_quick():
    with SessionLocal() as db_session:
        first_tab = db_session.scalar(
            _notebook_owner_filter(select(NotebookTab)).order_by(NotebookTab.position, NotebookTab.id).limit(1)
        )
        if first_tab is None:
            first_tab = NotebookTab(owner_username=g.user.username, title="Inbox", position=0)
            db_session.add(first_tab)
            db_session.flush()
        max_pos = db_session.scalar(
            select(func.max(NotebookPage.position)).where(NotebookPage.tab_id_fk == first_tab.id)
        ) or 0
        page = NotebookPage(
            tab_id_fk=first_tab.id,
            title="Untitled page",
            body="",
            position=max_pos + 1,
        )
        db_session.add(page)
        db_session.commit()
        return jsonify({"ok": True, "page": _serialize_page(page), "tab_id": first_tab.id})


def _templates_visible(me: str):
    """Your own templates, the lab's, and your project groups'."""
    lab_wide = NotebookTemplate.lab.is_(True) & NotebookTemplate.share_group_id.is_(None)
    mine = sorted(project_groups.ids_of())
    clause = (NotebookTemplate.owner_username == me) | lab_wide
    if mine:
        clause = clause | (NotebookTemplate.lab.is_(True) & NotebookTemplate.share_group_id.in_(mine))
    return clause


def _template_open(template) -> bool:
    """May this person start a page from it: theirs, the lab's, or a group's
    they are in."""
    return template is not None and (template.owner_username == g.user.username
                                     or project_groups.shared_with(template.lab, template.share_group_id))


@app.route("/notebook/templates")
@login_required
def notebook_templates_list():
    """Your templates, then the lab's, each with its page type."""
    me = g.user.username
    with SessionLocal() as db_session:
        rows = db_session.scalars(
            select(NotebookTemplate).where(_templates_visible(me))
            .order_by((NotebookTemplate.owner_username != me), NotebookTemplate.updated_at.desc())
        ).all()
        names = lab_notebook.display_names(db_session, list({t.owner_username for t in rows}))
        return jsonify({"ok": True, "templates": [
            {
                "id": t.id,
                "title": t.title,
                "icon": t.icon,
                "kind": t.kind or "note",
                "kind_label": lab_notebook.KINDS.get(t.kind or "note", "Note"),
                "lab": bool(t.lab),
                "group": project_groups.name_of(t.share_group_id) if t.lab and t.share_group_id else "",
                "mine": t.owner_username == me,
                "can_delete": t.owner_username == me or (bool(t.lab) and access.is_admin()),
                "owner_name": names.get(t.owner_username, t.owner_username),
                "body_preview": (t.body or "")[:160],
                "updated_at": t.updated_at.isoformat(),
            }
            for t in rows
        ]})


@app.route("/notebook/templates/<int:template_id>")
@login_required
def notebook_template_get(template_id: int):
    with SessionLocal() as db_session:
        template = db_session.get(NotebookTemplate, template_id)
        if not _template_open(template):
            return jsonify({"ok": False}), 404
        return jsonify({"ok": True, "template": {"id": template.id, "title": template.title, "icon": template.icon,
                                                 "kind": template.kind or "note", "body": template.body or ""}})


@app.route("/notebook/templates/create", methods=["POST"])
@login_required
def notebook_template_create():
    """Create a template either from scratch or from an existing page.

    Form fields:
      title (str, required)
      icon  (str, optional — emoji/single-character)
      body  (str, optional)
      kind  (str, optional — the page type pages made from it get)
      from_page_id (int, optional — copies the page's text and its type)
      structure_only (1, optional — with from_page_id: headings, steps and
        table headers, without the results; lab_notebook.structure_only)
      lab (1, optional — everyone in the lab can start pages from it)
    """
    title = (request.form.get("title") or "").strip()[:160] or "Untitled template"
    icon = (request.form.get("icon") or "").strip()
    body = request.form.get("body", "")
    kind = request.form.get("kind", "")
    from_page_id = request.form.get("from_page_id", type=int)

    with SessionLocal() as db_session:
        if from_page_id:
            page = db_session.get(NotebookPage, from_page_id)
            if page is None or lab_notebook.role_for(db_session, page) is None:
                return jsonify({"ok": False, "error": gettext("page not found")}), 404
            body = page.body or body
            info = lab_notebook.info_for(db_session, page.id)
            kind = info.kind if info is not None else kind
            if request.form.get("structure_only") == "1":
                body = lab_notebook.structure_only(body)
        # One of theirs by that name already: asked first, then saved over
        # (replace=1), not a second one of the same name in the list.
        template = db_session.scalar(select(NotebookTemplate).where(
            NotebookTemplate.owner_username == g.user.username,
            func.lower(NotebookTemplate.title) == title.lower()).limit(1))
        if template is not None and request.form.get("replace") != "1":
            return jsonify({"ok": False, "exists": True,
                            "error": gettext("You already have a template called “%(title)s”.",
                                             title=template.title)}), 409
        if template is None:
            template = NotebookTemplate(owner_username=g.user.username)
            db_session.add(template)
        template.title, template.icon, template.body = title, icon, body
        template.kind = kind if kind in lab_notebook.KINDS and kind != "daily" else "note"
        # "1": the lab's; "g<id>": a project group's (app/groups.py).
        shared, group_id = project_groups.parse(request.form.get("lab"))
        if group_id is not None and not project_groups.may_share_with(group_id):
            return jsonify({"ok": False, "error": project_groups.refusal(group_id)}), 403
        template.lab, template.share_group_id = shared, group_id if shared else None
        db_session.commit()
        return jsonify({
            "ok": True,
            "template": {"id": template.id, "title": template.title, "icon": template.icon,
                         "kind": template.kind, "lab": template.lab},
        })


@app.route("/notebook/templates/<int:template_id>/delete", methods=["POST"])
@login_required
def notebook_template_delete(template_id: int):
    """Its maker's to delete (a lab template also an admin's)."""
    with SessionLocal() as db_session:
        template = db_session.get(NotebookTemplate, template_id)
        if template is None or not (template.owner_username == g.user.username
                                    or (template.lab and access.is_admin())):
            return jsonify({"ok": False}), 404
        db_session.delete(template)
        db_session.commit()
        return jsonify({"ok": True})


@app.route("/notebook/pages/create-from-template", methods=["POST"])
@login_required
def notebook_create_page_from_template():
    """Create a new page seeded from a template. Optionally targets a specific
    tab; defaults to the first available (or creates an Inbox like
    create-quick)."""
    template_id = request.form.get("template_id", type=int)
    tab_id = request.form.get("tab_id", type=int)
    if not template_id:
        return jsonify({"ok": False, "error": "template_id required"}), 400

    with SessionLocal() as db_session:
        template = db_session.get(NotebookTemplate, template_id)
        if not _template_open(template):
            return jsonify({"ok": False, "error": gettext("template not found")}), 404

        if tab_id:
            tab = db_session.get(NotebookTab, tab_id)
            if tab is None or tab.owner_username != g.user.username:
                return jsonify({"ok": False}), 404
        else:
            tab = db_session.scalar(
                _notebook_owner_filter(select(NotebookTab)).order_by(NotebookTab.position, NotebookTab.id).limit(1)
            )
            if tab is None:
                tab = NotebookTab(owner_username=g.user.username, title="Inbox", position=0)
                db_session.add(tab)
                db_session.flush()

        max_pos = db_session.scalar(
            select(func.max(NotebookPage.position)).where(NotebookPage.tab_id_fk == tab.id)
        ) or 0
        page = NotebookPage(
            tab_id_fk=tab.id,
            title=template.title or "Untitled page",
            body=template.body or "",
            position=max_pos + 1,
        )
        db_session.add(page)
        db_session.commit()
        return jsonify({"ok": True, "page": _serialize_page(page), "tab_id": tab.id})


@app.route("/notebook/pages/<int:page_id>/move", methods=["POST"])
@login_required
def notebook_move_page(page_id: int):
    new_tab_id = request.form.get("tab_id", type=int)
    if not new_tab_id:
        return jsonify({"ok": False, "error": "tab_id required"}), 400
    with SessionLocal() as db_session:
        page = db_session.get(NotebookPage, page_id)
        if page is None or page.tab.owner_username != g.user.username:
            return jsonify({"ok": False}), 404
        new_tab = db_session.get(NotebookTab, new_tab_id)
        if new_tab is None or new_tab.owner_username != g.user.username:
            return jsonify({"ok": False}), 404
        # A folder holds a page with the pages made inside it: they move together.
        lab_notebook.move_page(db_session, page, new_tab)
        db_session.commit()
        return jsonify({"ok": True, "tab_id": new_tab_id, "page_id": page.id})


@app.route("/notebook/pages/<int:page_id>/update", methods=["POST"])
@login_required
def notebook_update_page(page_id: int):
    """Save a page's title, text, date or properties; anyone it is shared
    with for editing may. The live editor sends X-Collab-Gen with the text:
    a save from an editor on an older editing state is refused (it would put
    back what a restore replaced), and a save without it (the plain-text
    fallback) starts the live editors again from the new text."""
    with SessionLocal() as db_session:
        page = db_session.get(NotebookPage, page_id)
        role = lab_notebook.role_for(db_session, page) if page is not None else None
        if role is None:
            return jsonify({"ok": False}), 404
        if not lab_notebook.can_edit_role(role):
            return jsonify({"ok": False, "error": gettext("This page is view only.")}), 403
        from . import signatures
        refused = signatures.refuse_if_locked(db_session, page.id)
        if refused:
            return refused
        changed = False
        if "title" in request.form:
            title = (request.form.get("title") or "").strip() or "Untitled page"
            changed = changed or title != page.title
            page.title = title
        if "body" in request.form:
            body = request.form.get("body", "")
            collab = request.headers.get("X-Collab-Gen")
            generation = lab_notebook.collab_generation(db_session, page.id)
            if collab is not None and collab != str(generation):
                return jsonify({"ok": False, "reset": True, "gen": generation}), 409
            # From the live editor, with the edits it holds: a tab that has
            # fallen behind (a background tab saving late) does not put an
            # older text over a newer one; its own edits are already in it.
            state = request.form.get("collab_state", "")
            info = lab_notebook.info_for(db_session, page.id, create=True) if collab is not None else None
            if info is not None and state and lab_notebook.behind(info.body_state, state):
                db_session.commit()
                return jsonify({"ok": True, "behind": True, "updated_at": page.updated_at.isoformat()})
            if body != (page.body or ""):
                page.body = body
                changed = True
                if collab is None:
                    lab_notebook.reset_collab(db_session, page.id)
            if info is not None and state:
                info.body_state = state[:200_000]
        if "entry_date" in request.form:
            raw_date = (request.form.get("entry_date") or "").strip()
            page.entry_date = parse_date(raw_date) if raw_date else None
        if "properties" in request.form:
            # The page's details (a paper's DOI, a cover): kept to what a page holds.
            import json as _json
            try:
                parsed = _json.loads(request.form.get("properties") or "[]")
            except _json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                page.properties = _json.dumps(lab_notebook.clean_properties(parsed))
        page.updated_at = datetime.utcnow()
        if changed:
            live = request.headers.get("X-Collab-Gen") is not None
            lab_notebook.record_edit(db_session, page, lab_notebook.last_editor(db_session, page.id) if live else None)
        db_session.commit()
        return jsonify({"ok": True, "updated_at": page.updated_at.isoformat()})


@app.route("/notebook/pages/<int:page_id>/delete", methods=["POST"])
@login_required
def notebook_delete_page(page_id: int):
    with SessionLocal() as db_session:
        page = db_session.get(NotebookPage, page_id)
        if page is None or page.tab.owner_username != g.user.username:
            return redirect(url_for("notebook"))
        from . import signatures
        if signatures.is_signed(db_session, page.id):
            message = gettext("A signed page is a record: it can't be deleted.")
            if request.headers.get("X-Requested-With") == "fetch":
                return jsonify({"ok": False, "error": message}), 409
            flash(message, "error")
            return redirect(url_for("notebook", page=page.id))
        tab_id = page.tab_id_fk
        lab_notebook.delete_page_rows(db_session, [page.id])
        db_session.delete(page)
        db_session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "tab_id": tab_id})
    return redirect(url_for("notebook", tab=tab_id))


@app.route("/notebook/upload-image", methods=["POST"])
@login_required
def notebook_upload_image():
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return jsonify({"ok": False}), 400
    saved = save_uploaded_image(upload)
    if not saved:
        return jsonify({"ok": False}), 400
    _note_uploader(saved)
    return jsonify({"ok": True, "url": url_for("static", filename=saved)})


def _note_uploader(saved_path: str) -> None:
    """Its uploader may open the picture or file before the page naming it
    is saved (app/uploads.py). Uploaded into a page they may edit (the live
    editor sends page_id), the page owns it at once, so whoever else has the
    page open sees the picture now, not after autosave."""
    with SessionLocal() as db_session:
        _uploads.note_uploader(db_session, saved_path, g.user.username)
        page_id = request.form.get("page_id", "")
        page = db_session.get(NotebookPage, int(page_id)) if page_id.isdigit() else None
        if page is not None and lab_notebook.can_edit_role(lab_notebook.role_for(db_session, page)):
            from . import signatures
            if not signatures.is_signed(db_session, page.id):
                _uploads.note_record(db_session, saved_path, "notebook_pages", page.id)
        db_session.commit()


@app.route("/notebook/upload-file", methods=["POST"])
@login_required
def notebook_upload_file():
    upload = request.files.get("file")
    saved = save_uploaded_file(upload)
    if not saved:
        return jsonify({"ok": False}), 400
    _note_uploader(saved["path"])
    return jsonify({
        "ok": True,
        "url": url_for("static", filename=saved["path"]),
        "name": saved["original_name"],
        "size": saved["size_bytes"],
    })


@app.route("/notebook/lookup/mouse/<int:mouse_id>")
@login_required
def notebook_lookup_mouse(mouse_id: int):
    with SessionLocal() as db_session:
        mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == mouse_id))
        if mouse is None:
            return jsonify({"ok": False}), 404
        cage_id = mouse.cage.cage_id if mouse.cage else ""
        # Its age, from its litter's birth date, as the colony sheet counts it.
        ages = calculate_age_fields(mouse.litter.date_of_birth if mouse.litter else None)
        label = f"Mouse #{mouse.mouse_id} · {mouse.gender or '?'} · {mouse.genotype or '(no genotype)'} · cage {cage_id or '—'} · {mouse.owner or 'no owner'}"
        return jsonify(
            {
                "ok": True,
                "label": label,
                "mouse_id": mouse.mouse_id,
                "gender": mouse.gender,
                "genotype": mouse.genotype,
                "status": mouse.status,
                "owner": mouse.owner,
                "cage_id": cage_id,
                "age_days": ages["age_days"],
                "age_weeks": ages["age_weeks"],
            }
        )


@app.route("/notebook/lookup/plasmid/<int:plasmid_id>")
@login_required
def notebook_lookup_plasmid(plasmid_id: int):
    with SessionLocal() as db_session:
        plasmid = db_session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == plasmid_id))
        if plasmid is None:
            return jsonify({"ok": False}), 404
        label = f"Plasmid #{plasmid.plasmid_id} · {plasmid.name or '(no name)'} · {plasmid.backbone or '?'} · {plasmid.resistance or 'no resistance'} · {plasmid.owner or 'no owner'}"
        return jsonify(
            {
                "ok": True,
                "label": label,
                "plasmid_id": plasmid.plasmid_id,
                "name": plasmid.name,
                "backbone": plasmid.backbone,
                "insert_seq": plasmid.insert_seq,
                "resistance": plasmid.resistance,
                "owner": plasmid.owner,
                "location": plasmid.location,
            }
        )


# @links to inventory records: "@antibodies 12" is item #12 of the database
# whose key is "antibodies". Mice, plasmids and orders keep their own words.
MENTION_BUILTINS = ("mouse", "plasmid", "order", "all")


def _mention_modules(db_session, with_old: bool = False) -> dict[str, InventoryModule]:
    """The inventories the signed-in person can see and @link, by key; with
    `with_old`, also by the keys they had before a rename, so "@antibodies 5"
    written before Antibodies became Primary antibodies still finds it."""
    from . import database_keys
    from . import inventory_service as inventories
    modules = {m.key: m for m in inventories.list_modules(db_session)
               if m.kind != "orders" and m.key not in MENTION_BUILTINS}
    if with_old:
        by_id = {m.id: m for m in modules.values()}
        for old, module_id in database_keys.aliases_by_key(db_session, "inventory").items():
            if module_id in by_id and old not in modules and old not in MENTION_BUILTINS:
                modules[old] = by_id[module_id]
    return modules


def _mention_items(db_session, module: InventoryModule, query: str, limit: int) -> list[InventoryItem]:
    """Records whose name, catalogue number, lot or vendor holds what was
    typed, or one of whose own columns (a chemical's abbreviation or CAS
    number) starts with it; a record number finds that record too. A lot or
    catalogue number that is what was typed comes first, then the record of
    that number."""
    stmt = select(InventoryItem).where(InventoryItem.module_id_fk == module.id)
    if query:
        like = like_pattern(query)
        found = (InventoryItem.name.ilike(like, escape="\\") | InventoryItem.catalog_number.ilike(like, escape="\\")
                 | InventoryItem.lot.ilike(like, escape="\\") | InventoryItem.vendor.ilike(like, escape="\\")
                 | InventoryItem.attrs.ilike(attr_value_pattern(query), escape="\\"))
        exact = (func.lower(InventoryItem.lot) == query.lower()) | (func.lower(InventoryItem.catalog_number) == query.lower())
        number = _record_number(query)
        if number is not None:
            found = found | (InventoryItem.number == number)
        stmt = stmt.where(found).order_by(case((exact, 0), else_=1))
        if number is not None:
            stmt = stmt.order_by(InventoryItem.number != number)
    return list(db_session.scalars(stmt.order_by(InventoryItem.number.desc()).limit(limit)))


def _mention_label(module: InventoryModule, item: InventoryItem, with_noun: bool = False) -> str:
    parts = [f"{module.item_noun.capitalize()} #{item.number}" if with_noun else f"#{item.number}",
             item.name or "(no name)"]
    parts += [v for v in (item.vendor, item.lot and f"lot {item.lot}") if v]
    return " · ".join(parts)


@app.route("/notebook/lookup/<key>/<int:number>")
@login_required
def notebook_lookup_item(key: str, number: int):
    """What the popover on an @<inventory> <n> chip shows."""
    from . import inventory_service as inventories
    with SessionLocal() as db_session:
        module = _mention_modules(db_session, with_old=True).get(key)
        item = module and db_session.scalar(select(InventoryItem).where(
            InventoryItem.module_id_fk == module.id, InventoryItem.number == number))
        if not item:
            return jsonify({"ok": False}), 404
        where = " · ".join(v for v in (inventories.where_label(item), item.location_note) if v)
        amount = " ".join(v for v in (item.quantity, item.unit) if v)
        fields = [("Name", item.name), ("Category", item.category), ("Status", item.status),
                  ("Owner", "Lab common" if item.is_shared else item.owner), ("Vendor", item.vendor),
                  ("Catalog #", item.catalog_number), ("Lot", item.lot), ("Amount", amount), ("Where", where),
                  ("Expires", item.expires_on.isoformat() if item.expires_on else "")]
        # A chemical's abbreviation, CAS number and molecular weight, after its name.
        if module.kind == "chemicals":
            attrs = item.attrs_dict
            fields[1:1] = [(f["label"], str(attrs.get(f["key"]) or "")) for f in inventories.view(module).fields
                           if f["key"] in lab_notebook.CHEMICAL_KEYS]
        return jsonify({"ok": True, "label": _mention_label(module, item, True), "type_label": module.label,
                        "name": item.name or "", "fields": [[k, v] for k, v in fields if v]})


def _order_items_query(db_session, query: str, limit: int, by_number: bool = False):
    """Orders for @order mentions: items of the first orders inventory,
    where an order's number is what @order <n> refers to. `by_number`: that
    order only; otherwise what was typed is looked for in the item, vendor
    and catalogue number too (@order 2947 finds catalogue ab2947)."""
    from . import inventory_service as inventories
    module = inventories.first_of_kind(db_session, "orders")
    if module is None:
        return []
    stmt = select(InventoryItem).where(InventoryItem.module_id_fk == module.id)
    if by_number:
        stmt = stmt.where(InventoryItem.number == int(query))
    elif query:
        like = like_pattern(query)
        found = (InventoryItem.name.ilike(like, escape="\\") | InventoryItem.vendor.ilike(like, escape="\\")
                 | InventoryItem.catalog_number.ilike(like, escape="\\"))
        number = _record_number(query)
        if number is not None:
            stmt = stmt.where(found | (InventoryItem.number == number)).order_by(InventoryItem.number != number)
        else:
            stmt = stmt.where(found)
    return db_session.scalars(stmt.order_by(InventoryItem.number.desc()).limit(limit)).all()


@app.route("/notebook/lookup/order/<int:order_id>")
@login_required
def notebook_lookup_order(order_id: int):
    with SessionLocal() as db_session:
        found = _order_items_query(db_session, str(order_id), 1, by_number=True)
        if not found:
            return jsonify({"ok": False}), 404
        order = found[0]
        label = (
            f"Order #{order.number} · {order.vendor or '(no vendor)'} · "
            f"{order.name or '(no item)'} · cat# {order.catalog_number or '—'} · "
            f"qty {order.quantity or '?'} · {order.status or 'requested'} · "
            f"requested by {order.owner or '—'}"
        )
        return jsonify(
            {
                "ok": True,
                "label": label,
                "order_id": order.number,
                "vendor_name": order.vendor,
                "item_name": order.name,
                "catalog_number": order.catalog_number,
                "quantity": order.quantity,
                "status": order.status,
                "requester_name": order.owner,
            }
        )


@app.route("/notebook/open/<entity_type>/<int:number>")
@login_required
def notebook_open_mention(entity_type: str, number: int):
    """Where an @mention (@mouse 12, @plasmid 4, @order 7) leads: the record
    itself, not its list. The number is the one people see."""
    with SessionLocal() as db_session:
        if entity_type == "mouse":
            mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == number))
            if mouse is not None:
                return redirect(url_for("colony", view="mice", scope="all") + f"#mouse-update-{mouse.id}")
            flash(gettext("There is no mouse #%(number)s.", number=number), "warning")
            return redirect(url_for("colony", view="mice"))
        if entity_type == "plasmid":
            plasmid = db_session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == number))
            if plasmid is not None:
                return redirect(url_for("plasmid_page", number=plasmid.plasmid_id))
            flash(gettext("There is no plasmid #%(number)s.", number=number), "warning")
            return redirect(url_for("plasmids"))
        if entity_type == "order":
            found = _order_items_query(db_session, str(number), 1, by_number=True)
            if found:
                module = db_session.get(InventoryModule, found[0].module_id_fk)
                return redirect(url_for("inventory.module", key=module.key, open=found[0].id))
            flash(gettext("There is no order #%(number)s.", number=number), "warning")
            return redirect(url_for("home_dashboard"))
        module = _mention_modules(db_session, with_old=True).get(entity_type)
        if module is not None:
            item = db_session.scalar(select(InventoryItem).where(
                InventoryItem.module_id_fk == module.id, InventoryItem.number == number))
            if item is not None:
                return redirect(url_for("inventory.module", key=module.key, open=item.id))
            flash(gettext("There is no %(noun)s #%(number)s in %(database)s.", noun=i18n.translate_value(module.item_noun),
                          number=number, database=i18n.translate_value(module.label)), "warning")
            return redirect(url_for("inventory.module", key=module.key))
    abort(404)


@app.route("/notebook/backlinks/<entity_type>/<int:entity_id>")
@login_required
def notebook_backlinks(entity_type: str, entity_id: int):
    """Return notebook pages whose body mentions @<type> <id>.

    Scoped to pages the current user may open (theirs and shared). Returns a list of
    {page_id, page_title, tab_id, tab_title, snippet, updated_at}.
    """
    with SessionLocal() as db_session:
        # An inventory is found by every key it has had: "@antibodies 5" from
        # before a rename is the same record as "@primary_antibodies 5".
        keys = [entity_type]
        if entity_type not in ("mouse", "plasmid", "order"):
            known = _mention_modules(db_session, with_old=True)
            if entity_type not in known:
                return jsonify({"ok": False, "error": "bad type"}), 400
            keys = [k for k, m in known.items() if m.id == known[entity_type].id]
        found = None
        for key in keys:
            clause = NotebookPage.body.ilike(like_pattern(f"@{key} {entity_id}"), escape="\\")
            found = clause if found is None else (found | clause)
        stmt = (
            lab_notebook.accessible_filter(
                select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id))
            .where(found)
            .order_by(NotebookPage.updated_at.desc())
            .limit(25)
        )
        rows = db_session.scalars(stmt).all()
        items = []
        # Use a word-boundary check to avoid `@mouse 12` matching `@mouse 123`.
        import re
        pattern = re.compile(rf"@(?:{'|'.join(re.escape(k) for k in keys)})\s+{entity_id}(?!\d)")
        for page in rows:
            body = page.body or ""
            m = pattern.search(body)
            if not m:
                continue
            start = max(0, m.start() - 40)
            end = min(len(body), m.end() + 60)
            snippet = body[start:end].replace("\n", " ").strip()
            if start > 0:
                snippet = "…" + snippet
            if end < len(body):
                snippet = snippet + "…"
            items.append({
                "page_id": page.id,
                "page_title": page.title or "Untitled page",
                "tab_id": page.tab_id_fk,
                "tab_title": page.tab.title if page.tab else "",
                "snippet": snippet,
                "updated_at": page.updated_at.isoformat(),
            })
        return jsonify({"ok": True, "count": len(items), "items": items})


@app.route("/notebook/search/<entity_type>")
@login_required
def notebook_search_entity(entity_type: str):
    query = (request.args.get("q") or "").strip()
    limit = max(1, min(arg_int("limit", 8), 25))
    with SessionLocal() as db_session:
        if entity_type == "mouse":
            stmt = select(MouseRecord)
            if _record_number(query) is not None:
                stmt = stmt.where(MouseRecord.mouse_id == int(query))
            elif query:
                stmt = stmt.where(MouseRecord.genotype.ilike(like_pattern(query), escape="\\") | MouseRecord.owner.ilike(like_pattern(query), escape="\\")
                                  | MouseRecord.ear_tag.ilike(like_pattern(query), escape="\\")
                                  | MouseRecord.strain.ilike(like_pattern(query), escape="\\"))
            stmt = stmt.order_by(MouseRecord.mouse_id.desc()).limit(limit)
            rows = db_session.scalars(stmt).all()
            return jsonify({"ok": True, "items": [
                {"id": m.mouse_id, "label": f"#{m.mouse_id} · {m.gender or '?'} · {m.genotype or '(no genotype)'}"}
                for m in rows
            ]})
        if entity_type == "plasmid":
            stmt = select(PlasmidRecord)
            if _record_number(query) is not None:
                stmt = stmt.where(PlasmidRecord.plasmid_id == int(query))
            elif query:
                stmt = stmt.where(PlasmidRecord.name.ilike(like_pattern(query), escape="\\") | PlasmidRecord.backbone.ilike(like_pattern(query), escape="\\"))
            stmt = stmt.order_by(PlasmidRecord.plasmid_id.desc()).limit(limit)
            rows = db_session.scalars(stmt).all()
            return jsonify({"ok": True, "items": [
                {"id": p.plasmid_id, "label": f"#{p.plasmid_id} · {p.name or '(no name)'} · {p.backbone or '?'}"}
                for p in rows
            ]})
        if entity_type == "order":
            rows = _order_items_query(db_session, query, limit)
            return jsonify({"ok": True, "items": [
                {"id": o.number, "label": f"#{o.number} · {o.vendor or '?'} · {o.name or '(no item)'} · {o.status or 'requested'}"}
                for o in rows
            ]})
        if entity_type == "all":
            # Unified search across mouse + plasmid + order. Each result includes
            # `type` so the editor can build the right `@<type> <id>` chip.
            per_type_limit = max(2, limit // 3)
            items: list[dict] = []
            number = _record_number(query)
            # People first, for "@jordan" and action items ("- [ ] @jordan …"):
            # a name that starts with what was typed.
            if query and not query.isdigit():
                q = query.lower()
                for person in lab_notebook.people(db_session):
                    names = [person["username"].lower(), *person["name"].lower().split()]
                    if not person["guest"] and any(n.startswith(q) for n in names):
                        items.append({"type": "person", "type_label": gettext("Person"), "id": person["username"],
                                      "label": f"{person['name']} · @{person['username']}"})
                items = items[:3]

            # A database by its name ("@prim" → Primary antibodies): picking
            # it writes "@primary_antibodies " and the menu lists its records.
            if query and _record_number(query) is None:
                q = query.lower()
                for key, module in _mention_modules(db_session).items():
                    if any(w.startswith(q) for w in (key, *module.label.lower().split())):
                        items.append({"type": "database", "type_label": gettext("Database"), "id": key,
                                      "label": f"{module.label} · @{key} <number>"})

            # A lot or catalogue number typed as it is (@0012, @ab2947) is what
            # was meant, before any record that happens to have that number.
            codes = []
            if query:
                for module in _mention_modules(db_session).values():
                    for i in _mention_items(db_session, module, query, per_type_limit):
                        if query.lower() in ((i.lot or "").lower(), (i.catalog_number or "").lower()):
                            codes.append({"type": module.key, "type_label": module.label, "id": i.number,
                                          "label": _mention_label(module, i, True)})
            items += codes

            mouse_stmt = select(MouseRecord)
            if number is not None:
                mouse_stmt = mouse_stmt.where(MouseRecord.mouse_id == number)
            elif query:
                like = like_pattern(query)
                mouse_stmt = mouse_stmt.where(
                    MouseRecord.genotype.ilike(like, escape="\\") | MouseRecord.owner.ilike(like, escape="\\")
                    | MouseRecord.ear_tag.ilike(like, escape="\\")
                    | MouseRecord.strain.ilike(like, escape="\\") | MouseRecord.attrs.ilike(like, escape="\\")
                )
            mouse_stmt = mouse_stmt.order_by(MouseRecord.mouse_id.desc()).limit(per_type_limit)
            for m in db_session.scalars(mouse_stmt).all():
                items.append({
                    "type": "mouse",
                    "id": m.mouse_id,
                    "label": f"Mouse #{m.mouse_id} · {m.gender or '?'} · {m.genotype or '(no genotype)'}",
                })

            plasmid_stmt = select(PlasmidRecord)
            if number is not None:
                plasmid_stmt = plasmid_stmt.where(PlasmidRecord.plasmid_id == number)
            elif query:
                like = like_pattern(query)
                plasmid_stmt = plasmid_stmt.where(
                    PlasmidRecord.name.ilike(like, escape="\\") | PlasmidRecord.backbone.ilike(like, escape="\\")
                )
            plasmid_stmt = plasmid_stmt.order_by(PlasmidRecord.plasmid_id.desc()).limit(per_type_limit)
            for p in db_session.scalars(plasmid_stmt).all():
                items.append({
                    "type": "plasmid",
                    "id": p.plasmid_id,
                    "label": f"Plasmid #{p.plasmid_id} · {p.name or '(no name)'} · {p.backbone or '?'}",
                })

            for o in _order_items_query(db_session, query, per_type_limit):
                items.append({
                    "type": "order",
                    "id": o.number,
                    "label": f"Order #{o.number} · {o.vendor or '?'} · {o.name or '(no item)'}",
                })

            # Every other inventory, most recent first; a bare "@" keeps to
            # the three above so the list stays short.
            if query:
                # Each database's matches, taken in turn, so one with many
                # (six SO-RNA tubes) fills the list when the others have few.
                found = [[{"type": module.key, "type_label": module.label, "id": i.number,
                           "label": _mention_label(module, i, True)}
                          for i in _mention_items(db_session, module, query, limit)]
                         for module in _mention_modules(db_session).values()]
                room = limit - len(items)
                while room > 0 and any(found):
                    for hits in found:
                        if hits and room > 0:
                            hit = hits.pop(0)
                            if hit not in codes:
                                items.append(hit)
                                room -= 1

            return jsonify({"ok": True, "items": items[:limit]})
        module = _mention_modules(db_session, with_old=True).get(entity_type)
        if module is not None:
            return jsonify({"ok": True, "items": [
                {"id": i.number, "label": _mention_label(module, i)}
                for i in _mention_items(db_session, module, query, limit)]})
        return jsonify({"ok": False, "error": f"Unknown entity type: {entity_type}"}), 400
