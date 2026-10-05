"""What a plasmid was made from, and what was made from it.

Each link (plasmid_parents) names a parent, in the lab or from outside it,
its role and the method; an assembly adds the details it used (enzymes,
coordinates, primer ids). A plasmid's page shows its parents as
**Made from** and its children (plasmids, and anything in an inventory with
a plasmid column, such as viruses and glycerol stocks) as **Used to make**,
with a family tree of both directions.

The assembly wizard records its product's parents through add_parent, with
method set and details holding what it used.
"""
from __future__ import annotations

import json

from sqlalchemy import delete, select

from .models import PlasmidParent, PlasmidRecord

ROLES = ("backbone", "insert", "template", "donor", "other")
METHODS = ("", "digest_ligate", "gibson", "golden_gate", "pcr", "mutagenesis", "gateway", "synthesis", "other")
TREE_DEPTH = 4


def links_of(session, child: PlasmidRecord) -> list[PlasmidParent]:
    return list(session.scalars(select(PlasmidParent).where(PlasmidParent.child_row_id == child.id)
                                .order_by(PlasmidParent.position, PlasmidParent.id)))


def parents(session, child: PlasmidRecord) -> list[dict]:
    """Its parents, in order: {link, plasmid (or None), label, role, method, details}."""
    out = []
    for link in links_of(session, child):
        parent = session.get(PlasmidRecord, link.parent_row_id) if link.parent_row_id else None
        try:
            details = json.loads(link.details_json or "{}")
        except json.JSONDecodeError:
            details = {}
        out.append({"link": link, "plasmid": parent, "role": link.role, "method": link.method,
                    "label": (parent.name if parent else "") or link.parent_label, "details": details})
    return out


def children(session, parent: PlasmidRecord) -> list[PlasmidRecord]:
    ids = session.scalars(select(PlasmidParent.child_row_id).where(PlasmidParent.parent_row_id == parent.id)).all()
    if not ids:
        return []
    return list(session.scalars(select(PlasmidRecord).where(PlasmidRecord.id.in_(set(ids)))
                                .order_by(PlasmidRecord.plasmid_id)))


def _ancestors(session, row_id: int) -> set[int]:
    seen, frontier = set(), {row_id}
    while frontier:
        found = set(session.scalars(select(PlasmidParent.parent_row_id)
                                    .where(PlasmidParent.child_row_id.in_(frontier),
                                           PlasmidParent.parent_row_id.is_not(None))).all())
        frontier = found - seen
        seen |= found
    return seen


def add_parent(session, child: PlasmidRecord, *, parent: PlasmidRecord | None = None, label: str = "",
               role: str = "other", method: str = "", details: dict | None = None, user: str = "") -> PlasmidParent:
    """Link a parent. Refuses a plasmid as its own parent, or a link that
    would make a loop (a plasmid among its own ancestors)."""
    if parent is not None:
        if parent.id == child.id:
            raise ValueError("self")
        if child.id in _ancestors(session, parent.id):
            raise ValueError("loop")
    elif not label.strip():
        raise ValueError("empty")
    position = len(links_of(session, child))
    link = PlasmidParent(child_row_id=child.id, parent_row_id=parent.id if parent is not None else None,
                         parent_label=(label or (parent.name if parent is not None else ""))[:200],
                         role=role if role in ROLES else "other", method=method if method in METHODS else "other",
                         details_json=json.dumps(details or {}), position=position, created_by=user)
    session.add(link)
    session.flush()
    return link


def remove_link(session, child: PlasmidRecord, link_id: int) -> bool:
    found = session.get(PlasmidParent, link_id)
    if found is None or found.child_row_id != child.id:
        return False
    session.execute(delete(PlasmidParent).where(PlasmidParent.id == link_id))
    return True


def tree(session, p: PlasmidRecord) -> dict:
    """Its family to TREE_DEPTH generations each way: {"up": [...], "down": [...]},
    each node {plasmid, label, role, children: [...]}; a plasmid already shown
    is not followed again."""
    seen = {p.id}

    def up(node: PlasmidRecord, depth: int) -> list[dict]:
        if depth == 0:
            return []
        out = []
        for entry in parents(session, node):
            parent = entry["plasmid"]
            follow = parent is not None and parent.id not in seen
            if parent is not None:
                seen.add(parent.id)
            out.append({"plasmid": parent, "label": entry["label"], "role": entry["role"],
                        "method": entry["method"], "children": up(parent, depth - 1) if follow else []})
        return out

    def down(node: PlasmidRecord, depth: int) -> list[dict]:
        if depth == 0:
            return []
        out = []
        for child in children(session, node):
            follow = child.id not in seen
            seen.add(child.id)
            out.append({"plasmid": child, "label": child.name, "role": "", "method": "",
                        "children": down(child, depth - 1) if follow else []})
        return out

    return {"up": up(p, TREE_DEPTH), "down": down(p, TREE_DEPTH)}
