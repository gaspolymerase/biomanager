/* "Used in": the notebook pages that @link a record ("@antibodies 12").
   An element [data-used-in="<type>"] fills itself from
   /notebook/backlinks/<type>/<number>: on load when it carries
   data-used-in-number (a record's own page), or each time its dialog opens
   (record-dialog:open), from the record's _number. */
(function () {
  const esc = BioHtml.escape;

  function fill(box, number) {
    const type = box.dataset.usedIn;
    const list = box.querySelector('[data-used-in-list]');
    const code = box.querySelector('[data-used-in-code]');
    if (!number) { box.hidden = true; return; }
    box.hidden = false;
    if (code) code.textContent = `@${type} ${number}`;
    list.textContent = t('Looking…');
    fetch(`/notebook/backlinks/${encodeURIComponent(type)}/${number}`, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || !data.ok) { list.textContent = ''; return; }
        if (!data.items.length) { list.textContent = t('No notebook page links it yet.'); return; }
        list.innerHTML = data.items.map((p) => `
          <a class="used-in-row" href="/notebook?tab=${p.tab_id}&page=${p.page_id}">
            <span class="used-in-title">${esc(p.page_title)}</span>
            <span class="used-in-when">${esc(p.tab_title)}${p.tab_title ? ' · ' : ''}${esc(p.updated_at.slice(0, 10))}</span>
          </a>`).join('');
      })
      .catch(() => { list.textContent = ''; });
  }

  document.querySelectorAll('[data-used-in]').forEach((box) => {
    if (box.dataset.usedInNumber) { fill(box, box.dataset.usedInNumber); return; }
    const dialog = box.closest('dialog');
    if (!dialog) return;
    dialog.addEventListener('record-dialog:open', (event) => {
      const { data, isNew } = event.detail || {};
      fill(box, isNew || !data ? '' : data._number);
    });
  });
})();
