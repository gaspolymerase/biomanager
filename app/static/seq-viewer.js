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

   Edit… (on a plasmid the person may change) types over the picked
   bases, inserts after them or deletes them, or adds a feature on them;
   a feature's panel renames, retypes, turns round or removes it; ⌘Z
   undoes (app/sequence_routes.py edit.json, app/sequence_edit.py).

   Mutagenesis… (one base is enough) designs a site-directed mutagenesis: the
   picked bases replaced, bases inserted after them, or the bases deleted,
   or a coding feature's amino acid written as K79A; its two primers, what
   it does to the protein, silent changes that add or remove an enzyme's
   site, and Make it (app/mutagenesis_routes.py).

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
    if (!sel) { pop.hidden = true; return; }
    const s = pickedText();
    pop.querySelector('[data-pop-text]').innerHTML = s.length === 1 ? `<b class="tabular-nums">${esc(t('Base %(n)s', { n: num(sel.start) }))}</b> ${esc(s)}`
      : `<b class="tabular-nums">${num(sel.start)}–${num(sel.end)}</b> · ${esc(t('%(n)s bp', { n: s.length }))} · Tm ${quickTm(s).toFixed(0)} °C · GC ${gc(s)}%`;
    const primerOk = s.length >= 15 && s.length <= 60;
    pop.querySelectorAll('[data-make]').forEach((b) => { b.disabled = !primerOk; });
    pop.querySelector('[data-pop-translate]').disabled = s.length < 3;
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

  // ---------------------------------------------------------------- mutagenesis

  let mut = null;            // {mode, start, end, bases, protein, cds, result}
  let mutTimer = null;

  // The coding feature the picked bases sit in, and the codon they start in.
  function cdsAt(a, b) {
    const p = data.proteins.find((x) => x.start <= x.end && a >= x.start && b <= x.end);
    if (!p) return null;
    const n = p.direction >= 0 ? Math.floor((a - p.start) / 3) + 1 : Math.floor((p.end - b) / 3) + 1;
    return { ...p, codon: n, aa: (p.protein || '')[n - 1] || '' };
  }

  function mutatePanel() {
    mut = { mode: 'replace', start: sel.start, end: sel.end, bases: pickedText(), protein: '', cds: cdsAt(sel.start, sel.end), result: null };
    panel = { kind: 'mutate' };
    mutateForm();
  }

  // The request's start, end and bases for the mode.
  function mutRequest() {
    const url = new URL(root.dataset.mutate, window.location.href);
    if (mut.protein && mut.cds) {
      url.searchParams.set('feature', mut.cds.feature);
      url.searchParams.set('protein', mut.protein);
      return url;
    }
    const bases = mut.bases.toUpperCase().replace(/[^ACGT]/g, '');
    if (mut.mode === 'insert') { url.searchParams.set('start', mut.end + 1); url.searchParams.set('end', mut.end); }
    else { url.searchParams.set('start', mut.start); url.searchParams.set('end', mut.end); }
    url.searchParams.set('bases', mut.mode === 'delete' ? '' : bases);
    if (mut.site) url.searchParams.set('label', mut.site);
    return url;
  }

  function mutateForm() {
    const n = mut.end - mut.start + 1;
    const where = mut.mode === 'insert' ? t('After base %(n)s', { n: num(mut.end) })
      : n === 1 ? t('Base %(n)s', { n: num(mut.start) }) : `${num(mut.start)}–${num(mut.end)} · ${t('%(n)s bp', { n })}`;
    const seg = (key, label) => `<button type="button" class="seg-item${mut.mode === key ? ' is-active' : ''}" data-mut-mode="${key}">${esc(label)}</button>`;
    const bases = mut.mode === 'delete' ? `<p class="sv2-note">${esc(t('Deletes %(n)s bases.', { n }))}</p>`
      : `<label class="sv2-field"><span>${esc(mut.mode === 'insert' ? t('Bases to insert') : t('Becomes'))}</span><input type="text" data-mut-bases value="${esc(mut.mode === 'insert' ? '' : mut.bases)}" spellcheck="false" autocomplete="off"></label>`;
    const cds = mut.cds && mut.mode === 'replace'
      ? `<label class="sv2-field"><span>${esc(t('Or the protein (%(name)s)', { name: mut.cds.name }))}</span><input type="text" data-mut-protein value="${esc(mut.protein)}" placeholder="${esc(`${mut.cds.aa}${mut.cds.codon}A`)}" spellcheck="false" autocomplete="off"></label>`
      : '';
    sidePanel(`<div class="sv2-panel-head"><h3>${esc(t('Mutagenesis'))}</h3><span class="sv2-mut-where tabular-nums">${esc(where)}</span></div>
      <div class="seg sv2-mut-modes">${seg('replace', t('Replace'))}${seg('insert', t('Insert bases'))}${seg('delete', t('Delete'))}</div>
      ${bases}${cds}
      <div data-mut-result><p class="sv2-note">${esc(t('Type what the bases become.'))}</p></div>
      ${mut.cds ? `<div class="sv2-mut-silent" data-mut-silent-box><button type="button" class="btn btn-sm" data-mut-silent>${esc(t('Silent changes that add or remove a site'))}</button></div>` : ''}`);
    const focus = $('[data-mut-bases]');
    if (focus) { focus.focus(); focus.select(); }
    if (mut.mode === 'delete') mutate();
  }

  function mutateSoon() { clearTimeout(mutTimer); mutTimer = setTimeout(mutate, 250); }

  function mutate() {
    const box = $('[data-mut-result]');
    if (!box) return;
    const asked = mutRequest().toString();
    fetch(asked, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json()).then((j) => {
      if (!mut || mutRequest().toString() !== asked) return;               // typed on since
      mut.result = j.ok ? j : null;
      if (!j.ok) { box.innerHTML = `<p class="sv2-note">${esc(j.error || '')}</p>`; return; }
      const badge = (yes, good, bad) => `<span class="badge ${yes ? 'badge-ok' : 'badge-warn'}">${esc(yes ? good : bad)}</span>`;
      const primer = (label, p) => `<div class="sv2-mut-primer"><div class="sv2-mut-primer-head"><b>${esc(label)}</b>
          <span class="tabular-nums text-muted">${p.length} nt · ${esc(t('anneals %(n)s nt', { n: p.anneal_length }))} · Tm ${p.tm != null ? `${p.tm} °C` : '—'}</span></div>
        <code class="sv2-primer-seq"><span class="sv2-tail">${esc(p.tail)}</span>${esc(p.anneal)}</code>
        <div class="sv2-mut-checks">${p.length > 45 ? badge(false, '', t('long: order it purified')) : ''}${p.hairpin_ok ? '' : badge(false, '', `${t('Hairpin')} ΔG ${p.hairpin}`)}${p.dimer_ok ? '' : badge(false, '', `${t('Self-dimer')} ΔG ${p.dimer}`)}${p.binds > 1 ? badge(false, '', t('binds %(n)s places', { n: p.binds })) : ''}</div></div>`;
      const kinds = { silent: t('silent'), missense: t('missense'), nonsense: t('a stop'), frameshift: t('frameshift'),
        deletion: t('deletion in frame'), insertion: t('insertion in frame'), delins: t('in frame'), removed: t('removed') };
      const effects = j.effects.length ? j.effects.map((e) => `<li><b>${esc(e.name)}</b> ${e.label ? `<code>${esc(e.label)}</code> ` : ''}<span class="badge ${e.kind === 'silent' || e.kind === 'missense' ? 'badge-ok' : 'badge-warn'}">${esc(kinds[e.kind] || e.kind)}</span>${e.kind === 'frameshift' ? ` <span class="text-muted">${esc(t('%(n)s aa, then a stop', { n: e.length_after }))}</span>` : ''}</li>`).join('')
        : `<li class="text-muted">${esc(t('No coding feature is changed.'))}</li>`;
      const extend = j.extend_s >= 60 ? t('%(m)s min %(s)s s', { m: Math.floor(j.extend_s / 60), s: j.extend_s % 60 }) : `${j.extend_s} s`;
      box.innerHTML = `${j.codon ? `<p class="sv2-note">${esc(t('Codon %(from)s becomes %(to)s.', { from: j.codon.codon, to: j.codon.to }))}</p>` : ''}
        <ul class="sv2-mut-effects">${effects}</ul>
        ${primer(t('Forward'), j.forward)}${primer(t('Reverse'), j.reverse)}
        <dl class="pver-dl">
          <dt>${esc(t('The new plasmid'))}</dt><dd class="tabular-nums">${esc(t('%(n)s bp', { n: num(j.length) }))}${j.delta ? ` (${j.delta > 0 ? '+' : ''}${j.delta})` : ''}</dd>
          <dt>PCR</dt><dd>${esc(t('Anneal at %(c)s °C, extend %(time)s, then KLD.', { c: j.anneal, time: extend }))}</dd>
        </dl>
        <label class="sv2-field"><span>${esc(t('Name'))}</span><input type="text" data-mut-name value="${esc(j.name)}"></label>
        <div class="pver-actions">
          <button type="button" class="btn btn-primary" data-mut-make>${esc(t('Make it'))}</button>
          <button type="button" class="btn" data-mut-copy>${icon('copy')} ${esc(t('Copy the primers'))}</button>
        </div>
        <p class="sv2-note">${esc(t('Make it adds the new plasmid, made from this one, and puts both primers in Primers to order.'))}</p>`;
    }).catch(() => { box.innerHTML = `<p class="sv2-note">${esc(t('The server did not answer. Check the connection and try again.'))}</p>`; });
  }

  function silentSites() {
    const box = $('[data-mut-silent-box]');
    const url = new URL(root.dataset.silent, window.location.href);
    url.searchParams.set('feature', mut.cds.feature);
    url.searchParams.set('start', mut.start);
    url.searchParams.set('end', mut.end);
    box.innerHTML = `<p class="sv2-note">${esc(t('Looking for silent changes…'))}</p>`;
    fetch(url, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json()).then((j) => {
      const cuts = (n) => (n === 1 ? t('then cuts once') : t('then cuts %(n)s times', { n }));
      const row = (r, adding) => `<li><b>${adding ? '+' : '−'} ${esc(r.enzyme)}</b>
          <span class="sv2-mut-site-how tabular-nums">${esc(t('codon %(n)s', { n: r.codon }))} <code>${esc(r.from)}→${esc(r.to)}</code> · ${esc(t('%(n)s bp changed', { n: r.changes }))} · ${esc(cuts(r.cuts_after))}</span>
          <button type="button" class="btn btn-sm" data-mut-use="${esc(JSON.stringify({ start: r.start, end: r.end, bases: r.bases, site: `${adding ? '+' : '-'}${r.enzyme}` }))}">${esc(t('Use this'))}</button></li>`;
      const list = (rows, adding, title) => (rows.length ? `<h4>${esc(title)}</h4><ul class="sv2-mut-sites">${rows.map((r) => row(r, adding)).join('')}</ul>` : '');
      box.innerHTML = (list(j.add || [], true, t('Add a site')) + list(j.remove || [], false, t('Remove a site')))
        || `<p class="sv2-note">${esc(t('No silent change near here adds or removes a site.'))}</p>`;
    });
  }

  function useSilent(r) {
    sel = { start: r.start, end: r.end };
    redraw();
    goTo(r.start);
    mut = { ...mut, mode: 'replace', start: r.start, end: r.end, bases: r.bases, protein: '', site: r.site };
    mutateForm();
    mutate();
  }

  function makeMutant(button) {
    const body = new FormData();
    const url = mutRequest();
    url.searchParams.forEach((v, k) => body.append(k, v));
    body.append('name', $('[data-mut-name]').value);
    button.disabled = true;
    fetch(root.dataset.mutateMake, { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } })
      .then((r) => r.json()).then((j) => {
        if (!j.ok) { button.disabled = false; say(j.error, 'error'); return; }
        say(j.message);
        window.location.href = j.url;
      }).catch(() => { button.disabled = false; say(t('The server did not answer. Check the connection and try again.'), 'error'); });
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
    if (e.target.closest('[data-pop-mutate]') && sel) { mutatePanel(); return; }
    const mode = e.target.closest('[data-mut-mode]');
    if (mode && mut) { mut.mode = mode.dataset.mutMode; mut.protein = ''; mut.site = ''; mutateForm(); return; }
    if (e.target.closest('[data-mut-silent]') && mut) { silentSites(); return; }
    const use = e.target.closest('[data-mut-use]');
    if (use && mut) { useSilent(JSON.parse(use.dataset.mutUse)); return; }
    if (e.target.closest('[data-mut-make]') && mut && mut.result) { makeMutant(e.target.closest('[data-mut-make]')); return; }
    if (e.target.closest('[data-mut-copy]') && mut && mut.result) {
      copy(`${mut.result.name} F\t${mut.result.forward.sequence}\n${mut.result.name} R\t${mut.result.reverse.sequence}`, t('The primers'));
      return;
    }
    const feature = e.target.closest('[data-feature]');
    if (feature) {
      const f = featureSpans()[Number(feature.dataset.feature)];
      sel = { start: f.a, end: f.b };
      picked();
      featurePanel(f);
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
    if (e.target.matches('[data-mut-bases]') && mut) {
      mut.bases = e.target.value;
      mut.protein = '';
      mut.site = '';
      if ($('[data-mut-protein]')) $('[data-mut-protein]').value = '';
      mutateSoon();
    }
    if (e.target.matches('[data-mut-protein]') && mut) { mut.protein = e.target.value; mut.site = ''; mutateSoon(); }
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

  // ---------------------------------------------------------------- editing

  // On a plasmid the person may edit (data-edit, app/sequence_routes.py
  // edit.json): Edit… on picked bases types over them, inserts after them
  // or deletes them, or adds a feature on them; a feature's panel renames,
  // retypes, turns round or removes it. With bases picked, typing a base
  // letter opens Edit with it, Delete removes them, and ⌘Z undoes. Each
  // edit is saved at once; the server moves the features with it and
  // refuses an edit made on a page someone has since changed.
  const FEATURE_TYPES = ['misc_feature', 'CDS', 'promoter', 'terminator', 'rep_origin', 'polyA_signal', 'enhancer',
    'primer_bind', 'protein_bind', 'LTR', 'sig_peptide', "5'UTR", "3'UTR", 'intron', 'exon', 'misc_RNA', 'ncRNA',
    'regulatory', 'RBS', 'tag'];
  const IUPAC = /^[ACGTURYSWKMBDHVN]$/i;
  const undoStack = [];
  let ed = null;               // the Edit panel: {mode, start, end}
  const editable = () => !!(root.dataset.edit && data && data.editable);

  function editState() {
    const undo = $('[data-undo]');
    if (undo) { undo.hidden = !editable(); undo.disabled = !undoStack.length; }
    const pop = $('[data-pop-edit]');
    if (pop) pop.hidden = !editable();
    const topo = document.querySelector('[data-topology]');
    if (topo && data) topo.textContent = data.circular ? t('Make it linear') : t('Make it circular');
  }

  function send(body, after) {
    if (!editable()) return Promise.resolve();
    return fetch(root.dataset.edit, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
      body: JSON.stringify({ ...body, base: data.hash }),
    }).then((r) => r.json()).then((j) => {
      if (!j.ok) { say(j.error, 'error'); return; }
      if (!j.changed) { say(j.message); return; }
      if (body.op !== 'state' && j.before) undoStack.push(j.before);
      if (undoStack.length > 100) undoStack.shift();
      say(j.message);
      return load().then(() => { if (after) after(); editState(); });
    }).catch(() => say(t('The server did not answer. Check the connection and try again.'), 'error'));
  }

  function undo() {
    if (!undoStack.length) return;
    send({ op: 'state', state: undoStack.pop() }, () => { if (sel) picked(); });
  }

  function reselect(start, end) {
    const L = data.length;
    sel = { start: Math.max(1, Math.min(start, L)), end: Math.max(1, Math.min(end, L)) };
    goTo(sel.start);
    picked();
  }

  function editPanel(mode, typed) {
    if (!sel || !editable()) return;
    ed = { mode: mode || 'replace', start: sel.start, end: sel.end };
    panel = { kind: 'edit' };
    const n = ed.end - ed.start + 1;
    const where = ed.mode === 'insert' ? t('After base %(n)s', { n: num(ed.end) })
      : n === 1 ? t('Base %(n)s', { n: num(ed.start) }) : `${num(ed.start)}–${num(ed.end)} · ${t('%(n)s bp', { n })}`;
    const seg = (key, label) => `<button type="button" class="seg-item${ed.mode === key ? ' is-active' : ''}" data-ed-mode="${key}">${esc(label)}</button>`;
    const field = ed.mode === 'delete'
      ? `<p class="sv2-note">${esc(t('Deletes %(n)s bases.', { n }))}</p>`
      : `<label class="sv2-field"><span>${esc(ed.mode === 'insert' ? t('Bases to insert') : t('Becomes'))}</span>
           <input type="text" data-ed-bases value="${esc(typed != null ? typed : ed.mode === 'replace' ? pickedText() : '')}" spellcheck="false" autocomplete="off"></label>`;
    const types = FEATURE_TYPES.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
    sidePanel(`<div class="sv2-panel-head"><h3>${esc(t('Edit'))}</h3><span class="sv2-mut-where tabular-nums">${esc(where)}</span></div>
      <div class="seg sv2-mut-modes">${seg('replace', t('Replace'))}${seg('insert', t('Insert bases'))}${seg('delete', t('Delete'))}</div>
      ${field}
      <div class="pver-actions"><button type="button" class="btn btn-primary" data-ed-apply>${esc(t('Apply'))}</button></div>
      <p class="sv2-note">${esc(t('Saved at once. Features after the change move with it, and one it runs through grows or shrinks. ⌘Z undoes.'))}</p>
      <div class="sv2-mut-silent">
        <h4>${esc(t('Add a feature on these bases'))}</h4>
        <label class="sv2-field"><span>${esc(t('Name'))}</span><input type="text" data-ed-fname autocomplete="off"></label>
        <label class="sv2-field"><span>${esc(t('Type'))}</span><select data-ed-ftype>${types}</select></label>
        <div class="seg sv2-mut-modes"><button type="button" class="seg-item is-active" data-ed-fdir="1">→ ${esc(t('Forward'))}</button>
          <button type="button" class="seg-item" data-ed-fdir="-1">← ${esc(t('Reverse'))}</button></div>
        <div class="pver-actions"><button type="button" class="btn" data-ed-add>${esc(t('Add the feature'))}</button></div>
      </div>`);
    const input = $('[data-ed-bases]');
    if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }

  function applyEdit() {
    if (!ed) return;
    const typed = ($('[data-ed-bases]') ? $('[data-ed-bases]').value : '').replace(/\s+/g, '');
    if (ed.mode === 'delete') {
      const at = ed.start;
      send({ op: 'bases', start: ed.start, end: ed.end, bases: '' }, () => { reselect(at, at); sidePanel(''); ed = null; });
      return;
    }
    const start = ed.mode === 'insert' ? ed.end + 1 : ed.start;
    const end = ed.mode === 'insert' ? ed.end : ed.end;
    send({ op: 'bases', start, end, bases: typed }, () => {
      const put = typed.replace(/[^A-Za-z]/g, '').length;
      reselect(start, start + Math.max(put, 1) - 1);
      sidePanel('');
      ed = null;
    });
  }

  function addFeature() {
    if (!ed) return;
    const dir = $('[data-ed-fdir].is-active');
    send({ op: 'add_feature', start: ed.start, end: ed.end, name: $('[data-ed-fname]').value,
      type: $('[data-ed-ftype]').value, direction: dir ? Number(dir.dataset.edFdir) : 1 },
    () => { reselect(ed.start, ed.end); sidePanel(''); ed = null; });
  }

  // A feature's own block, under its protein or on its own.
  function featureEditor(f) {
    const stored = data.features[f.i];
    if (!editable() || !stored || stored.at == null) return '';
    const types = FEATURE_TYPES.concat(FEATURE_TYPES.includes(stored.type) || !stored.type ? [] : [stored.type])
      .map((x) => `<option value="${esc(x)}"${x === stored.type ? ' selected' : ''}>${esc(x)}</option>`).join('');
    const fwd = Number(stored.direction || 1) >= 0;
    return `<div class="sv2-mut-silent" data-fe-at="${stored.at}">
        <h4>${esc(t('Edit this feature'))}</h4>
        <label class="sv2-field"><span>${esc(t('Name'))}</span><input type="text" data-fe-name value="${esc(stored.name)}" autocomplete="off"></label>
        <label class="sv2-field"><span>${esc(t('Type'))}</span><select data-fe-type>${types}</select></label>
        <div class="seg sv2-mut-modes"><button type="button" class="seg-item${fwd ? ' is-active' : ''}" data-fe-dir="1">→ ${esc(t('Forward'))}</button>
          <button type="button" class="seg-item${fwd ? '' : ' is-active'}" data-fe-dir="-1">← ${esc(t('Reverse'))}</button></div>
        <div class="pver-actions"><button type="button" class="btn btn-primary" data-fe-save>${esc(t('Save'))}</button>
          <button type="button" class="btn" data-fe-delete>${icon('trash')} ${esc(t('Remove'))}</button></div>
      </div>`;
  }

  function featurePanel(f) {
    const p = data.proteins.find((x) => x.feature === f.i);
    if (p) proteinPanel(p);
    const block = featureEditor(f);
    if (!block) return;
    if (p) { $('[data-side]').insertAdjacentHTML('beforeend', block); return; }
    panel = { kind: 'feature' };
    sidePanel(`<div class="sv2-panel-head"><h3><span class="pdig-dot pmap-f-${f.kind}"></span>${esc(f.name || f.type)}</h3>
      <span class="sv2-mut-where tabular-nums">${num(f.a)}–${num(f.b)}</span></div>${block}`);
  }

  root.addEventListener('click', (e) => {
    if (e.target.closest('[data-pop-edit]') && sel) { editPanel('replace'); return; }
    if (e.target.closest('[data-undo]')) { undo(); return; }
    const mode = e.target.closest('[data-ed-mode]');
    if (mode && ed) { const was = sel; sel = { start: ed.start, end: ed.end }; editPanel(mode.dataset.edMode); sel = was; return; }
    if (e.target.closest('[data-ed-apply]')) { applyEdit(); return; }
    if (e.target.closest('[data-ed-add]')) { addFeature(); return; }
    const fdir = e.target.closest('[data-ed-fdir], [data-fe-dir]');
    if (fdir) { fdir.parentElement.querySelectorAll('.seg-item').forEach((b) => b.classList.toggle('is-active', b === fdir)); return; }
    const box = e.target.closest('[data-fe-at]');
    if (box && e.target.closest('[data-fe-save]')) {
      const dir = box.querySelector('[data-fe-dir].is-active');
      send({ op: 'edit_feature', at: Number(box.dataset.feAt), name: box.querySelector('[data-fe-name]').value,
        type: box.querySelector('[data-fe-type]').value, direction: dir ? Number(dir.dataset.feDir) : 1 });
      return;
    }
    if (box && e.target.closest('[data-fe-delete]')) {
      send({ op: 'delete_feature', at: Number(box.dataset.feAt) }, () => sidePanel(''));
    }
  });
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.matches('[data-ed-bases]')) { e.preventDefault(); applyEdit(); }
    if (e.key === 'Enter' && e.target.matches('[data-ed-fname]')) { e.preventDefault(); addFeature(); }
  });

  // Keys on the picked bases: the rows themselves can't take focus, so these
  // listen on the page while the Sequence tab is the one showing.
  document.addEventListener('keydown', (e) => {
    if (!editable()) return;
    const tab = root.closest('.plasmid-tab-panel');
    if (tab && !tab.classList.contains('is-active')) return;
    const typing = e.target.closest && e.target.closest('input, textarea, select, [contenteditable="true"]');
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'z' && !typing) { e.preventDefault(); undo(); return; }
    if (typing || e.metaKey || e.ctrlKey || e.altKey || !sel) return;
    if (e.key === 'Backspace' || e.key === 'Delete') {
      e.preventDefault();
      const at = sel.start;
      send({ op: 'bases', start: sel.start, end: sel.end, bases: '' }, () => reselect(at, at));
      return;
    }
    if (IUPAC.test(e.key)) { e.preventDefault(); editPanel('replace', e.key.toUpperCase()); }
  });

  // The tools under the viewer: the topology, and full screen.
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-topology]') && data) { send({ op: 'topology', circular: !data.circular }); return; }
    if (e.target.closest('[data-sv-fullscreen]')) {
      if (document.fullscreenElement) document.exitFullscreen();
      else root.requestFullscreen().catch(() => {});
    }
  });
  const fsButton = document.querySelector('[data-sv-fullscreen]');
  if (fsButton && root.requestFullscreen) {
    fsButton.hidden = false;
    document.addEventListener('fullscreenchange', () => {
      fsButton.querySelector('span').textContent = document.fullscreenElement === root ? t('Exit full screen') : t('Full screen');
    });
  }

  // ---------------------------------------------------------------- start

  function load() {
    return fetch(root.dataset.url, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json()).then((j) => {
      if (!j.ok) { $('[data-rowlist]').innerHTML = `<p class="sv2-note">${esc(j.error || '')}</p>`; return; }
      data = j;
      drawBar();
      layout();
      drawAll();
      editState();
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
  // A page without tabs (a virus's or a stock's plasmid) shows it at once.
  if (!tabPanel || tabPanel.classList.contains('is-active')) opened();
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
