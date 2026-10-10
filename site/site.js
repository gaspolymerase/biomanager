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

// The header: on the page at the very top, a pane of glass once it scrolls.
(function () {
  var nav = document.querySelector('.nav');
  if (!nav) return;
  var ticking = false;
  // Over a dark band (the feature stage, the glass cards, dark only on a dark
  // page) the glass turns dark too.
  var bands = document.querySelectorAll('.stage, .neon-band'), dim = window.matchMedia('(prefers-color-scheme: dark)');
  var update = function () {
    ticking = false;
    nav.classList.toggle('scrolled', window.scrollY > 8);
    var y = nav.getBoundingClientRect().top + nav.offsetHeight / 2, dark = false;
    if (dim.matches) bands.forEach(function (d) { var r = d.getBoundingClientRect(); if (r.top <= y && r.bottom >= y) dark = true; });
    nav.classList.toggle('on-dark', dark);
  };
  update();
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });

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
      clips[name] = fetch(root + 'assets/clips/' + name + '.json')
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
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
    still.src = video.getAttribute('poster');
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
        // The app as recorded (light unless the recording says dark),
        // whatever this visitor's system is.
        // The site's copies of the app's stylesheets are dark where the page
        // says data-theme=dark (scripts/live-clips.py); the controls follow.
        var root = doc.documentElement, frame = r.iframe;
        if (!root.getAttribute('data-theme')) root.setAttribute('data-theme', 'light');
        var scheme = function () {
          var cs = root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
          frame.style.colorScheme = cs;
          root.style.colorScheme = cs;      // the page's own controls, too
        };
        scheme();
        new MutationObserver(scheme).observe(root, { attributes: true, attributeFilter: ['data-theme'] });
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

    p.show = function (clipName, poster) {
      token++;
      drop();
      p.paused = true;
      name = clipName; clip = null;
      if (poster) still.src = poster;
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

// The feature stage: a dock of tabs under a window, each playing a short
// clip. Only the chosen clip loads. Clips follow one another until the
// visitor picks one, which then loops; they pause off screen or in a
// background tab. There is no play button: with reduced motion nothing
// plays by itself, and the still shows until a tab is picked.
(function () {
  var dock = document.querySelector('.dock[role="tablist"]');
  var video = document.getElementById('stage-video');
  if (!dock || !video) return;
  var tabs = Array.prototype.slice.call(dock.querySelectorAll('[role="tab"]'));
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
    var name = tabs[i].querySelector('.dock-tip').textContent;
    if (title) title.textContent = prefix + name;
    caption.innerHTML = '';
    var b = document.createElement('b');
    b.textContent = tabs[i].dataset.claim;
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
      var j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
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

// Scrolling: sections rise in as they appear, where the browser can't drive
// that from scrolling itself; each glass card's neon draws itself in.
(function () {
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!('IntersectionObserver' in window)) {
    document.querySelectorAll('.rise').forEach(function (el) { el.classList.add('in'); });
    return;
  }
  var scrolls = window.CSS && CSS.supports && CSS.supports('animation-timeline: view()');
  if (!scrolls) {
    var rise = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); rise.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -8% 0px' });
    document.querySelectorAll('.rise').forEach(function (el) { rise.observe(el); });
  }
  if (calm) return;
  var cards = document.querySelectorAll('.nc');
  cards.forEach(function (card) {
    card.querySelectorAll('.neon g > *').forEach(function (shape) { shape.setAttribute('pathLength', '1'); });
    card.classList.add('neon-draw');
  });
  var draw = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('drawn'); draw.unobserve(e.target); } });
  }, { threshold: 0.35 });
  cards.forEach(function (card) { draw.observe(card); });
})();

// A card with a clip (data-clip) opens it in a window over the page, with a
// link on to its part of the user guide; without scripts it is just that link.
(function () {
  var cards = document.querySelectorAll('.nc[data-clip]');
  if (!cards.length || typeof HTMLDialogElement !== 'function') return;
  var zh = document.documentElement.lang.indexOf('zh') === 0;
  var base = (document.querySelector('link[rel=stylesheet]').getAttribute('href') || '').replace(/style\.css(\?.*)?$/, '') + 'assets/clips/';
  var dialog = document.createElement('dialog');
  dialog.className = 'clip-dialog';
  dialog.innerHTML = '<div class="window-bar"><i></i><i></i><i></i><span></span>' +
    '<button type="button" class="clip-close" aria-label="' + (zh ? '关闭' : 'Close') + '">×</button></div>' +
    '<video muted loop playsinline disablepictureinpicture></video>' +
    '<div class="clip-text"><p></p><a></a></div>';
  document.body.appendChild(dialog);
  dialog.querySelector('video').setAttribute('poster', base + cards[0].dataset.clip + '.webp');
  var player = bmClips.mount(dialog.querySelector('video'));
  player.loop = true;
  function close() { dialog.close(); }
  dialog.querySelector('.clip-close').addEventListener('click', close);
  dialog.addEventListener('click', function (e) { if (e.target === dialog) close(); });
  dialog.addEventListener('close', function () { player.unload(); });
  cards.forEach(function (card) {
    card.addEventListener('click', function (e) {
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
      a.href = card.getAttribute('href');
      a.textContent = zh ? '在用户指南中阅读 →' : 'Read about it in the user guide →';
      player.show(card.dataset.clip, base + card.dataset.clip + '.webp');
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
    var base = opener.dataset.pagefind, dialog = null, ready = false;
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
          new window.PagefindUI({ element: '#guide-search', showSubResults: true, showImages: false, resetStyles: false,
            baseUrl: new URL(base + '../', location.href).pathname,
            translations: zh ? { placeholder: '搜索用户指南', zero_results: '没有找到“[SEARCH_TERM]”' } : { placeholder: 'Search the guide' } });
          ready = true;
          var input = dialog.querySelector('input');
          if (input) input.focus();
        };
        document.head.appendChild(js);
      }
      dialog.showModal();
      if (ready) { var i = dialog.querySelector('input'); if (i) { i.focus(); i.select(); } }
    };
    opener.addEventListener('click', open);
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
