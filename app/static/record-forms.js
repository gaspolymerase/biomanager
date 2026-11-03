/* What a record dialog's form does beyond its fields (every page; base.html).
 *
 *   ⌘↵ / Ctrl+↵      saves the form of the dialog that is open, as its main
 *                    button would, and that button shows the keys.
 *   select[data-segmented], and a dialog's Sex (name gender or sex) with
 *                    four choices or fewer: the choices as a row of buttons.
 *                    The select stays the field (it is what is saved, and
 *                    what a dialog's script fills in); the buttons follow it.
 *   fieldset[data-reveal] (data-reveal-label="Add a transgene"): its first
 *                    field and every one with a value show; the rest wait
 *                    behind + Add a transgene, one more each time.
 *
 * A dialog's script fills its fields when it opens, without telling anyone,
 * so the buttons and the hidden fields are brought up to date each time a
 * dialog opens (its open attribute) and whenever a field changes.
 */
(function () {
  'use strict';

  const t = window.t || ((s) => s);
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
  const keys = mac ? '⌘↵' : 'Ctrl ↵';
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };

  const mainButton = (dialog) => dialog.querySelector('.record-dialog-foot button[type="submit"].btn-primary, .record-dialog-foot .btn-primary[type="submit"]');

  /* ------------------------------------------------------------- ⌘↵ */

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || !(event.metaKey || event.ctrlKey) || event.altKey || event.defaultPrevented) return;
    const dialog = event.target.closest && event.target.closest('dialog.record-dialog[open]');
    if (!dialog) return;
    const button = mainButton(dialog);
    const form = button && button.form;
    if (!form || button.disabled) return;
    event.preventDefault();
    if (form.requestSubmit) form.requestSubmit(button); else button.click();
  });

  function showKeys(dialog) {
    const button = mainButton(dialog);
    if (!button || button.querySelector('.dt-kbd')) return;
    const kbd = el('kbd', 'dt-kbd', keys);
    kbd.setAttribute('aria-hidden', 'true');
    button.appendChild(kbd);
    button.setAttribute('aria-keyshortcuts', mac ? 'Meta+Enter' : 'Control+Enter');
  }

  /* -------------------------------------------------------- segmented */

  const segmentable = (select) => select.tagName === 'SELECT' && !select.multiple && select.options.length <= 4
    && (select.hasAttribute('data-segmented') || (/^(gender|sex)$/.test(select.name) && !!select.closest('.record-dialog-body')));

  function segment(select) {
    if (select.dataset.segmentedDrawn) return;
    select.dataset.segmentedDrawn = '1';
    const row = el('div', 'seg-field');
    row.setAttribute('role', 'radiogroup');
    const label = select.closest('label');
    const words = label && Array.from(label.childNodes).find((n) => (n.nodeType === 3 || n.tagName === 'SPAN') && n.textContent.trim());
    const name = words ? words.textContent.trim() : '';
    if (name) row.setAttribute('aria-label', name);
    const buttons = Array.from(select.options).map((option) => {
      const b = el('button', 'seg-field-item', option.textContent.trim());
      b.type = 'button';
      b.setAttribute('role', 'radio');
      b.dataset.value = option.value;
      b.addEventListener('click', () => {
        if (select.disabled) return;
        select.value = option.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      });
      row.appendChild(b);
      return b;
    });
    select.classList.add('is-segmented');
    select.tabIndex = -1;
    select.after(row);
    const sync = () => {
      buttons.forEach((b) => {
        b.setAttribute('aria-checked', b.dataset.value === select.value ? 'true' : 'false');
        b.disabled = select.disabled;
      });
    };
    select.addEventListener('change', sync);
    select._syncSegments = sync;
    sync();
  }

  /* ----------------------------------------------------------- reveal */

  function reveal(fieldset) {
    const fields = Array.from(fieldset.querySelectorAll(':scope > label'));
    if (fields.length < 2) return;
    let button = fieldset.querySelector(':scope > .reveal-more');
    if (!button) {
      button = el('button', 'reveal-more');
      button.type = 'button';
      button.innerHTML = '<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#plus"></use></svg>';
      button.appendChild(document.createTextNode(fieldset.dataset.revealLabel || t('Add another')));
      button.addEventListener('click', () => {
        const next = fields.find((f) => f.hidden);
        if (!next) return;
        next.hidden = false;
        const input = next.querySelector('input, select, textarea');
        if (input) input.focus();
        button.hidden = !fields.some((f) => f.hidden);
      });
      fieldset.appendChild(button);
    }
    const filled = (f) => Array.from(f.querySelectorAll('input, select, textarea')).some((i) => (i.value || '').trim() !== '');
    // The first, every filled one, and any before a filled one (no gaps).
    const last = fields.reduce((at, f, i) => (filled(f) ? i : at), 0);
    fields.forEach((f, i) => { f.hidden = i > last; });
    button.hidden = !fields.some((f) => f.hidden);
  }

  /* ------------------------------------------------------------- setup */

  function refresh(root) {
    root.querySelectorAll('select').forEach((select) => {
      if (segmentable(select)) segment(select);
      if (select._syncSegments) select._syncSegments();
    });
    root.querySelectorAll('fieldset[data-reveal]').forEach(reveal);
  }

  function opened(dialog) {
    if (!dialog.matches('.record-dialog')) return;
    showKeys(dialog);
    refresh(dialog);
    // The dialog's own script may fill it just after it opens.
    setTimeout(() => refresh(dialog), 0);
  }

  new MutationObserver((changes) => {
    changes.forEach((c) => { if (c.target.open) opened(c.target); });
  }).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ['open'] });

  document.addEventListener('reset', (event) => {
    const dialog = event.target.closest && event.target.closest('dialog.record-dialog');
    if (dialog) setTimeout(() => refresh(dialog), 0);
  }, true);

  const init = () => document.querySelectorAll('dialog.record-dialog').forEach(refresh);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
}());
