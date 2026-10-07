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

    bar.querySelectorAll('[data-selection-clear]').forEach((button) => {
      button.addEventListener('click', () => {
        boxes().forEach((box) => { box.checked = false; });
        refresh();
      });
    });

    // Inject the ids at submit time rather than keeping a hidden field in
    // sync, so the form can never be a step behind the checkboxes.
    bar.querySelectorAll('form[data-selection-form]').forEach((form) => {
      form.addEventListener('submit', (event) => {
        const ids = checked().map((box) => box.value);
        if (!ids.length) {
          event.preventDefault();
          return;
        }
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

  function init() {
    document.querySelectorAll('[data-selection-scope]').forEach(setup);
    document.querySelectorAll('[data-bulk-field]').forEach(wireFieldPicker);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
