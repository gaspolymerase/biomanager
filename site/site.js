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
  // Over a dark band (the feature stage, the glass cards) the glass turns dark too.
  var darks = document.querySelectorAll('.stage, .neon-band');
  var update = function () {
    ticking = false;
    nav.classList.toggle('scrolled', window.scrollY > 8);
    var y = nav.getBoundingClientRect().top + nav.offsetHeight / 2, dark = false;
    darks.forEach(function (d) { var r = d.getBoundingClientRect(); if (r.top <= y && r.bottom >= y) dark = true; });
    nav.classList.toggle('on-dark', dark);
  };
  update();
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });

})();

// The feature stage: a dock of tabs under a window, each playing a short
// clip. Only the chosen clip loads. Clips follow one another until the
// visitor picks one, which then loops; they pause off screen or in a
// background tab. With reduced motion nothing plays by itself: the still
// shows, with a play button.
(function () {
  var dock = document.querySelector('.dock[role="tablist"]');
  var video = document.getElementById('stage-video');
  if (!dock || !video) return;
  var tabs = Array.prototype.slice.call(dock.querySelectorAll('[role="tab"]'));
  var screen = video.parentNode;
  var title = document.getElementById('stage-title');
  var caption = document.getElementById('stage-caption');
  var play = document.getElementById('stage-play');
  var base = video.getAttribute('poster').replace(/[^/]*$/, '');
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var webm = !!video.canPlayType && video.canPlayType('video/webm; codecs="vp9"') !== '';
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
    video.src = base + clip(i) + (webm ? '.webm' : '.mp4');
    video.load();
  }
  function resume() {
    if (!wanted()) { video.pause(); return; }
    load(current);
    var p = video.play();
    if (p && p.catch) p.catch(function () { if (play) play.hidden = false; });
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
    if (play) play.hidden = !(calm && !chosen);
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
  if (play) play.addEventListener('click', function () { chosen = true; video.loop = true; play.hidden = true; resume(); });
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
