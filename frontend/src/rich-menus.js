// The small dialogs behind some "/" items and the toolbar's colour button:
// colours and highlights, a link to another page, a date, a reminder.

import { COLORS } from './markdown-extras.js';
import { api, ask, escapeHtml, isoDate, openInApp } from './util.js';

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

// Sub-pages and links between pages (extensions/PageLinks.js). A link is
// a pageLink piece; alone on an empty line it shows as a page in this one.
const pageLinkNode = (page) => ({ type: 'pageLink', attrs: { id: String(page.id), title: page.title || 'Untitled page' } });

// Put a link to a page where the caret is: on an empty line it fills the
// line (a page in this one); in a sentence it is a chip; `block` puts it on
// a line of its own below a line that has text.
export function insertPageLink(editor, page, { block = false } = {}) {
  const { $from } = editor.state.selection;
  const emptyLine = $from.parent.isTextblock && $from.parent.content.size === 0;
  if (block && !emptyLine && $from.parent.isTextblock) {
    const after = $from.after();
    return editor.chain().focus().insertContentAt(after, { type: 'paragraph', content: [pageLinkNode(page)] }).run();
  }
  return editor.chain().focus().insertContent(emptyLine ? [pageLinkNode(page)] : [pageLinkNode(page), { type: 'text', text: ' ' }]).run();
}

// A new page inside the one being written in (in the same topic).
export async function newSubPage(editor, title = '') {
  const parent = editor.storage.pageLink && editor.storage.pageLink.pageId;
  const data = await api('/notebook/api/pages/new', { method: 'POST', body: { parent_page_id: parent, title } });
  return { id: data.page_id, title: title || 'Untitled page', url: data.url };
}

function failed(err) {
  ask.alert(`The page could not be made (${err.message}).`);
}

// "/page": a new page inside this one, opened in a BioManager tab to write in.
export async function addSubPage(editor) {
  try {
    const page = await newSubPage(editor);
    insertPageLink(editor, page, { block: true });
    openInApp(page.url, editor);
  } catch (err) { failed(err); }
}

// "Turn into page": this line's words become a new page's title, and the
// line becomes the link to it.
export async function turnIntoPage(editor) {
  const { $from } = editor.state.selection;
  if (!$from.parent.isTextblock) return;
  const title = $from.parent.textContent.trim();
  const from = $from.before();
  const to = $from.after();
  try {
    const page = await newSubPage(editor, title);
    editor.chain().focus().insertContentAt({ from, to }, { type: 'paragraph', content: [pageLinkNode(page)] }).run();
  } catch (err) { failed(err); }
}

// Find a page to link (or, with a name typed, make one inside this page):
// from "/" Link to page and from typing "[[".
export function openPagePicker(editor, { inline = false } = {}) {
  const coords = (() => { try { return editor.view.coordsAtPos(editor.state.selection.from); } catch (_e) { return null; } })();
  const anchor = inline && coords ? { getBoundingClientRect: () => coords, contains: () => false } : null;
  const { pop, close } = popover('nb-page-picker', `
    <div class="nb-pop-label">Link to a page</div>
    <input type="search" class="field nb-pop-search" placeholder="Search your pages and pages shared with you" aria-label="Search pages">
    <div class="nb-pop-list"><div class="insert-empty">Loading…</div></div>
    <button type="button" class="nb-pop-row nb-pop-new"><b>＋ New page</b><small>inside this one</small></button>`, anchor);
  const input = pop.querySelector('input');
  const list = pop.querySelector('.nb-pop-list');
  const addNew = pop.querySelector('.nb-pop-new');
  let results = [];
  let seq = 0;
  let active = 0;
  const pick = (page) => {
    close();
    insertPageLink(editor, page);
  };
  const makeNew = async () => {
    const title = input.value.trim();
    close();
    try { insertPageLink(editor, await newSubPage(editor, title)); } catch (err) { failed(err); }
  };
  const mark = () => pop.querySelectorAll('.nb-pop-row').forEach((row, i) => row.classList.toggle('is-active', i === active));
  const paintNew = () => {
    const q = input.value.trim();
    addNew.querySelector('b').textContent = q ? `＋ New page “${q}”` : '＋ New page';
  };
  const load = async () => {
    const mine = ++seq;
    paintNew();
    try {
      const data = await api(`/notebook/api/search?q=${encodeURIComponent(input.value.trim())}`);
      if (mine !== seq) return;
      const here = editor.storage.pageLink && String(editor.storage.pageLink.pageId);
      results = (data.results || []).filter((p) => String(p.id) !== here).slice(0, 12);
      list.innerHTML = results.length ? results.map((p, i) => `<button type="button" class="nb-pop-row" data-i="${i}"><b>${escapeHtml(p.title)}</b><small>${escapeHtml(p.mine ? (p.tab || '') : p.owner_name)}</small></button>`).join('')
        : '<div class="insert-empty">No page matches.</div>';
    } catch (_e) {
      list.innerHTML = '<div class="insert-empty">Pages could not be loaded.</div>';
    }
    active = 0;
    mark();
  };
  let timer = null;
  input.addEventListener('input', () => { paintNew(); clearTimeout(timer); timer = setTimeout(load, 200); });
  input.addEventListener('keydown', (e) => {
    const rows = pop.querySelectorAll('.nb-pop-row');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      active = (active + (e.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length;
      mark();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (rows[active]) rows[active].click();
    } else if (e.key === 'Escape') {
      editor.commands.focus();
    }
  });
  list.addEventListener('click', (e) => {
    const row = e.target.closest('.nb-pop-row');
    if (row) pick(results[Number(row.dataset.i)]);
  });
  addNew.addEventListener('click', makeNew);
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
