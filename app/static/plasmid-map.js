/* A plasmid drawn by the app itself (no editor library): the circular map
   and an agarose gel, as SVG strings. The Digest tab draws both with what
   /plasmids/<id>/digest.json says (app/digest_routes.py); the colours come
   from static/plasmid.css, so light and dark both follow.

   BioPlasmidMap.circular({ length, features, cuts, pieces, picked, labels,
     title, subtitle, size })
   BioPlasmidMap.gel({ ladder, lanes, height, width, labelLane }) */
(function () {
  'use strict';

  const TAU = Math.PI * 2;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => Number(n || 0).toLocaleString('en-US');

  // A feature's colour by what it is, so every map reads the same.
  const MARKER = /(^|[\s(])(amp|kan|neo|puro|hygro|blast|zeo|cm|spec|gent|bla|cat)[a-z]*r?\b|resist/i;
  function kind(f) {
    const type = String(f.type || '').toLowerCase();
    const name = String(f.name || '');
    if (type === 'promoter' || type === 'enhancer') return 'promoter';
    if (type.includes('polya') || type === 'terminator') return 'polya';
    if (type.includes('origin') || type === 'rep_origin' || /\bori\b/i.test(name)) return 'ori';
    if ((type === 'cds' || type === 'gene') && MARKER.test(name)) return 'marker';
    if (type === 'cds' || type === 'gene') return 'cds';
    return 'other';
  }

  const angle = (bp, length) => (bp / length) * TAU - Math.PI / 2;
  const point = (cx, cy, r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  function arcPath(cx, cy, r, a0, a1) {
    const [x0, y0] = point(cx, cy, r, a0);
    const [x1, y1] = point(cx, cy, r, a1);
    const large = (a1 - a0) % TAU > Math.PI ? 1 : 0;
    return `M${x0.toFixed(1)},${y0.toFixed(1)} A${r},${r} 0 ${large} 1 ${x1.toFixed(1)},${y1.toFixed(1)}`;
  }
  // A feature as a thick arc with an arrowhead at its 3′ end.
  function featureArc(cx, cy, r, w, f, length) {
    const start = Number(f.start) || 0;
    let end = Number(f.end) || 0;
    if (end <= start) end += length;
    let a0 = angle(start, length);
    let a1 = angle(end, length);
    const head = Math.min(0.12, (a1 - a0) * 0.45);
    const forward = Number(f.direction ?? f.strand ?? 1) >= 0;
    const ri = r - w / 2;
    const ro = r + w / 2;
    const p = (rr, a) => point(cx, cy, rr, a).map((v) => v.toFixed(1)).join(',');
    const large = (x, y) => ((y - x) > Math.PI ? 1 : 0);
    if (forward) {
      const b = a1 - head;
      return `M${p(ro, a0)} A${ro},${ro} 0 ${large(a0, b)} 1 ${p(ro, b)} L${p(r, a1)} L${p(ri, b)} A${ri},${ri} 0 ${large(a0, b)} 0 ${p(ri, a0)} Z`;
    }
    const b = a0 + head;
    return `M${p(r, a0)} L${p(ro, b)} A${ro},${ro} 0 ${large(b, a1)} 1 ${p(ro, a1)} L${p(ri, a1)} A${ri},${ri} 0 ${large(b, a1)} 0 ${p(ri, b)} Z`;
  }

  // Labels round the outside, nudged apart so none sit on another.
  function spread(items, cx, cy, r, minGap) {
    const right = items.filter((i) => Math.cos(i.a) >= 0).sort((p, q) => Math.sin(p.a) - Math.sin(q.a));
    const left = items.filter((i) => Math.cos(i.a) < 0).sort((p, q) => Math.sin(p.a) - Math.sin(q.a));
    [right, left].forEach((side) => {
      let last = -Infinity;
      side.forEach((i) => {
        i.y = Math.max(cy + r * Math.sin(i.a), last + minGap);
        last = i.y;
      });
    });
    return items;
  }

  function circular(o) {
    const length = Number(o.length) || 1;
    const size = o.size || 420;
    const w = size;
    const h = size;
    const cx = w / 2;
    const cy = h / 2;
    const r = size * 0.3;
    const out = [`<svg class="pmap" viewBox="0 0 ${w} ${h}" width="100%" style="max-width:${w}px" role="img" aria-label="${esc(o.title || '')}">`];
    out.push(`<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" class="pmap-backbone"/>`);
    // Ticks inside every 1 kb (or 500 bp on a small plasmid).
    const step = length > 12000 ? 2000 : length > 3000 ? 1000 : 500;
    for (let bp = step; bp < length; bp += step) {
      const a = angle(bp, length);
      const [x0, y0] = point(cx, cy, r - 9, a);
      const [x1, y1] = point(cx, cy, r - 4, a);
      const [tx, ty] = point(cx, cy, r - 22, a);
      out.push(`<line x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}" class="pmap-tick"/>`);
      out.push(`<text x="${tx.toFixed(1)}" y="${(ty + 3.5).toFixed(1)}" text-anchor="middle" class="pmap-tick-text">${bp >= 1000 ? `${bp / 1000}k` : bp}</text>`);
    }
    // The pieces a digest leaves, outside the ring, the picked one strongest.
    (o.pieces || []).forEach((piece, i) => {
      if ((o.pieces || []).length < 2) return;
      const s = piece.start - 1;
      let e = piece.start - 1 + piece.length;
      const a0 = angle(s, length);
      const a1 = angle(e, length);
      const picked = o.picked === i;
      out.push(`<path d="${arcPath(cx, cy, r + 26, a0 + 0.012, a1 - 0.012)}" class="pmap-piece pmap-piece-${i % 4}${picked ? ' is-picked' : ''}" data-piece="${i}"/>`);
      const mid = (a0 + a1) / 2;
      const [lx, ly] = point(cx, cy, r + 44, mid);
      out.push(`<text x="${lx.toFixed(1)}" y="${(ly + 4).toFixed(1)}" text-anchor="${Math.cos(mid) > 0.25 ? 'start' : Math.cos(mid) < -0.25 ? 'end' : 'middle'}" class="pmap-piece-text pmap-piece-${i % 4}">${num(piece.length)} bp</text>`);
    });
    // Features.
    const feats = (o.features || []).filter((f) => Number(f.end) !== Number(f.start));
    feats.forEach((f) => {
      out.push(`<path d="${featureArc(cx, cy, r, 11, f, length)}" class="pmap-f pmap-f-${kind(f)}"><title>${esc(f.name)}</title></path>`);
    });
    // Feature labels, when asked.
    if (o.labels) {
      const items = feats.map((f) => {
        let end = Number(f.end);
        if (end <= Number(f.start)) end += length;
        return { text: f.name, a: angle((Number(f.start) + end) / 2, length), cls: kind(f) };
      });
      spread(items, cx, cy, r + 30, 14).forEach((i) => {
        const right = Math.cos(i.a) >= 0;
        const [ax, ay] = point(cx, cy, r + 8, i.a);
        const x = cx + (right ? 1 : -1) * (r + 34);
        out.push(`<path d="M${ax.toFixed(1)},${ay.toFixed(1)} L${x.toFixed(1)},${i.y.toFixed(1)}" class="pmap-leader"/>`);
        out.push(`<text x="${(x + (right ? 4 : -4)).toFixed(1)}" y="${(i.y + 4).toFixed(1)}" text-anchor="${right ? 'start' : 'end'}" class="pmap-label">${esc(i.text)}</text>`);
      });
    }
    // Cuts: a line across the ring and the enzyme's name outside.
    const cuts = (o.cuts || []).map((c) => ({ ...c, a: angle(c.at, length) }));
    cuts.forEach((c) => {
      const [x0, y0] = point(cx, cy, r - 14, c.a);
      const [x1, y1] = point(cx, cy, r + 16, c.a);
      out.push(`<line x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}" class="pmap-cut"/>`);
    });
    spread(cuts.map((c) => ({ ...c })), cx, cy, r + 70, 15).forEach((c) => {
      const right = Math.cos(c.a) >= 0;
      const x = cx + (right ? 1 : -1) * (r + 74) * Math.max(0.55, Math.abs(Math.cos(c.a)));
      out.push(`<text x="${x.toFixed(1)}" y="${(c.y + 4).toFixed(1)}" text-anchor="${right ? 'start' : 'end'}" class="pmap-cut-text"><tspan class="pmap-cut-name">${esc(c.enzyme)}</tspan> ${num(c.at)}</text>`);
    });
    if (o.title) out.push(`<text x="${cx}" y="${cy - 2}" text-anchor="middle" class="pmap-title">${esc(o.title)}</text>`);
    out.push(`<text x="${cx}" y="${cy + 18}" text-anchor="middle" class="pmap-sub">${esc(o.subtitle || `${num(length)} bp`)}</text>`);
    out.push('</svg>');
    return out.join('');
  }

  // ---------------------------------------------------------------- the gel

  function gel(o) {
    const lanes = o.lanes || [];
    const width = (o.width || Math.max(260, 70 + (lanes.length + 1) * 62)) + (lanes.some((l) => l.current) ? 40 : 0);
    const height = o.height || 520;
    const top = 70;
    const bottom = height - 26;
    const y = (f) => top + f * (bottom - top);
    const lw = Math.min(52, Math.floor((width - 70) / (lanes.length + 1)) - 12);
    const gap = 12;
    const x0 = 58;
    const out = [`<svg class="gel" viewBox="0 0 ${width} ${height}" width="100%" style="max-width:${width}px" role="img" aria-label="${esc(o.label || '')}">`,
      '<defs><filter id="gel-glow" x="-50%" y="-200%" width="200%" height="500%"><feGaussianBlur stdDeviation="2.2"/></filter></defs>',
      `<rect x="0" y="0" width="${width}" height="${height}" rx="18" class="gel-bg"/>`];
    const all = [{ label: o.ladder ? o.ladder.name : '', ladder: true, bands: o.ladder ? o.ladder.bands : [] }, ...lanes];
    // The lane being looked at has its sizes written beside it, so the one after it moves over.
    const SIZES = 46;
    let x = x0 - (lw + gap);
    all.forEach((lane, i) => {
      x += lw + gap + (i > 0 && all[i - 1].current ? SIZES : 0);
      out.push(`<rect x="${x - 4}" y="${top - 22}" width="${lw + 8}" height="${bottom - top + 34}" rx="8" class="gel-lane${lane.current ? ' is-current' : ''}"/>`);
      out.push(`<rect x="${x + 4}" y="${top - 16}" width="${lw - 8}" height="7" rx="2" class="gel-well"/>`);
      // A long name goes on two lines: "EcoRI +" over "NotI".
      const label = String(lane.label || '');
      const plus = label.indexOf(' + ');
      const space = label.lastIndexOf(' ', 11);
      const words = label.length > 9 && plus > 0 ? [`${label.slice(0, plus)} +`, label.slice(plus + 3)]
        : label.length > 11 && space > 0 ? [label.slice(0, space), label.slice(space + 1, space + 12)]
          : [label.slice(0, 12)];
      words.slice(0, 2).forEach((wd, k) => out.push(`<text x="${x + lw / 2}" y="${top - 40 + k * 12 - (words.length > 1 ? 6 : 0)}" text-anchor="middle" class="gel-text">${esc(wd)}</text>`));
      (lane.bands || []).forEach((b) => {
        const yy = y(b.y);
        const bright = lane.ladder ? (b.bright ? 1 : 0.55) : Math.max(0.3, Math.min(1, 0.35 + 0.65 * (b.mass ?? 1)));
        const hgt = lane.ladder ? (b.bright ? 3.2 : 2.4) : (b.sizes && b.sizes.length > 1 ? 4.4 : 3.4);
        out.push(`<rect x="${x + 3}" y="${(yy - hgt / 2).toFixed(1)}" width="${lw - 6}" height="${hgt}" rx="1.5" class="gel-glow" opacity="${bright.toFixed(2)}" filter="url(#gel-glow)"/>`);
        out.push(`<rect x="${x + 4}" y="${(yy - hgt / 2).toFixed(1)}" width="${lw - 8}" height="${hgt}" rx="1.5" class="gel-band" opacity="${bright.toFixed(2)}"><title>${esc((b.sizes || [b.size]).map(num).join(' + '))} bp${b.form ? ` · ${esc(b.form)}` : ''}</title></rect>`);
        if (lane.current && !lane.ladder && b.sizes) {
          out.push(`<text x="${x + lw + 5}" y="${(yy + 4).toFixed(1)}" class="gel-size">${b.sizes.map(num).join(' + ')}</text>`);
        }
      });
    });
    // Ladder sizes at the left.
    if (o.ladder) {
      let last = -Infinity;
      o.ladder.bands.forEach((b) => {
        const yy = y(b.y);
        if (yy - last < 13) return;
        last = yy;
        const s = b.size;
        const text = s >= 1000 ? `${(Math.round(s / 100) / 10).toString().replace(/\.0$/, '')}k` : String(s);
        out.push(`<text x="${x0 - 10}" y="${(yy + 4).toFixed(1)}" text-anchor="end" class="gel-text">${text}</text>`);
      });
    }
    out.push('</svg>');
    return out.join('');
  }

  window.BioPlasmidMap = { circular, gel, kind };
})();
