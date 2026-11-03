/* The orders page's Spending view (templates/inventory/_spending.html):
   what the lab's orders cost by month, stacked by person, project group,
   vendor or category, the same ranked as a table, and where each limit
   stands. The numbers are app/order_spending.py's, from
   /inventory/<key>/spending, asked for when the view is first shown and
   each time a choice above it changes; the old chart stays, dimmed, until
   the new numbers come.

   Drawn in SVG by hand: the column's segments 2px apart, the top one
   rounded at its data end; a tooltip on hover and keyboard focus lists
   every series in that month. Colours follow the series' rank in the
   chosen months (the categorical order in static's CSS, --spend-1…5),
   Other in grey. */
(function () {
  'use strict';

  const root = document.querySelector('[data-spending]');
  if (!root) return;
  const t = window.t || ((s) => s);
  const lang = document.documentElement.lang || 'en';
  const say = (message, kind) => { if (message && window.BiomanagerShell) window.BiomanagerShell.toast(message, kind); };
  const $ = (sel) => root.querySelector(sel);
  const SVG = 'http://www.w3.org/2000/svg';

  const state = { by: 'person', range: 'last12', from: '', to: '', data: null, asked: false, focus: null };
  try {
    const kept = JSON.parse(localStorage.getItem(`spending:${root.dataset.key}`) || '{}');
    if (kept.by) state.by = kept.by;
    if (kept.range) state.range = kept.range;
  } catch (_) { /* a private window keeps nothing */ }

  // ------------------------------------------------------------- numbers

  const whole = new Intl.NumberFormat(lang, { maximumFractionDigits: 0 });
  const cents = new Intl.NumberFormat(lang, { maximumFractionDigits: 2 });
  const compact = new Intl.NumberFormat(lang, { notation: 'compact', maximumFractionDigits: 1 });
  const currency = () => (state.data && state.data.currency) || '';
  const money = (v) => currency() + (Math.abs(v) >= 100 || v === Math.round(v) ? whole : cents).format(v || 0);
  const axisMoney = (v) => currency() + (v >= 10000 ? compact.format(v) : whole.format(v));
  const monthName = (ym, long) => {
    const [y, m] = ym.split('-').map(Number);
    return new Intl.DateTimeFormat(lang, long ? { month: 'long', year: 'numeric' } : { month: 'short' })
      .format(new Date(y, m - 1, 1));
  };

  // The axis' step: 1, 2 or 5 times a power of ten, about a quarter of the
  // tallest column; the top is the first step at or above it.
  function niceStep(v) {
    if (v <= 0) return 1;
    const power = 10 ** Math.floor(Math.log10(v));
    for (const k of [1, 2, 5, 10]) if (k * power >= v) return k * power;
    return 10 * power;
  }

  // ------------------------------------------------------------- the range

  function ym(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; }
  function range() {
    const now = new Date();
    const back = (n) => ym(new Date(now.getFullYear(), now.getMonth() - n, 1));
    const starts = (state.data && state.data.settings && state.data.settings.year_starts) || 1;
    switch (state.range) {
      case 'last3': return { from: back(2), to: back(0) };
      case 'year': {
        let y = now.getFullYear();
        if (now.getMonth() + 1 < starts) y -= 1;
        return { from: `${y}-${String(starts).padStart(2, '0')}`, to: back(0) };
      }
      case 'all': return { from: 'all', to: back(0) };
      case 'custom': return { from: state.from || back(11), to: state.to || back(0) };
      default: return { from: back(11), to: back(0) };
    }
  }

  // ------------------------------------------------------------- fetching

  function query() {
    const r = range();
    return new URLSearchParams({ by: state.by, from: r.from, to: r.to }).toString();
  }

  function load() {
    root.classList.add('is-loading');
    return fetch(`${root.dataset.src}?${query()}`, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((data) => { state.data = data; render(); })
      .catch(() => { state.asked = false; say(t('The spending could not be loaded. Check the connection and try again.'), 'error'); })
      .finally(() => root.classList.remove('is-loading'));
  }

  function keep() {
    try { localStorage.setItem(`spending:${root.dataset.key}`, JSON.stringify({ by: state.by, range: state.range })); } catch (_) {}
  }

  // ------------------------------------------------------------- drawing

  function el(tag, attrs, text) {
    const node = document.createElementNS(SVG, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
    if (text != null) node.textContent = text;
    return node;
  }

  // A column segment: square at the bottom, its top corners rounded when it
  // is the column's top (its data end).
  function segment(x, y, w, h, rounded) {
    const r = rounded ? Math.min(4, w / 2, h) : 0;
    return `M${x},${y + h}V${y + r}${r ? `Q${x},${y} ${x + r},${y}` : ''}H${x + w - r}${r ? `Q${x + w},${y} ${x + w},${y + r}` : ''}V${y + h}Z`;
  }

  function colourOf(i, key) { return key === '__other' ? 'var(--spend-other)' : `var(--spend-${i + 1})`; }

  function drawChart() {
    const data = state.data;
    const host = $('[data-spend-chart]');
    host.textContent = '';
    const months = data.months;
    if (!data.totals.orders) {
      const empty = document.createElement('p');
      empty.className = 'spend-empty';
      empty.textContent = t('No orders asked for in these months.');
      host.appendChild(empty);
      return;
    }
    const width = Math.max(host.clientWidth, 280);
    drawnWidth = host.clientWidth;
    const height = 248;
    const pad = { top: 12, right: 8, bottom: 26, left: 52 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const step = niceStep(Math.max(...data.month_totals) / 4);
    const ticks = Math.max(1, Math.ceil(Math.max(...data.month_totals) / step));
    const top = step * ticks;
    const y = (v) => pad.top + plotH - (v / top) * plotH;
    const band = plotW / months.length;
    const barW = Math.max(4, Math.min(24, band * 0.62));
    const svg = el('svg', { width, height, viewBox: `0 0 ${width} ${height}`, class: 'spend-svg', role: 'img',
                            'aria-label': t('Spending by month') });
    // Gridlines and the y axis' amounts.
    for (let i = 0; i <= ticks; i += 1) {
      const v = step * i;
      svg.appendChild(el('line', { x1: pad.left, x2: width - pad.right, y1: y(v), y2: y(v),
                                   class: i ? 'spend-grid' : 'spend-base' }));
      svg.appendChild(el('text', { x: pad.left - 8, y: y(v) + 4, class: 'spend-tick', 'text-anchor': 'end' }, axisMoney(v)));
    }
    // Month labels: as many as fit.
    const every = Math.ceil(36 / band);
    months.forEach((m, i) => {
      if (i % every) return;
      const label = monthName(m) + (m.endsWith('-01') || i === 0 ? ` ${m.slice(2, 4)}` : '');
      svg.appendChild(el('text', { x: pad.left + band * i + band / 2, y: height - 8, class: 'spend-tick',
                                   'text-anchor': 'middle' }, label));
    });
    // The columns, each segment 2px apart, and a hit area as wide as the band.
    const stack = data.stack;
    months.forEach((m, i) => {
      const x = pad.left + band * i + (band - barW) / 2;
      const g = el('g', { class: 'spend-col', tabindex: '0', 'data-month': i,
                          'aria-label': `${monthName(m, true)}: ${money(data.month_totals[i])}` });
      g.appendChild(el('rect', { x: pad.left + band * i, y: pad.top, width: band, height: plotH, class: 'spend-hit' }));
      let base = 0;
      const parts = stack.map((s, k) => ({ s, k, v: s.months[i] })).filter((p) => p.v > 0);
      parts.forEach((p, n) => {
        const y0 = y(base);
        const y1 = y(base + p.v);
        const gap = n ? 2 : 0;
        const h = Math.max(0, y0 - y1 - gap);
        if (h > 0) {
          g.appendChild(el('path', { d: segment(x, y1, barW, h, n === parts.length - 1), fill: colourOf(p.k, p.s.key),
                                     class: 'spend-seg', 'data-series': p.s.key }));
        }
        base += p.v;
      });
      svg.appendChild(g);
    });
    host.appendChild(svg);
    svg.querySelectorAll('.spend-col').forEach((g) => {
      const show = (event) => tip(g, Number(g.dataset.month), event);
      g.addEventListener('pointermove', show);
      g.addEventListener('focus', show);
      g.addEventListener('pointerleave', hideTip);
      g.addEventListener('blur', hideTip);
    });
    emphasise();
  }

  // ------------------------------------------------------------- tooltip

  const tipEl = document.createElement('div');
  tipEl.className = 'spend-tip';
  tipEl.hidden = true;
  root.appendChild(tipEl);

  function tip(g, i, event) {
    const data = state.data;
    tipEl.textContent = '';
    const head = document.createElement('div');
    head.className = 'spend-tip-head';
    const total = document.createElement('strong');
    total.textContent = money(data.month_totals[i]);
    const when = document.createElement('span');
    when.textContent = monthName(data.months[i], true);
    head.append(total, when);
    tipEl.appendChild(head);
    data.stack.forEach((s, k) => {
      if (!s.months[i]) return;
      const row = document.createElement('div');
      row.className = 'spend-tip-row';
      const key = document.createElement('i');
      key.style.background = colourOf(k, s.key);
      const value = document.createElement('b');
      value.textContent = money(s.months[i]);
      const name = document.createElement('span');
      name.textContent = s.label;
      row.append(key, value, name);
      tipEl.appendChild(row);
    });
    tipEl.hidden = false;
    const box = root.getBoundingClientRect();
    const at = event && event.clientX != null ? { x: event.clientX, y: event.clientY } : (() => {
      const r = g.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + 20 };
    })();
    const w = tipEl.offsetWidth;
    let left = at.x - box.left + 14;
    if (left + w > box.width - 8) left = at.x - box.left - w - 14;
    tipEl.style.left = `${Math.max(4, left)}px`;
    tipEl.style.top = `${at.y - box.top + 10}px`;
    g.classList.add('is-hover');
  }
  function hideTip() {
    tipEl.hidden = true;
    root.querySelectorAll('.spend-col.is-hover').forEach((g) => g.classList.remove('is-hover'));
  }

  // ------------------------------------------------------------- legend, table

  function drawLegend() {
    const host = $('[data-spend-legend]');
    host.textContent = '';
    const stack = state.data.stack;
    host.hidden = stack.length < 2;
    stack.forEach((s, k) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'spend-legend-item';
      item.dataset.series = s.key;
      const swatch = document.createElement('i');
      swatch.style.background = colourOf(k, s.key);
      const name = document.createElement('span');
      name.textContent = s.key === '__other' ? `${s.label} (${s.count})` : s.label;
      item.append(swatch, name);
      item.addEventListener('pointerenter', () => { state.focus = s.key; emphasise(); });
      item.addEventListener('pointerleave', () => { state.focus = null; emphasise(); });
      item.addEventListener('focus', () => { state.focus = s.key; emphasise(); });
      item.addEventListener('blur', () => { state.focus = null; emphasise(); });
      host.appendChild(item);
    });
  }

  // One series picked (a legend item or a table row under the pointer):
  // the others grey out, in the chart and the table.
  function emphasise() {
    root.classList.toggle('is-focusing', !!state.focus);
    root.querySelectorAll('[data-series]').forEach((node) => {
      node.classList.toggle('is-focus', node.dataset.series === state.focus);
    });
  }

  const BY_HEADINGS = () => ({ person: t('Person'), group: t('Project group'), vendor: t('Vendor'), category: t('Category') });

  function drawTable() {
    const data = state.data;
    const body = $('[data-spend-rows]');
    body.textContent = '';
    $('[data-spend-by-heading]').textContent = BY_HEADINGS()[data.by];
    const max = Math.max(0, ...data.series.map((s) => s.total));
    const stackIndex = new Map(data.stack.map((s, k) => [s.key, k]));
    data.series.forEach((s) => {
      const tr = document.createElement('tr');
      tr.dataset.series = stackIndex.has(s.key) ? s.key : '__other';
      const name = document.createElement('th');
      name.scope = 'row';
      const k = stackIndex.has(s.key) ? stackIndex.get(s.key) : stackIndex.get('__other');
      const swatch = document.createElement('i');
      swatch.className = 'spend-swatch';
      swatch.style.background = colourOf(k, stackIndex.has(s.key) ? s.key : '__other');
      const label = document.createElement('span');
      label.textContent = s.label;
      name.append(swatch, label);
      const barCell = document.createElement('td');
      barCell.className = 'spend-bar-cell';
      const bar = document.createElement('span');
      bar.className = 'spend-bar';
      const spent = document.createElement('span');
      spent.className = 'spend-bar-spent';
      spent.style.width = `${max ? (s.spent / max) * 100 : 0}%`;
      const asked = document.createElement('span');
      asked.className = 'spend-bar-asked';
      asked.style.width = `${max ? (s.asked / max) * 100 : 0}%`;
      bar.append(spent, asked);
      barCell.appendChild(bar);
      const cells = [money(s.total), money(s.spent), money(s.asked), whole.format(s.orders)].map((text, i) => {
        const td = document.createElement('td');
        td.className = i ? 'spend-num is-soft' : 'spend-num';
        td.textContent = text;
        return td;
      });
      tr.append(name, barCell, ...cells);
      tr.addEventListener('pointerenter', () => { state.focus = tr.dataset.series; emphasise(); });
      tr.addEventListener('pointerleave', () => { state.focus = null; emphasise(); });
      body.appendChild(tr);
    });
    $('[data-spend-group-note]').hidden = data.by !== 'group';
  }

  function drawFigures() {
    const { totals } = state.data;
    $('[data-spend-total]').textContent = money(totals.spent + totals.asked);
    $('[data-spend-spent]').textContent = money(totals.spent);
    $('[data-spend-asked]').textContent = money(totals.asked);
    $('[data-spend-orders]').textContent = whole.format(totals.orders);
    const unpriced = $('[data-spend-unpriced]');
    unpriced.hidden = !totals.unpriced;
    unpriced.textContent = t('%(n)s without a price', { n: whole.format(totals.unpriced) });
    const r = state.data;
    $('[data-spend-range]').textContent = r.first === r.last ? monthName(r.first, true)
      : `${monthName(r.first, true)} – ${monthName(r.last, true)}`;
    $('[data-spend-price-is]').textContent = r.price_is === 'total'
      ? t('An order costs its Price.') : t('An order costs its Price times its Quantity.');
  }

  const STATE_WORDS = () => ({ ok: t('Within the limit'), near: t('Near the limit'), over: t('Over the limit') });
  const STATE_ICONS = { ok: 'check', near: 'warning', over: 'warning' };

  function drawLimits() {
    const { limits } = state.data;
    const card = $('[data-spend-limits]');
    const list = $('[data-spend-limit-rows]');
    list.textContent = '';
    card.hidden = !limits.rows.length && !state.data.can_set_limits;
    $('[data-spend-limits-none]').hidden = !!limits.rows.length;
    const period = limits.period === 'month' ? monthName(limits.first, true)
      : `${monthName(limits.first, true)} – ${monthName(limits.last, true)}`;
    $('[data-spend-limit-period]').textContent = period;
    limits.rows.forEach((r) => {
      const row = document.createElement('div');
      row.className = `spend-limit is-${r.state}`;
      const head = document.createElement('div');
      head.className = 'spend-limit-head';
      const name = document.createElement('span');
      name.className = 'spend-limit-name';
      name.textContent = r.label;
      const kind = document.createElement('span');
      kind.className = 'spend-limit-kind';
      kind.textContent = { lab: '', person: t('Person'), group: t('Project group') }[r.kind];
      const figure = document.createElement('span');
      figure.className = 'spend-limit-figure';
      figure.textContent = t('%(used)s of %(limit)s', { used: money(r.used), limit: money(r.limit) });
      head.append(name, kind, figure);
      const meter = document.createElement('div');
      meter.className = 'spend-meter';
      meter.setAttribute('role', 'meter');
      meter.setAttribute('aria-valuemin', '0');
      meter.setAttribute('aria-valuemax', String(r.limit));
      meter.setAttribute('aria-valuenow', String(Math.round(r.used)));
      meter.setAttribute('aria-label', `${r.label}: ${figure.textContent}`);
      const fill = document.createElement('span');
      fill.style.width = `${Math.min(100, (r.share || 0) * 100)}%`;
      meter.appendChild(fill);
      const status = document.createElement('span');
      status.className = 'spend-limit-state';
      status.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${STATE_ICONS[r.state]}"></use></svg>`;
      const words = document.createElement('span');
      words.textContent = `${STATE_WORDS()[r.state]} · ${Math.round((r.share || 0) * 100)}%`;
      status.appendChild(words);
      row.append(head, meter, status);
      list.appendChild(row);
    });
  }

  function syncChoices() {
    root.querySelectorAll('[data-spend-by]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.spendBy === state.by)));
    root.querySelectorAll('[data-spend-range-pick]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.spendRangePick === state.range)));
    const custom = $('[data-spend-custom]');
    custom.hidden = state.range !== 'custom';
    const r = range();
    const csv = $('[data-spend-csv]');
    csv.href = `${root.dataset.csv}?${query()}`;
    if (state.range === 'custom') {
      $('[name="spend_from"]').value = r.from;
      $('[name="spend_to"]').value = r.to;
    }
    $('[data-spend-settings]').hidden = !(state.data && state.data.can_set_limits);
  }

  function render() {
    if (!state.data) return;
    syncChoices();
    drawFigures();
    drawLimits();   // first: whether its card shows sets the chart's width
    drawLegend();
    drawTable();
    drawChart();
  }

  // ------------------------------------------------------------- choices

  root.querySelectorAll('[data-spend-by]').forEach((b) => b.addEventListener('click', () => {
    state.by = b.dataset.spendBy; keep(); syncChoices(); load();
  }));
  root.querySelectorAll('[data-spend-range-pick]').forEach((b) => b.addEventListener('click', () => {
    state.range = b.dataset.spendRangePick; keep(); syncChoices();
    if (state.range !== 'custom') load();
  }));
  root.querySelectorAll('[data-spend-custom] input').forEach((input) => input.addEventListener('change', () => {
    state.from = $('[name="spend_from"]').value;
    state.to = $('[name="spend_to"]').value;
    if (state.from && state.to) { syncChoices(); load(); }
  }));

  // Drawn again at the chart's new width: the window or the cards beside it changed.
  let resizing = null;
  let drawnWidth = 0;
  const chartHost = $('[data-spend-chart]');
  const redraw = () => {
    clearTimeout(resizing);
    resizing = setTimeout(() => {
      if (state.data && chartHost.clientWidth && Math.abs(chartHost.clientWidth - drawnWidth) > 2) drawChart();
    }, 80);
  };
  if (window.ResizeObserver) new ResizeObserver(redraw).observe(chartHost);
  else window.addEventListener('resize', redraw);

  // ------------------------------------------------------------- settings

  const dialog = document.getElementById('spend-settings-dialog');
  function limitInput(name, value, label) {
    const row = document.createElement('label');
    row.className = 'spend-limit-input';
    const words = document.createElement('span');
    words.textContent = label;
    const input = document.createElement('input');
    input.type = 'text';
    input.inputMode = 'decimal';
    input.name = name;
    input.value = value == null ? '' : value;
    input.placeholder = t('No limit');
    input.className = 'field';
    row.append(words, input);
    return row;
  }
  function openSettings() {
    const s = state.data.settings;
    const form = dialog.querySelector('form');
    form.elements.currency.value = s.currency;
    form.elements.price_is.value = s.price_is;
    form.elements.period.value = s.period;
    form.elements.year_starts.value = String(s.year_starts);
    form.elements.limit_lab.value = s.lab == null ? '' : s.lab;
    const people = dialog.querySelector('[data-spend-people]');
    const groupsHost = dialog.querySelector('[data-spend-groups]');
    people.textContent = '';
    groupsHost.textContent = '';
    JSON.parse(root.dataset.people || '[]').forEach((p) => people.appendChild(
      limitInput(`limit_person_${p.username}`, s.people[p.username], p.name)));
    const groups = JSON.parse(root.dataset.groups || '[]');
    groups.forEach((g) => groupsHost.appendChild(limitInput(`limit_group_${g.id}`, s.groups[String(g.id)], g.name)));
    dialog.querySelector('[data-spend-groups-block]').hidden = !groups.length;
    const yearStarts = dialog.querySelector('[data-spend-year-starts]');
    const sync = () => { yearStarts.hidden = form.elements.period.value !== 'year'; };
    form.elements.period.onchange = sync;
    sync();
    dialog.showModal();
  }
  $('[data-spend-settings]').addEventListener('click', openSettings);
  if (dialog) {
    dialog.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dialog.close()));
    dialog.querySelector('form').addEventListener('submit', (event) => {
      event.preventDefault();
      fetch(root.dataset.settingsUrl, { method: 'POST', body: new FormData(event.target), credentials: 'same-origin',
                                        headers: { 'X-Requested-With': 'fetch' } })
        .then((r) => r.json().catch(() => ({ ok: false })))
        .then((answer) => {
          if (!answer.ok) { say(answer.error || t('Not saved. Check the connection and try again.'), 'error'); return; }
          dialog.close();
          say(t('Saved the spending settings.'), 'success');
          load();
        }, () => say(t('Not saved. Check the connection and try again.'), 'error'));
    });
  }

  // ------------------------------------------------------------- shown

  function show() {
    if (state.asked) { if (state.data) drawChart(); return; }
    state.asked = true;
    syncChoices();
    load();
  }
  document.addEventListener('layout:change', (event) => { if (event.detail && event.detail.layout === 'spending') show(); });
  if (!root.closest('[data-layout-panel]').hidden) show();
})();
