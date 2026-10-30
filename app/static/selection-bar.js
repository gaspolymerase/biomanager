/* Selection action bar.
 *
 * Tick rows, a bar rises with everything you can do to them. Replaces the
 * previous arrangement where the row checkboxes fed exactly one hard-coded
 * function (bulk sac) and every other batch action needed its own screen.
 *
 * Markup contract — no JavaScript changes needed to add an action:
 *
 *   <div data-selection-scope="mice">
 *     <input type="checkbox" class="mouse-select" value="12">   … per row
 *     <input type="checkbox" data-select-all>                   … optional
 *
 *     <div class="selbar" data-selection-bar hidden>
 *       <span data-selection-count></span>
 *       <form method="post" action="..." data-selection-form>…</form>
 *     </div>
 *   </div>
 *
 * Every form marked data-selection-form gets the checked ids injected as
 * repeated `selected_ids` fields on submit.
 */
(function () {
  'use strict';

  const ROW_SELECTOR = '.mouse-select, [data-select-row]';

  function setup(scope) {
    const bar = scope.querySelector('[data-selection-bar]');
    if (!bar) return;

    const countEl = bar.querySelector('[data-selection-count]');
    const noun = scope.dataset.selectionNoun || t('record');
    const nounPlural = scope.dataset.selectionNounPlural || (scope.dataset.selectionNoun ? noun + 's' : t('records'));

    const boxes = () => Array.from(scope.querySelectorAll(ROW_SELECTOR))
      .filter((box) => box.type === 'checkbox');
    const checked = () => boxes().filter((box) => box.checked);

    function refresh() {
      const selected = checked();
      bar.hidden = selected.length === 0;
      if (bar.hidden) bar.dispatchEvent(new CustomEvent('selbar:hidden'));
      if (countEl) {
        countEl.textContent = selected.length === 1
          ? t('1 %(noun)s selected', { noun: noun })
          : t('%(n)s %(noun)s selected', { n: selected.length, noun: nounPlural });
      }
      // Rows read as selected, so it is obvious what an action will hit.
      boxes().forEach((box) => {
        const row = box.closest('tr');
        if (row) row.classList.toggle('is-selected', box.checked);
      });
      const all = scope.querySelector('[data-select-all]');
      if (all) {
        const total = boxes().length;
        all.checked = total > 0 && selected.length === total;
        all.indeterminate = selected.length > 0 && selected.length < total;
      }
    }

    scope.addEventListener('change', (event) => {
      if (event.target.matches('[data-select-all]')) {
        const value = event.target.checked;
        // Only rows currently on screen — a hidden row is filtered out, and
        // acting on what you cannot see is how people lose data.
        boxes().forEach((box) => {
          const row = box.closest('tr');
          if (row && row.offsetParent === null) return;
          box.checked = value;
        });
        refresh();
        return;
      }
      if (event.target.matches(ROW_SELECTOR)) refresh();
    });

    // Shift-click extends a range, as in any file manager.
    let anchor = null;
    scope.addEventListener('click', (event) => {
      if (!event.target.matches(ROW_SELECTOR)) return;
      const all = boxes();
      const index = all.indexOf(event.target);
      if (event.shiftKey && anchor !== null && anchor !== index) {
        const [from, to] = index < anchor ? [index, anchor] : [anchor, index];
        for (let i = from; i <= to; i += 1) all[i].checked = event.target.checked;
        refresh();
      }
      anchor = index;
    });

    const clearAll = () => {
      boxes().forEach((box) => { box.checked = false; });
      refresh();
    };
    bar.querySelectorAll('[data-selection-clear]').forEach((button) => {
      button.addEventListener('click', clearAll);
    });

    anyField(scope, bar, checked);
    bar.selbarForms = Array.from(bar.querySelectorAll('form[data-selection-form]'));
    capsule(bar, checked);
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || bar.hidden || event.defaultPrevented) return;
      const target = event.target;
      if (target && target.closest && target.closest('input:not([type=checkbox]), select, textarea, [contenteditable], dialog[open], .menu')) return;
      if (document.querySelector('dialog[open]')) return;
      if ((bar.selbarPop && !bar.selbarPop.hidden) || (bar.selbarMore && !bar.selbarMore.hidden)) { closePanel(bar); return; }
      clearAll();
    });

    // Inject the ids at submit time rather than keeping a hidden field in
    // sync, so the form can never be a step behind the checkboxes.
    bar.selbarForms.forEach((form) => {
      form.addEventListener('submit', (event) => {
        const ids = checked().map((box) => box.value);
        if (!ids.length) {
          event.preventDefault();
          return;
        }
        // A choice that needs a reason (an experiment started with a plan,
        // whose groups this changes): ask for it, then submit again with it.
        const asking = form.querySelector('select option:checked[data-ask-reason]');
        if (asking && form.dataset.reasoned !== '1') {
          event.preventDefault();
          const submitter = event.submitter;
          window.BioDialog.prompt(asking.dataset.askReason, '', { placeholder: t('A reason, in a line'), okLabel: t('Save with this reason') }).then((text) => {
            if (text === null || !String(text).trim()) return;
            let field = form.querySelector('input[name="reason"]');
            if (!field) {
              field = document.createElement('input');
              field.type = 'hidden';
              field.name = 'reason';
              form.appendChild(field);
            }
            field.value = String(text).trim();
            form.dataset.reasoned = '1';
            form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
          });
          return;
        }
        delete form.dataset.reasoned;
        const confirmText = form.dataset.confirm;
        if (confirmText && form.dataset.confirmed !== '1') {
          // Ask in the app, then submit again with the same button.
          event.preventDefault();
          const question = confirmText.replace('{n}', ids.length);
          const submitter = event.submitter;
          window.BioDialog.confirm(question, { danger: window.BioDialog.looksDestructive(question) }).then((ok) => {
            if (!ok) return;
            form.dataset.confirmed = '1';
            form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
          });
          return;
        }
        delete form.dataset.confirmed;
        // A form may do the work itself (Set a field… on a column set row by
        // row): it answers false, and nothing is posted.
        if (typeof form.selbarBefore === 'function' && form.selbarBefore(ids, event) === false) {
          event.preventDefault();
          return;
        }
        form.querySelectorAll('input[name="selected_ids"]').forEach((el) => el.remove());
        ids.forEach((id) => {
          const field = document.createElement('input');
          field.type = 'hidden';
          field.name = 'selected_ids';
          field.value = id;
          form.appendChild(field);
        });
      });
    });

    refresh();
  }

  /* The bar as one capsule, as in the design: "2 selected", then each
   * action as a button with its key beside it, ··· for the rest, and × to
   * let go. An action that asks for something (a field and its value, a
   * rack, a group) opens its form in a small glass panel above the bar; one
   * that asks nothing (Sac, Delete) runs, after its question if it has one.
   *
   * Each form says, if it likes:
   *   data-selbar-label="Move to cage"   the button's name (else its submit button's)
   *   data-key="M"                       a key that opens it while rows are ticked
   *   data-selbar-more                   under ··· rather than on the bar
   */
  const make = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const SVG = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;

  function closePanel(bar) {
    if (bar.selbarPop) bar.selbarPop.hidden = true;
    if (bar.selbarMore) bar.selbarMore.hidden = true;
    bar.querySelectorAll('.selbar-act[aria-expanded="true"]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
  }

  function capsule(bar, checked) {
    if (bar.dataset.capsule === '1') return;
    bar.dataset.capsule = '1';
    bar.classList.add('selbar-capsule');
    const forms = bar.selbarForms;
    const clear = bar.querySelector('[data-selection-clear]');
    const panel = make('div', 'selbar-pop');
    panel.hidden = true;
    const line = make('div', 'selbar-line');
    const moreMenu = make('div', 'menu selbar-more-menu');
    moreMenu.setAttribute('role', 'menu');
    moreMenu.hidden = true;
    const keys = {};
    // Fixed to the window above the bar, so a short sheet's card never clips it.
    const place = (node, align) => {
      const r = bar.getBoundingClientRect();
      node.style.bottom = (window.innerHeight - r.top + 10) + 'px';
      if (align === 'end') {
        node.style.left = 'auto';
        node.style.right = Math.max(8, window.innerWidth - r.right) + 'px';
      } else {
        const w = node.offsetWidth;
        node.style.left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 8)) + 'px';
      }
    };

    const open = (form, launcher) => {
      const was = !panel.hidden && panel.dataset.for === form.dataset.selbarId;
      closePanel(bar);
      if (was) return;
      Array.from(panel.children).forEach((f) => { f.hidden = f !== form; });
      panel.dataset.for = form.dataset.selbarId;
      panel.hidden = false;
      place(panel);
      if (launcher) launcher.setAttribute('aria-expanded', 'true');
      // A form that fills itself from the ticked rows (Weigh) does so now.
      form.dispatchEvent(new CustomEvent('selbar:open', { detail: { boxes: checked() } }));
      const first = form.querySelector('[data-weigh-rows] input')
        || form.querySelector('select:not([hidden]), input:not([type=hidden]):not([hidden]), textarea');
      if (first) first.focus();
    };

    // What each action is called: its own label, else its button's words,
    // else (a bare Set or Move) what it sets or where it moves to.
    const nameOf = (form, submit, asks) => {
      if (form.dataset.selbarLabel) return form.dataset.selbarLabel;
      const words = submit ? submit.textContent.replace(/\s+/g, ' ').trim() : '';
      const what = asks ? (asks.getAttribute('aria-label') || '').trim() : '';
      if (what && words === t('Move')) return t('Move to %(place)s', { place: what.toLowerCase() });
      if (what && words === t('Set')) {
        return [t('Field'), t('Column to set'), t('Field to set')].includes(what) ? t('Set a field…') : what;
      }
      return words || t('Apply');
    };
    // The first three actions are on the bar, the rest (and any that
    // deletes) under ···, unless a form says where it goes.
    const actions = forms.map((form) => {
      const submit = form.querySelector('button[type=submit], button:not([type])');
      const asks = form.querySelector('select, input:not([type=hidden]), textarea');
      const danger = Boolean(submit && submit.classList.contains('btn-danger'));
      return { form, submit, asks, danger, label: nameOf(form, submit, asks) };
    });
    let onBar = 0;
    actions.forEach((a) => {
      a.more = a.form.hasAttribute('data-selbar-more') || a.danger || onBar >= 3;
      if (!a.more) onBar += 1;
    });
    // A key for each: the form's own, else the first letter of a word of its
    // name that no other action and not N (New) has taken.
    const taken = new Set(['N', ...actions.map((a) => (a.form.dataset.key || '').toUpperCase()).filter(Boolean)]);
    actions.forEach((a) => {
      if (a.form.dataset.key || a.more) return;
      const letter = (a.label.match(/\b[A-Za-z]/g) || []).map((c) => c.toUpperCase()).find((c) => !taken.has(c));
      if (letter) { a.form.dataset.key = letter; taken.add(letter); }
    });

    actions.forEach(({ form, submit, asks, danger, label, more: inMore }, i) => {
      form.dataset.selbarId = String(i);
      const launcher = make('button', inMore ? 'menu-item selbar-act' : 'selbar-act');
      launcher.type = 'button';
      if (inMore) launcher.setAttribute('role', 'menuitem');
      if (danger) launcher.classList.add(inMore ? 'menu-item-danger' : 'is-danger');
      launcher.appendChild(make('span', null, label));
      const key = (form.dataset.key || '').slice(0, 1).toUpperCase();
      if (key) {
        launcher.appendChild(make('kbd', 'selbar-kbd', key));
        launcher.setAttribute('aria-keyshortcuts', key);
      }
      if (submit && submit.title) launcher.title = submit.title;
      if (asks) {
        launcher.setAttribute('aria-haspopup', 'dialog');
        launcher.setAttribute('aria-expanded', 'false');
        // The panel names the action, then holds the form as it was.
        if (!form.querySelector('.selbar-pop-title')) form.prepend(make('span', 'selbar-pop-title', label));
      }
      const run = () => {
        if (asks) { open(form, inMore ? null : launcher); return; }
        closePanel(bar);
        form.requestSubmit(submit || undefined);
      };
      launcher.addEventListener('click', run);
      if (key) keys[key] = run;
      form.classList.remove('selbar-group');
      form.classList.add('selbar-form');
      form.hidden = true;
      panel.appendChild(form);
      (inMore ? moreMenu : line).appendChild(launcher);
    });

    // Whatever else the bar held (a link, a note) stays on it.
    Array.from(bar.children).forEach((child) => {
      if (child.matches('[data-selection-count], .selbar-spacer, [data-selection-clear]')) {
        if (child.matches('.selbar-spacer')) child.remove();
        return;
      }
      line.appendChild(child);
    });
    const count = bar.querySelector('[data-selection-count]');
    if (count) count.after(line);
    else bar.prepend(line);

    if (moreMenu.children.length) {
      const more = make('button', 'selbar-icon selbar-more');
      more.type = 'button';
      more.innerHTML = SVG('ellipsis');
      more.setAttribute('aria-label', t('More actions'));
      more.title = t('More actions');
      more.setAttribute('aria-haspopup', 'menu');
      more.addEventListener('click', () => {
        const show = moreMenu.hidden;
        closePanel(bar);
        moreMenu.hidden = !show;
        if (show) place(moreMenu, 'end');
        if (show) { const first = moreMenu.querySelector('button'); if (first) first.focus(); }
      });
      line.appendChild(more);
      document.body.appendChild(moreMenu);
      bar.selbarMore = moreMenu;
    }
    if (clear) {
      clear.className = 'selbar-icon selbar-clear';
      clear.innerHTML = SVG('close');
      clear.setAttribute('aria-label', t('Clear the selection'));
      clear.title = t('Clear the selection (Esc)');
      bar.appendChild(clear);
    }
    // The panel and the menu hang from the page, not the bar: its glass
    // (a backdrop filter) would make them place themselves against it.
    document.body.appendChild(panel);
    bar.selbarPop = panel;

    // A click away from the bar closes its panel or menu.
    document.addEventListener('pointerdown', (event) => {
      if (![bar, panel, moreMenu].some((node) => node.contains(event.target))) closePanel(bar);
    });
    moreMenu.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); closePanel(bar); }
    });
    panel.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); closePanel(bar); }
    });
    bar.addEventListener('selbar:hidden', () => closePanel(bar));
    window.addEventListener('resize', () => closePanel(bar));
    // An action's key, while rows are ticked and nothing is being typed in.
    document.addEventListener('keydown', (event) => {
      if (bar.hidden || event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
      const target = event.target;
      if (target && target.closest && target.closest('input:not([type=checkbox]), select, textarea, [contenteditable], dialog[open]')) return;
      if (document.querySelector('dialog[open]')) return;
      const run = keys[(event.key || '').toUpperCase()];
      if (!run) return;
      event.preventDefault();
      run();
    });
  }

  // "Set field": the value box follows the column chosen (a date picker
  // for a date, the column's own choices offered for a choice column).
  function wireFieldPicker(select) {
    const form = select.form;
    const input = form.querySelector('[data-bulk-value]');
    const list = input && input.list;
    const kind = form.querySelector('[data-bulk-source]');
    const sync = () => {
      const opt = select.selectedOptions[0];
      if (!opt || !input) return;
      const isSource = opt.dataset.type === 'source';
      // A source column asks two things: the colony, then the ID in it.
      if (kind) kind.hidden = !isSource;
      input.type = opt.dataset.type === 'date' ? 'date' : 'text';
      input.inputMode = opt.dataset.type === 'number' ? 'decimal' : '';
      input.placeholder = isSource ? t('ID in that colony') : t('value');
      if (list) {
        list.innerHTML = '';
        (opt.dataset.options || '').split('\n').filter(Boolean).forEach((o) => {
          const el = document.createElement('option');
          el.value = o;
          list.appendChild(el);
        });
      }
    };
    select.addEventListener('change', sync);
    sync();
    // An empty value clears the column on every ticked row: ask first, and
    // tell the server it was meant (clear=1), so a slip never wipes it.
    let clear = form.querySelector('input[name="clear"]');
    if (!clear) {
      clear = document.createElement('input');
      clear.type = 'hidden';
      clear.name = 'clear';
      form.appendChild(clear);
    }
    const guard = (edited) => {
      const empty = !input || !input.value.trim();
      const label = (select.selectedOptions[0] || {}).textContent || t('this column');
      clear.value = empty ? '1' : '';
      if (empty) form.dataset.confirm = t('Clear %(column)s on {n} rows?', { column: label.trim() });
      else delete form.dataset.confirm;
      if (edited) delete form.dataset.confirmed;      // a new question for a new value
    };
    if (input) input.addEventListener('input', () => guard(true));
    select.addEventListener('change', () => guard(true));
    form.addEventListener('submit', () => guard(false), true);   // before the bar's own handler reads it
    guard(true);
  }

  /* Set a field…, for every column of the sheet. A sheet's own batch route
     covers some columns (the server sets them on all the rows at once, and
     the page comes back where it was); every other column a person can
     edit in a cell is offered too, and set on each ticked row through that
     row's own save, as typing it into the cell would: the same checks and
     rules, the sheet not reloaded, and the rows one change in Batch history
     (app/batch_edit.py). Built when the panel opens, from the ticked rows. */
  function anyField(scope, bar, checked) {
    // The sheet: one saving through sheet.js, or one with a saver of its own (card.bioSave).
    const card = scope.closest('[data-autosave-sheet]') || scope.querySelector('[data-autosave-sheet]')
      || (scope.matches('.data-table-card') ? scope : scope.closest('.data-table-card'));
    if (!card) return;
    let select = bar.querySelector('[data-bulk-field]');
    let form = select && select.form;
    if (!form) {
      // No batch route for any column: a Set a field… of the rows alone.
      form = document.createElement('form');
      form.setAttribute('data-selection-form', '');
      form.setAttribute('data-selbar-more', '');
      form.dataset.selbarLabel = t('Set a field…');
      form.dataset.key = 'F';
      const listId = `selbar-any-${Math.random().toString(36).slice(2, 8)}`;
      form.innerHTML = `<select name="field" aria-label="${t('Field to set')}" data-bulk-field></select>` +
        `<input name="value" placeholder="${t('new value')}" list="${listId}" aria-label="${t('New value')}" data-bulk-value autocomplete="off">` +
        `<datalist id="${listId}"></datalist><button type="submit" class="btn btn-sm btn-primary">${t('Set')}</button>`;
      const anchor = bar.querySelector('form[data-selbar-more], .selbar-spacer, [data-selection-clear]');
      bar.insertBefore(form, anchor);
      select = form.querySelector('[data-bulk-field]');
    }
    const table = card.querySelector('table');
    const served = new Set(Array.from(select.options).map((o) => o.value));

    // The columns of the ticked rows a person may edit, in the sheet's order.
    function columns(boxes) {
      const found = new Map();
      boxes.forEach((box) => {
        const tr = box.closest('tr');
        if (!tr) return;
        Array.from(tr.cells).forEach((td) => {
          const fields = Array.from(td.querySelectorAll('[form][name], .cell-text[data-sort-for][data-form]'));
          fields.forEach((el) => {
            const name = el.getAttribute('name') || el.dataset.sortFor;
            if (!name || /_was$/.test(name) || found.has(name) || served.has(name)) return;
            const control = el.classList.contains('cell-text') ? fieldOf(el) : el;
            if (!control || control.type === 'hidden' || control.type === 'checkbox' || control.type === 'file') return;
            if (control.disabled || control.readOnly || el.classList.contains('is-disabled')) return;
            const head = table && table.tHead && table.tHead.rows[0] && table.tHead.rows[0].cells[td.cellIndex];
            const heading = head ? head.textContent.replace(/\s+/g, ' ').trim() : '';
            const label = fields.length > 1 ? (control.getAttribute('aria-label') || heading || name) : (heading || control.getAttribute('aria-label') || name);
            found.set(name, { name, label, control });
          });
        });
      });
      return Array.from(found.values());
    }
    // A cell drawn as text: its field, from its markup, without opening it.
    function fieldOf(span) {
      const holder = document.createElement('template');
      holder.innerHTML = span.dataset.field || '';
      return holder.content.firstElementChild;
    }
    function choicesOf(control) {
      if (control.tagName === 'SELECT') {
        if (window.BiomanagerShell && window.BiomanagerShell.fillChoices && control.isConnected) window.BiomanagerShell.fillChoices(control);
        return Array.from(control.options).filter((o) => o.value).map((o) => [o.value, o.textContent.trim()]);
      }
      const list = control.list || (control.getAttribute('list') && document.getElementById(control.getAttribute('list')));
      return list ? Array.from(list.options).map((o) => [o.value, o.label || '']) : [];
    }

    form.addEventListener('selbar:open', (event) => {
      select.querySelectorAll('option[data-mode="rows"]').forEach((o) => o.remove());
      columns(event.detail.boxes).forEach((col) => {
        const opt = document.createElement('option');
        opt.value = col.name;
        opt.textContent = col.label;
        opt.dataset.mode = 'rows';
        const type = col.control.type;
        if (type === 'date' || type === 'number') opt.dataset.type = type;
        const choices = choicesOf(col.control);
        if (choices.length) opt.dataset.options = choices.map((c) => c[0]).join('\n');
        select.appendChild(opt);
      });
      select.dispatchEvent(new Event('change'));
    });

    form.selbarBefore = () => {
      const opt = select.selectedOptions[0];
      if (!opt || opt.dataset.mode !== 'rows' || !(card.bioSave || window.BioSheet)) return true;      // the server's
      const value = (form.querySelector('[data-bulk-value]') || {}).value || '';
      setOnRows(card, checked(), opt.value, opt.textContent.trim(), value.trim());
      return false;
    };
  }

  async function setOnRows(card, boxes, name, label, value) {
    const say = (text, kind) => window.BiomanagerShell && window.BiomanagerShell.toast(text, kind);
    const what = t('Set %(column)s', { column: label });
    let batchId = null;
    try {
      const r = await fetch('/batch/start', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ what: `${what}: ${value || t('(blank)')}`, table: card.dataset.auditTable || '' }) });
      if (r.ok) batchId = (await r.json()).id;
    } catch (_) { /* each row is still its own change */ }
    let changed = 0;
    let skipped = 0;
    let failed = 0;
    let notChoice = 0;
    for (const box of boxes) {
      const tr = box.closest('tr');
      const holder = tr && tr.querySelector('[form]');
      const formId = holder && holder.getAttribute('form');
      const rowForm = formId && window.BioRowForms.get(formId);
      let el = tr && tr.querySelector(`[form="${formId}"][name="${name}"]`);
      if (!el && tr && window.BioTextCells) el = window.BioTextCells.field(tr, name, formId);
      if (!rowForm || !el || el.disabled || el.readOnly) { skipped += 1; continue; }
      if (el.tagName === 'SELECT') {
        if (window.BiomanagerShell && window.BiomanagerShell.fillChoices) window.BiomanagerShell.fillChoices(el);
        const low = value.toLowerCase();
        const option = Array.from(el.options).find((o) => o.value === value)
          || Array.from(el.options).find((o) => o.value.toLowerCase() === low || o.textContent.trim().toLowerCase() === low);
        if (!option) { notChoice += 1; continue; }
        if (el.value === option.value) continue;
        el.value = option.value;
      } else {
        if (el.value === value) continue;
        el.value = value;
        if (el.type === 'date') el.dataset.empty = value ? '0' : '1';
      }
      el.dataset.dirty = '1';
      // A pill takes its colour from the value (status, sex).
      if ('status' in el.dataset) el.dataset.status = el.value.toLowerCase();
      if ('sex' in el.dataset) el.dataset.sex = el.value;
      const headers = batchId ? { 'X-Batch-Id': String(batchId) } : null;
      const ok = card.bioSave ? await card.bioSave(rowForm, headers) : await window.BioSheet.save(rowForm, card, headers);
      if (ok) changed += 1; else failed += 1;
    }
    if (batchId) {
      fetch(`/batch/${batchId}/finish`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ count: changed }) }).catch(() => {});
    }
    const parts = [t('%(what)s on %(n)s rows.', { what, n: changed })];
    if (skipped) parts.push(t('%(n)s not yours to edit.', { n: skipped }));
    if (notChoice) parts.push(t('%(n)s have no such choice.', { n: notChoice }));
    if (failed) parts.push(t('%(n)s could not be saved; their cells are marked.', { n: failed }));
    say(parts.join(' '), failed || (!changed && (skipped || notChoice)) ? 'error' : '');
    const bar = card.querySelector('[data-selection-bar]') || document.querySelector('[data-selection-bar]:not([hidden])');
    if (bar) closePanel(bar);
  }

  /* Weigh: a weight box for each ticked mouse, named by its number. */
  function wireWeigh(form) {
    const rows = form.querySelector('[data-weigh-rows]');
    const day = form.querySelector('input[name="weigh_date"]');
    form.addEventListener('selbar:open', (event) => {
      if (day && !day.value) {
        const now = new Date();
        day.value = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      }
      const kept = {};
      rows.querySelectorAll('input').forEach((input) => { kept[input.name] = input.value; });
      rows.replaceChildren();
      event.detail.boxes.slice(0, 60).forEach((box) => {
        const tr = box.closest('tr');
        const idCell = tr && (tr.querySelector('td.sheet-pin-1') || tr.cells[1]);
        const name = idCell ? idCell.textContent.replace(/\s+/g, ' ').trim() : box.value;
        const label = document.createElement('label');
        label.className = 'selbar-weigh';
        label.appendChild(make('span', 'selbar-weigh-id', name.startsWith('#') ? name : '#' + name));
        const input = document.createElement('input');
        input.type = 'number';
        input.step = '0.1';
        input.min = '0';
        input.inputMode = 'decimal';
        input.name = 'grams_' + box.value;
        input.setAttribute('aria-label', t('Weight of %(mouse)s in grams', { mouse: name }));
        input.value = kept[input.name] || '';
        label.appendChild(input);
        label.appendChild(make('span', 'selbar-weigh-unit', 'g'));
        rows.appendChild(label);
      });
    });
  }

  function init() {
    document.querySelectorAll('[data-selection-scope]').forEach(setup);
    document.querySelectorAll('[data-bulk-field]').forEach(wireFieldPicker);
    document.querySelectorAll('form[data-weigh-form]').forEach(wireWeigh);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
