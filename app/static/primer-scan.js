/* Find saved primers (GitHub #62): a switch on a plasmid's Primers card
   (and on a record's read-only page) that asks /plasmids/<id>/primer-sites
   which of the lab's saved primers bind this sequence and lists them. Where
   each binds opens the Sequence tab on that site (static/seq-viewer.js),
   which already draws the lab's primers that bind as arrows. */
(function () {
  const tr = (text, values) => (typeof window.t === 'function' ? window.t(text, values) : text);

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  // The site on the sequence: open its tab (if the page has tabs), wait for
  // the viewer to load, and pick the bases.
  function showSite(a, b) {
    const tab = document.querySelector('.plasmid-tab[data-tab="sequence"]');
    if (tab) tab.click();
    let tries = 0;
    const pick = () => {
      const ready = document.querySelector('[data-seq-viewer] .sv2-row');
      if (ready && window.BioSeqViewer) window.BioSeqViewer.select(a, b);
      else if (tries++ < 40) setTimeout(pick, 100);
    };
    pick();
  }
  document.addEventListener('click', (e) => {
    const go = e.target.closest('[data-scan-go]');
    if (!go) return;
    const [a, b] = go.dataset.scanGo.split('-').map(Number);
    showSite(a, b);
  });

  function matchText(h) {
    if (h.exact) return tr('exact match');
    return tr('3′ %(annealed)s bases match · %(tail)s-base 5′ tail', { annealed: h.annealed, tail: h.tail });
  }

  function mount(box) {
    const url = box.dataset.scanUrl;
    const toggle = box.querySelector('[data-scan-switch]');
    const panel = box.querySelector('[data-scan-panel]');
    const summary = box.querySelector('[data-scan-summary]');
    const table = box.querySelector('[data-scan-table]');
    const body = table.querySelector('tbody');

    function show(j) {
      const parts = [];
      if (!j.primers) {
        parts.push(tr('None of the %(n)s saved primers checked binds here.', { n: j.scanned }));
      } else if (j.primers === 1) {
        parts.push(tr('1 saved primer binds here, of %(n)s checked.', { n: j.scanned }));
      } else {
        parts.push(tr('%(found)s saved primers bind here, of %(n)s checked.', { found: j.primers, n: j.scanned }));
      }
      if (j.listed) parts.push(tr('Those listed above aren’t repeated.'));
      summary.textContent = parts.join(document.documentElement.lang.startsWith('zh') ? '' : ' ');
      table.hidden = !j.hits.length;
      body.innerHTML = j.hits.map((h) => `
        <tr class="border-t border-[var(--color-line)]">
          <td class="px-4 py-2"><a href="${esc(h.url)}">${esc(h.name)}</a>${h.linked ? ` <span class="badge badge-quiet">${esc(tr('names this plasmid'))}</span>` : ''}</td>
          <td class="py-2 text-muted">${esc(h.database)}</td>
          <td class="py-2 whitespace-nowrap tabular-nums">${document.querySelector('[data-seq-viewer]') && h.start
            ? `<button type="button" class="pov-link" data-scan-go="${Number(h.start)}-${Number(h.end)}">${esc(h.where)}</button>` : esc(h.where)}</td>
          <td class="py-2 whitespace-nowrap ${h.exact ? '' : 'text-muted'}">${esc(matchText(h))}</td>
          <td class="py-2 font-mono text-[12px] break-all">${esc(h.sequence)}</td>
          <td class="py-2 pr-4">${esc(h.status || '')}</td>
        </tr>`).join('');
    }

    function scan() {
      panel.hidden = false;
      table.hidden = true;
      summary.textContent = tr('Looking through the saved primers…');
      return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then((r) => r.json().catch(() => ({ ok: false, error: tr('The server answered %(status)s.', { status: r.status }) })))
        .then((j) => {
          if (!toggle.checked) return;
          if (j && j.ok) show(j);
          else summary.textContent = tr('Couldn’t look: %(error)s', { error: (j && j.error) || tr('unknown error') });
        })
        .catch((err) => { summary.textContent = tr('Couldn’t look: %(error)s', { error: err.message }); });
    }

    toggle.addEventListener('change', () => {
      if (toggle.checked) { scan(); return; }
      panel.hidden = true;
    });
  }

  function start() { document.querySelectorAll('[data-primer-scan]').forEach(mount); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
