// Callouts, toggles, columns and coloured text in a notebook page. How each
// is written in the page's Markdown is in ../markdown-extras.js.

import { Mark, Node, mergeAttributes, markInputRule } from '@tiptap/core';
import { TextSelection } from '@tiptap/pm/state';
import { CALLOUTS, COLORS, richMarkdown, tintSuffix } from '../markdown-extras.js';
import { el } from '../util.js';

const setup = { setup(markdownit) { richMarkdown(markdownit); } };

// Where a new block of `type` goes: the innermost block around the caret
// that its parent would let `type` stand in for. A paragraph there is
// taken in (its words become the toggle's title, or the first column);
// anything else gets the new block after it.
function spotFor(state, type) {
  const { $from } = state.selection;
  for (let d = $from.depth; d >= 1; d--) {
    const parent = $from.node(d - 1);
    const index = $from.index(d - 1);
    if (parent.canReplaceWith(index, index + 1, type)) {
      const node = $from.node(d);
      return { node, from: $from.before(d), to: $from.after(d), takes: node.type.name === 'paragraph' };
    }
  }
  return null;
}

// ---------------------------------------------------------------- callout

function calloutMenu(anchor, current, pick) {
  document.querySelectorAll('.nb-callout-menu').forEach((m) => m.remove());
  const menu = el('div', { class: 'nb-callout-menu insert-menu' });
  Object.entries(CALLOUTS).forEach(([kind, spec]) => {
    const b = el('button', { type: 'button', class: `insert-menu-item${kind === current ? ' is-active' : ''}` });
    b.appendChild(el('span', { class: 'insert-menu-icon', text: spec.icon }));
    b.appendChild(el('span', { class: 'insert-menu-title', text: spec.label }));
    b.addEventListener('mousedown', (e) => e.preventDefault());
    b.addEventListener('click', () => { menu.remove(); pick(kind); });
    menu.appendChild(b);
  });
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.left = `${Math.round(r.left + window.scrollX)}px`;
  menu.style.top = `${Math.round(r.bottom + window.scrollY + 4)}px`;
  const away = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('mousedown', away); } };
  setTimeout(() => document.addEventListener('mousedown', away), 0);
}

export const Callout = Node.create({
  name: 'callout',
  group: 'block',
  content: 'block+',
  defining: true,

  addAttributes() {
    return {
      kind: {
        default: 'note',
        parseHTML: (element) => (CALLOUTS[element.getAttribute('data-callout')] ? element.getAttribute('data-callout') : 'note'),
        renderHTML: (attrs) => ({ 'data-callout': attrs.kind }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'div[data-callout]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { class: 'nb-callout' }), 0];
  },

  addStorage() {
    return {
      markdown: {
        serialize(state, node) {
          state.wrapBlock('> ', null, node, () => {
            state.write(`[!${node.attrs.kind.toUpperCase()}]`);
            state.ensureNewLine();
            state.renderContent(node);
          });
        },
        parse: setup,
      },
    };
  },

  addCommands() {
    return {
      setCallout: (kind = 'note') => ({ commands }) => commands.wrapIn(this.name, { kind }),
    };
  },

  addNodeView() {
    return ({ node, editor, getPos }) => {
      let current = node;
      const dom = el('div', { class: 'nb-callout', 'data-callout': node.attrs.kind });
      const icon = el('button', { type: 'button', class: 'nb-callout-icon', contenteditable: 'false', title: 'Change the kind of callout' });
      const content = el('div', { class: 'nb-callout-body' });
      const paint = () => {
        dom.dataset.callout = current.attrs.kind;
        icon.textContent = (CALLOUTS[current.attrs.kind] || CALLOUTS.note).icon;
        icon.setAttribute('aria-label', (CALLOUTS[current.attrs.kind] || CALLOUTS.note).label);
      };
      icon.addEventListener('mousedown', (e) => e.preventDefault());
      icon.addEventListener('click', () => {
        if (!editor.isEditable) return;
        calloutMenu(icon, current.attrs.kind, (kind) => {
          const pos = getPos();
          if (typeof pos === 'number') editor.view.dispatch(editor.state.tr.setNodeMarkup(pos, undefined, { ...current.attrs, kind }));
        });
      });
      dom.append(icon, content);
      paint();
      return {
        dom,
        contentDOM: content,
        update(next) {
          if (next.type !== current.type) return false;
          current = next;
          paint();
          return true;
        },
        ignoreMutation: (m) => m.type !== 'selection' && !content.contains(m.target),
      };
    };
  },
});

// ---------------------------------------------------------------- toggle

export const ToggleSummary = Node.create({
  name: 'toggleSummary',
  content: 'inline*',
  defining: true,
  selectable: false,

  parseHTML() {
    return [{ tag: 'summary' }];
  },

  renderHTML() {
    return ['summary', { class: 'nb-toggle-summary' }, 0];
  },

  addKeyboardShortcuts() {
    return {
      // Enter in the line you click on goes into what it hides.
      Enter: ({ editor }) => {
        const { $from, empty } = editor.state.selection;
        if (!empty || $from.parent.type.name !== this.name) return false;
        const toggle = $from.node(-1);
        const after = $from.after();
        const tr = editor.state.tr;
        if (toggle.childCount < 2 || $from.parentOffset < $from.parent.content.size) {
          tr.insert(after, editor.schema.nodes.paragraph.create());
        }
        tr.setSelection(TextSelection.near(tr.doc.resolve(after + 1)));
        editor.view.dispatch(tr.scrollIntoView());
        return true;
      },
      // Backspace in an empty line you click on turns the toggle back into text.
      Backspace: ({ editor }) => {
        const { $from, empty } = editor.state.selection;
        if (!empty || $from.parent.type.name !== this.name || $from.parentOffset > 0) return false;
        const pos = $from.before(-1);
        const toggle = $from.node(-1);
        const blocks = [];
        toggle.forEach((child, _o, i) => {
          if (i === 0) blocks.push(editor.schema.nodes.paragraph.create(null, child.content));
          else blocks.push(child);
        });
        const tr = editor.state.tr.replaceWith(pos, pos + toggle.nodeSize, blocks);
        tr.setSelection(TextSelection.near(tr.doc.resolve(pos + 1)));
        editor.view.dispatch(tr);
        return true;
      },
    };
  },
});

export const Toggle = Node.create({
  name: 'toggle',
  group: 'block',
  content: 'toggleSummary block+',
  defining: true,

  parseHTML() {
    return [{ tag: 'details' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['details', mergeAttributes(HTMLAttributes, { 'data-toggle': '', class: 'nb-toggle' }), 0];
  },

  addStorage() {
    return {
      markdown: {
        serialize(state, node) {
          state.write('<details>');
          state.ensureNewLine();
          state.write('<summary>');
          state.renderInline(node.firstChild);
          state.write('</summary>');
          state.closeBlock(node.firstChild);
          node.forEach((child, _o, i) => { if (i > 0) state.render(child, node, i); });
          state.write('</details>');
          state.closeBlock(node);
        },
        parse: setup,
      },
    };
  },

  addCommands() {
    return {
      setToggle: () => ({ state, tr, dispatch }) => {
        const spot = spotFor(state, state.schema.nodes.toggle);
        if (!spot) return false;
        if (!dispatch) return true;
        const title = spot.takes ? spot.node.content : null;
        const toggle = state.schema.nodes.toggle.create(null, [
          state.schema.nodes.toggleSummary.create(null, title),
          state.schema.nodes.paragraph.create(),
        ]);
        const at = spot.takes ? spot.from : spot.to;
        if (spot.takes) tr.replaceWith(spot.from, spot.to, toggle);
        else tr.insert(spot.to, toggle);
        tr.setSelection(TextSelection.near(tr.doc.resolve(at + 2 + (title ? title.size : 0))));
        return true;
      },
    };
  },

  addNodeView() {
    return ({ node, editor, getPos }) => {
      // Open or closed is each reader's own, as in Notion: not saved in
      // the page, so opening one doesn't open it for everyone. A new,
      // empty toggle starts open, to type in.
      const empty = node.childCount === 2 && node.child(1).content.size === 0;
      let open = empty;
      let current = node;
      const dom = el('div', { class: 'nb-toggle' });
      const caret = el('button', { type: 'button', class: 'nb-toggle-caret', contenteditable: 'false', 'aria-label': 'Open or close' });
      const content = el('div', { class: 'nb-toggle-content' });
      const paint = () => {
        dom.classList.toggle('is-open', open);
        caret.setAttribute('aria-expanded', String(open));
      };
      caret.addEventListener('mousedown', (e) => e.preventDefault());
      caret.addEventListener('click', () => { open = !open; paint(); });
      dom.append(caret, content);
      paint();
      // Moving into what it hides (arrows, a search hit) opens it.
      const follow = () => {
        if (open || typeof getPos !== 'function') return;
        const pos = getPos();
        if (typeof pos !== 'number') return;
        const { from } = editor.state.selection;
        const bodyStart = pos + 1 + current.firstChild.nodeSize;
        if (from > bodyStart && from < pos + current.nodeSize) { open = true; paint(); }
      };
      editor.on('selectionUpdate', follow);
      return {
        dom,
        contentDOM: content,
        update(next) {
          if (next.type !== current.type) return false;
          current = next;
          return true;
        },
        ignoreMutation: (m) => m.type !== 'selection' && !content.contains(m.target),
        destroy() { editor.off('selectionUpdate', follow); },
      };
    };
  },
});

// ---------------------------------------------------------------- columns

export const Column = Node.create({
  name: 'column',
  content: 'block+',
  isolating: true,
  defining: true,

  parseHTML() {
    return [{ tag: 'div[data-column]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-column': '', class: 'nb-column' }), 0];
  },

  addStorage() {
    return {
      markdown: {
        serialize(state, node) {
          state.write('::: column');
          state.closeBlock(node);
          state.renderContent(node);
          state.write(':::');
          state.closeBlock(node);
        },
        parse: setup,
      },
    };
  },
});

export const MAX_COLUMNS = 4;

function columnsAround(state) {
  const { $from } = state.selection;
  for (let d = $from.depth; d > 0; d--) {
    if ($from.node(d).type.name === 'column' && $from.node(d - 1).type.name === 'columns') {
      return { columnsPos: $from.before(d - 1), columns: $from.node(d - 1), index: $from.index(d - 1), columnPos: $from.before(d) };
    }
  }
  return null;
}

export const Columns = Node.create({
  name: 'columns',
  group: 'block',
  content: `column{2,${MAX_COLUMNS}}`,
  isolating: true,
  defining: true,

  parseHTML() {
    return [{ tag: 'div[data-columns]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-columns': '', class: 'nb-columns' }), 0];
  },

  addStorage() {
    return {
      markdown: {
        serialize(state, node) {
          state.write(':::: columns');
          state.closeBlock(node);
          state.renderContent(node);
          state.write('::::');
          state.closeBlock(node);
        },
        parse: setup,
      },
    };
  },

  addCommands() {
    const blank = (schema) => schema.nodes.column.create(null, schema.nodes.paragraph.create());
    return {
      setColumns: (count = 2) => ({ state, tr, dispatch }) => {
        const n = Math.max(2, Math.min(MAX_COLUMNS, count));
        const spot = spotFor(state, state.schema.nodes.columns);
        if (!spot) return false;
        if (!dispatch) return true;
        const first = spot.takes && spot.node.content.size
          ? state.schema.nodes.column.create(null, spot.node) : blank(state.schema);
        const cols = [first];
        while (cols.length < n) cols.push(blank(state.schema));
        const node = state.schema.nodes.columns.create(null, cols);
        const at = spot.takes ? spot.from : spot.to;
        if (spot.takes) tr.replaceWith(spot.from, spot.to, node);
        else tr.insert(spot.to, node);
        tr.setSelection(TextSelection.near(tr.doc.resolve(at + 3)));
        return true;
      },
      addColumn: () => ({ state, tr, dispatch }) => {
        const found = columnsAround(state);
        if (!found || found.columns.childCount >= MAX_COLUMNS) return false;
        if (!dispatch) return true;
        const column = found.columns.child(found.index);
        const at = found.columnPos + column.nodeSize;
        tr.insert(at, blank(state.schema));
        tr.setSelection(TextSelection.near(tr.doc.resolve(at + 2)));
        return true;
      },
      removeColumn: () => ({ state, tr, dispatch }) => {
        const found = columnsAround(state);
        if (!found) return false;
        if (!dispatch) return true;
        const { columns, columnsPos, index } = found;
        if (columns.childCount <= 2) {
          // One column left is no columns: its words go back into the page.
          const keep = columns.child(index === 0 ? 1 : 0);
          tr.replaceWith(columnsPos, columnsPos + columns.nodeSize, keep.content);
        } else {
          const column = columns.child(index);
          tr.delete(found.columnPos, found.columnPos + column.nodeSize);
        }
        return true;
      },
      unsetColumns: () => ({ state, tr, dispatch }) => {
        const found = columnsAround(state);
        if (!found) return false;
        if (!dispatch) return true;
        const blocks = [];
        found.columns.forEach((col) => col.forEach((child) => blocks.push(child)));
        tr.replaceWith(found.columnsPos, found.columnsPos + found.columns.nodeSize, blocks);
        return true;
      },
    };
  },
});

export function inColumns(editor) {
  return !!columnsAround(editor.state);
}

// ---------------------------------------------------------------- colours

export const Tint = Mark.create({
  name: 'tint',

  addAttributes() {
    return {
      color: {
        default: '',
        parseHTML: (element) => (COLORS.includes(element.getAttribute('data-color')) ? element.getAttribute('data-color') : ''),
        renderHTML: (attrs) => (attrs.color ? { 'data-color': attrs.color } : {}),
      },
      bg: {
        default: '',
        parseHTML: (element) => (COLORS.includes(element.getAttribute('data-bg')) ? element.getAttribute('data-bg') : ''),
        renderHTML: (attrs) => (attrs.bg ? { 'data-bg': attrs.bg } : {}),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'span[data-color]' }, { tag: 'span[data-bg]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes, { class: 'nb-tint' }), 0];
  },

  addStorage() {
    return {
      markdown: {
        serialize: {
          open: '[',
          close: (_state, mark) => tintSuffix(mark.attrs),
          mixable: true,
          expelEnclosingWhitespace: true,
        },
        parse: setup,
      },
    };
  },

  addCommands() {
    return {
      // { color } or { bg }: '' takes that one off, leaving the other.
      setTint: (change) => ({ editor, chain }) => {
        const now = editor.getAttributes(this.name);
        const next = { color: now.color || '', bg: now.bg || '', ...change };
        if (!next.color && !next.bg) return chain().unsetMark(this.name).run();
        return chain().setMark(this.name, next).run();
      },
      unsetTint: () => ({ chain }) => chain().unsetMark(this.name).run(),
    };
  },

  addInputRules() {
    return [markInputRule({ find: /(?:^|\s)(==(?!\s)([^=]+)(?<!\s)==)$/, type: this.type, getAttributes: () => ({ bg: 'yellow' }) })];
  },
});

export const RichBlocks = [Callout, ToggleSummary, Toggle, Column, Columns, Tint];
