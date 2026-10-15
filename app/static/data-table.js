/* Reusable data-table behavior.
 *
 * Activate by giving the wrapper a data-table-id attribute. The script
 * auto-attaches on DOMContentLoaded:
 *
 *   <section class="data-table-card" data-table-id="orders">
 *     <div class="dt-toolbar">
 *       <input class="dt-search" />
 *       <button class="dt-btn-sort">…</button>
 *       <button class="dt-btn-hide">…</button>
 *     </div>
 *     <div class="dt-scroll">
 *       <table class="dt" data-resizable="1">…</table>
 *     </div>
 *     <div class="dt-bottom-bar">
 *       <span class="dt-count"></span>
 *       <select class="dt-page-size"><option>50</option>…</select>
 *       <button class="dt-prev"></button><button class="dt-next"></button>
 *       <span class="dt-page-label"></span>
 *     </div>
 *   </section>
 *
 * Search filters rows by the union of all data-* attributes. Sort uses
 * th.dt-sortable[data-sort-key]. Column widths and hidden columns are
 * persisted to localStorage keyed by data-table-id, by the column's number
 * as the page draws it; so is the order a person has dragged the columns
 * into, by each column's key (its data-sort-key, else its name). A row may
 * carry a detail row, <tr data-detail-for="<its data-id>" hidden>, which
 * moves and pages with it (the page toggles its `hidden`).
 *
 * Columns that stay put when the others are moved: the pinned ones
 * (.sheet-pin: the tick boxes, the ID, the actions), any th[data-dt-fixed],
 * a header holding a checkbox, and one with no visible name.
 *
 * A sheet the server pages (data-dt-rows-url on the card) draws only its
 * first rows; each other row comes as an entry in
 * <script type="application/json" data-dt-stubs>: {id, e: its element id,
 * d: its data-*, k: each sortable cell's value, s: its search text}. It
 * stands in the sheet as an empty <tr> (a stub) until its page is shown,
 * or the sheet is exported or printed; its rows then come from
 * <rows-url>?ids=…, {rows: "<tr>…", forms: "<form>…"}, the forms going
 * into a <template data-row-forms> (row-forms.js).
 */

(function () {
  'use strict';

  // SVG glyphs for column-type icons. Inserted into .dt-col-type spans with
  // a data-icon attribute (date/status/check/list — types where a unicode
  // letter doesn't suffice). Text/number/id are handled by ::before in CSS.
  const ICONS = {
    date: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>',
    status: '<svg viewBox="0 0 24 24"><path d="M2 12a10 10 0 0 1 10-10"/><path d="M12 2a10 10 0 0 1 10 10"/><path d="M22 12a10 10 0 0 1-10 10"/><path d="M12 22A10 10 0 0 1 2 12"/></svg>',
    check: '<svg viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="3"/><path d="m9 12 2 2 4-4"/></svg>',
    list: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/></svg>',
    file: '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  };

  function injectIconSvgs(root) {
    root.querySelectorAll('.dt-col-type[data-icon]').forEach((el) => {
      const kind = el.dataset.icon;
      if (ICONS[kind] && !el.innerHTML.trim()) el.innerHTML = ICONS[kind];
    });
    root.querySelectorAll('.dt-row-icon').forEach((el) => {
      if (!el.innerHTML.trim()) el.innerHTML = ICONS.file;
    });
  }

  // Only one toolbar menu is open at a time.
  let openMenu = null;
  function closeMenus() {
    if (!openMenu) return;
    if (openMenu._anchor) openMenu._anchor.setAttribute('aria-expanded', 'false');
    openMenu.remove();
    openMenu = null;
  }
  document.addEventListener('click', (event) => {
    if (openMenu && !openMenu.contains(event.target)) closeMenus();
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeMenus(); });
  window.addEventListener('resize', closeMenus);
  document.addEventListener('scroll', (event) => {
    if (openMenu && !openMenu.contains(event.target)) closeMenus();
  }, true);

  // A sheet longer than this keeps the rows not yet shown off the page (_parkLater).
  const PARK_OVER = 1000;
  const sheets = [];

  /* A sheet's rows, on the page or waiting off it (_parkLater): use these
     rather than document.querySelector for a row that may not have been
     shown (a board drag, a grid click, ?open=…).
       BioSheets.row(id[, el])   the row with that data-id (in el's sheet), or null
       BioSheets.rows([el])      every row (of el's sheet)
       BioSheets.show(tr)        turn its sheet to the row, which puts it on the page */
  window.BioSheets = {
    rows(el) {
      const card = el && el.closest ? el.closest('.data-table-card') : null;
      return sheets.filter((c) => !card || c.card === card).flatMap((c) => c.rows);
    },
    row(id, el) {
      const want = String(id);
      return this.rows(el).find((tr) => tr.dataset.id === want) || null;
    },
    show(tr) {
      const sheet = sheets.find((c) => c.rows.includes(tr));
      return Boolean(sheet && sheet.reveal(tr));
    },
    // The row itself, fetched if it is still a stub (a sheet the server
    // pages): a Promise of the <tr>, or of null if it has gone.
    ready(tr) {
      const sheet = tr && sheets.find((c) => c.rows.includes(tr));
      if (!sheet || !tr._stub) return Promise.resolve(tr || null);
      const id = tr.dataset.id;
      return sheet._loadRows([tr]).then(() => sheet.rows.find((r) => r.dataset.id === id) || null);
    },
  };

  // A row the server has not drawn yet (see the top of this file).
  function makeStub(entry) {
    const tr = document.createElement('tr');
    tr.dataset.id = String(entry.id);
    if (entry.e) tr.id = entry.e;
    Object.entries(entry.d || {}).forEach(([key, value]) => { tr.dataset[key] = value; });
    tr._stub = { k: entry.k || {}, s: entry.s || '' };
    return tr;
  }
  const ROWS_AT_ONCE = 500;

  // Per-table state keyed by table id.
  class DataTableController {
    constructor(card) {
      this.card = card;
      sheets.push(this);
      this.id = card.dataset.tableId || 'data-table';
      this.table = card.querySelector('table.dt');
      this.tbody = this.table && this.table.tBodies[0];
      this.rows = this.tbody ? Array.from(this.tbody.querySelectorAll('tr[data-id]')) : [];
      this.rowsUrl = card.dataset.dtRowsUrl || '';
      const stubs = card.querySelector('script[data-dt-stubs]');
      if (stubs && this.rowsUrl && this.tbody) {
        try { JSON.parse(stubs.textContent).forEach((entry) => this.rows.push(makeStub(entry))); } catch (_) { /* drawn rows only */ }
        stubs.remove();
      }
      this.original = this.rows.slice();
      this.filtered = this.rows.slice();
      this._laidOut = this.rows.slice();   // the order the rows are in on the page
      // A row's detail row (<tr data-detail-for="<its data-id>">), wherever it is.
      this.details = new Map();
      if (this.tbody) this.tbody.querySelectorAll(':scope > tr[data-detail-for]').forEach((d) => this.details.set(d.dataset.detailFor, d));
      this.sortKey = null;
      this.sortDir = 1;
      this.page = 0;
      this.query = '';
      this.quick = null;   // [{attr, value}, …] from a .dt-chip
      this._indexColumns();
      this.hidden = this._loadHiddenCols();
      this.order = this._loadOrder();
      this.noun = card.dataset.noun || card.dataset.selectionNoun || 'entry';
      this.nounPlural = card.dataset.nounPlural || card.dataset.selectionNounPlural
        || (this.noun === 'entry' ? 'entries' : this.noun + 's');

      this.search = card.querySelector('.dt-search');
      this.count = card.querySelector('.dt-count');
      this.pageSize = card.querySelector('.dt-page-size');
      this.pageLabel = card.querySelector('.dt-page-label');
      this.prev = card.querySelector('.dt-prev');
      this.next = card.querySelector('.dt-next');
      this.selectAll = card.querySelector('.dt-select-all');

      injectIconSvgs(card);
      this._wireHeaderSort();
      this._wireSearch();
      this._wireSelection();
      this._wirePagination();
      this._wireHideButton();
      this._wireSortButton();
      this._wireChips();
      this._wireResize();
      this._wireExport();
      this._labelCells();
      this._applyOrder();
      this._wireColumnDrag();
      this._applyHidden();
      this._applyResizedWidths();
      this._restoreSort();
      this._wireQuickAdd();
      this._wireMore();
      this._wireTight();
      this.render();
      this._wireStickyParts();
      if (!this._showFresh()) this._revealHashTarget();
      window.addEventListener('hashchange', () => this._revealHashTarget());
      this._parkLater();
    }

    /* A long sheet's rows that have not been shown wait off the page until
       they are (render puts them back, and they stay). On the page, every
       field of every row, shown or not, was read by Chromium's autofill as
       the page loaded (Chrome, Edge, the Windows app): over a minute for a
       sheet of a few thousand rows. They leave once every script has seen
       them as the page loaded (window's DOMContentLoaded comes after the
       document's), and before Chromium looks. A sheet whose scripts need
       every row on the page says so with data-dt-keep-rows. Code that
       looks a row up later uses BioSheets.row(id). */
    _parkLater() {
      // A sheet the server pages has drawn only its first page.
      if (!this.tbody || this.rows.length <= PARK_OVER || this.rowsUrl || this.card.hasAttribute('data-dt-keep-rows')
          || document.readyState === 'complete') return;
      window.addEventListener('DOMContentLoaded', () => {
        // All of the body's rows lifted out at once, the shown ones put back
        // in their order: one row at a time took a second on a long sheet.
        const range = document.createRange();
        range.selectNodeContents(this.tbody);
        const away = range.extractContents();
        const rows = new Set(this.rows);
        const byId = new Map(this.rows.map((tr) => [tr.dataset.id, tr]));
        const shown = (tr) => tr.style.display !== 'none';
        this._parked = true;
        const back = Array.from(away.children).filter((tr) => {
          if (tr.dataset.detailFor !== undefined) {
            const owner = byId.get(tr.dataset.detailFor);
            return !owner || shown(owner);
          }
          return !rows.has(tr) || shown(tr);
        });
        this.tbody.append(...back);
      }, { once: true });
    }

    // The toolbar's rarer actions (Print, Import from Excel: data-dt-more)
    // go into a ••• menu beside the page's New button, so the toolbar keeps
    // to one line. They keep their own handlers. While open, the menu is on
    // the page itself (fixed), like the Columns and sort menus: inside the
    // sticky toolbar, the bottom bar and the sidebar covered it.
    _wireMore() {
      const toolbar = this.card.querySelector(':scope > .dt-toolbar');
      const items = toolbar ? toolbar.querySelectorAll(':scope > [data-dt-more]') : [];
      if (!items.length) return;
      const wrap = document.createElement('div');
      wrap.className = 'dt-more';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'dt-toolbar-btn dt-more-btn';
      button.title = t('More');
      button.setAttribute('aria-label', t('More'));
      button.setAttribute('aria-haspopup', 'menu');
      button.setAttribute('aria-expanded', 'false');
      button.innerHTML = '<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#ellipsis"></use></svg>';
      const menu = document.createElement('div');
      menu.className = 'menu dt-more-menu';
      menu.setAttribute('role', 'menu');
      menu.hidden = true;
      items.forEach((item) => {
        item.setAttribute('role', 'menuitem');
        menu.appendChild(item);
      });
      wrap.append(button, menu);
      toolbar.insertBefore(wrap, toolbar.querySelector(':scope > .btn-primary'));

      const close = () => {
        if (menu.hidden) return;
        menu.hidden = true;
        button.setAttribute('aria-expanded', 'false');
        wrap.appendChild(menu);
      };
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        if (!menu.hidden) { close(); return; }
        document.body.appendChild(menu);
        menu.hidden = false;
        button.setAttribute('aria-expanded', 'true');
        const r = button.getBoundingClientRect();
        const width = menu.offsetWidth;
        menu.style.top = (r.bottom + 6) + 'px';
        menu.style.left = Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8)) + 'px';
      });
      menu.addEventListener('click', () => setTimeout(close));
      document.addEventListener('click', (event) => {
        if (!wrap.contains(event.target) && !menu.contains(event.target)) close();
      });
      document.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
      document.addEventListener('scroll', close, true);
      window.addEventListener('resize', close);
    }

    // Where the toolbar would wrap onto a second line (a narrow window, many
    // chips), its buttons show their icons only (.is-tight), each still
    // named in its tooltip and to screen readers.
    _wireTight() {
      const toolbar = this.card.querySelector(':scope > .dt-toolbar');
      if (!toolbar || typeof ResizeObserver === 'undefined') return;
      toolbar.querySelectorAll('.dt-toolbar-btn').forEach((b) => {
        if (!b.title && b.textContent.trim()) b.title = b.textContent.trim();
      });
      const wraps = () => {
        const items = Array.from(toolbar.children).filter((el) => el.offsetParent !== null);
        if (items.length < 2) return false;
        const top = items[0].offsetTop;
        return items.some((el) => el.offsetTop > top + 8);
      };
      const fit = () => {
        toolbar.classList.remove('is-tight');
        if (wraps()) toolbar.classList.add('is-tight');
      };
      // Its first call comes with the first frame, not now: measuring now
      // laid out every row of a long sheet before the unseen ones left.
      new ResizeObserver(fit).observe(toolbar);
    }

    _restoreSort() {
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(`dt:${this.id}:sort`) || 'null'); } catch (_) {}
      if (!Array.isArray(saved) || !this.table) return;
      const th = Array.from(this.table.querySelectorAll('th.dt-sortable')).find((h) => h.dataset.sortKey === saved[0]);
      if (!th) return;
      this.sortKey = saved[0];
      this.sortDir = saved[1] === -1 ? -1 : 1;
      th.classList.add(this.sortDir === 1 ? 'dt-sort-asc' : 'dt-sort-desc');
      const sortBtn = this.card.querySelector('.dt-btn-sort');
      if (sortBtn) sortBtn.classList.add('is-active');
      this._applySort();
    }

    /* Bring a row into view: switch to the page it is on, first clearing a
       search or quick filter that hides it. Used when a link or a QR code
       names the row (…/colony?view=cages#cage-12). */
    reveal(tr) {
      if (!this.filtered.includes(tr)) {
        this.query = '';
        if (this.search) this.search.value = '';
        const everything = this.card.querySelector('.dt-chip[data-dt-filter=""]');
        if (everything) everything.click();   // also remembers "all" and repaints the chips
        if (!this.filtered.includes(tr)) { this.quick = null; this._refilter(); }
      }
      const index = this.filtered.indexOf(tr);
      if (index < 0) return false;
      const size = this.pageSize ? parseInt(this.pageSize.value, 10) : 50;
      this.page = Math.floor(index / size);
      this.render();
      return true;
    }

    _revealHashTarget() {
      const id = decodeURIComponent(location.hash.slice(1));
      const target = id && document.getElementById(id);
      // A sheet row's form sits outside the row, its cells joining it with
      // form="…" (a notification links to #mouse-update-39): find the row by
      // them, whether or not the form is on the page yet (row-forms.js).
      const byForm = () => this.rows.find((tr) => tr.querySelector(`[form="${CSS.escape(id)}"]`));
      // A row not shown yet is off the page (_parkLater): look in the rows.
      const inRows = () => this.rows.find((tr) => tr.id === id || tr.querySelector(`[id="${CSS.escape(id)}"]`));
      const row = id && (target ? (target.closest('tr') || (target.tagName === 'FORM' && byForm()))
                                : (inRows() || byForm()));
      if (!row || !this.rows.includes(row) || !this.reveal(row)) return;
      window.BioSheets.ready(row).then((tr) => {
        if (!tr) return;
        requestAnimationFrame(() => tr.scrollIntoView({ block: 'center' }));
        tr.dispatchEvent(new CustomEvent('dt:revealed', { bubbles: true }));
      });
    }

    /* The page scrolls, not the table: the toolbar and the bottom bar stick
       to the window's edges (tailwind.css). Their heights go to what keeps
       clear of them, as --dt-toolbar-h on the header row and --dt-bottom-h
       on the selection bar: set on the card, a change restyled every row of
       the sheet. A table no wider than its card scrolls with the page
       (.is-fit), which is what lets its header row stay in view; a wider one
       scrolls sideways in its card. */
    _wireStickyParts() {
      const toolbar = this.card.querySelector(':scope > .dt-toolbar');
      const bottom = this.card.querySelector(':scope > .dt-bottom-bar');
      const scroller = this.table && this.table.closest('.dt-scroll');
      const bars = Array.from(this.card.querySelectorAll(':scope > .selbar'));
      const setVar = (el, name, value) => { if (el.style.getPropertyValue(name) !== value) el.style.setProperty(name, value); };
      const measure = () => {
        const toolbarH = toolbar ? `${toolbar.offsetHeight}px` : '';
        const bottomH = bottom ? `${bottom.offsetHeight}px` : '';
        if (toolbar && this.table && this.table.tHead) setVar(this.table.tHead, '--dt-toolbar-h', toolbarH);
        if (bottom) bars.forEach((bar) => setVar(bar, '--dt-bottom-h', bottomH));
        if (scroller) {
          // Measured as a scroller, so the answer does not depend on itself.
          scroller.classList.remove('is-fit');
          scroller.classList.toggle('is-fit', this.table.offsetWidth <= scroller.clientWidth + 1);
        }
      };
      // A table wider than its card scrolls sideways in it, so CSS cannot
      // pin its header row to the window: the header is moved down instead,
      // to sit under the toolbar while the rows go by.
      const head = this.table && this.table.tHead;
      const page = this.card.closest('.shell-scroll') || window;
      let shift = 0;
      const follow = () => {
        if (!head || !scroller) return;
        // The header row's own box: moving its cells does not move it.
        const by = scroller.classList.contains('is-fit') ? 0 : Math.max(0, Math.min(
          // Under the toolbar, or the top of the page where it doesn't stick (a phone).
          Math.max(toolbar ? toolbar.getBoundingClientRect().bottom : 0, page === window ? 0 : page.getBoundingClientRect().top)
            - head.getBoundingClientRect().top,
          this.table.offsetHeight - head.offsetHeight));
        if (by === shift) return;
        shift = by;
        head.querySelectorAll('th').forEach((th) => { th.style.transform = by ? `translateY(${by}px)` : ''; });
      };
      let ticking = false;
      page.addEventListener('scroll', () => {
        if (ticking) return;
        ticking = true;
        requestAnimationFrame(() => { ticking = false; follow(); });
      }, { passive: true });
      this._measureSticky = () => { measure(); follow(); };
      measure();
      if (typeof ResizeObserver === 'function') {
        let queued = false;
        const observer = new ResizeObserver(() => {
          if (queued) return;
          queued = true;
          requestAnimationFrame(() => { queued = false; this._measureSticky(); });
        });
        [toolbar, bottom, scroller, this.table].forEach((el) => el && observer.observe(el));
      } else {
        window.addEventListener('resize', this._measureSticky);
      }
    }

    /* The bottom bar's New button (_sheet.html quick_add) makes an empty
       record and the page reloads. The rows there were are noted first, so
       the new one is known when the page comes back: it is shown last,
       its page turned to, and its first cell is ready to type in. */
    _wireQuickAdd() {
      const key = 'dt:quick-add';
      this.card.querySelectorAll('form[data-quick-add]').forEach((form) => {
        form.addEventListener('submit', () => {
          const ids = this.original.map((tr) => tr.dataset.id);
          try { sessionStorage.setItem(key, JSON.stringify({ table: this.id, ids, at: Date.now() })); } catch (_) {}
        });
      });
      let note = null;
      try { note = JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (_) {}
      if (!note || note.table !== this.id) return;
      try { sessionStorage.removeItem(key); } catch (_) {}
      if (Date.now() - (note.at || 0) > 60000) return;
      const before = new Set(note.ids || []);
      const fresh = this.original.filter((tr) => !before.has(tr.dataset.id));
      if (!fresh.length) return;
      const last = (list) => list.filter((tr) => !fresh.includes(tr)).concat(fresh.filter((tr) => list.includes(tr)));
      this.original = last(this.original);
      this.filtered = last(this.filtered);
      this.fresh = fresh;
    }

    _showFresh() {
      const tr = this.fresh && this.fresh[this.fresh.length - 1];
      if (!tr || !this.reveal(tr)) return false;
      // The search, chips or a sort may have put it elsewhere: last again.
      this.filtered = this.filtered.filter((r) => r !== tr).concat([tr]);
      const size = this.pageSize ? parseInt(this.pageSize.value, 10) : 50;
      this.page = Math.floor((this.filtered.length - 1) / size);
      this.render();
      tr.classList.add('is-fresh');
      const first = Array.from(tr.querySelectorAll('td:not(.sheet-pin-0) :is(input, select, textarea)'))
        .find((el) => el.type !== 'checkbox' && el.type !== 'hidden' && !el.disabled && !el.readOnly
          && el.offsetParent !== null && !el.classList.contains('cage-number'));
      requestAnimationFrame(() => {
        tr.scrollIntoView({ block: 'center' });
        if (first) first.focus({ preventScroll: true });
      });
      setTimeout(() => tr.classList.remove('is-fresh'), 2400);
      return true;
    }

    _wireHeaderSort() {
      if (!this.table) return;
      this.table.querySelectorAll('th.dt-sortable').forEach((th) => {
        th.addEventListener('click', (event) => {
          // Ignore clicks that started on the resize handle.
          if (event.target.classList && event.target.classList.contains('dt-col-resize')) return;
          const key = th.dataset.sortKey;
          if (!key) return;
          if (this.sortKey === key) this.sortDir = -this.sortDir;
          else { this.sortKey = key; this.sortDir = 1; }
          // Kept for this sheet, like its columns: after a reload the rows are
          // still in the order a pasted list was lined up against.
          try { localStorage.setItem(`dt:${this.id}:sort`, JSON.stringify([key, this.sortDir])); } catch (_) {}
          this.table.querySelectorAll('th.dt-sortable').forEach((other) => {
            other.classList.remove('dt-sort-asc', 'dt-sort-desc');
          });
          th.classList.add(this.sortDir === 1 ? 'dt-sort-asc' : 'dt-sort-desc');
          const sortBtn = this.card.querySelector('.dt-btn-sort');
          if (sortBtn) sortBtn.classList.add('is-active');
          this._applySort();
          this.render();
        });
      });
    }

    _wireSearch() {
      if (!this.search) return;
      this.search.addEventListener('input', () => {
        this.query = (this.search.value || '').trim().toLowerCase();
        this._refilter();
      });
      // Each row's text is worked out once while someone types a search,
      // not again for every row on every key; anything that changes a row
      // (an edit, a save, a move) starts afresh.
      this._texts = new WeakMap();
      const forget = (event) => { if (event.target !== this.search) this._texts = new WeakMap(); };
      this.search.addEventListener('focus', forget);
      ['input', 'change'].forEach((type) => this.card.addEventListener(type, forget, true));
      ['sheet:saved', 'rack-grid:moved'].forEach((type) => document.addEventListener(type, forget));
    }

    /* A row's searchable text: what it shows, its data-* attributes, and
       the current value of any cell you can edit (an input's value is not
       part of textContent, so an editable sheet would otherwise be
       unsearchable). */
    _rowText(tr) {
      if (tr._stub) return tr._stub.s;
      const known = this._texts && this._texts.get(tr);
      if (known !== undefined) return known;
      // Not every choice of a menu, only the one chosen (below): a row's Box
      // or Status menu lists them all, so any box name matched every row.
      let blob = '';
      if (tr.querySelector('select, datalist')) {
        const walk = document.createTreeWalker(tr, NodeFilter.SHOW_TEXT);
        for (let n = walk.nextNode(); n; n = walk.nextNode()) {
          if (n.parentNode.nodeName !== 'OPTION') blob += n.nodeValue;
        }
      } else {
        blob = tr.textContent || '';
      }
      blob = blob.toLowerCase();
      for (const key in tr.dataset) blob += ' ' + (tr.dataset[key] || '').toLowerCase();
      tr.querySelectorAll('input:not([type=checkbox]):not([type=hidden]), select, textarea').forEach((el) => {
        const shown = el.tagName === 'SELECT' && el.selectedOptions[0] ? el.selectedOptions[0].textContent : '';
        blob += ' ' + String(el.value || '').toLowerCase() + ' ' + shown.toLowerCase();
      });
      if (this._texts) this._texts.set(tr, blob);
      return blob;
    }

    _refilter() {
      const q = this.query;
      const quick = this.quick;
      this.filtered = this.original.filter((tr) => {
        if (quick && quick.some((c) => (tr.dataset[c.attr] || '') !== c.value)) return false;
        return !q || this._rowText(tr).includes(q);
      });
      if (this.sortKey) this._applySort();
      this.page = 0;
      this.render();
    }

    /* Quick filter chips: <button class="dt-chip" data-dt-filter="active:true">.
       An empty data-dt-filter means "everything". The choice is remembered.
       data-dt-also="active:true" adds a condition the row must meet as
       well (the cage sheet's purpose chips show only cages with living mice). */
    _wireChips() {
      const chips = Array.from(this.card.querySelectorAll('.dt-chip[data-dt-filter]'));
      if (!chips.length) return;
      const apply = (chip, save) => {
        chips.forEach((c) => c.setAttribute('aria-pressed', c === chip ? 'true' : 'false'));
        const spec = chip.dataset.dtFilter || '';
        const parse = (s) => {
          const at = (s || '').indexOf(':');
          return at > 0 ? { attr: s.slice(0, at), value: s.slice(at + 1) } : null;
        };
        const conditions = [parse(spec), parse(chip.dataset.dtAlso)].filter(Boolean);
        this.quick = conditions.length ? conditions : null;
        if (save) {
          try { localStorage.setItem(`dt:${this.id}:filter`, spec); } catch (_) {}
          // And in the address, so a bookmark or a link keeps it (?chip=…).
          try {
            const url = new URL(window.location.href);
            if (spec) url.searchParams.set('chip', spec); else url.searchParams.delete('chip');
            window.history.replaceState(window.history.state, '', url);
          } catch (_) { /* an address it can't change */ }
        }
        this._refilter();
      };
      chips.forEach((chip) => chip.addEventListener('click', () => apply(chip, true)));
      let saved = null;
      try { saved = localStorage.getItem(`dt:${this.id}:filter`); } catch (_) {}
      // The address first (?chip=…, or ?scope=mine for the Mine chip), then
      // this browser's last choice.
      let asked = null;
      try {
        const params = new URL(window.location.href).searchParams;
        asked = params.get('chip');
        if (asked === null && params.get('scope') === 'mine') {
          const mineWords = ['mine', t('Mine').toLowerCase()];
          const mine = chips.find((c) => mineWords.some((w) => c.textContent.trim().toLowerCase().startsWith(w)));
          if (mine) asked = mine.dataset.dtFilter || '';
        }
      } catch (_) { /* no URL API */ }
      const initial = (asked !== null && chips.find((c) => (c.dataset.dtFilter || '') === asked))
        || chips.find((c) => (c.dataset.dtFilter || '') === saved)
        || chips.find((c) => c.getAttribute('aria-pressed') === 'true');
      if (initial) apply(initial, false);
    }

    /* The value a row sorts by: an editable cell of that name if the row
       has one (so a sort reflects edits made since the page loaded),
       otherwise the data-* attribute. */
    /* A cell can also carry its own sort value, for columns rendered as
       text rather than inputs (e.g. a database's custom fields):
       <td data-sort-for="attr_size" data-sort-value="12.5">. */
    _sortValue(tr, key) {
      if (tr._stub) return key in tr._stub.k ? tr._stub.k[key] : (tr.dataset[key] || '');
      const cell = tr.querySelector(`[name="${key}"]`);
      if (cell) return String(cell.value || '');
      const valued = tr.querySelector(`[data-sort-for="${key}"]`);
      if (valued) return valued.dataset.sortValue || '';
      return tr.dataset[key] || '';
    }

    /* th[data-sort-type]: "number" compares numerically, "date" and
       "text" as text (ISO dates sort correctly that way). Without it the
       column is guessed: numbers numerically, anything else as text. */
    _applySort() {
      const key = this.sortKey;
      const dir = this.sortDir;
      const th = this.table && Array.from(this.table.querySelectorAll('th.dt-sortable'))
        .find((h) => h.dataset.sortKey === key);
      const type = (th && th.dataset.sortType) || '';
      const isoDate = /^\d{4}-\d{2}-\d{2}/;
      this.filtered.sort((a, b) => {
        const av = this._sortValue(a, key);
        const bv = this._sortValue(b, key);
        // Empty cells sink to the bottom whichever way the sort runs.
        if (!av.trim() !== !bv.trim()) return av.trim() ? -1 : 1;
        if (type === 'date' || type === 'text' || (isoDate.test(av) && isoDate.test(bv))) {
          return av.localeCompare(bv, undefined, { numeric: type === 'text' }) * dir;
        }
        const aNum = parseFloat(av), bNum = parseFloat(bv);
        if (!isNaN(aNum) && !isNaN(bNum) && av.trim() && bv.trim()) return (aNum - bNum) * dir;
        // A number column with a non-number in it: numbers first.
        if (type === 'number' && isNaN(aNum) !== isNaN(bNum)) return isNaN(aNum) ? 1 : -1;
        return av.localeCompare(bv) * dir;
      });
    }

    _wireSelection() {
      if (this.selectAll) {
        this.selectAll.addEventListener('change', () => {
          this.tbody.querySelectorAll('.dt-row-check').forEach((cb) => {
            const tr = cb.closest('tr');
            if (tr && tr.style.display !== 'none') {
              cb.checked = this.selectAll.checked;
              tr.classList.toggle('is-selected', this.selectAll.checked);
            }
          });
        });
      }
      if (this.tbody) {
        this.tbody.addEventListener('change', (event) => {
          if (event.target.classList && event.target.classList.contains('dt-row-check')) {
            const tr = event.target.closest('tr');
            tr && tr.classList.toggle('is-selected', event.target.checked);
          }
        });
      }
    }

    _wirePagination() {
      if (this.pageSize) this.pageSize.addEventListener('change', () => { this.page = 0; this.render(); });
      if (this.prev) this.prev.addEventListener('click', () => { if (this.page > 0) { this.page--; this.render(); } });
      if (this.next) this.next.addEventListener('click', () => { this.page++; this.render(); });
    }

    /* A small popover menu anchored under a toolbar button. Fixed
       positioning, because the card clips overflow. */
    _openMenu(anchor, build) {
      closeMenus();
      const menu = document.createElement('div');
      menu.className = 'dt-menu';
      menu.setAttribute('role', 'menu');
      build(menu);
      document.body.appendChild(menu);
      const rect = anchor.getBoundingClientRect();
      const width = menu.offsetWidth;
      const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
      menu.style.left = `${left}px`;
      menu.style.top = `${rect.bottom + 6}px`;
      menu.style.maxHeight = `${Math.max(160, window.innerHeight - rect.bottom - 24)}px`;
      anchor.setAttribute('aria-expanded', 'true');
      menu._anchor = anchor;
      openMenu = menu;
      return menu;
    }

    _columnLabel(th) {
      // A header shortened to fit (Pos) names its column in full here, for
      // the Columns and sort menus and a phone's cards.
      if (th.dataset.dtLabel) return th.dataset.dtLabel;
      const head = th.querySelector('.dt-col-head');
      return (head ? head.textContent : th.textContent || '').replace(/\s+/g, ' ').trim();
    }

    _wireHideButton() {
      const btn = this.card.querySelector('.dt-btn-hide');
      if (!btn || !this.table) return;
      btn.setAttribute('aria-haspopup', 'menu');
      // Pressed only when the columns differ from the page's defaults, so
      // a table that simply starts with empty columns tucked away is calm.
      const defaults = () => this.headers
        .map((th, col) => (th.dataset.defaultHidden === '1' ? col : -1)).filter((i) => i >= 0);
      const sync = () => {
        const d = defaults();
        const same = d.length === this.hidden.size && d.every((i) => this.hidden.has(i));
        btn.classList.toggle('is-active', !same || !this._orderIsDefault());
      };
      this._syncColumnsButton = sync;
      /* The menu lists the columns in the order the sheet shows them. Each
         that can move has ‹ › beside it (Move left / Move right), the way
         to move one without dragging its header. */
      const fill = (menu, focus) => {
        menu.replaceChildren();
        const title = document.createElement('div');
        title.className = 'dt-menu-title';
        title.textContent = t('Columns');
        menu.appendChild(title);
        const shown = this.order.filter((c) => this.headers[c].dataset.dtOff !== '1');
        Array.from(this.table.tHead.rows[0].cells).forEach((th) => {
          const col = this._col(th);
          const label = this._columnLabel(th);
          if (!label || th.dataset.dtOff === '1') return;   // checkbox and action columns, and one turned off
          const line = document.createElement('div');
          line.className = 'dt-menu-col';
          const row = document.createElement('label');
          row.className = 'dt-menu-item';
          const box = document.createElement('input');
          box.type = 'checkbox';
          box.checked = !this.hidden.has(col);
          box.addEventListener('change', () => {
            if (box.checked) this.hidden.delete(col); else this.hidden.add(col);
            this._saveHiddenCols();
            this._applyHidden();
            sync();
          });
          row.append(box, document.createTextNode(label));
          line.appendChild(row);
          if (this.movable.has(col)) {
            const at = shown.indexOf(col);
            [[-1, 'chevron-left', t('Move left'), t('Move %(name)s left', { name: label })],
             [1, 'chevron-right', t('Move right'), t('Move %(name)s right', { name: label })]].forEach(([step, glyph, tip, said]) => {
              const move = document.createElement('button');
              move.type = 'button';
              move.className = 'dt-menu-move';
              move.title = tip;
              move.setAttribute('aria-label', said);
              move.dataset.col = col;
              move.dataset.step = step;
              move.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${glyph}"></use></svg>`;
              move.disabled = at < 0 || !shown[at + step];
              move.addEventListener('click', (event) => {
                // The menu is drawn again, so this button leaves the page: kept
                // from the document's click, which would take it for outside.
                event.stopPropagation();
                const order = this.order.slice();
                const from = order.indexOf(col);
                const to = order.indexOf(shown[at + step]);
                if (from < 0 || to < 0) return;
                order.splice(from, 1);
                order.splice(to, 0, col);
                this._setOrder(order);
                fill(menu, { col, step });
              });
              line.appendChild(move);
            });
          } else {
            line.appendChild(document.createElement('span')).className = 'dt-menu-move-gap';
          }
          menu.appendChild(line);
        });
        const all = document.createElement('button');
        all.type = 'button';
        all.className = 'dt-menu-action';
        all.textContent = t('Show all columns');
        all.addEventListener('click', () => {
          this.hidden.clear();
          this._saveHiddenCols();
          this._applyHidden();
          sync();
          closeMenus();
        });
        menu.appendChild(all);
        if (!this._orderIsDefault()) {
          const reset = document.createElement('button');
          reset.type = 'button';
          reset.className = 'dt-menu-action';
          reset.textContent = t('Reset column order');
          reset.addEventListener('click', () => {
            this._setOrder(this._defaultOrder());
            closeMenus();
          });
          menu.appendChild(reset);
        }
        if (focus) {
          const same = menu.querySelector(`.dt-menu-move[data-col="${focus.col}"][data-step="${focus.step}"]`);
          const other = menu.querySelector(`.dt-menu-move[data-col="${focus.col}"]:not([data-step="${focus.step}"])`);
          (same && !same.disabled ? same : other || same)?.focus();
        }
      };
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        if (openMenu && openMenu._anchor === btn) { closeMenus(); return; }
        this._openMenu(btn, (menu) => fill(menu));
      });
      sync();
    }

    // -------- column order -----------------------------------------------
    /* Every cell is marked with its column's number as the page draws it
       (data-dt-col), so hidden columns and widths, which are saved by that
       number, still find their column wherever it has been moved to. */
    _indexColumns() {
      this.headers = [];
      this.colKeys = [];
      this.movable = new Set();
      if (!this.table || !this.table.tHead || !this.table.tHead.rows[0]) return;
      this.headers = Array.from(this.table.tHead.rows[0].cells);
      Array.from(this.table.tHead.rows).forEach((row) => this._markRow(row));
      this.rows.forEach((row) => this._markRow(row));
      const seen = new Map();
      this.colKeys = this.headers.map((th, col) => {
        const base = th.dataset.colKey || th.dataset.sortKey || this._columnLabel(th) || `#${col}`;
        const times = (seen.get(base) || 0) + 1;
        seen.set(base, times);
        return times > 1 ? `${base}~${times}` : base;
      });
      this.headers.forEach((th, col) => { if (!this._isFixedColumn(th)) this.movable.add(col); });
    }

    _markRow(row) {
      if (row.cells.length !== this.headers.length) return;
      Array.from(row.cells).forEach((cell, col) => { cell.dataset.dtCol = String(col); });
    }

    _col(cell) {
      const col = cell && cell.dataset.dtCol;
      return col === undefined ? -1 : Number(col);
    }

    _isFixedColumn(th) {
      if (th.classList.contains('sheet-pin') || th.hasAttribute('data-dt-fixed')) return true;
      if (th.querySelector('input[type=checkbox]')) return true;
      const named = th.cloneNode(true);
      named.querySelectorAll('.sr-only, .dt-col-resize').forEach((el) => el.remove());
      return !named.textContent.trim();
    }

    _defaultOrder() {
      return this.headers.map((th, col) => col).filter((col) => this.movable.has(col));
    }

    _orderIsDefault() {
      return this._defaultOrder().every((col, i) => this.order[i] === col);
    }

    /* The saved order, by column key. A column the saved order doesn't
       know (one added since) goes in after the column it follows on the
       page; one that has gone is dropped. */
    _loadOrder() {
      const def = this._defaultOrder();
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(`dt:${this.id}:order`) || 'null'); } catch (_) {}
      if (!Array.isArray(saved)) return def;
      const byKey = new Map(def.map((col) => [this.colKeys[col], col]));
      const order = [];
      saved.forEach((key) => {
        const col = byKey.get(key);
        if (col !== undefined && !order.includes(col)) order.push(col);
      });
      def.forEach((col, i) => {
        if (!order.includes(col)) order.splice(i ? order.indexOf(def[i - 1]) + 1 : 0, 0, col);
      });
      return order;
    }

    _setOrder(order) {
      this.order = order;
      try {
        if (this._orderIsDefault()) localStorage.removeItem(`dt:${this.id}:order`);
        else localStorage.setItem(`dt:${this.id}:order`, JSON.stringify(order.map((col) => this.colKeys[col])));
      } catch (_) {}
      this._applyOrder();
      if (this._syncColumnsButton) this._syncColumnsButton();
      if (this._measureSticky) this._measureSticky();
    }

    /* Moves the cells themselves, header and rows alike, so whatever reads
       a row across (pasting a block, the export, a phone's cards) reads it
       in the order shown. The fixed columns keep their places. */
    _applyOrder(rows) {
      if (!this.headers.length) return;
      let next = 0;
      const sequence = this.headers.map((th, col) => (this.movable.has(col) ? this.order[next++] : col));
      const place = (row) => {
        const cells = row.cells;
        if (cells.length !== sequence.length || sequence.every((col, i) => this._col(cells[i]) === col)) return;
        const byCol = [];
        Array.from(cells).forEach((cell) => { byCol[this._col(cell)] = cell; });
        if (sequence.some((col) => !byCol[col])) return;
        sequence.forEach((col) => row.appendChild(byCol[col]));
      };
      if (!rows) Array.from(this.table.tHead.rows).forEach(place);
      (rows || this.rows).forEach(place);
    }

    /* Drag a column's header along the header row to move the column. A
       line shows where it will land; it can't land among the fixed columns.
       A press that doesn't move is still a click, which sorts. */
    _wireColumnDrag() {
      if (!this.table || !this.table.tHead || !this.movable.size) return;
      const headRow = this.table.tHead.rows[0];
      const scroller = this.table.closest('.dt-scroll');
      headRow.addEventListener('mousedown', (event) => {
        if (event.button !== 0) return;
        const th = event.target.closest('th');
        if (!th || th.parentElement !== headRow || !this.movable.has(this._col(th))) return;
        if (event.target.closest('.dt-col-resize, input, select, textarea, button, a')) return;
        event.preventDefault();   // no text selection while dragging
        const col = this._col(th);
        const startX = event.clientX;
        let dragging = false;
        let target = null;
        let lastX = startX;
        let lastY = event.clientY;
        let line = null;
        let ghost = null;
        let frame = 0;

        const visibleMovable = () => Array.from(headRow.cells)
          .filter((h) => this.movable.has(this._col(h)) && h.style.display !== 'none');
        const plan = (x) => {
          const heads = visibleMovable();
          if (!heads.length) return null;
          const before = heads.find((h) => { const r = h.getBoundingClientRect(); return x < r.left + r.width / 2; });
          const last = heads[heads.length - 1];
          const order = this.order.filter((c) => c !== col);
          const at = before ? order.indexOf(this._col(before)) : order.indexOf(this._col(last)) + 1;
          if (before === th || (!before && last === th) || at < 0) return { same: true };
          order.splice(at, 0, col);
          const edge = before ? before.getBoundingClientRect().left : last.getBoundingClientRect().right;
          return { order, edge, same: order.every((c, i) => c === this.order[i]) };
        };
        const draw = () => {
          target = plan(lastX);
          if (ghost) { ghost.style.left = `${lastX + 12}px`; ghost.style.top = `${lastY + 12}px`; }
          if (!target || target.same) { line.hidden = true; return; }
          const box = this.table.getBoundingClientRect();
          const clip = scroller ? scroller.getBoundingClientRect() : box;
          const head = th.getBoundingClientRect();
          line.hidden = false;
          line.style.left = `${Math.max(clip.left, Math.min(clip.right, target.edge)) - 1}px`;
          line.style.top = `${Math.max(0, head.top)}px`;
          line.style.height = `${Math.max(head.height, Math.min(window.innerHeight, box.bottom) - Math.max(0, head.top))}px`;
        };
        // Near the edge of a sheet wider than its card, it scrolls sideways
        // (inside the pinned columns, which don't move).
        const pinned = (side) => Array.from(headRow.cells)
          .filter((h) => h.classList.contains('sheet-pin') && h.style.display !== 'none'
            && h.classList.contains('sheet-pin-end') === (side === 'right')
            && getComputedStyle(h).position === 'sticky')
          .reduce((w, h) => w + h.offsetWidth, 0);
        let leftPinned = 0;
        let rightPinned = 0;
        const roll = () => {
          frame = 0;
          if (!dragging || !scroller || scroller.scrollWidth <= scroller.clientWidth) return;
          const r = scroller.getBoundingClientRect();
          const from = r.left + leftPinned + 40;
          const to = r.right - rightPinned - 40;
          const dx = lastX < from ? -Math.ceil((from - lastX) / 3) : (lastX > to ? Math.ceil((lastX - to) / 3) : 0);
          if (!dx) return;
          const before = scroller.scrollLeft;
          scroller.scrollLeft += dx;
          if (scroller.scrollLeft === before) return;
          draw();
          frame = requestAnimationFrame(roll);
        };
        const begin = () => {
          dragging = true;
          leftPinned = pinned('left');
          rightPinned = pinned('right');
          document.body.classList.add('dt-col-moving');
          th.classList.add('is-col-moving');
          line = document.body.appendChild(document.createElement('div'));
          line.className = 'dt-col-drop';
          line.hidden = true;
          ghost = document.body.appendChild(document.createElement('div'));
          ghost.className = 'dt-col-ghost';
          ghost.textContent = this._columnLabel(th);
        };
        const finish = (drop) => {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          document.removeEventListener('keydown', onKey, true);
          if (!dragging) return;
          dragging = false;
          if (frame) cancelAnimationFrame(frame);
          document.body.classList.remove('dt-col-moving');
          th.classList.remove('is-col-moving');
          line.remove();
          ghost.remove();
          // The click that ends a drag is not a sort.
          const swallow = (e) => { e.stopPropagation(); e.preventDefault(); };
          window.addEventListener('click', swallow, true);
          setTimeout(() => window.removeEventListener('click', swallow, true), 0);
          if (drop && target && !target.same) this._setOrder(target.order);
        };
        const onMove = (ev) => {
          lastX = ev.clientX;
          lastY = ev.clientY;
          if (!dragging) {
            if (Math.abs(lastX - startX) < 6) return;
            begin();
          }
          ev.preventDefault();
          draw();
          if (!frame) frame = requestAnimationFrame(roll);
        };
        const onUp = () => finish(true);
        const onKey = (ev) => {
          if (ev.key !== 'Escape' || !dragging) return;
          ev.stopPropagation();
          finish(false);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('keydown', onKey, true);
      });
    }

    _wireSortButton() {
      const btn = this.card.querySelector('.dt-btn-sort');
      if (!btn || !this.table) return;
      btn.setAttribute('aria-haspopup', 'menu');
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        if (openMenu && openMenu._anchor === btn) { closeMenus(); return; }
        const headers = Array.from(this.table.querySelectorAll('th.dt-sortable'))
          .filter((th) => th.style.display !== 'none');
        if (!headers.length) return;
        this._openMenu(btn, (menu) => {
          const title = document.createElement('div');
          title.className = 'dt-menu-title';
          title.textContent = t('Sort by');
          menu.appendChild(title);
          headers.forEach((th) => {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'dt-menu-item';
            const active = this.sortKey === th.dataset.sortKey;
            item.classList.toggle('is-active', active);
            const arrow = active ? (this.sortDir === 1 ? ' ↑' : ' ↓') : '';
            item.textContent = this._columnLabel(th) + arrow;
            item.addEventListener('click', () => { th.click(); closeMenus(); });
            menu.appendChild(item);
          });
          if (this.sortKey) {
            const clear = document.createElement('button');
            clear.type = 'button';
            clear.className = 'dt-menu-action';
            clear.textContent = t('Original order');
            clear.addEventListener('click', () => {
              this.sortKey = null;
              try { localStorage.removeItem(`dt:${this.id}:sort`); } catch (_) {}
              this.table.querySelectorAll('th.dt-sortable').forEach((o) => o.classList.remove('dt-sort-asc', 'dt-sort-desc'));
              btn.classList.remove('is-active');
              this._refilter();
              closeMenus();
            });
            menu.appendChild(clear);
          }
        });
      });
    }

    /* Each cell carries its column's name, which a phone shows beside the
       value when the sheet turns into one card per row (tailwind.css,
       "Sheets on a phone"): there is no header row to look up to. */
    _labelCells(rows) {
      if (!this.table || !this.table.tHead || !this.table.tHead.rows[0]) return;
      const labels = this.headers.map((th) => this._columnLabel(th));
      (rows || this.rows).forEach((tr) => {
        Array.from(tr.cells).forEach((td, idx) => {
          const col = this._col(td) >= 0 ? this._col(td) : idx;
          if (labels[col] && !td.dataset.label) td.dataset.label = labels[col];
        });
      });
    }

    /* A column whose header the page marks data-dt-off="1" stays hidden
       whatever the Columns menu says, and is left out of that menu and of
       the export: the page has decided it (Samples' Custom tag, when this
       person hides it on the mouse sheet). It is not saved, so the column
       numbers the menu saves stay those of the columns as drawn.
       `col` is that number (a cell's data-dt-col), wherever the column now is. */
    _isHidden(col) {
      const th = this.headers[col];
      return this.hidden.has(col) || Boolean(th && th.dataset.dtOff === '1');
    }

    _applyHidden(rows) {
      if (!this.table) return;
      const apply = (cell, idx) => {
        const col = this._col(cell);
        const display = this._isHidden(col >= 0 ? col : idx) ? 'none' : '';
        if (cell.style.display !== display) cell.style.display = display;   // a long sheet has 100,000 cells
      };
      if (!rows) Array.from(this.table.tHead.rows).forEach((row) => Array.from(row.cells).forEach(apply));
      (rows || this.rows).forEach((tr) => Array.from(tr.cells).forEach(apply));
    }

    /* Saved choice if there is one, else the columns the page marks
       data-default-hidden (e.g. transgene columns nobody has filled). */
    _loadHiddenCols() {
      try {
        const raw = localStorage.getItem(`dt:${this.id}:hidden`);
        if (raw) return new Set(JSON.parse(raw));
      } catch (_) { /* storage unavailable */ }
      const defaults = new Set();
      this.headers.forEach((th, col) => {
        if (th.dataset.defaultHidden === '1') defaults.add(col);
      });
      return defaults;
    }

    _saveHiddenCols() {
      try { localStorage.setItem(`dt:${this.id}:hidden`, JSON.stringify([...this.hidden])); } catch (_) {}
    }

    // -------- column resize ----------------------------------------------
    _wireResize() {
      if (!this.table || this.table.dataset.resizable !== '1') return;
      const headers = this.headers;
      headers.forEach((th, idx) => {
        if (idx === headers.length - 1) return; // no handle on last col
        const handle = document.createElement('div');
        handle.className = 'dt-col-resize';
        handle.dataset.colIndex = idx;
        th.appendChild(handle);
        handle.addEventListener('mousedown', (event) => this._startResize(event, th, idx));
      });
    }

    _startResize(event, th, idx) {
      event.preventDefault();
      event.stopPropagation();
      const startX = event.clientX;
      const startWidth = th.getBoundingClientRect().width;
      this.table.classList.add('is-resizing');
      const handle = event.currentTarget; // the resize handle, not pseudo
      handle.classList.add('is-dragging');
      const onMove = (ev) => {
        const delta = ev.clientX - startX;
        const newWidth = Math.max(48, startWidth + delta);
        th.style.width = `${newWidth}px`;
        th.style.minWidth = `${newWidth}px`;
      };
      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        this.table.classList.remove('is-resizing');
        handle.classList.remove('is-dragging');
        this._saveColWidth(idx, parseInt(th.style.width || '', 10));
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    }

    _saveColWidth(idx, width) {
      if (!width || isNaN(width)) return;
      try {
        const raw = localStorage.getItem(`dt:${this.id}:widths`) || '{}';
        const map = JSON.parse(raw);
        map[idx] = width;
        localStorage.setItem(`dt:${this.id}:widths`, JSON.stringify(map));
      } catch (_) {}
    }

    _applyResizedWidths() {
      if (!this.table || this.table.dataset.resizable !== '1') return;
      try {
        const raw = localStorage.getItem(`dt:${this.id}:widths`);
        if (!raw) return;
        const map = JSON.parse(raw);
        const headers = this.headers;
        Object.keys(map).forEach((idxStr) => {
          const idx = parseInt(idxStr, 10);
          const width = map[idxStr];
          if (headers[idx] && width) {
            headers[idx].style.width = `${width}px`;
            headers[idx].style.minWidth = `${width}px`;
          }
        });
      } catch (_) {}
    }

    _renderNoMatch(show) {
      if (!this.tbody) return;
      let row = this.tbody.querySelector('tr.dt-nomatch');
      if (!show) { if (row) row.remove(); return; }
      if (!row) {
        row = document.createElement('tr');
        row.className = 'dt-nomatch';
        const cell = document.createElement('td');
        cell.className = 'dt-empty';
        cell.colSpan = this.table.tHead.rows[0].cells.length;
        cell.appendChild(document.createElement('span')).className = 'dt-empty-msg';
        row.appendChild(cell);
        this.tbody.appendChild(row);
      }
      row.firstChild.firstChild.textContent = this.query
        ? t('Nothing matches “%(query)s”.', { query: this.search.value.trim() })
        : t('No %(nouns)s match this filter.', { nouns: t(this.nounPlural) });
    }

    // -------- export & print ---------------------------------------------
    /* What a cell shows: an editable cell's value (a select's chosen
       label), otherwise its text. */
    _cellText(td) {
      const text = td.querySelector(':scope > .cell-text');   // a test: static/text-cells.js
      if (text) return text.classList.contains('is-placeholder') ? '' : text.textContent.trim();
      const field = td.querySelector('select, textarea, input:not([type=checkbox]):not([type=hidden]):not([type=radio])');
      if (field) {
        if (field.tagName === 'SELECT') return field.selectedOptions[0] ? field.selectedOptions[0].textContent.trim() : '';
        return String(field.value || '').trim();
      }
      return (td.textContent || '').replace(/\s+/g, ' ').trim();
    }

    /* Columns worth exporting: visible, and not the tick-box or actions
       column, in the order shown (idx is the cell's place in its row). */
    _exportColumns() {
      const headers = Array.from(this.table.tHead.rows[0].cells);
      return headers.map((th, idx) => ({ th, idx })).filter(({ th, idx }) => {
        if (this._isHidden(this._col(th) >= 0 ? this._col(th) : idx)) return false;
        if (th.querySelector('input[type=checkbox]')) return false;
        const label = (th.textContent || '').replace(/\s+/g, ' ').trim();
        return label && label !== 'Actions' && label !== t('Actions');
      }).map(({ th, idx }) => ({ idx, label: (th.textContent || '').replace(/\s+/g, ' ').trim() }));
    }

    /* Toolbar buttons .dt-btn-export (CSV of every row the filters and
       search let through, not just this page) and .dt-btn-print. */
    _wireExport() {
      const exportBtn = this.card.querySelector('.dt-btn-export');
      if (exportBtn) {
        exportBtn.addEventListener('click', () => this._whenDrawn(this.filtered, () => {
          const columns = this._exportColumns();
          const quote = (v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
          const lines = [columns.map((c) => quote(c.label)).join(',')];
          this.filtered.forEach((tr) => {
            lines.push(columns.map((c) => quote(tr.cells[c.idx] ? this._cellText(tr.cells[c.idx]) : '')).join(','));
          });
          const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
          const link = document.createElement('a');
          const stamp = new Date().toISOString().slice(0, 10);
          link.href = URL.createObjectURL(blob);
          link.download = `${this.card.dataset.exportName || this.id}-${stamp}.csv`;
          document.body.appendChild(link);
          link.click();
          link.remove();
          setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        }));
      }
      const printBtn = this.card.querySelector('.dt-btn-print');
      if (printBtn) {
        printBtn.addEventListener('click', () => this._whenDrawn(this.filtered, () => {
          // Print every filtered row, then put the page back.
          const size = this.pageSize;
          const before = size ? size.value : null;
          if (size) {
            if (!Array.from(size.options).some((o) => o.value === '100000')) size.add(new Option(t('All'), '100000'));
            size.value = '100000';
          }
          this.page = 0;
          this.render();
          document.body.classList.add('is-printing-sheet');
          this.card.classList.add('is-print-target');
          const restore = () => {
            document.body.classList.remove('is-printing-sheet');
            this.card.classList.remove('is-print-target');
            if (size && before !== null) size.value = before;
            this.render();
            window.removeEventListener('afterprint', restore);
          };
          window.addEventListener('afterprint', restore);
          window.print();
        }));
      }
    }

    // -------- rows the server pages --------------------------------------
    /* Fetches the rows of these stubs (a Promise, done when all are in):
       ROWS_AT_ONCE a request, one request after another. */
    _loadRows(list) {
      const waiting = [];
      const want = [];
      list.forEach((tr) => {
        if (!tr._stub) return;
        if (tr._loading) waiting.push(tr._loading); else want.push(tr);
      });
      let chain = Promise.resolve();
      for (let i = 0; i < want.length; i += ROWS_AT_ONCE) {
        const chunk = want.slice(i, i + ROWS_AT_ONCE);
        chain = chain.then(() => this._fetchRows(chunk));
        chunk.forEach((tr) => { tr._loading = chain; });
      }
      return Promise.all([...waiting, chain]);
    }

    _fetchRows(stubs) {
      const url = new URL(this.rowsUrl, window.location.href);
      // The page's own choices (?type=, ?ended=): the same rows, drawn the same.
      new URLSearchParams(window.location.search).forEach((value, key) => url.searchParams.set(key, value));
      url.searchParams.set('ids', stubs.map((tr) => tr.dataset.id).join(','));
      return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
        .then((answer) => this._takeRows(stubs, answer))
        .catch((error) => { stubs.forEach((tr) => { tr._loading = null; }); throw error; });
    }

    /* Each stub's place in the sheet goes to its row; one the answer leaves
       out (deleted meanwhile) leaves the sheet. */
    _takeRows(stubs, answer) {
      if (answer.forms) {
        const forms = document.createElement('template');
        forms.setAttribute('data-row-forms', '');
        forms.innerHTML = answer.forms;
        this.card.appendChild(forms);
      }
      const holder = document.createElement('template');
      holder.innerHTML = answer.rows || '';
      const drawn = new Map(Array.from(holder.content.children)
        .filter((tr) => tr.tagName === 'TR' && tr.dataset.id).map((tr) => [tr.dataset.id, tr]));
      const swap = new Map();
      const fresh = [];
      stubs.forEach((stub) => {
        const tr = drawn.get(stub.dataset.id);
        if (tr && !tr._stub) {
          document.adoptNode(tr);
          tr.style.display = 'none';
          fresh.push(tr);
        }
        swap.set(stub, tr || null);
      });
      [this.rows, this.original, this.filtered, this._laidOut].forEach((list) => {
        for (let i = list.length - 1; i >= 0; i--) {
          if (!swap.has(list[i])) continue;
          const tr = swap.get(list[i]);
          if (tr) list[i] = tr; else list.splice(i, 1);
        }
      });
      fresh.forEach((tr) => this._markRow(tr));
      this._applyOrder(fresh);
      this._labelCells(fresh);
      this._applyHidden(fresh);
      this.card.dispatchEvent(new CustomEvent('dt:rows-loaded', { bubbles: true, detail: { rows: fresh } }));
    }

    /* Runs `then` once these rows are all drawn (an export or a print of
       every row the search and chips let through). */
    _whenDrawn(rows, then) {
      if (!this.rowsUrl || !rows.some((tr) => tr._stub)) { then(); return; }
      this.card.classList.add('dt-loading');
      this._loadRows(rows).then(() => {
        this.card.classList.remove('dt-loading');
        then();
      }, () => {
        this.card.classList.remove('dt-loading');
        this._sayNotLoaded();
      });
    }

    _sayNotLoaded() {
      const say = window.BiomanagerShell && window.BiomanagerShell.toast;
      if (say) say(t('Some rows could not be loaded. Check the connection and try again.'), 'error');
    }

    // -------- render -----------------------------------------------------
    render() {
      const size = this.pageSize ? parseInt(this.pageSize.value, 10) : 50;
      const start = this.page * size;
      const end = start + size;
      // A page with stubs on it waits for their rows (a sheet the server
      // pages); the page shown until then stays.
      const page = this.filtered.slice(start, end);
      if (this.rowsUrl && page.some((tr) => tr._stub) && !this._notLoaded) {
        this.card.classList.add('dt-loading');
        this._loadRows(page).then(() => {
          this.card.classList.remove('dt-loading');
          this.render();
        }, () => {
          this.card.classList.remove('dt-loading');
          this._sayNotLoaded();
          this._notLoaded = true;   // this once: the next turn, search or sort asks again
          this.render();
        });
        return;
      }
      this._notLoaded = false;
      // Only the rows whose state changes are touched: a long sheet has
      // thousands, and the stylesheet hides those past the first page until
      // now (.dt-ready).
      const shown = new Set(this.filtered.slice(start, end));
      this.rows.forEach((tr) => {
        const display = shown.has(tr) ? '' : 'none';
        if (tr.style.display !== display) tr.style.display = display;
      });
      this.card.classList.add('dt-ready');
      const total = this.filtered.length;
      if (this.count) {
        const noun = t(total === 1 ? this.noun : this.nounPlural);
        this.count.textContent = total !== this.rows.length
          ? t('%(n)s %(noun)s of %(total)s', { n: total, noun, total: this.rows.length })
          : t('%(n)s %(noun)s', { n: total, noun });
      }
      this._renderNoMatch(total === 0 && this.rows.length > 0);
      // A detail row (<tr data-detail-for="<row's data-id>">, e.g. a cage's
      // mice) follows its row through sorting and paging, and is shown
      // only while its row is; its own `hidden` still opens and closes it.
      const details = this.details;
      // The rows are put in order only when it has changed (a sort, a
      // search, a chip) or a row to show is off the page (_parkLater):
      // moving every row on each page turn was most of it.
      const laidOut = this._laidOut;
      const away = (tr) => !tr.isConnected;
      if (this.rowsUrl || this._parked) {
        // A long sheet (one the server pages, or one whose unseen rows left
        // the page as it loaded) holds only the page shown, with its detail
        // rows: the rows of pages turned past piled up in it, hidden, and
        // every later search or sort restyled them all. A ticked row stays,
        // hidden, for the selection bar to act on.
        const keep = new Set();
        const page = this.filtered.slice(start, end);
        page.forEach((tr) => { keep.add(tr); const detail = details.get(tr.dataset.id); if (detail) keep.add(detail); });
        Array.from(this.tbody.children).forEach((tr) => {
          if ((tr.dataset.id || tr.dataset.detailFor !== undefined) && !keep.has(tr)
              && !tr.querySelector('.mouse-select:checked, [data-select-row]:checked, .dt-row-check:checked')) tr.remove();
        });
        page.forEach((tr) => {
          this.tbody.appendChild(tr);
          const detail = details.get(tr.dataset.id);
          if (detail) this.tbody.appendChild(detail);
        });
        this._laidOut = this.filtered.slice();
      } else if (laidOut.length !== this.filtered.length || this.filtered.some((tr, i) => tr !== laidOut[i])
          || this.filtered.slice(start, end).some(away)) {
        this.filtered.forEach((tr) => {
          if (away(tr) && !shown.has(tr)) return;
          this.tbody.appendChild(tr);
          const detail = details.get(tr.dataset.id);
          if (detail) this.tbody.appendChild(detail);
        });
        this._laidOut = this.filtered.slice();
      }
      const byId = new Map(this.rows.map((tr) => [tr.dataset.id, tr]));
      details.forEach((detail, id) => {
        const owner = byId.get(id);
        const display = owner && owner.style.display !== 'none' ? '' : 'none';
        if (detail.style.display !== display) detail.style.display = display;
      });
      const totalPages = Math.max(1, Math.ceil(total / size));
      if (this.pageLabel) this.pageLabel.textContent = t('Page %(page)s of %(pages)s', { page: this.page + 1, pages: totalPages });
      if (this.prev) this.prev.disabled = this.page === 0;
      if (this.next) this.next.disabled = this.page >= totalPages - 1;
    }
  }

  // ---------------------------------------------------------------------
  // Standalone resize binding. Works on any <table data-resizable="1"> that
  // isn't already managed by a DataTableController. The table id is taken
  // from the `data-resize-id` attribute (or the element id) — that's the
  // key used in localStorage for persisted widths.
  // ---------------------------------------------------------------------
  function makeTableResizable(table) {
    if (table.dataset.dtResizeBound === '1') return;
    table.dataset.dtResizeBound = '1';
    const tableId = table.dataset.resizeId || table.id || 'unnamed';
    const headers = Array.from(table.tHead && table.tHead.rows[0] ? table.tHead.rows[0].cells : []);
    if (!headers.length) return;

    // Apply persisted widths.
    try {
      const raw = localStorage.getItem(`dt:${tableId}:widths`);
      if (raw) {
        const map = JSON.parse(raw);
        Object.keys(map).forEach((idxStr) => {
          const idx = parseInt(idxStr, 10);
          if (headers[idx] && map[idxStr]) {
            headers[idx].style.width = `${map[idxStr]}px`;
            headers[idx].style.minWidth = `${map[idxStr]}px`;
          }
        });
      }
    } catch (_) {}

    headers.forEach((th, idx) => {
      if (idx === headers.length - 1) return;
      // Skip if a handle is already there (e.g. DataTableController added it).
      if (th.querySelector('.dt-col-resize')) return;
      const handle = document.createElement('div');
      handle.className = 'dt-col-resize';
      handle.dataset.colIndex = idx;
      th.appendChild(handle);
      handle.addEventListener('mousedown', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const startX = event.clientX;
        const startWidth = th.getBoundingClientRect().width;
        table.classList.add('is-resizing');
        handle.classList.add('is-dragging');
        const onMove = (ev) => {
          const delta = ev.clientX - startX;
          const newWidth = Math.max(40, startWidth + delta);
          th.style.width = `${newWidth}px`;
          th.style.minWidth = `${newWidth}px`;
        };
        const onUp = () => {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          table.classList.remove('is-resizing');
          handle.classList.remove('is-dragging');
          const width = parseInt(th.style.width || '', 10);
          if (width) {
            try {
              const raw = localStorage.getItem(`dt:${tableId}:widths`) || '{}';
              const map = JSON.parse(raw);
              map[idx] = width;
              localStorage.setItem(`dt:${tableId}:widths`, JSON.stringify(map));
            } catch (_) {}
          }
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      });
    });
  }

  function init() {
    document.querySelectorAll('.data-table-card[data-table-id]').forEach((card) => {
      // Avoid double-initializing if data-table.js is loaded twice.
      if (card.dataset.dtBound === '1') return;
      card.dataset.dtBound = '1';
      try {
        new DataTableController(card);
      } finally {
        card.classList.add('dt-ready');   // never leave rows hidden by the stylesheet
      }
    });
    // Bind resize to any standalone <table data-resizable="1"> not inside a
    // controlled .data-table-card (which already handles resize itself).
    document.querySelectorAll('table[data-resizable="1"]').forEach((table) => {
      if (table.closest('.data-table-card[data-table-id]')) return;
      makeTableResizable(table);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Expose for templates that want to re-init after dynamic DOM changes.
  window.BiomanagerDataTable = { init, makeTableResizable };
})();
