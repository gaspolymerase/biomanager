"""The cage sheet's rows, written here rather than by a template.

_cage_rows.html draws them through `cage_rows_html` (app.py): in its page
(colony.html) and in what /colony/cages/rows sends. Each cage is two rows:
its cells, and its detail row (its mice, and Litter born, Genotyping and
Wean), whose mice have their own cells. A Jinja loop drew a page of them in
half the page's time; this writes the same text in a fraction of that. It
is written as the template was, line for line, so the page is the same to
the byte: data-table.js, cell_text.row_stub (a stub must read as its row:
app.cage_stubs) and the text cells (app.as_text_cells) read it. Change a
row here; the icons are still icon()'s, and the badges owner_badge's.
"""
from __future__ import annotations

from markupsafe import Markup, escape

from . import i18n, life_stage
from .mouse_rows import _each, _t


def _off(locked: bool) -> str:
    return "disabled" if locked else ""


def _lower(value) -> str:
    return str(value or "").lower()


# A purpose pill's colour: the three a lab starts with keep theirs, and the
# lab's own purposes take the next colours in the order of its list
# (Configure → Cage purposes), so no two read alike.
_PURPOSE_TONES = {"breeding": "brand", "breeder": "ok", "experiment": "info"}
_MORE_TONES = ("violet", "warn", "pink", "sky")


def purpose_tones(purposes: list[str]) -> dict[str, str]:
    """Each purpose's tone, by its lower-case name."""
    tones, extra = {}, 0
    for p in purposes:
        low = _lower(p).strip()
        if low in _PURPOSE_TONES:
            tones[low] = _PURPOSE_TONES[low]
        elif low and low not in tones:
            tones[low] = _MORE_TONES[extra % len(_MORE_TONES)]
            extra += 1
    return tones


def html(rows: list[dict], *, fragment: bool, racks: list[dict], purposes: list[str], mouse_sheet: dict,
         end_statuses: list[str], custom_columns_all: list[dict], wean_offset_days, icon, owner_badge,
         payload, json_attr, url_for, id_url, group_choices, as_text: bool, text_cell) -> Markup:
    """The cages' rows (`rows`: app.cage_sheet_row's), or the sheet's empty
    line. `racks`, `purposes`: the Rack and Purpose menus' choices;
    `mouse_sheet`: the mice's Sex and Status choices and which transgene
    columns show. The others are the template's helpers, passed in:
    `payload` the mouse dialog's (app.mouse_payload_json), `json_attr` what
    `|tojson` writes, `group_choices` groups.choices. `as_text`: each
    field's cell drawn as text until used (cell_text.text_cells_on), as
    app.as_text_cells would draw it, each field told its attributes
    (`text_cell`) rather than read back from the page."""
    if not rows:
        if fragment:
            return Markup("")
        none = _t("No cages here yet. Use <strong>New cage</strong>, or type a new cage number on a mouse.")
        return Markup(f'\n                    <tr><td class="dt-empty" colspan="14">{none}</td></tr>\n                  ')
    w = {text: str(_t(text)) for text in (
        "Add a note", "Add existing mouse", "Age", "Alive", "Cage number", "Cage number: type to change it",
        "Card ID", "Genotyping", "Holds living mice", "In the mouse sheet", "Litter", "Litter born",
        "Location note", "Mouse", "No living mice", "No mice in this cage yet.", "No rack", "Not alive", "Note",
        "Open", "Position in the rack", "Purpose", "Rack", "Sex", "Shared", "Someone else", "Status",
        "Strain / genotype", "Strain or genotype", "U", "Wean", "empty", "someone else", "w", "Project group",
        "Owner")}
    owner_word = str(escape(i18n.translate_value("Owner")))
    personal, lab = escape(i18n.translate_value("Personal")), escape(i18n.translate_value("Shared"))
    alive_words = [str(i18n.translate_value(_t("Alive")))]
    not_alive = escape(i18n.translate_value(_t("Not alive")))
    mouse_noun = i18n.translate_value("mouse")
    tg_used = mouse_sheet["tg_used"]
    tg_hidden = ["" if n <= 2 or tg_used[n - 1] else "hidden" for n in range(1, 5)]
    tg_heads = "".join(f'<th data-tg="{n}" {tg_hidden[n - 1]}>{_t("Transgene %(n)s", n=n)}</th>' for n in range(1, 5))
    dead_values = escape(",".join(str(escape(s)) for s in [*end_statuses, "transfer"]))
    genders = [(g, _t("U") if g == "Unknown" else escape(i18n.translate_value(g))) for g in mouse_sheet["gender_choices"]]
    statuses = [(s, escape(i18n.translate_value(s))) for s in mouse_sheet["status_choices"]]
    purpose_words = [(p, escape(p), _lower(p), escape(i18n.translate_value(p, "cage purpose"))) for p in purposes]
    tones = purpose_tones(purposes)
    icons: dict[str, str] = {}

    def ic(name: str) -> str:
        if name not in icons:
            icons[name] = str(icon(name))
        return icons[name]

    select_cage, show_mice = _each("Select cage %(id)s"), _each("Show the mice in cage %(id)s")
    read_only, living_of = _each("%(owner)s’s cage: read only"), _each("%(live)s living of %(total)s: click to show them")
    none_of, pups_of = _each("0 of %(total)s"), _each("Pups of litter %(litter)s, born %(born)s")
    open_cage, print_card = _each("Open cage %(id)s"), _each("Print the card for cage %(id)s")
    cage_named, wean_on = _each("Cage %(id)s"), _each("Wean %(date)s")
    wean_due_title = _t("Wean due (P%(days)s)", days=int(wean_offset_days))
    cage_title = _each('Cage <span class="ident">%(id)s</span>')
    alive_of, alive_only = _each("%(live)s alive of %(total)s"), _each("%(live)s alive")
    genotype_by, read_only_cage = _each("genotype by %(date)s"), _each("Read only: %(owner)s’s cage")
    litter_born, n_pups = _each("Litter born %(day)s"), _each("%(n)s pups")
    add_it = _each("Use <strong>Add existing mouse</strong>, or type %(id)s as a mouse’s cage.")
    sex_of, status_of = _each("Sex of mouse %(id)s"), _each("Status of mouse %(id)s")
    transgene_of, litter_of = _each("Transgene %(n)s of mouse %(id)s"), _each("Litter of mouse %(id)s")
    note_on, open_mouse = _each("Note on mouse %(id)s"), _each("Open mouse %(id)s")
    tag_says, mouse_read_only = _each("Custom tag %(tag)s"), _each("%(owner)s’s %(noun)s: read only")
    add_url, birth_url = id_url("add_mouse_from_cage", "cage_row_id"), id_url("cage_give_birth", "cage_row_id")
    geno_url, card_url = id_url("cage_genotyping", "cage_row_id"), None
    distribute_url, wean_url = id_url("cage_wean_distribute", "cage_row_id"), id_url("cage_wean", "cage_row_id")
    stage_titles: dict[str, str] = {}
    shares: dict[tuple, str] = {}

    def cell(td_attrs: str, before: str, field: str, after: str, attrs: dict, locked: bool) -> str:
        """A cell holding one field: <td>, the field between the template's
        spaces, or, as text, what app.text_cell makes of it. `attrs`: the
        field's own, as its tag writes them (escaped)."""
        if as_text:
            drawn = text_cell(td_attrs, field, attrs, locked)
            if drawn is not None:
                return drawn
        return f"<td{td_attrs}>{before}{field}{after}</td>"

    def owner_cell(value, form_id: str, locked: bool) -> str:
        """_sheet.html's owner_cell, as the macro wrote it."""
        return (f'\n  <div class="owner-cell-wrap{" has-badge" if value else ""}">'
                f'\n    {owner_badge(value) if value else ""}'
                f'\n    <input name="owner" form="{form_id}" value="{escape(value or "")}" list="sheet-owners"'
                f'\n           data-autosave="1" aria-label="{owner_word}" {_off(locked)}>'
                f'\n  </div>\n')

    def sharing_options(value, group_id) -> str:
        """_sharing.html's sharing_options, as the macro wrote it."""
        key = (value, group_id)
        if key not in shares:
            out = (f'<option value="0" {"selected" if value == "0" else ""}>{personal}</option>'
                   f'\n  <option value="1" {"selected" if value == "1" else ""}>{lab}</option>')
            choices = group_choices(current=group_id)
            if choices:
                out += f'\n  <optgroup label="{w["Project group"]}">'
                out += "".join(f'\n    <option value="g{escape(gid)}" {"selected" if value == f"g{gid}" else ""}>'
                               f'{escape(name)}</option>' for gid, name in choices)
                out += "\n  </optgroup>"
            shares[key] = out
        return shares[key]

    def scope_of(value) -> str:
        return "lab" if value == "1" else ("group" if (value or "").startswith("g") else "mine")

    def mouse_row(mouse: dict) -> str:
        """A mouse in the cage's detail row, as the template's inner loop wrote it."""
        mf = f"cage-mouse-update-{mouse['id']}"
        mlocked = not mouse["editable"]
        off = _off(mlocked)
        number = mouse["mouse_id"]
        st = life_stage.stage("mouse", mouse["age_days"])
        if st not in stage_titles:
            stage_titles[st] = i18n.translate_value(i18n.translate_value(life_stage.describe("mouse", st))) if st else ""
        alive_title = escape(" · ".join(x for x in [*alive_words, stage_titles[st]] if x))
        active = mouse["active"]
        ear = mouse["ear_tag"]
        aside = f' title="{tag_says(tag=ear)}"' if ear else ""
        lock = (f'<span class="sheet-lock" title="{mouse_read_only(owner=mouse["owner"] or w["Someone else"], noun=mouse_noun)}">'
                f'{ic("lock")}</span>' if mlocked else "")
        id_cell = (f'\n  \n  \n  \n  <span class="sheet-id">\n    '
                   f'\n      <span class="life-dot{" is-alive" if active else ""}"{f" data-stage={chr(34)}{escape(st)}{chr(34)}" if st else ""}'
                   f'\n            data-alive-title="{alive_title}" data-dead-title="{not_alive}"'
                   f'\n            title="{alive_title if active else not_alive}"></span>'
                   f'\n    \n    \n    <span class="ident"{aside}>{escape(number)}</span>'
                   f'\n    {lock}\n  </span>\n')
        gender = mouse["gender"]
        sex_options = "".join(
            f'\n                                          <option value="{escape(g)}" {"selected" if g == gender else ""}>{label}</option>'
            f'\n                                        ' for g, label in genders)
        status = mouse["status"]
        status_options = "".join(
            f'\n                                          <option value="{escape(s)}" {"selected" if s == status else ""}>{label}</option>'
            f'\n                                        ' for s, label in statuses)
        weeks = mouse["age_weeks"]
        age = (f'{escape(weeks)}<span class="sheet-unit">{w["w"]}</span>' if weeks is not None
               else '<span class="sheet-empty">–</span>')
        transgenes = ""
        for n in range(1, 5):
            value = escape(mouse[f"transgene_{n}"])
            field = (f'<input name="transgene_{n}" form="{mf}" value="{value}" list="strain-name-options"'
                     f'\n                                                 data-autosave="1" aria-label="{transgene_of(n=n, id=number)}" {off}>')
            drawn = cell(f' data-tg="{n}" {tg_hidden[n - 1]}', "", field, "",
                         {"name": f"transgene_{n}", "form": mf, "value": value}, mlocked)
            transgenes += f'\n                                      {drawn}\n                                    '
        note = escape(mouse["note"])
        e_gender, e_status = escape(gender), escape(_lower(status) if status is not None else 'none')
        sex_cell = cell("", "\n                                      ",
                        f'<select class="sex-pill" name="gender" form="{mf}" data-sex="{e_gender}" data-autosave="1"'
                        f'\n                                              aria-label="{sex_of(id=number)}" {off}>'
                        f'\n                                        <option value="" {"" if gender else "selected"}>–</option>'
                        f'\n                                        {sex_options}\n                                      </select>',
                        "\n                                    ",
                        {"class": "sex-pill", "name": "gender", "form": mf, "data-sex": e_gender}, mlocked)
        status_cell = cell("", "\n                                      ",
                           f'<select class="status-pill" name="status" form="{mf}" data-status="{e_status}"'
                           f'\n                                              data-dead-values="{dead_values}"'
                           f'\n                                              data-autosave="1" aria-label="{status_of(id=number)}" {off}>'
                           f'\n                                        <option value="" {"" if status else "selected"}>–</option>'
                           f'\n                                        {status_options}\n                                      </select>',
                           "\n                                    ",
                           {"class": "status-pill", "name": "status", "form": mf, "data-status": e_status,
                            "data-dead-values": dead_values}, mlocked)
        litter = escape(mouse["litter_id"])
        litter_cell = cell("", "", f'<input class="ident" name="litter_id" form="{mf}" value="{litter}" placeholder="–"'
                                   f'\n                                               data-autosave="1" aria-label="{litter_of(id=number)}" {off}>', "",
                           {"class": "ident", "name": "litter_id", "form": mf, "value": litter, "placeholder": "–"}, mlocked)
        note_cell = cell("", "", f'<input class="note-cell" name="note" form="{mf}" value="{note}" title="{note}" placeholder="{w["Add a note"]}"'
                                 f'\n                                               data-autosave="1" aria-label="{note_on(id=number)}" {off}>', "",
                         {"class": "note-cell", "name": "note", "form": mf, "value": note, "title": note,
                          "placeholder": w["Add a note"]}, mlocked)
        return f'''
                                  
                                  
                                  <tr class="{'' if active else 'is-inactive'}{' is-locked' if mlocked else ''}">
                                    
                                    <td>{id_cell}</td>
                                    {sex_cell}
                                    <td class="sheet-num">{age}</td>
                                    {status_cell}
                                    {transgenes}
                                    <td>{owner_cell(mouse["owner"], mf, mlocked)}</td>
                                    {litter_cell}
                                    {note_cell}
                                    <td>
                                      <button type="button" class="row-icon-btn" title="{open_mouse(id=number)}"
                                              data-mouse-edit data-mouse-payload='{payload(mouse, mlocked, custom_columns_all)}'>{ic("edit")}</button>
                                    </td>
                                  </tr>
                                '''

    out = []
    for cage in rows:
        cid, code = cage["id"], cage["cage_id"]
        e_code = escape(code)
        f = f"cage-update-{cid}"
        locked = not cage["can_edit"]
        off = _off(locked)
        active, mine = cage["active"], cage["mine"]
        purpose = cage["purpose"]
        purpose_key = escape(str(purpose or "").strip().lower())
        mice = cage["mice"]
        mouse_ids = " ".join(str(escape(m["mouse_id"])) for m in mice)
        rack_id = cage["rack_id"]
        rack_options = "".join(
            f'\n                            <option value="{escape(r["id"])}" {"selected" if r["id"] == rack_id else ""}>{escape(r["name"])}</option>'
            f'\n                          ' for r in racks)
        chosen = _lower(purpose)
        purpose_options = "".join(
            f'\n                            \n                            <option data-tone="{tones.get(low.strip(), "")}" value="{written}" {"selected" if low == chosen else ""}>{label}</option>'
            f'\n                          ' for _p, written, low, label in purpose_words)
        e_tone = tones.get(chosen.strip(), "")
        live, total = cage["live_count"], cage["total_count"]
        if live:
            count = f'<strong>{escape(live)}</strong> <span class="cage-sexes">{escape(cage["sex_label"])}</span>\n                        '
        elif total:
            count = f'<span class="sheet-empty">{none_of(total=total)}</span>\n                        '
        else:
            count = f'<span class="sheet-empty">{w["empty"]}</span>'
        born = cage["date_give_birth"]
        pups = ""
        if cage["pup_litter"]:
            pups = (f'\n                            <a class="cage-pups" href="{escape(url_for("colony", view="litters", q=cage["pup_litter"]))}"'
                    f'\n                               title="{pups_of(litter=cage["pup_litter"], born=cage["pup_dob"])}">{ic("litter")}{escape(cage["pup_litter"])}</a>'
                    f'\n                          ')
        genotype, auto = cage["genotype_summary"], cage["genotype_auto"]
        share, share_lock = cage["share_value"], cage["share_lock"]
        location, notes = escape(cage["cage_location"]), escape(cage["notes"])
        cage_payload = json_attr(cage["payload"])
        e_card, e_place, e_purpose, e_genotype = (escape(cage["card_id"]), escape(cage["position"]),
                                                  escape(_lower(purpose)), escape(genotype))
        card_cell = cell("", "", f'<input class="ident" name="card_id" form="{f}" value="{e_card}" placeholder="–" title="{e_card}"'
                                 f'\n                                 data-autosave="1" aria-label="{w["Card ID"]}" {off}>', "",
                         {"class": "ident", "name": "card_id", "form": f, "value": e_card, "placeholder": "–",
                          "title": e_card}, locked)
        rack_cell = cell("", "\n                        ",
                         f'<select name="rack_id" form="{f}" data-autosave="1" aria-label="{w["Rack"]}" {off}>'
                         f'\n                          <option value="">–</option>\n                          {rack_options}'
                         f'\n                        </select>', "\n                      ",
                         {"name": "rack_id", "form": f}, locked)
        place_cell = cell("", "", f'<input class="ident" name="position" form="{f}" value="{e_place}" placeholder="–"'
                                  f'\n                                 data-autosave="1" aria-label="{w["Position in the rack"]}" {off}>', "",
                          {"class": "ident", "name": "position", "form": f, "value": e_place, "placeholder": "–"}, locked)
        purpose_cell = cell("", "\n                        ",
                            f'<select class="status-pill" name="purpose" form="{f}" data-status="{e_purpose}" data-tone="{e_tone}"'
                            f'\n                                data-autosave="1" aria-label="{w["Purpose"]}" {off}>'
                            f'\n                          <option value="" {"" if purpose else "selected"}>–</option>'
                            f'\n                          {purpose_options}\n                        </select>', "\n                      ",
                            {"class": "status-pill", "name": "purpose", "form": f, "data-status": e_purpose, "data-tone": e_tone}, locked)
        e_auto, e_title = escape(auto or "–"), escape(genotype or auto)
        genotype_cell = cell("", "", f'<input name="genotype_summary" form="{f}" value="{e_genotype}"'
                                     f'\n                                 placeholder="{e_auto}" title="{e_title}"'
                                     f'\n                                 list="strain-name-options" data-autosave="1" aria-label="{w["Strain or genotype"]}"'
                                     f'\n                                 {off}>', "",
                             {"name": "genotype_summary", "form": f, "value": e_genotype, "placeholder": e_auto,
                              "title": e_title}, locked)
        e_scope, e_lock = scope_of(share), escape(share_lock)
        share_attrs = {"class": "scope-pill", "name": "is_shared", "form": f, "data-scope": e_scope}
        if share_lock:
            share_attrs["title"] = e_lock
        share_cell = cell("", "\n                        \n                        ",
                          f'<select class="scope-pill" name="is_shared" form="{f}" data-autosave="1" aria-label="{w["Shared"]}"'
                          f'\n                                data-scope="{e_scope}" data-share-lock="{e_lock}"'
                          f'\n                                {f"title={chr(34)}{e_lock}{chr(34)}" if share_lock else ""}'
                          f'\n                                {"disabled" if locked or share_lock else ""}>'
                          f'\n                          {sharing_options(share, cage["share_group_id"])}'
                          f'\n                        </select>', "\n                      ",
                          share_attrs, locked or bool(share_lock))
        location_cell = cell("", "", f'<input name="cage_location" form="{f}" value="{location}" title="{location}"'
                                     f'\n                                 list="options-cage_location" data-autosave="1" aria-label="{w["Location note"]}" {off}>', "",
                             {"name": "cage_location", "form": f, "value": location, "title": location}, locked)
        notes_cell = cell("", "", f'<input class="note-cell" name="notes" form="{f}" value="{notes}" title="{notes}" placeholder="{w["Add a note"]}"'
                                  f'\n                                 data-autosave="1" aria-label="{w["Note"]}" {off}>', "",
                          {"class": "note-cell", "name": "notes", "form": f, "value": notes, "title": notes,
                           "placeholder": w["Add a note"]}, locked)
        lock = (f'<span class="sheet-lock" title="{read_only(owner=cage["owner"] or w["Someone else"])}">{ic("lock")}</span>'
                if locked else "")
        owner = cage["owner"]
        search = " ".join(str(x) for x in [code, cage["card_id"], cage["rack_name"], cage["position"], purpose, owner,
                                           genotype, auto, cage["cage_location"], cage["notes"]] if x).lower()
        rack_name, position = cage["rack_name"], cage["position"]
        if rack_name:
            where = f" {escape(rack_name)}" + (f" · {escape(position)}" if position else "")
        elif cage["cage_location"]:
            where = f" {location}"
        else:
            where = f" {w['No rack']}"
        head = ""
        head += (f'<span class="status-pill-static" data-status="{escape(_lower(purpose))}" data-tone="{tones.get(_lower(purpose).strip(), "")}">'
                 f'{escape(i18n.translate_value(purpose, "cage purpose"))}</span>' if purpose else "")
        badge = owner_badge(owner) if owner else ""
        geno_span = (f'\n                              <span class="cage-card-geno" title="{w["Strain / genotype"]}">{escape(genotype or auto)}</span>'
                     f'\n                            ' if genotype or auto else "")
        wean_span = (f'\n                              <span class="cage-wean" data-state="{escape(cage["wean_state"])}" title="{wean_due_title}">'
                     f'{ic("calendar-clock")} {wean_on(date=cage["wean_due"])}</span>\n                            '
                     if cage["wean_due"] else "")
        litters = cage["litters"]
        so_far = alive_of(live=live, total=total) if total else alive_only(live=live)
        if cage["genotyping_date"] and len(litters) < 2:
            so_far = f"{so_far} · {genotype_by(date=cage['genotyping_date'])}"
        if locked:
            tools = (f'\n                              <span class="cage-detail-lock">{ic("lock")} '
                     f'{read_only_cage(owner=owner or w["someone else"])}</span>\n                            ')
        else:
            litters_json = escape(str(json_attr(litters)))
            tools = f'''
                              <button type="button" class="btn btn-sm" data-add-existing-mouse
                                      data-action="{escape(add_url(cid))}" data-target-label="{e_code}">
                                {ic("plus")} {w["Add existing mouse"]}
                              </button>
                              <span class="breeding-actions {'' if cage['can_breed'] else 'hidden-row'}" data-breeding-actions>
                                <button type="button" class="btn btn-sm" data-litter-born
                                        data-action="{escape(birth_url(cid))}"
                                        data-cage-label="{e_code}" data-born="{escape(born)}"
                                        data-litters="{escape(str(json_attr(cage['open_litters'])))}">
                                  {ic("baby")} {w["Litter born"]}
                                </button>
                                <button type="button" class="btn btn-sm" data-genotyping
                                        data-action="{escape(geno_url(cid))}"
                                        data-father="{escape(cage['default_father'])}" data-mother="{escape(cage['default_mother'])}"
                                        data-litters="{litters_json}">
                                  {ic("dna")} {w["Genotyping"]}
                                </button>
                                <button type="button" class="btn btn-sm" data-wean
                                        data-distribute-action="{escape(distribute_url(cid))}"
                                        data-wean-action="{escape(wean_url(cid))}"
                                        data-target-label="{e_code}"
                                        data-born="{escape(cage['wean_born'])}" data-age="{escape(cage['wean_age'])}"
                                        data-pups="{escape(str(json_attr(cage['wean_pups'])))}"
                                        data-litters="{litters_json}">
                                  {ic("litter")} {w["Wean"]}
                                </button>
                              </span>
                            '''
        in_sheet = (f'\n                              <a class="btn btn-sm btn-ghost" href="{escape(url_for("colony", view="mice", scope="all", q=code))}">'
                    f'{ic("table")} {w["In the mouse sheet"]}</a>\n                            ' if mice else "")
        two = ""
        if len(litters) > 1:
            spans = "".join(
                f'\n                                <span class="cage-wean" data-state="{escape(lt["wean_state"])}">{ic("baby")}'
                f'\n                                  {litter_born(day=lt["label"])} · P{escape(lt["age"])}'
                + (f' · {n_pups(n=lt["pups"])}' if lt["pups"] else "")
                + (f' · {escape(lt["litter"])}' if lt["litter"] else "")
                + f'\n                                  · {wean_on(date=lt["wean_due"])} · {genotype_by(date=lt["genotyping_due"])}</span>'
                  f'\n                              ' for lt in litters)
            two = (f'\n                            \n                            <div class="flex flex-wrap gap-x-4 gap-1" data-cage-litters>'
                   f'\n                              {spans}\n                            </div>\n                          ')
        if mice:
            body = (f'\n                            \n                            <table class="cage-mice-table" data-tg-table>'
                    f'\n                              <thead>\n                                <tr>'
                    f'\n                                  <th>{w["Mouse"]}</th><th>{w["Sex"]}</th><th>{w["Age"]}</th><th>{w["Status"]}</th>'
                    f'\n                                  {tg_heads}'
                    f'\n                                  <th>{w["Owner"]}</th><th>{w["Litter"]}</th><th>{w["Note"]}</th>'
                    f'<th><span class="sr-only">{w["Open"]}</span></th>'
                    f'\n                                </tr>\n                              </thead>\n                              <tbody>'
                    f'\n                                {"".join(mouse_row(m) for m in mice)}\n                              </tbody>'
                    f'\n                            </table>\n                          ')
        else:
            body = (f'\n                            <p class="cage-detail-empty">{w["No mice in this cage yet."]}'
                    f'{" " + str(add_it(id=code)) if not locked else ""}</p>\n                          ')
        out.append(f'''
                    
                    
                    <tr id="cage-{cid}" data-id="{cid}" data-cage-row-id="{cid}" data-cage_id="{e_code}"
                        data-active="{'true' if active else 'false'}"
                        data-breeding="{escape(cage['breeding'])}" data-mine="{'true' if mine else 'false'}"
                        data-purpose="{purpose_key}"
                        data-card_id="{escape(cage['card_id'])}" data-rack_name="{escape(rack_name)}" data-mice="{escape(live)}"
                        data-wean_due="{escape(cage['wean_due'])}"
                        data-mouse-ids="{mouse_ids}"
                        data-genotypes="{escape(auto)}"
                        class="is-expanded{'' if active else ' is-inactive'}{' is-locked' if locked else ''}">
                      <td class="sheet-pin sheet-pin-0">
                        <input type="checkbox" data-select-row value="{cid}" aria-label="{select_cage(id=code)}">
                      </td>
                      <td class="sheet-pin sheet-pin-1 cage-id-cell">
                        <button type="button" class="cage-expand" data-cage-expand="{cid}" aria-expanded="true"
                                aria-controls="cage-detail-{cid}" title="{show_mice(id=code)}"
                                aria-label="{show_mice(id=code)}">{ic("chevron-right")}</button>
                        
                        <span class="sheet-id">
                          <span class="life-dot{' is-alive' if active else ''}"
                                data-alive-title="{w['Holds living mice']}" data-dead-title="{w['No living mice']}"
                                title="{w['Holds living mice'] if active else w['No living mice']}"></span>
                          <input class="ident cage-number" name="cage_id" form="{f}" value="{e_code}" required maxlength="80"
                                 data-autosave="1" data-autosave-on="change" aria-label="{w['Cage number']}" title="{w['Cage number: type to change it']}"
                                 {off}>
                          {lock}
                        </span>
                        
                        <input type="hidden" data-alive-values="1" value="{'1' if active else '0'}" data-cage-alive>
                      </td>
                      {card_cell}
                      {rack_cell}
                      {place_cell}
                      {purpose_cell}
                      <td class="sheet-num cage-mice-count" data-cage-open="{cid}"
                          title="{living_of(live=live, total=total)}">
                        {count}
                      </td>
                      <td>
                        <div class="cage-born-cell">
                          <input type="date" name="date_give_birth" form="{f}" value="{escape(born)}"
                                 data-empty="{'0' if born else '1'}" data-autosave="1" data-cage-born
                                 aria-label="{w['Litter born']}" {off}>
                          {pups}
                        </div>
                      </td>
                      <td class="tabular-nums"><span class="cage-wean" data-cage-wean data-state="{escape(cage['wean_state'])}">{escape(cage['wean_due'] or '–')}</span></td>
                      {genotype_cell}
                      <td>{owner_cell(owner, f, locked or not cage['can_reassign'])}</td>
                      {share_cell}
                      {location_cell}
                      {notes_cell}
                      <td class="sheet-pin sheet-pin-end">
                        <div class="sheet-actions">
                          <button type="button" class="row-icon-btn" title="{open_cage(id=code)}"
                                  data-record-edit="cage-dialog" data-record-payload='{cage_payload}'>{ic("edit")}</button>
                          <a class="row-icon-btn" href="{escape(url_for('labels.cage_cards', ids=cid))}" target="_blank"
                             title="{print_card(id=code)}">{ic("print")}</a>
                        </div>
                      </td>
                    </tr>
                    
                    <tr class="cage-detail" id="cage-detail-{cid}" data-detail-for="{cid}">
                      <td colspan="15">
                        <article class="cage-detail-body{' is-locked' if locked else ''}" aria-label="{cage_named(id=code)}"
                                 data-cage-card="{cid}" data-active="{'true' if active else 'false'}"
                                 data-breeding="{escape(cage['breeding'])}" data-mine="{'true' if mine else 'false'}"
                                 data-purpose="{purpose_key}"
                                 data-search="{escape(search)} {escape(mouse_ids.lower())}">
                          
                          <div class="cage-card-head">
                            <span class="cage-card-where">{ic("location")}{where}</span>
                            {head}
                            {badge}
                            {geno_span}
                            {wean_span}
                            <span class="cage-detail-spacer"></span>
                            <button type="button" class="row-icon-btn" title="{open_cage(id=code)}"
                                    data-record-edit="cage-dialog" data-record-payload='{cage_payload}'>{ic("edit")}</button>
                            <a class="row-icon-btn" href="{escape(url_for('labels.cage_cards', ids=cid))}" target="_blank"
                               title="{print_card(id=code)}">{ic("print")}</a>
                          </div>
                          <div class="cage-detail-tools">
                            <span class="cage-detail-title">{ic("cage")} {cage_title(id=code)}
                              <span class="text-muted">· {so_far}</span></span>
                            {tools}
                            <span class="cage-detail-spacer"></span>
                            {in_sheet}
                          </div>
                          {two}
                          {body}
                        </article>
                      </td>
                    </tr>
                  ''')
    return Markup("".join(out))
