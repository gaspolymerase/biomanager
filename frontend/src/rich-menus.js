// The small dialogs behind some "/" items and the toolbar's colour button:
// colours and highlights, a link to another page, a date, a reminder.

import { COLORS } from './markdown-extras.js';
import { api, ask, escapeHtml, isoDate } from './util.js';

const COLOR_NAMES = {
  gray: 'Gray', brown: 'Brown', orange: 'Orange', yellow: 'Yellow', green: 'Green',
  blue: 'Blue', purple: 'Purple', pink: 'Pink', red: 'Red',
};

function popover(className, html, anchor) {
  document.querySelectorAll('.nb-pop').forEach((p) => p.remove());
  const pop = document.createElement('div');
  pop.className = `nb-pop ${className}`;
  pop.innerHTML = html;
  document.body.appendChild(pop);
  const r = anchor ? anchor.getBoundingClientRect() : { left: window.innerWidth / 2 - 130, top: window.innerHeight / 3, bottom: window.innerHeight / 3 };
  const box = pop.getBoundingClientRect();
  const below = r.bottom + 6 + box.height < window.innerHeight;
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - box.width - 8, r.left)) + window.scrollX}px`;
  pop.style.top = `${(below ? r.bottom + 6 : Math.max(8, r.top - box.height - 6)) + window.scrollY}px`;
  const close = () => { pop.remove(); document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  const away = (e) => { if (!pop.contains(e.target) && !(anchor && anchor.contains(e.target))) close(); };
  const esc = (e) => { if (e.key === 'Escape') close(); };
  setTimeout(() => { document.addEventListener('mousedown', away); document.addEventListener('keydown', esc); }, 0);
  return { pop, close };
}

// Where the caret sits in a coloured stretch of text, or the whole line
// when nothing is selected (as Notion's /red colours the block).
function selectLineIfEmpty(editor) {
  const { state } = editor;
  if (!state.selection.empty) return;
  const { $from } = state.selection;
  if (!$from.parent.isTextblock || !$from.parent.content.size) return;
  editor.commands.setTextSelection({ from: $from.start(), to: $from.end() });
}

export function openColorMenu(editor, anchor) {
  const now = editor.getAttributes('tint');
  const swatch = (kind, c) => `<button type="button" class="nb-swatch nb-swatch-${kind}${(kind === 'color' ? now.color : now.bg) === c ? ' is-on' : ''}" data-kind="${kind}" data-c="${c}" title="${c ? COLOR_NAMES[c] : (kind === 'color' ? 'Default' : 'None')}"><span ${c ? `data-${kind === 'color' ? 'color' : 'bg'}="${c}"` : ''}>A</span></button>`;
  const { pop, close } = popover('nb-color-menu', `
    <div class="nb-pop-label">Text colour</div>
    <div class="nb-swatches">${swatch('color', '')}${COLORS.map((c) => swatch('color', c)).join('')}</div>
    <div class="nb-pop-label">Highlight</div>
    <div class="nb-swatches">${swatch('bg', '')}${COLORS.map((c) => swatch('bg', c)).join('')}</div>`, anchor);
  pop.addEventListener('mousedown', (e) => e.preventDefault());
  pop.addEventListener('click', (e) => {
    const b = e.target.closest('.nb-swatch');
    if (!b) return;
    selectLineIfEmpty(editor);
    editor.chain().focus().setTint({ [b.dataset.kind]: b.dataset.c }).run();
    close();
  });
}

export function openPagePicker(editor) {
  const { pop, close } = popover('nb-page-picker', `
    <div class="nb-pop-label">Link to a page</div>
    <input type="search" class="field nb-pop-search" placeholder="Search your pages and pages shared with you" aria-label="Search pages">
    <div class="nb-pop-list"><div class="insert-empty">Loading…</div></div>`, null);
  const input = pop.querySelector('input');
  const list = pop.querySelector('.nb-pop-list');
  let results = [];
  let seq = 0;
  const pick = (page) => {
    close();
    editor.chain().focus()
      .insertContent([{ type: 'text', text: page.title, marks: [{ type: 'link', attrs: { href: `/notebook?page=${page.id}` } }] }, { type: 'text', text: ' ' }])
      .run();
  };
  const load = async () => {
    const mine = ++seq;
    try {
      const data = await api(`/notebook/api/search?q=${encodeURIComponent(input.value.trim())}`);
      if (mine !== seq) return;
      results = (data.results || []).slice(0, 12);
      list.innerHTML = results.length ? results.map((p, i) => `<button type="button" class="nb-pop-row" data-i="${i}"><b>${escapeHtml(p.title)}</b><small>${escapeHtml(p.mine ? (p.tab || '') : p.owner_name)}</small></button>`).join('')
        : '<div class="insert-empty">No page matches.</div>';
    } catch (_e) {
      list.innerHTML = '<div class="insert-empty">Pages could not be loaded.</div>';
    }
  };
  let timer = null;
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 200); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && results[0]) { e.preventDefault(); pick(results[0]); } });
  list.addEventListener('click', (e) => {
    const row = e.target.closest('.nb-pop-row');
    if (row) pick(results[Number(row.dataset.i)]);
  });
  load();
  input.focus();
}

function dateLabel(day, time) {
  const d = new Date(`${day}T${time || '12:00'}`);
  if (Number.isNaN(d.getTime())) return day;
  const words = d.toLocaleDateString(document.documentElement.lang || undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  return time ? `${words} ${time}` : words;
}

export function openDatePicker(editor) {
  const { pop, close } = popover('nb-date-picker', `
    <div class="nb-pop-label">Insert a date</div>
    <input type="date" class="field" value="${isoDate()}" aria-label="Date">
    <div class="nb-pop-actions"><button type="button" class="btn btn-primary btn-sm">Insert</button></div>`, null);
  const input = pop.querySelector('input');
  const go = () => {
    if (!input.value) return;
    close();
    editor.chain().focus().insertContent(`${input.value} `).run();
  };
  pop.querySelector('button').addEventListener('click', go);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
  input.focus();
}

// A reminder is a to-do in your own calendar on that day (and time), and a
// link to it here, so the page says what was planned and when.
export function openReminder(editor) {
  const { $from } = editor.state.selection;
  const line = $from.parent.isTextblock ? $from.parent.textContent.trim() : '';
  const tomorrow = new Date(Date.now() + 86400000);
  const { pop, close } = popover('nb-reminder', `
    <div class="nb-pop-label">Remind me</div>
    <input type="text" class="field" maxlength="200" placeholder="What to do, e.g. check the transfection" aria-label="What" value="${escapeHtml(line.slice(0, 200))}">
    <div class="nb-pop-row2">
      <input type="date" class="field" value="${isoDate(tomorrow)}" aria-label="Day">
      <input type="time" class="field" value="09:00" aria-label="Time (leave empty for all day)">
    </div>
    <p class="nb-pop-note">Adds a to-do to your calendar on that day.</p>
    <div class="nb-pop-actions"><button type="button" class="btn btn-primary btn-sm">Add reminder</button></div>`, null);
  const [what, day, time] = pop.querySelectorAll('input');
  const go = async () => {
    const title = what.value.trim();
    if (!title || !day.value) { (title ? day : what).focus(); return; }
    const at = time.value || '';
    const start = `${day.value}T${at || '00:00'}:00`;
    let end = start;
    if (at) {
      const e = new Date(`${day.value}T${at}`);
      e.setMinutes(e.getMinutes() + 30);
      end = `${isoDate(e)}T${String(e.getHours()).padStart(2, '0')}:${String(e.getMinutes()).padStart(2, '0')}:00`;
    }
    try {
      await api('/calendar/items', { method: 'POST', body: { kind: 'task', title, start, end, isAllday: !at, audience: '0' } });
    } catch (err) {
      ask.alert(`The reminder could not be added: ${err.message}`);
      return;
    }
    close();
    editor.chain().focus()
      .insertContent([{ type: 'text', text: `⏰ ${dateLabel(day.value, at)} · ${title}`, marks: [{ type: 'link', attrs: { href: `/calendar?date=${day.value}` } }] }, { type: 'text', text: ' ' }])
      .run();
  };
  pop.querySelector('button').addEventListener('click', go);
  pop.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
  what.focus();
  what.select();
}
