// Run by tests/test_notebook_blocks.py: which tables Run mode shows with
// each step (frontend/src/docops.js taskItems, tableRows), read from a page's
// Markdown by the notebook's own editor, in a DOM (jsdom).
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
const { taskItems, tableRows } = await import('../../frontend/src/docops.js');

const editor = new Editor({
  element: document.createElement('div'),
  extensions: [StarterKit, Markdown.configure({ html: false, linkify: true, breaks: true }),
    TaskList, TaskItem.configure({ nested: true }), Table, TableRow, TableHeader, TableCell],
});
const steps = (markdown) => {
  editor.commands.setContent(markdown);
  return taskItems(editor.state.doc).map((s) => ({ text: s.text, tables: s.tables.map(tableRows) }));
};
const cells = (rows) => rows.map((r) => r.map((c) => c.text));

// What Start experiment writes: a table typed under a step goes with that step.
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
assert.deepEqual(page.map((s) => s.tables.length), [0, 1, 0]);
assert.deepEqual(cells(page[1].tables[0]), [['Reagent', '1× (µL)', '10× (µL)'], ['Buffer', '2.5', '25'], ['dNTP', '0.5', '5']]);
assert.deepEqual(page[1].tables[0][0].map((c) => c.header), [true, true, true]);
assert.deepEqual(page[1].tables[0][1].map((c) => c.header), [false, false, false]);

// The last step keeps the tables after it until a heading; text between
// doesn't end its reach, and two tables both go with it.
const last = steps([
  '- [ ] Load the gel', '', 'Lanes:', '',
  '| Lane | Sample |', '| --- | --- |', '| 1 | Ladder |', '',
  '| Voltage | Time |', '| --- | --- |', '| 120 V | 40 min |',
].join('\n'));
assert.equal(last.length, 1);
assert.deepEqual(last[0].tables.map(cells), [[['Lane', 'Sample'], ['1', 'Ladder']], [['Voltage', 'Time'], ['120 V', '40 min']]]);

// A page with no steps has none to show.
assert.deepEqual(steps('| a | b |\n| --- | --- |\n| 1 | 2 |'), []);

editor.destroy();
console.log('ok');
