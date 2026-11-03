// Run by tests/test_notebook_blocks.py: a sheet row's autosave (app/static/sheet.js) keeps the
// "<name>_was" copies that stop a row open since before a colleague's edit from undoing it
// (app/formutil.py form_changed). Through BioSheet.save, in a DOM (jsdom, tests/js/package.json).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { JSDOM } = require('jsdom');
const read = (name) => readFileSync(new URL(`../../app/static/${name}`, import.meta.url), 'utf8');

// One row of a sheet: a note (saved as typed), a count (saved on change) and a status the
// server may change; its hidden form carries what the row showed when it was drawn.
const PAGE = `<section class="data-table-card sheet" data-autosave-sheet>
  <span data-save-status></span>
  <table><tbody><tr>
    <td><input name="note" form="row-1" data-autosave="1" value="new note"></td>
    <td><input name="count" type="number" form="row-1" data-autosave="1" data-autosave-on="change" value="7"></td>
    <td><input name="status" form="row-1" data-autosave="1" value="in stock"></td>
  </tr></tbody></table>
  <form id="row-1" action="/rows/1/update" method="post" data-record-label="reagent 1">
    <input type="hidden" name="note_was" value="old note">
    <input type="hidden" name="count_was" value="3">
    <input type="hidden" name="status_was" value="in stock">
  </form>
</section>`;

async function page(answer) {
  const dom = new JSDOM(`<!doctype html><body>${PAGE}</body>`, { runScripts: 'outside-only', url: 'http://lab.test/' });
  const w = dom.window;
  w.t = (text, values) => (values ? text.replace(/%\((\w+)\)[sd]/g, (m, k) => (k in values ? values[k] : m)) : text);
  const sent = [];
  w.fetch = async (url, init) => {
    sent.push({ url, body: Object.fromEntries(init.body.entries()), headers: init.headers });
    return { ok: answer.status === undefined || answer.status < 400, status: answer.status || 200,
             json: async () => answer.body };
  };
  w.eval(read('row-forms.js'));
  w.eval(read('sheet.js'));
  const form = w.document.getElementById('row-1');
  const card = w.document.querySelector('[data-autosave-sheet]');
  const was = (name) => form.querySelector(`[name="${name}_was"]`).value;
  const cell = (name) => w.document.querySelector(`[form="row-1"][name="${name}"]`);
  return { w, form, card, sent, was, cell, save: () => w.BioSheet.save(form, card) };
}

// A save sends each cell with the copy of what the row showed, so the server writes only
// what this row changed (note) and leaves a field a colleague changed since alone.
{
  const p = await page({ body: { ok: true, row: { values: {} } } });
  assert.equal(await p.save(), true);
  assert.equal(p.sent.length, 1);
  assert.equal(p.sent[0].url, 'http://lab.test/rows/1/update');
  assert.equal(p.sent[0].headers['X-Autosave'], '1');
  assert.deepEqual(p.sent[0].body,
    { note: 'new note', count: '7', status: 'in stock', note_was: 'old note', count_was: '3', status_was: 'in stock' });
  // Saved: what the row shows is now what it last saw, so the next save of another cell
  // doesn't send the note again as a change.
  assert.equal(p.was('note'), 'new note');
  assert.equal(p.was('count'), '7');
  assert.equal(p.card.querySelector('[data-save-status]').dataset.state, 'saved');
}

// The server's own values (Low when the quantity fell) go into the cell and its copy.
{
  const p = await page({ body: { ok: true, row: { values: { status: 'low' } } } });
  assert.equal(await p.save(), true);
  assert.equal(p.cell('status').value, 'low');
  assert.equal(p.was('status'), 'low');
}

// Refused: nothing changes in the copies, so the next save still says what the row saw;
// a number saved on change goes back to the one the record has; the reason is shown.
{
  const p = await page({ status: 409, body: { ok: false, error: 'Changed since you opened it.' } });
  p.cell('count').dataset.dirty = '1';
  assert.equal(await p.save(), false);
  assert.equal(p.was('note'), 'old note');
  assert.equal(p.was('count'), '3');
  assert.equal(p.cell('count').value, '3');
  assert.equal(p.cell('note').value, 'new note');            // typed text is kept to try again
  const status = p.card.querySelector('[data-save-status]');
  assert.equal(status.dataset.state, 'error');
  assert.match(status.textContent, /reagent 1: Changed since you opened it\./);
  assert.ok(p.cell('count').closest('td').classList.contains('has-error'));
}
