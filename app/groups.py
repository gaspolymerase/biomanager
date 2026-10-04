"""Project groups: some of the lab's people working on one project.

A group is a layer between a person and the whole lab. Wherever something
can be shared with the lab, it can instead be shared with one group:

  * shared breeder cages, breeding tanks and lab stock vials, lab common
    plasmids and stock: the group's members may edit them (everyone still
    sees them, as for the lab's: a census with holes is not a census);
  * a database: only the group's members (and admins) see it;
  * a to-do: the group's members see it on the calendar and may tick it off;
  * a notebook page: shared with the group to view or to edit;
  * a notebook template: the group's members may start pages from it.

A record says whom it is shared with in two columns: `is_shared` (shared at
all) and `share_group_id` (with that group; empty: the lab). A database has
no `is_shared`: an empty `private_to` with a group is the group's. Notebook
pages are shared with a group by a share whose username is "group:<id>".

Admins make, rename and delete groups and choose their members on the
Project groups page; a group's leads may add and remove its members. A
person may be in several groups. Deleting a group makes what was shared
with it its owner's again (a breeding tank or lab stock vial, shared by its
purpose, the lab's; a group database its creator's own).
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, has_request_context, redirect, render_template, request, url_for
from sqlalchemy import delete, select, update

from . import access
from .db import SessionLocal
from .i18n import gettext
from .models import LabGroup, LabGroupMember, UserAccount

bp = Blueprint("groups", __name__)

# The share username for a group's notebook pages.
PAGE_SHARE_PREFIX = "group:"
NAME_LIMIT = 80


# ---------------------------------------------------------------- who is in which group

def _cache() -> dict:
    """Per request: groups and memberships are read once."""
    if not has_request_context():
        return {}
    if "_groups" not in g:
        with SessionLocal() as s:
            names = {gid: name for gid, name in s.execute(select(LabGroup.id, LabGroup.name)).all()}
            members: dict[int, set[str]] = {gid: set() for gid in names}
            leads: dict[int, set[str]] = {gid: set() for gid in names}
            for gid, who, lead in s.execute(select(LabGroupMember.group_id_fk, LabGroupMember.username,
                                                   LabGroupMember.lead)).all():
                members.setdefault(gid, set()).add(who)
                if lead:
                    leads.setdefault(gid, set()).add(who)
        g._groups = {"names": names, "members": members, "leads": leads}
    return g._groups


def forget() -> None:
    """After a change to groups in this request."""
    if has_request_context():
        g.pop("_groups", None)


def names() -> dict[int, str]:
    """{group id: name}, every group, by name."""
    data = _cache()
    if data:
        return dict(sorted(data["names"].items(), key=lambda kv: kv[1].lower()))
    with SessionLocal() as s:
        return {gid: name for gid, name in s.execute(select(LabGroup.id, LabGroup.name).order_by(LabGroup.name))}


def name_of(group_id) -> str:
    return names().get(group_id or 0, "")


def members_of(group_id) -> set[str]:
    data = _cache()
    if data:
        return set(data["members"].get(group_id or 0, set()))
    with SessionLocal() as s:
        return set(s.scalars(select(LabGroupMember.username).where(LabGroupMember.group_id_fk == group_id)))


def ids_of(user=None) -> frozenset[int]:
    """The groups this person is in."""
    who = access.username(user)
    if not who:
        return frozenset()
    data = _cache()
    if data:
        return frozenset(gid for gid, people in data["members"].items() if who in people)
    with SessionLocal() as s:
        return frozenset(s.scalars(select(LabGroupMember.group_id_fk).where(LabGroupMember.username == who)))


def in_group(group_id, user=None) -> bool:
    return bool(group_id) and group_id in ids_of(user)


def is_lead(group_id, user=None) -> bool:
    data = _cache()
    who = access.username(user)
    if data:
        return who in data["leads"].get(group_id or 0, set())
    with SessionLocal() as s:
        return bool(s.scalar(select(LabGroupMember.id).where(
            LabGroupMember.group_id_fk == group_id, LabGroupMember.username == who, LabGroupMember.lead.is_(True))))


def can_manage(group_id, user=None) -> bool:
    """Add and remove members: an admin or one of the group's leads."""
    return access.is_admin(user) or is_lead(group_id, user)


def colleagues(user=None) -> set[str]:
    """Everyone who shares a group with this person, them included."""
    people = {access.username(user)} - {""}
    for gid in ids_of(user):
        people |= members_of(gid)
    return people


def choices(user=None, current=None) -> list[tuple[int, str]]:
    """The groups this person may share something with: their own (an admin:
    every group), plus the one it is shared with now, so a form never drops
    it silently."""
    every = names()
    mine = every if access.is_admin(user) else {gid: n for gid, n in every.items() if gid in ids_of(user)}
    out = dict(mine)
    if current and current in every:
        out[current] = every[current]
    return sorted(out.items(), key=lambda kv: kv[1].lower())


def may_share_with(group_id, user=None) -> bool:
    return group_id in names() and (access.is_admin(user) or in_group(group_id, user))


# ---------------------------------------------------------------- shared with whom

def shared_with(is_shared, group_id, user=None) -> bool:
    """Does sharing (is_shared, share_group_id) take in this person? The lab
    takes in everyone; a group its members (and admins)."""
    if not is_shared:
        return False
    if not group_id:
        return True
    return access.is_admin(user) or in_group(group_id, user)


def record_shared_with(record, user=None, shared: bool | None = None) -> bool:
    """For a record with is_shared and share_group_id. `shared` overrides
    is_shared when a purpose decides it (a breeding tank, a stock vial)."""
    is_shared = getattr(record, "is_shared", False) if shared is None else shared
    return shared_with(is_shared, getattr(record, "share_group_id", None), user)


def value_of(is_shared, group_id) -> str:
    """The sharing a form shows: "0" personal, "1" the lab, "g<id>" a group."""
    if not is_shared:
        return "0"
    return f"g{group_id}" if group_id else "1"


def record_value(record) -> str:
    return value_of(getattr(record, "is_shared", False), getattr(record, "share_group_id", None))


def parse(raw) -> tuple[bool, int | None]:
    """A form's sharing value: (is_shared, group id). "1", "on", "true",
    "lab" or "shared": the lab; "g<id>" or "group:<id>": that group;
    anything else: personal."""
    text = str(raw if raw is not None else "").strip().lower()
    for prefix in ("group:", "g"):
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            return True, int(text[len(prefix):])
    return text in ("1", "on", "true", "yes", "lab", "shared"), None


def differs(record, raw, shared=None) -> bool:
    """Would saving `raw` change whom the record is shared with?"""
    now_shared = bool(getattr(record, "is_shared", False) if shared is None else shared)
    now = (now_shared, getattr(record, "share_group_id", None) if now_shared else None)
    want_shared, want_group = parse(raw)
    return (want_shared, want_group if want_shared else None) != now


def apply(record, raw, user=None, set_shared: bool = True) -> str | None:
    """Share the record as `raw` says ("0", "1", "g<id>"), or say why not:
    only with a group the person is in (an admin: any). With set_shared off
    (a tank or vial, shared by its purpose) only the group is set."""
    shared, group_id = parse(raw)
    if group_id is not None and group_id != getattr(record, "share_group_id", None) \
            and not may_share_with(group_id, user):
        return refusal(group_id)
    if set_shared:
        record.is_shared = shared
    record.share_group_id = group_id if shared else None
    return None


def label(is_shared, group_id, personal: str = "Personal", lab: str = "Shared") -> str:
    if not is_shared:
        return personal
    if group_id:
        return name_of(group_id) or "A deleted group"
    return lab


def refusal(group_id) -> str:
    return gettext("You can share only with a project group you are in.") if group_id in names() \
        else gettext("That project group doesn't exist any more.")


# ---------------------------------------------------------------- databases

def database_value(module) -> str:
    """A database's audience: "me", "lab" or "group:<id>"."""
    if getattr(module, "private_to", ""):
        return "me"
    gid = getattr(module, "share_group_id", None)
    return f"{PAGE_SHARE_PREFIX}{gid}" if gid else "lab"


def database_group(module) -> int | None:
    return None if getattr(module, "private_to", "") else getattr(module, "share_group_id", None)


def can_see_database(module, user=None) -> bool:
    gid = database_group(module)
    return not gid or access.is_admin(user) or in_group(gid, user)


def in_my_databases(module, user=None) -> bool:
    """A group's database is in its members' sidebar (not every admin's)."""
    gid = database_group(module)
    return not gid or in_group(gid, user)


# ---------------------------------------------------------------- notebook pages

def page_share_names(user=None) -> list[str]:
    """The share usernames that take in this person: their groups'."""
    return [f"{PAGE_SHARE_PREFIX}{gid}" for gid in sorted(ids_of(user))]


def page_share_group(share_username: str) -> int | None:
    text = share_username or ""
    if text.startswith(PAGE_SHARE_PREFIX) and text[len(PAGE_SHARE_PREFIX):].isdigit():
        return int(text[len(PAGE_SHARE_PREFIX):])
    return None


# ---------------------------------------------------------------- deleting a group

# Tables whose records can be shared with a group, and what a record becomes
# when its group goes: its owner's own.
_SHARED_RECORDS = ("CageRecord", "PlasmidRecord", "InventoryItem", "TankRecord", "StockUnit", "TaskItem")


def release(s, group_id: int) -> dict[str, int]:
    """Make everything shared with this group its owner's again."""
    from . import models
    from .models import NotebookShare, NotebookTemplate
    counts: dict[str, int] = {}
    for name in _SHARED_RECORDS:
        model = getattr(models, name)
        values = {"share_group_id": None}
        if hasattr(model, "is_shared") and name not in ("TankRecord", "StockUnit"):
            values["is_shared"] = False
        counts[model.__tablename__] = s.execute(
            update(model).where(model.share_group_id == group_id).values(**values)).rowcount or 0
    counts["notebook_templates"] = s.execute(update(NotebookTemplate).where(
        NotebookTemplate.share_group_id == group_id).values(share_group_id=None, lab=False)).rowcount or 0
    for name in ("OrganismModule", "StockModule", "InventoryModule"):
        model = getattr(models, name)
        n = 0
        for module in s.scalars(select(model).where(model.share_group_id == group_id)):
            module.share_group_id = None
            if not module.private_to:
                module.private_to = module.created_by or ""
            n += 1
        counts[model.__tablename__] = n
    counts["notebook_shares"] = s.execute(delete(NotebookShare).where(
        NotebookShare.username == f"{PAGE_SHARE_PREFIX}{group_id}")).rowcount or 0
    return counts


# ---------------------------------------------------------------- the page

def _people(s) -> list[UserAccount]:
    return list(s.scalars(select(UserAccount).where(
        UserAccount.disabled.is_(False), UserAccount.role != "pending").order_by(UserAccount.username)))


def _group_or_404(s, group_id: int) -> LabGroup:
    group = s.get(LabGroup, group_id)
    if group is None:
        abort(404)
    return group


def _clean_name(raw) -> str:
    return " ".join(str(raw or "").split())[:NAME_LIMIT]


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


@bp.route("/groups")
def page():
    with SessionLocal() as s:
        rows = list(s.scalars(select(LabGroup).order_by(LabGroup.name)))
        for group in rows:
            _ = group.members
        people = _people(s)
        shown = {u.username: u.display_name or u.username for u in people}
        return render_template("groups/index.html", groups=rows, people=people, shown=shown,
                               mine=ids_of(), is_admin=access.is_admin(),
                               can_manage={grp.id: can_manage(grp.id) for grp in rows})


@bp.route("/groups/create", methods=["POST"])
def create():
    if not access.is_admin():
        abort(403)
    name = _clean_name(request.form.get("name"))
    if not name:
        flash(gettext("Give the group a name."), "error")
        return redirect(url_for("groups.page"))
    with SessionLocal() as s:
        if s.scalar(select(LabGroup.id).where(LabGroup.name == name)):
            flash(gettext("There is already a group called %(name)s.", name=name), "error")
            return redirect(url_for("groups.page"))
        group = LabGroup(name=name, description=(request.form.get("description") or "").strip()[:500],
                         created_by=access.username())
        s.add(group)
        s.flush()
        for who in request.form.getlist("members"):
            if s.scalar(select(UserAccount.id).where(UserAccount.username == who)):
                s.add(LabGroupMember(group_id_fk=group.id, username=who))
        s.commit()
        gid = group.id
    forget()
    flash(gettext("Made the project group %(name)s.", name=name), "success")
    return redirect(url_for("groups.page", _anchor=f"group-{gid}"))


@bp.route("/groups/<int:group_id>/rename", methods=["POST"])
def rename(group_id: int):
    if not access.is_admin():
        abort(403)
    name = _clean_name(request.form.get("name"))
    with SessionLocal() as s:
        group = _group_or_404(s, group_id)
        if not name:
            flash(gettext("Give the group a name."), "error")
        elif s.scalar(select(LabGroup.id).where(LabGroup.name == name, LabGroup.id != group_id)):
            flash(gettext("There is already a group called %(name)s.", name=name), "error")
        else:
            group.name = name
            if "description" in request.form:
                group.description = (request.form.get("description") or "").strip()[:500]
            s.commit()
            flash(gettext("Saved."), "success")
    forget()
    return redirect(url_for("groups.page", _anchor=f"group-{group_id}"))


@bp.route("/groups/<int:group_id>/delete", methods=["POST"])
def remove(group_id: int):
    if not access.is_admin():
        abort(403)
    with SessionLocal() as s:
        group = _group_or_404(s, group_id)
        name = group.name
        release(s, group_id)
        s.delete(group)
        s.commit()
    forget()
    flash(gettext("Deleted the project group %(name)s. What was shared with it is its owner's own again.", name=name),
          "success")
    return redirect(url_for("groups.page"))


@bp.route("/groups/<int:group_id>/members", methods=["POST"])
def add_member(group_id: int):
    if not can_manage(group_id):
        abort(403)
    who = (request.form.get("username") or "").strip()
    with SessionLocal() as s:
        _group_or_404(s, group_id)
        if not s.scalar(select(UserAccount.id).where(UserAccount.username == who)):
            flash(gettext("Pick someone in the lab."), "error")
        elif s.scalar(select(LabGroupMember.id).where(LabGroupMember.group_id_fk == group_id,
                                                     LabGroupMember.username == who)):
            flash(gettext("%(who)s is already in the group.", who=who), "info")
        else:
            s.add(LabGroupMember(group_id_fk=group_id, username=who,
                                 lead=bool(request.form.get("lead")) and access.is_admin()))
            s.commit()
            flash(gettext("Added %(who)s.", who=who), "success")
    forget()
    return redirect(url_for("groups.page", _anchor=f"group-{group_id}"))


@bp.route("/groups/<int:group_id>/members/<path:who>/remove", methods=["POST"])
def remove_member(group_id: int, who: str):
    if not can_manage(group_id):
        abort(403)
    with SessionLocal() as s:
        _group_or_404(s, group_id)
        row = s.scalar(select(LabGroupMember).where(LabGroupMember.group_id_fk == group_id,
                                                    LabGroupMember.username == who))
        if row is not None:
            if row.lead and not access.is_admin():
                flash(gettext("Only an admin can take a lead out of the group."), "error")
            else:
                s.delete(row)
                s.commit()
                flash(gettext("Took %(who)s out of the group.", who=who), "success")
    forget()
    return redirect(url_for("groups.page", _anchor=f"group-{group_id}"))


@bp.route("/groups/<int:group_id>/members/<path:who>/lead", methods=["POST"])
def set_lead(group_id: int, who: str):
    if not access.is_admin():
        abort(403)
    with SessionLocal() as s:
        row = s.scalar(select(LabGroupMember).where(LabGroupMember.group_id_fk == group_id,
                                                    LabGroupMember.username == who))
        if row is None:
            abort(404)
        row.lead = request.form.get("lead") == "1"
        s.commit()
    forget()
    return redirect(url_for("groups.page", _anchor=f"group-{group_id}"))


@bp.app_context_processor
def inject():
    """For the sharing pickers in every template."""
    return {"groups_api": _TemplateApi}


class _TemplateApi:
    choices = staticmethod(choices)
    value = staticmethod(record_value)
    value_of = staticmethod(value_of)
    label = staticmethod(label)
    name_of = staticmethod(name_of)
    names = staticmethod(names)
    mine = staticmethod(ids_of)
