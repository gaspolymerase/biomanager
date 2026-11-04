// Run by tests/test_notebook_blocks.py: what Run mode shows with each step
// (frontend/src/docops.js taskItems: its sub-bullets and notes, the tables
// and other blocks written under it), read from a page's Markdown by the
// notebook's own editor, in a DOM (jsdom).
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://lab.test/' });
for (const name of ['window', 'document', 'navigator', 'Node', 'Element', 'HTMLElement', 'DOMParser',
  'MutationObserver', 'getComputedStyle', 'DocumentFragment']) {
  if (!(name in globalThis)) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
dom.window.requestAnimationFrame = (fn) => setTimeout(fn, 0);
dom.window.cancelAnimationFrame = (id) => clearTimeout(id);

const nm = '../../frontend/node_modules/';
const { Editor } = await import(`${nm}@tiptap/core/dist/index.js`);
const { default: StarterKit } = await import(`${nm}@tiptap/starter-kit/dist/index.js`);
const { default: TaskList } = await import(`${nm}@tiptap/extension-task-list/dist/index.js`);
const { default: TaskItem } = await import(`${nm}@tiptap/extension-task-item/dist/index.js`);
const { default: Table } = await import(`${nm}@tiptap/extension-table/dist/index.js`);
const { default: TableRow } = await import(`${nm}@tiptap/extension-table-row/dist/index.js`);
const { default: TableHeader } = await import(`${nm}@tiptap/extension-table-header/dist/index.js`);
const { default: TableCell } = await import(`${nm}@tiptap/extension-table-cell/dist/index.js`);
const { Markdown } = await import(`${nm}tiptap-markdown/dist/tiptap-markdown.es.js`);
const { taskItems } = await import('../../frontend/src/docops.js');

const editor = new Editor({
  element: document.createElement('div'),
  extensions: [StarterKit, Markdown.configure({ html: false, linkify: true, breaks: true }),
    TaskList, TaskItem.configure({ nested: true }), Table, TableRow, TableHeader, TableCell],
});
// Each step's text, and what goes with it: each block's kind and text.
const steps = (markdown) => {
  editor.commands.setContent(markdown);
  return taskItems(editor.state.doc).map((s) => ({
    text: s.text,
    context: s.context.map((n) => [n.type.name, n.textBetween(0, n.content.size, ' | ', ' ')]),
  }));
};
const kinds = (step) => step.context.map(([kind]) => kind);

// What Start experiment writes: a table typed under a step goes with that
// step; the Samples table before the steps and the Results after a heading
// are no step's.
const page = steps([
  '## Samples & materials', '',
  '| Reagent | Lot |', '| --- | --- |', '| Taq | 0012 |', '',
  '## Protocol', '',
  '- [ ] Thaw the reagents on ice',
  '- [ ] Make the master mix (table below)', '',
  '| Reagent | 1× (µL) | 10× (µL) |', '| --- | --- | --- |', '| Buffer | 2.5 | 25 |', '| dNTP | 0.5 | 5 |', '',
  '- [ ] Run 95 °C 2 min, then 35 cycles', '',
  '## Results', '',
  '| Sample | Band |', '| --- | --- |', '| A | yes |',
].join('\n'));
assert.deepEqual(page.map((s) => s.text), ['Thaw the reagents on ice', 'Make the master mix (table below)',
  'Run 95 °C 2 min, then 35 cycles']);
assert.deepEqual(page.map(kinds), [[], ['table'], []]);
assert.equal(page[1].context[0][1], 'Reagent | 1× (µL) | 10× (µL) | Buffer | 2.5 | 25 | dNTP | 0.5 | 5');

// Inside a step: its sub-bullets and sub-steps go with it, and are not
// steps of their own. After it: a note and a quote, up to the next step.
const inside = steps([
  '## Steps', '',
  '- [ ] Add 75 µL lysis reagent',
  '  - make sure the tissue is covered',
  '  - one blank per run',
  '- [ ] Heat at 95 °C for 30 min',
  '  - [ ] set the lid to 105 °C',
  '- [ ] Cool to 4 °C', '',
  'Hold in the cycler if there is no ice.', '',
  '> Tip: spin down before opening.', '',
  '- [ ] Add 75 µL neutralisation buffer', '',
  '---', '',
  'Clean punches between animals.',
].join('\n'));
assert.deepEqual(inside.map((s) => s.text), ['Add 75 µL lysis reagent', 'Heat at 95 °C for 30 min', 'Cool to 4 °C',
  'Add 75 µL neutralisation buffer']);
assert.deepEqual(inside.map(kinds), [['bulletList'], ['taskList'], ['paragraph', 'blockquote'], []]);
assert.equal(inside[0].context[0][1], 'make sure the tissue is covered | one blank per run');
assert.equal(inside[1].context[0][1], 'set the lid to 105 °C');
assert.equal(inside[2].context[0][1], 'Hold in the cycler if there is no ice.');
assert.equal(inside[2].context[1][1], 'Tip: spin down before opening.');

// The last step keeps what follows it until a heading: text and two
// tables. Empty lines are left out.
const last = steps([
  '- [ ] Load the gel', '', 'Lanes:', '',
  '| Lane | Sample |', '| --- | --- |', '| 1 | Ladder |', '',
  '| Voltage | Time |', '| --- | --- |', '| 120 V | 40 min |', '', '', '',
].join('\n'));
assert.equal(last.length, 1);
assert.deepEqual(kinds(last[0]), ['paragraph', 'table', 'table']);
assert.equal(last[0].context[2][1], 'Voltage | Time | 120 V | 40 min');

// A page with no steps has none to show.
assert.deepEqual(steps('| a | b |\n| --- | --- |\n| 1 | 2 |'), []);

editor.destroy();
console.log('ok');
