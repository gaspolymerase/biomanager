/* A mouse's brief, on hovering a link to it: the card the notebook shows
   over "@mouse 12" (frontend/src/extensions/MentionDecoration.js), from the
   same /notebook/lookup/mouse/<id> and /notebook/backlinks/mouse/<id>.
   A link opts in with data-mouse-peek="<mouse ID>"; data-mouse-peek-tag
   adds its custom tag, which the page leaves off where the person keeps
   custom tags hidden. Clicking the link still opens the mouse. */
(function () {
  const lookups = new Map(); // id -> Promise of the lookup, or null when not found
  const mentions = new Map(); // id -> Promise of the notebook pages naming it
  let card = null;
  let active = null;
  let hideTimer = null;
  let showTimer = null;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  function getJSON(url) {
    return fetch(url, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }

  function lookup(id) {
    if (!lookups.has(id)) lookups.set(id, getJSON(`/notebook/lookup/mouse/${encodeURIComponent(id)}`));
    return lookups.get(id);
  }

  function pagesNaming(id) {
    if (!mentions.has(id)) mentions.set(id, getJSON(`/notebook/backlinks/mouse/${encodeURIComponent(id)}`));
    return mentions.get(id);
  }

  function ensureCard() {
    if (card) return card;
    card = document.createElement('div');
    card.className = 'mouse-peek';
    card.hidden = true;
    card.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    card.addEventListener('mouseleave', hideSoon);
    document.body.appendChild(card);
    return card;
  }

  function place(link) {
    const rect = link.getBoundingClientRect();
    const box = card.getBoundingClientRect();
    const gap = 6;
    let top = rect.bottom + gap;
    if (top + box.height > window.innerHeight - 8) top = Math.max(8, rect.top - box.height - gap);
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - box.width - 8));
    card.style.top = `${top}px`;
    card.style.left = `${left}px`;
  }

  function row(key, value) {
    return `<div class="mouse-peek-row"><span class="mouse-peek-key">${esc(key)}</span><span class="mouse-peek-val">${esc(value)}</span></div>`;
  }

  function draw(link, id, data, pages) {
    const head = `
      <div class="mouse-peek-head">
        <span class="mouse-peek-tag">${esc(t('Mouse'))}</span>
        <span class="mouse-peek-id">#${esc(id)}</span>
      </div>`;
    let body;
    if (data === undefined) body = `<div class="mouse-peek-note">${esc(t('Loading…'))}</div>`;
    else if (!data || !data.ok) body = `<div class="mouse-peek-note">${esc(t('Not found.'))}</div>`;
    else {
      const rows = [
        [t('Sex'), data.gender],
        [t('Genotype'), data.genotype],
        [t('Status'), data.status],
        [t('Owner'), data.owner],
        [t('Cage'), data.cage_id],
        [t('Custom tag'), link.dataset.mousePeekTag],
      ].filter(([, v]) => v).map(([k, v]) => row(k, v)).join('');
      body = rows || `<div class="mouse-peek-note">${esc(t('No details yet.'))}</div>`;
    }
    let notebook = '';
    if (data && data.ok) {
      if (pages === undefined) notebook = `<div class="mouse-peek-note">${esc(t('Looking…'))}</div>`;
      else if (!pages || !pages.items || !pages.items.length) notebook = `<div class="mouse-peek-note">${esc(t('No notebook page links it yet.'))}</div>`;
      else {
        const n = pages.items.length;
        const list = pages.items.slice(0, 5).map((p) => `
          <a class="mouse-peek-page" href="/notebook?tab=${encodeURIComponent(p.tab_id)}&page=${encodeURIComponent(p.page_id)}" title="${esc(p.snippet || '')}">
            <span class="mouse-peek-page-title">${esc(p.page_title)}</span>
            ${p.snippet ? `<span class="mouse-peek-page-snippet">${esc(p.snippet)}</span>` : ''}
          </a>`).join('');
        notebook = `
          <div class="mouse-peek-pages-head">${esc(n === 1 ? t('In 1 notebook page') : t('In %(n)s notebook pages', { n }))}</div>
          ${list}
          ${n > 5 ? `<div class="mouse-peek-note">${esc(t('+%(n)s more', { n: n - 5 }))}</div>` : ''}`;
      }
      notebook = `<div class="mouse-peek-pages">${notebook}</div>`;
    }
    card.innerHTML = `${head}<div class="mouse-peek-body">${body}</div>${notebook}`;
    place(link);
  }

  function show(link) {
    const id = link.dataset.mousePeek;
    if (!id) return;
    clearTimeout(hideTimer);
    active = link;
    ensureCard().hidden = false;
    let data;
    let pages;
    draw(link, id, data, pages);
    lookup(id).then((d) => {
      data = d;
      if (active === link) draw(link, id, data, pages);
      if (!d || !d.ok) return;
      pagesNaming(id).then((p) => {
        pages = p;
        if (active === link) draw(link, id, data, pages);
      });
    });
  }

  function hide() {
    clearTimeout(showTimer);
    if (card) card.hidden = true;
    active = null;
  }

  function hideSoon() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, 150);
  }

  // A pointer resting on the link, not one passing over the sheet on its way.
  document.addEventListener('pointerover', (event) => {
    if (event.pointerType !== 'mouse') return;
    const link = event.target.closest && event.target.closest('[data-mouse-peek]');
    if (!link) return;
    clearTimeout(hideTimer);
    if (link === active) return;
    clearTimeout(showTimer);
    showTimer = setTimeout(() => show(link), 200);
  });
  document.addEventListener('pointerout', (event) => {
    const link = event.target.closest && event.target.closest('[data-mouse-peek]');
    if (!link) return;
    if (event.relatedTarget && (link.contains(event.relatedTarget) || (card && card.contains(event.relatedTarget)))) return;
    hideSoon();
  });
  document.addEventListener('focusin', (event) => {
    const link = event.target.closest && event.target.closest('[data-mouse-peek]');
    if (link && link.matches(':focus-visible')) show(link);
  });
  document.addEventListener('focusout', (event) => {
    if (event.target.closest && event.target.closest('[data-mouse-peek]')) hideSoon();
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && active) hide(); });
  document.addEventListener('scroll', () => { if (active) hide(); }, true);
})();
