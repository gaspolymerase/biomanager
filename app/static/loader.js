/* A small animation while a page takes a while to come.
 *
 * Opening a record or sending a form is a whole page load here, so the
 * animation lives on the page being left: it shows in the content area
 * until the next page replaces it. Each kind of load ("GET /colony/cage/:n",
 * "POST /proposals/:n/approve") remembers how long it took on this computer:
 *
 *   - expected to take more than half a second: the animation shows at once;
 *   - expected to be quicker (or never seen): it shows only if the page is
 *     still not here after half a second, so a quick page never flashes it.
 *     The wait is in the animation itself (.ld-later, loader.css).
 *
 * Ten animations (loader.css), one picked at random each time, never the
 * same twice in a row in a window. Downloads, new tabs, links within the page and
 * anything under [data-no-loading] are left alone. A button or form can
 * name what it does with data-loading-label="…".
 *
 * WebKit (Safari, the Mac app) stops painting a page the moment it starts
 * to leave it. So every moving piece of an animation is a box of its own
 * (.ld-p), which the compositor goes on moving by itself, and there the
 * page is held for one frame, while the animation is drawn, before it goes.
 */
(function () {
  'use strict';

  const THRESHOLD = 500;                   // ms: longer than this shows the animation at once (also .ld-later in loader.css)
  const STORE = 'biomanager:load-times';   // localStorage: kind of load -> ms
  const PENDING = 'biomanager:loading';    // sessionStorage: the load under way, for the next page to time
  const LAST = 'biomanager:loader-last';   // sessionStorage: the animation shown last, not shown next
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
    if (!(last >= 0 && last < count)) return Math.min(count - 1, Math.floor(random() * count));
    let i = Math.floor(random() * (count - 1));
    if (i >= last) i += 1;
    return i;
  }

  /* --------------------------------------------------------- the ten animations */

  /* Each picture is 96 × 96 (.ld-stage). What stands still is drawn in an
   * <svg> over the whole picture; each piece that moves is a part, a box
   * over the whole picture too, with its own <svg>, so a piece's numbers
   * (and the point it turns or swells about, `at`) are the picture's own.
   * Nothing moves inside an <svg>: WebKit would leave that standing still. */
  const CANDY = ['var(--ld-coral)', 'var(--ld-lemon)', 'var(--ld-mint)', 'var(--ld-sky)', 'var(--ld-lilac)', 'var(--ld-pink)'];
  const svg = (body) => `<svg viewBox="0 0 96 96" aria-hidden="true" focusable="false">${body}</svg>`;
  const part = (cls, inner, at, style) => `<div class="ld-p ${cls}" style="${at ? `transform-origin:${at[0]}px ${at[1]}px;` : ''}${style || ''}">${inner}</div>`;
  const dot = (x, y, r, fill) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${fill}"/>`;
  const eyes = (x1, x2, y, r) => part('ld-blink', svg(dot(x1, y, r || 1.7, 'var(--ld-eye)') + dot(x2, y, r || 1.7, 'var(--ld-eye)')), [(x1 + x2) / 2, y]);

  const LOADERS = [
    // DNA: two strands turning, near beads larger than far ones.
    () => Array.from({ length: 10 }, (_, i) => {
      const x = 12 + i * 8, d = `--d:${(-i * 0.15).toFixed(2)}s`;
      return part('ld-rung', svg(`<line x1="${x}" y1="35" x2="${x}" y2="61" stroke="var(--ld-grey)" stroke-width="1.6" stroke-linecap="round"/>`), [x, 48], d)
        + part('ld-dna', svg(dot(x, 48, 3.4, 'var(--ld-sky)')), [x, 48], d)
        + part('ld-dna ld-dna-b', svg(dot(x, 48, 3.4, 'var(--ld-coral)')), [x, 48], d);
    }).join(''),

    // A peptide chain: each bead turns a small circle a moment after the one before.
    () => {
      const bead = (x, y, r, fill, d, extra) => part('ld-ox', part('ld-oy', svg(dot(x, y, r, fill)) + (extra || ''), null, `--d:${d}s`), null, `--d:${d}s`);
      const beads = Array.from({ length: 8 }, (_, i) => bead((13 + i * 8.2).toFixed(1), 52, 4.6, CANDY[i % 5], (-i * 0.17).toFixed(2))).join('');
      return beads + bead(81, 51, 6.6, 'var(--ld-pink)', '-1.43', eyes(79, 84.4, 49.6, 1.5));
    },

    // A membrane: a molecule bounces on the lipid bilayer, which gives under it.
    () => {
      const lipids = Array.from({ length: 9 }, (_, i) => {
        const x = 16 + i * 8, dx = x - 48;
        const dip = (4.2 * Math.exp(-((dx / 12) ** 2))).toFixed(2), wait = (Math.abs(dx) / 8 * 0.035).toFixed(3);
        return part('ld-lip', svg(`<g stroke="var(--ld-grey)" stroke-width="1.2" stroke-linecap="round">`
          + `<line x1="${x - 1.3}" y1="52" x2="${x - 1.3}" y2="58"/><line x1="${x + 1.3}" y1="52" x2="${x + 1.3}" y2="58"/>`
          + `<line x1="${x - 1.3}" y1="62" x2="${x - 1.3}" y2="68"/><line x1="${x + 1.3}" y1="62" x2="${x + 1.3}" y2="68"/></g>`
          + dot(x, 49, 3.7, 'var(--ld-sky)') + dot(x, 71, 3.7, 'var(--ld-coral)')), null, `--dip:${dip};--dl:${wait}s`);
      }).join('');
      return lipids + part('ld-ball', svg(dot(48, 39.3, 6.2, 'var(--ld-lemon)')) + eyes(45.7, 50.3, 38.6, 1.3), [48, 45.5]);
    },

    // A cell divides; one daughter drifts off, the other becomes the next mother.
    // The waist between the two while they part is a third shape (.ld-mi-n).
    () => part('ld-mi-n', svg('<ellipse cx="48" cy="48" rx="8" ry="13" fill="var(--ld-pink)"/>'), [48, 48])
      + part('ld-mi-a', svg(dot(48, 48, 16, 'var(--ld-pink)')) + part('ld-eyes-a', eyes(44.6, 51.4, 47)), [48, 48])
      + part('ld-mi-b', svg(dot(48, 48, 16, 'var(--ld-pink)')) + eyes(44.6, 51.4, 47), [48, 48]),

    // A mouse sniffs twice and pauses; the whiskers follow the nose.
    () => svg('<ellipse cx="46" cy="77" rx="23" ry="2.2" fill="var(--ld-grey)" opacity=".55"/>')
      + part('ld-m-tail', svg('<path d="M27 66 C17 68 12 76 16 82 C19 86 26 84 30 86" fill="none" stroke="var(--ld-pink)" stroke-width="2.2" stroke-linecap="round"/>'), [27, 66])
      + svg('<ellipse cx="37" cy="75" rx="4.2" ry="2" fill="var(--ld-pink)"/><ellipse cx="52" cy="75" rx="4.2" ry="2" fill="var(--ld-pink)"/>')
      + part('ld-m-body', svg('<ellipse cx="44" cy="62" rx="19" ry="13" fill="var(--ld-fur)"/>'), [44, 75])
      + part('ld-m-head',
        part('ld-m-ear', svg(dot(57, 41, 6.6, 'var(--ld-fur)') + dot(57, 41, 3.7, 'var(--ld-pink)')), [58, 46])
        + svg(dot(61, 52, 10, 'var(--ld-fur)') + '<ellipse cx="70" cy="55" rx="7.2" ry="5.6" fill="var(--ld-fur)"/>')
        + part('ld-blink', svg(dot(66, 49.5, 1.8, 'var(--ld-eye)')), [66, 49.5])
        + part('ld-m-whisk', svg('<g stroke="var(--ld-grey)" stroke-width="1" stroke-linecap="round"><line x1="75" y1="55" x2="88" y2="51"/><line x1="75" y1="56" x2="88" y2="58.5"/></g>'), [75, 55])
        + part('ld-m-nose', svg(dot(77, 55, 2, 'var(--ld-pink)')), [77, 55]),
        [56, 56]),

    // A bacterium: a wave runs back along its flagellum, which fades out (.ld-fade-tail).
    () => part('ld-mote', svg(dot(88, 32, 1.4, 'var(--ld-sky)')), null, '--d:0s')
      + part('ld-mote', svg(dot(92, 66, 1.2, 'var(--ld-lemon)')), null, '--d:1.2s')
      + part('ld-bac',
        part('ld-fade-tail', part('ld-flag', svg('<path d="M-16 48 q4 -9 8 0 t8 0 t8 0 t8 0 t8 0 t8 0 t8 0 t8 0 t8 0 t8 0" fill="none" stroke="var(--ld-lilac)" stroke-width="1.8" stroke-linecap="round"/>')))
        + svg('<rect x="38" y="37" width="36" height="22" rx="11" fill="var(--ld-lilac)"/>') + eyes(62, 68.4, 46),
        [56, 48]),

    // A petri dish: colonies come up one by one, then go.
    () => {
      const spots = [[37, 39, 5.2, 0], [60, 36, 3.8, 4], [57, 59, 6.6, 1], [35, 60, 3.4, 3], [48, 49, 2.8, 5], [66, 48, 2.6, 2], [46, 67, 2.4, 0]];
      return svg('<circle cx="48" cy="48" r="31" fill="var(--ld-grey-l)"/><circle cx="48" cy="48" r="34" fill="none" stroke="var(--ld-grey)" stroke-width="2"/>')
        + spots.map(([x, y, r, c], i) => part('ld-col', svg(dot(x, y, r, CANDY[c])), [x, y], `--d:${(i * 0.3).toFixed(1)}s`)).join('');
    },

    // A zebrafish: head, body and tail swing on one beat, each a little later.
    () => {
      const stripe = (x1, y, x2) => `<line x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" stroke="var(--ld-sky)" stroke-width="1.6" stroke-linecap="round"/>`;
      const bubble = (r, w, d) => part('ld-z-bub', svg(`<circle cx="83" cy="45" r="${r}" fill="none" stroke="var(--ld-sky)" stroke-width="${w}"/>`), [83, 45], `--d:${d}s`);
      const tail = part('ld-z-t', svg('<path d="M30 48 C25 42 19 38.5 13 38.5 C15.5 44 15.5 52 13 57.5 C19 57.5 25 54 30 48 Z" fill="var(--ld-lemon)" opacity=".7"/>'
        + '<ellipse cx="32" cy="48" rx="7.5" ry="4.2" fill="var(--ld-lemon)"/>' + stripe(27, 48, 37)), [35, 48]);
      const middle = part('ld-z-m', tail + svg('<path d="M40 42 Q45 35.5 52 40.5" fill="var(--ld-lemon)" opacity=".7"/><ellipse cx="44" cy="48" rx="11.5" ry="7.4" fill="var(--ld-lemon)"/>'
        + stripe(35, 45.4, 54) + stripe(34, 50.6, 54)), [50, 48]);
      const head = part('ld-z-h', middle + svg('<path d="M50 40.6 C62 38.6 74 41.4 80 47 C81 48 81 49 80 50 C74 55.4 62 57.6 50 55.6 Z" fill="var(--ld-lemon)"/>'
        + stripe(52, 45.4, 67) + stripe(52, 50.6, 65)) + part('ld-blink', svg(dot(72.5, 46.4, 2, 'var(--ld-eye)')), [72.5, 46.4]), [60, 48]);
      return bubble(1.8, 1.2, 0.4) + bubble(1.2, 1.1, 1.3) + part('ld-z-all', head);
    },

    // A pipette: a drop swells, lets go and falls; the surface rings.
    // A ring is drawn at its widest and shrunk, so it stays sharp as it grows.
    () => {
      const ring = (d) => part('ld-ring', svg('<ellipse cx="48" cy="79" rx="22" ry="4.48" fill="none" stroke="var(--ld-sky)" stroke-width="2"/>'), [48, 79], `--d:${d}s`);
      return part('ld-pool', svg('<ellipse cx="48" cy="79" rx="28" ry="4.5" fill="var(--ld-sky-l)"/>'), [48, 79])
        + ring(1.92) + ring(2.14)
        + part('ld-drop', svg('<path fill="var(--ld-sky)" d="M48 29 C50.8 33 52.5 35.4 52.5 37.6 A4.5 4.5 0 0 1 43.5 37.6 C43.5 35.4 45.2 33 48 29 Z"/>'), [48, 29])
        + svg('<path d="M42.5 4 H53.5 L50 28 Q48 31 46 28 Z" fill="var(--ld-lemon)"/>');
    },

    // A well plate, filled column by column.
    () => {
      const wells = Array.from({ length: 24 }, (_, k) => {
        const c = k % 6, r = Math.floor(k / 6);
        return { c, x: +(20 + c * 11.2).toFixed(1), y: +(32 + r * 10.7).toFixed(1), d: (c * 0.16 + r * 0.035).toFixed(3) };
      });
      return svg('<rect x="9" y="23" width="78" height="50" rx="7" fill="none" stroke="var(--ld-grey)" stroke-width="1.8"/>'
        + wells.map((w) => dot(w.x, w.y, 3.9, 'var(--ld-grey-l)')).join(''))
        + wells.map((w) => part('ld-well', svg(dot(w.x, w.y, 3.9, CANDY[w.c])), [w.x, w.y], `--d:${w.d}s`)).join('');
    },
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

  let giveUp = 0, overlay = null;

  // Put in at once; one expected to be quick (later) stays clear for the first half second.
  function show(label, later) {
    let last = -1;
    try { last = parseInt(sessionStorage.getItem(LAST), 10); } catch (e) { /* storage off */ }
    last = pick(LOADERS.length, last, Math.random);
    try { sessionStorage.setItem(LAST, String(last)); } catch (e) { /* storage off */ }
    overlay = document.createElement('div');
    overlay.className = later ? 'ld-overlay ld-later' : 'ld-overlay';
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
    card.innerHTML = `<div class="ld-art"><div class="ld-stage">${LOADERS[last]()}</div></div>`;
    const text = document.createElement('div');
    text.className = 'ld-label';
    text.textContent = label;
    card.appendChild(text);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
  }

  function stop() {
    clearTimeout(giveUp);
    giveUp = 0;
    if (overlay) { overlay.remove(); overlay = null; }
    try { sessionStorage.removeItem(PENDING); } catch (e) { /* storage off */ }
  }

  function start(method, href, label) {
    if (overlay) return;
    const key = routeKey(method, href, location.href);
    try { sessionStorage.setItem(PENDING, JSON.stringify({ key, t: Date.now() })); } catch (e) { /* storage off */ }
    const expected = estimate(readTimes(), key);
    show(label, delayFor(expected) > 0);
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

  /* ------------------------------------------- WebKit: one frame to draw it in */

  const HOLD = /Apple/.test(navigator.vendor || '');   // Safari and the Mac app; not Chromium or Firefox
  let held = null;       // the navigation waiting for its frame: { href, go }
  let released = null;   // its address, once let go, so it passes here untouched

  // After the next frame is painted (or a tenth of a second, for a window out of sight).
  function afterPaint(go) {
    let done = false;
    const once = () => { if (!done) { done = true; go(); } };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => setTimeout(once, 0));
    setTimeout(once, 100);
  }

  // A form sent again, exactly as the browser was about to send it: its fields
  // as they were (event.formData), its button's among them, to the same address.
  function resubmit(href, data, source) {
    const own = source instanceof HTMLFormElement ? source : source && source.form;
    const declared = (source && source.getAttribute && source.getAttribute('formenctype')) || (own && own.getAttribute('enctype')) || '';
    const form = document.createElement('form');
    form.setAttribute('method', 'post');
    form.setAttribute('action', href);
    form.hidden = true;
    let files = false;
    for (const [name, value] of data) {
      if (typeof value === 'string') {
        const field = document.createElement('textarea');   // keeps line breaks as they were
        field.name = name;
        field.value = value;
        form.appendChild(field);
      } else {
        files = true;
        const field = document.createElement('input');
        field.type = 'file';
        field.name = name;
        if (value.name || value.size) {                       // an empty one is sent empty, as before
          const list = new DataTransfer();
          list.items.add(value);
          field.files = list.files;
        }
        form.appendChild(field);
      }
    }
    form.setAttribute('enctype', files || /multipart/i.test(declared) ? 'multipart/form-data' : (declared || 'application/x-www-form-urlencoded'));
    return () => {
      document.body.appendChild(form);
      HTMLFormElement.prototype.submit.call(form);
    };
  }

  // A file chosen in a form can be put in another only by way of a DataTransfer.
  const canResend = (data) => typeof DataTransfer === 'function' || Array.from(data.values()).every((value) => typeof value === 'string');

  function hold(event, href, post, source) {
    if (!HOLD || !event.cancelable || (event.navigationType !== 'push' && event.navigationType !== 'replace')) return false;
    if (post && !canResend(event.formData)) return false;
    let go;
    try {
      if (post) go = resubmit(href, event.formData, source);
      else if (event.navigationType === 'replace') go = () => location.replace(href);
      else go = () => location.assign(href);
    } catch (e) { return false; }   // can't be sent again: let it go now, as before
    event.preventDefault();
    // A second click in the same frame takes the place of the first, as it would have.
    if (!held) {
      afterPaint(() => {
        if (!held) return;
        const next = held;
        held = null;
        released = next.href;
        try { next.go(); } catch (e) { released = null; stop(); }
      });
    }
    held = { href, go };
    return true;
  }

  /* ---------------------------------------------------- every way a page is left */

  const watch = (event) => { if (event.signal) event.signal.addEventListener('abort', stop); };

  const nav = window.navigation;
  if (nav && typeof nav.addEventListener === 'function') {
    // Every way a page is left (a link, a form, a script, back and forward) passes here.
    nav.addEventListener('navigate', (event) => {
      if (event.hashChange || event.downloadRequest != null) return;
      if (!event.destination || event.destination.sameDocument) return;
      const url = new URL(event.destination.url);
      if (url.origin !== location.origin || isDownload(url.href)) return;
      if (released === url.href) { released = null; watch(event); return; }
      const post = !!event.formData;
      const source = post ? recent(lastSubmit) : recent(lastClick);
      if (leftAlone(source)) return;
      start(post ? 'POST' : 'GET', url.href, labelFor(post, source));
      if (hold(event, url.href, post, source)) return;
      held = null;   // back or forward while one was waiting: that one goes, not the one held
      watch(event);
    });
    // Holding one cancels it, which reports an error too: that one is still to go.
    nav.addEventListener('navigateerror', () => { if (!held) stop(); });
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
