/* A sheet's views, filter chips, inspector and who else is here.
 *
 * On a sheet (data-table.js) whose card asks for them:
 *
 *   data-dt-views        a row of saved views above the toolbar: All, then
 *                        each view this person saved (a search, filter
 *                        chips, a sort and the columns shown, under a name),
 *                        then + Save view. Kept per person with the lab
 *                        (app/sheet_views.py), so they follow you. Beside
 *                        them, who else has this sheet open.
 *   (every such sheet)   + Filter in the toolbar: pick a column, then one of
 *                        the values it holds, for a chip that keeps only
 *                        those rows; × takes it off. Chips add up.
 *   data-dt-inspector    an Inspector button: a panel beside the sheet with
 *                        every column of the row you last clicked, and, for
 *                        a mouse or a cage, its rack with its place lit.
 */
(function () {
  'use strict';

  const SVG = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch (_) { return null; } },
    set(key, value) { try { if (value == null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch (_) {} },
  };

  /* ------------------------------------------------------------ a small menu */

  let openPop = null;
  function closePop() {
    if (!openPop) return;
    openPop.anchor.setAttribute('aria-expanded', 'false');
    openPop.menu.remove();
    openPop = null;
  }
  document.addEventListener('pointerdown', (event) => {
    if (openPop && !openPop.menu.contains(event.target) && !openPop.anchor.contains(event.target)) closePop();
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closePop(); });
  window.addEventListener('resize', closePop);

  function popMenu(anchor, fill) {
    if (openPop && openPop.anchor === anchor) { closePop(); return null; }
    closePop();
    const menu = el('div', 'menu dt-pop');
    menu.setAttribute('role', 'menu');
    document.body.appendChild(menu);
    openPop = { anchor, menu };
    anchor.setAttribute('aria-expanded', 'true');
    const place = () => {
      const r = anchor.getBoundingClientRect();
      menu.style.top = Math.min(r.bottom + 6, window.innerHeight - menu.offsetHeight - 8) + 'px';
      menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) + 'px';
    };
    fill(menu, place);
    place();
    return menu;
  }

  function menuItem(label, onPick, extra) {
    const item = el('button', 'menu-item');
    item.type = 'button';
    item.setAttribute('role', 'menuitem');
    item.appendChild(el('span', 'dt-pop-label', label));
    if (extra != null) item.appendChild(el('span', 'dt-pop-count', String(extra)));
    item.addEventListener('click', onPick);
    return item;
  }

  /* ------------------------------------------------------------ filter chips */

  function filters(card, sheet, toolbar) {
    if (!toolbar) return;
    const box = el('div', 'dt-fields');
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', t('Filters'));
    const after = toolbar.querySelector(':scope > .dt-chips') || toolbar.querySelector(':scope > .dt-toolbar-search');
    if (after) after.after(box); else toolbar.prepend(box);

    const draw = () => {
      box.replaceChildren();
      sheet.fields.forEach((field, index) => {
        const chip = el('span', 'dt-chip dt-field-chip');
        chip.appendChild(el('span', 'dt-field-name', field.label || field.key));
        chip.appendChild(el('b', null, field.value === '' ? t('blank') : field.value));
        const off = el('button', 'dt-field-off');
        off.type = 'button';
        off.innerHTML = SVG('x');
        off.setAttribute('aria-label', t('Remove this filter'));
        off.addEventListener('click', () => sheet.setFields(sheet.fields.filter((_, i) => i !== index)));
        chip.appendChild(off);
        box.appendChild(chip);
      });
      const add = el('button', 'dt-chip dt-chip-add');
      add.type = 'button';
      add.setAttribute('aria-haspopup', 'menu');
      add.setAttribute('aria-expanded', 'false');
      add.innerHTML = SVG('plus');
      add.appendChild(document.createTextNode(t('Filter')));
      add.addEventListener('click', () => popMenu(add, (menu, place) => {
        const columns = () => {
          menu.replaceChildren(el('div', 'menu-label', t('Filter by')));
          sheet.columns().forEach((col) => menu.appendChild(menuItem(col.label, () => values(col))));
          place();
        };
        const values = (col) => {
          menu.replaceChildren();
          const back = menuItem('‹ ' + col.label, columns);
          back.classList.add('dt-pop-back');
          menu.appendChild(back);
          const taken = new Set(sheet.fields.filter((f) => f.key === col.key).map((f) => f.value));
          sheet.columnValues(col.key, 40).forEach(({ value, n }) => {
            if (taken.has(value)) return;
            menu.appendChild(menuItem(value === '' ? t('blank') : value, () => {
              closePop();
              sheet.setFields([...sheet.fields, { key: col.key, value, label: col.label }]);
            }, n));
          });
          place();
        };
        columns();
      }));
      box.appendChild(add);
    };
    card.addEventListener('dt:state', draw);
    draw();
  }

  /* ------------------------------------------------------------ saved views */

  function sameState(a, b) {
    const pick = (s) => JSON.stringify([s.query || '', s.chip || '', s.fields || [], s.sort || null]);
    return pick(a) === pick(b);
  }

  function views(card, sheet) {
    const name = (card.dataset.dtViews || sheet.id).replace(/[^A-Za-z0-9_.:-]+/g, '-').slice(0, 30);
    const url = '/sheet-views/' + encodeURIComponent(name);
    const activeKey = `dt:${sheet.id}:view`;
    const strip = el('div', 'dt-views');
    const tabs = el('div', 'dt-views-tabs');
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', t('Saved views'));
    const here = el('div', 'dt-here');
    strip.append(tabs, here);
    card.prepend(strip);

    let saved = [];
    let active = store.get(activeKey) || '';
    const ALL = { query: '', chip: '', fields: [], sort: null };

    const save = () => fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ views: saved }),
    }).then((r) => (r.ok ? r.json() : Promise.reject(r))).then((data) => { saved = data.views || saved; draw(); })
      .catch(() => window.BiomanagerShell && window.BiomanagerShell.toast(t('The view could not be saved.'), 'danger'));

    const choose = (name) => {
      active = name;
      store.set(activeKey, name || null);
      const view = saved.find((v) => v.name === name);
      sheet.applyState(view ? view.state : ALL);
      draw();
    };

    const draw = () => {
      tabs.replaceChildren();
      const current = sheet.state();
      const tab = (label, name, state) => {
        const b = el('button', 'dt-view');
        b.type = 'button';
        b.setAttribute('role', 'tab');
        const on = active === name;
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.textContent = label;
        if (on && !sameState(state, current)) {
          b.classList.add('is-changed');
          b.title = t('Changed since it was saved');
        }
        b.addEventListener('click', () => {
          if (on && name) { viewMenu(b, name); return; }
          choose(name);
        });
        return b;
      };
      tabs.appendChild(tab(t('All'), '', ALL));
      saved.forEach((view) => tabs.appendChild(tab(view.name, view.name, view.state)));
      const add = el('button', 'dt-view dt-view-add');
      add.type = 'button';
      add.innerHTML = SVG('plus');
      add.appendChild(document.createTextNode(t('Save view')));
      add.title = t('Keep this search, these filters, this sort and these columns under a name');
      add.addEventListener('click', () => {
        window.BioDialog.prompt(t('Name this view'), '', { placeholder: t('My breeders, Cohort 2…'), okLabel: t('Save view') }).then((name) => {
          name = (name || '').trim();
          if (!name) return;
          saved = saved.filter((v) => v.name !== name).concat([{ name, state: sheet.state() }]);
          active = name;
          store.set(activeKey, name);
          save();
        });
      });
      tabs.appendChild(add);
    };

    const viewMenu = (anchor, name) => popMenu(anchor, (menu) => {
      const view = saved.find((v) => v.name === name);
      menu.appendChild(menuItem(t('Update to what is shown'), () => {
        closePop();
        view.state = sheet.state();
        save();
      }));
      menu.appendChild(menuItem(t('Rename…'), () => {
        closePop();
        window.BioDialog.prompt(t('Name this view'), name, { okLabel: t('Rename') }).then((next) => {
          next = (next || '').trim();
          if (!next || next === name) return;
          saved = saved.filter((v) => v.name !== next);
          view.name = next;
          active = next;
          store.set(activeKey, next);
          save();
        });
      }));
      const del = menuItem(t('Delete this view'), () => {
        closePop();
        saved = saved.filter((v) => v.name !== name);
        if (active === name) choose('');
        save();
      });
      del.classList.add('menu-item-danger');
      menu.appendChild(del);
    });

    // What is shown changing (a search, a chip, a sort) marks the view changed.
    let pending = 0;
    const later = () => { clearTimeout(pending); pending = setTimeout(draw, 250); };
    card.addEventListener('dt:state', later);
    card.addEventListener('input', (event) => { if (event.target.classList.contains('dt-search')) later(); });
    card.addEventListener('click', (event) => {
      if (event.target.closest('.dt-chip, th.dt-sortable')) later();
    });
    document.addEventListener('click', (event) => { if (event.target.closest('.dt-menu-item, .dt-menu-action')) later(); });

    fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : { views: [] }))
      .then((data) => {
        saved = data.views || [];
        if (active && !saved.some((v) => v.name === active)) { active = ''; store.set(activeKey, null); }
        draw();
      })
      .catch(draw);
    draw();
    presence(here);
  }

  /* ------------------------------------------------------------ who is here */

  const TONES = ['sky', 'violet', 'pink', 'teal', 'amber', 'lime'];
  function tone(name) {
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return TONES[h % TONES.length];
  }
  function initials(person) {
    if (person.short) return person.short.slice(0, 3);
    const parts = (person.name || person.username).split(/[\s._-]+/).filter(Boolean);
    return (parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] || '?').slice(0, 2)).toUpperCase();
  }

  function presence(box) {
    const view = new URLSearchParams(location.search).get('view') || '';
    const body = (left) => JSON.stringify({ path: location.pathname, view, left: left ? 1 : 0 });
    const show = (people) => {
      box.replaceChildren();
      if (!people.length) return;
      const faces = el('span', 'dt-here-faces');
      people.slice(0, 4).forEach((p) => {
        const face = el('span', 'dt-here-face', initials(p));
        face.dataset.tone = tone(p.username);
        face.title = t('%(name)s is here', { name: p.name });
        faces.appendChild(face);
      });
      box.append(faces, el('span', 'dt-here-count', t('%(n)s here', { n: people.length })));
    };
    const beat = () => {
      if (document.hidden) return;
      fetch('/presence', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: body(false) })
        .then((r) => (r.ok ? r.json() : null)).then((data) => data && show(data.here || [])).catch(() => {});
    };
    beat();
    setInterval(beat, 30000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) beat(); });
    window.addEventListener('pagehide', () => {
      try { navigator.sendBeacon('/presence', new Blob([body(true)], { type: 'application/json' })); } catch (_) {}
    });
  }

  /* ------------------------------------------------------------ inspector */

  let racksPromise = null;
  function racks() {
    if (!racksPromise) {
      racksPromise = fetch('/colony/cages/grid?scope=all', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : null)).catch(() => null);
    }
    return racksPromise;
  }

  function payloadOf(tr) {
    const holder = tr.querySelector('[data-mouse-payload], [data-record-payload]');
    if (!holder) return null;
    try { return JSON.parse(holder.dataset.mousePayload || holder.dataset.recordPayload); } catch (_) { return null; }
  }

  function cellShown(td) {
    const pill = td.querySelector('.sex-pill, .status-pill, .scope-pill, .owner-badge, .avatar');
    if (pill) {
      // A pill keeps its colour: the same class and data-* on a plain span.
      const kind = ['sex-pill', 'status-pill', 'scope-pill'].find((c) => pill.classList.contains(c));
      if (!kind) return pill.cloneNode(true);
      const text = pill.tagName === 'SELECT' ? ((pill.selectedOptions[0] || {}).textContent || '') : pill.textContent;
      const span = el('span', kind, text.trim());
      Object.entries(pill.dataset).forEach(([k, v]) => { if (!['field', 'form', 'sortFor', 'sortValue'].includes(k)) span.dataset[k] = v; });
      if (pill.tagName === 'SELECT' && kind === 'status-pill' && !span.dataset.status) span.dataset.status = pill.value;
      return span;
    }
    const field = td.querySelector('select, input:not([type=checkbox]), textarea');
    if (field) return document.createTextNode(field.tagName === 'SELECT' ? ((field.selectedOptions[0] || {}).textContent || '') : field.value);
    return document.createTextNode(td.textContent.replace(/\s+/g, ' ').trim());
  }

  function inspector(card, sheet, toolbar) {
    if (!toolbar) return;
    const key = `dt:${sheet.id}:inspector`;
    const kind = card.dataset.dtInspector;
    const button = el('button', 'dt-toolbar-btn dt-btn-inspect');
    button.type = 'button';
    button.innerHTML = SVG('panel-right');
    button.appendChild(document.createTextNode(' ' + t('Inspector')));
    button.title = t('Show the row you click, in full, beside the sheet');
    const spacer = toolbar.querySelector(':scope > .dt-toolbar-spacer');
    (spacer ? spacer.nextElementSibling : null) ? spacer.after(button) : toolbar.appendChild(button);

    const wrap = el('div', 'dt-inspect-wrap');
    card.before(wrap);
    wrap.appendChild(card);
    const panel = el('aside', 'dt-inspector panel-thick');
    panel.setAttribute('aria-label', t('Inspector'));
    wrap.appendChild(panel);
    let row = null;

    const open = (on) => {
      wrap.classList.toggle('is-open', on);
      button.classList.toggle('is-active', on);
      button.setAttribute('aria-pressed', on ? 'true' : 'false');
      panel.hidden = !on;
      store.set(key, on ? '1' : null);
      if (on) draw();
    };

    const draw = () => {
      if (!wrap.classList.contains('is-open')) return;
      if (!row || !row.isConnected || row.style.display === 'none') {
        row = sheet.filtered.find((tr) => !tr._stub && tr.isConnected) || null;
      }
      panel.replaceChildren();
      const close = el('button', 'dt-inspector-close');
      close.type = 'button';
      close.innerHTML = SVG('x');
      close.setAttribute('aria-label', t('Close the inspector'));
      close.addEventListener('click', () => open(false));
      panel.appendChild(close);
      if (!row) { panel.appendChild(el('p', 'dt-inspector-empty', t('Click a row to see it here.'))); return; }
      sheet.rows.forEach((tr) => tr.classList.toggle('is-inspected', tr === row));
      const idCell = row.querySelector('td.sheet-pin-1') || row.cells[1] || row.cells[0];
      const head = el('div', 'dt-inspector-head');
      head.appendChild(el('div', 'dt-inspector-kicker', (sheet.headers[1] && sheet._columnLabel(sheet.headers[1])) || ''));
      head.appendChild(el('div', 'dt-inspector-title', idCell ? idCell.textContent.replace(/\s+/g, ' ').trim() : ''));
      panel.appendChild(head);

      const opener = row.querySelector('.sheet-actions [data-mouse-edit], .sheet-actions [data-record-edit], .sheet-actions button[title]');
      if (opener) {
        const edit = el('button', 'btn btn-sm');
        edit.type = 'button';
        edit.innerHTML = SVG('edit');
        edit.appendChild(document.createTextNode(' ' + t('Open')));
        edit.addEventListener('click', () => opener.click());
        head.appendChild(edit);
      }

      const list = el('dl', 'dt-inspector-fields');
      Array.from(row.cells).forEach((td) => {
        const col = sheet._col(td);
        const th = sheet.headers[col >= 0 ? col : td.cellIndex];
        if (!th || th.classList.contains('sheet-pin-0') || th.classList.contains('sheet-pin-1')
            || th.classList.contains('sheet-pin-end') || th.dataset.dtOff === '1') return;
        const label = sheet._columnLabel(th);
        if (!label) return;
        const shown = cellShown(td);
        if (!shown.textContent.trim() || shown.textContent.trim() === '–') return;
        list.append(el('dt', null, label));
        const dd = el('dd');
        dd.appendChild(shown);
        list.append(dd);
      });
      panel.appendChild(list);

      if (kind === 'mice' || kind === 'cages') rackMap(panel, row);
    };

    const rackMap = (panel, tr) => {
      const data = payloadOf(tr) || {};
      const rackId = data.cage_rack || data.rack_id;
      const cage = String(data.cage_id || data._label || '');
      if (!rackId) return;
      const box = el('section', 'dt-inspector-rack');
      panel.appendChild(box);
      racks().then((grid) => {
        if (!grid || tr !== row) return;
        const rack = (grid.racks || []).find((r) => String(r.id) === String(rackId));
        if (!rack) return;
        box.appendChild(el('div', 'dt-inspector-label', rack.name + (data.cage_position ? ' · ' + data.cage_position : (data.position ? ' · ' + data.position : ''))));
        const cells = el('div', 'dt-rack-mini');
        cells.style.gridTemplateColumns = `repeat(${rack.cols}, minmax(0, 1fr))`;
        const at = new Map();
        (grid.items || []).forEach((item) => {
          if (String(item.rack) !== String(rack.id) || !item.row || !item.col) return;
          const k = `${item.row}:${item.col}`;
          if (!at.has(k) || item.live) at.set(k, item);
        });
        for (let r = 1; r <= rack.rows; r += 1) {
          for (let c = 1; c <= rack.cols; c += 1) {
            const item = at.get(`${r}:${c}`);
            const cell = el(item ? 'a' : 'span', 'dt-rack-cell', item ? item.label : '');
            if (item) {
              cell.href = `/colony?view=cages#cage-${item.id}`;
              cell.title = item.title || item.label;
              cell.classList.add(item.live ? 'is-live' : 'is-empty-cage');
              if (item.label === cage) cell.classList.add('is-here');
            }
            cells.appendChild(cell);
          }
        }
        box.appendChild(cells);
      });
    };

    button.addEventListener('click', () => open(!wrap.classList.contains('is-open')));
    const pick = (event) => {
      const tr = event.target.closest && event.target.closest('tbody tr[data-id]');
      if (!tr || tr === row || !card.contains(tr)) return;
      row = tr;
      draw();
    };
    card.addEventListener('click', pick);
    card.addEventListener('focusin', pick);
    document.addEventListener('sheet:saved', () => { if (wrap.classList.contains('is-open')) draw(); });
    card.addEventListener('dt:state', () => draw());
    open(store.get(key) === '1');
  }

  /* ------------------------------------------------------------ setup */

  function setup(card) {
    const sheet = window.BioSheets && window.BioSheets.sheet(card);
    if (!sheet || card.dataset.dtExtras === '1') return;
    card.dataset.dtExtras = '1';
    const toolbar = card.querySelector(':scope > .dt-toolbar');
    if (card.hasAttribute('data-dt-views')) {
      views(card, sheet);
      filters(card, sheet, toolbar);
    }
    if (card.hasAttribute('data-dt-inspector')) inspector(card, sheet, toolbar);
  }

  function init() { document.querySelectorAll('.data-table-card[data-dt-views], .data-table-card[data-dt-inspector]').forEach(setup); }
  // After data-table.js has made each sheet (it does on DOMContentLoaded).
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(init));
  else setTimeout(init);
})();
