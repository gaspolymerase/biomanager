// Buffer / media recipe block. Enter what you want at the end (final
// volume, each component's final concentration) and it works out what to
// weigh or pipette: mass from molarity and molecular weight, volume from a
// stock (C1V1 = C2V2), grams for % w/v, millilitres for % v/v, and the
// water to make up. Change the volume (or press ½×, 2×, 10×) and every
// amount follows. Recipes can be saved to, and loaded from, the lab's
// library, which has the common ones built in.

import { api, ask, debounce, el, escapeHtml, fmt, toNumber } from '../util.js';
import { concOptions, family, litres, showMass, showVolume, toBase, unitOptions, VOLUME_UNITS } from './units.js';

export function defaultRecipe() {
  return {
    name: '', volume: 1, volumeUnit: 'L', ph: '', notes: '',
    components: [{ name: '', conc: '', unit: 'mM', mw: '', stock: '', stockUnit: 'M' }],
  };
}

function normalize(data) {
  const d = { ...defaultRecipe(), ...(data || {}) };
  d.components = Array.isArray(d.components) ? d.components.map((c) => ({ ...c })) : [];
  return d;
}

// What to add for one component, in `volumeL` litres of the final solution.
export function amountFor(c, volumeL) {
  const conc = toNumber(c.conc);
  if (!Number.isFinite(conc) || !c.unit) return { text: '', error: '' };
  const fam = family(c.unit);
  const stock = toNumber(c.stock);
  if (Number.isFinite(stock) && stock > 0 && c.stockUnit) {
    if (family(c.stockUnit) !== fam) return { error: 'stock and final units differ in kind' };
    const ratio = toBase(conc, c.unit) / toBase(stock, c.stockUnit);
    if (ratio > 1) return { error: 'stock is weaker than the final concentration' };
    const v = ratio * volumeL;
    return { text: showVolume(v), liquid: v, how: `from ${fmt(stock)} ${c.stockUnit} stock` };
  }
  if (fam === 'molar') {
    const mw = toNumber(c.mw);
    if (!Number.isFinite(mw) || mw <= 0) return { error: 'needs a molecular weight, or a stock' };
    const g = toBase(conc, c.unit) * volumeL * mw;
    return { text: showMass(g), mass: g, how: `${fmt(mw)} g/mol` };
  }
  if (fam === 'mass') {
    return { text: showMass(toBase(conc, c.unit) * volumeL), mass: toBase(conc, c.unit) * volumeL, how: 'weigh' };
  }
  if (fam === 'vol') {
    const v = toBase(conc, c.unit) * volumeL;
    return { text: showVolume(v), liquid: v, how: 'measure' };
  }
  return { error: 'needs a stock concentration' };
}

// ctx.library: the recipe is the library's own (the Recipes page edits
// it), not one in a page: no ticks, and nothing to save to or load from
// the library.
export function mountRecipe(host, ctx) {
  let data = normalize(ctx.data);
  let editable = ctx.editable;
  const inLibrary = !!ctx.library;
  const commit = debounce(() => ctx.commit(JSON.parse(JSON.stringify(data))), 350);
  const root = el('div', { class: inLibrary ? 'nb-recipe is-library' : 'nb-recipe' });
  host.appendChild(root);

  function volumeL() {
    return litres(toNumber(data.volume), data.volumeUnit);
  }

  function renderResults() {
    const v = volumeL();
    let liquids = 0;
    root.querySelectorAll('tr[data-i]').forEach((tr) => {
      const c = data.components[Number(tr.dataset.i)];
      const out = tr.querySelector('.nb-recipe-amount');
      const res = amountFor(c, v);
      if (res.liquid) liquids += res.liquid;
      out.innerHTML = res.error ? `<span class="nb-warn">${escapeHtml(res.error)}</span>` : res.text ? `<b>${res.text}</b><small>${escapeHtml(res.how || '')}</small>` : '';
    });
    const water = root.querySelector('.nb-recipe-water');
    if (water) {
      water.innerHTML = Number.isFinite(v)
        ? `Water (or solvent) to <b>${showVolume(v)}</b>${liquids > 0 ? ` <span class="nb-muted">— about ${showVolume(v - liquids)} after the liquids above</span>` : ''}${liquids > v ? ' <span class="nb-warn">the liquids add up to more than the final volume</span>' : ''}`
        : '';
    }
  }

  function render() {
    const dis = editable ? '' : ' disabled';
    root.innerHTML = `
      <div class="nb-recipe-head">
        <input class="nb-recipe-name" data-f="name" placeholder="Recipe name, e.g. 10× PBS" value="${escapeHtml(data.name)}"${dis}>
        <label class="nb-recipe-vol">Make
          <input data-f="volume" inputmode="decimal" value="${escapeHtml(data.volume)}"${dis}>
          <select data-f="volumeUnit"${dis}>${unitOptions(VOLUME_UNITS, data.volumeUnit)}</select>
        </label>
        ${editable ? `<span class="nb-seg nb-recipe-scale">${[0.5, 2, 10].map((f) => `<button type="button" data-scale="${f}">${f === 0.5 ? '½' : f}×</button>`).join('')}</span>` : ''}
        <label class="nb-recipe-ph">pH <input data-f="ph" value="${escapeHtml(data.ph || '')}" placeholder="—"${dis}></label>
      </div>
      <div class="nb-sheet-scroll"><table class="nb-recipe-table">
        <colgroup><col class="c-tick"><col class="c-name"><col class="c-conc"><col class="c-mw"><col class="c-stock"><col class="c-add">${editable ? '<col class="c-x">' : ''}</colgroup>
        <thead><tr><th class="nb-recipe-tick" title="Tick as you add">✓</th><th>Component</th><th>Final</th><th>MW (g/mol)</th><th>Stock (optional)</th><th>Add</th>${editable ? '<th></th>' : ''}</tr></thead>
        <tbody>${data.components.map((c, i) => `
          <tr data-i="${i}">
            <td class="nb-recipe-tick"><input type="checkbox" data-c="done"${c.done ? ' checked' : ''} aria-label="Added"></td>
            <td><input data-c="name" value="${escapeHtml(c.name || '')}" placeholder="e.g. NaCl"${dis}>${c.note ? `<small class="nb-muted">${escapeHtml(c.note)}</small>` : ''}</td>
            <td class="nb-recipe-conc"><input data-c="conc" inputmode="decimal" value="${escapeHtml(c.conc ?? '')}"${dis}><select data-c="unit"${dis}>${concOptions(c.unit)}</select></td>
            <td><input data-c="mw" inputmode="decimal" value="${escapeHtml(c.mw ?? '')}" placeholder="${family(c.unit) === 'molar' ? 'needed' : '—'}"${dis}></td>
            <td class="nb-recipe-conc"><input data-c="stock" inputmode="decimal" value="${escapeHtml(c.stock ?? '')}" placeholder="—"${dis}><select data-c="stockUnit"${dis}>${concOptions(c.stockUnit || c.unit, [family(c.unit)])}</select></td>
            <td class="nb-recipe-amount"></td>
            ${editable ? `<td><button type="button" class="nb-icon-btn" data-remove="${i}" title="Remove" aria-label="Remove component">×</button></td>` : ''}
          </tr>`).join('')}
        </tbody>
      </table></div>
      <div class="nb-recipe-water"></div>
      <textarea class="nb-recipe-notes" data-f="notes" rows="2" placeholder="Notes: how to dissolve, adjust pH, sterilise, store"${dis}>${escapeHtml(data.notes || '')}</textarea>
      <div class="nb-recipe-actions">
        ${editable ? '<button type="button" class="nb-mini" data-act="add">+ Component</button>' : ''}
        ${editable && !inLibrary ? '<button type="button" class="nb-mini" data-act="load">Load from library</button>' : ''}
        ${inLibrary ? '' : '<button type="button" class="nb-mini" data-act="save">Save to library</button><button type="button" class="nb-mini" data-act="untick">Clear ticks</button>'}
      </div>
      <div class="nb-recipe-library" hidden></div>`;
    renderResults();
  }

  root.addEventListener('input', (event) => {
    const t = event.target;
    if (t.dataset.f) {
      data[t.dataset.f] = t.value;
    } else if (t.dataset.c && t.type !== 'checkbox') {
      const i = Number(t.closest('tr').dataset.i);
      data.components[i][t.dataset.c] = t.value;
    } else return;
    commit();
    renderResults();
  });
  root.addEventListener('change', (event) => {
    const t = event.target;
    if (t.dataset.c === 'done') {
      data.components[Number(t.closest('tr').dataset.i)].done = t.checked;
      commit();
    } else if (t.dataset.c === 'unit') {
      const i = Number(t.closest('tr').dataset.i);
      const c = data.components[i];
      if (family(c.stockUnit) !== family(c.unit)) c.stockUnit = c.unit;
      commit(); render();
    } else if (t.tagName === 'SELECT') {
      commit(); renderResults();
    }
  });
  root.addEventListener('click', async (event) => {
    const t = event.target.closest('button');
    if (!t) return;
    if (t.dataset.scale) {
      const f = Number(t.dataset.scale);
      data.volume = String(Number((toNumber(data.volume) * f).toPrecision(6)));
      commit(); render();
    } else if (t.dataset.remove !== undefined) {
      data.components.splice(Number(t.dataset.remove), 1);
      commit(); render();
    } else if (t.dataset.act === 'add') {
      data.components.push({ name: '', conc: '', unit: 'mM', mw: '', stock: '', stockUnit: 'M' });
      commit(); render();
      root.querySelector(`tr[data-i="${data.components.length - 1}"] input[data-c="name"]`)?.focus();
    } else if (t.dataset.act === 'untick') {
      data.components.forEach((c) => { delete c.done; });
      if (editable) commit();
      render();
    } else if (t.dataset.act === 'save') {
      const name = await ask.prompt('Save to the lab library as', data.name || '', { okLabel: 'Save' });
      if (!name) return;
      try {
        await api('/notebook/api/recipes', { method: 'POST', body: { name, data: { ...data, components: data.components.map(({ done, ...c }) => c) } } });
        t.textContent = 'Saved ✓';
        setTimeout(() => { t.textContent = 'Save to library'; }, 2000);
      } catch (e) { ask.alert(`Could not save: ${e.message}`); }
    } else if (t.dataset.act === 'load') {
      showLibrary();
    } else if (t.dataset.pick) {
      const item = libraryItems.find((r) => String(r.id) === t.dataset.pick);
      if (!item) return;
      data = normalize({ ...item.data, name: item.data.name || item.name });
      commit(); render();
    } else if (t.dataset.closeLib !== undefined) {
      root.querySelector('.nb-recipe-library').hidden = true;
    }
  });

  let libraryItems = [];
  async function showLibrary() {
    const box = root.querySelector('.nb-recipe-library');
    box.hidden = false;
    box.innerHTML = '<p class="nb-muted">Loading…</p>';
    try {
      const res = await api('/notebook/api/recipes');
      libraryItems = [...res.recipes, ...res.presets];
      const row = (r, tag) => `<button type="button" class="nb-lib-item" data-pick="${escapeHtml(r.id)}"><b>${escapeHtml(r.name)}</b><small>${tag} · ${(r.data.components || []).length} components · ${escapeHtml(fmt(toNumber(r.data.volume)))} ${escapeHtml(r.data.volumeUnit || '')}</small></button>`;
      // The lab's recipes as the Recipes page files them: each folder, then the rest.
      const folders = (res.folders || []).map((f) => ({ label: f.name, items: res.recipes.filter((r) => r.folder_id === f.id) }))
        .filter((part) => part.items.length);
      const filed = new Set((res.folders || []).map((f) => f.id));
      const loose = res.recipes.filter((r) => !filed.has(r.folder_id));
      if (loose.length) folders.push({ label: folders.length ? 'Not in a folder' : 'Saved by the lab', items: loose });
      box.innerHTML = `<div class="nb-lib-head"><b>Recipe library</b><button type="button" class="nb-icon-btn" data-close-lib aria-label="Close">×</button></div>
        ${folders.map((part) => `<div class="nb-lib-label">${escapeHtml(part.label)}</div>${part.items.map((r) => row(r, escapeHtml(r.owner_name || r.owner))).join('')}`).join('')}
        <div class="nb-lib-label">Common recipes</div>${res.presets.map((r) => row(r, 'built in')).join('')}`;
    } catch (e) {
      box.innerHTML = `<p class="nb-warn">Could not load the library: ${escapeHtml(e.message)}</p>`;
    }
  }

  render();
  return {
    update(next) { data = normalize(next); render(); },
    destroy() { commit.flush(); },
  };
}
