// Run by tests/test_page_scripts.py: the loading animation (app/static/loader.js). Its rules
// in plain Node, then a page in a DOM (jsdom, tests/js/package.json), which has no
// Navigation API, so the link-and-form path is run there; last, the same page given a
// stand-in Navigation API, as WebKit (Safari, the Mac app) has it.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { JSDOM } = require('jsdom');
const SOURCE = readFileSync(new URL('../../app/static/loader.js', import.meta.url), 'utf8');
const CSS = readFileSync(new URL('../../app/static/loader.css', import.meta.url), 'utf8');
const L = require('../../app/static/loader.js');

// Every page of a kind is one kind of load, whatever its number.
assert.equal(L.routeKey('get', '/colony/cage/12', 'http://lab.test/'), 'GET /colony/cage/:n');
assert.equal(L.routeKey('post', 'http://lab.test/proposals/7/approve?x=1'), 'POST /proposals/:n/approve');
assert.equal(L.routeKey('GET', '/notebook/3f2a9c1e-77b0-4c1d-9a55-0e2b1c4d5e6f', 'http://lab.test/'), 'GET /notebook/:id');

// Approving a proposal is known to be slow before it has ever been timed; anything else is not.
assert.equal(L.estimate({}, 'POST /proposals/:n/approve'), 3000);
assert.equal(L.estimate({}, 'GET /colony'), 0);
assert.equal(L.estimate({ 'GET /colony': 420 }, 'GET /colony'), 420);

// More than half a second expected: at once. Otherwise only once half a second has gone by,
// which loader.css counts itself (.ld-later): the same half second there.
assert.equal(L.THRESHOLD, 500);
assert.equal(L.delayFor(800), 0);
assert.equal(L.delayFor(500), L.THRESHOLD);
assert.equal(L.delayFor(0), L.THRESHOLD);
for (const [rule, frames] of [['.ld-overlay.ld-later { animation: ld-fade-later', 'ld-fade-later'], ['.ld-later .ld { animation: ld-enter-later', 'ld-enter-later']]) {
  const seconds = parseFloat(CSS.split(rule)[1]);
  const clear = parseFloat(CSS.split(`@keyframes ${frames} {`)[1].match(/0%, ([\d.]+)%/)[1]);
  assert.ok(Math.abs(seconds * clear * 10 - L.THRESHOLD) < 1, `${frames} stays clear for THRESHOLD`);
}

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

// Ten animations, never the same twice in a row; with none before, any of the ten.
assert.equal(L.LOADERS.length, 10);
for (let k = 0; k < 10; k += 1) assert.equal(L.pick(10, -1, () => (k + 0.5) / 10), k);
assert.equal(L.pick(10, NaN, () => 0.9999), 9);
let last = -1;
for (let n = 0; n < 200; n += 1) {
  const next = L.pick(10, last, Math.random);
  assert.ok(next >= 0 && next < 10 && next !== last);
  last = next;
}

// WebKit stops painting a page as soon as it starts to leave it; only the compositor goes on,
// and it moves and fades boxes, never the shapes inside an <svg>. So each moving piece is a
// box of its own (.ld-p), and no animation waits to start (a delay to come stays still there).
L.LOADERS.forEach((draw, i) => {
  const html = draw();
  assert.match(html, /<div class="ld-p ld-/, `animation ${i} has moving parts`);
  assert.doesNotMatch(html, /<(?:g|circle|ellipse|path|line|rect|polygon|polyline)\b[^>]*\bclass=/, `animation ${i}: something moving inside an <svg>`);
  assert.doesNotMatch(html, /\b(?:filter|mask)="url\(/, `animation ${i}: an SVG filter or mask stands still`);
  for (const [, d] of html.matchAll(/--d:(-?[\d.]+)s/g)) assert.ok(Number(d) < 4.8, `animation ${i}: a delay longer than a beat`);
});
assert.doesNotMatch(CSS, /transform-box/);
for (const [, value] of CSS.matchAll(/animation-delay:\s*([^;]+);/g)) {
  for (const one of value.split(',').map((v) => v.trim()))
    assert.match(one, /^(?:-[\d.]+s|var\(--d\)|calc\(var\(--dl?\) - [\d.]+s\))$/, `a delay still to come: ${one}`);
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
  assert.ok(!shown(w).classList.contains('ld-later'));
  assert.equal(shown(w).querySelector('.ld-label').textContent, 'Opening…');
  assert.ok(shown(w).querySelector('.ld-art .ld-stage .ld-p'));
  assert.equal(JSON.parse(w.sessionStorage.getItem('biomanager:loading')).key, 'GET /colony/cage/:n');
  w.BiomanagerLoader.stop();
  assert.equal(shown(w), null);
}

// A page that is usually quick: put in at once, but clear for its first half second
// (.ld-later, loader.css), so a quick page never flashes it.
{
  const w = page({ 'GET /colony/cage/:n': 300 });
  click(w, 'cage');
  await wait(w, 20);
  assert.ok(shown(w) && shown(w).classList.contains('ld-later'), 'no flash for a quick page');
  w.BiomanagerLoader.stop();
}

// Approving a proposal: slow from the start, and named by its form.
{
  const w = page();
  w.document.getElementById('approve').requestSubmit();
  await wait(w, 20);
  assert.ok(shown(w) && !shown(w).classList.contains('ld-later'));
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
  await wait(w, 50);
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

// ---- WebKit: the page held for one frame ---------------------------------------
// There the animation must be drawn before the page starts to go: the navigation is
// held (cancelled), and once a frame has passed sent again as it was.

function webkit(times, vendor) {
  const dom = new JSDOM(`<!doctype html><body>
    <form id="approve" method="post" action="/proposals/9/approve"><input name="csrf_token" value="abc"><textarea name="note"></textarea><button name="decision" value="approve">Approve</button></form>
  </body>`, { runScripts: 'outside-only', url: 'http://lab.test/proposals', virtualConsole: new (require('jsdom').VirtualConsole)() });
  const w = dom.window;
  w.t = (text) => text;
  if (vendor) Object.defineProperty(w.navigator, 'vendor', { get: () => vendor });
  if (times) w.localStorage.setItem('biomanager:load-times', JSON.stringify(times));
  const nav = new w.EventTarget();
  w.navigation = nav;
  const sent = [];
  w.HTMLFormElement.prototype.submit = function () { sent.push(this); };
  w.eval(SOURCE);
  const go = (url, more) => {
    const event = new w.Event('navigate', { cancelable: !(more && more.traverse) });
    Object.assign(event, {
      destination: { url, sameDocument: false }, navigationType: more && more.traverse ? 'traverse' : 'push',
      formData: (more && more.formData) || null, hashChange: false, downloadRequest: null, signal: new w.AbortController().signal,
    });
    nav.dispatchEvent(event);
    return event;
  };
  return { w, nav, sent, go };
}

// A form: held while the animation is drawn, then sent with what it held then, button and all.
{
  const { w, nav, sent, go } = webkit();
  const form = w.document.getElementById('approve');
  form.elements.note.value = 'two\nlines';
  const button = form.querySelector('button');
  form.addEventListener('submit', (event) => event.preventDefault());   // jsdom sends nothing itself
  form.requestSubmit(button);
  const data = new w.FormData(form);
  data.append(button.name, button.value);
  const event = go('http://lab.test/proposals/9/approve', { formData: data });
  assert.ok(event.defaultPrevented, 'held');
  assert.ok(shown(w), 'the animation goes in before the page is let go');
  nav.dispatchEvent(new w.Event('navigateerror'));   // what holding it reports
  assert.ok(shown(w), 'and stays');
  assert.equal(sent.length, 0);
  await wait(w, 150);
  assert.equal(sent.length, 1, 'a frame later, sent');
  const again = sent[0];
  assert.equal(again.getAttribute('method'), 'post');
  assert.equal(again.getAttribute('action'), 'http://lab.test/proposals/9/approve');
  assert.equal(again.getAttribute('enctype'), 'application/x-www-form-urlencoded');
  assert.deepEqual(Array.from(again.elements, (f) => [f.name, f.value]), [['csrf_token', 'abc'], ['note', 'two\nlines'], ['decision', 'approve']]);
  assert.equal(go('http://lab.test/proposals/9/approve', { formData: data }).defaultPrevented, false, 'and let through when it comes back');
  assert.ok(shown(w));
  w.BiomanagerLoader.stop();
}

// A link: held too. Back and forward can't be held, and Chromium, which goes on painting, never is.
{
  const { w, go } = webkit({ 'GET /colony/cage/:n': 2400 });
  assert.ok(go('http://lab.test/colony/cage/4').defaultPrevented);
  assert.ok(shown(w));
  w.BiomanagerLoader.stop();
  assert.equal(go('http://lab.test/colony/cage/5', { traverse: true }).defaultPrevented, false);
  assert.ok(shown(w));
  w.BiomanagerLoader.stop();
}
{
  const { w, go } = webkit({}, 'Google Inc.');
  assert.equal(go('http://lab.test/colony/cage/4').defaultPrevented, false);
  assert.ok(shown(w) && shown(w).classList.contains('ld-later'));
  w.BiomanagerLoader.stop();
}
