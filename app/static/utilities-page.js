/* Utilities (templates/utilities.html): a search, the tools you pinned and
   opened last, the groups of tools, one tool open, and the reference
   tables. What the tools are, and their arithmetic, is static/bench-calcs.js
   (TOOLS, CALCS); this draws them. The open tool is in the address (#dilute;
   an old calculator's #dilution still opens it), and what was typed in each
   calculator, which tools are pinned and which were opened last are kept in
   this browser. */
(function () {
  'use strict';

  const B = window.BenchCalc;
  const root = document.querySelector('[data-util]');
  if (!B || !root) return;

  const KEY = 'biomanager:util:';
  const ICONS = root.dataset.icons;
  const canEditRotors = root.dataset.rotorsEditable === '1';   // anyone but a guest
  const me = root.dataset.me || '';
  const isAdmin = root.dataset.admin === '1';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const ico = (name) => `<svg class="icon" aria-hidden="true"><use href="${ICONS}#${name}"></use></svg>`;
  const get = (k, d) => { try { const v = JSON.parse(localStorage.getItem(KEY + k)); return v == null ? d : v; } catch (_) { return d; } };
  const put = (k, v) => { try { localStorage.setItem(KEY + k, JSON.stringify(v)); } catch (_) { /* storage off */ } };
  const forget = (k) => { try { localStorage.removeItem(KEY + k); } catch (_) { /* storage off */ } };
  const readJson = (id) => { try { return JSON.parse(document.getElementById(id).textContent || 'null'); } catch (_) { return null; } };
  const group = (id) => B.GROUPS.find((g) => g.id === id);
  const calcOf = (id) => B.CALCS.find((c) => c.id === id);
  const inGroup = (gid) => B.TOOLS.filter((x) => x.group === gid || (x.also || []).includes(gid));

  /* ------------------------------------------------------------ chemicals */

  // The lab's own chemicals come first in the picker, then the built-in
  // ones. A name as people type it: "MgCl2·6H2O", "mgcl2.6h2o", "MgCl₂ · 6
  // H₂O" and "magnesium chloride hexahydrate" are one chemical; "(anhydrous)"
  // and "(free acid)" are what the plain name means.
  const SUBSCRIPT = { '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4', '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9' };
  const WORDS = [
    [/\bmagnesium chloride\b/g, 'mgcl2'], [/\bmagnesium sulfate\b/g, 'mgso4'], [/\bcalcium chloride\b/g, 'cacl2'],
    [/\bsodium chloride\b/g, 'nacl'], [/\bpotassium chloride\b/g, 'kcl'], [/\bmanganese chloride\b/g, 'mncl2'],
    [/\bzinc chloride\b/g, 'zncl2'], [/\bsodium hydroxide\b/g, 'naoh'], [/\bpotassium hydroxide\b/g, 'koh'],
    [/\bmonohydrate\b/g, '.h2o'], [/\bdihydrate\b/g, '.2h2o'], [/\btrihydrate\b/g, '.3h2o'], [/\btetrahydrate\b/g, '.4h2o'],
    [/\bhexahydrate\b/g, '.6h2o'], [/\bheptahydrate\b/g, '.7h2o'],
  ];
  const normChem = (name) => {
    let x = String(name || '').toLowerCase().replace(/[₀-₉]/g, (d) => SUBSCRIPT[d]);
    x = x.replace(/\((anhydrous|free acid)\)/g, '');
    WORDS.forEach(([re, to]) => { x = x.replace(re, to); });
    return x.replace(/[·•*]/g, '.').replace(/\s+/g, '').replace(/\.h2o/g, '.1h2o').replace(/^\.+|\.+$/g, '');
  };
  const chemicals = new Map();
  const byKey = new Map();          // normalised name → the name listed
  const addChem = (name, mw) => {
    const key = normChem(name);
    if (!name || !(mw > 0) || byKey.has(key)) return;   // the lab's own first; no near-duplicates
    chemicals.set(name, mw);
    byKey.set(key, name);
  };
  (readJson('util-lab-chemicals') || []).forEach((c) => addChem(c.name, c.mw));
  B.CHEMICALS.forEach(([name, mw]) => addChem(name, mw));
  // A name it knows exactly, else every chemical it could be ("edta", "tris").
  const chemicalsFor = (typed) => {
    if (chemicals.has(typed)) return [typed];
    const k = normChem(typed);
    if (byKey.has(k)) return [byKey.get(k)];
    if (k.length < 3) return [];
    return [...byKey.entries()].filter(([key]) => key.startsWith(k) || key.replace(/\.\d*h2o$/, '') === k).map(([, name]) => name);
  };
  const datalist = document.getElementById('util-chemicals');
  chemicals.forEach((mw, name) => {
    const o = document.createElement('option');
    o.value = name;
    o.label = `${mw} g/mol`;
    datalist.appendChild(o);
  });
  let pendingChem = null;           // a chemical searched for, to fill in

  /* --------------------------------------------------------------- rotors */

  let rotors = readJson('util-rotors') || [];
  // The rotor a picker value names; '' (type the radius) is none, not the first.
  const rotorAt = (value) => (value === '' || value == null ? null : rotors[Number(value)] || null);

  /* ------------------------------------------------------ the lab's own */

  // Tools the lab made, listed with the rest; their maker or an admin may
  // change them, and anyone but a guest may make one.
  B.useLabTools(readJson('util-lab-tools') || []);
  const mayChange = (def) => canEditRotors && !!def && (def.author === me || isAdmin);

  /* ---------------------------------------------------------- the views */

  const view = root.querySelector('[data-util-view]');
  let pins = get('pins', ['dilute', 'make', 'rcf', 'mastermix']);
  const recentList = () => (get('recent', []) || []).filter((r) => r && B.locate(r.id));

  function home() {
    document.title = t('Utilities');
    const chip = (r) => {
      const found = B.locate(r.id || r);
      if (!found) return '';
      const x = found.tool;
      return `<a class="util-chip" href="#${x.id}">${pins.includes(x.id) ? ico('star') : ''}${esc(x.name)}</a>`;
    };
    const pinned = pins.map(chip).join('');
    const recent = recentList().slice(0, 6).map(chip).join('');
    view.innerHTML = `
      <label class="util-search">${ico('search')}
        <input type="search" data-util-search autocomplete="off" placeholder="${esc(t('Search: dilute, Tm, × g, MOI, a chemical…'))}" aria-label="${esc(t('Find a calculator'))}">
        <span class="kbd-chip">/</span></label>
      <div data-util-found></div>
      <div class="util-shortcuts" data-util-shortcuts>
        <div class="util-shortrow"><span>${esc(t('Pinned'))}</span>${pinned || `<em>${esc(t('Pin a tool to keep it here.'))}</em>`}</div>
        <div class="util-shortrow"><span>${esc(t('Recent'))}</span>${recent || `<em>${esc(t('Tools you open show here.'))}</em>`}</div>
      </div>
      <div class="util-groups">
        ${B.GROUPS.map((g) => `<section class="card util-group" aria-labelledby="util-g-${g.id}">
          <header><span class="util-gicon">${ico(g.icon)}</span><div><h2 id="util-g-${g.id}">${esc(g.name)}</h2><p>${esc(g.does)}</p></div></header>
          <ul>${inGroup(g.id).map((x) => `<li><a href="#${x.id}" title="${esc(x.does)}"><span>${esc(x.name)}</span>${x.group !== g.id ? `<small>${esc(group(x.group).name)}</small>` : ''}</a></li>`).join('')}</ul>
          ${g.id === 'lab' ? `${inGroup('lab').length ? '' : `<p class="util-hint">${esc(t('Your own formulas, kept for everyone in the lab.'))}</p>`}
            ${canEditRotors ? `<a class="util-new" href="#lab-new">${ico('plus')}${esc(t('Make a tool'))}</a>` : ''}` : ''}
        </section>`).join('')}
        <section class="card util-group" aria-labelledby="util-g-ref">
          <header><span class="util-gicon">${ico('table')}</span><div><h2 id="util-g-ref">${esc(t('Reference'))}</h2><p>${esc(t('Tables to look things up'))}</p></div></header>
          <ul>${[...B.REFERENCES, { id: 'ref-chemicals', title: t('Molecular weights') }].map((r) => `<li><a href="#${r.id}"><span>${esc(r.title)}</span></a></li>`).join('')}</ul>
        </section>
      </div>`;
    const q = view.querySelector('[data-util-search]');
    const found = view.querySelector('[data-util-found]');
    const shortcuts = view.querySelector('[data-util-shortcuts]');
    let hits = [];
    const go = (hit) => { if (hit.chem) pendingChem = hit.chem; location.hash = hit.href; };
    q.addEventListener('input', () => {
      const text = q.value.trim();
      shortcuts.hidden = !!text;
      if (!text) { found.innerHTML = ''; hits = []; return; }
      hits = B.search(text).slice(0, 7).map((x) => ({ name: x.name, where: x.group === 'reference' ? t('Reference') : group(x.group).name, does: x.does, href: x.id }));
      // A chemical's name opens Make a solution with it filled in.
      chemicalsFor(text).slice(0, 2).reverse().forEach((name) => hits.unshift({
        name: t('Make a solution: %(chemical)s', { chemical: name }), where: group('solutions').name, does: `${chemicals.get(name)} g/mol`, href: 'make', chem: name }));
      found.innerHTML = hits.length
        ? `<div class="card util-results">${hits.map((h, i) => `<a href="#${esc(h.href)}" data-i="${i}"${i ? '' : ' class="is-first"'}>
            <b>${esc(h.name)}</b><small>${esc(h.where)}</small>${h.does ? `<span>${esc(h.does)}</span>` : ''}</a>`).join('')}</div>`
        : `<p class="util-none">${esc(t('Nothing matches “%(text)s”. Browse the groups below.', { text }))}</p>`;
      found.querySelectorAll('[data-i]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); go(hits[Number(a.dataset.i)]); }));
    });
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter' && hits[0]) { e.preventDefault(); go(hits[0]); } });
  }

  function rail(current) {
    return `<nav class="util-rail" aria-label="${esc(t('Calculators'))}">
      <a class="util-back" href="#">${ico('chevron-left')}${esc(t('All tools'))}</a>
      ${B.GROUPS.map((g) => `<details${g.id === current.group ? ' open' : ''}><summary>${ico(g.icon)}${esc(g.name)}</summary>
        ${inGroup(g.id).map((x) => `<a href="#${x.id}"${x.id === current.id ? ' class="is-active" aria-current="page"' : ''}>${esc(x.name)}</a>`).join('')}</details>`).join('')}
      <details${current.group === 'reference' ? ' open' : ''}><summary>${ico('table')}${esc(t('Reference'))}</summary>
        ${[...B.REFERENCES, { id: 'ref-chemicals', title: t('Molecular weights') }].map((r) => `<a href="#${r.id}">${esc(r.title)}</a>`).join('')}</details>
    </nav>`;
  }

  /* --------------------------------------------------------- one tool */

  function unitSelect(inp, chosen) {
    const opts = B.UNITS[inp.units] || [];
    return `<select data-unit="${inp.key}" aria-label="${esc(t('%(label)s unit', { label: inp.label }))}">${opts.map(([u]) =>
      `<option${u === chosen ? ' selected' : ''}>${esc(u)}</option>`).join('')}</select>`;
  }

  function field(inp, raw) {
    const value = raw[inp.key] != null ? raw[inp.key] : (inp.value || '');
    const id = `util-f-${inp.key}`;
    const hint = inp.hint ? `<small class="util-hint">${esc(inp.hint)}</small>` : '';
    if (inp.type === 'select') {
      return `<label class="util-field" for="${id}"><span>${esc(inp.label)}</span><select id="${id}" data-key="${inp.key}">${inp.options.map(([val, label]) =>
        `<option value="${esc(val)}"${String(value || inp.options[0][0]) === String(val) ? ' selected' : ''}>${esc(label)}</option>`).join('')}</select>${hint}</label>`;
    }
    if (inp.type === 'textarea') {
      return `<label class="util-field is-wide" for="${id}"><span>${esc(inp.label)}</span><textarea id="${id}" data-key="${inp.key}" rows="5" spellcheck="false"
        placeholder="${esc(inp.placeholder || '')}">${esc(value)}</textarea>${hint}</label>`;
    }
    if (inp.type === 'chemical') {
      return `<label class="util-field is-wide" for="${id}"><span>${esc(inp.label)}</span><input id="${id}" data-key="${inp.key}" list="util-chemicals" autocomplete="off"
        placeholder="${esc(t('Type a name to fill the molecular weight'))}" value="${esc(value)}"><small class="util-hint" data-chem-note></small></label>`;
    }
    const wide = inp.type === 'text' ? ' is-wide' : '';
    const unit = inp.units ? unitSelect(inp, raw[`${inp.key}_unit`] || inp.unit) : '';
    return `<label class="util-field${wide}" for="${id}"><span>${esc(inp.label)}</span>
      <span class="util-input"><input id="${id}" data-key="${inp.key}" ${inp.type === 'text' ? 'spellcheck="false" class="font-mono"' : 'inputmode="decimal"'}
        autocomplete="off" value="${esc(value)}" placeholder="${esc(inp.placeholder || '')}">${unit}</span>${hint}</label>`;
  }

  function results(out) {
    let html = '';
    if (out.error) html += `<p class="util-error">${esc(out.error)}</p>`;
    const lines = out.lines || [];
    const mains = lines.filter((l) => l.main); const minors = lines.filter((l) => !l.main);
    if (mains.length) html += `<dl class="util-mains">${mains.map((l) => `<div><dt>${esc(l.label)}</dt><dd>${esc(l.value)}</dd></div>`).join('')}</dl>`;
    if (minors.length) html += `<dl class="util-minors">${minors.map((l) => `<div><dt>${esc(l.label)}</dt><dd>${esc(l.value)}</dd></div>`).join('')}</dl>`;
    if (out.table) {
      html += `<div class="util-table-wrap"><table class="util-table"><thead><tr>${out.table.head.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${out.table.rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    }
    (out.warnings || []).forEach((w) => { html += `<p class="util-warn">${esc(w)}</p>`; });
    (out.notes || []).forEach((w) => { html += `<p class="util-note">${esc(w)}</p>`; });
    if (out.hint) html += `<p class="util-muted">${esc(out.hint)}</p>`;
    return html || `<p class="util-muted">${esc(t('Fill in the fields to see the answer.'))}</p>`;
  }

  // The answer as text, to paste into a notebook or a sheet (tables by tabs).
  const asText = (out, title) => {
    const parts = (out.lines || []).map((l) => `${l.label}: ${l.value}`);
    if (out.table) parts.push(out.table.head.join('\t'), ...out.table.rows.map((r) => r.join('\t')));
    (out.warnings || []).forEach((w) => parts.push(w));
    return `${title}\n${parts.join('\n')}`;
  };

  function rotorPicker() {
    const opts = rotors.map((r, i) => `<option value="${i}">${esc(r.name)} · ${esc(B.fmt(r.radius))} cm${r.max ? ` · ${esc(t('max %(rpm)s rpm', { rpm: B.fmt(r.max) }))}` : ''}</option>`).join('');
    return `<div class="util-rotors">
      <label class="util-field"><span>${esc(t('Rotor'))}</span><select data-rotor><option value="">${esc(t('Type the radius'))}</option>${opts}</select></label>
      ${canEditRotors ? `<button type="button" class="btn btn-sm btn-ghost" data-rotors-edit>${ico('edit')}${esc(t('The lab’s rotors'))}</button>` : ''}
    </div><div data-rotors-editor hidden></div>`;
  }

  function rotorEditor(box, done) {
    const row = (r) => `<div class="util-rotor-row">
      <input data-r="name" value="${esc(r.name || '')}" placeholder="${esc(t('Centrifuge and rotor'))}" aria-label="${esc(t('Centrifuge and rotor'))}">
      <input data-r="radius" inputmode="decimal" value="${esc(r.radius || '')}" placeholder="${esc(t('Radius (cm)'))}" aria-label="${esc(t('Radius (cm)'))}">
      <input data-r="max" inputmode="decimal" value="${esc(r.max || '')}" placeholder="${esc(t('Top speed (rpm)'))}" aria-label="${esc(t('Top speed (rpm)'))}">
      <button type="button" class="btn btn-sm btn-ghost btn-icon" data-r-remove title="${esc(t('Remove'))}" aria-label="${esc(t('Remove'))}">${ico('close')}</button></div>`;
    box.innerHTML = `<div class="util-rotor-editor">
      <p class="util-hint">${esc(t('Saved for everyone in the lab. The radius is to the bottom of the tube; the rotor’s manual gives it as r max.'))}</p>
      <div data-rows>${(rotors.length ? rotors : [{}]).map(row).join('')}</div>
      <div class="util-rotor-actions"><button type="button" class="btn btn-sm" data-r-add>${ico('plus')}${esc(t('Add a rotor'))}</button>
        <span></span><button type="button" class="btn btn-sm btn-ghost" data-r-cancel>${esc(t('Cancel'))}</button>
        <button type="button" class="btn btn-sm btn-primary" data-r-save>${esc(t('Save'))}</button></div>
      <p class="util-error" data-r-error hidden></p></div>`;
    box.hidden = false;
    const rows = box.querySelector('[data-rows]');
    box.querySelector('[data-r-add]').addEventListener('click', () => rows.insertAdjacentHTML('beforeend', row({})));
    rows.addEventListener('click', (e) => { const b = e.target.closest('[data-r-remove]'); if (b) b.parentElement.remove(); });
    box.querySelector('[data-r-cancel]').addEventListener('click', () => { box.hidden = true; box.innerHTML = ''; });
    box.querySelector('[data-r-save]').addEventListener('click', async () => {
      const list = [...rows.querySelectorAll('.util-rotor-row')].map((r) => ({
        name: r.querySelector('[data-r="name"]').value.trim(), radius: B.num(r.querySelector('[data-r="radius"]').value),
        max: B.num(r.querySelector('[data-r="max"]').value) || 0,
      })).filter((r) => r.name || r.radius);
      const error = box.querySelector('[data-r-error]');
      try {
        const res = await fetch(root.dataset.rotorsUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rotors: list }) });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.error || t('Not saved. Check the connection and try again.'));
        rotors = body.rotors;
        done();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
      }
    });
  }

  function open(found) {
    const x = found.tool;
    const g = group(x.group);
    let modeIndex = found.explicit ? found.mode : Math.min(get(`mode:${x.id}`, found.mode) || 0, x.modes.length - 1);
    document.title = `${x.name} · ${t('Utilities')}`;
    // Home's Calculators card shows the ones opened last.
    put('recent', [{ id: x.id, title: x.name }, ...recentList().filter((r) => B.locate(r.id).tool.id !== x.id)].slice(0, 8));
    put('last', x.id);

    function draw() {
      const m = x.modes[modeIndex];
      const calc = calcOf(m.calc);
      const saved = get(m.calc, null);
      const raw = saved || { ...(m.example || {}) };
      if (pendingChem && calc.inputs.some((i) => i.key === 'chem')) {
        raw.chem = pendingChem; raw.mw = String(chemicals.get(pendingChem) || '');
        pendingChem = null;
      }
      const solve = m.solve ? (m.solve.find(([k]) => k === get(`solve:${x.id}`, '')) || m.solve[0])[0] : null;
      const pinned = pins.includes(x.id);
      view.innerHTML = `<div class="util-tool">${rail(x)}<div class="util-main">
        <a class="util-back util-back-narrow" href="#">${ico('chevron-left')}${esc(t('All tools'))}</a>
        <header class="util-head">
          <div><p class="util-crumb">${esc(g.name)}${x.lab && x.lab.author ? ` · ${esc(t('made by %(name)s', { name: x.lab.author }))}` : ''}</p><h2>${esc(x.name)}</h2><p class="util-does">${esc(x.does)}</p></div>
          <div class="util-actions">
            ${x.lab && mayChange(x.lab) ? `<a class="btn btn-sm" href="#lab-edit-${esc(x.id)}">${ico('edit')}${esc(t('Edit'))}</a>` : ''}
            <button type="button" class="btn btn-sm${pinned ? ' is-pinned' : ''}" data-pin aria-pressed="${pinned}">${ico('star')}${esc(pinned ? t('Pinned') : t('Pin'))}</button>
            <button type="button" class="btn btn-sm btn-ghost" data-reset title="${esc(t('Back to the example values'))}">${ico('refresh')}${esc(t('Reset'))}</button>
          </div>
        </header>
        ${(x.modes.length > 1 || m.solve) ? `<div class="util-controls">
          ${x.modes.length > 1 ? `<div class="util-seg" role="group" aria-label="${esc(t('Mode'))}">${x.modes.map((mm, i) => `<button type="button" data-mode="${i}" aria-pressed="${i === modeIndex}">${esc(mm.label)}</button>`).join('')}</div>` : ''}
          ${m.solve ? `<div class="util-solve"><span>${esc(t('Work out'))}</span><div class="util-seg" role="group" aria-label="${esc(t('Work out'))}">${m.solve.map(([k, label]) => `<button type="button" data-solve="${k}" aria-pressed="${k === solve}">${esc(label)}</button>`).join('')}</div></div>` : ''}
        </div>` : ''}
        <div class="util-work">
          <form class="card util-form" autocomplete="off">
            ${m.calc === 'rcf' ? rotorPicker() : ''}
            ${calc.inputs.filter((inp) => inp.key !== solve).map((inp) => field(inp, raw)).join('')}
          </form>
          <section class="card util-answer" aria-live="polite">
            <header><span>${esc(t('Answer'))}</span><button type="button" class="btn btn-sm btn-ghost" data-copy>${ico('copy')}<span>${esc(t('Copy'))}</span></button></header>
            <div class="util-answer-body" data-util-results></div>
          </section>
        </div></div></div>`;

      const form = view.querySelector('form');
      const out = view.querySelector('[data-util-results]');
      let last = {};
      const read = () => {
        const r = {};
        form.querySelectorAll('[data-key]').forEach((el) => { r[el.dataset.key] = el.value; });
        form.querySelectorAll('[data-unit]').forEach((el) => { r[`${el.dataset.unit}_unit`] = el.value; });
        return r;
      };
      const update = () => {
        const r = read();
        put(m.calc, { ...raw, ...r });
        if (solve) r[solve] = '';
        last = B.run(m.calc, r);
        // A rotor's top speed, which the arithmetic doesn't know.
        const rotor = rotorAt((form.querySelector('[data-rotor]') || {}).value);
        if (m.calc === 'rcf' && rotor && rotor.max) {
          const rpm = solve === 'g' ? B.num(r.rpm) : B.num(String((last.lines || [{}])[0].value || '').replace(/[^\d.]/g, ''));
          if (rpm > rotor.max) last = { ...last, warnings: [...(last.warnings || []), t('Over this rotor’s top speed (%(max)s rpm).', { max: B.fmt(rotor.max) })] };
        }
        out.innerHTML = results(last);
      };
      form.addEventListener('submit', (e) => e.preventDefault());
      form.addEventListener('input', (e) => {
        const mw = form.querySelector('[data-key="mw"]');
        // A chemical it knows fills its molecular weight. One it doesn't know
        // takes back the weight it filled for the last one (a stale 121.14 for
        // "MgSO4·7H2O" gave 121 g where 246 were needed); one typed by hand stays.
        if (e.target.dataset.key === 'chem' && mw) {
          const note = form.querySelector('[data-chem-note]');
          const matches = chemicalsFor(e.target.value.trim());
          if (matches.length === 1) {
            mw.value = chemicals.get(matches[0]);
            mw.dataset.filled = mw.value;
            note.textContent = matches[0] === e.target.value.trim() ? '' : t('%(chemical)s: %(mw)s g/mol', { chemical: matches[0], mw: mw.value });
          } else {
            if (mw.dataset.filled && mw.value === mw.dataset.filled) { mw.value = ''; delete mw.dataset.filled; }
            note.textContent = matches.length > 1 ? t('Which one? %(names)s', { names: matches.slice(0, 4).join(' · ') })
              : (e.target.value.trim() ? t('Not in the list: type its molecular weight.') : '');
          }
        } else if (e.target === mw) {
          delete mw.dataset.filled;
        }
        update();
      });
      form.addEventListener('change', (e) => {
        if (e.target.matches('[data-rotor]')) {
          const rotor = rotorAt(e.target.value);
          const radius = form.querySelector('[data-key="radius"]');
          if (rotor && radius) radius.value = rotor.radius;
          put('rotor', e.target.value);
        }
        update();
      });
      const picker = form.querySelector('[data-rotor]');
      if (picker && rotorAt(get('rotor', ''))) {
        picker.value = get('rotor', '');
        form.querySelector('[data-key="radius"]').value = rotorAt(picker.value).radius;
      }
      const edit = view.querySelector('[data-rotors-edit]');
      if (edit) edit.addEventListener('click', () => rotorEditor(view.querySelector('[data-rotors-editor]'), draw));
      view.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => { modeIndex = Number(b.dataset.mode); put(`mode:${x.id}`, modeIndex); draw(); }));
      view.querySelectorAll('[data-solve]').forEach((b) => b.addEventListener('click', () => { put(`solve:${x.id}`, b.dataset.solve); draw(); }));
      view.querySelector('[data-pin]').addEventListener('click', () => {
        pins = pins.includes(x.id) ? pins.filter((p) => p !== x.id) : [...pins, x.id];
        put('pins', pins);
        draw();
      });
      view.querySelector('[data-reset]').addEventListener('click', () => { forget(m.calc); draw(); });
      const copy = view.querySelector('[data-copy]');
      copy.addEventListener('click', () => {
        const label = copy.querySelector('span');
        const done = (text) => { label.textContent = text; setTimeout(() => { label.textContent = t('Copy'); }, 1500); };
        try {
          navigator.clipboard.writeText(asText(last, x.name)).then(() => done(t('Copied')), () => done(t('Not copied')));
        } catch (_) { done(t('Not copied')); }
      });
      update();
    }
    draw();
  }

  /* ---------------------------------------------------- reference tables */

  function reference(focus) {
    document.title = `${t('Reference')} · ${t('Utilities')}`;
    const chemRows = [...chemicals.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([name, mw]) => [name, B.fmt(mw, 6)]);
    const tables = [...B.REFERENCES, { id: 'ref-chemicals', title: t('Molecular weights'), head: [t('Chemical'), 'g/mol'], rows: chemRows }];
    view.innerHTML = `<div class="util-tool">${rail({ id: '', group: 'reference' })}<div class="util-main">
      <a class="util-back util-back-narrow" href="#">${ico('chevron-left')}${esc(t('All tools'))}</a>
      <header class="util-head"><div><p class="util-crumb">${esc(t('Reference'))}</p><h2>${esc(t('Reference tables'))}</h2>
        <p class="util-does">${esc(t('Values the calculators use, to look up at the bench.'))}</p></div></header>
      <div class="util-refs">${tables.map((r) => `<section class="card util-ref" id="${r.id}"><h3>${esc(r.title)}</h3><div class="util-table-wrap"><table class="util-table">
        <thead><tr>${r.head.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${r.rows.map((row) => `<tr>${row.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`).join('')}</div>
    </div></div>`;
    const el = focus && document.getElementById(focus);
    if (el) el.scrollIntoView({ block: 'start' });
  }

  /* ------------------------------------------- making a tool of the lab's */

  function editor(def) {
    const editing = !!def;
    const tool = def ? JSON.parse(JSON.stringify(def)) : {
      name: '', does: '', note: '',
      inputs: [{ name: 'mass', label: t('Mass'), unit: 'mg', value: '10' }, { name: 'volume', label: t('Volume'), unit: 'mL', value: '5' }],
      outputs: [{ label: t('Concentration'), formula: 'mass / volume', unit: 'mg/mL', name: '' }],
    };
    document.title = `${editing ? t('Edit a tool') : t('Make a tool')} · ${t('Utilities')}`;
    const inRow = (x, k) => `<div class="util-lab-row is-input" data-in="${k}">
      <input data-f="name" value="${esc(x.name)}" placeholder="${esc(t('name'))}" aria-label="${esc(t('Name in formulas'))}" spellcheck="false" class="font-mono">
      <input data-f="label" value="${esc(x.label)}" placeholder="${esc(t('Label'))}" aria-label="${esc(t('Label'))}">
      <input data-f="unit" value="${esc(x.unit)}" placeholder="${esc(t('Unit'))}" aria-label="${esc(t('Unit'))}">
      <input data-f="value" value="${esc(x.value)}" placeholder="${esc(t('Example'))}" aria-label="${esc(t('Example'))}" inputmode="decimal">
      <button type="button" class="btn btn-sm btn-ghost btn-icon" data-drop title="${esc(t('Remove'))}" aria-label="${esc(t('Remove'))}">${ico('close')}</button>
      <small class="util-lab-problem" data-problem="in${k}"></small></div>`;
    const outRow = (x, k) => `<div class="util-lab-row is-output" data-out="${k}">
      <input data-f="label" value="${esc(x.label)}" placeholder="${esc(t('Label'))}" aria-label="${esc(t('Label'))}">
      <input data-f="formula" value="${esc(x.formula)}" placeholder="mass / (conc * mw)" aria-label="${esc(t('Formula'))}" spellcheck="false" class="font-mono">
      <input data-f="unit" value="${esc(x.unit)}" placeholder="${esc(t('Unit'))}" aria-label="${esc(t('Unit'))}">
      <input data-f="name" value="${esc(x.name || '')}" placeholder="${esc(t('name (optional)'))}" aria-label="${esc(t('Name, to use in later formulas'))}" spellcheck="false" class="font-mono">
      <button type="button" class="btn btn-sm btn-ghost btn-icon" data-drop title="${esc(t('Remove'))}" aria-label="${esc(t('Remove'))}">${ico('close')}</button>
      <small class="util-lab-problem" data-problem="out${k}"></small></div>`;
    view.innerHTML = `<div class="util-tool">${rail({ id: '', group: 'lab' })}<div class="util-main">
      <a class="util-back util-back-narrow" href="#">${ico('chevron-left')}${esc(t('All tools'))}</a>
      <header class="util-head"><div><p class="util-crumb">${esc(t('The lab’s own'))}</p><h2>${esc(editing ? t('Edit a tool') : t('Make a tool'))}</h2>
        <p class="util-does">${esc(t('Inputs with a short name, and answers worked out from those names. Everyone in the lab can use it.'))}</p></div></header>
      <div class="util-work is-editor">
        <form class="card util-form util-lab-form" autocomplete="off">
          <label class="util-field is-wide"><span>${esc(t('Name'))}</span><input data-t="name" value="${esc(tool.name)}" placeholder="${esc(t('e.g. Pellet volume from OD'))}"></label>
          <label class="util-field is-wide"><span>${esc(t('What it gives, in a line'))}</span><input data-t="does" value="${esc(tool.does)}"></label>
          <div class="util-field is-wide"><span>${esc(t('Inputs'))}</span>
            <div class="util-lab-head is-input"><small>${esc(t('Name in formulas'))}</small><small>${esc(t('Label'))}</small><small>${esc(t('Unit'))}</small><small>${esc(t('Example'))}</small></div>
            <div data-inputs>${tool.inputs.map(inRow).join('')}</div>
            <button type="button" class="btn btn-sm btn-ghost util-lab-add" data-add-in>${ico('plus')}${esc(t('Add an input'))}</button></div>
          <div class="util-field is-wide"><span>${esc(t('Answers'))}</span>
            <div class="util-lab-head is-output"><small>${esc(t('Label'))}</small><small>${esc(t('Formula'))}</small><small>${esc(t('Unit'))}</small><small>${esc(t('Name, to use in later formulas'))}</small></div>
            <div data-outputs>${tool.outputs.map(outRow).join('')}</div>
            <button type="button" class="btn btn-sm btn-ghost util-lab-add" data-add-out>${ico('plus')}${esc(t('Add an answer'))}</button>
            <small class="util-hint">${esc(t('A formula uses the names above, numbers, + − * / ^, brackets, pi, and sqrt, ln, log10, log2, exp, abs, round, min, max, pow. Units are labels: keep the numbers in the units you name.'))}</small></div>
          <label class="util-field is-wide"><span>${esc(t('A note under the answer (optional)'))}</span><input data-t="note" value="${esc(tool.note || '')}"></label>
          <div class="util-lab-actions is-wide">
            ${editing ? `<button type="button" class="btn btn-sm btn-danger" data-delete>${ico('trash')}${esc(t('Delete'))}</button>` : ''}
            <span></span><a class="btn btn-sm btn-ghost" href="${editing ? `#${esc(def.id)}` : '#'}">${esc(t('Cancel'))}</a>
            <button type="submit" class="btn btn-sm btn-primary">${esc(t('Save'))}</button></div>
          <p class="util-error is-wide" data-error hidden></p>
        </form>
        <section class="card util-answer" aria-live="polite"><header><span>${esc(t('Preview'))}</span></header><div class="util-answer-body" data-preview></div></section>
      </div></div></div>`;
    const form = view.querySelector('form');
    const read = () => ({
      ...(editing ? { id: def.id } : {}),
      name: form.querySelector('[data-t="name"]').value.trim(), does: form.querySelector('[data-t="does"]').value.trim(),
      note: form.querySelector('[data-t="note"]').value.trim(),
      inputs: [...form.querySelectorAll('[data-in]')].map((r) => Object.fromEntries([...r.querySelectorAll('[data-f]')].map((i) => [i.dataset.f, i.value.trim()]))),
      outputs: [...form.querySelectorAll('[data-out]')].map((r) => Object.fromEntries([...r.querySelectorAll('[data-f]')].map((i) => [i.dataset.f, i.value.trim()]))),
    });
    let problems = {};
    const preview = () => {
      const now = read();
      const made = B.labTool({ ...now, id: 'preview' });
      problems = made.problems;
      form.querySelectorAll('[data-problem]').forEach((el) => { el.textContent = problems[el.dataset.problem] || ''; });
      const raw = Object.fromEntries(now.inputs.filter((x) => x.name).map((x) => [x.name, x.value]));
      let out;
      try { out = made.calc.compute({ raw, num: Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, B.num(v)])), base: {}, unit: {} }); } catch (_) { out = { error: t('That doesn\'t add up: check the numbers.') }; }
      view.querySelector('[data-preview]').innerHTML = `<h3 class="util-lab-title">${esc(now.name || t('Untitled tool'))}</h3>${results(out)}`;
    };
    // Rows renumber after a removal, so each problem shows on its own row.
    const renumber = () => {
      form.querySelectorAll('[data-in]').forEach((r, k) => { r.dataset.in = k; r.querySelector('[data-problem]').dataset.problem = `in${k}`; });
      form.querySelectorAll('[data-out]').forEach((r, k) => { r.dataset.out = k; r.querySelector('[data-problem]').dataset.problem = `out${k}`; });
    };
    form.addEventListener('input', preview);
    form.addEventListener('click', (e) => {
      if (e.target.closest('[data-drop]')) { e.target.closest('.util-lab-row').remove(); renumber(); preview(); }
      if (e.target.closest('[data-add-in]')) { const box = form.querySelector('[data-inputs]'); box.insertAdjacentHTML('beforeend', inRow({ name: '', label: '', unit: '', value: '' }, box.children.length)); }
      if (e.target.closest('[data-add-out]')) { const box = form.querySelector('[data-outputs]'); box.insertAdjacentHTML('beforeend', outRow({ label: '', formula: '', unit: '', name: '' }, box.children.length)); }
    });
    const error = form.querySelector('[data-error]');
    const send = async (url, body) => {
      error.hidden = true;
      try {
        const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || t('Not saved. Check the connection and try again.'));
        B.useLabTools(data.tools);
        return data;
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        return null;
      }
    };
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      preview();
      if (Object.keys(problems).length) { error.textContent = t('Fix the marked rows first.'); error.hidden = false; return; }
      const data = await send(root.dataset.labUrl, { tool: read() });
      if (data) location.hash = data.id;
    });
    const del = form.querySelector('[data-delete]');
    if (del) {
      del.addEventListener('click', async () => {
        if (del.dataset.sure !== '1') { del.dataset.sure = '1'; del.lastChild.textContent = t('Delete for everyone?'); return; }
        const data = await send(`${root.dataset.labUrl}/${encodeURIComponent(def.id)}/delete`, {});
        if (data) location.hash = '';
      });
    }
    preview();
  }

  /* --------------------------------------------------------------- route */

  function route() {
    const id = decodeURIComponent(location.hash.slice(1));
    if (id === 'reference' || id.startsWith('ref-')) { reference(id); return; }
    if (id === 'lab-new' && canEditRotors) { editor(null); window.scrollTo(0, 0); return; }
    if (id.startsWith('lab-edit-')) {
      const found = B.locate(id.slice(9));
      if (found && found.tool.lab && mayChange(found.tool.lab)) { editor(found.tool.lab); window.scrollTo(0, 0); return; }
    }
    const found = id && B.locate(id);
    if (found) {
      // An address naming a calculator opens its mode; a tool's own name
      // opens the mode used last.
      open({ ...found, explicit: found.tool.id !== id });
      window.scrollTo(0, 0);
      return;
    }
    home();
  }
  window.addEventListener('hashchange', route);
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName);
    if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      if (location.hash) { history.pushState(null, '', location.pathname + location.search); route(); }
      const q = view.querySelector('[data-util-search]');
      if (q) q.focus();
    }
  });
  route();
})();
