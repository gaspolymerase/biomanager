/* A mouse's brief, on hovering a link to it: the card the notebook shows
   over "@mouse 12" (frontend/src/extensions/MentionDecoration.js), from the
   same /notebook/lookup/mouse/<id> and /notebook/backlinks/mouse/<id>.
   A link opts in with data-mouse-peek="<mouse ID>"; data-mouse-peek-tag
   adds its custom tag, which the page leaves off where the person keeps
   custom tags hidden. Clicking the link still opens the mouse.
   An input opts in with an empty data-mouse-peek: the card is of the
   mouse whose ID is typed in it (a sample's source), its tag only while
   that is still the ID the page was drawn with. It shows on hovering, not
   while the input's suggestions are open or it is being typed in. */
(function () {
  const lookups = new Map(); // id -> Promise of the lookup, or null when not found
  const mentions = new Map(); // id -> Promise of the notebook pages naming it
  const MAX_ID = 2 ** 31 - 1; // app/formutil.py: no mouse has a bigger ID
  let card = null;
  let active = null;
  let hideTimer = null;
  let showTimer = null;

  const esc = BioHtml.escape;
  const isInput = (el) => el instanceof HTMLInputElement;

  // The mouse an input names: its digits, as the server reads them (007 is
  // mouse 7; app/inventory_items.py _mouse_number); words name none.
  function idOf(el) {
    if (!isInput(el)) return el.dataset.mousePeek;
    const typed = el.value.trim().replace(/^#/, '');
    return /^\d+$/.test(typed) ? typed.replace(/^0+(?=\d)/, '') : '';
  }

  function tagOf(el) {
    if (isInput(el) && el.value.trim() !== el.defaultValue.trim()) return '';
    return el.dataset.mousePeekTag || '';
  }

  function getJSON(url) {
    return fetch(url, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }

  function lookup(id) {
    if (Number(id) > MAX_ID) return Promise.resolve(null);
    if (!lookups.has(id)) lookups.set(id, getJSON(`/notebook/lookup/mouse/${encodeURIComponent(id)}`));
    return lookups.get(id);
  }

  function pagesNaming(id) {
    if (!mentions.has(id)) mentions.set(id, getJSON(`/notebook/backlinks/mouse/${encodeURIComponent(id)}`));
    return mentions.get(id);
  }

  function ensureCard(link) {
    if (!card) {
      card = document.createElement('div');
      card.className = 'mouse-peek';
      card.hidden = true;
      card.addEventListener('mouseenter', () => clearTimeout(hideTimer));
      card.addEventListener('mouseleave', hideSoon);
    }
    // In a dialog, in the dialog: the page behind a modal one is under it.
    const home = link.closest('dialog') || document.body;
    if (card.parentNode !== home) home.appendChild(card);
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
        [t('Age'), data.age_weeks == null ? '' : t('%(weeks)s weeks · %(days)s days', { weeks: data.age_weeks, days: data.age_days })],
        [t('Genotype'), data.genotype],
        [t('Status'), data.status],
        [t('Owner'), data.owner],
        [t('Cage'), data.cage_id],
        [t('Custom tag'), tagOf(link)],
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

  // An input whose suggestions are open (static/combobox.js) has the menu
  // where the card would go.
  const busy = (el) => isInput(el) && el.getAttribute('aria-expanded') === 'true';

  function show(link) {
    const id = idOf(link);
    if (!id || busy(link)) return;
    clearTimeout(hideTimer);
    active = link;
    ensureCard(link).hidden = false;
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

  function cancelShow() {
    clearTimeout(showTimer);
    showTimer = null;
  }

  function hide() {
    cancelShow();
    if (card) card.hidden = true;
    active = null;
  }

  function hideSoon() {
    cancelShow();
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, 150);
  }

  // A pointer resting on the link, not one passing over the sheet on its way.
  function showSoon(link) {
    clearTimeout(hideTimer);
    if (link === active) return;
    cancelShow();
    showTimer = setTimeout(() => { showTimer = null; show(link); }, 200);
  }
  document.addEventListener('pointerover', (event) => {
    if (event.pointerType !== 'mouse') return;
    const link = event.target.closest && event.target.closest('[data-mouse-peek]');
    if (link) showSoon(link);
  });
  // The pointer still on an input once it is typed in, or its menu closed:
  // moving it shows the card again.
  document.addEventListener('pointermove', (event) => {
    if (event.pointerType !== 'mouse' || active || showTimer) return;
    const el = event.target;
    if (isInput(el) && el.matches('[data-mouse-peek]') && !busy(el)) showSoon(el);
  });
  document.addEventListener('pointerout', (event) => {
    const link = event.target.closest && event.target.closest('[data-mouse-peek]');
    if (!link) return;
    if (event.relatedTarget && (link.contains(event.relatedTarget) || (card && card.contains(event.relatedTarget)))) return;
    hideSoon();
  });
  // Clicking or typing in an input is editing it: its menu, not the card.
  const editing = (event) => {
    const el = event.target;
    if (isInput(el) && el.matches('[data-mouse-peek]')) hide();
  };
  document.addEventListener('pointerdown', editing, true);
  document.addEventListener('input', editing, true);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && active) hide();
    else editing(event);
  }, true);
  document.addEventListener('focusin', (event) => {
    const link = event.target.closest && event.target.closest('[data-mouse-peek]');
    if (link && !isInput(link) && link.matches(':focus-visible')) show(link);
  });
  document.addEventListener('focusout', (event) => {
    const link = event.target.closest && event.target.closest('[data-mouse-peek]');
    if (link && !isInput(link)) hideSoon();
  });
  document.addEventListener('scroll', () => { if (active) hide(); }, true);
})();
