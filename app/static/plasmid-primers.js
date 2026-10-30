/* The plasmid page's Primers tab (templates/plasmid_detail.html): every
   primer in the lab's Primers databases that binds this plasmid, on a
   strip of the plasmid and in a list; two of them picked make a PCR,
   whose product can go on the Digest tab's gel beside the digests.

   The primers and features come from /plasmids/<id>/view.json, the PCR
   from /plasmids/<id>/pcr.json (app/sequence_routes.py). */
(function () {
  'use strict';

  const root = document.querySelector('[data-primers-tab]');
  if (!root) return;
  const t = window.t || ((s) => s);
  const num = (n) => Number(n || 0).toLocaleString('en-US');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const $ = (sel) => root.querySelector(sel);
  const say = (message, kind) => { if (message && window.BiomanagerShell) window.BiomanagerShell.toast(message, kind); };

  const state = { data: null, loose: false, f: null, r: null, product: null };

  function shown() {
    return state.data.primers.filter((p) => state.loose || !p.mismatches);
  }

  // The plasmid as a line: its features, the primers as arrows above, the product below.
  function strip() {
    const d = state.data;
    const L = d.length;
    const W = 960;
    const x = (bp) => 14 + (bp / L) * (W - 28);
    const list = shown();
    const lanes = [];
    const placed = list.map((p) => {
      const left = x(Math.min(p.start, p.end));
      let lane = lanes.findIndex((end) => end < left - 4);
      if (lane < 0) { lanes.push(0); lane = lanes.length - 1; }
      lanes[lane] = x(Math.max(p.start, p.end)) + 8 + String(p.name).length * 6.4;
      return { p, lane };
    });
    const top = 8 + lanes.length * 16;
    const H = top + 70;
    const out = [`<svg class="ppr-strip" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(t('Where the lab’s primers bind'))}">`,
      `<line x1="14" y1="${top + 10}" x2="${W - 14}" y2="${top + 10}" class="pmap-backbone"/>`];
    d.features.forEach((f) => {
      const s = Number(f.start);
      const e = Number(f.end) + 1;
      const kind = window.BioPlasmidMap.kind(f);
      const parts = e > s ? [[s, e]] : [[s, L], [0, e]];
      parts.forEach(([a, b]) => out.push(`<rect x="${x(a).toFixed(1)}" y="${top + 4}" width="${Math.max(2, x(b) - x(a)).toFixed(1)}" height="12" rx="3" class="pmap-f pmap-f-${kind}"/>`));
      if (e - s > L * 0.07) out.push(`<text x="${((x(s) + x(e)) / 2).toFixed(1)}" y="${top + 31}" text-anchor="middle" class="ppr-feat">${esc(f.name)}</text>`);
    });
    placed.forEach(({ p, lane }) => {
      const y = 12 + lane * 16;
      const a = x(p.start - 1);
      const b = x(p.end);
      const fw = p.direction > 0;
      const tone = p.id === state.f || p.id === state.r ? 'is-picked' : p.mismatches ? 'is-near' : '';
      const d2 = fw ? `M${a.toFixed(1)},${y - 4} H${(b - 6).toFixed(1)} L${b.toFixed(1)},${y} L${(b - 6).toFixed(1)},${y + 4} H${a.toFixed(1)} Z`
        : `M${b.toFixed(1)},${y - 4} H${(a + 6).toFixed(1)} L${a.toFixed(1)},${y} L${(a + 6).toFixed(1)},${y + 4} H${b.toFixed(1)} Z`;
      out.push(`<path d="${d2}" class="ppr-arrow ${tone}"/><text x="${(Math.max(a, b) + 5).toFixed(1)}" y="${y + 4}" class="ppr-name ${tone}">${esc(p.name)}</text>`);
      out.push(`<line x1="${(fw ? a : b).toFixed(1)}" y1="${y + 4}" x2="${(fw ? a : b).toFixed(1)}" y2="${top + 4}" class="ppr-drop ${tone}"/>`);
    });
    if (state.product) {
      const a = x(state.product.start - 1);
      const b = x(state.product.end);
      const parts = b >= a ? [[a, b]] : [[a, x(L)], [x(0), b]];
      parts.forEach(([p, q]) => out.push(`<rect x="${p.toFixed(1)}" y="${top + 38}" width="${Math.max(2, q - p).toFixed(1)}" height="8" rx="4" class="ppr-product"/>`));
      out.push(`<text x="${(parts[parts.length - 1][1] + 6).toFixed(1)}" y="${top + 46}" class="ppr-product-text">${num(state.product.length)} bp</text>`);
    }
    for (let k = 0; k <= L; k += L > 12000 ? 2000 : 1000) {
      out.push(`<text x="${x(k).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="pmap-tick-text">${num(k)}</text>`);
    }
    out.push('</svg>');
    return out.join('');
  }

  function table() {
    const list = shown();
    if (!list.length) {
      return `<p class="pver-note">${esc(state.loose ? t('None of the lab’s primers binds here, even with two mismatches.') : t('None of the lab’s primers binds here exactly. Try “≤ 2 mismatches”.'))}</p>`;
    }
    return `<table class="pdig-table ppr-table"><thead><tr><th>${esc(t('PCR'))}</th><th>${esc(t('Primer'))}</th><th></th>
      <th>${esc(t('Binds'))}</th><th>${esc(t('Match'))}</th><th class="num">Tm</th><th>${esc(t('Where it is'))}</th></tr></thead><tbody>
      ${list.map((p) => {
        const role = p.direction > 0 ? 'f' : 'r';
        const on = state[role] === p.id;
        const match = p.mismatches ? `<span class="badge badge-warn">${esc(t('%(n)s mismatches · 3′ end pairs', { n: p.mismatches }))}</span>`
          : p.tail ? `<span class="badge badge-ok">${esc(t('exact, %(n)s-base tail', { n: p.tail }))}</span>` : `<span class="badge badge-ok">${esc(t('exact'))}</span>`;
        return `<tr class="${on ? 'is-picked' : ''}"><td><button type="button" class="ppr-pick${on ? ' is-on' : ''}" data-pick="${p.id}" data-role="${role}"
          aria-label="${esc(on ? t('Not in the PCR') : t('Use in the PCR'))}">${role.toUpperCase()}</button></td>
          <td><a href="${esc(p.url)}">${esc(p.name)}</a></td><td class="text-muted">${p.direction > 0 ? '→' : '←'}</td>
          <td class="tabular-nums">${num(p.start)}–${num(p.end)}</td><td>${match}</td>
          <td class="num tabular-nums">${p.tm ? `${esc(p.tm)} °C` : '—'}</td><td class="text-muted">${esc(p.where)}</td></tr>`;
      }).join('')}</tbody></table>`;
  }

  function draw() {
    $('[data-strip]').innerHTML = strip();
    $('[data-list]').innerHTML = table();
    root.querySelectorAll('[data-loose]').forEach((b) => b.classList.toggle('is-active', (b.dataset.loose === '1') === state.loose));
    const next = state.data.next;
    const box = $('[data-to-sequence]');
    box.hidden = !next;
    if (next) {
      box.innerHTML = `<span class="badge badge-brand">${esc(t('To sequence it'))}</span><span>${esc(next.text)}</span>`;
    }
    drawPcr();
  }

  function drawPcr() {
    const byId = (id) => state.data.primers.find((p) => p.id === id);
    const f = byId(state.f);
    const r = byId(state.r);
    const box = $('[data-pcr]');
    const pickLine = (p, role) => `<div class="ppr-line"><span class="ppr-pick is-on">${role}</span>${p
      ? `<b>${esc(p.name)}</b> <span class="text-muted tabular-nums">${role === 'F' ? `${num(p.start)} →` : `← ${num(p.end)}`}</span>`
      : `<span class="text-muted">${esc(role === 'F' ? t('Pick a forward primer in the list') : t('Pick a reverse primer in the list'))}</span>`}</div>`;
    const p = state.product;
    box.innerHTML = `<div class="pver-head"><h3>${esc(t('PCR'))}</h3></div>
      <div class="ppr-picked">${pickLine(f, 'F')}${pickLine(r, 'R')}</div>
      ${p && p.ok ? `<dl class="pver-dl">
          <dt>${esc(t('Product'))}</dt><dd class="tabular-nums">${num(p.length)} bp</dd>
          <dt>${esc(t('Spans'))}</dt><dd class="tabular-nums">${num(p.start)}–${num(p.end)}</dd>
          ${p.holds.length ? `<dt>${esc(t('Holds'))}</dt><dd>${esc(p.holds.join(', '))}</dd>` : ''}
          ${p.empty ? `<dt>${esc(t('From %(name)s', { name: p.empty.name }))}</dt><dd class="tabular-nums">${p.empty.length ? `${num(p.empty.length)} bp` : esc(t('nothing'))}</dd>` : ''}
          ${p.anneal ? `<dt>${esc(t('Anneal'))}</dt><dd class="tabular-nums">${p.anneal} °C</dd>` : ''}
          <dt>${esc(t('Extend'))}</dt><dd class="tabular-nums">${esc(t('%(s)s s', { s: p.extend_s }))}</dd>
        </dl>
        <div class="pver-actions">
          <button type="button" class="btn btn-primary" data-to-gel>${icon('scissors')} ${esc(t('Put it on the gel'))}</button>
          <button type="button" class="btn" data-copy-product>${icon('copy')} ${esc(t('Copy the product as FASTA'))}</button>
        </div>` : p && !p.ok ? `<p class="pver-note">${esc(p.error)}</p>` : ''}`;
  }

  function runPcr() {
    state.product = null;
    if (!state.f || !state.r) { draw(); return; }
    const url = new URL(root.dataset.pcr, window.location.href);
    url.searchParams.set('f', state.f);
    url.searchParams.set('r', state.r);
    fetch(url, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json()).then((j) => {
      state.product = j;
      draw();
    });
  }

  root.addEventListener('click', (e) => {
    const loose = e.target.closest('[data-loose]');
    if (loose) { state.loose = loose.dataset.loose === '1'; draw(); return; }
    const pick = e.target.closest('[data-pick]');
    if (pick) {
      const id = Number(pick.dataset.pick);
      const role = pick.dataset.role;
      state[role] = state[role] === id ? null : id;
      runPcr();
      return;
    }
    if (e.target.closest('[data-to-gel]') && state.product) {
      const byId = (id) => state.data.primers.find((p) => p.id === id);
      const label = `${byId(state.f).name} + ${byId(state.r).name}`.slice(0, 40);
      if (window.BioDigest) window.BioDigest.addLane(`pcr:${state.product.length}:${label}`);
      return;
    }
    if (e.target.closest('[data-copy-product]') && state.product) {
      const fasta = `>${root.dataset.name} PCR ${state.product.start}-${state.product.end}\n${state.product.sequence.match(/.{1,70}/g).join('\n')}\n`;
      const done = () => say(t('The product is copied as FASTA.'));
      if (navigator.clipboard) navigator.clipboard.writeText(fasta).then(done, () => window.BioDialog.prompt(t('Copy the text below.'), fasta, { okLabel: t('Done') }));
    }
  });

  function use(data) {
    state.data = data;
    draw();
  }
  function load() {
    if (state.data) return;
    fetch(root.dataset.url, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json()).then((j) => {
      if (j.ok) use(j);
      else $('[data-list]').innerHTML = `<p class="pver-note">${esc(j.error || '')}</p>`;
    });
  }
  // The Sequence tab may have asked already.
  window.addEventListener('biomanager:plasmid-view', (e) => { if (!state.data) use(e.detail); });
  const panel = root.closest('.plasmid-tab-panel');
  if (panel && panel.classList.contains('is-active')) load();
  document.querySelectorAll('.plasmid-tab[data-tab="primers"]').forEach((tab) => tab.addEventListener('click', load));
})();
