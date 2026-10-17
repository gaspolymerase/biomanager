"""The mouse sheet's rows, written here rather than by a template.

_mouse_rows.html draws them through `mouse_rows_html` (app.py): in its page
(colony.html) and, for a sheet the server pages, the rows /colony/mice/rows
sends. A Jinja loop drew the first page in a fifth of the page's time; this
writes the same text in a twentieth of that. It is written as the template
was, line for line, so the page is the same to the byte: data-table.js,
cell_text.row_stub (a stub must read as its row) and the text cells
(app.as_text_cells) all read it. Change a row here; the lab's own columns
are still drawn by _sheet.html's custom_cell, and the icons by icon().
"""
from __future__ import annotations

from markupsafe import Markup, escape

from . import i18n, life_stage


def _t(message: str, **values) -> Markup:
    """A template's `_('…', name=value)`: the translation, the values put
    in and escaped (jinja2.ext's newstyle gettext, which always formats)."""
    return Markup(i18n._lookup(message)) % values


def _each(message: str):
    """_t for one message a row at a time: translated once. The values are
    escaped as Markup's % escapes them (a number is its digits)."""
    pattern = str(Markup(i18n._lookup(message)))
    return lambda **values: pattern % {k: v if type(v) is int else escape(v) for k, v in values.items()}


def _menu(chosen, options, blank: str) -> tuple[str, tuple[str, str] | None]:
    """A menu's <option>s with `chosen` selected, as the template's loop
    wrote them: (value, label) pairs after a blank option; and the option a
    text cell shows (app.text_cell), as written: the selected one, else the
    first. None when an option's own words say "selected", which the text
    cell's reading of the menu would take for the chosen one: it reads it."""
    shown = None if chosen else ("", blank)
    plain = True
    out = [f'\n                      <option value="" {"" if chosen else "selected"}>{blank}</option>\n                      ']
    for value, label in options:
        written, words = escape(value), escape(label)
        picked = value == chosen
        if picked and shown is None:
            shown = (str(written), str(words))
        if "selected" in written:
            plain = False
        out.append(f'\n                        <option value="{written}" {"selected" if picked else ""}>'
                   f'{words}</option>\n                      ')
    return "".join(out), (shown or ("", blank)) if plain else None


def html(rows: list[dict], sheet: dict, custom_columns: list[dict], custom_columns_all: list[dict], fragment: bool,
         as_text: bool, *, custom_cell, icon, owner_badge, payload, duplicate_url, delete_url, text_cell,
         text_cells) -> Markup:
    """The rows' <tr>s (`rows`: services.mouse_display_row's, with
    `editable`), or the sheet's empty line. `sheet`: app.mouse_sheet_meta's
    choices. `as_text`: each field's cell drawn as text until used
    (cell_text.text_cells_on), as app.as_text_cells would draw it, each
    field told its attributes rather than read back from the page. The
    others are the template's helpers, passed in."""
    if not rows:
        if fragment:
            return Markup("")
        none = _t("No mice here yet. Use <strong>New mouse</strong>, or <strong>Add many</strong> to import a batch.")
        return Markup(f'\n                <tr><td class="dt-empty" colspan="20">\n                  {none}'
                      f'\n                </td></tr>\n              ')
    alive_label, not_alive = _t("Alive"), _t("Not alive")
    labels = {key: str(_t(text)) for key, text in (
        ("custom_tag", "Custom tag"), ("strain", "Strain"), ("sex", "Sex"), ("w", "w"), ("status", "Status"),
        ("cage", "Cage"), ("rack", "Rack"), ("position", "Position in the rack"), ("owner", "Owner"),
        ("litter", "Litter"), ("dob", "Date of birth"), ("dod", "Date of death"), ("location", "Location note"),
        ("add_note", "Add a note"), ("note", "Note"))}
    transgene_labels = [str(_t("Transgene %(n)s", n=n)) for n in range(1, 5)]
    genders = [(value, _t("U") if value == "Unknown" else i18n.translate_value(value))
               for value in sheet["gender_choices"]]
    statuses = [(value, i18n.translate_value(value)) for value in sheet["status_choices"]]
    racks = [(rack["id"], rack["name"]) for rack in sheet["racks"]]
    stage_titles: dict[str, str] = {}
    menus: dict[tuple, tuple] = {}   # a menu with a value chosen is the same on every row

    def menu(kind: str, chosen, options):
        key = (kind, chosen)
        if key not in menus:
            menus[key] = _menu(chosen, options, "–")
        return menus[key]

    select_mouse, open_mouse = _each("Select mouse %(id)s"), _each("Open mouse %(id)s")
    duplicate_mouse, delete_mouse = _each("Duplicate mouse %(id)s"), _each("Delete mouse %(id)s")
    delete_asks = _each("Delete mouse %(id)s? This can be undone from Batch history only if it was part of a batch.")
    weeks_days = _each("%(weeks)s weeks · %(days)s days")
    read_only, tag_says = _each("%(owner)s’s mouse: read only"), _each("Custom tag %(tag)s")
    rack_menus: dict = {}
    cell_in, cell_out = "\n" + " " * 20, "\n" + " " * 18   # a cell's field on a line of its own

    def td(inside: str, field: str, after: str, attrs: dict, chosen=None) -> str:
        """A field's cell: <td>, the field between the template's spaces,
        or, as text, what app.text_cell makes of it. `attrs`: the field's
        own, as its tag writes them (escaped); `chosen`: a menu's shown
        option (_menu)."""
        if as_text:
            drawn = text_cell("", field, attrs, locked, chosen)
            if drawn is not None:
                return drawn
        return f"<td>{inside}{field}{after}</td>"

    out = []
    for row in rows:
        row_id, number = row["id"], row["mouse_id"]
        f = f"mouse-update-{row_id}"
        locked = not row["editable"]
        off = "disabled" if locked else ""
        active = row["active"]
        days = row["age_days"]
        st = life_stage.stage("mouse", days)
        if st and st not in stage_titles:
            stage_titles[st] = i18n.translate_value(life_stage.describe("mouse", st))
        stage_title = stage_titles.get(st, "")
        ear_tag, owner = row["ear_tag"], row["owner"]
        e_tag, e_owner = escape(ear_tag), escape(owner)
        weeks = row["age_weeks"]
        age_title = weeks_days(weeks=weeks, days=days) if weeks is not None else ""
        age = (f'{escape(weeks)}<span class="sheet-unit">{labels["w"]}</span>' if weeks is not None
               else '<span class="sheet-empty">–</span>')
        lock = (f'<span class="sheet-lock" title="{read_only(owner=owner)}">'
                f'{icon("lock")}</span>' if locked else "")
        tag_title = f' title="{tag_says(tag=ear_tag)}"' if ear_tag else ""
        dot_title = escape(alive_label + (" · " + stage_title if st else "")) if active else not_alive
        transgenes = ""
        for n in range(1, 5):
            value = escape(row[f"transgene_{n}"])
            field = (f'<input name="transgene_{n}" form="{f}" value="{value}"'
                     f'\n                               list="strain-name-options" data-autosave="1" aria-label="{transgene_labels[n - 1]}"'
                     f'\n                               {off}>')
            transgenes += f'\n                    {td("", field, "", {"name": f"transgene_{n}", "form": f, "value": value})}\n                  '

        rack_id = row["cage_rack_id"]
        if rack_id not in rack_menus:
            rack_menus[rack_id] = (
                "".join(f'\n                        <option value="{escape(value)}" {"selected" if value == rack_id else ""}>'
                        f'{escape(name)}</option>\n                      ' for value, name in racks),
                next(((str(escape(value)), str(escape(name))) for value, name in racks if value == rack_id), ("", "–")))
        rack_menu, rack_shown = rack_menus[rack_id]
        own = ""
        for fd in custom_columns:
            cell = f'<td>{custom_cell(fd, row["attrs"].get(fd["key"], ""), f, locked, "owner-name-options")}</td>'
            own += f'\n                    {text_cells(cell, True) if as_text else cell}\n                  '

        dob, dod, litter = row["date_of_birth"], row["date_of_death"], row["litter_id"]
        location, note = escape(row["cage_location"]), escape(row["note"])
        e_dob, e_dod, e_litter = escape(dob), escape(dod), escape(litter)
        e_strain, e_sex = escape(row["strain"]), escape(row["gender"])
        e_status = escape(str(row["status"]).lower())
        e_cage, e_place = escape(row["cage_id"]), escape(row["cage_position"])
        ear_field = (f'<input class="ident" name="ear_tag" form="{f}" value="{e_tag}" maxlength="40"'
                     f'\n                           placeholder="–" data-autosave="1" aria-label="{labels["custom_tag"]}" {off}>')
        ear_attrs = {"class": "ident", "name": "ear_tag", "form": f, "value": e_tag, "placeholder": "–"}
        strain_field = (f'<input name="strain" form="{f}" value="{e_strain}" maxlength="120" list="options-strain"'
                        f'\n                           placeholder="–" data-autosave="1" aria-label="{labels["strain"]}" {off}>')
        strain_attrs = {"name": "strain", "form": f, "value": e_strain, "placeholder": "–"}
        sex_menu, sex_shown = menu("sex", row["gender"], genders)
        sex_field = (f'<select class="sex-pill" name="gender" form="{f}" data-sex="{e_sex}" data-autosave="1"'
                     f'\n                            aria-label="{labels["sex"]}" {off}>{sex_menu}'
                     f'\n                    </select>')
        sex_attrs = {"class": "sex-pill", "name": "gender", "form": f, "data-sex": e_sex}
        status_menu, status_shown = menu("status", row["status"], statuses)
        status_field = (f'<select class="status-pill" name="status" form="{f}" data-status="{e_status}"'
                        f'\n                            data-autosave="1" aria-label="{labels["status"]}" {off}>'
                        f'{status_menu}\n                    </select>')
        status_attrs = {"class": "status-pill", "name": "status", "form": f, "data-status": e_status}
        cage_field = (f'<input class="ident" name="cage_id" form="{f}" value="{e_cage}" placeholder="–"'
                      f'\n                             data-autosave="1" aria-label="{labels["cage"]}" {off}>')
        cage_attrs = {"class": "ident", "name": "cage_id", "form": f, "value": e_cage, "placeholder": "–"}
        rack_field = (f'<select name="cage_rack" form="{f}" data-autosave="1" aria-label="{labels["rack"]}" {off}>'
                      f'\n                      <option value="">–</option>\n                      {rack_menu}'
                      f'\n                    </select>')
        rack_attrs = {"name": "cage_rack", "form": f}
        place_field = (f'<input class="ident" name="cage_position" form="{f}" value="{e_place}" placeholder="–"'
                       f'\n                             data-autosave="1" aria-label="{labels["position"]}" {off}>')
        place_attrs = {"class": "ident", "name": "cage_position", "form": f, "value": e_place, "placeholder": "–"}
        litter_field = (f'<input class="ident" name="litter_id" form="{f}" value="{e_litter}" placeholder="–"'
                        f'\n                             data-autosave="1" aria-label="{labels["litter"]}" {off}>')
        litter_attrs = {"class": "ident", "name": "litter_id", "form": f, "value": e_litter, "placeholder": "–"}
        dob_field = (f'<input type="date" name="date_of_birth" form="{f}" value="{e_dob}" data-empty="{"0" if dob else "1"}"'
                     f'\n                             data-autosave="1" data-original-dob="{e_dob}" data-original-litter="{e_litter}"'
                     f'\n                             aria-label="{labels["dob"]}" {off}>')
        dob_attrs = {"type": "date", "name": "date_of_birth", "form": f, "value": e_dob}
        dod_field = (f'<input type="date" name="date_of_death" form="{f}" value="{e_dod}" data-empty="{"0" if dod else "1"}"'
                     f'\n                             data-autosave="1" aria-label="{labels["dod"]}" {off}>')
        dod_attrs = {"type": "date", "name": "date_of_death", "form": f, "value": e_dod}
        location_field = (f'<input name="cage_location" form="{f}" value="{location}" title="{location}"'
                          f'\n                             list="options-cage_location" data-autosave="1" aria-label="{labels["location"]}" {off}>')
        location_attrs = {"name": "cage_location", "form": f, "value": location, "title": location}
        note_field = (f'<input class="note-cell" name="note" form="{f}" value="{note}" title="{note}" placeholder="{labels["add_note"]}"'
                      f'\n                             data-autosave="1" aria-label="{labels["note"]}" {off}>')
        note_attrs = {"class": "note-cell", "name": "note", "form": f, "value": note, "title": note,
                      "placeholder": labels["add_note"]}
        out.append(f'''
                
                
                <tr data-id="{escape(row_id)}" data-row-form="{f}"
                    data-active="{'true' if active else 'false'}"
                    data-mouse_id="{escape(number)}"
                    data-ear_tag="{e_tag}"
                    data-age="{escape(days) if days is not None else ''}"
                    class="{'' if active else 'is-inactive'}{' is-locked' if locked else ''}">
                  <td class="sheet-pin sheet-pin-0">
                    <input type="checkbox" class="mouse-select" value="{escape(row_id)}" aria-label="{select_mouse(id=number)}">
                  </td>
                  <td class="sheet-pin sheet-pin-1">
                    
                    <span class="sheet-id">
                      <span class="life-dot{' is-alive' if active else ''}"{f' data-stage="{escape(st)}"' if st else ''}
                            data-alive-title="{alive_label}{f' · {escape(stage_title)}' if st else ''}"
                            title="{dot_title}"></span>
                      <span class="ident"{tag_title}>{escape(number)}</span>
                      {lock}
                    </span>
                  </td>
                  {td(cell_in, ear_field, cell_out, ear_attrs)}
                  {td(cell_in, strain_field, cell_out, strain_attrs)}
                  {td(cell_in, sex_field, cell_out, sex_attrs, sex_shown)}
                  <td class="sheet-num" title="{age_title}">
                    {age}
                  </td>
                  {td(cell_in, status_field, cell_out, status_attrs, status_shown)}
                  {transgenes}
                  {td("", cage_field, "", cage_attrs)}
                  {td(cell_in, rack_field, cell_out, rack_attrs, rack_shown)}
                  {td("", place_field, "", place_attrs)}
                  <td>
                    <div class="owner-cell-wrap{' has-badge' if owner else ''}">
                      {owner_badge(owner) if owner else ''}
                      <input name="owner" form="{f}" value="{e_owner}" list="owner-name-options"
                             data-autosave="1" aria-label="{labels['owner']}" {off}>
                    </div>
                  </td>
                  {td("", litter_field, "", litter_attrs)}
                  {td("", dob_field, "", dob_attrs)}
                  {td("", dod_field, "", dod_attrs)}
                  {td("", location_field, "", location_attrs)}
                  {td("", note_field, "", note_attrs)}
                  {own}
                  <td class="sheet-pin sheet-pin-end">
                    <div class="sheet-actions">
                      <button type="button" class="row-icon-btn" title="{open_mouse(id=number)}"
                              data-mouse-edit data-mouse-payload='{payload(row, locked, custom_columns_all)}'>
                        {icon('edit')}
                      </button>
                      <form method="post" action="{escape(duplicate_url(row_id))}">
                        <button type="submit" class="row-icon-btn" title="{duplicate_mouse(id=number)}">{icon('copy')}</button>
                      </form>
                      <form method="post" action="{escape(delete_url(row_id))}"
                            data-confirm="{delete_asks(id=number)}">
                        <button type="submit" class="row-icon-btn danger-button" title="{delete_mouse(id=number)}"
                                {off}>{icon('trash')}</button>
                      </form>
                    </div>
                  </td>
                </tr>
              ''')
    return Markup("".join(out))


# What each row's form carries of its cells as drawn: the server changes only
# a cell that differs, so a stale row cannot undo a colleague's edit.
_WAS = ("gender", "status", "transgene_1", "transgene_2", "transgene_3", "transgene_4", "owner",
        "note", "cage_id", "litter_id", "date_of_birth", "date_of_death")


def forms_html(rows: list[dict], update_url) -> Markup:
    """The rows' forms (row-forms.js), as _mouse_row_forms.html wrote them:
    each row's what-it-showed (…_was), for the update to compare."""
    out = []
    for row in rows:
        was = "".join(
            f'\n              <input type="hidden" name="{name}_was" value="{"" if row[name] is None else escape(row[name])}">'
            f'\n            '
            for name in _WAS)
        out.append(f'''
          <form id="mouse-update-{escape(row["id"])}" method="post" action="{escape(update_url(row["id"]))}"
                data-mouse-label="{escape(row["mouse_id"])}">
            <input type="hidden" name="confirm_cohort_dob_change" value="0">
            
            <input type="hidden" name="cage_rack_was" value="{escape(row["cage_rack_id"] or "")}">
            <input type="hidden" name="cage_position_was" value="{escape(row["cage_position"])}">
            <input type="hidden" name="cage_location_was" value="{escape(row["cage_location"])}">
            
            {was}
          </form>
        ''')
    return Markup("".join(out))
