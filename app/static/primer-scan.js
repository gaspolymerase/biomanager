/* Find saved primers (GitHub #62): a switch on a plasmid's Primers card
   (and on a record's read-only map) that asks /plasmids/<id>/primer-sites
   which of the lab's saved primers bind this sequence, lists them, and with
   Show on map draws them on the editor's map.

   What it draws is for looking only. Each mark's id starts with
   "found-primer-": the plasmid page leaves those out of every save, and the
   server drops any that slip through, so a found primer never becomes part
   of the plasmid (nor a second record in Primers). The map is the editor
   mounted in #ove-root, which leaves itself on the element as oveEditor. */
(function () {
  const PREFIX = 'found-primer-';
  const COLOR = '#f59e0b';
  const tr = (text, values) => (typeof window.t === 'function' ? window.t(text, values) : text);

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  function editor() {
    const root = document.getElementById('ove-root');
    return root && root.oveEditor;
  }

  function currentSequence() {
    const ed = editor();
    const state = ed && ed.getState ? ed.getState() : null;
    return state && state.sequenceData ? state.sequenceData : null;
  }

  // The map's primers without the found ones, plus `hits` drawn when given.
  function draw(hits) {
    const ed = editor();
    const sd = currentSequence();
    if (!ed || !sd) return false;
    const primers = {};
    const own = sd.primers || {};
    (Array.isArray(own) ? own : Object.values(own)).forEach((p) => {
      if (p && p.id != null && !String(p.id).startsWith(PREFIX)) primers[p.id] = p;
    });
    (hits || []).forEach((h, i) => {
      const id = `${PREFIX}${h.id}-${i}`;
      primers[id] = {
        id, name: h.name, type: 'primer_bind', color: COLOR,
        start: h.start - 1, end: h.end - 1, forward: h.strand > 0, strand: h.strand,
      };
    });
    ed.updateEditor({ justPassingPartialSeqData: true, sequenceData: { primers } });
    return true;
  }

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
    const onMap = box.querySelector('[data-scan-map]');
    const mapRow = box.querySelector('[data-scan-map-row]');
    let result = null;

    function show(j) {
      result = j;
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
      mapRow.hidden = !j.hits.length || !editor();
      body.innerHTML = j.hits.map((h) => `
        <tr class="border-t border-[var(--color-line)]">
          <td class="px-4 py-2"><a href="${esc(h.url)}">${esc(h.name)}</a>${h.linked ? ` <span class="badge badge-quiet">${esc(tr('names this plasmid'))}</span>` : ''}</td>
          <td class="py-2 text-muted">${esc(h.database)}</td>
          <td class="py-2 whitespace-nowrap tabular-nums">${esc(h.where)}</td>
          <td class="py-2 whitespace-nowrap ${h.exact ? '' : 'text-muted'}">${esc(matchText(h))}</td>
          <td class="py-2 font-mono text-[12px] break-all">${esc(h.sequence)}</td>
          <td class="py-2 pr-4">${esc(h.status || '')}</td>
        </tr>`).join('');
      if (onMap.checked) draw(j.hits);
    }

    function scan() {
      panel.hidden = false;
      table.hidden = true;
      mapRow.hidden = true;
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
      if (onMap.checked) { onMap.checked = false; draw(null); }
    });
    onMap.addEventListener('change', () => {
      if (!onMap.checked) { draw(null); return; }
      const sd = currentSequence();
      // Edited since the scan: look again, so the marks land where they bind now.
      if (result && sd && (sd.sequence || '').length !== result.length) scan();
      else if (result) draw(result.hits);
    });
  }

  function start() { document.querySelectorAll('[data-primer-scan]').forEach(mount); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
