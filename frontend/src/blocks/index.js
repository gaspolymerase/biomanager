// The notebook's working blocks — data sheets, recipes, formulations,
// calculators, plates, qPCR, diagrams and equations — as one TipTap node.
//
// In Markdown each is a fenced block named by its kind, holding its data
// (JSON, or the diagram/equation source):
//
//   ```sheet
//   {"columns":[...],"rows":[...]}
//   ```
//
// so a page stays readable Markdown anywhere (GitHub renders ```mermaid and
// ```math itself), and pasting such a fence back in brings the block back.
//
// In the editor the data lives in one node attribute, not in the node's
// text. Live editing (Yjs) merges text character by character, which would
// splice two people's JSON together; an attribute is replaced whole, so the
// block always holds valid data.

import { Node, mergeAttributes } from '@tiptap/core';
import { el } from '../util.js';
import { mountSheet, defaultSheet } from './sheet.js';
import { mountRecipe, defaultRecipe } from './recipe.js';
import { mountFormulation, defaultFormulation } from './formulation.js';
import { mountCalc, defaultCalc, CALC_TYPES } from './calc.js';
import { mountPlate, defaultPlate } from './plate.js';
import { mountQpcr, defaultQpcr } from './qpcr.js';
import { mountDiagram, defaultDiagram } from './diagram.js';
import { mountExperiment, defaultExperiment } from './experiment.js';
import { mountToc } from './toc.js';

export const BLOCKS = {
  sheet: { label: 'Data sheet', json: true, mount: mountSheet, make: defaultSheet },
  recipe: { label: 'Buffer recipe', json: true, mount: mountRecipe, make: defaultRecipe },
  formulation: { label: 'Formulation', json: true, mount: mountFormulation, make: defaultFormulation },
  calc: { label: 'Calculator', json: true, mount: mountCalc, make: () => defaultCalc('dilution') },
  plate: { label: 'Plate reader', json: true, mount: mountPlate, make: defaultPlate },
  qpcr: { label: 'qPCR ΔΔCt', json: true, mount: mountQpcr, make: defaultQpcr },
  experiment: { label: 'Colony experiment', json: true, mount: mountExperiment, make: defaultExperiment },
  mermaid: { label: 'Diagram', json: false, mount: mountDiagram, make: () => defaultDiagram('mermaid') },
  mindmap: { label: 'Mind map', json: false, mount: mountDiagram, make: () => defaultDiagram('mindmap') },
  math: { label: 'Equation', json: false, mount: mountDiagram, make: () => defaultDiagram('math') },
  toc: { label: 'Contents', json: false, mount: mountToc, make: () => '' },
};

export { CALC_TYPES };

function decode(kind, raw) {
  if (!BLOCKS[kind].json) return raw || '';
  try {
    const parsed = JSON.parse(raw || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_e) {
    return { __invalid: raw };
  }
}

export function encode(kind, value) {
  return BLOCKS[kind].json ? JSON.stringify(value) : String(value ?? '');
}

class LabBlockView {
  constructor({ node, editor, getPos }) {
    this.node = node;
    this.editor = editor;
    this.getPos = getPos;
    this.lastWritten = node.attrs.data;
    const kind = node.attrs.kind;
    const spec = BLOCKS[kind];
    this.dom = el('div', { class: `nb-block nb-block-${kind}`, 'data-kind': kind, contenteditable: 'false' });
    const head = el('div', { class: 'nb-block-head' });
    head.appendChild(el('span', { class: 'nb-block-grip', 'data-drag-handle': '', title: 'Drag to move', html: '⋮⋮' }));
    head.appendChild(el('span', { class: 'nb-block-label', text: spec.label }));
    this.actions = el('span', { class: 'nb-block-actions' });
    this.removeBtn = el('button', { type: 'button', class: 'nb-icon-btn', title: 'Remove block', 'aria-label': 'Remove block', html: '×' });
    this.removeBtn.addEventListener('click', () => this.remove());
    this.actions.appendChild(this.removeBtn);
    head.appendChild(this.actions);
    this.dom.appendChild(head);
    this.body = el('div', { class: 'nb-block-body' });
    this.dom.appendChild(this.body);
    this.mount();
    this.onEditable = () => {
      if (this.editable !== editor.isEditable) this.remount();
    };
    editor.on('transaction', this.onEditable);
  }

  mount() {
    const kind = this.node.attrs.kind;
    const value = decode(kind, this.node.attrs.data);
    this.editable = this.editor.isEditable;
    this.removeBtn.hidden = !this.editable;
    if (value && value.__invalid !== undefined) {
      this.body.innerHTML = '';
      this.body.appendChild(el('p', { class: 'nb-warn', text: 'This block’s data could not be read. Its text is kept below.' }));
      this.body.appendChild(el('pre', { class: 'nb-diagram-src', text: value.__invalid }));
      this.impl = null;
      return;
    }
    this.impl = BLOCKS[kind].mount(this.body, {
      kind,
      editor: this.editor,
      data: value,
      editable: this.editable,
      commit: (next) => this.commit(next),
    });
  }

  remount() {
    this.impl?.destroy?.();
    this.body.innerHTML = '';
    this.mount();
  }

  commit(value) {
    if (!this.editor.isEditable || this.editor.isDestroyed) return;
    const str = encode(this.node.attrs.kind, value);
    if (str === this.node.attrs.data) return;
    const pos = typeof this.getPos === 'function' ? this.getPos() : null;
    if (pos === null || pos === undefined) return;
    this.lastWritten = str;
    const tr = this.editor.state.tr.setNodeMarkup(pos, undefined, { ...this.node.attrs, data: str });
    tr.setMeta('addToHistory', true);
    this.editor.view.dispatch(tr);
  }

  remove() {
    const pos = this.getPos();
    if (typeof pos !== 'number') return;
    this.editor.chain().focus().deleteRange({ from: pos, to: pos + this.node.nodeSize }).run();
  }

  update(node) {
    if (node.type !== this.node.type || node.attrs.kind !== this.node.attrs.kind) return false;
    const changed = node.attrs.data !== this.node.attrs.data;
    this.node = node;
    if (changed && node.attrs.data !== this.lastWritten) {
      this.lastWritten = node.attrs.data;
      const value = decode(node.attrs.kind, node.attrs.data);
      if (this.impl && value.__invalid === undefined) this.impl.update(value);
      else this.remount();
    }
    return true;
  }

  selectNode() { this.dom.classList.add('is-selected'); }

  deselectNode() { this.dom.classList.remove('is-selected'); }

  stopEvent(event) {
    // The block's own inputs handle their events; only the drag handle is
    // left to the editor.
    const t = event.target;
    if (t && t.closest && t.closest('[data-drag-handle]')) return false;
    return true;
  }

  ignoreMutation() { return true; }

  destroy() {
    this.editor.off('transaction', this.onEditable);
    this.impl?.destroy?.();
  }

  focus() { this.impl?.focus?.(); }
}

export const LabBlock = Node.create({
  name: 'labBlock',
  group: 'block',
  atom: true,
  draggable: true,
  selectable: true,
  isolating: true,

  addAttributes() {
    return {
      kind: { default: 'sheet' },
      data: { default: '' },
    };
  },

  parseHTML() {
    return [{
      tag: 'pre',
      priority: 100,
      preserveWhitespace: 'full',
      getAttrs: (element) => {
        const code = element.querySelector('code');
        const cls = (code && code.className) || element.getAttribute('data-lab-block') || '';
        const m = /(?:language-)?(\w+)/.exec(element.getAttribute('data-lab-block') || cls);
        const kind = m && m[1];
        if (!kind || !BLOCKS[kind]) return false;
        const text = (code || element).textContent || '';
        return { kind, data: text.replace(/\n$/, '') };
      },
    }];
  },

  renderHTML({ node, HTMLAttributes }) {
    return ['pre', mergeAttributes(HTMLAttributes, { 'data-lab-block': node.attrs.kind }),
      ['code', { class: `language-${node.attrs.kind}` }, node.attrs.data || '']];
  },

  addStorage() {
    return {
      markdown: {
        serialize(state, node) {
          const data = String(node.attrs.data || '');
          // A fence longer than any run of backticks inside it.
          const longest = Math.max(2, ...((data.match(/`+/g) || []).map((s) => s.length)));
          const fence = '`'.repeat(longest + 1);
          state.write(fence + node.attrs.kind + '\n');
          state.text(data, false);
          state.ensureNewLine();
          state.write(fence);
          state.closeBlock(node);
        },
        parse: {},
      },
    };
  },

  addCommands() {
    return {
      insertLabBlock: (kind, value) => ({ chain }) => {
        const spec = BLOCKS[kind];
        if (!spec) return false;
        const data = encode(kind, value !== undefined ? value : spec.make());
        return chain().insertContent([{ type: this.name, attrs: { kind, data } }, { type: 'paragraph' }]).run();
      },
    };
  },

  addNodeView() {
    return (props) => new LabBlockView(props);
  },
});
