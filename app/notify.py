"""In-app notifications: something happened that someone should know about.

What tells whom (the category decides the Settings switch that silences it):

  transfer    an animal, cage, tank or vial of yours was moved or given away
              by someone else; one was given to you; a mouse was put in your cage
  picked      someone took a mouse of yours from a breeder cage
  genotyping  a genotype was recorded for your animal, or it was marked for
              genotyping, by someone else; once a day, what of yours waits
  orders      an order you placed was ordered, received or cancelled; for
              admins, a new order request (so nobody has to look for them)
  lab         a database or function was added or switched on for the lab
  notebook    pages shared with you, comments, and @you in a page or in the
              notes of any record (a plasmid "for @rowan", an order's note)
  account     sign-ups waiting for approval (admins)

Changes are noticed where the change history notices them, at the flush,
so every route and every bulk edit is covered without a call in each.
Notifications are made at commit: none for a change that is rolled back,
none to the person who made it, and one per person per kind of change,
so moving twenty mice sends one message listing them, not twenty.

Every notification is written in its recipient's language (app/i18n.py):
the texts here are English templates with %(name)s places, translated and
filled in for each recipient when it is made.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime

from flask import g, has_request_context, url_for
from sqlalchemy import event, func, inspect as sa_inspect, select
from sqlalchemy.orm import Session

import re

from .i18n import gettext, ngettext
from .models import (CageRecord, FishRecord, InventoryItem, InventoryModule, MouseRecord, NotificationRecord,
                     OrgGenotype, OrgHousing, Organism, OrganismModule, PlasmidRecord, StockModule, StockUnit,
                     TankRecord, UserAccount)

CATEGORIES = {
    "transfer": ("Transfers", "Your animals, cages, tanks or vials moved or given by someone else"),
    "picked": ("Picked from breeders", "Someone took one of your mice from a breeder cage"),
    "genotyping": ("Genotyping", "Genotypes recorded or requested for your animals, and what is waiting"),
    "orders": ("Orders", "Your orders placed, received or cancelled; for admins, new requests"),
    "lab": ("Lab news", "Databases or functions added for the lab"),
    "notebook": ("Notebook and @mentions", "Pages shared with you, comments and @mentions (in pages and in records' notes), "
                             "meeting notes and action items"),
    "experiments": ("Experiments", "Once a day: manipulations and readouts due in your experiments"),
}
MAX_LISTED = 5


def _actor() -> str | None:
    if not has_request_context():
        return None
    user = g.get("user")
    return user.username if user is not None else None


def _change(obj, attr: str):
    """(before, after) if `attr` changed in this flush, else None."""
    history = sa_inspect(obj).attrs[attr].history
    if not history.has_changes():
        return None
    before = history.deleted[0] if history.deleted else None
    after = history.added[0] if history.added else None
    if before == after:
        return None
    return before, after


def _note(recipient, category, group, item, one, many, link="", values=None, **extra):
    """A thing to tell `recipient`. Notes with the same recipient, category
    and group become one notification: `one` for a single item (its label
    is %(label)s), `many` (with %(n)s and %(items)s) for several. Both are
    English templates filled from `values`; `item` is a label made by _label
    (or someone's own text), written in the recipient's language later."""
    return {"recipient": recipient, "category": category, "group": group, "item": item,
            "one": one, "many": many, "link": link, "values": dict(values or {}), **extra}


def _label(template: str, **values) -> tuple:
    """A record's label to translate later ("mouse #%(id)s"): hashable, so
    the same item is listed once."""
    return (template, tuple(sorted(values.items())))


def _render(item) -> str:
    """A label in the language being written (see _label)."""
    if isinstance(item, tuple):
        return gettext(item[0], **dict(item[1]))
    return str(item)


# ---------------------------------------------------------------- what each kind of record notices

def _mouse(obj: MouseRecord, actor: str) -> list[dict]:
    notes = []
    label = _label("mouse #%(id)s", id=obj.mouse_id)
    link = ("mouse", obj.id)
    who = {"actor": actor}
    owner = _change(obj, "owner")
    if owner:
        before, after = owner
        if after and after != actor:
            notes.append(_note(after, "transfer", ("given", actor), label,
                               "%(actor)s gave you %(label)s", "%(actor)s gave you %(n)s mice: %(items)s", link, who))
        if before and before != actor:
            if after == actor:
                notes.append(_note(before, "picked", ("taken", actor), label,
                                   "%(actor)s took %(label)s from you", "%(actor)s took %(n)s of your mice: %(items)s",
                                   link, who))
            elif not after:
                notes.append(_note(before, "transfer", ("unowned", actor), label,
                                   "%(actor)s removed you as the owner of %(label)s",
                                   "%(actor)s removed you as the owner of %(n)s mice: %(items)s", link, who))
            else:
                notes.append(_note(before, "transfer", ("given-away", actor, after), label,
                                   "%(actor)s gave your %(label)s to %(to)s",
                                   "%(actor)s gave %(n)s of your mice to %(to)s: %(items)s", link,
                                   {"actor": actor, "to": after}))
    cage = _change(obj, "cage_id_fk")
    current_owner = obj.owner or ""
    if cage and cage[1]:
        if current_owner and current_owner != actor:
            notes.append(_note(current_owner, "transfer", ("moved", actor, cage[1]), label,
                               "%(actor)s moved your %(label)s to cage %(cage)s",
                               "%(actor)s moved %(n)s of your mice to cage %(cage)s: %(items)s", link, who,
                               cage_id=cage[1]))
        notes.append(_note(None, "transfer", ("into-cage", actor, cage[1]), label,
                           "%(actor)s moved %(label)s into your cage %(cage)s",
                           "%(actor)s moved %(n)s mice into your cage %(cage)s: %(items)s", link, who,
                           cage_owner_of=cage[1], skip=(actor, current_owner)))
    if current_owner and current_owner != actor:
        typed = [_change(obj, f) for f in ("genotype", "transgene_1", "transgene_2", "transgene_3", "transgene_4")]
        if any(typed) and (obj.genotype or "").strip():
            genotype = obj.genotype.strip()
            notes.append(_note(current_owner, "genotyping", ("genotyped", actor),
                               _label("mouse #%(id)s (%(genotype)s)", id=obj.mouse_id, genotype=genotype),
                               "%(actor)s recorded the genotype of mouse #%(id)s: %(genotype)s",
                               "%(actor)s recorded genotypes for %(n)s of your mice: %(items)s", link,
                               {"actor": actor, "id": obj.mouse_id, "genotype": genotype}))
        status = _change(obj, "status")
        if status and status[1] == "geno":
            notes.append(_note(current_owner, "genotyping", ("to-genotype", actor), label,
                               "%(actor)s marked your %(label)s for genotyping",
                               "%(actor)s marked %(n)s of your mice for genotyping: %(items)s", link, who))
    return notes


def _cage(obj: CageRecord, actor: str) -> list[dict]:
    owner = _change(obj, "owner")
    if owner and owner[1] and owner[1] != actor:
        return [_note(owner[1], "transfer", ("cage-given", actor), _label("cage %(cage)s", cage=obj.cage_id),
                      "%(actor)s gave you %(label)s", "%(actor)s gave you %(n)s cages: %(items)s", ("cage", obj.id),
                      {"actor": actor})]
    return []


def _tank(obj: TankRecord, actor: str) -> list[dict]:
    notes = []
    label = _label("tank %(tank)s", tank=obj.tank_id)
    who = {"actor": actor}
    owner = _change(obj, "owner")
    if owner and owner[1] and owner[1] != actor:
        notes.append(_note(owner[1], "transfer", ("tank-given", actor), label, "%(actor)s gave you %(label)s",
                           "%(actor)s gave you %(n)s tanks: %(items)s", ("tank", obj.id), who))
    flag = _change(obj, "needs_genotyping")
    if flag and obj.owner and obj.owner != actor:
        if flag[1]:
            notes.append(_note(obj.owner, "genotyping", ("tank-geno", actor), label,
                               "%(actor)s flagged your %(label)s for genotyping",
                               "%(actor)s flagged %(n)s of your tanks for genotyping: %(items)s", ("tank", obj.id),
                               who))
        else:
            notes.append(_note(obj.owner, "genotyping", ("tank-geno-done", actor), label,
                               "%(actor)s finished genotyping your %(label)s",
                               "%(actor)s finished genotyping %(n)s of your tanks: %(items)s", ("tank", obj.id), who))
    return notes


def _fish(obj: FishRecord, actor: str) -> list[dict]:
    tank = _change(obj, "tank_id_fk")
    if not tank:
        return []
    label = (_label("fish %(fish)s", fish=obj.individual_id) if obj.individual_id
             else _label("fish group of %(count)s", count=obj.count or 1))
    who = {"actor": actor}
    notes = []
    if tank[1]:
        notes.append(_note(None, "transfer", ("fish-in", actor, tank[1]), label,
                           "%(actor)s moved %(label)s into your tank %(tank)s",
                           "%(actor)s moved %(n)s fish into your tank %(tank)s: %(items)s", ("tank", tank[1]), who,
                           tank_owner_of=tank[1], skip=(actor,)))
    if tank[0]:
        notes.append(_note(None, "transfer", ("fish-out", actor, tank[0]), label,
                           "%(actor)s moved %(label)s out of your tank %(tank)s",
                           "%(actor)s moved %(n)s fish out of your tank %(tank)s: %(items)s", ("tank", tank[0]), who,
                           tank_owner_of=tank[0], skip=(actor,)))
    return notes


def _organism(obj: Organism, actor: str) -> list[dict]:
    notes = []
    label = obj.code or f"#{obj.id}"
    link = ("organism", obj.module_id_fk)
    who = {"actor": actor}
    owner = _change(obj, "owner")
    if owner:
        before, after = owner
        if after and after != actor:
            notes.append(_note(after, "transfer", ("org-given", actor, obj.module_id_fk), label,
                               "%(actor)s gave you %(label)s", "%(actor)s gave you %(n)s animals: %(items)s", link,
                               who))
        if before and before != actor and before != after:
            notes.append(_note(before, "transfer", ("org-given-away", actor, obj.module_id_fk), label,
                               "%(actor)s gave your %(label)s to %(to)s" if after
                               else "%(actor)s removed you as the owner of %(label)s",
                               "%(actor)s gave %(n)s of your animals away: %(items)s", link,
                               {"actor": actor, "to": after or ""}))
    housing = _change(obj, "housing_id_fk")
    if housing and obj.owner and obj.owner != actor:
        notes.append(_note(obj.owner, "transfer", ("org-moved", actor, obj.module_id_fk), label,
                           "%(actor)s moved your %(label)s", "%(actor)s moved %(n)s of your animals: %(items)s", link,
                           who))
    if obj.owner and obj.owner != actor and _change(obj, "genotype") and (obj.genotype or "").strip():
        genotype = obj.genotype.strip()
        notes.append(_note(obj.owner, "genotyping", ("org-genotyped", actor, obj.module_id_fk),
                           f"{label} ({genotype})",
                           "%(actor)s recorded the genotype of your %(code)s: %(genotype)s",
                           "%(actor)s recorded genotypes for %(n)s of your animals: %(items)s", link,
                           {"actor": actor, "code": label, "genotype": genotype}))
    return notes


def _housing(obj: OrgHousing, actor: str) -> list[dict]:
    owner = _change(obj, "owner")
    if owner and owner[1] and owner[1] != actor:
        label = obj.code or f"#{obj.id}"
        return [_note(owner[1], "transfer", ("housing-given", actor, obj.module_id_fk), label,
                      "%(actor)s gave you %(label)s", "%(actor)s gave you %(n)s: %(items)s",
                      ("organism", obj.module_id_fk), {"actor": actor})]
    return []


def _unit(obj: StockUnit, actor: str) -> list[dict]:
    owner = _change(obj, "owner")
    if owner and owner[1] and owner[1] != actor:
        label = f"#{obj.number}"
        return [_note(owner[1], "transfer", ("unit-given", actor, obj.module_id_fk), label,
                      "%(actor)s gave you %(label)s", "%(actor)s gave you %(n)s: %(items)s", ("stock", obj.module_id_fk),
                      {"actor": actor}, stock_module=obj.module_id_fk)]
    return []


ORDER_WORDS = {"ordered": "ordered", "received": "received", "cancelled": "cancelled"}
# What an order's owner is told, for one order and for several.
ORDER_TEMPLATES = {
    "ordered": ("Your order %(label)s was ordered by %(actor)s",
                "%(n)s of your orders were ordered by %(actor)s: %(items)s"),
    "received": ("Your order %(label)s was received by %(actor)s",
                 "%(n)s of your orders were received by %(actor)s: %(items)s"),
    "cancelled": ("Your order %(label)s was cancelled by %(actor)s",
                  "%(n)s of your orders were cancelled by %(actor)s: %(items)s"),
}


def _item(obj: InventoryItem, actor: str) -> list[dict]:
    status = _change(obj, "status")
    if not status or not obj.owner or obj.owner == actor:
        return []
    word = ORDER_WORDS.get((status[1] or "").lower())
    if not word:
        return []
    label = obj.name or f"#{obj.number}"
    one, many = ORDER_TEMPLATES[word]
    return [_note(obj.owner, "orders", ("order", actor, word, obj.module_id_fk), label, one, many,
                  ("inventory", obj.module_id_fk), {"actor": actor},
                  orders_module=obj.module_id_fk, one_link=("inventory-item", obj.id))]


DIRTY_RULES = {MouseRecord: _mouse, CageRecord: _cage, TankRecord: _tank, FishRecord: _fish,
               Organism: _organism, OrgHousing: _housing, StockUnit: _unit, InventoryItem: _item}


def _genotype_call(obj: OrgGenotype, actor: str) -> list[dict]:
    item = f"{obj.assay}: {obj.result}" if obj.assay else _label("genotype: %(result)s", result=obj.result)
    return [_note(None, "genotyping", ("org-call", actor, obj.module_id_fk), item,
                  "%(actor)s recorded a genotype for your %(subject)s: %(call)s",
                  "%(actor)s recorded %(n)s genotypes for your animals: %(items)s", ("organism", obj.module_id_fk),
                  {"actor": actor, "call": f"{obj.assay or ''} {obj.result}".strip()},
                  subject=(obj.subject_kind, obj.subject_id), skip=(actor,))]


MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_.-]{0,79})")
# Where a record keeps its notes, and how a mention in them links back.
NOTE_FIELDS = {InventoryItem: ("notes", lambda o: ("inventory-item", o.id), lambda o: o.name or f"#{o.number}"),
               PlasmidRecord: ("notes", lambda o: ("plasmid", o.id),
                               lambda o: o.name or _label("plasmid #%(id)s", id=o.plasmid_id)),
               MouseRecord: ("note", lambda o: ("mouse", o.id), lambda o: _label("mouse #%(id)s", id=o.mouse_id)),
               CageRecord: ("notes", lambda o: ("cage", o.id), lambda o: _label("cage %(cage)s", cage=o.cage_id)),
               TankRecord: ("notes", lambda o: ("tank", o.id), lambda o: _label("tank %(tank)s", tank=o.tank_id)),
               StockUnit: ("notes", lambda o: ("stock", o.module_id_fk), lambda o: f"#{o.number}"),
               Organism: ("notes", lambda o: ("organism", o.module_id_fk), lambda o: o.code or f"#{o.id}"),
               OrgHousing: ("notes", lambda o: ("organism", o.module_id_fk), lambda o: o.code or f"#{o.id}")}


def mentioned(text: str | None) -> set[str]:
    return {m.group(1).rstrip(".") for m in MENTION_RE.finditer(text or "")}


def _mentions(obj, actor: str, new: bool) -> list[dict]:
    """@someone written into a record's notes: they are told, once (only
    names that weren't there before count)."""
    field, target, label = NOTE_FIELDS[type(obj)]
    if new:
        before, after = "", getattr(obj, field, "") or ""
    else:
        change = _change(obj, field)
        if not change:
            return []
        before, after = change[0] or "", change[1] or ""
    names = mentioned(after) - mentioned(before) - {actor}
    if not names:
        return []
    # A new record has no id before its INSERT; its link is resolved at commit.
    return [_note(name, "notebook", ("mention", actor, type(obj).__name__, id(obj)), label(obj),
                  "%(actor)s mentioned you on %(label)s", "%(actor)s mentioned you on %(n)s records: %(items)s",
                  "", {"actor": actor}, mention_obj=obj, mention_target=target, mention_text=after[:300])
            for name in names]


def _order_request(session, obj: InventoryItem, actor: str) -> list[dict]:
    """A new order request: every admin who hasn't switched order notices
    off is told (one message listing several at once)."""
    with session.no_autoflush:
        module = session.get(InventoryModule, obj.module_id_fk) if obj.module_id_fk else None
        if module is None or module.kind != "orders":
            return []
        admins = session.scalars(select(UserAccount.username).where(
            UserAccount.role == "admin", UserAccount.disabled.is_(False), UserAccount.username != actor)).all()
    label = obj.name or _label("an order")
    return [_note(admin, "orders", ("order-request", actor, obj.module_id_fk), label,
                  "%(actor)s asked for %(label)s", "%(actor)s asked for %(n)s orders: %(items)s",
                  ("inventory", obj.module_id_fk), {"actor": actor}, orders_module=obj.module_id_fk)
            for admin in admins]


@event.listens_for(Session, "before_flush")
def _notice(session, flush_context, instances):
    actor = _actor()
    if not actor:
        return
    notes = session.info.setdefault("notify_notes", [])
    for obj in list(session.dirty):
        rule = DIRTY_RULES.get(type(obj))
        if rule and session.is_modified(obj, include_collections=False):
            notes.extend(rule(obj, actor))
        if type(obj) in NOTE_FIELDS and session.is_modified(obj, include_collections=False):
            notes.extend(_mentions(obj, actor, new=False))
    for obj in list(session.new):
        if isinstance(obj, OrgGenotype):
            notes.extend(_genotype_call(obj, actor))
        if type(obj) in NOTE_FIELDS:
            notes.extend(_mentions(obj, actor, new=True))
        if isinstance(obj, InventoryItem):
            notes.extend(_order_request(session, obj, actor))


@event.listens_for(Session, "after_rollback")
def _forget(session):
    session.info.pop("notify_notes", None)


@event.listens_for(Session, "before_commit")
def _deliver(session):
    notes = session.info.pop("notify_notes", None)
    if not notes:
        return
    deliver(session, notes)


# ---------------------------------------------------------------- turning notes into notifications

def _resolve(session, note: dict) -> dict | None:
    """Fill in what the note could not know at flush time: whose cage or
    tank it is, codes, whether an inventory is an orders one."""
    if "cage_owner_of" in note:
        cage = session.get(CageRecord, note["cage_owner_of"])
        if cage is None or not cage.owner or cage.owner in note["skip"]:
            return None
        note["recipient"] = cage.owner
    if "cage_id" in note or "cage_owner_of" in note:
        cage = session.get(CageRecord, note.get("cage_id") or note.get("cage_owner_of"))
        note["values"]["cage"] = cage.cage_id if cage else "?"
    if "tank_owner_of" in note:
        tank = session.get(TankRecord, note["tank_owner_of"])
        if tank is None or not tank.owner or tank.owner in note["skip"]:
            return None
        note["recipient"] = tank.owner
        note["values"]["tank"] = tank.tank_id
    if "orders_module" in note:
        module = session.get(InventoryModule, note["orders_module"])
        if module is None or module.kind != "orders":
            return None
    if "mention_obj" in note:
        obj = note.pop("mention_obj")
        target = note.pop("mention_target")(obj)
        note["one_link"] = note["link"] = target
        # Still in the text as it was saved (a mention typed and taken out
        # again in the same save isn't one).
        if note["recipient"] not in mentioned(note.pop("mention_text")):
            return None
        exists = session.scalar(select(UserAccount.id).where(UserAccount.username == note["recipient"]))
        if exists is None:
            return None
    if "subject" in note:
        kind, subject_id = note["subject"]
        model = {"organism": Organism, "housing": OrgHousing}.get(kind)
        subject = session.get(model, subject_id) if model else None
        if subject is None or not subject.owner or subject.owner in note["skip"]:
            return None
        note["recipient"] = subject.owner
        note["values"]["subject"] = subject.code or f"#{subject.id}"
    return note if note.get("recipient") else None


def _link(session, target) -> str:
    kind, ident = target
    try:
        if kind == "mouse":
            return url_for("colony", view="mice", scope="all") + f"#mouse-update-{ident}"
        if kind == "cage":
            return url_for("colony", view="cages", scope="all") + f"#cage-{ident}"
        if kind == "tank":
            return url_for("zebrafish") + f"#tank-{ident}"
        if kind == "organism":
            module = session.get(OrganismModule, ident)
            return url_for("organisms.module", key=module.key) if module else ""
        if kind == "stock":
            module = session.get(StockModule, ident)
            return url_for("stocks.module", key=module.key) if module else ""
        if kind == "inventory":
            module = session.get(InventoryModule, ident)
            return url_for("inventory.module", key=module.key) if module else ""
        if kind == "plasmid":
            plasmid = session.get(PlasmidRecord, ident)
            return url_for("plasmid_page", number=plasmid.plasmid_id) if plasmid else ""
        if kind == "inventory-item":
            # The order itself, opened in its dialog (?open=, inventory_routes).
            item = session.get(InventoryItem, ident)
            module = session.get(InventoryModule, item.module_id_fk) if item else None
            return url_for("inventory.module", key=module.key, open=item.id) if module else ""
    except Exception:  # noqa: BLE001 — a link is a convenience; the message still goes
        return ""
    return ""


def deliver(session, notes: list[dict]) -> int:
    from . import i18n
    groups: "OrderedDict[tuple, list[dict]]" = OrderedDict()
    for note in notes:
        note = _resolve(session, dict(note, values=dict(note.get("values") or {})))
        if note is None:
            continue
        key = (note["recipient"], note["category"], note["group"])
        groups.setdefault(key, [])
        if note["item"] not in {n["item"] for n in groups[key]}:
            groups[key].append(note)
    sent = 0
    for (recipient, category, _group), items in groups.items():
        first = items[0]
        # Written in the recipient's language: the labels too.
        with i18n.using(i18n.language_for(session, recipient)):
            if len(items) == 1:
                title = i18n.gettext(first["one"], **first["values"], label=_render(first["item"]))
            else:
                listed = ", ".join(_render(n["item"]) for n in items[:MAX_LISTED])
                if len(items) > MAX_LISTED:
                    listed = gettext("%(names)s and %(n)s more", names=listed, n=len(items) - MAX_LISTED)
                title = i18n.gettext(first["many"], **first["values"], n=len(items), items=listed)
        # One item links to that item where there is a page for it; several
        # to the list they are in.
        target = first.get("one_link") if len(items) == 1 and first.get("one_link") else first.get("link")
        link = _link(session, target) if target else ""
        actor = first["group"][1] if len(first["group"]) > 1 else ""
        if send(session, recipient, title, category=category, link=link, actor=actor):
            sent += 1
    return sent


def send(session, recipient: str, title: str, message: str = "", category: str = "general",
         link: str = "", actor: str = "", values: dict | None = None,
         message_values: dict | None = None) -> bool:
    """One notification, if the recipient exists, is active, wants this
    category, and is not the person who caused it.

    Written in the recipient's language (app/i18n.py language_for): `title`
    is an English text with `%(name)s` places filled from `values`, looked up
    in the catalogs; `message` is translated the same way only when
    `message_values` is given (`{}` for a fixed text), and otherwise kept as
    it is (what someone typed). A title passed without `values` is still
    translated when it has an entry."""
    if not recipient or recipient == actor:
        return False
    user = session.scalar(select(UserAccount).where(UserAccount.username == recipient))
    if user is None or user.disabled:
        return False
    if getattr(user, f"notify_{category}", True) is False:
        return False
    from . import i18n
    with i18n.using(i18n.language_for(session, recipient)):
        title = i18n.gettext(title, **(values or {}))
        if message_values is not None:
            message = i18n.gettext(message, **message_values)
    session.add(NotificationRecord(recipient_username=recipient, title=title[:200], message=message,
                                   category=category, link=link[:300], actor=actor))
    return True


def tell_lab(session, actor: str, title: str, message: str = "", link: str = "",
             values: dict | None = None, message_values: dict | None = None) -> int:
    """A "lab" notification for every active member but the actor, each in
    their own language (see send)."""
    from . import lab
    return sum(send(session, name, title, message, category="lab", link=link, actor=actor,
                    values=values, message_values=message_values)
               for name in lab.everyone_but(session, actor))


def tell_group(session, group_id: int, actor: str, title: str, message: str = "", link: str = "",
               values: dict | None = None, message_values: dict | None = None) -> int:
    """A "lab" notification for a project group's members but the actor."""
    from . import groups, lab
    members = groups.members_of(group_id)
    return sum(send(session, name, title, message, category="lab", link=link, actor=actor,
                    values=values, message_values=message_values)
               for name in lab.everyone_but(session, actor) if name in members)


# ---------------------------------------------------------------- reading

SIGNUP_TITLE = "Account waiting for approval"
# Its message, as an English template (send it with message_values; each
# admin reads it in their language). settle_signups finds the username in it.
SIGNUP_MESSAGE = "%(who)s signed up as %(username)s. Approve them in Settings → Manage users."
SIGNUP_WITH_PROVIDER = "%(who)s asked to join with %(provider)s as %(username)s. Approve them in Settings → Manage users."
_SIGNUP_NAME = re.compile(r"\bas (\S+)\. Approve")


def _signup_titles() -> set[str]:
    """SIGNUP_TITLE in every language it may have been written in."""
    from . import i18n
    return {SIGNUP_TITLE} | {i18n.catalog(lang).get(SIGNUP_TITLE, SIGNUP_TITLE) for lang in i18n.LANGUAGES
                             if lang != i18n.DEFAULT}


def _signup_patterns() -> list:
    """The signup messages in every language, as patterns that find the
    username in a message (the English one, and each translation)."""
    from . import i18n
    patterns = [_SIGNUP_NAME]
    for template in (SIGNUP_MESSAGE, SIGNUP_WITH_PROVIDER):
        for lang in i18n.LANGUAGES:
            text = template if lang == i18n.DEFAULT else i18n.catalog(lang).get(template)
            if not text or "%(username)s" not in text:
                continue
            parts = re.split(r"(%\(\w+\)s)", text)
            regex = "".join("(?P<username>\\S+?)" if p == "%(username)s" else ".*?" if p.startswith("%(")
                            else re.escape(p) for p in parts)
            patterns.append(re.compile("^" + regex + "$", re.S))
    return patterns


def settle_signups(session) -> int:
    """Mark read every "Account waiting for approval" whose account no
    longer waits (approved, or removed), for every admin: one admin's
    approval settles the others' notices too, whatever language each was
    written in. Commits if it changed any."""
    notes = session.scalars(select(NotificationRecord).where(
        NotificationRecord.category == "account", NotificationRecord.title.in_(_signup_titles()),
        NotificationRecord.is_read.is_(False))).all()
    if not notes:
        return 0
    waiting = set(session.scalars(select(UserAccount.username).where(UserAccount.role == "pending")))
    patterns = _signup_patterns()
    settled = 0
    for n in notes:
        name = None
        for pattern in patterns:
            m = pattern.search(n.message or "")
            if m:
                name = m.group("username") if "username" in pattern.groupindex else m.group(1)
                break
        if name and name not in waiting:
            n.is_read = True
            settled += 1
    if settled:
        session.commit()
    return settled


def unread_count(session, username: str) -> int:
    return session.scalar(select(func.count(NotificationRecord.id)).where(
        NotificationRecord.recipient_username == username, NotificationRecord.is_read.is_(False))) or 0


def recent(session, username: str, limit: int = 8, unread_only: bool = False, category: str = "") -> list:
    stmt = select(NotificationRecord).where(NotificationRecord.recipient_username == username)
    if unread_only:
        stmt = stmt.where(NotificationRecord.is_read.is_(False))
    if category:
        stmt = stmt.where(NotificationRecord.category == category)
    return session.scalars(stmt.order_by(NotificationRecord.created_at.desc(), NotificationRecord.id.desc())
                           .limit(limit)).all()


# ---------------------------------------------------------------- the daily reminder

def daily_genotyping_reminder(session, user) -> bool:
    """Once a day, the first time someone opens the app: what of theirs is
    waiting for genotyping. Silent when nothing is, or they turned it off."""
    if not getattr(user, "notify_genotyping", True):
        return False
    today_start = datetime.combine(date.today(), datetime.min.time())
    already = session.scalar(select(func.count(NotificationRecord.id)).where(
        NotificationRecord.recipient_username == user.username, NotificationRecord.category == "genotyping",
        NotificationRecord.actor == "", NotificationRecord.created_at >= today_start))
    if already:
        return False
    from . import i18n, lab
    features = lab.features_on(session)
    parts = []
    with i18n.using(i18n.language_for(session, user.username)):
        if features.get("colony", True):
            mice = session.scalar(select(func.count(MouseRecord.id)).where(
                MouseRecord.owner == user.username, MouseRecord.status == "geno",
                MouseRecord.date_of_death.is_(None))) or 0
            if mice:
                parts.append(ngettext("%(num)s mouse", "%(num)s mice", mice))
        if features.get("zebrafish", True):
            tanks = session.scalar(select(func.count(TankRecord.id)).where(
                TankRecord.owner == user.username, TankRecord.needs_genotyping.is_(True),
                TankRecord.active.is_(True))) or 0
            if tanks:
                parts.append(ngettext("%(num)s tank", "%(num)s tanks", tanks))
        if not parts:
            return False
        what = parts[0] if len(parts) == 1 else gettext("%(first)s and %(second)s", first=parts[0],
                                                              second=parts[1])
        title = gettext("Waiting for genotyping: %(what)s", what=what)
    link = url_for("colony", view="mice", scope="mine") if features.get("colony", True) else url_for("zebrafish")
    session.add(NotificationRecord(recipient_username=user.username, title=title,
                                   category="genotyping", link=link, actor=""))
    return True


def daily_experiment_reminder(session, user) -> bool:
    """Once a day, the first time someone opens the app: the manipulations
    and readout days due today (and any overdue) in their active
    experiments. Silent when nothing is, or they turned it off. Each is
    still recorded on the experiment's page."""
    if not getattr(user, "notify_experiments", True):
        return False
    today_start = datetime.combine(date.today(), datetime.min.time())
    already = session.scalar(select(func.count(NotificationRecord.id)).where(
        NotificationRecord.recipient_username == user.username, NotificationRecord.category == "experiments",
        NotificationRecord.actor == "", NotificationRecord.created_at >= today_start))
    if already:
        return False
    from . import experiment_steps as xs
    from . import experiments as ex
    from . import i18n
    from .models import Experiment
    due, overdue, first = [], 0, None
    for exp in session.scalars(select(Experiment).where(Experiment.owner_username == user.username,
                                                        Experiment.status == "active",
                                                        Experiment.start_date.is_not(None))):
        if not exp.steps:
            continue
        place = ex.place_for(session, exp.db or "colony")
        if place is None:
            continue
        for row in xs.schedule(session, exp, place):
            if row["state"] == "today":
                due.append((row["title"], exp.name, row["day"]))
                first = first or ex.page_url(exp) + f"#day-{row['day']}"
            elif row["state"] == "overdue":
                overdue += 1
                first = first or ex.page_url(exp) + f"#day-{row['day']}"
    if not due and not overdue:
        return False
    with i18n.using(i18n.language_for(session, user.username)):
        if due:
            listed = "; ".join(gettext("%(step)s (%(experiment)s, day %(day)s)", step=step, experiment=name,
                                            day=day) for step, name, day in due[:3])
            if len(due) > 3:
                listed = gettext("%(names)s and %(n)s more", names=listed, n=len(due) - 3)
            title = gettext("Due today: %(steps)s", steps=listed)
        else:
            title = gettext("Nothing due today in your experiments")
        if overdue:
            title = gettext("%(title)s · %(n)s overdue, not recorded yet", title=title, n=overdue)
    session.add(NotificationRecord(recipient_username=user.username, title=title[:200], category="experiments",
                                   link=first or "", actor=""))
    return True
