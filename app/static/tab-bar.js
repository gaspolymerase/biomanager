/* Workspace tabs — the persistent strip at the top of the main column.
 *
 * Tabs behave like a browser's: following a link navigates the tab you are
 * already in, and only the "+" button opens a new one (on the person's start
 * page, chosen in Settings). Anything else would
 * spawn a tab per click and turn the strip into a history list.
 *
 * State lives in localStorage as an array of { url, title } plus the index
 * of the active tab, because each navigation is a full page load — without
 * remembering which tab was active, the next page would not know which one
 * to navigate in place.
 *
 * Tabs can be closed, reordered by dragging, and driven from the keyboard
 * via window.BiomanagerTabs (see shell.js for the bindings).
 *
 * The icon for a tab is derived from its URL using the rules Flask embeds in
 * #tab-icon-rules, so a tab restored from storage shows the right glyph
 * without having to store it.
 */
(function () {
  'use strict';

  // Each person's own tabs: a shared lab computer, or a second account in
  // the same browser, would otherwise open the last person's pages.
  const bar = document.getElementById('app-tabbar');
  const who = bar && bar.dataset.user ? `:u${bar.dataset.user}` : '';
  const STORAGE_KEY = `biomanager:tabs${who}`;
  const ACTIVE_KEY = `biomanager:tabs${who}:active`;
  // Set by the "+" button; the next navigation consumes it and opens a new
  // tab instead of navigating in place.
  const NEW_TAB_KEY = `biomanager:tabs${who}:new`;
  if (who) {
    // Tabs kept before they were per person belong to no one in particular.
    try { ['', ':active', ':new'].forEach((k) => localStorage.removeItem(`biomanager:tabs${k}`)); } catch (e) {}
  }
  // If "+" was pressed and then abandoned, don't let the request haunt a
  // navigation made minutes later.
  const NEW_TAB_TTL_MS = 2 * 60 * 1000;
  const MAX_TABS = 14;
  const SKIP_PREFIXES = ['/login', '/register', '/logout', '/static/'];

  let iconRules = [];

  function loadIconRules() {
    const node = document.getElementById('tab-icon-rules');
    if (!node) return;
    try {
      const parsed = JSON.parse(node.textContent);
      // Longest prefix wins, so sort descending by length once up front.
      iconRules = parsed.slice().sort((a, b) => b[0].length - a[0].length);
    } catch (e) {
      iconRules = [];
    }
  }

  function svgIcon(name, className) {
    return `<svg class="icon ${className}" aria-hidden="true">`
         + `<use href="/static/icons.svg#${name}"></use></svg>`;
  }

  function iconFor(url) {
    const path = String(url).split('?')[0];
    for (const [prefix, icon] of iconRules) {
      if (prefix === '/' ? path === '/' : path === prefix || path.startsWith(prefix + '/')) {
        return icon;
      }
    }
    return 'file';
  }

  /* ----------------------------------------------------------------- state */

  function load() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      return Array.isArray(parsed) ? parsed.filter((t) => t && t.url) : [];
    } catch (e) {
      return [];
    }
  }

  function save(tabs) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs)); } catch (e) {}
  }

  function currentUrl() {
    const bar = document.getElementById('app-tabbar');
    return bar ? bar.dataset.currentUrl || '' : '';
  }

  function loadActive() {
    try {
      const value = parseInt(localStorage.getItem(ACTIVE_KEY), 10);
      return Number.isInteger(value) && value >= 0 ? value : 0;
    } catch (e) {
      return 0;
    }
  }

  function saveActive(index) {
    try { localStorage.setItem(ACTIVE_KEY, String(index)); } catch (e) {}
  }

  /* The "+" button parks a request here, because opening a new tab happens
     on the *next* page load — the palette has to be used to choose a
     destination first. */
  function requestNewTab() {
    try { localStorage.setItem(NEW_TAB_KEY, String(Date.now())); } catch (e) {}
  }

  function cancelNewTab() {
    try { localStorage.removeItem(NEW_TAB_KEY); } catch (e) {}
  }

  function consumeNewTabRequest() {
    let stamp = null;
    try { stamp = localStorage.getItem(NEW_TAB_KEY); } catch (e) { return false; }
    cancelNewTab();
    if (!stamp) return false;
    return Date.now() - parseInt(stamp, 10) < NEW_TAB_TTL_MS;
  }

  /* Work out what this page load means for the tab strip.

     Four cases, in order: the page is already open somewhere (just make
     that tab active), "+" asked for a new tab, there are no tabs at all, or
     — the common one — navigate the active tab in place. */
  function reconcile(tabs, active, url, title) {
    const label = title || url;
    const existing = tabs.findIndex((t) => t.url === url);
    if (existing >= 0) {
      tabs[existing].title = label;
      return { tabs: tabs, active: existing };
    }

    if (consumeNewTabRequest()) {
      const at = Math.min(Math.max(active + 1, 0), tabs.length);
      tabs.splice(at, 0, { url: url, title: label });
      if (tabs.length > MAX_TABS) {
        // Drop the oldest tab that is not the one just opened.
        const victim = at === 0 ? tabs.length - 1 : 0;
        tabs.splice(victim, 1);
        return { tabs: tabs, active: tabs.findIndex((t) => t.url === url) };
      }
      return { tabs: tabs, active: at };
    }

    if (!tabs.length) {
      return { tabs: [{ url: url, title: label }], active: 0 };
    }

    const index = Math.min(Math.max(active, 0), tabs.length - 1);
    tabs[index] = { url: url, title: label };
    return { tabs: tabs, active: index };
  }

  /* ---------------------------------------------------------------- render */

  function render(bar, tabs, active) {
    bar.textContent = '';

    tabs.forEach((tab, index) => {
      const isActive = tab.url === active;

      const el = document.createElement('div');
      el.className = 'wtab' + (isActive ? ' is-active' : '');
      el.dataset.url = tab.url;
      el.dataset.index = String(index);
      el.draggable = true;

      el.insertAdjacentHTML('beforeend', svgIcon(iconFor(tab.url), 'wtab-icon'));

      const link = document.createElement('a');
      link.className = 'wtab-link';
      link.href = tab.url;
      link.title = tab.title || tab.url;
      link.textContent = tab.title || tab.url;
      // Links to pages in a <nav>, not tabs of one page: the open one is the current page.
      if (isActive) link.setAttribute('aria-current', 'page');
      el.appendChild(link);

      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'wtab-close';
      close.title = t('Close tab (Alt+W)');
      close.setAttribute('aria-label', t('Close %(name)s', { name: tab.title || tab.url }));
      close.insertAdjacentHTML('beforeend', svgIcon('close', 'wtab-close-icon'));
      close.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        closeTab(tab.url);
      });
      el.appendChild(close);

      // Middle-click closes, matching every browser and editor.
      el.addEventListener('auxclick', (event) => {
        if (event.button === 1) {
          event.preventDefault();
          closeTab(tab.url);
        }
      });

      bar.appendChild(el);
    });

    updateOverflow(bar);
    const activeEl = bar.querySelector('.wtab.is-active');
    if (activeEl) activeEl.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  /* Fade the strip's edges only while there is something scrolled out of
     view, so a short tab list stays perfectly crisp. */
  function updateOverflow(bar) {
    const overflowing = bar.scrollWidth > bar.clientWidth + 1;
    if (!overflowing) {
      delete bar.dataset.overflow;
      return;
    }
    const atStart = bar.scrollLeft <= 1;
    const atEnd = bar.scrollLeft + bar.clientWidth >= bar.scrollWidth - 1;
    if (atStart && atEnd) delete bar.dataset.overflow;
    else if (atStart) bar.dataset.overflow = 'end';
    else if (atEnd) bar.dataset.overflow = 'start';
    else bar.dataset.overflow = 'both';
  }

  /* ---------------------------------------------------------------- actions */

  function closeTab(url) {
    const closingActive = url === currentUrl();
    const tabs = load();
    const index = tabs.findIndex((t) => t.url === url);
    if (index < 0) return;
    const remaining = tabs.filter((t) => t.url !== url);
    save(remaining);

    if (!closingActive) {
      // Keep the active tab active: its index shifts if it sat to the right.
      const active = loadActive();
      saveActive(index < active ? Math.max(0, active - 1) : active);
      const bar = document.getElementById('app-tabbar');
      if (bar) render(bar, remaining, currentUrl());
      return;
    }
    if (!remaining.length) {
      saveActive(0);
      window.location.href = '/';
      return;
    }
    // Fall back to the neighbour on the left, like a browser does.
    const target = Math.max(0, Math.min(index - 1, remaining.length - 1));
    saveActive(target);
    window.location.href = remaining[target].url;
  }

  function activate(index) {
    const tabs = load();
    if (index < 0 || index >= tabs.length) return;
    if (tabs[index].url === currentUrl()) return;
    // Record the destination before leaving: switching tabs is a
    // navigation, and the next page load needs to know it was deliberate.
    saveActive(index);
    window.location.href = tabs[index].url;
  }

  function step(delta) {
    const tabs = load();
    if (tabs.length < 2) return;
    const here = tabs.findIndex((t) => t.url === currentUrl());
    const next = ((here < 0 ? 0 : here) + delta + tabs.length) % tabs.length;
    activate(next);
  }

  /* ------------------------------------------------------------ reordering */

  function initDrag(bar) {
    let draggingUrl = null;

    bar.addEventListener('dragstart', (event) => {
      const tab = event.target.closest('.wtab');
      if (!tab) return;
      draggingUrl = tab.dataset.url;
      tab.classList.add('is-dragging');
      event.dataTransfer.effectAllowed = 'move';
      // Firefox refuses to start a drag without payload.
      event.dataTransfer.setData('text/plain', draggingUrl);
    });

    bar.addEventListener('dragend', () => {
      draggingUrl = null;
      bar.querySelectorAll('.wtab').forEach((el) => {
        el.classList.remove('is-dragging', 'is-drop-before', 'is-drop-after');
      });
    });

    bar.addEventListener('dragover', (event) => {
      if (!draggingUrl) return;
      event.preventDefault();
      const tab = event.target.closest('.wtab');
      bar.querySelectorAll('.wtab').forEach((el) => {
        el.classList.remove('is-drop-before', 'is-drop-after');
      });
      if (!tab || tab.dataset.url === draggingUrl) return;
      const box = tab.getBoundingClientRect();
      const after = event.clientX > box.left + box.width / 2;
      tab.classList.add(after ? 'is-drop-after' : 'is-drop-before');
    });

    bar.addEventListener('drop', (event) => {
      if (!draggingUrl) return;
      event.preventDefault();
      const tab = event.target.closest('.wtab');
      if (!tab || tab.dataset.url === draggingUrl) return;

      const tabs = load();
      const from = tabs.findIndex((t) => t.url === draggingUrl);
      let to = tabs.findIndex((t) => t.url === tab.dataset.url);
      if (from < 0 || to < 0) return;

      const box = tab.getBoundingClientRect();
      if (event.clientX > box.left + box.width / 2) to += 1;
      if (to > from) to -= 1;

      const [moved] = tabs.splice(from, 1);
      tabs.splice(to, 0, moved);
      save(tabs);
      // The active tab kept its identity, not its position.
      const nowActive = tabs.findIndex((t) => t.url === currentUrl());
      if (nowActive >= 0) saveActive(nowActive);
      render(bar, tabs, currentUrl());
    });
  }

  /* ------------------------------------------------------------------ init */

  function init() {
    const bar = document.getElementById('app-tabbar');
    if (!bar) return;

    loadIconRules();

    const url = bar.dataset.currentUrl || '';
    const title = bar.dataset.currentTitle || '';
    let tabs = load();

    if (url && !SKIP_PREFIXES.some((p) => url.startsWith(p))) {
      const result = reconcile(tabs, loadActive(), url, title);
      tabs = result.tabs;
      save(tabs);
      saveActive(result.active);
    }

    render(bar, tabs, url);
    initDrag(bar);

    bar.addEventListener('scroll', () => updateOverflow(bar), { passive: true });
    window.addEventListener('resize', () => updateOverflow(bar));

    // Horizontal wheel/trackpad gestures should scroll the strip, and a
    // plain vertical wheel over it is far more likely to mean "show me the
    // other tabs" than "scroll the page".
    bar.addEventListener('wheel', (event) => {
      if (bar.scrollWidth <= bar.clientWidth) return;
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      if (!delta) return;
      event.preventDefault();
      bar.scrollLeft += delta;
    }, { passive: false });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.BiomanagerTabs = {
    /* Navigate the current tab, the way a link does. */
    go(url) {
      window.location.href = url;
    },
    /* Open a destination in a new tab. */
    open(url, title) {
      requestNewTab();
      window.location.href = url;
    },
    /* Ask for the *next* navigation to land in a new tab — this is what
       the "+" button uses, since the destination is chosen afterwards. */
    requestNewTab: requestNewTab,
    cancelNewTab: cancelNewTab,
    close: closeTab,
    closeCurrent() { closeTab(currentUrl()); },
    activate: activate,
    step: step,
    list: load,
    /* The current page's name changed (a notebook page renamed): its tab
       says so at once, not on the next visit. */
    retitle(title) {
      const bar = document.getElementById('app-tabbar');
      const url = currentUrl();
      const tabs = load();
      const index = tabs.findIndex((t) => t.url === url);
      if (index < 0 || !title) return;
      tabs[index].title = title;
      save(tabs);
      if (bar) render(bar, tabs, url);
    },
    clear() {
      save([]);
      saveActive(0);
      cancelNewTab();
      const bar = document.getElementById('app-tabbar');
      if (bar) render(bar, [], currentUrl());
    },
  };
})();
