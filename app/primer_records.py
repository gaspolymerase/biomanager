"""Primers and the plasmids they bind.

A primer drawn on a plasmid's map (the editor's primer annotations) gets a
record of its own in the lab's Primers database, with the plasmid in its
**Plasmid** column, so it can be ordered and kept in a box like any primer.
The plasmid's page lists its primers, finds where each binds, and copies or
exports several at once for ordering. The assembly wizard saves the primers
it designs through save_primer too. Find saved primers checks every
primer the person may see, whatever plasmid it names, against a sequence
(TemplateIndex), so a lab doesn't order a primer it already has.

Matching keeps it idempotent: the editor saves after every edit and may not
echo back the record id it was given, so a primer annotation is matched to
the record with the same plasmid and sequence (or name) before a new one is
made. Removing a primer from the map leaves its record: it may be a real tube.
"""
from __future__ import annotations

import csv
import io
import json
import re

from sqlalchemy import select

from .models import InventoryItem, InventoryModule, PlasmidRecord

TEMPLATE_KEY = "template"
TEMPLATE_FIELD = {"key": TEMPLATE_KEY, "label": "Plasmid", "type": "plasmid", "icon": "plasmid", "width": 120}
MIN_ANCHOR = 15            # 3′ bases that must match for a primer to bind
TO_ORDER = "to order"

_COMPLEMENT = str.maketrans("ACGTURYKMSWBDHVNacgturykmswbdhvn", "TGCAAYRMKSWVHDBNtgcaayrmkswvhdbn")


def reverse_complement(seq: str) -> str:
    return (seq or "").translate(_COMPLEMENT)[::-1]


def region_primer(template: str, start: int, end: int, direction: int) -> str:
    """The 5′→3′ sequence of a primer annotated from start to end (0-based,
    inclusive; start > end wraps the origin), on the given strand."""
    if start <= end:
        region = template[start:end + 1]
    else:
        region = template[start:] + template[:end + 1]
    return region.upper() if direction >= 0 else reverse_complement(region).upper()


def binding_sites(template: str, circular: bool, primer: str, min_anchor: int = MIN_ANCHOR) -> list[dict]:
    """Where a primer anneals: its 3′ end must match `min_anchor` bases
    exactly; the match is then extended towards the 5′ end. Each site is
    {start, end, direction, annealed, tail} (0-based inclusive; a 5′ tail,
    such as a restriction site or homology arm, is the part that doesn't
    anneal)."""
    template = (template or "").upper()
    primer = re.sub(r"[^A-Za-z]", "", primer or "").upper()
    n = len(template)
    if not template or len(primer) < 8:
        return []
    anchor_len = min(min_anchor, len(primer))
    anchor = primer[-anchor_len:]
    search = template + (template[:len(primer) - 1] if circular else "")
    sites = []
    for direction, strand_anchor in ((1, anchor), (-1, reverse_complement(anchor))):
        for m in re.finditer(f"(?={re.escape(strand_anchor)})", search):
            if m.start() < n:
                sites.append(_site(template, circular, primer, anchor_len, direction, m.start()))
    return sites


def _site(template: str, circular: bool, primer: str, anchor_len: int, direction: int, pos: int) -> dict:
    """The site of a primer whose 3′ `anchor_len` bases (on the reverse
    strand, their reverse complement) sit at `pos` of the top strand,
    extended towards the primer's 5′ end while the bases pair."""
    n = len(template)
    annealed = anchor_len
    if direction == 1:
        # 3′ end is at pos + anchor_len - 1; extend leftwards.
        while annealed < len(primer):
            i = pos - (annealed - anchor_len) - 1
            if i < 0 and not circular:
                break
            if template[i % n] != primer[-annealed - 1]:
                break
            annealed += 1
        end = (pos + anchor_len - 1) % n
        start = (end - annealed + 1) % n
    else:
        # The reverse primer's 3′ end pairs with the template at pos;
        # its reverse complement reads along the top strand from there.
        rc = reverse_complement(primer)
        while annealed < len(primer):
            i = pos + annealed
            if i >= n and not circular:
                break
            if template[i % n] != rc[annealed]:
                break
            annealed += 1
        start = pos % n
        end = (pos + annealed - 1) % n
    return {"start": start, "end": end, "direction": direction, "annealed": annealed,
            "tail": len(primer) - annealed}


class TemplateIndex:
    """Where every MIN_ANCHOR-base stretch of a template's top strand starts,
    so that the lab's saved primers can be checked against it at once (Find
    saved primers): a primer binds only where its 3′-terminal MIN_ANCHOR
    bases, or their reverse complement, start. Each site is then extended as
    binding_sites does, so the answer is the same as asking binding_sites of
    every primer, in a fraction of the time."""

    def __init__(self, template: str, circular: bool, k: int = MIN_ANCHOR):
        self.template = (template or "").upper()
        self.circular = bool(circular)
        self.k = k
        n = len(self.template)
        # Across the origin too: an anchor may start in the last k-1 bases.
        search = self.template + (self.template[:k - 1] if self.circular else "")
        self.starts: dict[str, list[int]] = {}
        for i in range(min(n, len(search) - k + 1)):
            self.starts.setdefault(search[i:i + k], []).append(i)

    def sites(self, primer: str) -> list[dict]:
        """binding_sites(template, circular, primer) for a primer of at
        least k bases whose 3′ end is plain A, C, G, T (others: [])."""
        primer = re.sub(r"[^A-Za-z]", "", primer or "").upper()
        if len(primer) < self.k:
            return []
        anchor = primer[-self.k:]
        if not _PLAIN.fullmatch(anchor):
            return []
        return [_site(self.template, self.circular, primer, self.k, direction, pos)
                for direction, strand_anchor in ((1, anchor), (-1, reverse_complement(anchor)))
                for pos in self.starts.get(strand_anchor, ())]


_PLAIN = re.compile(r"[ACGT]+")


def visible_primer_databases(session) -> list[InventoryModule]:
    """The Primers databases this person may see: the lab's, their own,
    their project groups' (not someone else's own, nor one switched off)."""
    from . import inventory_service as inventories
    from . import lab

    return [m for m in inventories.list_modules(session) if m.kind == "primers" and lab.can_see(m)]


def saved_primers(session) -> list[tuple[InventoryModule, InventoryItem]]:
    """Every record in the Primers databases this person may see, as
    (database, record) pairs."""
    modules = visible_primer_databases(session)
    if not modules:
        return []
    by_id = {m.id: m for m in modules}
    items = session.scalars(select(InventoryItem).where(InventoryItem.module_id_fk.in_(list(by_id)))
                            .order_by(InventoryItem.module_id_fk, InventoryItem.number))
    return [(by_id[i.module_id_fk], i) for i in items]


def primers_module(session, user: str = "", create: bool = True) -> InventoryModule | None:
    """The lab's Primers database, made when none exists yet and `create`."""
    from . import inventory_service as inventories

    from . import lab

    module = inventories.first_of_kind(session, "primers")
    # Made for the lab only by someone Lab setup lets add lab databases.
    if module is None and create and lab.may_create_lab_database(session):
        module = inventories.create_module(session, "primers", created_by=user)
    if module is not None:
        ensure_template_field(module)
    return module


def ensure_template_field(module: InventoryModule) -> None:
    """A Primers database made before primers linked to plasmids gets the
    Plasmid column (after Target)."""
    from . import inventory as presets

    settings = presets.normalise_settings(module.settings)
    if any(f["key"] == TEMPLATE_KEY for f in settings["fields"]):
        return
    fields = list(settings["fields"])
    at = next((i + 1 for i, f in enumerate(fields) if f["key"] == "target"), len(fields))
    fields.insert(at, dict(TEMPLATE_FIELD))
    settings["fields"] = fields
    module.settings = json.dumps(presets.normalise_settings(settings))


def _items(session, module: InventoryModule):
    return session.scalars(select(InventoryItem).where(InventoryItem.module_id_fk == module.id))


def primers_for(session, plasmid: PlasmidRecord) -> list[InventoryItem]:
    """Records in the Primers database whose Plasmid column names this one."""
    module = primers_module(session, create=False)
    if module is None:
        return []
    number = str(plasmid.plasmid_id)
    return [i for i in session.scalars(select(InventoryItem).where(
                InventoryItem.module_id_fk == module.id, InventoryItem.attrs.like(f'%"{number}"%'))
                .order_by(InventoryItem.number))
            if str(i.attrs_dict.get(TEMPLATE_KEY, "")).strip() == number]


def save_primer(session, module: InventoryModule, plasmid: PlasmidRecord | None, *, name: str, sequence: str,
                direction: str = "", user: str = "", category: str = "", notes: str = "") -> tuple[InventoryItem, bool]:
    """The record for this primer: an existing one for the same plasmid with
    the same sequence (or, failing that, the same name) is updated; else a
    new one is made. Returns (record, made)."""
    from . import inventory as presets
    from . import inventory_service as inventories

    sequence = re.sub(r"[^A-Za-z]", "", sequence or "").upper()
    number = str(plasmid.plasmid_id) if plasmid is not None else ""
    candidates = [i for i in _items(session, module)
                  if not number or str(i.attrs_dict.get(TEMPLATE_KEY, "")).strip() == number]
    found = next((i for i in candidates if i.attrs_dict.get("sequence", "").upper() == sequence), None)
    if found is None and name:
        found = next((i for i in candidates if (i.name or "").strip().lower() == name.strip().lower()), None)
    made = found is None
    if made:
        statuses = presets.normalise_settings(module.settings)["statuses"]
        found = InventoryItem(module_id_fk=module.id, number=inventories.next_number(session, module.id),
                              status=TO_ORDER if TO_ORDER in statuses else "", owner=user, category=category)
        session.add(found)
    attrs = found.attrs_dict
    attrs["sequence"] = sequence
    if direction:
        attrs["direction"] = direction
    if number:
        attrs[TEMPLATE_KEY] = number
    found.attrs = json.dumps(attrs)
    if name:
        found.name = name[:200]
    if notes and not (found.notes or "").strip():
        found.notes = notes
    found.updated_by = user
    session.flush()
    return found, made


def sync_from_map(session, plasmid: PlasmidRecord, annotations: list[dict], user: str) -> int:
    """Give every primer drawn on the map its record in the Primers database
    (made if the lab has none yet), and note the record's id on the
    annotation. Returns how many records were new."""
    primers = [a for a in annotations if isinstance(a, dict) and a.get("kind") == "primer"]
    if not primers:
        return 0
    module = primers_module(session, user)
    if module is None:   # no Primers database, and this person may not add one
        return 0
    made_count = 0
    template = plasmid.full_sequence or ""
    for a in primers:
        try:
            start, end = int(a["start"]), int(a["end"])
        except (KeyError, TypeError, ValueError):
            continue
        direction = 1 if int(a.get("direction", 1) or 1) >= 0 else -1
        bases = a.get("bases") if isinstance(a.get("bases"), str) and a["bases"].strip() else ""
        sequence = bases or region_primer(template, start, end, direction)
        if len(sequence) < 8:
            continue
        name = (a.get("name") or "").strip() or f"{plasmid.name or f'#{plasmid.plasmid_id}'} {start + 1}-{end + 1}"
        item, made = save_primer(session, module, plasmid, name=name, sequence=sequence,
                                 direction="forward" if direction > 0 else "reverse", user=user, category="cloning")
        a["inventory_id"] = item.id
        made_count += made
    return made_count


ORDER_COLUMNS = ("Name", "Sequence", "Scale", "Purification")


def order_sheet(items: list[InventoryItem]) -> str:
    """A CSV to paste or upload to an oligo supplier: Name, Sequence (5′→3′),
    Scale, Purification (blank scale for the supplier's default)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(ORDER_COLUMNS)
    for i in items:
        attrs = i.attrs_dict
        writer.writerow([i.name or f"primer-{i.number}", attrs.get("sequence", ""), "",
                         attrs.get("purification", "") or ""])
    return out.getvalue()


def order_lines(items: list[InventoryItem]) -> str:
    """Name<TAB>sequence lines, for pasting into a supplier's bulk-entry box."""
    return "\n".join(f"{i.name or f'primer-{i.number}'}\t{i.attrs_dict.get('sequence', '')}" for i in items)
