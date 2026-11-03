// Run mode: go through a protocol at the bench one step at a time. The
// page's checklist is shown a step at a time in large type; Done ticks the
// step in the page with the time ("— ✓ 14:32"), durations in the step get
// their own timer buttons, and a deviation is written under the page's
// Deviations heading with the time and step number. A table written under
// the step is shown with it, as it reads in the page now. The screen is kept
// on while it is open (where the browser allows).

import { appendLogLine, checkStep, tableRows, taskItems } from './docops.js';
import { findDurations, timers } from './timers.js';
import { ask, el, escapeHtml } from './util.js';

function tableHtml(table) {
  const rows = tableRows(table).map((cells) => `<tr>${cells.map((c) => {
    const tag = c.header ? 'th' : 'td';
    const span = `${c.colspan > 1 ? ` colspan="${c.colspan}"` : ''}${c.rowspan > 1 ? ` rowspan="${c.rowspan}"` : ''}`;
    return `<${tag}${span}>${escapeHtml(c.text)}</${tag}>`;
  }).join('')}</tr>`).join('');
  return `<div class="nb-run-table"><table>${rows}</table></div>`;
}

export function openRunMode(editor, { title = '' } = {}) {
  if (!taskItems(editor.state.doc).length) {
    ask.alert('This page has no checklist to run. Steps written as a task list (☐) can be run one at a time.');
    return;
  }
  let index = taskItems(editor.state.doc).findIndex((t) => !t.checked);
  if (index < 0) index = 0;
  let wakeLock = null;
  try { navigator.wakeLock?.request('screen').then((l) => { wakeLock = l; }).catch(() => {}); } catch (_e) { /* not supported */ }

  const overlay = el('div', { class: 'nb-run', role: 'dialog', 'aria-label': 'Run protocol' });
  document.body.appendChild(overlay);
  document.body.classList.add('nb-run-open');

  function render() {
    const steps = taskItems(editor.state.doc);
    if (index >= steps.length) index = steps.length - 1;
    const step = steps[index];
    const done = steps.filter((s) => s.checked).length;
    const clean = step.text.replace(/\s—\s✓\s\d{1,2}:\d{2}$/, '');
    const durations = findDurations(clean);
    overlay.innerHTML = `
      <div class="nb-run-top">
        <div class="nb-run-title">${escapeHtml(title || 'Protocol')}</div>
        <div class="nb-run-progress"><i style="width:${(100 * done) / steps.length}%"></i></div>
        <div class="nb-run-count">Step ${index + 1} of ${steps.length} · ${done} done</div>
        <button type="button" class="nb-run-close" data-act="close" aria-label="Close run mode">×</button>
      </div>
      <div class="nb-run-step${step.checked ? ' is-done' : ''}">
        <div class="nb-run-num">${index + 1}</div>
        <div class="nb-run-body">
          <div class="nb-run-text">${escapeHtml(clean || '(empty step)')}${step.checked ? '<div class="nb-run-stamp">Done</div>' : ''}</div>
          ${step.tables.map(tableHtml).join('')}
        </div>
      </div>
      ${durations.length ? `<div class="nb-run-timers">${durations.map((d, i) => `<button type="button" class="nb-run-timer" data-timer="${i}">⏱ Start ${escapeHtml(d.text)}</button>`).join('')}</div>` : ''}
      <div class="nb-run-next">${steps[index + 1] ? `Next: ${escapeHtml(steps[index + 1].text.replace(/\s—\s✓\s\d{1,2}:\d{2}$/, '').slice(0, 120))}` : 'Last step'}</div>
      <div class="nb-run-actions">
        <button type="button" data-act="back"${index === 0 ? ' disabled' : ''}>← Back</button>
        <button type="button" data-act="deviation">Note a deviation</button>
        <button type="button" data-act="skip"${index >= steps.length - 1 ? ' disabled' : ''}>Skip</button>
        <button type="button" class="is-primary" data-act="${step.checked ? 'undo' : 'done'}">${step.checked ? 'Undo tick' : '✓ Done'}</button>
      </div>`;
    overlay._durations = durations;
    overlay._step = step;
    overlay._clean = clean;
  }

  function close() {
    editor.off('update', onUpdate);
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    document.body.classList.remove('nb-run-open');
    try { wakeLock?.release(); } catch (_e) { /* ignored */ }
  }

  function act(name) {
    const steps = taskItems(editor.state.doc);
    const step = steps[index];
    if (name === 'close') { close(); return; }
    if (name === 'back') index = Math.max(0, index - 1);
    if (name === 'skip') index = Math.min(steps.length - 1, index + 1);
    if (name === 'done') {
      checkStep(editor, step.pos, true);
      const next = taskItems(editor.state.doc).findIndex((t, i) => i > index && !t.checked);
      if (next >= 0) index = next;
      else if (taskItems(editor.state.doc).every((t) => t.checked)) {
        render();
        overlay.querySelector('.nb-run-next').textContent = 'All steps done.';
        return;
      }
    }
    if (name === 'undo') checkStep(editor, step.pos, false, { stamp: false });
    if (name === 'deviation') {
      ask.prompt(`What was different at step ${index + 1}?`, '', { okLabel: 'Note it' }).then((text) => {
        if (text && text.trim()) appendLogLine(editor, /deviation/i, 'Deviations', `Step ${index + 1} (${overlay._clean.slice(0, 50)}): ${text.trim()}`);
        render();
      });
      return;
    }
    render();
  }

  function onKey(event) {
    if (event.target && /INPUT|TEXTAREA/.test(event.target.tagName)) return;
    if (event.key === 'Escape') close();
    else if (event.key === 'ArrowRight') act('skip');
    else if (event.key === 'ArrowLeft') act('back');
    else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); act(overlay._step?.checked ? 'skip' : 'done'); }
  }

  const onUpdate = () => render();
  overlay.addEventListener('click', (event) => {
    const b = event.target.closest('button');
    if (!b) return;
    if (b.dataset.timer !== undefined) {
      const d = overlay._durations[Number(b.dataset.timer)];
      timers().start(`Step ${index + 1}: ${overlay._clean.slice(0, 50)}`, d.seconds);
      b.textContent = `⏱ ${d.text} started`;
      return;
    }
    if (b.dataset.act) act(b.dataset.act);
  });
  editor.on('update', onUpdate);
  document.addEventListener('keydown', onKey);
  render();
  return { close };
}
