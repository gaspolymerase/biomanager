// Run by tests/test_page_scripts.py: the loading animation (app/static/loader.js). Its rules
// in plain Node, then a page in a DOM (jsdom, tests/js/package.json), which has no
// Navigation API, so the link-and-form path is the one run here.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { JSDOM } = require('jsdom');
const SOURCE = readFileSync(new URL('../../app/static/loader.js', import.meta.url), 'utf8');
const L = require('../../app/static/loader.js');

// Every page of a kind is one kind of load, whatever its number.
assert.equal(L.routeKey('get', '/colony/cage/12', 'http://lab.test/'), 'GET /colony/cage/:n');
assert.equal(L.routeKey('post', 'http://lab.test/proposals/7/approve?x=1'), 'POST /proposals/:n/approve');
assert.equal(L.routeKey('GET', '/notebook/3f2a9c1e-77b0-4c1d-9a55-0e2b1c4d5e6f', 'http://lab.test/'), 'GET /notebook/:id');

// Approving a proposal is known to be slow before it has ever been timed; anything else is not.
assert.equal(L.estimate({}, 'POST /proposals/:n/approve'), 3000);
assert.equal(L.estimate({}, 'GET /colony'), 0);
assert.equal(L.estimate({ 'GET /colony': 420 }, 'GET /colony'), 420);

// More than a second expected: at once. Otherwise only once a second has gone by.
assert.equal(L.delayFor(1500), 0);
assert.equal(L.delayFor(1000), L.THRESHOLD);
assert.equal(L.delayFor(0), L.THRESHOLD);

// The newest time counts as much as all before it, and the oldest kinds drop off.
assert.deepEqual(L.remember({}, 'GET /a', 800), { 'GET /a': 800 });
assert.deepEqual(L.remember({ 'GET /a': 800 }, 'GET /a', 2000), { 'GET /a': 1400 });
let many = {};
for (let i = 0; i < 205; i += 1) many = L.remember(many, `GET /p${i}`, i);
assert.equal(Object.keys(many).length, 200);
assert.ok(!('GET /p0' in many) && 'GET /p204' in many);

// A file is a download, not a page: no animation for it.
for (const href of ['/colony/mice/export', '/plasmids/3/download.gb', '/qr.svg', '/inv/spending.csv', '/settings/lab-file'])
  assert.ok(L.isDownload(href, 'http://lab.test/'), href);
for (const href of ['/colony', '/proposals/2/approve', '/notebook/12'])
  assert.ok(!L.isDownload(href, 'http://lab.test/'), href);

// Ten animations, never the same twice in a row.
assert.equal(L.LOADERS.length, 10);
L.LOADERS.forEach((draw, i) => assert.match(draw(i), /^<svg viewBox="0 0 96 96"/));
let last = -1;
for (let n = 0; n < 200; n += 1) {
  const next = L.pick(10, last, Math.random);
  assert.ok(next >= 0 && next < 10 && next !== last);
  last = next;
}

// ---- On a page ----------------------------------------------------------------

function page(times) {
  const dom = new JSDOM(`<!doctype html><body>
    <a id="cage" href="/colony/cage/4">Cage 4</a>
    <a id="file" href="/colony/mice/export">Export</a>
    <a id="away" href="https://elsewhere.test/">Elsewhere</a>
    <a id="mine" href="/colony" data-no-loading>Colony</a>
    <form id="approve" method="post" action="/proposals/9/approve" data-loading-label="Applying the proposal…"><button>Approve</button></form>
  </body>`, { runScripts: 'outside-only', url: 'http://lab.test/proposals', virtualConsole: new (require('jsdom').VirtualConsole)() });
  const w = dom.window;
  w.t = (text) => text;
  if (times) w.localStorage.setItem('biomanager:load-times', JSON.stringify(times));
  w.eval(SOURCE);
  return w;
}
const wait = (w, ms) => new Promise((done) => w.setTimeout(done, ms));
const click = (w, id) => w.document.getElementById(id).dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
const shown = (w) => w.document.querySelector('.ld-overlay');

// A page known to be slow: the animation at once, saying what is loading.
{
  const w = page({ 'GET /colony/cage/:n': 2400 });
  click(w, 'cage');
  await wait(w, 20);
  assert.ok(shown(w), 'a slow page shows the animation at once');
  assert.equal(shown(w).querySelector('.ld-label').textContent, 'Opening…');
  assert.equal(shown(w).querySelectorAll('svg').length, 1);
  assert.equal(JSON.parse(w.sessionStorage.getItem('biomanager:loading')).key, 'GET /colony/cage/:n');
  w.BiomanagerLoader.stop();
  assert.equal(shown(w), null);
}

// A page that is usually quick: nothing for the first second, then the animation.
{
  const w = page({ 'GET /colony/cage/:n': 300 });
  click(w, 'cage');
  await wait(w, 300);
  assert.equal(shown(w), null, 'no flash for a quick page');
  await wait(w, 900);
  assert.ok(shown(w), 'still loading after a second: the animation');
  w.BiomanagerLoader.stop();
}

// Approving a proposal: slow from the start, and named by its form.
{
  const w = page();
  w.document.getElementById('approve').requestSubmit();
  await wait(w, 20);
  assert.ok(shown(w));
  assert.equal(shown(w).querySelector('.ld-label').textContent, 'Applying the proposal…');
  w.BiomanagerLoader.stop();
}

// A download, another site, a page that asked to be left alone, a click a script kept: nothing.
{
  const w = page({ 'GET /colony/mice/export': 5000, 'GET /colony': 5000 });
  click(w, 'file');
  click(w, 'away');
  click(w, 'mine');
  const cage = w.document.getElementById('cage');
  cage.addEventListener('click', (event) => event.preventDefault());
  click(w, 'cage');
  await wait(w, 1200);
  assert.equal(shown(w), null);
}

// The page that was waited for has come: it remembers how long that took.
{
  const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only', url: 'http://lab.test/colony/cage/4' });
  const w = dom.window;
  w.sessionStorage.setItem('biomanager:loading', JSON.stringify({ key: 'GET /colony/cage/:n', t: Date.now() - 1800 }));
  w.eval(SOURCE);
  const took = JSON.parse(w.localStorage.getItem('biomanager:load-times'))['GET /colony/cage/:n'];
  assert.ok(took >= 1800 && took < 2500, String(took));
  assert.equal(w.sessionStorage.getItem('biomanager:loading'), null);
}
