/* App shell behavior: the rail, the mobile nav drawer, the account menu,
 * lightweight toasts, and the keyboard shortcuts that make the workspace
 * tabs usable without the mouse.
 *
 * Tab rendering itself lives in tab-bar.js; this file only drives it through
 * the window.BiomanagerTabs API.
 */
(function () {
  'use strict';

  const RAIL_KEY = 'biomanager:rail';
  const isMac = /Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent);

  /* ---------------------------------------------------------------- toasts */

  let toastHost = null;

  function toast(message, kind) {
    if (!toastHost) {
      toastHost = document.createElement('div');
      toastHost.className = 'toast-host';
      // In the top layer, so a message sent from an open dialog shows in
      // front of it rather than behind its backdrop.
      if ('popover' in toastHost) toastHost.popover = 'manual';
      document.body.appendChild(toastHost);
    }
    if (toastHost.popover) {
      try { toastHost.hidePopover(); toastHost.showPopover(); } catch (e) { /* not in the page yet */ }
    }
    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' toast-' + kind : '');
    el.textContent = message;
    toastHost.appendChild(el);
    // Let the element land in the DOM before animating it in.
    requestAnimationFrame(() => el.classList.add('is-in'));
    setTimeout(() => {
      el.classList.remove('is-in');
      setTimeout(() => el.remove(), 220);
    }, 3200);
  }

  /* ------------------------------------------------------------------ rail */

  function setRail(state) {
    document.body.dataset.rail = state;
    try { localStorage.setItem(RAIL_KEY, state); } catch (e) {}
  }

  function toggleRail() {
    setRail(document.body.dataset.rail === 'open' ? 'closed' : 'open');
  }

  /* ---------------------------------------------------------------- drawer */

  function setDrawer(open) {
    if (open) document.body.dataset.drawer = 'open';
    else delete document.body.dataset.drawer;
  }

  /* ---------------------------------------------------------- account menu */

  function initAccountMenu() {
    const root = document.querySelector('[data-account]');
    if (!root) return;
    const button = root.querySelector('[data-account-toggle]');
    const menu = root.querySelector('[data-account-menu]');
    if (!button || !menu) return;

    const close = () => {
      menu.hidden = true;
      button.setAttribute('aria-expanded', 'false');
    };

    button.addEventListener('click', (event) => {
      event.stopPropagation();
      const opening = menu.hidden;
      document.querySelectorAll('[data-bell-menu]').forEach((other) => { other.hidden = true; });
      menu.hidden = !opening;
      button.setAttribute('aria-expanded', String(opening));
    });

    document.addEventListener('click', (event) => {
      if (!menu.hidden && !root.contains(event.target)) close();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') close();
    });
  }

  /* ------------------------------------------------ the sidebar's menus */

  // More and Help at the foot of the sidebar. The menu is fixed beside the
  // sidebar (which scrolls, so it would clip an absolute one), its bottom
  // level with the button, and closes on a click elsewhere or Escape.
  function setupRailMenus() {
    // The sidebar's backdrop blur makes it the box a fixed child is placed
    // in (and clipped by), so each menu moves to the page's body.
    const menus = [...document.querySelectorAll('[data-rail-menu]')].map((root) => {
      const pop = root.querySelector('.rail-pop');
      pop.dataset.railPop = '';
      document.body.appendChild(pop);
      return { root, pop, button: root.querySelector('[data-rail-menu-toggle]') };
    });
    const closeAll = (except) => menus.forEach((m) => {
      if (m.root === except) return;
      m.pop.hidden = true;
      m.button.setAttribute('aria-expanded', 'false');
    });
    menus.forEach(({ root, pop, button }) => {
      const place = () => {
        const r = button.getBoundingClientRect();
        const rail = button.closest('.rail').getBoundingClientRect();
        // Beside the sidebar; over it when there's no room (a phone's drawer).
        const width = pop.offsetWidth || 224;
        pop.style.left = `${Math.round(Math.min(rail.right + 6, window.innerWidth - width - 8))}px`;
        pop.style.bottom = `${Math.max(8, Math.round(window.innerHeight - r.bottom))}px`;
      };
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        const opening = pop.hidden;
        closeAll(root);
        pop.hidden = !opening;
        button.setAttribute('aria-expanded', String(opening));
        if (opening) {
          place();
          requestAnimationFrame(place);          // again once it has its width
          const first = pop.querySelector('a, button');
          if (first && event.detail === 0) first.focus();     // opened from the keyboard
        }
      });
      pop.addEventListener('keydown', (event) => {
        const items = [...pop.querySelectorAll('a, button')];
        const at = items.indexOf(document.activeElement);
        if (event.key === 'ArrowDown') { event.preventDefault(); items[(at + 1) % items.length].focus(); }
        if (event.key === 'ArrowUp') { event.preventDefault(); items[(at - 1 + items.length) % items.length].focus(); }
      });
    });
    document.addEventListener('click', (event) => {
      if (!event.target.closest || !event.target.closest('[data-rail-menu], [data-rail-pop]')) closeAll(null);
    });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeAll(null); });
    window.addEventListener('resize', () => closeAll(null));
  }

  /* --------------------------------------------------------- notifications */

  // The bell (app/notify.py). The list is rendered by the server and fetched
  // when the bell opens; the unread count refreshes every minute while the
  // tab is visible, so a transfer or a finished order shows up by itself.
  function initBell() {
    const root = document.querySelector('[data-bell]');
    if (!root) return;
    const button = root.querySelector('[data-bell-toggle]');
    const menu = root.querySelector('[data-bell-menu]');
    const badge = root.querySelector('[data-bell-count]');

    const setCount = (n) => {
      badge.textContent = n > 99 ? '99+' : String(n);
      badge.hidden = !n;
      button.setAttribute('aria-label', n ? t('Notifications: %(count)s unread', { count: n }) : t('Notifications'));
    };
    const load = async () => {
      try {
        const response = await fetch(root.dataset.panelUrl, { credentials: 'same-origin' });
        if (response.ok) menu.innerHTML = await response.text();   // our own escaped template
      } catch (_) {
        menu.textContent = t('Could not load notifications.');
      }
    };
    const close = () => {
      menu.hidden = true;
      button.setAttribute('aria-expanded', 'false');
    };
    // On a phone the bell is not at the screen's edge, so a menu hung from
    // it would run off the left side: pin it under the header instead.
    const place = () => {
      if (window.innerWidth > 480) {
        menu.style.cssText = '';
        return;
      }
      const below = Math.round(button.getBoundingClientRect().bottom + 6);
      menu.style.cssText = `position: fixed; top: ${below}px; left: 8px; right: 8px; width: auto; max-width: none;`;
    };

    button.addEventListener('click', (event) => {
      event.stopPropagation();
      const opening = menu.hidden;
      document.querySelectorAll('[data-account-menu]').forEach((other) => { other.hidden = true; });
      menu.hidden = !opening;
      button.setAttribute('aria-expanded', String(opening));
      if (opening) {
        place();
        load();
      }
    });
    window.addEventListener('resize', () => { if (!menu.hidden) place(); });
    document.addEventListener('click', (event) => {
      if (!menu.hidden && !root.contains(event.target)) close();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') close();
    });
    menu.addEventListener('submit', async (event) => {
      const form = event.target.closest('form[data-mark-read]');
      if (!form) return;
      event.preventDefault();
      await fetch(form.action, { method: 'POST', credentials: 'same-origin',
                                 headers: { Accept: 'application/json' } });
      setCount(0);
      load();
    });

    const poll = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(root.dataset.countUrl, { credentials: 'same-origin' });
        if (response.ok) setCount((await response.json()).unread || 0);
      } catch (_) { /* offline for a moment: try again next minute */ }
    };
    setInterval(poll, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  }

  /* ------------------------------------------------------------- shortcuts */

  function initShortcuts() {
    document.addEventListener('keydown', (event) => {
      const tabs = window.BiomanagerTabs;
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) ||
                     event.target.isContentEditable;

      // Cmd/Ctrl+B — collapse or expand the rail. Safe to use while typing
      // is not: browsers map it to "bold" in rich text fields.
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'b' && !typing) {
        event.preventDefault();
        toggleRail();
        return;
      }

      // Everything below is Alt-based so it never collides with the
      // browser's own Cmd/Ctrl+number and Cmd/Ctrl+W bindings.
      if (!event.altKey || event.metaKey || event.ctrlKey || !tabs) return;

      // By the physical key (event.code): on a Mac, Option turns 1 into ¡
      // and W into ∑, so event.key never matches there.
      const code = event.code || '';
      const digit = /^(?:Digit|Numpad)([1-9])$/.exec(code);
      if (digit) {
        event.preventDefault();
        tabs.activate(parseInt(digit[1], 10) - 1);
        return;
      }
      if (code === 'KeyW') { event.preventDefault(); tabs.closeCurrent(); return; }
      if (event.key === 'ArrowLeft' || code === 'BracketLeft') { event.preventDefault(); tabs.step(-1); return; }
      if (event.key === 'ArrowRight' || code === 'BracketRight') { event.preventDefault(); tabs.step(1); }
    });
  }

  /* ------------------------------------------------- the desktop app's menus */

  // In the desktop app, the Go menu lists what this sidebar shows
  // (desktop_menu.py, through pywebview's bridge). Anywhere else there is
  // no window.pywebview and this does nothing.
  function shareNavWithDesktop() {
    const send = () => {
      const api = window.pywebview && window.pywebview.api;
      if (!api || typeof api.set_nav !== 'function') return;
      const links = (root) => [...root.querySelectorAll('a.rail-item[href], .rail-pop a[href]')]
        .filter((a) => a.getAttribute('href').startsWith('/'))
        .map((a) => ({ label: a.dataset.label || a.textContent.trim(), url: a.getAttribute('href') }));
      const sections = [...document.querySelectorAll('.rail-group')].map((group) => ({
        label: ((group.querySelector('.rail-group-label') || {}).textContent || '').trim(),
        links: links(group),
      }));
      const foot = document.querySelector('.rail-foot');
      if (foot) {
        const more = [...links(foot), ...[...document.querySelectorAll('[data-rail-pop] a[href]')]
          .filter((a) => a.getAttribute('href').startsWith('/'))
          .map((a) => ({ label: a.dataset.label || a.textContent.trim(), url: a.getAttribute('href') }))];
        sections.push({ label: t('More'), links: more });
      }
      api.set_nav(sections);
    };
    const ready = () => window.pywebview && window.pywebview.api && typeof window.pywebview.api.set_nav === 'function';
    if (ready()) send();
    else window.addEventListener('pywebviewready', send, { once: true });
  }

  /* ------------------------------------------------- arranging the rail */

  // An admin drags a database up or down the rail itself to put the lab's
  // databases in the order it works in. The rail's group carries the save
  // address (data-rail-order) only for an admin; the drop posts the keys
  // as they now read, which organisms.reorder places among the rest.
  function setupRailOrder() {
    const group = document.querySelector('[data-rail-order]');
    if (!group) return;
    let dragged = null;
    let before = '';
    const keys = () => [...group.querySelectorAll('[data-db-key]')].map((el) => el.dataset.dbKey);
    group.addEventListener('dragstart', (event) => {
      dragged = event.target.closest && event.target.closest('[data-db-key]');
      if (!dragged) return;
      before = keys().join(',');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', dragged.dataset.dbKey);
      requestAnimationFrame(() => { if (dragged) dragged.style.opacity = '0.4'; });
    });
    group.addEventListener('dragover', (event) => {
      if (!dragged) return;
      const over = event.target.closest && event.target.closest('[data-db-key]');
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      if (!over || over === dragged) return;
      const box = over.getBoundingClientRect();
      over.parentNode.insertBefore(dragged, event.clientY > box.top + box.height / 2 ? over.nextSibling : over);
    });
    group.addEventListener('drop', (event) => { if (dragged) event.preventDefault(); });
    group.addEventListener('dragend', () => {
      if (!dragged) return;
      dragged.style.opacity = '';
      dragged = null;
      const now = keys();
      if (now.join(',') === before) return;
      const body = new FormData();
      body.append('rail', '1');
      now.forEach((key) => body.append('keys', key));
      fetch(group.dataset.railOrder, { method: 'POST', body, headers: { 'X-Autosave': '1' } })
        .then((r) => r.json().catch(() => ({})).then((data) => {
          if (!r.ok || data.ok === false) throw new Error(data.error || '');
          toast(t('Saved the order of the databases.'), 'ok');
        }))
        .catch((error) => toast(error.message || t('Could not save the order. Reload the page and try again.'), 'danger'));
    });
  }

  /* ------------------------------------------------------------------ init */

  function init() {
    shareNavWithDesktop();
    setupRailMenus();
    setupRailOrder();
    document.querySelectorAll('[data-rail-toggle]').forEach((el) => {
      el.addEventListener('click', toggleRail);
    });
    document.querySelectorAll('[data-drawer-toggle]').forEach((el) => {
      el.addEventListener('click', () => setDrawer(document.body.dataset.drawer !== 'open'));
    });
    document.querySelectorAll('[data-drawer-close]').forEach((el) => {
      el.addEventListener('click', () => setDrawer(false));
    });

    // Placeholder modules (Drosophila, "add database") explain themselves
    // instead of firing a native alert().
    document.querySelectorAll('[data-soon]').forEach((el) => {
      el.addEventListener('click', () => toast(el.dataset.soon));
    });

    // The omnibox searches in the tab you are already in; "+" (below) opens
    // a new tab on your start page.
    const omnibox = document.querySelector('#app-global-search');
    if (omnibox) {
      omnibox.addEventListener('click', (event) => {
        event.preventDefault();
        if (window.BiomanagerTabs) window.BiomanagerTabs.cancelNewTab();
        if (window.BiomanagerSearch) window.BiomanagerSearch.open();
      });
    }

    const newTabButton = document.querySelector('[data-new-tab]');
    if (newTabButton) {
      // A new tab opens on the person's start page, as a browser's opens on
      // its home page; the palette (Cmd/Ctrl+K) is for going somewhere else.
      newTabButton.addEventListener('click', (event) => {
        event.preventDefault();
        const start = newTabButton.dataset.newTab || '/';
        if (window.BiomanagerTabs) window.BiomanagerTabs.open(start);
        else window.location.href = start;
      });
    }

    // Dismissing the palette abandons the request, so a link clicked
    // afterwards still navigates in place.
    document.addEventListener('biomanager:palette-closed', () => {
      if (window.BiomanagerTabs) window.BiomanagerTabs.cancelNewTab();
    });

    // Show the right modifier glyph for the platform.
    if (!isMac) {
      document.querySelectorAll('.omnibox .kbd').forEach((el) => { el.textContent = 'Ctrl K'; });
    }

    // macOS toolbars are borderless until content scrolls beneath them.
    const scroller = document.getElementById('app-scroll');
    const toolbar = document.getElementById('app-toolbar');
    if (scroller && toolbar) {
      const sync = () => toolbar.classList.toggle('is-scrolled', scroller.scrollTop > 2);
      scroller.addEventListener('scroll', sync, { passive: true });
      sync();
    }

    initAccountMenu();
    initBell();
    initShortcuts();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.BiomanagerShell = { toast, toggleRail, setRail, setDrawer };
})();

/* Confirm before a destructive submit: <form data-confirm="Delete V12?">,
   or on one button of a form: <button data-confirm="…">.
   The text lives in an attribute (escaped by Jinja), never inside an
   onsubmit string, where a name containing a quote could break out and
   run as script. Selection-bar forms handle their own ({n}) prompt. */
document.addEventListener('submit', (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || form.hasAttribute('data-selection-form')) return;
  const text = (event.submitter && event.submitter.getAttribute('data-confirm')) || form.getAttribute('data-confirm');
  if (!text) return;
  // Answered yes a moment ago: this is the submit that follows (see below).
  if (form.dataset.confirmed === '1') { delete form.dataset.confirmed; return; }
  // Ask in the app (BioDialog), then submit again with the same button.
  event.preventDefault();
  event.stopImmediatePropagation();
  const submitter = event.submitter;
  window.BioDialog.confirm(text, { danger: window.BioDialog.looksDestructive(text) }).then((ok) => {
    if (!ok) return;
    form.dataset.confirmed = '1';
    if (typeof form.requestSubmit === 'function') form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
    else { delete form.dataset.confirmed; form.submit(); }
  });
}, true);
