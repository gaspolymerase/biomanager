/* The plasmid's own sequence viewer, on its Sequence tab
   (templates/plasmid_detail.html): every base, row under row, with its
   complement, the features, the enzymes that cut once, the lab's primers
   that bind, and the protein under each coding sequence (or the open
   reading frames in all six frames).

   Drag across bases to pick a stretch: a small bar beside it gives its
   length, Tm and GC, and makes it a forward or reverse primer (checked by
   /primer-check, saved to the lab's Primers by /primers/new), copies it,
   or translates it. Click a feature to pick it; a coding one shows its
   protein. Everything comes from /plasmids/<id>/view.json
   (app/sequence_routes.py).

   Rows are laid out first, then drawn as they scroll into view, so a long
   plasmid opens as fast as a short one. */
(function () {
  'use strict';

  const root = document.querySelector('[data-seq-viewer]');
  if (!root) return;
  const t = window.t || ((s) => s);
  const num = (n) => Number(n || 0).toLocaleString('en-US');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const $ = (sel) => root.querySelector(sel);
  const say = (message, kind) => { if (message && window.BiomanagerShell) window.BiomanagerShell.toast(message, kind); };
  const COMP = { A: 'T', C: 'G', G: 'C', T: 'A', N: 'N' };
  const revcomp = (s) => s.split('').reverse().map((b) => COMP[b] || 'N').join('');
  const CODE = (() => {
    const b = 'TCAG';
    const aa = 'FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG';
    const out = {};
    let i = 0;
    for (const x of b) for (const y of b) for (const z of b) out[x + y + z] = aa[i++];
    return out;
  })();

  // Average residue masses (Da), as app/protein.py has them.
  const RESIDUE = { A: 71.08, R: 156.19, N: 114.10, D: 115.09, C: 103.14, E: 129.12, Q: 128.13, G: 57.05, H: 137.14,
    I: 113.16, L: 113.16, K: 128.17, M: 131.19, F: 147.18, P: 97.12, S: 87.08, T: 101.11, W: 186.21, Y: 163.18, V: 99.13 };

  const STORE = 'biomanager:seqviewer';
  const show = { complement: true, translation: true, features: true, enzymes: true, primers: true, frames: 'cds' };
  try { Object.assign(show, JSON.parse(localStorage.getItem(STORE) || '{}')); } catch (_e) { /* defaults */ }
  const remember = () => { try { localStorage.setItem(STORE, JSON.stringify(show)); } catch (_e) { /* fine */ } };

  let data = null;
  let per = 80;
  let cw = 8.4;
  let rows = [];
  let sel = null;            // {start, end} 1-based inclusive along the top strand
  let panel = null;          // what the side panel shows: {kind: 'primer'|'protein'|'stretch', ...}
  let observer = null;

  // ---------------------------------------------------------------- layout

  const GUTTER = 62;
  const kindOf = (f) => (window.BioPlasmidMap ? window.BioPlasmidMap.kind(f) : 'other');

  function measure() {
    const probe = document.createElement('span');
    probe.className = 'sv2-probe';
    probe.textContent = 'ACGTACGTAC';
    $('[data-rowlist]').appendChild(probe);
    cw = probe.getBoundingClientRect().width / 10 || 8.4;
    probe.remove();
    const width = $('[data-rows]').clientWidth - GUTTER - 24;
    per = Math.max(30, Math.min(120, Math.floor(width / cw / 10) * 10));
  }

  // Each span [a, b] (1-based) cut to the row's [r0, r1].
  function clip(a, b, r0, r1) {
    const spans = a <= b ? [[a, b]] : [[a, data.length], [1, b]];
    return spans.map(([x, y]) => [Math.max(x, r0), Math.min(y, r1)]).filter(([x, y]) => x <= y);
  }

  // Features as 1-based spans (kept 0-based, the end included).
  function featureSpans() {
    return data.features.map((f, i) => ({ ...f, i, a: Number(f.start) + 1, b: Number(f.end) + 1, kind: kindOf(f) }));
  }

  // Coding rows: under each CDS, its amino acids by codon (or the ORFs).
  function codingTracks() {
    if (show.frames === 'six') {
      return data.orfs.map((o, i) => ({ id: `orf${i}`, name: t('ORF %(n)s', { n: i + 1 }), a: o.start, b: o.end, strand: o.strand, protein: null }));
    }
    return data.proteins.map((p) => ({ id: `cds${p.feature}`, name: p.name, a: p.start, b: p.end, strand: p.direction, protein: p.protein, feature: p.feature }));
  }

  // The codon starting positions of a coding track, with their amino acids.
  function codons(track) {
    const out = [];
    const L = data.length;
    const len = (track.b - track.a + L) % L + 1;
    const seq = data.sequence;
    for (let k = 0; k + 2 < len; k += 3) {
      let p1;
      let tri;
      if (track.strand >= 0) {
        p1 = ((track.a - 1 + k) % L) + 1;
        tri = [0, 1, 2].map((d) => seq[(track.a - 1 + k + d) % L]).join('');
      } else {
        p1 = ((track.b - 1 - k - 2 + L * 2) % L) + 1;               // the codon's leftmost base on the top strand
        tri = revcomp([0, 1, 2].map((d) => seq[(p1 - 1 + d) % L]).join(''));
      }
      out.push({ at: p1, aa: CODE[tri] || 'X', n: k / 3 + 1 });
    }
    return out;
  }

  function layout() {
    measure();
    const L = data.length;
    const feats = show.features ? featureSpans() : [];
    const tracks = show.translation ? codingTracks().map((tr) => ({ ...tr, codons: codons(tr) })) : [];
    rows = [];
    for (let r0 = 1; r0 <= L; r0 += per) {
      const r1 = Math.min(L, r0 + per - 1);
      const row = { r0, r1, enz: [], prim: [], feat: [], cds: [] };
      if (show.enzymes) row.enz = data.cutters.filter((c) => c.cut >= r0 && c.cut <= r1);
      if (show.primers) {
        const lanes = [];
        data.primers.forEach((p) => clip(p.start, p.end, r0, r1).forEach(([x, y]) => {
          let lane = lanes.findIndex((end) => end < x - 2);
          if (lane < 0) { lanes.push(0); lane = lanes.length - 1; }
          lanes[lane] = y + Math.ceil(String(p.name).length * 6.2 / cw) + 2;
          row.prim.push({ p, x, y, lane, head: p.direction > 0 ? y === (p.end) : x === p.start });
        }));
        row.primLanes = lanes.length;
      }
      const flanes = [];
      feats.forEach((f) => clip(f.a, f.b, r0, r1).forEach(([x, y]) => {
        let lane = flanes.findIndex((end) => end < x);
        if (lane < 0) { flanes.push(0); lane = flanes.length - 1; }
        flanes[lane] = y;
        row.feat.push({ f, x, y, lane });
      }));
      row.featLanes = flanes.length;
      tracks.forEach((tr) => {
        const inRow = tr.codons.filter((c) => c.at >= r0 - 2 && c.at <= r1);
        if (inRow.length) row.cds.push({ tr, codons: inRow });
      });
      // Heights, top to bottom.
      row.hEnz = row.enz.length ? 16 : 0;
      row.hPrim = (row.primLanes || 0) * 13;
      row.hTop = 18;
      row.hBot = show.complement ? 16 : 0;
      row.hFeat = row.featLanes * 14 + (row.featLanes ? 4 : 0);
      row.hCds = row.cds.length * 26;
      row.height = 10 + row.hEnz + row.hPrim + row.hTop + row.hBot + row.hFeat + row.hCds + 12;
      rows.push(row);
    }
  }

  // ---------------------------------------------------------------- drawing

  const X = (pos, row) => GUTTER + (pos - row.r0) * cw;

  function drawRow(row) {
    const W = GUTTER + per * cw + 20;
    const out = [`<svg class="sv2-svg" viewBox="0 0 ${W.toFixed(1)} ${row.height}" width="${W.toFixed(1)}" height="${row.height}">`];
    let y = 10;
    // Enzymes that cut once, with a tick down to the strand.
    if (row.hEnz) {
      row.enz.forEach((c) => {
        const x = X(c.cut, row) + cw;
        out.push(`<line x1="${x.toFixed(1)}" y1="${y + 3}" x2="${x.toFixed(1)}" y2="${y + row.hEnz + row.hPrim + 4}" class="sv2-enz-tick"/>`);
        out.push(`<text x="${(x + 3).toFixed(1)}" y="${y + 9}" class="sv2-enz">${esc(c.name)}</text>`);
      });
      y += row.hEnz;
    }
    // The lab's primers, as arrows.
    row.prim.forEach(({ p, x, y: e, lane }) => {
      const yy = y + lane * 13 + 6;
      const x0 = X(x, row);
      const x1 = X(e, row) + cw;
      const fw = p.direction > 0;
      const tip = fw ? (e === p.end) : (x === p.start);
      const cls = p.mismatches ? 'is-near' : '';
      const d = fw
        ? `M${x0.toFixed(1)},${yy - 3.5} H${(x1 - (tip ? 6 : 0)).toFixed(1)} L${x1.toFixed(1)},${yy} L${(x1 - (tip ? 6 : 0)).toFixed(1)},${yy + 3.5} H${x0.toFixed(1)} Z`
        : `M${x1.toFixed(1)},${yy - 3.5} H${(x0 + (tip ? 6 : 0)).toFixed(1)} L${x0.toFixed(1)},${yy} L${(x0 + (tip ? 6 : 0)).toFixed(1)},${yy + 3.5} H${x1.toFixed(1)} Z`;
      out.push(`<path d="${d}" class="sv2-primer ${cls}" data-primer="${p.id}"><title>${esc(p.name)} · ${num(p.start)}–${num(p.end)}${p.mismatches ? ` · ${esc(t('%(n)s mismatches', { n: p.mismatches }))}` : ''}</title></path>`);
      out.push(`<text x="${(x1 + 4).toFixed(1)}" y="${yy + 4}" class="sv2-primer-name ${cls}">${esc(p.name)}</text>`);
    });
    y += row.hPrim;
    // The selection, behind the bases.
    if (sel) {
      clip(sel.start, sel.end, row.r0, row.r1).forEach(([a, b]) => {
        out.push(`<rect x="${(X(a, row) - 1).toFixed(1)}" y="${y + 1}" width="${((b - a + 1) * cw + 2).toFixed(1)}" height="${row.hTop + row.hBot}" rx="4" class="sv2-sel"/>`);
      });
    }
    // Position, then the strands, each base placed by its own x.
    const bases = data.sequence.slice(row.r0 - 1, row.r1);
    const xs = Array.from(bases, (_b, i) => (GUTTER + i * cw + cw / 2).toFixed(1)).join(' ');
    out.push(`<text x="${GUTTER - 10}" y="${y + 13}" text-anchor="end" class="sv2-pos">${num(row.r0)}</text>`);
    out.push(`<text x="${xs}" y="${y + 13}" text-anchor="middle" class="sv2-top">${bases}</text>`);
    y += row.hTop;
    if (row.hBot) {
      out.push(`<text x="${xs}" y="${y + 11}" text-anchor="middle" class="sv2-bot">${Array.from(bases, (b) => COMP[b] || 'N').join('')}</text>`);
      y += row.hBot;
    }
    // Features.
    row.feat.forEach(({ f, x, y: e, lane }) => {
      const yy = y + 2 + lane * 14;
      const x0 = X(x, row);
      const w = (e - x + 1) * cw;
      out.push(`<rect x="${x0.toFixed(1)}" y="${yy}" width="${w.toFixed(1)}" height="11" rx="3" class="sv2-feat pmap-f pmap-f-${f.kind}" data-feature="${f.i}"><title>${esc(f.name)}</title></rect>`);
      if (w > 40) out.push(`<text x="${(x0 + 5).toFixed(1)}" y="${yy + 8.5}" class="sv2-feat-name" data-feature="${f.i}">${esc(String(f.name).slice(0, Math.floor((w - 8) / 6)))}</text>`);
    });
    y += row.hFeat;
    // Proteins: a letter under the middle of each codon.
    row.cds.forEach(({ tr, codons: cs }) => {
      cs.forEach((c) => {
        const mid = c.at + 1;
        if (mid < row.r0 || mid > row.r1) return;
        const x0 = X(c.at, row);
        const shade = c.n % 2 ? 'sv2-codon' : 'sv2-codon is-alt';
        const hot = panel && panel.kind === 'protein' && panel.mark && panel.mark.includes(c.n) && panel.track === tr.id;
        out.push(`<rect x="${(x0 + 1).toFixed(1)}" y="${y + 1}" width="${(3 * cw - 2).toFixed(1)}" height="15" rx="4" class="${shade}${hot ? ' is-hot' : ''}" data-track="${tr.id}"/>`);
        out.push(`<text x="${(X(mid, row) + cw / 2).toFixed(1)}" y="${y + 12.5}" text-anchor="middle" class="sv2-aa${c.aa === '*' ? ' is-stop' : ''}">${c.aa}</text>`);
        if (c.n % 10 === 0) out.push(`<text x="${(X(mid, row) + cw / 2).toFixed(1)}" y="${y + 25}" text-anchor="middle" class="sv2-aa-n">${c.n}</text>`);
      });
      y += 26;
    });
    out.push('</svg>');
    return out.join('');
  }

  function drawAll() {
    if (observer) observer.disconnect();
    const box = $('[data-rowlist]');
    box.innerHTML = rows.map((row, i) => `<div class="sv2-row" data-row="${i}" style="height:${row.height}px"></div>`).join('');
    observer = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const i = Number(e.target.dataset.row);
      e.target.innerHTML = drawRow(rows[i]);
      e.target.dataset.drawn = '1';
    }), { root: null, rootMargin: '600px 0px' });
    box.querySelectorAll('.sv2-row').forEach((el) => observer.observe(el));
    drawOverview();
  }

  // Redraw the rows already drawn (after the selection or a mark changes).
  function redraw() {
    root.querySelectorAll('.sv2-row[data-drawn]').forEach((el) => { el.innerHTML = drawRow(rows[Number(el.dataset.row)]); });
    placePopover();
    drawOverview();
  }

  function drawOverview() {
    const L = data.length;
    const W = 1000;
    const x = (bp) => 8 + (bp / L) * (W - 16);
    const out = [`<svg viewBox="0 0 ${W} 26" preserveAspectRatio="none" class="sv2-over-svg" aria-hidden="true">`,
      `<line x1="8" y1="13" x2="${W - 8}" y2="13" class="pmap-backbone"/>`];
    featureSpans().forEach((f) => clip(f.a, f.b, 1, L).forEach(([a, b]) => out.push(`<rect x="${x(a - 1).toFixed(1)}" y="8" width="${Math.max(2, x(b) - x(a - 1)).toFixed(1)}" height="10" rx="3" class="pmap-f pmap-f-${f.kind}" opacity=".7"/>`)));
    if (sel) clip(sel.start, sel.end, 1, L).forEach(([a, b]) => out.push(`<rect x="${x(a - 1).toFixed(1)}" y="2" width="${Math.max(3, x(b) - x(a - 1)).toFixed(1)}" height="22" rx="4" class="sv2-over-sel"/>`));
    out.push('</svg>');
    $('[data-over]').innerHTML = out.join('');
  }

  // ---------------------------------------------------------------- picking bases

  // The base under a point in a row, 1-based (clamped to the row).
  function baseAt(ev) {
    const rowEl = ev.target.closest('.sv2-row');
    if (!rowEl) return null;
    const row = rows[Number(rowEl.dataset.row)];
    const svg = rowEl.querySelector('svg');
    if (!svg) return null;
    const box = svg.getBoundingClientRect();
    const scale = box.width / svg.viewBox.baseVal.width;
    const i = Math.floor(((ev.clientX - box.left) / scale - GUTTER) / cw);
    return Math.max(row.r0, Math.min(row.r1, row.r0 + i));
  }

  let dragFrom = null;
  root.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || !e.target.closest('[data-rows]') || e.target.closest('[data-feature], [data-primer]')) return;
    const at = baseAt(e);
    if (at == null) return;
    e.preventDefault();
    if (e.shiftKey && sel) {
      sel = { start: Math.min(sel.start, at), end: Math.max(sel.end, at) };
      picked();
      return;
    }
    dragFrom = at;
    sel = { start: at, end: at };
    redraw();
  });
  root.addEventListener('pointermove', (e) => {
    if (dragFrom == null || !e.buttons) return;
    const at = baseAt(e);
    if (at == null) return;
    const next = { start: Math.min(dragFrom, at), end: Math.max(dragFrom, at) };
    if (next.start !== sel.start || next.end !== sel.end) { sel = next; redraw(); }
  });
  window.addEventListener('pointerup', () => {
    if (dragFrom == null) return;
    dragFrom = null;
    if (sel && sel.end === sel.start) { sel = null; redraw(); return; }
    picked();
  });

  const pickedText = () => {
    if (!sel) return '';
    return data.sequence.slice(sel.start - 1, sel.end);
  };
  function gc(s) { return s.length ? Math.round(100 * (s.match(/[GC]/g) || []).length / s.length) : 0; }
  // Tm by the Wallace rule for the bar; the panel asks the server for nearest neighbours.
  function quickTm(s) {
    const g = (s.match(/[GC]/g) || []).length;
    return s.length < 14 ? 2 * (s.length - g) + 4 * g : 64.9 + 41 * (g - 16.4) / s.length;
  }

  function picked() {
    redraw();
    placePopover();
  }

  function placePopover() {
    const pop = $('[data-pop]');
    if (!sel || sel.start === sel.end) { pop.hidden = true; return; }
    const s = pickedText();
    pop.querySelector('[data-pop-text]').innerHTML = `<b class="tabular-nums">${num(sel.start)}–${num(sel.end)}</b> · ${esc(t('%(n)s bp', { n: s.length }))} · Tm ${quickTm(s).toFixed(0)} °C · GC ${gc(s)}%`;
    const primerOk = s.length >= 15 && s.length <= 60;
    pop.querySelectorAll('[data-make]').forEach((b) => { b.disabled = !primerOk; });
    const i = rows.findIndex((r) => sel.end >= r.r0 && sel.end <= r.r1);
    const rowEl = root.querySelector(`.sv2-row[data-row="${i}"]`);
    if (!rowEl) { pop.hidden = true; return; }
    const row = rows[i];
    const host = $('[data-rows]').getBoundingClientRect();
    const r = rowEl.getBoundingClientRect();
    const top = r.top - host.top + 10 + row.hEnz + row.hPrim + row.hTop + row.hBot + 6 + $('[data-rows]').scrollTop;
    let left = X(sel.end, row) - 120;
    pop.hidden = false;
    left = Math.max(8, Math.min(left, host.width - pop.offsetWidth - 8));
    pop.style.top = `${top}px`;
    pop.style.left = `${left}px`;
  }

  // ---------------------------------------------------------------- the side panel

  function sidePanel(html) {
    const box = $('[data-side]');
    box.innerHTML = html;
    box.hidden = !html;
  }

  function primerPanel(direction) {
    if (!sel) return;
    const url = new URL(root.dataset.check, window.location.href);
    url.searchParams.set('start', sel.start);
    url.searchParams.set('end', sel.end);
    url.searchParams.set('direction', direction);
    sidePanel(`<p class="sv2-note">${esc(t('Checking the primer…'))}</p>`);
    fetch(url, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json()).then((j) => {
      if (!j.ok) { sidePanel(`<p class="sv2-note">${esc(j.error || '')}</p>`); return; }
      panel = { kind: 'primer', check: j };
      const ok = (yes, good, bad) => `<span class="badge ${yes ? 'badge-ok' : 'badge-warn'}">${esc(yes ? good : bad)}</span>`;
      const same = j.same.length ? `<p>${esc(t('The lab has it already:'))} ${j.same.map((h) => `<a href="${esc(h.url)}">${esc(h.name)}</a>${h.where ? ` <span class="text-muted">${esc(h.where)}</span>` : ''}`).join(', ')}</p>`
        : j.near.length ? `<p>${esc(t('No primer at this site. Nearest:'))} ${j.near.map((h) => `<a href="${esc(h.url)}">${esc(h.name)}</a> <span class="text-muted">${num(h.start)}</span>`).join(', ')}</p>`
          : `<p class="text-muted">${esc(t('No primer of the lab’s binds near here.'))}</p>`;
      sidePanel(`<div class="sv2-panel-head"><h3>${esc(t('New primer'))}</h3>
          <div class="seg"><button type="button" class="seg-item${direction > 0 ? ' is-active' : ''}" data-make="1">→ ${esc(t('Forward'))}</button>
          <button type="button" class="seg-item${direction < 0 ? ' is-active' : ''}" data-make="-1">← ${esc(t('Reverse'))}</button></div></div>
        <label class="sv2-field"><span>${esc(t('Name'))}</span><input type="text" data-primer-name value="${esc(j.name)}"></label>
        <label class="sv2-field"><span>${esc(t('5′ tail (a site, an overlap)'))}</span><input type="text" data-primer-tail placeholder="${esc(t('none'))}" spellcheck="false"></label>
        <div class="sv2-field"><span>5′ → 3′</span><code class="sv2-primer-seq" data-primer-seq>${esc(j.sequence)}</code></div>
        <dl class="pver-dl">
          <dt>${esc(t('Length'))}</dt><dd class="tabular-nums">${j.length} nt ${j.length_ok ? '' : ok(false, '', t('18–30 is usual'))}</dd>
          <dt>Tm</dt><dd class="tabular-nums">${j.tm != null ? `${j.tm} °C` : '—'}</dd>
          <dt>GC</dt><dd class="tabular-nums">${j.gc}% ${j.gc_ok ? '' : ok(false, '', t('40–60% is usual'))}</dd>
          <dt>${esc(t('3′ end'))}</dt><dd>${ok(j.clamp, t('G/C clamp'), t('no G/C clamp'))}</dd>
          <dt>${esc(t('Hairpin'))}</dt><dd>${ok(j.hairpin_ok, j.hairpin < 0 ? `ΔG ${j.hairpin}` : t('none'), `ΔG ${j.hairpin}`)}</dd>
          <dt>${esc(t('Self-dimer'))}</dt><dd>${ok(j.dimer_ok, `ΔG ${j.dimer}`, `ΔG ${j.dimer}`)}</dd>
          <dt>${esc(t('Binds here'))}</dt><dd>${ok(j.binds === 1 && !j.near, t('once'), j.binds > 1 ? t('%(n)s places', { n: j.binds }) : t('also near-matches'))}</dd>
        </dl>
        <div class="sv2-lab">${same}</div>
        <div class="pver-actions">
          <button type="button" class="btn btn-primary" data-save-primer>${esc(t('Save to Primers'))}</button>
          <button type="button" class="btn" data-copy-primer>${icon('copy')} ${esc(t('Copy'))}</button>
        </div>`);
    });
  }

  function proteinPanel(track, stretch) {
    const p = track;
    const groups = (p.protein || '').match(/.{1,10}/g) || [];
    const lines = [];
    for (let i = 0; i < groups.length; i += 3) lines.push(`<div><span class="sv2-aa-start">${i * 10 + 1}</span>${groups.slice(i, i + 3).join(' ')}</div>`);
    sidePanel(`<div class="sv2-panel-head"><h3><span class="pdig-dot pmap-f-cds"></span>${esc(stretch ? t('Translation of %(start)s–%(end)s', { start: num(p.start), end: num(p.end) }) : t('%(name)s protein', { name: p.name }))}</h3>
        <button type="button" class="btn btn-sm" data-copy-protein>${esc(t('Copy'))}</button></div>
      <dl class="pver-dl">
        <dt>${esc(t('Length'))}</dt><dd class="tabular-nums">${esc(t('%(n)s aa', { n: p.length }))}</dd>
        <dt>${esc(t('Mass'))}</dt><dd class="tabular-nums">${p.mass_kda} kDa</dd>
        <dt>pI</dt><dd class="tabular-nums">${p.pi != null ? p.pi : '—'}</dd>
        <dt>ε280</dt><dd class="tabular-nums" title="${esc(t('Every cysteine in a cystine; %(r)s if none are', { r: num(p.extinction_reduced) }))}">${num(p.extinction)} M⁻¹cm⁻¹</dd>
        <dt>${esc(t('Starts · ends'))}</dt><dd>${p.starts_with_m ? 'M' : esc(p.protein[0] || '—')} … ${esc(p.stop || t('no stop'))}</dd>
        <dt>${esc(t('Frame'))}</dt><dd>${esc(p.internal_stop ? t('a stop inside it') : p.whole_codons ? t('whole, no stop inside') : t('not whole codons'))}</dd>
      </dl>
      <div class="sv2-protein" data-protein-text>${lines.join('')}</div>`);
    panel = { kind: 'protein', protein: p.protein, name: p.name };
  }

  function translateStretch() {
    if (!sel) return;
    const s = pickedText();
    const aa = [];
    for (let i = 0; i + 2 < s.length; i += 3) aa.push(CODE[s.slice(i, i + 3)] || 'X');
    const protein = aa.join('');
    const stop = protein.indexOf('*');
    const body = stop < 0 ? protein : protein.slice(0, stop);
    const count = (a) => body.split(a).length - 1;
    const mass = body.length ? (Array.from(body).reduce((m, a) => m + (RESIDUE[a] || 110), 0) + 18.015) / 1000 : 0;
    const reduced = count('W') * 5500 + count('Y') * 1490;
    proteinPanel({ name: '', start: sel.start, end: sel.end, protein, length: body.length, mass_kda: mass.toFixed(1),
      pi: null, extinction: reduced + Math.floor(count('C') / 2) * 125, extinction_reduced: reduced, starts_with_m: protein[0] === 'M',
      stop: stop >= 0 ? s.slice(3 * stop, 3 * stop + 3) : '', internal_stop: stop >= 0 && stop < protein.length - 1,
      whole_codons: s.length % 3 === 0 }, true);
  }

  function copy(text, what) {
    const done = () => say(t('%(what)s copied.', { what }));
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => window.BioDialog.prompt(t('Copy the text below.'), text, { okLabel: t('Done') }));
  }

  // ---------------------------------------------------------------- finding

  function find(q) {
    const query = q.trim();
    const box = $('[data-find-count]');
    if (!query) { box.textContent = ''; return; }
    const upper = query.toUpperCase().replace(/\s+/g, '');
    let hits = [];
    const primer = data.primers.filter((p) => p.name.toLowerCase().includes(query.toLowerCase()));
    const enzyme = data.cutters.filter((c) => c.name.toLowerCase() === query.toLowerCase());
    const feature = featureSpans().filter((f) => String(f.name).toLowerCase().includes(query.toLowerCase()));
    if (/^[ACGTN]{4,}$/.test(upper)) {
      const seq = data.sequence + (data.circular ? data.sequence.slice(0, upper.length - 1) : '');
      [upper, revcomp(upper)].forEach((probe) => {
        let at = seq.indexOf(probe);
        while (at >= 0 && at < data.length) {
          hits.push({ start: at + 1, end: ((at + probe.length - 1) % data.length) + 1 });
          at = seq.indexOf(probe, at + 1);
        }
      });
    }
    hits = hits.concat(primer.map((p) => ({ start: p.start, end: p.end })), enzyme.map((c) => ({ start: c.cut, end: c.cut + 1 })),
      feature.map((f) => ({ start: f.a, end: f.b })));
    box.textContent = hits.length ? t('%(n)s found', { n: hits.length }) : t('Not found');
    if (!hits.length) return;
    const i = (Number(box.dataset.i || -1) + 1) % hits.length;
    box.dataset.i = i;
    sel = hits[i];
    goTo(sel.start);
    picked();
  }

  // Scroll the rows (not the window) to the row holding `pos`, near the middle.
  function goTo(pos) {
    const i = rows.findIndex((r) => pos >= r.r0 && pos <= r.r1);
    const el = root.querySelector(`.sv2-row[data-row="${i}"]`);
    const box = $('[data-rows]');
    if (el) box.scrollTo({ top: Math.max(0, el.offsetTop - box.clientHeight / 3), behavior: 'smooth' });
  }

  // ---------------------------------------------------------------- the bar and clicks

  function drawBar() {
    root.querySelectorAll('[data-show]').forEach((b) => b.classList.toggle('is-on', !!show[b.dataset.show]));
    root.querySelectorAll('[data-frames]').forEach((b) => b.classList.toggle('is-active', b.dataset.frames === show.frames));
  }

  root.addEventListener('click', (e) => {
    const toggle = e.target.closest('[data-show]');
    if (toggle) { show[toggle.dataset.show] = !show[toggle.dataset.show]; remember(); drawBar(); layout(); drawAll(); return; }
    const frames = e.target.closest('[data-frames]');
    if (frames) { show.frames = frames.dataset.frames; show.translation = true; remember(); drawBar(); layout(); drawAll(); return; }
    const make = e.target.closest('[data-make]');
    if (make && sel) { primerPanel(Number(make.dataset.make)); return; }
    if (e.target.closest('[data-pop-copy]') && sel) { copy(pickedText(), t('The sequence')); return; }
    if (e.target.closest('[data-pop-translate]') && sel) { translateStretch(); return; }
    const feature = e.target.closest('[data-feature]');
    if (feature) {
      const f = featureSpans()[Number(feature.dataset.feature)];
      sel = { start: f.a, end: f.b };
      picked();
      const p = data.proteins.find((x) => x.feature === f.i);
      if (p) proteinPanel(p);
      return;
    }
    const primer = e.target.closest('[data-primer]');
    if (primer) {
      const p = data.primers.find((x) => String(x.id) === primer.dataset.primer);
      if (p) { sel = { start: p.start, end: p.end }; picked(); }
      return;
    }
    if (e.target.closest('[data-copy-protein]') && panel && panel.kind === 'protein') { copy(panel.protein, t('The protein')); return; }
    if (e.target.closest('[data-copy-primer]') && panel && panel.kind === 'primer') { copy(primerSequence(), t('The primer')); return; }
    if (e.target.closest('[data-save-primer]') && panel && panel.kind === 'primer') { savePrimer(e.target.closest('[data-save-primer]')); return; }
    const over = e.target.closest('[data-over]');
    if (over) {
      const box = over.getBoundingClientRect();
      goTo(Math.max(1, Math.round(((e.clientX - box.left) / box.width) * data.length)));
    }
  });

  const primerSequence = () => {
    const tail = ($('[data-primer-tail]') ? $('[data-primer-tail]').value : '').toUpperCase().replace(/[^ACGT]/g, '');
    return tail + panel.check.sequence;
  };
  root.addEventListener('input', (e) => {
    if (e.target.matches('[data-primer-tail]') && panel && panel.kind === 'primer') {
      $('[data-primer-seq]').innerHTML = `<span class="sv2-tail">${esc(primerSequence().slice(0, primerSequence().length - panel.check.sequence.length))}</span>${esc(panel.check.sequence)}`;
    }
    if (e.target.matches('[data-find]')) $('[data-find-count]').dataset.i = -1;
  });
  root.addEventListener('keydown', (e) => {
    if (e.target.matches('[data-find]') && e.key === 'Enter') { e.preventDefault(); find(e.target.value); }
    if (e.key === 'Escape' && sel) { sel = null; redraw(); }
  });

  function savePrimer(button) {
    const body = new FormData();
    body.append('name', $('[data-primer-name]').value);
    body.append('sequence', primerSequence());
    body.append('direction', panel.check.direction);
    button.disabled = true;
    fetch(root.dataset.save, { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } })
      .then((r) => r.json()).then((j) => {
        button.disabled = false;
        if (!j.ok) { say(j.error, 'error'); return; }
        say(j.message);
        load();               // its arrow shows on the sequence now
      }).catch(() => { button.disabled = false; say(t('The server did not answer. Check the connection and try again.'), 'error'); });
  }

  // ---------------------------------------------------------------- start

  function load() {
    return fetch(root.dataset.url, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json()).then((j) => {
      if (!j.ok) { $('[data-rowlist]').innerHTML = `<p class="sv2-note">${esc(j.error || '')}</p>`; return; }
      data = j;
      drawBar();
      layout();
      drawAll();
      window.dispatchEvent(new CustomEvent('biomanager:plasmid-view', { detail: data }));
    });
  }
  // Laid out again when its column changes width: the window, or the side panel opening.
  let width = 0;
  let again = null;
  new ResizeObserver(() => {
    const now = $('[data-rows]').clientWidth;
    if (!data || !now || Math.abs(now - width) < 20) return;
    width = now;
    clearTimeout(again);
    again = setTimeout(() => { layout(); drawAll(); placePopover(); if (sel) goTo(sel.start); }, 60);
  }).observe($('[data-rows]'));

  const tabPanel = root.closest('.plasmid-tab-panel');
  const opened = () => { if (!data) { width = $('[data-rows]').clientWidth; load(); } };
  if (tabPanel && tabPanel.classList.contains('is-active')) opened();
  document.querySelectorAll('.plasmid-tab[data-tab="sequence"]').forEach((tab) => tab.addEventListener('click', () => setTimeout(opened, 0)));

  // From elsewhere on the page (Overview's features): go to a stretch and
  // pick it; a coding feature's protein shows beside it.
  window.BioSeqViewer = {
    goTo: (pos) => { if (data) goTo(pos); },
    select: (a, b) => {
      if (!data) return;
      sel = { start: a, end: b };
      goTo(a);
      picked();
      const p = data.proteins.find((x) => x.start === a && x.end === b);
      if (p) proteinPanel(p);
    },
  };
})();
