/* Hover a place in the sidebar.
 *
 * With the sidebar collapsed to icons, a place's name comes up beside it.
 * A database shows more, open or collapsed: a look inside it (its count,
 * what is due soon, your latest records, its tabs) from /rail/peek
 * (app/rail_peek.py), so you can see how it stands, or go straight to a
 * tab, without leaving the page. Move onto the card to click in it.
 * A mouse only: a finger has no hover, and the phone's sidebar is a sheet.
 */
(function () {
  'use strict';

  const canHover = window.matchMedia('(hover: hover) and (pointer: fine)');
  const wide = window.matchMedia('(min-width: 768px)');
  const CACHE_MS = 60 * 1000;
  const cache = new Map();
  let card = null;
  let current = null;
  let showTimer = null;
  let hideTimer = null;
  let ticket = 0;

  const closed = () => document.body.dataset.rail === 'closed';

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function iconSvg(name, cls) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'icon' + (cls ? ' ' + cls : ''));
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(ns, 'use');
    use.setAttribute('href', '/static/icons.svg#' + name);
    svg.appendChild(use);
    return svg;
  }

  function ensureCard() {
    if (card) return card;
    card = el('div', 'rail-peek pop-surface');
    card.setAttribute('role', 'tooltip');
    card.hidden = true;
    card.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    card.addEventListener('mouseleave', scheduleHide);
    document.body.appendChild(card);
    return card;
  }

  function place(item) {
    const rail = item.closest('.rail');
    const railBox = rail.getBoundingClientRect();
    const box = item.getBoundingClientRect();
    card.style.left = Math.round(railBox.right + 8) + 'px';
    const h = card.offsetHeight;
    const top = card.classList.contains('is-label')
      ? box.top + box.height / 2 - h / 2
      : box.top - 6;
    card.style.top = Math.round(Math.max(8, Math.min(top, window.innerHeight - h - 8))) + 'px';
  }

  function labelOf(item) {
    return item.dataset.label || (item.querySelector('.rail-label') || item).textContent.trim();
  }

  function showLabel(item) {
    ensureCard();
    card.className = 'rail-peek pop-surface is-label';
    card.replaceChildren(el('span', 'rail-peek-name', labelOf(item)));
    if (item.dataset.hint) card.appendChild(el('span', 'rail-peek-hint', item.dataset.hint));
    card.hidden = false;
    place(item);
  }

  function render(item, data) {
    card.className = 'rail-peek pop-surface is-db';
    const head = el('a', 'rail-peek-head');
    head.href = data.url || item.getAttribute('href') || '#';
    if (data.icon) head.appendChild(iconSvg(data.icon, 'rail-peek-icon'));
    const title = el('span', 'rail-peek-title');
    title.appendChild(el('span', 'rail-peek-name', data.label || labelOf(item)));
    title.appendChild(el('span', 'rail-peek-sub', data.sub || ''));
    head.appendChild(title);
    head.appendChild(el('span', 'rail-peek-value num', String(data.value ?? '')));
    const parts = [head];

    if (data.tabs && data.tabs.length) {
      const tabs = el('div', 'rail-peek-tabs');
      data.tabs.forEach((tab) => {
        const a = el('a', 'rail-peek-tab', tab.label);
        a.href = tab.url;
        tabs.appendChild(a);
      });
      parts.push(tabs);
    }

    const words = data.words || {};
    if (data.due) {
      const due = el('div', 'rail-peek-section');
      due.appendChild(el('div', 'rail-peek-label', words.due || ''));
      if (!data.due.length) due.appendChild(el('div', 'rail-peek-empty', words.nothing_due || ''));
      data.due.forEach((row) => {
        const line = el(row.url ? 'a' : 'div', 'rail-peek-row');
        if (row.url) line.href = row.url;
        line.appendChild(el('span', 'rail-peek-row-title', row.title));
        line.appendChild(el('span', 'rail-peek-when' + (row.overdue ? ' is-overdue' : ''), row.when));
        due.appendChild(line);
      });
      parts.push(due);
    }

    if (data.recent && data.recent.length) {
      const recent = el('div', 'rail-peek-section');
      recent.appendChild(el('div', 'rail-peek-label', words.recent || ''));
      data.recent.forEach((row) => {
        const line = el('div', 'rail-peek-row');
        line.appendChild(el('span', 'rail-peek-row-title ident', row.title));
        line.appendChild(el('span', 'rail-peek-row-detail', row.detail || ''));
        recent.appendChild(line);
      });
      parts.push(recent);
    }
    card.replaceChildren(...parts);
    card.hidden = false;
    place(item);
  }

  function showDatabase(item) {
    const key = item.dataset.dbKey;
    const mine = ++ticket;
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < CACHE_MS) { render(item, hit.data); return; }
    if (closed()) showLabel(item);
    fetch('/rail/peek?key=' + encodeURIComponent(key), { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        cache.set(key, { at: Date.now(), data });
        if (mine === ticket && current === item) render(item, data);
      })
      .catch(() => {});
  }

  function show(item) {
    current = item;
    if (item.dataset.dbKey) showDatabase(item);
    else if (closed()) showLabel(item);
    else hide();
  }

  function hide() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    current = null;
    ticket++;
    if (card) card.hidden = true;
  }

  function scheduleHide() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, 160);
  }

  function init() {
    const rail = document.querySelector('.rail');
    if (!rail) return;
    // The card says what a native tooltip would, and both at once overlap.
    rail.querySelectorAll('.rail-item[title]').forEach((item) => {
      item.dataset.hint = item.getAttribute('title');
      item.removeAttribute('title');
    });
    rail.addEventListener('mouseover', (event) => {
      if (!canHover.matches || !wide.matches) return;
      const item = event.target.closest('.rail-item');
      if (!item || item === current) { if (item) clearTimeout(hideTimer); return; }
      if (!item.dataset.dbKey && !closed()) { scheduleHide(); return; }
      clearTimeout(hideTimer);
      clearTimeout(showTimer);
      // Quick between neighbours once a card is up; a moment's pause first.
      const wait = card && !card.hidden ? 60 : (item.dataset.dbKey ? 320 : 140);
      showTimer = setTimeout(() => show(item), wait);
    });
    rail.addEventListener('mouseleave', scheduleHide);
    rail.addEventListener('mouseout', (event) => {
      const item = event.target.closest('.rail-item');
      if (item && !item.contains(event.relatedTarget)) {
        clearTimeout(showTimer);
        if (current === item) scheduleHide();
      }
    });
    rail.addEventListener('dragstart', hide);
    document.addEventListener('pointerdown', (event) => {
      if (card && !card.hidden && !card.contains(event.target)) hide();
    });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') hide(); });
    rail.addEventListener('scroll', hide, { passive: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
