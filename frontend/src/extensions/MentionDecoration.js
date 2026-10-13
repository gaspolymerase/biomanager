// Inline @mouse 123 / @plasmid 4 / @order 7 / @antibodies 12 → styled chip via ProseMirror
// decorations. The text stays as plain markdown source — only visual styling
// is added on matching ranges. Click → navigate, hover → custom rich popover
// fetched from /notebook/lookup/<type>/<id>. No schema changes, no markdown
// serializer changes, so save/reload round-trips are stable.

import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

// The types are mouse, plasmid, order and every inventory (mentionTypes.js).
// The backend exposes /notebook/lookup/<type>/<id> and /notebook/search/<type>
// for hover info and the suggestion dropdown, for each of them.
import { mentionRe, mentionTypes, styleOf } from './mentionTypes.js';

// The record itself (app.py notebook_open_mention redirects to its page).
const navigation = (type, id) => `/notebook/open/${type}/${id}`;

// Per-page cache so multiple hovers on the same chip skip the network.
const lookupCache = new Map(); // key: `${type}:${id}` -> data
const backlinkCache = new Map(); // key: `${type}:${id}` -> { items, count }

// Singleton popover element. One per page is enough since only one chip can
// be hovered at a time. Lazy-created on first hover.
let popoverEl = null;
let popoverHideTimer = null;
let popoverActiveChip = null;

/* Records open in a new tab of BioManager's own tab strip (static/tab-bar.js),
   not a new browser tab or window, so the notebook stays one click away. */
function openInAppTab(url) {
  if (window.BiomanagerTabs && window.BiomanagerTabs.open) window.BiomanagerTabs.open(url);
  else window.location.href = url;
}

function ensurePopover() {
  if (popoverEl) return popoverEl;
  popoverEl = document.createElement('div');
  popoverEl.className = 'entity-popover';
  popoverEl.style.display = 'none';
  // Keep the popover open while the user moves the mouse onto it.
  popoverEl.addEventListener('mouseenter', () => {
    if (popoverHideTimer) clearTimeout(popoverHideTimer);
  });
  popoverEl.addEventListener('mouseleave', () => {
    schedulePopoverHide();
  });
  popoverEl.addEventListener('click', (event) => {
    const open = event.target.closest && event.target.closest('.entity-popover-open');
    if (!open) return;
    event.preventDefault();
    openInAppTab(open.getAttribute('href'));
  });
  document.body.appendChild(popoverEl);
  return popoverEl;
}

function schedulePopoverHide() {
  if (popoverHideTimer) clearTimeout(popoverHideTimer);
  popoverHideTimer = setTimeout(() => {
    if (popoverEl) popoverEl.style.display = 'none';
    popoverActiveChip = null;
  }, 120);
}

function positionPopover(chip) {
  const rect = chip.getBoundingClientRect();
  const popRect = popoverEl.getBoundingClientRect();
  // Default: below the chip. If that overflows the viewport, place above.
  const margin = 6;
  let top = rect.bottom + window.scrollY + margin;
  let left = rect.left + window.scrollX;
  if (top + popRect.height > window.scrollY + window.innerHeight - 8) {
    top = rect.top + window.scrollY - popRect.height - margin;
  }
  if (left + popRect.width > window.scrollX + window.innerWidth - 8) {
    left = Math.max(8, window.scrollX + window.innerWidth - popRect.width - 8);
  }
  popoverEl.style.top = `${top}px`;
  popoverEl.style.left = `${left}px`;
}

function renderPopover(type, id, data, errorMsg) {
  const el = ensurePopover();
  const navUrl = navigation(type, id);
  const typeLabel = mentionTypes().labels[type] || type;
  const tag = styleOf(type);

  // A database's name is the lab's own words: text, never markup.
  if (errorMsg) {
    el.innerHTML = `
      <div class="entity-popover-head">
        <span class="entity-popover-tag entity-popover-tag-${tag}">${escapeHtml(typeLabel)}</span>
        <span class="entity-popover-id">#${escapeHtml(String(id))}</span>
      </div>
      <div class="entity-popover-body">${escapeHtml(errorMsg)}</div>
    `;
    return;
  }

  if (!data) {
    el.innerHTML = `
      <div class="entity-popover-head">
        <span class="entity-popover-tag entity-popover-tag-${tag}">${escapeHtml(typeLabel)}</span>
        <span class="entity-popover-id">#${escapeHtml(String(id))}</span>
      </div>
      <div class="entity-popover-body entity-popover-loading">Loading…</div>
    `;
    return;
  }

  // Build a key→value list using a type-specific field map. Fields that come
  // back null/empty are skipped.
  let rows = '';
  if (type === 'mouse') {
    const fields = [
      ['Gender', data.gender],
      ['Age', data.age_weeks == null ? '' : `${data.age_weeks} weeks · ${data.age_days} days`],
      ['Genotype', data.genotype],
      ['Status', data.status],
      ['Owner', data.owner],
      ['Cage', data.cage_id],
    ];
    rows = fields.filter(([_, v]) => v).map(([k, v]) => row(k, v)).join('');
  } else if (type === 'plasmid') {
    const fields = [
      ['Name', data.name],
      ['Backbone', data.backbone],
      ['Insert', data.insert_seq],
      ['Resistance', data.resistance],
      ['Owner', data.owner],
      ['Location', data.location],
    ];
    rows = fields.filter(([_, v]) => v).map(([k, v]) => row(k, v)).join('');
  } else if (type === 'order') {
    const fields = [
      ['Vendor', data.vendor_name],
      ['Item', data.item_name],
      ['Catalog #', data.catalog_number],
      ['Qty', data.quantity],
      ['Status', data.status],
      ['Requester', data.requester_name],
    ];
    rows = fields.filter(([_, v]) => v).map(([k, v]) => row(k, v)).join('');
  } else if (Array.isArray(data.fields)) {
    // An inventory record: the server sends the fields worth showing.
    rows = data.fields.map(([k, v]) => row(k, v)).join('');
  }

  el.innerHTML = `
    <div class="entity-popover-head">
      <span class="entity-popover-tag entity-popover-tag-${tag}">${escapeHtml(typeLabel)}</span>
      <span class="entity-popover-id">#${escapeHtml(String(id))}</span>
      <a href="${escapeAttr(navUrl)}" class="entity-popover-open" title="Open in a new tab">↗</a>
    </div>
    <div class="entity-popover-body">${rows || '<div class="entity-popover-empty">No details available.</div>'}</div>
    <div class="entity-popover-backlinks" data-loading="1">
      <div class="entity-popover-backlinks-loading">Loading mentions…</div>
    </div>
    <div class="entity-popover-hint">Cmd / Ctrl + click the chip to open it in a new tab</div>
  `;
}

function renderBacklinks(type, id, items) {
  const section = popoverEl && popoverEl.querySelector('.entity-popover-backlinks');
  if (!section) return;
  if (!items || items.length === 0) {
    section.innerHTML = '<div class="entity-popover-backlinks-empty">No notebook pages mention this yet.</div>';
    return;
  }
  const rows = items.slice(0, 5).map((item) => {
    const href = `/notebook?tab=${item.tab_id}&page=${item.page_id}`;
    return `
      <a href="${escapeAttr(href)}" class="entity-popover-backlink-row" title="${escapeAttr(item.snippet || '')}">
        <span class="entity-popover-backlink-title">${escapeHtml(item.page_title)}</span>
        <span class="entity-popover-backlink-snippet">${escapeHtml(item.snippet || '')}</span>
      </a>
    `;
  }).join('');
  const more = items.length > 5 ? `<div class="entity-popover-backlinks-more">+${items.length - 5} more</div>` : '';
  section.innerHTML = `
    <div class="entity-popover-backlinks-head">Mentioned in ${items.length} ${items.length === 1 ? 'page' : 'pages'}</div>
    <div class="entity-popover-backlinks-list">${rows}${more}</div>
  `;
  // Reposition since the popover may have grown taller.
  if (popoverActiveChip) positionPopover(popoverActiveChip);
}

function loadBacklinks(type, id, chip) {
  const cacheKey = `${type}:${id}`;
  const cached = backlinkCache.get(cacheKey);
  if (cached) {
    if (popoverActiveChip === chip) renderBacklinks(type, id, cached.items);
    return;
  }
  fetch(`/notebook/backlinks/${type}/${id}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data || !data.ok) return;
      backlinkCache.set(cacheKey, { items: data.items });
      if (popoverActiveChip === chip) renderBacklinks(type, id, data.items);
    })
    .catch(() => {});
}

function row(key, value) {
  return `<div class="entity-popover-row"><span class="entity-popover-key">${escapeHtml(key)}</span><span class="entity-popover-val">${escapeHtml(String(value))}</span></div>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function escapeAttr(s) { return escapeHtml(s); }

function showPopoverFor(chip) {
  const type = chip.getAttribute('data-entity-type');
  const id = chip.getAttribute('data-entity-id');
  if (!type || !id) return;
  if (popoverHideTimer) {
    clearTimeout(popoverHideTimer);
    popoverHideTimer = null;
  }
  popoverActiveChip = chip;
  const el = ensurePopover();
  el.style.display = 'block';

  const cacheKey = `${type}:${id}`;
  const cached = lookupCache.get(cacheKey);
  if (cached && cached.data) {
    renderPopover(type, id, cached.data, null);
    positionPopover(chip);
    loadBacklinks(type, id, chip);
    return;
  }
  if (cached && cached.error) {
    renderPopover(type, id, null, cached.error);
    positionPopover(chip);
    return;
  }

  // Show loading state immediately, then fetch.
  renderPopover(type, id, null, null);
  positionPopover(chip);
  // Backlinks load in parallel; they're rendered into the same popover.
  loadBacklinks(type, id, chip);
  fetch(`/notebook/lookup/${type}/${id}`)
    .then((r) => {
      if (r.status === 404) {
        const err = `Not found in the database.`;
        lookupCache.set(cacheKey, { error: err });
        if (popoverActiveChip === chip) {
          renderPopover(type, id, null, err);
          positionPopover(chip);
        }
        return null;
      }
      return r.ok ? r.json() : null;
    })
    .then((data) => {
      if (!data) return;
      if (data.ok) {
        lookupCache.set(cacheKey, { data });
        if (popoverActiveChip === chip) {
          renderPopover(type, id, data, null);
          positionPopover(chip);
        }
      } else {
        const err = data.error || 'Lookup failed.';
        lookupCache.set(cacheKey, { error: err });
        if (popoverActiveChip === chip) {
          renderPopover(type, id, null, err);
          positionPopover(chip);
        }
      }
    })
    .catch(() => {
      const err = 'Network error.';
      if (popoverActiveChip === chip) renderPopover(type, id, null, err);
    });
}

/* A chip reads "@antibodies 6 · Anti-p53 (DO-1)": the record's name after
   its number, from the same lookup as the popover. The page's text stays
   "@antibodies 6"; the name is only shown (styles.css, ::after). */
const nameCache = new Map(); // `${type}:${id}` -> name ('' when it has none)
const nameWaiting = new Set();

function nameOf(type, data) {
  if (!data) return '';
  if (type === 'plasmid') return data.name || '';
  if (type === 'order') return data.item_name || '';
  return data.name || '';
}

function fetchNames(doc, view, key) {
  const wanted = [];
  doc.descendants((node) => {
    if (!node.isText || !node.text) return;
    const re = mentionRe();
    let match;
    while ((match = re.exec(node.text)) !== null) {
      const k = `${match[1]}:${match[2]}`;
      if (match[1] !== 'mouse' && !nameCache.has(k) && !nameWaiting.has(k)) wanted.push([match[1], match[2], k]);
    }
  });
  wanted.slice(0, 40).forEach(([type, id, k]) => {
    nameWaiting.add(k);
    fetch(`/notebook/lookup/${type}/${id}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        nameCache.set(k, data && data.ok ? nameOf(type, data) : '');
        if (data && data.ok) lookupCache.set(k, { data });
      })
      .catch(() => nameCache.set(k, ''))
      .finally(() => {
        nameWaiting.delete(k);
        if (nameCache.get(k) && !view.isDestroyed) view.dispatch(view.state.tr.setMeta(key, 'names'));
      });
  });
}

function buildDecorations(doc) {
  const decos = [];
  doc.descendants((node, pos) => {
    if (!node.isText || !node.text) return;
    const text = node.text;
    const re = mentionRe();
    let match;
    while ((match = re.exec(text)) !== null) {
      const from = pos + match.index;
      const to = from + match[0].length;
      const name = nameCache.get(`${match[1]}:${match[2]}`);
      decos.push(
        Decoration.inline(from, to, {
          class: `entity-mention entity-mention-${styleOf(match[1])}`,
          'data-entity-type': match[1],
          'data-entity-id': match[2],
          ...(name ? { 'data-entity-name': name.length > 40 ? `${name.slice(0, 39)}…` : name } : {}),
        })
      );
    }
  });
  return DecorationSet.create(doc, decos);
}

export const MentionDecoration = Extension.create({
  name: 'mentionDecoration',

  addProseMirrorPlugins() {
    const key = new PluginKey('mentionDecoration');
    return [
      new Plugin({
        key,
        state: {
          init(_config, { doc }) {
            return buildDecorations(doc);
          },
          apply(tr, old) {
            return tr.docChanged || tr.getMeta(key) === 'names' ? buildDecorations(tr.doc) : old;
          },
        },
        view(editorView) {
          fetchNames(editorView.state.doc, editorView, key);
          return {
            update(view, prev) {
              if (!view.state.doc.eq(prev.doc)) fetchNames(view.state.doc, view, key);
            },
          };
        },
        props: {
          decorations(state) {
            return key.getState(state);
          },
          handleClick(view, _pos, event) {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return false;
            const chip = target.closest('.entity-mention');
            if (!chip) return false;
            if (!(event.metaKey || event.ctrlKey)) return false;
            openInAppTab(navigation(chip.getAttribute('data-entity-type'), chip.getAttribute('data-entity-id')));
            event.preventDefault();
            return true;
          },
          handleDOMEvents: {
            mouseover(_view, event) {
              const target = event.target;
              if (!(target instanceof HTMLElement)) return false;
              const chip = target.closest('.entity-mention');
              if (!chip) return false;
              showPopoverFor(chip);
              return false;
            },
            mouseout(_view, event) {
              const target = event.target;
              if (!(target instanceof HTMLElement)) return false;
              const chip = target.closest('.entity-mention');
              if (!chip) return false;
              // Don't hide if the user is moving from the chip onto the
              // popover (or back). The popover's own mouseleave handler
              // will eventually hide it.
              const related = event.relatedTarget;
              if (related instanceof Node && popoverEl && popoverEl.contains(related)) return false;
              schedulePopoverHide();
              return false;
            },
          },
        },
      }),
    ];
  },
});
