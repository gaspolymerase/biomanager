/* An inventory's Configure page (inventory/configure.html): its fields' rows.
 * The database's words for an item are in #inventory-configure-data. */
const INV_CONFIGURE = JSON.parse(document.getElementById('inventory-configure-data').textContent);
(function () {
  const body = document.querySelector('[data-field-body]');
  const count = document.querySelector('[data-field-count]');
  document.querySelector('[data-add-field]').addEventListener('click', () => {
    const last = body.querySelector('[data-field-row]:last-child');
    const n = Number(count.value);
    const row = last.cloneNode(true);
    row.querySelectorAll('[name]').forEach((el) => {
      el.name = el.name.replace(/^field_\d+_/, `field_${n}_`);
      if (el.type === 'checkbox') el.checked = el.name.endsWith('_in_table');
      else if (el.tagName === 'SELECT') el.selectedIndex = 0;
      else el.value = el.name.endsWith('_width') ? '130' : '';
    });
    row.classList.remove('is-removed');
    body.appendChild(row);
    count.value = n + 1;
    row.querySelector('[name$="_label"]').focus();
  });
  body.addEventListener('change', (e) => {
    if (e.target.matches('[data-remove-toggle]')) e.target.closest('tr').classList.toggle('is-removed', e.target.checked);
  });
  /* Move up / Move down: the rows are numbered again in the order shown,
     and the server keeps the columns in that order (field_0, field_1…),
     which is the order the sheet shows them in. */
  const renumber = () => {
    body.querySelectorAll('[data-field-row]').forEach((row, i) => {
      row.querySelectorAll('[name]').forEach((el) => { el.name = el.name.replace(/^field_\d+_/, `field_${i}_`); });
    });
  };
  body.addEventListener('click', (e) => {
    const button = e.target.closest('[data-field-move]');
    if (!button) return;
    const row = button.closest('[data-field-row]');
    if (button.dataset.fieldMove === '-1' && row.previousElementSibling) body.insertBefore(row, row.previousElementSibling);
    if (button.dataset.fieldMove === '1' && row.nextElementSibling) body.insertBefore(row.nextElementSibling, row);
    renumber();
    button.focus();
  });
})();

/* Statuses and categories: one row per value, so a rename is known to
   be a rename. Removing a value items still hold asks what they become;
   saving asks first when anything will be relabelled. */
(function () {
  const form = document.getElementById('configure-form');
  const noun = INV_CONFIGURE.noun, plural = INV_CONFIGURE.nouns;
  const editors = Array.from(form.querySelectorAll('[data-choice-editor]'));
  const n = (k) => t('%(n)s %(noun)s', { n: k, noun: k === 1 ? noun : plural });

  function kept(editor) {
    const seen = new Set();
    return Array.from(editor.querySelectorAll('[data-choice-row]')).filter((tr) => {
      const remove = tr.querySelector('[data-choice-remove]');
      return !(remove && remove.checked);
    }).map((tr) => tr.querySelector('[data-choice-value]').value.trim())
      .filter((v) => v && !seen.has(v.toLowerCase()) && seen.add(v.toLowerCase()));
  }

  function sync() {
    const changes = [];
    editors.forEach((editor) => {
      const values = kept(editor);
      editor.querySelectorAll('[data-choice-row]').forEach((tr) => {
        const was = tr.querySelector('[data-choice-was]').value;
        const input = tr.querySelector('[data-choice-value]');
        const remove = tr.querySelector('[data-choice-remove]');
        const count = Number(tr.dataset.count || 0);
        const now = input.value.trim();
        const gone = Boolean(was) && ((remove && remove.checked) || !now);
        tr.classList.toggle('is-removed', gone);
        const replace = tr.querySelector('[data-choice-replace]');
        if (replace) {
          const select = replace.querySelector('select');
          const others = values.filter((v) => v.toLowerCase() !== was.toLowerCase());
          const needs = gone && count > 0 && values.length > 0
            && !values.some((v) => v.toLowerCase() === was.toLowerCase());
          replace.hidden = !needs;
          select.required = needs;
          const current = select.value;
          select.replaceChildren(new Option(t('Pick…'), ''), ...others.map((v) => new Option(v, v)));
          select.value = others.includes(current) ? current : '';
          if (needs && select.value) changes.push(t('%(items)s from “%(was)s” to “%(now)s”', { items: n(count), was, now: select.value }));
        }
        if (!gone && was && count && now !== was) changes.push(t('%(items)s from “%(was)s” to “%(now)s”', { items: n(count), was, now }));
      });
    });
    if (changes.length) form.dataset.confirm = t('Relabel %(changes)s?', { changes: changes.join(t('; ')) });
    else delete form.dataset.confirm;
  }

  editors.forEach((editor) => {
    const body = editor.querySelector('[data-choice-body]');
    const count = editor.querySelector('[data-choice-count]');
    const name = editor.dataset.choiceEditor;
    editor.querySelector('[data-choice-add]').addEventListener('click', () => {
      const i = Number(count.value);
      const tr = document.createElement('tr');
      tr.dataset.choiceRow = '';
      tr.dataset.count = '0';
      tr.innerHTML = '<td><input type="hidden" data-choice-was><input type="text" data-choice-value></td><td class="choice-usage"></td><td></td><td class="choice-replace"></td>';
      tr.querySelector('[data-choice-was]').name = `${name}_${i}_was`;
      const input = tr.querySelector('[data-choice-value]');
      input.placeholder = t('New…');
      input.setAttribute('aria-label', t('Name'));
      input.name = `${name}_${i}_value`;
      input.maxLength = name === 'statuses' ? 40 : 80;
      body.appendChild(tr);
      count.value = i + 1;
      input.focus();
    });
  });
  form.addEventListener('input', sync);
  form.addEventListener('change', sync);
  sync();
})();
