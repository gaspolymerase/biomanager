/* An experiment's page (templates/experiment.html, app/experiments.py,
   app/experiment_steps.py): the sheet of its animals, switching between the
   treatments each got and the readout (editable in place); the readout over
   the days, the manipulation days dashed and explained on hover; the plan
   day by day; and the dialogs that add animals, plan, record a
   manipulation and record a readout day. Every change answers with the
   whole page's data, and the page is drawn again from it. */
(function () {
  'use strict';
  const dataEl = document.getElementById('xp-data');
  if (!dataEl) return;
  let D = JSON.parse(dataEl.textContent || '{}');
  const API = `/colony/experiments/${D.id}`;      // manipulations (experiment_steps.py)
  const XP = `/experiments/${D.id}`;              // the experiment itself (experiments.py)
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  let view = 'treatment';
  let scale = null;           // raw values, or %: a count starts as % of those at the start
  try { view = sessionStorage.getItem(`xp:view:${D.id}`) || 'treatment'; } catch (_e) { /* no storage */ }

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const icon = (name) => `<svg class="icon xp-icon" aria-hidden="true"><use href="/static/icons.svg#${esc(name)}"></use></svg>`;
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
  const LOCALE = window.BM_LANG === 'zh' ? 'zh-CN' : undefined;
  // A label that arrives as a value (a readout, a kind, the animals' noun): in the page's language.
  const tc = (ctx, s) => { const k = `${ctx}::${s}`; const v = t(k); return v === k ? t(s) : v; };
  const RL = () => t(D.readout.label);
  const verb = () => tc('readout', D.readout.verb || 'Record');
  const unitOf = () => (D.readout.unit ? t(D.readout.unit) : '');
  const noun = () => t(D.noun);
  const nouns = () => t(D.nouns);
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  // A day's title in the page's language, from its manipulation: what was given, or its kind.
  const titleOf = (r) => {
    const s = (D.steps || []).find((x) => x.id === r.step_id);
    if (!s) return titleOf(r);
    if (s.reading) return RL();
    return [s.agent || t(s.kind_label), s.dose, s.route].filter(Boolean).join(' ');
  };
  const niceDate = (iso, weekday) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString(LOCALE,
    weekday ? { weekday: 'short', day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short' }) : '');
  const num = (v, digits = 1) => (v === null || v === undefined || Number.isNaN(v) ? ''
    : Number(v).toFixed(digits).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, ''));
  const fraction = () => D.readout.kind === 'fraction';
  // Another measure a plate filled beside the main readout (an ELISA): its own chip on the sheet, which the chart follows.
  const viewed = () => (String(view).startsWith('measure:') && (D.measures || []).find((m) => m.key === view)) || null;
  const VR = () => viewed() || D.readout;
  const VT = () => (viewed() ? viewed().table : D.table);
  const vFraction = () => VR().kind === 'fraction';
  const vRL = () => t(VR().label);
  const vUnit = () => (VR().unit ? t(VR().unit) : '');
  // A readout BioManager works out (app/derived.py): typed as what was measured (length and width), never as the result.
  const derivedRO = () => Boolean(D.readout.formula);
  const inputsOf = () => D.readout.inputs || [];
  // The main readout's own cells open its day; another measure worked out from Cts is typed in its cell.
  const workedOut = () => Boolean(VR().formula) && !viewed();
  const measuredText = (inputs, of) => (inputs ? (of || inputsOf()).filter((i) => inputs[i.key] != null)
    .map((i) => `${t(i.label)} ${num(inputs[i.key], 3)}${i.unit ? ` ${i.unit}` : ''}`).join(', ') : '');
  const STATE = { done: t('Done'), today: t('Today'), overdue: t('Overdue'), upcoming: t('To come'), planned: t('Planned') };

  // ---------------------------------------------------------------- talking to the app

  function say(state, text) {
    const el = $('[data-save-status]');
    if (!el) return;
    clearTimeout(el._clear);
    el.dataset.state = state;
    el.textContent = text;
    if (state === 'saved') el._clear = setTimeout(() => { el.textContent = ''; el.dataset.state = ''; }, 2400);
  }

  async function send(url, body, { form = false } = {}) {
    say('saving', t('Saving…'));
    const r = await fetch(url, form ? { method: 'POST', body, headers: { 'X-Autosave': '1' } }
      : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Autosave': '1' }, body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) {
      const error = new Error(data.error || t('The app answered %(status)s.', { status: r.status }));
      error.data = data;
      say('error', t('Not saved: %(error)s', { error: error.message }));
      throw error;
    }
    say('saved', t('All changes saved'));
    return data;
  }

  // A change from the plan kept at Start (app/experiment_plan.py): the app
  // answers needs_reason, the person gives one (or Cancel: nothing changes),
  // and it is sent again with it.
  async function askReason(message, checks) {
    for (;;) {
      const answer = await BioDialog.prompt(t('Why the change from the plan?'), '', { message, placeholder: t('A reason, in a line'), okLabel: t('Save with this reason'), checks: checks || undefined });
      if (answer === null || answer === undefined) return null;
      const out = typeof answer === 'string' ? { value: answer, checks: {} } : answer;
      if (String(out.value || '').trim()) return { value: String(out.value).trim(), checks: out.checks || {} };
    }
  }

  async function sendReason(url, body, { form = false, checks = null, extra = null } = {}) {
    try {
      return await send(url, body, { form });
    } catch (error) {
      if (!error.data || !error.data.needs_reason) throw error;
      const answer = await askReason(error.message, checks);
      if (!answer) { say('saved', t('Not changed')); return null; }
      if (form) {
        body.set('reason', answer.value);
        return send(url, body, { form });
      }
      return send(url, { ...body, reason: answer.value, ...(extra ? extra(answer.checks) : {}) });
    }
  }

  async function get(url) {
    const r = await fetch(url);
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) throw new Error(data.error || t('The app answered %(status)s.', { status: r.status }));
    return data;
  }

  // For the Plate dialog (static/plate-layout.js): the page's data, and drawing it again after a save.
  window.BMExperiment = {
    data: () => D, apply: (data) => apply(data), say: (state, text) => say(state, text),
    // After a plate is saved: the sheet and chart show what it filled.
    show: (key) => {
      view = key && key.startsWith('measure:') ? key : 'readout';
      try { sessionStorage.setItem(`xp:view:${D.id}`, view); } catch (_e) { /* no storage */ }
      scale = null;
      drawSheet(); drawChart(); drawLabels();
    },
  };

  function apply(data, { sheet = true } = {}) {
    ['subjects', 'groups', 'steps', 'schedule', 'table', 'readout', 'weighs', 'start_date', 'editable', 'kinds',
      'regimens', 'reagents', 'sample_inventories', 'planned', 'fixed', 'plan', 'readout_day', 'planned_test',
      'deviations', 'comparison', 'measures'].forEach((k) => {
      if (data[k] !== undefined) D[k] = data[k];
    });
    if (String(view).startsWith('measure:') && !viewed()) view = 'readout';
    drawPlan();
    if (data.add) refreshAddOptions(data.add);
    const list = $('#xp-groups');
    if (list) list.innerHTML = D.groups.map((g) => `<option value="${esc(g)}">`).join('');
    if (sheet) drawSheet();
    drawChart();
    drawDays();
    drawLabels();
  }

  const confirmAsk = (text, danger) => BioDialog.confirm(text, { danger: Boolean(danger) });
  const alertSay = (text) => BioDialog.alert(text);

  // ---------------------------------------------------------------- labels that follow the readout

  function drawLabels() {
    const r = D.readout;
    $$('[data-xp-readout-label]').forEach((el) => { el.textContent = RL(); });
    $$('[data-xp-reading-verb]').forEach((el) => { el.textContent = verb(); });
    const note = $('[data-xp-derived-note]');
    if (note) note.textContent = derivedRO() ? t(r.description || '') : '';
    const title = $('[data-xp-chart-title]');
    if (title) title.textContent = `${vRL()}${VR().unit && !vFraction() ? ` (${vUnit()})` : ''}`;
    const chips = $('[data-xp-measures]');
    if (chips) {
      // The measure shown has its ••• beside it: rename it, or delete it with its readings.
      chips.innerHTML = (D.measures || []).map((m) => `<button type="button" class="dt-chip" role="tab" data-xp-view="${esc(m.key)}" aria-pressed="false" title="${esc(m.formula ? `${t('Another measure, beside the main readout')} · ${t(m.kind_label)}: ${m.expression}` : t('Another measure, beside the main readout'))}">${icon('chart')} ${esc(m.label)}</button>${D.editable && m.key === view ? `<button type="button" class="dt-chip xp-measure-more" data-xp-measure-edit="${esc(m.key)}" aria-label="${esc(t('Rename or delete %(measure)s', { measure: m.label }))}" title="${esc(t('Rename or delete %(measure)s', { measure: m.label }))}">${icon('ellipsis')}</button>` : ''}`).join('');
    }
    const [raw, pct] = $$('[data-xp-scale]');
    if (raw && pct) {
      raw.textContent = vFraction() ? t('How many %(unit)s', { unit: vUnit() }).trim() : (vUnit() || t('Value'));
      pct.textContent = vFraction() ? t('% of those at the start') : t('% of the first');
      raw.setAttribute('aria-pressed', String(scale === 'raw'));
      pct.setAttribute('aria-pressed', String(scale === 'pct'));
    }
    $$('[data-xp-view]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.xpView === view)));
  }

  // ---------------------------------------------------------------- the sheet

  const inGroup = (subject, group) => !group || subject.group.trim().toLowerCase() === group.trim().toLowerCase();
  const treatmentCols = () => D.schedule.filter((r) => !r.reading);

  function drawSheet() {
    const table = $('[data-xp-table]');
    const empty = $('[data-xp-empty]');
    const subs = D.subjects;
    empty.hidden = subs.length > 0;
    if (!subs.length) {
      table.innerHTML = '';
      empty.innerHTML = t('No %(nouns)s yet.', { nouns: esc(nouns()) }) + (D.editable ? ` ${t('<b>Add %(nouns)s</b> to start.', { nouns: esc(nouns()) })}` : '');
      return;
    }
    const showSex = subs.some((s) => s.sex);
    const showStart = D.family !== 'mouse';
    const head = [`<th class="sheet-pin sheet-pin-start xp-id">${esc(cap(noun()))}</th>`];
    if (showSex) head.push(`<th>${t('Sex')}</th>`);
    head.push(`<th>${t('Genotype')}</th>`, `<th>${esc(cap(t(D.housing)))}</th>`, `<th>${t('Group')}</th>`);
    if (showStart) head.push(`<th title="${esc(t('How many at the start, for a count or survival'))}">${t('At start')}</th>`);

    let cols = [];
    if (view === 'treatment') {
      cols = treatmentCols();
      if (!cols.length) head.push(`<th class="xp-note-col">${D.editable ? t('No manipulations yet: <b>Record manipulation</b>, or add them to the <b>Plan</b>.') : t('No manipulations yet.')}</th>`);
      cols.forEach((c) => head.push(`<th class="xp-daycol" data-state="${esc(c.state)}" title="${esc(`${t('Day %(n)s', { n: c.day })}${c.date ? `, ${niceDate(c.date, true)}` : ''}: ${titleOf(c)}${c.group ? ` (${c.group})` : ''}`)}">
        <span class="xp-dayhead">${t('Day %(n)s', { n: c.day })}</span><span class="xp-daywhat">${esc(titleOf(c))}</span><span class="xp-daydate">${esc(niceDate(c.date))}${c.group ? ` · ${esc(c.group)}` : ''}</span></th>`));
    } else {
      VT().dates.forEach((d, i) => head.push(`<th class="xp-daycol"><span class="xp-dayhead">${VT().days[i] !== null ? t('Day %(n)s', { n: VT().days[i] }) : ''}</span><span class="xp-daydate">${esc(niceDate(d))}</span></th>`));
      if (D.editable && workedOut()) head.push(`<th class="xp-daycol xp-newday"><button type="button" class="xp-link" data-xp-open="reading">${t('Another day')}</button></th>`);
      else if (D.editable) head.push(`<th class="xp-daycol xp-newday"><span class="xp-dayhead">${t('Another day')}</span><input type="date" class="xp-newday-date" value="${today()}" max="${today()}" aria-label="${esc(t('The day of the new column'))}"></th>`);
    }
    if (D.editable) head.push('<th class="xp-actions"></th>');

    const rowsByKey = Object.fromEntries(VT().rows.map((r) => [r.key, r]));
    const body = subs.map((s) => {
      const out = s.left_out ? ` <span class="xp-left-tag" title="${esc(t('Left out of the analysis: %(reason)s', { reason: s.left_out }))}">${esc(t('left out'))}</span>` : '';
      const cells = [`<td class="sheet-pin sheet-pin-start xp-id"><span class="sheet-id"><span class="life-dot${s.alive ? ' is-alive' : ''}"></span><span class="ident">${esc(s.label)}</span>${out}</span></td>`];
      if (showSex) cells.push(`<td>${esc(s.sex)}</td>`);
      cells.push(`<td class="xp-geno" title="${esc(s.genotype)}">${esc(s.genotype)}</td>`, `<td>${esc(s.housing)}</td>`);
      cells.push(`<td><input class="xp-cell" data-xp-group="${esc(s.key)}" value="${esc(s.group)}" placeholder="${esc(t('(no group)'))}" list="xp-groups" aria-label="${esc(t('Group of %(label)s', { label: s.label }))}" ${D.editable ? '' : 'disabled'}></td>`);
      if (showStart) cells.push(`<td><input class="xp-cell xp-num" data-xp-start="${esc(s.key)}" value="${s.start == null ? '' : s.start}" inputmode="numeric" aria-label="${esc(t('How many %(label)s started with', { label: s.label }))}" ${D.editable ? '' : 'disabled'}></td>`);
      if (view === 'treatment') {
        if (!cols.length) cells.push('<td></td>');
        cols.forEach((c) => cells.push(treatmentCell(s, c)));
      } else {
        const row = rowsByKey[s.key] || { values: [] };
        VT().dates.forEach((d, i) => cells.push(workedOut() ? derivedCell(d, row.values[i], (row.inputs || [])[i]) : readingCell(s, d, row.values[i], false, (row.inputs || [])[i])));
        if (D.editable) cells.push(workedOut() ? '<td></td>' : readingCell(s, '', null, true));
      }
      if (D.editable) {
        const leave = s.left_out
          ? `<button type="button" class="xp-link" data-xp-leave="${esc(s.key)}" data-back="1" title="${esc(t('Count it in the tests and the chart again'))}">${t('Put back')}</button>`
          : `<button type="button" class="xp-link" data-xp-leave="${esc(s.key)}" title="${esc(t('Leave it out of the tests by day and the chart; its readout stays'))}">${t('Leave out…')}</button>`;
        cells.push(`<td class="xp-actions">${leave}<button type="button" class="xp-link xp-danger" data-xp-remove="${esc(s.key)}" aria-label="${esc(t('Take %(label)s out of the experiment', { label: s.label }))}" title="${esc(t('Take out of the experiment'))}">×</button></td>`);
      }
      return `<tr${s.left_out ? ' class="xp-left-out"' : ''}>${cells.join('')}</tr>`;
    }).join('');
    table.innerHTML = `<thead><tr>${head.join('')}</tr></thead><tbody>${body}</tbody>`;
  }

  // The reagent a recorded day used: "Tamoxifen lot L7", "(expired)" when
  // its lot had expired by that day.
  function usedText(rec) {
    const r = rec && rec.reagent;
    if (!r || !r.name) return '';
    const expired = r.expires && r.expires < rec.done_on ? ` ${t('(expired)')}` : '';
    return `${r.name}${r.lot ? ` ${t('lot %(lot)s', { lot: r.lot })}` : ''}${expired}`;
  }

  function treatmentCell(subject, col) {
    if (!inGroup(subject, col.group)) return `<td class="xp-t" data-t="none" title="${esc(t('Not in this group'))}">·</td>`;
    const rec = col.record;
    if (!rec) {
      const label = { today: t('due'), overdue: t('due'), upcoming: '', planned: '' }[col.state] || '';
      return `<td class="xp-t" data-t="${esc(col.state)}">${label ? `<span class="xp-due">${label}</span>` : ''}</td>`;
    }
    const entry = rec.subjects.find((e) => e.subject === subject.key);
    const detail = entry ? [entry.amount, entry.volume].filter(Boolean).join(' · ') : '';
    const lot = usedText(rec) ? ` · ${usedText(rec)}` : '';
    const title = entry ? `${t('Given %(date)s by %(who)s', { date: niceDate(rec.done_on), who: rec.done_by })}${detail ? `: ${detail}` : ''}${lot}`
      : t('Not given (recorded %(date)s)', { date: niceDate(rec.done_on) });
    const inner = entry ? `<span class="xp-tick">✓</span>${detail ? `<span class="xp-amount">${esc(detail)}</span>` : ''}` : '<span class="xp-miss">—</span>';
    return D.editable
      ? `<td class="xp-t" data-t="${entry ? 'given' : 'missed'}"><button type="button" class="xp-tbtn" data-xp-toggle="${col.step_id}:${col.day}" data-subject="${esc(subject.key)}" data-given="${entry ? 1 : 0}" title="${esc(`${title} ${t('(click to change)')}`)}">${inner}</button></td>`
      : `<td class="xp-t" data-t="${entry ? 'given' : 'missed'}" title="${esc(title)}">${inner}</td>`;
  }

  // A measure worked out from two Cts takes them typed in its cell, "24.8 − 18.2" (a ΔCt also as it is).
  function readingCell(subject, date, value, fresh = false, inputs = null) {
    const of = vFraction() && subject.start != null ? `<span class="xp-of">/ ${subject.start}</span>` : '';
    const cts = VR().formula ? (VR().formula === 'delta_ct' ? t('The ΔCt, or the two Cts: target − reference, e.g. 24.8 − 18.2') : t('The two Cts: target − reference, e.g. 24.8 − 18.2')) : '';
    const tip = [measuredText(inputs, VR().inputs), cts].filter(Boolean).join(' · ');
    return `<td class="xp-r"><span class="xp-rwrap"><input class="xp-cell xp-num" data-xp-reading="${esc(subject.key)}" data-date="${esc(date)}" ${fresh ? 'data-fresh="1"' : ''}
      value="${value === null || value === undefined ? '' : num(value, 2)}" inputmode="${VR().formula ? 'text' : 'decimal'}"${tip ? ` title="${esc(tip)}"` : ''} aria-label="${esc(date ? t('%(readout)s of %(label)s on %(date)s', { readout: vRL(), label: subject.label, date }) : t('%(readout)s of %(label)s', { readout: vRL(), label: subject.label }))}" ${D.editable ? '' : 'disabled'}>${of}</span></td>`;
  }

  // A readout BioManager works out: the value, what was measured on hover; a click opens the day to change it.
  function derivedCell(date, value, inputs) {
    const shown = value === null || value === undefined ? '' : num(value, 2);
    const what = measuredText(inputs);
    if (!D.editable) return `<td class="xp-r" title="${esc(what)}"><span class="xp-derived-value">${shown}</span></td>`;
    return `<td class="xp-r"><button type="button" class="xp-tbtn xp-derived-value" data-xp-open="reading" data-on="${esc(date)}" title="${esc(`${what} ${t('(click to change)')}`.trim())}">${shown || '·'}</button></td>`;
  }

  // Sheet edits
  document.addEventListener('change', async (event) => {
    const t = event.target;
    if (t.matches('[data-xp-group]')) {
      const s = D.subjects.find((x) => x.key === t.dataset.xpGroup);
      const was = s ? s.group : '';
      const n = was ? D.subjects.filter((x) => inGroup(x, was)).length : 0;
      // A whole group can be renamed, or merged into another, at once.
      const checks = n > 1 ? [{ name: 'whole', label: window.t('Every %(noun)s in “%(group)s” (%(n)s): rename or merge the group', { noun: noun(), group: was, n }) }] : null;
      try {
        const data = await sendReason(`${XP}/subjects/${encodeURIComponent(t.dataset.xpGroup)}/update`, { group: t.value },
          { checks, extra: (c) => (c.whole ? { whole_group: 1 } : {}) });
        if (data) apply(data); else t.value = was;
      } catch (_e) { /* shown */ }
    } else if (t.matches('[data-xp-start]')) {
      const s = D.subjects.find((x) => x.key === t.dataset.xpStart);
      try {
        const data = await sendReason(`${XP}/subjects/${encodeURIComponent(t.dataset.xpStart)}/update`, { start: t.value });
        if (data) apply(data, { sheet: false }); else t.value = s && s.start != null ? s.start : '';
      } catch (_e) { t.classList.add('is-error'); }
    } else if (t.matches('[data-xp-reading]')) {
      const on = t.dataset.fresh ? ($('.xp-newday-date') || {}).value : t.dataset.date;
      if (!on) return;
      try {
        const data = await send(`${XP}/readings`, { on, values: { [t.dataset.xpReading]: t.value }, ...(viewed() ? { measure: viewed().key } : {}) });
        t.classList.remove('is-error');
        // A new day becomes its own column; two Cts become their ΔCt (and a ΔΔCt moves the day's others).
        const redraw = Boolean(t.dataset.fresh) || Boolean(VR().formula);
        const next = redraw && document.activeElement && document.activeElement.matches('[data-xp-reading]')
          ? [document.activeElement.dataset.xpReading, document.activeElement.dataset.date] : null;
        apply(data, { sheet: redraw });
        if (next) {
          const again = $$('[data-xp-reading]').find((el) => el.dataset.xpReading === next[0] && el.dataset.date === next[1]);
          if (again) again.focus();
        }
      } catch (_e) { t.classList.add('is-error'); }
    }
  });

  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('button');
    if (!btn) return;
    if (btn.dataset.xpView) {
      const was = VR();
      view = btn.dataset.xpView;
      try { sessionStorage.setItem(`xp:view:${D.id}`, view); } catch (_e) { /* no storage */ }
      if (VR() !== was) { scale = null; drawChart(); }
      drawSheet(); drawLabels();
    } else if (btn.dataset.xpScale) {
      scale = btn.dataset.xpScale; drawChart(); drawLabels();
    } else if (btn.dataset.xpToggle) {
      const [stepId, day] = btn.dataset.xpToggle.split(':');
      try {
        apply(await send(`${API}/steps/${stepId}/day/${day}/subject`, { subject: btn.dataset.subject, given: btn.dataset.given !== '1' }));
      } catch (_e) { /* shown */ }
    } else if (btn.dataset.xpRemove) {
      const s = D.subjects.find((x) => x.key === btn.dataset.xpRemove);
      if (!(await confirmAsk(t('Take %(label)s out of this experiment? Its own record is unchanged.', { label: s ? s.label : t('it') }), true))) return;
      try {
        const data = await sendReason(`${XP}/subjects/${encodeURIComponent(btn.dataset.xpRemove)}/remove`, {});
        if (data) apply(data);
      } catch (_e) { /* shown */ }
    } else if (btn.dataset.xpLeave) {
      const s = D.subjects.find((x) => x.key === btn.dataset.xpLeave);
      const url = `${XP}/subjects/${encodeURIComponent(btn.dataset.xpLeave)}/leave-out`;
      try {
        if (btn.dataset.back) {
          const data = await sendReason(url, { back: 1 });
          if (data) apply(data);
          return;
        }
        const answer = await BioDialog.prompt(t('Leave %(label)s out of the analysis?', { label: s ? s.label : '' }), '', { message: t('Its readout stays in the sheet; the tests by day and the chart leave it out. Say why.'), placeholder: t('A reason, in a line'), okLabel: t('Leave it out') });
        if (answer === null || answer === undefined || !String(answer).trim()) return;
        apply(await send(url, { reason: String(answer).trim() }));
      } catch (_e) { /* shown */ }
    } else if (btn.dataset.xpMeasureEdit) {
      openMeasure(btn.dataset.xpMeasureEdit);
    } else if (btn.matches('[data-xp-measure-delete]')) {
      deleteMeasure();
    } else if (btn.dataset.xpOpen) {
      open(btn.dataset.xpOpen, btn.dataset.on);
    } else if (btn.matches('[data-xp-close]')) {
      btn.closest('dialog').close();
    } else if (btn.dataset.xpDay) {
      open('record', btn.dataset.xpDay);
    }
  });

  // ---------------------------------------------------------------- the chart

  const COLORS = ['var(--viz-1, #2a78d6)', 'var(--viz-2, #eb6834)', 'var(--viz-3, #1baf7a)', 'var(--viz-4, #eda100)',
    'var(--viz-5, #e87ba4)', 'var(--viz-7, #4a3aa7)'];

  function drawChart() {
    if (scale === null) scale = vFraction() ? 'pct' : 'raw';
    const host = $('[data-xp-chart]');
    const tb = VT();
    const xs = tb.days.every((d) => d !== null) ? tb.days : tb.dates.map((_d, i) => i + 1);
    const pct = scale === 'pct';
    const byGroup = new Map();
    tb.rows.filter((r) => !r.left_out).forEach((r) => {
      const key = r.group || t('All %(nouns)s', { nouns: nouns() });
      if (!byGroup.has(key)) byGroup.set(key, []);
      byGroup.get(key).push(r);
    });
    const series = [...byGroup.entries()].map(([name, rows], gi) => ({
      name, color: COLORS[gi % COLORS.length],
      points: xs.map((x, i) => {
        const have = rows.filter((r) => r.values[i] !== null && r.values[i] !== undefined);
        if (!have.length) return null;
        if (vFraction() && pct) {
          const total = have.reduce((a, r) => a + (r.start || 0), 0);
          if (!total) return null;
          return { x, y: (have.reduce((a, r) => a + r.values[i], 0) / total) * 100, sem: 0, n: have.length };
        }
        const vals = have.map((r) => (pct ? r.pct[i] : r.values[i])).filter((v) => v !== null && v !== undefined);
        if (!vals.length) return null;
        const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
        const sd = vals.length > 1 ? Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / (vals.length - 1)) : 0;
        return { x, y: mean, sem: vals.length > 1 ? sd / Math.sqrt(vals.length) : 0, n: vals.length };
      }).filter(Boolean),
    }));
    const marks = new Map();
    treatmentCols().forEach((c) => {
      if (!marks.has(c.day)) marks.set(c.day, []);
      marks.get(c.day).push(c);
    });
    const all = series.flatMap((s) => s.points.flatMap((p) => [p.y - p.sem, p.y + p.sem]));
    if (!all.length) {
      host.innerHTML = `<p class="xp-empty">${t('No %(readout)s yet.', { readout: esc(vRL().toLowerCase()) })} ${D.editable ? t("<b>%(verb)s</b> the %(nouns)s, or type in the sheet's %(readout)s view.", { verb: esc(tc('readout', VR().verb || 'Record')), nouns: esc(nouns()), readout: esc(vRL()) }) : ''}</p>`;
      return;
    }
    const dayList = [...new Set([...xs, ...marks.keys()])].sort((a, b) => a - b);
    const xMin = dayList[0];
    const xMax = dayList[dayList.length - 1];
    let yMin = Math.min(...all);
    let yMax = Math.max(...all);
    const pad = (yMax - yMin || 1) * 0.12;
    yMin -= pad; yMax += pad;
    // A count or a percentage never goes below nothing; a percentage of those at the start tops out at all of them.
    if (vFraction() || pct) yMin = Math.max(0, yMin);
    if (vFraction() && pct) { yMin = 0; yMax = 105; }
    const W = 760; const H = 260; const L = 48; const R = 14; const T = 14; const B = 34;
    const sx = (x) => L + ((x - xMin) / ((xMax - xMin) || 1)) * (W - L - R);
    const sy = (y) => T + (1 - (y - yMin) / ((yMax - yMin) || 1)) * (H - T - B);
    let svg = `<svg viewBox="0 0 ${W} ${H}" class="xp-svg" role="img" aria-label="${esc(t('%(readout)s by group over the days', { readout: vRL() }))}">`;
    for (let i = 0; i <= 4; i += 1) {
      const y = yMin + ((yMax - yMin) * i) / 4;
      svg += `<line x1="${L}" x2="${W - R}" y1="${sy(y)}" y2="${sy(y)}" class="xp-grid"/><text x="${L - 6}" y="${sy(y) + 4}" text-anchor="end" class="xp-axis">${num(y, pct ? 0 : 1)}</text>`;
    }
    dayList.forEach((d) => { svg += `<text x="${sx(d)}" y="${H - B + 17}" text-anchor="middle" class="xp-axis">${d}</text>`; });
    svg += `<text x="${(L + W - R) / 2}" y="${H - 3}" text-anchor="middle" class="xp-axis">${t('day')}</text>`;
    marks.forEach((_items, d) => {
      svg += `<line x1="${sx(d)}" x2="${sx(d)}" y1="${T}" y2="${H - B}" class="xp-mark"/>`;
    });
    series.forEach((s) => {
      const path = s.points.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ');
      svg += `<path d="${path}" fill="none" stroke="${s.color}" stroke-width="2.2"/>`;
      s.points.forEach((p) => {
        if (p.sem) svg += `<line x1="${sx(p.x)}" x2="${sx(p.x)}" y1="${sy(p.y - p.sem)}" y2="${sy(p.y + p.sem)}" stroke="${s.color}" stroke-width="1.5"/>`;
        svg += `<circle cx="${sx(p.x)}" cy="${sy(p.y)}" r="4" fill="${s.color}" class="xp-dot" data-tip="${esc(`${s.name}, ${t('day %(n)s', { n: p.x })}: ${num(p.y, pct ? 0 : 1)}${pct ? '%' : (VR().unit && !vFraction() ? ` ${vUnit()}` : '')}${p.sem ? ` ± ${num(p.sem, pct ? 0 : 1)}` : ''} (n = ${p.n})`)}"/>`;
      });
    });
    // Wide, invisible bands on the dashed lines: pointing at one says what was done that day.
    marks.forEach((items, d) => {
      const text = items.map((c) => `${titleOf(c)}${c.group ? ` · ${c.group}` : ''} — ${c.record ? t('done %(date)s by %(who)s', { date: niceDate(c.record.done_on), who: c.record.done_by }) : STATE[c.state].toLowerCase()}`);
      svg += `<rect x="${sx(d) - 8}" y="${T}" width="16" height="${H - T - B}" class="xp-hit" data-tip-title="${esc(`${t('Day %(n)s', { n: d })}${items[0].date ? ` · ${niceDate(items[0].date, true)}` : ''}`)}" data-tip="${esc(text.join('\n'))}"/>`;
    });
    // Stars over the days where the groups differ (the table below has each test).
    const stats = (VT().stats || {})[pct && !vFraction() ? 'pct' : 'raw'] || [];
    stats.forEach((st, i) => {
      if (!st.stars || st.stars === 'ns') return;
      const x = xs[i];
      if (x === undefined) return;
      svg += `<text x="${sx(x)}" y="${T + 10}" text-anchor="middle" class="xp-star" data-tip="${esc(`${t('Day %(n)s', { n: x })}: ${st.test}, p = ${st.p < 0.001 ? st.p.toExponential(1) : st.p.toFixed(3)}`)}">${st.stars}</text>`;
    });
    svg += '</svg>';
    drawStats(stats);
    const legend = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('');
    host.innerHTML = `${svg}<div class="xp-tip" hidden></div><div class="xp-legend">${legend}<span class="xp-muted">${vFraction() && pct ? t('% of those at the start') : t('mean ± SEM')}${marks.size ? ` · ${t('dashed: manipulation days (point at one)')}` : ''}</span></div>`;
  }

  function drawStats(stats) {
    const box = $('[data-xp-stats]');
    const tested = stats.filter((st) => st.test);
    box.hidden = !tested.length;
    if (!tested.length) return;
    const unit = scale === 'pct' || vFraction() ? '%' : (VR().unit ? ` ${VR().unit}` : '');
    $('[data-xp-stats-table]').innerHTML = `<table class="dense-table fit-table"><thead><tr><th>${tc('experiment', 'Day')}</th><th>${t('Groups')}</th><th>${tc('experiment', 'Test')}</th><th>p</th><th></th></tr></thead><tbody>${
      stats.map((st) => `<tr><td>${st.day !== null ? t('Day %(n)s', { n: st.day }) : ''} <span class="xp-muted">${esc(niceDate(st.date))}</span></td>
        <td>${st.groups.map((g) => `${esc(g.group)}: ${num(g.value, 2)}${unit}${g.sem != null ? ` ± ${num(g.sem, 2)}` : ''} <span class="xp-muted">n=${g.n}</span>`).join('<br>')}</td>
        <td>${esc(st.test || '—')}</td><td>${st.p == null ? '' : (st.p < 0.001 ? st.p.toExponential(1) : st.p.toFixed(3))}</td><td><b>${esc(st.stars)}</b></td></tr>`).join('')}</tbody></table>`;
  }

  const chartHost = $('[data-xp-chart]');
  chartHost.addEventListener('mousemove', (event) => {
    const target = event.target.closest('[data-tip]');
    const tip = $('.xp-tip', chartHost);
    if (!tip) return;
    if (!target) { tip.hidden = true; return; }
    const title = target.dataset.tipTitle;
    tip.innerHTML = `${title ? `<b>${esc(title)}</b>` : ''}${esc(target.dataset.tip).replace(/\n/g, '<br>')}`;
    tip.hidden = false;
    const box = chartHost.getBoundingClientRect();
    const x = Math.min(event.clientX - box.left + 12, box.width - tip.offsetWidth - 4);
    tip.style.left = `${Math.max(4, x)}px`;
    tip.style.top = `${event.clientY - box.top + 12}px`;
  });
  chartHost.addEventListener('mouseleave', () => { const tip = $('.xp-tip', chartHost); if (tip) tip.hidden = true; });

  // ---------------------------------------------------------------- day by day

  function drawDays() {
    const list = $('[data-xp-days]');
    const done = D.schedule.filter((r) => r.record).length;
    $('[data-xp-days-count]').textContent = D.schedule.length ? t('%(done)s of %(total)s done', { done, total: D.schedule.length }) : '';
    if (!D.schedule.length) {
      list.innerHTML = `<li class="xp-empty">${t('Nothing planned yet.')}${D.editable ? ` ${t('<b>Plan</b> the manipulations and readout days, or <b>Record manipulation</b> as you go.')}` : ''}</li>`;
      return;
    }
    const byDay = new Map();
    D.schedule.forEach((r) => { if (!byDay.has(r.day)) byDay.set(r.day, []); byDay.get(r.day).push(r); });
    list.innerHTML = [...byDay.entries()].map(([day, rows]) => `
      <li class="xp-day" id="day-${day}"><div class="xp-day-head"><b>${t('Day %(n)s', { n: day })}</b><span>${esc(niceDate(rows[0].date, true))}</span></div>
      <ul class="xp-items">${rows.map((r) => {
        const rec = r.record;
        const extra = rec ? [usedText(rec),
          rec.samples && rec.samples.length ? t(rec.samples.length === 1 ? '%(n)s sample in %(inventory)s' : '%(n)s samples in %(inventory)s', { n: rec.samples.length, inventory: rec.samples[0].inventory }) : ''].filter(Boolean).map(esc).join(' · ') : '';
        const status = rec ? `${t('Done %(date)s by %(who)s', { date: esc(niceDate(rec.done_on)), who: esc(rec.done_by) })} · ${t('%(count)s of %(total)s', { count: rec.count, total: r.group_size })}${extra ? ` · ${extra}` : ''}` : STATE[r.state];
        const action = r.reading
          ? (D.editable ? `<button type="button" class="xp-link" data-xp-open="reading" data-on="${esc(r.date)}">${esc(verb())}</button>` : '')
          : `<button type="button" class="${rec || !D.editable ? 'xp-link' : 'btn xp-record-btn'}" data-xp-day="${r.step_id}:${r.day}">${rec || !D.editable ? t('Details') : t('Record')}</button>`;
        return `<li class="xp-item" data-state="${esc(r.state)}"><span class="xp-item-icon">${icon(r.icon)}</span>
          <span class="xp-item-body"><b>${esc(titleOf(r))}</b>${r.group ? ` <span class="xp-muted">· ${esc(r.group)}</span>` : ''}
          <span class="xp-status">${status}</span>${rec && rec.note ? `<span class="xp-note">${esc(rec.note)}</span>` : ''}</span>${action}</li>`;
      }).join('')}</ul></li>`).join('');
    const target = window.location.hash && document.getElementById(window.location.hash.slice(1));
    if (target && target.classList.contains('xp-day')) target.classList.add('is-target');
  }

  // ---------------------------------------------------------------- dialogs

  function showError(dialog, text) {
    const el = $('[data-xp-error]', dialog);
    if (el) { el.textContent = text || ''; el.hidden = !text; }
  }

  function fillKinds(select, chosen) {
    select.innerHTML = D.kinds.map((k) => `<option value="${esc(k.key)}" ${k.key === chosen ? 'selected' : ''}>${esc(k.key === 'reading' ? t('%(readout)s day', { readout: RL() }) : t(k.label))}</option>`).join('');
  }
  function fillGroups(select, chosen) {
    select.innerHTML = [`<option value="">${esc(t('Every %(noun)s', { noun: noun() }))}</option>`, ...D.groups.map((g) => `<option value="${esc(g)}" ${g === chosen ? 'selected' : ''}>${esc(g)}</option>`)].join('');
  }

  // The items named as what is given come first; with no item chosen, the
  // one of them (the one not expired) is, as recording it would.
  const sameName = (agent) => (D.reagents || []).filter((r) => agent && r.name.trim().toLowerCase() === agent.trim().toLowerCase());
  function fillReagents(select, chosen, agent) {
    const named = sameName(agent);
    if (!chosen && named.length) {
      const fresh = named.filter((r) => !(r.expires && r.expires < today()));
      const pool = fresh.length ? fresh : named;
      if (pool.length === 1) chosen = pool[0].id;
    }
    const list = [...named, ...(D.reagents || []).filter((r) => !named.includes(r))];
    select.innerHTML = `<option value="">${esc(t('None recorded'))}</option>` + list.map((r) => `<option value="${r.id}" ${Number(chosen) === r.id ? 'selected' : ''}>${esc(r.label)}${r.expires && r.expires < today() ? ` ${esc(t('(expired)'))}` : ''}</option>`).join('');
  }

  // The lot beside the item: the item's own, or the one the day kept; what
  // to know about it (expired, several of the name, taken off already).
  function reagentNote(dialog, kept, keepLot) {
    const f = $('[data-xp-form="record"]').elements;
    const item = (D.reagents || []).find((r) => String(r.id) === f.reagent_id.value);
    if (!keepLot) f.lot.value = kept && kept.lot && (!item || kept.id === item.id) ? kept.lot : (item ? item.lot : '');
    const on = f.done_on.value || today();
    const agent = recordCtx && recordCtx.adhoc ? f.agent.value : ((recordCtx && recordCtx.data.step && recordCtx.data.step.agent) || '');
    const named = sameName(agent);
    const notes = [];
    if (item && item.expires && item.expires < on && f.lot.value.trim().toLowerCase() === item.lot.toLowerCase()) notes.push(t('Expired on %(date)s, before this day.', { date: niceDate(item.expires) }));
    if (!item && named.length > 1) notes.push(t('There are %(n)s of %(name)s in the inventory: pick the one used.', { n: named.length, name: agent }));
    const taken = kept && kept.taken && item && kept.id === item.id ? kept.taken : '';
    if (taken) notes.push(t('Taken off the inventory: %(what)s', { what: taken }));
    const note = $('[data-xp-reagent-note]', dialog);
    note.textContent = notes.join(' ');
    note.hidden = !notes.length;
    $('[data-xp-take-off]', dialog).hidden = !item || Boolean(taken) || Boolean(recordCtx && !recordCtx.adhoc && recordCtx.data.step && recordCtx.data.step.reading);
  }

  function open(which, arg) {
    const dialog = $(`#xp-${which}`);
    if (!dialog) return;
    showError(dialog, '');
    if (which === 'plan') openPlan(dialog);
    if (which === 'record') openRecord(dialog, arg);
    if (which === 'reading') openReading(dialog, arg || '');
    if (!dialog.open) dialog.showModal();
  }

  // Add animals
  function refreshAddOptions(add) {
    const groups = $('[data-xp-add-groups]');
    const single = $('[data-xp-add-single]');
    if (!groups || !single) return;
    groups.innerHTML = '<option value="">—</option>' + add.groups.map(([v, l, n]) => `<option value="${esc(v)}">${esc(l)} (${n})</option>`).join('');
    single.innerHTML = '<option value="">—</option>' + add.single.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
  }
  const addForm = $('[data-xp-form="add"]');
  if (addForm) addForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const dialog = addForm.closest('dialog');
    const f = addForm.elements;
    const body = f.group_value.value ? { how: 'group', value: f.group_value.value } : { how: 'one', value: f.one_value.value };
    if (!body.value) return showError(dialog, t('Pick a %(housing)s, or one.', { housing: t(D.housing) }));
    body.group = f.group.value;
    try {
      const data = await sendReason(`${XP}/subjects/add`, body);
      if (!data) return undefined;
      apply(data);
      addForm.reset();
      say('saved', data.message || t('Added'));
      dialog.close();
    } catch (error) { showError(dialog, error.message); }
    return undefined;
  });

  // The plan
  let editing = null;
  function openPlan(dialog) {
    const saved = D.regimens || [];
    $('[data-xp-regimens]', dialog).innerHTML = saved.length ? `<label class="xp-regimen-pick">${t('Start from a saved regimen')}
      <span class="xp-row"><select data-xp-regimen>${saved.map((r) => `<option value="${r.id}">${esc(r.name)} — ${esc(r.summary)}${r.mine ? '' : ` (${esc(r.owner)})`}</option>`).join('')}</select>
      <button type="button" class="btn btn-sm" data-xp-regimen-apply>${t('Plan it')}</button>
      <button type="button" class="xp-link xp-danger" data-xp-regimen-delete aria-label="${esc(t('Delete this saved regimen'))}" title="${esc(t('Delete this saved regimen'))}">×</button></span></label>
      <p class="xp-sub">${t('Planning one adds its days here; each is still recorded as it is done.')}</p>` : '';
    const list = $('[data-xp-plan-list]', dialog);
    list.innerHTML = D.steps.length ? `<table class="dense-table fit-table xp-plan"><thead><tr><th>${t('Days')}</th><th>${t('Kind')}</th><th>${t('What')}</th><th>${t('Dose')}</th><th>${t('Route')}</th><th>${t('Group')}</th><th></th></tr></thead><tbody>${D.steps.map((s) => `
      <tr><td class="xp-days-cell">${t('Day %(n)s', { n: esc(s.days_label) })}</td><td><span class="xp-kind">${icon(s.icon)}${esc(s.reading ? t('%(readout)s day', { readout: RL() }) : t(s.kind_label))}</span></td>
      <td><b>${esc(s.agent)}</b>${s.notes ? `<div class="xp-muted">${esc(s.notes)}</div>` : ''}</td><td>${esc(s.dose)}${s.concentration ? `<div class="xp-muted">${esc(s.concentration)}</div>` : ''}${stimText(s) ? `<div class="xp-muted">${esc(stimText(s))}</div>` : ''}</td>
      <td>${esc(s.route)}</td><td>${s.group ? esc(s.group) : `<span class="xp-muted">${esc(t('every %(noun)s', { noun: noun() }))}</span>`}</td>
      <td class="xp-actions"><button type="button" class="xp-link" data-xp-edit="${s.id}">${t('Edit')}</button><button type="button" class="xp-link xp-danger" data-xp-delete="${s.id}" aria-label="${esc(t('Delete'))}">×</button></td></tr>`).join('')}</tbody></table>`
      : `<p class="xp-empty">${t('Nothing planned yet.')}</p>`;
    resetStepForm(dialog, editing && D.steps.find((s) => s.id === editing));
  }
  // A stimulation's settings show only for a Stimulation.
  const STIM = ['frequency', 'intensity', 'pulse_width', 'duration'];
  function stimShown(form) {
    $$('[data-xp-stim]', form).forEach((label) => { label.hidden = form.elements.kind.value !== 'stimulation'; });
  }
  ['plan', 'record'].forEach((name) => {
    const form = $(`[data-xp-form="${name}"]`);
    if (form && form.elements.kind) form.elements.kind.addEventListener('change', () => stimShown(form));
  });
  const stimText = (s) => STIM.map((k) => s[k]).filter(Boolean).join(' · ');
  function resetStepForm(dialog, step) {
    const f = $('[data-xp-form="plan"]').elements;
    fillKinds(f.kind, step ? step.kind : D.kinds[0].key);
    fillGroups(f.group, step ? step.group : '');
    ['id', 'agent', 'days', 'dose', 'route', 'concentration', 'notes', ...STIM].forEach((n) => { f[n].value = step ? (step[n] || '') : ''; });
    stimShown($('[data-xp-form="plan"]'));
    fillReagents(f.reagent_id, step ? step.reagent_id : '');
    $('[data-xp-plan-form-title]', dialog).textContent = step ? t('Change this line') : t('Add to the regimen');
    $('[data-xp-plan-save]', dialog).textContent = step ? t('Save') : t('Add to the regimen');
    $('[data-xp-plan-new]', dialog).hidden = !step;
    editing = step ? step.id : null;
  }
  const planForm = $('[data-xp-form="plan"]');
  if (planForm) {
    planForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const dialog = planForm.closest('dialog');
      const f = planForm.elements;
      const body = Object.fromEntries(['id', 'kind', 'agent', 'days', 'dose', 'route', 'concentration', ...STIM, 'group', 'notes', 'reagent_id'].map((n) => [n, f[n].value]));
      try {
        apply(await send(`${API}/steps/save`, body));
        editing = null;
        showError(dialog, '');
        openPlan(dialog);
      } catch (error) { showError(dialog, error.message); }
    });
    planForm.addEventListener('click', async (event) => {
      const btn = event.target.closest('button');
      if (!btn) return;
      const dialog = planForm.closest('dialog');
      if (btn.dataset.xpEdit) { resetStepForm(dialog, D.steps.find((s) => s.id === Number(btn.dataset.xpEdit))); }
      if (btn.matches('[data-xp-plan-new]')) resetStepForm(dialog, null);
      if (btn.matches('[data-xp-regimen-save]')) {
        if (!D.steps.length) return showError(dialog, t('Plan something first: this experiment has nothing to save yet.'));
        const name = await BioDialog.prompt(t('Name this regimen'), '');
        if (!name) return undefined;
        try { const data = await send(`${API}/regimens/save`, { name }); apply(data); openPlan(dialog); say('saved', data.message); } catch (error) { showError(dialog, error.message); }
      }
      if (btn.matches('[data-xp-regimen-apply]')) {
        const id = $('[data-xp-regimen]', dialog).value;
        try { const data = await send(`${API}/regimens/${id}/apply`, {}); apply(data); openPlan(dialog); say('saved', data.message); } catch (error) { showError(dialog, error.message); }
      }
      if (btn.matches('[data-xp-regimen-delete]')) {
        const r = (D.regimens || []).find((x) => String(x.id) === $('[data-xp-regimen]', dialog).value);
        if (!r || !(await confirmAsk(t('Delete the saved regimen %(name)s? Experiments planned from it keep their days.', { name: r.name }), true))) return undefined;
        try { apply(await send(`${API}/regimens/${r.id}/delete`, {})); openPlan(dialog); } catch (error) { showError(dialog, error.message); }
      }
      if (btn.dataset.xpDelete) {
        const step = D.steps.find((s) => s.id === Number(btn.dataset.xpDelete));
        if (!(await confirmAsk(t('Delete %(what)s (day %(days)s) from the plan?', { what: step.agent || t(step.kind_label), days: step.days_label }), true))) return;
        try {
          apply(await send(`${API}/steps/${step.id}/delete`, {}));
        } catch (error) {
          if (error.data && error.data.needs_confirm && await confirmAsk(`${error.message} ${t('Delete it and those records too?')}`, true)) {
            apply(await send(`${API}/steps/${step.id}/delete`, { confirm: '1' }));
          }
        }
        openPlan(dialog);
      }
    });
  }

  // Record a manipulation
  let recordCtx = null;
  function openRecord(dialog, preset) {
    const f = $('[data-xp-form="record"]').elements;
    $('[data-xp-form="record"]').reset();
    const rows = treatmentCols();
    const group = (label, list) => (list.length ? `<optgroup label="${esc(label)}">${list.map((r) => `<option value="${r.step_id}:${r.day}">${t('Day %(n)s', { n: r.day })}${r.date ? ` (${niceDate(r.date)})` : ''} · ${esc(titleOf(r))}${r.group ? ` · ${esc(r.group)}` : ''}${r.record ? ' ✓' : ''}</option>`).join('')}</optgroup>` : '');
    const due = rows.filter((r) => !r.record && (r.state === 'today' || r.state === 'overdue'));
    const later = rows.filter((r) => !r.record && (r.state === 'upcoming' || r.state === 'planned'));
    const done = rows.filter((r) => r.record);
    f.which.innerHTML = group(tc('experiment', 'Due'), due) + `<option value="adhoc">${esc(t('Something not in the plan…'))}</option>` + group(t('To come'), later) + group(t('Done: change it'), done);
    f.which.value = preset || (due[0] ? `${due[0].step_id}:${due[0].day}` : 'adhoc');
    fillKinds(f.kind, D.kinds[0].key);
    fillGroups(f.group, '');
    stimShown($('[data-xp-form="record"]'));
    f.done_on.value = today();
    f.done_on.max = today();
    const inventories = D.sample_inventories || [];
    $('[data-xp-samples]', dialog).hidden = !inventories.length;
    $('[data-xp-sample-inventories]', dialog).innerHTML = inventories.map((i) => `<option value="${esc(i.key)}">${esc(i.label)}</option>`).join('');
    $('[data-xp-sample-fields]', dialog).hidden = true;
    chooseRecord(dialog);
  }

  async function chooseRecord(dialog, on) {
    const form = $('[data-xp-form="record"]');
    const f = form.elements;
    const adhoc = f.which.value === 'adhoc';
    $('[data-xp-adhoc]', dialog).hidden = !adhoc;
    let data;
    try {
      if (adhoc) {
        const q = new URLSearchParams({ dose: f.dose.value, concentration: f.concentration.value, group: f.group.value, on: f.done_on.value || today(), kind: f.kind.value });
        data = await get(`${API}/preview?${q}`);
        recordCtx = { adhoc: true, data };
      } else {
        const [stepId, day] = f.which.value.split(':');
        data = await get(`${API}/steps/${stepId}/day/${day}${on ? `?on=${encodeURIComponent(on)}` : ''}`);
        recordCtx = { adhoc: false, stepId, day, data };
        if (!on) f.done_on.value = data.on;
        if (!on && data.record) f.note.value = data.record.note || '';
      }
    } catch (error) { showError(dialog, error.message); return; }
    const step = data.step || { dose: f.dose.value, per_weight: /\/\s*(k?g)\s*$/i.test(f.dose.value) };
    $('[data-xp-record-sub]', dialog).textContent = adhoc
      ? (D.start_date ? t('Day %(n)s of the experiment. It joins the plan, done.', { n: dayOf(f.done_on.value) }) : t('The experiment has no start date yet: this day becomes day 1.'))
      : [data.planned ? t('Planned for %(date)s', { date: niceDate(data.planned, true) }) : '', data.record ? t('recorded by %(who)s', { who: data.record.done_by }) : ''].filter(Boolean).join(' · ');
    $('[data-xp-dose-note]', dialog).textContent = D.weighs && step.per_weight
      ? (step.concentration
        ? t("%(dose)s from each %(noun)s's latest weight on or before that day, as %(solution)s.", { dose: step.dose, noun: noun(), solution: step.concentration })
        : t("%(dose)s from each %(noun)s's latest weight on or before that day.", { dose: step.dose, noun: noun() })) : '';
    const weighs = D.weighs;
    $('[data-xp-animals-head]', dialog).innerHTML = `<tr><th><input type="checkbox" data-xp-all checked aria-label="${esc(t('All'))}"></th><th>${esc(cap(noun()))}</th><th>${t('Group')}</th>${weighs ? `<th>${t('Weight')}</th>` : (D.family !== 'mouse' ? `<th>${t('At start')}</th>` : '')}<th>${t('Amount')}</th><th>${t('Volume')}</th></tr>`;
    const animals = data.animals || data.mice || [];
    $('[data-xp-animals]', dialog).innerHTML = animals.length ? animals.map((a) => `<tr>
      <td><input type="checkbox" data-xp-animal="${esc(a.subject)}" ${a.given ? 'checked' : ''} aria-label="${esc(a.label)}"></td>
      <td>${esc(a.label)} <span class="xp-muted">${esc(a.sex || '')}</span></td><td>${esc(a.group)}</td>
      ${weighs ? `<td>${a.grams != null ? `${num(a.grams)} g <span class="xp-muted">${esc(niceDate(a.weighed_on))}</span>` : `<span class="xp-muted">${t('no weight')}</span>`}</td>` : (D.family !== 'mouse' ? `<td>${a.start == null ? '' : a.start}</td>` : '')}
      <td>${a.needs ? `<span class="xp-warn">${t('needs a weight')}</span>` : esc(a.amount || '')}</td><td>${esc(a.volume || '')}</td></tr>`).join('')
      : `<tr><td colspan="6" class="xp-muted">${t('No %(nouns)s in this group.', { nouns: esc(nouns()) })}</td></tr>`;
    const kept = data.record && data.record.reagent;
    if (!on) {
      fillReagents(f.reagent_id, adhoc ? '' : ((kept && kept.id) || (data.step && data.step.reagent_id) || ''), adhoc ? f.agent.value : (data.step && data.step.agent));
      f.take_off.checked = false;
    }
    reagentNote(dialog, kept, Boolean(on));
    const made = (data.record && data.record.samples) || [];
    $('[data-xp-samples-made]', dialog).textContent = made.length ? t('Sample records made: %(list)s in %(inventory)s.', { list: made.map((m) => `#${m.number}`).join(', '), inventory: made[0].inventory }) : '';
    $('.xp-check', dialog).hidden = made.length > 0;
    const kind = adhoc ? f.kind.value : (data.step && data.step.kind);
    if (kind === 'sample' && !made.length && !f.sample_name.value) f.sample_name.value = adhoc ? f.agent.value : (data.step.agent || '');
    $('[data-xp-undo]', dialog).hidden = !(recordCtx.data.record);
    $('[data-xp-record-save]', dialog).textContent = recordCtx.data.record ? t('Save') : t('Record as done');
  }

  const dayOf = (iso) => (D.start_date && iso ? Math.round((new Date(`${iso}T12:00:00`) - new Date(`${D.start_date}T12:00:00`)) / 86400000) + 1 : '');

  const recordForm = $('[data-xp-form="record"]');
  if (recordForm) {
    let timer = null;
    recordForm.addEventListener('change', (event) => {
      const dialog = recordForm.closest('dialog');
      const t = event.target;
      if (t.matches('[data-xp-all]')) { $$('[data-xp-animal]', dialog).forEach((b) => { b.checked = t.checked; }); return; }
      if (t.name === 'make_samples') { $('[data-xp-sample-fields]', dialog).hidden = !t.checked; return; }
      if (t.name === 'reagent_id') { reagentNote(dialog, recordCtx && recordCtx.data.record && recordCtx.data.record.reagent); return; }
      if (t.name === 'lot') { reagentNote(dialog, null, true); return; }
      if (t.name === 'agent' && recordCtx && recordCtx.adhoc) { fillReagents(recordForm.elements.reagent_id, '', t.value); reagentNote(dialog); return; }
      if (['sample_inventory', 'sample_name', 'note', 'take_off'].includes(t.name)) return;
      if (t.name === 'which') {
        if (t.value === 'adhoc') recordForm.elements.done_on.value = today();   // something done now, usually
        chooseRecord(dialog);
      }
      else if (t.name === 'done_on') chooseRecord(dialog, recordCtx && !recordCtx.adhoc ? t.value : undefined);
      else if (['dose', 'concentration', 'group', 'kind'].includes(t.name)) chooseRecord(dialog);
    });
    recordForm.addEventListener('input', (event) => {
      if (!['dose', 'concentration'].includes(event.target.name)) return;
      clearTimeout(timer);
      timer = setTimeout(() => chooseRecord(recordForm.closest('dialog')), 400);
    });
    recordForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const dialog = recordForm.closest('dialog');
      const f = recordForm.elements;
      const subjects = $$('[data-xp-animal]:checked', dialog).map((b) => b.dataset.xpAnimal);
      const common = { done_on: f.done_on.value, subjects, note: f.note.value, reagent_id: f.reagent_id.value,
        lot: f.lot.value, take_off: Boolean(f.take_off.checked && !$('[data-xp-take-off]', dialog).hidden),
        make_samples: Boolean(f.make_samples && f.make_samples.checked), sample_inventory: f.sample_inventory ? f.sample_inventory.value : '',
        sample_name: f.sample_name ? f.sample_name.value : '' };
      try {
        const data = recordCtx.adhoc
          ? await send(`${API}/record-now`, { ...common, kind: f.kind.value, agent: f.agent.value, dose: f.dose.value, route: f.route.value, concentration: f.concentration.value, ...Object.fromEntries(STIM.map((k) => [k, f[k].value])), group: f.group.value })
          : await send(`${API}/steps/${recordCtx.stepId}/day/${recordCtx.day}/record`, common);
        apply(data);
        dialog.close();
        const notes = [data.message, ...(data.problems || [])].filter(Boolean);
        if (notes.length) alertSay(notes.join('\n'));
      } catch (error) { showError(dialog, error.message); }
    });
    $('[data-xp-undo]').addEventListener('click', async () => {
      if (!recordCtx || recordCtx.adhoc) return;
      if (!(await confirmAsk(t('Mark this day as not done?')))) return;
      try {
        const data = await send(`${API}/steps/${recordCtx.stepId}/day/${recordCtx.day}/undo`, {});
        apply(data);
        recordForm.closest('dialog').close();
        if (data.message) say('saved', data.message);   // what it took off, put back
      } catch (error) { showError(recordForm.closest('dialog'), error.message); }
    });
  }

  // A readout day
  function openReading(dialog, on) {
    const form = $('[data-xp-form="reading"]');
    const day = on && on <= today() ? on : today();
    form.elements.on.value = day;
    form.elements.on.max = today();
    $('#xp-reading-title').textContent = `${verb()}: ${RL()}`;
    const control = D.readout.per_group ? (D.readout.control
      ? t('Against the mean ΔCt of the control group, %(group)s, on the same day.', { group: D.readout.control })
      : t('Say which group is the control first (Control group, beside the readout).')) : '';
    $('[data-xp-reading-sub]', dialog).textContent = derivedRO()
      ? `${t('Type what you measured; BioManager works out the rest: %(expression)s. Leave one blank to skip it.', { expression: D.readout.expression })} ${control}`.trim()
      : fraction()
        ? t('How many %(unit)s of those each started with.', { unit: unitOf() }).replace('  ', ' ')
        : (D.readout.unit ? t('%(readout)s in %(unit)s; leave one blank to skip it.', { readout: RL(), unit: unitOf() })
          : t('%(readout)s; leave one blank to skip it.', { readout: RL() }));
    drawReadingRows(dialog, day);
  }
  function drawReadingRows(dialog, day) {
    const idx = D.table.dates.indexOf(day);
    const rows = Object.fromEntries(D.table.rows.map((r) => [r.key, r]));
    $('[data-xp-reading-rows]', dialog).innerHTML = D.subjects.map((s) => {
      const r = rows[s.key] || { values: [] };
      const current = idx >= 0 ? r.values[idx] : null;
      const before = [...D.table.dates.keys()].filter((i) => D.table.dates[i] < day && r.values[i] != null).pop();
      if (derivedRO()) {
        const measured = idx >= 0 ? (r.inputs || [])[idx] : null;
        const fields = inputsOf().map((i) => `<label>${esc(t(i.label))}<input class="xp-cell xp-num" data-key="${esc(s.key)}" data-input="${esc(i.key)}" inputmode="decimal" value="${measured && measured[i.key] != null ? num(measured[i.key], 4) : ''}" aria-label="${esc(`${t(i.label)}, ${s.label}`)}"></label>`).join('');
        const unit = D.readout.unit ? ` ${unitOf()}` : '';
        return `<tr><td>${esc(s.label)} <span class="xp-muted">${esc(s.group)}</span></td><td><span class="xp-inputs">${fields}</span></td>
          <td class="xp-muted">${current != null ? `→ ${num(current, 2)}${unit}` : (before !== undefined ? t('before: %(value)s (%(date)s)', { value: num(r.values[before], 2), date: esc(niceDate(D.table.dates[before])) }) : '')}</td></tr>`;
      }
      return `<tr><td>${esc(s.label)} <span class="xp-muted">${esc(s.group)}</span></td>
        <td><input class="xp-cell xp-num" name="v-${esc(s.key)}" data-key="${esc(s.key)}" inputmode="decimal" value="${current == null ? '' : num(current, 2)}" aria-label="${esc(s.label)}">${fraction() && s.start != null ? ` <span class="xp-of">/ ${s.start}</span>` : ''}</td>
        <td class="xp-muted">${before !== undefined ? t('before: %(value)s (%(date)s)', { value: num(r.values[before], 2), date: esc(niceDate(D.table.dates[before])) }) : ''}</td></tr>`;
    }).join('') || `<tr><td class="xp-muted">${t('No %(nouns)s yet.', { nouns: esc(nouns()) })}</td></tr>`;
  }
  const readingForm = $('[data-xp-form="reading"]');
  if (readingForm) {
    readingForm.elements.on.addEventListener('change', (e) => drawReadingRows(readingForm.closest('dialog'), e.target.value));
    readingForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const dialog = readingForm.closest('dialog');
      const values = {};
      const known = D.table.dates.includes(readingForm.elements.on.value);
      if (derivedRO()) {
        // What was measured, by animal; one left blank is skipped (or, on a day it has, removed).
        $$('[data-input]', dialog).forEach((i) => { (values[i.dataset.key] = values[i.dataset.key] || {})[i.dataset.input] = i.value; });
        Object.keys(values).forEach((k) => { if (!known && Object.values(values[k]).every((v) => !v.trim())) delete values[k]; });
      } else {
        $$('[data-key]', dialog).forEach((i) => { if (i.value.trim() !== '' || known) values[i.dataset.key] = i.value; });
      }
      try {
        const data = await send(`${XP}/readings`, { on: readingForm.elements.on.value, values });
        apply(data);
        dialog.close();
        if (data.problems && data.problems.length) alertSay(`${t('Saved, except:')}\n${data.problems.join('\n')}`);
      } catch (error) { showError(dialog, error.message); }
    });
  }

  // ---------------------------------------------------------------- another measure: rename, delete

  let measureKey = '';
  const measureNow = () => (D.measures || []).find((m) => m.key === measureKey) || null;
  function openMeasure(key) {
    measureKey = key;
    const m = measureNow();
    const dialog = $('#xp-measure');
    if (!m || !dialog) return;
    const f = $('[data-xp-form="measure"]').elements;
    f.label.value = m.label;
    f.unit.value = m.unit || '';
    f.control.value = m.control || '';
    f.kind.value = m.formula || '';
    measureKindShown();
    $('[data-xp-measure-kind]', dialog).textContent = m.formula ? `${t(m.kind_label)}: ${m.expression}. ${t(m.description)}` : '';
    $('[data-xp-measure-count]', dialog).textContent = m.count
      ? t(m.count === 1 ? 'It holds %(n)s reading; renaming keeps it.' : 'It holds %(n)s readings; renaming keeps them.', { n: m.count })
      : t('It holds no readings yet.');
    showError(dialog, '');
    if (!dialog.open) dialog.showModal();
    f.label.focus();
  }
  // The unit is a number's own; a control group, ΔΔCt's and fold change's.
  const PER_GROUP = ['delta_delta_ct', 'fold_change'];
  function measureKindShown() {
    const dialog = $('#xp-measure');
    const kind = $('[data-xp-form="measure"]').elements.kind.value;
    $('[data-xp-measure-unit]', dialog).hidden = Boolean(kind);
    $('[data-xp-measure-control]', dialog).hidden = !PER_GROUP.includes(kind);
  }
  const measureForm = $('[data-xp-form="measure"]');
  if (measureForm) {
    measureForm.elements.kind.addEventListener('change', measureKindShown);
    measureForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const m = measureNow();
      const dialog = measureForm.closest('dialog');
      if (!m) { dialog.close(); return; }
      const f = measureForm.elements;
      const body = { label: f.label.value, kind: f.kind.value };
      if (!f.kind.value) body.unit = f.unit.value;
      if (PER_GROUP.includes(f.kind.value)) body.control = f.control.value;
      try {
        apply(await send(`${XP}/measures/${m.id}/update`, body));
        dialog.close();
      } catch (error) { showError(dialog, error.message); }
    });
  }
  async function deleteMeasure() {
    const m = measureNow();
    const dialog = $('#xp-measure');
    if (!m) return;
    const question = m.count
      ? t(m.count === 1 ? 'Delete %(measure)s and its %(n)s reading? Batch history can undo it.' : 'Delete %(measure)s and its %(n)s readings? Batch history can undo it.', { measure: m.label, n: m.count })
      : t('Delete %(measure)s? It holds no readings.', { measure: m.label });
    if (!(await confirmAsk(question, true))) return;
    try {
      const data = await send(`${XP}/measures/${m.id}/delete`, {});
      if (dialog && dialog.open) dialog.close();
      view = 'readout';
      try { sessionStorage.setItem(`xp:view:${D.id}`, view); } catch (_e) { /* no storage */ }
      scale = null;
      apply(data);
      if (data.message) say('saved', data.message);
    } catch (error) { if (dialog) showError(dialog, error.message); }
  }

  // ---------------------------------------------------------------- the details, autosaved

  const info = $('[data-xp-info]');
  // Cancel on a reason puts back what the plan fixed, and saves the rest.
  function revertPlanned() {
    const f = info.elements;
    const r = D.readout || {};
    if (f.readout) f.readout.value = r.key === 'custom' ? 'custom' : r.key;
    if (f.readout_label) f.readout_label.value = r.key === 'custom' ? r.label : '';
    if (f.readout_unit) f.readout_unit.value = r.key === 'custom' ? r.unit : '';
    if (f.readout_kind) f.readout_kind.value = r.kind || 'value';
    if (f.readout_control) f.readout_control.value = r.control || '';
    if (f.readout_day) f.readout_day.value = D.readout_day || '';
    if (f.planned_test) f.planned_test.value = D.planned_test || '';
    showReadoutFields();
  }
  // Under the readout: your own one's name and unit, or what one BioManager works out takes (and its control group).
  function showReadoutFields() {
    const select = $('[data-xp-readout]', info);
    if (!select) return;
    const custom = $('[data-xp-custom]', info);
    if (custom) custom.hidden = select.value !== 'custom';
    const chosen = select.selectedOptions[0];
    const box = $('[data-xp-derived]', info);
    if (box) box.hidden = !select.value.startsWith('derived:');
    const control = $('[data-xp-control]', info);
    if (control) control.hidden = !(chosen && chosen.dataset.perGroup);
    const note = $('[data-xp-derived-note]', info);
    if (note && chosen && select.value !== (D.readout || {}).key) note.textContent = chosen.title || '';
  }
  if (info) {
    let timer = null;
    const save = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          const data = await sendReason(info.action, new FormData(info), { form: true });
          if (data) apply(data);
          else { revertPlanned(); apply(await send(info.action, new FormData(info), { form: true })); }
        } catch (_e) { /* shown */ }
      }, 350);
    };
    $$('[data-autosave]', info).forEach((el) => {
      el.addEventListener('input', () => { if (el.tagName !== 'SELECT') save(); });
      el.addEventListener('change', save);
    });
    const readout = $('[data-xp-readout]', info);
    if (readout) readout.addEventListener('change', showReadoutFields);
  }

  // ---------------------------------------------------------------- the plan and the changes from it

  function drawPlan() {
    const list = $('[data-xp-deviations]');
    if (!list) return;
    const devs = D.deviations || [];
    const count = $('[data-xp-deviation-count]');
    if (count) count.textContent = `(${devs.length})`;
    // A new change from the plan opens the list, so it is seen kept.
    const box = $('[data-xp-deviations-box]');
    if (box && drawPlan.seen !== undefined && devs.length > drawPlan.seen) box.open = true;
    drawPlan.seen = devs.length;
    const when = (iso) => (iso ? new Date(`${iso}Z`).toLocaleString(LOCALE, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '');
    list.innerHTML = devs.length ? devs.slice().reverse().map((d) => `<li class="xp-deviation">
      <div class="xp-dev-head"><b>${esc(d.what_label)}</b><span class="xp-dev-change">${esc(d.before)} → ${esc(d.after)}</span></div>
      <p class="xp-dev-reason">${esc(d.reason)}</p>
      <span class="xp-muted">${esc(d.who)} · ${esc(when(d.when))}</span>
      ${d.detail && d.detail.length ? `<details class="xp-dev-detail"><summary>${esc(t(d.detail.length === 1 ? '%(n)s change' : '%(n)s changes', { n: d.detail.length }))}</summary><ul>${d.detail.map((l) => `<li>${esc(l)}</li>`).join('')}</ul></details>` : ''}
    </li>`).join('') : `<li class="xp-empty">${t('No changes from the plan.')}</li>`;
    const table = $('[data-xp-compare]');
    if (table) {
      table.innerHTML = `<thead><tr><th></th><th>${tc('plan', 'Planned')}</th><th>${tc('plan', 'Done')}</th></tr></thead><tbody>${(D.comparison || []).map((r) => `
        <tr${r.changed ? ' class="is-changed"' : ''}><th scope="row">${esc(r.what)}</th><td>${esc(r.planned)}</td><td>${esc(r.done)}${r.changed ? ` <span class="xp-changed">${t('changed')}</span>` : ''}</td></tr>`).join('')}</tbody>`;
    }
  }

  drawSheet();
  drawChart();
  drawDays();
  drawLabels();
  drawPlan();
  if (window.location.hash.startsWith('#day-')) {
    const target = document.getElementById(window.location.hash.slice(1));
    if (target) target.scrollIntoView({ block: 'center' });
  }
})();
