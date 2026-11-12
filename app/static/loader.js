/* A small animation while a page takes a while to come.
 *
 * Opening a record or sending a form is a whole page load here, so the
 * animation lives on the page being left: it shows in the content area
 * until the next page replaces it. Each kind of load ("GET /colony/cage/:n",
 * "POST /proposals/:n/approve") remembers how long it took on this computer:
 *
 *   - expected to take more than a second: the animation shows at once;
 *   - expected to be quicker (or never seen): it shows only if the page is
 *     still not here after a second, so a quick page never flashes it.
 *
 * Ten animations (loader.css), one picked at random each time, never the
 * same twice in a row. Downloads, new tabs, links within the page and
 * anything under [data-no-loading] are left alone. A button or form can
 * name what it does with data-loading-label="…".
 */
(function () {
  'use strict';

  const THRESHOLD = 1000;                  // ms: longer than this shows the animation at once
  const STORE = 'biomanager:load-times';   // localStorage: kind of load -> ms
  const PENDING = 'biomanager:loading';    // sessionStorage: the load under way, for the next page to time
  const KEEP = 200;                        // kinds of load remembered
  const GIVE_UP = 15000;                   // ms: a page that never came (a download, a stop) lets go
  // How long a kind of load takes before this computer has timed one.
  const SEEDS = { 'POST /proposals/:n/approve': 3000 };
  // Pages that answer with a file rather than a page (exports, labels, sequence files).
  const DOWNLOAD = /\.[a-z0-9]{2,5}$|(?:^|\/)(?:export|download|lab-file|snapshot)(?:[./]|$)/i;

  /* One name for every page of a kind: numbers and ids become :n and :id. */
  function routeKey(method, href, base) {
    const url = new URL(href, base);
    const path = url.pathname.split('/').map((part) => {
      if (/^\d+$/.test(part)) return ':n';
      if (/^[0-9a-f-]{16,}$/i.test(part)) return ':id';
      return part;
    }).join('/');
    return String(method || 'GET').toUpperCase() + ' ' + path;
  }

  function estimate(times, key) {
    if (times && Object.prototype.hasOwnProperty.call(times, key)) return times[key];
    return SEEDS[key] || 0;
  }

  /* The newest time counts as much as all the earlier ones; the oldest kinds drop off. */
  function remember(times, key, ms) {
    const next = Object.assign({}, times);
    const before = next[key];
    delete next[key];
    next[key] = Math.round(before == null ? ms : (before + ms) / 2);
    const keys = Object.keys(next);
    keys.slice(0, Math.max(0, keys.length - KEEP)).forEach((k) => { delete next[k]; });
    return next;
  }

  function delayFor(expected) { return expected > THRESHOLD ? 0 : THRESHOLD; }

  function isDownload(href, base) { return DOWNLOAD.test(new URL(href, base).pathname); }

  function pick(count, last, random) {
    if (count < 2) return 0;
    let i = Math.floor(random() * (count - 1));
    if (i >= last && last >= 0) i += 1;
    return i;
  }

  /* --------------------------------------------------------- the ten animations */

  const CANDY = ['var(--ld-coral)', 'var(--ld-lemon)', 'var(--ld-mint)', 'var(--ld-sky)', 'var(--ld-lilac)', 'var(--ld-pink)'];
  const eyes = (x1, x2, y, r) => `<g class="ld-blink"><circle cx="${x1}" cy="${y}" r="${r || 1.7}" fill="var(--ld-eye)"/><circle cx="${x2}" cy="${y}" r="${r || 1.7}" fill="var(--ld-eye)"/></g>`;
  const svg = (body, defs) => `<svg viewBox="0 0 96 96" aria-hidden="true" focusable="false">${defs ? `<defs>${defs}</defs>` : ''}${body}</svg>`;

  const LOADERS = [
    // DNA: two strands turning, near beads larger than far ones.
    () => svg(Array.from({ length: 10 }, (_, i) => {
      const x = 12 + i * 8;
      return `<g style="--d:${(-i * 0.15).toFixed(2)}s"><line class="ld-rung" x1="${x}" y1="35" x2="${x}" y2="61" stroke="var(--ld-grey)" stroke-width="1.6" stroke-linecap="round"/>`
        + `<circle class="ld-dna" cx="${x}" cy="48" r="3.4" fill="var(--ld-sky)"/><circle class="ld-dna ld-dna-b" cx="${x}" cy="48" r="3.4" fill="var(--ld-coral)"/></g>`;
    }).join('')),

    // A peptide chain: each bead turns a small circle a moment after the one before.
    () => {
      const bead = (x, y, r, fill, d, extra) => `<g class="ld-ox" style="--d:${d}s"><g class="ld-oy" style="--d:${d}s"><circle cx="${x}" cy="${y}" r="${r}" fill="${fill}"/>${extra || ''}</g></g>`;
      const beads = Array.from({ length: 8 }, (_, i) => bead((13 + i * 8.2).toFixed(1), 52, 4.6, CANDY[i % 5], (-i * 0.17).toFixed(2))).join('');
      return svg(beads + bead(81, 51, 6.6, 'var(--ld-pink)', '-1.43', eyes(79, 84.4, 49.6, 1.5)));
    },

    // A membrane: a molecule bounces on the lipid bilayer, which gives under it.
    () => {
      const lipids = Array.from({ length: 9 }, (_, i) => {
        const x = 16 + i * 8, dx = x - 48;
        const dip = (4.2 * Math.exp(-((dx / 12) ** 2))).toFixed(2), wait = (Math.abs(dx) / 8 * 0.035).toFixed(3);
        return `<g class="ld-lip" style="--dip:${dip};--dl:${wait}s"><g stroke="var(--ld-grey)" stroke-width="1.2" stroke-linecap="round">`
          + `<line x1="${x - 1.3}" y1="52" x2="${x - 1.3}" y2="58"/><line x1="${x + 1.3}" y1="52" x2="${x + 1.3}" y2="58"/>`
          + `<line x1="${x - 1.3}" y1="62" x2="${x - 1.3}" y2="68"/><line x1="${x + 1.3}" y1="62" x2="${x + 1.3}" y2="68"/></g>`
          + `<circle cx="${x}" cy="49" r="3.7" fill="var(--ld-sky)"/><circle cx="${x}" cy="71" r="3.7" fill="var(--ld-coral)"/></g>`;
      }).join('');
      return svg(lipids + `<g class="ld-ball"><circle cx="48" cy="39.3" r="6.2" fill="var(--ld-lemon)"/>${eyes(45.7, 50.3, 38.6, 1.3)}</g>`);
    },

    // A cell divides; one daughter drifts off, the other becomes the next mother.
    (u) => svg(
      `<g filter="url(#ld-goo${u})" fill="var(--ld-pink)"><circle class="ld-mi-a" cx="48" cy="48" r="16"/><circle class="ld-mi-b" cx="48" cy="48" r="16"/></g>`
      + `<g class="ld-mi-a"><g class="ld-eyes-a">${eyes(44.6, 51.4, 47)}</g></g><g class="ld-mi-b">${eyes(44.6, 51.4, 47)}</g>`,
      `<filter id="ld-goo${u}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur in="SourceGraphic" stdDeviation="3"/><feColorMatrix values="1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 22 -10"/></filter>`),

    // A mouse sniffs twice and pauses; the whiskers follow the nose.
    () => svg(
      '<ellipse cx="46" cy="77" rx="23" ry="2.2" fill="var(--ld-grey)" opacity=".55"/>'
      + '<path class="ld-m-tail" d="M27 66 C17 68 12 76 16 82 C19 86 26 84 30 86" fill="none" stroke="var(--ld-pink)" stroke-width="2.2" stroke-linecap="round"/>'
      + '<ellipse cx="37" cy="75" rx="4.2" ry="2" fill="var(--ld-pink)"/><ellipse cx="52" cy="75" rx="4.2" ry="2" fill="var(--ld-pink)"/>'
      + '<ellipse class="ld-m-body" cx="44" cy="62" rx="19" ry="13" fill="var(--ld-fur)"/>'
      + '<g class="ld-m-head"><g class="ld-m-ear"><circle cx="57" cy="41" r="6.6" fill="var(--ld-fur)"/><circle cx="57" cy="41" r="3.7" fill="var(--ld-pink)"/></g>'
      + '<circle cx="61" cy="52" r="10" fill="var(--ld-fur)"/><ellipse cx="70" cy="55" rx="7.2" ry="5.6" fill="var(--ld-fur)"/>'
      + '<g class="ld-blink"><circle cx="66" cy="49.5" r="1.8" fill="var(--ld-eye)"/></g>'
      + '<g class="ld-m-whisk" stroke="var(--ld-grey)" stroke-width="1" stroke-linecap="round"><line x1="75" y1="55" x2="88" y2="51"/><line x1="75" y1="56" x2="88" y2="58.5"/></g>'
      + '<circle class="ld-m-nose" cx="77" cy="55" r="2" fill="var(--ld-pink)"/></g>'),

    // A bacterium: a wave runs back along its flagellum.
    (u) => svg(
      '<circle class="ld-mote" cx="88" cy="32" r="1.4" fill="var(--ld-sky)" style="--d:0s"/><circle class="ld-mote" cx="92" cy="66" r="1.2" fill="var(--ld-lemon)" style="--d:1.2s"/>'
      + `<g class="ld-bac"><g mask="url(#ld-fm${u})"><path class="ld-flag" d="M-16 48 q4 -9 8 0 t8 0 t8 0 t8 0 t8 0 t8 0 t8 0 t8 0 t8 0 t8 0" fill="none" stroke="var(--ld-lilac)" stroke-width="1.8" stroke-linecap="round"/></g>`
      + `<rect x="38" y="37" width="36" height="22" rx="11" fill="var(--ld-lilac)"/>${eyes(62, 68.4, 46)}</g>`,
      `<linearGradient id="ld-fg${u}" x1="4" x2="40" y1="0" y2="0" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".7" stop-color="#fff"/></linearGradient>`
      + `<mask id="ld-fm${u}" maskUnits="userSpaceOnUse" x="0" y="0" width="96" height="96"><rect width="96" height="96" fill="url(#ld-fg${u})"/></mask>`),

    // A petri dish: colonies come up one by one, then go.
    () => {
      const spots = [[37, 39, 5.2, 0], [60, 36, 3.8, 4], [57, 59, 6.6, 1], [35, 60, 3.4, 3], [48, 49, 2.8, 5], [66, 48, 2.6, 2], [46, 67, 2.4, 0]];
      return svg('<circle cx="48" cy="48" r="31" fill="var(--ld-grey-l)"/><circle cx="48" cy="48" r="34" fill="none" stroke="var(--ld-grey)" stroke-width="2"/>'
        + spots.map(([x, y, r, c], i) => `<circle class="ld-col" cx="${x}" cy="${y}" r="${r}" fill="${CANDY[c]}" style="--d:${(i * 0.3).toFixed(1)}s"/>`).join(''));
    },

    // A zebrafish: head, body and tail swing on one beat, each a little later.
    () => {
      const stripe = (x1, y, x2) => `<line x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" stroke="var(--ld-sky)" stroke-width="1.6" stroke-linecap="round"/>`;
      return svg(
        '<circle class="ld-z-bub" cx="83" cy="45" r="1.8" fill="none" stroke="var(--ld-sky)" stroke-width="1.2" style="--d:.4s"/>'
        + '<circle class="ld-z-bub" cx="83" cy="45" r="1.2" fill="none" stroke="var(--ld-sky)" stroke-width="1.1" style="--d:1.3s"/>'
        + '<g class="ld-z-all"><g class="ld-z-h"><g class="ld-z-m"><g class="ld-z-t">'
        + '<path d="M30 48 C25 42 19 38.5 13 38.5 C15.5 44 15.5 52 13 57.5 C19 57.5 25 54 30 48 Z" fill="var(--ld-lemon)" opacity=".7"/>'
        + '<ellipse cx="32" cy="48" rx="7.5" ry="4.2" fill="var(--ld-lemon)"/>' + stripe(27, 48, 37) + '</g>'
        + '<path d="M40 42 Q45 35.5 52 40.5" fill="var(--ld-lemon)" opacity=".7"/><ellipse cx="44" cy="48" rx="11.5" ry="7.4" fill="var(--ld-lemon)"/>'
        + stripe(35, 45.4, 54) + stripe(34, 50.6, 54) + '</g>'
        + '<path d="M50 40.6 C62 38.6 74 41.4 80 47 C81 48 81 49 80 50 C74 55.4 62 57.6 50 55.6 Z" fill="var(--ld-lemon)"/>'
        + stripe(52, 45.4, 67) + stripe(52, 50.6, 65)
        + '<g class="ld-blink"><circle cx="72.5" cy="46.4" r="2" fill="var(--ld-eye)"/></g></g></g>');
    },

    // A pipette: a drop swells, lets go and falls; the surface rings.
    () => svg(
      '<ellipse class="ld-pool" cx="48" cy="79" rx="28" ry="4.5" fill="var(--ld-sky-l)"/>'
      + '<ellipse class="ld-ring" cx="48" cy="79" rx="5" ry="1.4" fill="none" stroke="var(--ld-sky)" stroke-width="1.3" vector-effect="non-scaling-stroke" style="--d:1.92s"/>'
      + '<ellipse class="ld-ring" cx="48" cy="79" rx="5" ry="1.4" fill="none" stroke="var(--ld-sky)" stroke-width="1.3" vector-effect="non-scaling-stroke" style="--d:2.14s"/>'
      + '<path class="ld-drop" fill="var(--ld-sky)" d="M48 29 C50.8 33 52.5 35.4 52.5 37.6 A4.5 4.5 0 0 1 43.5 37.6 C43.5 35.4 45.2 33 48 29 Z"/>'
      + '<path d="M42.5 4 H53.5 L50 28 Q48 31 46 28 Z" fill="var(--ld-lemon)"/>'),

    // A well plate, filled column by column.
    () => svg('<rect x="9" y="23" width="78" height="50" rx="7" fill="none" stroke="var(--ld-grey)" stroke-width="1.8"/>'
      + Array.from({ length: 24 }, (_, k) => {
        const c = k % 6, r = Math.floor(k / 6), x = (20 + c * 11.2).toFixed(1), y = (32 + r * 10.7).toFixed(1);
        return `<circle cx="${x}" cy="${y}" r="3.9" fill="var(--ld-grey-l)"/><circle class="ld-well" cx="${x}" cy="${y}" r="3.9" fill="${CANDY[c]}" style="--d:${(c * 0.16 + r * 0.035).toFixed(3)}s"/>`;
      }).join('')),
  ];

  const api = { routeKey, estimate, remember, delayFor, isDownload, pick, THRESHOLD, LOADERS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window === 'undefined' || typeof document === 'undefined') return;

  /* -------------------------------------------------------------- on the page */

  const say = (text) => (typeof window.t === 'function' ? window.t(text) : text);

  function readTimes() {
    try { return JSON.parse(localStorage.getItem(STORE) || '{}') || {}; } catch (e) { return {}; }
  }

  // The page this window was waiting for has come: remember how long it took.
  (function timeArrival() {
    let pending = null;
    try {
      pending = JSON.parse(sessionStorage.getItem(PENDING) || 'null');
      sessionStorage.removeItem(PENDING);
    } catch (e) { return; }
    if (!pending || typeof pending.key !== 'string' || typeof pending.t !== 'number') return;
    const took = Date.now() - pending.t;
    if (took < 0 || took > 120000) return;
    try { localStorage.setItem(STORE, JSON.stringify(remember(readTimes(), pending.key, took))); } catch (e) { /* storage off */ }
  })();

  let timer = 0, giveUp = 0, overlay = null, last = -1, uid = 0;

  function show(label) {
    last = pick(LOADERS.length, last, Math.random);
    overlay = document.createElement('div');
    overlay.className = 'ld-overlay';
    overlay.setAttribute('role', 'status');
    // Over the page's content, beneath the tabs and the toolbar, which stay as they are.
    const area = document.getElementById('app-scroll');
    if (area) {
      const box = area.getBoundingClientRect();
      const bars = parseFloat(getComputedStyle(area).paddingTop) || 0;
      overlay.classList.add('ld-in-page');
      overlay.style.left = box.left + 'px';
      overlay.style.width = box.width + 'px';
      overlay.style.top = (box.top + bars) + 'px';
      overlay.style.height = Math.max(0, box.height - bars) + 'px';
    }
    const card = document.createElement('div');
    card.className = 'ld';
    card.innerHTML = `<div class="ld-art">${LOADERS[last](++uid)}</div>`;
    const text = document.createElement('div');
    text.className = 'ld-label';
    text.textContent = label;
    card.appendChild(text);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
  }

  function stop() {
    clearTimeout(timer);
    clearTimeout(giveUp);
    timer = giveUp = 0;
    if (overlay) { overlay.remove(); overlay = null; }
    try { sessionStorage.removeItem(PENDING); } catch (e) { /* storage off */ }
  }

  function start(method, href, label) {
    if (timer || overlay) return;
    const key = routeKey(method, href, location.href);
    try { sessionStorage.setItem(PENDING, JSON.stringify({ key, t: Date.now() })); } catch (e) { /* storage off */ }
    const expected = estimate(readTimes(), key);
    timer = setTimeout(() => { timer = 0; show(label); }, delayFor(expected));
    giveUp = setTimeout(stop, Math.max(GIVE_UP, expected * 3));
  }

  function labelFor(post, source) {
    const named = source && source.closest && source.closest('[data-loading-label]');
    if (named) return named.getAttribute('data-loading-label');
    return post ? say('Working…') : say('Opening…');
  }

  function leftAlone(source) {
    return !!(source && source.closest && source.closest('[data-no-loading]'));
  }

  // Back to this page from the browser's cache: nothing is loading any more.
  window.addEventListener('pageshow', (event) => { if (event.persisted) stop(); });

  // What started the load: the link or button pressed, the form's button.
  let lastClick = null, lastSubmit = null;
  window.addEventListener('click', (event) => {
    lastClick = { el: event.target instanceof Element ? event.target : null, at: Date.now() };
  }, true);
  window.addEventListener('submit', (event) => {
    lastSubmit = { el: event.submitter || event.target, at: Date.now() };
  }, true);
  const recent = (seen) => (seen && Date.now() - seen.at < 1000 ? seen.el : null);

  const nav = window.navigation;
  if (nav && typeof nav.addEventListener === 'function') {
    // Every way a page is left (a link, a form, a script, back and forward) passes here.
    nav.addEventListener('navigate', (event) => {
      if (event.hashChange || event.downloadRequest != null) return;
      if (!event.destination || event.destination.sameDocument) return;
      const url = new URL(event.destination.url);
      if (url.origin !== location.origin || isDownload(url.href)) return;
      const post = !!event.formData;
      const source = post ? recent(lastSubmit) : recent(lastClick);
      if (leftAlone(source)) return;
      start(post ? 'POST' : 'GET', url.href, labelFor(post, source));
      if (event.signal) event.signal.addEventListener('abort', stop);
    });
    nav.addEventListener('navigateerror', stop);
  } else {
    // Without the Navigation API: links and forms. Waits a turn, so a handler
    // that keeps the page (preventDefault) after this one is still heard.
    window.addEventListener('click', (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
      if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self') || leftAlone(link)) return;
      const url = new URL(link.href, location.href);
      if (url.origin !== location.origin || isDownload(url.href)) return;
      if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
      setTimeout(() => { if (!event.defaultPrevented) start('GET', url.href, labelFor(false, link)); }, 0);
    });
    window.addEventListener('submit', (event) => {
      if (event.defaultPrevented) return;
      const form = event.target, button = event.submitter;
      if (!(form instanceof HTMLFormElement) || leftAlone(button || form)) return;
      const target = (button && button.getAttribute('formtarget')) || form.getAttribute('target');
      if (target && target !== '_self') return;
      const method = ((button && button.getAttribute('formmethod')) || form.getAttribute('method') || 'get').toUpperCase();
      if (method === 'DIALOG') return;
      const action = (button && button.getAttribute('formaction')) || form.action || location.href;
      if (isDownload(action, location.href)) return;
      setTimeout(() => { if (!event.defaultPrevented) start(method, action, labelFor(method === 'POST', button || form)); }, 0);
    });
  }

  window.BiomanagerLoader = { start, stop };
})();
