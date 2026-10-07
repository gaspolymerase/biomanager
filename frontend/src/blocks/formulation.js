// Formulation block: what goes into a reaction or a batch, by mass. Pick
// each component from the lab's Chemicals database (its molecular weight,
// purity, density, CAS number and lot come with it), type what you weigh,
// and the moles follow. Or give a component's equivalents against the
// basis row and the mass to weigh follows; change the basis and those
// follow it. Each row also shows its share of the total mass, and a
// liquid's volume when its density is known.
//
// data = {
//   name, notes, basis: 0,
//   components: [{ name, abbr, ref: '@chemicals 12', cas, lot, mw, purity, density,
//                  mass, unit: 'g', eq, given: 'mass'|'eq', done }],
// }
// `ref` is written as a record link, so the chemical's "Used in notebook
// pages" finds the page.

import { api, debounce, escapeHtml, fmt, toNumber } from '../util.js';
import { MASS_UNITS, showMass, unitOptions } from './units.js';

const WEIGH_UNITS = { kg: MASS_UNITS.kg, g: MASS_UNITS.g, mg: MASS_UNITS.mg, 'µg': MASS_UNITS['µg'] };

export function emptyComponent() {
  return { name: '', mw: '', mass: '', unit: 'g', eq: '', given: 'mass' };
}

export function defaultFormulation() {
  return { name: '', notes: '', basis: 0, components: [emptyComponent(), emptyComponent()] };
}

function normalize(data) {
  const d = { ...defaultFormulation(), ...(data || {}) };
  d.components = Array.isArray(d.components) ? d.components.map((c) => ({ ...emptyComponent(), ...c })) : [];
  const b = Number(d.basis);
  d.basis = Number.isInteger(b) && b >= 0 && b < d.components.length ? b : 0;
  return d;
}

// Amount of substance in the handiest unit: "12.5 mmol", "250 µmol".
export function showMol(mol) {
  if (!Number.isFinite(mol)) return '—';
  const a = Math.abs(mol);
  if (a >= 1) return `${fmt(mol, 4)} mol`;
  if (a >= 1e-3) return `${fmt(mol * 1e3, 4)} mmol`;
  if (a >= 1e-6) return `${fmt(mol * 1e6, 4)} µmol`;
  return `${fmt(mol * 1e9, 4)} nmol`;
}

// A number for an input box: no thousands separators or decimal commas,
// which the box would read back wrongly.
function plain(n, sig = 4) {
  return Number.isFinite(n) ? String(Number(n.toPrecision(sig))) : '';
}

function positive(v) {
  const n = toNumber(v);
  return Number.isFinite(n) && n > 0 ? n : NaN;
}

// The fraction of what is weighed that is the chemical itself.
function fraction(c) {
  const p = positive(c.purity);
  return Number.isFinite(p) && p <= 100 ? p / 100 : 1;
}

// Grams, moles and equivalents of every row: the basis row and rows given
// by mass from what was typed; rows given in equivalents from the basis.
export function compute(data) {
  const rows = data.components.map((c, i) => {
    const mw = positive(c.mw);
    const isBasis = i === data.basis;
    const byEq = !isBasis && c.given === 'eq';
    const typed = toNumber(c.mass);
    const grams = !byEq && Number.isFinite(typed) ? typed * (WEIGH_UNITS[c.unit] ?? 1) : NaN;
    return { c, mw, isBasis, byEq, grams, mol: grams * fraction(c) / mw, eq: NaN };
  });
  const basis = rows[data.basis];
  const basisMol = basis ? basis.mol : NaN;
  for (const r of rows) {
    if (r.isBasis) {
      r.eq = Number.isFinite(r.mol) ? 1 : NaN;
    } else if (r.byEq) {
      const eq = toNumber(r.c.eq);
      r.eq = eq;
      r.mol = eq * basisMol;
      r.grams = r.mol * r.mw / fraction(r.c);
    } else {
      r.eq = basisMol > 0 ? r.mol / basisMol : NaN;
    }
    const density = positive(r.c.density);
    r.mL = Number.isFinite(density) ? r.grams / density : NaN;
  }
  const total = rows.reduce((sum, r) => sum + (Number.isFinite(r.grams) ? r.grams : 0), 0);
  for (const r of rows) r.wt = total > 0 && Number.isFinite(r.grams) ? (r.grams / total) * 100 : NaN;
  return { rows, total, liquids: rows.reduce((sum, r) => sum + (Number.isFinite(r.mL) ? r.mL : 0), 0) };
}

// "@chemicals 12" → the address that opens that record.
function recordUrl(ref) {
  const m = /^@(\w+)\s+(\d+)$/.exec(ref || '');
  return m ? `/notebook/open/${encodeURIComponent(m[1])}/${m[2]}` : '';
}

let uid = 0;

export function mountFormulation(host, ctx) {
  let data = normalize(ctx.data);
  const editable = ctx.editable;
  const group = `nb-basis-${++uid}`;
  const commit = debounce(() => ctx.commit(JSON.parse(JSON.stringify(data))), 350);
  const root = document.createElement('div');
  root.className = 'nb-recipe nb-formulation';
  host.appendChild(root);

  function about(c) {
    const bits = [c.abbr, c.cas && `CAS ${c.cas}`, c.lot && `lot ${c.lot}`,
      positive(c.purity) < 100 && `${fmt(positive(c.purity))} % pure`,
      positive(c.density) && `${fmt(positive(c.density))} g/mL`].filter(Boolean);
    const url = recordUrl(c.ref);
    const link = url ? `<a href="${url}" target="_blank" rel="noopener" title="Open the record">${escapeHtml(c.ref.replace(/^@\w+\s+/, '#'))}</a>` : '';
    return bits.length || link ? `<small class="nb-muted">${[link, ...bits.map(escapeHtml)].filter(Boolean).join(' · ')}</small>` : '';
  }

  function renderResults() {
    const { rows, total, liquids } = compute(data);
    root.querySelectorAll('tr[data-i]').forEach((tr) => {
      const r = rows[Number(tr.dataset.i)];
      if (!r) return;
      const mass = tr.querySelector('input[data-c="mass"]');
      const eq = tr.querySelector('input[data-c="eq"]');
      // The value worked out, shown in the box the person did not type in.
      if (r.byEq && mass && document.activeElement !== mass) {
        mass.value = plain(r.grams / (WEIGH_UNITS[r.c.unit] ?? 1));
      }
      if (!r.byEq && !r.isBasis && eq && document.activeElement !== eq) {
        eq.value = plain(r.eq, 3);
      }
      mass?.classList.toggle('is-computed', r.byEq);
      eq?.classList.toggle('is-computed', !r.byEq && !r.isBasis);
      const mol = tr.querySelector('.nb-form-mol');
      const needsMw = !Number.isFinite(r.mw) && (Number.isFinite(r.grams) || r.byEq);
      mol.innerHTML = needsMw ? '<span class="nb-warn">needs a molecular weight</span>'
        : Number.isFinite(r.mol) ? `<b>${showMol(r.mol)}</b>${Number.isFinite(r.mL) ? `<small>≈ ${fmt(r.mL, 4)} mL</small>` : ''}` : '';
      tr.querySelector('.nb-form-wt').textContent = Number.isFinite(r.wt) ? `${fmt(r.wt, 3)} %` : '';
    });
    const foot = root.querySelector('.nb-form-total');
    if (foot) {
      const basis = rows[data.basis];
      const missingBasis = rows.some((r) => r.byEq) && !(basis && basis.mol > 0);
      foot.innerHTML = (total > 0 ? `Total <b>${showMass(total)}</b>${liquids > 0 ? ` <span class="nb-muted">· liquids ≈ ${fmt(liquids, 4)} mL</span>` : ''}` : '')
        + (missingBasis ? ' <span class="nb-warn">Weigh the basis row (and give its molecular weight) for the equivalents to have a mass.</span>' : '');
    }
  }

  function render() {
    const dis = editable ? '' : ' disabled';
    root.innerHTML = `
      <div class="nb-recipe-head">
        <input class="nb-recipe-name" data-f="name" placeholder="Formulation name, e.g. Batch P-03" value="${escapeHtml(data.name)}"${dis}>
      </div>
      <div class="nb-sheet-scroll"><table class="nb-recipe-table nb-form-table">
        <colgroup><col class="c-tick"><col class="c-name"><col class="c-mw"><col class="c-mass"><col class="c-mol"><col class="c-eq"><col class="c-wt">${editable ? '<col class="c-x">' : ''}</colgroup>
        <thead><tr><th class="nb-recipe-tick" title="Tick as you weigh">✓</th><th>Component</th><th>MW (g/mol)</th><th>Mass</th><th>Amount</th><th title="Moles against the basis row, which is 1">Equiv.</th><th>wt %</th>${editable ? '<th></th>' : ''}</tr></thead>
        <tbody>${data.components.map((c, i) => `
          <tr data-i="${i}">
            <td class="nb-recipe-tick"><input type="checkbox" data-c="done"${c.done ? ' checked' : ''} aria-label="Weighed"></td>
            <td class="nb-form-name"><input data-c="name" value="${escapeHtml(c.name || '')}" placeholder="${editable ? 'Name, abbreviation or CAS' : ''}" autocomplete="off"${dis}>${about(c)}</td>
            <td><input data-c="mw" inputmode="decimal" value="${escapeHtml(c.mw ?? '')}" placeholder="needed"${dis}></td>
            <td class="nb-recipe-conc"><input data-c="mass" inputmode="decimal" value="${escapeHtml(c.given === 'eq' && i !== data.basis ? '' : c.mass ?? '')}"${dis}><select data-c="unit"${dis}>${unitOptions(WEIGH_UNITS, c.unit || 'g')}</select></td>
            <td class="nb-recipe-amount nb-form-mol"></td>
            <td><span class="nb-form-eq"><label class="nb-form-basis" title="Basis: the others' equivalents count against this one"><input type="radio" name="${group}" data-basis="${i}"${i === data.basis ? ' checked' : ''}${dis}></label>${i === data.basis
              ? '<span class="nb-form-one">1 <small>basis</small></span>'
              : `<input data-c="eq" inputmode="decimal" value="${escapeHtml(c.given === 'eq' ? c.eq ?? '' : '')}"${dis}>`}</span></td>
            <td class="nb-form-wt"></td>
            ${editable ? `<td><button type="button" class="nb-icon-btn" data-remove="${i}" title="Remove" aria-label="Remove component">×</button></td>` : ''}
          </tr>`).join('')}
        </tbody>
      </table></div>
      <div class="nb-recipe-water nb-form-total"></div>
      <textarea class="nb-recipe-notes" data-f="notes" rows="2" placeholder="Notes: order of addition, temperature, atmosphere"${dis}>${escapeHtml(data.notes || '')}</textarea>
      <div class="nb-recipe-actions">
        ${editable ? '<button type="button" class="nb-mini" data-act="add">+ Component</button>' : ''}
        <button type="button" class="nb-mini" data-act="untick">Clear ticks</button>
      </div>`;
    renderResults();
  }

  // ---- picking a chemical from the lab's Chemicals database
  let menu = null;
  let found = [];
  let active = -1;
  let forRow = -1;

  // Scrolling the page leaves the menu behind, so it closes; scrolling its
  // own list does not.
  function onScroll(event) {
    if (!(menu && event.target instanceof Node && menu.contains(event.target))) closeMenu();
  }

  function closeMenu() {
    window.removeEventListener('scroll', onScroll, { capture: true });
    menu?.remove();
    menu = null;
    found = [];
    active = -1;
  }

  function showMenu(input, res) {
    closeMenu();
    found = res.items || [];
    forRow = Number(input.closest('tr').dataset.i);
    if (!found.length && res.has_database) return;
    menu = document.createElement('div');
    menu.className = 'nb-chem-menu';
    menu.setAttribute('role', 'listbox');
    menu.innerHTML = found.map((c, k) => `<button type="button" role="option" data-pick="${k}">
        <b>${escapeHtml(c.name)}</b>${c.abbr ? ` <span>${escapeHtml(c.abbr)}</span>` : ''}
        <small>${[c.mw ? `${fmt(c.mw)} g/mol` : 'no MW', c.cas && `CAS ${escapeHtml(c.cas)}`, c.lot && `lot ${escapeHtml(c.lot)}`,
          c.builtin ? 'built in' : `${escapeHtml(c.database)} ${escapeHtml(c.ref.replace(/^@\w+\s+/, '#'))}`,
          c.available ? '' : escapeHtml(c.status)].filter(Boolean).join(' · ')}</small></button>`).join('')
      + (res.has_database ? '' : '<p class="nb-muted">Keep your chemicals in a <a href="/inventory/new?preset=chemicals" target="_blank" rel="noopener">Chemicals database</a> to pick them here with their molecular weight, CAS number and lot.</p>');
    // Fixed under the box: the table scrolls sideways, which would clip it.
    const box = input.getBoundingClientRect();
    Object.assign(menu.style, { left: `${box.left}px`, top: `${box.bottom + 2}px`, width: `${Math.max(box.width, 320)}px` });
    root.appendChild(menu);
    window.addEventListener('scroll', onScroll, { capture: true });
  }

  function highlight(k) {
    active = k;
    menu?.querySelectorAll('[data-pick]').forEach((b) => b.classList.toggle('is-active', Number(b.dataset.pick) === k));
  }

  function pick(k) {
    const chem = found[k];
    const c = data.components[forRow];
    closeMenu();
    if (!chem || !c) return;
    Object.assign(c, {
      name: chem.name, abbr: chem.abbr || '', ref: chem.ref || '', cas: chem.cas || '', lot: chem.lot || '',
      mw: chem.mw ? String(chem.mw) : c.mw, purity: chem.purity ? String(chem.purity) : '',
      density: chem.density ? String(chem.density) : '',
    });
    commit(); render();
    root.querySelector(`tr[data-i="${forRow}"] input[data-c="mass"]`)?.focus();
  }

  const search = debounce(async (input) => {
    const q = input.value.trim();
    if (!q) { closeMenu(); return; }
    try {
      const res = await api(`/notebook/api/chemicals?q=${encodeURIComponent(q)}`);
      if (document.activeElement === input && input.value.trim() === q) showMenu(input, res);
    } catch (_e) { closeMenu(); }
  }, 200);

  root.addEventListener('input', (event) => {
    const t = event.target;
    if (t.dataset.f) {
      data[t.dataset.f] = t.value;
    } else if (t.dataset.c && t.type !== 'checkbox' && t.type !== 'radio') {
      const i = Number(t.closest('tr').dataset.i);
      const c = data.components[i];
      c[t.dataset.c] = t.value;
      if (t.dataset.c === 'name') {
        // Typed over: no longer that record.
        ['ref', 'abbr', 'cas', 'lot', 'purity', 'density'].forEach((k) => { delete c[k]; });
        t.parentElement.querySelector('small')?.remove();
        search(t);
      } else if (t.dataset.c === 'mass' && i !== data.basis) {
        c.given = 'mass';
        delete c.eq;
      } else if (t.dataset.c === 'eq') {
        c.given = 'eq';
        delete c.mass;
      }
    } else return;
    commit();
    renderResults();
  });
  root.addEventListener('change', (event) => {
    const t = event.target;
    if (t.dataset.c === 'done') {
      data.components[Number(t.closest('tr').dataset.i)].done = t.checked;
      commit();
    } else if (t.dataset.basis !== undefined) {
      // The new basis is weighed: keep the mass it had, worked out or typed.
      const i = Number(t.dataset.basis);
      const r = compute(data).rows[i];
      const c = data.components[i];
      if (c.given === 'eq' && Number.isFinite(r.grams)) c.mass = plain(r.grams / (WEIGH_UNITS[c.unit] ?? 1), 6);
      c.given = 'mass';
      delete c.eq;
      data.basis = i;
      commit(); render();
    } else if (t.dataset.c === 'unit') {
      const i = Number(t.closest('tr').dataset.i);
      const c = data.components[i];
      const before = toNumber(c.mass);
      const from = WEIGH_UNITS[c.unit] ?? 1;
      c.unit = t.value;
      // The same mass in the new unit, when it was typed.
      if (Number.isFinite(before) && c.given !== 'eq') c.mass = plain(before * from / WEIGH_UNITS[c.unit], 6);
      commit(); render();
    }
  });
  root.addEventListener('keydown', (event) => {
    if (!menu || event.target.dataset.c !== 'name') return;
    const count = found.length;
    if (event.key === 'ArrowDown' && count) { event.preventDefault(); highlight((active + 1) % count); }
    else if (event.key === 'ArrowUp' && count) { event.preventDefault(); highlight((active - 1 + count) % count); }
    else if (event.key === 'Enter' && active >= 0) { event.preventDefault(); pick(active); }
    else if (event.key === 'Escape') { event.preventDefault(); closeMenu(); }
  });
  root.addEventListener('focusout', (event) => {
    if (menu && !(event.relatedTarget && menu.contains(event.relatedTarget))) setTimeout(closeMenu, 150);
  });
  root.addEventListener('mousedown', (event) => {
    // Keep the name box's focus while a chemical is clicked.
    if (event.target.closest('[data-pick]')) event.preventDefault();
  });
  root.addEventListener('click', (event) => {
    const t = event.target.closest('button');
    if (!t) return;
    if (t.dataset.pick !== undefined) {
      pick(Number(t.dataset.pick));
    } else if (t.dataset.remove !== undefined) {
      const i = Number(t.dataset.remove);
      data.components.splice(i, 1);
      if (data.basis === i) data.basis = 0;
      else if (data.basis > i) data.basis -= 1;
      const b = data.components[data.basis];
      if (b && b.given === 'eq') { b.given = 'mass'; delete b.eq; }
      commit(); render();
    } else if (t.dataset.act === 'add') {
      data.components.push(emptyComponent());
      commit(); render();
      root.querySelector(`tr[data-i="${data.components.length - 1}"] input[data-c="name"]`)?.focus();
    } else if (t.dataset.act === 'untick') {
      data.components.forEach((c) => { delete c.done; });
      if (editable) commit();
      render();
    }
  });

  render();
  return {
    update(next) { closeMenu(); data = normalize(next); render(); },
    destroy() { closeMenu(); commit.flush(); },
  };
}
