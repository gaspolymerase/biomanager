// Links between notebook pages, as Notion makes them. A link to a page is
// one piece, not editable text: the page's icon and its title as it is now,
// so renaming a page renames every link to it. Alone on its line it shows
// as a page in this one (what /page makes); in a sentence, as a chip.
// Clicking it opens the page in a BioManager tab.
//
// In the Markdown it stays an ordinary link, [Title](/notebook?page=12), so
// search, export and "Linked from" read it.
//
// A citation (/cite) is the same link to a paper's reading note, showing
// the paper as papers cite it, "(Wang et al., 2024)", from the note's
// Authors and Year as they are now (../cite.js). Its Markdown is a link too,
// [(Wang et al., 2024)](/notebook?page=12&cite=1).
//
// Also here: "[[" opens the page search where you type, and every other
// link opens in a BioManager tab (pages of the app) or the browser (the web).

import { InputRule, Node, mergeAttributes } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { citeText } from '../cite.js';
import { ICONS } from '../icons.js';
import { openPagePicker } from '../rich-menus.js';
import { openInApp } from '../util.js';

const HREF = /^(?:https?:\/\/[^/]+)?\/notebook\?page=(\d+)(?:[&#].*)?$/;
const CITE = /[?&]cite=1(?:[&#]|$)/;
export const pageUrl = (id) => `/notebook?page=${id}`;
const linkUrl = (attrs) => (attrs.cite ? `${pageUrl(attrs.id)}&cite=1` : pageUrl(attrs.id));

// What a link shows: the page's title, or for a citation the paper's
// author and year.
const shown = (attrs, page) => (attrs.cite ? citeText(page.fields || {}, page.title) : page.title);


// ---------------------------------------------------------------- live titles

const known = new Map();      // id -> { title, kind, fields } or null (no access / gone)
const waiting = new Map();    // id -> [resolve]
let batch = null;

function flushBatch() {
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
        known.set(id, page);
        (callbacks.get(id) || []).forEach((done) => done(page));
      }
    });
}

function pageInfo(id, fresh = false) {
  const key = String(id);
  if (!fresh && known.has(key)) return Promise.resolve(known.get(key));
  return new Promise((resolve) => {
    if (!waiting.has(key)) waiting.set(key, []);
    waiting.get(key).push(resolve);
    if (!batch) batch = setTimeout(flushBatch, 30);
  });
}

// Seen again after a while (back from the sub-page just renamed): read again.
let readAt = Date.now();
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - readAt > 2000) {
      known.clear();
      readAt = Date.now();
      window.dispatchEvent(new CustomEvent('nb:page-titles'));
    }
  });
}

// ---------------------------------------------------------------- the node

const escapeLabel = (s) => String(s || 'Untitled page').replace(/[\\[\]]/g, '\\$&').replace(/\n/g, ' ');

export const PageLink = Node.create({
  name: 'pageLink',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: true,

  addAttributes() {
    return {
      id: { default: null, parseHTML: (el) => (el.getAttribute('href') || '').match(HREF)?.[1] || el.getAttribute('data-page') },
      title: { default: '', parseHTML: (el) => el.textContent || '' },
      cite: { default: false, parseHTML: (el) => CITE.test(el.getAttribute('href') || '') },
    };
  },

  // Above the link mark, so a link to a page reads as one of these.
  parseHTML() {
    return [{ tag: 'a[href]', priority: 1000, getAttrs: (el) => (HREF.test(el.getAttribute('href') || '') ? null : false) }];
  },

  renderHTML({ node, HTMLAttributes }) {
    const { cite: _cite, ...attrs } = HTMLAttributes;
    return ['a', mergeAttributes(attrs, { href: linkUrl(node.attrs), class: node.attrs.cite ? 'nb-pagelink nb-cite' : 'nb-pagelink', 'data-page': node.attrs.id }), node.attrs.title || 'Untitled page'];
  },

  renderText({ node }) { return node.attrs.title || 'Untitled page'; },

  addStorage() {
    return {
      markdown: {
        serialize(state, node) {
          state.write(`[${escapeLabel(node.attrs.title)}](${linkUrl(node.attrs)})`);
        },
        parse: {},
      },
    };
  },

  addNodeView() {
    return ({ node, editor, getPos }) => {
      let current = node;
      const dom = document.createElement('a');
      dom.className = node.attrs.cite ? 'nb-pagelink nb-cite' : 'nb-pagelink';
      dom.href = pageUrl(node.attrs.id);
      dom.contentEditable = 'false';
      dom.draggable = false;
      dom.innerHTML = node.attrs.cite ? '<span class="nb-pagelink-title"></span>'
        : `<span class="nb-pagelink-icon">${ICONS.page}</span><span class="nb-pagelink-title"></span>`;
      const label = dom.querySelector('.nb-pagelink-title');
      const paint = (title, missing) => {
        label.textContent = title || 'Untitled page';
        dom.classList.toggle('is-missing', !!missing);
        dom.title = missing ? 'A page you cannot open, or one that was deleted' : (current.attrs.cite ? hint : '');
      };
      let hint = '';
      paint(node.attrs.title, false);

      const refresh = (fresh) => pageInfo(current.attrs.id, fresh).then((page) => {
        if (!page) { paint(current.attrs.title, true); return; }
        const words = shown(current.attrs, page);
        hint = page.title;
        paint(words, false);
        // Keep the Markdown's words the page's title (a citation's, the
        // paper's author and year), for search and export.
        if (editor.isEditable && words !== current.attrs.title && typeof getPos === 'function') {
          const pos = getPos();
          const here = pos != null && editor.state.doc.nodeAt(pos);
          if (here && here.type.name === 'pageLink' && here.attrs.id === current.attrs.id) {
            editor.view.dispatch(editor.state.tr.setNodeMarkup(pos, undefined, { ...here.attrs, title: words }).setMeta('addToHistory', false));
          }
        }
      });
      const onTitles = () => refresh(false);
      window.addEventListener('nb:page-titles', onTitles);
      refresh(false);

      dom.addEventListener('click', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        if (e.metaKey || e.ctrlKey) { window.open(pageUrl(current.attrs.id), '_blank', 'noopener'); return; }
        openInApp(pageUrl(current.attrs.id), editor);
      });
      return {
        dom,
        update(next) {
          if (next.type.name !== 'pageLink' || next.attrs.cite !== current.attrs.cite) return false;
          const moved = next.attrs.id !== current.attrs.id;
          current = next;
          dom.href = pageUrl(next.attrs.id);
          const page = known.get(String(next.attrs.id));
          if (moved) refresh(false); else if (!dom.classList.contains('is-missing')) paint(page ? shown(next.attrs, page) : next.attrs.title, false);
          return true;
        },
        stopEvent: (e) => e.type === 'click',
        ignoreMutation: () => true,
        destroy() { window.removeEventListener('nb:page-titles', onTitles); },
      };
    };
  },

  addInputRules() {
    return [
      // "[[" — find a page to link, or make one, right where you type.
      new InputRule({
        find: /\[\[$/,
        handler: ({ state, range }) => {
          state.tr.delete(range.from, range.to);
          const editor = this.editor;
          setTimeout(() => openPagePicker(editor, { inline: true }), 0);
        },
      }),
    ];
  },

  addOptions() { return { pageId: null }; },
  onBeforeCreate() { this.storage.pageId = this.options.pageId; },

  addProseMirrorPlugins() {
    const key = new PluginKey('pageLinkBlocks');
    const { editor } = this;
    const decorate = (doc) => {
      const found = [];
      doc.descendants((n, pos) => {
        if (n.isTextblock && n.childCount === 1 && n.firstChild.type.name === 'pageLink' && !n.firstChild.attrs.cite) {
          found.push(Decoration.node(pos + 1, pos + 1 + n.firstChild.nodeSize, { class: 'is-block' }));
          return false;
        }
        return !n.isTextblock;
      });
      return DecorationSet.create(doc, found);
    };
    return [
      // A link alone on its line is a page in this one: shown as a row.
      new Plugin({
        key,
        state: {
          init: (_c, state) => decorate(state.doc),
          apply: (tr, old) => (tr.docChanged ? decorate(tr.doc) : old),
        },
        props: { decorations: (state) => key.getState(state) },
      }),
      // Other links: the app's own pages open in a BioManager tab, the web
      // in the browser.
      new Plugin({
        key: new PluginKey('linksInApp'),
        props: {
          handleDOMEvents: {
            click: (view, event) => {
              const a = event.target.closest && event.target.closest('a[href]');
              if (!a || event.defaultPrevented || a.classList.contains('nb-pagelink') || event.button !== 0) return false;
              const href = a.getAttribute('href') || '';
              if (!href || href.startsWith('#')) return false;
              let url;
              try { url = new URL(href, window.location.href); } catch (_e) { return false; }
              event.preventDefault();
              if (url.origin === window.location.origin && !event.metaKey && !event.ctrlKey) openInApp(url.pathname + url.search + url.hash, editor);
              else window.open(url.href, '_blank', 'noopener,noreferrer');
              return true;
            },
          },
        },
      }),
    ];
  },
});
