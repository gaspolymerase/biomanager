"""Utilities: the bench calculators (static/bench-calcs.js) with the lab's own
molecular weights in their chemical picker, the lab's centrifuge rotors for
rpm ↔ × g, and the calculators a lab makes for itself (lab tools), which
their maker or an admin may change.

These pages hang on the application object from app/flask_app.py and keep
the endpoint names every page links to ("utilities", ...);
tests/test_url_map.py holds them to it. They take what every page needs
from app/web.py and never import app.py, which imports them.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import date

from flask import g, jsonify, render_template, request
from sqlalchemy import select

from .db import SessionLocal
from . import access
from .i18n import gettext
from .models import ChemicalReference
from . import lab_notebook
from .inventory_service import get_setting, set_setting
from .flask_app import app
from .web import login_required


@app.route("/utilities")
@login_required
def utilities():
    """The bench calculators (static/bench-calcs.js); the lab's own
    molecular weights come first in their chemical picker: its Chemicals
    database (each by name, and by abbreviation too), then its older list."""
    with SessionLocal() as db_session:
        chemicals = []
        for c in lab_notebook.chemicals_search(db_session, "", limit=2000):
            if c["mw"]:
                chemicals.append({"name": c["name"], "mw": c["mw"], "notes": c["cas"]})
                if c["abbr"] and c["abbr"].lower() != c["name"].lower():
                    chemicals.append({"name": c["abbr"], "mw": c["mw"], "notes": c["name"]})
        chemicals += [{"name": c.name, "mw": c.molecular_weight, "notes": c.notes}
                      for c in db_session.scalars(select(ChemicalReference).order_by(ChemicalReference.name))]
        rotors = _utility_rotors(db_session)
        lab_tools = _lab_tools(db_session)
    return render_template("utilities.html", chemicals=chemicals, rotors=rotors, lab_tools=lab_tools,
                           rotors_editable=g.user.expires_at is None)


UTILITY_ROTORS_KEY = "utilities_rotors"


def _utility_rotors(db_session) -> list[dict]:
    """The lab's centrifuge rotors for rpm ↔ × g: a name, the radius to the
    bottom of the tube (cm) and the top speed (rpm, 0 when not given)."""
    try:
        rows = json.loads(get_setting(db_session, UTILITY_ROTORS_KEY, "[]") or "[]")
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict)]


@app.route("/utilities/rotors", methods=["POST"])
@login_required
def utilities_rotors():
    """Save the lab's rotors (Utilities, rpm ↔ × g). Anyone in the lab may;
    a guest may not."""
    if g.user.expires_at is not None:
        return jsonify({"error": gettext("Guests can't change the lab's rotors.")}), 403
    data = request.get_json(silent=True) or {}
    rows = data.get("rotors")
    if not isinstance(rows, list) or len(rows) > 40:
        return jsonify({"error": gettext("Up to 40 rotors.")}), 400
    rotors = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()[:80]
        try:
            radius = float(row.get("radius") or 0)
            top = float(row.get("max") or 0)
        except (TypeError, ValueError):
            radius, top = 0.0, 0.0
        if not name or not 0.5 <= radius <= 60 or not 0 <= top <= 300000:
            return jsonify({"error": gettext("Each rotor needs a name and a radius between 0.5 and 60 cm.")}), 400
        rotors.append({"name": name, "radius": round(radius, 2), "max": round(top)})
    with SessionLocal() as db_session:
        set_setting(db_session, UTILITY_ROTORS_KEY, json.dumps(rotors, ensure_ascii=False))
        db_session.commit()
    return jsonify({"rotors": rotors})


# The lab's own tools in Utilities: each a name, inputs (a name used in
# formulas, a label, a unit, an example) and answers (a label, a formula
# over the names, a unit). The formulas are read by static/bench-calcs.js,
# never run as code; here they are only kept to the characters a formula
# may hold.
LAB_TOOLS_KEY = "utilities_lab_tools"
_LAB_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,23}$")
_LAB_FORMULA = re.compile(r"^[A-Za-z0-9_\s.+\-*/^(),×÷−]{1,300}$")


def _lab_tools(db_session) -> list[dict]:
    try:
        rows = json.loads(get_setting(db_session, LAB_TOOLS_KEY, "[]") or "[]")
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def _lab_tool_from(data) -> tuple[dict | None, str]:
    """A tool as typed, cleaned; or None and what is wrong with it."""
    if not isinstance(data, dict):
        return None, gettext("That tool could not be read.")
    text = lambda key, most: str(data.get(key) or "").strip()[:most]  # noqa: E731
    name = text("name", 80)
    if not name:
        return None, gettext("Give the tool a name.")
    inputs, outputs = [], []
    for row in (data.get("inputs") or [])[:12]:
        if not isinstance(row, dict) or not str(row.get("name") or "").strip():
            continue
        key = str(row["name"]).strip()
        if not _LAB_NAME.match(key):
            return None, gettext("An input's name is letters, digits and _, starting with a letter: %(name)s", name=key[:24])
        inputs.append({"name": key, "label": str(row.get("label") or "").strip()[:60],
                       "unit": str(row.get("unit") or "").strip()[:20], "value": str(row.get("value") or "").strip()[:30]})
    for row in (data.get("outputs") or [])[:8]:
        if not isinstance(row, dict) or not str(row.get("formula") or "").strip():
            continue
        formula = str(row["formula"]).strip()
        key = str(row.get("name") or "").strip()
        if not _LAB_FORMULA.match(formula):
            return None, gettext("A formula holds names, numbers, + − × ÷ ^ and brackets: %(formula)s", formula=formula[:40])
        if key and not _LAB_NAME.match(key):
            return None, gettext("An input's name is letters, digits and _, starting with a letter: %(name)s", name=key[:24])
        try:
            digits = max(1, min(8, int(row.get("digits") or 4)))
        except (TypeError, ValueError):
            digits = 4
        outputs.append({"label": str(row.get("label") or "").strip()[:60], "formula": formula,
                        "unit": str(row.get("unit") or "").strip()[:20], "name": key, "digits": digits})
    if not inputs or not outputs:
        return None, gettext("A tool needs at least one input and one answer.")
    return {"name": name, "does": text("does", 160), "note": text("note", 300), "keys": text("keys", 200),
            "inputs": inputs, "outputs": outputs}, ""


def _may_change_lab_tool(tool: dict) -> bool:
    return tool.get("author") == g.user.username or access.is_admin()


@app.route("/utilities/lab-tools", methods=["POST"])
@login_required
def utilities_lab_tool_save():
    """Make a lab tool, or change one (its maker, or an admin)."""
    if g.user.expires_at is not None:
        return jsonify({"error": gettext("Guests can use the lab's tools but not make them.")}), 403
    data = request.get_json(silent=True) or {}
    tool, problem = _lab_tool_from(data.get("tool"))
    if tool is None:
        return jsonify({"error": problem}), 400
    with SessionLocal() as db_session:
        tools = _lab_tools(db_session)
        wanted = str((data.get("tool") or {}).get("id") or "")
        old = next((t for t in tools if t["id"] == wanted), None) if wanted else None
        if wanted and old is None:
            return jsonify({"error": gettext("That tool was deleted.")}), 404
        if old is not None:
            if not _may_change_lab_tool(old):
                return jsonify({"error": gettext("Only the person who made a tool, or an admin, can change it.")}), 403
            tool.update(id=old["id"], author=old.get("author", ""))
            tools[tools.index(old)] = tool
        else:
            if len(tools) >= 200:
                return jsonify({"error": gettext("Up to 200 lab tools.")}), 400
            tool.update(id=f"lab{secrets.token_hex(4)}", author=g.user.username)
            tools.append(tool)
        tool["updated"] = date.today().isoformat()
        set_setting(db_session, LAB_TOOLS_KEY, json.dumps(tools, ensure_ascii=False))
        db_session.commit()
    return jsonify({"tools": tools, "id": tool["id"]})


@app.route("/utilities/lab-tools/<tool_id>/delete", methods=["POST"])
@login_required
def utilities_lab_tool_delete(tool_id):
    with SessionLocal() as db_session:
        tools = _lab_tools(db_session)
        tool = next((t for t in tools if t["id"] == tool_id), None)
        if tool is None:
            return jsonify({"tools": tools})
        if g.user.expires_at is not None or not _may_change_lab_tool(tool):
            return jsonify({"error": gettext("Only the person who made a tool, or an admin, can change it.")}), 403
        tools.remove(tool)
        set_setting(db_session, LAB_TOOLS_KEY, json.dumps(tools, ensure_ascii=False))
        db_session.commit()
    return jsonify({"tools": tools})
