// Edits the notebook makes on its own: a timestamped line under a heading
// ("## Log" in a daily page, "## Deviations" in an experiment), a time
// stamp at the caret, a tick with the time on a protocol step.

import { clock } from './util.js';

function textNodes(parts) {
  return parts.filter((p) => p.text).map((p) => ({ type: 'text', text: p.text, ...(p.bold ? { marks: [{ type: 'bold' }] } : {}) }));
}

// Where the section under the first heading matching `re` ends, and
// whether it ends in a bullet list (to add to that list rather than start
// a new one). null when there is no such heading.
function sectionEnd(doc, re) {
  let heading = null;
  let end = null;
  let lastChild = null;
  doc.forEach((node, offset) => {
    if (end !== null) return;
    if (node.type.name === 'heading') {
      if (heading && node.attrs.level <= heading.level) { end = offset; return; }
      if (!heading && re.test(node.textContent)) { heading = { level: node.attrs.level }; lastChild = null; return; }
    }
    if (heading) lastChild = { node, offset };
  });
  if (!heading) return null;
  if (end === null) end = doc.content.size;
  // Trailing empty paragraphs belong to the gap before the next heading.
  let list = null;
  if (lastChild && lastChild.node.type.name === 'bulletList') list = lastChild;
  return { end, list };
}

// Add "**HH:MM** text" as a bullet under the heading (made at the end of
// the page if missing).
export function appendLogLine(editor, headingRe, headingText, text, { level = 2, time = clock() } = {}) {
  const { state } = editor;
  const item = {
    type: 'listItem',
    content: [{ type: 'paragraph', content: textNodes([{ text: time, bold: true }, { text: ` ${text}` }]) }],
  };
  const where = sectionEnd(state.doc, headingRe);
  if (where && where.list) {
    const insertAt = where.list.offset + where.list.node.nodeSize - 1;
    editor.chain().insertContentAt(insertAt, item).run();
    return;
  }
  const list = { type: 'bulletList', content: [item] };
  if (where) {
    editor.chain().insertContentAt(where.end, list).run();
    return;
  }
  editor.chain().insertContentAt(state.doc.content.size, [
    { type: 'heading', attrs: { level }, content: [{ type: 'text', text: headingText }] },
    list,
  ]).run();
}

export function stampTime(editor) {
  const now = new Date();
  editor.chain().focus().insertContent([{ type: 'text', text: clock(now), marks: [{ type: 'bold' }] }, { type: 'text', text: ' ' }]).run();
}

// An empty line: a paragraph with nothing in it but spaces or line breaks.
function blank(node) {
  if (!node.isTextblock || node.textContent.trim()) return false;
  let other = false;
  node.forEach((child) => { if (!child.isText && child.type.name !== 'hardBreak') other = true; });
  return !other;
}

function holdsStep(node) {
  let found = false;
  node.descendants((inner) => {
    if (found) return false;
    if (inner.type.name === 'taskItem') { found = true; return false; }
    return true;
  });
  return found;
}

// Protocol steps: every task item in the page, in order, each with what is
// written for it (its context): what is inside the step after its first
// line (sub-bullets, a note, sub-steps), then the blocks written after it
// up to the next step or the next heading or line (---): a note, a table,
// a picture, a callout, a calculator. Whatever is typed under a step
// usually ends the checklist in the page's Markdown, so "the table below"
// sits between two steps rather than in one. A heading ends a step's
// reach, so a Results table or the Notes are no step's. Empty lines are
// left out. Sub-steps (a checklist inside a step) are part of their step,
// not steps of their own.
export function taskItems(doc) {
  const items = [];
  let open = null;
  doc.descendants((node, pos) => {
    const type = node.type.name;
    if (type === 'taskItem') {
      const para = node.firstChild;
      const own = para && para.isTextblock;
      const context = [];
      node.forEach((child, _offset, i) => {
        if (i === 0 && own) return;
        if (!blank(child)) context.push(child);
      });
      open = { pos, checked: !!node.attrs.checked, text: own ? para.textContent : node.textContent, node, context };
      items.push(open);
      return false;
    }
    if (type === 'heading' || type === 'horizontalRule') { open = null; return false; }
    // A list of steps, or a callout or column holding one: look inside.
    if (holdsStep(node)) return true;
    if (open && !blank(node)) open.context.push(node);
    return false;
  });
  return items;
}

export function checkStep(editor, pos, checked, { stamp = true } = {}) {
  const { state } = editor;
  const node = state.doc.nodeAt(pos);
  if (!node || node.type.name !== 'taskItem') return;
  let tr = state.tr.setNodeMarkup(pos, undefined, { ...node.attrs, checked });
  const para = node.firstChild;
  if (checked && stamp && para && para.isTextblock) {
    const end = pos + 1 + para.nodeSize - 1;
    const mark = state.schema.marks.italic;
    const text = state.schema.text(` — ✓ ${clock()}`, mark ? [mark.create()] : []);
    tr = tr.insert(end, text);
  }
  editor.view.dispatch(tr);
}
