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

from sqlalchemy import delete, func, select

from .models import PlasmidParent, PlasmidRecord

ROLES = ("backbone", "insert", "template", "donor", "other")
METHODS = ("", "digest_ligate", "gibson", "infusion", "golden_gate", "pcr", "mutagenesis", "gateway", "synthesis", "other")
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


# Offered in the Backbone column with the lab's own plasmids: vectors many
# labs clone into, so a backbone the lab has no record of is picked, not typed.
COMMON_BACKBONES = (
    "pcDNA3.1(+)", "pcDNA3.1(-)", "pcDNA5/FRT/TO", "pCAGGS", "pCMV", "pEGFP-N1", "pEGFP-C1", "pmCherry-N1",
    "pLKO.1-puro", "pLenti CMV", "pLVX-Puro", "pCDH-CMV", "lentiCRISPRv2", "pLL3.7", "psPAX2", "pMD2.G",
    "pAAV-MCS", "pAAV-CAG", "pAAV-hSyn", "pX330", "pX459", "pMSCV", "pBABE-puro", "pRetroX-TetOne",
    "pET-28a(+)", "pET-21a(+)", "pGEX-4T-1", "pMAL-c5X", "pUC19", "pBluescript II SK(+)", "pGL3-Basic",
    "pGL4.10", "pENTR/D-TOPO", "pDONR221", "pcDNA-DEST40", "pFastBac1", "pCS2+", "pTol2",
)


def column_parent(session, typed: str) -> PlasmidRecord | None:
    """The lab's plasmid a Backbone or Insert names: "#42", "#42 pcDNA3.1"
    picked from the list, "@pcDNA3.1" or "@42", or its exact name. A bare
    number is not taken as one: an insert can start with one ("5' UTR",
    "12S rRNA")."""
    from .inventory_service import resolve_plasmid
    import re
    text = (typed or "").strip()
    if text.startswith("@"):
        return resolve_plasmid(session, text[1:].strip())
    if re.match(r"#\s*\d", text):
        return resolve_plasmid(session, text)
    return session.scalar(select(PlasmidRecord).where(func.lower(PlasmidRecord.name) == text.lower()).limit(1)) if text else None


def follow_column(session, p: PlasmidRecord, role: str, before: str, typed: str, user: str = "") -> str:
    """The Backbone (role "backbone") or Insert ("insert") typed for a
    plasmid, and what it was made from to match: one naming a plasmid of
    the lab links it as Made from in that role, and reads as its name; one
    that named another before drops the link this column made (a link added
    on the plasmid's page, or by an assembly, stays). Returns the text to
    keep."""
    text = (typed or "").strip()
    parent = column_parent(session, text) if text else None
    old = column_parent(session, before) if (before or "").strip() else None
    if parent is not None and parent.id == p.id:
        parent = None
    if old is not None and (parent is None or old.id != parent.id):
        for link in links_of(session, p):
            if (link.parent_row_id == old.id and link.role == role
                    and json.loads(link.details_json or "{}").get("from_column")):
                session.execute(delete(PlasmidParent).where(PlasmidParent.id == link.id))
    if parent is None:
        return text.lstrip("@").strip() if text.startswith("@") else text
    if not any(link.parent_row_id == parent.id and link.role == role for link in links_of(session, p)):
        try:
            add_parent(session, p, parent=parent, role=role, details={"from_column": True}, user=user)
        except ValueError:
            pass    # a loop: the column keeps its words, without the link
    return parent.name or text


def unlinked(session, plasmids: list[PlasmidRecord]) -> list[tuple[PlasmidRecord, str, PlasmidRecord]]:
    """(plasmid, role, parent) for each Backbone or Insert that names
    another of the lab's plasmids, by its exact name or number, which the
    plasmid is not yet Made from in that role: written before the columns
    linked, or typed elsewhere."""
    import re
    by_name: dict[str, PlasmidRecord] = {}
    for p in plasmids:
        by_name.setdefault((p.name or "").strip().casefold(), p)
    by_number = {p.plasmid_id: p for p in plasmids}
    linked = {(link.child_row_id, link.parent_row_id, link.role)
              for link in session.scalars(select(PlasmidParent).where(PlasmidParent.parent_row_id.is_not(None)))}
    out = []
    for p in plasmids:
        for column, role in (("backbone", "backbone"), ("insert_seq", "insert")):
            text = (getattr(p, column) or "").strip().lstrip("@").strip()
            if not text:
                continue
            number = re.match(r"^#\s*(\d+)\b", text)
            parent = by_number.get(int(number.group(1))) if number else by_name.get(text.casefold())
            if parent is not None and parent.id != p.id and (p.id, parent.id, role) not in linked:
                out.append((p, role, parent))
    return out


def link_columns(session, found: list[tuple[PlasmidRecord, str, PlasmidRecord]], user: str = "") -> int:
    """Make each plasmid Made from what its column names (unlinked's
    answer); a link that would make a loop is left out. Returns how many."""
    made = 0
    for p, role, parent in found:
        try:
            add_parent(session, p, parent=parent, role=role, details={"from_column": True}, user=user)
        except ValueError:
            continue
        setattr(p, "backbone" if role == "backbone" else "insert_seq", (parent.name or "")[:200])
        made += 1
    return made


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
