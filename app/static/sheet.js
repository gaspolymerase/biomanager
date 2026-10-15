/* Sheet autosave: inline editing for every database sheet, the way the
 * mouse sheet works.
 *
 * Opt in on the card:  <section class="data-table-card sheet" data-autosave-sheet>
 *
 * Each row's cells point at a hidden per-row form (form="row-12"); the
 * form's action takes the POST. Cells marked data-autosave="1" save 450 ms
 * after typing stops, or at once on change. The request carries
 * X-Autosave: 1 and expects JSON:
 *
 *   { "ok": true,
 *     "row": { "active": false,               // availability dot
 *              "values": { "position": "D7" } } }  // server's view of cells
 *   { "ok": false, "error": "…" }              // shown in the toolbar
 *
 * values also refresh hidden "<name>_was" copies in the form (the stale-
 * form guard, app/formutil.py).
 *
 * Live availability dot, before the server answers: give the field that
 * decides it one of
 *   data-alive-values="in stock,low"        alive only for these values
 *   data-dead-values="discarded,used up"    alive except for these
 *   data-dead-if-set                        alive only while empty (a date of death)
 * and the row's .life-dot follows it.
 *
 * A block pasted into a cell (a column of readings, a copied range) fills
 * the cells down and across from it, each saved as if typed.
 *
 * Events for page-specific rules: "sheet:change" (before saving; detail
 * {input, form, previous}) and "sheet:saved" (detail {form, body}).
 */
(function () {
  'use strict';

  const timers = new Map();

  function statusEl(card) {
    return (card && card.querySelector('[data-save-status]')) || document.querySelector('[data-save-status]');
  }
  function say(card, state, text) {
    const el = statusEl(card);
    if (!el) return;
    clearTimeout(el._clear);
    el.dataset.state = state;
    el.textContent = text;
    if (state === 'saved') el._clear = setTimeout(() => { el.textContent = ''; el.dataset.state = ''; }, 2400);
  }

  const cellsOf = (form) => document.querySelectorAll(`[form="${form.id}"]`);

  /* The availability dot of a row, from the fields that decide it. */
  function refreshDot(row, activeFromServer) {
    const dot = row && row.querySelector('.life-dot');
    if (!dot) return;
    let alive = true;
    if (typeof activeFromServer === 'boolean') {
      alive = activeFromServer;
    } else {
      row.querySelectorAll('[data-alive-values], [data-dead-values], [data-dead-if-set]').forEach((el) => {
        const value = String(el.value || '').trim().toLowerCase();
        if (el.hasAttribute('data-dead-if-set') && value) alive = false;
        if (el.dataset.aliveValues !== undefined) {
          const ok = el.dataset.aliveValues.toLowerCase().split(',').map((v) => v.trim());
          if (!ok.includes(value)) alive = false;
        }
        if (el.dataset.deadValues !== undefined) {
          const dead = el.dataset.deadValues.toLowerCase().split(',').map((v) => v.trim());
          if (dead.includes(value)) alive = false;
        }
      });
    }
    dot.classList.toggle('is-alive', alive);
    dot.title = alive ? (dot.dataset.aliveTitle || t('Available')) : (dot.dataset.deadTitle || t('Not available'));
    row.dataset.active = alive ? 'true' : 'false';
    row.classList.toggle('is-inactive', !alive);
  }

  async function save(form, card) {
    say(card, 'saving', t('Saving…'));
    const label = form.dataset.recordLabel || t('this change');
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form), headers: { 'X-Autosave': '1' },
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || body.ok === false) throw new Error(body.error || t('The server answered %(status)s.', { status: response.status }));
      const row = body.row || {};
      // What was just saved is what the row now shows (the server's own
      // values below correct it where it changed them).
      cellsOf(form).forEach((el) => {
        const was = el.name && el.type !== 'checkbox' && form.querySelector(`[name="${el.name}_was"]`);
        if (was) was.value = el.value;
      });
      Object.entries(row.values || {}).forEach(([name, value]) => {
        const was = form.querySelector(`[name="${name}_was"]`);
        if (was) was.value = value == null ? '' : value;
        document.querySelectorAll(`[form="${form.id}"][name="${name}"]`).forEach((el) => {
          // A menu holding only its own choice (shell.js) takes its list first,
          // or a value the server set (Low when the quantity fell) shows blank.
          if (el.tagName === 'SELECT' && window.BiomanagerShell) window.BiomanagerShell.fillChoices(el);
          if (el !== document.activeElement && el.type !== 'checkbox') el.value = value == null ? '' : value;
          if (el.type === 'date') el.dataset.empty = el.value ? '0' : '1';
        });
      });
      const anyCell = cellsOf(form)[0];
      const tr = anyCell && anyCell.closest('tr');
      if (tr && 'active' in row) refreshDot(tr, row.active);
      cellsOf(form).forEach((el) => el.closest('td') && el.closest('td').classList.remove('has-error'));
      form.dispatchEvent(new CustomEvent('sheet:saved', { bubbles: true, detail: { form, body } }));
      say(card, 'saved', t('All changes saved'));
    } catch (error) {
      cellsOf(form).forEach((el) => {
        if (el.dataset.dirty === '1' && el.closest('td')) el.closest('td').classList.add('has-error');
        // A refused number goes back to the one it has, or every later save
        // of the row would ask for it again.
        const was = el.dataset.autosaveOn === 'change' && form.querySelector(`[name="${el.name}_was"]`);
        if (was) el.value = was.value;
      });
      say(card, 'error', t('Couldn’t save %(what)s: %(error)s', { what: label, error: error.message }));
    }
  }

  function queue(input, delay) {
    const form = window.BioRowForms.get(input.getAttribute('form'));
    if (!form) return;
    const card = input.closest('[data-autosave-sheet]');
    clearTimeout(timers.get(form.id));
    timers.set(form.id, setTimeout(() => save(form, card), delay));
  }

  // The value a cell had when the page came, for one changed before it was
  // ever focused (a pasted block).
  function startValue(el) {
    if (el.tagName === 'SELECT') {
      const option = Array.from(el.options).find((o) => o.defaultSelected);
      return option ? option.value : '';
    }
    return el.defaultValue || '';
  }

  /* One listener on the sheet for all its cells, not one on each: a long
     sheet has a hundred thousand fields, and cells made later (a hazard
     menu opened) save too. */
  function wire(card) {
    card.addEventListener('paste', (event) => pasteBlock(card, event));
    // A cell of this sheet (not of one inside it: each saves its own).
    const cellOf = (el) => (el && el.matches && el.matches('[data-autosave="1"][form]')
      && el.closest('[data-autosave-sheet]') === card ? el : null);
    // What a cell held before this edit (sheet:change's `previous`).
    const remember = (event) => {
      const el = cellOf(event.target);
      if (el && el.dataset.previous === undefined) el.dataset.previous = el.value;
    };
    card.addEventListener('focusin', remember, true);
    card.addEventListener('pointerdown', remember, true);
    card.addEventListener('input', (event) => {
      const input = cellOf(event.target);
      if (!input) return;
      input.dataset.dirty = '1';
      // data-autosave-on="change": saved when you leave the cell or press
      // Enter, not while typing (a cage's number: "1", "12" and "120" are
      // all numbers other cages may have).
      if (input.tagName !== 'SELECT' && input.type !== 'date' && input.dataset.autosaveOn !== 'change') queue(input, 450);
    });
    card.addEventListener('change', (event) => {
      const input = cellOf(event.target);
      if (!input) return;
      input.dataset.dirty = '1';
      const previous = (input.dataset.previous !== undefined ? input.dataset.previous : startValue(input)) || '';
      if (input.classList.contains('status-pill')) input.dataset.status = input.value.toLowerCase();
      if (input.classList.contains('sex-pill')) input.dataset.sex = input.value;
      if (input.dataset.scopePill !== undefined || input.classList.contains('scope-pill')) {
        input.dataset.scope = input.value === '1' ? 'lab' : (/^g\d+$/.test(input.value) ? 'group' : 'mine');
      }
      const detail = { input, form: window.BioRowForms.get(input.getAttribute('form')), previous };
      const go = input.dispatchEvent(new CustomEvent('sheet:change', { bubbles: true, cancelable: true, detail }));
      input.dataset.previous = input.value;
      if (input.type === 'date') input.dataset.empty = input.value ? '0' : '1';
      refreshDot(input.closest('tr'));
      if (go) queue(input, 0);
    });
  }

  /* Pasting a block into a cell: a column of Nanodrop readings goes down
     the rows shown from that cell, a copied range of a spreadsheet across
     and down, each cell saved as if typed. One value pastes as usual. */
  function editable(td) {
    // A cell drawn as text (static/text-cells.js) becomes its field first.
    const text = td && window.BioTextCells && td.querySelector(':scope > .cell-text');
    if (text) window.BioTextCells.open(text);
    const el = td && td.querySelector('[data-autosave="1"][form]');
    return el && !el.disabled && !el.readOnly ? el : null;
  }

  // A row's long list of choices comes in when first used (BiomanagerShell.fillChoices).
  function choices(select) {
    if (window.BiomanagerShell && window.BiomanagerShell.fillChoices) window.BiomanagerShell.fillChoices(select);
    return Array.from(select.options);
  }

  function fits(select, value) {
    const want = value.trim().toLowerCase();
    return choices(select).some((o) => o.value.toLowerCase() === want || o.text.trim().toLowerCase() === want);
  }

  function put(el, value) {
    if (el.tagName === 'SELECT') {
      const want = value.trim().toLowerCase();
      const option = choices(el).find((o) => o.value.toLowerCase() === want || o.text.trim().toLowerCase() === want);
      if (!option) return false;
      el.value = option.value;
    } else if (el.type === 'date') {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value.trim())) return false;
      el.value = value.trim();
    } else if (el.type === 'checkbox') {
      return false;
    } else {
      el.value = value.trim();
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function pasteBlock(card, event) {
    const start = event.target.closest && event.target.closest('[data-autosave="1"][form]');
    const text = event.clipboardData && event.clipboardData.getData('text/plain');
    if (!start || !text || !/[\t\n]/.test(text.replace(/\r?\n$/, ''))) return;
    const td = start.closest('td');
    const tr = td && td.closest('tr');
    const body = tr && tr.parentElement;
    if (!body) return;
    event.preventDefault();
    const grid = text.replace(/\r/g, '').replace(/\n$/, '').split('\n').map((line) => line.split('\t'));
    // Rows and columns as shown: a filtered-out row or a hidden column is skipped.
    const shown = (el) => !el.hidden && el.offsetParent !== null;
    const rows = Array.from(body.rows).filter(shown);
    const shownCells = (row) => Array.from(row.cells).filter(shown);
    const first = rows.indexOf(tr);
    const column = shownCells(tr).indexOf(td);
    let skipped = 0;
    const filled = [];
    grid.forEach((cells, r) => {
      const row = rows[first + r];
      if (!row) { skipped += cells.filter((v) => v.trim()).length; return; }
      const line = shownCells(row);
      let at = column;
      cells.forEach((value) => {
        // A number that isn't one of a choice column's options goes on to
        // the next column (a Nanodrop "conc ⇥ 260/280" block steps over the
        // unit column); at most two such columns are stepped over.
        let el = editable(line[at]);
        for (let hop = 0; el && el.tagName === 'SELECT' && value.trim() && !fits(el, value) && hop < 2; hop += 1) {
          at += 1;
          el = editable(line[at]);
        }
        if (el && put(el, value)) filled.push(row);
        else if (value.trim()) skipped += 1;
        at += 1;
      });
    });
    const say = window.BiomanagerShell && window.BiomanagerShell.toast;
    // Which rows it went into, first and last, as the sheet shows them, so a
    // list pasted in another order than the rows is seen at once.
    const nameOf = (row) => {
      const el = row && row.querySelector('[name="name"], .cell-text[data-sort-for="name"]');
      return !el ? '' : (el.value !== undefined ? el.value : el.dataset.sortValue || '');
    };
    if (say && filled.length) {
      const a = nameOf(filled[0]); const z = nameOf(filled[filled.length - 1]);
      const pastedRows = new Set(filled).size;
      say((a && z ? t('Pasted into %(n)s rows, %(first)s to %(last)s.', { n: pastedRows, first: a, last: z })
                  : t('Pasted into %(n)s rows.', { n: pastedRows }))
        + (skipped ? ' ' + t(skipped === 1 ? '%(n)s value had no cell to go in.' : '%(n)s values had no cell to go in.', { n: skipped }) : ''));
    } else if (say && skipped) {
      say(t(skipped === 1
        ? '%(n)s pasted value had no cell to go in (past the last row, or not a choice there).'
        : '%(n)s pasted values had no cell to go in (past the last row, or not a choice there).', { n: skipped }));
    }
  }

  window.BioSheet = { refreshDot, save };

  function init() { document.querySelectorAll('[data-autosave-sheet]').forEach(wire); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
