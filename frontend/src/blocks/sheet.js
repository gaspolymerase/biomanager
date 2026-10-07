// Data sheet block: a small spreadsheet in the page, with a plot and
// statistics drawn from it.
//
// data = {
//   title, columns: [{ name, type: 'text'|'number'|'formula', formula, unit }],
//   rows: [[cell, ...], ...],
//   showPlot, showStats,
//   chart: { type: 'bar'|'box'|'dots'|'scatter'|'line', x, y: [..], group, error, fit, logY, showNs },
//   stats: { group, value, test, control }
// }
// Cells are kept as typed text; formula columns are computed on the fly.
// Paste a block copied from Excel or Sheets into any cell and it spreads
// out from there, adding rows and columns as needed. A pasted or imported
// log over time (looksLikeLog) is drawn as a line.

import { computeSheet, colLetter } from './formula.js';
import { compare, linearFit, summary, TESTS } from './stats.js';
import { renderPlot, svgText, svgToPng } from './plot.js';
import { ask, debounce, download, el, escapeHtml, fmt, fmtP, isNum, parseDelimited, stars, toCsv, toNumber } from '../util.js';

export function defaultSheet() {
  return {
    title: '',
    columns: [{ name: 'Group', type: 'text' }, { name: 'Value', type: 'number', unit: '' }],
    rows: [['Control', ''], ['Control', ''], ['Control', ''], ['Treated', ''], ['Treated', ''], ['Treated', '']],
    showPlot: true,
    showStats: true,
    chart: { type: 'bar', x: 0, y: [1], group: -1, error: 'sem', fit: false, logY: false },
    stats: { group: 0, value: 1, test: 'auto', control: '' },
  };
}

function normalize(data) {
  const d = { ...defaultSheet(), ...(data || {}) };
  d.columns = Array.isArray(d.columns) && d.columns.length ? d.columns : defaultSheet().columns;
  d.rows = Array.isArray(d.rows) ? d.rows.map((r) => (Array.isArray(r) ? r : [])) : [];
  d.chart = { ...defaultSheet().chart, ...(d.chart || {}) };
  if (!Array.isArray(d.chart.y)) d.chart.y = [Number(d.chart.y) || 1];
  d.stats = { ...defaultSheet().stats, ...(d.stats || {}) };
  return d;
}

// A log from an instrument: time (or any steadily rising number) down the
// first column and readings beside it, like a reactor's temperature and
// pressure. It reads as a line over time, not as groups to compare.
const TIME_HEADER = /\b(t|times?|mins?|minutes?|s|secs?|seconds?|h|hrs?|hours?|days?|elapsed|date|datetime|timestamp)\b|时间|时刻/i;

// A number, a clock time (10:32, 10:32:05.5) or a date (2026-10-05 10:32).
const timeLike = (v) => isNum(v) || /^\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(v)
  || /^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$/.test(v);

export function looksLikeLog(columns, rows) {
  if (columns.length < 2) return false;
  const first = rows.map((r) => String(r[0] ?? '').trim()).filter(Boolean);
  if (first.length < 5) return false;
  const readings = columns.some((_c, j) => j > 0 && rows.some((r) => isNum(r[j]))
    && rows.every((r) => String(r[j] ?? '').trim() === '' || isNum(r[j])));
  if (!readings || !first.every(timeLike)) return false;
  if (TIME_HEADER.test(columns[0].name || '')) return true;
  const xs = first.map(toNumber);
  return xs.every(Number.isFinite) && xs.every((x, k) => k === 0 || x > xs[k - 1]);
}

const CHART_TYPES = {
  bar: 'Bar (mean ± error)', dots: 'Dot plot', box: 'Box plot', scatter: 'Scatter (x–y)', line: 'Line (x–y)',
};

export function mountSheet(host, ctx) {
  let data = normalize(ctx.data);
  let editable = ctx.editable;
  const commit = debounce(() => ctx.commit(JSON.parse(JSON.stringify(data))), 350);

  const root = el('div', { class: 'nb-sheet' });
  host.appendChild(root);

  function colIndexOptions(selected, { allowNone = false, numericOnly = false } = {}) {
    const opts = allowNone ? [`<option value="-1"${selected === -1 ? ' selected' : ''}>—</option>`] : [];
    data.columns.forEach((c, i) => {
      if (numericOnly && c.type === 'text') return;
      opts.push(`<option value="${i}"${Number(selected) === i ? ' selected' : ''}>${colLetter(i)} · ${escapeHtml(c.name || 'Column')}</option>`);
    });
    return opts.join('');
  }

  // ---------------------------------------------------------------- table

  function renderTable() {
    const { values, errors } = computeSheet(data);
    const table = el('table', { class: 'nb-sheet-table' });
    const head = el('tr');
    head.appendChild(el('th', { class: 'nb-sheet-corner' }));
    data.columns.forEach((col, ci) => {
      const th = el('th', { 'data-col': ci });
      const letter = el('span', { class: 'nb-sheet-letter', text: colLetter(ci) });
      const name = el('input', { class: 'nb-sheet-colname', value: col.name || '', placeholder: 'Column', disabled: !editable, 'aria-label': `Column ${colLetter(ci)} name` });
      name.addEventListener('input', () => { col.name = name.value; commit(); refreshPanels(); });
      const typeSel = el('select', { class: 'nb-sheet-coltype', disabled: !editable, 'aria-label': 'Column type', title: 'Column type' });
      typeSel.innerHTML = ['text', 'number', 'formula'].map((t) => `<option value="${t}"${(col.type || 'text') === t ? ' selected' : ''}>${t === 'text' ? 'Aa' : t === 'number' ? '123' : 'ƒx'}</option>`).join('');
      typeSel.addEventListener('change', () => {
        col.type = typeSel.value;
        if (col.type === 'formula' && !col.formula) col.formula = `=${colLetter(Math.max(0, ci - 1))}*1`;
        commit(); renderAll();
      });
      const menu = el('button', { type: 'button', class: 'nb-sheet-colmenu', title: 'Remove column', hidden: !editable, html: '×', 'aria-label': 'Remove column' });
      menu.addEventListener('click', async () => {
        if (data.columns.length <= 1) return;
        if (!(await ask.confirm(`Remove column “${col.name || colLetter(ci)}”?`, { danger: true }))) return;
        data.columns.splice(ci, 1);
        data.rows.forEach((r) => r.splice(ci, 1));
        fixIndices(ci);
        commit(); renderAll();
      });
      th.append(el('div', { class: 'nb-sheet-colhead' }, [letter, name, typeSel, menu]));
      if (col.type === 'formula') {
        const f = el('input', { class: 'nb-sheet-formula' + (errors[ci] ? ' has-error' : ''), value: col.formula || '', placeholder: '=B/mean(B)', disabled: !editable, title: errors[ci] || 'Formula: columns by letter or {name}; mean(), sd(), log2(), sqrt() …' });
        f.addEventListener('change', () => { col.formula = f.value.startsWith('=') ? f.value : `=${f.value}`; commit(); renderAll(); });
        th.appendChild(f);
      } else if (col.type === 'number') {
        const u = el('input', { class: 'nb-sheet-unit', value: col.unit || '', placeholder: 'unit', disabled: !editable, 'aria-label': 'Unit' });
        u.addEventListener('input', () => { col.unit = u.value; commit(); refreshPanels(); });
        th.appendChild(u);
      }
      head.appendChild(th);
    });
    if (editable) {
      const add = el('th', { class: 'nb-sheet-addcol' });
      add.appendChild(el('button', { type: 'button', title: 'Add column', html: '+', onclick: () => {
        data.columns.push({ name: `Column ${colLetter(data.columns.length)}`, type: 'number', unit: '' });
        commit(); renderAll();
      } }));
      head.appendChild(add);
    }
    const thead = el('thead');
    thead.appendChild(head);
    table.appendChild(thead);

    const tbody = el('tbody');
    data.rows.forEach((row, ri) => {
      const tr = el('tr');
      const num = el('td', { class: 'nb-sheet-rownum', text: String(ri + 1), title: editable ? 'Remove row' : '' });
      if (editable) num.addEventListener('click', async () => {
        if (row.some((v) => String(v ?? '').trim()) && !(await ask.confirm(`Remove row ${ri + 1}?`, { danger: true }))) return;
        data.rows.splice(ri, 1);
        commit(); renderAll();
      });
      tr.appendChild(num);
      data.columns.forEach((col, ci) => {
        const td = el('td', { class: col.type === 'number' || col.type === 'formula' ? 'is-num' : '' });
        if (col.type === 'formula') {
          const v = values[ri][ci];
          td.classList.add('is-formula');
          td.textContent = v === '' ? '' : fmt(v, 5);
        } else {
          const raw = row[ci] ?? '';
          const input = el('input', { value: raw, disabled: !editable, 'data-r': ri, 'data-c': ci, 'aria-label': `${colLetter(ci)}${ri + 1}` });
          if (col.type === 'number' && raw !== '' && !isNum(raw)) input.classList.add('is-bad');
          input.addEventListener('input', () => {
            row[ci] = input.value;
            input.classList.toggle('is-bad', col.type === 'number' && input.value !== '' && !isNum(input.value));
            commit();
            refreshComputed();
            refreshPanels();
          });
          input.addEventListener('keydown', (event) => onCellKey(event, ri, ci));
          input.addEventListener('paste', (event) => onPaste(event, ri, ci));
          td.appendChild(input);
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function fixIndices(removed) {
    const shift = (i) => (i === removed ? -1 : i > removed ? i - 1 : i);
    data.chart.x = Math.max(0, shift(data.chart.x));
    data.chart.group = shift(data.chart.group);
    data.chart.y = data.chart.y.map(shift).filter((i) => i >= 0);
    if (!data.chart.y.length) data.chart.y = [Math.min(1, data.columns.length - 1)];
    data.stats.group = Math.max(0, shift(data.stats.group));
    data.stats.value = Math.max(0, shift(data.stats.value));
  }

  function focusCell(r, c) {
    const input = root.querySelector(`input[data-r="${r}"][data-c="${c}"]`);
    if (input) { input.focus(); input.select(); }
  }

  function onCellKey(event, ri, ci) {
    if (event.key === 'Enter' || (event.key === 'ArrowDown' && !event.shiftKey)) {
      event.preventDefault();
      if (ri + 1 >= data.rows.length && event.key === 'Enter') {
        data.rows.push(data.columns.map(() => ''));
        commit(); renderAll();
      }
      focusCell(ri + 1, ci);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      focusCell(Math.max(0, ri - 1), ci);
    }
  }

  // A pasted or imported log is drawn as a line of its first reading over
  // the first column; the Y box picks another reading.
  function plotAsLog() {
    const y = data.columns.findIndex((c, j) => j > 0 && c.type === 'number');
    data.chart = { ...data.chart, type: 'line', x: 0, y: [y > 0 ? y : 1], group: -1, fit: false };
    data.showPlot = true;
    data.showStats = false;
  }

  function onPaste(event, ri, ci) {
    const text = event.clipboardData && event.clipboardData.getData('text/plain');
    if (!text || (!text.includes('\t') && !text.includes('\n'))) return;
    event.preventDefault();
    const grid = parseDelimited(text);
    // A pasted header row names the columns when pasting at the top-left.
    const looksLikeHeader = ri === 0 && ci === 0 && grid.length > 1 && grid[0].every((v) => v && !isNum(v)) && grid[1].some(isNum);
    let rows = grid;
    if (looksLikeHeader) {
      grid[0].forEach((name, j) => {
        if (!data.columns[j]) data.columns.push({ name, type: 'number', unit: '' });
        else data.columns[j].name = name;
      });
      rows = grid.slice(1);
    }
    rows.forEach((cells, dr) => {
      const r = ri + dr;
      while (data.rows.length <= r) data.rows.push(data.columns.map(() => ''));
      cells.forEach((v, dc) => {
        const c = ci + dc;
        while (data.columns.length <= c) {
          data.columns.push({ name: `Column ${colLetter(data.columns.length)}`, type: 'number', unit: '' });
          data.rows.forEach((row) => row.push(''));
        }
        data.rows[r][c] = v.trim();
      });
    });
    // Columns that are all numbers become number columns.
    data.columns.forEach((col, j) => {
      if (col.type === 'formula') return;
      const vals = data.rows.map((r) => r[j]).filter((v) => String(v ?? '').trim() !== '');
      if (vals.length) col.type = vals.every(isNum) ? 'number' : 'text';
    });
    if (looksLikeHeader && data.chart.type === 'bar' && looksLikeLog(data.columns, data.rows)) plotAsLog();
    commit(); renderAll();
    focusCell(ri, ci);
  }

  // ---------------------------------------------------------------- plot + stats

  function numericColumn(ci, values) {
    return values.map((r) => toNumber(r[ci]));
  }

  function groupsFor(groupCol, valueCol, values) {
    const order = [];
    const map = new Map();
    values.forEach((r) => {
      const v = toNumber(r[valueCol]);
      if (!Number.isFinite(v)) return;
      const key = groupCol >= 0 ? String(r[groupCol] ?? '').trim() || '(blank)' : (data.columns[valueCol]?.name || 'Value');
      if (!map.has(key)) { map.set(key, []); order.push(key); }
      map.get(key).push(v);
    });
    return order.map((name) => ({ name, values: map.get(name), summary: summary(map.get(name)) }));
  }

  function colLabel(ci) {
    const c = data.columns[ci];
    if (!c) return '';
    return c.unit ? `${c.name} (${c.unit})` : c.name;
  }

  function buildSpec(values) {
    const ch = data.chart;
    if (ch.type === 'scatter' || ch.type === 'line') {
      const xCol = ch.x;
      const xIsText = data.columns[xCol]?.type === 'text';
      const series = [];
      const ys = ch.y.filter((i) => i >= 0 && i < data.columns.length);
      if (ch.group >= 0) {
        const byGroup = new Map();
        values.forEach((r, ri) => {
          const key = String(r[ch.group] ?? '').trim() || '(blank)';
          if (!byGroup.has(key)) byGroup.set(key, []);
          byGroup.get(key).push({ x: xIsText ? String(r[xCol] ?? '') : toNumber(r[xCol]), y: toNumber(r[ys[0]]), label: `row ${ri + 1}` });
        });
        for (const [name, points] of byGroup) series.push({ name, points });
      } else {
        for (const yi of ys) {
          series.push({ name: colLabel(yi), points: values.map((r, ri) => ({ x: xIsText ? String(r[xCol] ?? '') : toNumber(r[xCol]), y: toNumber(r[yi]), label: `row ${ri + 1}` })) });
        }
      }
      if (ch.fit && !xIsText) {
        for (const s of series) {
          const pts = s.points.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
          s.fit = linearFit(pts.map((p) => p.x), pts.map((p) => p.y));
        }
      }
      const cats = xIsText ? [...new Set(values.map((r) => String(r[xCol] ?? '')).filter(Boolean))] : null;
      return { kind: 'xy', type: ch.type, series, xLabel: colLabel(xCol), yLabel: ch.group >= 0 || ys.length === 1 ? colLabel(ys[0]) : '', xCategories: cats, fit: ch.fit, logY: ch.logY };
    }
    const groupCol = data.stats.group;
    const valueCol = data.stats.value;
    const groups = groupsFor(data.columns[groupCol]?.type === 'text' ? groupCol : -1, valueCol, values);
    const result = compare(groups, data.stats.test, data.stats.control);
    return { kind: 'groups', type: ch.type, groups, error: ch.error, yLabel: colLabel(valueCol), comparisons: result.comparisons, logY: ch.logY, showNs: ch.showNs };
  }

  const plotBox = el('div', { class: 'nb-sheet-plot' });
  const statsBox = el('div', { class: 'nb-sheet-stats' });
  let lastSvg = null;

  function plotControls() {
    const ch = data.chart;
    const xy = ch.type === 'scatter' || ch.type === 'line';
    const bar = el('div', { class: 'nb-sheet-controls' });
    const dis = editable ? '' : ' disabled';
    bar.innerHTML = `
      <label>Chart <select data-k="type"${dis}>${Object.entries(CHART_TYPES).map(([k, v]) => `<option value="${k}"${ch.type === k ? ' selected' : ''}>${v}</option>`).join('')}</select></label>
      ${xy ? `
        <label>X <select data-k="x"${dis}>${colIndexOptions(ch.x)}</select></label>
        <label>Y <select data-k="y"${dis}>${colIndexOptions(ch.y[0], { numericOnly: true })}</select></label>
        <label>Series by <select data-k="group"${dis}>${colIndexOptions(ch.group, { allowNone: true })}</select></label>
        <label class="nb-check"><input type="checkbox" data-k="fit"${ch.fit ? ' checked' : ''}${dis}> Linear fit</label>
      ` : `
        <label>Groups <select data-k="sgroup"${dis}>${colIndexOptions(data.stats.group)}</select></label>
        <label>Values <select data-k="svalue"${dis}>${colIndexOptions(data.stats.value, { numericOnly: true })}</select></label>
        ${ch.type === 'bar' ? `<label>Error bars <select data-k="error"${dis}><option value="sem"${ch.error === 'sem' ? ' selected' : ''}>SEM</option><option value="sd"${ch.error === 'sd' ? ' selected' : ''}>SD</option><option value="none"${ch.error === 'none' ? ' selected' : ''}>None</option></select></label>` : ''}
      `}
      <label class="nb-check"><input type="checkbox" data-k="logY"${ch.logY ? ' checked' : ''}${dis}> Log y</label>
      <span class="nb-spacer"></span>
      <button type="button" class="nb-mini" data-act="svg">SVG</button>
      <button type="button" class="nb-mini" data-act="png">PNG</button>`;
    bar.addEventListener('change', (event) => {
      const k = event.target.dataset.k;
      if (!k) return;
      const v = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
      if (k === 'type') ch.type = v;
      else if (k === 'x') ch.x = Number(v);
      else if (k === 'y') ch.y = [Number(v)];
      else if (k === 'group') ch.group = Number(v);
      else if (k === 'sgroup') data.stats.group = Number(v);
      else if (k === 'svalue') data.stats.value = Number(v);
      else ch[k] = v;
      commit(); refreshPanels();
    });
    bar.addEventListener('click', async (event) => {
      const act = event.target.dataset.act;
      if (!act || !lastSvg) return;
      const name = (data.title || 'plot').replace(/[^\w-]+/g, '-');
      if (act === 'svg') download(`${name}.svg`, svgText(lastSvg), 'image/svg+xml');
      if (act === 'png') download(`${name}.png`, await svgToPng(lastSvg));
    });
    return bar;
  }

  function statsView(values) {
    const box = el('div');
    const dis = editable ? '' : ' disabled';
    const st = data.stats;
    const groupIsText = data.columns[st.group]?.type === 'text';
    const groups = groupsFor(groupIsText ? st.group : -1, st.value, values);
    const controls = el('div', { class: 'nb-sheet-controls' });
    controls.innerHTML = `
      <label>Groups <select data-k="group"${dis}>${colIndexOptions(st.group)}</select></label>
      <label>Values <select data-k="value"${dis}>${colIndexOptions(st.value, { numericOnly: true })}</select></label>
      <label>Test <select data-k="test"${dis}>${Object.entries(TESTS).map(([k, v]) => `<option value="${k}"${st.test === k ? ' selected' : ''}>${v}</option>`).join('')}</select></label>
      ${groups.length > 2 ? `<label>Compare with <select data-k="control"${dis}><option value="">every pair</option>${groups.map((g) => `<option${st.control === g.name ? ' selected' : ''}>${escapeHtml(g.name)}</option>`).join('')}</select></label>` : ''}`;
    controls.addEventListener('change', (event) => {
      const k = event.target.dataset.k;
      if (!k) return;
      st[k] = k === 'group' || k === 'value' ? Number(event.target.value) : event.target.value;
      commit(); refreshPanels();
    });
    box.appendChild(controls);
    if (!groups.length) {
      box.appendChild(el('p', { class: 'nb-muted', text: 'Pick a column of numbers to summarise, and a text column to group them by.' }));
      return box;
    }
    const rows = groups.map((g) => `<tr><th>${escapeHtml(g.name)}</th><td>${g.summary.n}</td><td>${fmt(g.summary.mean)}</td><td>${fmt(g.summary.sd)}</td><td>${fmt(g.summary.sem)}</td><td>${fmt(g.summary.median)}</td><td>${fmt(g.summary.min)} – ${fmt(g.summary.max)}</td></tr>`).join('');
    box.appendChild(el('table', { class: 'nb-stats-table', html: `<thead><tr><th>Group</th><th>n</th><th>Mean</th><th>SD</th><th>SEM</th><th>Median</th><th>Range</th></tr></thead><tbody>${rows}</tbody>` }));
    const result = compare(groups, st.test, st.control);
    if (result.overall) {
      const o = result.overall;
      const detail = o.f !== undefined ? `F(${o.df1}, ${o.df2}) = ${fmt(o.f, 4)}` : o.t !== undefined ? `t(${fmt(o.df, 3)}) = ${fmt(o.t, 4)}` : o.u !== undefined ? `U = ${fmt(o.u, 4)}` : '';
      const lines = [`<p class="nb-stats-result"><b>${escapeHtml(o.test)}</b>: ${detail}, p = ${fmtP(o.p)} <span class="nb-stars">${stars(o.p)}</span>${o.note ? ` <span class="nb-muted">(${escapeHtml(o.note)})</span>` : ''}</p>`];
      if (result.comparisons.length > 1 || o.f !== undefined) {
        lines.push(`<p class="nb-muted">${escapeHtml(result.pairTest || '')}</p><table class="nb-stats-table"><thead><tr><th>Comparison</th><th>p</th><th>adjusted p</th><th></th></tr></thead><tbody>${result.comparisons.map((c) => `<tr><th>${escapeHtml(c.a)} vs ${escapeHtml(c.b)}</th><td>${fmtP(c.p)}</td><td>${fmtP(c.padj)}</td><td>${stars(c.padj)}</td></tr>`).join('')}</tbody></table>`);
      }
      box.appendChild(el('div', { html: lines.join('') }));
    } else if (st.test !== 'none') {
      box.appendChild(el('p', { class: 'nb-muted', text: 'A test needs at least two groups with two or more values each.' }));
    }
    return box;
  }

  function refreshComputed() {
    const { values } = computeSheet(data);
    data.columns.forEach((col, ci) => {
      if (col.type !== 'formula') return;
      root.querySelectorAll(`tbody tr`).forEach((tr, ri) => {
        const td = tr.children[ci + 1];
        if (td) td.textContent = values[ri][ci] === '' ? '' : fmt(values[ri][ci], 5);
      });
    });
  }

  const refreshPanels = debounce(() => {
    const { values } = computeSheet(data);
    plotBox.innerHTML = '';
    statsBox.innerHTML = '';
    plotBox.hidden = !data.showPlot;
    statsBox.hidden = !data.showStats;
    if (data.showPlot) {
      plotBox.appendChild(plotControls());
      const canvas = el('div', { class: 'nb-plot-host' });
      plotBox.appendChild(canvas);
      try {
        lastSvg = renderPlot(canvas, buildSpec(values), { title: data.title });
      } catch (e) {
        canvas.textContent = `Could not draw this plot: ${e.message}`;
      }
    }
    if (data.showStats) statsBox.appendChild(statsView(values));
  }, 120);

  // ---------------------------------------------------------------- frame

  function toolbar() {
    const bar = el('div', { class: 'nb-sheet-toolbar' });
    const title = el('input', { class: 'nb-sheet-title', value: data.title || '', placeholder: 'Sheet title', disabled: !editable });
    title.addEventListener('input', () => { data.title = title.value; commit(); });
    bar.appendChild(title);
    const toggles = el('div', { class: 'nb-seg' });
    for (const [key, label] of [['showPlot', 'Plot'], ['showStats', 'Stats']]) {
      const b = el('button', { type: 'button', class: data[key] ? 'is-on' : '', text: label, 'aria-pressed': String(!!data[key]) });
      b.addEventListener('click', () => { data[key] = !data[key]; if (editable) commit(); renderAll(); });
      toggles.appendChild(b);
    }
    bar.appendChild(toggles);
    if (editable) {
      bar.appendChild(el('button', { type: 'button', class: 'nb-mini', text: '+ Row', onclick: () => {
        data.rows.push(data.columns.map(() => ''));
        commit(); renderAll(); focusCell(data.rows.length - 1, 0);
      } }));
      const importBtn = el('button', { type: 'button', class: 'nb-mini', text: 'Import CSV', onclick: () => {
        const input = el('input', { type: 'file', accept: '.csv,.tsv,.txt,text/csv' });
        input.addEventListener('change', async () => {
          const file = input.files[0];
          if (!file) return;
          const grid = parseDelimited(await file.text());
          if (!grid.length) return;
          const header = grid[0];
          data.columns = header.map((name, j) => {
            const vals = grid.slice(1).map((r) => r[j]).filter((v) => String(v ?? '').trim() !== '');
            return { name: name || `Column ${colLetter(j)}`, type: vals.length && vals.every(isNum) ? 'number' : 'text', unit: '' };
          });
          data.rows = grid.slice(1).map((r) => data.columns.map((_c, j) => (r[j] ?? '').trim()));
          data.stats.group = Math.max(0, data.columns.findIndex((c) => c.type === 'text'));
          data.stats.value = Math.max(0, data.columns.findIndex((c) => c.type === 'number'));
          if (looksLikeLog(data.columns, data.rows)) plotAsLog();
          commit(); renderAll();
        });
        input.click();
      } });
      bar.appendChild(importBtn);
    }
    bar.appendChild(el('button', { type: 'button', class: 'nb-mini', text: 'Export CSV', onclick: () => {
      const { values } = computeSheet(data);
      download(`${(data.title || 'sheet').replace(/[^\w-]+/g, '-')}.csv`, toCsv([data.columns.map((c) => c.name), ...values]), 'text/csv');
    } }));
    return bar;
  }

  function renderAll() {
    const active = document.activeElement;
    const focus = active && root.contains(active) && active.dataset.r !== undefined
      ? { r: active.dataset.r, c: active.dataset.c, s: active.selectionStart, e: active.selectionEnd } : null;
    root.innerHTML = '';
    root.appendChild(toolbar());
    const wrap = el('div', { class: 'nb-sheet-scroll' });
    wrap.appendChild(renderTable());
    root.appendChild(wrap);
    root.appendChild(plotBox);
    root.appendChild(statsBox);
    // Drawn once attached, so the plot can measure the width it has.
    if (root.isConnected) refreshPanels.now(); else requestAnimationFrame(() => refreshPanels.now());
    if (focus) {
      const input = root.querySelector(`input[data-r="${focus.r}"][data-c="${focus.c}"]`);
      if (input) { input.focus(); try { input.setSelectionRange(focus.s, focus.e); } catch (_e) { /* number inputs */ } }
    }
  }

  renderAll();

  return {
    update(next) { data = normalize(next); renderAll(); },
    setEditable(value) { editable = value; renderAll(); },
    destroy() { commit.flush(); },
  };
}
