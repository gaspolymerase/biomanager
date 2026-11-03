/* The zebrafish pages (zebrafish.html): delete confirmations, the genotyping flag, the
 * water chart (it draws only where the Water view has one). #zebrafish-page-data has the flag's address. */
const ZEBRAFISH = JSON.parse(document.getElementById('zebrafish-page-data').textContent);
/* Inline editing, the save indicator and the live dots come from
   static/sheet.js (data-autosave-sheet on each sheet). */

// Delete confirmations say what else the delete touches.
document.querySelectorAll('form[data-confirm-delete]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (form.dataset.confirmed === '1') { delete form.dataset.confirmed; return; }
    event.preventDefault();
    const submitter = event.submitter;
    BioDialog.confirm(form.dataset.confirmDelete, { danger: true }).then((ok) => {
      if (!ok) return;
      form.dataset.confirmed = '1';
      form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
    });
  });
});

// Genotyping flag, on the tank sheet and the queue.
const GENO_URL = ZEBRAFISH.genoUrl;
document.querySelectorAll('.flag-toggle[data-tank-id]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const status = document.querySelector('[data-save-status]');
    try {
      const response = await fetch(GENO_URL.replace('/0/', `/${btn.dataset.tankId}/`), { method: 'POST', headers: { 'X-Autosave': '1' } });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.error || t('The server answered %(status)s.', { status: response.status }));
      btn.setAttribute('aria-pressed', result.needs_genotyping ? 'true' : 'false');
      btn.querySelector('[data-flag-label]').textContent = result.needs_genotyping ? t('Queued') : t('No');
      const row = btn.closest('tr');
      if (row) row.dataset.geno = result.needs_genotyping ? '1' : '';
    } catch (error) {
      if (status) { status.dataset.state = 'error'; status.textContent = t('Couldn’t change the genotyping flag: %(error)s', { error: error.message }); }
    }
  });
});

// The sacrifice log opens when linked to (#sac-log).
if (location.hash === '#sac-log') {
  const sac = document.getElementById('sac-log');
  if (sac) sac.open = true;
}

// Returned on a mating tank opens its dialog; so does a link to it
// (#return-12, from the home page when a group has no recorded home).
document.addEventListener('click', (event) => {
  const opener = event.target.closest('[data-open-dialog]');
  const dialog = opener && document.getElementById(opener.dataset.openDialog);
  if (dialog && dialog.showModal) dialog.showModal();
});
if (/^#return-\d+$/.test(location.hash)) {
  const dialog = document.getElementById(location.hash.slice(1));
  if (dialog && dialog.showModal) dialog.showModal();
}

// Sac log: a picked fish row brings its own tank and line, and caps the count.
document.querySelectorAll('form[data-sac-form]').forEach((form) => {
  const fish = form.querySelector('[data-sac-fish]');
  const count = form.querySelector('[data-sac-count]');
  const sync = () => {
    const picked = fish.selectedOptions[0];
    const row = Boolean(fish.value);
    form.querySelectorAll('[data-sac-loose]').forEach((el) => {
      el.hidden = row;
      el.querySelectorAll('select').forEach((s) => { s.disabled = row; });
    });
    if (row && picked.dataset.count) count.max = picked.dataset.count; else count.removeAttribute('max');
  };
  fish.addEventListener('change', sync);
  sync();
});

// New tank: "How many" only for new tanks.
const tankDialog = document.getElementById('tank-dialog');
if (tankDialog) {
  tankDialog.addEventListener('record-dialog:open', (event) => {
    const isNew = event.detail && event.detail.isNew;
    tankDialog.querySelectorAll('[data-new-only]').forEach((el) => {
      el.hidden = !isNew;
      el.querySelectorAll('input').forEach((input) => { input.disabled = !isNew; });
    });
  });
}

// Water trend chart. Apple system colours: temperature red, pH purple,
// conductivity blue.
const WATER_SERIES = [
  ['temp', '°C', '#ff3b30'],
  ['ph', 'pH', '#af52de'],
  ['cond', 'µS/cm', '#007aff'],
];
document.querySelectorAll('.water-chart[data-system-id]').forEach((host) => {
  fetch(`/zebrafish/water/${host.dataset.systemId}/series.json`)
    .then((r) => r.json())
    .then((j) => { host.innerHTML = renderChart((j && j.series) || []); })
    .catch(() => { host.innerHTML = `<div class="water-chart-empty">${t('Couldn’t load readings.')}</div>`; });
});
function renderChart(series) {
  if (!series.length) return `<div class="water-chart-empty">${t('No readings in the last 30 days.')}</div>`;
  const W = 460, H = 140, P = 16;
  const xs = series.map((p) => new Date(p.t).getTime());
  const xMin = Math.min(...xs), xRange = Math.max(1, Math.max(...xs) - xMin);
  const line = (key, color) => {
    const pts = series.filter((p) => p[key] != null).map((p) => ({ x: new Date(p.t).getTime(), y: p[key] }));
    if (!pts.length) return '';
    const yMin = Math.min(...pts.map((p) => p.y)), yRange = Math.max(0.01, Math.max(...pts.map((p) => p.y)) - yMin);
    const d = pts.map((p, i) => {
      const x = P + (p.x - xMin) / xRange * (W - 2 * P);
      const y = H - P - (p.y - yMin) / yRange * (H - 2 * P);
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(' ');
    return `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>`;
  };
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="water-chart-svg" role="img" aria-label="${t('Water readings, last 30 days')}">
      ${WATER_SERIES.map(([k, , c]) => line(k, c)).join('')}
    </svg>
    <div class="water-legend">${WATER_SERIES.map(([, l, c]) => `<span><i style="background:${c}"></i>${l}</span>`).join('')}</div>`;
}
