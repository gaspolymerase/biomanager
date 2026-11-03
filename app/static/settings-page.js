/* Settings (settings.html): font, text size and the rows that save as they change. */
// Font: the page changes at once (the choice saves itself). Text size: in
// the Mac app, Smaller / Default / Larger set the window's zoom; elsewhere
// the browser's zoom does it, so the row stays hidden.
(function () {
  var font = document.querySelector('[data-ui-font]');
  if (font) font.addEventListener('change', function () {
    if (font.value === 'system' || font.value === 'inter') document.documentElement.setAttribute('data-font', font.value);
    else document.documentElement.removeAttribute('data-font');
  });
  var seg = document.querySelector('[data-text-size]');
  var row = document.querySelector('[data-text-size-row]');
  if (!seg) return;
  function mark(size) {
    seg.querySelectorAll('[data-size]').forEach(function (b) { b.classList.toggle('is-active', b.dataset.size === size); });
  }
  document.addEventListener('DOMContentLoaded', function () {
    var shell = window.BiomanagerShell;
    if (!shell || !shell.whenDesktop) return;
    shell.whenDesktop(function () {
      // The Mac app answers with the current size; others don't answer.
      window.bmTextSize = function (size) {
        row.hidden = false;
        mark(size);
      };
      shell.desktopCall('text_size');
      seg.addEventListener('click', function (event) {
        var b = event.target.closest('[data-size]');
        if (!b) return;
        mark(b.dataset.size);
        shell.desktopCall('set_text_size', b.dataset.size);
      });
    });
  });
})();

// Lab icon: saved as soon as a picture is chosen; the sidebar shows it at once.
(function () {
  var form = document.querySelector('[data-lab-icon-form]');
  var file = form && form.querySelector('[data-lab-icon-file]');
  if (!file) return;
  var preview = form.querySelector('[data-lab-icon-preview]');
  var remove = form.querySelector('[data-lab-icon-remove]');
  function send(data) {
    return fetch(form.action, { method: 'POST', body: data, headers: { 'X-Autosave': '1' } })
      .then(function (res) { return res.json(); })
      .then(function (body) {
        if (body.ok) { location.reload(); return; }
        if (window.BiomanagerShell) window.BiomanagerShell.toast(body.message, 'danger');
        if (preview) preview.src = preview.dataset.was;
      });
  }
  file.addEventListener('change', function () {
    if (!file.files.length) return;
    var data = new FormData();
    data.append('icon', file.files[0]);
    if (preview) { preview.dataset.was = preview.getAttribute('src'); preview.src = URL.createObjectURL(file.files[0]); }
    send(data);
  });
  if (remove) remove.addEventListener('click', function (event) {
    event.preventDefault();
    var data = new FormData();
    data.append('remove', '1');
    send(data);
  });
})();

// One pane at a time, picked by the address's #hash. A hash naming
// something inside a pane (#api-tokens, #lab-copies) opens that pane
// and scrolls to it, so links from other pages keep working.
(function () {
  var root = document.querySelector('[data-settings]');
  if (!root) return;
  var panes = Array.prototype.slice.call(root.querySelectorAll('[data-pane]'));
  var links = Array.prototype.slice.call(root.querySelectorAll('[data-pane-link]'));
  var aliases = { 'api-tokens': 'assistant', 'security': 'profile', 'language': 'appearance', 'app-icon': 'appearance', 'racks': 'stats',
                  'lab-copies': 'devices', 'lab-copy': 'devices', 'lab-server': 'devices', 'updates': 'version' };
  root.classList.add('is-ready');
  function show() {
    var id = decodeURIComponent(location.hash.slice(1));
    var target = id && document.getElementById(id);
    var pane = target && target.closest('[data-pane]');
    if (!pane && aliases[id]) pane = root.querySelector('[data-pane="' + aliases[id] + '"]');
    // On a phone, an empty hash shows the list; on a wider screen, Profile.
    root.toggleAttribute('data-open', !!pane);
    if (!pane) pane = panes[0];
    panes.forEach(function (p) { p.classList.toggle('is-on', p === pane); });
    links.forEach(function (l) {
      var on = l.dataset.paneLink === pane.dataset.pane;
      l.classList.toggle('is-on', on);
      if (on) l.setAttribute('aria-current', 'page'); else l.removeAttribute('aria-current');
    });
    var page = within(pane, target);
    if (target && target !== pane && !page) target.scrollIntoView({ block: 'start' });
    else document.getElementById('app-scroll') && (document.getElementById('app-scroll').scrollTop = 0);
  }
  // A pane with tabs (People & access) shows the tab the hash is in, and
  // in Project groups, one group's page or the list. Returns true when
  // the hash named a tab or a page, which then opens at its top.
  function within(pane, target) {
    var tabs = Array.prototype.slice.call(pane.querySelectorAll('[data-tabpane]'));
    if (!tabs.length) return false;
    var inner = target && target !== pane ? target : null;
    var tab = (inner && inner.closest('[data-tabpane]')) || tabs[0];
    tabs.forEach(function (t) { t.classList.toggle('is-on', t === tab); });
    pane.querySelectorAll('[data-tab-link]').forEach(function (a) {
      a.classList.toggle('is-on', a.dataset.tabLink === tab.id);
    });
    var sub = inner && inner.closest('[data-subview]');
    tab.querySelectorAll('[data-subview]').forEach(function (v) { v.hidden = v !== sub; });
    tab.querySelectorAll('[data-subview-list]').forEach(function (l) { l.hidden = !!sub; });
    return !inner || inner === tab || inner === sub;
  }
  window.addEventListener('hashchange', show);
  show();

  // Statistics: one animal's bars at a time, each counted in its own unit.
  var animalTabs = root.querySelector('[data-animal-tabs]');
  if (animalTabs) animalTabs.addEventListener('click', function (event) {
    var b = event.target.closest('[data-animal-tab]');
    if (!b) return;
    animalTabs.querySelectorAll('[data-animal-tab]').forEach(function (x) { x.classList.toggle('is-active', x === b); });
    root.querySelectorAll('[data-animal-pane]').forEach(function (p) { p.hidden = p.dataset.animalPane !== b.dataset.animalTab; });
  });

  // Search: keeps the panes whose name or settings mention the words.
  var search = root.querySelector('[data-settings-search]');
  var none = root.querySelector('[data-settings-none]');
  search.addEventListener('input', function () {
    var q = search.value.trim().toLowerCase(), shown = 0;
    links.forEach(function (l) {
      var pane = root.querySelector('[data-pane="' + l.dataset.paneLink + '"]');
      var hit = !q || l.textContent.toLowerCase().indexOf(q) >= 0 ||
                (pane && pane.textContent.toLowerCase().indexOf(q) >= 0);
      l.hidden = !hit;
      if (hit) shown++;
    });
    root.querySelectorAll('.set-group-head').forEach(function (h) {
      var next = h.nextElementSibling, any = false;
      while (next && !next.classList.contains('set-group-head')) {
        if (next.matches('[data-pane-link]') && !next.hidden) any = true;
        next = next.nextElementSibling;
      }
      h.hidden = !any;
    });
    none.hidden = shown > 0;
  });
})();

// Saved as you change it: no Save buttons. "Saved" shows by the pane's title.
(function () {
  function flag(form, text) {
    var pane = form.closest('[data-pane]');
    var badge = pane && pane.querySelector('[data-saved]');
    if (!badge) return;
    badge.textContent = text;
    badge.classList.add('is-shown');
    clearTimeout(badge._hide);
    badge._hide = setTimeout(function () { badge.classList.remove('is-shown'); }, 1800);
  }
  document.querySelectorAll('[data-autosave-form]').forEach(function (form) {
    var latest = 0;
    function save() {
      if (!form.reportValidity()) return;
      var mine = ++latest;
      flag(form, t('Saving…'));
      fetch(form.getAttribute('action') || window.location.pathname, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: { 'X-Autosave': '1' }
      }).then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok || !body.ok) throw new Error(body.error || res.statusText);
        });
      }).then(function () {
        if (mine === latest) flag(form, t('Saved'));
      }).catch(function (err) {
        if (mine === latest) flag(form, err && err.message ? err.message : t('Not saved. Check the connection and try again.'));
      });
    }
    form.addEventListener('change', save);
    form.addEventListener('submit', function (event) { event.preventDefault(); save(); });
  });
})();

// Language: saved the moment it is picked, and the page comes back in it.
(function () {
  var form = document.querySelector('[data-language-form]');
  if (form) form.addEventListener('change', function () { form.submit(); });
})();

// App icon: saved the moment a picture or colour is picked. The icons and
// the accent change in place, so the page never reloads under you.
(function () {
  var form = document.querySelector('[data-icon-picker]');
  if (!form) return;
  var urls = JSON.parse(form.dataset.urls);
  var pane = form.closest('[data-pane]');
  var status = pane && pane.querySelector('[data-saved]');
  function say(text) {
    if (!status) return;
    status.textContent = text;
    status.classList.add('is-shown');
    clearTimeout(status._hide);
    status._hide = setTimeout(function () { status.classList.remove('is-shown'); }, 1800);
  }
  var latest = 0;
  form.addEventListener('change', function () {
    var data = new FormData(form);
    var glyph = data.get('glyph'), color = data.get('color');
    form.querySelectorAll('img[data-glyph]').forEach(function (img) {
      img.src = urls[img.dataset.glyph + '/' + color];
    });
    document.querySelectorAll('img[data-app-icon]').forEach(function (img) {
      img.src = urls[glyph + '/' + color];
    });
    var mine = ++latest;
    say(t('Saving…'));
    fetch(form.getAttribute('action') || window.location.pathname, {
      method: 'POST', body: data, credentials: 'same-origin', headers: {'X-Autosave': '1'}
    }).then(function (res) {
      return res.json().then(function (body) {
        if (!res.ok || !body.ok) throw new Error(body.error || res.statusText);
        return body;
      });
    }).then(function (body) {
      if (mine !== latest) return;
      document.getElementById('brand-css').textContent = body.brand_css;
      var link = document.getElementById('app-icon-link');
      if (link) link.href = body.icon;
      say(t('Saved'));
    }).catch(function () {
      if (mine === latest) say(t('Not saved. Check the connection and pick again.'));
    });
  });
})();

// Update now: follow host/update.sh (app/server_updates.py) until the
// server answers with the new version, then reload. While it restarts
// the page cannot reach it, and says so.
(function () {
  var box = document.querySelector('[data-updates]');
  var wordsEl = document.getElementById('update-words');
  if (!box || !wordsEl) return;
  var words = JSON.parse(wordsEl.textContent);
  var row = box.querySelector('[data-update-progress]');
  var line = box.querySelector('[data-update-line]');
  var detail = box.querySelector('[data-update-detail]');
  var button = box.querySelector('[data-update-button]');
  var started = box.dataset.current;
  function fill(text, p) {
    return text.replace('%(version)s', (p && p.version) || '').replace('%(detail)s', (p && p.detail) || '');
  }
  function show(p, away) {
    row.hidden = false;
    detail.textContent = '';
    if (away) { line.textContent = words.away; return; }
    var state = p ? p.state : '';
    if (state === 'running') line.textContent = fill(words[p.step] || words.restart, p);
    else if (state === 'failed') { line.textContent = fill(words.failed, p); detail.textContent = words.failed_help; }
    else line.textContent = fill(words[state] || '', p);
    if (button) button.disabled = state === 'asked' || state === 'running';
  }
  function poll() {
    fetch(box.dataset.statusUrl, {credentials: 'same-origin', cache: 'no-store'}).then(function (res) {
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    }).then(function (body) {
      var p = body.progress;
      if (body.current !== started) { window.location.reload(); return; }
      show(p, false);
      if (p && (p.state === 'asked' || p.state === 'running')) setTimeout(poll, 3000);
    }).catch(function () {
      show(null, true);
      setTimeout(poll, 4000);
    });
  }
  var state = box.dataset.state;
  if (state) poll();
})();
