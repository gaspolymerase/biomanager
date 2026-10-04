"""QR codes and printable cage / tank / vial cards.

The gap this closes: the label on the physical cage and the record in the
database were separate things, so anyone at the rack had to walk back and
type an ID. Every comparable tool solves this the same way — put a scannable
code on the card that opens the record.

The pieces:

  * `/labels/qr.svg?d=…` renders a QR as inline SVG. Pure Python via segno,
    no image libraries, no network, so it works in the packaged desktop app.
  * `/labels/cards/<kind>` lays cards out for printing: cages, a species'
    housing units, fly vials (`stocks/<key>`) and inventory tubes
    (`inventory/<key>`). Either a sheet of cards for any printer, or one
    label a page at a label printer's size (STOCKS: Brother QL rolls,
    Zebra labels, cryo-tube labels), which the printer's own driver prints.
  * `?format=zpl` gives the same labels as ZPL for a Zebra, and a Zebra on
    the lab's network (its address set once by an admin) is sent them
    directly on port 9100.

QR payloads are always absolute URLs built from the incoming request, so a
card printed from the lab server scans to the lab server rather than to
localhost.
"""
from __future__ import annotations

import ipaddress
import math
import socket
from datetime import date

from flask import Blueprint, Response, abort, flash, g, redirect, request, session as cookie, url_for
from flask import render_template
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .formutil import arg_int
from .db import SessionLocal
from .i18n import gettext, ngettext, translate_value
from .models import CageRecord, InventoryItem, OrgHousing, StockUnit, TankRecord
from . import access, positions
from . import organism_service as svc

bp = Blueprint("labels", __name__, url_prefix="/labels")

# Card sizes in millimetres, matched to common cage-card stock.
CARD_SIZES = {
    "cage": (100, 60),      # a typical mouse cage card
    "tank": (75, 45),       # aquatic tank label
    "vial": (50, 25),       # fly vial / plate label
    "tube": (50, 25),       # an inventory tube or box
}

# What the labels are printed on: a sheet of cards, or a label printer's
# roll, (width, height) in mm as the label reads. Each label printer's
# driver offers the same sizes by name.
STOCKS = {
    "sheet": ("Sheet of cards · any printer", None),
    "62x29": ("Brother QL · 62 × 29 mm (DK-11209)", (62, 29)),
    "90x29": ("Brother QL · 90 × 29 mm (DK-11201)", (90, 29)),
    "100x62": ("Brother QL · 100 × 62 mm (DK-11202)", (100, 62)),
    "51x25": ("Zebra · 2 × 1 in", (51, 25)),
    "76x25": ("Zebra · 3 × 1 in", (76, 25)),
    "102x51": ("Zebra · 4 × 2 in", (102, 51)),
    "102x64": ("Zebra · 4 × 2.5 in, a cage card", (102, 64)),
    "33x13": ("Cryo tube · 1.28 × 0.5 in", (33, 13)),
}
PRINTER_SETTING = "label_printer"          # a Zebra on the lab's network: host or host:port
PRINTER_DPI_SETTING = "label_printer_dpi"  # 203 or 300
ZEBRA_PORT = 9100


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login"))
    return None


def _qr_svg(payload: str, scale: int = 4, fit: bool = False) -> str:
    """Inline SVG for a QR code, or an empty string if segno is missing.
    `fit`: sized by the page (a viewBox, no width): a label's CSS shrinks
    the whole code to its box. Without it, a code drawn 99 px wide in a
    58 px box was cut to one corner and could not be scanned.

    segno is an optional dependency: without it the cards still print, just
    without the scannable part, which is better than a 500 at the printer.
    """
    try:
        import segno
    except ImportError:
        return ""
    # Error level M survives a smudged or partly peeled label.
    qr = segno.make(payload, error="m")
    return qr.svg_inline(scale=scale, border=0, dark="#15191d", omitsize=fit)


@bp.route("/qr.svg")
def qr_svg():
    """A single QR as a standalone SVG, for embedding anywhere."""
    payload = (request.args.get("d") or "").strip()
    if not payload:
        abort(400)
    try:
        scale = max(1, min(12, arg_int("scale", 4)))
    except ValueError:
        scale = 4
    svg = _qr_svg(payload, scale)
    if not svg:
        abort(503)
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


def _cage_where(cage) -> str:
    """"Rack A · D7", "Rack A" when not placed in it, else the room or the
    location note."""
    if cage.rack is not None:
        where = cage.rack.name
        if cage.rack_row and cage.rack_col:
            where += " · " + positions.label(cage.rack_row, cage.rack_col, cage.rack.naming, cage.rack.cols)
        return where
    return cage.room or cage.cage_location or "—"


def _absolute(path: str) -> str:
    """Turn an app path into a URL that resolves from a phone on the LAN:
    a desktop sharing its lab names its network address, not 127.0.0.1."""
    from . import devices
    return (devices.share_url() or request.url_root).rstrip("/") + path


def _ids() -> list[int]:
    """The records asked for: ?ids=1,2,3 (a link) or selected_ids (the sheet's ticked rows)."""
    raw = (request.values.get("ids") or "").split(",") + request.values.getlist("selected_ids")
    return [int(i) for i in raw if i.strip().isdigit()]


def _cage_cards(session) -> dict:
    """Mouse cages: `ids`, or every cage in the current scope (the usual case
    after setting up a rack)."""
    scope = access.resolve_scope(request.values.get("scope"))
    ids = _ids()
    stmt = select(CageRecord).options(selectinload(CageRecord.mice), selectinload(CageRecord.rack)).order_by(CageRecord.cage_id)
    if ids:
        stmt = stmt.where(CageRecord.id.in_(ids))
    cages = [c for c in session.scalars(stmt).all()
             if ids or access.in_scope(c, scope, shared=access.cage_shared_with(c),
                                                    group_id=access.cage_group(c))]
    cards = []
    for cage in cages:
        living = [m for m in cage.mice if m.date_of_death is None]
        females = sum(1 for m in living if m.gender == "F")
        males = sum(1 for m in living if m.gender == "M")
        split = " ".join(f"{n}{sign}" for n, sign in
                         ((females, "♀"), (males, "♂"), (len(living) - females - males, "?")) if n)
        genotypes = sorted({(m.genotype or "").strip() for m in living if (m.genotype or "").strip()})
        # scope=all: a card is scanned by whoever is at the rack, and the
        # default "My colony" view would leave someone else's cage out.
        target = _absolute(url_for("colony", view="cages", scope="all", card=1) + f"#cage-{cage.id}")
        cards.append({
            "title": f"Cage {cage.cage_id}",
            "target": target,
            "rows": [
                ("Owner", cage.owner or "—"),
                ("Purpose", cage.purpose or "—"),
                # Where it goes back: its rack and position, else the room.
                ("Where", _cage_where(cage)),
                ("Animals", f"{len(living)} · {split}" if split else str(len(living))),
                ("Genotype", "; ".join(genotypes)[:60] or "—"),
                ("Card ID", cage.card_id or "—"),
            ],
            "shared": access.is_shared_cage(cage),
        })
    return {"cards": cards, "heading": "Cage cards", "size": CARD_SIZES["cage"], "kind": "cages",
            "back_url": url_for("colony", view="cages", scope=scope)}


def _tank_cards(session) -> dict:
    """Zebrafish tanks: `ids`, or every active tank."""
    from .app import fish_position_label
    ids = _ids()
    stmt = select(TankRecord).options(selectinload(TankRecord.fish), selectinload(TankRecord.line),
                                      selectinload(TankRecord.rack)).order_by(TankRecord.tank_id)
    stmt = stmt.where(TankRecord.id.in_(ids)) if ids else stmt.where(TankRecord.active.is_(True))
    cards = []
    for tank in session.scalars(stmt):
        alive = [f for f in tank.fish if (f.status or "alive") == "alive"]
        genotypes = sorted({(f.genotype or "").strip() for f in alive if (f.genotype or "").strip()})
        where = (tank.rack.name + (" · " + fish_position_label(tank) if fish_position_label(tank) else "")) if tank.rack else "—"
        cards.append({
            "title": f"Tank {tank.tank_id}",
            "target": _absolute(url_for("zebrafish", view="tanks") + f"#tank-{tank.id}"),
            "rows": [
                ("Line", tank.line.name if tank.line else "—"),
                ("Owner", tank.owner or "—"),
                ("Where", where),
                ("Fish", str(sum(f.count or 0 for f in alive))),
                ("Genotype", "; ".join(genotypes)[:60] or "—"),
            ],
            "shared": False,
        })
    return {"cards": cards, "heading": "Tank labels", "size": CARD_SIZES["tank"], "kind": "tanks",
            "back_url": url_for("zebrafish", view="tanks")}


def _module_cards(session, module_key: str) -> dict:
    """A configurable species' housing units: `ids`, or every active one."""
    module = svc.get_module(session, module_key)
    if module is None:
        abort(404)
    mv = svc.view(module)
    ids = _ids()
    stmt = select(OrgHousing).where(OrgHousing.module_id_fk == module.id).order_by(OrgHousing.code)
    if ids:
        stmt = stmt.where(OrgHousing.id.in_(ids))
    elif request.args.get("active", "1") == "1":
        stmt = stmt.where(OrgHousing.active.is_(True))
    fields = svc.fields_for(session, module.id, "housing")
    shown = [f for f in fields if f.show_in_table][:2]
    cards = []
    for unit in session.scalars(stmt):
        attrs = unit.attrs_dict
        rows = [
            (mv.line_noun.title(), unit.line.code if unit.line else "—"),
            ("Owner", unit.owner or "—"),
            ("Purpose", unit.purpose or "—"),
        ]
        rows += [(f.label, str(attrs.get(f.key, "") or "—")) for f in shown]
        if unit.last_serviced_on:
            rows.append(("Last serviced", unit.last_serviced_on.isoformat()))
        cards.append({
            "title": f"{mv.housing_noun.title()} {unit.code}",
            "target": _absolute(url_for("organisms.module", key=module.key, view="housing") + f"#unit-{unit.id}"),
            "rows": rows,
            "shared": False,
        })
    # The heading is only on the page (never printed): in the page's language.
    return {"cards": cards, "heading": gettext("%(name)s · %(units)s labels", name=translate_value(mv.label),
                                               units=translate_value(mv.housing_noun)),
            "size": CARD_SIZES.get("vial" if mv.housing_noun in ("vial", "plate") else "tank"),
            "kind": module_key, "back_url": url_for("organisms.module", key=module.key, view="housing")}


def _stock_cards(session, key: str) -> dict:
    """Fly vials (and every stock database's units): `ids`, or every one in use."""
    from . import stock_service as ssvc
    from .stock_routes import _module_or_404
    module = _module_or_404(session, key)
    mv = ssvc.view(module)
    ids = _ids()
    stmt = (select(StockUnit).options(selectinload(StockUnit.rack))
            .where(StockUnit.module_id_fk == module.id).order_by(StockUnit.number))
    stmt = stmt.where(StockUnit.id.in_(ids)) if ids else stmt.where(StockUnit.active.is_(True))
    cards = []
    for unit in session.scalars(stmt):
        where = "—"
        if unit.rack is not None:
            where = unit.rack.name
            if unit.rack_row and unit.rack_col:
                where += " · " + positions.label(unit.rack_row, unit.rack_col, unit.rack.naming, unit.rack.cols)
        rows = [("Genotype", (unit.genotype or "—")[:80]), ("Owner", unit.owner or "—"),
                ("Purpose", unit.purpose or "—"), ("Where", where)]
        if unit.set_up_on:
            rows.append(("Set up", unit.set_up_on.isoformat()))
        cards.append({
            "title": mv.code(unit),
            "target": _absolute(url_for("stocks.module", key=key) + f"#unit-{unit.id}"),
            "rows": rows,
            "shared": False,
        })
    return {"cards": cards, "heading": gettext("%(name)s · %(units)s labels", name=translate_value(module.label),
                                               units=translate_value(mv.units)), "size": CARD_SIZES["vial"],
            "kind": f"stocks/{key}", "back_url": url_for("stocks.module", key=key)}


def _inventory_cards(session, key: str) -> dict:
    """Tubes and boxes in an inventory: the ticked rows (`ids`)."""
    from . import inventory_service as isvc
    from .inventory_routes import _module_or_404
    module = _module_or_404(session, key)
    mv = isvc.view(module)
    ids = _ids()
    items = session.scalars(select(InventoryItem).options(selectinload(InventoryItem.rack))
                            .where(InventoryItem.module_id_fk == module.id, InventoryItem.id.in_(ids or [0]))
                            .order_by(InventoryItem.number)).all()
    # What a label can say, in the order it is printed; the person printing
    # ticks which (remembered per database), e.g. only the position and the
    # date on a cryo tube.
    fields = [("category", mv.category_label), ("lot", "Lot"), ("owner", "Owner"), ("where", "Where"),
              ("box", "Box"), ("position", "Position"), ("received", "Received"), ("expires", "Expires")]
    fields += [(f"attr_{f['key']}", f["label"]) for f in mv.fields if f["type"] not in ("source", "textarea")]
    fields.append(("printed", "Printed on"))
    chosen = _label_fields(f"inventory/{key}", [k for k, _ in fields],
                           ["category", "lot", "owner", "where", "received", "expires"])
    labels = dict(fields)
    today = date.today().isoformat()
    cards = []
    for item in items:
        place = isvc.rack_label(item)
        attrs = item.attrs_dict
        values = {
            "category": item.category, "lot": item.lot, "owner": item.owner or "—",
            "where": (item.rack.name + (" · " + place if place else "")) if item.rack else (item.location_note or "—"),
            "box": item.rack.name if item.rack else "", "position": place,
            "received": item.received_on.isoformat() if item.received_on else "",
            "expires": item.expires_on.isoformat() if item.expires_on else "",
            "printed": today,
            **{f"attr_{k}": str(v) for k, v in attrs.items() if isinstance(v, (str, int, float)) and str(v).strip()},
        }
        # A concentration reads with its unit ("812.4 ng/µL"), in one line.
        if values.get("attr_concentration") and values.get("attr_conc_unit"):
            values["attr_concentration"] += " " + values.pop("attr_conc_unit")
        cards.append({
            "title": f"#{item.number} {item.name}".strip(),
            "target": _absolute(url_for("inventory.module", key=key) + f"#item-{item.id}"),
            "rows": [(labels[k], values[k]) for k in chosen if values.get(k)],
            "shared": False,
        })
    return {"cards": cards, "heading": gettext("%(name)s · labels", name=translate_value(module.label)),
            "size": CARD_SIZES["tube"],
            "kind": f"inventory/{key}", "back_url": url_for("inventory.module", key=key),
            "fields": fields, "chosen": chosen}


def _label_fields(kind: str, allowed: list[str], default: list[str]) -> list[str]:
    """The fields ticked on the labels page (`f`, with `fields_set`), else
    the ones this person last printed for this kind, else `default`."""
    remembered = cookie.get("label_fields") or {}
    if request.values.get("fields_set"):
        chosen = [k for k in request.values.getlist("f") if k in allowed]
        cookie["label_fields"] = {**remembered, kind: chosen}
        return chosen
    kept = [k for k in remembered.get(kind) or [] if k in allowed]
    return kept or default


def _label_wrap(kind: str) -> bool:
    """Long names and places on two lines instead of cut short."""
    remembered = cookie.get("label_wrap") or {}
    if request.values.get("fields_set") or "wrap" in request.values:
        wrap = request.values.get("wrap") == "1"
        cookie["label_wrap"] = {**remembered, kind: wrap}
        return wrap
    return bool(remembered.get(kind))


# ---- how they come out: a sheet, a label printer's page, or ZPL

def _stock_choice(kind: str) -> str:
    """The label stock asked for, else the one this person last used for this kind."""
    chosen = cookie.get("label_stock") or {}
    stock = request.values.get("stock") or chosen.get(kind) or "sheet"
    stock = stock if stock in STOCKS else "sheet"
    if chosen.get(kind) != stock:
        cookie["label_stock"] = {**chosen, kind: stock}
    return stock


def fit(size: tuple[float, float]) -> dict:
    """Type sizes for a label of this size (mm), and how many rows fit under
    its title beside a square QR code."""
    w, h = size
    pad = round(max(1.0, min(3.5, h * 0.06)), 1)
    qr = round(min(h - 2 * pad, w * 0.42), 1)
    title = round(max(2.0, min(3.8, h * 0.12)), 2)
    row = round(max(1.6, min(2.6, h * 0.075)), 2)
    # Rows are typed at line-height 1.25 with 0.3 mm between them.
    rows = max(0, math.floor((h - 2 * pad - title * 1.15 - 0.4) / (row * 1.25 + 0.3)))
    return {"pad": pad, "qr": qr, "title": title, "row": row, "rows": rows}


def _zpl_text(text: str) -> str:
    """A field's text for ^FH_: ZPL's own characters escaped as hex, and the
    symbols its standard font lacks spelled out."""
    for a, b in (("♀", "F"), ("♂", "M"), ("—", "-"), ("–", "-")):
        text = text.replace(a, b)
    return text.replace("_", "_5F").replace("^", "_5E").replace("~", "_7E")


def _qr_modules(payload: str) -> int:
    try:
        import segno
        return segno.make(payload, error="m").symbol_size(border=0)[0]
    except ImportError:
        # Byte-mode capacity at level M for versions 3 to 10.
        for version, capacity in enumerate((42, 62, 84, 106, 122, 152, 180, 213), start=3):
            if len(payload.encode("utf-8")) <= capacity:
                return 17 + 4 * version
        return 57


def to_zpl(cards: list[dict], size: tuple[float, float], dpi: int = 203, wrap: bool = False) -> str:
    """The labels as ZPL II, one ^XA…^XZ each, for a Zebra of this resolution;
    with `wrap`, a long title or value takes two lines instead of one."""
    dots = 12 if dpi >= 300 else 8        # dots a millimetre
    w, h = round(size[0] * dots), round(size[1] * dots)
    f = fit(size)
    pad = round(f["pad"] * dots)
    title, row = round(f["title"] * dots * 1.25), round(f["row"] * dots * 1.25)
    out = []
    for card in cards:
        z = ["^XA", "^CI28", f"^PW{w}", f"^LL{h}", "^LH0,0"]
        side = 0
        if card.get("target"):
            modules = _qr_modules(card["target"])
            mag = max(1, min(10, round(f["qr"] * dots) // modules))
            side = mag * modules
            z.append(f"^FO{w - pad - side},{pad}^BQN,2,{mag}^FH_^FDMA,{_zpl_text(card['target'])}^FS")
        text_w = max(40, w - 3 * pad - side)
        y = pad
        lines = 2 if wrap else 1
        z.append(f"^FO{pad},{y}^A0N,{title},{title}^FB{text_w},{lines},0,L^FH_^FD{_zpl_text(card['title'])}^FS")
        y += round(title * 1.15) * lines
        for key, value in card["rows"][:f["rows"]]:
            if y + row > h - pad:
                break
            z.append(f"^FO{pad},{y}^A0N,{row},{row}^FB{text_w},1,0,L^FH_^FD{_zpl_text(f'{key}: {value}')}^FS")
            y += round(row * 1.2)
        z.append("^XZ")
        out.append("\n".join(z))
    return "\n".join(out) + "\n"


def printer_address(raw: str) -> tuple[str, int] | None:
    """(host, port) of a label printer on the lab's own network, or None
    when the address is empty, unreadable or out on the internet: the
    server only ever sends labels to a private address."""
    raw = (raw or "").strip()
    if not raw:
        return None
    host, port = raw, ZEBRA_PORT
    if raw.count(":") == 1:
        host, _, digits = raw.partition(":")
        if not digits.isdigit():
            return None
        port = int(digits)
    host = host.strip("[] ")
    try:
        found = {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except (socket.gaierror, UnicodeError, ValueError):
        return None
    tailnet = ipaddress.ip_network("100.64.0.0/10")
    for addr in found:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if not (ip.is_private or ip.is_loopback or ip.is_link_local or (ip.version == 4 and ip in tailnet)):
            return None
    return (host, port) if found else None


def _lab_printer(session) -> tuple[str, int]:
    from .inventory_service import get_setting
    try:
        dpi = int(get_setting(session, PRINTER_DPI_SETTING, "203"))
    except ValueError:
        dpi = 203
    return get_setting(session, PRINTER_SETTING, ""), (300 if dpi >= 300 else 203)


def _page(built: dict):
    stock = _stock_choice(built["kind"])
    size = STOCKS[stock][1] or built["size"]
    with SessionLocal() as session:
        printer, dpi = _lab_printer(session)
    if request.args.get("format") == "zpl":
        try:
            dpi = arg_int("dpi", dpi)
        except ValueError:
            pass
        name = built["kind"].replace("/", "-")
        return Response(to_zpl(built["cards"], size, dpi, wrap=_label_wrap(built["kind"])), mimetype="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{name}-labels.zpl"'})
    cards = built["cards"]
    wrap = _label_wrap(built["kind"])
    layout = fit(size) if stock != "sheet" else None
    left_out: set[str] = set()      # rows that didn't fit, to say so
    for card in cards:
        card["qr"] = _qr_svg(card["target"], scale=3, fit=True) if card.get("target") else ""
        if layout:
            # A title on two lines takes the room of one row.
            room = layout["rows"] - (1 if card.get("shared") else 0) - (1 if wrap and layout["rows"] > 1 else 0)
            left_out.update(key for key, _value in card["rows"][max(1, room):])
            card["rows"] = card["rows"][:max(1, room)]
    ids = ",".join(str(i) for i in _ids())
    # What the page was asked for, for its own links: the ticked rows as one `ids`.
    base_args = {k: v for k, v in request.args.items()
                 if k not in ("stock", "format", "dpi", "selected_ids", "ids", "f", "fields_set", "wrap")}
    if ids:
        base_args["ids"] = ids
    here = url_for(request.endpoint, **(request.view_args or {}), **base_args, stock=stock)
    return render_template(
        "labels/cards.html", cards=cards, heading=built["heading"], size=size, stock=stock, stocks=STOCKS,
        layout=layout, printed_on=date.today().isoformat(), back_url=built["back_url"],
        printer=printer, dpi=dpi, can_set_printer=access.is_admin(), here=here,
        ids=ids, base_args=base_args, kind=built["kind"],
        fields=built.get("fields"), chosen=built.get("chosen") or [], wrap=wrap,
        left_out=sorted(left_out),
    )


def _build(kind: str, key: str = "") -> dict:
    with SessionLocal() as session:
        if kind == "cages":
            return _cage_cards(session)
        if kind == "tanks":
            return _tank_cards(session)
        if kind == "stocks":
            return _stock_cards(session, key)
        if kind == "inventory":
            return _inventory_cards(session, key)
        return _module_cards(session, key)


@bp.route("/cards/cages")
def cage_cards():
    """Printable cards for mouse cages."""
    return _page(_build("cages"))


@bp.route("/cards/tanks")
def tank_cards():
    return _page(_build("tanks"))


@bp.route("/cards/stocks/<key>")
def stock_cards(key: str):
    return _page(_build("stocks", key))


@bp.route("/cards/inventory/<key>")
def inventory_cards(key: str):
    return _page(_build("inventory", key))


@bp.route("/cards/<module_key>")
def module_cards(module_key: str):
    """Printable labels for a configurable organism module's housing units."""
    return _page(_build("module", module_key))


@bp.post("/send")
def send():
    """The labels on the page, as ZPL, to the lab's Zebra on the network."""
    kind, key = request.form.get("kind", ""), ""
    if "/" in kind:
        kind, key = kind.split("/", 1)
    elif kind not in ("cages", "tanks"):
        kind, key = "module", kind
    built = _build(kind, key)
    back = request.form.get("back") or url_for("labels.cage_cards")
    if not back.startswith("/labels/"):
        back = url_for("labels.cage_cards")
    stock = request.form.get("stock") if request.form.get("stock") in STOCKS else "sheet"
    size = STOCKS[stock][1] or built["size"]
    with SessionLocal() as session:
        raw, dpi = _lab_printer(session)
    where = printer_address(raw)
    if where is None:
        flash(gettext("No label printer is set up for the lab yet: an admin adds its address on this page."), "error")
        return redirect(back)
    if not built["cards"]:
        flash(gettext("There are no labels to send."), "error")
        return redirect(back)
    try:
        with socket.create_connection(where, timeout=6) as conn:
            conn.sendall(to_zpl(built["cards"], size, dpi, wrap=_label_wrap(built["kind"])).encode("utf-8"))
    except OSError as exc:
        flash(gettext("The label printer at %(printer)s didn't answer (%(error)s). Check it is on and on the network.",
                      printer=raw, error=exc.strerror or exc), "error")
        return redirect(back)
    n = len(built["cards"])
    flash(ngettext("Sent %(num)s label to the printer at %(printer)s.", "Sent %(num)s labels to the printer at %(printer)s.",
                   n, printer=raw), "success")
    return redirect(back)


@bp.post("/printer")
def set_printer():
    """An admin sets the lab's Zebra: its address on the network, and its resolution."""
    from .inventory_service import set_setting
    if not access.is_admin():
        abort(403)
    back = request.form.get("back") or url_for("labels.cage_cards")
    if not back.startswith("/labels/"):
        back = url_for("labels.cage_cards")
    raw = (request.form.get("address") or "").strip()
    if raw and printer_address(raw) is None:
        flash(gettext("%(printer)s isn't a printer on the lab's own network (a private address such as 192.168.1.50).",
                      printer=raw), "error")
        return redirect(back)
    dpi = "300" if request.form.get("dpi") == "300" else "203"
    with SessionLocal() as session:
        set_setting(session, PRINTER_SETTING, raw)
        set_setting(session, PRINTER_DPI_SETTING, dpi)
        session.commit()
    flash(gettext("The lab's label printer is %(printer)s (%(dpi)s dpi).", printer=raw, dpi=dpi) if raw
          else gettext("The lab's label printer is removed."), "success")
    return redirect(back)
