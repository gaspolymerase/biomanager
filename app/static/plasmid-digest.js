/* The plasmid page's Digest tab (templates/plasmid_detail.html): tick
   enzymes, see where they cut on the map, the pieces they leave and the gel
   they would run as, beside the lab's own gel photo if one is put there.

   Everything biological comes from /plasmids/<id>/digest.json
   (app/digest_routes.py); this only asks and draws (static/plasmid-map.js).
   Lanes kept on the gel, the agarose and the ladder are remembered per
   plasmid in this browser. The Primers tab adds a PCR product as a lane
   with BioDigest.addLane('pcr:951:Clone 4'). */
(function () {
  'use strict';

  const root = document.querySelector('[data-digest]');
  if (!root) return;
  const t = window.t || ((s) => s);
  const num = (n) => Number(n || 0).toLocaleString('en-US');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const STORE = `biomanager:digest:${root.dataset.key}`;
  const MAX = 4;

  const state = { chosen: [], filter: 'unique', find: '', agarose: '1', ladder: '1kb_plus', methylated: true, lanes: [], picked: null };
  let firstVisit = true;
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || 'null');
    if (saved) firstVisit = false;
    ['agarose', 'ladder', 'lanes', 'methylated', 'chosen'].forEach((k) => { if (saved && saved[k] !== undefined) state[k] = saved[k]; });
  } catch (_e) { /* private window: start fresh */ }
  function remember() {
    try {
      localStorage.setItem(STORE, JSON.stringify({ agarose: state.agarose, ladder: state.ladder, lanes: state.lanes,
        methylated: state.methylated, chosen: state.chosen }));
    } catch (_e) { /* nothing kept, nothing lost */ }
  }

  const $ = (sel) => root.querySelector(sel);
  let data = null;
  let asked = 0;

  function load() {
    const url = new URL(root.dataset.url, window.location.href);
    url.searchParams.set('enzymes', state.chosen.join(','));
    url.searchParams.set('agarose', state.agarose);
    url.searchParams.set('ladder', state.ladder);
    url.searchParams.set('methylated', state.methylated ? '1' : '0');
    state.lanes.forEach((l) => url.searchParams.append('lane', l));
    const mine = ++asked;
    root.classList.add('is-loading');
    return fetch(url, { headers: { 'X-Requested-With': 'fetch' } })
      .then((r) => r.json())
      .then((answer) => {
        if (mine !== asked) return;
        root.classList.remove('is-loading');
        if (!answer.ok) { $('[data-map]').innerHTML = `<p class="pdig-empty">${esc(answer.error || t('That could not be worked out.'))}</p>`; return; }
        data = answer;
        state.chosen = answer.chosen;
        // The first time a plasmid is opened here, start on the suggested check.
        if (firstVisit && !state.chosen.length && answer.suggestions.length) {
          firstVisit = false;
          state.chosen = answer.suggestions[0].enzymes;
          remember();
          load();
          return;
        }
        firstVisit = false;
        draw();
      })
      .catch(() => {
        root.classList.remove('is-loading');
        if (window.BiomanagerShell) window.BiomanagerShell.toast(t('The server did not answer. Check the connection and try again.'), 'error');
      });
  }

  // ---------------------------------------------------------------- the enzymes

  function where(row) {
    if (!row.sites.length) return t("doesn't cut");
    const shown = row.sites.slice(0, 3).map((s) => num(s.cut)).join(' · ');
    return row.sites.length > 3 ? `${shown} · +${row.sites.length - 3}` : shown;
  }
  function listed() {
    const find = state.find.trim().toLowerCase();
    return data.enzymes.filter((row) => {
      if (find) return row.name.toLowerCase().includes(find) || row.site.toLowerCase().includes(find);
      if (state.chosen.includes(row.name)) return true;
      if (state.filter === 'unique') return row.cuts === 1;
      if (state.filter === 'two') return row.cuts === 2;
      if (state.filter === 'freezer') return !!row.freezer;
      return true;
    }).sort((a, b) => {
      const pa = a.sites.length ? a.sites[0].cut : Infinity;
      const pb = b.sites.length ? b.sites[0].cut : Infinity;
      return state.filter === 'all' || state.find ? a.name.localeCompare(b.name) : pa - pb;
    });
  }
  function drawEnzymes() {
    const rows = listed();
    const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
    $('[data-enzyme-list]').innerHTML = rows.length ? rows.map((row) => {
      const on = state.chosen.includes(row.name);
      const blocked = row.blocked.map((b) => `<span class="pdig-tag" title="${esc(t('Methylation stops it here'))}">${b === 'dam' ? 'Dam' : 'Dcm'}</span>`).join('');
      const freezer = row.freezer
        ? `<span class="pdig-freezer" title="${esc(t('In the freezer: %(item)s', { item: row.freezer.item }) + (row.freezer.where ? ` · ${row.freezer.where}` : ''))}">${icon('snowflake')}</span>` : '<span class="pdig-freezer"></span>';
      return `<li><label class="pdig-enzyme${on ? ' is-on' : ''}${row.sites.length ? '' : ' is-none'}">
        <input type="checkbox" value="${esc(row.name)}"${on ? ' checked' : ''}${!on && state.chosen.length >= MAX ? ' disabled' : ''}>
        <span class="pdig-name">${esc(row.name)}</span><span class="pdig-where">${where(row)}</span>${blocked}${freezer}</label></li>`;
    }).join('') : `<li class="pdig-empty">${esc(t('No enzyme here. Try All.'))}</li>`;
    root.querySelectorAll('[data-filter]').forEach((b) => b.classList.toggle('is-active', b.dataset.filter === state.filter));

    const chosen = $('[data-chosen]');
    const picked = data.enzymes.filter((r) => state.chosen.includes(r.name));
    chosen.hidden = !picked.length;
    if (picked.length) {
      const have = picked.filter((r) => r.freezer);
      const lines = have.map((r) => `<li><a href="${esc(r.freezer.url)}">${esc(r.freezer.item)}</a>${r.freezer.where ? `<span>${esc(r.freezer.where)}</span>` : ''}</li>`).join('');
      const missing = picked.filter((r) => !r.freezer).map((r) => r.name);
      chosen.innerHTML = `<div class="pdig-chosen-head"><b>${esc(picked.map((r) => r.name).join(' + '))}</b>
          <button type="button" class="btn btn-sm" data-clear>${esc(t('Clear'))}</button></div>
        ${lines ? `<ul>${lines}</ul>` : ''}
        ${missing.length ? `<p class="text-muted">${esc(t('Not in the lab’s reagents: %(names)s', { names: missing.join(', ') }))}</p>` : ''}`;
    }
  }

  // ---------------------------------------------------------------- the map, pieces and suggestions

  function drawMap() {
    $('[data-map]').innerHTML = window.BioPlasmidMap.circular({
      length: data.length, features: data.features, cuts: data.cuts, pieces: data.pieces,
      picked: state.picked, title: root.dataset.name, subtitle: `${num(data.length)} bp`, size: 440,
    });
  }
  function drawPieces() {
    const box = $('[data-pieces]');
    if (!data.pieces.length) {
      box.innerHTML = `<p class="pdig-empty">${esc(state.chosen.length ? t('These enzymes don’t cut it.') : t('Tick enzymes on the left, or use a suggested check.'))}</p>`;
      return;
    }
    box.innerHTML = `<table class="pdig-table"><thead><tr><th>${esc(t('Piece'))}</th><th class="num">bp</th><th>${esc(t('Holds'))}</th></tr></thead><tbody>${
      data.pieces.map((p, i) => `<tr data-piece="${i}" class="${state.picked === i ? 'is-picked' : ''}">
        <td><span class="pdig-dot pmap-piece-${i % 4}"></span>${p.left ? `${esc(p.left)} ` : ''}<span class="tabular-nums">${num(p.start)}</span> → ${p.right ? `${esc(p.right)} ` : ''}<span class="tabular-nums">${num(p.end)}</span></td>
        <td class="num tabular-nums">${num(p.length)}</td><td>${esc(p.holds.join(', '))}</td></tr>`).join('')}</tbody></table>`;
  }
  function drawSuggestions() {
    const box = $('[data-suggest]');
    const list = data.suggestions || [];
    if (!list.length) { box.innerHTML = ''; return; }
    const [first, ...rest] = list;
    const same = (s) => s.enzymes.join('+') === state.chosen.join('+');
    box.innerHTML = `<div class="pdig-check${same(first) ? ' is-on' : ''}">
        <div class="pdig-check-head"><span class="badge badge-brand">${esc(t('Suggested check'))}</span>
          ${first.freezer ? `<span class="text-muted text-[12px]">${esc(t('In the freezer'))}</span>` : ''}<span class="grow"></span>
          ${same(first) ? '' : `<button type="button" class="btn btn-sm" data-use="${esc(first.enzymes.join(','))}">${esc(t('Use these'))}</button>`}</div>
        <p>${esc(first.text)}</p></div>
      ${rest.map((s) => `<button type="button" class="pdig-alt" data-use="${esc(s.enzymes.join(','))}"><b>${esc(s.enzymes.join(' + '))}</b> <span class="tabular-nums">${s.sizes.map(num).join(' + ')} bp</span>${s.freezer ? ' · ' + esc(t('In the freezer')) : ''}</button>`).join('')}`;
  }

  // ---------------------------------------------------------------- the gel

  function drawGel() {
    const gel = data.gel;
    $('[data-gel]').innerHTML = window.BioPlasmidMap.gel({
      ladder: gel.ladder, lanes: gel.lanes, height: 540, label: t('Expected gel'),
      width: Math.max(300, 70 + (gel.lanes.length + 1) * 64),
    });
    const agarose = $('[data-agarose]');
    if (!agarose.options.length) data.agaroses.forEach((a) => agarose.add(new Option(t('%(pct)s agarose', { pct: `${a}%` }), a)));
    agarose.value = gel.agarose;
    const ladder = $('[data-ladder]');
    if (!ladder.options.length) data.ladders.forEach((l) => ladder.add(new Option(l.name, l.key)));
    ladder.value = gel.ladder.key;
    $('[data-methylated]').checked = data.methylated;

    const kept = gel.lanes.filter((l) => l.kept);
    const current = gel.lanes.find((l) => l.current);
    $('[data-lanes]').innerHTML = `${kept.map((l) => `<span class="pdig-lane-chip">${esc(l.label)} <button type="button" data-drop="${esc(l.key)}" aria-label="${esc(t('Take %(lane)s off the gel', { lane: l.label }))}">×</button></span>`).join('')}
      ${current && !state.lanes.includes(current.key) ? `<button type="button" class="btn btn-sm" data-keep="${esc(current.key)}">+ ${esc(t('Keep %(lane)s on the gel', { lane: current.label }))}</button>` : ''}`;
    $('[data-notes]').innerHTML = data.notes.map((n) => `<li><span class="badge badge-warn">${esc(n.label)}</span><span>${esc(n.text)}</span></li>`).join('');
  }

  function draw() {
    drawEnzymes();
    drawMap();
    drawPieces();
    drawSuggestions();
    drawGel();
  }

  // ---------------------------------------------------------------- what people do

  root.addEventListener('change', (e) => {
    if (e.target.matches('[data-enzyme-list] input')) {
      const name = e.target.value;
      state.chosen = e.target.checked ? [...state.chosen, name].slice(0, MAX) : state.chosen.filter((n) => n !== name);
      state.picked = null;
    } else if (e.target.matches('[data-agarose]')) state.agarose = e.target.value;
    else if (e.target.matches('[data-ladder]')) state.ladder = e.target.value;
    else if (e.target.matches('[data-methylated]')) state.methylated = e.target.checked;
    else if (e.target.matches('[data-photo-file]')) { showPhoto(e.target.files[0]); return; }
    else return;
    remember();
    load();
  });
  root.addEventListener('input', (e) => {
    if (!e.target.matches('[data-enzyme-find]')) return;
    state.find = e.target.value;
    if (data) drawEnzymes();
  });
  root.addEventListener('click', (e) => {
    const filter = e.target.closest('[data-filter]');
    if (filter) { state.filter = filter.dataset.filter; drawEnzymes(); return; }
    const use = e.target.closest('[data-use]');
    if (use) { state.chosen = use.dataset.use.split(','); state.picked = null; remember(); load(); return; }
    if (e.target.closest('[data-clear]')) { state.chosen = []; state.picked = null; remember(); load(); return; }
    const keep = e.target.closest('[data-keep]');
    if (keep) { addLane(keep.dataset.keep); return; }
    const drop = e.target.closest('[data-drop]');
    if (drop) { state.lanes = state.lanes.filter((l) => l !== drop.dataset.drop); remember(); load(); return; }
    const piece = e.target.closest('[data-piece]');
    if (piece) {
      const i = Number(piece.dataset.piece);
      state.picked = state.picked === i ? null : i;
      drawMap();
      drawPieces();
      return;
    }
    if (e.target.closest('[data-photo-remove]')) showPhoto(null);
  });

  // The lab's own gel photo beside the expected one: only in this window.
  let photoUrl = null;
  function showPhoto(file) {
    const fig = $('[data-photo]');
    if (photoUrl) URL.revokeObjectURL(photoUrl);
    photoUrl = file ? URL.createObjectURL(file) : null;
    fig.hidden = !photoUrl;
    fig.querySelector('img').src = photoUrl || '';
    root.querySelector('[data-photo-file]').value = '';
  }

  function addLane(spec) {
    if (!spec || state.lanes.includes(spec)) return;
    state.lanes = [...state.lanes, spec].slice(-6);
    remember();
    if (data) load();
  }

  // Load when the tab is first shown (or straight away if it is the one open).
  const panel = root.closest('.plasmid-tab-panel');
  function opened() { if (!data) load(); }
  if (panel && panel.classList.contains('is-active')) opened();
  document.querySelectorAll('.plasmid-tab[data-tab="digest"]').forEach((tab) => tab.addEventListener('click', opened));

  window.BioDigest = {
    addLane(spec) {
      addLane(spec);
      const tab = document.querySelector('.plasmid-tab[data-tab="digest"]');
      if (tab) tab.click();
    },
  };
})();
