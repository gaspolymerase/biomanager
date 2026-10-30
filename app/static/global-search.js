/* Global Cmd+K search palette.
 *
 * Triggered by Cmd+K (Ctrl+K on Windows/Linux) or by clicking the topbar
 * search input. Three kinds of row, in this order:
 *   - Actions: every place in the sidebar (Go to …), the calendar's Board,
 *     today's log, hiding the sidebar; all of them before anything is
 *     typed, the ones that match after.
 *   - Records: /search?q=…, grouped by kind.
 *   - Ask the user guide: the words typed, searched in the guide on the
 *     BioManager website (its ?q=).
 * Arrow keys move, Enter opens the row, Esc closes.
 */
(function () {
  'use strict';

  let overlayEl = null;
  let inputEl = null;
  let resultsEl = null;
  let currentResults = [];
  let activeIndex = 0;
  let pendingFetch = null;
  let debounceTimer = null;
  let flat = [];          // the rows in the order shown: what the arrows step through

  // What can be done from here, read from the page: the sidebar's places
  // and Help's menu, then a few commands of the palette's own.
  function actions() {
    const seen = new Set();
    const out = [];
    document.querySelectorAll('.rail a.rail-item[href], .rail .rail-pop a.menu-item[href]').forEach((a) => {
      const label = (a.dataset.label || a.textContent).replace(/\s+/g, ' ').trim();
      const href = a.getAttribute('href');
      if (!label || !href || href.startsWith('#') || seen.has(href)) return;
      seen.add(href);
      const use = a.querySelector('svg use');
      const icon = use ? (use.getAttribute('href') || '').split('#').pop().replace(/^i-/, '') : 'arrow-right';
      out.push({ kind: 'action', label: t('Go to %(place)s', { place: label }), words: label, icon, url: href,
                 newTab: a.target === '_blank' });
    });
    if (document.querySelector('.rail a.rail-item[href^="/calendar"]')) {
      out.push({ kind: 'action', label: t('Your to-dos on the Board'), words: 'board kanban todo to-do list', icon: 'kanban', url: '/calendar?view=board' });
    }
    if (document.querySelector('.rail a.rail-item[href^="/notebook"]')) {
      out.push({ kind: 'action', label: t("Today's log"), words: 'notebook daily today log', icon: 'calendar-clock', url: '/notebook/today' });
    }
    out.push({ kind: 'action', label: t('Hide or show the sidebar'), words: 'sidebar rail', icon: 'sidebar',
               run: () => window.BiomanagerShell && window.BiomanagerShell.toggleRail() });
    return out;
  }

  function guideUrl() {
    const link = document.querySelector('a[data-milestone="guide"]');
    return link ? link.getAttribute('href') : 'https://biomanager.org/guide.html';
  }

  const TYPE_LABEL = {
    mouse: t('Mouse'), cage: t('Cage'), litter: t('Litter'), experiment: t('Experiment'), strain: t('Strain'),
    vial: t('Fly / worm'), tank: t('Tank'), 'fish-line': t('Fish line'), clutch: t('Clutch'), organism: t('Animal database'),
    plasmid: t('Plasmid'), order: t('Order'), sample: t('Sample'), reagent: t('Reagent'), chemical: t('Chemical'), antibody: t('Antibody'), virus: t('Virus'), primer: t('Primer'), 'cell-line': t('Cell line'), 'glycerol-stock': t('Glycerol stock'), item: t('Item'),
    page: t('Notebook'),
  };

  function build() {
    if (overlayEl) return;
    overlayEl = document.createElement('div');
    overlayEl.className = 'cmdk-overlay';
    overlayEl.hidden = true;
    overlayEl.innerHTML = `
      <div class="cmdk-backdrop"></div>
      <div class="cmdk-card" role="dialog" aria-label="${escapeHtml(t('Global search'))}">
        <div class="cmdk-input-row">
          <svg class="icon cmdk-icon" aria-hidden="true"><use href="/static/icons.svg#search"></use></svg>
          <input id="cmdk-input" type="text" placeholder="${escapeHtml(t('Search, go somewhere, or ask the guide…'))}" autocomplete="off">
          <kbd class="cmdk-kbd">esc</kbd>
        </div>
        <div id="cmdk-results" class="cmdk-results"></div>
        <div class="cmdk-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> ${escapeHtml(t('to navigate'))}</span>
          <span><kbd>↵</kbd> ${escapeHtml(t('to open'))}</span>
          <span><kbd>esc</kbd> ${escapeHtml(t('to close'))}</span>
        </div>
      </div>
    `;
    document.body.appendChild(overlayEl);

    inputEl = overlayEl.querySelector('#cmdk-input');
    resultsEl = overlayEl.querySelector('#cmdk-results');
    overlayEl.querySelector('.cmdk-backdrop').addEventListener('click', close);
    inputEl.addEventListener('input', onInput);
    inputEl.addEventListener('keydown', onKey);
  }

  function open(seed) {
    build();
    overlayEl.hidden = false;
    inputEl.value = seed || '';
    activeIndex = 0;
    currentResults = [];
    render();
    setTimeout(() => inputEl.focus(), 0);
    if (seed) runFetch(seed);
  }

  function close() {
    if (!overlayEl || overlayEl.hidden) return;
    overlayEl.hidden = true;
    // Lets the shell know a pending "new tab" request was abandoned.
    document.dispatchEvent(new CustomEvent('biomanager:palette-closed'));
  }

  function onInput() {
    const q = inputEl.value.trim();
    if (debounceTimer) clearTimeout(debounceTimer);
    activeIndex = 0;
    if (!q) {
      currentResults = [];
      render();
      return;
    }
    render();           // the actions that match, at once; the records as they come
    debounceTimer = setTimeout(() => runFetch(q), 120);
  }

  function runFetch(q) {
    if (pendingFetch && pendingFetch.abort) pendingFetch.abort();
    const controller = new AbortController();
    pendingFetch = controller;
    fetch(`/search?q=${encodeURIComponent(q)}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || !data.ok) return;
        currentResults = data.results || [];
        activeIndex = 0;
        render();
      })
      .catch(() => {});
  }

  function rowHtml(row, idx, type, typeLabel, iconName) {
    const active = idx === activeIndex ? 'is-active' : '';
    const lead = iconName
      ? `<span class="cmdk-row-icon"><svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${escapeAttr(iconName)}"></use></svg></span>`
      : `<span class="cmdk-row-type cmdk-row-type-${type}">${escapeHtml(typeLabel)}</span>`;
    return `
      <a href="${escapeAttr(row.url || '#')}" class="cmdk-row cmdk-row-${row.kind || 'result'} ${active}" data-idx="${idx}"${row.newTab ? ' target="_blank" rel="noopener"' : ''}>
        ${lead}
        <span class="cmdk-row-text">
          <span class="cmdk-row-label">${escapeHtml(row.label)}</span>
          <span class="cmdk-row-sublabel">${escapeHtml(row.sublabel || '')}</span>
        </span>
        ${row.kind === 'ask' ? '<kbd class="cmdk-kbd">↗</kbd>' : ''}
      </a>`;
  }

  function render() {
    const q = inputEl ? inputEl.value.trim() : '';
    const words = q.toLowerCase();
    const acts = actions().filter((a) => !words || a.label.toLowerCase().includes(words) || (a.words || '').toLowerCase().includes(words));
    const shownActions = words ? acts.slice(0, 5) : acts.slice(0, 9);
    flat = [];
    let html = '';
    if (shownActions.length) {
      html += `<div class="cmdk-group-label">${escapeHtml(words ? t('Actions') : t('Go to'))}</div>`;
      shownActions.forEach((a) => { html += rowHtml(a, flat.length, 'action', '', a.icon); flat.push(a); });
    }
    // Records, grouped by kind, in the search's order.
    const groups = {};
    currentResults.forEach((item) => {
      if (!groups[item.type]) groups[item.type] = [];
      groups[item.type].push(item);
    });
    const known = ['mouse', 'cage', 'litter', 'experiment', 'strain', 'vial', 'tank', 'fish-line', 'clutch', 'organism', 'plasmid', 'order', 'sample', 'reagent', 'antibody', 'virus', 'item', 'page'];
    // Any other type the server sends still shows, after the known ones.
    const order = known.concat(Object.keys(groups).filter((k) => !known.includes(k)));
    order.forEach((type) => {
      if (!groups[type]) return;
      html += `<div class="cmdk-group-label">${escapeHtml(TYPE_LABEL[type] || type)}</div>`;
      groups[type].forEach((item) => {
        const row = Object.assign({ kind: 'result' }, item);
        html += rowHtml(row, flat.length, item.type, TYPE_LABEL[item.type] || item.type);
        flat.push(row);
      });
    });
    if (words) {
      const ask = { kind: 'ask', label: t('Ask the user guide: “%(q)s”', { q }), sublabel: t('How to do it, step by step, on the BioManager website'),
                    url: `${guideUrl()}?q=${encodeURIComponent(q)}`, newTab: true };
      html += `<div class="cmdk-group-label">${escapeHtml(t('Ask'))}</div>`;
      html += rowHtml(ask, flat.length, 'ask', '', 'sparkle');
      flat.push(ask);
    }
    if (!flat.length) html = `<div class="cmdk-empty">${escapeHtml(t('No matches.'))}</div>`;
    if (activeIndex >= flat.length) activeIndex = Math.max(0, flat.length - 1);
    resultsEl.innerHTML = html;
    resultsEl.querySelectorAll('.cmdk-row').forEach((row) => {
      row.addEventListener('mouseenter', () => {
        activeIndex = parseInt(row.dataset.idx, 10);
        refreshActive();
      });
      row.addEventListener('click', (event) => {
        const picked = flat[parseInt(row.dataset.idx, 10)];
        if (picked && picked.run) { event.preventDefault(); close(); picked.run(); }
      });
    });
  }

  function refreshActive() {
    resultsEl.querySelectorAll('.cmdk-row').forEach((row) => {
      const idx = parseInt(row.dataset.idx, 10);
      row.classList.toggle('is-active', idx === activeIndex);
      if (idx === activeIndex) row.scrollIntoView({ block: 'nearest' });
    });
  }

  function onKey(event) {
    if (event.key === 'Escape') { close(); event.preventDefault(); return; }
    if (event.key === 'ArrowDown') {
      activeIndex = Math.min(flat.length - 1, activeIndex + 1);
      refreshActive();
      event.preventDefault();
      return;
    }
    if (event.key === 'ArrowUp') {
      activeIndex = Math.max(0, activeIndex - 1);
      refreshActive();
      event.preventDefault();
      return;
    }
    if (event.key === 'Enter') {
      const row = flat[activeIndex];
      event.preventDefault();
      if (!row) return;
      if (row.run) { close(); row.run(); return; }
      if (row.newTab) { window.open(row.url, '_blank', 'noopener'); close(); return; }
      if (row.url) window.location.href = row.url;
      return;
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }
  const escapeAttr = escapeHtml;

  // Activate Cmd+K (or Ctrl+K) globally. Also enables the topbar search input
  // (which is rendered disabled in base.html) by hijacking click/focus.
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      open();
    }
  });

  document.addEventListener('DOMContentLoaded', () => {
    const topbarInput = document.getElementById('app-global-search');
    if (topbarInput) {
      topbarInput.disabled = false;
      topbarInput.placeholder = t('Search Cmd+K');
      topbarInput.readOnly = true;
      topbarInput.addEventListener('focus', (event) => {
        event.target.blur();
        open();
      });
      topbarInput.addEventListener('click', (event) => {
        event.preventDefault();
        open();
      });
    }
  });

  window.BiomanagerSearch = { open, close };
})();
