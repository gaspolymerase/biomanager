/* Verify sequencing: the chosen Sanger reads
   (.ab1, FASTA or text, pasted, or kept with the plasmid) go to
   /plasmids/<id>/verify, which checks them against the map
   (app/seqverify.py, where all of the biology is) and answers with a report.
   This draws it: the clone's verdict, where the reads lie over the map with
   each difference marked, the reads, and the list of differences, each
   with the stretch of chromatogram around it when the read was an .ab1. */
(function () {
  const tr = (text, values) => (typeof window.t === 'function' ? window.t(text, values) : text);
  const SVG = 'http://www.w3.org/2000/svg';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }
  const num = (n) => Number(n).toLocaleString('en-US');

  function badge(status) {
    if (status === 'match') return `<span class="badge badge-ok">${esc(tr('Matches'))}</span>`;
    if (status === 'differences') return `<span class="badge badge-warn">${esc(tr('Differs from the map'))}</span>`;
    if (status === 'poor') return `<span class="badge badge-quiet">${esc(tr('Too poor to check'))}</span>`;
    return `<span class="badge badge-danger">${esc(tr('Doesn’t match'))}</span>`;
  }

  // Pieces of a 1-based span on a plasmid of `length` (two when it crosses the origin).
  function pieces(start, end, length) {
    return start <= end ? [[start, end]] : [[start, length], [1, end]];
  }

  // Spans into as few lanes as fit without overlapping.
  function lanes(items, length) {
    const rows = [];
    items.forEach((item) => {
      const parts = item.parts;
      let row = rows.findIndex((r) => r.every((p) => parts.every((q) => q[1] < p[0] - length * 0.004 || q[0] > p[1] + length * 0.004)));
      if (row < 0) { rows.push([]); row = rows.length - 1; }
      rows[row].push(...parts);
      item.lane = row;
    });
    return rows.length;
  }

  function el(name, attrs, text) {
    const node = document.createElementNS(SVG, name);
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
    if (text != null) node.textContent = text;
    return node;
  }

  // The map as a line, its features above, the reads below, the differences as ticks.
  function drawMap(report, width) {
    const length = report.plasmid.length;
    const W = Math.max(Math.round(width || 0), 320);   // drawn at the size it shows, so the names stay readable
    const x = (pos) => ((pos - 1) / length) * W;
    const w = (a, b) => Math.max(((b - a + 1) / length) * W, 1.5);
    const features = report.map.map((f) => ({ name: f.name, parts: f.ranges.flatMap(([s, e]) => pieces(s, e, length)), direction: f.direction }));
    const featureLanes = Math.min(lanes(features, length), 4);
    const LANE = 16;
    const reads = report.reads.filter((r) => r.placed).map((r) => ({ read: r, parts: pieces(r.start, r.end, length) }));
    const readLanes = lanes(reads, length);
    const top = 4;
    const featureTop = top + 16;
    const lineY = featureTop + featureLanes * LANE + 4;
    const readTop = lineY + 10;
    const height = readTop + Math.max(readLanes, 1) * 14 + 6;
    const svg = el('svg', { viewBox: `0 0 ${W} ${height}`, class: 'sv-map', role: 'img',
      'aria-label': tr('Where the reads lie on the map') });
    svg.appendChild(el('text', { x: 0, y: top + 9, class: 'sv-label' }, '1'));
    svg.appendChild(el('text', { x: W, y: top + 9, class: 'sv-label', 'text-anchor': 'end' }, `${num(length)} bp`));
    features.forEach((f) => {
      if (f.lane >= featureLanes) return;
      f.parts.forEach(([s, e]) => {
        const r = el('rect', { x: x(s), y: featureTop + f.lane * LANE, width: w(s, e), height: 12, rx: 2, class: 'sv-feature' });
        r.appendChild(el('title', {}, `${f.name} · ${num(s)}–${num(e)}`));
        svg.appendChild(r);
      });
      const [s, e] = f.parts[0];
      if (w(s, e) > 30) {
        svg.appendChild(el('text', { x: x(s) + 3, y: featureTop + f.lane * LANE + 9.5, class: 'sv-feature-name' }, f.name.slice(0, Math.floor((w(s, e) - 4) / 6))));
      }
    });
    svg.appendChild(el('line', { x1: 0, x2: W, y1: lineY, y2: lineY, class: 'sv-line' }));
    reads.forEach((item) => {
      const r = item.read;
      const y = readTop + item.lane * 14;
      item.parts.forEach(([s, e], i) => {
        const bar = el('rect', { x: x(s), y, width: w(s, e), height: 8, rx: 2, class: 'sv-read' });
        bar.appendChild(el('title', {}, `${r.name} · ${num(r.start)}–${num(r.end)} · ${r.strand === 'reverse' ? tr('reverse') : tr('forward')}`));
        svg.appendChild(bar);
        // An arrowhead at the read's 3′ end.
        const last = r.strand === 'reverse' ? i === 0 : i === item.parts.length - 1;
        if (last) {
          const tip = r.strand === 'reverse' ? x(s) - 5 : x(e) + w(e, e) + 5;
          const base = r.strand === 'reverse' ? x(s) : x(e) + w(e, e);
          svg.appendChild(el('path', { d: `M${base} ${y - 1} L${tip} ${y + 4} L${base} ${y + 9} Z`, class: 'sv-read' }));
        }
      });
      r.differences.forEach((d) => {
        const tick = el('rect', { x: x(d.position) - 1, y: y - 2, width: 2.5, height: 12, class: d.confident ? 'sv-diff' : 'sv-diff-low' });
        tick.appendChild(el('title', {}, d.text));
        svg.appendChild(tick);
      });
    });
    return svg;
  }

  // A stretch of chromatogram: the four dyes in the usual colours (G in the
  // page's ink, so it shows in the dark), the called bases under it, the
  // one in question shaded.
  const DYES = { A: '#16a34a', C: '#2563eb', G: 'var(--color-ink)', T: '#dc2626' };
  function drawTrace(picture) {
    const W = 220;
    const H = 70;
    const n = picture.channels.A.length;
    const top = Math.max(1, ...['A', 'C', 'G', 'T'].flatMap((b) => picture.channels[b]));
    const x = (i) => (i / Math.max(n - 1, 1)) * W;
    const y = (v) => 4 + (1 - Math.max(v, 0) / top) * (H - 22);
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: 'block', role: 'img',
      'aria-label': tr('Trace around this base') });
    picture.bases.filter((b) => b.at).forEach((b) => {
      svg.appendChild(el('rect', { x: x(b.x) - 6, y: 0, width: 12, height: H, fill: 'var(--color-accent-soft, rgba(250,204,21,.25))' }));
    });
    ['A', 'C', 'G', 'T'].forEach((b) => {
      const points = picture.channels[b].map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
      svg.appendChild(el('polyline', { points, fill: 'none', 'stroke-width': 1.2, style: `stroke:${DYES[b]}` }));
    });
    picture.bases.forEach((b) => {
      svg.appendChild(el('text', { x: x(b.x), y: H - 4, 'text-anchor': 'middle', 'font-size': 10,
        'font-family': 'ui-monospace, monospace', 'font-weight': b.at ? 700 : 400, style: `fill:${DYES[b.base] || 'currentColor'}` }, b.base));
    });
    return svg;
  }

  function changeText(d) {
    if (d.kind === 'mismatch') return `${d.plasmid}→${d.read}`;
    if (d.kind === 'deletion') return `−${d.plasmid.length > 12 ? tr('%(n)s bp', { n: d.length }) : d.plasmid}`;
    return `+${d.read.length > 12 ? tr('%(n)s bp', { n: d.length }) : d.read}`;
  }

  function where(d) {
    if (d.kind === 'deletion' && d.end !== d.position) return `${num(d.position)}–${num(d.end)}`;
    if (d.kind === 'insertion') return tr('after %(at)s', { at: num(d.position) });
    return num(d.position);
  }

  function render(box, j) {
    const report = j.report;
    const result = box.querySelector('[data-verify-result]');
    const length = report.plasmid.length;
    const parts = [];
    parts.push(`<div class="px-4 py-3 text-[13px]"><div class="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1">${badge(report.status)}
      <span class="text-[12px] text-muted tabular-nums">${esc(tr('%(covered)s of %(length)s bp covered', { covered: num(report.covered_bp), length: num(length) }))}</span></div>
      <p>${esc(report.verdict)}</p></div>`);
    (j.problems || []).forEach((p) => parts.push(`<p class="px-4 pb-2 text-[13px] text-danger-700">${esc(p)}</p>`));
    if (j.kept && j.kept.length) {
      parts.push(`<p class="px-4 pb-2 text-[13px] text-muted">${esc(tr('Kept with this plasmid’s files (Storage → Files): %(names)s.', { names: j.kept.join(', ') }))}</p>`);
    }
    parts.push('<div class="px-4 pb-3" data-verify-map></div>');
    parts.push(`<div class="overflow-x-auto border-t border-[var(--color-line)]"><table class="w-full text-[13px]">
      <thead><tr class="text-left text-muted"><th class="px-4 py-2">${esc(tr('Read'))}</th><th class="py-2">${esc(tr('Strand'))}</th>
      <th class="py-2">${esc(tr('Covers'))}</th><th class="py-2">${esc(tr('Identity (%)'))}</th><th class="py-2 pr-4">${esc(tr('Verdict'))}</th></tr></thead><tbody>
      ${report.reads.map((r) => `<tr class="border-t border-[var(--color-line)]">
        <td class="px-4 py-2 font-medium">${esc(r.name)}</td>
        <td class="py-2 whitespace-nowrap">${r.placed ? esc(r.strand === 'reverse' ? tr('reverse') : tr('forward')) : '—'}</td>
        <td class="py-2 whitespace-nowrap tabular-nums">${r.placed ? `${num(r.start)}–${num(r.end)}` : '—'}</td>
        <td class="py-2 tabular-nums">${r.identity != null ? `${r.identity}` : '—'}</td>
        <td class="py-2 pr-4">${badge(r.status)} ${esc(r.verdict)}${r.mixed && r.mixed.trace ? `<div class="mt-1" data-trace-mixed="${esc(r.name)}"></div>` : ''}</td></tr>`).join('')}
      </tbody></table></div>`);
    if (report.differences.length) {
      parts.push(`<div class="overflow-x-auto border-t border-[var(--color-line)]"><table class="w-full text-[13px]">
        <thead><tr class="text-left text-muted"><th class="px-4 py-2">${esc(tr('Position'))}</th><th class="py-2">${esc(tr('Change'))}</th>
        <th class="py-2">${esc(tr('Falls in'))}</th><th class="py-2">${esc(tr('Effect'))}</th><th class="py-2">${esc(tr('Quality'))}</th>
        <th class="py-2">${esc(tr('Seen in'))}</th><th class="py-2 pr-4">${esc(tr('Trace'))}</th></tr></thead><tbody>
        ${report.differences.map((d, i) => `<tr class="border-t border-[var(--color-line)]${d.confident ? '' : ' text-muted'}">
          <td class="px-4 py-2 whitespace-nowrap tabular-nums">${esc(where(d))}</td>
          <td class="py-2 font-mono text-[12px] break-all">${esc(changeText(d))}</td>
          <td class="py-2">${esc(d.features.join(', ') || '—')}</td>
          <td class="py-2">${esc(d.effect ? d.effect.text : '—')}</td>
          <td class="py-2 whitespace-nowrap tabular-nums">${d.quality == null ? '—' : `Q${d.quality}`}${d.confident ? '' : ` · ${esc(tr('low quality'))}`}</td>
          <td class="py-2 whitespace-nowrap">${esc(tr('%(seen)s of %(covering)s reads', { seen: d.reads.length, covering: d.reads_covering }))}</td>
          <td class="py-2 pr-4" data-trace-diff="${i}">${d.trace ? '' : '—'}</td></tr>`).join('')}
        </tbody></table></div>`);
    }
    result.innerHTML = parts.join('');
    result.hidden = false;
    report.differences.forEach((d, i) => {
      const cell = result.querySelector(`[data-trace-diff="${i}"]`);
      if (cell && d.trace) cell.appendChild(drawTrace(d.trace));
    });
    report.reads.filter((r) => r.mixed && r.mixed.trace).forEach((r) => {
      const cell = [...result.querySelectorAll('[data-trace-mixed]')].find((c) => c.dataset.traceMixed === r.name);
      if (cell) cell.appendChild(drawTrace(r.mixed.trace));
    });
    const map = result.querySelector('[data-verify-map]');
    if (report.reads.some((r) => r.placed)) map.appendChild(drawMap(report, map.clientWidth));
    else map.remove();
  }

  function mount(box) {
    const form = box.querySelector('[data-verify-form]');
    const status = box.querySelector('[data-verify-status]');
    const go = box.querySelector('[data-verify-go]');
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      status.textContent = tr('Checking the reads…');
      go.disabled = true;
      fetch(box.dataset.verifyUrl, { method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: { Accept: 'application/json' } })
        .then((r) => r.json().catch(() => ({ ok: false, error: tr('The server answered %(status)s.', { status: r.status }) })))
        .then((j) => {
          if (j && j.ok) { status.textContent = ''; render(box, j); } else {
            status.textContent = tr('Couldn’t check: %(error)s', { error: (j && j.error) || tr('unknown error') });
          }
        })
        .catch((err) => { status.textContent = tr('Couldn’t check: %(error)s', { error: err.message }); })
        .finally(() => { go.disabled = false; });
    });
  }

  // The plasmid's Verify tab (static/plasmid-verify.js) draws with these too.
  window.BioSeqVerify = { drawMap, drawTrace, changeText, where, badge };

  function start() { document.querySelectorAll('[data-verify-url]').forEach(mount); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
