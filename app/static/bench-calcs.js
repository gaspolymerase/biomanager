/* Bench calculators for Utilities (templates/utilities.html,
   static/utilities-page.js).

   Each calculator is data: its inputs, and a `compute` that takes what was
   typed and returns lines of answers, a table, notes and warnings, or an
   error. The page draws any of them the same way, and the arithmetic is
   checked in Node (tests/js/bench-calcs.check.mjs), so this file touches no
   page: it sets window.BenchCalc in a browser and module.exports in Node.

   Values are worked in base units (g, L, mol/L, g/L, mol, s), converted
   from and to the unit each field shows. */
(function (root) {
  'use strict';

  /* ------------------------------------------------------------ units */

  const U = {
    mass: [['kg', 1e3], ['g', 1], ['mg', 1e-3], ['µg', 1e-6], ['ng', 1e-9], ['pg', 1e-12]],
    volume: [['L', 1], ['mL', 1e-3], ['µL', 1e-6], ['nL', 1e-9]],
    molar: [['M', 1], ['mM', 1e-3], ['µM', 1e-6], ['nM', 1e-9], ['pM', 1e-12]],
    massconc: [['g/L', 1], ['mg/mL', 1], ['µg/mL', 1e-3], ['ng/µL', 1e-3], ['ng/mL', 1e-6], ['% (w/v)', 10]],
    amount: [['mol', 1], ['mmol', 1e-3], ['µmol', 1e-6], ['nmol', 1e-9], ['pmol', 1e-12], ['fmol', 1e-15]],
    time: [['s', 1], ['min', 60], ['h', 3600], ['d', 86400]],
    weight: [['g', 1], ['kg', 1000]],
  };
  // Any concentration, for a dilution: each unit's family converts only
  // within itself.
  const CONC = [
    ...U.molar.map(([u, f]) => [u, f, 'molar']),
    ...U.massconc.map(([u, f]) => [u, f, 'mass']),
    ['% (v/v)', 0.01, 'vv'], ['×', 1, 'fold'], ['U/mL', 1, 'units'], ['U/µL', 1000, 'units'],
    ['cells/mL', 1, 'cells'], ['TU/mL', 1, 'titer'],
  ];
  U.conc = CONC.map(([u, f]) => [u, f]);
  const FAMILY = Object.fromEntries(CONC.map(([u, , fam]) => [u, fam]));

  function factor(set, unit) {
    const row = (U[set] || []).find(([u]) => u === unit);
    return row ? row[1] : NaN;
  }

  /* "2,5" and "1 000" read as numbers; anything else is NaN. */
  function num(raw) {
    if (typeof raw === 'number') return raw;
    let s = String(raw == null ? '' : raw).trim().replace(/\s+/g, '');
    if (!s) return NaN;
    if (/^-?\d+,\d+$/.test(s) && !/^-?\d{1,3}(,\d{3})+$/.test(s)) s = s.replace(',', '.');
    else s = s.replace(/,/g, '');
    s = s.replace(/×10\^?/i, 'e').replace(/x10\^?/i, 'e');
    return /^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(s) ? Number(s) : NaN;
  }

  const ok = (x) => typeof x === 'number' && Number.isFinite(x);

  /* 4 significant figures, no trailing zeros, thousands spaced; a million
     and up as a power of ten, so counts and titers read alike. */
  function fmt(x, sig = 4) {
    if (!ok(x)) return '—';
    if (x === 0) return '0';
    const a = Math.abs(x);
    if (a >= 1e6 || a < 1e-4) {
      const [m, e] = x.toExponential(sig - 1).split('e');
      return `${String(Number(m))} × 10${superscript(Number(e))}`;
    }
    const digits = Math.max(0, sig - 1 - Math.floor(Math.log10(a)));
    const text = Number(x.toFixed(Math.min(digits, 10))).toString();
    const [int, dec] = text.split('.');
    const spaced = int.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    return dec ? `${spaced}.${dec}` : spaced;
  }
  function superscript(n) {
    const map = { '-': '⁻', 0: '⁰', 1: '¹', 2: '²', 3: '³', 4: '⁴', 5: '⁵', 6: '⁶', 7: '⁷', 8: '⁸', 9: '⁹' };
    return String(n).split('').map((c) => map[c] || c).join('');
  }

  /* A base value in the unit of `set` that reads best (1–999), from `only`
     when given: show(0.0025, 'volume') → "2.5 mL". */
  function show(base, set, only) {
    if (!ok(base)) return '—';
    const rows = (U[set] || []).filter(([u]) => !only || only.includes(u));
    const pick = rows.find(([, f]) => Math.abs(base / f) >= 1 - 1e-9) || rows[rows.length - 1];
    if (!pick) return fmt(base);
    return `${fmt(base / pick[1])} ${pick[0]}`;
  }
  const L = (label, value, main = false) => ({ label, value, main });
  // Words shown on the page, in its language (base.html's t()); as written
  // here when there is no t (in Node, for the checks).
  const t = (text, values) => (typeof root.t === 'function' ? root.t(text, values)
    : (values ? String(text).replace(/%\((\w+)\)[sd]/g, (m, k) => (k in values ? values[k] : m)) : text));

  /* ------------------------------------------------------ reference data */

  const CHEMICALS = [
    ['NaCl', 58.44], ['KCl', 74.55], ['Tris base', 121.14], ['Tris-HCl', 157.60], ['EDTA (free acid)', 292.24],
    ['EDTA disodium dihydrate', 372.24], ['EGTA', 380.35], ['HEPES', 238.30], ['HEPES sodium salt', 260.29],
    ['MOPS', 209.26], ['MES monohydrate', 213.25], ['PIPES', 302.37], ['Glycine', 75.07], ['Imidazole', 68.08],
    ['MgCl₂ (anhydrous)', 95.21], ['MgCl₂·6H₂O', 203.30], ['MgSO₄·7H₂O', 246.47], ['CaCl₂ (anhydrous)', 110.98],
    ['CaCl₂·2H₂O', 147.01], ['NaH₂PO₄·H₂O', 137.99], ['Na₂HPO₄ (anhydrous)', 141.96], ['Na₂HPO₄·7H₂O', 268.07],
    ['KH₂PO₄', 136.09], ['K₂HPO₄', 174.18], ['Sodium acetate (anhydrous)', 82.03], ['Sodium acetate·3H₂O', 136.08],
    ['Sodium citrate·2H₂O', 294.10], ['Citric acid·H₂O', 210.14], ['Sodium bicarbonate', 84.01], ['NaOH', 40.00],
    ['KOH', 56.11], ['HCl', 36.46], ['Ammonium sulfate', 132.14], ['Urea', 60.06], ['Guanidine HCl', 95.53],
    ['SDS', 288.38], ['DTT', 154.25], ['β-Mercaptoethanol', 78.13], ['TCEP·HCl', 286.65], ['PMSF', 174.19],
    ['IPTG', 238.30], ['X-gal', 408.63], ['Glucose', 180.16], ['Sucrose', 342.30], ['Glycerol', 92.09],
    ['Sodium azide', 65.01], ['ZnCl₂', 136.30], ['MnCl₂·4H₂O', 197.91], ['Sodium pyruvate', 110.04],
    ['L-Glutamine', 146.14], ['DMSO', 78.13], ['Ethanol', 46.07], ['Ampicillin sodium', 371.39],
    ['Kanamycin sulfate', 582.58], ['Tamoxifen', 371.51], ['4-Hydroxytamoxifen', 387.51],
    ['Doxycycline hyclate', 512.94], ['Puromycin dihydrochloride', 544.43],
  ];

  // Good's and other buffers: pKa at 25 °C, its change per °C, and how it
  // is made: one form weighed and titrated (HCl or NaOH), two salts mixed,
  // or (citrate, with three pKa) the ratio only.
  const BUFFERS = [
    ['Tris', 8.06, -0.028, { weigh: ['Tris base', 121.14], titrant: 'HCl' }],
    ['HEPES', 7.48, -0.014, { weigh: ['HEPES (free acid)', 238.30], titrant: 'NaOH' }],
    ['MOPS', 7.20, -0.015, { weigh: ['MOPS (free acid)', 209.26], titrant: 'NaOH' }],
    ['MES', 6.10, -0.011, { weigh: ['MES monohydrate', 213.25], titrant: 'NaOH' }],
    ['PIPES', 6.76, -0.0085, { weigh: ['PIPES (free acid)', 302.37], titrant: 'NaOH' }],
    ['Bis-Tris', 6.46, -0.017, { weigh: ['Bis-Tris', 209.24], titrant: 'HCl' }],
    ['Phosphate (pKa₂)', 7.20, -0.0028, { pair: [['NaH₂PO₄·H₂O', 137.99], ['Na₂HPO₄ (anhydrous)', 141.96]] }],
    ['Acetate', 4.76, 0.0002, { pair: [['Acetic acid, glacial', 60.05, 17.4], ['Sodium acetate·3H₂O', 136.08]] }],
    ['Citrate (pKa₃)', 6.40, 0, null],
    ['Bicine', 8.26, -0.018, { weigh: ['Bicine', 163.17], titrant: 'NaOH' }],
    ['Tricine', 8.05, -0.021, { weigh: ['Tricine', 179.17], titrant: 'NaOH' }],
    ['EPPS (HEPPS)', 8.00, -0.015, { weigh: ['EPPS', 252.33], titrant: 'NaOH' }],
    ['Glycine (pKa₂)', 9.60, -0.025, { weigh: ['Glycine', 75.07], titrant: 'NaOH' }],
    ['CHES', 9.50, -0.011, { weigh: ['CHES', 207.29], titrant: 'NaOH' }],
    ['Carbonate (pKa₂)', 10.33, -0.009, { pair: [['NaHCO₃', 84.01], ['Na₂CO₃ (anhydrous)', 105.99]] }],
    ['CAPS', 10.40, -0.009, { weigh: ['CAPS', 221.32], titrant: 'NaOH' }],
    ['Imidazole', 6.95, -0.020, { weigh: ['Imidazole', 68.08], titrant: 'HCl' }],
  ];
  // The titrants as they sit on the shelf: concentrated HCl, and 10 M NaOH.
  const TITRANTS = { HCl: ['37 % HCl (12 M)', 12], NaOH: ['10 M NaOH', 10] };

  // Liquids sold concentrated: name, w/w %, density (g/mL), MW, and acid or
  // base (for the safety line).
  const REAGENTS = [
    ['Hydrochloric acid', 37, 1.18, 36.46, 'acid'], ['Sulfuric acid', 96, 1.84, 98.08, 'acid'],
    ['Nitric acid', 70, 1.41, 63.01, 'acid'], ['Phosphoric acid', 85, 1.685, 98.00, 'acid'],
    ['Acetic acid, glacial', 100, 1.049, 60.05, 'acid'], ['Sodium hydroxide solution', 50, 1.52, 40.00, 'base'],
    ['Ammonia solution', 28, 0.90, 17.03, 'base'], ['Hydrogen peroxide', 30, 1.11, 34.01, ''],
    ['β-Mercaptoethanol', 100, 1.114, 78.13, ''], ['Glycerol', 100, 1.261, 92.09, ''],
    ['Ethanol', 100, 0.789, 46.07, ''], ['DMSO', 100, 1.10, 78.13, ''],
  ];

  // Surface area (cm²) and usual medium volume (mL) of culture vessels.
  const VESSELS = [
    ['96-well', 0.32, 0.1], ['48-well', 0.95, 0.3], ['24-well', 1.9, 0.5], ['12-well', 3.8, 1],
    ['6-well', 9.5, 2], ['35 mm dish', 9, 2], ['60 mm dish', 21, 4], ['100 mm dish', 55, 10],
    ['150 mm dish', 145, 20], ['T25 flask', 25, 5], ['T75 flask', 75, 15], ['T175 flask', 175, 30],
  ];

  // Working concentration (µg/mL), a usual stock (mg/mL), what it is
  // dissolved in, and how it keeps.
  const ANTIBIOTICS = [
    ['Ampicillin (E. coli)', 100, 100, 'Water, filtered', '−20 °C'], ['Carbenicillin (E. coli)', 100, 100, 'Water, filtered', '−20 °C'],
    ['Kanamycin (E. coli)', 50, 50, 'Water, filtered', '−20 °C'],
    ['Chloramphenicol (E. coli, in ethanol)', 34, 34, 'Ethanol', '−20 °C'], ['Tetracycline (E. coli)', 10, 10, '70 % ethanol', '−20 °C, dark'],
    ['Spectinomycin (E. coli)', 50, 50, 'Water, filtered', '−20 °C'], ['Gentamicin (E. coli)', 15, 10, 'Water, filtered', '4 °C'],
    ['Zeocin (E. coli, low salt)', 25, 100, 'As sold', '−20 °C, dark'],
    ['Puromycin (mammalian)', 2, 10, 'Water, filtered', '−20 °C'], ['Blasticidin (mammalian)', 10, 10, 'Water, filtered', '−20 °C, few thaws'],
    ['G418 / Geneticin (mammalian)', 500, 50, 'Water, filtered', '4 °C'],
    ['Hygromycin B (mammalian)', 200, 50, 'As sold', '4 °C, dark'], ['Zeocin (mammalian)', 200, 100, 'As sold', '−20 °C, dark'],
    ['Penicillin–streptomycin (1×, U/mL pen)', 100, 10, 'As sold (100×)', '−20 °C'],
  ];
  // Other stocks a lab makes: name, a usual stock, solvent, how it keeps.
  const STOCKS = [
    ['IPTG', '1 M', 'Water, filtered', '−20 °C'], ['X-gal', '20 mg/mL', 'DMF or DMSO', '−20 °C, dark'],
    ['DTT', '1 M', 'Water', '−20 °C, single-use aliquots'], ['PMSF', '100 mM', 'Isopropanol or ethanol', 'Room temperature; add just before use (minutes in water)'],
    ['Arabinose', '20 %', 'Water, filtered', '4 °C'], ['Doxycycline', '1 mg/mL', 'Water, filtered', '−20 °C, dark'],
    ['Tamoxifen', '20 mg/mL', 'Corn oil (shake at 37 °C to dissolve)', '4 °C, dark; make weekly'], ['4-Hydroxytamoxifen', '10 mM', 'Ethanol', '−20 °C, dark'],
  ];
  // Band sizes (bp), largest first, of ladders most labs run.
  const LADDERS = [
    ['1 kb DNA Ladder (NEB N3232)', [10000, 8000, 6000, 5000, 4000, 3000, 2000, 1500, 1000, 500]],
    ['1 kb Plus DNA Ladder (NEB N3200)', [10000, 8000, 6000, 5000, 4000, 3000, 2000, 1500, 1200, 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]],
    ['100 bp DNA Ladder (NEB N3231)', [1517, 1200, 1000, 900, 800, 700, 600, 517, 500, 400, 300, 200, 100]],
    ['GeneRuler 1 kb (Thermo SM0311)', [10000, 8000, 6000, 5000, 4000, 3500, 3000, 2500, 2000, 1500, 1000, 750, 500, 250]],
    ['GeneRuler 1 kb Plus (Thermo SM1331)', [20000, 10000, 7000, 5000, 4000, 3000, 2000, 1500, 1000, 700, 500, 400, 300, 200, 75]],
    ['GeneRuler 100 bp Plus (Thermo SM0321)', [3000, 2000, 1500, 1200, 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]],
  ];


  const GEL_RANGES = [
    ['0.5%', '1–30 kb'], ['0.7%', '0.8–12 kb'], ['1.0%', '0.5–10 kb'], ['1.2%', '0.4–7 kb'],
    ['1.5%', '0.2–3 kb'], ['2.0%', '0.05–2 kb'], ['3.0%', t('< 0.5 kb (small PCR products)')],
  ];

  /* ------------------------------------------------------ small helpers */

  function lines(text) {
    return String(text || '').split(/\n/).map((l) => l.trim()).filter(Boolean);
  }
  // "name 1.2 3" → { name: "name", nums: [1.2, 3] } (a name may have spaces).
  function row(line) {
    const parts = line.split(/[\t;,]+|\s+/).filter(Boolean);
    const nums = [];
    while (parts.length && ok(num(parts[parts.length - 1]))) nums.unshift(num(parts.pop()));
    return { name: parts.join(' '), nums };
  }
  const mean = (xs) => xs.reduce((a, x) => a + x, 0) / xs.length;
  function linfit(xs, ys) {
    const n = xs.length;
    const mx = xs.reduce((a, b) => a + b, 0) / n;
    const my = ys.reduce((a, b) => a + b, 0) / n;
    let sxy = 0; let sxx = 0; let syy = 0;
    for (let i = 0; i < n; i += 1) {
      sxy += (xs[i] - mx) * (ys[i] - my); sxx += (xs[i] - mx) ** 2; syy += (ys[i] - my) ** 2;
    }
    const slope = sxy / sxx;
    return { slope, intercept: my - slope * mx, r2: syy ? (sxy * sxy) / (sxx * syy) : 1 };
  }
  // y = a + b x + c x², by least squares.
  function quadfit(xs, ys) {
    const s = (p) => xs.reduce((acc, x) => acc + x ** p, 0);
    const t = (p) => xs.reduce((acc, x, i) => acc + ys[i] * x ** p, 0);
    const A = [[xs.length, s(1), s(2)], [s(1), s(2), s(3)], [s(2), s(3), s(4)]];
    const B = [t(0), t(1), t(2)];
    const det = (m) => m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
      + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    const d = det(A);
    const col = (i) => det(A.map((r, k) => r.map((v, j) => (j === i ? B[k] : v))));
    const [a, b, c] = [col(0) / d, col(1) / d, col(2) / d];
    const my = ys.reduce((x, y) => x + y, 0) / ys.length;
    const ssr = ys.reduce((acc, y, i) => acc + (y - (a + b * xs[i] + c * xs[i] ** 2)) ** 2, 0);
    const sst = ys.reduce((acc, y) => acc + (y - my) ** 2, 0);
    return { a, b, c, r2: sst ? 1 - ssr / sst : 1 };
  }
  // The inverse of the standard normal (Acklam's approximation).
  function zq(p) {
    const a = [-39.6968302866538, 220.946098424521, -275.928510446969, 138.357751867269, -30.6647980661472, 2.50662827745924];
    const b = [-54.4760987982241, 161.585836858041, -155.698979859887, 66.8013118877197, -13.2806815528857];
    const c = [-0.00778489400243029, -0.322396458041136, -2.40075827716184, -2.54973253934373, 4.37466414146497, 2.93816398269878];
    const d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742];
    const lo = 0.02425;
    let q; let r;
    if (p < lo) {
      q = Math.sqrt(-2 * Math.log(p));
      return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    }
    if (p > 1 - lo) return -zq(1 - p);
    q = p - 0.5; r = q * q;
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
      / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
  }

  /* DNA: nearest-neighbour Tm (SantaLucia 1998), as app/inventory_service.py. */
  const NN = { AA: [-7.9, -22.2], AT: [-7.2, -20.4], TA: [-7.2, -21.3], CA: [-8.5, -22.7], GT: [-8.4, -22.4],
    CT: [-7.8, -21.0], GA: [-8.2, -22.2], CG: [-10.6, -27.2], GC: [-9.8, -24.4], GG: [-8.0, -19.9] };
  const COMP = { A: 'T', T: 'A', G: 'C', C: 'G' };
  const revcomp = (s) => s.split('').reverse().map((c) => COMP[c] || 'N').join('');
  function cleanDna(raw) {
    return String(raw || '').toUpperCase().replace(/5'|3'/g, '').replace(/[^A-Z]/g, '');
  }
  function dnaTm(seq, naM = 0.05, primerM = 250e-9) {
    if (seq.length < 8 || /[^ACGT]/.test(seq)) return NaN;
    let dh = 0; let ds = 0;
    [seq[0], seq[seq.length - 1]].forEach((end) => {
      const [h, e] = 'GC'.includes(end) ? [0.1, -2.8] : [2.3, 4.1];
      dh += h; ds += e;
    });
    for (let i = 0; i < seq.length - 1; i += 1) {
      const step = seq.slice(i, i + 2);
      const [h, e] = NN[step] || NN[revcomp(step)];
      dh += h; ds += e;
    }
    if (seq === revcomp(seq)) ds -= 1.4;
    ds += 0.368 * (seq.length - 1) * Math.log(naM);
    return (dh * 1000) / (ds + 1.987 * Math.log(primerM)) - 273.15;
  }

  const RESIDUE = { A: 71.0788, R: 156.1875, N: 114.1038, D: 115.0886, C: 103.1388, E: 129.1155, Q: 128.1307,
    G: 57.0519, H: 137.1411, I: 113.1594, L: 113.1594, K: 128.1741, M: 131.1926, F: 147.1766, P: 97.1167,
    S: 87.0782, T: 101.1051, W: 186.2132, Y: 163.176, V: 99.1326 };
  function cleanProtein(raw) {
    const text = String(raw || '').split('\n').filter((l) => !l.trim().startsWith('>')).join('');
    return text.toUpperCase().replace(/[^A-Z]/g, '');
  }
  function protein(seq) {
    if (!seq || /[^ACDEFGHIKLMNPQRSTVWY]/.test(seq)) return null;
    const count = (aa) => seq.split(aa).length - 1;
    const mw = [...seq].reduce((m, aa) => m + RESIDUE[aa], 18.01524);
    const reduced = count('W') * 5500 + count('Y') * 1490;
    const epsilon = reduced + Math.floor(count('C') / 2) * 125;
    // pI: the pH where the net charge is 0 (EMBOSS pKa values), by bisection.
    const pos = { K: 10.8, R: 12.5, H: 6.5 };
    const neg = { D: 3.9, E: 4.1, C: 8.5, Y: 10.1 };
    const charge = (pH) => {
      let z = 1 / (1 + 10 ** (pH - 8.6)) - 1 / (1 + 10 ** (3.6 - pH));
      Object.entries(pos).forEach(([aa, pk]) => { z += count(aa) / (1 + 10 ** (pH - pk)); });
      Object.entries(neg).forEach(([aa, pk]) => { z -= count(aa) / (1 + 10 ** (pk - pH)); });
      return z;
    };
    let lo = 0; let hi = 14;
    for (let i = 0; i < 60; i += 1) { const mid = (lo + hi) / 2; if (charge(mid) > 0) lo = mid; else hi = mid; }
    return { length: seq.length, mw, epsilon, reduced, pI: (lo + hi) / 2, charge7: charge(7.0) };
  }

  /* --------------------------------------------------- the calculators */

  const n = (v, key) => v.num[key];
  const b = (v, key) => v.base[key];
  const opt = (list, labelIndex = 0) => list.map((r, i) => [String(i), t(r[labelIndex])]);

  const CALCS = [
    /* ================================================= Solutions */
    {
      id: 'molarity',
      solveOne: ['mass', 'volume', 'conc', 'mw'],
      inputs: [
        { key: 'chem', label: t('Chemical'), type: 'chemical' },
        { key: 'mass', label: t('Mass'), units: 'mass', unit: 'mg' },
        { key: 'volume', label: t('Volume'), units: 'volume', unit: 'mL' },
        { key: 'conc', label: t('Concentration'), units: 'molar', unit: 'mM' },
        { key: 'mw', label: t('Molecular weight (g/mol)') },
        { key: 'purity', label: t('Purity (%)'), value: '100', hint: t('Weigh more of a powder that is not 100 %') },
      ],
      compute(v) {
        const vals = { mass: b(v, 'mass'), volume: b(v, 'volume'), conc: b(v, 'conc'), mw: n(v, 'mw') };
        const blank = Object.keys(vals).filter((k) => !ok(vals[k]));
        if (blank.length !== 1) return { hint: t('Fill in three of mass, volume, concentration and molecular weight.') };
        const k = blank[0];
        const purity = ok(n(v, 'purity')) && n(v, 'purity') > 0 ? n(v, 'purity') / 100 : 1;
        let out;
        if (k === 'mass') out = L(t('Weigh out'), show(vals.conc * vals.volume * vals.mw / purity, 'mass'), true);
        if (k === 'volume') out = L(t('Make it up to'), show(vals.mass * purity / (vals.conc * vals.mw), 'volume'), true);
        if (k === 'conc') out = L(t('Concentration'), show(vals.mass * purity / (vals.volume * vals.mw), 'molar'), true);
        if (k === 'mw') out = L(t('Molecular weight'), `${fmt(vals.mass * purity / (vals.conc * vals.volume))} g/mol`, true);
        return { solved: k, lines: [out], notes: purity < 1 ? [t('Allowing for %(pct)s % purity.', { pct: fmt(purity * 100) })] : [] };
      },
    },
    {
      id: 'dilution',
      solveOne: ['c1', 'v1', 'c2', 'v2'],
      inputs: [
        { key: 'c1', label: t('Stock C₁'), units: 'conc', unit: 'mM' },
        { key: 'v1', label: t('Stock volume V₁'), units: 'volume', unit: 'µL' },
        { key: 'c2', label: t('Final C₂'), units: 'conc', unit: 'µM' },
        { key: 'v2', label: t('Final volume V₂'), units: 'volume', unit: 'mL' },
        { key: 'mw', label: t('MW (g/mol), to go between mg/mL and µM') },
      ],
      compute(v) {
        // A stock in mg/mL and a final in µM (or the other way) meet through
        // the molecular weight: `to2` turns C₁ into C₂'s kind of unit.
        const f1 = FAMILY[v.unit.c1]; const f2 = FAMILY[v.unit.c2]; const mw = n(v, 'mw');
        let to2 = 1;
        if (f1 !== f2) {
          const pair = (f1 === 'molar' && f2 === 'mass') || (f1 === 'mass' && f2 === 'molar');
          if (!pair) return { error: t('C₁ and C₂ must be the same kind of unit.') };
          if (!(mw > 0)) return { error: t('One is molar and the other mass per volume: give the MW.') };
          to2 = f1 === 'molar' ? mw : 1 / mw;
        }
        const vals = { c1: b(v, 'c1') * to2, v1: b(v, 'v1'), c2: b(v, 'c2'), v2: b(v, 'v2') };
        const blank = Object.keys(vals).filter((k) => !ok(vals[k]));
        if (blank.length !== 1) return { hint: t('Fill in three of the four; the blank one is worked out.') };
        const k = blank[0];
        const x = { c1: vals.c2 * vals.v2 / vals.v1, v1: vals.c2 * vals.v2 / vals.c1,
          c2: vals.c1 * vals.v1 / vals.v2, v2: vals.c1 * vals.v1 / vals.c2 }[k];
        const all = { ...vals, [k]: x };
        // Diluting can't make it stronger: no volume that suggests otherwise.
        if (all.c2 > all.c1 * (1 + 1e-9)) {
          return { solved: k, error: t('The final is stronger than the stock: a dilution can’t reach it. Use a stronger stock, or a weaker final.') };
        }
        const label = { c1: t('Stock C₁'), v1: t('Stock to add'), c2: t('Final C₂'), v2: t('Final volume') }[k];
        const value = k[0] === 'c' ? `${fmt(x / (k === 'c1' ? to2 : 1) / factor('conc', v.unit[k]))} ${v.unit[k]}` : show(x, 'volume');
        const out = { solved: k, lines: [L(label, value, true)], notes: [], warnings: [] };
        if (all.v2 >= all.v1) out.lines.push(L(t('Diluent'), show(all.v2 - all.v1, 'volume')));
        if (ok(all.v1) && all.v1 < 0.5e-6) out.warnings.push(t('Under 0.5 µL is hard to pipette: make an intermediate dilution.'));
        out.notes.push(t('1 : %(ratio)s dilution.', { ratio: fmt(all.v2 / all.v1, 3) }));
        if (to2 !== 1) out.notes.push(t('Through MW %(mw)s g/mol.', { mw: fmt(mw) }));
        return out;
      },
    },
    {
      id: 'serial',
      inputs: [
        { key: 'start', label: t('Starting concentration'), units: 'conc', unit: 'µM', value: '100' },
        { key: 'fold', label: t('Fold per step'), value: '10' },
        { key: 'steps', label: t('Tubes'), value: '6' },
        { key: 'each', label: t('Volume left in each tube'), units: 'volume', unit: 'µL', value: '100' },
      ],
      compute(v) {
        const f = n(v, 'fold'); const steps = Math.round(n(v, 'steps')); const each = b(v, 'each');
        if (!(f > 1) || !(steps >= 1 && steps <= 30) || !ok(each) || !ok(n(v, 'start'))) return { hint: t('A start, a fold above 1, 1–30 tubes and a volume.') };
        const transfer = each / (f - 1);
        const rows = [];
        for (let i = 0; i <= steps; i += 1) rows.push([i ? t('Tube %(n)s', { n: i }) : t('Stock solution'), `${fmt(n(v, 'start') / f ** i)} ${v.unit.start}`]);
        return {
          lines: [L(t('Diluent in each tube'), show(each, 'volume'), true), L(t('Carry from tube to tube'), show(transfer, 'volume'), true)],
          table: { head: ['', t('Concentration')], rows },
          notes: [t('Mix each tube before the next transfer; take %(transfer)s out of the last one so all end with %(each)s. Starting with %(start)s of stock is enough.',
            { transfer: show(transfer, 'volume'), each: show(each, 'volume'), start: show(each + transfer, 'volume') })],
        };
      },
    },
    {
      id: 'serialfixed',
      inputs: [
        { key: 'start', label: t('Starting concentration'), units: 'conc', unit: 'µM', value: '100' },
        { key: 'transfer', label: t('Carry (µL)'), value: '10' },
        { key: 'diluent', label: t('Into diluent (µL)'), value: '90' },
        { key: 'steps', label: t('Tubes'), value: '6' },
      ],
      compute(v) {
        const tr = n(v, 'transfer'); const dl = n(v, 'diluent'); const steps = Math.round(n(v, 'steps'));
        if (!(tr > 0) || !(dl > 0) || !(steps >= 1 && steps <= 30) || !ok(n(v, 'start'))) return { hint: t('A start, the volumes and 1–30 tubes.') };
        const f = (tr + dl) / tr;
        const rows = [];
        for (let i = 0; i <= steps; i += 1) rows.push([i ? t('Tube %(n)s', { n: i }) : t('Stock solution'), `${fmt(n(v, 'start') / f ** i)} ${v.unit.start}`]);
        return {
          lines: [L(t('Each step'), `1 : ${fmt(f, 4)}`, true), L(t('Diluent in each tube'), `${fmt(dl)} µL`), L(t('Carry from tube to tube'), `${fmt(tr)} µL`)],
          table: { head: ['', t('Concentration')], rows },
          notes: [t('Mix each tube before the next transfer. Each tube keeps %(left)s µL; the last keeps %(last)s µL unless you take %(tr)s µL out.',
            { left: fmt(dl), last: fmt(tr + dl), tr: fmt(tr) })],
        };
      },
    },
    {
      id: 'percent',
      inputs: [
        { key: 'pct', label: t('Percent'), value: '10' },
        { key: 'kind', label: t('Kind'), type: 'select', options: [['wv', t('% w/v (a solid)')], ['vv', t('% v/v (a liquid)')]] },
        { key: 'volume', label: t('Final volume'), units: 'volume', unit: 'mL', value: '50' },
      ],
      compute(v) {
        const pct = n(v, 'pct'); const vol = b(v, 'volume');
        if (!ok(pct) || !ok(vol)) return { hint: t('A percent and a volume.') };
        const amount = pct / 100 * vol * 1000;    // g or mL per litre → per this volume
        return v.raw.kind === 'vv'
          ? { lines: [L(t('Liquid to add'), show(amount * 1e-3, 'volume'), true), L(t('Make up to'), show(vol, 'volume'))] }
          : { lines: [L(t('Weigh out'), show(amount, 'mass'), true), L(t('Make up to'), show(vol, 'volume'))] };
      },
    },
    {
      id: 'buffer',
      inputs: [
        { key: 'buffer', label: t('Buffer'), type: 'select', options: [...opt(BUFFERS), ['custom', t('Other (give its pKa)')]] },
        { key: 'pka', label: t('pKa at 25 °C (for Other)') },
        { key: 'ph', label: t('Wanted pH'), value: '7.4' },
        { key: 'temp', label: t('Temperature (°C)'), value: '25' },
        { key: 'conc', label: t('Buffer concentration'), units: 'molar', unit: 'mM', value: '50' },
        { key: 'volume', label: t('Volume'), units: 'volume', unit: 'mL', value: '500' },
      ],
      compute(v) {
        const custom = v.raw.buffer === 'custom';
        const buf = custom ? [t('Buffer'), n(v, 'pka'), 0] : BUFFERS[Number(v.raw.buffer || 0)];
        const temp = ok(n(v, 'temp')) ? n(v, 'temp') : 25;
        const pka = buf[1] + buf[2] * (temp - 25);
        const ph = n(v, 'ph');
        if (!ok(pka) || !ok(ph)) return { hint: t('The wanted pH (and a pKa for Other).') };
        const baseFrac = 1 / (1 + 10 ** (pka - ph));
        const mol = b(v, 'conc') * b(v, 'volume');
        const out = {
          lines: [
            L(t('pKa of %(buffer)s at %(temp)s °C', { buffer: t(buf[0]), temp: fmt(temp) }), fmt(pka, 3)),
            L(t('Base : acid'), `${fmt(baseFrac / (1 - baseFrac), 3)} : 1`),
          ],
          notes: [], warnings: [],
        };
        const how = custom ? null : buf[3];
        if (ok(mol) && how && how.weigh) {
          const [form, mw] = how.weigh;
          const [name, molar] = TITRANTS[how.titrant];
          const titrant = how.titrant === 'HCl' ? mol * (1 - baseFrac) : mol * baseFrac;
          out.lines.unshift(
            L(t('Weigh'), `${show(mol * mw, 'mass')} ${t(form)}`, true),
            L(t('Titrate with about'), `${show(titrant / molar, 'volume')} ${name}`, true),
            L(t('…or 1 M %(titrant)s', { titrant: how.titrant }), show(titrant, 'volume')),
            L(t('Then make up to'), show(b(v, 'volume'), 'volume')),
          );
          out.notes.push(t('Dissolve in about 80 % of the volume, bring to pH with the meter, then make up to volume. Set the pH at the temperature you use it.'));
        } else if (ok(mol) && how && how.pair) {
          const [[acid, mwA, liquid], [base, mwB]] = how.pair;
          const molA = mol * (1 - baseFrac); const molB = mol * baseFrac;
          out.lines.unshift(
            L(t(acid), liquid ? `${show(molA / liquid, 'volume')} (${show(molA * mwA, 'mass')})` : show(molA * mwA, 'mass'), true),
            L(t(base), show(molB * mwB, 'mass'), true),
            L(t('Then make up to'), show(b(v, 'volume'), 'volume')),
          );
          out.notes.push(t('Mix the two, check the pH with the meter, and adjust with a little of either.'));
        } else if (ok(mol)) {
          out.lines.push(L(t('Base form'), show(mol * baseFrac, 'amount')), L(t('Acid form'), show(mol * (1 - baseFrac), 'amount')));
        }
        if (Math.abs(ph - pka) > 1) out.warnings.push(t('pH %(ph)s is more than one unit from the pKa: %(buffer)s buffers poorly there.', { ph: fmt(ph, 3), buffer: t(buf[0]) }));
        out.notes.push(t('The equation ignores ionic strength; check the pH with a meter.'));
        return out;
      },
    },
    {
      id: 'saltform',
      inputs: [
        { key: 'mass', label: t('Mass in the recipe'), units: 'mass', unit: 'g', value: '1' },
        { key: 'mw1', label: t('MW of the form in the recipe'), value: '95.21' },
        { key: 'mw2', label: t('MW of the form you have'), value: '203.30' },
      ],
      compute(v) {
        if (!ok(b(v, 'mass')) || !ok(n(v, 'mw1')) || !ok(n(v, 'mw2'))) return { hint: t('The mass and both molecular weights.') };
        return { lines: [L(t('Weigh out'), show(b(v, 'mass') * n(v, 'mw2') / n(v, 'mw1'), 'mass'), true)] };
      },
    },

    {
      id: 'fromconc',
      inputs: [
        { key: 'reagent', label: t('Reagent'), type: 'select', options: REAGENTS.map((r, i) => [String(i), `${t(r[0])} (${fmt(r[1])} %)`]) },
        { key: 'pct', label: t('Bottle strength (% w/w, blank: usual)') },
        { key: 'want', label: t('Wanted'), units: 'molar', unit: 'M', value: '1' },
        { key: 'volume', label: t('Final volume'), units: 'volume', unit: 'mL', value: '100' },
      ],
      compute(v) {
        const r = REAGENTS[Number(v.raw.reagent || 0)];
        const pct = ok(n(v, 'pct')) ? n(v, 'pct') : r[1];
        const molar = pct / 100 * r[2] * 1000 / r[3];
        const add = b(v, 'want') * b(v, 'volume') / molar;
        if (!ok(add)) return { lines: [L(t('The bottle is'), `${fmt(molar, 3)} M`)], hint: t('The strength wanted and the volume.') };
        const out = { lines: [L(t('Concentrate to add'), show(add, 'volume'), true), L(t('Water'), show(b(v, 'volume') - add, 'volume')),
          L(t('The bottle is'), `${fmt(molar, 3)} M`)], notes: [], warnings: [] };
        if (add > b(v, 'volume')) return { error: t('Stronger than the bottle (%(m)s M).', { m: fmt(molar, 3) }) };
        if (r[4] === 'acid') out.warnings.push(t('Add the acid to the water, slowly, in a fume hood; never water to acid.'));
        if (r[4] === 'base') out.warnings.push(t('It heats as it dilutes: add it to cold water slowly.'));
        out.notes.push(t('From %(pct)s % w/w and a density of %(d)s g/mL; a bottle’s label gives its own.', { pct: fmt(pct), d: fmt(r[2]) }));
        return out;
      },
    },

    /* ================================================= DNA and RNA */
    {
      id: 'a260',
      inputs: [
        { key: 'kind', label: t('Kind'), type: 'select', options: [['50', t('dsDNA (50)')], ['40', t('RNA (40)')], ['33', t('ssDNA (33)')], ['20', t('Oligo (≈ 20–33)')]] },
        { key: 'a260', label: 'A₂₆₀' },
        { key: 'dilution', label: t('Dilution (×)'), value: '1' },
        { key: 'path', label: t('Path length (cm)'), value: '1', hint: t('NanoDrop readings are already per 1 cm') },
        { key: 'r280', label: t('260/280 (optional)') },
        { key: 'r230', label: t('260/230 (optional)') },
        { key: 'volume', label: t('Sample volume (optional)'), units: 'volume', unit: 'µL' },
      ],
      compute(v) {
        const a = n(v, 'a260');
        if (!ok(a)) return { hint: t('The A₂₆₀ reading.') };
        const conc = a * Number(v.raw.kind || 50) * (n(v, 'dilution') || 1) / (n(v, 'path') || 1);   // ng/µL
        const out = { lines: [L(t('Concentration'), `${fmt(conc)} ng/µL`, true)], notes: [], warnings: [] };
        if (ok(b(v, 'volume'))) out.lines.push(L(t('In the tube'), show(conc * b(v, 'volume') * 1e-3, 'mass')));   // ng/µL × µL
        const r1 = n(v, 'r280'); const r2 = n(v, 'r230');
        const rna = v.raw.kind === '40';
        if (ok(r1)) (r1 < (rna ? 1.9 : 1.75) ? out.warnings : out.notes).push(r1 < (rna ? 1.9 : 1.75)
          ? t('260/280 %(ratio)s: protein or phenol left in (pure %(pure)s).', { ratio: fmt(r1, 3), pure: rna ? 'RNA ≈ 2.0' : 'DNA ≈ 1.8' })
          : t('%(ratio)s: clean.', { ratio: `260/280 ${fmt(r1, 3)}` }));
        if (ok(r2)) (r2 < 1.8 ? out.warnings : out.notes).push(r2 < 1.8
          ? t('260/230 %(ratio)s: salt, guanidine or carbohydrate carried over (clean is 2.0–2.2).', { ratio: fmt(r2, 3) })
          : t('%(ratio)s: clean.', { ratio: `260/230 ${fmt(r2, 3)}` }));
        return out;
      },
    },
    {
      id: 'dnamoles',
      inputs: [
        { key: 'kind', label: t('Kind'), type: 'select', options: [['ds', 'dsDNA'], ['ss', 'ssDNA'], ['rna', 'RNA']] },
        { key: 'length', label: t('Length (bp or nt)'), value: '5000' },
        { key: 'mass', label: t('Mass'), units: 'mass', unit: 'ng', value: '100' },
        { key: 'moles', label: t('…or moles'), units: 'amount', unit: 'pmol' },
        { key: 'conc', label: t('Concentration (ng/µL, optional)') },
        { key: 'want', label: t('Amount wanted (fmol, optional)') },
      ],
      compute(v) {
        const len = n(v, 'length');
        if (!(len > 0)) return { hint: t('The length.') };
        const mw = { ds: len * 617.96 + 36.04, ss: len * 308.97 + 18.02, rna: len * 321.47 + 18.02 }[v.raw.kind || 'ds'];
        let mol = b(v, 'moles'); let mass = b(v, 'mass');
        if (ok(mass)) mol = mass / mw; else if (ok(mol)) mass = mol * mw; else return { hint: t('A mass or an amount in moles.') };
        const out = { lines: [L(t('Molecular weight'), `${fmt(mw)} g/mol`), L(t('Mass'), show(mass, 'mass'), true),
          L(t('Moles'), show(mol, 'amount'), true), L(t('Copies'), fmt(mol * 6.02214076e23, 3), true)] };
        const nM = n(v, 'conc') / mw * 1e6;                 // ng/µL is mg/L
        if (ok(nM)) {
          out.lines.push(L(t('Molar concentration'), `${fmt(nM)} nM`, true));
          if (ok(n(v, 'want'))) out.lines.push(L(t('Volume for %(fmol)s fmol', { fmol: fmt(n(v, 'want')) }), `${fmt(n(v, 'want') / nM)} µL`, true));
        }
        return out;
      },
    },
    {
      id: 'oligo',
      inputs: [
        { key: 'seq', label: t('Sequence 5′→3′'), type: 'text', value: 'GTAAAACGACGGCCAGT' },
        { key: 'na', label: 'Na⁺ (mM)', value: '50' },
        { key: 'primer', label: t('Primer (nM)'), value: '250' },
        { key: 'nmol', label: t('Delivered (nmol, optional)') },
        { key: 'stock', label: t('Stock wanted (µM)'), value: '100' },
        { key: 'seq2', label: t('Its partner (optional)'), type: 'text' },
        { key: 'pol', label: t('Polymerase'), type: 'select', options: [['taq', 'Taq'], ['hf', 'Q5 / Phusion']] },
        { key: 'amplicon', label: t('Product length (bp, optional)') },
      ],
      compute(v) {
        const seq = cleanDna(v.raw.seq);
        if (!seq) return { hint: t('A DNA sequence.') };
        const gc = (seq.match(/[GCS]/g) || []).length / seq.length * 100;
        const masses = { A: 313.21, C: 289.18, G: 329.21, T: 304.2 };
        const mw = [...seq].reduce((m, c) => m + (masses[c] || 308.9), 0) - 61.96;
        const eps = [...seq].reduce((e, c) => e + ({ A: 15400, C: 7400, G: 11500, T: 8700 }[c] || 10000), 0) * 0.9;
        const tm = dnaTm(seq, (n(v, 'na') || 50) / 1000, (n(v, 'primer') || 250) * 1e-9);
        const out = { lines: [L(t('Length'), `${seq.length} nt`), L('GC', `${fmt(gc, 3)} %`), L('Tm', ok(tm) ? `${fmt(tm, 3)} °C` : t('— (A, C, G, T only; 8 nt or more)'), true),
          L('MW', `${fmt(mw)} g/mol`), L(t('ε₂₆₀ (approx.)'), `${fmt(eps)} M⁻¹cm⁻¹`)], notes: [], warnings: [] };
        if (ok(n(v, 'nmol'))) out.lines.push(L(t('Water for %(conc)s µM', { conc: fmt(n(v, 'stock') || 100) }), `${fmt(n(v, 'nmol') * 1000 / (n(v, 'stock') || 100))} µL`, true));
        if (/GGGG|CCCC|AAAAA|TTTTT/.test(seq)) out.warnings.push(t('A run of 4+ G/C or 5+ A/T: mispriming and synthesis trouble.'));
        if (!/[GC]$/.test(seq.slice(-1)) ) out.notes.push(t('No G or C at the 3′ end (a "GC clamp" helps priming).'));
        const partner = cleanDna(v.raw.seq2);
        if (partner) {
          const tm2 = dnaTm(partner, (n(v, 'na') || 50) / 1000, (n(v, 'primer') || 250) * 1e-9);
          if (ok(tm) && ok(tm2)) {
            out.lines.push(L(t('Partner Tm'), `${fmt(tm2, 3)} °C`));
            // Taq anneals below the lower Tm; Q5 and Phusion buffers raise it,
            // so their makers anneal above it.
            const hf = v.raw.pol === 'hf';
            const ta = Math.min(tm, tm2) + (hf ? 3 : -5);
            out.lines.push(L(t('Annealing, to start'), `${fmt(Math.min(ta, 72), 3)} °C`, true));
            out.notes.push(hf ? t('Q5 / Phusion: the lower Tm + 3 °C. Their makers’ calculators allow for the buffer and can differ by a few degrees; above 72 °C, run a two-step PCR at 72 °C.')
              : t('Taq: the lower Tm − 5 °C.'));
            if (Math.abs(tm - tm2) > 5) out.warnings.push(t('The two Tm differ by %(diff)s °C (aim for within 5).', { diff: fmt(Math.abs(tm - tm2), 2) }));
          }
        }
        const bp = n(v, 'amplicon');
        if (bp > 0) {
          const perKb = v.raw.pol === 'hf' ? 30 : 60;
          out.lines.push(L(t('Extension'), `${fmt(Math.max(v.raw.pol === 'hf' ? 10 : 30, Math.ceil(bp / 1000 * perKb / 5) * 5))} s`, true));
          out.notes.push(v.raw.pol === 'hf' ? t('Extension at 72 °C, 30 s per kb (20 s per kb from a plasmid).') : t('Extension at 72 °C, 1 min per kb.'));
        }
        return out;
      },
    },
    {
      id: 'digest',
      inputs: [
        { key: 'ug', label: t('DNA (µg)'), value: '1' }, { key: 'conc', label: t('DNA concentration (ng/µL)'), value: '200' },
        { key: 'e1', label: t('First enzyme (U/µL)'), value: '20' }, { key: 'e2', label: t('Second enzyme (U/µL, blank: none)'), value: '20' },
        { key: 'units', label: t('Units per µg'), value: '10', hint: t('1 U cuts 1 µg in an hour; 10 U is plenty.') },
        { key: 'rxn', label: t('Reaction (µL)'), value: '50' },
      ],
      compute(v) {
        const ug = n(v, 'ug'); const rxn = n(v, 'rxn'); const units = n(v, 'units') || 10;
        const dna = ug * 1000 / n(v, 'conc');
        if (!ok(dna) || !ok(rxn) || !ok(n(v, 'e1'))) return { hint: t('DNA, its concentration, an enzyme and the reaction volume.') };
        const enzymes = [n(v, 'e1'), n(v, 'e2')].filter((u) => u > 0).map((u) => ug * units / u);
        const buffer = rxn / 10;
        const water = rxn - dna - buffer - enzymes.reduce((a, x) => a + x, 0);
        const rows = [[t('DNA'), fmt(dna)], [t('10× buffer'), fmt(buffer)],
          ...enzymes.map((ul, i) => [t('Enzyme %(n)s', { n: i + 1 }), fmt(ul)]), [t('Water'), water < 0 ? '—' : fmt(water)], [t('In all'), fmt(rxn)]];
        const glycerol = enzymes.reduce((a, x) => a + x, 0) * 0.5 / rxn * 100;
        const out = { lines: [L(t('Water'), water < 0 ? '—' : `${fmt(water)} µL`, true), L(t('Glycerol'), `${fmt(glycerol, 2)} %`)],
          table: { head: ['', 'µL'], rows }, warnings: [], notes: [t('Add the enzymes last; 1 h at the enzyme’s temperature (usually 37 °C).')] };
        if (water < 0) out.warnings.push(t('More than the reaction holds: use a bigger reaction or more concentrated DNA.'));
        if (glycerol > 5) out.warnings.push(t('Over 5 % glycerol invites star activity: use less enzyme or a bigger reaction.'));
        enzymes.forEach((ul, i) => { if (ul < 0.5) out.notes.push(t('Enzyme %(n)s: under 0.5 µL; 0.5–1 µL is fine, extra enzyme does no harm in an hour.', { n: i + 1 })); });
        return out;
      },
    },
    {
      id: 'ligation',
      inputs: [
        { key: 'vng', label: t('Vector (ng)'), value: '50' }, { key: 'vbp', label: t('Vector length (bp)'), value: '5000' },
        { key: 'ibp', label: t('Insert length (bp)'), value: '1000' }, { key: 'ratio', label: t('Insert : vector'), value: '3' },
        { key: 'iconc', label: t('Insert concentration (ng/µL, optional)') },
        { key: 'vconc', label: t('Vector concentration (ng/µL, optional)') },
        { key: 'rxn', label: t('Reaction'), type: 'select', options: [['20', '20 µL'], ['10', '10 µL']] },
      ],
      compute(v) {
        const ng = n(v, 'vng') * n(v, 'ibp') / n(v, 'vbp') * n(v, 'ratio');
        if (!ok(ng)) return { hint: t('Vector amount, both lengths and a ratio.') };
        const fmolV = n(v, 'vng') / (n(v, 'vbp') * 617.96 + 36.04) * 1e6;
        const out = { lines: [L(t('Insert'), `${fmt(ng)} ng`, true), L(t('Vector, in moles'), `${fmt(fmolV)} fmol`), L(t('Insert, in moles'), `${fmt(fmolV * n(v, 'ratio'))} fmol`)], warnings: [] };
        const iul = ng / n(v, 'iconc'); const vul = n(v, 'vng') / n(v, 'vconc');
        if (ok(iul)) out.lines.push(L(t('Insert to add'), `${fmt(iul)} µL`, true));
        if (ok(iul) && ok(vul)) {
          const rxn = Number(v.raw.rxn || 20); const buffer = rxn / 10; const ligase = 1;
          const water = rxn - iul - vul - buffer - ligase;
          if (water < 0) out.warnings.push(t('The DNA is more than the reaction holds: use more concentrated DNA or a bigger reaction.'));
          out.table = { head: ['', 'µL'], rows: [[t('Vector'), fmt(vul)], [t('Insert'), fmt(iul)], [t('10× T4 ligase buffer'), fmt(buffer)],
            [t('T4 DNA ligase'), fmt(ligase)], [t('Water'), water < 0 ? '—' : fmt(water)], [t('In all'), fmt(rxn)]] };
        }
        [[t('Insert'), iul], [t('Vector'), vul]].forEach(([what, ul]) => {
          if (ok(ul) && ul < 0.5) out.warnings.push(t('%(what)s: under 0.5 µL is hard to pipette; dilute it 1:10 first.', { what }));
        });
        return out;
      },
    },
    {
      id: 'assembly',
      inputs: [
        { key: 'list', label: t('Fragments'), type: 'textarea', value: 'pUC19 (cut) 2686 45\ninsert A 1200 38\ninsert B 800 52',
          hint: t('One a line: name, length (bp), ng/µL. The first is the vector.') },
        { key: 'vpmol', label: t('Vector (pmol)'), value: '0.05' },
        { key: 'ratio', label: t('Each insert : vector'), value: '2' },
        { key: 'rxn', label: t('Reaction volume (µL)'), value: '20' },
      ],
      compute(v) {
        const frags = lines(v.raw.list).map(row).filter((r) => r.nums.length >= 2);
        if (!frags.length) return { hint: t('Name, length and ng/µL for each fragment.') };
        let total = 0;
        const rows = frags.map((f, i) => {
          const pmol = (n(v, 'vpmol') || 0.05) * (i ? n(v, 'ratio') || 2 : 1);
          const ng = pmol * 1e-12 * (f.nums[0] * 617.96 + 36.04) * 1e9;
          const ul = ng / f.nums[1];
          total += ul;
          return [f.name || t('Fragment %(n)s', { n: i + 1 }), `${fmt(pmol, 3)} pmol`, `${fmt(ng)} ng`, `${fmt(ul)} µL`];
        });
        const rxn = n(v, 'rxn') || 20;
        const out = { lines: [L(t('DNA in total'), `${fmt(total)} µL`, true), L(t('2× master mix'), `${fmt(rxn / 2)} µL`), L(t('Water'), total <= rxn / 2 ? `${fmt(rxn / 2 - total)} µL` : '—')],
          table: { head: [t('Fragment'), t('Amount'), t('Mass'), t('Volume')], rows }, warnings: [], notes: [] };
        if (total > rxn / 2) out.warnings.push(t('The DNA is more than half the reaction (%(half)s µL): concentrate it or lower the amounts.', { half: fmt(rxn / 2) }));
        // NEB's ranges for HiFi: 0.03–0.2 pmol in all for 2–3 fragments,
        // 0.2–0.5 pmol for 4–6.
        const pmol = frags.reduce((a, f, i) => a + (n(v, 'vpmol') || 0.05) * (i ? n(v, 'ratio') || 2 : 1), 0);
        const most = frags.length <= 3 ? 0.2 : 0.5;
        if (pmol > most) out.warnings.push(t('%(pmol)s pmol of DNA in all: more than %(most)s pmol for %(n)s fragments lowers the yield.', { pmol: fmt(pmol, 3), most: fmt(most), n: frags.length }));
        frags.forEach((f, i) => {
          if (f.nums[0] < 200) out.notes.push(t('%(name)s is under 200 bp: use it at 5× the vector.', { name: f.name || t('Fragment %(n)s', { n: i + 1 }) }));
          const ul = (n(v, 'vpmol') || 0.05) * (i ? n(v, 'ratio') || 2 : 1) * 1e-12 * (f.nums[0] * 617.96 + 36.04) * 1e9 / f.nums[1];
          if (ul < 0.5) out.warnings.push(t('%(what)s: under 0.5 µL is hard to pipette; dilute it 1:10 first.', { what: f.name || t('Fragment %(n)s', { n: i + 1 }) }));
        });
        return out;
      },
    },
    {
      id: 'pcrmix',
      inputs: [
        { key: 'n', label: t('Reactions'), value: '12' }, { key: 'extra', label: t('Extra (%)'), value: '10' },
        { key: 'list', label: t('Per reaction'), type: 'textarea', value: '2× master mix 10\nForward primer 10 µM 0.8\nReverse primer 10 µM 0.8\nWater 6.4',
          hint: t('One component a line, then its µL per reaction.') },
        { key: 'template', label: t('Template per reaction (µL)'), value: '2' },
      ],
      compute(v) {
        const k = n(v, 'n') * (1 + (n(v, 'extra') || 0) / 100);
        const comps = lines(v.raw.list).map(row).filter((r) => r.nums.length);
        if (!ok(k) || !comps.length) return { hint: t('How many reactions and what goes in each.') };
        let per = 0;
        const rows = comps.map((c) => { const u = c.nums[c.nums.length - 1]; per += u; return [c.name, fmt(u), fmt(u * k)]; });
        rows.push([t('Mix'), fmt(per), fmt(per * k)]);
        return { lines: [L(t('Mix into each tube'), `${fmt(per)} µL`, true), L(t('Then template'), `${fmt(n(v, 'template') || 0)} µL`), L(t('Each reaction'), `${fmt(per + (n(v, 'template') || 0))} µL`)],
          table: { head: [t('Component'), t('µL / reaction'), t('µL for %(n)s + %(extra)s %', { n: fmt(n(v, 'n')), extra: fmt(n(v, 'extra') || 0) })], rows } };
      },
    },
    {
      id: 'qpcreff',
      inputs: [
        { key: 'list', label: t('Standards'), type: 'textarea', value: '100000 17.1\n10000 20.5\n1000 23.9\n100 27.3\n10 30.7',
          hint: t('One standard a line: its amount (any unit, or copies), then its Ct.') },
        { key: 'slope', label: t('…or the slope') },
      ],
      compute(v) {
        let slope = n(v, 'slope'); let fit = null;
        const pts = lines(v.raw.list).map(row).filter((r) => r.nums.length >= 2 && r.nums[0] > 0);
        if (!ok(slope) && pts.length >= 3) { fit = linfit(pts.map((p) => Math.log10(p.nums[0])), pts.map((p) => p.nums[1])); slope = fit.slope; }
        if (!ok(slope)) return { hint: t('Three or more standards, or the slope.') };
        const e = 10 ** (-1 / slope);
        const out = { lines: [L(t('Slope'), fmt(slope, 4)), L(t('Efficiency'), `${fmt((e - 1) * 100, 3)} %`, true), L(t('Amplification factor'), fmt(e, 3))], notes: [], warnings: [] };
        if (fit) out.lines.push(L('R²', fmt(fit.r2, 4)));
        if (e - 1 < 0.9 || e - 1 > 1.1) out.warnings.push(t('Outside 90–110 %: check the dilutions, inhibitors or primer design.'));
        return out;
      },
    },
    {
      id: 'ddct',
      inputs: [
        { key: 'tc', label: t('Target, control'), value: '25.0' }, { key: 'rc', label: t('Reference, control'), value: '18.1' },
        { key: 'tt', label: t('Target, treated'), value: '22.1' }, { key: 'rt', label: t('Reference, treated'), value: '18.0' },
        { key: 'et', label: t('Target efficiency (%)'), value: '100' }, { key: 'er', label: t('Reference efficiency (%)'), value: '100' },
      ],
      compute(v) {
        const [tc, rc, tt, rt] = ['tc', 'rc', 'tt', 'rt'].map((k) => n(v, k));
        if (![tc, rc, tt, rt].every(ok)) return { hint: t('All four Ct values.') };
        const ddct = (tt - rt) - (tc - rc);
        const et = 1 + (n(v, 'et') || 100) / 100; const er = 1 + (n(v, 'er') || 100) / 100;
        const pfaffl = et ** (tc - tt) / er ** (rc - rt);
        const out = { lines: [L(t('ΔCt control'), fmt(tc - rc, 4)), L(t('ΔCt treated'), fmt(tt - rt, 4)), L('ΔΔCt', fmt(ddct, 4)),
          L(t('Fold change (2^−ΔΔCt)'), fmt(2 ** -ddct, 4), true)], warnings: [] };
        if (et !== 2 || er !== 2) out.lines.push(L(t('Fold change, with efficiencies (Pfaffl)'), fmt(pfaffl, 4), true));
        if ([tc, rc, tt, rt].some((c) => c > 35)) out.warnings.push(t('A Ct above 35 is near the detection limit: the fold change is unreliable.'));
        return out;
      },
    },

    {
      id: 'ddcttable',
      inputs: [
        { key: 'list', label: t('Ct values'), type: 'textarea', value: 'Control GAPDH 18.1\nControl GAPDH 18.2\nControl GAPDH 18.0\nControl MYC 25.0\nControl MYC 25.2\nControl MYC 24.9\nTreated GAPDH 18.0\nTreated GAPDH 18.1\nTreated GAPDH 17.9\nTreated MYC 22.1\nTreated MYC 22.3\nTreated MYC 22.0',
          hint: t('One well a line: sample, gene, Ct. Paste from a spreadsheet (tabs) when a sample name has spaces.') },
        { key: 'control', label: t('Control sample (blank: the first)') },
        { key: 'ref', label: t('Reference gene (blank: the first)') },
      ],
      compute(v) {
        // A line is sample, gene, Ct: by tabs when pasted from a sheet, else
        // the last word before the Ct is the gene and the rest the sample.
        const wells = lines(v.raw.list).map((line) => {
          const cells = line.includes('\t') ? line.split('\t').map((c) => c.trim()).filter(Boolean) : line.split(/\s+/);
          const ct = num(cells.pop());
          const gene = cells.pop();
          return { sample: cells.join(' '), gene, ct };
        }).filter((w) => w.sample && w.gene && (ok(w.ct) || /undet|^n\/?a$/i.test(String(w.ct))));
        const used = wells.filter((w) => ok(w.ct));
        if (!used.length) return { hint: t('Sample, gene and Ct, one well a line.') };
        const samples = [...new Set(used.map((w) => w.sample))];
        const genes = [...new Set(used.map((w) => w.gene))];
        const control = samples.find((x) => x.toLowerCase() === String(v.raw.control || '').trim().toLowerCase()) || samples[0];
        const ref = genes.find((x) => x.toLowerCase() === String(v.raw.ref || '').trim().toLowerCase()) || genes[0];
        if (genes.length < 2) return { hint: t('A target gene and a reference gene.') };
        const stat = (sample, gene) => {
          const cts = used.filter((w) => w.sample === sample && w.gene === gene).map((w) => w.ct);
          if (!cts.length) return null;
          const m = mean(cts);
          const sd = cts.length > 1 ? Math.sqrt(cts.reduce((a, c) => a + (c - m) ** 2, 0) / (cts.length - 1)) : 0;
          return { m, sd, n: cts.length, spread: Math.max(...cts) - Math.min(...cts) };
        };
        const warnings = [];
        const flagged = new Set();
        samples.forEach((sm) => genes.forEach((g) => {
          const st = stat(sm, g);
          if (st && st.spread > 0.5) flagged.add(t('%(sample)s %(gene)s: replicates %(spread)s cycles apart.', { sample: sm, gene: g, spread: fmt(st.spread, 2) }));
          if (st && st.m > 35) flagged.add(t('%(sample)s %(gene)s: mean Ct %(ct)s, near the detection limit.', { sample: sm, gene: g, ct: fmt(st.m, 3) }));
        }));
        warnings.push(...flagged);
        const rows = []; const lines_ = [];
        genes.filter((g) => g !== ref).forEach((g) => {
          const c0 = stat(control, g); const r0 = stat(control, ref);
          if (!c0 || !r0) return;
          const dctControl = c0.m - r0.m;
          samples.forEach((sm) => {
            const tg = stat(sm, g); const rf = stat(sm, ref);
            if (!tg || !rf) return;
            const dct = tg.m - rf.m; const sd = Math.sqrt(tg.sd ** 2 + rf.sd ** 2);
            const ddct = dct - dctControl; const fold = 2 ** -ddct;
            rows.push([sm, g, fmt(dct, 3), fmt(ddct, 3), fmt(fold, 3), `${fmt(2 ** -(ddct + sd), 3)}–${fmt(2 ** -(ddct - sd), 3)}`]);
            if (sm !== control) lines_.push(L(`${sm} · ${g}`, t('%(fold)s-fold', { fold: fmt(fold, 3) }), true));
          });
        });
        if (!rows.length) return { hint: t('The control sample needs the target and the reference gene.') };
        return { lines: lines_.slice(0, 6), table: { head: [t('Sample'), t('Gene'), 'ΔCt', 'ΔΔCt', t('Fold change'), t('Range (± SD)')], rows }, warnings,
          notes: [t('Relative to %(control)s, normalised to %(ref)s; 2^−ΔΔCt, replicates averaged.', { control, ref })] };
      },
    },

    /* ================================================= Protein */
    {
      id: 'a280',
      inputs: [
        { key: 'seq', label: t('Sequence (optional)'), type: 'textarea', placeholder: t('MKV… (FASTA is fine)') },
        { key: 'eps', label: 'ε₂₈₀ (M⁻¹cm⁻¹)' }, { key: 'mw', label: 'MW (g/mol)' },
        { key: 'a280', label: 'A₂₈₀' }, { key: 'path', label: t('Path (cm)'), value: '1' }, { key: 'dil', label: t('Dilution (×)'), value: '1' },
        { key: 'cys', label: t('Cysteines (for ε from the sequence)'), type: 'select', options: [['paired', t('Paired (no reducing agent)')], ['reduced', t('Reduced (DTT, TCEP, β-ME)')]] },
      ],
      compute(v) {
        const seq = cleanProtein(v.raw.seq);
        const p = seq ? protein(seq) : null;
        if (seq && !p) return { error: t('The sequence has letters that are not amino acids.') };
        const fromSeq = p && (v.raw.cys === 'reduced' ? p.reduced : p.epsilon);
        const eps = ok(n(v, 'eps')) ? n(v, 'eps') : fromSeq;
        const mw = ok(n(v, 'mw')) ? n(v, 'mw') : p && p.mw;
        const out = { lines: [], notes: [], warnings: [] };
        if (p) {
          out.notes.push(t('From the sequence: %(length)s aa, %(kda)s kDa, ε₂₈₀ %(eps)s (%(reduced)s reduced).',
            { length: p.length, kda: fmt(p.mw / 1000, 4), eps: fmt(p.epsilon), reduced: fmt(p.reduced) }));
          if (!ok(n(v, 'eps')) && p.epsilon !== p.reduced) {
            out.notes.push(v.raw.cys === 'reduced' ? t('Using the reduced ε.') : t('Using the ε with cysteines paired; pick Reduced for a buffer with DTT or TCEP.'));
          }
          // A typed ε or MW wins over the sequence's: say so, not leave the
          // sequence's figure looking like the one used.
          if (ok(n(v, 'eps'))) out.notes.push(t('Worked out with the ε you typed (%(eps)s), not the sequence’s.', { eps: fmt(n(v, 'eps')) }));
          if (ok(n(v, 'mw'))) out.notes.push(t('Worked out with the MW you typed (%(mw)s g/mol), not the sequence’s.', { mw: fmt(n(v, 'mw')) }));
        }
        if (eps === 0) { out.error = t('No Trp or Tyr: A₂₈₀ can\'t measure this protein; use BCA or A₂₀₅.'); return out; }
        if (!ok(eps) || !ok(n(v, 'a280'))) { out.hint = t('The reading, and ε (or the sequence).'); return out; }
        const molar = n(v, 'a280') / (eps * (n(v, 'path') || 1)) * (n(v, 'dil') || 1);
        out.lines.push(L(t('Concentration'), show(molar, 'molar'), true));
        if (ok(mw)) { out.lines.push(L(t('Concentration'), `${fmt(molar * mw)} mg/mL`, true)); out.lines.push(L(t('A₂₈₀ of 1 mg/mL'), fmt(eps / mw, 3))); }
        return out;
      },
    },
    {
      id: 'protparam',
      inputs: [{ key: 'seq', label: t('Sequence'), type: 'textarea', placeholder: t('One-letter code; a FASTA header is fine') }],
      compute(v) {
        const seq = cleanProtein(v.raw.seq);
        if (!seq) return { hint: t('A protein sequence.') };
        const p = protein(seq);
        if (!p) return { error: t('Letters that are not amino acids (B, J, O, U, X, Z) are in it.') };
        return { lines: [L(t('Length'), `${p.length} aa`), L('MW', `${fmt(p.mw)} g/mol (${fmt(p.mw / 1000, 4)} kDa)`, true),
          L(t('ε₂₈₀, Cys paired'), `${fmt(p.epsilon)} M⁻¹cm⁻¹`), L(t('ε₂₈₀, reduced'), `${fmt(p.reduced)} M⁻¹cm⁻¹`),
          L(t('A₂₈₀ of 1 mg/mL'), p.epsilon ? fmt(p.epsilon / p.mw, 3) : '—'), L(t('pI (approx.)'), fmt(p.pI, 3), true), L(t('Charge at pH 7'), fmt(p.charge7, 3))] };
      },
    },
    {
      id: 'stdcurve',
      inputs: [
        { key: 'std', label: t('Standards'), type: 'textarea', value: '0 0.10\n125 0.21\n250 0.33\n500 0.55\n1000 0.98\n2000 1.62',
          hint: t('One a line: concentration, then its reading (or several replicate readings).') },
        { key: 'unk', label: t('Unknowns'), type: 'textarea', value: 'Lysate A 0.72\nLysate B 0.64',
          hint: t('One a line: name, then its reading (or several).') },
        { key: 'fit', label: t('Fit'), type: 'select', options: [['quad', t('Quadratic (BCA bends)')], ['lin', t('Straight line')]] },
        { key: 'dil', label: t('Unknowns diluted (×)'), value: '1' },
        { key: 'unit', label: t('Standards in'), type: 'select', options: [['µg/mL', 'µg/mL'], ['mg/mL', 'mg/mL'], ['ng/mL', 'ng/mL'], ['pg/mL', 'pg/mL']] },
      ],
      compute(v) {
        // A standard is its concentration, then one reading or several
        // (replicates, averaged): "125 0.21 0.22 0.21".
        const reps = [];
        const std = lines(v.raw.std).map(row).filter((r) => r.nums.length >= 2).map((r) => {
          const readings = r.nums.slice(1);
          if (readings.length > 1) reps.push({ conc: r.nums[0], readings });
          return [r.nums[0], mean(readings)];
        });
        if (std.length < 3) return { hint: t('Three or more standards.') };
        const xs = std.map((s) => s[0]); const ys = std.map((s) => s[1]);
        const quad = v.raw.fit !== 'lin' && std.length >= 4;
        const f = quad ? quadfit(xs, ys) : linfit(xs, ys);
        // A curve term too small to matter over the standards (straight-line
        // standards fit with a rounding-noise x² of 10⁻²²) is none: shown as
        // nothing, and the reading is taken off the straight line.
        const xMax = Math.max(...xs.map(Math.abs)) || 1; const yMax = Math.max(...ys.map(Math.abs)) || 1;
        const none = (coef, power) => Math.abs(coef) * xMax ** power < 1e-9 * yMax;
        if (quad && none(f.c, 2)) f.c = 0;
        const inv = (y) => {
          if (!quad) return (y - f.intercept) / f.slope;
          const { a, b: bb, c } = f;
          if (c === 0) return (y - a) / bb;
          const disc = bb * bb - 4 * c * (a - y);
          if (disc < 0) return NaN;
          const roots = [(-bb + Math.sqrt(disc)) / (2 * c), (-bb - Math.sqrt(disc)) / (2 * c)];
          const lo = Math.min(...xs) - (Math.max(...xs) - Math.min(...xs)); const hi = Math.max(...xs) * 2;
          return roots.find((r) => r >= lo && r <= hi) ?? NaN;
        };
        const dil = n(v, 'dil') || 1;
        const top = Math.max(...ys); const bottom = Math.min(...ys);
        const warnings = [];
        // An unknown is its name, then one reading or several (averaged). A
        // whole number before the readings is part of the name ("Lysate 2
        // 0.72 0.70"): readings carry a decimal point.
        const rows = lines(v.raw.unk).map((line) => {
          const parts = line.split(/[\t;]+|\s+/).filter(Boolean);
          const readings = [];
          while (parts.length && ok(num(parts[parts.length - 1])) && /[.,]/.test(parts[parts.length - 1])) readings.unshift(num(parts.pop()));
          if (!readings.length && parts.length && ok(num(parts[parts.length - 1]))) readings.push(num(parts.pop()));
          if (readings.length > 1) reps.push({ conc: null, name: parts.join(' '), readings });
          return { name: parts.join(' '), readings };
        }).filter((r) => r.readings.length).map((r) => {
          const y = mean(r.readings);
          if (y > top || y < bottom) warnings.push(t('%(name)s is outside the standards: dilute it and read again.', { name: r.name || fmt(y) }));
          return dil === 1 ? [r.name || '—', fmt(y), fmt(inv(y))] : [r.name || '—', fmt(y), fmt(inv(y)), fmt(inv(y) * dil)];
        });
        const unit = v.raw.unit || 'µg/mL';
        const term = (c, x) => (c === 0 || none(c, x === '' ? 0 : x === '·x' ? 1 : 2) ? '' : `${c < 0 ? ' − ' : ' + '}${fmt(Math.abs(c))}${x}`);
        const eq = quad ? `y = ${fmt(f.a)}${term(f.b, '·x')}${term(f.c, '·x²')}` : `y = ${fmt(f.slope)}·x${term(f.intercept, '')}`;
        reps.forEach((r) => {
          const m = mean(r.readings);
          if (m && (Math.max(...r.readings) - Math.min(...r.readings)) / Math.abs(m) > 0.1) {
            warnings.push(t('%(which)s: its readings differ by more than 10 %; check that well.', { which: r.conc == null ? (r.name || fmt(m)) : fmt(r.conc) }));
          }
        });
        const notes = reps.length ? [t('Replicate readings are averaged.')] : [];
        const mains = rows.slice(0, 8).map((r) => L(r[0], `${r[r.length - 1]} ${unit}`, true));
        if (f.r2 < 0.98) warnings.push(t('R² %(r2)s: a standard is off; check it or leave it out.', { r2: fmt(f.r2, 3) }));
        return { lines: [...mains, L(t('Fit'), eq), L('R²', fmt(f.r2, 4))],
          table: rows.length ? { head: dil === 1 ? [t('Sample'), t('Reading'), unit] : [t('Sample'), t('Reading'), t('On the curve'), t('In the sample (× %(dil)s)', { dil: fmt(dil) })], rows } : null, warnings, notes };
      },
    },
    {
      id: 'sdspage',
      inputs: [
        { key: 'pct', label: t('Resolving gel (%)'), value: '12' }, { key: 'gels', label: t('Gels'), value: '2' },
        { key: 'res', label: t('Resolving gel each (mL)'), value: '5' }, { key: 'stack', label: t('Stacking gel each (mL)'), value: '2' },
      ],
      compute(v) {
        const pct = n(v, 'pct'); const g = n(v, 'gels') || 1;
        const R = (n(v, 'res') || 5) * g; const S = (n(v, 'stack') || 2) * g;
        if (!(pct > 3 && pct <= 20)) return { hint: t('A resolving gel of 4–20 %.') };
        const acr = R * pct / 30; const tris = R / 4; const sds = R / 100; const aps = R / 100; const temed = R * 0.0004;
        const sAcr = S * 5 / 30; const sTris = S / 8; const sSds = S / 100; const sAps = S / 100; const sTemed = S * 0.001;
        const ml = (x) => (x < 0.1 ? `${fmt(x * 1000)} µL` : `${fmt(x)} mL`);
        return {
          table: { head: ['', t('Resolving %(pct)s % (%(ml)s mL)', { pct: fmt(pct), ml: fmt(R) }), t('Stacking 5 % (%(ml)s mL)', { ml: fmt(S) })], rows: [
            [t('Water'), ml(R - acr - tris - sds - aps - temed), ml(S - sAcr - sTris - sSds - sAps - sTemed)],
            [t('30 % acrylamide/bis'), ml(acr), ml(sAcr)],
            ['Tris', t('%(volume)s of 1.5 M pH 8.8', { volume: ml(tris) }), t('%(volume)s of 1.0 M pH 6.8', { volume: ml(sTris) })],
            ['10 % SDS', ml(sds), ml(sSds)], [t('10 % APS (fresh)'), ml(aps), ml(sAps)], ['TEMED', ml(temed), ml(sTemed)],
          ] },
          notes: [t('Add APS and TEMED last, just before pouring; overlay the resolving gel with isopropanol or water.'),
            t('Resolves about: 8 % 40–200 kDa, 10 % 20–150, 12 % 12–100, 15 % 10–60 kDa.')],
        };
      },
    },
    {
      id: 'loading',
      inputs: [
        { key: 'list', label: t('Samples'), type: 'textarea', value: 'WT 2.48\nKO 2.31\nKO + drug 1.95',
          hint: t('One a line: name, then µg/µL (from a BCA).') },
        { key: 'ug', label: t('µg per lane'), value: '30' }, { key: 'lane', label: t('Volume per lane (µL)'), value: '20' },
        { key: 'buf', label: t('Loading buffer (×)'), value: '4' },
      ],
      compute(v) {
        const samples = lines(v.raw.list).map(row).filter((r) => r.nums.length);
        const ug = n(v, 'ug'); const lane = n(v, 'lane'); const bx = n(v, 'buf') || 4;
        if (!samples.length || !ok(ug) || !ok(lane)) return { hint: t('Samples with µg/µL, µg per lane and lane volume.') };
        const buf = lane / bx; const warnings = [];
        const d1 = (x) => (Math.round(x * 10) / 10).toFixed(1);
        const rows = samples.map((s) => {
          const conc = s.nums[s.nums.length - 1];
          const sv = ug / conc; const water = lane - buf - sv;
          if (water < 0) {
            warnings.push(t('%(name)s: too dilute for %(ug)s µg in %(lane)s µL; it can give %(most)s µg.', { name: s.name, ug: fmt(ug), lane: fmt(lane), most: fmt((lane - buf) * conc, 3) }));
            return [s.name, d1(lane - buf), d1(buf), '0.0'];
          }
          return [s.name, d1(sv), d1(buf), d1(water)];
        });
        return { table: { head: [t('Sample'), t('Sample µL'), t('%(x)s× buffer µL', { x: fmt(bx) }), t('Water µL')], rows }, warnings };
      },
    },

    {
      id: 'normalise',
      inputs: [
        { key: 'list', label: t('Samples'), type: 'textarea', value: 'WT-1 412\nWT-2 388\nKO-1 256\nKO-2 97',
          hint: t('One a line: name, then ng/µL.') },
        { key: 'ng', label: t('Amount in each (ng)'), value: '1000' },
        { key: 'vol', label: t('Volume of each (µL)'), value: '10' },
      ],
      compute(v) {
        const samples = lines(v.raw.list).map(row).filter((r) => r.nums.length);
        const want = n(v, 'ng'); const vol = n(v, 'vol');
        if (!samples.length || !ok(want) || !ok(vol)) return { hint: t('Samples with ng/µL, the amount and the volume.') };
        const d1 = (x) => (Math.round(x * 10) / 10).toFixed(1);
        const warnings = [];
        const rows = samples.map((s) => {
          const conc = s.nums[s.nums.length - 1]; const sv = want / conc;
          if (sv > vol) {
            warnings.push(t('%(name)s: too dilute for %(ng)s ng in %(vol)s µL; it can give %(most)s ng.', { name: s.name, ng: fmt(want), vol: fmt(vol), most: fmt(conc * vol, 3) }));
            return [s.name, d1(vol), '0.0'];
          }
          if (sv < 0.5) warnings.push(t('%(what)s: under 0.5 µL is hard to pipette; dilute it 1:10 first.', { what: s.name }));
          return [s.name, d1(sv), d1(vol - sv)];
        });
        return { table: { head: [t('Sample'), t('Sample µL'), t('Water µL')], rows }, warnings,
          notes: [t('The lowest sample sets the most you can use: %(most)s ng in %(vol)s µL.', { most: fmt(Math.min(...samples.map((s) => s.nums[s.nums.length - 1])) * vol, 3), vol: fmt(vol) })] };
      },
    },
    {
      id: 'concentrate',
      inputs: [
        { key: 'now', label: t('Concentration now (mg/mL)'), value: '1.2' },
        { key: 'vol', label: t('Volume now'), units: 'volume', unit: 'mL', value: '10' },
        { key: 'want', label: t('Wanted (mg/mL)'), value: '5' },
        { key: 'recovery', label: t('Expected recovery (%)'), value: '90' },
      ],
      compute(v) {
        const mg = n(v, 'now') * b(v, 'vol') * 1000;
        const rec = (n(v, 'recovery') || 100) / 100;
        const to = mg * rec / n(v, 'want') / 1000;               // L
        if (!ok(to)) return { hint: t('Concentration and volume now, and the concentration wanted.') };
        if (n(v, 'want') <= n(v, 'now')) return { error: t('Already at or above that: dilute instead.') };
        return { lines: [L(t('Spin down to'), show(to, 'volume'), true), L(t('Protein now'), `${fmt(mg)} mg`), L(t('Expected after'), `${fmt(mg * rec)} mg`)],
          notes: [t('Stop a little above the volume, mix, and measure (A₂₈₀) before going further; above about 10 mg/mL many proteins aggregate.')] };
      },
    },
    {
      id: 'dialysis',
      inputs: [
        { key: 'sample', label: t('Sample'), units: 'volume', unit: 'mL', value: '5' },
        { key: 'buffer', label: t('Buffer each change'), units: 'volume', unit: 'mL', value: '1000' },
        { key: 'changes', label: t('Changes'), value: '2' },
        { key: 'start', label: t('What to remove, now (e.g. 250 mM imidazole)'), value: '250' },
      ],
      compute(v) {
        const s0 = b(v, 'sample'); const buf = b(v, 'buffer'); const k = Math.round(n(v, 'changes')); const c0 = n(v, 'start');
        if (!ok(s0) || !ok(buf) || !(k >= 1 && k <= 10) || !ok(c0)) return { hint: t('Sample and buffer volumes, the changes and the starting amount.') };
        const f = s0 / (s0 + buf);
        const rows = []; let c = c0;
        for (let i = 1; i <= k; i += 1) { c *= f; rows.push([t('After change %(n)s', { n: i }), fmt(c, 3)]); }
        return { lines: [L(t('Left at the end'), fmt(c, 3), true), L(t('Each change dilutes'), `1 : ${fmt(1 / f, 4)}`)],
          table: { head: ['', t('Left (same unit)')], rows },
          notes: [t('At equilibrium: give each change 2–4 h, the last overnight, with the buffer stirring.')] };
      },
    },

    /* ================================================= Cells */
    {
      id: 'count',
      inputs: [
        { key: 'live', label: t('Live cells counted'), value: '212' }, { key: 'dead', label: t('Dead (blue) counted'), value: '14' },
        { key: 'squares', label: t('Large squares counted'), value: '4' }, { key: 'dil', label: t('Dilution (trypan 1:1 = 2)'), value: '2' },
        { key: 'volume', label: t('Suspension volume (optional)'), units: 'volume', unit: 'mL' },
      ],
      compute(v) {
        const per = (x) => x / n(v, 'squares') * (n(v, 'dil') || 1) * 1e4;
        const live = per(n(v, 'live')); const dead = ok(n(v, 'dead')) ? per(n(v, 'dead')) : 0;
        if (!ok(live)) return { hint: t('Live cells and squares counted.') };
        const out = { lines: [L(t('Live cells'), `${fmt(live, 3)} /mL`, true), L(t('Viability'), `${fmt(live / (live + dead) * 100, 3)} %`)], warnings: [] };
        if (ok(b(v, 'volume'))) out.lines.push(L(t('In all'), t('%(n)s live cells', { n: fmt(live * b(v, 'volume') * 1000, 3) })));
        if (n(v, 'live') / n(v, 'squares') < 20 || n(v, 'live') / n(v, 'squares') > 200) out.warnings.push(t('Aim for 20–200 cells a square for an accurate count.'));
        return out;
      },
    },
    {
      id: 'seeding',
      inputs: [
        { key: 'susp', label: t('Suspension (cells/mL)'), value: '1.2e6' },
        { key: 'vessel', label: t('Vessel'), type: 'select', options: opt(VESSELS), value: '4' },
        { key: 'wells', label: t('Wells or flasks'), value: '6' },
        { key: 'per', label: t('Cells per well'), value: '3e5' }, { key: 'percm', label: t('…or cells per cm²') },
        { key: 'vol', label: t('Medium per well (mL, blank: usual)') }, { key: 'extra', label: t('Extra (%)'), value: '10' },
      ],
      compute(v) {
        const vessel = VESSELS[Number(v.raw.vessel || 4)];
        const per = ok(n(v, 'percm')) ? n(v, 'percm') * vessel[1] : n(v, 'per');
        const wells = n(v, 'wells') * (1 + (n(v, 'extra') || 0) / 100);
        const vol = ok(n(v, 'vol')) ? n(v, 'vol') : vessel[2];
        const cells = per * wells; const susp = cells / n(v, 'susp');
        if (!ok(susp)) return { hint: t('Suspension density, cells per well and wells.') };
        const total = vol * wells;
        const ml = (x) => show(x * 1e-3, 'volume');
        const out = { lines: [L(t('Cell suspension'), ml(susp), true)], warnings: [] };
        if (susp > total) {
          // No "−0.99 mL medium": say what would work instead.
          out.warnings.push(t('The suspension alone (%(susp)s) is more than the wells hold (%(total)s): spin the cells down and resuspend at %(density)s cells/mL or more.',
            { susp: ml(susp), total: ml(total), density: fmt(cells / total, 3) }));
        } else {
          out.lines.push(L(t('Medium'), ml(total - susp), true), L(t('Together'), t('%(total)s, %(each)s a well', { total: ml(total), each: ml(vol) })));
        }
        out.lines.push(L(t('Cells needed'), fmt(cells, 3)), L(t('Density'), `${fmt(per / vessel[1], 3)} cells/cm²`));
        return out;
      },
    },
    {
      id: 'doubling',
      inputs: [
        { key: 'n0', label: t('First count'), value: '2e5' }, { key: 'n1', label: t('Second count'), value: '1.6e6' },
        { key: 't', label: t('Time between'), units: 'time', unit: 'h', value: '72' },
        { key: 'later', label: t('Cells this long after the second count (optional)'), units: 'time', unit: 'h' },
      ],
      compute(v) {
        const g = Math.log(n(v, 'n1') / n(v, 'n0')) / b(v, 't');         // per second
        if (!ok(g) || g <= 0) return { hint: t('Two counts (the second higher) and the time.') };
        const out = { lines: [L(t('Doubling time'), `${fmt(Math.LN2 / g / 3600, 3)} h`, true), L(t('Growth rate'), `${fmt(g * 3600, 3)} /h`)] };
        if (ok(b(v, 'later'))) out.lines.push(L(t('Cells then'), fmt(n(v, 'n1') * Math.exp(g * b(v, 'later')), 3), true));
        return out;
      },
    },
    {
      id: 'transfection',
      inputs: [
        { key: 'from', label: t('Works in'), type: 'select', options: opt(VESSELS), value: '4' },
        { key: 'dna', label: t('DNA there (µg)'), value: '2.5' }, { key: 'reagent', label: t('Reagent there (µL)'), value: '7.5' },
        { key: 'medium', label: t('Dilution medium there (µL)'), value: '250' },
        { key: 'to', label: t('Scale to'), type: 'select', options: opt(VESSELS), value: '2' },
        { key: 'n', label: t('How many'), value: '12' }, { key: 'extra', label: t('Extra (%)'), value: '10' },
      ],
      compute(v) {
        const a = VESSELS[Number(v.raw.from || 0)]; const to = VESSELS[Number(v.raw.to || 0)];
        const k = to[1] / a[1]; const many = (n(v, 'n') || 1) * (1 + (n(v, 'extra') || 0) / 100);
        if (!ok(n(v, 'dna'))) return { hint: t('The amounts that work.') };
        return { table: { head: ['', t('One %(vessel)s', { vessel: t(to[0]) }), t('For %(n)s', { n: fmt(many, 3) })], rows: [
          ['DNA', `${fmt(n(v, 'dna') * k)} µg`, `${fmt(n(v, 'dna') * k * many)} µg`],
          [t('Reagent'), `${fmt(n(v, 'reagent') * k)} µL`, `${fmt(n(v, 'reagent') * k * many)} µL`],
          [t('Dilution medium'), `${fmt(n(v, 'medium') * k)} µL`, `${fmt(n(v, 'medium') * k * many)} µL`]] },
          notes: [t('%(to)s has %(k)s× the area of %(from)s. Keep the DNA : reagent ratio (%(ratio)s µL per µg).',
            { to: t(to[0]), k: fmt(k, 3), from: t(a[0]), ratio: fmt(n(v, 'reagent') / n(v, 'dna'), 3) })] };
      },
    },
    {
      id: 'split',
      inputs: [
        { key: 'susp', label: t('Suspension (cells/mL)'), value: '1e6' },
        { key: 'vessel', label: t('Into'), type: 'select', options: opt(VESSELS), value: '10' },
        { key: 'n', label: t('How many'), value: '1' },
        { key: 'confl', label: t('Wanted confluence (%)'), value: '80' },
        { key: 'days', label: t('In (days)'), value: '3' },
        { key: 'dt', label: t('Doubling time (h)'), value: '24' },
        { key: 'full', label: t('Cells/cm² when confluent'), value: '1e5', hint: t('About 1 × 10⁵ for many lines; more for HEK293, fewer for fibroblasts.') },
        { key: 'now', label: t('Suspension you have (mL, optional)') },
      ],
      compute(v) {
        const vessel = VESSELS[Number(v.raw.vessel || 10)];
        const target = n(v, 'full') * vessel[1] * n(v, 'confl') / 100;
        // Seeded cells settle for about a day before they grow.
        const hours = Math.max(0, n(v, 'days') * 24 - 24);
        const seed = target / 2 ** (hours / n(v, 'dt'));
        const susp = seed / n(v, 'susp');                      // mL a vessel
        if (!ok(susp) || !(n(v, 'dt') > 0)) return { hint: t('Suspension density, the day and the doubling time.') };
        const each = vessel[2]; const many = n(v, 'n') || 1;
        const out = { lines: [L(t('Cells to seed'), t('%(n)s per vessel', { n: fmt(seed, 3) }), true),
          L(t('Suspension'), t('%(volume)s per vessel', { volume: show(susp * 1e-3, 'volume') }), true),
          L(t('Medium'), t('%(volume)s per vessel', { volume: show(Math.max(0, each - susp) * 1e-3, 'volume') }))], warnings: [], notes: [] };
        if (many > 1) out.lines.push(L(t('For %(n)s', { n: fmt(many) }), t('%(susp)s suspension, %(medium)s medium', { susp: show(susp * many * 1e-3, 'volume'), medium: show(Math.max(0, each - susp) * many * 1e-3, 'volume') })));
        if (ok(n(v, 'now'))) out.lines.push(L(t('Split ratio'), `1 : ${fmt(n(v, 'now') / (susp * many), 3)}`));
        if (susp > each) out.warnings.push(t('More suspension than the vessel holds: spin the cells down and resuspend in less.'));
        out.notes.push(t('Allows a day for the cells to settle before they grow; growth slows as they near confluence.'));
        return out;
      },
    },
    {
      id: 'moi',
      inputs: [
        { key: 'cells', label: t('Cells at infection'), value: '2e5' }, { key: 'moi', label: 'MOI', value: '5' },
        { key: 'titer', label: t('Titer (TU, PFU or vg per mL)'), value: '1e8' }, { key: 'wells', label: t('Wells'), value: '1' },
      ],
      compute(v) {
        const vol = n(v, 'cells') * n(v, 'moi') / n(v, 'titer') * 1e-3;   // L
        if (!ok(vol)) return { hint: t('Cells, MOI and titer.') };
        const out = { lines: [L(t('Virus per well'), show(vol, 'volume'), true), L(t('Infectious units'), fmt(n(v, 'cells') * n(v, 'moi'), 3))], warnings: [] };
        if ((n(v, 'wells') || 1) > 1) out.lines.push(L(t('For %(n)s wells', { n: fmt(n(v, 'wells')) }), show(vol * n(v, 'wells'), 'volume')));
        if (vol < 1e-6) out.warnings.push(t('Under 1 µL: dilute the virus first (e.g. 1:10 in medium).'));
        out.notes = [t('At MOI %(moi)s, about %(pct)s % of cells get at least one (Poisson).', { moi: fmt(n(v, 'moi')), pct: fmt((1 - Math.exp(-n(v, 'moi'))) * 100, 3) })];
        return out;
      },
    },
    {
      id: 'titer',
      inputs: [
        { key: 'cells', label: t('Cells at transduction'), value: '1e5' },
        { key: 'list', label: t('Wells'), type: 'textarea', value: '10 58\n1 12\n0.1 1.4',
          hint: t('One well a line: µL of the virus stock added, then % positive.') },
      ],
      compute(v) {
        const wells = lines(v.raw.list).map(row).filter((r) => r.nums.length >= 2).map((r) => ({ ul: r.nums[r.nums.length - 2], pos: r.nums[r.nums.length - 1] }));
        const cells = n(v, 'cells');
        if (!ok(cells) || !wells.length) return { hint: t('Cells, and µL and % positive for each well.') };
        const titer = (w) => cells * w.pos / 100 / (w.ul * 1e-3);
        // Only wells with 1–20 % positive: above that many cells carry two
        // copies, below it the count is noise.
        const good = wells.filter((w) => w.pos >= 1 && w.pos <= 20);
        const use = good.length ? good : [wells.reduce((a, w) => (Math.abs(w.pos - 10) < Math.abs(a.pos - 10) ? w : a))];
        const tu = mean(use.map(titer));
        const out = { lines: [L(t('Titer'), `${fmt(tu, 3)} TU/mL`, true)], warnings: [],
          table: { head: [t('µL'), t('Positive'), 'TU/mL', ''], rows: wells.map((w) => [fmt(w.ul), `${fmt(w.pos)} %`, fmt(titer(w), 3), use.includes(w) ? t('used') : '']) } };
        if (!good.length) out.warnings.push(t('No well had 1–20 % positive: the titer is under- or over-estimated.'));
        return out;
      },
    },
    {
      id: 'freezing',
      inputs: [
        { key: 'vials', label: t('Vials'), value: '6' }, { key: 'per', label: t('Cells per vial'), value: '2e6' },
        { key: 'vol', label: t('Per vial (mL)'), value: '1' }, { key: 'dmso', label: t('DMSO (%)'), value: '10' },
        { key: 'susp', label: t('Your suspension (cells/mL, optional)') },
      ],
      compute(v) {
        const vials = n(v, 'vials'); const total = vials * n(v, 'vol');
        if (!ok(total)) return { hint: t('Vials and volume per vial.') };
        const dmso = total * n(v, 'dmso') / 100;
        const out = { lines: [L(t('Cells needed'), fmt(vials * n(v, 'per'), 3), true), L(t('Freezing medium'), `${fmt(total)} mL`, true),
          L('DMSO', `${fmt(dmso)} mL`), L(t('Medium or FBS'), `${fmt(total - dmso)} mL`)] };
        if (ok(n(v, 'susp'))) out.lines.push(L(t('Suspension to spin down'), `${fmt(vials * n(v, 'per') / n(v, 'susp'))} mL`, true));
        out.notes = [t('Cool at about −1 °C/min (an isopropanol box at −80 °C), then move to LN₂ within a day or two.')];
        return out;
      },
    },
    {
      id: 'treat',
      inputs: [
        { key: 'stock', label: t('Stock solution'), units: 'molar', unit: 'mM', value: '10' },
        { key: 'final', label: t('Final'), units: 'molar', unit: 'µM', value: '10' },
        { key: 'well', label: t('Medium per well'), units: 'volume', unit: 'mL', value: '2' },
        { key: 'wells', label: t('Wells'), value: '6' },
      ],
      compute(v) {
        const add = b(v, 'final') * b(v, 'well') / b(v, 'stock');
        if (!ok(add)) return { hint: t('Stock, final and medium volume.') };
        const pct = add / b(v, 'well') * 100;
        const out = { lines: [L(t('Stock per well'), show(add, 'volume'), true), L(t('For all wells'), show(add * (n(v, 'wells') || 1), 'volume')),
          L(t('Vehicle in the medium'), `${fmt(pct, 3)} %`)], warnings: [] };
        if (pct > 0.1) out.warnings.push(t('Over 0.1 % vehicle: many cells notice DMSO above that. Use a stronger stock.'));
        if (add < 0.5e-6) {
          const k = 10 ** Math.ceil(Math.log10(1e-6 / add));
          out.warnings.push(t('Under 0.5 µL per well: dilute the stock 1:%(k)s in medium first, then add %(volume)s of that to each well.', { k: fmt(k), volume: show(add * k, 'volume') }));
        }
        out.notes = [t('Give the control wells the same volume of vehicle alone.')];
        return out;
      },
    },

    {
      id: 'doseseries',
      inputs: [
        { key: 'top', label: t('Top dose in the well'), units: 'molar', unit: 'µM', value: '10' },
        { key: 'fold', label: t('Fold per step'), value: '3' }, { key: 'points', label: t('Doses'), value: '8' },
        { key: 'stock', label: t('Stock in DMSO'), units: 'molar', unit: 'mM', value: '10' },
        { key: 'well', label: t('Final volume per well (µL)'), value: '200' },
        { key: 'dmso', label: t('DMSO in every well (%)'), value: '0.1' },
        { key: 'tube', label: t('DMSO per tube in the series (µL)'), value: '20' },
      ],
      compute(v) {
        // The series is made in DMSO at F× the well, so every dose reaches the
        // cells with the same DMSO; F is 100 / DMSO %.
        const F = 100 / n(v, 'dmso'); const f = n(v, 'fold'); const k = Math.round(n(v, 'points'));
        const topDmso = b(v, 'top') * F; const well = n(v, 'well'); const tube = n(v, 'tube') || 20;
        if (!ok(F) || !(f > 1) || !(k >= 2 && k <= 24) || !ok(topDmso) || !ok(well) || !ok(b(v, 'stock'))) return { hint: t('Top dose, fold, doses, stock, well volume and DMSO %.') };
        if (topDmso > b(v, 'stock') * (1 + 1e-9)) {
          return { error: t('The top dose needs %(need)s in DMSO, stronger than the stock: allow more DMSO or lower the top dose.', { need: show(topDmso, 'molar') }) };
        }
        const carry = tube / (f - 1);
        const first = topDmso / b(v, 'stock') * (tube + carry);   // µL of stock in the first tube
        const rows = [];
        for (let i = 0; i < k; i += 1) rows.push([String(i + 1), show(b(v, 'top') / f ** i, 'molar'), show(topDmso / f ** i, 'molar')]);
        rows.push([t('Vehicle'), '0', t('DMSO alone')]);
        const direct = well / F;
        const dmsoFirst = tube + carry - first;
        const out = { lines: [L(t('First tube'), dmsoFirst > 1e-6 ? t('%(stock)s µL of stock + %(dmso)s µL DMSO', { stock: fmt(first), dmso: fmt(dmsoFirst) })
          : t('%(stock)s µL of the stock as it is', { stock: fmt(first) }), true),
          L(t('Then each tube'), t('%(dmso)s µL DMSO, carry %(carry)s µL', { dmso: fmt(tube), carry: fmt(carry) }), true)],
          table: { head: ['', t('In the well'), t('In DMSO')], rows }, notes: [] };
        if (direct >= 1) {
          out.lines.push(L(t('Into each well'), t('%(add)s µL to %(medium)s µL medium', { add: fmt(direct), medium: fmt(well - direct) }), true));
        } else {
          out.lines.push(L(t('Into each well'), t('Each 1:%(k)s in medium, then %(add)s µL of that to %(medium)s µL', { k: fmt(F / 10), add: fmt(well / 10), medium: fmt(well - well / 10) }), true));
        }
        out.notes.push(t('Every well gets %(pct)s % DMSO; treat the vehicle wells the same way.', { pct: fmt(n(v, 'dmso')) }));
        return out;
      },
    },
    {
      id: 'lentipack',
      inputs: [
        { key: 'vessel', label: t('Vessel'), type: 'select', options: opt(VESSELS), value: '7' },
        { key: 'n', label: t('How many'), value: '2' },
        { key: 'dna', label: t('DNA in all, per 10 cm dish (µg)'), value: '10' },
        { key: 'ratio', label: t('Transfer : packaging : envelope, by mass'), value: '4:3:1' },
        { key: 'pei', label: t('PEI (µg per µg DNA)'), value: '3', hint: t('As µL of a 1 mg/mL PEI solution.') },
        { key: 'opti', label: t('Opti-MEM per 10 cm dish (µL)'), value: '1000' },
        { key: 'extra', label: t('Extra (%)'), value: '10' },
      ],
      compute(v) {
        const vessel = VESSELS[Number(v.raw.vessel || 7)];
        const k = vessel[1] / 55;                       // a 10 cm dish is 55 cm²
        const parts = String(v.raw.ratio || '').split(/[:\s,/]+/).map(num).filter((x) => x > 0);
        if (parts.length !== 3 || !ok(n(v, 'dna'))) return { hint: t('The DNA, and three parts for the ratio (4:3:1).') };
        const sum = parts[0] + parts[1] + parts[2];
        const dna = n(v, 'dna') * k; const many = (n(v, 'n') || 1) * (1 + (n(v, 'extra') || 0) / 100);
        const each = parts.map((p) => dna * p / sum);
        const pei = dna * (n(v, 'pei') || 3); const opti = (n(v, 'opti') || 1000) * k;
        const row = (name, x, unit) => [name, `${fmt(x)} ${unit}`, `${fmt(x * many)} ${unit}`];
        return {
          lines: [L(t('DNA per vessel'), `${fmt(dna)} µg`, true), L(t('Medium to harvest'), t('%(volume)s per vessel', { volume: show(vessel[2] * 1e-3, 'volume') }))],
          table: { head: ['', t('One %(vessel)s', { vessel: t(vessel[0]) }), t('For %(n)s + %(extra)s %', { n: fmt(n(v, 'n') || 1), extra: fmt(n(v, 'extra') || 0) })], rows: [
            row(t('Transfer plasmid'), each[0], 'µg'), row(t('Packaging (psPAX2)'), each[1], 'µg'), row(t('Envelope (pMD2.G)'), each[2], 'µg'),
            row(t('PEI, 1 mg/mL'), pei, 'µL'), row('Opti-MEM', opti, 'µL')] },
          notes: [t('Mix the DNA into half the Opti-MEM and the PEI into the other half, combine, wait 15–20 min, add dropwise. Collect the medium at 48 and 72 h.')],
        };
      },
    },

    /* ================================================= Microbes */
    {
      id: 'od600',
      inputs: [
        { key: 'org', label: t('Organism'), type: 'select', options: [['8e8', t('E. coli (≈ 8 × 10⁸ per OD)')], ['3e7', t('Yeast (≈ 3 × 10⁷ per OD)')], ['custom', t('Other')]] },
        { key: 'factor', label: t('Cells per mL at OD 1 (Other)') },
        { key: 'od', label: t('OD₆₀₀ now'), value: '2.4' },
        { key: 'target', label: t('Start the new culture at OD'), value: '0.1' },
        { key: 'vol', label: t('New culture volume'), units: 'volume', unit: 'mL', value: '50' },
      ],
      compute(v) {
        const f = v.raw.org === 'custom' ? n(v, 'factor') : Number(v.raw.org || 8e8);
        const od = n(v, 'od');
        if (!ok(od)) return { hint: t('The OD₆₀₀.') };
        const out = { lines: [L(t('Cells'), `${fmt(od * f, 3)} /mL`, true)], warnings: [] };
        if (ok(n(v, 'target')) && ok(b(v, 'vol'))) {
          const add = b(v, 'vol') * n(v, 'target') / od;
          out.lines.push(L(t('Culture to add'), show(add, 'volume'), true), L(t('Fresh medium'), show(b(v, 'vol') - add, 'volume')));
        }
        if (od > 1) out.warnings.push(t('Above OD 1 a reading is no longer linear: dilute 1:10 to measure.'));
        return out;
      },
    },
    {
      id: 'growth',
      inputs: [
        { key: 'od', label: t('OD now'), value: '0.05' }, { key: 'target', label: t('Wanted OD'), value: '0.6' },
        { key: 'dt', label: t('Doubling time'), units: 'time', unit: 'min', value: '30' },
        { key: 'lag', label: t('Lag before growth'), units: 'time', unit: 'min', value: '0', hint: t('30–60 min after diluting back an overnight culture.') },
      ],
      compute(v) {
        const secs = Math.log2(n(v, 'target') / n(v, 'od')) * b(v, 'dt') + (ok(b(v, 'lag')) ? b(v, 'lag') : 0);
        if (!ok(secs) || secs < 0) return { hint: t('OD now, a higher wanted OD and the doubling time.') };
        return { lines: [L(t('Time'), `${fmt(secs / 60, 3)} min (${fmt(secs / 3600, 3)} h)`, true), L(t('Doublings'), fmt(Math.log2(n(v, 'target') / n(v, 'od')), 3))],
          notes: [t('E. coli in LB at 37 °C doubles in about 20–30 min; yeast in YPD at 30 °C in about 90 min.')] };
      },
    },
    {
      id: 'antibiotic',
      inputs: [
        { key: 'drug', label: t('Antibiotic'), type: 'select', options: opt(ANTIBIOTICS) },
        { key: 'work', label: t('Working (µg/mL, blank: usual)') }, { key: 'stock', label: t('Stock (mg/mL, blank: usual)') },
        { key: 'vol', label: t('Medium'), units: 'volume', unit: 'mL', value: '500' },
      ],
      compute(v) {
        const d = ANTIBIOTICS[Number(v.raw.drug || 0)];
        const work = ok(n(v, 'work')) ? n(v, 'work') : d[1]; const stock = ok(n(v, 'stock')) ? n(v, 'stock') : d[2];
        const add = work / (stock * 1000) * b(v, 'vol');
        if (!ok(add)) return { hint: t('The medium volume.') };
        const unit = d[0].startsWith('Penicillin') ? 'U/mL' : 'µg/mL';
        const mammalian = d[0].includes('mammalian');
        const bacterial = d[0].includes('E. coli');
        return { lines: [L(t('Stock to add'), show(add, 'volume'), true), L(t('Working'), `${fmt(work)} ${unit}`),
          L(t('From a stock of'), `${fmt(stock)} ${unit === 'U/mL' ? 'kU/mL' : 'mg/mL'}${ok(n(v, 'stock')) ? '' : t(' (the usual)')}`),
          L(t('That is'), t('%(x)s× stock', { x: fmt(stock * 1000 / work, 3) }))],
          notes: mammalian ? [t('For selection, find the lowest dose that kills untransduced cells with a kill curve first.')]
            : bacterial ? [t('Add to agar once it has cooled to about 55 °C.')] : [] };
      },
    },

    {
      id: 'plates',
      inputs: [
        { key: 'plates', label: t('Plates'), value: '20' },
        { key: 'each', label: t('mL per plate'), value: '25', hint: t('About 25 mL for a 10 cm plate.') },
        { key: 'medium', label: t('Medium'), type: 'select', options: [['premix', t('LB agar premix (40 g/L)')], ['broth', t('LB broth (25 g/L) + agar (15 g/L)')]] },
        { key: 'drug', label: t('Antibiotic'), type: 'select', options: [['', t('None')], ...ANTIBIOTICS.map((r, i) => [String(i), t(r[0])]).filter(([i]) => ANTIBIOTICS[Number(i)][0].includes('E. coli'))] },
        { key: 'extra', label: t('Extra (%)'), value: '10' },
      ],
      compute(v) {
        const ml = n(v, 'plates') * n(v, 'each') * (1 + (n(v, 'extra') || 0) / 100);
        if (!ok(ml)) return { hint: t('Plates and mL per plate.') };
        const litres = ml / 1000;
        const out = { lines: [L(t('Medium'), show(litres, 'volume'), true)], notes: [] };
        if (v.raw.medium === 'broth') out.lines.push(L(t('LB broth powder'), `${fmt(25 * litres)} g`, true), L(t('Agar'), `${fmt(15 * litres)} g`, true));
        else out.lines.push(L(t('LB agar powder'), `${fmt(40 * litres)} g`, true));
        if (v.raw.drug !== '' && v.raw.drug != null) {
          const d = ANTIBIOTICS[Number(v.raw.drug)];
          out.lines.push(L(t('%(drug)s stock (%(stock)s mg/mL)', { drug: t(d[0]), stock: fmt(d[2]) }), show(d[1] / (d[2] * 1000) * litres, 'volume'), true));
        }
        out.notes.push(t('Autoclave, cool to about 55 °C (you can hold the bottle), add the antibiotic, swirl and pour.'));
        return out;
      },
    },

    /* ================================================= Centrifuge, animals, gels */
    {
      id: 'rcf',
      inputs: [
        { key: 'radius', label: t('Rotor radius (cm)'), value: '9.5' }, { key: 'rpm', label: 'rpm' }, { key: 'g', label: '× g', value: '12000' },
      ],
      compute(v) {
        const r = n(v, 'radius');
        if (!ok(r)) return { hint: t('The rotor radius (to the bottom of the tube).') };
        if (ok(n(v, 'rpm'))) return { lines: [L(t('Force'), `${fmt(1.118e-5 * r * n(v, 'rpm') ** 2)} × g`, true)] };
        if (ok(n(v, 'g'))) return { lines: [L(t('Speed'), `${fmt(Math.sqrt(n(v, 'g') / (1.118e-5 * r)))} rpm`, true)] };
        return { hint: t('An rpm or a × g.') };
      },
    },
    {
      id: 'dose',
      inputs: [
        { key: 'dose', label: t('Dose (mg/kg)'), value: '75' }, { key: 'weight', label: t('Body weight'), units: 'weight', unit: 'g', value: '25' },
        { key: 'conc', label: t('Solution (mg/mL)'), value: '20' }, { key: 'animals', label: t('Animals'), value: '5' },
        { key: 'extra', label: t('Extra (%)'), value: '20' }, { key: 'limit', label: t('Max volume (mL/kg)'), value: '10', hint: t('e.g. 10 mL/kg i.p. in mice') },
        { key: 'days', label: t('Days'), value: '1' },
      ],
      compute(v) {
        const kg = b(v, 'weight') / 1000; const mg = n(v, 'dose') * kg; const ml = mg / n(v, 'conc');
        if (!ok(ml)) return { hint: t('Dose, weight and solution strength.') };
        const out = { lines: [L(t('Amount'), `${fmt(mg)} mg`), L(t('Inject'), `${fmt(ml * 1000)} µL`, true), L(t('Volume per kg'), `${fmt(ml / kg)} mL/kg`)], warnings: [] };
        const days = Math.max(1, Math.round(n(v, 'days') || 1));
        const k = (n(v, 'animals') || 1) * days * (1 + (n(v, 'extra') || 0) / 100);
        const count = n(v, 'animals') || 1;
        out.lines.push(L(days > 1 ? t(count === 1 ? 'Solution for %(n)s animal, %(days)s days (+%(extra)s %)' : 'Solution for %(n)s animals, %(days)s days (+%(extra)s %)', { n: fmt(count), days, extra: fmt(n(v, 'extra') || 0) })
          : t(count === 1 ? 'Solution for %(n)s animal (+%(extra)s %)' : 'Solution for %(n)s animals (+%(extra)s %)', { n: fmt(count), extra: fmt(n(v, 'extra') || 0) }), `${fmt(ml * k)} mL · ${fmt(ml * k * n(v, 'conc'))} mg`));
        if (ok(n(v, 'limit')) && ml / kg > n(v, 'limit')) out.warnings.push(t('More than %(limit)s mL/kg: make the solution stronger or split the dose.', { limit: fmt(n(v, 'limit')) }));
        return out;
      },
    },
    {
      id: 'dosesheet',
      inputs: [
        { key: 'list', label: t('Animals'), type: 'textarea', value: 'M1023 24.6\nM1024 26.1\nM1027 22.8\nM1031 25.3',
          hint: t('One a line: ID, then weight in g.') },
        { key: 'dose', label: t('Dose (mg/kg)'), value: '75' }, { key: 'conc', label: t('Solution (mg/mL)'), value: '20' },
        { key: 'days', label: t('Days'), value: '5' }, { key: 'extra', label: t('Extra (%)'), value: '20' },
        { key: 'limit', label: t('Max volume (mL/kg)'), value: '10' },
      ],
      compute(v) {
        const animals = lines(v.raw.list).map(row).filter((r) => r.nums.length);
        const dose = n(v, 'dose'); const conc = n(v, 'conc');
        if (!animals.length || !ok(dose) || !ok(conc)) return { hint: t('Animals with weights, the dose and the solution.') };
        const days = Math.max(1, Math.round(n(v, 'days') || 1)); const warnings = [];
        let perDay = 0;
        const rows = animals.map((a) => {
          const g = a.nums[a.nums.length - 1]; const ul = dose * g / 1000 / conc * 1000;
          perDay += ul;
          if (ok(n(v, 'limit')) && ul / 1000 / (g / 1000) > n(v, 'limit')) warnings.push(t('%(id)s: more than %(limit)s mL/kg.', { id: a.name, limit: fmt(n(v, 'limit')) }));
          return [a.name || '—', `${fmt(g)} g`, `${fmt(dose * g / 1000, 3)} mg`, `${fmt(ul, 3)} µL`];
        });
        const total = perDay * days * (1 + (n(v, 'extra') || 0) / 100) / 1000;    // mL
        return { lines: [L(t('Make'), t('%(ml)s mL at %(conc)s mg/mL', { ml: fmt(total, 3), conc: fmt(conc) }), true), L(t('Weigh'), `${fmt(total * conc, 3)} mg`, true),
          L(t('Each day'), `${fmt(perDay, 3)} µL`)], table: { head: [t('Animal'), t('Weight'), t('Dose'), t('Inject each day')], rows }, warnings,
          notes: [t('Weigh the animals again during a long course; doses follow the weight.')] };
      },
    },
    {
      id: 'agarose',
      inputs: [{ key: 'pct', label: t('Agarose (%)'), value: '1' }, { key: 'vol', label: t('Gel volume (mL)'), value: '50' },
        { key: 'buf', label: t('Buffer stock (×)'), type: 'select', options: [['50', t('50× TAE')], ['10', t('10× TBE')], ['5', t('5× TBE')]] }],
      compute(v) {
        const g = n(v, 'pct') / 100 * n(v, 'vol');
        if (!ok(g)) return { hint: t('% and volume.') };
        const stock = Number(v.raw.buf || 50);
        return { lines: [L(t('Agarose'), `${fmt(g)} g`, true), L(t('In 1× buffer'), t('%(vol)s mL (%(stock_ml)s mL of %(x)s× + water)', { vol: fmt(n(v, 'vol')), stock_ml: fmt(n(v, 'vol') / stock), x: stock }))],
          table: { head: [t('Agarose'), t('Separates')], rows: GEL_RANGES },
          notes: [t('Stain: 1:10 000 of a SYBR Safe/GelRed stock, or ethidium bromide at 0.5 µg/mL.')] };
      },
    },

    {
      id: 'samplesize',
      inputs: [
        { key: 'diff', label: t('Smallest difference that matters'), value: '10' }, { key: 'sd', label: t('Standard deviation (same unit)'), value: '8' },
        { key: 'alpha', label: t('Significance (α)'), value: '0.05' }, { key: 'power', label: t('Power'), value: '0.8' },
      ],
      compute(v) {
        const d = n(v, 'diff') / n(v, 'sd');
        const za = zq(1 - (n(v, 'alpha') || 0.05) / 2); const zb = zq(n(v, 'power') || 0.8);
        if (!ok(d) || d <= 0) return { hint: t('A difference and the SD.') };
        const nz = 2 * ((za + zb) / d) ** 2;
        const nt = Math.ceil(nz + za * za / 4);          // the usual small-sample correction
        return { lines: [L(t('Per group'), String(Math.max(2, nt)), true), L(t('Effect size (Cohen\'s d)'), fmt(d, 3))],
          notes: [t('Normal approximation with a small-sample correction; add animals for expected losses. Ask a statistician for anything but two groups.')] };
      },
    },
  ];

  const REFERENCES = [
    { id: 'ref-vessels', keys: 'vessel well plate flask dish area cm2 medium volume t75 培养皿', title: t('Plates & flasks'), head: [t('Vessel'), t('Growth area (cm²)'), t('Usual medium (mL)')], rows: VESSELS.map((r) => [t(r[0]), fmt(r[1]), fmt(r[2])]) },
    { id: 'ref-buffers', keys: 'buffer pka range', title: t('Buffer pKa'), head: [t('Buffer'), t('pKa at 25 °C'), t('Range'), t('ΔpKa / °C')], rows: BUFFERS.map((r) => [t(r[0]), fmt(r[1], 3), `${fmt(r[1] - 1, 2)}–${fmt(r[1] + 1, 2)}`, fmt(r[2], 2)]) },
    { id: 'ref-stocks', keys: 'antibiotic stock storage solvent keep shelf life iptg x-gal dtt pmsf tamoxifen doxycycline 储存 保存', title: t('Stocks & antibiotics'), head: [t('Reagent'), t('Working'), t('Stock'), t('Dissolve in'), t('Keep')],
      rows: [...ANTIBIOTICS.map((r) => [t(r[0]), `${fmt(r[1])} ${r[0].startsWith('Penicillin') ? 'U/mL' : 'µg/mL'}`, `${fmt(r[2])} ${r[0].startsWith('Penicillin') ? 'kU/mL' : 'mg/mL'}`, t(r[3]), t(r[4])]),
        ...STOCKS.map((r) => [t(r[0]), '', r[1], t(r[2]), t(r[3])])] },
    { id: 'ref-gels', keys: 'agarose gel percent fragment size', title: t('Agarose % by size'), head: [t('Agarose'), t('Separates')], rows: GEL_RANGES },
    { id: 'ref-ladders', keys: 'ladder marker 1kb 100bp band size dna 分子量标准', title: t('DNA ladders'), head: [t('Ladder'), t('Bands (bp)')], rows: LADDERS.map(([name, bands]) => [name, bands.map((x) => fmt(x)).join(' · ')]) },
    { id: 'ref-concentrates', keys: 'concentrated acid base hcl naoh molarity density bottle 浓盐酸', title: t('Concentrated reagents'), head: [t('Reagent'), '% w/w', 'g/mL', 'M'], rows: REAGENTS.map((r) => [t(r[0]), fmt(r[1]), fmt(r[2]), fmt(r[1] / 100 * r[2] * 1000 / r[3], 3)]) },
  ];


  /* ------------------------------------------------------------ the tools

     What Utilities lists: seven groups by bench task, each tool a name, a
     line on what it gives, the words people search for (the bench's, in
     English and Chinese), and its modes, each running one calculator above.
     A mode's `solve` lists the values it can work out ("Work out" on the
     page); its `example` is what the tool opens on. A tool used in two
     places names the second group in `also`. */
  const GROUPS = [
    { id: 'solutions', name: t('Solutions'), icon: 'flask', does: t('Weigh, dilute, buffer') },
    { id: 'dna', name: t('DNA & cloning'), icon: 'dna', does: t('Measure, cut, join, run') },
    { id: 'pcr', name: t('PCR & qPCR'), icon: 'chart', does: t('Mix and analyse') },
    { id: 'protein', name: t('Protein'), icon: 'gel', does: t('Quantify, load, concentrate') },
    { id: 'cells', name: t('Cell culture'), icon: 'petri', does: t('Count, seed, treat, infect') },
    { id: 'microbes', name: t('Bacteria & yeast'), icon: 'bacteria', does: t('Grow and select') },
    { id: 'bench', name: t('Bench & animals'), icon: 'centrifuge', does: t('Spin, dose, plan') },
  ];
  const mode = (calc, label, extra) => ({ calc, label: label || '', ...(extra || {}) });
  const TOOLS = [
    { id: 'make', group: 'solutions', name: t('Make a solution'), does: t('What to weigh or measure for a volume and strength.'),
      keys: 'molarity molar mass weigh grams gram stock solution recipe percent w/v v/v % hydrate anhydrous salt form substitute mw molecular weight powder dissolve reconstitute 1m 0.5m concentrated hcl naoh acetic acid ammonia 37% glycerol pbs media 配溶液 配制 称量 溶解 母液 浓盐酸 百分比',
      modes: [
        mode('molarity', t('Weigh a solid'), { example: { chem: 'NaCl', volume: '200', volume_unit: 'mL', conc: '150', conc_unit: 'mM', mw: '58.44' },
          solve: [['mass', t('Mass')], ['volume', t('Volume')], ['conc', t('Concentration')], ['mw', 'MW']] }),
        mode('percent', t('Percent')), mode('fromconc', t('From a concentrate')), mode('saltform', t('Other hydrate or salt')),
      ] },
    { id: 'dilute', group: 'solutions', name: t('Dilute (C₁V₁)'), does: t('Stock and diluent for a working solution.'),
      keys: 'c1v1 c1 v1 dilute dilution diluting stock working 10x 1x 50x 1000x 1:1000 1:100 fold concentrate antibody primer final concentration mg/ml um nm convert ethanol etoh 70% 稀释 稀释倍数 终浓度 抗体',
      modes: [mode('dilution', '', { example: { c1: '10', c1_unit: 'mM', c2: '50', c2_unit: 'µM', v2: '1', v2_unit: 'mL' },
        solve: [['v1', t('Stock volume')], ['c2', t('Final conc.')], ['v2', t('Final volume')], ['c1', t('Stock conc.')]] })] },
    { id: 'serial', group: 'solutions', name: t('Serial dilution'), does: t('Tube-by-tube volumes and concentrations.'),
      keys: 'serial dilution series standards titration two-fold 2-fold 1:10 梯度稀释 倍比稀释',
      modes: [mode('serialfixed', t('Fixed transfer')), mode('serial', t('Fold per step'))] },
    { id: 'buffer', group: 'solutions', name: t('Buffer at a pH'), does: t('What to weigh, and the acid or base to titrate with.'),
      keys: 'buffer ph pka henderson hasselbalch tris hepes mops phosphate titrate 缓冲液 调ph', modes: [mode('buffer')] },

    { id: 'a260', group: 'dna', name: t('DNA / RNA concentration'), does: t('ng/µL and purity from A₂₆₀.'),
      keys: 'nanodrop a260 od260 dna rna concentration 260/280 260/230 purity ng/ul spectrophotometer 核酸浓度 测浓度',
      modes: [mode('a260', '', { example: { a260: '0.412', r280: '1.86', r230: '2.05', dilution: '10' } })] },
    { id: 'copies', group: 'dna', name: t('ng ↔ pmol ↔ copies'), does: t('Moles, nM and copy number from mass and length.'),
      keys: 'pmol fmol copies copy number moles nm ng/ul molarity dna mass plasmid fragment library 拷贝数', modes: [mode('dnamoles')] },
    { id: 'primer', group: 'dna', name: t('Primer: Tm & resuspend'), does: t('Tm, GC, annealing temperature, and water for 100 µM.'),
      keys: 'oligo primer melting temperature tm gc resuspend annealing ta extension time polymerase q5 phusion taq 引物 退火温度',
      modes: [mode('oligo', '', { example: { seq: 'GTAAAACGACGGCCAGT', seq2: 'CAGGAAACAGCTATGAC', nmol: '25' } })] },
    { id: 'digest', group: 'dna', name: t('Restriction digest'), does: t('Enzyme, buffer and water for a digest.'),
      keys: 'restriction digest enzyme ecori bamhi cut cutsmart units glycerol 酶切', modes: [mode('digest')] },
    { id: 'ligation', group: 'dna', name: t('Ligation'), does: t('Insert for a molar ratio, and the whole reaction.'),
      keys: 'ligation ligate insert vector ratio molar ratio t4 ligase cloning 3:1 连接',
      modes: [mode('ligation', '', { example: { iconc: '25', vconc: '50' } })] },
    { id: 'assembly', group: 'dna', name: t('HiFi / Gibson assembly'), does: t('Volume of each fragment for a reaction.'),
      keys: 'gibson hifi nebuilder in-fusion infusion assembly fragments pmol 同源重组 无缝克隆', modes: [mode('assembly')] },
    { id: 'agarose', group: 'dna', name: t('Agarose gel'), does: t('Agarose and buffer, and which % to pour.'),
      keys: 'agarose dna gel electrophoresis tae tbe percent run gel 琼脂糖 电泳 跑胶', modes: [mode('agarose')] },

    { id: 'mastermix', group: 'pcr', name: t('Master mix'), does: t('Each component for n reactions, with extra.'),
      keys: 'master mix mastermix premix n+1 pcr qpcr colony pcr reaction sybr taqman cdna rt 预混 体系 配体系', modes: [mode('pcrmix')] },
    { id: 'ddct', group: 'pcr', name: t('ΔΔCt fold change'), does: t('Fold change ± SD from a table of Ct values.'),
      keys: 'ddct delta delta ct 2^-ddct livak pfaffl fold change qpcr analysis expression relative quantification 相对定量 相对表达',
      modes: [mode('ddcttable', t('Table of Cts')), mode('ddct', t('Four Cts'))] },
    { id: 'efficiency', group: 'pcr', name: t('qPCR efficiency'), does: t('Efficiency and R² from a dilution series.'),
      keys: 'efficiency slope standard curve qpcr primer validation 扩增效率', modes: [mode('qpcreff')] },

    { id: 'a280', group: 'protein', name: t('Protein concentration (A₂₈₀)'), does: t('mg/mL and µM; ε and MW from the sequence.'),
      keys: 'a280 nanodrop protein concentration extinction coefficient protparam molecular weight pi isoelectric sequence 蛋白浓度',
      modes: [mode('a280', t('From A₂₈₀'), { example: { a280: '0.85', eps: '43824', mw: '66430' } }),
        mode('protparam', t('Sequence only'), { example: { seq: 'MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG' } })] },
    { id: 'stdcurve', group: 'protein', name: t('BCA / Bradford curve'), does: t('Read your samples off a standard curve.'),
      keys: 'bca bradford elisa lowry standard curve protein assay absorbance 标准曲线 蛋白定量', modes: [mode('stdcurve')] },
    { id: 'loading', group: 'protein', also: ['pcr'], name: t('Equal loading'), does: t('Sample and water so every lane or tube gets the same.'),
      keys: 'western loading lysate laemmli sample buffer lane equal protein normalise normalize rna input cdna rt template ng 上样 等量 归一',
      modes: [mode('loading', t('Protein lanes')), mode('normalise', t('RNA / DNA input'))] },
    { id: 'sdspage', group: 'protein', name: t('SDS-PAGE gel'), does: t('Resolving and stacking recipe for n gels.'),
      keys: 'sds page sds-page acrylamide gel resolving stacking western cast tris 配胶 分离胶 浓缩胶', modes: [mode('sdspage')] },
    { id: 'concentrate', group: 'protein', name: t('Concentrate & exchange'), does: t('Spin-down volume; what dialysis leaves behind.'),
      keys: 'concentrate amicon spin concentrator dialysis buffer exchange desalting imidazole 浓缩 透析 换液',
      modes: [mode('concentrate', t('Concentrate')), mode('dialysis', t('Dialysis'))] },

    { id: 'count', group: 'cells', name: t('Count cells'), does: t('Cells/mL and viability from a haemocytometer.'),
      keys: 'count counting cell number hemocytometer haemocytometer hemacytometer trypan blue viability cells/ml neubauer countess 计数 细胞计数 台盼蓝', modes: [mode('count')] },
    { id: 'seed', group: 'cells', name: t('Seed plates'), does: t('Suspension and medium for each plate.'),
      keys: 'seed seeding plating plate wells 6 well 96 well density cells per well flask t75 cm2 铺板 接种 种板', modes: [mode('seeding')] },
    { id: 'split', group: 'cells', name: t('Split & passage'), does: t('How much to seed to be ready on the day.'),
      keys: 'split splitting passage passaging split ratio confluence subculture doubling time growth rate 传代 分瓶 倍增时间',
      modes: [mode('split', t('Split')), mode('doubling', t('Doubling time'))] },
    { id: 'treat', group: 'cells', name: t('Drug & vehicle'), does: t('Stock per well and the DMSO cells get.'),
      keys: 'drug treat treatment dmso vehicle dose cells inhibitor compound dose response ic50 加药 给药 处理',
      modes: [mode('treat', t('One dose')), mode('doseseries', t('Dose series'))] },
    { id: 'transfection', group: 'cells', name: t('Transfection'), does: t('DNA and reagent, scaled to a vessel.'),
      keys: 'transfection transfect lipofectamine pei fugene dna reagent scale lentivirus packaging pspax2 pmd2.g 293t 转染 包毒',
      modes: [mode('transfection', t('Scale')), mode('lentipack', t('Lentivirus packaging'))] },
    { id: 'virus', group: 'cells', name: t('Virus: MOI & titer'), does: t('Virus for an MOI; titer from % positive.'),
      keys: 'virus multiplicity infection moi transduction transduce lentivirus aav titre titer tu/ml facs flow polybrene 病毒 感染复数 滴度 感染',
      modes: [mode('moi', t('Volume for an MOI')), mode('titer', t('Titer'))] },
    { id: 'freeze', group: 'cells', name: t('Freeze cells'), does: t('Cells and freezing medium for n vials.'),
      keys: 'freeze freezing cryopreserve dmso vials cryo 冻存 冻细胞', modes: [mode('freezing')] },

    { id: 'od600', group: 'microbes', name: t('OD₆₀₀'), does: t('Cells/mL, dilute to a starting OD, time to an OD.'),
      keys: 'od600 od bacteria yeast optical density culture dilute back time grow induce induction iptg 菌液 od值 诱导',
      modes: [mode('od600', t('Cells & dilution')), mode('growth', t('Time to an OD'))] },
    { id: 'antibiotic', group: 'microbes', also: ['cells'], name: t('Antibiotics & plates'), does: t('Antibiotic for media; LB and agar for plates.'),
      keys: 'antibiotic antibiotics ampicillin amp carbenicillin carb kanamycin kan chloramphenicol cm spectinomycin selection agar plates lb pour plates puromycin puro blasticidin g418 hygromycin pen strep penicillin streptomycin 抗生素 平板 倒板 筛选',
      modes: [mode('antibiotic', t('Add to media')), mode('plates', t('Pour plates'))] },

    { id: 'rcf', group: 'bench', name: t('rpm ↔ × g'), does: t('Convert for your rotor.'),
      keys: 'rpm x g xg rcf centrifuge centrifugation g-force spin speed rotor 离心 转速 离心力',
      modes: [mode('rcf', '', { solve: [['rpm', 'rpm'], ['g', '× g']] })] },
    { id: 'dose', group: 'bench', name: t('Dosing sheet'), does: t('µL for each animal from mg/kg and its weight.'),
      keys: 'dose dosing mg/kg injection mouse mice rat animal body weight tamoxifen ip gavage 给药 剂量 小鼠 注射',
      modes: [mode('dosesheet', t('Sheet')), mode('dose', t('One weight'))] },
    { id: 'samplesize', group: 'bench', name: t('Sample size'), does: t('Animals or samples per group for a t-test.'),
      keys: 'sample size power n group animals iacuc 样本量', modes: [mode('samplesize')] },
  ];
  // Addresses from before the redesign (…/utilities#dilution, the Home card's
  // recent list): the calculator's id opens the tool and mode that runs it.
  const OLD = { molarity: 'make', percent: 'make', saltform: 'make', dilution: 'dilute', xfold: 'dilute', massmolar: 'dilute',
    serial: 'serial', buffer: 'buffer', a260: 'a260', dnamoles: 'copies', oligo: 'primer', ligation: 'ligation', assembly: 'assembly',
    pcrmix: 'mastermix', qpcreff: 'efficiency', protparam: 'a280', a280: 'a280', stdcurve: 'stdcurve', sdspage: 'sdspage',
    seeding: 'seed', doubling: 'split', transfection: 'transfection', moi: 'virus', titer: 'virus', freezing: 'freeze',
    treat: 'treat', od600: 'od600', growth: 'od600', antibiotic: 'antibiotic', rcf: 'rcf', agarose: 'agarose', samplesize: 'samplesize' };
  function locate(id) {
    let tool = TOOLS.find((x) => x.id === id);
    if (tool) return { tool, mode: 0 };
    tool = TOOLS.find((x) => x.modes.some((m) => m.calc === id)) || TOOLS.find((x) => x.id === OLD[id]);
    if (!tool) return null;
    return { tool, mode: Math.max(0, tool.modes.findIndex((m) => m.calc === id)) };
  }

  /* Search: the bench's words, any language. Subscripts read as digits, ×
     as x, Δ as d, µ as u, so "a260", "c1v1", "ddct" and "ng/ul" all find. */
  const plain = (text) => String(text || '').toLowerCase()
    .replace(/[₀-₉]/g, (d) => String('₀₁₂₃₄₅₆₇₈₉'.indexOf(d))).replace(/×/g, 'x').replace(/δ/g, 'd')
    .replace(/↔/g, ' ').replace(/[µμ]/g, 'u').replace(/[()·,;:]/g, ' ').replace(/(\D)-|-(\D)/g, '$1 $2');
  const STOP = new Set(['to', 'a', 'an', 'the', 'of', 'for', 'how', 'much', 'many', 'and', 'from', 'in', 'my', 'make', 'making',
    'calculate', 'calculator', 'calculation', 'calc', 'what', 'is', 'do', 'i', 'need', 'work', 'out', 'with', 'into', 'on']);
  const CJK = /[㐀-鿿]/;
  const stem = (w) => (w.length > 4 && w.endsWith('s') && !w.endsWith('ss') ? w.slice(0, -1) : w.length > 6 && w.endsWith('ing') ? w.slice(0, -3) : w);
  function near(a, b) {        // a typo: one edit apart, two for long words
    if (Math.abs(a.length - b.length) > 2) return false;
    const d = Array.from({ length: a.length + 1 }, (_, i) => [i]);
    for (let j = 1; j <= b.length; j += 1) d[0][j] = j;
    for (let i = 1; i <= a.length; i += 1) {
      for (let j = 1; j <= b.length; j += 1) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    return d[a.length][b.length] <= (a.length >= 8 ? 2 : 1);
  }
  /* The tools (and reference tables) for a query, best first. A word scores
     its best match: a whole word, then the start of one, then (4+ letters)
     inside one or a near miss. Tools matching more of the words come first;
     the name counts more than the search words. Chinese matches as written.
     `extra` is more to search, by id (the page passes the English names when
     it shows another language). */
  function search(q, extra) {
    const words = plain(q).trim().split(/\s+/).filter((w) => w && !STOP.has(w)).map(stem);
    if (!words.length) return [];
    const all = [...TOOLS, ...REFERENCES.map((r) => ({ id: r.id, group: 'reference', name: r.title, does: '', keys: r.keys || '' }))];
    return all.map((tool) => {
      const name = plain(tool.name); const more = plain((extra && extra[tool.id]) || '');
      const nameWords = `${name} ${more}`.split(/\s+/).filter(Boolean).map(stem);
      const keyText = plain(`${tool.keys} ${tool.does}`);
      const keyWords = keyText.split(/\s+/).filter(Boolean).map(stem);
      const compact = name.replace(/[^a-z0-9]/g, '');
      let score = 0; let hits = 0;
      words.forEach((w) => {
        let best = 0;
        if (CJK.test(w)) {
          if (keyText.includes(w) || name.includes(w)) best = 5;
        } else {
          const grade = (list, weight) => list.forEach((k) => {
            if (k === w) best = Math.max(best, 5 * weight);
            else if (w.length >= 2 && k.startsWith(w)) best = Math.max(best, 4 * weight);
            else if (w.length >= 4 && k.includes(w)) best = Math.max(best, 2 * weight);
            else if (w.length >= 5 && k.length >= 4 && near(w, k)) best = Math.max(best, 2 * weight);
          });
          grade(nameWords, 1.5); grade(keyWords, 1);
          if (!best && w.length >= 3 && compact.includes(w.replace(/[^a-z0-9]/g, ''))) best = 4;
        }
        if (best) { hits += 1; score += best; }
      });
      return hits ? { tool, score, hits } : null;
    }).filter(Boolean).sort((a, b) => b.hits - a.hits || b.score - a.score).map((r) => r.tool);
  }


  /* ------------------------------------------------------ the lab's own

     Tools a lab makes itself: inputs with a name, a label and a unit, and
     answers worked out by formulas over those names (`mass / (conc * mw)`).
     A formula is read by hand, never run as code: numbers, the tool's own
     names, + − × ÷ ^, brackets, and the functions below. */
  const FORMULA_FUNCS = {
    sqrt: [1, Math.sqrt], abs: [1, Math.abs], exp: [1, Math.exp], ln: [1, Math.log], log: [1, Math.log10],
    log10: [1, Math.log10], log2: [1, Math.log2], round: [1, Math.round], ceil: [1, Math.ceil], floor: [1, Math.floor],
    min: [2, Math.min], max: [2, Math.max], pow: [2, Math.pow],
  };
  const FORMULA_CONSTS = { pi: Math.PI, e: Math.E, avogadro: 6.02214076e23 };
  const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,23}$/;

  // A formula as a tree, or an error a person can act on.
  function parseFormula(src, names) {
    const text = String(src || '').replace(/×/g, '*').replace(/÷/g, '/').replace(/−/g, '-');
    const tokens = [];
    const re = /\s*(?:(\d+\.?\d*(?:e[-+]?\d+)?|\.\d+(?:e[-+]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|([-+*/^(),]))/iy;
    let pos = 0;
    while (pos < text.length) {
      if (/^\s*$/.test(text.slice(pos))) break;
      re.lastIndex = pos;
      const m = re.exec(text);
      if (!m) throw new Error(t('“%(text)s” is not something a formula can use.', { text: text.slice(pos).trim().slice(0, 12) }));
      tokens.push(m[1] ? { num: Number(m[1]) } : m[2] ? { name: m[2] } : { op: m[3] });
      pos = re.lastIndex;
    }
    if (!tokens.length) throw new Error(t('Write a formula.'));
    let i = 0;
    const peek = () => tokens[i];
    const take = (op) => (tokens[i] && tokens[i].op === op ? (i += 1, true) : false);
    function primary() {
      const tok = tokens[i];
      if (!tok) throw new Error(t('The formula stops too soon.'));
      i += 1;
      if ('num' in tok) return { num: tok.num };
      if (tok.op === '(') {
        const inner = expr();
        if (!take(')')) throw new Error(t('A bracket is not closed.'));
        return inner;
      }
      if (tok.op === '-') return { neg: power() };
      if (tok.op === '+') return power();
      if (tok.name) {
        const lower = tok.name.toLowerCase();
        if (peek() && peek().op === '(') {
          if (!FORMULA_FUNCS[lower]) throw new Error(t('No function called %(name)s.', { name: tok.name }));
          i += 1;
          const args = [];
          if (!take(')')) {
            do { args.push(expr()); } while (take(','));
            if (!take(')')) throw new Error(t('A bracket is not closed.'));
          }
          const [want] = FORMULA_FUNCS[lower];
          if (args.length !== want) throw new Error(t('%(name)s takes %(n)s value(s).', { name: lower, n: want }));
          return { fn: lower, args };
        }
        if (names.includes(tok.name)) return { ref: tok.name };
        if (lower in FORMULA_CONSTS) return { num: FORMULA_CONSTS[lower] };
        throw new Error(t('No input or answer called %(name)s.', { name: tok.name }));
      }
      throw new Error(t('“%(op)s” is out of place.', { op: tok.op }));
    }
    function power() {
      const base = primary();
      return take('^') ? { op: '^', a: base, b: unary() } : base;
    }
    function unary() { return take('-') ? { neg: unary() } : (take('+'), power()); }
    function term() {
      let left = unary();
      while (peek() && (peek().op === '*' || peek().op === '/')) { const op = tokens[i].op; i += 1; left = { op, a: left, b: unary() }; }
      return left;
    }
    function expr() {
      let left = term();
      while (peek() && (peek().op === '+' || peek().op === '-')) { const op = tokens[i].op; i += 1; left = { op, a: left, b: term() }; }
      return left;
    }
    const tree = expr();
    if (i < tokens.length) throw new Error(t('“%(op)s” is out of place.', { op: tokens[i].op || tokens[i].name || tokens[i].num }));
    return tree;
  }
  function evalFormula(node, vars) {
    if ('num' in node) return node.num;
    if (node.ref) return vars[node.ref];
    if (node.neg) return -evalFormula(node.neg, vars);
    if (node.fn) return FORMULA_FUNCS[node.fn][1](...node.args.map((a) => evalFormula(a, vars)));
    const a = evalFormula(node.a, vars); const b = evalFormula(node.b, vars);
    return { '+': a + b, '-': a - b, '*': a * b, '/': a / b, '^': a ** b }[node.op];
  }

  /* A lab tool as the page lists it: its definition (as saved) becomes a
     calculator and a tool. Problems with the definition come back as
     `problems`, one per answer or input, so the editor can show them. */
  function labTool(def) {
    const inputs = (def.inputs || []).filter((x) => x && x.name);
    const outputs = (def.outputs || []).filter((x) => x && x.formula);
    const problems = {};
    const names = [];
    inputs.forEach((x, k) => {
      if (!NAME.test(x.name)) problems[`in${k}`] = t('A name is letters, digits and _ , starting with a letter.');
      else if (names.includes(x.name)) problems[`in${k}`] = t('%(name)s is used twice.', { name: x.name });
      names.push(x.name);
    });
    const trees = outputs.map((o, k) => {
      try {
        const tree = parseFormula(o.formula, names.slice());
        if (o.name) {
          if (!NAME.test(o.name)) problems[`out${k}`] = t('A name is letters, digits and _ , starting with a letter.');
          else if (names.includes(o.name)) problems[`out${k}`] = t('%(name)s is used twice.', { name: o.name });
          names.push(o.name);       // later answers may use this one
        }
        return tree;
      } catch (err) {
        problems[`out${k}`] = err.message;
        return null;
      }
    });
    const calc = {
      id: def.id,
      inputs: inputs.map((x) => ({ key: x.name, label: x.unit ? `${x.label || x.name} (${x.unit})` : (x.label || x.name), value: x.value == null ? '' : String(x.value) })),
      compute(v) {
        const vars = {};
        const missing = inputs.filter((x) => !ok(n(v, x.name))).map((x) => x.label || x.name);
        if (missing.length) return { hint: t('Fill in %(names)s.', { names: missing.join(', ') }) };
        inputs.forEach((x) => { vars[x.name] = n(v, x.name); });
        const lines_ = [];
        const warnings = [];
        outputs.forEach((o, k) => {
          if (!trees[k]) return;
          const x = evalFormula(trees[k], vars);
          if (o.name) vars[o.name] = x;
          if (!ok(x)) warnings.push(t('%(label)s can’t be worked out from these numbers.', { label: o.label || o.formula }));
          lines_.push(L(o.label || o.formula, ok(x) ? `${fmt(x, Number(o.digits) || 4)}${o.unit ? ` ${o.unit}` : ''}` : '—', k === 0 || !!o.main));
        });
        const notes = [def.note, ...outputs.map((o) => `${o.label || o.name || ''} = ${o.formula}`)].filter(Boolean);
        return { lines: lines_, warnings, notes };
      },
    };
    const tool = { id: def.id, group: 'lab', name: def.name || t('Untitled tool'), does: def.does || '', keys: `${def.keys || ''} ${inputs.map((x) => x.label).join(' ')}`,
      lab: def, modes: [mode(def.id)] };
    return { calc, tool, problems };
  }
  // Lists the lab's own tools with the rest (the page calls this once it
  // has them); replaces any listed before.
  const LAB_GROUP = { id: 'lab', name: t('The lab’s own'), icon: 'users', does: t('Tools made in this lab') };
  function useLabTools(defs) {
    for (let k = CALCS.length - 1; k >= 0; k -= 1) if (CALCS[k].lab) CALCS.splice(k, 1);
    for (let k = TOOLS.length - 1; k >= 0; k -= 1) if (TOOLS[k].group === 'lab') TOOLS.splice(k, 1);
    if (!GROUPS.includes(LAB_GROUP)) GROUPS.push(LAB_GROUP);
    (defs || []).forEach((def) => {
      const made = labTool(def);
      made.calc.lab = true;
      CALCS.push(made.calc);
      TOOLS.push(made.tool);
    });
  }

  /* ------------------------------------------------------------ running */

  // raw: { key: text, key_unit: unit } as the form has them.
  function run(id, raw) {
    const calc = CALCS.find((c) => c.id === id);
    if (!calc) throw new Error(`No calculator ${id}`);
    const v = { raw: {}, num: {}, base: {}, unit: {} };
    calc.inputs.forEach((inp) => {
      const text = raw[inp.key] != null ? raw[inp.key] : (inp.value || '');
      v.raw[inp.key] = text;
      v.num[inp.key] = num(text);
      if (inp.units) {
        const unit = raw[`${inp.key}_unit`] || inp.unit;
        v.unit[inp.key] = unit;
        v.base[inp.key] = v.num[inp.key] * factor(inp.units, unit);
      } else {
        v.base[inp.key] = v.num[inp.key];
      }
    });
    try {
      return calc.compute(v) || {};
    } catch (err) {
      return { error: t('That doesn\'t add up: check the numbers.') };
    }
  }

  const api = { CALCS, REFERENCES, GROUPS, TOOLS, UNITS: U, CHEMICALS, run, search, locate, plain, num, fmt, show, dnaTm, protein, cleanDna, cleanProtein, zq,
    parseFormula, evalFormula, labTool, useLabTools, FORMULA_FUNCS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.BenchCalc = api;
})(typeof window !== 'undefined' ? window : globalThis);
