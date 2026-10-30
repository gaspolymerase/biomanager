/* From Addgene or NCBI (templates/_sequence_fetch.html): fetch a plasmid's
   GenBank record by its Addgene number or accession
   (app/sequence_fetch_routes.py) and put it in the form's paste box, so
   creating the plasmid or replacing its sequence reads it as any pasted
   GenBank record. On New plasmid it also fills an empty name and notes. */
(function () {
  'use strict';

  const t = window.t || ((s) => s);
  const num = (n) => Number(n || 0).toLocaleString('en-US');

  function fetchInto(box) {
    const input = box.querySelector('[data-seq-fetch-ref]');
    const button = box.querySelector('[data-seq-fetch-go]');
    const said = box.querySelector('[data-seq-fetch-said]');
    const ref = (input.value || '').trim();
    if (!ref) { input.focus(); return; }
    const url = new URL(box.dataset.url, window.location.href);
    url.searchParams.set('ref', ref);
    button.disabled = true;
    said.className = 'seq-fetch-said';
    said.textContent = t('Fetching…');
    fetch(url, { headers: { 'X-Requested-With': 'fetch' } })
      .then((r) => r.json())
      .then((j) => {
        button.disabled = false;
        if (!j.ok) { said.classList.add('is-error'); said.textContent = j.error || ''; return; }
        const form = box.closest('form');
        const paste = form && form.querySelector('textarea[name="sequence_text"]');
        if (paste) {
          paste.value = j.text;
          const fold = paste.closest('details');
          if (fold) fold.open = true;
        }
        const name = form && form.querySelector('input[name="name"]');
        if (name && !name.value.trim() && j.name) name.value = j.name;
        const notes = form && form.querySelector('textarea[name="notes"]');
        if (notes && !notes.value.trim()) notes.value = t('From %(source)s.', { source: j.label });
        said.classList.add('is-ok');
        said.textContent = '';
        const line = document.createElement('span');
        line.textContent = t('%(name)s · %(bp)s bp · %(n)s features. It goes in when you save.',
          { name: j.name || j.label, bp: num(j.length), n: j.features });
        const link = document.createElement('a');
        link.href = j.url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = j.label;
        said.append(line, ' ', link);
      })
      .catch(() => {
        button.disabled = false;
        said.classList.add('is-error');
        said.textContent = t('The server did not answer. Check the connection and try again.');
      });
  }

  document.addEventListener('click', (e) => {
    const go = e.target.closest('[data-seq-fetch-go]');
    if (go) fetchInto(go.closest('[data-seq-fetch]'));
  });
  // Enter fetches; it must not submit the form around it.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || !e.target.matches('[data-seq-fetch-ref]')) return;
    e.preventDefault();
    fetchInto(e.target.closest('[data-seq-fetch]'));
  });
})();
