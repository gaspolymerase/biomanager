/* Rack grid: housing units shown where they physically sit.
 *
 * One component for mouse cages, fly vials, worm plates and fish tanks. The
 * page renders the shell with the `rack_grid` macro (templates/_rack_grid.html)
 * and a JSON payload:
 *
 *   { "racks": [{ "id": 3, "name": "B", "rows": 8, "cols": 10, "group": "Incubator 25", "edit": {...} }],
 *     "items": [{ "id": 12, "label": "C-104", "sub": "DBH-Cre", "badge": "4",
 *                 "tone": "breeder", "flag": false, "rack": 3, "row": 2, "col": 7,
 *                 "search": "…", "edit": { "data-org-edit": "…", … } }],
 *     "create": { "attrs": {…}, "payload": {…}, "rack_field": "location_id_fk",
 *                 "row_field": "row", "col_field": "col", "text_field": null } }
 *
 * A rack's optional "group" (an incubator, a room) fills a first picker,
 * [data-rack-group], that narrows the rack picker to the racks inside it.
 *
 * A position may hold more than one item (mouse cages: a cage set up there
 * before the old one is ended). The cell shows one: the latest placed
 * ("placed", an ISO time) of those that are "live", else the latest. With
 * two or more live there, a "!" on the cell lists them all; each opens,
 * drags and moves like any tile. An item that is "live": false (an empty
 * cage) is left out of the Unplaced tray.
 *
 * Rows and columns are 1-based in the payload. A page whose storage is
 * 0-based (fish tanks) sets data-index-base="0" and this script converts on
 * the way out.
 *
 * Editing on the grid:
 *   · drag a tile to a cell to move it (an occupied cell swaps), or onto the
 *     tray to unplace it — saved immediately through data-move-url;
 *   · or press Move, tap a tile, then tap where it goes (a cell, another
 *     tile to swap, or the tray): dragging doesn't work on a touch screen;
 *   · click a tile to open that record's own edit dialog (its `edit`
 *     attributes are the same data-*-edit hooks the table rows use);
 *   · click an empty cell to create a record already placed there.
 */
(function () {
  'use strict';

  /* Position names — mirrors app/positions.py, so the grid, the sheet and
     the dialogs all call a cell the same thing under each rack's scheme. */
  const DEFAULT_NAMING = { mode: 'grid', rows: 'letters', cols: 'numbers', order: 'row_col', separator: '', start: 1 };
  function naming(raw) {
    const s = Object.assign({}, DEFAULT_NAMING, raw || {});
    s.start = parseInt(s.start, 10) === 0 ? 0 : 1;
    if (s.mode === 'grid' && s.rows === s.cols && !s.separator) s.separator = '-';
    return s;
  }
  function letters(n) {
    let out = '';
    while (n > 0) { const rem = (n - 1) % 26; out = String.fromCharCode(65 + rem) + out; n = Math.floor((n - 1) / 26); }
    return out;
  }
  const axisLabel = (n, style, start) => (style === 'letters' ? letters(n) : String(n - 1 + start));
  function cellLabel(row, col, raw, cols) {
    const s = naming(raw);
    if (s.mode === 'sequential') return String((row - 1) * cols + col - 1 + s.start);
    const r = axisLabel(row, s.rows, s.start);
    const c = axisLabel(col, s.cols, s.start);
    return s.order === 'row_col' ? `${r}${s.separator}${c}` : `${c}${s.separator}${r}`;
  }
  const escapeHtml = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* Click an invisible button carrying data-* hooks, so the page's existing
     delegated listeners (record-dialog.js, orgEdit, the mouse dialog) open
     their own dialog exactly as a table row would. */
  function trigger(attrs) {
    const button = document.createElement('button');
    button.type = 'button';
    button.hidden = true;
    Object.entries(attrs || {}).forEach(([name, value]) => {
      button.setAttribute(name, typeof value === 'string' ? value : JSON.stringify(value));
    });
    document.body.appendChild(button);
    button.click();
    button.remove();
  }

  /* A grid whose payload is not in the page (data-rack-src: an inventory's
     box grid, every tile carrying its item's dialog) asks for it the first
     time its panel is shown, then draws as any other. */
  function setupLater(root) {
    const panel = root.closest('[data-layout-panel]');
    const status = root.querySelector('[data-rack-status]');
    let asked = false;
    const load = async () => {
      if (asked) return;
      asked = true;
      if (status) status.textContent = t('Loading…');
      try {
        const response = await fetch(root.dataset.rackSrc, { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error(t('The server answered %(status)s.', { status: response.status }));
        const node = document.createElement('script');
        node.type = 'application/json';
        node.dataset.rackData = '';
        node.textContent = await response.text();
        root.appendChild(node);
        if (status) status.textContent = '';
        setup(root);
      } catch (error) {
        asked = false;
        if (status) status.textContent = t('Couldn’t load the grid: %(error)s', { error: error.message });
      }
    };
    if (!panel || !panel.hidden) { load(); return; }
    document.addEventListener('layout:change', () => { if (!panel.hidden) load(); });
  }

  function setup(root) {
    const dataNode = root.querySelector('script[data-rack-data]');
    if (!dataNode) {
      if (root.dataset.rackSrc) setupLater(root);
      return;
    }
    let data;
    try { data = JSON.parse(dataNode.textContent); } catch (e) { console.error(e); return; }
    const racks = data.racks || [];
    const items = data.items || [];
    const create = data.create || null;
    const base = parseInt(root.dataset.indexBase || '1', 10);
    const moveUrl = root.dataset.moveUrl || '';
    const fields = {
      rack: root.dataset.rackField || 'rack_id',
      row: root.dataset.rowField || 'row',
      col: root.dataset.colField || 'col',
    };
    const key = `rack:${root.dataset.rackView}:active`;

    const select = root.querySelector('[data-rack-select]');
    const meta = root.querySelector('[data-rack-meta]');
    const grid = root.querySelector('[data-rack-grid]');
    const tray = root.querySelector('[data-rack-tray-items]');
    const trayBox = root.querySelector('[data-rack-tray]');
    const search = root.querySelector('[data-rack-search]');
    const editRack = root.querySelector('[data-rack-edit]');
    const rackAction = root.querySelector('[data-rack-action]');
    const status = root.querySelector('[data-rack-status]');
    const moveButton = root.querySelector('[data-rack-move]');
    // Move mode: tiles are picked up and put down by tapping, not opened.
    let moving = false;
    let picked = null;

    let active = null;
    const rackById0 = (id) => racks.find((r) => r.id === id);
    try { active = parseInt(localStorage.getItem(key) || '', 10) || null; } catch (_) {}
    if (!racks.some((r) => r.id === active)) active = racks[0] ? racks[0].id : null;

    // Incubator (or room) first, when the page offers that picker.
    const groupSelect = root.querySelector('[data-rack-group]');
    const NO_GROUP = '\u0000';
    const groupOf = (rack) => (rack && rack.group) || NO_GROUP;
    const groups = [...new Set(racks.map(groupOf))].sort((a, b) => (a === NO_GROUP) - (b === NO_GROUP) || a.localeCompare(b, undefined, { numeric: true }));
    if (groupSelect) {
      groups.forEach((name) => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name === NO_GROUP ? (root.dataset.noGroupText || t('Not in one')) : name;
        groupSelect.appendChild(option);
      });
      if (!groups.length) {
        groupSelect.disabled = true;
        groupSelect.appendChild(Object.assign(document.createElement('option'), { textContent: '—' }));
      }
    }

    function fillRacks(group) {
      select.innerHTML = '';
      const shown = racks.filter((rack) => !groupSelect || groupOf(rack) === group);
      shown.forEach((rack) => {
        const option = document.createElement('option');
        option.value = rack.id;
        option.textContent = rack.name;
        select.appendChild(option);
      });
      select.disabled = !shown.length;
      if (!shown.length) {
        const option = document.createElement('option');
        option.textContent = t('No racks yet');
        select.appendChild(option);
      }
      return shown;
    }
    if (groupSelect && groups.length) groupSelect.value = groupOf(rackById0(active));
    fillRacks(groupSelect ? groupSelect.value : null);
    if (active) select.value = String(active);

    const say = (state, text) => {
      if (!status) return;
      status.dataset.state = state;
      status.textContent = text;
      if (state === 'saved') setTimeout(() => { if (status.textContent === text) status.textContent = ''; }, 2000);
    };

    const rackById = (id) => racks.find((r) => r.id === id);
    const placedIn = (rack, item) => item.rack === rack.id && item.row >= 1 && item.col >= 1
      && item.row <= rack.rows && item.col <= rack.cols;
    const isPlacedAnywhere = (item) => racks.some((rack) => placedIn(rack, item));
    // Everything at one position, the one the cell shows first: live before
    // not, then the latest placed (mirrors services.slot_order).
    const shownFirst = (a, b) => (!!b.live - !!a.live) || String(b.placed || '').localeCompare(String(a.placed || ''))
      || b.id - a.id;
    const itemsAt = (rack, r, c, except) => items.filter((i) => i !== except && i.rack === rack.id
      && i.row === r && i.col === c).sort(shownFirst);
    // A move puts an item there now: later than anything placed before.
    let moves = 0;
    const justNow = () => `~${String(moves += 1).padStart(6, '0')}`;

    // `where`: the cell's own name ("A1"), shown on a placed tile so a long
    // name doesn't hide which position it is.
    function tile(item, where) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'rack-tile';
      // Someone else's record opens read-only and cannot be dragged.
      el.draggable = !item.locked;
      if (item.locked) el.dataset.locked = 'true';
      el.dataset.id = item.id;
      if (item.tone) el.dataset.tone = String(item.tone).toLowerCase();
      el.title = [where, item.title || [item.label, item.sub].filter(Boolean).join(' · ')].filter(Boolean).join(' · ');
      el.innerHTML = `
        <span class="rack-tile-top">
          <span class="rack-tile-id">${escapeHtml(item.label)}</span>
          ${item.flag ? `<span class="rack-tile-flag" aria-label="${escapeHtml(t('Needs attention'))}"></span>` : ''}
          ${item.badge ? `<span class="rack-tile-badge">${escapeHtml(item.badge)}</span>` : ''}
        </span>
        ${item.sub || where ? `<span class="rack-tile-foot">${item.sub ? `<span class="rack-tile-sub">${escapeHtml(item.sub)}</span>` : ''}${
          where ? `<span class="rack-tile-pos">${escapeHtml(where)}</span>` : ''}</span>` : ''}`;
      el.addEventListener('dragstart', (event) => {
        event.dataTransfer.setData('text/plain', String(item.id));
        event.dataTransfer.effectAllowed = 'move';
        el.classList.add('is-dragging');
      });
      el.addEventListener('dragend', () => el.classList.remove('is-dragging'));
      if (picked === item.id) el.classList.add('is-picked');
      el.addEventListener('click', (event) => {
        if (!moving) { if (item.edit) trigger(item.edit); return; }
        event.stopPropagation();
        tapTile(item);
      });
      return el;
    }

    /* The "!" on a position holding two or more live items: hover or tap it
       for all of them, each a tile that opens, drags or (in Move) is picked
       like the one shown. The list is a popover, so the grid's scrolling
       box doesn't cut it off. */
    const canPop = typeof HTMLElement.prototype.showPopover === 'function';
    const stackOpen = (list) => (canPop ? list.matches(':popover-open') : !list.hidden);
    const closeStack = (list) => {
      if (!stackOpen(list)) return;
      if (canPop) list.hidePopover(); else list.hidden = true;
    };
    document.addEventListener('pointerdown', (event) => {
      if (event.target.closest && event.target.closest('.rack-stack, .rack-stack-toggle')) return;
      root.querySelectorAll('.rack-stack').forEach(closeStack);
    });
    function stack(rack, r, c, all) {
      const where = whereText(rack, r, c);
      const live = all.filter((i) => i.live).length;
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'rack-stack-toggle';
      toggle.textContent = '!';
      toggle.dataset.ids = all.map((i) => i.id).join(' ');
      const said = t('%(where)s holds %(n)s cages with living mice. The rack shows the latest; open the others to move or end them.', { where, n: live });
      toggle.title = said;
      toggle.setAttribute('aria-label', said);
      const list = document.createElement('div');
      list.className = 'rack-stack';
      const head = document.createElement('p');
      head.className = 'rack-stack-head';
      head.textContent = said;
      list.appendChild(head);
      all.forEach((item) => list.appendChild(tile(item)));
      list.addEventListener('click', (event) => { if (event.target.closest('.rack-tile') && !moving) hide(); });
      // Manual: a tap on the "!" toggles it, a tap elsewhere closes it (below).
      if (canPop) list.popover = 'manual';
      else list.hidden = true;
      let timer = null;
      // Opened by hovering, it closes when the mouse leaves; a click pins it.
      let hovered = false;
      const open = () => {
        clearTimeout(timer);
        if (stackOpen(list)) return;
        root.querySelectorAll('.rack-stack').forEach(closeStack);
        if (canPop) list.showPopover(); else list.hidden = false;
        // Under the cell, so the cage shown stays in view (above it near the bottom).
        const box = (toggle.closest('.rack-cell') || toggle).getBoundingClientRect();
        const left = Math.max(8, Math.min(box.left, window.innerWidth - list.offsetWidth - 8));
        const below = box.bottom + 4 + list.offsetHeight <= window.innerHeight;
        list.style.left = `${left}px`;
        list.style.top = `${below ? box.bottom + 4 : Math.max(8, box.top - 4 - list.offsetHeight)}px`;
      };
      function hide() {
        clearTimeout(timer);
        hovered = false;
        closeStack(list);
      }
      const later = () => { clearTimeout(timer); if (hovered) timer = setTimeout(hide, 250); };
      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        if (hovered) { hovered = false; clearTimeout(timer); return; }
        if (stackOpen(list)) hide(); else open();
      });
      // Hovering opens it; a mouse leaving both closes it. A touch screen taps.
      toggle.addEventListener('pointerenter', (event) => {
        if (event.pointerType === 'mouse' && !stackOpen(list)) { open(); hovered = true; }
      });
      toggle.addEventListener('pointerleave', (event) => { if (event.pointerType === 'mouse') later(); });
      list.addEventListener('pointerenter', () => clearTimeout(timer));
      list.addEventListener('pointerleave', (event) => { if (event.pointerType === 'mouse') later(); });
      const holder = document.createDocumentFragment();
      holder.append(toggle, list);
      return holder;
    }

    // The position alone ("D7"); the rack is a separate field.
    const positionText = (rack, row, col) => cellLabel(row, col, rack.naming, rack.cols);
    const whereText = (rack, row, col) => `${rack.name} · ${positionText(rack, row, col)}`;

    function render() {
      const rack = rackById(active);
      grid.innerHTML = '';
      tray.innerHTML = '';
      if (editRack) {
        editRack.hidden = !rack;
        if (rack && rack.edit) Object.entries(rack.edit).forEach(([n, v]) => editRack.setAttribute(n, typeof v === 'string' ? v : JSON.stringify(v)));
      }
      if (rackAction) {
        // A plain form post: the server records it and brings the page back.
        const action = rack && rack.action;
        rackAction.hidden = !action;
        rackAction.innerHTML = '';
        if (action) {
          rackAction.action = action.url;
          Object.entries(action.fields || {}).forEach(([name, value]) => {
            rackAction.appendChild(Object.assign(document.createElement('input'), { type: 'hidden', name, value }));
          });
          const button = Object.assign(document.createElement('button'), { type: 'submit', className: 'btn btn-sm', title: action.title ? t(action.title) : '' });
          button.disabled = !!action.done;
          button.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${escapeHtml(action.icon || 'check')}"></use></svg> ${escapeHtml(action.done ? `${t(action.label)} ✓` : t(action.label))}`;
          rackAction.appendChild(button);
        }
      }
      if (!rack) {
        grid.innerHTML = `<div class="rack-empty">${escapeHtml(root.dataset.emptyText || t('Add a rack to start placing.'))}</div>`;
      } else {
        const occupied = new Set(items.filter((item) => placedIn(rack, item)).map((i) => `${i.row}:${i.col}`)).size;
        if (meta) {
          // A rack may carry a note for this line, e.g. a fly rack's last flip.
          const note = rack.note && rack.note.text
            ? ` · <span class="rack-note ${{ overdue: 'text-danger-700 font-semibold', due: 'text-warn-700 font-semibold' }[rack.note.tone] || ''}" title="${escapeHtml(rack.note.title || '')}">${escapeHtml(rack.note.text)}</span>`
            : '';
          meta.innerHTML = `${rack.rows} × ${rack.cols} · ${escapeHtml(t('%(n)s of %(total)s filled', { n: occupied, total: rack.rows * rack.cols }))}${note}`;
        }
        const scheme = naming(rack.naming);
        const sequential = scheme.mode === 'sequential';
        grid.style.gridTemplateColumns = `28px repeat(${rack.cols}, minmax(84px, 1fr))`;
        grid.appendChild(Object.assign(document.createElement('div'), { className: 'rack-corner' }));
        for (let c = 1; c <= rack.cols; c += 1) {
          grid.appendChild(Object.assign(document.createElement('div'), {
            className: 'rack-col-label', textContent: sequential ? '' : axisLabel(c, scheme.cols, scheme.start) }));
        }
        for (let r = 1; r <= rack.rows; r += 1) {
          grid.appendChild(Object.assign(document.createElement('div'), {
            className: 'rack-row-label', textContent: sequential ? '' : axisLabel(r, scheme.rows, scheme.start) }));
          for (let c = 1; c <= rack.cols; c += 1) {
            const cell = document.createElement('div');
            cell.className = 'rack-cell';
            cell.dataset.row = r;
            cell.dataset.col = c;
            cell.title = whereText(rack, r, c);
            const all = itemsAt(rack, r, c);
            const here = all[0];
            if (here) {
              cell.appendChild(tile(here, positionText(rack, r, c)));
              if (all.filter((i) => i.live).length > 1) cell.appendChild(stack(rack, r, c, all));
            } else {
              // An empty cell shows its own name, so what the sheet calls D7
              // is visibly the D7 here.
              const name = `<span class="rack-cell-name">${escapeHtml(positionText(rack, r, c))}</span>`;
              if (create) {
                const add = document.createElement('button');
                add.type = 'button';
                add.className = 'rack-cell-add';
                add.setAttribute('aria-label', t('New at %(where)s', { where: whereText(rack, r, c) }));
                add.innerHTML = name + '<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#plus"></use></svg>';
                add.addEventListener('click', () => { if (!moving) createAt(rack, r, c); });
                cell.appendChild(add);
              } else {
                cell.insertAdjacentHTML('beforeend', name);
              }
            }
            wireDrop(cell, () => moveTo(cell._dragId, rack, r, c));
            if (!here) cell.addEventListener('click', () => { if (moving) tapPlace(rack, r, c); });
            grid.appendChild(cell);
          }
        }
      }
      // Unplaced: no valid position in any rack. A cage with no living
      // mice ("live": false) has nothing to put on a shelf, so it stays out
      // of the tray; the table still lists it.
      const unplaced = items.filter((item) => !isPlacedAnywhere(item));
      unplaced.filter((item) => item.live !== false).forEach((item) => tray.appendChild(tile(item)));
      if (!tray.children.length) tray.innerHTML = `<div class="rack-tray-empty">${escapeHtml(t('Everything is placed.'))}</div>`;
      if (unplaced.some((item) => item.live === false)) {
        tray.insertAdjacentHTML('beforeend', `<div class="rack-tray-empty">${escapeHtml(t('Empty cages are not listed here; the table shows them.'))}</div>`);
      }
      applySearch();
    }

    function wireDrop(target, onDrop) {
      target.addEventListener('dragover', (event) => { event.preventDefault(); target.classList.add('is-drop-target'); });
      target.addEventListener('dragleave', () => target.classList.remove('is-drop-target'));
      target.addEventListener('drop', (event) => {
        event.preventDefault();
        target.classList.remove('is-drop-target');
        target._dragId = parseInt(event.dataTransfer.getData('text/plain'), 10);
        if (target._dragId) onDrop();
      });
    }
    wireDrop(trayBox, () => moveTo(trayBox._dragId, null, null, null));

    /* Move mode, step by step. The status line says what the next tap does. */
    const pickedItem = () => items.find((i) => i.id === picked);
    function nextStep() {
      if (!moving) return;
      const item = pickedItem();
      say('saving', item
        ? t('Tap where %(item)s goes: an empty position, another tile to swap, or Unplaced.', { item: item.label })
        : t('Tap the tile to move.'));
    }
    function setMoving(on) {
      moving = on;
      picked = null;
      root.classList.toggle('is-moving', on);
      if (moveButton) {
        moveButton.setAttribute('aria-pressed', String(on));
        moveButton.classList.toggle('btn-primary', on);
      }
      render();
      if (on) nextStep(); else say('saved', '');
    }
    function tapTile(item) {
      const holding = pickedItem();
      if (holding && holding.id === item.id) { picked = null; render(); nextStep(); return; }
      const where = item.rack ? rackById(item.rack) : null;
      if (holding && where && placedIn(where, item)) { moveTo(holding.id, where, item.row, item.col); return; }
      if (item.locked) {
        say('error', item.owner
          ? t('%(item)s is %(owner)s\'s; you may not move it.', { item: item.label, owner: item.owner })
          : t('%(item)s is someone else’s; you may not move it.', { item: item.label }));
        return;
      }
      picked = item.id;
      render();
      nextStep();
    }
    function tapPlace(rack, r, c) {
      if (picked) moveTo(picked, rack, r, c);
      else nextStep();
    }
    if (moveButton) moveButton.addEventListener('click', () => setMoving(!moving));
    trayBox.addEventListener('click', (event) => {
      if (!moving || !picked || event.target.closest('.rack-tile')) return;
      moveTo(picked, null, null, null);
    });
    root.addEventListener('keydown', (event) => {
      const lists = [...root.querySelectorAll('.rack-stack')].filter(stackOpen);
      if (event.key === 'Escape' && lists.length) { event.stopPropagation(); lists.forEach(closeStack); return; }
      if (event.key === 'Escape' && moving) { event.stopPropagation(); setMoving(false); }
    });

    function createAt(rack, r, c) {
      const payload = Object.assign({}, create.payload || {});
      if (create.rack_field) payload[create.rack_field] = rack.id;
      if (create.row_field) payload[create.row_field] = r - 1 + base;
      if (create.col_field) payload[create.col_field] = c - 1 + base;
      if (create.text_field) payload[create.text_field] = positionText(rack, r, c);
      // The payload attribute pairs with the dialog hook:
      // data-org-edit → data-org-payload, data-record-edit → data-record-payload.
      const attrs = Object.assign({}, create.attrs || {});
      const hook = Object.keys(attrs).find((name) => name.endsWith('-edit'));
      attrs[hook ? hook.replace(/-edit$/, '-payload') : 'data-record-payload'] = JSON.stringify(payload);
      trigger(attrs);
    }

    async function moveTo(itemId, rack, r, c) {
      const item = items.find((i) => i.id === itemId);
      if (!item) return;
      if (rack && item.rack === rack.id && item.row === r && item.col === c) return;
      const previous = { rack: item.rack, row: item.row, col: item.col };
      // A swap is with the one the cell shows; any others there stay.
      const occupant = rack ? itemsAt(rack, r, c, item)[0] || null : null;
      if (occupant && occupant.locked) {
        say('error', occupant.owner
          ? t('%(where)s holds %(item)s (%(owner)s\'s), which you may not move. Pick an empty position.',
              { where: whereText(rack, r, c), item: occupant.label, owner: occupant.owner })
          : t('%(where)s holds %(item)s, which you may not move. Pick an empty position.',
              { where: whereText(rack, r, c), item: occupant.label }));
        return;
      }
      const body = new FormData();
      body.append(fields.rack, rack ? rack.id : '');
      body.append(fields.row, rack ? r - 1 + base : '');
      body.append(fields.col, rack ? c - 1 + base : '');
      say('saving', t('Saving…'));
      try {
        const response = await fetch(moveUrl.replace('{id}', itemId), {
          method: 'POST', body, headers: { 'X-Autosave': '1' },
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.ok === false) throw new Error(result.error || t('The server answered %(status)s.', { status: response.status }));
        // Mirror the server: the moved item takes the cell, an occupant swaps.
        Object.assign(item, rack ? { rack: rack.id, row: r, col: c, placed: justNow() } : { rack: null, row: null, col: null });
        if (occupant) Object.assign(occupant, previous, { placed: justNow() });
        syncEditPayload(item);
        if (occupant) syncEditPayload(occupant);
        // Let the page update anything else showing these records.
        [item, occupant].filter(Boolean).forEach((moved) => {
          const where = moved.rack ? rackById(moved.rack) : null;
          document.dispatchEvent(new CustomEvent('rack-grid:moved', { detail: {
            view: root.dataset.rackView, id: moved.id, rackId: moved.rack,
            position: where && moved.row ? positionText(where, moved.row, moved.col) : '',
          } }));
        });
        // A swap moves two records: say so, or the second move goes unseen.
        const swapped = occupant
          ? ' · ' + t('%(item)s went to %(where)s', { item: occupant.label,
            where: occupant.rack ? whereText(rackById(occupant.rack), occupant.row, occupant.col) : t('Unplaced') })
          : '';
        picked = null;
        say('saved', (rack ? t('Moved %(item)s to %(where)s', { item: item.label, where: whereText(rack, r, c) })
                           : t('Unplaced %(item)s', { item: item.label })) + swapped);
        render();
      } catch (error) {
        say('error', t('Couldn’t move %(item)s: %(error)s', { item: item.label, error: error.message }));
      }
    }

    /* A tile's edit dialog was filled from the page as it loaded; after a
       move, write the new position into it so the dialog shows where the
       record is now, not where it was. */
    function syncEditPayload(item) {
      if (!item.edit || !create) return;
      const name = Object.keys(item.edit).find((n) => n.endsWith('-payload'));
      if (!name) return;
      let payload;
      try { payload = JSON.parse(item.edit[name]); } catch (_) { return; }
      const rack = item.rack ? rackById(item.rack) : null;
      const placed = rack && item.row && item.col;
      if (create.text_field) payload[create.text_field] = placed ? positionText(rack, item.row, item.col) : '';
      if (create.rack_field && rack) payload[create.rack_field] = rack.id;
      if (create.row_field) payload[create.row_field] = placed ? item.row - 1 + base : '';
      if (create.col_field) payload[create.col_field] = placed ? item.col - 1 + base : '';
      item.edit[name] = JSON.stringify(payload);
    }

    function applySearch() {
      const q = search ? search.value.trim().toLowerCase() : '';
      const byId = new Map(items.map((i) => [String(i.id), i]));   // not a search of the list for every tile
      const hit = (item) => Boolean(q) && Boolean(item)
        && (item.search || `${item.label} ${item.sub || ''}`).toLowerCase().includes(q);
      root.querySelectorAll('.rack-tile').forEach((el) => {
        const found = hit(byId.get(el.dataset.id));
        el.classList.toggle('is-dimmed', Boolean(q) && !found);
        el.classList.toggle('is-match', found);
      });
      // A match hidden under a position's "!" lights the "!".
      root.querySelectorAll('.rack-stack-toggle').forEach((el) => {
        el.classList.toggle('is-match', el.dataset.ids.split(' ').some((id) => hit(byId.get(id))));
      });
    }

    if (groupSelect) {
      groupSelect.addEventListener('change', () => {
        const shown = fillRacks(groupSelect.value);
        active = shown[0] ? shown[0].id : null;
        if (active) select.value = String(active);
        try { localStorage.setItem(key, String(active)); } catch (_) {}
        render();
      });
    }
    select.addEventListener('change', () => {
      active = parseInt(select.value, 10) || null;
      try { localStorage.setItem(key, String(active)); } catch (_) {}
      render();
    });
    if (search) search.addEventListener('input', applySearch);
    // Drawn when first shown: behind the table, a long list's Unplaced tray
    // was thousands of tiles nobody saw.
    const panel = root.closest('[data-layout-panel]');
    if (panel && panel.hidden) {
      const shown = () => {
        if (panel.hidden) return;
        document.removeEventListener('layout:change', shown);
        render();
      };
      document.addEventListener('layout:change', shown);
    } else {
      render();
    }
  }

  /* Table / grid switch, remembered per page:
       <div class="view-switch" data-view-switch="mouse-cages">
         <button data-layout="table" class="dt-chip">…</button>
         <button data-layout="grid" class="dt-chip">…</button>
       </div>
       <div data-layout-panel="table">…</div> <div data-layout-panel="grid" hidden>…</div>
     A third, data-layout="cards", works the same; each change fires
     "layout:change" on the scope. */
  function setupSwitch(switcher) {
    const key = `layout:${switcher.dataset.viewSwitch}`;
    const buttons = Array.from(switcher.querySelectorAll('[data-layout]'));
    const scope = switcher.closest('[data-layout-scope]') || document;
    const panels = Array.from(scope.querySelectorAll('[data-layout-panel]'));
    const apply = (name, save) => {
      if (!buttons.some((b) => b.dataset.layout === name)) name = buttons[0].dataset.layout;
      buttons.forEach((b) => b.setAttribute('aria-pressed', b.dataset.layout === name ? 'true' : 'false'));
      panels.forEach((p) => { p.hidden = p.dataset.layoutPanel !== name; });
      if (save) { try { localStorage.setItem(key, name); } catch (_) {} }
      // A page that fills a panel when it is shown (the cage cards) listens.
      scope.dispatchEvent(new CustomEvent('layout:change', { detail: { layout: name }, bubbles: true }));
    };
    buttons.forEach((b) => b.addEventListener('click', () => apply(b.dataset.layout, true)));
    let saved = null;
    try { saved = localStorage.getItem(key); } catch (_) {}
    apply(saved || buttons[0].dataset.layout, false);
  }

  /* A rack dialog opened for an existing rack shows its Delete button,
     pointed at that rack. */
  document.addEventListener('record-dialog:open', (event) => {
    const dialog = event.target;
    const form = dialog.querySelector('form[data-rack-delete]');
    if (!form) return;
    const { data, isNew } = event.detail;
    form.hidden = isNew;
    if (!isNew) form.action = form.dataset.rackDelete.replace('/0/', `/${data.id}/`);
  }, true);

  /* Rack dialog: show what the chosen scheme names the first, a middle and
     the last position, and hide the row/column options in sequential mode. */
  function setupNamingPreview(fieldset) {
    const form = fieldset.closest('form');
    const read = (name) => (form.elements[name] ? form.elements[name].value : undefined);
    const update = () => {
      const rows = Math.max(1, parseInt(read('rows'), 10) || 8);
      const cols = Math.max(1, parseInt(read('cols'), 10) || 10);
      const s = { mode: read('naming_mode'), rows: read('naming_rows'), cols: read('naming_cols'),
                  order: read('naming_order'), separator: read('naming_separator'), start: read('naming_start') };
      const mid = [Math.min(4, rows), Math.min(7, cols)];
      fieldset.querySelector('[data-naming-example]').textContent =
        [cellLabel(1, 1, s, cols), cellLabel(mid[0], mid[1], s, cols), cellLabel(rows, cols, s, cols)].join(' · ');
      fieldset.querySelectorAll('[data-naming-grid]').forEach((el) => { el.hidden = s.mode === 'sequential'; });
    };
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    form.closest('dialog').addEventListener('record-dialog:open', () => setTimeout(update, 0));
    update();
  }

  /* Rack dialog: a new layout may give the places of what is in the rack
     other names, or leave some out (app/rack_layout.py). Before saving, ask
     the server what would change; if anything would, show every one and
     ask whether each keeps its position name or its cell. Cancel saves
     nothing. The choice goes with the form as "cages". */
  function setupLayoutCheck(form) {
    const ask = document.getElementById(form.dataset.layoutDialog);
    const choice = form.elements.cages;
    if (!ask || !choice) return;
    let asked = false;
    form.closest('dialog').addEventListener('record-dialog:open', () => { choice.value = ''; });
    const send = () => {
      asked = true;
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
      asked = false;
    };
    form.addEventListener('submit', async (event) => {
      const id = form.elements.id ? form.elements.id.value : '';
      if (asked || !id) return;
      event.preventDefault();
      choice.value = '';
      let plan = null;
      try {
        const response = await fetch(form.dataset.layoutPreview.replace('/0/', `/${id}/`), {
          method: 'POST', body: new FormData(form),
        });
        if (response.ok) plan = await response.json();
      } catch (e) { plan = null; }
      // Nothing would change (or no answer: the server refuses a save that
      // needs a choice, saying so).
      if (!plan || !plan.affected || !plan.affected.length) { send(); return; }
      const rows = ask.querySelector('[data-layout-rows]');
      const none = `<span class="rack-layout-none">${escapeHtml(rows.dataset.none)}</span>`;
      rows.innerHTML = plan.affected.map((a) => `<tr><td class="rack-layout-code">${escapeHtml(a.code)}</td>`
        + `<td>${escapeHtml(a.now)}</td><td>${a.keep_names ? escapeHtml(a.keep_names) : none}</td>`
        + `<td>${a.keep_cells ? escapeHtml(a.keep_cells) : none}</td></tr>`).join('');
      ask.querySelector('[data-layout-summary]').textContent = plan.summary || '';
      ask.querySelectorAll('[data-layout-note]').forEach((el) => {
        el.textContent = (plan.notes || {})[el.dataset.layoutNote] || '';
        el.hidden = !el.textContent;
      });
      const pick = ask.querySelector(`input[name="layout_choice"][value="${plan.default}"]`);
      if (pick) pick.checked = true;
      ask.showModal();
    });
    ask.querySelectorAll('[data-layout-cancel]').forEach((button) => button.addEventListener('click', () => ask.close()));
    ask.querySelector('[data-layout-save]').addEventListener('click', () => {
      const picked = ask.querySelector('input[name="layout_choice"]:checked');
      if (!picked) return;
      choice.value = picked.value;
      ask.close();
      send();
    });
  }

  function init() {
    document.querySelectorAll('[data-rack-naming]').forEach(setupNamingPreview);
    document.querySelectorAll('form[data-layout-preview]').forEach(setupLayoutCheck);
    document.querySelectorAll('[data-view-switch]').forEach(setupSwitch);
    document.querySelectorAll('[data-rack-view]').forEach(setup);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
