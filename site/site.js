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

// Clips. Each browser gets the format it plays best: Apple's browsers MP4,
// which they play reliably, the others the smaller WebM. A clip plays with
// no button: one a browser won't start by itself (Safari in Low Power Mode)
// starts at the visitor's first tap, click or key, wherever it is.
var bmClips = (function () {
  var ua = navigator.userAgent;
  var apple = /iP(hone|ad|od)/.test(ua) || (/Safari\//.test(ua) && !/Chrome|Chromium|CriOS|Edg|OPR|Android/.test(ua));
  var v = document.createElement('video');
  var ext = !apple && v.canPlayType && v.canPlayType('video/webm; codecs="vp9"') !== '' ? '.webm' : '.mp4';
  var waiting = [];
  function retry() { var w = waiting; waiting = []; w.forEach(function (f) { f(); }); }
  ['pointerdown', 'touchend', 'keydown'].forEach(function (t) { document.addEventListener(t, retry, { passive: true }); });
  return {
    ext: ext,
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
  var title = document.getElementById('stage-title');
  var caption = document.getElementById('stage-caption');
  var base = video.getAttribute('poster').replace(/[^/]*$/, '');
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
    video.src = base + clip(i) + bmClips.ext;
    video.load();
  }
  function resume() {
    if (!wanted()) { video.pause(); return; }
    load(current);
    bmClips.play(video, resume);
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
    video.loop = chosen;
    screen.classList.add('swapping');
    setTimeout(function () {
      video.pause();
      video.setAttribute('poster', base + clip(i) + '.webp');
      loaded = -1;
      if (wanted()) { resume(); } else { video.removeAttribute('src'); video.load(); }
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
  video.addEventListener('ended', function () {
    if (!chosen) show((current + 1) % tabs.length);
  });
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
  var video = dialog.querySelector('video');
  function close() { video.pause(); dialog.close(); }
  dialog.querySelector('.clip-close').addEventListener('click', close);
  dialog.addEventListener('click', function (e) { if (e.target === dialog) close(); });
  dialog.addEventListener('close', function () { video.pause(); video.removeAttribute('src'); video.load(); });
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
      video.poster = base + card.dataset.clip + '.webp';
      video.src = base + card.dataset.clip + bmClips.ext;
      dialog.showModal();
      bmClips.play(video, function () { if (dialog.open) video.play(); });
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
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var v = e.target;
        if (e.isIntersecting) { v.dataset.seen = '1'; } else { delete v.dataset.seen; }
        if (e.isIntersecting) {
          // data-clip-still: a picture that moves, never a player; asked for
          // less motion (or not allowed to play), it stays its still frame.
          var still = 'clipStill' in v.dataset;
          if (calm && still) return;
          if (!v.src) v.src = v.dataset.clipSrc + bmClips.ext;
          if (calm) { v.controls = true; }
          else if (still) { bmClips.play(v, function () { if (v.dataset.seen) v.play(); }); }
          else { var p = v.play(); if (p && p.catch) p.catch(function () { v.controls = true; }); }
        } else { v.pause(); }
      });
    }, { threshold: 0.4 });
    clips.forEach(function (v) { io.observe(v); });
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
