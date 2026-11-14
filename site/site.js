// Running: lets the stylesheet hide what the script will bring in.
document.documentElement.classList.add('js');
// Every page: the English ⇄ 中文 link remembers the choice, so a Chinese
// browser is not sent back to the Chinese page after picking English.
// Guide pages: highlight the section being read in the contents, and
// copy buttons (<button data-copy="id-of-element">).
(function () {
  document.querySelectorAll('a[data-lang]').forEach(function (a) {
    a.addEventListener('click', function () {
      try { localStorage.setItem('bm-lang', a.dataset.lang); } catch (e) {}
      if (location.hash && a.getAttribute('href').indexOf('#') < 0) a.href = a.getAttribute('href') + location.hash;
    });
  });
  // The contents: always open beside the text on a wide screen; on a phone
  // a closed box above it (it is a screen tall), closed again after a jump.
  var box = document.querySelector('.toc-box');
  if (box && window.matchMedia) {
    var wide = window.matchMedia('(min-width: 960px)');
    var fit = function () { box.open = wide.matches; };
    fit();
    if (wide.addEventListener) wide.addEventListener('change', fit);
    box.addEventListener('click', function (e) {
      if (!wide.matches && e.target.closest('a')) box.open = false;
    });
  }
  var links = {};
  document.querySelectorAll('.toc a').forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && links[e.target.id]) {
          document.querySelectorAll('.toc a.on').forEach(function (a) { a.classList.remove('on'); });
          links[e.target.id].classList.add('on');
        }
      });
    }, { rootMargin: '-70px 0px -70% 0px' });
    document.querySelectorAll('.guide-body section[id], .guide-body h3[id]').forEach(function (s) { io.observe(s); });
  }
  document.querySelectorAll('[data-copy]').forEach(function (button) {
    button.addEventListener('click', function () {
      var source = document.getElementById(button.dataset.copy);
      if (!source || !navigator.clipboard) return;
      navigator.clipboard.writeText(source.innerText.trim()).then(function () {
        var was = button.textContent;
        button.textContent = document.documentElement.lang.indexOf('zh') === 0 ? '已复制' : 'Copied';
        setTimeout(function () { button.textContent = was; }, 1500);
      });
    });
  });
})();

// The header: dark while it is over the front page's dark opening.
(function () {
  var nav = document.querySelector('.nav'), band = document.querySelector('.hero-night');
  if (!nav || !band) return;
  var ticking = false;
  var update = function () {
    ticking = false;
    nav.classList.toggle('on-dark', band.getBoundingClientRect().bottom > nav.offsetHeight / 2);
  };
  update();
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
})();

// The front page's opening: a double helix made of points of light, turning
// slowly behind the words. It has the proportions of real B-DNA: one turn
// is 1.7 times as long as the helix is wide (3.4 nm to 2.0 nm), with 10.5
// base pairs to a turn, right-handed, and the two strands 127° apart round
// the axis rather than opposite, which leaves a wide groove and a narrow
// one. Each strand is a cloud of points with a sparser halo round it, the
// base pairs rows of fainter points; points near the plane of focus are
// small and sharp, those in front of and behind it large and soft. The
// points drift along their strand, a wave of light runs down it, and the
// whole leans a little towards the pointer. Drawn on a canvas, only while
// the opening is on screen; asked for less motion, it is one still frame.
(function () {
  var canvas = document.querySelector('.hero-helix');
  if (!canvas || !canvas.getContext) return;
  var band = canvas.closest('.hero-night') || canvas.parentNode, g = canvas.getContext('2d');
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var W = 0, H = 0, dpr = 1, s0 = 1, grain = 1;
  function fit() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);   // soft points: no need for every pixel
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    s0 = Math.min(Math.max(W / 800, 0.8), 1.45);         // the helix is larger on a wide screen
    grain = 1.12 * s0;                                   // and its points with it
  }
  fit();
  // One soft dot of each colour, drawn once and stamped for every point.
  function dot(r, gr, b) {
    var s = document.createElement('canvas'); s.width = s.height = 64;
    var x = s.getContext('2d'), fill = x.createRadialGradient(32, 32, 0, 32, 32, 32);
    fill.addColorStop(0, 'rgba(' + r + ',' + gr + ',' + b + ',1)');
    fill.addColorStop(0.28, 'rgba(' + r + ',' + gr + ',' + b + ',0.5)');
    fill.addColorStop(1, 'rgba(' + r + ',' + gr + ',' + b + ',0)');
    x.fillStyle = fill; x.fillRect(0, 0, 64, 64);
    return s;
  }
  var DOTS = [dot(62, 208, 178), dot(134, 206, 255), dot(170, 152, 255), dot(226, 246, 241)];   // teal, sky, violet, white
  function rnd() { return (Math.random() + Math.random() + Math.random() - 1.5) / 1.5; }
  var R = 88, PITCH = 3.4 * R, K = 2 * Math.PI / PITCH, RISE = PITCH / 10.5, GROOVE = 2 * Math.PI * 12 / 34;
  var L = 620, F = 820, FOCUS = -R * 0.35;
  var many = Math.min(Math.max(s0 * s0 * 1.25, 0.7), 2.8);   // more points where the helix is larger, to keep it as dense
  var P = [], D = [], i, j, t, u;
  for (j = 0; j < 2; j++) for (i = 0; i < 1150 * many; i++) {   // the strands
    u = Math.random();
    P.push({ t: (Math.random() * 2 - 1) * L, a: j * GROOVE + rnd() * 0.2, r: R + rnd() * 12, k: j, w: 0.45 + u * u * u * 2.4,
      al: 0.3 + Math.random() * 0.65, c: Math.random(), tw: Math.random() * 6.28, ts: 0.6 + Math.random() * 1.8, f: 6 + Math.random() * 8 });
  }
  for (j = 0; j < 2; j++) for (i = 0; i < 260 * many; i++) {    // their halo
    P.push({ t: (Math.random() * 2 - 1) * L, a: j * GROOVE + rnd() * 0.5, r: R + rnd() * 32, k: j, w: 0.4 + Math.random() * 0.8,
      al: 0.18 + Math.random() * 0.3, c: Math.random(), tw: Math.random() * 6.28, ts: 0.5 + Math.random(), f: 10 + Math.random() * 14 });
  }
  var across = many > 1.3 ? 22 : 16;
  for (t = -L; t < L; t += RISE) for (j = 0; j < across; j++) {   // the base pairs
    u = 0.035 + j / (across - 1) * 0.93;
    P.push({ t: t + rnd() * 1.6, u: u, k: u < 0.5 ? 0 : 1, w: 0.42 + Math.random() * 0.45, al: 0.42, c: Math.random(),
      tw: Math.random() * 6.28, ts: 1, f: 0, rung: true, o: rnd() * 2 });
  }
  for (i = 0; i < 110 * many; i++) {                           // dust adrift round it
    D.push({ x: (Math.random() * 2 - 1) * 660, y: (Math.random() * 2 - 1) * 330, z: (Math.random() * 2 - 1) * 380, v: 4 + Math.random() * 10, k: i % 3 });
  }
  var px = 0, py = 0, tx = 0, ty = 0, time = 2, last = 0, raf = 0, seen = true;
  band.addEventListener('pointermove', function (e) {
    var r = band.getBoundingClientRect();
    tx = (e.clientX - r.left) / r.width * 2 - 1; ty = (e.clientY - r.top) / r.height * 2 - 1;
  });
  band.addEventListener('pointerleave', function () { tx = 0; ty = 0; });
  var cx, cy;
  function put(k, X, Y, z, w, al) {
    var sc = F / (F + z), blur = Math.abs(z - FOCUS) / R, size = (1.25 * w + blur * 3.1) * sc * dpr * grain, a = 1.3 * al / (1 + blur * blur * 1.9);
    if (a < 0.012) return;
    g.globalAlpha = a > 1 ? 1 : a;
    g.drawImage(DOTS[k], (cx + X * sc) * dpr - size, (cy + Y * sc) * dpr - size, size * 2, size * 2);
  }
  function draw() {
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalCompositeOperation = 'source-over'; g.globalAlpha = 1;
    g.clearRect(0, 0, canvas.width, canvas.height);
    g.globalCompositeOperation = 'lighter';
    // leaning across a wide screen; steeper down a phone's tall one, where more of it then shows
    var tilt = (W < 700 ? -1.0 : -0.33) + px * 0.05, ct = Math.cos(tilt), st = Math.sin(tilt), phase = time * 0.3 + px * 0.5;
    var n, p, th, y, z, fade, pulse, k, tt, sc, gs, y0, z0, along, ci;
    cx = W / 2; cy = H * 0.45 + py * 10 * s0;
    for (n = 0; n < D.length; n++) { p = D[n]; put(p.k, p.x * s0, p.y * s0, p.z, 0.7, 0.5); }
    for (k = 0; k < 2; k++) for (tt = -L; tt < L; tt += 16) {   // a faint glow down the middle of each strand
      th = K * tt + phase + k * GROOVE; y = R * Math.cos(th); z = R * Math.sin(th);
      fade = 1 - Math.pow(Math.abs(tt) / L, 3); sc = F / (F + z);
      gs = (26 + 16 * (R - z) / (2 * R)) * sc * s0 * dpr;
      g.globalAlpha = 0.05 * fade * (0.5 + (R - z) / (2 * R));
      g.drawImage(DOTS[k], (cx + (tt * ct - y * st) * s0 * sc) * dpr - gs, (cy + (tt * st + y * ct) * s0 * sc) * dpr - gs, gs * 2, gs * 2);
    }
    for (n = 0; n < P.length; n++) {
      p = P[n];
      if (p.rung) {
        th = K * p.t + phase; y0 = R * Math.cos(th); z0 = R * Math.sin(th);
        y = y0 + (R * Math.cos(th + GROOVE) - y0) * p.u + p.o; z = z0 + (R * Math.sin(th + GROOVE) - z0) * p.u;
      } else {
        th = K * p.t + phase + p.a; y = p.r * Math.cos(th); z = p.r * Math.sin(th);
      }
      fade = 1 - Math.pow(Math.abs(p.t) / L, 3);
      pulse = Math.max(0, Math.sin(p.t * 0.01 - time * 0.9));
      along = (p.t / L + 1) / 2;                               // teal at one end, through sky, to violet at the other
      ci = p.k === 0 ? (p.c < along * 0.8 ? 1 : 0) : (p.c < along * 0.9 ? 2 : 1);
      if (!p.rung && p.c > 0.965) ci = 3;
      put(ci, (p.t * ct - y * st) * s0, (p.t * st + y * ct) * s0, z, p.w,
        p.al * fade * (0.72 + 0.28 * Math.sin(time * p.ts + p.tw)) * (0.6 + 0.75 * pulse));
    }
  }
  function frame(now) {
    raf = 0;
    var dt = Math.min(0.05, (now - last) / 1000 || 0), n, p;
    last = now; time += dt;
    px += (tx - px) * 0.05; py += (ty - py) * 0.05;
    for (n = 0; n < D.length; n++) { p = D[n]; p.x -= p.v * dt; if (p.x < -680) p.x = 680; }
    for (n = 0; n < P.length; n++) { p = P[n]; if (p.f) { p.t += p.f * dt; if (p.t > L) p.t -= 2 * L; } }
    draw();
    go();
  }
  function go() {
    if (raf || calm || !seen || document.hidden) return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }
  draw();
  if ('ResizeObserver' in window) new ResizeObserver(function () { fit(); draw(); }).observe(canvas);
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) { seen = entries[0].isIntersecting; go(); }).observe(band);
  }
  document.addEventListener('visibilitychange', go);
  go();
})();

// Clips. Most are the app itself, replayed (scripts/live-clips.py): a
// recording of its pages with rrweb, played back here as pages, so the words
// are as sharp as the text around them on any screen. bmClips.mount(video)
// puts one in place of a <video> whose poster is the clip's still, which is
// all a browser without scripts shows. One clip, the AI assistants card's, is
// a video: Apple's browsers get MP4, which they play reliably, the others the
// smaller WebM, and one a browser won't start by itself (Safari in Low Power
// Mode) starts at the visitor's first tap, click or key, wherever it is.
var bmClips = (function () {
  var ua = navigator.userAgent;
  var apple = /iP(hone|ad|od)/.test(ua) || (/Safari\//.test(ua) && !/Chrome|Chromium|CriOS|Edg|OPR|Android/.test(ua));
  var v = document.createElement('video');
  var ext = !apple && v.canPlayType && v.canPlayType('video/webm; codecs="vp9"') !== '' ? '.webm' : '.mp4';
  var waiting = [];
  function retry() { var w = waiting; waiting = []; w.forEach(function (f) { f(); }); }
  ['pointerdown', 'touchend', 'keydown'].forEach(function (t) { document.addEventListener(t, retry, { passive: true }); });

  // The site's root, from this script's own address.
  var me = document.querySelector('script[src*="site.js"]');
  var root = me ? me.src.replace(/site\.js(\?.*)?$/, '') : '';
  var lib = null, clips = {};
  // The site's language and light or dark: a Chinese page plays the Chinese
  // recording (<clip>.zh.json), and a clip follows the site's dark mode
  // (scripts/live-clips.py), its still too (<clip>-dark.webp).
  var zh = /^zh/i.test(document.documentElement.lang || '');
  var dim = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  function siteDark() { return !!(dim && dim.matches); }
  function stillFor(src, recorded) {
    if (!src || !/\.webp(\?.*)?$/.test(src)) return src;
    return src.replace(/\.webp(\?.*)?$/, (zh ? '.zh' : '') + (siteDark() && !recorded ? '-dark' : '') + '.webp$1');
  }
  function replayer() {
    if (!lib) {
      lib = new Promise(function (resolve, reject) {
        var s = document.createElement('script');
        s.src = root + 'assets/vendor/rrweb-replay.js?v=2.1.7';
        s.onload = function () { resolve(window.rrwebReplay.Replayer); };
        s.onerror = function () { lib = null; reject(); };
        document.head.appendChild(s);
      });
    }
    return lib;
  }
  function fetchClip(name) {
    if (!clips[name]) {
      var get = function (file) {
        return fetch(root + 'assets/clips/' + file).then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); });
      };
      clips[name] = (zh ? get(name + '.zh.json').catch(function () { return get(name + '.json'); }) : get(name + '.json'))
        .then(function (text) { return JSON.parse(text.split('%BM%/').join(root + 'assets/app/')); })
        .catch(function (e) { delete clips[name]; throw e; });
    }
    return clips[name];
  }
  function ease(x) { x = Math.min(Math.max(x, 0), 1); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  // The camera at t seconds: easing 0.7 s from wherever it was into each keyframe.
  function camera(zooms, t) {
    var state = zooms[0][1];
    for (var i = 1; i < zooms.length && t >= zooms[i][0]; i++) {
      var from = camera(zooms.slice(0, i), zooms[i][0]), to = zooms[i][1], k = ease((t - zooms[i][0]) / 0.7);
      state = [0, 1, 2].map(function (j) { return from[j] + (to[j] - from[j]) * k; });
    }
    return state;
  }
  var live = typeof fetch === 'function' && typeof Promise === 'function' && 'ResizeObserver' in window;

  function mount(video) {
    var box = document.createElement('div');
    box.className = 'live-clip';
    box.setAttribute('role', 'img');
    if (video.getAttribute('aria-describedby')) box.setAttribute('aria-describedby', video.getAttribute('aria-describedby'));
    if (video.id) box.id = video.id;
    var still = document.createElement('img');
    still.className = 'live-still';
    still.alt = '';
    still.decoding = 'async';
    var poster = video.getAttribute('poster');
    // This language's and theme's still, else the one the page names.
    function showStill() {
      var want = stillFor(poster);
      still.onerror = want !== poster ? function () { still.onerror = null; still.src = poster; } : null;
      still.src = want;
    }
    showStill();
    if (dim && dim.addEventListener) dim.addEventListener('change', function () { showStill(); });
    var screen = document.createElement('div');
    screen.className = 'live-screen';
    box.appendChild(still);
    box.appendChild(screen);
    video.parentNode.replaceChild(box, video);

    var p = { el: box, loop: false, onended: null, paused: true };
    var name = null, clip = null, seg = 0, r = null, stage = null, frame = null, raf = 0, last = '';
    var token = 0;   // a newer show() or unload() makes older loads stop

    function layout() {
      if (!stage || !clip) return;
      var s = clip.segments[seg], bw = box.clientWidth, bh = box.clientHeight;
      if (!bw || !bh) return;
      var z = camera(s.zooms, r ? r.getCurrentTime() / 1000 : 0);
      var w = s.width, h = s.height, k, ox = 0, oy = 0;
      if (s.kind === 'phone') {
        var ph = bh * 0.92, pw = ph * w / h;
        frame.style.width = pw + 'px';
        frame.style.height = ph + 'px';
        frame.style.left = (bw - pw) / 2 + 'px';
        frame.style.top = (bh - ph) / 2 + 'px';
        k = pw / w;
      } else {
        k = Math.max(bw / w, bh / h);
        ox = (bw - w * k) / 2;
      }
      var vw = w / z[2], vh = h / z[2];
      var left = Math.min(Math.max(z[0] - vw / 2, 0), w - vw), top = Math.min(Math.max(z[1] - vh / 2, 0), h - vh);
      var t = 'translate(' + ox.toFixed(2) + 'px,' + oy.toFixed(2) + 'px) scale(' + (k * z[2]).toFixed(5) + ') translate(' +
        (-left).toFixed(2) + 'px,' + (-top).toFixed(2) + 'px)';
      if (t !== last) { stage.style.transform = t; last = t; }
    }
    function tick() {
      raf = 0;
      layout();
      if (!p.paused) raf = requestAnimationFrame(tick);
    }
    function drop() {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      if (r) { try { r.pause(); r.destroy && r.destroy(); } catch (e) {} }
      r = null; stage = null; frame = null; last = '';
      screen.innerHTML = '';
      box.classList.remove('live', 'live-on-phone');
    }
    function build(Replayer, i) {
      drop();
      seg = i;
      var s = clip.segments[i];
      stage = document.createElement('div');
      stage.className = 'live-stage';
      stage.style.width = s.width + 'px';
      stage.style.height = s.height + 'px';
      if (s.kind === 'phone') {
        frame = document.createElement('div');
        frame.className = 'live-phone';
        frame.appendChild(stage);
        screen.appendChild(frame);
        box.classList.add('live-on-phone');
      } else {
        screen.appendChild(stage);
      }
      var mine = token;
      r = new Replayer(s.events, {
        root: stage, speed: clip.speed || 1, mouseTail: false, showWarning: false, showDebug: false,
        triggerFocus: false, skipInactive: false, pauseAnimation: true, UNSAFE_replayCanvas: false
      });
      r.on('fullsnapshot-rebuilded', function () {
        var doc = r && r.iframe.contentDocument;
        if (!doc) return;
        // Light or dark as the site is (the site's copies of the app's
        // stylesheets are dark where the page says data-theme=dark,
        // scripts/live-clips.py); a clip that shows the theme itself keeps
        // the one it was recorded in. The controls follow.
        var root = doc.documentElement, frame = r.iframe;
        var follow = clip.follow !== false;
        if (!root.getAttribute('data-theme')) root.setAttribute('data-theme', 'light');
        var scheme = function () {
          var want = siteDark() ? 'dark' : 'light';
          if (follow && root.getAttribute('data-theme') !== want) { root.setAttribute('data-theme', want); return; }
          var cs = root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
          frame.style.colorScheme = cs;
          root.style.colorScheme = cs;      // the page's own controls, too
        };
        scheme();
        new MutationObserver(scheme).observe(root, { attributes: true, attributeFilter: ['data-theme'] });
        if (follow && dim && dim.addEventListener) dim.addEventListener('change', scheme);
        var sheets = Array.prototype.slice.call(doc.querySelectorAll('link[rel~="stylesheet"]'));
        Promise.all(sheets.map(function (l) {
          return l.sheet ? null : new Promise(function (done) { l.addEventListener('load', done); l.addEventListener('error', done); setTimeout(done, 3000); });
        })).then(function () { if (mine === token) box.classList.add('live'); });
      });
      r.on('finish', function () {
        if (mine !== token) return;
        if (seg + 1 < clip.segments.length) { build(Replayer, seg + 1); return; }
        if (p.loop) { build(Replayer, 0); return; }
        p.paused = true;
        if (p.onended) p.onended();
      });
      layout();
      if (p.paused) { r.pause(0); } else { r.play(0); tick(); }
    }
    function start() {
      var mine = token;
      Promise.all([replayer(), fetchClip(name)]).then(function (got) {
        if (mine !== token || p.paused) return;
        clip = got[1];
        build(got[0], 0);
      }, function () {});
    }
    new ResizeObserver(function () { layout(); }).observe(box);

    p.show = function (clipName, newPoster) {
      token++;
      drop();
      p.paused = true;
      name = clipName; clip = null;
      if (newPoster) { poster = newPoster; showStill(); }
    };
    p.play = function () {
      if (!live || !name || !p.paused) return;
      p.paused = false;
      if (r) { r.resume(r.getCurrentTime()); tick(); } else { start(); }
    };
    p.pause = function () {
      p.paused = true;
      if (r) r.pause();
    };
    p.unload = function () { token++; p.paused = true; drop(); };
    var src = video.getAttribute('data-clip-src') || '';
    if (src) name = src.replace(/^.*\//, '');
    return p;
  }

  return {
    ext: ext,
    live: live,
    mount: mount,
    // play, or once the browser allows it, do `again` (which checks it is still wanted)
    play: function (video, again) {
      var p = video.play();
      if (p && p.catch) p.catch(function () { if (waiting.indexOf(again) < 0) waiting.push(again); });
    }
  };
})();

// The tour: a row of tabs above a window, each playing a short clip. Only
// the chosen clip loads. Clips follow one another until the visitor picks
// one, which then loops; they pause off screen or in a background tab.
// There is no play button: with reduced motion nothing plays by itself, and
// the still shows until a tab is picked.
(function () {
  var list = document.querySelector('.tour-tabs[role="tablist"]');
  var video = document.getElementById('stage-video');
  if (!list || !video) return;
  var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
  var screen = video.parentNode;
  var base = video.getAttribute('poster').replace(/[^/]*$/, '');
  var player = bmClips.mount(video);
  var title = document.getElementById('stage-title');
  var caption = document.getElementById('stage-caption');
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var chosen = false;     // the visitor picked a tab: stop moving on by itself
  var inView = false;
  var current = 0;
  var loaded = -1;
  var prefix = title ? title.textContent.replace(/—.*$/, '— ') : 'BioManager — ';
  function clip(i) { return tabs[i].dataset.clip; }
  function wanted() { return inView && !document.hidden && (!calm || chosen); }
  function load(i) {
    if (loaded === i) return;
    loaded = i;
    player.show(clip(i), base + clip(i) + '.webp');
  }
  function resume() {
    if (!wanted()) { player.pause(); return; }
    load(current);
    player.play();
  }
  function show(i, byHand) {
    current = i;
    tabs.forEach(function (t, j) {
      t.setAttribute('aria-selected', j === i ? 'true' : 'false');
      t.tabIndex = j === i ? 0 : -1;
    });
    screen.setAttribute('aria-labelledby', tabs[i].id);
    if (title) title.textContent = prefix + tabs[i].querySelector('b').textContent;
    caption.innerHTML = '';
    var b = document.createElement('b');
    b.textContent = tabs[i].querySelector('small').textContent;
    caption.appendChild(b);
    caption.appendChild(document.createTextNode(' ' + tabs[i].dataset.more));
    player.loop = chosen;
    screen.classList.add('swapping');
    setTimeout(function () {
      loaded = -1;
      load(i);
      if (wanted()) resume();
      screen.classList.remove('swapping');
    }, byHand === 'first' ? 0 : 120);
  }
  tabs.forEach(function (t, i) {
    t.addEventListener('click', function () { chosen = true; show(i); });
    t.addEventListener('keydown', function (e) {
      var j = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (j === undefined) return;
      e.preventDefault();
      j = (j + tabs.length) % tabs.length;
      tabs[j].focus();
      chosen = true;
      show(j);
      tabs[j].scrollIntoView({ block: 'nearest', inline: 'center' });
    });
  });
  player.onended = function () {
    if (!chosen) show((current + 1) % tabs.length);
  };
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      inView = entries[0].isIntersecting;
      resume();
    }, { threshold: 0.35 }).observe(screen);
  }
  document.addEventListener('visibilitychange', resume);
  show(0, 'first');
})();

// Download: the front page's button names this visitor's system and, where it
// can tell the file, downloads it; the Download page marks that file. Browsers
// can't tell an Apple-silicon Mac from an Intel one reliably, so Macs get the page.
(function () {
  var zh = document.documentElement.lang.indexOf('zh') === 0;
  var latest = 'https://github.com/gaspolymerase/biomanager/releases/latest/download/';
  var ua = navigator.userAgent, os = null, label = null;
  if (/Android/.test(ua)) { os = 'android'; label = zh ? '获取 Android 应用' : 'Get the Android app'; }
  else if (/Windows/.test(ua)) { os = 'win'; label = zh ? '下载 Windows 版' : 'Download for Windows'; }
  else if (/Macintosh|Mac OS X/.test(ua) && !/iPhone|iPad/.test(ua)) { label = zh ? '下载 Mac 版' : 'Download for Mac'; }
  else if (/Linux/.test(ua)) { os = 'linux'; label = zh ? '下载 Linux 版' : 'Download for Linux'; }
  var files = { android: 'BioManager-Android.apk', win: 'BioManager-Windows.zip', linux: 'BioManager-Linux.AppImage' };
  var hero = document.getElementById('hero-download');
  if (hero && label) hero.textContent = label;
  if (hero && os) hero.href = latest + files[os];
  if (os) document.querySelectorAll('[data-os="' + os + '"]').forEach(function (a) { a.classList.add('mine'); });
  document.querySelectorAll('u[data-href]').forEach(function (u) {
    u.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); location.href = u.dataset.href; });
  });
})();

// The version: written in the page (so it shows where GitHub can't be
// reached), then brought up to date from the latest release, with its date.
(function () {
  var spots = document.querySelectorAll('[data-latest]');
  if (!spots.length || !window.fetch) return;
  var zh = document.documentElement.lang.indexOf('zh') === 0;
  fetch('https://api.github.com/repos/gaspolymerase/biomanager/releases/latest', { headers: { Accept: 'application/vnd.github+json' } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (rel) {
      if (!rel || !rel.tag_name) return;
      var v = rel.tag_name.replace(/^v/, '');
      var when = rel.published_at ? new Date(rel.published_at).toLocaleDateString(zh ? 'zh-CN' : 'en-GB', { year: 'numeric', month: 'long', day: 'numeric' }) : '';
      spots.forEach(function (el) {
        var b = el.querySelector('[data-version]'), d = el.querySelector('[data-date]'), n = el.querySelector('[data-notes]');
        if (b) b.textContent = v;
        if (d && when) d.textContent = (zh ? '，' : ', ') + (zh ? when + '发布' : 'released ' + when);
        if (n && rel.html_url) n.href = rel.html_url;
      });
    })
    .catch(function () {});
})();

// The Features page: a picture of a database, or a Watch button, opens its
// clip in a window over the page, with a link on to its part of the user
// guide. Without scripts the picture is just that link, and Watch stays hidden.
(function () {
  var openers = document.querySelectorAll('[data-clip-open]');
  if (!openers.length || typeof HTMLDialogElement !== 'function') return;
  var zh = document.documentElement.lang.indexOf('zh') === 0;
  var base = (document.querySelector('link[rel=stylesheet]').getAttribute('href') || '').replace(/style\.css(\?.*)?$/, '') + 'assets/clips/';
  var dialog = document.createElement('dialog');
  dialog.className = 'clip-dialog';
  dialog.innerHTML = '<div class="window-bar"><i></i><i></i><i></i><span></span>' +
    '<button type="button" class="clip-close" aria-label="' + (zh ? '关闭' : 'Close') + '">×</button></div>' +
    '<video muted loop playsinline disablepictureinpicture></video>' +
    '<div class="clip-text"><p></p><a></a></div>';
  document.body.appendChild(dialog);
  dialog.querySelector('video').setAttribute('poster', base + openers[0].dataset.clipOpen + '.webp');
  var player = bmClips.mount(dialog.querySelector('video'));
  player.loop = true;
  function close() { dialog.close(); }
  dialog.querySelector('.clip-close').addEventListener('click', close);
  dialog.addEventListener('click', function (e) { if (e.target === dialog) close(); });
  dialog.addEventListener('close', function () { player.unload(); });
  openers.forEach(function (opener) {
    opener.hidden = false;
    var card = opener.closest('.feat, .nc');
    if (!card) return;
    opener.addEventListener('click', function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;   // a new tab: the guide, as the link says
      e.preventDefault();
      var title = card.querySelector('h3').textContent;
      dialog.querySelector('.window-bar span').textContent = 'BioManager — ' + title;
      var p = dialog.querySelector('.clip-text p');
      p.innerHTML = '';
      var b = document.createElement('b'); b.textContent = title + (zh ? '。' : '. ');
      p.appendChild(b);
      p.appendChild(document.createTextNode(card.querySelector('p').textContent));
      var a = dialog.querySelector('.clip-text a');
      a.href = card.querySelector('.feat-links a').getAttribute('href');
      a.textContent = zh ? '在用户指南中阅读' : 'Read about it in the user guide';
      player.show(opener.dataset.clipOpen, base + opener.dataset.clipOpen + '.webp');
      dialog.showModal();
      player.play();
    });
  });
})();

// The guide: search (Pagefind, in a window: the box at the top of the
// contents, ⌘K or /), each page's clip (loaded when it scrolls into view),
// "On this page" following the reading, and "Was this helpful?".
(function () {
  // On a phone the contents start folded, so the page comes first.
  var toc = document.querySelector('.docs-toc');
  if (toc && window.matchMedia('(max-width: 979px)').matches) toc.open = false;
  // On a wide screen the contents scroll on their own: keep them where they
  // were when a link in them opens the next page, and otherwise bring this
  // page's entry into view, so a page far down the list doesn't open at the top.
  var side = document.querySelector('.docs-nav');
  if (side && side.scrollHeight > side.clientHeight) {
    var kept = null;
    try { kept = sessionStorage.getItem('bm-guide-nav'); sessionStorage.removeItem('bm-guide-nav'); } catch (e) {}
    if (kept !== null) side.scrollTop = +kept;
    var here = side.querySelector('.docs-toc a[aria-current="page"]');
    if (here) {
      var box = side.getBoundingClientRect(), at = here.getBoundingClientRect();
      if (at.top < box.top || at.bottom > box.bottom) side.scrollTop += at.top - box.top - (box.height - at.height) / 2;
    }
  }
  if (side) side.addEventListener('click', function (e) {
    if (e.target.closest('.docs-toc a')) try { sessionStorage.setItem('bm-guide-nav', side.scrollTop); } catch (e2) {}
  });
  var zh = document.documentElement.lang.indexOf('zh') === 0;
  var opener = document.querySelector('[data-search]');
  if (opener) {
    var base = opener.dataset.pagefind, dialog = null, ready = false, ui = null;
    // ?q=… (the app's search palette, "Ask the guide") opens the search with it.
    var asked = '';
    try { asked = new URLSearchParams(location.search).get('q') || ''; } catch (e) { /* old browser */ }
    var open = function () {
      if (!dialog) {
        dialog = document.createElement('dialog');
        dialog.className = 'search-dialog';
        dialog.innerHTML = '<div id="guide-search"></div>';
        document.body.appendChild(dialog);
        dialog.addEventListener('click', function (e) { if (e.target === dialog) dialog.close(); });
        var css = document.createElement('link');
        css.rel = 'stylesheet'; css.href = base + 'pagefind-ui.css';
        document.head.appendChild(css);
        var js = document.createElement('script');
        js.src = base + 'pagefind-ui.js';
        js.onload = function () {
          // Results are links from the site's root, which is a folder down on GitHub Pages.
          ui = new window.PagefindUI({ element: '#guide-search', showSubResults: true, showImages: false, resetStyles: false,
            baseUrl: new URL(base + '../', location.href).pathname,
            translations: zh ? { placeholder: '搜索用户指南', zero_results: '没有找到“[SEARCH_TERM]”' } : { placeholder: 'Search the guide' } });
          ready = true;
          if (asked && ui.triggerSearch) { ui.triggerSearch(asked); asked = ''; }
          var input = dialog.querySelector('input');
          if (input) input.focus();
        };
        document.head.appendChild(js);
      }
      dialog.showModal();
      if (ready) { var i = dialog.querySelector('input'); if (i) { i.focus(); i.select(); } }
    };
    opener.addEventListener('click', open);
    if (asked) open();
    document.addEventListener('keydown', function (e) {
      var typing = /input|textarea|select/i.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
      if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !typing)) { e.preventDefault(); open(); }
    });
  }

  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var clips = document.querySelectorAll('video[data-clip-src]');
  if (clips.length && 'IntersectionObserver' in window) {
    // The app's own clips: replayed while in view. Asked for less motion, a
    // clip stays its still until clicked, and a click pauses it again.
    var players = new Map();
    var watch = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var p = players.get(e.target);
        p.seen = e.isIntersecting;
        if (!e.isIntersecting) { p.pause(); } else if (!calm || p.wanted) { p.play(); }
      });
    }, { threshold: 0.4 });
    // The AI assistants card's clip, a video: a picture that moves, never a
    // player; asked for less motion (or not allowed to play), it stays still.
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var v = e.target;
        if (e.isIntersecting) { v.dataset.seen = '1'; } else { delete v.dataset.seen; }
        if (e.isIntersecting) {
          if (calm) return;
          if (!v.src) v.src = v.dataset.clipSrc + bmClips.ext;
          bmClips.play(v, function () { if (v.dataset.seen) v.play(); });
        } else { v.pause(); }
      });
    }, { threshold: 0.4 });
    clips.forEach(function (v) {
      if ('clipStill' in v.dataset) {
        io.observe(v);
        // A clip inside a link (the AI assistants card) is not the link: a
        // click on it stays on the page, and starts it if the browser held it back.
        var frame = v.closest('figure') || v.parentNode;
        if (v.closest('a')) {
          frame.addEventListener('click', function (e) {
            e.preventDefault();
            if (v.src && v.paused && !calm) v.play();
          });
        }
        return;
      }
      if (!bmClips.live) return;
      var p = bmClips.mount(v);
      p.loop = true;
      players.set(p.el, p);
      watch.observe(p.el);
      if (calm) {
        p.el.classList.add('calm');
        p.el.addEventListener('click', function () {
          p.wanted = p.paused;
          if (p.wanted) { p.play(); } else { p.pause(); }
          p.el.classList.toggle('playing', !p.paused);
        });
      }
    });
  }

  var rail = {};
  document.querySelectorAll('.docs-rail a').forEach(function (a) { rail[a.getAttribute('href').slice(1)] = a; });
  if (Object.keys(rail).length && 'IntersectionObserver' in window) {
    var heads = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && rail[e.target.id]) {
          Object.keys(rail).forEach(function (k) { rail[k].classList.toggle('on', k === e.target.id); });
        }
      });
    }, { rootMargin: '-90px 0px -70% 0px' });
    Object.keys(rail).forEach(function (id) { var h = document.getElementById(id); if (h) heads.observe(h); });
  }

  document.querySelectorAll('[data-helpful="yes"]').forEach(function (b) {
    b.addEventListener('click', function () {
      b.setAttribute('aria-pressed', 'true');
      b.textContent = zh ? '谢谢！' : 'Thanks!';
    });
  });
})();
