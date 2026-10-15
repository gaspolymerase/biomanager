/* A sheet's cells drawn as text until used (app.py, text_cells_on), in
 * Chromium, which reads every field on a page as it loads; text it leaves
 * alone. Each carries its field's own markup in data-field (as_text_cells),
 * its value in data-sort-value, its name in data-sort-for and its row form
 * in data-form, and becomes that field when used: pressed, reached with
 * Tab, pasted into (sheet.js), or given a value by a page's script.
 *
 *   BioTextCells.open(span)                 the field, in the text's place (and its partner's)
 *   BioTextCells.field(root, name, form)    a row's field, put back if it was text
 *   BioTextCells.read(root, name, form)     {value, text} of a cell, without putting it back
 *
 * A field put back sends text-cells:open (it bubbles), for a page that
 * wires its fields as it loads to wire this one too. The field then stays.
 * Box and Position are saved together, so opening one opens the other in
 * its row. A date shows as its field would, in the browser's language
 * (data-sort-value keeps 2026-10-14 for sort and export).
 *
 * Loaded in the page's head: a page's own scripts use it as they run.
 */
(function () {
  'use strict';

  const PARTNERS = { rack_id: 'position', position: 'rack_id', cage_rack: 'cage_position', cage_position: 'cage_rack' };
  const esc = (text) => (window.CSS && CSS.escape ? CSS.escape(text) : String(text).replace(/"/g, '\\"'));
  let pointer = null;   // where the last press was, for the caret

  // force: a cell drawn as locked (a field the page may unlock after a save).
  function open(span, force) {
    if (!span || !span.parentNode || !span.dataset.field) return null;
    if (span.classList.contains('is-disabled') && !force) return null;
    const holder = document.createElement('template');
    holder.innerHTML = span.dataset.field;
    const field = holder.content.firstElementChild;
    if (!field) return null;
    const name = span.dataset.sortFor;
    const row = span.closest('tr');
    span.replaceWith(field);
    field.dispatchEvent(new CustomEvent('text-cells:open', { bubbles: true }));
    const partner = PARTNERS[name];
    if (partner && row) row.querySelectorAll(`.cell-text[data-sort-for="${partner}"]`).forEach((other) => open(other, force));
    return field;
  }

  // A row's cell for `name` (in its form, when given): its field, else its text.
  function cell(root, name, form) {
    const scope = root || document;
    const inForm = form ? `[form="${esc(form)}"]` : '';
    return scope.querySelector(`[name="${esc(name)}"]${inForm}:not([type=hidden])`)
      || scope.querySelector(`.cell-text[data-sort-for="${esc(name)}"]${form ? `[data-form="${esc(form)}"]` : ''}`);
  }
  function isText(el) { return Boolean(el && el.classList && el.classList.contains('cell-text')); }

  function field(root, name, form, force) {
    const el = cell(root, name, form);
    return isText(el) ? open(el, force) : el;
  }

  function read(root, name, form) {
    const el = cell(root, name, form);
    if (!el) return null;
    if (isText(el)) {
      const value = el.dataset.sortValue || '';
      return { value, text: el.classList.contains('is-placeholder') ? '' : (el.classList.contains('is-date') ? value : el.textContent.trim()) };
    }
    if (el.tagName === 'SELECT') {
      const chosen = el.selectedOptions[0];
      return { value: el.value, text: chosen ? chosen.textContent.trim() : '' };
    }
    return { value: el.value, text: el.value };
  }

  // Where in the text the press was, as a caret offset (null if not in it).
  function offsetAt(span, x, y) {
    let node = null;
    let offset = 0;
    if (document.caretPositionFromPoint) {
      const at = document.caretPositionFromPoint(x, y);
      if (at) { node = at.offsetNode; offset = at.offset; }
    } else if (document.caretRangeFromPoint) {
      const at = document.caretRangeFromPoint(x, y);
      if (at) { node = at.startContainer; offset = at.startOffset; }
    }
    return node && node.parentNode === span ? offset : null;
  }

  document.addEventListener('pointerdown', (event) => {
    const span = event.target.closest && event.target.closest('.cell-text');
    pointer = span ? { span, offset: offsetAt(span, event.clientX, event.clientY) } : null;
  }, true);

  // A press focuses the text (tabindex), as does Tab: either way it becomes the field.
  document.addEventListener('focusin', (event) => {
    const span = event.target.closest && event.target.closest('.cell-text');
    if (!span) return;
    const pressed = pointer && pointer.span === span;
    const offset = pressed ? pointer.offset : null;
    pointer = null;
    const opened = open(span);
    if (!opened) return;
    opened.focus({ preventScroll: true });
    if (opened.tagName === 'SELECT') {
      // Its list opens at once when pressed, as a menu's does.
      if (pressed && opened.showPicker) { try { opened.showPicker(); } catch (_) { /* not allowed here */ } }
    } else if (opened.type !== 'date' && opened.setSelectionRange) {
      const end = opened.value.length;
      try { opened.setSelectionRange(offset === null ? end : offset, offset === null ? end : offset); } catch (_) { /* a number field */ }
    }
  });

  // A date field shows the date in the browser's own way (10/14/2026,
  // 2026/10/14): its text does the same.
  const dateFormat = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: '2-digit', day: '2-digit' });
  function showDates(root) {
    root.querySelectorAll('.cell-text.is-date').forEach((span) => {
      const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(span.dataset.sortValue || '');
      if (parts) span.textContent = dateFormat.format(new Date(+parts[1], parts[2] - 1, +parts[3]));
    });
  }
  // Before a sheet takes its unseen rows off the page (data-table.js, on
  // the window's DOMContentLoaded, after the document's); rows the server
  // sends later come with dt:rows-loaded.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => showDates(document));
  else showDates(document);
  document.addEventListener('dt:rows-loaded', (event) => {
    ((event.detail && event.detail.rows) || []).forEach(showDates);
  });

  window.BioTextCells = { open, field, read };
})();
