/* The calendar's Board: your to-dos as cards in lists (app/task_board.py).
 *
 * Inbox, then your lists, then Done. Drag a card to another list, or within
 * one to reorder it; into Done ticks it off. + Add a card makes one in that
 * list; a day written in it ("… Fri", "明天…") is its day on the calendar,
 * shown under the box as it is typed (× keeps the words as written). Inbox
 * also lists what the calendar has to do this week, each opening where it
 * is done;
 * + Add a list makes a list, and a list's ••• renames or deletes it (its
 * cards go back to Inbox). Click a card to change its title, day, notes or
 * list, or to delete it; the tick box beside its title ticks it off.
 * Shown by calendar-page.js when Board is chosen (window.BioBoard.show).
 */
(function () {
  'use strict';

  const host = document.getElementById('biocal-board');
  if (!host) return;
  const DONE = '__done__';
  const LOCALE = document.documentElement.lang || undefined;
  let columns = [];
  let drag = null;

  const SVG = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const toast = (text, bad) => window.BiomanagerShell && window.BiomanagerShell.toast(text, bad ? 'danger' : '');

  function post(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body || {}),
    }).then((r) => r.json().catch(() => ({})).then((d) => {
      if (!r.ok || d.ok === false) throw new Error(d.error || t("Couldn't save that."));
      return d;
    }));
  }
  const fail = (err) => toast(err.message, true);

  function show() {
    return fetch('/calendar/board', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(t("Couldn't load the board.")))))
      .then((d) => { columns = d.columns || []; render(); })
      .catch(fail);
  }

  function dueLabel(card) {
    if (!card.due) return '';
    const d = new Date(`${card.due}T12:00`);
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    const days = Math.round((d - today) / 86400000);
    if (days === 0) return t('today');
    if (days === 1) return t('tomorrow');
    return d.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' });
  }

  function cardHtml(card) {
    const due = dueLabel(card);
    return `<article class="todo-card${card.done ? ' is-done' : ''}${card.readOnly ? ' is-locked' : ''}" data-card="${card.id}"` +
      `${card.readOnly ? '' : ' draggable="true"'}${card.color ? ` style="--card-tint:${esc(card.color)}"` : ''}>` +
      `<label class="todo-tick" title="${esc(card.done ? t('Not done') : t('Done'))}"><input type="checkbox"${card.done ? ' checked' : ''}${card.readOnly ? ' disabled' : ''} data-tick="${card.key}" aria-label="${esc(t('Done'))}"></label>` +
      `<button type="button" class="todo-card-open" data-open="${card.id}"><span class="todo-card-title">${esc(card.title)}</span>` +
      `<span class="todo-card-meta">` +
      (due ? `<span class="todo-due${card.overdue ? ' is-overdue' : ''}">${SVG('calendar')}${esc(due)}</span>` : '') +
      (card.audience ? `<span class="todo-who">${esc(t(card.audience))}</span>` : '') +
      (card.notes ? `<span class="todo-has-notes" title="${esc(t('Has notes'))}">${SVG('note')}</span>` : '') +
      `</span></button></article>`;
  }

  function columnHtml(col) {
    const count = col.cards.length;
    const menu = col.kind === 'list' && !col.theirs
      ? `<button type="button" class="todo-col-more" data-col-menu="${esc(col.name)}" aria-haspopup="menu" aria-label="${esc(t('List options'))}">${SVG('ellipsis')}</button>` : '';
    const icon = col.kind === 'inbox' ? SVG('inbox') : col.kind === 'done' ? SVG('check') : '';
    const add = col.kind === 'done' ? '' :
      `<div class="todo-add" data-add-wrap><button type="button" class="todo-add-btn" data-add="${esc(col.name)}">${SVG('plus')}${esc(t('Add a card'))}</button></div>`;
    const fromCal = (col.calendar || []).length
      ? `<div class="todo-from-cal"><h4>${SVG('calendar')}${esc(t('From the calendar'))}<span class="todo-count">${col.calendar.length}</span></h4>` +
        col.calendar.map(calHtml).join('') + '</div>' : '';
    return `<section class="todo-col todo-col-${col.kind}" data-col="${esc(col.name)}" aria-label="${esc(col.label)}">` +
      `<header class="todo-col-head">${icon}<h3>${esc(col.label)}</h3><span class="todo-count">${count}</span>${menu}</header>` +
      `<div class="todo-cards" data-drop="${esc(col.name)}">${col.cards.map(cardHtml).join('') ||
        (col.kind === 'done' ? `<p class="todo-empty">${esc(t('Tick a card, or drag it here.'))}</p>` : '')}</div>${add}${fromCal}</section>`;
  }

  // Work the calendar has for this week (the colony's, stocks', supplies'):
  // done where it is kept, so the card opens it there.
  function calHtml(item) {
    const due = dueLabel(item);
    return `<a class="todo-card todo-card-cal" data-track="${esc(item.track)}" href="${esc(item.url || '/calendar')}">` +
      `<i class="todo-cal-ring" aria-hidden="true"></i><span class="todo-card-open"><span class="todo-card-title">${esc(item.title)}</span>` +
      `<span class="todo-card-meta"><span class="todo-due${item.overdue ? ' is-overdue' : ''}">${SVG('calendar')}${esc(due)}</span>` +
      `<span class="todo-who">${esc(item.from)}</span></span></span></a>`;
  }

  function render() {
    const done = columns.filter((c) => c.kind === 'done');
    const rest = columns.filter((c) => c.kind !== 'done');
    host.innerHTML = `<div class="todo-board">${rest.map(columnHtml).join('')}${done.map(columnHtml).join('')}` +
      `<section class="todo-col todo-col-new"><button type="button" class="todo-add-list" data-add-list>${SVG('plus')}${esc(t('Add a list'))}</button></section></div>`;
  }

  const findCard = (id) => columns.flatMap((c) => c.cards).find((c) => String(c.id) === String(id));

  // ------------------------------------------------------------ adding

  function openAdder(button) {
    const wrap = button.closest('[data-add-wrap]');
    const list = button.dataset.add;
    wrap.innerHTML = `<form class="todo-add-form"><textarea rows="2" maxlength="200" placeholder="${esc(t('What needs doing? A day too: Fri, tomorrow, Nov 8'))}" aria-label="${esc(t('Card title'))}"></textarea>` +
      `<p class="todo-add-when" hidden>${SVG('calendar')}<span></span><button type="button" data-when-off aria-label="${esc(t('Keep the words as written'))}" title="${esc(t('Keep the words as written'))}">${SVG('close')}</button></p>` +
      `<div class="todo-add-row"><button type="submit" class="btn btn-primary btn-sm">${esc(t('Add card'))}</button>` +
      `<button type="button" class="btn btn-ghost btn-sm" data-add-cancel>${esc(t('Cancel'))}</button></div></form>`;
    const form = wrap.querySelector('form');
    const field = form.querySelector('textarea');
    const hint = form.querySelector('.todo-add-when');
    let readDay = true;
    let asked = 0;
    // The day the words name, under the box as they are typed.
    const look = () => {
      const seq = ++asked;
      const text = field.value.trim();
      if (!text || !readDay) { hint.hidden = true; return; }
      fetch(`/calendar/board/when?text=${encodeURIComponent(text)}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then((r) => r.json()).then((d) => {
          if (seq !== asked) return;
          hint.hidden = !d.due;
          if (d.due) hint.querySelector('span').textContent = t('%(day)s · on the calendar', { day: dueLabel({ due: d.due }) });
        }).catch(() => {});
    };
    let timer = null;
    field.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(look, 180); });
    hint.querySelector('[data-when-off]').addEventListener('click', () => { readDay = false; hint.hidden = true; field.focus(); });
    field.focus();
    const submit = () => {
      const title = field.value.trim();
      if (!title) return;
      post('/calendar/board/cards', { title, list, read_day: readDay }).then(() => show().then(() => {
        // Ready for the next one, as Trello keeps it open.
        const again = host.querySelector(`[data-add="${CSS.escape(list)}"]`);
        if (again) openAdder(again);
      })).catch(fail);
    };
    form.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
    field.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
      if (e.key === 'Escape') render();
    });
    form.querySelector('[data-add-cancel]').addEventListener('click', render);
  }

  function addList() {
    window.BioDialog.prompt(t('Name the list'), '', { placeholder: t('This week, Waiting on…'), okLabel: t('Add list') }).then((name) => {
      name = (name || '').trim();
      if (name) post('/calendar/board/lists', { add: name }).then(show).catch(fail);
    });
  }

  // ------------------------------------------------------------ a list's menu

  let menu = null;
  function closeMenu() { if (menu) { menu.remove(); menu = null; } }
  function listMenu(button) {
    closeMenu();
    const name = button.dataset.colMenu;
    menu = document.createElement('div');
    menu.className = 'menu todo-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `<button type="button" class="menu-item" role="menuitem" data-do="rename">${SVG('edit')}${esc(t('Rename…'))}</button>` +
      `<button type="button" class="menu-item" role="menuitem" data-do="left">${SVG('chevron-left')}${esc(t('Move left'))}</button>` +
      `<button type="button" class="menu-item" role="menuitem" data-do="right">${SVG('chevron-right')}${esc(t('Move right'))}</button>` +
      `<button type="button" class="menu-item menu-item-danger" role="menuitem" data-do="delete">${SVG('trash')}${esc(t('Delete list'))}</button>`;
    document.body.appendChild(menu);
    const r = button.getBoundingClientRect();
    menu.style.top = `${r.bottom + 6}px`;
    menu.style.left = `${Math.max(8, Math.min(r.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8))}px`;
    menu.addEventListener('click', (e) => {
      const item = e.target.closest('[data-do]');
      if (!item) return;
      closeMenu();
      const order = columns.filter((c) => c.kind === 'list' && !c.theirs).map((c) => c.name);
      const at = order.indexOf(name);
      if (item.dataset.do === 'rename') {
        window.BioDialog.prompt(t('Name the list'), name, { okLabel: t('Rename') }).then((next) => {
          next = (next || '').trim();
          if (next && next !== name) post('/calendar/board/lists', { rename: [name, next] }).then(show).catch(fail);
        });
      } else if (item.dataset.do === 'delete') {
        window.BioDialog.confirm(t('Delete the list %(name)s? Its cards go back to Inbox.', { name }), { danger: true, okLabel: t('Delete list') })
          .then((ok) => { if (ok) post('/calendar/board/lists', { remove: name }).then(show).catch(fail); });
      } else {
        const to = item.dataset.do === 'left' ? at - 1 : at + 1;
        if (to < 0 || to >= order.length) return;
        order.splice(at, 1);
        order.splice(to, 0, name);
        post('/calendar/board/lists', { order }).then(show).catch(fail);
      }
    });
  }

  // ------------------------------------------------------------ a card

  function openCard(card) {
    if (card.readOnly) {
      toast(t('%(title)s: %(whose)s’s to-do, read only.', { title: card.title, whose: card.audience ? t(card.audience) : card.owner || t('someone else') }));
      return;
    }
    const lists = columns.filter((c) => c.kind !== 'done');
    const dialog = document.createElement('dialog');
    dialog.className = 'record-dialog record-dialog-narrow bio-dialog todo-dialog';
    dialog.setAttribute('aria-labelledby', 'todo-dialog-title');
    dialog.innerHTML = `<form method="dialog"><header class="record-dialog-head"><div><h3 id="todo-dialog-title">${esc(t('To-do'))}</h3></div></header>` +
      `<div class="record-dialog-body bio-dialog-body">` +
      `<label class="record-wide">${esc(t('Title'))}<input name="title" maxlength="200" value="${esc(card.title)}"></label>` +
      `<label>${esc(t('Due'))}<input type="date" name="due" value="${esc(card.due)}"></label>` +
      `<label>${esc(t('List'))}<select name="list">${lists.map((c) => `<option value="${esc(c.name)}"${c.name === card.list ? ' selected' : ''}>${esc(c.label)}</option>`).join('')}` +
      `<option value="${DONE}"${card.done ? ' selected' : ''}>${esc(t('Done'))}</option></select></label>` +
      `<label class="record-wide">${esc(t('Notes'))}<textarea name="notes" rows="4">${esc(card.notes)}</textarea></label>` +
      `<p class="todo-dialog-hint record-wide">${esc(t('With a due day it is on the calendar on that day too.'))}</p></div>` +
      `<footer class="record-dialog-foot"><button type="button" class="btn btn-ghost todo-delete" data-delete>${SVG('trash')}${esc(t('Delete'))}</button>` +
      `<span class="ml-auto"></span><button type="button" class="btn" value="cancel" data-cancel>${esc(t('Cancel'))}</button>` +
      `<button type="submit" class="btn btn-primary" value="save">${esc(t('Save'))}</button></footer></form>`;
    document.body.appendChild(dialog);
    const f = dialog.querySelector('form');
    dialog.addEventListener('close', () => dialog.remove());
    dialog.querySelector('[data-cancel]').addEventListener('click', () => dialog.close());
    dialog.querySelector('[data-delete]').addEventListener('click', () => {
      window.BioDialog.confirm(t('Delete “%(title)s”?', { title: card.title }), { danger: true }).then((ok) => {
        if (!ok) return;
        post(`/calendar/items/${card.key}/delete`, {}).then(() => { dialog.close(); show(); }).catch(fail);
      });
    });
    f.addEventListener('submit', (e) => {
      e.preventDefault();
      const body = { title: f.title.value, due: f.due.value, notes: f.notes.value };
      const list = f.list.value;
      if (list !== (card.done ? DONE : card.list)) body.list = list;
      post(`/calendar/board/cards/${card.id}`, body).then(() => { dialog.close(); show(); }).catch(fail);
    });
    dialog.showModal();
    f.title.focus();
  }

  // ------------------------------------------------------------ clicks

  host.addEventListener('click', (e) => {
    const add = e.target.closest('[data-add]');
    const open = e.target.closest('[data-open]');
    const more = e.target.closest('[data-col-menu]');
    if (add) return openAdder(add);
    if (e.target.closest('[data-add-list]')) return addList();
    if (more) { e.stopPropagation(); return listMenu(more); }
    if (open) { const card = findCard(open.dataset.open); if (card) openCard(card); }
    return undefined;
  });
  host.addEventListener('change', (e) => {
    const tick = e.target.closest('[data-tick]');
    if (!tick) return;
    post(`/calendar/items/${tick.dataset.tick}/toggle`, {}).then(show).catch((err) => { tick.checked = !tick.checked; fail(err); });
  });
  document.addEventListener('click', (e) => { if (menu && !menu.contains(e.target)) closeMenu(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

  // ------------------------------------------------------------ dragging

  function spot(e) {
    const zone = e.target.closest && e.target.closest('[data-drop]');
    if (!zone) return null;
    const cards = Array.from(zone.querySelectorAll('.todo-card:not(.is-dragging)'));
    const below = cards.find((c) => e.clientY < c.getBoundingClientRect().top + c.offsetHeight / 2);
    return { zone, list: zone.dataset.drop, before: below ? below.dataset.card : null, after: below ? null : (cards.length ? cards[cards.length - 1].dataset.card : null), below };
  }
  function unmark() {
    host.querySelectorAll('.is-drop-zone, .is-drop-before').forEach((el) => el.classList.remove('is-drop-zone', 'is-drop-before'));
    host.querySelectorAll('.todo-drop-end').forEach((el) => el.classList.remove('todo-drop-end'));
  }
  host.addEventListener('dragstart', (e) => {
    const card = e.target.closest && e.target.closest('.todo-card[draggable="true"]');
    if (!card) return;
    drag = card;
    card.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', card.textContent.trim().slice(0, 200)); } catch (_) { /* old browser */ }
  });
  host.addEventListener('dragover', (e) => {
    if (!drag) return;
    const at = spot(e);
    unmark();
    if (!at) return;
    e.preventDefault();
    at.zone.classList.add('is-drop-zone');
    if (at.below) at.below.classList.add('is-drop-before'); else at.zone.classList.add('todo-drop-end');
  });
  host.addEventListener('drop', (e) => {
    if (!drag) return;
    const at = spot(e);
    unmark();
    if (!at) return;
    e.preventDefault();
    const body = { list: at.list };
    if (at.list !== DONE) {
      if (at.before) body.before = Number(at.before);
      else if (at.after) body.after = Number(at.after);
    }
    post(`/calendar/board/cards/${drag.dataset.card}`, body).then(show).catch(fail);
  });
  host.addEventListener('dragend', () => {
    if (drag) drag.classList.remove('is-dragging');
    drag = null;
    unmark();
  });

  window.BioBoard = { show };
})();
