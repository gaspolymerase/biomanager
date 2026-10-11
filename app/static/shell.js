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

  // A call to the desktop app (desktop_menu.DesktopApi). pywebview builds
  // window.pywebview.api with new Function() and sends answers back through
  // eval, both of which the page's CSP refuses; so this uses the call
  // channel under them, and the app answers, where it needs to, by calling
  // the page itself (desktop_menu._js).
  function desktopCall(name, ...args) {
    const pv = window.pywebview;
    if (!pv || typeof pv._jsApiCallback !== 'function') return false;
    pv._jsApiCallback(name, args, String(Math.random()).slice(2));
    return true;
  }
  // Run `fn` once the desktop app's channel is there (pywebview adds it
  // after the page loads); never outside the app.
  function whenDesktop(fn) {
    let tries = 0;
    const look = () => {
      if (window.pywebview && typeof window.pywebview._jsApiCallback === 'function') fn();
      else if (++tries < 50) setTimeout(look, 100);
    };
    look();
  }

  // In the desktop app, the Go menu lists what this sidebar shows
  // (desktop_menu.py). Anywhere else this does nothing.
  function shareNavWithDesktop() {
    const send = () => {
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
      desktopCall('set_nav', sections);
    };
    whenDesktop(send);
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

    // The Mac window's tools beside its buttons: search as the omnibox does,
    // and back and forward, greyed where there is nowhere to go (where the
    // browser can say: the Navigation API).
    const wtools = document.querySelector('.wtools');
    if (wtools) {
      wtools.querySelector('[data-wtool-search]').addEventListener('click', () => {
        if (window.BiomanagerTabs) window.BiomanagerTabs.cancelNewTab();
        if (window.BiomanagerSearch) window.BiomanagerSearch.open();
      });
      const back = wtools.querySelector('[data-wtool-back]');
      const forward = wtools.querySelector('[data-wtool-forward]');
      back.addEventListener('click', () => history.back());
      forward.addEventListener('click', () => history.forward());
      const nav = window.navigation;
      if (nav && 'canGoBack' in nav) {
        const sync = () => { back.disabled = !nav.canGoBack; forward.disabled = !nav.canGoForward; };
        sync();
        nav.addEventListener('currententrychange', sync);
        // The Mac window's web view fills in what lies ahead a moment after a
        // page loads, and a page brought back from the back-forward cache
        // says nothing: ask again then, and as the pointer comes near.
        window.addEventListener('pageshow', () => { sync(); setTimeout(sync, 400); });
        wtools.addEventListener('pointerenter', sync);
      }
    }

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

    // The page scrolls beneath the two rows; once it does, a frosted band
    // (.chrome-edge) comes up behind them to keep them readable.
    const scroller = document.getElementById('app-scroll');
    const toolbar = document.getElementById('app-toolbar');
    if (scroller && toolbar) {
      const sync = () => toolbar.classList.toggle('is-scrolled', scroller.scrollTop > 2);
      scroller.addEventListener('scroll', sync, { passive: true });
      sync();
    }

    initWindowFrame();
    initPhone();
    initDrops();
    initSheen();
    initAccountMenu();
    initBell();
    initShortcuts();
  }

  // The window around the page. A window in the background shows its
  // selection in grey (html.window-inactive), as a Mac's does. In the Mac
  // app, which draws no title bar (desktop_mac.py), the empty part of the
  // top row and the title row stands in for one: drag it to move the window,
  // double-click it to zoom.
  function initWindowFrame() {
    const root = document.documentElement;
    const sync = () => root.classList.toggle('window-inactive', !document.hasFocus());
    window.addEventListener('focus', sync);
    window.addEventListener('blur', sync);
    sync();

    const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bmWindow;
    if (!native || !root.classList.contains('mac-window')) return;
    // The bare rows themselves, and the sidebar's top strip beside the lights
    // (or, with the sidebar collapsed below the top row, the window above it).
    const rowHeight = () => parseFloat(getComputedStyle(root).getPropertyValue('--tabbar-h')) || 46;
    const bare = (event) => {
      const el = event.target;
      if (!el.matches) return false;
      if (el.matches('.topstrip, .strip-actions, .toolbar, .toolbar > .min-w-0, .toolbar-spacer, .toolbar-title, .toolbar-sub')) return true;
      return el.matches('.rail, .rail-top, .shell') && event.clientY < rowHeight();
    };
    let press = null;
    document.addEventListener('mousedown', (event) => {
      press = event.button === 0 && bare(event) ? { x: event.screenX, y: event.screenY } : null;
    });
    // A drag, not a click: ask once the mouse has moved a little while held.
    document.addEventListener('mousemove', (event) => {
      if (!press || !(event.buttons & 1)) { press = null; return; }
      if (Math.abs(event.screenX - press.x) + Math.abs(event.screenY - press.y) < 3) return;
      press = null;
      native.postMessage('drag');
    });
    document.addEventListener('mouseup', () => { press = null; });
    document.addEventListener('dblclick', (event) => {
      if (bare(event)) native.postMessage('zoom');
    });
  }

  // The selection in the tab strip and in each segmented control (.seg) is
  // one drop that slides to what you pick. Most picks load a new page, so
  // the drop's last place is kept for the next page (sessionStorage), which
  // starts it there and slides it on. tab-bar.js redraws the tabs, so their
  // drop lives in the capsule around them (.wtab-strip).
  function initDrops() {
    const remember = (key, box) => {
      try { sessionStorage.setItem('biomanager:drop:' + key, JSON.stringify(box)); } catch (e) {}
    };
    const recall = (key) => {
      try {
        const box = JSON.parse(sessionStorage.getItem('biomanager:drop:' + key) || 'null');
        sessionStorage.removeItem('biomanager:drop:' + key);
        return box;
      } catch (e) { return null; }
    };

    function drop({ holder, list, itemSelector, activeSelector, key, className }) {
      const el = document.createElement('span');
      el.className = className;
      el.setAttribute('aria-hidden', 'true');
      holder.prepend(el);
      // Where `item` is, in the drop's own coordinates (inside the
      // holder's border; moving with its scroll when it scrolls itself).
      const boxOf = (item) => {
        const h = holder.getBoundingClientRect();
        const r = item.getBoundingClientRect();
        const scroll = holder === list ? holder.scrollLeft : 0;
        return { x: r.left - h.left - holder.clientLeft + scroll, y: r.top - h.top - holder.clientTop, w: r.width, h: r.height };
      };
      const place = (box) => {
        el.style.transform = `translate(${box.x}px, ${box.y}px)`;
        el.style.width = box.w + 'px';
        el.style.height = box.h + 'px';
      };
      const still = (fn) => {
        holder.classList.add('drop-still');
        fn();
        void el.offsetWidth;
        holder.classList.remove('drop-still');
      };
      const sync = (animate) => {
        const active = list.querySelector(activeSelector);
        el.hidden = !active;
        list.classList.toggle('has-drop', !!active);
        if (!active) return;
        if (animate) place(boxOf(active)); else still(() => place(boxOf(active)));
      };
      // The tabs may be drawn after this runs: start once there is a
      // selection, from where the last page left the drop.
      let from = recall(key);
      const start = () => {
        if (!list.querySelector(activeSelector)) { sync(false); return false; }
        if (from) {
          const box = from;
          from = null;
          el.hidden = false;
          list.classList.add('has-drop');
          // Drawn there with no transition, then moved: the move slides.
          still(() => place(box));
          sync(true);
        } else {
          sync(false);
        }
        return true;
      };
      let started = start();
      // Picked: slide there at once (before any page loads), and remember.
      list.addEventListener('click', (event) => {
        const item = event.target.closest(itemSelector);
        if (!item || !list.contains(item) || event.target.closest('.wtab-close')) return;
        const active = list.querySelector(activeSelector);
        if (active) remember(key, boxOf(active));
        place(boxOf(item));
      });
      new MutationObserver(() => {
        if (started) sync(true); else started = start();
      }).observe(list, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
      list.addEventListener('scroll', () => sync(false), { passive: true });
      window.addEventListener('resize', () => sync(false));
    }

    const strip = document.querySelector('.wtab-strip');
    const tabs = document.getElementById('app-tabbar');
    if (strip && tabs) {
      drop({ holder: strip, list: tabs, itemSelector: '.wtab', activeSelector: '.wtab.is-active', key: 'tabs', className: 'wtab-drop' });
    }
    document.querySelectorAll('.seg').forEach((seg, index) => {
      if (!seg.querySelector('.seg-item')) return;
      drop({ holder: seg, list: seg, itemSelector: '.seg-item', activeSelector: '.seg-item.is-active',
             key: location.pathname + ':' + index, className: 'seg-drop' });
    });
  }

  // The phone's tab bar (base.html .phone-tabs). Databases opens a sheet of
  // the lab's databases, Search the search palette. Scan asks the phone app
  // for its scanner (iOS: bmScan, which answers through window.bmScanned;
  // Android: BioManagerApp.scan, which opens the card itself), else reads
  // the code with the camera here (BarcodeDetector), else says to use the
  // Camera app, which opens the card's link. The bar shrinks to its icons
  // while the page scrolls down.
  function initPhone() {
    const bar = document.querySelector('.phone-tabs');
    if (!bar) return;
    const ios = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bmScan;
    const android = window.BioManagerApp;
    const phone = window.matchMedia('(max-width: 767px)');

    // The page has its own Scan: the app's floating button can go.
    const claim = () => {
      if (!phone.matches) return;
      if (ios) ios.postMessage('page-has-scan');
      if (android && android.pageHasScan) android.pageHasScan();
    };
    claim();
    phone.addEventListener('change', claim);

    const sheet = (id) => {
      const d = document.getElementById(id);
      if (d && !d.open) d.showModal();
    };
    document.querySelectorAll('dialog.phone-sheet').forEach((d) => {
      d.addEventListener('click', (event) => {
        if (event.target === d || event.target.closest('[data-phone-sheet-close]')) d.close();
      });
    });
    const dbs = bar.querySelector('[data-phone-dbs]');
    if (dbs) dbs.addEventListener('click', () => sheet('phone-dbs'));
    bar.querySelector('[data-phone-search]').addEventListener('click', () => {
      const search = document.getElementById('app-global-search');
      if (search) search.click();
    });

    // What a scan read: a card is a link to its record on this lab.
    if (!window.bmScanned) {
      window.bmScanned = (value) => {
        let url = null;
        try { url = new URL(String(value || '').trim(), location.href); } catch (e) {}
        if (url && url.origin === location.origin && /^https?:/.test(String(value).trim())) {
          location.href = url.href;
        } else if (window.BiomanagerShell) {
          toast(bar.dataset.notThisLab, 'danger');
        }
      };
    }
    async function camera() {
      const box = document.createElement('div');
      box.className = 'scan-camera';
      box.innerHTML = '<video playsinline muted></video><button type="button" class="btn"></button>';
      box.querySelector('button').textContent = t('Cancel');
      document.body.appendChild(box);
      const video = box.querySelector('video');
      let stream = null;
      let done = false;
      const stop = () => { done = true; if (stream) stream.getTracks().forEach((track) => track.stop()); box.remove(); };
      box.querySelector('button').addEventListener('click', stop);
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
        video.srcObject = stream;
        await video.play();
        const detector = new window.BarcodeDetector({ formats: ['qr_code'] });
        const look = async () => {
          if (done) return;
          const codes = await detector.detect(video).catch(() => []);
          if (codes.length) { stop(); window.bmScanned(codes[0].rawValue); return; }
          setTimeout(look, 120);
        };
        look();
      } catch (e) {
        stop();
        sheet('phone-scan-help');
      }
    }
    bar.querySelector('[data-phone-scan]').addEventListener('click', () => {
      if (android && android.scan) android.scan();
      else if (ios) ios.postMessage('scan');
      else if ('BarcodeDetector' in window && navigator.mediaDevices) camera();
      else sheet('phone-scan-help');
    });

    // Smaller while reading down the page, full again on the way back up.
    const scroller = document.getElementById('app-scroll');
    if (scroller) {
      let last = scroller.scrollTop;
      scroller.addEventListener('scroll', () => {
        const now = scroller.scrollTop;
        if (now > last + 6 && now > 40) bar.classList.add('is-small');
        else if (now < last - 6 || now <= 40) bar.classList.remove('is-small');
        last = now;
      }, { passive: true });
    }
  }

  // A sheen follows the mouse across glass, as light would.
  function initSheen() {
    const GLASS = '.wtab-strip, .strip-you, .omnibox, .toolbar-actions .btn, .toolbar-actions .btn-ghost';
    let lit = null;
    let frame = 0;
    const clear = () => {
      if (lit) { lit.style.removeProperty('--glass-x'); lit.style.removeProperty('--glass-y'); }
      lit = null;
    };
    document.addEventListener('pointermove', (event) => {
      if (event.pointerType !== 'mouse') return;
      const glass = event.target.closest && event.target.closest(GLASS);
      if (glass !== lit) clear();
      if (!glass) return;
      lit = glass;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const r = glass.getBoundingClientRect();
        glass.style.setProperty('--glass-x', (event.clientX - r.left) + 'px');
        glass.style.setProperty('--glass-y', (event.clientY - r.top) + 'px');
      });
    }, { passive: true });
    document.addEventListener('pointerleave', clear);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.BiomanagerShell = { toast, toggleRail, setRail, setDrawer, desktopCall, whenDesktop };
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
