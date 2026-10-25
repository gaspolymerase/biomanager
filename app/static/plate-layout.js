/* The experiment page's Plate dialog (templates/experiment.html, app/plates.py):
   a layout of which animal is in which well, with blanks and standards, kept
   with the experiment to read the next plate onto; then a plate reader's or
   qPCR machine's export, pasted or as a file, laid on it. The app works out
   each animal's value and flags the wells; nothing is saved until Save
   readings. A selection of wells is a rectangle, dragged or a whole row or
   column, as the notebook's plate block takes them. A plate fills the
   experiment's main readout or another measure beside it (Readout), and
   standards are read with a line, a log–log line or a 4PL curve, drawn
   beside the plate. */
(function () {
  'use strict';
  const dialog = document.getElementById('xp-plate');
  const page = window.BMExperiment;
  if (!dialog || !page) return;
  const D = () => page.data();
  const URL_BASE = `/experiments/${D().id}/plates`;
  const LETTERS = 'ABCDEFGHIJKLMNOP';
  const FORMATS = { 24: [4, 6], 48: [6, 8], 96: [8, 12], 384: [16, 24] };
  const COLORS = ['var(--viz-1, #2a78d6)', 'var(--viz-2, #eb6834)', 'var(--viz-3, #1baf7a)', 'var(--viz-4, #eda100)',
    'var(--viz-5, #e87ba4)', 'var(--viz-7, #4a3aa7)'];
  const $ = (sel) => dialog.querySelector(sel);
  const $$ = (sel) => Array.from(dialog.querySelectorAll(sel));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
  const num = (v) => (v === null || v === undefined || Number.isNaN(v) ? '' : String(Number(Number(v).toPrecision(4))));
  const FLAG = {
    outlier: t('Far from its replicates'),
    limit: t('Past what the instrument reads'),
    no_value: t('No value (Undetermined, or not a number)'),
  };

  const S = {
    step: 'layout', layouts: [], reads: [], id: null, name: '', format: 96, wells: {}, dirty: false,
    brush: { role: '' }, conc: '', drag: null, result: null, skip: new Set(), timer: null, busy: false,
    main: null, others: [], curveChosen: false,
  };
  const CURVES = { linear: t('Linear'), loglog: t('Log–log'), '4pl': t('4PL (four-parameter logistic)') };

  // ---------------------------------------------------------------- talking to the app

  function showError(text) {
    const el = $('[data-xp-error]');
    el.textContent = text || '';
    el.hidden = !text;
  }

  async function call(url, { json = null, form = null, method = 'POST' } = {}) {
    const init = method === 'GET' ? {} : form ? { method, body: form, headers: { 'X-Autosave': '1' } }
      : { method, body: JSON.stringify(json || {}), headers: { 'Content-Type': 'application/json', 'X-Autosave': '1' } };
    const r = await fetch(url, init);
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) throw new Error(data.error || t('The app answered %(status)s.', { status: r.status }));
    return data;
  }

  // The app's own dialog: the Mac app's web view answers the browser's confirm() with no.
  const ask = (message, danger = false) => BioDialog.confirm(message, { danger });

  // ---------------------------------------------------------------- wells

  const wellName = (r, c) => `${LETTERS[r]}${c + 1}`;
  const posOf = (w) => [LETTERS.indexOf(w[0]), Number(w.slice(1)) - 1];
  const fits = (w, fmt) => { const [r, c] = posOf(w); const [rows, cols] = FORMATS[fmt]; return r < rows && c < cols; };
  function rect(a, b) {
    const [r1, c1] = posOf(a); const [r2, c2] = posOf(b);
    const out = [];
    for (let r = Math.min(r1, r2); r <= Math.max(r1, r2); r += 1) {
      for (let c = Math.min(c1, c2); c <= Math.max(c1, c2); c += 1) out.push(wellName(r, c));
    }
    return out;
  }
  const subjects = () => D().subjects || [];
  const subjectOf = (key) => subjects().find((s) => s.key === key);
  const groups = () => Array.from(new Set(subjects().map((s) => s.group).filter(Boolean)));
  const colorOf = (key) => {
    const s = subjectOf(key);
    const i = s && s.group ? groups().indexOf(s.group) : -1;
    return COLORS[(i < 0 ? subjects().indexOf(s) : i) % COLORS.length] || COLORS[0];
  };
  const shortLabel = (key) => { const s = subjectOf(key); return s ? s.label.replace(/^#/, '') : '?'; };

  function paint(names) {
    const b = S.brush;
    names.forEach((w) => {
      if (!b.role) delete S.wells[w];
      else if (b.role === 'sample') S.wells[w] = { role: 'sample', subject: b.subject };
      else if (b.role === 'standard') S.wells[w] = { role: 'standard', conc: Number(String(S.conc).replace(',', '.')) };
      else S.wells[w] = { role: b.role };
    });
    S.dirty = true;
    drawGrid();
    drawPalette();
  }

  // ---------------------------------------------------------------- the layout

  function drawLayouts() {
    const select = $('[data-plate-layouts]');
    const options = S.layouts.map((l, i) => `<option value="${l.id}" ${l.id === S.id ? 'selected' : ''}>${esc(l.name)}${i === 0 && l.used_at ? ` · ${esc(t('Same as last plate'))}` : ''}</option>`);
    options.push(`<option value="" ${S.id ? '' : 'selected'}>${esc(t('New layout'))}</option>`);
    select.innerHTML = options.join('');
    $('[data-plate-name]').value = S.name;
    $('[data-plate-format]').value = String(S.format);
    $('[data-plate-delete]').hidden = S.step !== 'layout' || !S.id;
  }

  function load(layout) {
    S.id = layout ? layout.id : null;
    S.name = layout ? layout.name : '';
    S.format = layout ? layout.format : 96;
    S.wells = layout ? { ...layout.wells } : {};
    S.dirty = false;
    drawLayouts();
    drawPalette();
    drawGrid();
  }

  function drawPalette() {
    const counts = {};
    Object.values(S.wells).forEach((w) => { const k = w.role === 'sample' ? w.subject : w.role; counts[k] = (counts[k] || 0) + 1; });
    const item = (role, key, label, extra, color) => {
      const on = S.brush.role === role && (role !== 'sample' || S.brush.subject === key);
      return `<button type="button" class="pl-chip" role="radio" aria-checked="${on}" data-role="${role}" data-key="${esc(key || '')}">
        <i style="background:${color}"></i><span>${esc(label)}</span>${extra ? `<em>${esc(extra)}</em>` : ''}</button>`;
    };
    const subs = subjects().map((s) => item('sample', s.key, s.label, [s.group, counts[s.key] ? `×${counts[s.key]}` : ''].filter(Boolean).join(' '), colorOf(s.key)));
    $('[data-plate-palette]').innerHTML = `
      <div class="pl-chips">${subs.join('') || `<p class="xp-sub">${esc(t('Add animals to the experiment first.'))}</p>`}</div>
      <div class="pl-chips pl-roles">
        ${item('blank', '', t('Blank'), counts.blank ? `×${counts.blank}` : '', 'var(--color-sand-300, #ccc)')}
        ${item('standard', '', t('Standard'), counts.standard ? `×${counts.standard}` : '', 'var(--color-ink-soft, #666)')}
        <label class="pl-conc" ${S.brush.role === 'standard' ? '' : 'hidden'}>${esc(t('Concentration'))} <input data-plate-conc inputmode="decimal" value="${esc(S.conc)}" placeholder="10"></label>
        ${item('', '', t('Empty'), '', 'transparent')}
      </div>`;
  }

  function drawGrid() {
    const [rows, cols] = FORMATS[S.format];
    const reading = S.step === 'read' && S.result;
    const table = $('[data-plate-grid]');
    table.className = `pl-plate pl-${S.format}${reading ? ' is-reading' : ''}`;
    let html = `<thead><tr><th></th>${Array.from({ length: cols }, (_, c) => `<th><button type="button" data-col="${c}">${c + 1}</button></th>`).join('')}</tr></thead><tbody>`;
    for (let r = 0; r < rows; r += 1) {
      html += `<tr><th><button type="button" data-row="${r}">${LETTERS[r]}</button></th>`;
      for (let c = 0; c < cols; c += 1) html += cell(wellName(r, c), reading);
      html += '</tr>';
    }
    table.innerHTML = `${html}</tbody>`;
  }

  function cell(w, reading) {
    const spec = S.wells[w] || {};
    let text = '';
    let style = '';
    const tips = [w];
    if (spec.role === 'sample') { text = shortLabel(spec.subject); style = `--pl-color:${colorOf(spec.subject)}`; tips.push(subjectOf(spec.subject) ? subjectOf(spec.subject).label : t('No longer in the experiment')); }
    if (spec.role === 'blank') { text = t('B'); tips.push(t('Blank')); }
    if (spec.role === 'standard') { text = `S${num(spec.conc)}`; tips.push(`${t('Standard')} ${num(spec.conc)}`); }
    const classes = ['pl-well', spec.role ? `is-${spec.role}` : 'is-empty'];
    if (reading) {
      const info = S.result.wells[w];
      text = '';
      if (info) {
        const reads = Object.entries(info.reads || {});
        if (reads.length) text = reads[0][1].value === null ? reads[0][1].text.slice(0, 5) : num(reads[0][1].value);
        reads.forEach(([target, r]) => {
          tips.push(`${target ? `${target}: ` : ''}${r.text}`);
          r.flags.forEach((f) => { classes.push(`is-${f}`); tips.push(FLAG[f] || f); });
        });
        if (info.skipped) { classes.push('is-skipped'); tips.push(t('Left out (click to put it back)')); }
        else if (reads.length) tips.push(t('Click to leave it out'));
      }
    }
    return `<td><button type="button" class="${classes.join(' ')}" data-well="${w}" style="${style}" title="${esc(tips.join(' · '))}">${esc(text)}</button></td>`;
  }

  function fillInOrder() {
    const reps = Math.max(1, Math.min(24, Number($('[data-plate-reps]').value) || 1));
    const [rows, cols] = FORMATS[S.format];
    const order = [];
    if ($('[data-plate-dir]').value === 'cols') { for (let c = 0; c < cols; c += 1) for (let r = 0; r < rows; r += 1) order.push(wellName(r, c)); }
    else { for (let r = 0; r < rows; r += 1) for (let c = 0; c < cols; c += 1) order.push(wellName(r, c)); }
    const placed = new Set(Object.values(S.wells).filter((w) => w.role === 'sample').map((w) => w.subject));
    const free = order.filter((w) => !S.wells[w]);
    const todo = subjects().filter((s) => !placed.has(s.key));
    if (todo.length * reps > free.length) {
      showError(t('%(n)s animals in %(reps)s wells each need %(need)s empty wells; the plate has %(free)s.', { n: todo.length, reps, need: todo.length * reps, free: free.length }));
      return;
    }
    showError('');
    todo.forEach((s, i) => { free.slice(i * reps, i * reps + reps).forEach((w) => { S.wells[w] = { role: 'sample', subject: s.key }; }); });
    S.dirty = true;
    drawGrid();
    drawPalette();
  }

  async function saveLayout() {
    const name = $('[data-plate-name]').value.trim();
    if (!name) { showError(t('Give the layout a name.')); $('[data-plate-name]').focus(); return false; }
    const data = await call(`${URL_BASE}/layouts`, { json: { id: S.id, name, format: S.format, wells: S.wells } });
    S.layouts = data.layouts;
    S.id = data.id;
    S.name = name;
    S.dirty = false;
    drawLayouts();
    page.say('saved', t('Layout saved'));
    return true;
  }

  function drawReads() {
    const box = $('[data-plate-reads]');
    box.hidden = !S.reads.length;
    box.innerHTML = `<h4 class="xp-h4">${esc(t('Plates read'))}</h4><ul>${S.reads.map((r) => `<li>${esc(r.read_on)} · ${esc(r.layout)}${r.readout ? ` · ${esc(r.readout)}` : ''} · ${esc(t('%(n)s readings', { n: r.count }))}${r.by ? ` · ${esc(r.by)}` : ''}${r.file_url ? ` · <a href="${esc(r.file_url)}" download="${esc(r.file_name)}">${esc(r.file_name || t('File'))}</a>` : ''}</li>`).join('')}</ul>`;
  }

  // ---------------------------------------------------------------- reading a plate onto it

  function step(which) {
    S.step = which;
    $$('[data-plate-step]').forEach((el) => { el.hidden = el.dataset.plateStep !== which; });
    $$('[data-plate-for]').forEach((el) => { el.hidden = el.dataset.plateFor !== which; });
    $('[data-plate-delete]').hidden = which !== 'layout' || !S.id;
    // The plate moves with the step: drawn on the layout, then as the values read (the curve beside it).
    const grid = $('.pl-plate-wrap');
    if (which === 'read') $('[data-plate-top]').prepend(grid); else $('.pl-work').append(grid);
    drawGrid();
  }

  function formOf() {
    const form = new FormData();
    form.set('layout_id', S.id || '');
    form.set('on', $('[data-plate-on]').value || today());
    const file = $('[data-plate-file]').files[0];
    if (file) form.set('file', file); else form.set('text', $('[data-plate-text]').value);
    const opts = $('[data-plate-options]');
    const pick = (name) => { const el = opts.querySelector(`[name="${name}"]`); return el ? (el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value) : ''; };
    ['target', 'reference', 'subtract_blank', 'standard_curve'].forEach((n) => { const v = pick(n); if (v !== '') form.set(n, v); });
    if (S.curveChosen && pick('curve')) form.set('curve', pick('curve'));
    form.set('measure', $('[data-plate-measure]').value);
    if ($('[data-plate-measure]').value === 'new') {
      form.set('measure_label', $('[data-plate-measure-label]').value.trim());
      form.set('measure_unit', $('[data-plate-measure-unit]').value.trim());
      // A measure worked out from two genes' Cts (ΔCt, ΔΔCt, fold change): the genes are asked for below, as for the main readout.
      form.set('measure_kind', $('[data-plate-measure-kind]').value);
      form.set('measure_control', $('[data-plate-measure-control]').value.trim());
    }
    form.set('skip', Array.from(S.skip).join(','));
    return form;
  }

  const hasExport = () => $('[data-plate-file]').files.length || $('[data-plate-text]').value.trim();

  async function preview() {
    if (!hasExport()) { S.result = null; drawPreview(); return; }
    try {
      S.result = await call(`${URL_BASE}/preview`, { form: formOf() });
      showError('');
    } catch (e) {
      S.result = null;
      showError(e.message);
    }
    drawOptions();
    drawPreview();
  }
  const later = () => { clearTimeout(S.timer); S.timer = setTimeout(preview, 400); };

  function drawOptions() {
    const r = S.result;
    const box = $('[data-plate-options]');
    const keep = (name) => { const el = box.querySelector(`[name="${name}"]`); return el ? (el.type === 'checkbox' ? el.checked : el.value) : null; };
    const was = { target: keep('target'), reference: keep('reference'), subtract_blank: keep('subtract_blank'), standard_curve: keep('standard_curve') };
    if (!r) { box.innerHTML = ''; return; }
    const select = (name, label, chosen) => `<label>${esc(label)} <select name="${name}">${r.targets.map((x) => `<option value="${esc(x)}" ${x === chosen ? 'selected' : ''}>${esc(x || t('Values'))}</option>`).join('')}</select></label>`;
    const parts = [];
    if (r.mode === 'ct') {
      parts.push(select('target', t('Target gene'), r.target), select('reference', t('Reference gene'), r.reference));
    } else if (r.targets.length > 1) {
      parts.push(select('target', t('Which read'), r.target));
    }
    if (r.mode === 'value' && r.blank.n) {
      const on = was.subtract_blank === null ? r.blank.subtracted : was.subtract_blank;
      parts.push(`<label class="xp-check"><input type="checkbox" name="subtract_blank" ${on ? 'checked' : ''}> ${esc(t('Take off the blank (mean of %(n)s wells: %(value)s)', { n: r.blank.n, value: num(r.blank.mean) }))}</label>`);
    }
    if (r.mode === 'value' && r.standards.length >= 2) {
      const on = was.standard_curve === null ? Boolean(r.fit) : was.standard_curve;
      parts.push(`<label class="xp-check"><input type="checkbox" name="standard_curve" ${on ? 'checked' : ''}> ${esc(t('Read each value off the standard curve'))}</label>`);
      // Linear, log–log or 4PL: the app's choice until the person picks one.
      if (on && r.fit) {
        parts.push(`<label>${esc(t('Curve'))} <select name="curve">${Object.entries(CURVES).map(([k, label]) => `<option value="${k}" ${k === r.fit.model ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></label>`);
      }
    }
    box.innerHTML = parts.join('');
  }

  function drawPreview() {
    const r = S.result;
    const save = $('[data-plate-save]');
    const out = $('.pl-preview-notes') || (() => { const el = document.createElement('div'); el.className = 'pl-preview-notes'; $('[data-plate-preview]').append(el); return el; })();
    drawGrid();
    if (!r) {
      out.innerHTML = hasExport() ? '' : `<p class="xp-sub">${esc(t('Paste the export or choose its file: its values show on the plate here, with each animal\'s value below, before anything is saved.'))}</p>`;
      save.disabled = true;
      save.textContent = t('Save readings');
      return;
    }
    const unit = r.readout.unit ? ` (${t(r.readout.unit)})` : '';
    const notes = [];
    const flagged = Object.entries(r.flagged).filter(([, ws]) => ws.length);
    if (flagged.length) notes.push(`<div class="pl-legend">${flagged.map(([f, ws]) => `<span class="pl-key is-${f}"></span>${esc(FLAG[f])}: ${esc(ws.join(', '))}`).join('<br>')}</div>`);
    if (r.blank.subtracted) notes.push(esc(t('Blank: mean of %(n)s wells, %(value)s, taken off each.', { n: r.blank.n, value: num(r.blank.mean) })));
    if (r.fit) notes.push(esc(fitText(r.fit)));
    if (r.skipped.length) notes.push(esc(t('Left out by hand: %(wells)s.', { wells: r.skipped.join(', ') })));
    r.warnings.forEach((w) => notes.push(esc(w)));
    if (r.replaces) notes.push(`<b>${esc(t('This replaces %(n)s readings already saved for that day.', { n: r.replaces }))}</b>`);
    const ct = r.mode === 'ct';
    const head = ct ? [t('Animal'), t('Group'), t('Its wells'), `${r.target} Ct`, `${r.reference} Ct`, `${t(r.readout.label)}${unit}`]
      : [t('Animal'), t('Group'), t('Its wells'), t('Mean (CV)'), `${t(r.readout.label)}${unit}`];
    const meanOf = (row, target) => {
      const m = row.reads && row.reads[target];
      return m ? `${num(m.mean)}${m.cv !== null && m.cv !== undefined ? ` <span class="xp-muted">(${m.cv}%)</span>` : ''}` : '';
    };
    const rows = r.subjects.map((row) => `<tr class="${row.error ? 'is-error' : ''}"><td>${esc(row.label)}</td><td>${esc(row.group)}</td><td>${esc(row.wells.join(' '))}</td>
      ${ct ? `<td>${meanOf(row, r.target)}</td><td>${meanOf(row, r.reference)}</td>` : `<td>${meanOf(row, r.target)}</td>`}
      <td>${row.error ? `<span class="xp-danger-text">${esc(row.error)}</span>` : `<b>${num(row.value)}</b>${row.outside ? ` <span class="xp-warn">${esc(t('outside the standards'))}</span>` : ''}`}</td></tr>`);
    drawCurve(r);
    out.innerHTML = `${notes.map((n) => `<p class="xp-sub">${n}</p>`).join('')}
      <div class="table-wrap"><table class="dense-table fit-table"><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
    save.disabled = !r.ready;
    save.textContent = r.ready ? t('Save %(n)s readings', { n: r.ready }) : t('Save readings');
  }

  // The standard curve in words: its equation, how well it fits, and that it was chosen for them.
  function fitText(f) {
    const auto = f.auto ? ` ${t('(chosen for these standards; change it under Curve)')}` : '';
    if (f.model === '4pl') {
      return t('Standard curve, 4PL: signal = %(d)s + (%(a)s − %(d)s) / (1 + (concentration / %(c)s)^%(b)s); r² = %(r2)s, typical distance of a standard from it %(rmse)s.', { a: num(f.a), b: num(f.b), c: num(f.c), d: num(f.d), r2: f.r2, rmse: num(f.rmse) }) + auto;
    }
    if (f.model === 'loglog') {
      return t('Standard curve, log–log: log signal = %(slope)s × log concentration + %(intercept)s; r² = %(r2)s (on the logarithms).', { slope: num(f.slope), intercept: num(f.intercept), r2: f.r2 })
        + (f.left_out ? ` ${t('%(n)s standard wells at zero were left out (no logarithm).', { n: f.left_out })}` : '') + auto;
    }
    return t('Standard curve: signal = %(slope)s × concentration + %(intercept)s, r² = %(r2)s.', { slope: num(f.slope), intercept: num(f.intercept), r2: f.r2 }) + auto;
  }

  // The standards, the curve through them and each animal read off it, beside the plate.
  function drawCurve(r) {
    const box = $('[data-plate-curve]');
    const c = r && r.curve;
    if (!c || !c.line.length) { box.hidden = true; box.innerHTML = ''; return; }
    const pts = c.standards.filter(([x]) => !c.log || x > 0);
    const sampled = c.samples.filter(([x]) => !c.log || x > 0);
    const xsAll = [...pts.map((p) => p[0]), ...c.line.map((p) => p[0]), ...sampled.map((p) => p[0])];
    const ysAll = [...pts.map((p) => p[1]), ...c.line.map((p) => p[1]), ...sampled.map((p) => p[1])];
    const fx = (x) => (c.log ? Math.log10(x) : x);
    let [x0, x1] = [Math.min(...xsAll.map(fx)), Math.max(...xsAll.map(fx))];
    let [y0, y1] = [Math.min(...ysAll), Math.max(...ysAll)];
    if (x1 === x0) { x0 -= 1; x1 += 1; }
    if (y1 === y0) { y0 -= 1; y1 += 1; }
    const padY = (y1 - y0) * 0.08; y0 -= padY; y1 += padY;
    const W = 320; const H = 220; const L = 40; const R = 8; const T = 8; const B = 30;
    const sx = (x) => L + ((fx(x) - x0) / (x1 - x0)) * (W - L - R);
    const sy = (y) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t('Standard curve'))}">`;
    for (let i = 0; i <= 3; i += 1) {
      const y = y0 + ((y1 - y0) * i) / 3;
      svg += `<line x1="${L}" x2="${W - R}" y1="${sy(y)}" y2="${sy(y)}" class="pl-curve-grid"/><text x="${L - 4}" y="${sy(y) + 3}" text-anchor="end" class="pl-curve-axis">${num(y)}</text>`;
    }
    const ticks = c.log ? Array.from({ length: Math.floor(x1) - Math.ceil(x0) + 1 }, (_, i) => 10 ** (Math.ceil(x0) + i))
      : [0, 1, 2, 3].map((i) => x0 + ((x1 - x0) * i) / 3);
    ticks.forEach((x) => { svg += `<text x="${sx(x)}" y="${H - B + 13}" text-anchor="middle" class="pl-curve-axis">${num(x)}</text>`; });
    svg += `<text x="${(L + W - R) / 2}" y="${H - 3}" text-anchor="middle" class="pl-curve-axis">${esc(c.log ? t('concentration (log scale)') : t('concentration'))}</text>`;
    svg += `<path class="pl-curve-line" d="${c.line.map(([x, y], i) => `${i ? 'L' : 'M'}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(' ')}"/>`;
    pts.forEach(([x, y]) => { svg += `<circle class="pl-curve-std" cx="${sx(x)}" cy="${sy(y)}" r="3"><title>${esc(`${t('Standard')} ${num(x)}: ${num(y)}`)}</title></circle>`; });
    const outside = new Set(r.subjects.filter((s) => s.outside).map((s) => s.value));
    sampled.forEach(([x, y]) => { svg += `<rect class="pl-curve-sample${outside.has(x) ? ' is-outside' : ''}" x="${sx(x) - 3.5}" y="${sy(y) - 3.5}" width="7" height="7"/>`; });
    svg += '</svg>';
    box.innerHTML = `${svg}<figcaption>${esc(t('Dots: the standards. Squares: each animal, read off the curve (dashed: outside the standards).'))}</figcaption>`;
    box.hidden = false;
  }

  // Which readout the plate fills: the main one when a plate gives it, else the measure last read, else a new one.
  function drawMeasures() {
    const select = $('[data-plate-measure]');
    const main = S.main || { key: '', label: '', plate: true };
    const options = [`<option value="main" ${main.plate ? '' : 'disabled'}>${esc(t('%(readout)s (main readout)', { readout: t(main.label) }))}</option>`]
      .concat(S.others.map((m) => `<option value="${esc(m.key)}">${esc(m.label)}${m.unit ? ` (${esc(m.unit)})` : ''}</option>`))
      .concat([`<option value="new">${esc(t('Another measure…'))}</option>`]);
    select.innerHTML = options.join('');
    const last = (S.reads[0] || {}).readout_key;
    const keys = S.others.map((m) => m.key);
    select.value = last && keys.includes(last) ? last : (main.plate ? 'main' : (keys[0] || 'new'));
    $('[data-plate-measure-label]').value = '';
    $('[data-plate-measure-unit]').value = '';
    $('[data-plate-measure-kind]').value = '';
    $('[data-plate-measure-control]').value = '';
    showNewMeasure();
  }
  function showNewMeasure() {
    const fresh = $('[data-plate-measure]').value === 'new';
    const kind = $('[data-plate-measure-kind]').value;
    $$('[data-plate-new-measure]').forEach((el) => { el.hidden = !fresh; });
    // From Cts there is no unit to give; ΔΔCt and fold change are against a control group.
    $('[data-plate-new-unit]').hidden = !fresh || Boolean(kind);
    $('[data-plate-new-control]').hidden = !fresh || !['delta_delta_ct', 'fold_change'].includes(kind);
  }

  async function savePlate() {
    if (S.busy) return;
    S.busy = true;
    const button = $('[data-plate-save]');
    button.disabled = true;
    try {
      const data = await call(`${URL_BASE}/save`, { form: formOf() });
      page.apply(data);
      if (page.show) page.show(data.filled);
      page.say('saved', data.message || t('All changes saved'));
      dialog.close();
    } catch (e) {
      showError(e.message);
      button.disabled = false;
    } finally {
      S.busy = false;
    }
  }

  // ---------------------------------------------------------------- opening, and what each control does

  async function openDialog() {
    showError('');
    S.result = null;
    S.skip = new Set();
    $('[data-plate-text]').value = '';
    $('[data-plate-file]').value = '';
    $('[data-plate-on]').value = today();
    $('[data-plate-on]').max = today();
    try {
      const data = await call(`${URL_BASE.replace(/\/plates$/, '/plates.json')}`, { method: 'GET' });
      S.layouts = data.layouts;
      S.reads = data.reads;
      S.main = data.main;
      S.others = data.others || [];
    } catch (e) {
      showError(e.message);
    }
    load(S.layouts[0] || null);     // Same as last plate
    if (S.brush.role !== 'blank' && S.brush.role !== 'standard' && !subjectOf(S.brush.subject) && subjects()[0]) {
      S.brush = { role: 'sample', subject: subjects()[0].key };
    }
    S.curveChosen = false;
    drawPalette();
    drawReads();
    drawMeasures();
    step('layout');
    if (!dialog.open) dialog.showModal();
  }

  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-plate-open]')) openDialog();
  });

  dialog.addEventListener('click', async (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    if (btn.matches('.pl-chip')) {
      S.brush = btn.dataset.role === 'sample' ? { role: 'sample', subject: btn.dataset.key } : { role: btn.dataset.role };
      drawPalette();
      if (S.brush.role === 'standard') { const c = $('[data-plate-conc]'); if (c) c.focus(); }
    } else if (btn.dataset.col !== undefined || btn.dataset.row !== undefined) {
      if (S.step !== 'layout') return;
      const [rows, cols] = FORMATS[S.format];
      const names = btn.dataset.col !== undefined
        ? Array.from({ length: rows }, (_, r) => wellName(r, Number(btn.dataset.col)))
        : Array.from({ length: cols }, (_, c) => wellName(Number(btn.dataset.row), c));
      if (S.brush.role === 'standard' && !(String(S.conc).trim())) { showError(t('Type the standard\'s concentration first.')); return; }
      paint(names);
    } else if (btn.dataset.well && S.step === 'read') {
      if (!S.result || !S.result.wells[btn.dataset.well]) return;
      if (S.skip.has(btn.dataset.well)) S.skip.delete(btn.dataset.well); else S.skip.add(btn.dataset.well);
      preview();
    } else if (btn.matches('[data-plate-fill]')) {
      fillInOrder();
    } else if (btn.matches('[data-plate-clear]')) {
      S.wells = {};
      S.dirty = true;
      drawGrid();
      drawPalette();
    } else if (btn.matches('[data-plate-save-layout]')) {
      try { await saveLayout(); showError(''); } catch (err) { showError(err.message); }
    } else if (btn.matches('[data-plate-next]')) {
      try {
        if ((S.dirty || !S.id) && !(await saveLayout())) return;
        showError('');
        step('read');
        drawOptions();
        drawPreview();
        if (hasExport()) preview();
      } catch (err) { showError(err.message); }
    } else if (btn.matches('[data-plate-back]')) {
      showError('');
      step('layout');
    } else if (btn.matches('[data-plate-save]')) {
      savePlate();
    } else if (btn.matches('[data-plate-delete]')) {
      if (!(await ask(t('Delete the layout %(name)s? The plates already read onto it keep their copy.', { name: S.name }), true))) return;
      try {
        const data = await call(`${URL_BASE}/layouts/${S.id}/delete`);
        S.layouts = data.layouts;
        load(S.layouts[0] || null);
      } catch (err) { showError(err.message); }
    }
  });

  // Dragging across wells paints them all.
  dialog.addEventListener('pointerdown', (e) => {
    const btn = e.target.closest('[data-well]');
    if (!btn || S.step !== 'layout') return;
    if (S.brush.role === 'standard' && !(String(S.conc).trim())) { showError(t('Type the standard\'s concentration first.')); return; }
    e.preventDefault();
    S.drag = { from: btn.dataset.well, to: btn.dataset.well };
    btn.classList.add('is-picked');
  });
  dialog.addEventListener('pointerover', (e) => {
    const btn = e.target.closest('[data-well]');
    if (!S.drag || !btn) return;
    S.drag.to = btn.dataset.well;
    const picked = new Set(rect(S.drag.from, S.drag.to));
    $$('[data-well]').forEach((el) => el.classList.toggle('is-picked', picked.has(el.dataset.well)));
  });
  document.addEventListener('pointerup', () => {
    if (!S.drag) return;
    const names = rect(S.drag.from, S.drag.to);
    S.drag = null;
    paint(names);
  });

  dialog.addEventListener('change', async (e) => {
    const el = e.target;
    if (el.matches('[data-plate-layouts]')) {
      load(S.layouts.find((l) => String(l.id) === el.value) || null);
    } else if (el.matches('[data-plate-format]')) {
      const fmt = Number(el.value);
      const off = Object.keys(S.wells).filter((w) => !fits(w, fmt));
      if (off.length && !(await ask(t('%(n)s wells are off a %(fmt)s-well plate and will be emptied.', { n: off.length, fmt })))) {
        el.value = String(S.format);
        return;
      }
      off.forEach((w) => delete S.wells[w]);
      S.format = fmt;
      S.dirty = true;
      drawGrid();
    } else if (el.matches('[data-plate-file]')) {
      if (el.files.length) $('[data-plate-text]').value = '';
      S.skip = new Set();
      preview();
    } else if (el.matches('[data-plate-measure-kind]')) {
      showNewMeasure();
      preview();
    } else if (el.matches('[data-plate-measure]')) {
      showNewMeasure();
      if ($('[data-plate-measure]').value === 'new') $('[data-plate-measure-label]').focus();
      preview();
    } else if (el.closest('[data-plate-options]') || el.matches('[data-plate-on]')) {
      if (el.name === 'curve') S.curveChosen = true;
      if (el.name === 'standard_curve' && !el.checked) S.curveChosen = false;
      preview();
    }
  });
  dialog.addEventListener('input', (e) => {
    if (e.target.matches('[data-plate-name]')) { S.dirty = true; }
    if (e.target.matches('[data-plate-conc]')) { S.conc = e.target.value; }
    if (e.target.matches('[data-plate-text]')) { $('[data-plate-file]').value = ''; S.skip = new Set(); later(); }
    if (e.target.matches('[data-plate-measure-label], [data-plate-measure-unit], [data-plate-measure-control]')) later();
  });
}());
