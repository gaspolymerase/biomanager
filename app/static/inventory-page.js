/* An inventory's sheet, board and grid kept in step after a save (inventory/module.html).
 * The database's words for an item are in #inventory-page-data. */
const INV_WORDS = JSON.parse(document.getElementById('inventory-page-data').textContent);
/* Keep everything that shows an item in step after a save, a board drag
   or a grid move: the row's cells and its "_was" copies, the dot, the
   Open button's and the board card's payloads, the board column. The
   autosave itself is static/sheet.js. */
(function () {
  const page = JSON.parse(document.getElementById('inv-page-data').textContent);
  const board = document.querySelector('[data-board]');
  // A row may wait off the page until it is shown (data-table.js, BioSheets).
  const rowOf = (id) => window.BioSheets.row(id, document.querySelector('.data-table-card'));
  const cellsOf = (id, name) => {
    const tr = rowOf(id);
    return tr ? tr.querySelectorAll(`[form="inv-${id}"]${name ? `[name="${name}"]` : ''}`) : [];
  };
  const openOf = (id) => rowOf(id)?.querySelector('[data-open-item]') || null;

  function recount() {
    if (!board) return;
    board.querySelectorAll('.board-col').forEach((col) => {
      col.querySelector('[data-board-count]').textContent = col.querySelectorAll('.board-card').length;
    });
  }

  /* The Open and per-status chips count rows as they are now. */
  function recountChips() {
    const rows = window.BioSheets.rows(document.querySelector('.data-table-card'));
    const show = (chip, n) => { const count = chip.querySelector('.dt-chip-count'); if (count) count.textContent = n; };
    document.querySelectorAll('[data-count-status]').forEach((chip) => {
      show(chip, rows.filter((tr) => tr.dataset.status === chip.dataset.countStatus).length);
    });
    const open = document.querySelector('[data-count-open]');
    if (open) show(open, rows.filter((tr) => tr.dataset.open === 'true').length);
  }

  function payloadOf(id) {
    const open = openOf(id) || document.querySelector(`.board-card[data-id="${id}"]`);
    try { return JSON.parse((open && open.dataset.recordPayload) || '{}'); } catch (_) { return {}; }
  }

  /* A received order: offer to add it to stock, in the inventory its
     category points to: an antibody to Antibodies, a virus to Viruses, a
     consumable or equipment to the lab's own list (Custom list), the rest to
     Reagents. */
  const OFFER_KIND = { antibody: 'antibodies', virus: 'viruses', reagent: 'reagents', chemical: 'chemicals' };
  function offerStock(id) {
    const dialog = document.getElementById('stock-offer-dialog');
    if (!dialog) return;
    const form = dialog.querySelector('form');
    const item = payloadOf(id);
    form.action = form.dataset.action.replace('/0/', `/${id}/`);
    dialog.querySelector('[data-offer-name]').textContent = item.name || item._label || t('The order');
    const category = String(item.category || '').toLowerCase();
    const target = dialog.querySelector('[data-offer-target]');
    const options = Array.from(target.options);
    // A list of the lab's own named for the category ("Consumables" or
    // "耗材" for a consumable: data-category, from the server) first.
    const named = !OFFER_KIND[category] && category && options.find((o) => o.dataset.kind === 'custom'
      && (o.dataset.category || '').split('|').includes(category));
    const want = OFFER_KIND[category] || (['consumable', 'equipment'].includes(category) ? 'custom' : 'reagents');
    const option = named || options.find((o) => o.dataset.kind === want)
      || options.find((o) => o.dataset.kind === 'reagents');
    if (option) target.value = option.value;
    dialog.showModal();
  }
  BioActions.register({ 'offer-stock': offerStock });

  function setPayload(id, payload) {
    const json = JSON.stringify(payload);
    [openOf(id), ...document.querySelectorAll(`.board-card[data-id="${id}"]`)]
      .forEach((el) => { if (el) el.dataset.recordPayload = json; });
  }

  function editPayload(id, change) {
    const open = openOf(id) || document.querySelector(`.board-card[data-id="${id}"]`);
    if (!open) return;
    let payload;
    try { payload = JSON.parse(open.dataset.recordPayload || '{}'); } catch (_) { return; }
    setPayload(id, Object.assign(payload, change));
  }

  /* The server's view of a row: cells, "_was" copies, pills, dot, lock. */
  function applyRow(id, answer) {
    const form = BioRowForms.get(`inv-${id}`);
    const row = answer.row || {};
    // A cell drawn as text (static/text-cells.js) whose value the server
    // changed becomes its field, to take the new one.
    const drawn = window.BioTextCells && rowOf(id);
    if (drawn) Object.entries(row.values || {}).forEach(([name, value]) => {
      drawn.querySelectorAll(`.cell-text[data-sort-for="${name}"]`).forEach((span) => {
        if ((span.dataset.sortValue || '') !== (value == null ? '' : String(value))) window.BioTextCells.open(span);
      });
    });
    Object.entries(row.values || {}).forEach(([name, value]) => {
      const v = value == null ? '' : String(value);
      if (form) {
        const was = form.querySelector(`[name="${name}_was"]`);
        if (was) was.value = v;
      }
      cellsOf(id, name).forEach((el) => {
        if (el.tagName === 'SELECT' && window.BiomanagerShell) window.BiomanagerShell.fillChoices(el);   // its list first (shell.js)
        if (el !== document.activeElement) el.value = v;
        el.dataset.previous = el.value;
        if (el.type === 'date') el.dataset.empty = el.value ? '0' : '1';
        if (el.classList.contains('status-pill')) el.dataset.status = el.value.toLowerCase();
        if (el.classList.contains('scope-pill')) el.dataset.scope = el.value === '1' ? 'lab' : 'mine';
      });
    });
    const tr = rowOf(id);
    if (tr && 'active' in row && window.BioSheet) window.BioSheet.refreshDot(tr, row.active);
    if (tr && row.values) {
      const toStock = tr.querySelector('[data-to-stock]');
      if (toStock && answer.payload) {
        toStock.hidden = String(row.values.status || '').toLowerCase() !== 'received' || answer.payload._locked;
      }
      if ('is_shared' in row.values) tr.dataset.shared = row.values.is_shared && row.values.is_shared !== '0' ? 'true' : 'false';
      if ('owner' in row.values) tr.dataset.mine = row.values.owner === page.me ? 'true' : 'false';
      if ('status' in row.values) {
        const status = String(row.values.status || '').toLowerCase();
        tr.dataset.status = status;
        tr.dataset.open = page.openStatuses.includes(status) ? 'true' : 'false';
        recountChips();
      }
    }
    if (answer.payload) {
      setPayload(id, answer.payload);
      if (answer.payload._locked && tr) {
        tr.classList.add('is-locked');
        cellsOf(id).forEach((el) => { el.disabled = true; });
        tr.querySelectorAll('.cell-text').forEach((span) => { span.classList.add('is-disabled'); span.removeAttribute('tabindex'); });
      }
    }
    // A status changed in the sheet moves the card on the board too.
    const card = board && board.querySelector(`.board-card[data-id="${id}"]`);
    const status = row.values && row.values.status;
    if (card && status !== undefined && card.closest('.board-col').dataset.status !== status) {
      const col = Array.from(board.querySelectorAll('.board-col')).find((c) => c.dataset.status === status);
      if (col) { col.querySelector('[data-board-drop]').prepend(card); recount(); }
    }
  }
  window.InventoryPage = { applyRow, offerStock };

  /* A batch change done in place (selection-bar.js; the rows themselves
     are redrawn, their chips recounted, by data-table.js): the type tabs
     take the counts the answer brings, and the board and the box grid,
     drawn from the rows as they were, come again when next shown. */
  document.addEventListener('selbar:done', (event) => {
    const answer = (event.detail && event.detail.answer) || {};
    if (answer.tabs) {
      document.querySelectorAll('[data-type-tabs] .dt-chip').forEach((chip) => {
        const count = chip.querySelector('.dt-chip-count');
        const n = chip.dataset.typeTab !== undefined ? answer.tabs[chip.dataset.typeTab] : (chip.getAttribute('href') ? answer.tabs.all : undefined);
        if (count && n !== undefined) count.textContent = n;
      });
    }
    if (board && boardAsked) boardAsked = null;
    document.querySelectorAll('[data-rack-src]').forEach((root) => root.dispatchEvent(new CustomEvent('rack-grid:stale')));
  });

  document.addEventListener('sheet:saved', (event) => {
    const { form, body } = event.detail || {};
    if (!form || !body) return;
    const id = form.id.replace('inv-', '');
    applyRow(id, body);
    if (body.offer_stock) offerStock(id);
  });

  // A grid move changes the box and position of the item (and of the one
  // it swapped with); the row must not post the old ones back.
  document.addEventListener('rack-grid:moved', (event) => {
    const { id, rackId, position } = event.detail || {};
    applyRow(id, { row: { values: { rack_id: rackId || '', position: position || '' } } });
    editPayload(id, { rack_id: rackId || '', position: position || '' });
  });

  /* Board: drag a card to another status; click to open it. Its cards
     come when it is first shown (inventory_routes.board). */
  let boardAsked = null;
  function showBoard() {
    if (!board || boardAsked) return;
    boardAsked = fetch(board.dataset.boardSrc, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((html) => { board.innerHTML = html; wireBoard(); }, () => {
        boardAsked = null;   // asked again the next time it is shown
        const say = window.BiomanagerShell && window.BiomanagerShell.toast;
        if (say) say(t('Some rows could not be loaded. Check the connection and try again.'), 'error');
      });
  }
  document.addEventListener('layout:change', (event) => { if (event.detail && event.detail.layout === 'board') showBoard(); });
  if (board && !board.closest('[data-layout-panel]').hidden) showBoard();

  function wireBoard() {
    let dragged = null;
    board.querySelectorAll('.board-card').forEach((card) => {
      card.addEventListener('dragstart', (e) => { dragged = card; card.classList.add('is-dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', card.dataset.id); });
      card.addEventListener('dragend', () => { card.classList.remove('is-dragging'); dragged = null; });
    });
    board.querySelectorAll('.board-col').forEach((col) => {
      const drop = col.querySelector('[data-board-drop]');
      col.addEventListener('dragover', (e) => { e.preventDefault(); col.classList.add('is-drop-target'); });
      col.addEventListener('dragleave', () => col.classList.remove('is-drop-target'));
      col.addEventListener('drop', async (e) => {
        e.preventDefault();
        col.classList.remove('is-drop-target');
        if (!dragged || dragged.closest('.board-col') === col) return;
        const card = dragged;
        const from = card.parentElement;
        drop.prepend(card);
        recount();
        const body = new FormData();
        body.append('status', col.dataset.status);
        const response = await fetch(board.dataset.statusUrl.replace('/0/', `/${card.dataset.id}/`), { method: 'POST', body, headers: { 'X-Autosave': '1' } });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.ok === false) {
          from.prepend(card);
          recount();
          BioDialog.alert(result.error || t('Could not move it.'));
          return;
        }
        applyRow(card.dataset.id, result);
        if (result.offer_stock) offerStock(card.dataset.id);
      });
    });
  }
})();

/* Item dialog: suggest source IDs from the chosen colony; keep an old
   status the list no longer has; owner and sharing only for those who
   may change them. */
(function () {
  const page = JSON.parse(document.getElementById('inv-page-data').textContent);
  const dialog = document.getElementById('item-dialog');
  const kind = dialog.querySelector('[data-source-kind]');
  const ref = dialog.querySelector('[data-source-ref]');
  /* The ID box offers the identifiers of whichever colony is chosen —
     in the dialog, and in every source cell in the sheet. */
  let sourceRefs = null;   // fetched once, the first time an ID box is used
  const sourceList = (id) => {
    if (!document.getElementById(id)) {
      const datalist = document.createElement('datalist');
      datalist.id = id;
      document.body.append(datalist);
      if (!sourceRefs) {
        sourceRefs = page.sourcesUrl
          ? fetch(page.sourcesUrl, { credentials: 'same-origin' })
            .then((r) => (r.ok ? r.json() : Promise.reject(r.status))).then((data) => data.refs)
          : Promise.resolve([]);
        sourceRefs.catch(() => { sourceRefs = null; });   // asked again next time
      }
      sourceRefs.then((refs) => {
        (refs[Number(id.replace('inv-src-', ''))] || []).forEach((value) => {
          const option = document.createElement('option');
          option.value = value;
          datalist.append(option);
        });
      }, () => datalist.remove());
    }
    return id;
  };
  const pair = (from) => {
    const box = from.closest('.source-cell, label, fieldset, .record-dialog-body');
    const to = box && box.querySelector('[data-source-ref]');
    if (!to) return;
    const list = from.selectedOptions[0] && from.selectedOptions[0].dataset.list;
    if (list) to.setAttribute('list', sourceList(list)); else to.removeAttribute('list');
  };
  const sync = () => { if (kind && ref) pair(kind); };
  document.addEventListener('change', (event) => {
    const from = event.target.closest && event.target.closest('[data-source-kind]');
    if (from) pair(from);
  });
  // A row's ID box gets its colony's list when it is used, not every row's as the page loads.
  document.addEventListener('focusin', (event) => {
    const to = event.target.closest && event.target.closest('.source-cell [data-source-ref]');
    const from = to && to.closest('.source-cell').querySelector('[data-source-kind]');
    if (from) pair(from);
  });
  dialog.addEventListener('record-dialog:open', (event) => {
    const { data, isNew } = event.detail || {};
    if (kind && ref) sync();
    const status = dialog.querySelector('[data-dialog-status]');
    if (status) {
      status.querySelectorAll('[data-extra]').forEach((o) => o.remove());
      if (data.status && !Array.from(status.options).some((o) => o.value === data.status)) {
        const extra = new Option(data.status, data.status, true, true);
        extra.dataset.extra = '1';
        status.add(extra);
      }
    }
    const fixed = !data._locked && data._manage === false;
    const owner = dialog.querySelector('[data-dialog-owner]');
    const shared = dialog.querySelector('[data-dialog-shared]');
    if (owner) {
      owner.readOnly = isNew && !page.isAdmin;
      if (fixed) owner.disabled = true;
    }
    if (shared && fixed) shared.disabled = true;
  });
})();

/* Item dialog, new only: How many / pasted names. Pasted names set the
   count and stand in for the name field. */
(function () {
  const dialog = document.getElementById('item-dialog');
  const form = dialog.querySelector('form');
  const count = form.querySelector('[data-add-count]');
  const names = form.querySelector('[data-add-names]');
  const name = form.elements.name;
  const nameLabel = form.querySelector('[data-name-label]');
  const sub = dialog.querySelector('[data-next-number]');
  const span = dialog.querySelector('[data-number-span]');
  const submit = dialog.querySelector('[data-record-submit]');
  const noun = INV_WORDS.noun, plural = INV_WORDS.nouns;
  let nameRequired = false;
  let isNew = true;
  const sync = () => {
    const pasted = names.value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean).length;
    if (pasted) count.value = pasted;
    count.readOnly = pasted > 0;
    nameLabel.hidden = isNew && pasted > 0;
    name.required = nameRequired && !(isNew && pasted > 0);
    const k = Math.max(1, Number(count.value) || 1);
    const first = Number(sub.dataset.nextNumber);
    span.textContent = k > 1 ? `#${first}–#${first + k - 1}` : `#${first}`;
    if (isNew) submit.textContent = k > 1 ? t('Create %(n)s %(nouns)s', { n: k, nouns: plural }) : t('Create %(noun)s', { noun });
  };
  count.addEventListener('input', sync);
  names.addEventListener('input', sync);
  dialog.addEventListener('record-dialog:open', (event) => {
    isNew = Boolean((event.detail || {}).isNew);
    // Required to add one; an old one that never had it can still be saved.
    form.querySelectorAll('[data-required]').forEach((el) => { el.required = isNew || el.value.trim() !== ''; });
    nameRequired = name.required;
    form.querySelectorAll('[data-new-only]').forEach((el) => { el.hidden = !isNew; });
    dialog.querySelectorAll('.record-dialog-head [data-new-only]').forEach((el) => { el.hidden = !isNew; });
    sync();
  });
})();

/* Item dialog, new only: pick a name or catalog # used before and the
   empty fields fill in from that entry (the newest one), tinted until
   edited. "Order again" opens with a hint of where it came from. */
(function () {
  const dialog = document.getElementById('item-dialog');
  const form = dialog.querySelector('form');
  const hint = form.querySelector('[data-fill-hint]');
  const fills = JSON.parse(document.getElementById('inv-fill-data').textContent || '{}');
  let isNew = false;
  const labelOf = (el) => {
    // "Vendor", whether or not it is wrapped with a required star.
    const label = el.closest('label');
    const head = label && label.firstElementChild && label.firstElementChild.tagName === 'SPAN'
      ? label.firstElementChild : label;
    return ((head && head.firstChild && head.firstChild.textContent) || el.name).trim();
  };
  const say = (text, warn) => { hint.textContent = text; hint.hidden = !text; hint.classList.toggle('is-warning', Boolean(warn)); };
  function fillFrom(input) {
    if (!isNew) return;
    const found = (fills[input.dataset.fillKey] || {})[input.value.trim().toLowerCase()];
    if (!found) return;
    const filled = [];
    Array.from(form.elements).forEach((el) => {   // in the order the form shows them
      const value = el.name && !el.name.startsWith('_') ? found[el.name] : '';
      if (!value || el === input || el.disabled || el.readOnly || el.value !== '') return;
      if (el.tagName === 'SELECT' && !Array.from(el.options).some((o) => o.value === value)) return;
      el.value = value;
      el.classList.add('is-filled');
      filled.push(labelOf(el));
    });
    if (!filled.length) return;
    const list = filled.length > 1
      ? t('%(first)s and %(last)s', { first: filled.slice(0, -1).join(t(', ')), last: filled[filled.length - 1] })
      : filled[0];
    say(t('Filled %(fields)s from %(from)s.', { fields: list, from: found._from }));
  }
  form.querySelectorAll('[data-fill-key]').forEach((input) => {
    // A pick from the suggestions arrives as an input without a typed character.
    input.addEventListener('input', (e) => { if (!e.inputType || e.inputType === 'insertReplacementText') fillFrom(input); });
    input.addEventListener('change', () => fillFrom(input));
  });
  form.addEventListener('input', (e) => e.target.classList && e.target.classList.remove('is-filled'));
  dialog.addEventListener('record-dialog:open', (event) => {
    const { data, isNew: fresh } = event.detail || {};
    isNew = Boolean(fresh);
    form.querySelectorAll('.is-filled').forEach((el) => el.classList.remove('is-filled'));
    say((data && data._hint) || '', data && data._warn);
  });
})();

/* Links that open something once: ?reorder= (a new order prefilled from
   stock), ?offer= (add a received order to stock), ?open= (a record's
   dialog, e.g. the reagent just made from an order). They leave the
   address, so a reload or the next save does not open them again. */
window.addEventListener('load', () => {
  const params = new URLSearchParams(window.location.search);
  const once = {};
  ['reorder', 'offer', 'open', 'from_order', 'shared'].forEach((k) => { if (params.has(k)) { once[k] = params.get(k); params.delete(k); } });
  if (!Object.keys(once).length) return;
  const rest = params.toString();
  history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash);
  const reorder = document.querySelector('[data-reorder-open]');
  if (reorder) reorder.click();
  else if (once.offer && /^\d+$/.test(once.offer)) window.InventoryPage.offerStock(once.offer);
  else if (once.open && /^\d+$/.test(once.open)) {
    // Its row is brought onto the page first: the dialog opens from a click there.
    const row = window.BioSheets.row(once.open, document.querySelector('.data-table-card'));
    if (row) window.BioSheets.show(row);
    window.BioSheets.ready(row).then((tr) => {
      const open = tr && tr.querySelector('[data-open-item]');
      if (open) open.click();
    });
  }
});

/* ?q=… (from global search or the home page) searches the sheet on load. */
window.addEventListener('load', () => {
  const q = new URLSearchParams(window.location.search).get('q');
  if (!q) return;
  const tableTab = document.querySelector('[data-view-switch] [data-layout="table"]');
  if (tableTab) tableTab.click();
  const all = document.querySelector('.data-table-card .dt-chip[data-dt-filter=""]');
  if (all && all.getAttribute('aria-pressed') !== 'true') all.click();
  const search = document.querySelector('.data-table-card .dt-search');
  if (search) {
    search.value = q;
    search.dispatchEvent(new Event('input', { bubbles: true }));
  }
});
