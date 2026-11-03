/* The colony page (templates/colony.html): its sheets' rows, their dialogs and
 * buttons. What the page knows from the server is in #colony-page-data. */
const COLONY = JSON.parse(document.getElementById('colony-page-data').textContent);
const autosaveTimers = new Map();

/* Batch actions now live in the selection bar (selection-bar.js). */

/* Card and litter-row buttons carry their target as data attributes (never
   inside an onclick string, where a quote in a value could break out). */
document.addEventListener('click', (event) => {
  const add = event.target.closest('[data-add-existing-mouse]');
  if (add) {
    const dialog = document.getElementById('existing-mouse-modal');
    const form = document.getElementById('existing-mouse-form');
    const litter = add.hasAttribute('data-litter');
    form.action = add.dataset.action;
    dialog.querySelector('[data-existing-title]').textContent = litter ? t('Add an existing mouse to the litter') : t('Move an existing mouse into the cage');
    dialog.querySelector('[data-existing-target-label]').textContent = litter ? t('Litter') : t('Cage');
    dialog.querySelector('[data-existing-submit]').textContent = litter ? t('Add to litter') : t('Move into cage');
    document.getElementById('existing-mouse-target').value = add.dataset.targetLabel || '';
    form.querySelector('input[name="mouse_id"]').value = '';
    dialog.showModal();
    return;
  }
  const born = event.target.closest('[data-litter-born]');
  if (born) {
    const pad = (n) => String(n).padStart(2, '0');
    const now = new Date();
    const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    document.getElementById('litter-born-form').action = born.dataset.action;
    document.getElementById('litter-born-cage').textContent = t('cage %(id)s', { id: born.dataset.cageLabel || '' });
    const form = document.getElementById('litter-born-form');
    const day = document.getElementById('litter-born-date');
    day.value = today;
    day.max = today;
    form.elements.pups.value = '';
    form.elements.sire.value = '';
    form.elements.dam.value = '';
    // A litter not yet weaned: ask whether this is another one or a
    // correction (the server refuses to guess). The same date again
    // needs no answer: it is that litter.
    let earlier = [];
    try { earlier = JSON.parse(born.dataset.litters || '[]'); } catch (_) {}
    litterBornEarlier = earlier;
    document.getElementById('litter-born-earlier').textContent = earlier.length === 1
      ? t('This cage has a litter born %(date)s that is not weaned yet. Is this one:', { date: earlier[0].label })
      : t('This cage has litters born %(dates)s that are not weaned yet. Is this one:', { dates: earlier.map((l) => l.label).join(', ') });
    const which = form.elements.litter;
    which.replaceChildren(...earlier.map((l) => new Option(l.label, l.born)));
    form.querySelectorAll('input[name="litter_choice"]').forEach((radio) => { radio.checked = false; });
    showLitterBornChoice();
    document.getElementById('litter-born-modal').showModal();
    day.focus();
    return;
  }
  const geno = event.target.closest('[data-genotyping]');
  if (geno) {
    const form = document.getElementById('genotyping-form');
    form.action = geno.dataset.action;
    document.getElementById('genotyping-father').value = geno.dataset.father || '';
    document.getElementById('genotyping-mother').value = geno.dataset.mother || '';
    form.elements.total_pups.value = '';
    // Which litter's pups, when the cage has two waiting: the oldest not
    // yet genotyped first; its count, father and mother fill in when recorded.
    let litters = [];
    try { litters = JSON.parse(geno.dataset.litters || '[]'); } catch (_) {}
    const pick = form.elements.litter;
    pick.replaceChildren(...litters.map((l) => new Option(litterOptionLabel(l), l.born)));
    pick.value = (litters.find((l) => !l.genotyped) || litters[0] || {}).born || '';
    pick.disabled = litters.length < 2;
    document.getElementById('genotyping-which').hidden = litters.length < 2;
    const fill = () => {
      const chosen = litters.find((l) => l.born === pick.value) || litters[0];
      form.elements.total_pups.value = (chosen && chosen.pups) || '';
      document.getElementById('genotyping-father').value = (chosen && chosen.sire) || geno.dataset.father || '';
      document.getElementById('genotyping-mother').value = (chosen && chosen.dam) || geno.dataset.mother || '';
    };
    pick.onchange = fill;
    fill();
    document.getElementById('genotyping-modal').showModal();
    return;
  }
  const wean = event.target.closest('[data-wean]');
  if (wean) openWeanModal(wean);
});

/* A cage's litter in a "which litter" list: its birth date, its age, and
   the litter of mice its pups are once genotyped. */
function litterOptionLabel(l) {
  return `${l.label} · P${l.age}${l.litter ? ' · ' + l.litter : ''}`;
}

/* Litter born on a cage with a litter not yet weaned: Another litter or
   Correct the date, asked unless the date is that litter's own. */
let litterBornEarlier = [];
function showLitterBornChoice() {
  const form = document.getElementById('litter-born-form');
  // A blank date is today, as the server reads it.
  const date = form.elements.date_give_birth.value || form.elements.date_give_birth.max;
  const ask = litterBornEarlier.length > 0 && !litterBornEarlier.some((l) => l.born === date);
  document.getElementById('litter-born-choice').hidden = !ask;
  form.querySelectorAll('input[name="litter_choice"]').forEach((radio) => { radio.required = ask; });
  const correcting = ask && form.querySelector('input[name="litter_choice"][value="correct"]').checked;
  const several = correcting && litterBornEarlier.length > 1;
  document.getElementById('litter-born-which').hidden = !several;
  form.elements.litter.disabled = !several;
}
document.getElementById('litter-born-form')?.addEventListener('change', showLitterBornChoice);
document.getElementById('litter-born-form')?.addEventListener('input', showLitterBornChoice);

/* Weaning pups younger than the lab's earliest weaning age asks first (the server refuses without early=1). */
function confirmYoungPups(cageLabel, age) {
  return BioDialog.confirm(
    (age < 0 ? t('The pups in cage %(cage)s are not born yet by their date.', { cage: cageLabel })
      : t('The pups in cage %(cage)s are %(age)s days old.', { cage: cageLabel, age: age }))
    + ' ' + t('Weaning is due at P%(days)s.', { days: COLONY.weanOffsetDays }) + '\n\n' + t('Wean them early anyway?'),
    { danger: true, okLabel: t('Wean early'), title: t('These pups are young') });
}

/* A cage with two litters waiting: the one picked in the Wean dialog is
   checked for its age when the dialog is sent. */
let weanLitters = [];
document.getElementById('wean-form')?.addEventListener('submit', async (event) => {
  const form = event.target;
  const pick = form.elements.litter;
  if (pick.disabled || form.elements.early.value === '1') return;
  const chosen = weanLitters.find((l) => l.born === pick.value);
  if (!chosen || chosen.age >= COLONY.minWeanAgeDays) return;
  event.preventDefault();
  const submitter = event.submitter;
  if (!(await confirmYoungPups(form.dataset.cageLabel || '', chosen.age))) return;
  form.elements.early.value = '1';
  form.requestSubmit(submitter);
});

function fillWeanRows(pups) {
  const body = document.getElementById('wean-rows-body');
  body.innerHTML = '';
  addWeanRow({ gender: 'F', mice: pups.F || '' });
  addWeanRow({ gender: 'M', mice: pups.M || '' });
  if (pups['']) addWeanRow({ gender: '', mice: pups[''] });
}

function addWeanRow(prefill) {
  const body = document.getElementById('wean-rows-body');
  if (!body) return;
  const tr = document.createElement('tr');
  tr.className = 'wean-row';
  tr.innerHTML = `
    <td><input name="mouse_ids[]" placeholder="${t('e.g. 23, 45, 67')}" autocomplete="off"></td>
    <td>
      <select name="gender[]">
        <option value="">—</option>
        <option value="M">${t('M')}</option>
        <option value="F">${t('F')}</option>
        <option value="Unknown">?</option>
      </select>
    </td>
    <td><input name="card_id[]" placeholder="${t('optional card label')}" autocomplete="off"></td>
    <td><input name="cage_id[]" placeholder="${t('leave empty for new cage')}" autocomplete="off"></td>
    <td><button type="button" class="row-icon-btn" data-on-click="remove-row" title="${t('Remove row')}" aria-label="${t('Remove row')}">×</button></td>
  `;
  if (prefill && prefill.gender) tr.querySelector('select[name="gender[]"]').value = prefill.gender;
  if (prefill && prefill.mice) tr.querySelector('input[name="mouse_ids[]"]').value = prefill.mice;
  body.appendChild(tr);
}

async function openWeanModal(button) {
  const dialog = document.getElementById('wean-modal');
  const form = document.getElementById('wean-form');
  if (!dialog || !form) return;
  const cageLabel = button.dataset.targetLabel || '';
  form.dataset.cageLabel = cageLabel;
  form.elements.early.value = '';
  // Two litters waiting: the dialog asks which (the oldest first), fills
  // the rows with its pups, and checks its age when sent (above).
  try { weanLitters = JSON.parse(button.dataset.litters || '[]'); } catch (_) { weanLitters = []; }
  const pick = form.elements.litter;
  pick.replaceChildren(...weanLitters.map((l) => new Option(litterOptionLabel(l), l.born)));
  pick.disabled = weanLitters.length < 2;
  document.getElementById('wean-which').hidden = weanLitters.length < 2;
  if (weanLitters.length < 2) {
    // Pups younger than the earliest weaning age: ask first (the server refuses without early=1).
    const age = parseInt(button.dataset.age, 10);
    if (!Number.isNaN(age) && age < COLONY.minWeanAgeDays) {
      if (!(await confirmYoungPups(cageLabel, age))) return;
      form.elements.early.value = '1';
    }
  }
  document.getElementById('wean-cage-label').textContent = cageLabel ? '· ' + t('cage %(id)s', { id: cageLabel }) : '';
  document.getElementById('wean-submit-distribute').setAttribute('formaction', button.dataset.distributeAction);
  document.getElementById('wean-submit-self').setAttribute('formaction', button.dataset.weanAction);
  // One row for the females and one for the males, filled with this
  // cage's pups when they are entered as mice; unsexed pups get a row too.
  let pups = {};
  try { pups = JSON.parse(button.dataset.pups || '{}'); } catch (_) {}
  fillWeanRows(pups);
  pick.onchange = () => {
    const chosen = weanLitters.find((l) => l.born === pick.value);
    if (chosen) fillWeanRows(chosen.wean_pups || {});
  };
  dialog.showModal();
}

function openExportModal() {
  document.getElementById('export-modal').showModal();
}

function printMouseTable() {
  window.open(COLONY.exportPdfUrl, '_blank');
}

/* Experiment cards: status chips. */
document.querySelectorAll('[data-experiment-filter]').forEach((chip) => {
  chip.addEventListener('click', () => {
    const status = chip.dataset.experimentFilter;
    document.querySelectorAll('[data-experiment-filter]').forEach((c) => c.setAttribute('aria-pressed', c === chip ? 'true' : 'false'));
    let shown = 0;
    document.querySelectorAll('.experiment-card[data-status]').forEach((card) => {
      card.hidden = Boolean(status) && card.dataset.status !== status;
      if (!card.hidden) shown += 1;
    });
    const none = document.querySelector('[data-experiment-nomatch]');
    if (none) none.hidden = shown > 0;
  });
});

/* ?q= from global search (or a link) fills this tab's search box once the
   tables are wired, so the record you asked for is what you see. */
document.addEventListener('DOMContentLoaded', () => {
  const q = new URLSearchParams(window.location.search).get('q');
  if (!q) return;
  const box = document.getElementById('breeder-search')
    || document.querySelector('.data-table-card .dt-search');
  if (!box) return;
  box.value = q;
  box.dispatchEvent(new Event('input', { bubbles: true }));
});

function autosaveInput(input, options) {
  const immediate = !!(options && options.immediate);
  const formId = input.getAttribute('form') || input.closest('form')?.id;
  const form = formId ? BioRowForms.get(formId) : input.closest('form');
  if (!form) return;
  if (!form.dataset.autosaveKey) {
    form.dataset.autosaveKey = formId || `autosave-${Math.random().toString(36).slice(2)}`;
  }
  const autosaveKey = form.dataset.autosaveKey;
  clearTimeout(autosaveTimers.get(autosaveKey));
  if (immediate) {
    saveFormInBackground(form);
  } else {
    autosaveTimers.set(autosaveKey, setTimeout(() => saveFormInBackground(form), 250));
  }
}

/* Alive or not, shown by the green dot beside the mouse ID. A mouse is
   alive while it has no date of death and its status is not one that
   means it died (sac, dead…) or left (transfer). Choosing such a status
   fills today's date of death, which stays editable; moving a mouse back
   to a living status (a sac entered by mistake) clears it again. The
   server applies the same rule and its answer has the final word. */
const END_STATUSES = new Set(COLONY.endStatuses);
function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
/* A row's field (a cell drawn as text becomes its field: static/text-cells.js),
   and a cell's value, read without that. */
function sheetField(formId, name) {
  return BioTextCells.field(document, name, formId);
}
function sheetValue(formId, name) {
  const cell = BioTextCells.read(document, name, formId);
  return cell ? cell.value : '';
}
function sheetRow(formId) {
  return document.querySelector(`[form="${formId}"]:not([type=hidden]), .cell-text[data-form="${formId}"]`)?.closest('tr') || null;
}
function refreshLife(formId, activeFromServer) {
  const row = sheetRow(formId);
  if (!row) return;
  const now = sheetValue(formId, 'status').trim().toLowerCase();
  const alive = typeof activeFromServer === 'boolean'
    ? activeFromServer
    : !sheetValue(formId, 'date_of_death') && !END_STATUSES.has(now) && now !== 'transfer';
  const dot = row.querySelector('.life-dot');
  if (dot) {
    dot.classList.toggle('is-alive', alive);
    dot.title = alive ? (dot.dataset.aliveTitle || t('Alive')) : t('Not alive');
  }
  row.dataset.active = alive ? 'true' : 'false';
  row.classList.toggle('is-inactive', !alive);
}
function applyStatusChange(select, previous) {
  const formId = select.getAttribute('form');
  const dod = sheetField(formId, 'date_of_death');
  const now = select.value.trim().toLowerCase();
  if (dod) {
    if (END_STATUSES.has(now) && !dod.value) dod.value = localToday();
    else if (END_STATUSES.has(previous) && !END_STATUSES.has(now)) dod.value = '';
    dod.dataset.empty = dod.value ? '0' : '1';
  }
  refreshLife(formId);
}

/* "Change the date of birth of mouse 12 from … to …?" */
function dobQuestion(what, from, to) {
  const values = { what: what, from: from, to: to || t('(blank)') };
  return from ? t('Change the date of birth of %(what)s from %(from)s to %(to)s?', values)
    : t('Change the date of birth of %(what)s to %(to)s?', values);
}

/* Changing a birth date asks first (filling in a blank one doesn't). A
   mouse in a cohort shares its litter's date, so saying yes moves it to a
   litter of its own. */
async function confirmDobChange(input) {
  const from = input.dataset.originalDob || '';
  const to = input.value || '';
  if (to === from) return false;
  const formId = input.getAttribute('form');
  const form = formId ? BioRowForms.get(formId) : input.closest('form');
  if (input.hasAttribute('data-litter-dob')) {
    // A litter's date of birth is the age of every mouse in it.
    const n = Number(input.dataset.miceCount || 0);
    const what = form?.dataset.recordLabel || t('this litter');
    const message = dobQuestion(what, from, to)
      + (n ? '\n\n' + (n === 1 ? t('The age of its %(n)s mouse changes with it.', { n: n })
        : t('The age of its %(n)s mice changes with it.', { n: n })) : '');
    if (!(await BioDialog.confirm(message, { okLabel: t('Change the date') }))) {
      input.value = from;
      input.dataset.empty = from ? '0' : '1';
      return false;
    }
    input.dataset.originalDob = to;
    return true;
  }
  const who = form?.dataset.mouseLabel ? t('mouse %(id)s', { id: form.dataset.mouseLabel }) : t('this mouse');
  const cohort = input.dataset.originalLitter && from;
  // Filling in a blank date replaces nothing: no question, like any cell.
  if (!from) {
    const hidden = form?.querySelector('input[name="confirm_cohort_dob_change"]');
    if (hidden) hidden.value = '0';
    input.dataset.originalDob = to;
    return true;
  }
  const message = cohort
    ? dobQuestion(who, from, to) + '\n\n'
      + t('It is in litter %(litter)s, so it will move to a new litter of its own. The other mice in that litter keep their date.',
        { litter: input.dataset.originalLitter })
    : dobQuestion(who, from, to);
  if (!(await BioDialog.confirm(message, { okLabel: t('Change the date') }))) {
    input.value = from;
    input.dataset.empty = from ? '0' : '1';
    return false;
  }
  const hidden = form?.querySelector('input[name="confirm_cohort_dob_change"]');
  if (hidden) hidden.value = cohort ? '1' : '0';
  input.dataset.originalDob = to;  // asked once per change
  return true;
}
function wireDobInput(input) {
  // A date typed digit by digit changes several times; ask once the value
  // has settled, or when the field is left.
  let timer = null;
  const settle = async () => {
    clearTimeout(timer);
    timer = null;
    if (!(await confirmDobChange(input))) return;
    input.dataset.dirty = '1';
    input.dataset.empty = input.value ? '0' : '1';
    autosaveInput(input, { immediate: true });
  };
  input.addEventListener('change', () => { clearTimeout(timer); timer = setTimeout(settle, 900); });
  input.addEventListener('blur', () => { if (timer) settle(); });
}

/* After a save, the server's view of the mouse wins. A cell drawn as text
   becomes its field only to take a value that changed; the birth date's
   always, as it keeps what was saved for the next question. */
function syncMouse(form, state) {
  const dod = sheetValue(form.id, 'date_of_death') !== (state.date_of_death || '') ? sheetField(form.id, 'date_of_death') : null;
  if (dod && document.activeElement !== dod) {
    dod.value = state.date_of_death || '';
    dod.dataset.empty = dod.value ? '0' : '1';
  }
  const litter = 'litter_id' in state && sheetValue(form.id, 'litter_id') !== (state.litter_id || '') ? sheetField(form.id, 'litter_id') : null;
  const dob = sheetField(form.id, 'date_of_birth');
  if (litter && document.activeElement !== litter) litter.value = state.litter_id || '';
  if (dob) {
    dob.dataset.originalLitter = state.litter_id || '';
    dob.dataset.originalDob = state.date_of_birth || '';
    if (document.activeElement !== dob) dob.value = state.date_of_birth || '';
  }
  const hidden = form.querySelector('input[name="confirm_cohort_dob_change"]');
  if (hidden) hidden.value = '0';
  // The server's values (a status rule may set the date of death) are what the row shows.
  ['status', 'date_of_death', 'litter_id', 'date_of_birth'].forEach((name) => {
    const was = form.querySelector(`[name="${name}_was"]`);
    if (was && name in state) was.value = state[name] || '';
  });
  refreshLife(form.id, state.active);
}

/* The sheet's save indicator. One line in the toolbar, so a refused or
   failed save is visible where you are working instead of surfacing on
   the next page load. */
const saveStatus = document.querySelector('[data-save-status]');
let saveStatusTimer = null;
let savesInFlight = 0;
function showSaveStatus(state, text) {
  if (!saveStatus) return;
  clearTimeout(saveStatusTimer);
  saveStatus.dataset.state = state;
  saveStatus.textContent = text;
  if (state === 'saved') {
    saveStatusTimer = setTimeout(() => { saveStatus.textContent = ''; saveStatus.dataset.state = ''; }, 2400);
  }
}

function rowsForForm(form) {
  return document.querySelectorAll(`[form="${form.id}"]`);
}

/* Rack, position and location note belong to the cage, so every mouse row
   of that cage shows them. After any save the server returns the cage's
   current values; write them into every row of that cage, including the
   hidden `_was` copies that tell the server what a row last showed. */
function setValue(el, value) {
  if (!el) return;
  el.value = value == null ? '' : value;
  if (el.type === 'date') el.dataset.empty = el.value ? '0' : '1';
  if (el.name === 'cage_location') el.title = el.value;
}

function syncCage(state, sourceForm) {
  // Every row, including those waiting off the page (data-table.js, BioSheets).
  // A cell drawn as text becomes its field only where the value changed.
  const put = (tr, name, value) => {
    const cell = BioTextCells.read(tr, name);
    if (cell && cell.value !== (value == null ? '' : String(value))) setValue(BioTextCells.field(tr, name, undefined, true), value);
  };
  window.BioSheets.rows().forEach((tr) => {
    const cage = BioTextCells.read(tr, 'cage_id');
    if (!cage || cage.value.trim() !== String(state.cage_id)) return;
    put(tr, 'cage_rack', state.rack_id);
    put(tr, 'cage_position', state.position);
    put(tr, 'cage_location', state.location);
    const rowForm = BioRowForms.get(`mouse-update-${tr.dataset.id}`);
    if (rowForm) {
      setValue(rowForm.querySelector('[name="cage_rack_was"]'), state.rack_id);
      setValue(rowForm.querySelector('[name="cage_position_was"]'), state.position);
      setValue(rowForm.querySelector('[name="cage_location_was"]'), state.location);
    }
    const open = tr.querySelector('[data-mouse-edit]');
    if (open) {
      try {
        const payload = JSON.parse(open.dataset.mousePayload || '{}');
        Object.assign(payload, {
          cage_id: state.cage_id, cage_rack: state.rack_id, cage_position: state.position,
          cage_location: state.location, cage_rack_was: state.rack_id,
          cage_position_was: state.position, cage_location_was: state.location,
        });
        open.dataset.mousePayload = JSON.stringify(payload);
      } catch (_) { /* leave it */ }
    }
  });
}

/* A cage dragged on the rack grid: its row on the cage sheet follows, so a
   later edit on the row does not post the old position back. */
document.addEventListener('rack-grid:moved', (event) => {
  const { id, rackId, position } = event.detail || {};
  const form = BioRowForms.get(`cage-update-${id}`);
  if (!form) return;
  // Its row, on the page or off it (a long sheet holds only the page shown).
  const sheetCard = document.querySelector('.cage-sheet');
  const row = (sheetCard && window.BioSheets.row(id, sheetCard)) || document;
  setValue(BioTextCells.field(row, 'rack_id', form.id, true), rackId || '');
  setValue(BioTextCells.field(row, 'position', form.id, true), position || '');
  setValue(form.querySelector('[name="rack_id_was"]'), rackId || '');
  setValue(form.querySelector('[name="position_was"]'), position || '');
});

async function saveFormInBackground(form, headers) {
  form.dataset.saveState = 'saving';
  savesInFlight += 1;
  showSaveStatus('saving', t('Saving…'));
  const label = form.dataset.mouseLabel ? t('mouse %(id)s', { id: form.dataset.mouseLabel }) : (form.dataset.recordLabel || t('this change'));
  const sent = new FormData(form);
  try {
    const response = await fetch(form.action, {
      method: form.method || 'POST',
      body: sent,
      headers: Object.assign({ 'X-Autosave': '1' }, headers || {}),
    });
    let body = {};
    try { body = await response.json(); } catch (_) { /* a redirect or HTML page */ }
    if (!response.ok || body.ok === false) {
      throw new Error(body.error || t('The server answered %(status)s.', { status: response.status }));
    }
    form.dataset.saveState = 'saved';
    // What was just saved is what the row now shows: its "_was" copies say
    // so, or setting a cell back (sac by mistake, then its old status)
    // would read as no change and not be saved.
    sent.forEach((value, name) => {
      const was = typeof value === 'string' && form.querySelector(`[name="${name}_was"]`);
      if (was) was.value = value;
    });
    if (body.cage) syncCage(body.cage, form);
    if (body.mouse) syncMouse(form, body.mouse);
    rowsForForm(form).forEach((el) => el.closest('td')?.classList.remove('has-error'));
    savesInFlight -= 1;
    if (savesInFlight === 0) showSaveStatus('saved', t('All changes saved'));
    return true;
  } catch (error) {
    savesInFlight -= 1;
    form.dataset.saveState = 'error';
    rowsForForm(form).forEach((el) => {
      if (el === document.activeElement || el.dataset.dirty === '1') el.closest('td')?.classList.add('has-error');
    });
    showSaveStatus('error', t('Couldn’t save %(what)s: %(error)s', { what: label, error: error.message }));
    console.error(error);
    return false;
  }
}
// Set a field… on ticked rows saves each as its cell would (selection-bar.js).
document.querySelectorAll('[data-selection-scope="mice"]').forEach((card) => {
  card.bioSave = (form, headers) => saveFormInBackground(form, headers);
});

function wireAutosave(input) {
  if (input.closest('[data-autosave-sheet]')) return;  // static/sheet.js saves these
  if (input.name === 'date_of_birth') { wireDobInput(input); return; }
  input.addEventListener('input', () => { input.dataset.dirty = '1'; autosaveInput(input); });
  input.addEventListener('change', () => {
    input.dataset.dirty = '1';
    // Pills recolour as soon as the value changes.
    if (input.classList.contains('sex-pill')) input.dataset.sex = input.value;
    if (input.classList.contains('status-pill')) {
      const previous = input.dataset.status || '';
      input.dataset.status = input.value.toLowerCase();
      if (input.dataset.tone !== undefined) input.dataset.tone = (input.selectedOptions[0] && input.selectedOptions[0].dataset.tone) || '';
      applyStatusChange(input, previous);
    }
    if (input.type === 'date') input.dataset.empty = input.value ? '0' : '1';
    if (input.name === 'date_of_death' && input.getAttribute('form')) refreshLife(input.getAttribute('form'));
    autosaveInput(input, { immediate: true });
  });
}
document.querySelectorAll('[data-autosave="1"]').forEach(wireAutosave);

/* New / open mouse dialog. Buttons carry the row as data attributes; the
   same form creates (no id) or updates (id present). */
(function () {
  const dialog = document.getElementById('mouse-edit-dialog');
  if (!dialog) return;
  const form = dialog.querySelector('form[data-mouse-form]');
  const title = dialog.querySelector('[data-mouse-title]');
  const sub = dialog.querySelector('[data-mouse-sub]');
  const submit = dialog.querySelector('[data-mouse-submit]');
  const hint = dialog.querySelector('[data-mouse-hint]');
  const anotherRow = dialog.querySelector('[data-mouse-another-row]');
  const another = dialog.querySelector('[data-mouse-another]');
  const createSub = sub.textContent;
  let original = {};
  let made = 0;      // mice made with Add another: the sheet shows them when the form closes
  try { another.checked = localStorage.getItem('biomanager:mouse-add-another') === '1'; } catch (_e) { /* storage off */ }
  another.addEventListener('change', () => {
    try { localStorage.setItem('biomanager:mouse-add-another', another.checked ? '1' : '0'); } catch (_e) { /* storage off */ }
  });

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-mouse-edit]');
    if (!button) return;
    let data = {};
    try { data = JSON.parse(button.dataset.mousePayload || '{}'); } catch (e) { console.error(e); }
    original = data;
    form.reset();
    form.elements.confirm_cohort_dob_change.value = '0';
    Array.from(form.elements).forEach((el) => {
      if (!el.name || el.type === 'hidden') return;
      el.value = data[el.name] ?? '';
      el.disabled = Boolean(data.locked);
    });
    if (data.id) {
      form.action = form.dataset.updateAction.replace('/0/', `/${data.id}/`);
      title.textContent = t('Mouse %(id)s', { id: data.mouse_id });
      sub.textContent = data.locked ? t('Read only: this is %(owner)s’s mouse.', { owner: data.owner || t('another member') }) : t('Changes save when you press Save.');
      submit.textContent = t('Save');
      submit.hidden = Boolean(data.locked);
      anotherRow.hidden = true;
    } else {
      anotherRow.hidden = false;
      form.action = form.dataset.createAction;
      title.textContent = t('New mouse');
      sub.textContent = createSub;
      submit.textContent = t('Create mouse');
      submit.hidden = false;
    }
    hint.textContent = '';
    dialog.showModal();
    form.querySelector('select, input:not([type=hidden])')?.focus();
  });

  // Sac / dead fills today's date of death; back to a living status clears it.
  let dialogStatus = '';
  dialog.addEventListener('focusin', (event) => {
    if (event.target === form.elements.status) dialogStatus = form.elements.status.value.trim().toLowerCase();
  });
  form.elements.status.addEventListener('change', () => {
    const now = form.elements.status.value.trim().toLowerCase();
    const dod = form.elements.date_of_death;
    if (END_STATUSES.has(now) && !dod.value) dod.value = localToday();
    else if (END_STATUSES.has(dialogStatus) && !END_STATUSES.has(now)) dod.value = '';
    dialogStatus = now;
  });

  // Add another after this: the mouse is saved without leaving the page, and
  // the form stays open with what a batch of mice shares (strain, sex,
  // genotype, cage, litter, birth, owner); its tag, note and death clear.
  form.addEventListener('submit', async (event) => {
    if (original.id || !another.checked || event.defaultPrevented) return;
    event.preventDefault();
    submit.disabled = true;
    try {
      const res = await fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: { 'X-Add-Another': '1', Accept: 'application/json' } });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.ok) throw new Error(body.error || t('Not saved. Check the connection and try again.'));
      made += 1;
      ['ear_tag', 'note', 'date_of_death'].forEach((name) => { if (form.elements[name]) form.elements[name].value = ''; });
      sub.textContent = t('The next free ID (%(id)s) is assigned when you save.', { id: body.next_id });
      hint.textContent = [t('Mouse %(id)s created.', { id: body.mouse_id }), ...(body.warnings || [])].join(' ');
      (form.elements.ear_tag || form.querySelector('input:not([type=hidden])')).focus();
    } catch (error) {
      hint.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
  dialog.addEventListener('close', () => { if (made) { made = 0; window.location.reload(); } });

  form.addEventListener('submit', (event) => {
    // Any change to an existing mouse's birth date asks first. In a
    // litter, saying yes moves it to a new litter of its own.
    const dob = form.elements.date_of_birth.value;
    // Filling in a blank date replaces nothing: no question.
    if (!original.id || dob === (original.date_of_birth || '') || !original.date_of_birth) return;
    if (form.dataset.confirmed === '1') { delete form.dataset.confirmed; return; }
    const cohort = original.litter_id && original.date_of_birth && form.elements.litter_id.value === original.litter_id;
    const who = t('mouse %(id)s', { id: original.mouse_id });
    const message = cohort
      ? dobQuestion(who, original.date_of_birth, dob) + '\n\n'
        + t('It is in litter %(litter)s, so it will move to a new litter of its own. The other mice keep their date.', { litter: original.litter_id })
      : dobQuestion(who, original.date_of_birth || '', dob);
    event.preventDefault();
    const submitter = event.submitter;
    BioDialog.confirm(message, { okLabel: t('Change the date') }).then((ok) => {
      if (!ok) return;
      if (cohort) form.elements.confirm_cohort_dob_change.value = '1';
      form.dataset.confirmed = '1';
      form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
    });
  });
})();


/* The cage sheet. static/sheet.js saves the cells; this keeps the parts it
   does not know about in step: the sub-row of mice, the wean-due date
   (as the server answers it), the shared cell of breeder cages, the chips'
   data, and the dialog. */
(function () {
  const sheet = document.querySelector('.cage-sheet');
  if (!sheet) return;
  const cageRow = (form) => document.getElementById(`cage-${form.id.replace('cage-update-', '')}`);
  const showWean = (row, due, state) => {
    const cell = row && row.querySelector('[data-cage-wean]');
    if (!cell) return;
    cell.textContent = due || '–';
    cell.dataset.state = state || '';
    row.dataset.wean_due = due || '';
  };

  // Open a cage: its mice and per-cage actions in a sub-row. The arrow at
  // the start of the row and its mice count both do it (the number is for typing).
  // A long sheet (paged by the server) holds only the page shown: a cage
  // off it is found through the sheet (BioSheets), and one not yet fetched
  // takes the state when it comes (rows-loaded, below).
  const rowOf = (id) => document.getElementById(`cage-${id}`) || window.BioSheets.row(id, sheet);
  const detailOf = (id) => {
    const row = rowOf(id);
    return document.getElementById(`cage-detail-${id}`) || (row && window.BioSheets.detail(row));
  };
  const setOpen = (id, open) => {
    const detail = detailOf(id);
    const row = rowOf(id);
    if (!detail || !row || row._stub) return;
    detail.hidden = !open;
    row.classList.toggle('is-expanded', open);
    row.querySelectorAll('[data-cage-expand]').forEach((b) => b.setAttribute('aria-expanded', open ? 'true' : 'false'));
  };
  const toggle = (id) => {
    const detail = detailOf(id);
    if (detail) setOpen(id, detail.hidden);
  };
  sheet.addEventListener('click', (event) => {
    const button = event.target.closest('[data-cage-expand]');
    if (button) { toggle(button.dataset.cageExpand); return; }
    const count = event.target.closest('[data-cage-open]');
    if (count) toggle(count.dataset.cageOpen);
  });

  // A cage's mice show the transgene columns the mouse sheet shows: the
  // person's Columns choice there (data-table.js keeps it on this computer
  // by column number; see mouse_tg1_column), else its default, as the page
  // drew them.
  const showTransgenes = (root) => {
    try {
      const raw = localStorage.getItem(COLONY.hiddenColumnsKey);
      if (raw) {
        const hiddenCols = new Set(JSON.parse(raw));
        root.querySelectorAll('[data-tg-table] [data-tg]').forEach((cell) => {
          cell.hidden = hiddenCols.has(COLONY.firstTransgeneColumn - 1 + Number(cell.dataset.tg));
        });
      }
    } catch (_) { /* storage unavailable: the page's default stands */ }
  };
  showTransgenes(sheet);

  // Every cage shows open, with its mice, unless this person chose Close
  // all (remembered on this computer). Open all / Close all sets every cage
  // at once; the arrow or count still opens or closes one.
  const OPEN_KEY = 'cages:open';
  const all = sheet.querySelector('[data-cage-expand-all]');
  let allOpen = true;   // what a cage fetched later starts as (rows-loaded, below)
  const setAll = (open) => {
    allOpen = open;
    window.BioSheets.rows(sheet).forEach((row) => setOpen(row.dataset.id, open));
    if (all) {
      all.setAttribute('aria-pressed', open ? 'true' : 'false');
      all.querySelector('span').textContent = open ? t('Close all') : t('Open all');
    }
  };
  let startOpen = true;
  try { startOpen = localStorage.getItem(OPEN_KEY) !== 'closed'; } catch (_) {}
  if (!startOpen) setAll(false);
  if (all) {
    all.addEventListener('click', () => {
      const open = all.getAttribute('aria-pressed') !== 'true';
      setAll(open);
      try { localStorage.setItem(OPEN_KEY, open ? 'open' : 'closed'); } catch (_) {}
    });
  }

  // Cards: the same cage panels, moved into a grid of cards while that
  // layout is shown, and back into their rows for the table.
  const cardsPanel = document.querySelector('[data-cage-cards-panel]');
  const grid = cardsPanel && cardsPanel.querySelector('[data-cage-cards]');
  let cardFilter = 'active';
  const filterCards = () => {
    if (!grid) return;
    const q = (cardsPanel.querySelector('[data-cage-card-search]').value || '').trim().toLowerCase();
    let shown = 0;
    grid.querySelectorAll('[data-cage-card]').forEach((card) => {
      // A purpose shows only its cages with living mice; All shows past ones too.
      const byPurpose = cardFilter.startsWith('purpose:') ? cardFilter.slice(8) : null;
      const ok = (!cardFilter || (byPurpose !== null
        ? (card.dataset.purpose || '') === byPurpose && card.dataset.active === 'true'
        : card.dataset[cardFilter] === 'true'))
        && (!q || card.dataset.search.includes(q));
      card.hidden = !ok;
      if (ok) shown += 1;
    });
    cardsPanel.querySelector('[data-cage-cards-none]').hidden = shown > 0;
  };
  // A long sheet fetches every cage's panel first (BioSheets.readyAll).
  let cardsShown = false;
  const loading = cardsPanel && cardsPanel.querySelector('[data-cage-cards-loading]');
  const showCards = (on) => {
    if (!grid) return;
    cardsShown = on;
    if (on) {
      if (loading) loading.hidden = false;
      window.BioSheets.readyAll(sheet).then((rows) => {
        if (loading) loading.hidden = true;
        if (cardsShown) placeCards(rows);
      }, (error) => {
        if (loading) loading.textContent = t('Couldn’t load the cages: %(error)s', { error: error.message });
      });
    } else {
      grid.querySelectorAll('[data-cage-card]').forEach((card) => {
        card.hidden = false;
        const detail = detailOf(card.dataset.cageCard);
        const home = detail && detail.querySelector(':scope > td');
        if (home) home.appendChild(card);
      });
    }
  };
  const placeCards = (rows) => {
    rows.forEach((row) => {
      const detail = window.BioSheets.detail(row);
      const card = detail && detail.querySelector('[data-cage-card]');
      if (card) grid.appendChild(card);
    });
    filterCards();
    // Arriving from a cage card's QR code (#cage-12): that card, marked.
    const scanned = (location.hash.match(/^#cage-(\d+)$/) || [])[1];
    const card = scanned && grid.querySelector(`[data-cage-card="${scanned}"]`);
    if (card) {
      card.hidden = false;
      card.classList.add('is-scanned');
      card.scrollIntoView({ block: 'center' });
    }
  };

  // Cages fetched as they are shown (a long sheet): open or closed as the
  // sheet is (Open all / Close all), their mice's transgene columns as the
  // mouse sheet's.
  sheet.addEventListener('dt:rows-loaded', (event) => {
    ((event.detail && event.detail.rows) || []).forEach((row) => setOpen(row.dataset.id, allOpen));
    ((event.detail && event.detail.details) || []).forEach(showTransgenes);
  });
  if (grid) {
    const scope = cardsPanel.closest('[data-layout-scope]');
    scope.addEventListener('layout:change', (event) => showCards(event.detail.layout === 'cards'));
    if (!cardsPanel.closest('[data-layout-panel]').hidden) showCards(true);
    cardsPanel.querySelector('[data-cage-card-search]').addEventListener('input', filterCards);
    cardsPanel.querySelectorAll('[data-cage-card-filter]').forEach((chip) => chip.addEventListener('click', () => {
      cardFilter = chip.dataset.cageCardFilter;
      cardsPanel.querySelectorAll('[data-cage-card-filter]').forEach((c) => c.setAttribute('aria-pressed', c === chip ? 'true' : 'false'));
      filterCards();
    }));
  }

  // Arriving from a cage card's QR code (#cage-12): the table has already
  // brought that cage onto the page (data-table.js reveal); open it, so its
  // mice are what you see at the rack, and mark it.
  sheet.addEventListener('dt:revealed', (event) => {
    const row = event.target;
    const match = (row.id || '').match(/^cage-(\d+)$/);
    if (!match) return;
    document.querySelectorAll('tr.is-scanned').forEach((other) => other.classList.remove('is-scanned'));
    row.classList.add('is-scanned');
    const detail = document.getElementById(`cage-detail-${match[1]}`);
    const button = row.querySelector(`[data-cage-expand="${match[1]}"]`);
    if (button && detail && detail.hidden) button.click();
  });

  sheet.addEventListener('sheet:saved', (event) => {
    const { form, body } = event.detail || {};
    if (!form) return;
    const values = (body && body.row && body.row.values) || {};
    if (form.id.startsWith('cage-mouse-update-')) {
      // A mouse's status decides whether its cage still holds living mice.
      const detail = document.querySelector(`[form="${form.id}"]`)?.closest('tr.cage-detail');
      const row = detail && document.getElementById(`cage-${detail.dataset.detailFor}`);
      if (row) {
        const alive = Boolean(detail.querySelector('.life-dot.is-alive'));
        const flag = row.querySelector('[data-cage-alive]');
        if (flag) flag.value = alive ? '1' : '0';
        window.BioSheet.refreshDot(row, alive);
      }
      return;
    }
    if (!form.id.startsWith('cage-update-')) return;
    const row = cageRow(form);
    if (!row) return;
    const active = body && body.row ? body.row.active : undefined;
    const flag = row.querySelector('[data-cage-alive]');
    if (flag && typeof active === 'boolean') flag.value = active ? '1' : '0';
    showWean(row, values.wean_due, values.wean_state);
    row.dataset.breeding = values.breeding || '0';
    // A renumbered cage: its panel and its card say the new number.
    if (values.cage_id) {
      row.dataset.cage_id = values.cage_id;
      const card = document.querySelector(`[data-cage-card="${row.dataset.id}"]`);
      const title = card && card.querySelector('.cage-detail-title .ident');
      if (title) title.textContent = values.cage_id;
      if (card) card.setAttribute('aria-label', t('Cage %(id)s', { id: values.cage_id }));
    }
    // Read as the row shows it, whether a field or a cell drawn as text (static/text-cells.js).
    const rack = BioTextCells.read(row, 'rack_id');
    row.dataset.rack_name = rack && rack.value ? rack.text : '';
    if ('purpose' in values) {
      row.dataset.purpose = String(values.purpose || '').trim().toLowerCase();
      const card = document.querySelector(`[data-cage-card="${row.dataset.id}"]`);
      if (card) card.dataset.purpose = row.dataset.purpose;
    }
    // Only its owner or an admin shares a cage: the server says why not
    // (share_lock) and what it is now.
    // Drawn as text, the menu becomes its field only when its lock or its value changed.
    let shared = row.querySelector('select[name="is_shared"]');
    const drawn = !shared && row.querySelector('.cell-text[data-sort-for="is_shared"]');
    if (drawn && !row.classList.contains('is-locked')
        && (Boolean(values.share_lock) !== drawn.classList.contains('is-disabled')
            || ('is_shared' in values && String(values.is_shared) !== (drawn.dataset.sortValue || '')))) {
      shared = BioTextCells.open(drawn, true);
    }
    if (shared && !shared.closest('tr').classList.contains('is-locked')) {
      const lock = values.share_lock || '';
      shared.disabled = Boolean(lock);
      shared.title = lock;
      shared.dataset.shareLock = lock;
      if ('is_shared' in values) shared.value = values.is_shared;
      shared.dataset.scope = shared.value === '1' ? 'lab' : (/^g\d+$/.test(shared.value) ? 'group' : 'mine');
    }
    const genotype = row.querySelector('input[name="genotype_summary"]');
    if (genotype) genotype.title = genotype.value || genotype.placeholder;
    else {
      const text = row.querySelector('.cell-text[data-sort-for="genotype_summary"]');
      if (text) text.title = text.textContent.trim();
    }
    const detail = document.getElementById(`cage-detail-${row.dataset.id}`);
    const breeding = detail && detail.querySelector('[data-breeding-actions]');
    // Litter born, Genotyping and Wean: a mating (Breeding) cage's only, as
    // the server says (app/litters.py).
    if (breeding && 'breeding' in values) breeding.classList.toggle('hidden-row', values.breeding !== '1');
    // The dialog opens with what the row now shows.
    const open = row.querySelector('[data-record-edit="cage-dialog"]');
    if (open) {
      try {
        const payload = JSON.parse(open.dataset.recordPayload || '{}');
        ['rack_id', 'position', 'purpose', 'cage_location', 'genotype_summary', 'notes', 'date_give_birth']
          .forEach((key) => { if (key in values) payload[key] = values[key]; });
        open.dataset.recordPayload = JSON.stringify(payload);
      } catch (_) { /* leave it */ }
    }
  });

  // "How many" is for new cages only.
  const dialog = document.getElementById('cage-dialog');
  dialog?.addEventListener('record-dialog:open', (event) => {
    const isNew = Boolean(event.detail && event.detail.isNew);
    dialog.querySelectorAll('[data-new-only]').forEach((el) => { el.hidden = !isNew; });
    const count = dialog.querySelector('input[name="count"]');
    if (count) { count.disabled = !isNew; if (isNew && !count.value) count.value = '1'; }
  });
})();

// Keep the note input's `title` attribute in sync with its current value so
// hovering always shows the latest text — without this, the tooltip would be
// stuck on whatever was server-rendered until the page is refreshed.
function wireNote(input) {
  const sync = () => { input.title = input.value || ''; };
  input.addEventListener('input', sync);
  input.addEventListener('change', sync);
}
document.querySelectorAll('input.note-cell').forEach(wireNote);

// A big lab's Breeders tab draws its first cards in full and the rest empty
// (data-breeder-stub; colony_routes.BREEDERS_DRAWN_OVER), each carrying what
// the search, Alive only and the count read. Then at most breederShowing of
// the cards that match are shown, with Show more for the rest, and an empty
// card is filled from /colony/breeders/cards when it is to be shown.
const BREEDERS_AT_ONCE = 100;
let breederShowing = BREEDERS_AT_ONCE;
const breederAsked = new Set();

function filterBreeders() {
  const searchInput = document.getElementById('breeder-search');
  const activeOnly = document.getElementById('breeder-active-only');
  if (!searchInput) return;
  const stack = document.getElementById('breeder-stack');
  const paged = !!(stack && stack.dataset.breederCardsUrl);
  const query = searchInput.value.trim().toLowerCase();
  const onlyActive = activeOnly ? activeOnly.checked : false;
  let visibleCards = 0;
  let totalMatches = 0;
  const toFill = [];
  document.querySelectorAll('.breeder-card').forEach((card) => {
    const haystack = (card.dataset.breederSearch || '').toLowerCase();
    const matchesQuery = !query || haystack.includes(query);
    const activeCount = parseInt(card.dataset.activeCount || '0', 10);
    const matchesActive = !onlyActive || activeCount > 0;
    const visible = matchesQuery && matchesActive;
    if (visible) visibleCards += 1;
    const shown = visible && (!paged || visibleCards <= breederShowing);
    card.style.display = shown ? '' : 'none';
    if (shown && card.dataset.breederStub && !breederAsked.has(card.dataset.breederStub)) toFill.push(card.dataset.breederStub);

    let cardMatches = 0;
    card.querySelectorAll('tbody tr[data-mouse-active]').forEach((row) => {
      const rowActive = row.dataset.mouseActive === 'true';
      if (onlyActive && !rowActive) {
        row.style.display = 'none';
        row.classList.remove('breeder-match');
        return;
      }
      row.style.display = '';
      if (query && shown) {          // matches counted in the cards shown
        const rowText = (row.textContent || '').toLowerCase();
        if (rowText.includes(query)) {
          row.classList.add('breeder-match');
          cardMatches += 1;
          totalMatches += 1;
        } else {
          row.classList.remove('breeder-match');
        }
      } else {
        row.classList.remove('breeder-match');
      }
    });
    if (shown && query && cardMatches > 0) {
      card.classList.add('breeder-card-has-match');
    } else {
      card.classList.remove('breeder-card-has-match');
    }
  });
  const counter = document.getElementById('breeder-count');
  if (counter) {
    const total = document.querySelectorAll('.breeder-card').length;
    let text = visibleCards === total ? t('%(n)s cages', { n: total })
      : t('%(shown)s / %(total)s cages', { shown: visibleCards, total: total });
    if (query && totalMatches > 0) {
      text += ' · ' + (totalMatches === 1 ? t('%(n)s match', { n: totalMatches }) : t('%(n)s matches', { n: totalMatches }));
    }
    counter.textContent = text;
  }
  const more = document.getElementById('breeder-more');
  if (more) {
    const left = paged ? visibleCards - breederShowing : 0;
    more.hidden = left <= 0;
    if (left > 0) more.textContent = t('Show %(n)s more', { n: Math.min(left, BREEDERS_AT_ONCE) });
  }
  if (toFill.length) fillBreeders(stack, toFill);
}

function fillBreeders(stack, ids) {
  ids.forEach((id) => breederAsked.add(id));
  const url = new URL(stack.dataset.breederCardsUrl, window.location.href);
  url.searchParams.set('ids', ids.join(','));
  fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((answer) => {
      const box = document.createElement('div');
      box.innerHTML = answer.cards;
      box.querySelectorAll('.breeder-card[data-breeder-id]').forEach((card) => {
        const empty = stack.querySelector('[data-breeder-stub="' + card.dataset.breederId + '"]');
        if (empty) empty.replaceWith(card);
      });
      filterBreeders();
    }, () => {
      ids.forEach((id) => breederAsked.delete(id));   // the next search or Show more asks again
      const say = window.BiomanagerShell && window.BiomanagerShell.toast;
      if (say) say(t('Some rows could not be loaded. Check the connection and try again.'), 'error');
    });
}

function moreBreeders() {
  breederShowing += BREEDERS_AT_ONCE;
  filterBreeders();
}

// Pick: asks, then posts the tab's one form to that mouse's address.
document.addEventListener('click', (event) => {
  const button = event.target.closest && event.target.closest('[data-pick-action]');
  if (!button) return;
  const text = button.dataset.pickConfirm;
  window.BioDialog.confirm(text, { danger: window.BioDialog.looksDestructive(text) }).then((ok) => {
    if (!ok) return;
    const form = document.getElementById('breeder-pick');
    form.action = button.dataset.pickAction;
    form.submit();
  });
});

function resetBreederSearch() {
  const searchInput = document.getElementById('breeder-search');
  const activeOnly = document.getElementById('breeder-active-only');
  if (searchInput) searchInput.value = '';
  if (activeOnly) activeOnly.checked = false;
  breederShowing = BREEDERS_AT_ONCE;
  filterBreeders();
}

if (document.getElementById('breeder-search')) {
  filterBreeders();
}

/* Rows of a long mouse sheet that come from the server as they are shown
   (data-table.js) are wired as the page's own rows were. */
document.addEventListener('dt:rows-loaded', (event) => {
  [...((event.detail && event.detail.rows) || []), ...((event.detail && event.detail.details) || [])].forEach((tr) => {
    tr.querySelectorAll('[data-autosave="1"]').forEach(wireAutosave);
    tr.querySelectorAll('input.note-cell').forEach(wireNote);
  });
});

/* A cell drawn as text that becomes its field (static/text-cells.js) is
   wired as the fields were when the page loaded. */
document.addEventListener('text-cells:open', (event) => {
  const input = event.target;
  if (input.matches('[data-autosave="1"]')) wireAutosave(input);
  if (input.matches('input.note-cell')) wireNote(input);
});

  BioActions.register({ addWeanRow: addWeanRow, filterBreeders: filterBreeders, moreBreeders: moreBreeders, openExportModal: openExportModal, printMouseTable: printMouseTable, resetBreederSearch: resetBreederSearch });
