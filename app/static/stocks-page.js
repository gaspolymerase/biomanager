/* A fly or worm database's vials (stocks/module.html): what is particular to vials.
 * The word for its vials is in #stocks-page-data. */
const STOCK_PAGE = JSON.parse(document.getElementById('stocks-page-data').textContent);
/* Inline editing is static/sheet.js (data-autosave-sheet on the card).
   What is particular to vials: */
(function () {
  // A row may wait off the page until it is shown (data-table.js, BioSheets).
  const rowOf = (form) => window.BioSheets.rows().find((tr) => tr.querySelector(`[form="${form.id}"], .cell-text[data-form="${form.id}"]`)) || null;
  // A new purpose shows or hides the cross parents and the egg button.
  document.addEventListener('sheet:change', (event) => {
    const { input } = event.detail;
    if (input.name !== 'purpose') return;
    const row = input.closest('tr');
    if (!row) return;
    row.dataset.purpose = input.value;
    row.dataset.purposeActive = row.dataset.active === 'true' ? input.value : '';
  });
  // After a save: the incubator it now sits in, and a fresh payload for
  // its Open button, so the dialog shows what was just saved.
  document.addEventListener('sheet:saved', (event) => {
    const { form, body } = event.detail;
    const row = rowOf(form);
    if (!row) return;
    const cell = row.querySelector('[data-incubator-cell]');
    if (cell && 'incubator' in body) {
      const named = body.incubator && String(body.incubator).includes(`${body.temp} °C`);
      cell.textContent = body.incubator ? (named ? body.incubator : `${body.incubator} · ${body.temp} °C`) : '–';
      cell.title = body.incubator || '';
    }
    const open = row.querySelector('[data-open-payload]');
    if (open && body.payload) open.dataset.recordPayload = JSON.stringify(body.payload);
  });
  // A move on the grid updates the row (cells, stale-guard copies, payload).
  document.addEventListener('rack-grid:moved', (event) => {
    const { id, rackId, position } = event.detail || {};
    const form = BioRowForms.get(`unit-${id}`);
    if (!form) return;
    // A cell drawn as text (static/text-cells.js) becomes its field to take the value.
    const set = (name, v) => { const tr = rowOf(form); const el = tr && BioTextCells.field(tr, name, `unit-${id}`, true); if (el) el.value = v || ''; };
    set('rack_id', rackId);
    set('position', position);
    form.querySelector('[name="rack_id_was"]').value = rackId || '';
    form.querySelector('[name="position_was"]').value = position || '';
    const open = rowOf(form)?.querySelector('[data-open-payload]');
    if (open) {
      try {
        const payload = JSON.parse(open.dataset.recordPayload);
        Object.assign(payload, { rack_id: rackId || '', rack_id_was: rackId || '', position: position || '', position_was: position || '' });
        open.dataset.recordPayload = JSON.stringify(payload);
      } catch (_) { /* leave it */ }
    }
  });
})();

/* Renaming a genotype relabels its vials: say how many first. */
(function () {
  const dialog = document.getElementById('genotype-dialog');
  if (!dialog) return;
  const form = dialog.querySelector('form');
  let opened = {};
  dialog.addEventListener('record-dialog:open', (event) => { opened = event.detail.data || {}; });
  form.addEventListener('submit', (event) => {
    const now = form.elements.genotype.value.trim();
    if (!opened.id || !opened._uses || now === opened.genotype) return;
    if (form.dataset.confirmed === '1') { delete form.dataset.confirmed; return; }
    event.preventDefault();
    const submitter = event.submitter;
    BioDialog.confirm(t('Rename “%(old)s” to “%(new)s”? %(n)s active %(units)s (and any crosses using it as a parent) will be relabelled.',
                        { old: opened.genotype, new: now, n: opened._uses, units: STOCK_PAGE.unitsWord }),
                      { okLabel: t('Rename') }).then((ok) => {
      if (!ok) return;
      form.dataset.confirmed = '1';
      form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
    });
  });
})();

/* Vial dialog: show the cross / experiment / progeny parts only for
   that purpose, and "How many" only for new vials. */
(function () {
  const dialog = document.getElementById('unit-dialog');
  const form = dialog.querySelector('form');
  const purpose = form.querySelector('[data-purpose-select]');
  const sync = () => {
    const p = purpose.value;
    form.querySelectorAll('[data-show-for]').forEach((el) => {
      el.hidden = !el.dataset.showFor.split(' ').includes(p);
    });
    const isNew = !form.elements.id.value;
    form.querySelectorAll('[data-new-only]').forEach((el) => { el.hidden = !isNew; });
  };
  purpose.addEventListener('change', sync);
  dialog.addEventListener('record-dialog:open', () => setTimeout(sync, 0));
  sync();
})();
