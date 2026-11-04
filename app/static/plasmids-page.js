/* The plasmid sheet (plasmids.html). The most made at once is in #plasmids-page-data. */
const PLASMIDS = JSON.parse(document.getElementById('plasmids-page-data').textContent);
/* The Belongs to pill takes its colour from what it says. */
document.addEventListener('change', (e) => {
  const pill = e.target.closest && e.target.closest('.plasmid-sheet .scope-pill');
  if (pill) pill.dataset.scope = pill.value === '1' ? 'lab' : 'mine';
});
/* Open a given box (after saving one, the server sends ?box=<id>): the
   rack grid reads its remembered box when it starts, which is after this. */
(function () {
  const box = new URLSearchParams(window.location.search).get('box');
  if (!box || !/^\d+$/.test(box)) return;
  try {
    localStorage.setItem('rack:plasmid-boxes:active', box);
    localStorage.setItem('plasmids:view-mode', 'cards');
  } catch (_) {}
})();

/* The record dialog: New shows the sequence upload and How many; Open
   shows who owns a read-only plasmid, and links to its page. */
(function () {
  const dialog = document.getElementById('plasmid-dialog');
  if (!dialog) return;
  const form = dialog.querySelector('form');
  const sub = dialog.querySelector('[data-plasmid-sub]');
  const hint = dialog.querySelector('[data-plasmid-hint]');
  const count = form.querySelector('[data-plasmid-count]');
  const names = form.querySelector('[data-plasmid-names]');
  const nameInput = form.elements.name;
  const manyHint = form.querySelector('[data-plasmid-many-hint]');
  const submit = dialog.querySelector('[data-record-submit]');
  const nextId = parseInt(sub.dataset.nextId, 10) || 1;
  let isNew = true;

  const pasted = () => names.value.split('\n').map((s) => s.trim()).filter(Boolean);
  function syncMany() {
    if (!isNew) return;
    const list = pasted();
    const n = list.length || Math.max(1, Math.min(PLASMIDS.maxBatch, parseInt(count.value, 10) || 1));
    count.disabled = list.length > 0;
    if (list.length) count.value = list.length;
    const file = document.getElementById('plasmid-new-file');
    const hasFile = !!(file && file.files && file.files.length);
    // A list names them; so can a sequence file (the server reads its name).
    nameInput.required = !list.length && !hasFile;
    nameInput.closest('label').classList.toggle('is-muted', list.length > 0);
    nameInput.placeholder = list.length ? t('from the list below')
      : hasFile ? t('from the file, or type one') : t('e.g. %(example)s', { example: 'pCMV-mCherry-NLS' });
    sub.textContent = n > 1
      ? t('They will be plasmids #%(first)s–#%(last)s (or the next free run of numbers).', { first: nextId, last: nextId + n - 1 })
      : t('It will be plasmid #%(n)s, or the next free number if that is taken when you save.', { n: nextId });
    manyHint.textContent = list.length
      ? t(list.length === 1 ? '%(n)s name: one plasmid each, with the construct and storage below.'
        : '%(n)s names: one plasmid each, with the construct and storage below.', { n: list.length })
      : t('Each gets its own number, one after another, side by side in the box from the position below.');
    if (submit) submit.textContent = n > 1 ? t('Create %(n)s plasmids', { n }) : t('Create plasmid');
  }
  count.addEventListener('input', syncMany);
  names.addEventListener('input', syncMany);

  dialog.addEventListener('record-dialog:open', (event) => {
    const { data } = event.detail || {};
    isNew = event.detail.isNew;
    dialog.querySelectorAll('[data-new-only]').forEach((el) => { el.hidden = !isNew; });
    count.disabled = !isNew;
    names.disabled = !isNew;
    nameInput.required = true;
    nameInput.placeholder = t('e.g. %(example)s', { example: 'pCMV-mCherry-NLS' });
    nameInput.closest('label').classList.remove('is-muted');
    const namesBox = form.querySelector('[data-plasmid-names-box]');
    if (namesBox) namesBox.open = false;
    sub.innerHTML = '';
    if (isNew) {
      syncMany();
    } else {
      const link = document.createElement('a');
      link.href = `/plasmid/${data.plasmid_id}`;
      link.textContent = t('Open its page (sequence map)');
      sub.appendChild(link);
    }
    if (hint) hint.textContent = (!isNew && data._locked) ? t('%(owner)s’s plasmid: read only', { owner: data.owner || t('Someone else') }) : '';
    // Lab common lets anyone edit it; whose it is stays with its owner.
    const fixed = !isNew && !data._locked && data._manage === false;
    ['owner', 'is_shared'].forEach((name) => {
      const el = form.elements[name];
      if (!el) return;
      el.disabled = Boolean(data._locked) || fixed;
      el.title = fixed ? t('Lab common: only %(owner)s or an admin can change whose it is', { owner: data.owner || t('its owner') }) : '';
    });
  });
})();

/* The box dialog: Delete says how many plasmids leave the box, and a box
   someone else made opens read only. */
(function () {
  const dialog = document.getElementById('plasmid-box-dialog');
  if (!dialog) return;
  const del = dialog.querySelector('form[data-rack-delete]');
  const note = dialog.querySelector('[data-box-locked]');
  dialog.addEventListener('record-dialog:open', (event) => {
    const { data, isNew } = event.detail || {};
    const locked = !isNew && data._locked;
    if (del) {
      del.hidden = isNew || locked;
      const n = data._count || 0;
      del.setAttribute('data-confirm', n
        ? t(n === 1
          ? 'Delete the box “%(name)s”? Its %(n)s plasmid will no longer be in a box (nothing else is deleted). You can undo it from Batch history.'
          : 'Delete the box “%(name)s”? Its %(n)s plasmids will no longer be in a box (nothing else is deleted). You can undo it from Batch history.',
          { name: data.name, n })
        : t('Delete the empty box “%(name)s”?', { name: data.name }));
    }
    if (note) {
      note.hidden = !locked;
      note.textContent = !locked ? ''
        : data._creator
          ? t('Only %(who)s (who made it) or an admin can change or delete this box. You can still put plasmids in it.', { who: data._creator })
          : t('Only an admin can change or delete this box. You can still put plasmids in it.');
    }
  });
})();

/* A sheet row's Open button follows what was saved, and what the grid moved. */
(function () {
  // A row may wait off the page until it is shown (data-table.js, BioSheets).
  const rowOf = (id) => window.BioSheets.row(id, document.getElementById('plasmids-section'));
  const openOf = (id) => rowOf(id)?.querySelector('[data-open-payload]') || null;
  document.addEventListener('sheet:saved', (event) => {
    const { form, body } = event.detail || {};
    const open = form && body && body.payload && openOf(form.id.replace('plasmid-', ''));
    if (open) open.dataset.recordPayload = JSON.stringify(body.payload);
  });
  // A move on the box grid: the row's box, position, their _was copies,
  // its dot and its Open payload. The Unplaced tray keeps the box.
  document.addEventListener('rack-grid:moved', (event) => {
    const { view, id, rackId, position } = event.detail || {};
    if (view !== 'plasmid-boxes') return;
    const form = BioRowForms.get(`plasmid-${id}`);
    if (!form) return;
    const set = (name, v) => {
      // A cell drawn as text (static/text-cells.js) becomes its field to take the value.
      const tr = rowOf(id);
      const cell = tr && BioTextCells.read(tr, name, `plasmid-${id}`);
      if (cell && cell.value !== v) BioTextCells.field(tr, name, `plasmid-${id}`, true);
      tr?.querySelectorAll(`[form="plasmid-${id}"][name="${name}"]`).forEach((el) => { el.value = v; });
      const was = form.querySelector(`[name="${name}_was"]`);
      if (was) was.value = v;
    };
    if (rackId) set('box_id', String(rackId));
    set('position', position || '');
    const tr = rowOf(id);
    if (tr && window.BioSheet) window.BioSheet.refreshDot(tr, Boolean(rackId && position));
    const open = openOf(id);
    if (open) {
      try {
        const payload = JSON.parse(open.dataset.recordPayload || '{}');
        if (rackId) Object.assign(payload, { box_id: String(rackId), box_id_was: String(rackId) });
        Object.assign(payload, { position: position || '', position_was: position || '' });
        open.dataset.recordPayload = JSON.stringify(payload);
      } catch (_) { /* leave it */ }
    }
  });
})();

// View mode switcher (Table / Box / List). Persisted to localStorage.
(function () {
  const VIEW_KEY = 'plasmids:view-mode';
  const section = document.getElementById('plasmids-section');
  if (!section) return;
  const btns = document.querySelectorAll('.plasmid-view-btn');
  const panels = section.querySelectorAll('.plasmid-view');
  const searchInput = section.querySelector('.dt-search');

  function applyView(name) {
    btns.forEach((b) => {
      b.classList.toggle('is-active', b.dataset.view === name);
      b.setAttribute('aria-pressed', b.dataset.view === name ? 'true' : 'false');
    });
    panels.forEach((p) => p.classList.toggle('is-active', p.dataset.view === name));
    section.dataset.view = name;
    document.dispatchEvent(new CustomEvent('plasmids:view', { detail: name }));
    try { localStorage.setItem(VIEW_KEY, name); } catch (_) {}
  }

  btns.forEach((b) => b.addEventListener('click', () => applyView(b.dataset.view)));
  let saved = '';
  try { saved = localStorage.getItem(VIEW_KEY) || ''; } catch (_) {}
  applyView(['table', 'cards', 'list'].includes(saved) ? saved : 'table');

  // The search also asks which plasmids carry it on their map.
  const onMap = section.querySelector('[data-on-map]');
  let mapTimer = null;
  let mapAsked = '';
  function showOnMap(q, answer) {
    onMap.textContent = '';
    if (!answer || !answer.total) {
      onMap.hidden = true;
      return;
    }
    const t = window.t || ((x) => x);
    const lead = document.createElement('span');
    lead.textContent = t('“%(q)s” on the map of %(n)s plasmids:', { q, n: answer.total });
    onMap.appendChild(lead);
    answer.plasmids.slice(0, 12).forEach((p, i) => {
      onMap.appendChild(document.createTextNode(i ? ' · ' : ' '));
      const a = document.createElement('a');
      a.href = p.url;
      a.textContent = `#${p.number} ${p.name}`;
      a.title = p.features.join(', ');
      onMap.appendChild(a);
    });
    if (answer.total > 12) {
      const more = document.createElement('span');
      more.className = 'text-muted';
      more.textContent = ' ' + t('and %(n)s more', { n: answer.total - 12 });
      onMap.appendChild(more);
    }
    onMap.hidden = false;
  }
  if (onMap && searchInput) {
    searchInput.addEventListener('input', () => {
      clearTimeout(mapTimer);
      const q = searchInput.value.trim();
      if (q.length < 2) { mapAsked = ''; showOnMap(q, null); return; }
      mapTimer = setTimeout(() => {
        mapAsked = q;
        fetch(`${onMap.dataset.src}?q=${encodeURIComponent(q)}`, { credentials: 'same-origin' })
          .then((r) => (r.ok ? r.json() : null))
          .then((answer) => { if (mapAsked === q) showOnMap(q, answer); }, () => {});
      }, 350);
    });
  }

  // The toolbar search also filters the list view.
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      const q = searchInput.value.trim().toLowerCase();
      section.querySelectorAll('.plasmid-list-row[data-search]').forEach((el) => {
        const match = !q || (el.dataset.search || '').includes(q);
        el.style.display = match ? '' : 'none';
      });
    });
  }
})();

// ---- New plasmid dialog: drop-zone wiring ------------------------------
(function () {
  const modal = document.getElementById('plasmid-dialog');
  const dz = document.getElementById('plasmid-new-dropzone');
  const fileInput = document.getElementById('plasmid-new-file');
  const fileLabel = document.getElementById('plasmid-new-filename');
  if (!modal || !dz || !fileInput) return;

  function showFile(file) {
    if (!file) { fileLabel.textContent = ''; return; }
    const kb = (file.size / 1024).toFixed(1);
    fileLabel.textContent = `${file.name} · ${kb} KB`;
  }

  fileInput.addEventListener('change', () => {
    showFile(fileInput.files[0]);
    // The name may now come from the file (see syncMany).
    modal.querySelector('[data-plasmid-names]')?.dispatchEvent(new Event('input'));
  });

  ['dragenter', 'dragover'].forEach((ev) => {
    dz.addEventListener(ev, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dz.classList.add('is-drop-target');
    });
  });
  ['dragleave', 'drop'].forEach((ev) => {
    dz.addEventListener(ev, (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (ev === 'dragleave' && event.target !== dz) return;
      dz.classList.remove('is-drop-target');
    });
  });
  dz.addEventListener('drop', (event) => {
    const f = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (!f) return;
    // Move the dropped file into the hidden <input type=file> so the form
    // submits it normally.
    const dt = new DataTransfer();
    dt.items.add(f);
    fileInput.files = dt.files;
    fileInput.dispatchEvent(new Event('change'));
  });

  // Reset the filename display when the dialog closes or reopens.
  modal.addEventListener('close', () => { fileLabel.textContent = ''; });
  modal.addEventListener('record-dialog:open', () => { fileLabel.textContent = ''; });
})();
