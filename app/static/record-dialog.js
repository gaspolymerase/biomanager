/* Record dialog: one <dialog> serves both New and Edit for a database page.
 *
 *   <button data-record-edit="sample-dialog"
 *           data-record-payload='{"id": 4, "sample_id": "S-12", ...}'>
 *
 *   <dialog id="sample-dialog" class="record-dialog">
 *     <form method="post" data-record-form
 *           data-create-action="/samples"
 *           data-update-action="/samples/0/update"      ← /0/ becomes the id
 *           data-new-title="New sample" data-edit-title="Sample {label}"
 *           data-new-submit="Create sample" data-edit-submit="Save">
 *       <h3 data-record-title></h3> … <button data-record-submit>
 *
 * Fields are filled by name from the payload; a field the payload does not
 * mention keeps its default, which is how New gets sensible defaults.
 * Payload keys starting with "_" are metadata: _label names the record in
 * the title, _locked opens it read-only.
 *
 * The row travels as a data attribute, never inside an onclick: Jinja's
 * tojson leaves double quotes unescaped, which silently broke every Add
 * button on the organism pages.
 */
(function () {
  'use strict';

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-record-edit]');
    if (!button) return;
    const dialog = document.getElementById(button.dataset.recordEdit);
    // A stored value that is a choice in another case ("Rabbit" for
    // "rabbit") shows as that choice; one that is no choice at all (typed in
    // the sheet, or from an import) is kept as an extra option rather than
    // showing blank, and saved back as it was.
    function setChoice(select, raw) {
      const value = raw === null || raw === undefined ? '' : String(raw);
      select.querySelectorAll('option[data-kept]').forEach((o) => o.remove());
      const options = Array.from(select.options);
      const same = options.find((o) => o.value === value)
        || options.find((o) => o.value.toLowerCase() === value.toLowerCase());
      if (same) { select.value = same.value; return; }
      if (value) {
        const kept = new Option(value, value, true, true);
        kept.dataset.kept = '';
        select.add(kept);
      }
      select.value = value;
    }
    const form = dialog && dialog.querySelector('form[data-record-form]');
    if (!form) return;

    let data = {};
    try { data = JSON.parse(button.dataset.recordPayload || '{}'); } catch (e) { console.error(e); }
    const isNew = !data.id;
    const locked = Boolean(data._locked);

    form.reset();
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      if (Object.prototype.hasOwnProperty.call(data, el.name)) {
        const value = data[el.name];
        // A box of a several-choice field is ticked when its value is in the list.
        if (el.type === 'checkbox' && 'multi' in el.dataset) el.checked = String(value || '').split(',').map((v) => v.trim()).includes(el.value);
        else if (el.type === 'checkbox') el.checked = Boolean(value);
        else if (el.tagName === 'SELECT') setChoice(el, value);
        else el.value = value === null || value === undefined ? '' : value;
      }
      if (el.type !== 'hidden' && el.tagName !== 'BUTTON') el.disabled = locked;
    });

    form.action = isNew
      ? form.dataset.createAction
      : form.dataset.updateAction.replace('/0/', `/${data.id}/`);

    const title = dialog.querySelector('[data-record-title]');
    if (title) {
      title.textContent = isNew
        ? form.dataset.newTitle
        : (form.dataset.editTitle || t('Edit')).replace('{label}', data._label || '');
    }
    const submit = dialog.querySelector('[data-record-submit]');
    if (submit) {
      submit.textContent = isNew ? (form.dataset.newSubmit || t('Create')) : (form.dataset.editSubmit || t('Save'));
      submit.hidden = locked;
    }

    dialog.dispatchEvent(new CustomEvent('record-dialog:open', { detail: { data, isNew } }));
    dialog.showModal();
    const first = form.querySelector('input:not([type=hidden]):not(:disabled), select:not(:disabled), textarea:not(:disabled)');
    if (first) first.focus();
  });
})();
