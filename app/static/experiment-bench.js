/* Bench mode (templates/experiment_bench.html): pick what you are doing
   now — the readout (weigh, count) or a manipulation due — then go through
   the animals one at a time, with big numbers and big buttons. A readout
   is saved animal by animal; a manipulation is recorded at the end, with
   the animals you ticked as given. */
(function () {
  'use strict';
  const el = document.getElementById('xb-data');
  if (!el) return;
  const D = JSON.parse(el.textContent || '{}');
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
  const nice = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString(window.BM_LANG === 'zh' ? 'zh-CN' : undefined, { day: 'numeric', month: 'short' }) : '');
  // Labels that arrive as values (the readout, the animals' noun), in the page's language.
  const RL = () => t(D.readout.label);
  const tc = (ctx, s) => { const k = `${ctx}::${s}`; const v = t(k); return v === k ? t(s) : v; };
  const verb = () => tc('readout', D.readout.verb || 'Record');
  const unitOf = () => (D.readout.unit ? t(D.readout.unit) : '');
  const nouns = () => t(D.nouns);
  // A day's title in the page's language, from its manipulation: what was given, or its kind.
  const titleOf = (r) => {
    const s = (D.steps || []).find((x) => x.id === r.step_id);
    if (!s) return t(r.title);
    if (s.reading) return RL();
    return [s.agent || t(s.kind_label), s.dose, s.route].filter(Boolean).join(' ');
  };
  const fraction = D.readout.kind === 'fraction';
  let task = null;      // {kind: 'reading'} | {kind: 'step', stepId, day, title, animals}
  let at = 0;
  const given = new Map();

  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) throw new Error(data.error || t('The app answered %(status)s.', { status: r.status }));
    return data;
  }

  // ---- scanning a cage, tank or housing card: jump to its animal. The iOS
  // app lends its scanner (LabWebView.swift, "bmScan"); a browser that can
  // read QR codes itself (BarcodeDetector) uses the camera here.
  const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bmScan;
  const canScan = Boolean(native) || ('BarcodeDetector' in window && navigator.mediaDevices);
  const scanButton = () => (canScan ? `<button type="button" class="btn xb-scan" data-xb-scan>${t('Scan a card')}</button>` : '');

  window.bmScanned = (value) => {
    const text = String(value || '').trim();
    const anchor = text.includes('#') ? text.split('#').pop() : '';
    const list = task ? task.animals : D.subjects;
    const keyOf = (a) => a.key || a.subject;
    const subject = D.subjects.find((x) => (anchor && x.card === anchor) || x.label === text || x.label.replace(/^#/, '') === text);
    const index = subject ? list.findIndex((a) => keyOf(a) === subject.key) : -1;
    if (index < 0) {
      if (window.BioDialog) BioDialog.alert(anchor ? t('No animal of this experiment is on that card.') : t("“%(text)s” isn't a card of this experiment.", { text }));
      return;
    }
    if (!task) { start('reading').then(() => { at = index; show(); }); return; }
    at = index;
    show();
  };

  async function scanHere() {
    const box = document.createElement('div');
    box.className = 'xb-camera';
    box.innerHTML = `<video playsinline muted></video><button type="button" class="btn">${t('Cancel')}</button>`;
    document.body.appendChild(box);
    const video = box.querySelector('video');
    let stream = null;
    let done = false;
    const stop = () => { done = true; if (stream) stream.getTracks().forEach((t) => t.stop()); box.remove(); };
    box.querySelector('button').addEventListener('click', stop);
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
      video.srcObject = stream;
      await video.play();
      const detector = new window.BarcodeDetector({ formats: ['qr_code'] });
      const look = async () => {
        if (done) return;
        const codes = await detector.detect(video).catch(() => []);
        if (codes.length) { stop(); window.bmScanned(codes[0].rawValue); return; }
        requestAnimationFrame(look);
      };
      look();
    } catch (_e) {
      stop();
      if (window.BioDialog) BioDialog.alert(t('The camera could not be opened here.'));
    }
  }

  function pick() {
    const due = D.schedule.filter((r) => !r.record && !r.reading && (r.state === 'today' || r.state === 'overdue'));
    const later = D.schedule.filter((r) => !r.record && !r.reading && r.state !== 'today' && r.state !== 'overdue');
    const button = (r) => `<button type="button" class="xb-choice" data-step="${r.step_id}:${r.day}"><b>${esc(titleOf(r))}</b>
      <span>${t('Day %(n)s', { n: r.day })}${r.date ? ` · ${esc(nice(r.date))}` : ''}${r.group ? ` · ${esc(r.group)}` : ''} · ${r.state === 'overdue' ? t('overdue') : r.state === 'today' ? t('due today') : t('to come')}</span></button>`;
    $('[data-xb-pick]').innerHTML = `
      <div class="xb-pick-head"><h2>${t('What are you doing now?')}</h2>${scanButton()}</div>
      ${D.editable ? `<button type="button" class="xb-choice xb-main" data-reading><b>${esc(verb())}: ${esc(RL())}</b><span>${t('Today, %(count)s %(nouns)s, one at a time', { count: D.subjects.length, nouns: esc(nouns()) })}</span></button>
      ${due.map(button).join('')}
      ${later.length ? `<details class="xb-later"><summary>${t('Planned for other days (%(n)s)', { n: later.length })}</summary>${later.map(button).join('')}</details>` : ''}`
        : `<p class="xp-empty">${t('This experiment is read only for you.')}</p>`}`;
  }

  async function start(which) {
    at = 0;
    given.clear();
    if (which === 'reading') {
      task = { kind: 'reading', animals: D.subjects };
    } else {
      const [stepId, day] = which.split(':');
      const r = await fetch(`/colony/experiments/${D.id}/steps/${stepId}/day/${day}?on=${today()}`);
      const data = await r.json();
      const row = D.schedule.find((x) => `${x.step_id}:${x.day}` === which);
      task = { kind: 'step', stepId, day, title: row ? titleOf(row) : '', animals: data.animals || [] };
      task.animals.forEach((a) => given.set(a.subject, true));
    }
    $('[data-xb-pick]').hidden = true;
    $('[data-xb-done]').hidden = true;
    $('[data-xb-run]').hidden = false;
    show();
  }

  function previous(key) {
    const row = D.table.rows.find((r) => r.key === key);
    if (!row) return null;
    for (let i = D.table.dates.length - 1; i >= 0; i -= 1) {
      if (row.values[i] != null) return { value: row.values[i], date: D.table.dates[i], today: D.table.dates[i] === today() };
    }
    return null;
  }

  function show() {
    const list = task.animals;
    if (at >= list.length) return finish();
    const a = list[at];
    const key = a.key || a.subject;
    $('[data-xb-count]').textContent = t('%(count)s of %(total)s', { count: at + 1, total: list.length });
    $('[data-xb-bar]').style.width = `${(at / list.length) * 100}%`;
    const head = `<div class="xb-who"><span class="xb-label">${esc(a.label)}</span><span>${esc([a.group, a.housing, a.sex].filter(Boolean).join(' · '))}</span></div>`;
    if (task.kind === 'reading') {
      const prev = previous(key);
      $('[data-xb-card]').innerHTML = `${head}
        <label class="xb-input"><span>${fraction && D.readout.unit ? esc(unitOf().charAt(0).toUpperCase() + unitOf().slice(1)) : esc(RL())}${D.readout.unit && !fraction ? ` (${esc(unitOf())})` : ''}</span>
          <input type="text" inputmode="decimal" autocomplete="off" data-xb-value value="${prev && prev.today ? prev.value : ''}">
          ${fraction && a.start != null ? `<em>${t('of %(n)s', { n: a.start })}</em>` : ''}</label>
        <p class="xb-prev">${prev && !prev.today ? t('Last: %(value)s on %(date)s', { value: `${prev.value}${D.readout.unit && !fraction ? ` ${esc(unitOf())}` : ''}`, date: esc(nice(prev.date)) }) : prev ? t('Already recorded today: change it or go on.') : t('No readout yet.')}</p>
        <p class="xp-error" data-xb-error hidden></p>
        <div class="xb-buttons"><button type="button" class="btn" data-xb-back ${at ? '' : 'disabled'}>${t('Back')}</button>
          <button type="button" class="btn" data-xb-skip>${t('Skip')}</button>
          <button type="button" class="btn btn-primary" data-xb-save>${t('Save and next')}</button></div>`;
      const input = $('[data-xb-value]');
      input.focus();
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveReading(); } });
    } else {
      const dose = [a.amount, a.volume].filter(Boolean).join(' · ');
      $('[data-xb-card]').innerHTML = `${head}
        <p class="xb-what">${esc(task.title || '')}</p>
        <p class="xb-dose">${a.needs ? `<span class="xp-warn">${t('needs a weight first')}</span>` : esc(dose || '—')}</p>
        <p class="xb-prev">${a.grams != null ? t('From %(grams)s g on %(date)s', { grams: a.grams, date: esc(nice(a.weighed_on)) }) : ''}</p>
        <div class="xb-buttons"><button type="button" class="btn" data-xb-back ${at ? '' : 'disabled'}>${t('Back')}</button>
          <button type="button" class="btn" data-xb-miss>${t('Not given')}</button>
          <button type="button" class="btn btn-primary" data-xb-give>${t('Given ✓')}</button></div>`;
    }
    $('[data-xb-jump]').innerHTML = scanButton() + list.map((x, i) => {
      const k = x.key || x.subject;
      const state = task.kind === 'step' ? (given.get(k) ? 'given' : 'missed') : (previous(k) && previous(k).today ? 'given' : '');
      return `<button type="button" class="xb-chip${i === at ? ' is-now' : ''}" data-xb-go="${i}" data-state="${state}">${esc(x.label)}</button>`;
    }).join('');
  }

  async function saveReading() {
    const a = task.animals[at];
    const input = $('[data-xb-value]');
    const value = input.value.trim();
    if (value) {
      try {
        const data = await post(`/experiments/${D.id}/readings`, { on: today(), values: { [a.key]: value } });
        D.table = data.table;
      } catch (error) {
        const box = $('[data-xb-error]');
        box.textContent = error.message;
        box.hidden = false;
        return;
      }
    }
    at += 1;
    show();
  }

  async function finish() {
    $('[data-xb-bar]').style.width = '100%';
    const done = $('[data-xb-done]');
    if (task.kind === 'reading') {
      $('[data-xb-run]').hidden = true;
      done.hidden = false;
      done.innerHTML = `<h2>${t('Done')}</h2><p>${t('%(readout)s saved for today.', { readout: esc(RL()) })}</p><div class="xb-buttons"><a class="btn" href="/experiments/${D.id}">${t('The experiment')}</a><button type="button" class="btn btn-primary" data-xb-again>${t('Something else')}</button></div>`;
      return;
    }
    const yes = task.animals.filter((a) => given.get(a.subject));
    $('[data-xb-run]').hidden = true;
    done.hidden = false;
    done.innerHTML = `<h2>${esc(task.title || t('Done'))}</h2><p>${t('%(count)s of %(total)s %(nouns)s given.', { count: yes.length, total: task.animals.length, nouns: esc(nouns()) })}</p>
      <label class="xb-note">${t('Note')} <textarea rows="3" data-xb-note placeholder="${esc(t('Anything that differed'))}"></textarea></label>
      <p class="xp-error" data-xb-error hidden></p>
      <div class="xb-buttons"><button type="button" class="btn" data-xb-review>${t('Back')}</button><button type="button" class="btn btn-primary" data-xb-record>${t('Record as done')}</button></div>`;
  }

  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('button');
    if (!btn) return;
    if (btn.matches('[data-xb-scan]')) { if (native) native.postMessage('scan'); else scanHere(); return; }
    if (btn.matches('[data-reading]')) start('reading');
    else if (btn.dataset.step) start(btn.dataset.step);
    else if (btn.matches('[data-xb-save]')) saveReading();
    else if (btn.matches('[data-xb-skip]')) { at += 1; show(); }
    else if (btn.matches('[data-xb-back]')) { at = Math.max(0, at - 1); show(); }
    else if (btn.matches('[data-xb-give], [data-xb-miss]')) { given.set(task.animals[at].subject, btn.matches('[data-xb-give]')); at += 1; show(); }
    else if (btn.dataset.xbGo) { at = Number(btn.dataset.xbGo); show(); }
    else if (btn.matches('[data-xb-review]')) { at = task.animals.length - 1; $('[data-xb-done]').hidden = true; $('[data-xb-run]').hidden = false; show(); }
    else if (btn.matches('[data-xb-again]')) { $('[data-xb-done]').hidden = true; $('[data-xb-pick]').hidden = false; pick(); }
    else if (btn.matches('[data-xb-record]')) {
      const subjects = task.animals.filter((a) => given.get(a.subject)).map((a) => a.subject);
      try {
        const data = await post(`/colony/experiments/${D.id}/steps/${task.stepId}/day/${task.day}/record`,
          { done_on: today(), subjects, note: ($('[data-xb-note]') || {}).value || '' });
        D.schedule = data.schedule;
        $('[data-xb-done]').innerHTML = `<h2>${t('Recorded')}</h2><p>${esc(task.title || '')}: ${subjects.length} ${esc(nouns())}.</p>${(data.problems || []).map((p) => `<p class="xp-warn">${esc(p)}</p>`).join('')}
          <div class="xb-buttons"><a class="btn" href="/experiments/${D.id}">${t('The experiment')}</a><button type="button" class="btn btn-primary" data-xb-again>${t('Something else')}</button></div>`;
      } catch (error) {
        const box = $('[data-xb-error]');
        box.textContent = error.message;
        box.hidden = false;
      }
    }
  });

  pick();
})();
