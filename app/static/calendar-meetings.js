/* Meetings, in the calendar (templates/calendar.html).
 *
 * A meeting series — lab meeting, journal club — is kept here: its day and
 * time, where, and who presents in what order. New → Meeting sets one up;
 * Meetings in the side panel lists them, each with who presents next. Its
 * dialog moves the rotation on or back, opens the notes for the next
 * meeting (a notebook page shared with everyone in it), puts the coming
 * meetings on the calendar, each naming its presenter, and lists the notes
 * of the past ones. The series and its notes are app/lab_notebook.py's
 * (/notebook/api/meetings); a meeting's notes are notebook pages.
 */
(function () {
  'use strict';

  const API = '/notebook/api/meetings';
  const LOCALE = window.BM_LANG === 'zh' ? 'zh-CN' : undefined;
  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const toast = (text, bad) => window.BiomanagerShell && window.BiomanagerShell.toast(text, bad ? 'danger' : '');
  const day = (iso, opts) => (iso ? new Date(iso + 'T12:00').toLocaleDateString(LOCALE, opts || { weekday: 'short', day: 'numeric', month: 'short' }) : '');

  function api(url, body) {
    return fetch(url, body === undefined ? { headers: { Accept: 'application/json' } } : {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body),
    }).then((r) => r.json().catch(() => ({})).then((j) => {
      if (!r.ok || j.ok === false) throw new Error(j.error || t('That could not be saved.'));
      return j;
    }));
  }

  let data = { series: [], people: [], weekdays: [] };
  const list = $('#cal-meetings');
  const dialog = document.createElement('dialog');
  dialog.className = 'record-dialog cal-meeting-dialog';
  dialog.setAttribute('aria-labelledby', 'cal-meeting-title');
  document.body.appendChild(dialog);
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });

  function load() {
    return api(API).then((d) => { data = d; drawList(); return d; })
      .catch(() => { if (list) list.innerHTML = `<li class="cal-muted">${t('Meetings could not be loaded.')}</li>`; });
  }

  // ---- the side panel: each series, who presents next
  function drawList() {
    if (!list) return;
    list.innerHTML = data.series.length
      ? data.series.map((s) => `<li><button type="button" class="cal-meeting-item" data-series="${s.id}">
          <span class="cal-meeting-name">${esc(s.name)}</span>
          <small>${s.next ? esc(t('%(name)s next', { name: s.next.name })) + (s.next.date ? ' · ' + esc(day(s.next.date)) : '') : esc(t('No one in the rotation yet.'))}</small>
        </button></li>`).join('')
      : `<li class="cal-muted">${t('Set one up and the calendar keeps track of who presents next.')}</li>`;
  }
  if (list) list.addEventListener('click', (e) => {
    const b = e.target.closest('[data-series]');
    if (b) show(Number(b.dataset.series));
  });

  // ---- a series: who is next, the rotation, its notes
  function show(id) {
    const s = data.series.find((x) => x.id === id);
    if (!s) return;
    const when = [s.weekday_name ? t(s.weekday_name + 's') : '', s.time, s.location].filter(Boolean).join(' · ');
    dialog.innerHTML = `
      <div class="cal-dialog-inner">
        <header class="record-dialog-head">
          <div><h3 id="cal-meeting-title">${esc(s.name)}</h3>${when ? `<p class="record-dialog-sub">${esc(when)}</p>` : ''}</div>
          <button type="button" class="modal-close" data-close aria-label="${t('Close')}">${icon('close')}</button>
        </header>
        <div class="record-dialog-body cal-meeting-body">
          ${s.next ? `<div class="cal-meeting-next"><span class="cal-meeting-face">${esc(initials(s.next.name))}</span>
              <div><small>${t('Next to present')}${s.next.date ? ' · ' + esc(day(s.next.date)) : ''}</small><b>${esc(s.next.name)}</b></div>
              ${s.can_edit ? `<span class="cal-meeting-steer"><button type="button" class="btn btn-sm" data-back title="${t('Move the rotation back one')}">‹</button><button type="button" class="btn btn-sm" data-skip title="${t('Move the rotation on one person')}">${t('Skip ›')}</button></span>` : ''}
            </div>` : `<p class="cal-muted">${t('No one in the rotation yet.')}</p>`}
          ${s.upcoming.length > 1 ? `<ol class="cal-meeting-rota">${s.upcoming.slice(1).map((u) => `<li><span>${esc(u.name)}</span><small>${esc(day(u.date, { day: 'numeric', month: 'short' }))}</small></li>`).join('')}</ol>` : ''}
          <div class="cal-meeting-actions">
            <button type="button" class="btn btn-primary" data-note>${icon('note')} ${t('Notes for the next meeting')}</button>
            ${s.can_edit ? `<button type="button" class="btn" data-cal>${icon('calendar')} ${t('Put the next meetings on the calendar')}</button>` : ''}
          </div>
          ${s.notes.length ? `<section><h4 class="cal-section-h">${t('Past notes')}</h4><ul class="cal-meeting-notes">${s.notes.map((n) => `<li><a href="${esc(n.url)}">${esc(n.title)}</a><small>${esc(day(n.date, { day: 'numeric', month: 'short', year: 'numeric' }))}</small></li>`).join('')}</ul></section>` : ''}
        </div>
        <footer class="record-dialog-foot">
          ${s.can_edit ? `<button type="button" class="btn" data-edit>${icon('edit')} ${t('Edit')}</button>` : ''}
          <span style="flex: 1"></span>
          <button type="button" class="btn" data-close>${t('Done')}</button>
        </footer>
      </div>`;
    dialog.onclick = (e) => {
      if (e.target === dialog) { dialog.close(); return; }
      const b = e.target.closest('button');
      if (!b) return;
      if (b.hasAttribute('data-close')) dialog.close();
      if (b.hasAttribute('data-edit')) form(s);
      if (b.hasAttribute('data-skip') || b.hasAttribute('data-back')) {
        api(`${API}/${s.id}/advance`, { step: b.hasAttribute('data-back') ? -1 : 1 }).then(() => load()).then(() => show(s.id));
      }
      if (b.hasAttribute('data-note')) {
        api(`${API}/${s.id}/note`, {}).then((r) => { window.location.href = r.url; }).catch((err) => toast(err.message, true));
      }
      if (b.hasAttribute('data-cal')) {
        window.BioDialog.prompt(t('How many of the coming meetings?'), String(Math.max(4, s.members.length)), { okLabel: t('Add to the calendar') }).then((n) => {
          if (!n) return;
          api(`${API}/${s.id}/calendar`, { count: Number(n) }).then((r) => {
            toast(r.made ? t(r.made > 1 ? '%(n)s meetings added to the calendar, each naming its presenter.' : '%(n)s meeting added to the calendar, each naming its presenter.', { n: r.made }) : t('They are on the calendar already.'));
            document.dispatchEvent(new CustomEvent('calendar:changed'));
          }).catch((err) => toast(err.message, true));
        });
      }
    };
    if (!dialog.open) dialog.showModal();
  }

  function initials(name) {
    const parts = String(name || '?').split(/[\s._-]+/).filter(Boolean);
    return (parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] || '?').slice(0, 2)).toUpperCase();
  }

  // ---- set one up, or change it
  function form(s) {
    let me = '';
    try { me = JSON.parse(document.getElementById('cal-data').textContent).me || ''; } catch (_) {}
    let members = s ? s.members.map((m) => m.username) : (me ? [me] : []);
    const fields = { name: s ? s.name : t('Lab meeting'), weekday: s && s.weekday !== null ? String(s.weekday) : '', time: s ? s.time : '', location: s ? s.location : '' };
    const names = {};
    data.people.forEach((p) => { names[p.username] = p.name; });
    const draw = () => {
      const old = $('form', dialog);
      if (old) ['name', 'weekday', 'time', 'location'].forEach((k) => { fields[k] = old.elements[k].value; });
      dialog.innerHTML = `
        <form class="cal-dialog-inner" novalidate>
          <header class="record-dialog-head">
            <div><h3 id="cal-meeting-title">${s ? esc(t('Edit %(name)s', { name: s.name })) : t('New meeting')}</h3>
              <p class="record-dialog-sub">${t('A meeting that comes round every week, and who presents in turn.')}</p></div>
            <button type="button" class="modal-close" data-close aria-label="${t('Close')}">${icon('close')}</button>
          </header>
          <div class="record-dialog-body cal-meeting-body">
            <label>${t('Name')} <input name="name" required value="${esc(fields.name)}"></label>
            <div class="cal-meeting-row">
              <label>${t('Every')} <select name="weekday"><option value="">${t('— day —')}</option>${data.weekdays.map((w, i) => `<option value="${i}"${fields.weekday === String(i) ? ' selected' : ''}>${esc(t(w))}</option>`).join('')}</select></label>
              <label>${t('at')} <input type="time" name="time" value="${esc(fields.time)}"></label>
            </div>
            <label>${t('Where')} <input name="location" value="${esc(fields.location)}" placeholder="${t('Room, or a video link')}"></label>
            <div class="cal-section-h">${t('Presenting order')}</div>
            <ol class="cal-meeting-order">${members.map((u, i) => `<li><span>${esc(names[u] || u)}</span>
              <button type="button" class="cal-icon-btn" data-up="${i}" aria-label="${t('Earlier')}">${icon('chevron-up')}</button>
              <button type="button" class="cal-icon-btn" data-down="${i}" aria-label="${t('Later')}">${icon('chevron-down')}</button>
              <button type="button" class="cal-icon-btn" data-out="${i}" aria-label="${t('Remove')}">${icon('close')}</button></li>`).join('')}</ol>
            <div class="cal-meeting-row">
              <select data-add aria-label="${t('Add someone…')}"><option value="">${t('Add someone…')}</option>${data.people.filter((p) => members.indexOf(p.username) < 0).map((p) => `<option value="${esc(p.username)}">${esc(p.name)}</option>`).join('')}</select>
              <button type="button" class="cal-link-btn" data-all>${t('Add everyone')}</button>
            </div>
          </div>
          <footer class="record-dialog-foot">
            ${s && s.owner === me ? `<button type="button" class="btn btn-ghost" style="color: var(--color-danger-700)" data-delete>${t('Delete')}</button>` : ''}
            <span style="flex: 1"></span>
            <button type="button" class="btn" data-cancel>${t('Cancel')}</button>
            <button type="submit" class="btn btn-primary">${t('Save')}</button>
          </footer>
        </form>`;
      const f = $('form', dialog);
      $('[data-add]', f).addEventListener('change', (e) => { if (e.target.value) { members.push(e.target.value); draw(); } });
      f.onclick = (e) => {
        const b = e.target.closest('button');
        if (!b) return;
        let i;
        if (b.dataset.up !== undefined) { i = Number(b.dataset.up); if (i > 0) { members.splice(i - 1, 0, members.splice(i, 1)[0]); draw(); } }
        if (b.dataset.down !== undefined) { i = Number(b.dataset.down); if (i < members.length - 1) { members.splice(i + 1, 0, members.splice(i, 1)[0]); draw(); } }
        if (b.dataset.out !== undefined) { members.splice(Number(b.dataset.out), 1); draw(); }
        if (b.hasAttribute('data-all')) { data.people.forEach((p) => { if (!p.guest && members.indexOf(p.username) < 0) members.push(p.username); }); draw(); }
        if (b.hasAttribute('data-close')) dialog.close();
        if (b.hasAttribute('data-cancel')) { if (s) show(s.id); else dialog.close(); }
        if (b.hasAttribute('data-delete')) {
          window.BioDialog.confirm(t('Delete %(name)s? Its notes stay.', { name: s.name }), { danger: true }).then((ok) => {
            if (ok) api(`${API}/${s.id}/delete`, {}).then(() => { dialog.close(); load(); });
          });
        }
      };
      f.onsubmit = (e) => {
        e.preventDefault();
        const el = f.elements;
        const body = { name: el.name.value, weekday: el.weekday.value, time: el.time.value, location: el.location.value, members };
        api(s ? `${API}/${s.id}` : API, body).then((r) => load().then(() => show(r.series.id)))
          .catch((err) => toast(err.message, true));
      };
    };
    draw();
    if (!dialog.open) dialog.showModal();
    const name = $('input[name="name"]', dialog);
    if (name && !s) name.select();
  }

  window.BioMeetings = { create: () => (data.people.length ? form(null) : load().then(() => form(null))), show, load };
  const newBtn = $('#cal-meeting-new');
  if (newBtn) newBtn.addEventListener('click', () => window.BioMeetings.create());
  load().then(() => {
    // /calendar?meetings=new sets one up; ?meetings=<id> opens it (the notebook's Meetings sends people here).
    const asked = new URLSearchParams(window.location.search).get('meetings');
    if (asked === 'new' || (asked === 'all' && !data.series.length)) form(null);
    else if (asked === 'all' && data.series.length === 1) show(data.series[0].id);
    else if (asked === 'all' && list) {
      // Several: the list, brought into view and lit for a moment.
      const box = list.closest('.cal-section');
      box.scrollIntoView({ block: 'center' });
      box.classList.add('is-lit');
      setTimeout(() => box.classList.remove('is-lit'), 1600);
    } else if (asked && /^\d+$/.test(asked)) show(Number(asked));
  });
})();
