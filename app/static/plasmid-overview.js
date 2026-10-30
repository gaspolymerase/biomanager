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
})();
