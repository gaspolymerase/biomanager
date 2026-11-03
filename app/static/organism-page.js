/* An organism database's pages (organisms/module.html): its dialogs and rows.
 * Who is signed in is in #organism-page-data. */
const ORGANISM = JSON.parse(document.getElementById('organism-page-data').textContent);
const CURRENT_USER = ORGANISM.currentUser;

/* Fill a dialog's form from a row payload and open it.
 *
 * Inputs are matched by name, so the same helper drives every entity;
 * `attrs` is the module's custom fields, which are named attr_<key>.
 * A payload with `locked` opens the dialog read-only. */
function orgEdit(dialogId, payload) {
  const dialog = document.getElementById(dialogId);
  if (!dialog) return;
  const form = dialog.querySelector('form[data-entity-form]');
  if (!form) return;

  form.reset();
  form.querySelectorAll('option[data-kept]').forEach((option) => option.remove());
  const set = (name, value) => {
    const element = form.elements[name];
    if (!element) return;
    if (element.type === 'checkbox') { element.checked = Boolean(value) && value !== '0'; return; }
    const text = (value === null || value === undefined) ? '' : String(value);
    // A stored value no longer offered (a former member as owner, a status
    // since removed from Configure) still shows as itself.
    if (element.tagName === 'SELECT' && text && !Array.from(element.options).some((o) => o.value === text)) {
      const kept = new Option(text, text);
      kept.dataset.kept = '1';
      element.add(kept);
    }
    element.value = text;
  };

  Object.entries(payload || {}).forEach(([key, value]) => {
    if (key === 'attrs' || key === 'locked' || key === 'heading') return;
    set(key, value);
  });
  Object.entries((payload && payload.attrs) || {}).forEach(([key, value]) => {
    set('attr_' + key, value);
  });

  // A new record belongs to whoever creates it. Unowned records are
  // editable by everyone under the sharing rules, so blank is not a
  // neutral default.
  const isNew = !(payload && payload.id);
  if (isNew && form.elements.owner && !(payload && payload.owner)) set('owner', CURRENT_USER);

  const locked = Boolean(payload && payload.locked);
  form.querySelectorAll('input:not([type=hidden]), select, textarea, button[type=submit]').forEach((el) => {
    el.disabled = locked;
  });
  // "How many" is for creating only; an edit neither shows nor sends it.
  form.querySelectorAll('[data-new-only]').forEach((el) => {
    el.hidden = !isNew;
    el.querySelectorAll('input, select').forEach((input) => { input.disabled = locked || !isNew; });
  });
  // Rows for a harvest only (the seed lot dialog's From): shown when the
  // payload names what the seed came from.
  const harvesting = isNew && Boolean(payload && payload.source_kind);
  form.querySelectorAll('[data-source-only]').forEach((el) => {
    el.hidden = !harvesting;
    el.querySelectorAll('input, select').forEach((input) => { input.disabled = locked || !harvesting; });
  });
  const note = form.querySelector('[data-locked-note]');
  if (note) {
    note.hidden = !locked;
    const owner = payload && payload.owner;
    note.querySelector('span').textContent = owner
      ? t('Read only: %(owner)s’s record. Ask them, or an admin, to change it.', { owner: owner })
      : t('Read only: someone else’s record. Ask them, or an admin, to change it.');
  }

  // "New vial" / "Edit vial" rather than a bare noun.
  const heading = dialog.querySelector('.record-dialog-head h3');
  if (heading) {
    heading.dataset.base = heading.dataset.base || heading.textContent.trim();
    const base = heading.dataset.base;
    const noun = base.charAt(0).toLowerCase() + base.slice(1);
    heading.textContent = (payload && payload.heading) ? payload.heading
      : isNew ? t('New %(noun)s', { noun: noun })
      : (locked ? t('View %(noun)s', { noun: noun }) : t('Edit %(noun)s', { noun: noun }));
  }

  dialog.showModal();
}

/* Buttons carry the dialog id and the row as data attributes. JSON is
   never placed inside an onclick attribute. */
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-org-edit]');
  if (!button) return;
  let payload = {};
  try { payload = JSON.parse(button.dataset.orgPayload || '{}'); } catch (e) { console.error(e); }
  orgEdit(button.dataset.orgEdit, payload);
});

/* Status → date removed, as the mouse sheet does it: an ending status
   fills today's date when empty; moving back out of one clears it. The
   server applies the same rule; this keeps the row honest meanwhile. */
document.addEventListener('sheet:change', (event) => {
  const input = event.detail && event.detail.input;
  if (!input || input.name !== 'status' || input.dataset.deadValues === undefined) return;
  const dead = input.dataset.deadValues.toLowerCase().split(',').map((v) => v.trim()).filter(Boolean);
  const isDead = (v) => dead.includes(String(v || '').trim().toLowerCase());
  const died = document.querySelector(`[form="${input.getAttribute('form')}"][name="death_on"]`);
  if (!died) return;
  if (isDead(input.value) && !died.value) died.value = new Date().toISOString().slice(0, 10);
  else if (!isDead(input.value) && isDead(event.detail.previous)) died.value = '';
  died.dataset.empty = died.value ? '0' : '1';
});

/* In the rule builder, only show anchors that belong to the chosen
   subject — a cohort has no "last flip" date. */
(function () {
  const subject = document.getElementById('rule-subject');
  const anchor = document.getElementById('rule-anchor');
  if (!subject || !anchor) return;
  const options = Array.from(anchor.options);
  function sync() {
    let firstVisible = null;
    options.forEach((option) => {
      const match = option.dataset.subject === subject.value;
      option.hidden = !match;
      option.disabled = !match;
      if (match && !firstVisible) firstVisible = option;
    });
    if (firstVisible && (!anchor.selectedOptions[0] || anchor.selectedOptions[0].hidden)) {
      firstVisible.selected = true;
    }
  }
  subject.addEventListener('change', sync);
  sync();
})();
