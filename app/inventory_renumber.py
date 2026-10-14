"""Renumbering an inventory's items.

An item's # (InventoryItem.number, 1, 2, 3… within its inventory) is given
when it is made, so after an import it follows the order of the file, not
the lab's own IDs. Renumbering gives every item of one inventory a new #,
ascending (or descending) by one of its columns, in natural order (B2
before B10), or the number in its name ("B12" is 12), when every name has
one and together they run 1, 2, 3… with none twice and none missing.

    plan = inventory_renumber.plan(session, module, "name_number")
    plan.problems or plan.moves        # [(item, old, new), …]: only those that change
    inventory_renumber.apply(session, module, plan)   # inside an audit.batch

It is one batch in Batch history, undone as one. Each item first gets a
number no item has (minus its row id), and then its new one, so no two
items share a number at any moment. What stores an item's number rather
than its row, it rewrites with it:

  * an order's "stocked_as" ("<inventory key>:<number>") and the notes
    "Reorder of <inventory> #<n>" (an open order for an item is found by
    them) and "From <order> #<n>": in the same batch, undone with it;
  * a day of an experiment's samples made, as the experiment page lists
    them: in the same batch;
  * "@<inventory key> <n>" in the notebook (pages, templates, comments and
    lines waiting to go into a page; a formulation's chemicals too): the
    notebook keeps no change history, so `follow_text` rewrites them, and
    undo.py calls it again the other way when the batch is undone. A page
    that is signed and locked keeps its old numbers (`locked_mentions` names
    them, for the preview to say so); so do the page's earlier versions.

What it can't reach is said before it is done: printed labels show the old
# (their QR code opens the item by its row, so it still finds it), as may
anything outside BioManager that wrote a # down.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import select, update

from .models import ExperimentStepRecord, InventoryItem, InventoryModule

NAME_NUMBER = "name_number"
ORDERS = ("ascending", "descending")
# The item's own columns that can be sorted by (with the inventory's own).
COLUMNS = ("name", "category", "status", "owner", "vendor", "catalog_number", "lot", "received_on", "expires_on",
           "created_at", "location_note", "notes")
DIGITS = re.compile(r"\d+")
MAX_SHOWN = 12


@dataclass
class Plan:
    by: str
    order: str
    moves: list[tuple] = field(default_factory=list)       # (item, old, new) for those that change
    count: int = 0                                         # items in the inventory
    problems: list[str] = field(default_factory=list)

    @property
    def mapping(self) -> dict[int, int]:
        return {old: new for _item, old, new in self.moves}


def columns(module) -> list[tuple[str, str]]:
    """(key, label) of what an inventory's items can be renumbered by."""
    from . import inventory_service as svc
    mv = svc.view(module)
    labels = {"name": mv.name_label, "category": mv.category_label, "status": "Status", "owner": "Owner",
              "vendor": "Vendor", "catalog_number": "Catalog number", "lot": "Lot", "received_on": "Received",
              "expires_on": "Expires", "created_at": "Date added", "location_note": "Location", "notes": "Notes"}
    out = [(NAME_NUMBER, "The number in the name")]
    out += [(key, labels[key]) for key in COLUMNS]
    out += [(f["key"], f["label"]) for f in mv.fields if f["key"] not in labels and f["type"] != "source"]
    return out


def natural(value) -> tuple:
    """A sort key that reads the numbers in a value as numbers (B2 < B10);
    an empty value goes last."""
    text = "" if value is None else (value.isoformat() if hasattr(value, "isoformat") else str(value)).strip()
    parts = [(0, int(p), "") if p.isdigit() else (1, 0, p.casefold()) for p in re.split(r"(\d+)", text) if p]
    return (not text, parts)


def name_number(name: str) -> int | None:
    """The number in a name, its last run of digits ("B12" → 12), or None."""
    found = DIGITS.findall(name or "")
    return int(found[-1]) if found else None


def _shown(items, limit: int = MAX_SHOWN) -> str:
    names = [i.name or f"#{i.number}" for i in items]
    return ", ".join(names[:limit]) + ("…" if len(names) > limit else "")


def plan(session, module, by: str, order: str = "ascending") -> Plan:
    """The new # of each item, or the problems that stop it, in words."""
    from .i18n import gettext, ngettext
    out = Plan(by=by, order=order)
    items = list(session.scalars(select(InventoryItem).where(InventoryItem.module_id_fk == module.id)
                                 .order_by(InventoryItem.number, InventoryItem.id)))
    out.count = len(items)
    if order not in ORDERS:
        out.problems.append(gettext("Order is ascending or descending."))
        return out
    known = dict(columns(module))
    if by not in known:
        out.problems.append(gettext("There is no column “%(column)s” to renumber by.", column=by))
        return out
    if not items:
        out.problems.append(gettext("There is nothing in it to renumber."))
        return out
    if by == NAME_NUMBER:
        numbers = {i.id: name_number(i.name) for i in items}
        none = [i for i in items if not numbers[i.id]]
        if none:
            out.problems.append(ngettext("%(num)s has no number in its name: %(items)s.",
                                         "%(num)s have no number in their names: %(items)s.", len(none),
                                         items=_shown(none)))
        seen: dict[int, list] = {}
        for i in items:
            if numbers[i.id]:
                seen.setdefault(numbers[i.id], []).append(i)
        twice = sorted((n, same) for n, same in seen.items() if len(same) > 1)
        for n, same in twice[:MAX_SHOWN]:
            out.problems.append(gettext("%(number)s is in more than one name: %(items)s.", number=n, items=_shown(same)))
        if len(twice) > MAX_SHOWN:
            out.problems.append(ngettext("…and %(num)s more number in more than one name.",
                                         "…and %(num)s more numbers in more than one name.", len(twice) - MAX_SHOWN))
        if seen and not none:
            missing = [n for n in range(1, max(seen) + 1) if n not in seen]
            if missing:
                shown = ", ".join(str(n) for n in missing[:MAX_SHOWN]) + ("…" if len(missing) > MAX_SHOWN else "")
                out.problems.append(gettext("No name has %(numbers)s (%(count)s missing): the numbering would have gaps.",
                                            numbers=shown, count=len(missing)))
        if out.problems:
            return out
        new = numbers
    else:
        def value(item):
            if by in COLUMNS:
                return getattr(item, by)
            return item.attrs_dict.get(by)
        # Ties keep the order they had; empty values go last either way.
        ranked = sorted(items, key=lambda i: (natural(value(i))[0], natural(value(i))[1] if order == "ascending"
                                              else _Desc(natural(value(i))[1]), i.number, i.id))
        new = {item.id: n for n, item in enumerate(ranked, 1)}
    out.moves = sorted(((i, i.number, new[i.id]) for i in items if i.number != new[i.id]), key=lambda m: m[2])
    if not out.moves:
        out.problems.append(gettext("Every item has that number already: nothing would change."))
    return out


class _Desc:
    """Reverses a sort key's order."""
    def __init__(self, key):
        self.key = key

    def __lt__(self, other):
        return other.key < self.key

    def __eq__(self, other):
        return self.key == other.key


def mention_words(session, module) -> list[str]:
    """How the notebook writes this inventory before a number: its key and
    every key it had ("@antibodies 5"), or "order" for the lab's first
    orders inventory."""
    from . import database_keys
    from . import inventory_service as svc
    if module.kind == "orders":
        first = svc.first_of_kind(session, "orders")
        return ["order"] if first is not None and first.id == module.id else []
    return [module.key, *database_keys.old_keys(session, "inventory", module.id)]


def _mention_re(words: list[str]):
    return re.compile(r"@(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(\s+)(\d+)(?!\d)")


def rewrite(text: str, pattern, mapping: dict[int, int]) -> str:
    """`text` with each "@<word> <old>" made "@<word> <new>", in one pass."""
    def swap(m):
        number = int(m.group(3))
        return f"@{m.group(1)}{m.group(2)}{mapping[number]}" if number in mapping else m.group(0)
    return pattern.sub(swap, text)


def _notebook_rows(session, words):
    """(model, column) of the notebook's text and the rows that may mention
    one of `words`."""
    from .models import NotebookComment, NotebookPage, NotebookPendingInsert, NotebookTemplate
    for model, column in ((NotebookPage, "body"), (NotebookTemplate, "body"), (NotebookComment, "body"),
                          (NotebookPendingInsert, "text")):
        like = None
        for w in words:
            clause = getattr(model, column).like(f"%@{w}%")
            like = clause if like is None else like | clause
        for row in session.scalars(select(model).where(like)):
            yield model, column, row


def locked_mentions(session, module, mapping: dict[int, int]) -> list[str]:
    """The titles of signed, locked pages that mention a renumbered item:
    they keep the old number."""
    from . import signatures
    from .models import NotebookPage
    words = mention_words(session, module)
    if not words or not mapping:
        return []
    pattern, out = _mention_re(words), []
    for model, column, row in _notebook_rows(session, words):
        if model is NotebookPage and rewrite(row.body or "", pattern, mapping) != (row.body or "") \
                and signatures.is_locked(session, row.id):
            out.append(row.title or "Untitled page")
    return out


def follow_text(session, module, mapping: dict[int, int]) -> int:
    """Rewrite "@<inventory> <old>" to the new number across the notebook
    (a locked page keeps it). The live editors of a page rewritten start
    again from its new text. Returns how many texts changed."""
    from . import lab_notebook, signatures
    from .models import NotebookPage
    words = mention_words(session, module)
    if not words or not mapping:
        return 0
    pattern, changed = _mention_re(words), 0
    for model, column, row in list(_notebook_rows(session, words)):
        before = getattr(row, column) or ""
        after = rewrite(before, pattern, mapping)
        if after == before:
            continue
        if model is NotebookPage:
            if signatures.is_locked(session, row.id):
                continue
            row.body = after
            lab_notebook.reset_collab(session, row.id)
        else:
            setattr(row, column, after)
        changed += 1
    return changed


def apply(session, module, the_plan: Plan) -> int:
    """Give each item its new number and follow it wherever it is stored.
    Call inside an audit.batch and commit after. Returns how many items
    changed."""
    moves = the_plan.moves
    if not moves:
        return 0
    ids = [item.id for item, _old, _new in moves]
    # First a number nothing has (minus the row's id), past the change
    # history, so no two items have one number at any point; then the new
    # one, written as an ordinary change (old → new) that undo puts back.
    for start in range(0, len(ids), 500):
        session.execute(update(InventoryItem).where(InventoryItem.id.in_(ids[start:start + 500]))
                        .values(number=-InventoryItem.id).execution_options(synchronize_session=False))
    for item, _old, new in moves:
        item.number = new
    session.flush()
    _follow_records(session, module, moves)
    follow_text(session, module, the_plan.mapping)
    session.flush()
    return len(moves)


def _follow_records(session, module, moves) -> None:
    """The records that store an item's number, rewritten (and kept in the
    change history, so the batch's undo puts them back too)."""
    from . import database_keys
    from . import inventory_service as svc
    mapping = {old: new for _item, old, new in moves}
    by_id = {item.id: new for item, _old, new in moves}
    keys = [module.key, *database_keys.old_keys(session, "inventory", module.id)]
    # An order put into stock: "stocked_as": "<key>:<number>".
    clause = None
    for key in keys:
        c = InventoryItem.attrs.like(f"%{key}:%")
        clause = c if clause is None else clause | c
    for order in session.scalars(select(InventoryItem).where(clause)):
        attrs = order.attrs_dict
        key, _, number = str(attrs.get("stocked_as", "")).rpartition(":")
        if key in keys and number.isdigit() and int(number) in mapping:
            attrs["stocked_as"] = f"{module.key}:{mapping[int(number)]}"
            order.attrs = json.dumps(attrs)
    # "Reorder of <inventory> #<n>", on an order made with Order again.
    reorder = re.compile(r"^Reorder of " + re.escape(module.label) + r" #(\d+)(?!\d)")
    for order in session.scalars(select(InventoryItem).where(
            InventoryItem.notes.like(f"Reorder of {module.label} #%"))):
        m = reorder.match(order.notes or "")
        if m and int(m.group(1)) in mapping:
            order.notes = f"Reorder of {module.label} #{mapping[int(m.group(1))]}" + order.notes[m.end():]
    if module.kind == "orders":
        # "From <order> #<n>", on what an order was put into stock as.
        noun = svc.view(module).item_noun
        stocked = {}
        for order, _old, _new in moves:
            key, _, number = str(order.attrs_dict.get("stocked_as", "")).rpartition(":")
            target = svc.get_module(session, key) if key and number.isdigit() else None
            if target is not None:
                stocked[(target.id, int(number))] = order
        from_re = re.compile(r"^From " + re.escape(noun) + r" #(\d+)(?!\d)")
        for (target_id, number), order in stocked.items():
            stock = session.scalar(select(InventoryItem).where(InventoryItem.module_id_fk == target_id,
                                                               InventoryItem.number == number))
            m = from_re.match(stock.notes or "") if stock is not None else None
            if m and int(m.group(1)) in mapping:
                stock.notes = f"From {noun} #{mapping[int(m.group(1))]}" + stock.notes[m.end():]
    # The samples a day of an experiment made, as its page lists them.
    for record in session.scalars(select(ExperimentStepRecord).where(ExperimentStepRecord.samples.like('%"number"%'))):
        try:
            samples = json.loads(record.samples or "[]")
        except ValueError:
            continue
        changed = False
        for s in samples if isinstance(samples, list) else []:
            if isinstance(s, dict) and s.get("id") in by_id and s.get("number") != by_id[s["id"]]:
                s["number"], changed = by_id[s["id"]], True
        if changed:
            record.samples = json.dumps(samples)


def undone(session, entries) -> None:
    """After undo.py puts items' numbers back: the notebook's mentions
    follow, as `apply` made them follow forwards. `entries` are the audit
    entries reverted ((table, record id, the number's [before, after]))."""
    by_module: dict[int, dict[int, int]] = {}
    for record_id, pair in entries:
        item = session.get(InventoryItem, record_id)
        if item is None or not isinstance(pair, list) or len(pair) != 2:
            continue
        before, after = pair
        if isinstance(before, int) and isinstance(after, int) and before > 0 and after > 0:
            by_module.setdefault(item.module_id_fk, {})[after] = before
    for module_id, mapping in by_module.items():
        module = session.get(InventoryModule, module_id)
        if module is not None:
            follow_text(session, module, mapping)
