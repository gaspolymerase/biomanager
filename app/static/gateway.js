/* Gateway BP/LR in Assemble a plasmid (templates/plasmid_assembly.html).

   Pick two plasmids; the server works out the reaction and the product
   (app/gateway.py, /plasmids/assembly/gateway/preview). This draws the
   product's ring map and the one line that says what will be made, and
   Recombine posts to make it. */
(function () {
  'use strict';

  const root = document.querySelector('[data-gateway]');
  if (!root) return;
  const t = window.t || ((s) => s);
  const num = (n) => Number(n || 0).toLocaleString('en-US');
  const say = (m, kind) => { if (m && window.BiomanagerShell) window.BiomanagerShell.toast(m, kind); };
  const a = root.querySelector('[data-gw-a]');
  const b = root.querySelector('[data-gw-b]');
  const status = root.querySelector('[data-gw-status]');
  const sizeLine = root.querySelector('[data-gw-size]');
  const map = root.querySelector('[data-gw-map]');
  const outBox = root.querySelector('[data-gw-out]');
  const byproductLine = root.querySelector('[data-gw-byproduct]');
  const nameField = root.querySelector('[data-gw-name]');
  const makeButton = root.querySelector('[data-gw-make]');
  if (!a || !b) return;

  const svgns = 'http://www.w3.org/2000/svg';
  function arc(centre, radius, from, to) {
    const point = (f) => { const ang = (f * 2 - 0.5) * Math.PI; return [centre + radius * Math.cos(ang), centre + radius * Math.sin(ang)]; };
    const [x1, y1] = point(from);
    const [x2, y2] = point(Math.min(to, from + 0.9999));
    return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${radius} ${radius} 0 ${to - from > 0.5 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  }
  function drawMap(product) {
    map.textContent = '';
    const add = (tag, attrs, text) => { const n = document.createElementNS(svgns, tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); if (text != null) n.textContent = text; map.append(n); return n; };
    if (!product) return;
    const total = product.length;
    add('circle', { class: 'ring', cx: 130, cy: 130, r: 96 });
    (product.features || []).forEach((f) => {
      const from = f.start / total;
      const to = (f.end + 1) / total;
      add('path', { class: 'mark', d: arc(130, 106, from, Math.max(to, from + 0.004)), stroke: f.color || '#cbd5e1' });
    });
    add('text', { class: 'bp', x: 130, y: 127 }, t('%(n)s bp', { n: total }));
    add('text', { class: 'bp-sub', x: 130, y: 142 }, t('circular'));
  }

  let latest = 0;
  let ready = false;

  function preview() {
    const mine = ++latest;
    ready = false;
    makeButton.disabled = true;
    if (a.value === b.value) { status.textContent = t('Pick two different plasmids.'); drawMap(null); outBox.hidden = true; sizeLine.textContent = ''; return; }
    status.textContent = t('Working it out…');
    fetch(root.dataset.previewUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
      body: JSON.stringify({ a: a.value, b: b.value }) })
      .then((r) => r.json()).then((j) => {
        if (mine !== latest) return;
        if (!j.ok) { status.textContent = j.error || ''; drawMap(null); outBox.hidden = true; sizeLine.textContent = ''; return; }
        ready = true;
        makeButton.disabled = false;
        const kind = j.reaction === 'BP' ? t('entry clone') : t('expression clone');
        status.textContent = t('%(reaction)s makes the %(kind)s: %(insert)s on %(backbone)s’s backbone.',
          { reaction: j.reaction, kind, insert: j.insert, backbone: j.backbone });
        sizeLine.textContent = t('%(n)s bp', { n: num(j.product.length) });
        drawMap(j.product);
        outBox.hidden = false;
        const removed = (j.removed || []).length
          ? t('Removed: %(what)s.', { what: j.removed.join(', ') }) + ' '
          : '';
        byproductLine.textContent = removed + t('Byproduct: %(n)s bp with att%(t)s sites.', { n: num(j.byproduct.length), t: j.byproduct.kind });
        if (!nameField.value) {
          const base = (a.selectedOptions[0].textContent.split(' · ')[0] || '').replace(/^#\d+\s+/, '').trim();
          nameField.value = `${base}-${j.reaction === 'BP' ? 'entry' : 'expr'}`;
        }
      }).catch(() => { if (mine === latest) status.textContent = t('The server did not answer. Check the connection and try again.'); });
  }

  a.addEventListener('change', preview);
  b.addEventListener('change', preview);

  makeButton.addEventListener('click', () => {
    if (!ready) return;
    const body = new FormData();
    body.append('a', a.value);
    body.append('b', b.value);
    body.append('name', nameField.value);
    makeButton.disabled = true;
    fetch(root.dataset.makeUrl, { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } })
      .then((r) => r.json()).then((j) => {
        if (!j.ok) { makeButton.disabled = false; say(j.error, 'error'); return; }
        say(j.message);
        window.location.href = j.url;
      }).catch(() => { makeButton.disabled = false; say(t('The server did not answer. Check the connection and try again.'), 'error'); });
  });

  preview();
})();
