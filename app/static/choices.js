/* The colony's Dropdowns tab (templates/colony.html, app/choices.py).

   The columns with choices on the left; on the right the one picked, its
   choices in the order its dropdown shows them, each with how many records
   hold it. An admin renames a choice where it stands (and may change it on
   those records too), drags it to a new place (or Alt+↑/↓), removes it
   (putting another in its place on its records, if they like) and adds
   one, or many pasted a line each. Every change is saved at once and the
   tab redrawn from the page. */
(function () {
  'use strict';

  const root = document.querySelector('[data-choices]');
  if (!root) return;
  const t = window.t || ((s) => s);
  const canEdit = root.hasAttribute('data-can-edit');
  const say = (message, kind) => { if (message && window.BiomanagerShell) window.BiomanagerShell.toast(message, kind); };
  const idUrl = (name, id) => root.dataset[name].replace('/0/', `/${id}/`);

  function post(url, fields) {
    const body = new FormData();
    Object.entries(fields || {}).forEach(([k, v]) => body.append(k, v));
    return fetch(url, { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } })
      .then((r) => r.json().catch(() => ({ ok: r.ok })))
      .catch(() => ({ ok: false, error: t('The server did not answer. Check the connection and try again.') }));
  }

  // Redraw the tab from the page, on the column shown.
  function refresh(focusAdd) {
    const field = shownField();
    const url = new URL(window.location.href);
    url.searchParams.set('field', field);
    return fetch(url, { headers: { 'X-Requested-With': 'fetch' } })
      .then((r) => r.text())
      .then((html) => {
        const fresh = new DOMParser().parseFromString(html, 'text/html').querySelector('[data-choices]');
        if (!fresh) return;
        root.innerHTML = fresh.innerHTML;
        show(field);
        if (focusAdd) { const box = root.querySelector(`[data-pane="${field}"] .choice-add textarea`); if (box) box.focus(); }
      });
  }
  function done(answer, focusAdd) {
    if (answer && answer.ok === false) say(answer.error || t('That could not be saved.'), 'error');
    else if (answer && answer.message) say(answer.message);
    return refresh(focusAdd);
  }

  // ---------------------------------------------------------------- columns

  function shownField() {
    const pane = root.querySelector('.choices-pane:not([hidden])');
    return pane ? pane.dataset.pane : '';
  }
  function show(field) {
    root.querySelectorAll('.choices-pane').forEach((p) => { p.hidden = p.dataset.pane !== field; });
    root.querySelectorAll('.choices-col').forEach((a) => {
      const on = a.dataset.col === field;
      a.classList.toggle('is-active', on);
      if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    });
  }
  root.addEventListener('click', (e) => {
    const col = e.target.closest('.choices-col');
    if (!col || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    show(col.dataset.col);
    const url = new URL(window.location.href);
    url.searchParams.set('field', col.dataset.col);
    history.replaceState(history.state, '', url);
  });

  if (!canEdit) return;

  // ---------------------------------------------------------------- add

  root.addEventListener('submit', (e) => {
    const form = e.target.closest('[data-add]');
    if (!form) return;
    e.preventDefault();
    const value = form.elements.option_value.value;
    if (!value.trim()) return;
    post(root.dataset.createUrl, { field_name: form.dataset.add, option_value: value }).then((a) => done(a, true));
  });
  root.addEventListener('keydown', (e) => {
    const box = e.target.closest('.choice-add textarea');
    if (box && e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      box.form.requestSubmit();
    }
  });
  // The box grows with what is pasted into it.
  root.addEventListener('input', (e) => {
    const box = e.target.closest('.choice-add textarea');
    if (!box) return;
    box.style.height = 'auto';
    box.style.height = `${Math.min(box.scrollHeight, 220)}px`;
  });
  root.addEventListener('click', (e) => {
    const adopt = e.target.closest('[data-adopt]');
    if (adopt) post(root.dataset.createUrl, { field_name: adopt.dataset.field, option_value: adopt.dataset.adopt }).then(done);
  });

  // ---------------------------------------------------------------- rename

  function rename(input) {
    const row = input.closest('.choice');
    const old = row.dataset.value;
    const value = input.value.trim();
    if (!value || value === old) { input.value = old; return; }
    const uses = Number(row.dataset.uses || 0);
    const ask = uses
      ? window.BioDialog.confirm(t('Also change “%(old)s” to “%(new)s” on the %(things)s that have it?', { old, new: value, things: row.dataset.usesText }),
        { title: t('Rename a choice'), okLabel: t('Change them too'), cancelLabel: t('Only the choice') })
      : Promise.resolve(false);
    ask.then((also) => post(idUrl('updateUrl', row.dataset.id), { option_value: value, also_records: also ? '1' : '' })).then(done);
  }
  root.addEventListener('keydown', (e) => {
    const input = e.target.closest('input.choice-name');
    if (!input) return;
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = input.closest('.choice').dataset.value; input.blur(); }
    // Alt+↑ / Alt+↓ moves the choice in the dropdown.
    if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault();
      const row = input.closest('.choice');
      const other = e.key === 'ArrowUp' ? row.previousElementSibling : row.nextElementSibling;
      // Built-in choices come first, always.
      if (!other || other.classList.contains('is-built-in')) return;
      if (e.key === 'ArrowUp') other.before(row); else other.after(row);
      input.focus();
      saveOrder(row.parentElement);
    }
  });
  root.addEventListener('focusout', (e) => {
    const input = e.target.closest('input.choice-name');
    if (input) rename(input);
  });

  // ---------------------------------------------------------------- remove

  const dialog = document.getElementById('choice-remove-dialog');
  root.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-remove]');
    if (!btn) return;
    const row = btn.closest('.choice');
    const value = row.dataset.value;
    const uses = Number(row.dataset.uses || 0);
    const url = idUrl('deleteUrl', row.dataset.id);
    if (!uses) { post(url, {}).then(done); return; }
    // The records that hold it: left as they are, or given another choice.
    dialog.querySelector('#choice-remove-title').textContent = t('Remove “%(value)s”?', { value });
    dialog.querySelector('[data-remove-text]').textContent =
      t('%(things)s have “%(value)s”. It goes from the dropdown; on those records it can stay as it is, or another choice can take its place.', { things: row.dataset.usesText, value });
    const pick = dialog.querySelector('[data-remove-with]');
    pick.innerHTML = '';
    pick.add(new Option(t('Keep “%(value)s” on them', { value }), ''));
    row.parentElement.querySelectorAll('.choice').forEach((other) => {
      // Any other choice, a built-in one too, may take its place.
      if (other !== row) pick.add(new Option(t('Change it to “%(value)s”', { value: other.dataset.value }), other.dataset.value));
    });
    dialog.onclose = () => {
      if (dialog.returnValue === 'remove') post(url, { replace_with: pick.value }).then(done);
    };
    dialog.returnValue = '';
    dialog.showModal();
  });
  if (dialog) {
    dialog.querySelector('form').addEventListener('submit', () => { dialog.returnValue = 'remove'; });
    dialog.querySelector('[data-remove-cancel]').addEventListener('click', () => dialog.close(''));
  }

  // ---------------------------------------------------------------- order

  function saveOrder(list) {
    const ids = Array.from(list.querySelectorAll('.choice')).map((r) => Number(r.dataset.id)).filter(Boolean);
    fetch(root.dataset.orderUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
      body: JSON.stringify({ field_name: list.dataset.field, ids }),
    }).then((r) => { if (!r.ok) say(t('That could not be saved.'), 'error'); });
  }
  let dragged = null;
  root.addEventListener('dragstart', (e) => {
    const row = e.target.closest && e.target.closest('.choice');
    // Dragging from the name would move its text: only from the rest of the row.
    if (!row || e.target.closest('input')) return;
    dragged = row;
    row.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', row.dataset.value);
  });
  root.addEventListener('dragover', (e) => {
    const over = dragged && e.target.closest && e.target.closest('.choice');
    if (!over || over === dragged || over.parentElement !== dragged.parentElement || over.classList.contains('is-built-in')) return;
    e.preventDefault();
    const box = over.getBoundingClientRect();
    if (e.clientY < box.top + box.height / 2) over.before(dragged); else over.after(dragged);
  });
  root.addEventListener('dragend', () => {
    if (!dragged) return;
    dragged.classList.remove('is-dragging');
    saveOrder(dragged.parentElement);
    dragged = null;
  });
})();
