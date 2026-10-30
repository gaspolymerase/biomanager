/* The plasmid page's Verify tab (templates/plasmid_detail.html): the clones
   checked by sequencing, down the left; the one open in the middle, where
   its reads fall on the map and each difference with its trace; on the
   right what the clone comes to, and Accept.

   The routes are app/clone_routes.py; the biology is app/seqverify.py. The
   map strip and small traces are static/seq-verify.js's (BioSeqVerify); the
   wide trace at a difference is drawn here. */
(function () {
  'use strict';

  const root = document.querySelector('[data-verify-tab]');
  if (!root) return;
  const t = window.t || ((s) => s);
  const urls = JSON.parse(root.dataset.urls);
  const idUrl = (name, id) => urls[name].replace('/0', `/${id}`);
  const num = (n) => Number(n || 0).toLocaleString('en-US');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const $ = (sel) => root.querySelector(sel);
  const say = (message, kind) => { if (message && window.BiomanagerShell) window.BiomanagerShell.toast(message, kind); };

  const state = { clones: [], open: null, report: null, summary: null, diff: 0, canEdit: false, loaded: false };

  function get(url) {
    return fetch(url, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json());
  }
  function post(url, body) {
    return fetch(url, { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } })
      .then((r) => r.json().catch(() => ({ ok: false, error: t('The server answered %(status)s.', { status: r.status }) })))
      .catch(() => ({ ok: false, error: t('The server did not answer. Check the connection and try again.') }));
  }

  // ---------------------------------------------------------------- the clones

  const TONE = { match: 'ok', differences: 'warn', poor: 'bad', no_match: 'bad' };
  function drawClones() {
    $('[data-clone-count]').textContent = state.clones.length || '';
    $('[data-clone-list]').innerHTML = state.clones.map((c) => {
      const tone = c.status === 'differences' && c.summary && c.summary.changes && !c.summary.coding ? 'warn' : TONE[c.status] || 'bad';
      const mark = c.accepted ? icon('check') : tone === 'ok' ? icon('check') : tone === 'warn' ? '!' : '×';
      return `<li><button type="button" class="pver-clone${state.open === c.id ? ' is-open' : ''}" data-clone="${c.id}">
        <span class="pver-mark is-${c.accepted ? 'accepted' : tone}">${mark}</span>
        <span class="pver-clone-name">${esc(c.label)}</span>
        <span class="pver-when">${esc(c.accepted ? t('Verified %(when)s', { when: c.when }) : c.when)}</span>
        <span class="pver-detail">${esc(c.detail)}</span></button></li>`;
    }).join('');
  }

  function drawNext(next) {
    const box = $('[data-next]');
    box.hidden = !next;
    if (!next) return;
    box.innerHTML = `<b>${esc(t('To sequence the next clone'))}</b><p>${esc(next.text)}</p>
      <div>${next.primers.map((p) => `<a href="${esc(p.url)}">${esc(p.name)}</a> <span class="text-muted">${esc(p.where)}</span>`).join('<br>')}</div>`;
  }

  function load(openId) {
    return get(urls.list).then((j) => {
      if (!j.ok) return;
      state.clones = j.clones;
      state.canEdit = j.can_edit;
      state.loaded = true;
      drawClones();
      drawNext(j.next);
      pill(j.verified);
      const pick = openId || state.open || (state.clones[0] && state.clones[0].id);
      if (pick) open(pick);
      else showEmpty(true);
    });
  }

  function open(id) {
    state.open = id;
    drawClones();
    return get(idUrl('one', id)).then((j) => {
      if (!j.ok) return;
      show(j.report, j.summary, j.clone);
    });
  }

  // The header's "Verified · Clone 3 · Oct 12".
  function pill(verified) {
    const meta = document.querySelector('.plasmid-meta');
    let el = document.querySelector('[data-verified-pill]');
    if (!verified) { if (el) el.remove(); return; }
    if (!el && meta) {
      el = document.createElement('span');
      el.className = 'plasmid-meta-item plasmid-meta-verified';
      el.setAttribute('data-verified-pill', '');
      meta.appendChild(el);
    }
    if (el) el.innerHTML = `${icon('check')} ${esc(t('Verified · %(clone)s · %(when)s', { clone: verified.label, when: verified.when }))}`;
  }

  // ---------------------------------------------------------------- one clone

  function showEmpty(on) {
    $('[data-empty]').hidden = !on;
    if (on) {
      $('[data-diff-card]').hidden = true;
      $('[data-reads-card]').hidden = true;
      $('[data-summary]').hidden = true;
      $('[data-strip]').innerHTML = '';
    }
  }

  function show(report, summary, clone) {
    state.report = report;
    state.summary = summary;
    state.diff = 0;
    showEmpty(false);
    const strip = $('[data-strip]');
    strip.innerHTML = '';
    if (report.reads && report.reads.some((r) => r.placed) && window.BioSeqVerify) {
      strip.appendChild(window.BioSeqVerify.drawMap(report, strip.clientWidth || 640));
    } else {
      strip.innerHTML = `<p class="pver-note">${esc(report.verdict || '')}</p>`;
    }
    drawDiff();
    drawReads();
    drawSummary(clone);
  }

  function effectText(d) {
    const e = d.effect;
    if (!e) return d.features && d.features.length ? t('In %(features)s, outside a coding sequence', { features: d.features.join(', ') }) : t('Outside every feature');
    if (e.codon_from) {
      const kind = { silent: t('silent'), missense: t('changes the protein'), nonsense: t('a stop codon'),
        stop_lost: t('the stop is lost'), start_lost: t('the start is lost') }[e.kind] || '';
      return t('In %(feature)s, codon %(codon)s: %(from)s → %(to)s, %(aa_from)s → %(aa_to)s (%(kind)s)',
        { feature: e.feature, codon: e.codon, from: e.codon_from, to: e.codon_to, aa_from: e.from, aa_to: e.to, kind });
    }
    return `${e.feature}: ${e.text}`;
  }

  // The trace at a difference, wide: the four dyes, the read's bases under
  // it, the one that differs shaded, and the map's base beside the read's.
  const DYES = { A: '#16a34a', C: '#2563eb', G: 'var(--color-ink)', T: '#dc2626' };
  function wideTrace(d, width) {
    const p = d.trace;
    const W = Math.max(320, width);
    const H = 150;
    const LEFT = 44;                       // room for the rows' names
    const n = p.channels.A.length;
    const top = Math.max(1, ...['A', 'C', 'G', 'T'].flatMap((b) => p.channels[b]));
    const x = (i) => LEFT + (i / Math.max(n - 1, 1)) * (W - LEFT - 6);
    const y = (v) => 6 + (1 - Math.max(v, 0) / top) * (H - 50);
    const out = [`<svg class="pver-trace" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(t('Trace around this base'))}">`];
    p.bases.filter((b) => b.at).forEach((b) => out.push(`<rect x="${(x(b.x) - 9).toFixed(1)}" y="0" width="18" height="${H}" class="pver-at"/>`));
    ['A', 'C', 'G', 'T'].forEach((b) => {
      const points = p.channels[b].map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
      out.push(`<polyline points="${points}" fill="none" stroke-width="1.4" stroke-linejoin="round" style="stroke:${DYES[b]}"/>`);
    });
    p.bases.forEach((b) => {
      out.push(`<text x="${x(b.x).toFixed(1)}" y="${H - 26}" text-anchor="middle" class="pver-base${b.at ? ' is-at' : ''}" style="fill:${DYES[b.base] || 'currentColor'}">${esc(b.base)}</text>`);
      if (b.at && d.kind === 'mismatch') {
        out.push(`<text x="${x(b.x).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="pver-base is-map">${esc(p.strand === 'reverse' ? complement(d.plasmid) : d.plasmid)}</text>`);
      }
    });
    out.push(`<text x="0" y="${H - 26}" class="pver-row-label">${esc(p.strand === 'reverse' ? t('read ←') : t('read →'))}</text>`);
    if (d.kind === 'mismatch') out.push(`<text x="0" y="${H - 8}" class="pver-row-label">${esc(t('map'))}</text>`);
    out.push('</svg>');
    return out.join('');
  }
  const complement = (b) => ({ A: 'T', C: 'G', G: 'C', T: 'A' }[b] || b);

  function drawDiff() {
    const card = $('[data-diff-card]');
    const diffs = (state.report && state.report.differences) || [];
    card.hidden = !diffs.length;
    if (!diffs.length) return;
    state.diff = Math.max(0, Math.min(state.diff, diffs.length - 1));
    const d = diffs[state.diff];
    const V = window.BioSeqVerify;
    $('[data-diff-title]').textContent = diffs.length > 1
      ? t('At %(where)s · %(n)s of %(count)s', { where: V.where(d), n: state.diff + 1, count: diffs.length })
      : t('At %(where)s', { where: V.where(d) });
    card.querySelectorAll('[data-diff-step]').forEach((b) => {
      const to = state.diff + Number(b.dataset.diffStep);
      b.disabled = to < 0 || to >= diffs.length;
    });
    const box = $('[data-diff]');
    box.innerHTML = `${d.trace ? wideTrace(d, box.clientWidth || 640) : `<p class="pver-note">${esc(t('The read was text, so there is no trace to show.'))}</p>`}
      <div class="pver-diff-line">
        <span class="badge ${d.confident ? 'badge-warn' : 'badge-quiet'}">${esc(V.changeText(d))}</span>
        <span class="tabular-nums">${d.quality == null ? '' : esc(t('Q %(q)s', { q: d.quality }))}${d.quality == null ? '' : ' · '}${esc(t('%(seen)s of %(covering)s reads', { seen: d.reads.length, covering: d.reads_covering }))}</span>
        <span class="pver-effect">${esc(effectText(d))}</span>
      </div>
      ${d.confident ? '' : `<p class="pver-note">${esc(t('Low quality here: probably the read, not the clone.'))}</p>`}`;
  }

  function drawReads() {
    const card = $('[data-reads-card]');
    const reads = (state.report && state.report.reads) || [];
    card.hidden = !reads.length;
    const V = window.BioSeqVerify;
    $('[data-reads]').innerHTML = `<table class="pdig-table"><thead><tr><th>${esc(t('Read'))}</th><th>${esc(t('Strand'))}</th>
      <th>${esc(t('Covers'))}</th><th class="num">${esc(t('Identity (%)'))}</th><th>${esc(t('Verdict'))}</th></tr></thead><tbody>
      ${reads.map((r) => `<tr><td>${esc(r.name)}</td><td>${r.placed ? esc(r.strand === 'reverse' ? t('reverse') : t('forward')) : '—'}</td>
        <td class="tabular-nums">${r.placed ? `${num(r.start)}–${num(r.end)}` : '—'}</td><td class="num tabular-nums">${r.identity != null ? r.identity : '—'}</td>
        <td>${V ? V.badge(r.status) : ''} ${esc(r.verdict)}</td></tr>`).join('')}</tbody></table>`;
  }

  function drawSummary(clone) {
    const box = $('[data-summary]');
    const s = state.summary || {};
    box.hidden = false;
    const features = (s.features || []).map((f) => t('%(feature)s, %(n)s×', { feature: f.feature || f.name, n: f.reads })).join(' · ');
    const changes = !s.changes ? t('None') : s.coding ? t('%(n)s, %(coding)s in a protein', { n: s.changes, coding: s.coding })
      : t('%(n)s, silent', { n: s.changes });
    const tone = clone && clone.accepted ? 'ok' : TONE[state.report.status] || 'bad';
    box.innerHTML = `<div class="pver-sum-head"><span class="badge badge-${tone === 'bad' ? 'danger' : tone}">${esc(clone && clone.accepted ? t('Accepted') : clone ? clone.detail : '')}</span></div>
      <h2>${esc(clone ? clone.label : t('These reads'))}</h2>
      <p class="pver-verdict">${esc(state.report.verdict)}</p>
      <dl class="pver-dl">
        <dt>${esc(t('Identity to the map'))}</dt><dd class="tabular-nums">${s.identity != null ? `${s.identity}%` : '—'}</dd>
        <dt>${esc(t('Covered'))}</dt><dd class="tabular-nums">${num(s.covered_bp)} / ${num(s.length)} bp</dd>
        ${features ? `<dt>${esc(t('Read through'))}</dt><dd>${esc(features)}</dd>` : ''}
        <dt>${esc(t('Reads'))}</dt><dd class="tabular-nums">${esc(t('%(placed)s of %(reads)s placed', { placed: s.placed || 0, reads: s.reads || 0 }))}</dd>
        <dt>${esc(t('Changes from the map'))}</dt><dd>${esc(changes)}</dd>
      </dl>
      ${clone && state.canEdit ? `<div class="pver-actions">
        ${clone.accepted
          ? `<button type="button" class="btn" data-accept="${clone.id}" data-undo="1">${esc(t('No longer the accepted clone'))}</button>`
          : `<button type="button" class="btn btn-primary" data-accept="${clone.id}">${icon('check')} ${esc(t('Accept %(clone)s', { clone: clone.label }))}</button>`}
        <button type="button" class="btn btn-ghost" data-remove="${clone.id}">${icon('trash')} ${esc(t('Remove'))}</button>
      </div>` : ''}`;
  }

  // ---------------------------------------------------------------- checking reads

  const form = $('[data-clone-form]');
  const status = $('[data-clone-status]');
  function check(body) {
    status.textContent = t('Checking the reads…');
    return post(urls.check, body).then((j) => {
      if (!j.ok) { status.textContent = j.error || t('That could not be checked.'); return; }
      status.textContent = j.kept ? '' : t('Checked, not kept: only the plasmid’s owner can keep reads with it.');
      (j.problems || []).forEach((p) => say(p, 'error'));
      form.reset();
      if (j.made && j.made.length) {
        state.clones = j.clones;
        load(j.made[0]);
      } else {
        show(j.report, j.summary, null);
      }
    });
  }
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    check(new FormData(form));
  });
  form.querySelector('input[type=file]').addEventListener('change', () => check(new FormData(form)));
  const zone = $('[data-zone]');
  ['dragenter', 'dragover'].forEach((ev) => root.addEventListener(ev, (e) => {
    if (!e.dataTransfer || ![...e.dataTransfer.types].includes('Files')) return;
    e.preventDefault();
    zone.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach((ev) => root.addEventListener(ev, () => zone.classList.remove('is-over')));
  root.addEventListener('drop', (e) => {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    const body = new FormData(form);
    body.delete('reads');
    [...e.dataTransfer.files].forEach((f) => body.append('reads', f, f.name));
    check(body);
  });

  root.addEventListener('click', (e) => {
    const clone = e.target.closest('[data-clone]');
    if (clone) { open(Number(clone.dataset.clone)); return; }
    const step = e.target.closest('[data-diff-step]');
    if (step) { state.diff += Number(step.dataset.diffStep); drawDiff(); return; }
    const accept = e.target.closest('[data-accept]');
    if (accept) {
      const body = new FormData();
      if (accept.dataset.undo) body.append('undo', '1');
      post(idUrl('accept', accept.dataset.accept), body).then((j) => {
        if (!j.ok) { say(j.error, 'error'); return; }
        say(j.message);
        state.clones = j.clones;
        pill(j.verified);
        drawClones();
        drawSummary(state.clones.find((c) => c.id === state.open));
      });
      return;
    }
    const remove = e.target.closest('[data-remove]');
    if (remove) {
      const c = state.clones.find((x) => x.id === Number(remove.dataset.remove));
      window.BioDialog.confirm(t('Remove %(clone)s? Its reads stay in the plasmid’s files.', { clone: c ? c.label : '' }),
        { title: t('Remove a clone'), okLabel: t('Remove') }).then((yes) => {
        if (!yes) return;
        post(idUrl('remove', remove.dataset.remove), new FormData()).then((j) => {
          if (!j.ok) { say(j.error, 'error'); return; }
          say(j.message);
          state.open = null;
          pill(j.verified);
          load();
        });
      });
    }
  });

  // Load when the tab is first shown (or straight away if it is open).
  const panel = root.closest('.plasmid-tab-panel');
  const opened = () => { if (!state.loaded) load(); };
  if (panel && panel.classList.contains('is-active')) opened();
  document.querySelectorAll('.plasmid-tab[data-tab="verify"]').forEach((tab) => tab.addEventListener('click', opened));
})();
