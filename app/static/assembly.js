/* The assembly wizard's tray (templates/plasmid_assembly.html).

   None of the biology is here: the tray is a list of {plasmid, kind, …}
   entries, and after every change the server works out the junctions, the
   product and the one sentence that says what is wrong (app/cloning.py,
   /plasmids/assembly/preview). This draws the tray, the junctions between
   its rows and a ring map of the product, and keeps the hidden field the
   Create form posts in step with it. */
(function () {
  'use strict';

  const form = document.getElementById('assembly');
  if (!form) return;

  const t = window.t || ((s) => s);
  const method = form.dataset.method;
  const max = Number(form.dataset.max || 20);
  const list = form.querySelector('[data-list]');
  const trayField = form.querySelector('[data-tray]');
  const statusLine = form.querySelector('[data-status]');
  const createButton = form.querySelector('[data-create]');
  const sizeLine = form.querySelector('[data-size]');
  const primerLine = form.querySelector('[data-primers]');
  const map = form.querySelector('[data-map]');
  const picker = document.getElementById('assembly-picker');

  /** The tray: one entry a fragment, in the order they go together. */
  let tray = [];
  let lastPreview = null;
  let pending = 0;

  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const escape = (text) => String(text == null ? '' : text).replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---------------------------------------------------------------- drawing

  function fragmentRow(entry, i, shown) {
    const row = document.createElement('li');
    row.className = 'assembly-row';
    row.dataset.at = String(i);
    row.dataset.flipped = entry.flip ? '1' : '0';
    const name = shown ? shown.name : entry.label || '…';
    const bits = [];
    if (shown) {
      bits.push(t('%(n)s bp', { n: shown.length }));
      if (shown.features) bits.push(t('%(n)s features', { n: shown.features }));
      if (shown.left || shown.right) bits.push(`${shown.left || t('blunt')} … ${shown.right || t('blunt')}`);
      if (shown.role) bits.push(shown.role);
    }
    row.innerHTML =
      `<span class="assembly-num">${i + 1}</span>` +
      `<span><span class="assembly-name">${escape(name)}</span>` +
      `<span class="assembly-meta ident">${escape(bits.join(' · '))}</span></span>` +
      `<span class="assembly-acts">` +
      `<button type="button" data-move="-1" title="${escape(t('Move up'))}" aria-label="${escape(t('Move up'))}">${icon('chevron-up')}</button>` +
      `<button type="button" data-move="1" title="${escape(t('Move down'))}" aria-label="${escape(t('Move down'))}">${icon('chevron-down')}</button>` +
      `<button type="button" data-flip title="${escape(t('Turn it round'))}" aria-label="${escape(t('Turn it round'))}">${icon('repeat')}</button>` +
      `<button type="button" data-drop title="${escape(t('Take it out'))}" aria-label="${escape(t('Take it out'))}">${icon('close')}</button>` +
      `</span>`;
    return row;
  }

  /* The bases are the junction; what kind of end it is reads off them, so
     the row is the icon and the bases, and the words are the tooltip. A
     junction with no bases to show yet (a Gibson's, before its primers are
     designed) has nothing else to say, so there the words stay. */
  function junctionRow(junction) {
    const row = document.createElement('li');
    row.className = 'assembly-join';
    row.dataset.ok = junction.ok ? '1' : '0';
    row.title = junction.words;
    const tm = junction.tm ? `<span class="words">${escape(junction.tm)} °C</span>` : '';
    const says = junction.bases
      ? `<span class="bases">${escape(junction.bases)}</span>${tm}`
      : `<span class="words">${escape(junction.words)}</span>`;
    row.innerHTML = `${icon(junction.ok ? 'link' : 'warning')}${says}`;
    return row;
  }

  function draw() {
    const rows = (lastPreview && lastPreview.fragments) || [];
    const joins = (lastPreview && lastPreview.junctions) || [];
    list.textContent = '';
    if (!tray.length) {
      const empty = document.createElement('li');
      empty.className = 'assembly-empty';
      empty.textContent = t('Nothing here yet. Add the backbone first, then what goes into it.');
      list.append(empty);
      return;
    }
    tray.forEach((entry, i) => {
      list.append(fragmentRow(entry, i, rows[i]));
      const after = joins.find((j) => j.at === i + 1);
      if (after) list.append(junctionRow(after));
    });
  }

  /* The product as a ring: one arc a fragment in its own colour, the
     features as ticks outside it, the length in the middle. A linear
     product is drawn as the same ring with a gap at the top. */
  const COLOURS = ['#60a5fa', '#34d399', '#fbbf24', '#f472b6', '#a78bfa', '#fb923c', '#22d3ee', '#c084fc'];

  /* An arc of the ring, `from` and `to` as fractions of the way round,
     clockwise from twelve o'clock. */
  function arc(centre, radius, from, to) {
    const point = (f) => {
      const angle = (f * 2 - 0.5) * Math.PI;
      return [centre + radius * Math.cos(angle), centre + radius * Math.sin(angle)];
    };
    const [x1, y1] = point(from);
    const [x2, y2] = point(Math.min(to, from + 0.9999));
    return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${radius} ${radius} 0 ${to - from > 0.5 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  }

  /* The whole way round is a circle, not an arc: an arc whose two ends are
     the same point draws nothing at all, which left the ring invisible. */
  function ring(add, centre, radius, circular) {
    if (circular) return add('circle', { class: 'ring', cx: centre, cy: centre, r: radius });
    return add('path', { class: 'ring', d: arc(centre, radius, 0.012, 0.988) });
  }

  const svgns = 'http://www.w3.org/2000/svg';

  function drawing(into) {
    into.textContent = '';
    return (tag, attrs, text) => {
      const node = document.createElementNS(svgns, tag);
      Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
      if (text != null) node.textContent = text;
      into.append(node);
      return node;
    };
  }

  function drawMap() {
    const preview = lastPreview;
    if (!preview || !preview.length) { map.textContent = ''; return; }
    const total = preview.length;
    const circular = preview.circular;
    const add = drawing(map);
    const gap = circular ? 0 : 0.012;
    ring(add, 130, 96, circular);
    (preview.parts || []).forEach((part, i) => {
      const from = part.start / total;
      const to = (part.end + 1) / total;
      add('path', { class: 'part', d: arc(130, 96, from + gap, Math.max(to - gap, from + gap + 0.002)),
                    stroke: COLOURS[i % COLOURS.length] });
    });
    (preview.features || []).forEach((feature) => {
      const from = feature.start / total;
      const to = (feature.end + 1) / total;
      add('path', { class: 'mark', d: arc(130, 106, from, Math.max(to, from + 0.003)) });
    });
    add('text', { class: 'bp', x: 130, y: 127 }, t('%(n)s bp', { n: total }));
    add('text', { class: 'bp-sub', x: 130, y: 142 },
        circular ? t('circular') : t('linear'));
  }

  // ---------------------------------------------------------------- the server

  function payload() {
    return {
      method: method,
      circular: form.elements.circular ? form.elements.circular.checked : true,
      design: form.elements.design ? form.elements.design.checked : true,
      enzyme: form.elements.enzyme ? form.elements.enzyme.value : '',
      fragments: tray,
    };
  }

  async function refresh() {
    trayField.value = JSON.stringify(tray);
    if (!tray.length) {
      lastPreview = null;
      draw();
      drawMap();
      say(t('Add fragments to start.'), null);
      return;
    }
    const mine = ++pending;
    try {
      const response = await fetch(form.dataset.previewUrl, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
      });
      const answer = await response.json();
      if (mine !== pending) return;                 // a later change already asked
      lastPreview = answer.ok || answer.fragments ? answer : null;
      draw();
      drawMap();
      say(answer.status, answer.ok);
      sizeLine.textContent = answer.length ? t('%(n)s bp', { n: answer.length }) : '';
      const primers = (answer.primers || []).length;
      primerLine.textContent = primers ? t('%(n)s primers to design and order', { n: primers }) : '';
    } catch (e) {
      if (mine !== pending) return;
      say(t('Could not work the assembly out. Try again.'), false);
    }
  }

  function say(text, ok) {
    statusLine.textContent = text || '';
    if (ok == null) statusLine.removeAttribute('data-ok');
    else statusLine.dataset.ok = ok ? '1' : '0';
    createButton.disabled = !ok;
  }

  // ---------------------------------------------------------------- the tray

  list.addEventListener('click', (event) => {
    const row = event.target.closest('.assembly-row');
    if (!row) return;
    const at = Number(row.dataset.at);
    const move = event.target.closest('[data-move]');
    if (move) {
      const to = at + Number(move.dataset.move);
      if (to < 0 || to >= tray.length) return;
      [tray[at], tray[to]] = [tray[to], tray[at]];
    } else if (event.target.closest('[data-flip]')) {
      tray[at].flip = !tray[at].flip;
    } else if (event.target.closest('[data-drop]')) {
      tray.splice(at, 1);
    } else {
      return;
    }
    refresh();
  });

  form.querySelectorAll('[data-live]').forEach((control) => control.addEventListener('change', refresh));

  // ---------------------------------------------------------------- the picker

  const pick = {
    plasmid: picker && picker.querySelector('[data-pick-plasmid]'),
    feature: picker && picker.querySelector('[data-pick-feature]'),
    piece: picker && picker.querySelector('[data-pick-piece]'),
    start: picker && picker.querySelector('[data-pick-start]'),
    end: picker && picker.querySelector('[data-pick-end]'),
    span: picker && picker.querySelector('[data-pick-span]'),
    role: picker && picker.querySelector('[data-pick-role]'),
    problem: picker && picker.querySelector('[data-pick-problem]'),
    enzymes: picker && picker.querySelector('[data-pick-enzymes]'),
    cutters: picker && picker.querySelector('[data-pick-cutters]'),
    pieceMap: picker && picker.querySelector('[data-pick-map]'),
  };
  let kind = 'whole';
  let source = null;
  let asked = 0;          // ticking a second enzyme must not be overtaken by the first answer

  function panels() {
    if (!picker) return;
    picker.querySelectorAll('[data-pick-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.pickPanel !== kind;
    });
    picker.querySelectorAll('[data-pick-kind]').forEach((chip) => {
      chip.setAttribute('aria-pressed', chip.dataset.pickKind === kind ? 'true' : 'false');
      chip.classList.toggle('is-active', chip.dataset.pickKind === kind);
    });
  }

  function chosenEnzymes() {
    return Array.from(pick.enzymes ? pick.enzymes.querySelectorAll('input:checked') : []).map((box) => box.value);
  }

  /* Only the enzymes that cut the plasmid in hand, fewest sites first: a
     single cutter is usually the one you want, and forty that mostly do not
     cut is a list to read rather than a choice to make. */
  function showCutters(cutters) {
    const ticked = new Set(chosenEnzymes());
    pick.enzymes.textContent = '';
    cutters.forEach((enzyme) => {
      const label = document.createElement('label');
      label.className = 'assembly-enzyme';
      label.title = t('cuts at %(at)s', { at: enzyme.at.join(', ') });
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.value = enzyme.name;
      box.checked = ticked.has(enzyme.name);
      const site = document.createElement('span');
      site.textContent = enzyme.label;
      const where = document.createElement('i');
      where.textContent = enzyme.where;
      label.append(box, site, where);
      pick.enzymes.append(label);
    });
    pick.cutters.textContent = cutters.length
      ? t('%(n)s of them cut this plasmid', { n: cutters.length })
      : t('no enzyme in the list cuts this plasmid');
  }

  /* Where the chosen piece sits on the plasmid it came off: the whole ring
     faint, the piece itself drawn over it, and a tick at each cut. */
  function drawPiece() {
    if (!pick.pieceMap) return;
    const piece = source && (source.pieces || []).find((x) => String(x.piece) === pick.piece.value);
    if (!piece || !source.length) { pick.pieceMap.textContent = ''; return; }
    const total = source.length;
    const add = drawing(pick.pieceMap);
    ring(add, 75, 54, source.circular);
    const from = (piece.start - 1) / total;
    // A 51 bp piece of a 2.7 kb plasmid is half a degree: draw it long
    // enough to see, or the preview shows nothing where the piece is.
    const sweep = Math.max(piece.length / total, 0.02);
    add('path', { class: 'piece', d: arc(75, 54, from, from + sweep) });
    (source.pieces || []).forEach((other) => {
      const at = (other.start - 1) / total;
      add('path', { class: 'cut', d: arc(75, 64, at - 0.004, at + 0.004) });
    });
    add('text', { class: 'bp', x: 75, y: 73 }, t('%(n)s bp', { n: piece.length }));
    add('text', { class: 'bp-sub', x: 75, y: 86 }, t('of %(n)s bp', { n: total }));
  }

  function problem(text) {
    pick.problem.textContent = text || '';
    pick.problem.hidden = !text;
  }

  async function loadSource() {
    if (!pick.plasmid || !pick.plasmid.value) return;
    const url = form.dataset.sourceUrl.replace(/0$/, pick.plasmid.value)
      + (kind === 'digest' ? `?enzymes=${encodeURIComponent(chosenEnzymes().join(','))}` : '');
    problem('');
    const mine = ++asked;
    const response = await fetch(url);
    const answer = await response.json();
    if (mine !== asked) return;
    if (!answer.ok) { problem(answer.error || ''); return; }
    source = answer;
    showCutters(answer.cutters || []);
    pick.feature.textContent = '';
    answer.features.forEach((feature) => {
      const option = document.createElement('option');
      option.value = String(feature.index);
      option.textContent = `${feature.name} · ${feature.length} bp · ${feature.start + 1}–${feature.end + 1}`;
      pick.feature.append(option);
    });
    if (!answer.features.length) {
      const option = document.createElement('option');
      option.disabled = true;
      option.textContent = t('This map has no named features yet.');
      pick.feature.append(option);
    }
    pick.piece.textContent = '';
    (answer.pieces || []).forEach((piece) => {
      const option = document.createElement('option');
      option.value = String(piece.piece);
      const ends = `${piece.left || t('blunt')} … ${piece.right || t('blunt')}`;
      option.textContent = `${piece.length} bp · ${piece.start}–${piece.end} · ${ends}`
        + (piece.features.length ? ` · ${piece.features.join(', ')}` : '');
      pick.piece.append(option);
    });
    if (answer.uncut) problem(t('Those enzymes do not cut this plasmid.'));
    if (pick.piece.options.length) pick.piece.selectedIndex = 0;
    drawPiece();
    pick.start.max = pick.end.max = String(answer.length);
    if (Number(pick.end.value) <= 1) pick.end.value = String(answer.length);
    span();
  }

  function span() {
    if (!source) return;
    const from = Number(pick.start.value);
    const to = Number(pick.end.value);
    const size = to >= from ? to - from + 1 : (source.circular ? source.length - from + 1 + to : 0);
    pick.span.textContent = size > 0 ? t('%(n)s bp', { n: size }) : t('backwards');
  }

  if (picker) {
    picker.addEventListener('click', (event) => {
      const chip = event.target.closest('[data-pick-kind]');
      if (chip) { kind = chip.dataset.pickKind; panels(); loadSource(); return; }
      if (event.target.closest('[data-pick-piece]')) drawPiece();
      if (event.target.closest('[data-pick-ok]')) { addFromPicker(); }
    });
    picker.addEventListener('change', (event) => {
      if (event.target.closest('[data-pick-plasmid]') || event.target.closest('[data-pick-enzymes]')) loadSource();
      if (event.target.closest('[data-pick-start]') || event.target.closest('[data-pick-end]')) span();
      if (event.target.closest('[data-pick-piece]')) drawPiece();
    });
  }

  function addFromPicker() {
    if (tray.length >= max) { problem(t('That is as many fragments as one assembly takes.')); return; }
    const entry = { plasmid: Number(pick.plasmid.value), kind: kind };
    if (kind === 'feature') {
      if (!pick.feature.value) { problem(t('Choose a feature.')); return; }
      entry.feature = Number(pick.feature.value);
    } else if (kind === 'region') {
      entry.start = Number(pick.start.value);
      entry.end = Number(pick.end.value);
    } else if (kind === 'digest') {
      entry.enzymes = chosenEnzymes();
      if (!entry.enzymes.length) { problem(t('Choose an enzyme to cut with.')); return; }
      if (!pick.piece.value) { problem(t('Choose which piece to take.')); return; }
      entry.piece = Number(pick.piece.value);
    }
    if (pick.role.value) entry.role = pick.role.value;
    tray.push(entry);
    picker.close();
    refresh();
  }

  const addButton = form.querySelector('[data-add]');
  if (addButton && picker) {
    addButton.addEventListener('click', () => {
      problem('');
      panels();
      picker.showModal();
      loadSource();
    });
  }

  // The plasmid the wizard was opened from goes in first.
  if (form.dataset.start) {
    tray.push({ plasmid: Number(form.dataset.start), kind: 'whole' });
  }
  draw();
  refresh();
}());
