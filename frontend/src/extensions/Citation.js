// A paper cited in a sentence, as a paper is: (Smith et al., 2024). It is
// one piece, linked to the paper's note in Reading; the words are worked out
// by the server from the note's authors and year (lab_notebook.citing), so
// a note corrected later corrects every citation of it. Hovering shows the
// full reference; clicking opens the note.
//
// In the Markdown it stays a link, [(Smith et al., 2024)](/notebook?page=12&cite=1),
// so search, export and "Linked from" read it, and a ```references block
// (blocks/references.js) lists every paper the page cites.
//
// "[@" opens Cite a paper where you type; /cite does the same.

import { InputRule, Node, mergeAttributes } from '@tiptap/core';
import { api, escapeHtml, openInApp } from '../util.js';

const HREF = /^(?:https?:\/\/[^/]+)?\/notebook\?page=(\d+)&cite=1$/;
export const citeUrl = (id) => `/notebook?page=${id}&cite=1`;
export const isCiteHref = (href) => HREF.test(href || '');
const t = (s, v) => (typeof window !== 'undefined' && window.t ? window.t(s, v) : s);

// ---------------------------------------------------------------- what each paper is cited as

const known = new Map();      // id -> { cite, ref, title } or null
const waiting = new Map();
let batch = null;

function flush() {
  const ids = [...waiting.keys()];
  const callbacks = new Map(waiting);
  waiting.clear();
  batch = null;
  fetch(`/notebook/api/pages/titles?ids=${ids.join(',')}`, { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : { pages: {} }))
    .catch(() => ({ pages: {} }))
    .then((data) => {
      for (const id of ids) {
        const page = (data.pages || {})[id] || null;
        const paper = page && page.cite !== undefined ? { cite: page.cite, ref: page.ref, title: page.title } : null;
        known.set(id, paper);
        (callbacks.get(id) || []).forEach((done) => done(paper));
      }
    });
}

export function paperInfo(id) {
  const key = String(id);
  if (known.has(key)) return Promise.resolve(known.get(key));
  return new Promise((resolve) => {
    if (!waiting.has(key)) waiting.set(key, []);
    waiting.get(key).push(resolve);
    if (!batch) batch = setTimeout(flush, 30);
  });
}

// ---------------------------------------------------------------- the node

export const Citation = Node.create({
  name: 'citation',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      id: { default: null, parseHTML: (el) => (el.getAttribute('href') || '').match(HREF)?.[1] || el.getAttribute('data-cite') },
      label: { default: '', parseHTML: (el) => (el.textContent || '').replace(/^\(|\)$/g, '') },
    };
  },

  // Above PageLink (1000) and the link mark: a cited paper reads as this.
  parseHTML() {
    return [{ tag: 'a[href]', priority: 1001, getAttrs: (el) => (HREF.test(el.getAttribute('href') || '') ? null : false) }];
  },

  renderHTML({ node, HTMLAttributes }) {
    return ['a', mergeAttributes(HTMLAttributes, { href: citeUrl(node.attrs.id), class: 'nb-cite', 'data-cite': node.attrs.id }),
      `(${node.attrs.label || '?'})`];
  },

  renderText({ node }) { return `(${node.attrs.label || '?'})`; },

  addStorage() {
    return {
      markdown: {
        serialize(state, node) {
          const label = String(node.attrs.label || '?').replace(/[\\[\]]/g, '\\$&');
          state.write(`[(${label})](${citeUrl(node.attrs.id)})`);
        },
        parse: {},
      },
    };
  },

  addNodeView() {
    return ({ node, editor, getPos }) => {
      let current = node;
      const dom = document.createElement('a');
      dom.className = 'nb-cite';
      dom.href = citeUrl(node.attrs.id);
      dom.contentEditable = 'false';
      const paint = (label, ref, missing) => {
        dom.textContent = `(${label || '?'})`;
        dom.title = missing ? t('A paper note you cannot open, or one that was deleted') : (ref || '');
        dom.classList.toggle('is-missing', !!missing);
      };
      paint(node.attrs.label, '', false);
      const refresh = () => paperInfo(current.attrs.id).then((paper) => {
        if (!paper) { paint(current.attrs.label, '', true); return; }
        paint(paper.cite, paper.ref, false);
        // Keep the Markdown's words what the note says now.
        if (editor.isEditable && paper.cite && paper.cite !== current.attrs.label && typeof getPos === 'function') {
          const pos = getPos();
          const here = pos != null && editor.state.doc.nodeAt(pos);
          if (here && here.type.name === 'citation' && here.attrs.id === current.attrs.id) {
            editor.view.dispatch(editor.state.tr.setNodeMarkup(pos, undefined, { ...here.attrs, label: paper.cite }).setMeta('addToHistory', false));
          }
        }
      });
      refresh();
      dom.addEventListener('click', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        if (e.metaKey || e.ctrlKey) { window.open(`/notebook?page=${current.attrs.id}`, '_blank', 'noopener'); return; }
        openInApp(`/notebook?page=${current.attrs.id}`, editor);
      });
      return {
        dom,
        update(next) {
          if (next.type.name !== 'citation') return false;
          const moved = next.attrs.id !== current.attrs.id;
          current = next;
          dom.href = citeUrl(next.attrs.id);
          if (moved) refresh(); else paint(known.get(String(next.attrs.id))?.cite || next.attrs.label, known.get(String(next.attrs.id))?.ref, false);
          return true;
        },
        stopEvent: (e) => e.type === 'click',
        ignoreMutation: () => true,
      };
    };
  },

  addInputRules() {
    return [
      // "[@" — cite a paper right where you type.
      new InputRule({
        find: /\[@$/,
        handler: ({ state, range }) => {
          state.tr.delete(range.from, range.to);
          const editor = this.editor;
          setTimeout(() => openCitePicker(editor), 0);
        },
      }),
    ];
  },
});

export function insertCitation(editor, paper) {
  editor.chain().focus().insertContent({ type: 'citation', attrs: { id: paper.id, label: paper.cite } }).run();
  known.set(String(paper.id), { cite: paper.cite, ref: paper.ref, title: paper.title });
}

// ---------------------------------------------------------------- Cite a paper

export function openCitePicker(editor) {
  document.querySelectorAll('.nb-pop').forEach((p) => p.remove());
  const coords = (() => { try { return editor.view.coordsAtPos(editor.state.selection.from); } catch (_e) { return null; } })();
  const pop = document.createElement('div');
  pop.className = 'nb-pop nb-cite-picker';
  pop.innerHTML = `
    <div class="nb-pop-label">${escapeHtml(t('Cite a paper'))}</div>
    <input type="search" class="field nb-pop-search" placeholder="${escapeHtml(t('Title, author, year, journal or DOI'))}" aria-label="${escapeHtml(t('Find a paper'))}">
    <div class="nb-pop-list"><div class="insert-empty">${escapeHtml(t('Loading…'))}</div></div>`;
  document.body.appendChild(pop);
  const r = coords || { left: window.innerWidth / 2 - 160, top: window.innerHeight / 3, bottom: window.innerHeight / 3 };
  const box = pop.getBoundingClientRect();
  const below = r.bottom + 6 + box.height < window.innerHeight;
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - box.width - 8, r.left)) + window.scrollX}px`;
  pop.style.top = `${(below ? r.bottom + 6 : Math.max(8, r.top - box.height - 6)) + window.scrollY}px`;
  const input = pop.querySelector('input');
  const list = pop.querySelector('.nb-pop-list');
  let results = [];
  let active = 0;
  let seq = 0;
  const close = () => { pop.remove(); document.removeEventListener('mousedown', away); };
  const away = (e) => { if (!pop.contains(e.target)) close(); };
  setTimeout(() => document.addEventListener('mousedown', away), 0);
  const mark = () => pop.querySelectorAll('.nb-pop-row').forEach((row, i) => row.classList.toggle('is-active', i === active));
  const load = async () => {
    const mine = ++seq;
    try {
      const data = await api(`/notebook/api/cite-search?q=${encodeURIComponent(input.value.trim())}`);
      if (mine !== seq) return;
      results = data.papers || [];
      list.innerHTML = results.length
        ? results.map((p, i) => `<button type="button" class="nb-pop-row" data-i="${i}"><b>${escapeHtml(p.cite)}</b><small>${escapeHtml(p.title)}</small></button>`).join('')
        : `<div class="insert-empty">${escapeHtml(t('No paper note matches. Papers come from Reading: a Paper notes page, or Zotero.'))}</div>`;
    } catch (_e) {
      list.innerHTML = `<div class="insert-empty">${escapeHtml(t('Papers could not be loaded.'))}</div>`;
    }
    active = 0;
    mark();
  };
  const pick = (paper) => { close(); insertCitation(editor, paper); };
  let timer = null;
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 200); });
  input.addEventListener('keydown', (e) => {
    const rows = pop.querySelectorAll('.nb-pop-row');
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && rows.length) {
      e.preventDefault();
      active = (active + (e.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length;
      mark();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (rows[active]) rows[active].click();
    } else if (e.key === 'Escape') {
      close();
      editor.commands.focus();
    }
  });
  list.addEventListener('click', (e) => {
    const row = e.target.closest('.nb-pop-row');
    if (row) pick(results[Number(row.dataset.i)]);
  });
  load();
  input.focus();
}
