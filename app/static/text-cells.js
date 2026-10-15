/* An inventory's cells drawn as text until used (inventory_routes._text_cells).
 * Each carries its field's own markup in data-field (app.py, as_text_cells)
 * and becomes that field when used: pressed, reached with Tab, pasted into
 * (sheet.js), or given a value by the page's script (module.html, applyRow).
 * Chromium reads every field on a page as it loads; text it leaves alone.
 *
 *   BioTextCells.open(span)   the field, in the text's place (and its partner's)
 *
 * The field then stays. Box and Position are saved together, so opening one
 * opens the other in its row. A date shows as its field would, in the
 * browser's language (data-sort-value keeps 2026-10-14 for sort and export).
 */
(function () {
  'use strict';

  const PARTNERS = { rack_id: 'position', position: 'rack_id' };
  let pointer = null;   // where the last press was, for the caret

  function open(span) {
    if (!span || !span.isConnected || !span.dataset.field || span.classList.contains('is-disabled')) return null;
    const holder = document.createElement('template');
    holder.innerHTML = span.dataset.field;
    const field = holder.content.firstElementChild;
    if (!field) return null;
    const name = span.dataset.sortFor;
    const row = span.closest('tr');
    span.replaceWith(field);
    const partner = PARTNERS[name];
    if (partner && row) row.querySelectorAll(`.cell-text[data-sort-for="${partner}"]`).forEach(open);
    return field;
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
    const field = open(span);
    if (!field) return;
    field.focus({ preventScroll: true });
    if (field.tagName === 'SELECT') {
      // Its list opens at once when pressed, as a menu's does.
      if (pressed && field.showPicker) { try { field.showPicker(); } catch (_) { /* not allowed here */ } }
    } else if (field.type !== 'date' && field.setSelectionRange) {
      const end = field.value.length;
      try { field.setSelectionRange(offset === null ? end : offset, offset === null ? end : offset); } catch (_) { /* a number field */ }
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
  // Here before the sheet takes its unseen rows off the page (data-table.js,
  // window's DOMContentLoaded); rows the server sends later come with dt:rows-loaded.
  showDates(document);
  document.addEventListener('dt:rows-loaded', (event) => {
    ((event.detail && event.detail.rows) || []).forEach(showDates);
  });

  window.BioTextCells = { open };
})();
