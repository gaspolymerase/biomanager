/* The plasmid page's Overview tab (templates/plasmid_detail.html): the
   map, drawn by the app (static/plasmid-map.js) from the features on the
   page, ready to save as SVG or PNG for a lab meeting or a paper; what the
   plasmid is; and its features, each opening the Sequence tab on it. */
(function () {
  'use strict';

  const root = document.querySelector('[data-overview]');
  if (!root) return;
  const features = JSON.parse(root.dataset.features || '[]');
  const length = Number(root.dataset.length || 0);

  function draw() {
    const box = root.querySelector('[data-map]');
    if (!box || !length || !window.BioPlasmidMap) return;
    box.innerHTML = window.BioPlasmidMap.circular({
      length, features, labels: true, title: root.dataset.name, size: 520,
      subtitle: `${length.toLocaleString('en-US')} bp`,
    });
  }

  // Each feature's swatch in its map colour.
  root.querySelectorAll('[data-kind-of]').forEach((dot) => {
    const [type, name] = dot.dataset.kindOf.split('|');
    dot.classList.add(`pmap-f-${window.BioPlasmidMap ? window.BioPlasmidMap.kind({ type, name }) : 'other'}`);
  });

  // The map as a file: every colour and font written in, since the page's
  // stylesheet doesn't go with it.
  function standalone() {
    const svg = root.querySelector('[data-map] svg');
    const copy = svg.cloneNode(true);
    const from = svg.querySelectorAll('*');
    copy.querySelectorAll('*').forEach((el, i) => {
      const s = getComputedStyle(from[i]);
      ['fill', 'stroke', 'stroke-width', 'opacity', 'font-family', 'font-size', 'font-weight'].forEach((k) => {
        const v = s.getPropertyValue(k);
        if (v) el.style.setProperty(k, v);
      });
    });
    const box = svg.viewBox.baseVal;
    copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    copy.setAttribute('width', box.width);
    copy.setAttribute('height', box.height);
    copy.removeAttribute('style');
    return { text: new XMLSerializer().serializeToString(copy), width: box.width, height: box.height };
  }
  function save(blob, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  const fileName = (ext) => `${(root.dataset.name || 'plasmid').replace(/[^\w.-]+/g, '_')}-map.${ext}`;

  root.addEventListener('click', (e) => {
    const exp = e.target.closest('[data-export]');
    if (exp) {
      const { text, width, height } = standalone();
      if (exp.dataset.export === 'svg') { save(new Blob([text], { type: 'image/svg+xml' }), fileName('svg')); return; }
      const img = new Image();
      img.onload = () => {
        const scale = 3;                       // print-sharp
        const canvas = document.createElement('canvas');
        canvas.width = width * scale;
        canvas.height = height * scale;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => save(blob, fileName('png')), 'image/png');
      };
      img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}`;
      return;
    }
    const go = e.target.closest('[data-feature-go]');
    if (go) {
      const [a, b] = go.dataset.featureGo.split('-').map(Number);
      const tab = document.querySelector('.plasmid-tab[data-tab="sequence"]');
      if (tab) tab.click();
      // The viewer loads when its tab first opens: wait for it.
      let tries = 0;
      const pick = () => {
        const ready = document.querySelector('[data-seq-viewer] .sv2-row');
        if (ready && window.BioSeqViewer) window.BioSeqViewer.select(a, b);
        else if (tries++ < 40) setTimeout(pick, 100);
      };
      pick();
    }
  });
  // Buttons that open another tab (Edit → Properties, Sequencing → Verify).
  document.addEventListener('click', (e) => {
    const go = e.target.closest('[data-go-tab]');
    if (!go) return;
    const tab = document.querySelector(`.plasmid-tab[data-tab="${go.dataset.goTab}"]`);
    if (tab) tab.click();
  });

  if (document.readyState === 'complete') draw();
  else window.addEventListener('load', draw);

  // ---------------------------------------------------------------- Compare with…
  (function () {
    const box = root.querySelector('[data-compare]');
    if (!box) return;
    const t = window.t || ((s) => s);
    const num = (n) => Number(n || 0).toLocaleString('en-US');
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const pick = box.querySelector('[data-compare-pick]');
    const out = box.querySelector('[data-compare-out]');
    let loaded = false;

    function miniMap(len, feats, diffs) {
      if (!window.BioPlasmidMap) return '';
      return window.BioPlasmidMap.circular({ length: len, features: feats, mini: true, size: 150,
        picked: (diffs || []).map((d) => ({ start: d, end: d })) });
    }

    pick.addEventListener('focus', () => {
      if (loaded) return;
      loaded = true;
      fetch(box.dataset.others, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json()).then((j) => {
        if (!j.ok) return;
        j.plasmids.forEach((p) => {
          const o = document.createElement('option');
          o.value = p.number;
          o.textContent = `${p.label} · ${num(p.length)} bp`;
          pick.append(o);
        });
      });
    }, { once: false });

    pick.addEventListener('change', () => {
      if (!pick.value) { out.innerHTML = ''; return; }
      out.innerHTML = `<p class="pov-note">${esc(t('Comparing…'))}</p>`;
      const url = new URL(box.dataset.compareUrl, window.location.href);
      url.searchParams.set('other', pick.value);
      fetch(url, { headers: { 'X-Requested-With': 'fetch' } }).then((r) => r.json()).then((j) => {
        if (!j.ok) { out.innerHTML = `<p class="pov-note">${esc(j.error || '')}</p>`; return; }
        if (!j.matched) {
          out.innerHTML = `<p class="pov-note">${esc(t('These two don’t line up — they look like different plasmids.'))}</p>`;
          return;
        }
        const big = j.a_outside > 200;
        const lead = j.count === 1 ? t('1 difference from %(name)s', { name: j.b.name })
          : j.count ? t('%(n)s differences from %(name)s', { n: j.count, name: j.b.name })
            : big ? t('No base differs across the part that lines up with %(name)s', { name: j.b.name })
              : t('No base differs from %(name)s', { name: j.b.name });
        const pct = j.count && j.identity >= 100 ? '99.9' : j.identity;
        const head = j.same
          ? `<p class="pov-compare-same">${esc(t('Identical: the same sequence, %(n)s bp.', { n: num(j.a.length) }))}</p>`
          : `<p class="pov-compare-head">${esc(lead)}
             <span class="text-muted">${esc(t('%(pct)s% identical', { pct }))}${j.strand === 'reverse' ? ' · ' + esc(t('saved on the other strand')) : ''}</span></p>`;
        const kinds = { silent: 'badge-ok', missense: 'badge-warn', in_frame: 'badge-warn', frameshift: 'badge-danger',
          nonsense: 'badge-danger', start_lost: 'badge-danger', stop_lost: 'badge-danger' };
        const rows = j.differences.map((d) => `<li>
            <span class="tabular-nums pov-compare-at">${num(d.at_b)}</span>
            <span class="pov-compare-what">${esc(d.text)}</span>
            ${d.effect ? `<span class="badge ${kinds[d.effect_kind] || 'badge-quiet'}">${esc(d.effect)}</span>` : ''}
          </li>`).join('');
        const outside = j.a_outside > 200
          ? `<p class="pov-note">${esc(t('%(n)s bp of this plasmid did not line up (an insert, or a large change).', { n: num(j.a_outside) }))}</p>`
          : '';
        out.innerHTML = `${head}
          <div class="pov-compare-maps">
            <figure>${miniMap(j.a.length, j.features_a, j.differences.map((d) => d.at_a).filter(Boolean))}<figcaption>${esc(j.a.name)}</figcaption></figure>
            <figure>${miniMap(j.b.length, j.features_b, j.differences.map((d) => d.at_b))}<figcaption><a href="${esc(j.b.url || '#')}">${esc(j.b.name)}</a></figcaption></figure>
          </div>
          ${rows ? `<ul class="pov-compare-list">${rows}</ul>` : ''}${outside}`;
      }).catch(() => { out.innerHTML = `<p class="pov-note">${esc(t('The server did not answer. Check the connection and try again.'))}</p>`; });
    });
  })();
})();
