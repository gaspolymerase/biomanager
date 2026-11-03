/* Shared CSV import modal — triggered by openCsvImport('mouse'|'plasmid'|'order').
 *
 * Creates a one-shot overlay with: file picker, "Preview" (dry-run) and
 * "Import" buttons. Hits /import/<entity> with form-data. Shows per-row
 * errors when present.
 */
(function () {
  'use strict';

  const SCHEMA = {
    mouse: {
      label: 'mice',
      hint: 'Headers: mouse_id (optional), gender, genotype, owner, status, note',
      template: 'mouse_id,gender,genotype,owner,status,note\n,F,DBH-Cre,HXT,breeder,founder',
    },
    plasmid: {
      label: 'plasmids',
      hint: 'Headers: plasmid_id (optional), name, backbone, insert_seq, resistance, owner, location, notes',
      template: 'plasmid_id,name,backbone,insert_seq,resistance,owner,location,notes\n,pCAG-GFP,pCAG,EGFP,Amp,HXT,-80 box A,from Addgene',
    },
    order: {
      label: 'orders',
      hint: 'Headers: requester_name, vendor_name, item_name, catalog_number, quantity, status, notes',
      template: 'requester_name,vendor_name,item_name,catalog_number,quantity,status,notes\nHXT,Sigma,NaCl,S7653,500g,requested,',
    },
  };

  let modalEl = null;

  function buildModal() {
    if (modalEl) return modalEl;
    modalEl = document.createElement('div');
    modalEl.className = 'csv-modal';
    modalEl.hidden = true;
    modalEl.innerHTML = `
      <div class="csv-modal-backdrop"></div>
      <div class="csv-modal-card" role="dialog" aria-label="${escapeHtml(t('Import CSV'))}">
        <header class="csv-modal-head">
          <h3>${escapeHtml(t('Import CSV'))}</h3>
          <button type="button" class="csv-modal-close" aria-label="${escapeHtml(t('Close'))}">×</button>
        </header>
        <div class="csv-modal-body">
          <p class="csv-modal-hint" id="csv-modal-hint"></p>
          <div class="csv-template-line">
            <button type="button" class="csv-template-btn" id="csv-template-copy">${escapeHtml(t('Copy header template'))}</button>
          </div>
          <input type="file" id="csv-file" accept=".csv,text/csv">
          <div class="csv-modal-result" id="csv-modal-result"></div>
        </div>
        <footer class="csv-modal-foot">
          <button type="button" class="dt-bottom-action" id="csv-cancel">${escapeHtml(t('Cancel'))}</button>
          <button type="button" class="dt-bottom-action" id="csv-preview-btn">${escapeHtml(t('Preview (dry-run)'))}</button>
          <button type="button" class="dt-bottom-action is-primary" id="csv-import-btn">${escapeHtml(t('Import'))}</button>
        </footer>
      </div>
    `;
    document.body.appendChild(modalEl);
    modalEl.querySelector('.csv-modal-backdrop').addEventListener('click', close);
    modalEl.querySelector('.csv-modal-close').addEventListener('click', close);
    modalEl.querySelector('#csv-cancel').addEventListener('click', close);
    modalEl.querySelector('#csv-preview-btn').addEventListener('click', () => submit(true));
    modalEl.querySelector('#csv-import-btn').addEventListener('click', () => submit(false));
    modalEl.querySelector('#csv-template-copy').addEventListener('click', () => {
      const tpl = SCHEMA[currentEntity] && SCHEMA[currentEntity].template;
      if (tpl) navigator.clipboard.writeText(tpl).then(() => {
        const btn = modalEl.querySelector('#csv-template-copy');
        const orig = btn.textContent;
        btn.textContent = t('Copied!');
        setTimeout(() => { btn.textContent = orig; }, 1200);
      });
    });
    return modalEl;
  }

  let currentEntity = null;

  function open(entity) {
    if (!SCHEMA[entity]) return;
    currentEntity = entity;
    const m = buildModal();
    m.querySelector('#csv-modal-hint').textContent = t(SCHEMA[entity].hint);
    m.querySelector('#csv-modal-result').innerHTML = '';
    m.querySelector('#csv-file').value = '';
    m.hidden = false;
  }

  function close() {
    if (modalEl) modalEl.hidden = true;
  }

  async function submit(dryRun) {
    const fileInput = modalEl.querySelector('#csv-file');
    const file = fileInput.files && fileInput.files[0];
    const result = modalEl.querySelector('#csv-modal-result');
    if (!file) {
      result.innerHTML = `<div class="csv-error">${escapeHtml(t('Pick a .csv file first.'))}</div>`;
      return;
    }
    const fd = new FormData();
    fd.append('file', file);
    if (dryRun) fd.append('dry_run', '1');
    result.innerHTML = `<div class="csv-loading">${escapeHtml(t('Working…'))}</div>`;
    try {
      // A page can say which inventory to import into (window.csvImportModule).
      const target = window.csvImportModule ? `?module=${encodeURIComponent(window.csvImportModule)}` : '';
      const r = await fetch(`/import/${currentEntity}${target}`, { method: 'POST', body: fd, headers: { 'X-Requested-With': 'fetch' } });
      const data = await r.json();
      if (!data.ok) {
        result.innerHTML = `<div class="csv-error">${escapeHtml(data.error || t('Import failed'))}</div>`;
        return;
      }
      const previewRows = (data.preview || []).map((p) => `<li>${escapeHtml(JSON.stringify(p))}</li>`).join('');
      const errs = (data.errors || []).length
        ? `<div class="csv-errors-block"><strong>${escapeHtml(t('%(n)s skipped:', { n: data.errors.length }))}</strong><ul>${data.errors.map((e) => `<li>${escapeHtml(e)}</li>`).join('')}</ul></div>`
        : '';
      result.innerHTML = `
        <div class="csv-success">
          ${escapeHtml(dryRun ? t('Previewed') : t('Imported'))} <strong>${data.count}</strong> ${escapeHtml(t(SCHEMA[currentEntity].label))}.
          ${dryRun ? escapeHtml(' ' + t('Nothing committed yet — click Import to commit.')) : ''}
        </div>
        ${previewRows ? `<details class="csv-preview-details"><summary>${escapeHtml(t('Preview (%(n)s)', { n: data.preview.length }))}</summary><ul>${previewRows}</ul></details>` : ''}
        ${errs}
      `;
      if (!dryRun && data.count > 0) {
        // Reload after a moment so the table refreshes with new rows.
        setTimeout(() => { window.location.reload(); }, 900);
      }
    } catch (err) {
      result.innerHTML = `<div class="csv-error">${escapeHtml(t('Network error: %(error)s', { error: String(err) }))}</div>`;
    }
  }

  const escapeHtml = BioHtml.escape;

  window.openCsvImport = open;

  // <button data-on-click="csv-import" data-arg="order" data-csv-module="…">
  // (actions.js). The button can name the inventory to import into.
  window.BioActions.register({
    'csv-import': function (kind) {
      window.csvImportModule = this.dataset.csvModule || window.csvImportModule;
      open(kind);
    },
  });
})();
