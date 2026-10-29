/* The calendar page (templates/calendar.html).

   TOAST UI Calendar draws the month, week and day grids; the list view, the
   sidebar and every dialog are ours. Items come from /calendar/events.json
   for the range on screen: events (repeats already expanded), to-dos,
   colony dates, stocks and supplies, protocol steps, bookings, time away
   and connected calendars. Each has a `kind`, which decides its layer in
   the sidebar and what a click does. */
(function () {
  'use strict';

  const DATA = JSON.parse(document.getElementById('cal-data').textContent || '{}');
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const tuiHost = $('#biocal-tui');
  const listHost = $('#biocal-list');
  // The month is the page's title, in the title row.
  const titleEl = $('#app-toolbar .toolbar-title');
  const side = $('#cal-side');

  const LAYER_KEY = 'biomanager:cal-layers';
  const VIEW_KEY = 'biomanager:cal-view';
  const LAYER_COLORS = Object.assign({
    events: '#17a38f', tasks: '#007aff', auto: '#ff9500', subs: '#34c759', done: '#8e8e93',
  }, DATA.colors || {});
  const LAYER_NAMES = {
    events: t('Event'), tasks: t('To-do'), auto: t('Colony'), stocks: t('Stocks & organisms'), supplies: t('Supplies'),
    protocols: t('Protocol'), bookings: t('Equipment booking'), away: t('Time away'), subs: t('Connected calendar'),
  };
  // Dates, months and weekdays in the page's language (app/i18n.py); the
  // browser's own when the page is in English.
  const LOCALE = window.BM_LANG === 'zh' ? 'zh-CN' : undefined;
  const weekdayNames = (style) => [0, 1, 2, 3, 4, 5, 6].map((i) => new Date(2023, 0, 1 + i).toLocaleDateString(LOCALE, { weekday: style }));
  const DAY_NAMES = LOCALE ? weekdayNames('short') : ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MINI_DAY_NAMES = LOCALE ? weekdayNames('narrow') : ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
  // Colony items say what they are in their id ("auto-cage-12-wean").
  const AUTO_ICONS = { wean: 'baby', geno: 'microscope', sac: 'age', start: 'flask', end: 'flask' };

  let calendar = null;
  let allItems = [];
  let coverReport = [];
  let protocols = { templates: [], runs: [] };
  let currentView = 'month';
  let listAnchor = startOfDay(new Date());
  let miniMonth = firstOfMonth(new Date());
  let fetchSeq = 0;

  // ================================================================ layers

  const layerInputs = $$('[data-layer-toggle]');
  (function restoreLayers() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(LAYER_KEY) || 'null'); } catch (_) { saved = null; }
    layerInputs.forEach((input) => {
      const key = input.dataset.layerToggle;
      if (saved && key in saved) input.checked = !!saved[key];
      input.closest('.cal-layer').style.setProperty('--tone', LAYER_COLORS[key] || '#8e8e93');
      input.addEventListener('change', () => {
        const state = {};
        layerInputs.forEach((i) => { state[i.dataset.layerToggle] = i.checked; });
        try { localStorage.setItem(LAYER_KEY, JSON.stringify(state)); } catch (_) {}
        renderAll();
      });
    });
  })();

  function layerOn(key) {
    const input = layerInputs.find((i) => i.dataset.layerToggle === key);
    return !input || input.checked;
  }

  function layerOf(item) {
    switch (item.kind) {
      case 'event': return 'events';
      case 'task': return 'tasks';
      case 'auto': return 'auto';
      case 'agenda': return item.calendarId === 'supplies' ? 'supplies' : 'stocks';
      case 'protocol': return 'protocols';
      case 'booking': return 'bookings';
      case 'away': return 'away';
      default: return 'subs';
    }
  }

  function visible(item) {
    if (!layerOn(layerOf(item))) return false;
    if (item.kind === 'task' && item.raw && item.raw.done && !layerOn('done')) return false;
    return true;
  }

  // ================================================================ colour

  function parseColor(value) {
    const v = String(value || '').trim();
    let m = v.match(/^#([0-9a-f]{3})$/i);
    if (m) return m[1].split('').map((c) => parseInt(c + c, 16));
    m = v.match(/^#([0-9a-f]{6})/i);
    if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
    m = v.match(/rgba?\(([^)]+)\)/i);
    if (m) return m[1].split(',').slice(0, 3).map((n) => parseFloat(n));
    return [142, 142, 147];
  }
  function luminance(color) {
    const [r, g, b] = parseColor(color).map((c) => c / 255);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  // The app's own colours, as CSS: the calendar follows light and dark as
  // the page switches (and a recording of it, on the website, does too).
  const SURFACE = 'var(--color-surface, #ffffff)';
  const INK = 'var(--color-ink, #1d1d1f)';
  const NOW = 'var(--color-danger-600, #e5484d)';
  function tone(color, share, rest) { return `color-mix(in srgb, ${color} ${share}, ${rest})`; }

  /* A chip is a tint of its colour with a solid edge in the colour itself,
     and text in the page's ink, so pastel and saturated colours both read.
     How strong the tint is, light or dark, is in calendar.css (--cal-chip). */
  function paint(item) {
    const base = item.backgroundColor || LAYER_COLORS[layerOf(item)] || '#8e8e93';
    const edge = luminance(base) > 0.72 ? tone(base, 'var(--cal-chip-edge, 72%)', INK) : base;
    const fill = tone(base, item.kind === 'away' ? 'var(--cal-chip-away, 12%)' : 'var(--cal-chip, 20%)', SURFACE);
    const out = Object.assign({}, item, {
      backgroundColor: fill, dragBackgroundColor: fill, borderColor: edge, color: INK,
    });
    // The month grid draws no "task" category (it's the week view's to-do
    // strip): there a to-do is an all-day chip on the day it's due.
    if (currentView === 'month' && out.category === 'task') out.category = 'allday';
    // A repeat is changed from its dialog, not by dragging one occurrence.
    if (item.raw && item.raw.occurrence) out.isReadOnly = true;
    if (item.kind === 'away' || item.kind === 'protocol' || item.kind === 'agenda' || item.kind === 'auto') out.isReadOnly = true;
    // Someone else's to-do, or a group's you are not in: shown, not changed.
    if ((item.kind === 'task' || item.kind === 'event') && item.raw && item.raw.readOnly) out.isReadOnly = true;
    return out;
  }

  function iconFor(item) {
    const raw = item.raw || {};
    if (raw.icon) return raw.icon;
    if (item.kind === 'auto') {
      const tag = String(item.id || '').split('-').pop();
      return AUTO_ICONS[tag] || 'mouse';
    }
    if (item.kind === 'subscription' || item.kind === 'google') return 'rss';
    if (raw.repeat) return 'repeat';
    return '';
  }

  /* TOAST UI sanitises what its templates return and drops <use>, so icons
     are drawn from the sprite's own paths, read once. */
  const sprite = {};
  const spriteReady = fetch('/static/icons.svg').then((r) => (r.ok ? r.text() : '')).then((text) => {
    const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
    doc.querySelectorAll('symbol').forEach((sym) => {
      sprite[sym.id] = { box: sym.getAttribute('viewBox') || '0 0 512 512', body: sym.innerHTML };
    });
  }).catch(() => {});

  function svgIcon(name) {
    const sym = name && sprite[name];
    return sym ? `<svg class="icon" viewBox="${sym.box}" aria-hidden="true">${sym.body}</svg>` : '';
  }

  // ================================================================ TOAST UI

  function waitForTui() {
    return new Promise((resolve) => {
      const tick = () => {
        if (window.tui && window.tui.Calendar) return resolve('ready');
        if (window.__tuiMissing) return resolve('missing');
        if (window.__tuiLoaded && !(window.tui && window.tui.Calendar)) {
          return setTimeout(() => resolve(window.tui && window.tui.Calendar ? 'ready' : 'broken'), 200);
        }
        setTimeout(tick, 80);
      };
      tick();
    });
  }

  function tuiTheme() {
    const hairline = 'var(--color-hairline, #e5e5ea)';
    const line = `1px solid ${hairline}`;
    const surface = SURFACE;
    const recess = 'var(--color-recess, #f5f5f7)';
    const ink = INK;
    const muted = 'var(--color-muted, #86868b)';
    const brand = 'var(--color-brand-600, #17a38f)';
    const tint = tone(brand, '12%', surface);
    // The other month's days: quieter than this month's, still readable.
    const sand = 'var(--color-muted, #66666b)';
    return {
      common: {
        backgroundColor: surface, border: line,
        gridSelection: { backgroundColor: tint, border: `1px solid ${brand}` },
        dayName: { color: muted }, holiday: { color: muted }, saturday: { color: muted }, today: { color: '#ffffff' },
      },
      month: {
        dayName: { borderLeft: 'none', backgroundColor: surface },
        dayExceptThisMonth: { color: sand },
        holidayExceptThisMonth: { color: sand },
        weekend: { backgroundColor: tone(recess, '65%', surface) },
        moreView: { backgroundColor: surface, border: line, boxShadow: 'var(--shadow-lg, none)' },
        moreViewTitle: { backgroundColor: surface },
      },
      week: {
        dayName: { borderLeft: 'none', borderTop: 'none', borderBottom: line, backgroundColor: surface },
        dayGrid: { borderRight: line, backgroundColor: surface },
        dayGridLeft: { borderRight: line, backgroundColor: surface },
        timeGrid: { borderRight: line },
        timeGridLeft: { borderRight: line, backgroundColor: surface },
        timeGridLeftAdditionalTimezone: { backgroundColor: surface },
        timeGridHourLine: { borderBottom: line },
        timeGridHalfHourLine: { borderBottom: `1px dotted ${tone(hairline, '60%', surface)}` },
        today: { color: brand, backgroundColor: tone(brand, '5%', surface) },
        pastDay: { color: muted }, pastTime: { color: muted },
        weekend: { backgroundColor: tone(recess, '65%', surface) },
        // Now: a red line across today with a dot, as a phone's calendar draws it.
        nowIndicatorLabel: { color: NOW },
        nowIndicatorPast: { border: '1px solid transparent' },
        nowIndicatorBullet: { backgroundColor: NOW },
        nowIndicatorToday: { border: `2px solid ${NOW}` },
        panelResizer: { border: line },
        gridSelection: { color: ink },
      },
    };
  }

  function chip(ev) {
    const item = findItem(ev.id) || ev;
    // Week and day: what is due that day is a pill with a ring, as in the design.
    const lead = currentView === 'month' ? svgIcon(iconFor(item)) : '<i class="cal-ring" aria-hidden="true"></i>';
    return `<span class="cal-chip cal-chip-${item.kind || 'event'}" data-item-id="${ev.id}">${lead}<span class="cal-chip-t">${escapeHtml(ev.title || '')}</span></span>`;
  }

  // A timed item in week and day: its title, then its hours in mono.
  function timedBlock(ev) {
    const item = findItem(ev.id) || ev;
    return `<span class="cal-tev" data-item-id="${ev.id}"><b class="cal-tev-t">${escapeHtml(ev.title || '')}</b>
      <span class="cal-tev-time">${hm(new Date(item.start))} – ${hm(new Date(item.end))}</span></span>`;
  }
  function hm(d) { return Number.isNaN(d.getTime()) ? '' : `${pad(d.getHours())}:${pad(d.getMinutes())}`; }

  waitForTui().then((status) => {
    if (status !== 'ready') {
      tuiHost.innerHTML = `<div class="cal-empty">${t("The calendar didn't load (%(status)s). Run <code>npm run build:calendar</code> and reload.", { status })}</div>`;
      return;
    }
    calendar = new window.tui.Calendar('#biocal-tui', {
      defaultView: 'month',
      usageStatistics: false,
      theme: tuiTheme(),
      useFormPopup: false,
      useDetailPopup: false,
      isReadOnly: false,
      month: { visibleEventCount: 6, dayNames: DAY_NAMES },
      week: { taskView: false, eventView: ['allday', 'time'], hourStart: 0, hourEnd: 24, dayNames: DAY_NAMES },
      template: {
        allday: chip,
        time(ev) {
          if (currentView !== 'month') return timedBlock(ev);
          // In the month grid a timed item is a dot, its start and its title.
          const item = findItem(ev.id) || ev;
          return `<span class="cal-chip cal-chip-timed" data-item-id="${ev.id}">
              <i class="cal-chip-dot" style="background:${paint(item).borderColor}"></i>
              <span class="cal-chip-time">${fmtTime(item.start)}</span>
              <span class="cal-chip-t">${escapeHtml(ev.title || '')}</span></span>`;
        },
        task(ev) {
          const done = ev.raw && ev.raw.done;
          return `<span class="cal-chip cal-chip-task ${done ? 'is-done' : ''}" data-item-id="${ev.id}">
              <input type="checkbox" class="biocal-task-cb" ${done ? 'checked' : ''} data-item-id="${ev.id}" aria-label="${t('Done')}">
              <span class="cal-chip-t">${escapeHtml(ev.title || '')}</span></span>`;
        },
        monthMoreTitleDate(more) { return `<span class="cal-more-date">${more.ymd ? new Date(more.ymd + 'T00:00').toLocaleDateString(LOCALE, { weekday: 'long', month: 'long', day: 'numeric' }) : ''}</span>`; },
        monthGridHeaderExceed(hidden) { return `<span class="cal-more">${t('+%(n)s more', { n: hidden })}</span>`; },
        alldayTitle() { return `<span class="cal-panel-title">${t('Due')}</span>`; },
        // The week's days: the weekday, then the date in a circle (filled on today).
        weekDayName(day) {
          const isToday = day.dateInstance && ymd(tuiToDate(day.dateInstance)) === ymd(new Date());
          return `<span class="cal-wd${isToday ? ' is-today' : ''}"><span class="cal-wd-dow">${escapeHtml(day.dayName)}</span><span class="cal-wd-n">${day.date}</span></span>`;
        },
        // Hours in 24-hour mono type, and the time now beside its red line.
        timegridDisplayPrimaryTime({ time }) { return `<span class="cal-hour">${pad(time.getHours())}:00</span>`; },
        timegridNowIndicatorLabel({ time }) { return `<span class="cal-now">${pad(time.getHours())}:${pad(time.getMinutes())}</span>`; },
        taskTitle() { return `<span class="cal-panel-title">${t('To-dos')}</span>`; },
      },
    });

    calendar.on('selectDateTime', (sel) => {
      const start = tuiToDate(sel.start);
      const end = tuiToDate(sel.end || sel.start);
      // One day picked in Month view (a double-click): a timed event that
      // day, 09:00–10:00, like New event. Several days dragged: all day.
      if (sel.isAllday && ymd(start) === ymd(end)) {
        const day = ymd(start);
        openItem({ kind: 'event', start: `${day}T09:00`, end: `${day}T10:00`, isAllday: false });
      } else {
        openItem({ kind: 'event', start: toLocalInput(start), end: toLocalInput(end), isAllday: !!sel.isAllday });
      }
      try { calendar.clearGridSelections(); } catch (_) {}
    });

    calendar.on('beforeUpdateEvent', ({ event, changes }) => {
      const item = findItem(event.id);
      if (!item || item.isReadOnly || (item.raw && item.raw.occurrence)) return;
      if (!['event', 'task', 'booking'].includes(item.kind)) return;
      try { calendar.updateEvent(event.id, event.calendarId, changes); } catch (_) {}
      const next = Object.assign({}, event, changes);
      let saving;
      if (item.kind === 'booking') {
        saving = postJson('/calendar/bookings', {
          id: item.raw.bookingId, equipment_id: item.raw.equipmentId,
          start: tuiToIso(next.start), end: tuiToIso(next.end), purpose: item.raw.purpose,
        });
      } else {
        saving = postJson(`/calendar/items/${event.id}`, {
          kind: item.kind, title: item.title, start: tuiToIso(next.start), end: tuiToIso(next.end),
          isAllday: !!next.isAllday, backgroundColor: item.backgroundColor, body: item.body,
        });
      }
      saving.then((j) => { if (!j.ok) toast(j.error || t("Couldn't move it.")); fetchAndRender(); });
    });

    calendar.on('clickEvent', ({ event, nativeEvent }) => {
      const target = nativeEvent && nativeEvent.target;
      if (target && target.classList && target.classList.contains('biocal-task-cb')) {
        nativeEvent.stopPropagation();
        toggleDone(event.id);
        return;
      }
    });
    // One click on an item shows its quick look, in every view: the
    // calendar's own click came only on a double-click in Month, and not
    // at all on a read-only item, so the page listens itself.
    tuiHost.addEventListener('click', (e) => {
      if (e.target.closest('.biocal-task-cb')) return;
      const chipEl = e.target.closest('[data-item-id]');
      const item = chipEl && findItem(chipEl.dataset.itemId);
      if (item) peek(item, e);
    });

    let savedView = null;
    try { savedView = localStorage.getItem(VIEW_KEY); } catch (_) {}
    // A link or bookmark may say which view and day: /calendar?view=week&date=2026-10-06.
    const params = new URLSearchParams(window.location.search);
    const askedView = ['month', 'week', 'day', 'list', 'board'].includes(params.get('view')) ? params.get('view') : null;
    const askedDate = /^\d{4}-\d{2}-\d{2}$/.test(params.get('date') || '') ? new Date(`${params.get('date')}T12:00`) : null;
    if (askedDate && !Number.isNaN(askedDate.getTime())) {
      calendar.setDate(askedDate);
      listAnchor = startOfDay(askedDate);
    }
    const initial = askedView || savedView || (window.innerWidth < 720 ? 'list' : 'month');
    setView(initial, false);
    fetchAndRender();
    loadProtocols();
    spriteReady.then(() => { if (allItems.length) renderAll(); });
  });

  // ================================================================ navigation

  $('#biocal-prev').addEventListener('click', () => step(-1));
  $('#biocal-next').addEventListener('click', () => step(1));
  $('#biocal-today').addEventListener('click', () => {
    listAnchor = startOfDay(new Date());
    if (calendar) calendar.today();
    miniMonth = firstOfMonth(new Date());
    afterMove();
  });

  function step(direction) {
    if (currentView === 'list') {
      listAnchor = addDays(listAnchor, 14 * direction);
    } else if (calendar) {
      direction < 0 ? calendar.prev() : calendar.next();
    }
    miniMonth = firstOfMonth(currentDate());
    afterMove();
  }

  function goTo(day) {
    // A day picked while on the Board opens that day.
    if (currentView === 'board') { listAnchor = startOfDay(day); if (calendar) calendar.setDate(day); setView('day', true); return; }
    listAnchor = startOfDay(day);
    if (calendar) calendar.setDate(day);
    miniMonth = firstOfMonth(day);
    afterMove();
  }

  function afterMove() {
    refreshTitle();
    renderMini();
    fetchAndRender();
  }

  function currentDate() {
    if (currentView === 'list' || !calendar) return listAnchor;
    return tuiToDate(calendar.getDate());
  }

  $$('.biocal-view-btn').forEach((btn) => btn.addEventListener('click', () => setView(btn.dataset.view, true)));

  function setView(view, refetch) {
    currentView = view;
    $$('.biocal-view-btn').forEach((b) => {
      b.classList.toggle('is-active', b.dataset.view === view);
      b.setAttribute('aria-pressed', String(b.dataset.view === view));
    });
    try { localStorage.setItem(VIEW_KEY, view); } catch (_) {}
    const isList = view === 'list';
    // The Board: the to-dos as cards in lists, with no days to step through
    // (static/calendar-board.js).
    const isBoard = view === 'board';
    tuiHost.hidden = isList || isBoard;
    listHost.hidden = !isList;
    $('#biocal-board').hidden = !isBoard;
    document.body.classList.toggle('cal-is-board', isBoard);
    if (isBoard) {
      if (window.BioBoard) window.BioBoard.show();
      titleEl.textContent = t('To-dos');
      return;
    }
    if (!isList && calendar) {
      calendar.changeView(view);
      if (listAnchor) calendar.setDate(listAnchor);
    }
    if (isList && calendar) listAnchor = startOfDay(tuiToDate(calendar.getDate()));
    refreshTitle();
    renderMini();
    if (refetch) fetchAndRender(); else renderAll();
  }

  function refreshTitle() {
    const d = currentDate();
    let html;
    if (currentView === 'month') {
      html = d.toLocaleDateString(LOCALE, { month: 'long', year: 'numeric' });
    } else if (currentView === 'week' && calendar) {
      const s = tuiToDate(calendar.getDateRangeStart());
      const e = tuiToDate(calendar.getDateRangeEnd());
      const sameMonth = s.getMonth() === e.getMonth();
      // "October 2026 · Oct 25 – 31": the month the week is mostly in, then its days.
      const mid = addDays(s, 3);
      const range = `${s.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })} – ${e.toLocaleDateString(LOCALE, sameMonth ? { day: 'numeric' } : { month: 'short', day: 'numeric' })}`;
      html = `${mid.toLocaleDateString(LOCALE, { month: 'long', year: 'numeric' })} <span class="cal-title-sub">· ${range}</span>`;
    } else if (currentView === 'day') {
      html = `${d.toLocaleDateString(LOCALE, { weekday: 'long', month: 'long', day: 'numeric' })} <span class="cal-title-sub">${d.getFullYear()}</span>`;
    } else {
      html = `${t('From %(date)s', { date: d.toLocaleDateString(LOCALE, { month: 'long', day: 'numeric' }) })} <span class="cal-title-sub">${t('6 weeks')}</span>`;
    }
    titleEl.innerHTML = html;
  }

  // ================================================================ data

  function visibleRange() {
    if (currentView === 'list' || !calendar) return [addDays(listAnchor, -1), addDays(listAnchor, 42)];
    return [addDays(tuiToDate(calendar.getDateRangeStart()), -7), addDays(tuiToDate(calendar.getDateRangeEnd()), 7)];
  }

  function fetchAndRender() {
    const [s, e] = visibleRange();
    const seq = ++fetchSeq;
    fetch(`/calendar/events.json?start=${ymd(s)}&end=${ymd(e)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!j || !j.ok || seq !== fetchSeq) return;
        allItems = j.items || [];
        coverReport = j.cover || [];
        renderAll();
        renderCover();
      });
  }

  function findItem(id) { return allItems.find((x) => x.id === id); }

  function renderAll() {
    const shown = allItems.filter(visible);
    if (calendar && currentView !== 'list') {
      calendar.clear();
      calendar.createEvents(shown.map(paint));
    }
    if (currentView === 'list') renderList(shown);
    renderCounts();
    renderMini();
  }

  function renderCounts() {
    const [s, e] = currentView === 'month' && calendar
      ? [tuiToDate(calendar.getDateRangeStart()), tuiToDate(calendar.getDateRangeEnd())]
      : visibleRange();
    const counts = {};
    allItems.forEach((it) => {
      const d = new Date(it.start);
      if (d < s || d > addDays(e, 1)) return;
      const key = layerOf(it);
      counts[key] = (counts[key] || 0) + 1;
    });
    $$('[data-layer-count]').forEach((el) => { el.textContent = counts[el.dataset.layerCount] || ''; });
  }

  // ================================================================ list view

  function renderList(items) {
    const [s, e] = [listAnchor, addDays(listAnchor, 42)];
    const rows = items.filter((it) => {
      const d = new Date(it.start);
      const end = new Date(it.end);
      return end >= s && d <= e;
    }).sort((a, b) => a.start.localeCompare(b.start) || (b.isAllday - a.isAllday));
    if (!rows.length) {
      listHost.innerHTML = `<div class="cal-empty">${t('Nothing in these six weeks.')}</div>`;
      return;
    }
    const groups = new Map();
    rows.forEach((it) => {
      // A span (time away, a protocol step) is listed on its first day in range.
      const first = new Date(it.start) < s ? s : new Date(it.start);
      const key = ymd(first);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(it);
    });
    const today = ymd(new Date());
    listHost.innerHTML = Array.from(groups.entries()).map(([day, dayItems]) => {
      const d = new Date(day + 'T00:00');
      const rel = day === today ? `<span class="cal-list-rel">${t('Today')}</span>`
        : day === ymd(addDays(new Date(), 1)) ? `<span class="cal-list-rel">${t('Tomorrow')}</span>` : '';
      return `<section class="cal-list-day${day === today ? ' is-today' : ''}">
          <header class="cal-list-date">
            <span class="cal-list-num">${d.getDate()}</span>
            <span class="cal-list-dow">${d.toLocaleDateString(LOCALE, { weekday: 'long' })}<small>${d.toLocaleDateString(LOCALE, { month: 'long', year: 'numeric' })}</small></span>
            ${rel}
          </header>
          <ul class="cal-list-rows">${dayItems.map(listRow).join('')}</ul>
        </section>`;
    }).join('');
  }

  function listRow(it) {
    const painted = paint(it);
    const done = it.kind === 'task' && it.raw && it.raw.done;
    const time = it.isAllday ? spanLabel(it) : `${fmtTime(it.start)} – ${fmtTime(it.end)}`;
    const lead = it.kind === 'task'
      ? `<input type="checkbox" class="biocal-list-cb" data-item-id="${it.id}" ${done ? 'checked' : ''} ${it.raw && it.raw.readOnly ? 'disabled' : ''} aria-label="${t('Done')}">`
      : `<span class="cal-list-icon" style="--edge:${painted.borderColor};--fill:${painted.backgroundColor}">${svgIcon(iconFor(it) || 'calendar')}</span>`;
    const whose = it.kind === 'task' && it.raw && it.raw.audienceLabel ? t('%(whose)s’s', { whose: t(it.raw.audienceLabel) }) : '';
    const meta = [(it.raw && it.raw.group && t(it.raw.group)) || LAYER_NAMES[layerOf(it)], whose, it.body].filter(Boolean).join(' · ');
    return `<li class="cal-list-row${done ? ' is-done' : ''}" data-item-id="${it.id}" tabindex="0">
        ${lead}
        <span class="cal-list-time">${time}</span>
        <span class="cal-list-main"><span class="cal-list-title">${escapeHtml(it.title)}</span><span class="cal-list-meta">${escapeHtml(meta.slice(0, 140))}</span></span>
      </li>`;
  }

  function spanLabel(it) {
    const s = new Date(it.start), e = new Date(it.end);
    if (ymd(s) === ymd(e)) return t('All day');
    return `${s.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })} – ${e.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })}`;
  }

  listHost.addEventListener('click', (e) => {
    const cb = e.target.closest('.biocal-list-cb');
    if (cb) { e.stopPropagation(); toggleDone(cb.dataset.itemId); return; }
    const row = e.target.closest('.cal-list-row');
    if (!row) return;
    const item = findItem(row.dataset.itemId);
    if (item) openFromItem(item);
  });
  listHost.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const row = e.target.closest('.cal-list-row');
    const item = row && findItem(row.dataset.itemId);
    if (item) openFromItem(item);
  });

  // ================================================================ mini month

  $('#cal-mini-prev').addEventListener('click', () => { miniMonth = new Date(miniMonth.getFullYear(), miniMonth.getMonth() - 1, 1); renderMini(); });
  $('#cal-mini-next').addEventListener('click', () => { miniMonth = new Date(miniMonth.getFullYear(), miniMonth.getMonth() + 1, 1); renderMini(); });

  function renderMini() {
    const grid = $('#cal-mini-grid');
    $('#cal-mini-title').textContent = miniMonth.toLocaleDateString(LOCALE, { month: 'long', year: 'numeric' });
    const busy = new Set(allItems.filter(visible).map((it) => (it.start || '').slice(0, 10)));
    const start = addDays(miniMonth, -miniMonth.getDay());
    const today = ymd(new Date());
    const selected = ymd(currentDate());
    // The week on show is a band across the small month.
    let band = null;
    if (currentView === 'week' && calendar) band = [ymd(tuiToDate(calendar.getDateRangeStart())), ymd(tuiToDate(calendar.getDateRangeEnd()))];
    let html = MINI_DAY_NAMES.map((d) => `<span class="cal-mini-dow">${d}</span>`).join('');
    for (let i = 0; i < 42; i++) {
      const d = addDays(start, i);
      const key = ymd(d);
      const cls = ['cal-mini-day'];
      if (d.getMonth() !== miniMonth.getMonth()) cls.push('is-other');
      if (key === today) cls.push('is-today');
      if (key === selected && !band) cls.push('is-selected');
      if (busy.has(key)) cls.push('has-items');
      if (band && key >= band[0] && key <= band[1]) {
        cls.push('in-band');
        if (key === band[0]) cls.push('band-start');
        if (key === band[1]) cls.push('band-end');
      }
      html += `<button type="button" class="${cls.join(' ')}" data-day="${key}" aria-label="${LOCALE ? d.toLocaleDateString(LOCALE, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' }) : d.toDateString()}"><span>${d.getDate()}</span></button>`;
    }
    grid.innerHTML = html;
  }
  $('#cal-mini-grid').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-day]');
    if (btn) goTo(new Date(btn.dataset.day + 'T00:00'));
  });

  // ================================================================ sidebar lists

  function renderEquipment() {
    const list = $('#cal-equip-list');
    const eq = DATA.equipment || [];
    list.innerHTML = eq.length
      ? eq.map((e) => `<li><button type="button" class="cal-equip-item" data-equipment="${e.id}" title="${t('Book %(name)s', { name: escapeHtml(e.name) })}">
            <span class="cal-dot" style="background:${e.color}"></span><span class="cal-equip-name">${escapeHtml(e.name)}</span>
            ${e.location ? `<small>${escapeHtml(e.location)}</small>` : ''}</button></li>`).join('')
      : `<li class="cal-muted">${t('No instruments yet.')}</li>`;
    const select = $('#cal-equipment-select');
    select.innerHTML = eq.map((e) => `<option value="${e.id}">${escapeHtml(e.name)}${e.location ? ' · ' + escapeHtml(e.location) : ''}</option>`).join('');
  }
  $('#cal-equip-list').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-equipment]');
    if (btn) openNew('booking', { equipmentId: Number(btn.dataset.equipment) });
  });

  function renderRuns() {
    const list = $('#cal-run-list');
    const today = ymd(new Date());
    const live = protocols.runs.filter((r) => {
      const last = r.steps.length ? r.steps[r.steps.length - 1].to : 0;
      return ymd(addDays(new Date(r.start_date + 'T00:00'), last)) >= today;
    });
    const buttons = `<li><button type="button" class="cal-link-btn cal-start-run" data-new="protocol">${svgIcon('plus')} ${t('Start a protocol')}</button></li>`;
    if (!live.length) {
      list.innerHTML = (protocols.templates.length ? '' : `<li class="cal-muted">${t('Write one under Edit, then start it on a day 0.')}</li>`) + buttons;
      return;
    }
    list.innerHTML = live.slice(0, 6).map((r) => {
      const start = new Date(r.start_date + 'T00:00');
      const dayN = Math.round((startOfDay(new Date()) - start) / 86400000);
      const total = r.steps.length ? r.steps[r.steps.length - 1].to : 0;
      const when = dayN < 0 ? t('starts %(date)s', { date: start.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' }) }) : t('day %(n)s of %(total)s', { n: dayN, total });
      const pct = total > 0 ? Math.max(0, Math.min(100, (dayN / total) * 100)) : 0;
      return `<li><button type="button" class="cal-run-item" data-run="${r.id}" style="--tone:${r.color}">
          <span class="cal-run-name">${escapeHtml(r.label || r.name)}</span>
          <small>${escapeHtml(r.label ? r.name : '')}${r.label ? ' · ' : ''}${when}</small>
          <span class="cal-run-bar"><i style="width:${pct}%"></i></span></button></li>`;
    }).join('') + buttons;
  }
  $('#cal-run-list').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-run]');
    if (btn) openRun(protocols.runs.find((r) => r.id === Number(btn.dataset.run)));
  });

  function renderCover() {
    const box = $('#cal-cover');
    const list = $('#cal-cover-list');
    box.hidden = !coverReport.length;
    if (!coverReport.length) return;
    const options = (selected, owner) => [`<option value="">${t('No one yet')}</option>`]
      .concat((DATA.people || []).filter((p) => p.username !== owner)
        .map((p) => `<option value="${escapeHtml(p.username)}"${p.username === selected ? ' selected' : ''}>${escapeHtml(p.name)}</option>`)).join('');
    list.innerHTML = coverReport.map((a) => {
      const s = new Date(a.start + 'T00:00'), e = new Date(a.end + 'T00:00');
      const span = a.start === a.end ? s.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })
        : `${s.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })} – ${e.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })}`;
      const needs = a.count && !a.cover;
      const jobs = a.jobs.slice(0, 3).map((j) => `<li><span>${new Date(j.date + 'T00:00').toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })}</span>${escapeHtml(j.title)}</li>`).join('');
      const coverCtl = a.editable
        ? `<label class="cal-cover-pick">${t('Covered by')} <select data-cover="${a.id}">${options(a.cover, a.owner)}</select></label>`
        : `<p class="cal-cover-who">${a.cover ? t('Covered by %(name)s', { name: escapeHtml(a.cover_name) }) : t('No cover chosen yet')}</p>`;
      return `<article class="cal-cover-card${needs ? ' needs-cover' : ''}">
          <header><b>${escapeHtml(a.name)}</b><span>${span}</span></header>
          ${a.count ? `<p class="cal-cover-count">${t(a.count === 1 ? '%(n)s thing due while away' : '%(n)s things due while away', { n: a.count })}</p><ul class="cal-cover-jobs">${jobs}</ul>`
                    : `<p class="cal-cover-count">${t('Nothing of theirs falls due.')}</p>`}
          ${coverCtl}
        </article>`;
    }).join('');
  }
  $('#cal-cover-list').addEventListener('change', (e) => {
    const sel = e.target.closest('[data-cover]');
    if (!sel) return;
    postJson(`/calendar/away/${sel.dataset.cover}/cover`, { cover: sel.value }).then((j) => {
      if (!j.ok) toast(j.error || t("Couldn't save that."));
      else toast(sel.value ? t('Cover saved; they have been told.') : t('Cover cleared.'));
      fetchAndRender();
    });
  });

  // ================================================================ new menu

  const newMenu = $('#cal-new-menu');
  const newMore = $('#cal-new-more');
  newMore.addEventListener('click', (e) => {
    e.stopPropagation();
    newMenu.hidden = !newMenu.hidden;
    newMore.setAttribute('aria-expanded', String(!newMenu.hidden));
  });
  document.addEventListener('click', (e) => {
    if (!newMenu.hidden && !newMenu.contains(e.target)) { newMenu.hidden = true; newMore.setAttribute('aria-expanded', 'false'); }
    const btn = e.target.closest('[data-new]');
    if (btn) { newMenu.hidden = true; openNew(btn.dataset.new); }
  });

  function openNew(kind, extra) {
    const now = new Date();
    const day = ymd(currentView === 'list' ? new Date() : currentDate() < startOfDay(now) ? now : currentDate());
    if (kind === 'protocol') return openRun(null);
    if (kind === 'booking') {
      const start = new Date(now);
      start.setMinutes(0, 0, 0);
      start.setHours(start.getHours() + 1);
      return openItem(Object.assign({ kind, bookingStart: toLocalInput(start), bookingEnd: toLocalInput(new Date(start.getTime() + 3600000)) }, extra || {}));
    }
    if (kind === 'away') return openItem({ kind, awayStart: day, awayEnd: day, awayOwner: DATA.me });
    // Timed, as the times it shows say: typed times were dropped when All day was ticked by default.
    return openItem({ kind, start: `${day}T09:00`, end: `${day}T10:00`, isAllday: false });
  }

  $('#cal-side-toggle').addEventListener('click', () => {
    const open = !side.classList.contains('is-open');
    side.classList.toggle('is-open', open);
    $('#cal-side-toggle').setAttribute('aria-expanded', String(open));
  });

  // ================================================================ opening an item

  let lastOpened = { id: '', at: 0 };
  function openOnce(item) {
    const now = Date.now();
    if (lastOpened.id === item.id && now - lastOpened.at < 800) return undefined;
    lastOpened = { id: item.id, at: now };
    return openFromItem(item);
  }

  // The Board opens a dated to-do in the same editor as the calendar does.
  window.BioCalendar = { open: (item) => openFromItem(item) };

  function openFromItem(item) {
    const raw = item.raw || {};
    switch (item.kind) {
      case 'task':
        if (raw.readOnly) return toast(t('%(title)s: %(whose)s’s to-do, read only.', { title: item.title, whose: raw.audienceLabel ? t(raw.audienceLabel) : raw.owner || t('someone else') }));
        // falls through
      case 'event':
        if (item.kind === 'event' && raw.readOnly) {
          return toast(t('%(what)s. Only %(who)s or an admin can change it.', { what: `${item.title}${item.body ? ': ' + item.body : ''}`, who: raw.owner || t('the person who added it') }));
        }
        return openItem({
          kind: item.kind, id: item.id.split('@')[0], occurrence: raw.occurrence || '', title: item.title,
          start: toLocalInput(new Date(item.start)), end: toLocalInput(new Date(item.end)),
          isAllday: !!item.isAllday, color: item.backgroundColor, body: item.body || '', repeat: raw.repeat || null,
          audience: raw.audience || '0',
        });
      case 'booking':
        if (item.isReadOnly) return toast(t('%(what)s. Only the person who booked it can change it.', { what: `${item.title}${raw.purpose ? ': ' + raw.purpose : ''}` }));
        return openItem({ kind: 'booking', id: raw.bookingId, equipmentId: raw.equipmentId, purpose: raw.purpose,
          bookingStart: toLocalInput(new Date(item.start)), bookingEnd: toLocalInput(new Date(item.end)) });
      case 'away':
        if (!raw.editable) return toast(`${item.title}. ${item.body || ''}`);
        return openItem({ kind: 'away', id: raw.absenceId, awayOwner: raw.owner, awayStart: item.start.slice(0, 10),
          awayEnd: item.end.slice(0, 10), awayKind: raw.kind, awayCover: raw.cover, awayNote: raw.note });
      case 'protocol': {
        const run = protocols.runs.find((r) => r.id === raw.runId);
        return run ? openRun(run) : undefined;
      }
      case 'auto':
      case 'agenda':
        if (raw.href) window.location.href = raw.href;
        return undefined;
      default:
        return undefined;
    }
  }

  // ================================================================ quick look

  /* Clicking an item shows it in a small pane of glass beside it, as in the
     design: its colour and title, its day and hours, what kind it is and
     whose, the mice it names, its note, then Mark done (a to-do) and Open. */
  const peekEl = document.createElement('div');
  peekEl.className = 'cal-peek';
  peekEl.setAttribute('role', 'dialog');
  peekEl.hidden = true;
  document.body.appendChild(peekEl);
  let peeked = null;
  const KIND_WORDS = { event: t('Event'), task: t('To-do'), booking: t('Booking'), away: t('Away'),
    protocol: t('Protocol'), auto: t('Mouse colony'), agenda: t('Due'), subscription: t('Connected calendar'),
    google: t('Connected calendar'), meeting: t('Meeting') };

  function closePeek() { peekEl.hidden = true; peeked = null; }
  document.addEventListener('pointerdown', (e) => {
    if (!peekEl.hidden && !peekEl.contains(e.target) && !(e.target.closest && e.target.closest('[data-item-id], .toastui-calendar-weekday-event, .toastui-calendar-event-time'))) closePeek();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !peekEl.hidden) closePeek(); });
  window.addEventListener('resize', closePeek);

  function peek(item, nativeEvent) {
    if (peeked === item.id && !peekEl.hidden) { closePeek(); return; }
    peeked = item.id;
    const raw = item.raw || {};
    const start = new Date(item.start);
    const end = new Date(item.end);
    const day = start.toLocaleDateString(LOCALE, { weekday: 'short', day: 'numeric', month: 'short' });
    const hours = item.isAllday || item.category === 'allday' ? '' : ` · <span class="cal-peek-mono">${hm(start)} – ${hm(end)}</span>`;
    const colour = paint(item).borderColor;
    const chips = [];
    const kindWord = raw.eventType === 'meeting' ? KIND_WORDS.meeting : (raw.group || KIND_WORDS[item.kind] || '');
    if (kindWord) {
      const extra = item.kind === 'protocol' && item.body ? ` · ${escapeHtml(String(item.body).split(', ').pop())}` : '';
      chips.push(`<span class="cal-peek-chip is-kind" style="--edge:${colour}">${escapeHtml(kindWord)}${extra}</span>`);
    }
    if (raw.owner) chips.push(`<span class="cal-peek-chip">${escapeHtml(t('by %(who)s', { who: raw.owner }))}</span>`);
    if (raw.audienceLabel) chips.push(`<span class="cal-peek-chip">${escapeHtml(t(raw.audienceLabel))}</span>`);
    // Mice the item names (#37, #38…), as chips.
    const mice = Array.from(new Set(String(item.title || '').match(/#\d+/g) || []));
    const body = item.kind === 'protocol' ? '' : String(item.body || '').trim();
    const canDone = item.kind === 'task' && !raw.readOnly;
    const canOpen = ['event', 'task', 'booking', 'away', 'protocol'].includes(item.kind) || raw.href;
    peekEl.innerHTML = `
      <div class="cal-peek-head">
        <span class="cal-peek-swatch" style="background:${colour}"></span>
        <div class="cal-peek-titles"><div class="cal-peek-title">${escapeHtml(item.title || '')}</div>
          <div class="cal-peek-when">${escapeHtml(day)}${hours}</div></div>
        <button type="button" class="cal-peek-x" aria-label="${t('Close')}">${svgIcon('close')}</button>
      </div>
      ${chips.length ? `<div class="cal-peek-chips">${chips.join('')}</div>` : ''}
      ${mice.length ? `<div><div class="cal-peek-label">${t('Mice')}</div><div class="cal-peek-chips">${mice.map((m) => `<a class="cal-peek-mouse" href="/colony?view=mice&q=${encodeURIComponent(m.slice(1))}">${svgIcon('mouse')}${m}</a>`).join('')}</div></div>` : ''}
      ${body ? `<p class="cal-peek-body">${escapeHtml(body)}</p>` : ''}
      ${raw.pageUrl ? `<div class="cal-peek-label">${t('Notebook')}: <a href="${escapeHtml(raw.pageUrl)}">${escapeHtml(raw.pageTitle || item.title || '')}</a></div>` : ''}
      <div class="cal-peek-actions">
        ${canDone ? `<button type="button" class="btn btn-primary btn-sm" data-peek="done">${raw.done ? t('Mark not done') : t('Mark done')}</button>` : ''}
        ${canOpen ? `<button type="button" class="btn btn-sm" data-peek="open">${t('Open')}</button>` : ''}
      </div>`;
    peekEl.setAttribute('aria-label', item.title || '');
    peekEl.querySelector('.cal-peek-x').addEventListener('click', closePeek);
    const done = peekEl.querySelector('[data-peek="done"]');
    if (done) done.addEventListener('click', () => { closePeek(); toggleDone(item.id); });
    const open = peekEl.querySelector('[data-peek="open"]');
    if (open) open.addEventListener('click', () => { closePeek(); openOnce(item); });
    peekEl.hidden = false;
    // Beside what was clicked, on whichever side has room.
    const target = nativeEvent && nativeEvent.target && nativeEvent.target.closest
      ? (nativeEvent.target.closest('.toastui-calendar-weekday-event, .toastui-calendar-event-time, .cal-chip') || nativeEvent.target) : null;
    const r = target ? target.getBoundingClientRect() : { left: innerWidth / 2, right: innerWidth / 2, top: 120, bottom: 120 };
    const w = peekEl.offsetWidth;
    const h = peekEl.offsetHeight;
    let left = r.right + 10;
    if (left + w > innerWidth - 12) left = Math.max(12, r.left - w - 10);
    const top = Math.max(12, Math.min(r.top - 8, innerHeight - h - 12));
    peekEl.style.left = left + 'px';
    peekEl.style.top = top + 'px';
    const first = peekEl.querySelector('[data-peek]') || peekEl.querySelector('.cal-peek-x');
    if (first) first.focus({ preventScroll: true });
  }

  // ================================================================ item dialog

  const modal = $('#biocal-modal');
  const form = $('#biocal-form');
  // Changing a start or end time means a timed event.
  ['start', 'end'].forEach((name) => form.elements[name].addEventListener('input', () => {
    const v = form.elements[name].value;
    if (v && v.slice(11, 16) !== '00:00') form.elements.isAllday.checked = false;
  }));

  if (form.elements.audience) form.elements.audience.addEventListener('change', () => { form.elements.audience.dataset.touched = '1'; });
  const formError = $('#cal-form-error');
  const delBtn = $('#biocal-delete');
  const delOneBtn = $('#cal-delete-one');
  const dupBtn = $('#cal-duplicate');
  const changeOneBtn = $('#cal-change-one');
  const ORDINALS = [t('first'), t('second'), t('third'), t('fourth'), t('last')];
  const repeatFreq = $('#cal-repeat-freq');
  const dueCustom = $('#biocal-due-custom');
  const duePresets = $$('.biocal-due-btn', form);
  const KIND_TITLES = { event: [t('New event'), t('Edit event')], task: [t('New to-do'), t('Edit to-do')],
    booking: [t('Book equipment'), t('Change booking')], away: [t('Time away'), t('Change time away')] };

  function showError(el, message) { el.textContent = message || ''; el.hidden = !message; }

  function setKind(kind, editing) {
    $$('[data-for]', form).forEach((el) => { el.hidden = !el.dataset.for.split(' ').includes(kind); });
    $('#biocal-modal-title').textContent = KIND_TITLES[kind][editing ? 1 : 0];
    $('#cal-no-equipment').hidden = kind !== 'booking' || (DATA.equipment || []).length > 0;
    showError(formError, '');
  }

  $$('input[name="kind"]', form).forEach((r) => r.addEventListener('change', () => {
    const kind = r.value;
    if (kind === 'task') {
      const startVal = form.elements.start.value;
      if (startVal) { dueCustom.value = startVal.slice(0, 10); selectDuePreset(presetForDate(dueCustom.value)); }
    } else if (kind === 'event' && dueCustom.value && !form.elements.start.value) {
      form.elements.start.value = `${dueCustom.value}T09:00`;
      form.elements.end.value = `${dueCustom.value}T10:00`;
    } else if (kind === 'booking' && !form.elements.booking_start.value) {
      const base = form.elements.start.value || `${ymd(new Date())}T09:00`;
      form.elements.booking_start.value = base.length > 10 ? base : `${base}T09:00`;
      form.elements.booking_end.value = toLocalInput(new Date(new Date(form.elements.booking_start.value).getTime() + 3600000));
    } else if (kind === 'away' && !form.elements.away_start.value) {
      const day = (form.elements.start.value || ymd(new Date())).slice(0, 10);
      form.elements.away_start.value = day;
      form.elements.away_end.value = day;
    }
    const audience = form.elements.audience;
    if (audience && !form.elements.id.value && !audience.dataset.touched) audience.value = kind === 'event' ? '1' : '0';
    setKind(kind, !!form.elements.id.value);
  }));

  function fillPeople(select, selected, blank) {
    select.innerHTML = (blank ? [`<option value="">${blank}</option>`] : [])
      .concat((DATA.people || []).map((p) => `<option value="${escapeHtml(p.username)}"${p.username === selected ? ' selected' : ''}>${escapeHtml(p.name)}</option>`)).join('');
  }

  function openItem(item) {
    form.reset();
    const kind = item.kind || 'event';
    const editing = !!item.id;
    form.elements.id.value = item.id || '';
    form.elements.occurrence.value = item.occurrence || '';
    form.querySelector(`input[name="kind"][value="${kind}"]`).checked = true;
    // An event can become a to-do and back; bookings and time away stay what they are.
    $$('input[name="kind"]', form).forEach((r) => {
      const locked = editing && (['booking', 'away'].includes(kind) ? r.value !== kind : ['booking', 'away'].includes(r.value));
      r.disabled = locked;
      r.closest('.seg-item').classList.toggle('is-disabled', locked);
    });

    form.elements.title.value = item.title || '';
    if (form.elements.audience) {
      const audience = form.elements.audience;
      // A group the to-do is for but this person can't pick still shows as itself.
      if (item.audience && !Array.from(audience.options).some((o) => o.value === item.audience)) {
        audience.add(new Option(t('A project group’s'), item.audience));
      }
      // New: an event is the lab's, a to-do yours, unless chosen otherwise.
      audience.value = item.audience || (kind === 'event' ? '1' : '0');
      audience.dataset.touched = item.audience ? '1' : '';
    }
    form.elements.start.value = kind === 'event' ? (item.start || '') : '';
    form.elements.end.value = kind === 'event' ? (item.end || '') : '';
    form.elements.isAllday.checked = !!item.isAllday;
    const day = (item.start || '').slice(0, 10) || ymd(new Date());
    dueCustom.value = day;
    selectDuePreset(presetForDate(day));

    const rep = item.repeat || null;
    repeatFreq.value = rep ? rep.freq : '';
    form.elements.repeat_interval.value = rep ? rep.interval : 1;
    form.elements.repeat_until.value = rep ? rep.until : '';
    syncRepeat();
    $('#cal-repeat-hint').hidden = !rep;

    form.elements.equipment_id.value = item.equipmentId || ((DATA.equipment || [])[0] || {}).id || '';
    form.elements.booking_start.value = item.bookingStart || '';
    form.elements.booking_end.value = item.bookingEnd || '';
    form.elements.purpose.value = item.purpose || '';

    fillPeople($('#cal-away-owner'), item.awayOwner || DATA.me);
    $('#cal-away-owner').disabled = !DATA.admin || (editing && kind === 'away');
    fillPeople($('#cal-away-cover'), item.awayCover || '', t('No one yet'));
    form.elements.away_start.value = item.awayStart || '';
    form.elements.away_end.value = item.awayEnd || '';
    form.elements.away_kind.value = item.awayKind || 'leave';
    form.elements.away_note.value = item.awayNote || '';

    setSelectedSwatch(item.color || '#a4c8f0');
    form.elements.body.value = item.body || '';
    delBtn.hidden = !editing;
    delBtn.innerHTML = `${svgIcon('trash')} ${rep ? t('Delete all') : kind === 'booking' ? t('Cancel booking') : t('Delete')}`;
    delOneBtn.hidden = !(editing && rep && item.occurrence);
    changeOneBtn.hidden = !(editing && rep && item.occurrence && kind === 'event');
    dupBtn.hidden = !(editing && kind === 'booking');
    $('#cal-booking-repeat').hidden = editing;
    syncBookingRepeat();
    setKind(kind, editing);
    modal.showModal();
    setTimeout(() => { const t = form.elements.title; if (!t.closest('[hidden]')) t.focus(); }, 30);
  }

  function syncBookingRepeat() {
    const f = form.elements;
    $('#cal-booking-until').hidden = !f.booking_repeat.value;
    if (f.booking_repeat.value && !f.booking_until.value && f.booking_start.value) {
      const d = new Date(`${f.booking_start.value.slice(0, 10)}T12:00`);
      d.setDate(d.getDate() + (f.booking_repeat.value === 'weekly' ? 28 : 4));
      f.booking_until.value = ymd(d);
    }
  }
  form.elements.booking_repeat.addEventListener('change', syncBookingRepeat);
  form.elements.start.addEventListener('change', syncRepeat);

  /* Duplicate: the same instrument, times and purpose as a new booking, to
     move to another day (or repeat) before saving. */
  dupBtn.addEventListener('click', () => {
    form.elements.id.value = '';
    dupBtn.hidden = true;
    delBtn.hidden = true;
    $('#cal-booking-repeat').hidden = false;
    setKind('booking', false);
    form.elements.booking_start.focus();
  });

  function syncRepeat() {
    const freq = repeatFreq.value;
    $('#cal-repeat-more').hidden = !freq;
    const n = Number(form.elements.repeat_interval.value) || 1;
    const unit = REPEAT_UNITS[freq] || REPEAT_UNITS.weekly;
    // "Every month on the same weekday" says which: "the first Monday".
    const first = form.elements.start.value ? new Date(`${form.elements.start.value.slice(0, 10)}T12:00`) : null;
    $('#cal-repeat-nth').textContent = first
      ? t('Every month on the %(nth)s %(weekday)s', { nth: ORDINALS[Math.min(4, Math.floor((first.getDate() - 1) / 7))], weekday: first.toLocaleDateString(LOCALE, { weekday: 'long' }) })
      : t('Every month on the same weekday');
    $('#cal-repeat-unit').textContent = n === 1 ? unit[0] : unit[1];
  }
  // The word after "every N" in the dialog, one and several.
  const REPEAT_UNITS = { daily: [t('day'), t('days')], weekly: [t('week'), t('weeks')], monthly: [t('month'), t('months')], nthweekday: [t('month'), t('months')] };

  /* How an event repeats, for its hover card: the server says it in English
     (lab_calendar.repeat_summary); in another language it is said here. */
  function repeatText(rep, item) {
    if (!LOCALE) return rep.text || '';
    const n = Number(rep.interval) || 1;
    const every = n <= 1
      ? { daily: t('Every day'), weekly: t('Every week') }[rep.freq] || t('Every month')
      : t({ daily: 'Every %(n)s days', weekly: 'Every %(n)s weeks' }[rep.freq] || 'Every %(n)s months', { n });
    let text = every;
    if (rep.freq === 'nthweekday') {
      const d = new Date(item.start);
      text = t('%(every)s on the %(nth)s %(weekday)s', { every, nth: ORDINALS[Math.min(4, Math.floor((d.getDate() - 1) / 7))],
        weekday: d.toLocaleDateString(LOCALE, { weekday: 'long' }) });
    }
    if (rep.until) {
      text = t('%(every)s until %(date)s', { every: text,
        date: new Date(`${rep.until}T12:00`).toLocaleDateString(LOCALE, { year: 'numeric', month: 'short', day: 'numeric' }) });
    }
    return text;
  }
  repeatFreq.addEventListener('change', syncRepeat);
  form.elements.repeat_interval.addEventListener('input', syncRepeat);

  // Swatches for events and to-dos.
  function setSelectedSwatch(color) {
    const target = (color || '').toLowerCase();
    let matched = false;
    $$('#biocal-color-swatches .biocal-color-swatch').forEach((s) => {
      const on = s.dataset.color.toLowerCase() === target;
      s.classList.toggle('is-selected', on);
      matched = matched || on;
    });
    if (!matched) { const sky = $('#biocal-color-swatches [data-color="#a4c8f0"]'); if (sky) sky.classList.add('is-selected'); }
    $('#biocal-color-val').value = color || '#a4c8f0';
  }
  $$('#biocal-color-swatches .biocal-color-swatch').forEach((s) => s.addEventListener('click', () => setSelectedSwatch(s.dataset.color)));

  // Due presets for to-dos.
  function dueFromPreset(name) {
    const d = startOfDay(new Date());
    if (name === 'tomorrow') d.setDate(d.getDate() + 1);
    else if (name === 'next-week') d.setDate(d.getDate() + 7);
    return d;
  }
  function selectDuePreset(name) {
    duePresets.forEach((b) => b.classList.toggle('is-active', b.dataset.due === name));
    if (name === 'custom') {
      dueCustom.hidden = false;
      if (!dueCustom.value) dueCustom.value = ymd(new Date());
    } else {
      dueCustom.hidden = true;
      dueCustom.value = ymd(dueFromPreset(name));
    }
  }
  function presetForDate(day) {
    if (!day || day === ymd(dueFromPreset('today'))) return 'today';
    if (day === ymd(dueFromPreset('tomorrow'))) return 'tomorrow';
    if (day === ymd(dueFromPreset('next-week'))) return 'next-week';
    return 'custom';
  }
  duePresets.forEach((b) => b.addEventListener('click', () => selectDuePreset(b.dataset.due)));
  dueCustom.addEventListener('change', () => duePresets.forEach((b) => b.classList.toggle('is-active', b.dataset.due === 'custom')));

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = form.elements;
    const kind = form.querySelector('input[name="kind"]:checked').value;
    const id = f.id.value;
    let request;
    if (kind === 'booking') {
      request = postJson('/calendar/bookings', { id: id || null, equipment_id: f.equipment_id.value,
        start: f.booking_start.value, end: f.booking_end.value, purpose: f.purpose.value,
        repeat: !id && f.booking_repeat.value ? { freq: f.booking_repeat.value, until: f.booking_until.value } : null });
    } else if (kind === 'away') {
      request = postJson('/calendar/away', { id: id || null, owner: f.away_owner.value, start: f.away_start.value,
        end: f.away_end.value || f.away_start.value, kind: f.away_kind.value, note: f.away_note.value, cover: f.away_cover.value });
    } else {
      if (!f.title.value.trim()) return showError(formError, t('Give it a title.'));
      let start, end, isAllday;
      if (kind === 'task') {
        const day = dueCustom.value || ymd(new Date());
        start = `${day}T00:00:00`; end = `${day}T23:59:59`; isAllday = true;
      } else {
        if (!f.start.value) return showError(formError, t('Choose when it starts.'));
        start = f.start.value.length === 16 ? f.start.value + ':00' : f.start.value;
        end = f.end.value ? (f.end.value.length === 16 ? f.end.value + ':00' : f.end.value) : start;
        isAllday = f.isAllday.checked;
      }
      const payload = { kind, title: f.title.value, start, end, isAllday, backgroundColor: f.color.value, body: f.body.value };
      if (f.audience) payload.audience = f.audience.value;
      if (kind === 'event') {
        payload.repeat = repeatFreq.value ? { freq: repeatFreq.value, interval: f.repeat_interval.value, until: f.repeat_until.value } : null;
      }
      const savedKind = id ? id.split('-')[0] : null;
      if (id && savedKind !== kind) {
        request = postJson(`/calendar/items/${id}/delete`, {}).then(() => postJson('/calendar/items', payload));
      } else {
        request = postJson(id ? `/calendar/items/${id}` : '/calendar/items', payload);
      }
    }
    request.then((j) => {
      if (!j.ok) return showError(formError, j.error || t("Couldn't save that."));
      modal.close();
      fetchAndRender();
      if (currentView === 'board' && window.BioBoard) window.BioBoard.show();
      return undefined;
    });
  });

  /* Ends follows Starts: moving the start keeps the event's length, so an
     event can't be made to end before it begins by forgetting the end. */
  /* The same for a booking's From and To, so moving a duplicated booking to
     another day is one change. */
  function endFollowsStart(startEl, endEl) {
    if (!startEl || !endEl) return;
    let length = null;
    const ms = (v) => (v ? new Date(v.length === 10 ? `${v}T00:00` : v).getTime() : NaN);
    const remember = () => { const d = ms(endEl.value) - ms(startEl.value); length = Number.isFinite(d) && d >= 0 ? d : null; };
    startEl.addEventListener('focus', remember);
    endEl.addEventListener('change', remember);
    startEl.addEventListener('change', () => {
      const start = ms(startEl.value);
      if (!Number.isFinite(start)) return;
      if (length === null && Number.isFinite(ms(endEl.value)) && ms(endEl.value) >= start) return;
      const end = new Date(start + (length ?? 60 * 60 * 1000));
      const pad = (n) => String(n).padStart(2, '0');
      const date = `${end.getFullYear()}-${pad(end.getMonth() + 1)}-${pad(end.getDate())}`;
      endEl.value = endEl.type === 'date' ? date : `${date}T${pad(end.getHours())}:${pad(end.getMinutes())}`;
    });
  }
  endFollowsStart(form.elements.start, form.elements.end);
  endFollowsStart(form.elements.booking_start, form.elements.booking_end);

  /* Change this one only: this date becomes an event of its own with what
     the dialog now says, and the series leaves the date out. */
  changeOneBtn.addEventListener('click', () => {
    const f = form.elements;
    if (!f.title.value.trim()) return showError(formError, t('Give it a title.'));
    if (!f.start.value) return showError(formError, t('Choose when it starts.'));
    const start = f.start.value.length === 16 ? f.start.value + ':00' : f.start.value;
    const end = f.end.value ? (f.end.value.length === 16 ? f.end.value + ':00' : f.end.value) : start;
    postJson('/calendar/items', {
      kind: 'event', title: f.title.value, start, end, isAllday: f.isAllday.checked,
      backgroundColor: f.color.value, body: f.body.value,
      split_from: { event_id: Number(String(f.id.value).split('-')[1]), date: f.occurrence.value },
    }).then((j) => {
      if (!j.ok) return showError(formError, j.error || t("Couldn't save that."));
      modal.close();
      fetchAndRender();
      return undefined;
    });
  });

  delBtn.addEventListener('click', async () => {
    const f = form.elements;
    const kind = form.querySelector('input[name="kind"]:checked').value;
    const id = f.id.value;
    if (!id) return;
    const repeat = repeatFreq.value && kind === 'event';
    const question = kind === 'booking' ? t('Cancel this booking?') : kind === 'away' ? t('Remove this time away?')
      : repeat ? t('Delete every repeat of this event?') : t('Delete this?');
    if (!(await BioDialog.confirm(question, { danger: true }))) return;
    const url = kind === 'booking' ? `/calendar/bookings/${id}/delete` : kind === 'away' ? `/calendar/away/${id}/delete`
      : `/calendar/items/${id}/delete`;
    postJson(url, {}).then((j) => {
      if (j.ok === false) return showError(formError, j.error || t("Couldn't delete that."));
      modal.close();
      fetchAndRender();
      return undefined;
    });
  });

  delOneBtn.addEventListener('click', () => {
    const rowId = form.elements.id.value.split('-')[1];
    postJson(`/calendar/events/${rowId}/skip`, { date: form.elements.occurrence.value }).then((j) => {
      if (!j.ok) return showError(formError, j.error || t("Couldn't take that date out."));
      modal.close();
      fetchAndRender();
      return undefined;
    });
  });

  function toggleDone(id) {
    return postJson(`/calendar/items/${id}/toggle`, {}).then((j) => {
      if (j && j.ok === false && j.error) toast(j.error);
      return fetchAndRender();
    });
  }

  // ================================================================ protocols

  const runModal = $('#cal-run-modal');
  const runForm = $('#cal-run-form');
  const tplModal = $('#cal-tpl-modal');
  const tplForm = $('#cal-tpl-form');
  let tplSelected = null;

  function loadProtocols() {
    return fetch('/calendar/protocols').then((r) => (r.ok ? r.json() : null)).then((j) => {
      if (j && j.ok) protocols = { templates: j.templates || [], runs: j.runs || [] };
      renderRuns();
    });
  }

  function openRun(run) {
    if (!run && !protocols.templates.length) return openTemplates(null, t('Write a protocol first; then you can start it on a day 0.'));
    runForm.reset();
    showError($('#cal-run-error'), '');
    const f = runForm.elements;
    f.id.value = run ? run.id : '';
    $('#cal-run-title').textContent = run ? (run.label ? `${run.name} · ${run.label}` : run.name) : t('Start a protocol');
    const templateSelect = $('#cal-run-template');
    templateSelect.innerHTML = protocols.templates.map((tpl) => `<option value="${tpl.id}">${t('%(name)s (%(n)s days)', { name: escapeHtml(tpl.name), n: tpl.days })}</option>`).join('');
    $('#cal-run-template-wrap').hidden = !!run;
    $('#cal-run-experiment').innerHTML = [`<option value="">${t('None')}</option>`]
      .concat((DATA.experiments || []).map((x) => `<option value="${x.id}">${escapeHtml(x.name)}</option>`)).join('');
    f.start_date.value = run ? run.start_date : ymd(new Date());
    f.label.value = run ? run.label : '';
    f.experiment_id.value = run && run.experiment_id ? run.experiment_id : '';
    f.notes.value = run ? run.notes : '';
    const editable = !run || run.editable;
    $$('input, select, textarea', runForm).forEach((el) => { if (el.type !== 'hidden') el.disabled = !editable; });
    runForm.querySelector('[type="submit"]').hidden = !editable;
    $('#cal-run-delete').hidden = !(run && run.editable);
    runForm._run = run || null;
    previewRun();
    runModal.showModal();
    return undefined;
  }

  function previewRun() {
    const f = runForm.elements;
    const run = runForm._run;
    const tpl = run ? null : protocols.templates.find((t) => t.id === Number(f.template_id.value));
    const steps = run ? run.steps : tpl ? tpl.steps : [];
    const day0 = f.start_date.value ? new Date(f.start_date.value + 'T00:00') : null;
    $('#cal-run-preview').innerHTML = steps.length ? steps.map((s) => {
      const a = day0 ? addDays(day0, s.from) : null;
      const b = day0 ? addDays(day0, s.to) : null;
      const fmt = (d) => d.toLocaleDateString(LOCALE, { weekday: 'short', month: 'short', day: 'numeric' });
      const when = !a ? '' : s.from === s.to ? fmt(a) : `${fmt(a)} – ${fmt(b)}`;
      const days = s.from === s.to ? t('Day %(n)s', { n: s.from }) : t('Days %(from)s–%(to)s', { from: s.from, to: s.to });
      return `<li><span class="cal-step-day">${days}</span><span class="cal-step-title">${escapeHtml(s.title)}</span><span class="cal-step-date">${when}</span></li>`;
    }).join('') : `<li class="cal-muted">${t('This protocol has no steps.')}</li>`;
  }
  ['template_id', 'start_date'].forEach((name) => runForm.elements[name].addEventListener('change', previewRun));

  runForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = runForm.elements;
    postJson('/calendar/protocols/runs', { id: f.id.value || null, template_id: f.template_id.value, start_date: f.start_date.value,
      label: f.label.value, experiment_id: f.experiment_id.value, notes: f.notes.value }).then((j) => {
      if (!j.ok) return showError($('#cal-run-error'), j.error || t("Couldn't save that."));
      runModal.close();
      loadProtocols();
      fetchAndRender();
      return undefined;
    });
  });
  $('#cal-run-delete').addEventListener('click', async () => {
    const id = runForm.elements.id.value;
    if (!id || !(await BioDialog.confirm(t('Remove this protocol run and all its steps from the calendar?'), { danger: true }))) return;
    postJson(`/calendar/protocols/runs/${id}/delete`, {}).then((j) => {
      if (!j.ok) return showError($('#cal-run-error'), j.error || t("Couldn't remove it."));
      runModal.close();
      loadProtocols();
      fetchAndRender();
      return undefined;
    });
  });

  $('#cal-templates-btn').addEventListener('click', () => openTemplates(null));

  function openTemplates(id, message) {
    loadProtocols().then(() => {
      const first = id ? protocols.templates.find((t) => t.id === id) : protocols.templates[0];
      editTemplate(first || null);
      showError($('#cal-tpl-error'), message || '');
      if (!tplModal.open) tplModal.showModal();
    });
  }

  function renderTemplateList() {
    $('#cal-tpl-list').innerHTML = protocols.templates.map((tpl) => `
      <li><button type="button" class="cal-tpl-item${tplSelected && tplSelected.id === tpl.id ? ' is-active' : ''}" data-tpl="${tpl.id}">
        <span class="cal-dot" style="background:${tpl.color}"></span><span>${escapeHtml(tpl.name)}</span><small>${t('%(steps)s steps · %(days)s d', { steps: tpl.steps.length, days: tpl.days })}</small>
      </button></li>`).join('') || `<li class="cal-muted">${t('None yet.')}</li>`;
  }
  $('#cal-tpl-list').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-tpl]');
    if (btn) editTemplate(protocols.templates.find((t) => t.id === Number(btn.dataset.tpl)));
  });
  $('#cal-tpl-new').addEventListener('click', () => editTemplate(null));

  function editTemplate(tpl) {
    tplSelected = tpl;
    const f = tplForm.elements;
    f.id.value = tpl ? tpl.id : '';
    f.name.value = tpl ? tpl.name : '';
    f.description.value = tpl ? tpl.description : '';
    setTplColor(tpl ? tpl.color : '#5856d6');
    const steps = tpl ? tpl.steps : [{ from: 0, to: 0, title: '' }];
    $('#cal-tpl-steps').innerHTML = '';
    steps.forEach(addStepRow);
    const editable = !tpl || tpl.editable;
    $$('.cal-tpl-edit input, .cal-tpl-edit button', tplForm).forEach((el) => { el.disabled = !editable; });
    tplForm.querySelector('[type="submit"]').hidden = !editable;
    $('#cal-tpl-delete').hidden = !(tpl && tpl.editable);
    showError($('#cal-tpl-error'), editable ? '' : t('Only the person who wrote this protocol can change it.'));
    renderTemplateList();
  }

  function addStepRow(step) {
    const li = document.createElement('li');
    li.className = 'cal-step-row';
    li.innerHTML = `<input type="number" name="step_from" value="${step.from}" min="-365" max="3650" aria-label="${t('From day')}">
      <input type="number" name="step_to" value="${step.to}" min="-365" max="3650" aria-label="${t('To day')}">
      <input type="text" name="step_title" value="${escapeHtml(step.title)}" placeholder="${t('e.g. Tamoxifen i.p.')}" aria-label="${t('Step')}">
      <button type="button" class="cal-icon-btn" data-remove-step aria-label="${t('Remove step')}">${svgIcon('close')}</button>`;
    $('#cal-tpl-steps').appendChild(li);
  }
  $('#cal-tpl-add-step').addEventListener('click', () => {
    const rows = $$('.cal-step-row', tplForm);
    const last = rows.length ? Number(rows[rows.length - 1].querySelector('[name="step_to"]').value) || 0 : -1;
    addStepRow({ from: last + 1, to: last + 1, title: '' });
    const inputs = $$('[name="step_title"]', tplForm);
    inputs[inputs.length - 1].focus();
  });
  $('#cal-tpl-steps').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-remove-step]');
    if (btn) btn.closest('li').remove();
  });
  $('#cal-tpl-steps').addEventListener('change', (e) => {
    // Typing a start day after the end day moves the end along.
    if (e.target.name !== 'step_from') return;
    const to = e.target.closest('li').querySelector('[name="step_to"]');
    if (Number(to.value) < Number(e.target.value)) to.value = e.target.value;
  });

  function setTplColor(color) {
    $('#cal-tpl-color').value = color;
    $$('#cal-tpl-swatches .biocal-color-swatch').forEach((s) => s.classList.toggle('is-selected', s.dataset.color.toLowerCase() === (color || '').toLowerCase()));
  }
  $$('#cal-tpl-swatches .biocal-color-swatch').forEach((s) => s.addEventListener('click', () => setTplColor(s.dataset.color)));

  tplForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = tplForm.elements;
    const steps = $$('.cal-step-row', tplForm).map((li) => ({
      from: Number(li.querySelector('[name="step_from"]').value) || 0,
      to: Number(li.querySelector('[name="step_to"]').value) || 0,
      title: li.querySelector('[name="step_title"]').value,
    }));
    postJson('/calendar/protocols/templates', { id: f.id.value || null, name: f.name.value, description: f.description.value,
      color: f.color.value, steps }).then((j) => {
      if (!j.ok) return showError($('#cal-tpl-error'), j.error || t("Couldn't save the protocol."));
      loadProtocols().then(() => editTemplate(protocols.templates.find((t) => t.id === j.template.id)));
      toast(t('Protocol saved.'));
      return undefined;
    });
  });
  $('#cal-tpl-delete').addEventListener('click', async () => {
    const id = tplForm.elements.id.value;
    if (!id || !(await BioDialog.confirm(t('Delete this protocol? Runs already started keep their steps.'), { danger: true }))) return;
    postJson(`/calendar/protocols/templates/${id}/delete`, {}).then((j) => {
      if (!j.ok) return showError($('#cal-tpl-error'), j.error || t("Couldn't delete it."));
      loadProtocols().then(() => editTemplate(protocols.templates[0] || null));
      return undefined;
    });
  });

  // ================================================================ equipment

  const equipModal = $('#cal-equip-modal');
  const equipForm = $('#cal-equip-form');
  $('#cal-equip-manage').addEventListener('click', () => { renderEquipManage(); editEquipment(null); showError($('#cal-equip-error'), ''); equipModal.showModal(); });

  function renderEquipManage() {
    $('#cal-equip-manage-list').innerHTML = (DATA.equipment || []).map((e) => `
      <li><span class="cal-dot" style="background:${e.color}"></span>
        <span class="cal-equip-name">${escapeHtml(e.name)}${e.location ? `<small>${escapeHtml(e.location)}</small>` : ''}</span>
        ${e.editable ? `<button type="button" class="btn btn-sm btn-ghost" data-equip-edit="${e.id}">${t('Edit')}</button>
          <button type="button" class="btn btn-sm" data-retire="${e.id}">${t('Remove')}</button>` : ''}</li>`).join('')
      || `<li class="cal-muted">${t('No instruments yet. Add the first below.')}</li>`;
  }
  // Edit: the form below takes the instrument's name, place and colour, and
  // Save changes it (a new name keeps its bookings).
  const equipSubmit = $('#cal-equip-submit');
  const addLabel = equipSubmit.innerHTML;
  function editEquipment(eq) {
    const f = equipForm.elements;
    f.id.value = eq ? eq.id : '';
    f.name.value = eq ? eq.name : '';
    f.location.value = eq ? eq.location || '' : '';
    if (eq && eq.color) f.color.value = eq.color;
    if (eq) equipSubmit.textContent = t('Save changes');
    else equipSubmit.innerHTML = addLabel;
    $('#cal-equip-cancel-edit').hidden = !eq;
    if (eq) f.name.focus();
  }
  $('#cal-equip-cancel-edit').addEventListener('click', () => { equipForm.reset(); editEquipment(null); });
  $('#cal-equip-manage-list').addEventListener('click', async (e) => {
    const edit = e.target.closest('[data-equip-edit]');
    if (edit) {
      editEquipment((DATA.equipment || []).find((x) => x.id === Number(edit.dataset.equipEdit)));
      return;
    }
    const btn = e.target.closest('[data-retire]');
    if (!btn || !(await BioDialog.confirm(t('Remove this instrument? Its past bookings stay on the calendar.'), { danger: true }))) return;
    postJson(`/calendar/equipment/${btn.dataset.retire}/delete`, {}).then((j) => {
      if (!j.ok) return showError($('#cal-equip-error'), j.error || t("Couldn't remove it."));
      DATA.equipment = DATA.equipment.filter((x) => x.id !== Number(btn.dataset.retire));
      renderEquipManage();
      renderEquipment();
      return undefined;
    });
  });
  equipForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = equipForm.elements;
    const renamed = !!f.id.value;
    postJson('/calendar/equipment', { id: f.id.value || undefined, name: f.name.value, location: f.location.value, color: f.color.value }).then((j) => {
      if (!j.ok) return showError($('#cal-equip-error'), j.error || t("Couldn't save it."));
      DATA.equipment = (DATA.equipment || []).filter((x) => x.id !== j.equipment.id).concat([j.equipment])
        .sort((a, b) => a.name.localeCompare(b.name));
      equipForm.reset();
      editEquipment(null);
      showError($('#cal-equip-error'), '');
      renderEquipManage();
      renderEquipment();
      if (renamed && calendar) fetchAndRender();   // its bookings, under the new name
      return undefined;
    });
  });

  // ================================================================ phone feed

  const feedModal = $('#cal-feed-modal');
  let feedSeq = 0;
  // Only the newest answer counts: opening the dialog and pressing a button
  // straight away must not let the slower first answer undo the second.
  const feedCall = (promise) => { const seq = ++feedSeq; return promise.then((j) => { if (seq === feedSeq) showFeed(j); }); };
  $('#cal-feed-btn').addEventListener('click', () => {
    feedCall(fetch('/calendar/phone-feed').then((r) => r.json()));
    feedModal.showModal();
  });
  function showFeed(j) {
    if (!j || j.ok === false) return showError($('#cal-feed-error'), (j && j.error) || t("Couldn't load your link."));
    showError($('#cal-feed-error'), '');
    const on = !!j.url;
    $('#cal-feed-on').hidden = !on;
    $('#cal-feed-url').value = j.url || '';
    $('#cal-feed-create').hidden = on;
    $('#cal-feed-reset').hidden = !on;
    $('#cal-feed-stop').hidden = !on;
    const radio = feedModal.querySelector(`input[name="feed_scope"][value="${j.scope || 'mine'}"]`);
    if (radio) radio.checked = true;
    return undefined;
  }
  const feedScope = () => (feedModal.querySelector('input[name="feed_scope"]:checked') || {}).value || 'mine';
  $('#cal-feed-create').addEventListener('click', () => feedCall(postJson('/calendar/phone-feed', { action: 'create', scope: feedScope() })));
  $$('input[name="feed_scope"]', feedModal).forEach((r) => r.addEventListener('change', () => {
    if ($('#cal-feed-url').value) feedCall(postJson('/calendar/phone-feed', { action: 'create', scope: feedScope() }));
  }));
  $('#cal-feed-reset').addEventListener('click', async () => {
    if (!(await BioDialog.confirm(t('Make a new link? The old one stops working, so calendars subscribed to it need the new one.'), { danger: true }))) return;
    feedCall(postJson('/calendar/phone-feed', { action: 'reset', scope: feedScope() }));
  });
  $('#cal-feed-stop').addEventListener('click', async () => {
    if (!(await BioDialog.confirm(t('Stop sharing? Calendars subscribed to the link stop updating.'), { danger: true }))) return;
    feedCall(postJson('/calendar/phone-feed', { action: 'stop' }));
  });
  $('#cal-feed-copy').addEventListener('click', () => {
    const input = $('#cal-feed-url');
    const done = () => toast(t('Link copied.'));
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(input.value).then(done, () => { input.select(); document.execCommand('copy'); done(); });
    } else {
      input.select();
      document.execCommand('copy');
      done();
    }
  });

  // ================================================================ Mac Calendar

  // The desktop app on a Mac only (app/mac_calendar.py): BioManager's
  // calendar in the Mac's own Calendar app, and so in iCloud or Google.
  const macModal = $('#cal-mac-modal');
  if (macModal) {
    let macSeq = 0;
    let macState = null;
    const macBusy = (on) => $$('.record-dialog-foot .btn', macModal).forEach((b) => { b.disabled = on; });
    const macCall = (promise) => {
      const seq = ++macSeq;
      macBusy(true);
      return promise.then((j) => { if (seq === macSeq) { macBusy(false); showMac(j); } return j; });
    };
    const macPost = (body) => macCall(postJson('/calendar/mac', body));
    $('#cal-mac-btn').addEventListener('click', () => {
      macCall(fetch('/calendar/mac').then((r) => r.json()).catch(() => ({ ok: false })));
      macModal.showModal();
    });
    const macScope = () => (macModal.querySelector('input[name="mac_scope"]:checked') || {}).value || 'mine';
    function macTargets(j) {
      const select = $('#cal-mac-target');
      const keep = select.value;
      select.textContent = '';
      const group = (label, options) => {
        if (!options.length) return;
        const og = document.createElement('optgroup');
        og.label = label;
        options.forEach(([value, text]) => { const o = document.createElement('option'); o.value = value; o.textContent = text; og.appendChild(o); });
        select.appendChild(og);
      };
      group(t('Calendars called BioManager'), (j.calendars || []).map((c) => [`cal:${c.id}`, `${c.title} · ${c.account}`]));
      group(t('A new BioManager calendar in'), (j.accounts || []).map((a) => [`new:${a.id}`, a.title]));
      const values = Array.from(select.options).map((o) => o.value);
      const icloud = (j.accounts || []).find((a) => /icloud/i.test(a.title));
      select.value = values.includes(keep) ? keep : (j.calendars || []).length ? values[0] : icloud ? `new:${icloud.id}` : (values[0] || '');
      return values.length;
    }
    function showMac(j) {
      if (!j || j.ok === false) {
        if (j && j.access) macState = j;
        return showError($('#cal-mac-error'), (j && j.error) || t('The server did not answer. Check the connection and try again.'));
      }
      macState = j;
      const setup = j.setup;
      const granted = j.access === 'granted';
      $('#cal-mac-ask').hidden = j.access !== 'ask';
      $('#cal-mac-denied').hidden = granted || j.access === 'ask';
      $('#cal-mac-allow').hidden = j.access !== 'ask';
      $('#cal-mac-on').hidden = !setup;
      $('#cal-mac-scope').hidden = !(setup || granted);
      $('#cal-mac-choose').hidden = !!setup || !granted;
      $('#cal-mac-stop').hidden = !setup;
      $('#cal-mac-now').hidden = !setup;
      $('#cal-mac-start').hidden = !!setup || !granted;
      let problem = '';
      if (setup) {
        const values = { title: setup.title, account: setup.account, who: setup.who };
        $('#cal-mac-where').textContent = setup.mine ? t('Kept up to date in %(title)s (%(account)s).', values)
          : t('Kept up to date in %(title)s (%(account)s) for %(who)s.', values);
        $('#cal-mac-last').textContent = setup.last_ok
          ? t('Last updated %(when)s · %(n)s events.', { when: new Date(setup.last_ok).toLocaleString(LOCALE), n: setup.count || 0 })
          : t('Not updated yet.');
        problem = setup.error || '';
      } else if (granted && !macTargets(j)) {
        problem = t('No calendar accounts on this Mac. Turn on iCloud, or add an account in System Settings → Internet Accounts.');
        $('#cal-mac-start').hidden = true;
      }
      const radio = macModal.querySelector(`input[name="mac_scope"][value="${(setup && setup.scope) || macScope()}"]`);
      if (radio) radio.checked = true;
      showError($('#cal-mac-error'), problem);
      return undefined;
    }
    $('#cal-mac-allow').addEventListener('click', () => macPost({ action: 'allow' }));
    $('#cal-mac-start').addEventListener('click', () => macPost({ action: 'start', scope: macScope(), target: $('#cal-mac-target').value }));
    $('#cal-mac-now').addEventListener('click', () => macPost({ action: 'now' }).then((j) => {
      if (j && j.ok && j.setup && !j.setup.error) toast(t('Updated.'));
    }));
    $$('input[name="mac_scope"]', macModal).forEach((r) => r.addEventListener('change', () => {
      if (macState && macState.setup) macPost({ action: 'scope', scope: macScope() });
    }));
    $('#cal-mac-stop').addEventListener('click', async () => {
      const setup = (macState && macState.setup) || {};
      const question = setup.created
        ? t('Stop, and remove the BioManager calendar from %(account)s?', { account: setup.account })
        : t('Stop, and empty the calendar %(title)s?', { title: setup.title });
      if (!(await BioDialog.confirm(question, { danger: true }))) return;
      macPost({ action: 'stop' });
    });
  }

  // ================================================================ hover card

  const hoverCard = document.createElement('div');
  hoverCard.className = 'biocal-hover-card cal-hover';
  hoverCard.hidden = true;
  document.body.appendChild(hoverCard);
  let hoverShow = null;
  let hoverHide = null;

  function showHover(item, rect) {
    clearTimeout(hoverHide);
    const painted = paint(item);
    const raw = item.raw || {};
    const s = new Date(item.start), e = new Date(item.end);
    const when = item.isAllday
      ? (ymd(s) === ymd(e) ? s.toLocaleDateString(LOCALE, { weekday: 'long', month: 'long', day: 'numeric' })
        : `${s.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })} – ${e.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })}`)
      : `${s.toLocaleDateString(LOCALE, { weekday: 'short', month: 'short', day: 'numeric' })}, ${fmtTime(item.start)} – ${fmtTime(item.end)}`;
    const extra = [raw.repeat ? repeatText(raw.repeat, item) : '', raw.location || ''].filter(Boolean).join(' · ');
    hoverCard.innerHTML = `
      <div class="biocal-hover-strip" style="background:${painted.borderColor}"></div>
      <div class="biocal-hover-body">
        <div class="biocal-hover-title">${escapeHtml(item.title || t('(untitled)'))}</div>
        <div class="biocal-hover-date">${escapeHtml(when)}${extra ? ' · ' + escapeHtml(extra) : ''}</div>
        <div class="biocal-hover-tag"><span class="biocal-hover-dot" style="background:${painted.borderColor}"></span>
          ${escapeHtml((raw.group && t(raw.group)) || LAYER_NAMES[layerOf(item)] || '')}${raw.href ? ' · ' + t('click to open') : ''}</div>
        ${item.body ? `<div class="biocal-hover-notes">${escapeHtml(item.body)}</div>` : ''}
      </div>`;
    hoverCard.hidden = false;
    const width = 320;
    let left = rect.right + 8;
    let top = rect.top;
    if (left + width > window.innerWidth - 12) left = Math.max(8, rect.left - width - 8);
    if (top + 180 > window.innerHeight - 12) top = Math.max(8, window.innerHeight - 200);
    hoverCard.style.left = `${left}px`;
    hoverCard.style.top = `${top}px`;
  }
  function hideHover() { clearTimeout(hoverShow); hoverHide = setTimeout(() => { hoverCard.hidden = true; }, 150); }
  // The grid shows an item on a click (the quick look); the list on hover.
  [listHost].forEach((host) => {
    host.addEventListener('mouseover', (e) => {
      const el = e.target.closest('[data-item-id]');
      if (!el || !host.contains(el)) return;
      const item = findItem(el.dataset.itemId);
      if (!item) return;
      clearTimeout(hoverShow);
      const rect = el.getBoundingClientRect();
      hoverShow = setTimeout(() => showHover(item, rect), 180);
    });
    host.addEventListener('mouseout', (e) => { if (e.target.closest('[data-item-id]')) hideHover(); });
  });
  hoverCard.addEventListener('mouseenter', () => clearTimeout(hoverHide));
  hoverCard.addEventListener('mouseleave', hideHover);

  // ================================================================ connected calendars

  const subsModal = $('#biocal-subs-modal');
  const subsListEl = $('#biocal-subs-list');
  const subsAddForm = $('#biocal-subs-add');
  const googleStatusEl = $('#biocal-google-status');

  $('#biocal-subs-btn').addEventListener('click', () => { loadSubscriptions(); loadGoogleStatus(); subsModal.showModal(); });

  subsAddForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const fd = new FormData(subsAddForm);
    postJson('/calendar/subscriptions', { name: fd.get('name'), url: fd.get('url'), color: fd.get('color') }).then((j) => {
      if (j && j.ok) { subsAddForm.reset(); loadSubscriptions(); fetchAndRender(); } else toast(t("Couldn't add it: %(error)s", { error: (j && j.error) || t('unknown error') }));
    });
  });

  function loadSubscriptions() {
    fetch('/calendar/subscriptions').then((r) => r.json()).then((j) => {
      const subs = (j && j.subscriptions) || [];
      if (!subs.length) {
        subsListEl.innerHTML = `<li class="biocal-subs-empty">${t('No subscriptions yet. Paste an iCal URL above to add one.')}</li>`;
        return;
      }
      subsListEl.innerHTML = subs.map((s) => `
        <li class="biocal-sub-row" data-id="${s.id}">
          <span class="biocal-sub-swatch" style="background:${s.color}"></span>
          <div class="biocal-sub-main">
            <div class="biocal-sub-name">${escapeHtml(s.name)}</div>
            <div class="biocal-sub-url">${escapeHtml(s.url)}</div>
            <div class="biocal-sub-meta">${t('Last fetched: %(when)s', { when: s.last_fetched_at ? new Date(s.last_fetched_at).toLocaleString(LOCALE) : '—' })}</div>
            ${s.last_error ? `<div class="biocal-sub-error">${escapeHtml(s.last_error)}</div>` : ''}
          </div>
          <label class="biocal-sub-toggle" title="${t('Show or hide')}"><input type="checkbox" data-action="toggle" ${s.enabled ? 'checked' : ''}></label>
          <button type="button" class="biocal-btn" data-action="refresh" title="${t('Refresh now')}">${svgIcon('refresh')}</button>
          <button type="button" class="biocal-btn" data-action="delete" title="${t('Remove')}">${svgIcon('trash')}</button>
        </li>`).join('');
    });
  }
  subsListEl.addEventListener('change', (e) => {
    const row = e.target.closest('.biocal-sub-row');
    if (!row || e.target.dataset.action !== 'toggle') return;
    postJson(`/calendar/subscriptions/${row.dataset.id}`, { enabled: e.target.checked }).then(() => fetchAndRender());
  });
  subsListEl.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action]');
    const row = e.target.closest('.biocal-sub-row');
    if (!btn || !row || btn.dataset.action === 'toggle') return;
    if (btn.dataset.action === 'refresh') {
      postJson(`/calendar/subscriptions/${row.dataset.id}/refresh`, {}).then(() => { loadSubscriptions(); fetchAndRender(); });
    } else if (btn.dataset.action === 'delete' && (await BioDialog.confirm(t('Remove this subscription?'), { danger: true }))) {
      postJson(`/calendar/subscriptions/${row.dataset.id}/delete`, {}).then(() => { loadSubscriptions(); fetchAndRender(); });
    }
  });

  function loadGoogleStatus() {
    fetch('/calendar/google/status').then((r) => (r.ok ? r.json() : null)).then((j) => {
      if (!j) {
        googleStatusEl.innerHTML = `<span class="muted-inline">${t("Google Calendar isn't set up on this server.")}</span>`;
      } else if (j.connected) {
        googleStatusEl.innerHTML = `
          <div class="biocal-google-connected">
            <div><strong>${t('Connected as %(email)s', { email: escapeHtml(j.email || t('Google account')) })}</strong>
              <div class="muted-inline">${t('Last sync: %(when)s', { when: j.last_synced_at ? new Date(j.last_synced_at).toLocaleString(LOCALE) : '—' })}</div></div>
            <button type="button" class="biocal-btn" data-google="refresh">${t('Refresh')}</button>
            <button type="button" class="biocal-btn" data-google="disconnect">${t('Disconnect')}</button>
          </div>`;
      } else if (j.configured) {
        googleStatusEl.innerHTML = `<a href="/calendar/google/connect" class="biocal-btn is-primary biocal-google-connect">${t('Connect Google Calendar')}</a>
          <div class="muted-inline cal-gap">${t('Only read access to your events is requested.')}</div>`;
      } else {
        googleStatusEl.innerHTML = `<div class="biocal-google-unconfigured">${t("Google Calendar isn't configured on this server yet. An admin sets <code>GOOGLE_OAUTH_CLIENT_ID</code> and <code>GOOGLE_OAUTH_CLIENT_SECRET</code> in <code>.env</code>.")}</div>`;
      }
    });
  }
  googleStatusEl.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-google]');
    if (!btn) return;
    if (btn.dataset.google === 'disconnect' && !(await BioDialog.confirm(t('Disconnect Google Calendar?'), { danger: true }))) return;
    postJson(`/calendar/google/${btn.dataset.google}`, {}).then(() => { loadGoogleStatus(); fetchAndRender(); });
  });

  // ================================================================ helpers

  function postJson(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then((r) => r.json().catch(() => ({ ok: r.ok })).then((j) => Object.assign({ ok: r.ok }, j)))
      .catch(() => ({ ok: false, error: t('The server did not answer. Check the connection and try again.') }));
  }

  function toast(message) { window.BiomanagerShell.toast(message); }

  function startOfDay(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; }
  function firstOfMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }
  function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
  function pad(n) { return String(n).padStart(2, '0'); }
  function ymd(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function toLocalInput(d) {
    const dt = d instanceof Date ? d : new Date(d);
    return `${ymd(dt)}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
  }
  function tuiToDate(v) {
    if (!v) return new Date();
    if (v instanceof Date) return v;
    if (v.toDate) return v.toDate();
    return new Date(v);
  }
  // Wall-clock ISO ("YYYY-MM-DDTHH:mm:ss"), so a drag never drifts by the time zone.
  function tuiToIso(v) {
    const dt = tuiToDate(v);
    return `${toLocalInput(dt)}:${pad(dt.getSeconds())}`;
  }
  function fmtTime(iso) { return iso ? new Date(iso).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' }) : ''; }
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  renderEquipment();
  renderMini();
  refreshTitle();
})();
