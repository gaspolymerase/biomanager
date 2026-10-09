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

  /* 4 significant figures, no trailing zeros, thousands spaced. */
  function fmt(x, sig = 4) {
    if (!ok(x)) return '—';
    if (x === 0) return '0';
    const a = Math.abs(x);
    if (a >= 1e7 || a < 1e-4) {
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

  // Good's and other buffers: pKa at 25 °C and its change per °C.
  const BUFFERS = [
    ['Tris', 8.06, -0.028], ['HEPES', 7.48, -0.014], ['MOPS', 7.20, -0.015], ['MES', 6.10, -0.011],
    ['PIPES', 6.76, -0.0085], ['Bis-Tris', 6.46, -0.017], ['Phosphate (pKa₂)', 7.20, -0.0028],
    ['Acetate', 4.76, 0.0002], ['Citrate (pKa₃)', 6.40, 0], ['Bicine', 8.26, -0.018], ['Tricine', 8.05, -0.021],
    ['EPPS (HEPPS)', 8.00, -0.015], ['Glycine (pKa₂)', 9.60, -0.025], ['CHES', 9.50, -0.011],
    ['Carbonate (pKa₂)', 10.33, -0.009], ['CAPS', 10.40, -0.009], ['Imidazole', 6.95, -0.020],
  ];

  // Surface area (cm²) and usual medium volume (mL) of culture vessels.
  const VESSELS = [
    ['96-well', 0.32, 0.1], ['48-well', 0.95, 0.3], ['24-well', 1.9, 0.5], ['12-well', 3.8, 1],
    ['6-well', 9.5, 2], ['35 mm dish', 9, 2], ['60 mm dish', 21, 4], ['100 mm dish', 55, 10],
    ['150 mm dish', 145, 20], ['T25 flask', 25, 5], ['T75 flask', 75, 15], ['T175 flask', 175, 30],
  ];

  // Working concentration (µg/mL) and a usual stock (mg/mL).
  const ANTIBIOTICS = [
    ['Ampicillin (E. coli)', 100, 100], ['Carbenicillin (E. coli)', 100, 100], ['Kanamycin (E. coli)', 50, 50],
    ['Chloramphenicol (E. coli, in ethanol)', 34, 34], ['Tetracycline (E. coli)', 10, 10],
    ['Spectinomycin (E. coli)', 50, 50], ['Gentamicin (E. coli)', 15, 10], ['Zeocin (E. coli, low salt)', 25, 100],
    ['Puromycin (mammalian)', 2, 10], ['Blasticidin (mammalian)', 10, 10], ['G418 / Geneticin (mammalian)', 500, 50],
    ['Hygromycin B (mammalian)', 200, 50], ['Zeocin (mammalian)', 200, 100],
    ['Penicillin–streptomycin (1×, U/mL pen)', 100, 10],
  ];

  const ISOTOPES = [
    ['³²P', 14.29], ['³³P', 25.34], ['³⁵S', 87.37], ['¹²⁵I', 59.49], ['⁵¹Cr', 27.70], ['³H', 4500.4], ['¹⁴C', 2092700],
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
  function numbers(text) {
    return String(text || '').split(/[\s,;\t\n]+/).map(num).filter(ok);
  }
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
  // Two-sided 95% t critical values, df 1–30; the normal beyond.
  const T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145,
    2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042];
  const tcrit = (df) => (df <= 30 ? T975[df - 1] : 1.96 + 2.4 / df);

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
      id: 'molarity', group: t('Solutions & buffers'), title: t('Molarity: mass, volume, concentration'),
      blurb: t('Leave one of the four blank and it is worked out. Pick a chemical to fill its molecular weight.'),
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
      id: 'dilution', group: t('Solutions & buffers'), title: t('Dilution (C₁V₁ = C₂V₂)'),
      blurb: t('Leave one of the four blank. Stock and final must be the same kind of unit (molar, mass, %, ×…).'),
      solveOne: ['c1', 'v1', 'c2', 'v2'],
      inputs: [
        { key: 'c1', label: t('Stock C₁'), units: 'conc', unit: 'mM' },
        { key: 'v1', label: t('Stock volume V₁'), units: 'volume', unit: 'µL' },
        { key: 'c2', label: t('Final C₂'), units: 'conc', unit: 'µM' },
        { key: 'v2', label: t('Final volume V₂'), units: 'volume', unit: 'mL' },
      ],
      compute(v) {
        if (FAMILY[v.unit.c1] !== FAMILY[v.unit.c2]) return { error: t('C₁ and C₂ must be the same kind of unit.') };
        const vals = { c1: b(v, 'c1'), v1: b(v, 'v1'), c2: b(v, 'c2'), v2: b(v, 'v2') };
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
        const value = k[0] === 'c' ? `${fmt(x / factor('conc', v.unit[k]))} ${v.unit[k]}` : show(x, 'volume');
        const out = { solved: k, lines: [L(label, value, true)], notes: [], warnings: [] };
        if (all.v2 >= all.v1) out.lines.push(L(t('Diluent'), show(all.v2 - all.v1, 'volume')));
        if (ok(all.v1) && all.v1 < 0.5e-6) out.warnings.push(t('Under 0.5 µL is hard to pipette: make an intermediate dilution.'));
        out.notes.push(t('1 : %(ratio)s dilution.', { ratio: fmt(all.v2 / all.v1, 3) }));
        return out;
      },
    },
    {
      id: 'serial', group: t('Solutions & buffers'), title: t('Serial dilution'),
      blurb: t('A series of tubes, each the same fold weaker than the last.'),
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
      id: 'percent', group: t('Solutions & buffers'), title: t('Percent solutions'),
      blurb: t('w/v is grams per 100 mL; v/v is mL per 100 mL.'),
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
      id: 'xfold', group: t('Solutions & buffers'), title: t('×-fold stocks (10× to 1×)'),
      blurb: t('Buffers and media sold or made as 5×, 10×, 50× stocks.'),
      inputs: [
        { key: 'stock', label: t('Stock strength (×)'), value: '10' },
        { key: 'final', label: t('Wanted strength (×)'), value: '1' },
        { key: 'volume', label: t('Final volume'), units: 'volume', unit: 'mL', value: '500' },
      ],
      compute(v) {
        const s = n(v, 'stock'); const f = n(v, 'final'); const vol = b(v, 'volume');
        if (!(s > 0) || !(f > 0) || !ok(vol)) return { hint: t('Both strengths and a volume.') };
        if (f > s) return { error: t('The wanted strength is above the stock.') };
        return { lines: [L(t('Stock solution'), show(vol * f / s, 'volume'), true), L(t('Water or diluent'), show(vol - vol * f / s, 'volume'))] };
      },
    },
    {
      id: 'massmolar', group: t('Solutions & buffers'), title: t('Mass ↔ molar concentration'),
      blurb: t('mg/mL to µM and back, from the molecular weight.'),
      inputs: [
        { key: 'value', label: t('Concentration'), units: 'conc', unit: 'mg/mL', value: '1' },
        { key: 'mw', label: t('Molecular weight (g/mol)'), value: '66430' },
      ],
      compute(v) {
        const mw = n(v, 'mw'); const fam = FAMILY[v.unit.value]; const x = b(v, 'value');
        if (!ok(mw) || !ok(x)) return { hint: t('A concentration and a molecular weight.') };
        if (fam === 'mass') return { lines: [L(t('Molar'), show(x / mw, 'molar'), true)] };
        if (fam === 'molar') return { lines: [L(t('Mass per volume'), show(x * mw, 'massconc', ['mg/mL', 'µg/mL', 'ng/mL']), true)] };
        return { error: t('Give a molar or a mass-per-volume concentration.') };
      },
    },
    {
      id: 'buffer', group: t('Solutions & buffers'), title: t('Buffer pH (Henderson–Hasselbalch)'),
      blurb: t('How much of the acid and base forms make a buffer of this pH, at its temperature.'),
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
            L(t('Base : acid'), `${fmt(baseFrac / (1 - baseFrac), 3)} : 1`, true),
            L(t('Base form'), ok(mol) ? `${fmt(baseFrac * 100, 3)} % · ${show(mol * baseFrac, 'amount')}` : `${fmt(baseFrac * 100, 3)} %`),
            L(t('Acid form'), ok(mol) ? `${fmt((1 - baseFrac) * 100, 3)} % · ${show(mol * (1 - baseFrac), 'amount')}` : `${fmt((1 - baseFrac) * 100, 3)} %`),
          ],
          notes: [], warnings: [],
        };
        if (Math.abs(ph - pka) > 1) out.warnings.push(t('pH %(ph)s is more than one unit from the pKa: %(buffer)s buffers poorly there.', { ph: fmt(ph, 3), buffer: t(buf[0]) }));
        if (buf[0] === 'Tris' && ok(mol)) out.notes.push(t('Tris: weigh %(mass)s of Tris base and titrate with about %(amount)s HCl (%(volume)s of 1 M), then make up to volume. Set the pH at the temperature you use it.',
          { mass: show(mol * 121.14, 'mass'), amount: show(mol * (1 - baseFrac), 'amount'), volume: show(mol * (1 - baseFrac), 'volume') }));
        out.notes.push(t('The equation ignores ionic strength; check the pH with a meter.'));
        return out;
      },
    },
    {
      id: 'osmolarity', group: t('Solutions & buffers'), title: t('Osmolarity'),
      blurb: t('One solute a line: its name, concentration in mM and the particles it gives (NaCl 2, glucose 1).'),
      inputs: [{ key: 'list', label: t('Solutes'), type: 'textarea', value: 'NaCl 137 2\nKCl 2.7 2\nNa2HPO4 10 3\nKH2PO4 1.8 2' }],
      compute(v) {
        const rows = lines(v.raw.list).map(row).filter((r) => r.nums.length >= 1);
        if (!rows.length) return { hint: t('Name, mM and particles, one solute a line.') };
        let total = 0;
        const table = rows.map((r) => {
          const i = r.nums.length > 1 ? r.nums[1] : 1;
          total += r.nums[0] * i;
          return [r.name || '—', fmt(r.nums[0]), fmt(i), fmt(r.nums[0] * i)];
        });
        return { lines: [L(t('Osmolarity (ideal)'), `${fmt(total)} mOsm/L`, true)], table: { head: [t('Solute'), 'mM', t('Particles'), 'mOsm/L'], rows: table },
          notes: [t('Real solutions are about 7 % lower (osmotic coefficient ≈ 0.93); cells like 280–300 mOsm/kg.')] };
      },
    },
    {
      id: 'saltform', group: t('Solutions & buffers'), title: t('Another form of a chemical'),
      blurb: t('A recipe for the anhydrous form, and you have the hydrate (or another salt): weigh this much.'),
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

    /* ================================================= DNA and RNA */
    {
      id: 'a260', group: t('DNA & RNA'), title: t('Nucleic acid from A₂₆₀'),
      blurb: t('Concentration from a spectrophotometer reading, and what the purity ratios say.'),
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
      id: 'dnamoles', group: t('DNA & RNA'), title: t('DNA mass ↔ moles ↔ copies'),
      blurb: t('ng of a plasmid or fragment to pmol and copy number, and back.'),
      inputs: [
        { key: 'kind', label: t('Kind'), type: 'select', options: [['ds', 'dsDNA'], ['ss', 'ssDNA'], ['rna', 'RNA']] },
        { key: 'length', label: t('Length (bp or nt)'), value: '5000' },
        { key: 'mass', label: t('Mass'), units: 'mass', unit: 'ng', value: '100' },
        { key: 'moles', label: t('…or moles'), units: 'amount', unit: 'pmol' },
      ],
      compute(v) {
        const len = n(v, 'length');
        if (!(len > 0)) return { hint: t('The length.') };
        const mw = { ds: len * 617.96 + 36.04, ss: len * 308.97 + 18.02, rna: len * 321.47 + 18.02 }[v.raw.kind || 'ds'];
        let mol = b(v, 'moles'); let mass = b(v, 'mass');
        if (ok(mass)) mol = mass / mw; else if (ok(mol)) mass = mol * mw; else return { hint: t('A mass or an amount in moles.') };
        return { lines: [L(t('Molecular weight'), `${fmt(mw)} g/mol`), L(t('Mass'), show(mass, 'mass'), true),
          L(t('Moles'), show(mol, 'amount'), true), L(t('Copies'), fmt(mol * 6.02214076e23, 3), true)] };
      },
    },
    {
      id: 'oligo', group: t('DNA & RNA'), title: t('Oligo: Tm, GC, MW, resuspension'),
      blurb: t('Nearest-neighbour Tm (SantaLucia) for a primer in excess, as the Primers database works it out.'),
      inputs: [
        { key: 'seq', label: t('Sequence 5′→3′'), type: 'text', value: 'GTAAAACGACGGCCAGT' },
        { key: 'na', label: 'Na⁺ (mM)', value: '50' },
        { key: 'primer', label: t('Primer (nM)'), value: '250' },
        { key: 'nmol', label: t('Delivered (nmol, optional)') },
        { key: 'stock', label: t('Stock wanted (µM)'), value: '100' },
        { key: 'seq2', label: t('Its partner (optional)'), type: 'text' },
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
            out.lines.push(L(t('Annealing, to start'), `${fmt(Math.min(tm, tm2) - 3, 3)} °C`, true));
            if (Math.abs(tm - tm2) > 5) out.warnings.push(t('The two Tm differ by %(diff)s °C (aim for within 5).', { diff: fmt(Math.abs(tm - tm2), 2) }));
          }
        }
        return out;
      },
    },
    {
      id: 'ligation', group: t('DNA & RNA'), title: t('Ligation: insert to vector'),
      blurb: t('ng of insert for a molar ratio to the vector.'),
      inputs: [
        { key: 'vng', label: t('Vector (ng)'), value: '50' }, { key: 'vbp', label: t('Vector length (bp)'), value: '5000' },
        { key: 'ibp', label: t('Insert length (bp)'), value: '1000' }, { key: 'ratio', label: t('Insert : vector'), value: '3' },
        { key: 'iconc', label: t('Insert concentration (ng/µL, optional)') },
      ],
      compute(v) {
        const ng = n(v, 'vng') * n(v, 'ibp') / n(v, 'vbp') * n(v, 'ratio');
        if (!ok(ng)) return { hint: t('Vector amount, both lengths and a ratio.') };
        const fmolV = n(v, 'vng') / (n(v, 'vbp') * 617.96 + 36.04) * 1e6;
        const out = { lines: [L(t('Insert'), `${fmt(ng)} ng`, true), L(t('Vector'), `${fmt(fmolV)} fmol`), L(t('Insert'), `${fmt(fmolV * n(v, 'ratio'))} fmol`)] };
        if (ok(n(v, 'iconc'))) out.lines.push(L(t('Insert to add'), `${fmt(ng / n(v, 'iconc'))} µL`, true));
        return out;
      },
    },
    {
      id: 'assembly', group: t('DNA & RNA'), title: t('HiFi / Gibson assembly'),
      blurb: t('One fragment a line: name, length (bp) and ng/µL; the first is the vector.'),
      inputs: [
        { key: 'list', label: t('Fragments'), type: 'textarea', value: 'pUC19 (cut) 2686 45\ninsert A 1200 38\ninsert B 800 52' },
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
          table: { head: [t('Fragment'), t('Amount'), t('Mass'), t('Volume')], rows }, warnings: [] };
        if (total > rxn / 2) out.warnings.push(t('The DNA is more than half the reaction (%(half)s µL): concentrate it or lower the amounts.', { half: fmt(rxn / 2) }));
        return out;
      },
    },
    {
      id: 'pcrmix', group: t('DNA & RNA'), title: t('PCR / qPCR master mix'),
      blurb: t('One component a line with its µL per reaction; the template is added to each tube after.'),
      inputs: [
        { key: 'n', label: t('Reactions'), value: '12' }, { key: 'extra', label: t('Extra (%)'), value: '10' },
        { key: 'list', label: t('Per reaction'), type: 'textarea', value: '2× master mix 10\nForward primer 10 µM 0.8\nReverse primer 10 µM 0.8\nWater 6.4' },
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
          table: { head: [t('Component'), t('µL / reaction'), t('µL for %(n)s', { n: fmt(k, 3) })], rows } };
      },
    },
    {
      id: 'qpcreff', group: t('DNA & RNA'), title: t('qPCR efficiency from a standard curve'),
      blurb: t('One standard a line: its amount (any unit, or copies) and its Ct. Or just the slope.'),
      inputs: [
        { key: 'list', label: t('Standards'), type: 'textarea', value: '100000 17.1\n10000 20.5\n1000 23.9\n100 27.3\n10 30.7' },
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
      id: 'ddct', group: t('DNA & RNA'), title: t('ΔΔCt fold change'),
      blurb: t('Mean Ct of the target and the reference gene, in the control and the treated sample.'),
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
        return { lines: [L(t('ΔCt control'), fmt(tc - rc, 4)), L(t('ΔCt treated'), fmt(tt - rt, 4)), L('ΔΔCt', fmt(ddct, 4)),
          L(t('Fold change (2^−ΔΔCt)'), fmt(2 ** -ddct, 4), true), L(t('Fold change, with efficiencies (Pfaffl)'), fmt(pfaffl, 4))] };
      },
    },
    {
      id: 'transformation', group: t('DNA & RNA'), title: t('Transformation efficiency'),
      blurb: t('Colonies per µg of DNA, from a plate of a known part of the transformation.'),
      inputs: [
        { key: 'colonies', label: t('Colonies counted'), value: '150' }, { key: 'ng', label: t('DNA used (ng)'), value: '0.1' },
        { key: 'plated', label: t('Volume plated (µL)'), value: '100' }, { key: 'total', label: t('Total after recovery (µL)'), value: '1000' },
        { key: 'dil', label: t('Dilution before plating (×)'), value: '1' },
      ],
      compute(v) {
        const frac = n(v, 'plated') / n(v, 'total') / (n(v, 'dil') || 1);
        const eff = n(v, 'colonies') / (n(v, 'ng') * 1e-3 * frac);
        if (!ok(eff)) return { hint: t('Colonies, DNA and volumes.') };
        return { lines: [L(t('Efficiency'), `${fmt(eff, 3)} cfu/µg`, true), L(t('DNA on the plate'), `${fmt(n(v, 'ng') * frac * 1000, 3)} pg`)] };
      },
    },

    /* ================================================= Protein */
    {
      id: 'a280', group: t('Protein'), title: t('Protein from A₂₈₀'),
      blurb: t('µM and mg/mL from the reading, with ε and MW given or worked out from the sequence.'),
      inputs: [
        { key: 'seq', label: t('Sequence (optional)'), type: 'textarea', placeholder: t('MKV… (FASTA is fine)') },
        { key: 'eps', label: 'ε₂₈₀ (M⁻¹cm⁻¹)' }, { key: 'mw', label: 'MW (g/mol)' },
        { key: 'a280', label: 'A₂₈₀' }, { key: 'path', label: t('Path (cm)'), value: '1' }, { key: 'dil', label: t('Dilution (×)'), value: '1' },
      ],
      compute(v) {
        const seq = cleanProtein(v.raw.seq);
        const p = seq ? protein(seq) : null;
        if (seq && !p) return { error: t('The sequence has letters that are not amino acids.') };
        const eps = ok(n(v, 'eps')) ? n(v, 'eps') : p && p.epsilon;
        const mw = ok(n(v, 'mw')) ? n(v, 'mw') : p && p.mw;
        const out = { lines: [], notes: [], warnings: [] };
        if (p) {
          out.notes.push(t('From the sequence: %(length)s aa, %(kda)s kDa, ε₂₈₀ %(eps)s (%(reduced)s reduced).',
            { length: p.length, kda: fmt(p.mw / 1000, 4), eps: fmt(p.epsilon), reduced: fmt(p.reduced) }));
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
      id: 'protparam', group: t('Protein'), title: t('Protein properties from the sequence'),
      blurb: t('Length, MW, extinction coefficient, pI and charge at pH 7.'),
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
      id: 'stdcurve', group: t('Protein'), title: t('Standard curve (BCA, Bradford, ELISA)'),
      blurb: t('Standards as "concentration reading" a line, unknowns as "name reading", and read them off the curve.'),
      inputs: [
        { key: 'std', label: t('Standards'), type: 'textarea', value: '0 0.10\n125 0.21\n250 0.33\n500 0.55\n1000 0.98\n2000 1.62' },
        { key: 'unk', label: t('Unknowns'), type: 'textarea', value: 'Lysate A 0.72\nLysate B 0.64' },
        { key: 'fit', label: t('Fit'), type: 'select', options: [['quad', t('Quadratic (BCA bends)')], ['lin', t('Straight line')]] },
        { key: 'dil', label: t('Unknowns diluted (×)'), value: '1' },
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
          return [r.name || '—', fmt(y), fmt(inv(y)), fmt(inv(y) * dil)];
        });
        const term = (c, x) => (c === 0 || none(c, x === '' ? 0 : x === '·x' ? 1 : 2) ? '' : `${c < 0 ? ' − ' : ' + '}${fmt(Math.abs(c))}${x}`);
        const eq = quad ? `y = ${fmt(f.a)}${term(f.b, '·x')}${term(f.c, '·x²')}` : `y = ${fmt(f.slope)}·x${term(f.intercept, '')}`;
        reps.forEach((r) => {
          const m = mean(r.readings);
          if (m && (Math.max(...r.readings) - Math.min(...r.readings)) / Math.abs(m) > 0.1) {
            warnings.push(t('%(which)s: its readings differ by more than 10 %; check that well.', { which: r.conc == null ? (r.name || fmt(m)) : fmt(r.conc) }));
          }
        });
        const notes = reps.length ? [t('Replicate readings are averaged.')] : [];
        return { lines: [L(t('Fit'), eq), L('R²', fmt(f.r2, 4), true)], table: rows.length ? { head: [t('Sample'), t('Reading'), t('On the curve'), `× ${fmt(dil)}`], rows } : null, warnings, notes };
      },
    },
    {
      id: 'sdspage', group: t('Protein'), title: t('SDS-PAGE gel recipe'),
      blurb: t('Resolving and stacking gels from 30 % acrylamide/bis, for a number of gels.'),
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
      id: 'loading', group: t('Protein'), title: t('Gel loading: µg per lane'),
      blurb: t('One sample a line: its name and µg/µL (from BCA); the volume of sample, buffer and water for each lane.'),
      inputs: [
        { key: 'list', label: t('Samples'), type: 'textarea', value: 'WT 2.48\nKO 2.31\nKO + drug 1.95' },
        { key: 'ug', label: t('µg per lane'), value: '30' }, { key: 'lane', label: t('Volume per lane (µL)'), value: '20' },
        { key: 'buf', label: t('Loading buffer (×)'), value: '4' },
      ],
      compute(v) {
        const samples = lines(v.raw.list).map(row).filter((r) => r.nums.length);
        const ug = n(v, 'ug'); const lane = n(v, 'lane'); const bx = n(v, 'buf') || 4;
        if (!samples.length || !ok(ug) || !ok(lane)) return { hint: t('Samples with µg/µL, µg per lane and lane volume.') };
        const buf = lane / bx; const warnings = [];
        const rows = samples.map((s) => {
          const sv = ug / s.nums[s.nums.length - 1]; const water = lane - buf - sv;
          if (water < 0) warnings.push(t('%(name)s: too dilute for %(ug)s µg in %(lane)s µL.', { name: s.name, ug: fmt(ug), lane: fmt(lane) }));
          return [s.name, fmt(sv), fmt(buf), water < 0 ? '—' : fmt(water)];
        });
        return { table: { head: [t('Sample'), t('Sample µL'), t('%(x)s× buffer µL', { x: fmt(bx) }), t('Water µL')], rows }, warnings };
      },
    },

    /* ================================================= Cells */
    {
      id: 'count', group: t('Cells'), title: t('Cell count (haemocytometer)'),
      blurb: t('Live and dead cells counted over the large squares, with trypan blue.'),
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
      id: 'seeding', group: t('Cells'), title: t('Seeding plates'),
      blurb: t('How much cell suspension and medium for a number of wells, by cells per well or per cm².'),
      inputs: [
        { key: 'susp', label: t('Suspension (cells/mL)'), value: '1.2e6' },
        { key: 'vessel', label: t('Vessel'), type: 'select', options: opt(VESSELS) },
        { key: 'wells', label: t('Wells or flasks'), value: '6' },
        { key: 'per', label: t('Cells per well'), value: '3e5' }, { key: 'percm', label: t('…or cells per cm²') },
        { key: 'vol', label: t('Medium per well (mL, blank: usual)') }, { key: 'extra', label: t('Extra (%)'), value: '10' },
      ],
      compute(v) {
        const vessel = VESSELS[Number(v.raw.vessel || 0)];
        const per = ok(n(v, 'percm')) ? n(v, 'percm') * vessel[1] : n(v, 'per');
        const wells = n(v, 'wells') * (1 + (n(v, 'extra') || 0) / 100);
        const vol = ok(n(v, 'vol')) ? n(v, 'vol') : vessel[2];
        const cells = per * wells; const susp = cells / n(v, 'susp');
        if (!ok(susp)) return { hint: t('Suspension density, cells per well and wells.') };
        const total = vol * wells;
        const out = { lines: [L(t('Cells needed'), fmt(cells, 3)), L(t('Cell suspension'), `${fmt(susp)} mL`, true)], warnings: [] };
        if (susp > total) {
          // No "−0.99 mL medium": say what would work instead.
          out.warnings.push(t('The suspension alone (%(susp)s mL) is more than the wells hold (%(total)s mL): spin the cells down and resuspend at %(density)s cells/mL or more.',
            { susp: fmt(susp), total: fmt(total), density: fmt(cells / total, 3) }));
        } else {
          out.lines.push(L(t('Add medium to'), t('%(total)s mL (%(medium)s mL medium)', { total: fmt(total), medium: fmt(total - susp) }), true));
        }
        out.lines.push(L(t('Per well'), `${fmt(vol)} mL · ${fmt(per / vessel[1], 3)} cells/cm²`));
        return out;
      },
    },
    {
      id: 'doubling', group: t('Cells'), title: t('Doubling time and growth'),
      blurb: t('From two counts; and how many cells after a time.'),
      inputs: [
        { key: 'n0', label: t('First count'), value: '2e5' }, { key: 'n1', label: t('Second count'), value: '1.6e6' },
        { key: 't', label: t('Time between'), units: 'time', unit: 'h', value: '72' },
        { key: 'later', label: t('Predict after (optional)'), units: 'time', unit: 'h' },
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
      id: 'transfection', group: t('Cells'), title: t('Scale a transfection'),
      blurb: t('From a condition that works in one vessel to another, by growth area.'),
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
      id: 'moi', group: t('Cells'), title: t('Virus for a multiplicity of infection'),
      blurb: t('Volume of virus for an MOI, from its titer.'),
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
      id: 'titer', group: t('Cells'), title: t('Lentivirus titer from % positive cells'),
      blurb: t('Use a dilution that gave 1–20 % positive cells, so most had one integration.'),
      inputs: [
        { key: 'cells', label: t('Cells at transduction'), value: '1e5' }, { key: 'pos', label: t('Positive (%)'), value: '12' },
        { key: 'vol', label: t('Virus added (µL)'), value: '1' }, { key: 'dil', label: t('Its dilution (×)'), value: '1' },
      ],
      compute(v) {
        const tu = n(v, 'cells') * n(v, 'pos') / 100 / (n(v, 'vol') * 1e-3 / (n(v, 'dil') || 1));
        if (!ok(tu)) return { hint: t('Cells, % positive and the virus volume.') };
        const out = { lines: [L(t('Titer'), `${fmt(tu, 3)} TU/mL`, true)], warnings: [] };
        if (n(v, 'pos') > 20 || n(v, 'pos') < 1) out.warnings.push(t('Outside 1–20 % positive: the titer is under- or over-estimated.'));
        return out;
      },
    },
    {
      id: 'freezing', group: t('Cells'), title: t('Freezing cells down'),
      blurb: t('Cells and freezing medium for a number of vials.'),
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
      id: 'treat', group: t('Cells'), title: t('Treating cells: drug and vehicle'),
      blurb: t('Stock to add to each well, and how much vehicle (DMSO, ethanol) the cells get.'),
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
        if (add < 0.5e-6) out.warnings.push(t('Under 0.5 µL per well: dilute the stock in medium first and add more of it.'));
        out.notes = [t('Give the control wells the same volume of vehicle alone.')];
        return out;
      },
    },

    /* ================================================= Microbes */
    {
      id: 'od600', group: t('Bacteria & yeast'), title: t('OD₆₀₀: cells and dilution'),
      blurb: t('Cells per mL from OD₆₀₀, and how to dilute a culture to a starting OD.'),
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
      id: 'growth', group: t('Bacteria & yeast'), title: t('Time to reach an OD'),
      blurb: t('In log phase, from its doubling time.'),
      inputs: [
        { key: 'od', label: t('OD now'), value: '0.05' }, { key: 'target', label: t('Wanted OD'), value: '0.6' },
        { key: 'dt', label: t('Doubling time'), units: 'time', unit: 'min', value: '30' },
      ],
      compute(v) {
        const secs = Math.log2(n(v, 'target') / n(v, 'od')) * b(v, 'dt');
        if (!ok(secs) || secs < 0) return { hint: t('OD now, a higher wanted OD and the doubling time.') };
        return { lines: [L(t('Time'), `${fmt(secs / 60, 3)} min (${fmt(secs / 3600, 3)} h)`, true), L(t('Doublings'), fmt(Math.log2(n(v, 'target') / n(v, 'od')), 3))],
          notes: [t('E. coli in LB at 37 °C doubles in about 20–30 min; yeast in YPD at 30 °C in about 90 min.')] };
      },
    },
    {
      id: 'antibiotic', group: t('Bacteria & yeast'), title: t('Antibiotic for plates and media'),
      blurb: t('Stock to add for the usual working concentration (change either).'),
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

    /* ================================================= Centrifuge, animals, gels */
    {
      id: 'rcf', group: t('Centrifuge, animals & gels'), title: t('rpm ↔ × g'),
      blurb: t('RCF = 1.118 × 10⁻⁵ × radius (cm) × rpm². Give one and the rotor radius.'),
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
      id: 'dose', group: t('Centrifuge, animals & gels'), title: t('Dose by body weight'),
      blurb: t('mg/kg to the amount and volume for each animal (tamoxifen, drugs, anaesthetics).'),
      inputs: [
        { key: 'dose', label: t('Dose (mg/kg)'), value: '75' }, { key: 'weight', label: t('Body weight'), units: 'weight', unit: 'g', value: '25' },
        { key: 'conc', label: t('Solution (mg/mL)'), value: '20' }, { key: 'animals', label: t('Animals'), value: '5' },
        { key: 'extra', label: t('Extra (%)'), value: '20' }, { key: 'limit', label: t('Most volume (mL/kg)'), value: '10', hint: t('e.g. 10 mL/kg i.p. in mice') },
      ],
      compute(v) {
        const kg = b(v, 'weight') / 1000; const mg = n(v, 'dose') * kg; const ml = mg / n(v, 'conc');
        if (!ok(ml)) return { hint: t('Dose, weight and solution strength.') };
        const out = { lines: [L(t('Amount'), `${fmt(mg)} mg`), L(t('Inject'), `${fmt(ml * 1000)} µL`, true), L(t('Volume per kg'), `${fmt(ml / kg)} mL/kg`)], warnings: [] };
        const k = (n(v, 'animals') || 1) * (1 + (n(v, 'extra') || 0) / 100);
        const count = n(v, 'animals') || 1;
        out.lines.push(L(t(count === 1 ? 'Solution for %(n)s animal (+%(extra)s %)' : 'Solution for %(n)s animals (+%(extra)s %)', { n: fmt(count), extra: fmt(n(v, 'extra') || 0) }), `${fmt(ml * k)} mL · ${fmt(ml * k * n(v, 'conc'))} mg`));
        if (ok(n(v, 'limit')) && ml / kg > n(v, 'limit')) out.warnings.push(t('More than %(limit)s mL/kg: make the solution stronger or split the dose.', { limit: fmt(n(v, 'limit')) }));
        return out;
      },
    },
    {
      id: 'agarose', group: t('Centrifuge, animals & gels'), title: t('Agarose gel'),
      blurb: t('Agarose for a gel, and which % separates your fragments.'),
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
      id: 'decay', group: t('Centrifuge, animals & gels'), title: t('Radioactive decay'),
      blurb: t('Activity left after a time, from the isotope\'s half-life.'),
      inputs: [
        { key: 'iso', label: t('Isotope'), type: 'select', options: ISOTOPES.map((r, i) => [String(i), `${r[0]} (t½ ${fmt(r[1])} d)`]) },
        { key: 'a0', label: t('Activity on the reference date'), value: '10' },
        { key: 'days', label: t('Days since then'), value: '14' },
      ],
      compute(v) {
        const [name, half] = ISOTOPES[Number(v.raw.iso || 0)];
        const frac = 0.5 ** (n(v, 'days') / half);
        if (!ok(frac)) return { hint: t('The days since the reference date.') };
        const out = { lines: [L(t('Left'), `${fmt(frac * 100, 3)} %`, true)] };
        if (ok(n(v, 'a0'))) out.lines.push(L(t('Activity now'), t('%(n)s (same unit)', { n: fmt(n(v, 'a0') * frac, 3) }), true));
        out.lines.push(L(t('Volume to use for the same activity'), t('%(x)s× the original', { x: fmt(1 / frac, 3) })));
        out.notes = [t('%(isotope)s: half-life %(days)s days.', { isotope: name, days: fmt(half) })];
        return out;
      },
    },

    /* ================================================= Numbers */
    {
      id: 'stats', group: t('Numbers & units'), title: t('Mean, SD, SEM, CV'),
      blurb: t('Paste numbers (one a line, or separated by spaces or commas).'),
      inputs: [{ key: 'data', label: t('Values'), type: 'textarea', value: '1.02 0.95 1.10 0.98 1.05' }],
      compute(v) {
        const xs = numbers(v.raw.data);
        if (xs.length < 2) return { hint: t('Two or more numbers.') };
        const k = xs.length; const mean = xs.reduce((a, c) => a + c, 0) / k;
        const sd = Math.sqrt(xs.reduce((a, c) => a + (c - mean) ** 2, 0) / (k - 1)); const sem = sd / Math.sqrt(k);
        const sorted = [...xs].sort((a, c) => a - c);
        const median = k % 2 ? sorted[(k - 1) / 2] : (sorted[k / 2 - 1] + sorted[k / 2]) / 2;
        const ci = tcrit(k - 1) * sem;
        return { lines: [L('n', String(k)), L(t('Mean'), fmt(mean), true), L('SD', fmt(sd), true), L('SEM', fmt(sem)), L('CV', `${fmt(sd / mean * 100, 3)} %`),
          L(t('Median'), fmt(median)), L(t('Range'), `${fmt(sorted[0])} – ${fmt(sorted[k - 1])}`), L(t('95 % CI of the mean'), `${fmt(mean - ci)} – ${fmt(mean + ci)}`)] };
      },
    },
    {
      id: 'samplesize', group: t('Numbers & units'), title: t('Animals or samples per group'),
      blurb: t('For comparing two group means (a two-sided t-test), before an experiment.'),
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
    {
      id: 'convert', group: t('Numbers & units'), title: t('Unit converter'),
      blurb: t('Between units of one kind.'),
      inputs: [
        { key: 'kind', label: t('Kind'), type: 'select', options: [['mass', t('Mass')], ['volume', t('Volume')], ['molar', t('Molar concentration')], ['massconc', t('Mass concentration')], ['amount', t('Amount (moles)')], ['time', t('Time')], ['temp', t('Temperature')]] },
        { key: 'value', label: t('Value'), value: '1' }, { key: 'from', label: t('From (unit)'), value: 'mg' },
      ],
      compute(v) {
        const kind = v.raw.kind || 'mass'; const x = n(v, 'value');
        if (!ok(x)) return { hint: t('A value.') };
        // The unit as typed: any case, u or μ for µ, µg/µL for mg/mL. A unit
        // of another kind (mg after switching to Temperature) is refused,
        // not read as °C.
        const typed = String(v.raw.from || '').trim().toLowerCase().replace(/[uμ]/g, 'µ').replace(/\s+/g, '');
        const kindName = (this.inputs[0].options.find(([k]) => k === kind) || [kind, kind])[1];
        if (kind === 'temp') {
          const scale = { c: 'C', '°c': 'C', celsius: 'C', f: 'F', '°f': 'F', fahrenheit: 'F', k: 'K', kelvin: 'K' }[typed];
          if (!scale) return { error: t('%(kind)s: °C, °F or K.', { kind: kindName }) };
          const c = scale === 'F' ? (x - 32) * 5 / 9 : scale === 'K' ? x - 273.15 : x;
          return { lines: [L('°C', fmt(c, 5), true), L('°F', fmt(c * 9 / 5 + 32, 5)), L('K', fmt(c + 273.15, 5))] };
        }
        const same = { 'µg/µl': 'mg/ml', 'mg/l': 'µg/ml', 'µg/l': 'ng/ml' }[typed] || typed;
        const row = U[kind].find(([u]) => u.toLowerCase().replace(/\s+/g, '') === same);
        if (!row) return { error: `${kindName}: ${U[kind].map(([u]) => u).join(', ')}.` };
        return { lines: U[kind].map(([u, g]) => L(u, fmt(x * row[1] / g, 5))) };
      },
    },
  ];

  const REFERENCES = [
    { id: 'ref-vessels', title: t('Culture vessels'), head: [t('Vessel'), t('Growth area (cm²)'), t('Usual medium (mL)')], rows: VESSELS.map((r) => [t(r[0]), fmt(r[1]), fmt(r[2])]) },
    { id: 'ref-buffers', title: t('Buffers'), head: [t('Buffer'), t('pKa at 25 °C'), t('Range'), t('ΔpKa / °C')], rows: BUFFERS.map((r) => [t(r[0]), fmt(r[1], 3), `${fmt(r[1] - 1, 2)}–${fmt(r[1] + 1, 2)}`, fmt(r[2], 2)]) },
    { id: 'ref-antibiotics', title: t('Antibiotics'), head: [t('Antibiotic'), t('Working (µg/mL)'), t('Stock (mg/mL)')], rows: ANTIBIOTICS.map((r) => [t(r[0]), fmt(r[1]), fmt(r[2])]) },
    { id: 'ref-gels', title: t('Agarose gels'), head: [t('Agarose'), t('Separates')], rows: GEL_RANGES },
    { id: 'ref-isotopes', title: t('Isotopes'), head: [t('Isotope'), t('Half-life (days)')], rows: ISOTOPES.map((r) => [r[0], fmt(r[1])]) },
  ];

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

  const api = { CALCS, REFERENCES, UNITS: U, CHEMICALS, run, num, fmt, show, dnaTm, protein, cleanDna, cleanProtein, zq };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.BenchCalc = api;
})(typeof window !== 'undefined' ? window : globalThis);
