// Run by tests/test_notebook_blocks.py: the Utilities calculators' arithmetic, in Node.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const B = createRequire(import.meta.url)('../../app/static/bench-calcs.js');
const main = (out) => out.lines.filter((l) => l.main).map((l) => l.value);
const value = (out, label) => (out.lines.find((l) => l.label === label) || {}).value;

// Every calculator runs on its own example values without an error.
for (const c of B.CALCS) {
  const out = B.run(c.id, {});
  assert.ok(!out.error, `${c.id}: ${out.error}`);
}

// 150 mM NaCl in 100 mL: 0.8766 g.
assert.deepEqual(main(B.run('molarity', { mass: '', volume: '100', conc: '150', conc_unit: 'mM', mw: '58.44' })), ['876.6 mg']);
// …and 95 % pure: 922.7 mg.
assert.deepEqual(main(B.run('molarity', { mass: '', volume: '100', conc: '150', conc_unit: 'mM', mw: '58.44', purity: '95' })), ['922.7 mg']);
// 10 mM stock to 10 µM in 1 mL: 1 µL (and a 1:1000 note); mixed families refused.
assert.equal(main(B.run('dilution', { c1: '10', c1_unit: 'mM', v1: '', c2: '10', c2_unit: 'µM', v2: '1', v2_unit: 'mL' }))[0], '1 µL');
assert.ok(B.run('dilution', { c1: '10', c1_unit: 'mM', v1: '', c2: '1', c2_unit: 'mg/mL', v2: '1' }).error);
// 10× to 1× in 500 mL: 50 mL stock.
assert.equal(main(B.run('xfold', {}))[0], '50 mL');
// 1 mg/mL BSA (66 430) is 15.05 µM.
assert.equal(main(B.run('massmolar', {}))[0], '15.05 µM');
// Tris pH 8.06 at 25 °C: base : acid = 1.
assert.equal(value(B.run('buffer', { buffer: '0', ph: '8.06', temp: '25' }), 'Base : acid'), '1 : 1');
// PBS as listed: 137×2 + 2.7×2 + 10×3 + 1.8×2 = 313 mOsm/L.
assert.equal(main(B.run('osmolarity', {}))[0], '313 mOsm/L');
// A260 1.0 of dsDNA: 50 ng/µL; 1 kb dsDNA: 100 ng = 0.1618 pmol.
assert.equal(main(B.run('a260', { a260: '1' }))[0], '50 ng/µL');
assert.equal(value(B.run('dnamoles', { length: '1000', mass: '100', mass_unit: 'ng' }), 'Moles'), '161.8 fmol');
// Oligo Tm matches the Primers database (M13 -20: 53.9 °C) and resuspension.
const oligo = B.run('oligo', { seq: 'GTAAAACGACGGCCAGT', nmol: '25', stock: '100' });
assert.equal(value(oligo, 'Tm'), '53.9 °C');
assert.equal(value(oligo, 'Water for 100 µM'), '250 µL');
// Ligation 3:1, 50 ng of 5 kb vector, 1 kb insert: 30 ng.
assert.equal(main(B.run('ligation', {}))[0], '30 ng');
// qPCR: slope −3.32 is 100 % efficient; ΔΔCt −3 is 8-fold.
assert.equal(value(B.run('qpcreff', { list: '', slope: '-3.3219' }), 'Efficiency'), '100 %');
assert.equal(value(B.run('ddct', { tc: '25', rc: '18', tt: '22', rt: '18' }), 'Fold change (2^−ΔΔCt)'), '8');
// Protein: ubiquitin (76 aa, 1 Tyr, no Trp): ε 1490, 8565 g/mol.
const ubq = 'MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG';
const pp = B.run('protparam', { seq: ubq });
assert.equal(value(pp, 'ε₂₈₀, Cys paired'), '1 490 M⁻¹cm⁻¹');
assert.ok(value(pp, 'MW').startsWith('8 565'), value(pp, 'MW'));
const pI = Number(value(pp, 'pI (approx.)'));
assert.ok(pI > 6 && pI < 8, `ubiquitin pI ${pI}`);
// A280 0.745 of ubiquitin: 500 µM.
assert.equal(main(B.run('a280', { seq: ubq, a280: '0.745' }))[0], '500 µM');
// A straight-line standard curve reads its own standards back.
const std = B.run('stdcurve', { std: '0 0\n1 2\n2 4\n3 6', unk: 'x 3', fit: 'lin' });
assert.equal(std.table.rows[0][2], '1.5');
// SDS-PAGE 12 %, two 5 mL gels: 4 mL of 30 % acrylamide.
assert.equal(B.run('sdspage', {}).table.rows[1][1], '4 mL');
// Haemocytometer: 212 over 4 squares, ×2: 1.06 × 10⁶ /mL.
assert.equal(main(B.run('count', {}))[0], '1 060 000 /mL');
// Doubling: 2e5 → 1.6e6 in 72 h is 3 doublings: 24 h.
assert.equal(main(B.run('doubling', {}))[0], '24 h');
// MOI 5 on 2e5 cells at 1e8/mL: 10 µL.
assert.equal(main(B.run('moi', {}))[0], '10 µL');
// Lentivirus: 1e5 cells, 12 % positive with 1 µL: 1.2 × 10⁷ TU/mL.
assert.equal(main(B.run('titer', {}))[0], '1.2 × 10⁷ TU/mL');
// 10 µM from 10 mM in 2 mL: 2 µL, 0.1 % vehicle.
assert.equal(main(B.run('treat', {}))[0], '2 µL');
// E. coli OD 2.4 into 50 mL at 0.1: 2.083 mL.
assert.equal(value(B.run('od600', {}), 'Culture to add'), '2.083 mL');
// 0.05 to 0.6 doubling every 30 min: 107.5 min, shown as 108.
assert.ok(main(B.run('growth', {}))[0].startsWith('108 min'));
// Ampicillin 100 µg/mL from 100 mg/mL into 500 mL: 500 µL.
assert.equal(main(B.run('antibiotic', {}))[0], '500 µL');
// 12 000 × g at 9.5 cm: 10 629 rpm.
assert.equal(main(B.run('rcf', { rpm: '', g: '12000' }))[0], '10 629 rpm');
// Tamoxifen 75 mg/kg, 25 g mouse, 20 mg/mL: 93.75 µL.
assert.equal(main(B.run('dose', {}))[0], '93.75 µL');
// ³²P after one half-life: 50 %.
assert.equal(main(B.run('decay', { iso: '0', days: '14.29' }))[0], '50 %');
// Mean and SD of 2, 4, 4, 4, 5, 5, 7, 9: 5 and 2.138.
const st = B.run('stats', { data: '2 4 4 4 5 5 7 9' });
assert.equal(value(st, 'Mean'), '5');
assert.equal(value(st, 'SD'), '2.138');
// Sample size: d = 1, α 0.05, power 0.8 → 17 per group.
assert.equal(main(B.run('samplesize', { diff: '1', sd: '1' }))[0], '17');
// 1 mg is 1000 µg; 37 °C is 98.6 °F.
assert.equal(value(B.run('convert', { kind: 'mass', value: '1', from: 'mg' }), 'µg'), '1 000');
assert.equal(value(B.run('convert', { kind: 'temp', value: '37', from: '°C' }), '°F'), '98.6');
// Numbers read as a lab types them.
assert.equal(B.num('2,5'), 2.5);
assert.equal(B.num('1,000'), 1000);
assert.equal(B.num('3e5'), 300000);
// From the re-test: no answer that isn't one.
assert.ok(B.run('dilution', { c1: '1', c1_unit: 'mM', c2: '10', c2_unit: 'mM', v2: '10', v2_unit: 'mL' }).error);
assert.match(B.run('convert', { kind: 'temp', value: '1', from: 'mg' }).error, /°C, °F or K/);
assert.equal(B.run('convert', { kind: 'massconc', value: '2', from: 'ug/ul' }).lines[1].value, '2');
{
  const r = B.run('seeding', { susp: '1e6', vessel: String(B.CALCS.find((c) => c.id === 'seeding').inputs[1].options.findIndex(([, l]) => /96/.test(l))), wells: '1', per: '3e5', extra: '0' });
  assert.ok(!r.lines.some((l) => /medium/.test(l.value) && /−|-/.test(l.value)), JSON.stringify(r.lines));
  assert.ok(r.warnings.length);
}
{
  const r = B.run('stdcurve', { std: '0 0.1\n250 0.35\n500 0.6\n1000 1.1\n2000 2.1', unk: 's1 0.6' });
  assert.ok(!/x²/.test(r.lines[0].value), r.lines[0].value);
  assert.equal(r.table.rows[0][2], '500');
}
assert.match(B.run('dose', { weight: '25', weight_unit: 'g', dose: '75', conc: '20', animals: '1', extra: '0' }).lines.map((l) => l.label).join(' '), /1 animal /);

// Replicate standards and unknowns are averaged, not read as a
// concentration and a reading (an unknown near 700 came out as 0.71).
{
  const r = B.run('stdcurve', { std: '0 0.10 0.11\n125 0.21 0.22\n250 0.33 0.32\n500 0.55 0.56\n1000 0.95 0.97\n2000 1.62 1.60', unk: 'Lysate 2 0.72 0.70', fit: 'lin' });
  const read = Number(r.table.rows[0][2].replace(/\s/g, ''));
  assert.ok(read > 600 && read < 800, r.table.rows[0][2]);
  assert.equal(r.table.rows[0][0], 'Lysate 2');
  assert.ok(r.notes.length);
  assert.ok(B.run('stdcurve', { std: '0 0.1\n125 0.21 0.30\n250 0.33\n500 0.55', unk: 'x 0.3' }).warnings.some((w) => /125/.test(w)));
}
// A280 with ε and MW typed over a sequence says it used the typed ones.
{
  const r = B.run('a280', { seq: ubq, eps: '2000', mw: '9000', a280: '1' });
  assert.equal(main(r)[0], '500 µM');
  assert.ok(r.notes.some((x) => /ε you typed/.test(x)) && r.notes.some((x) => /MW you typed/.test(x)), r.notes.join(' | '));
  assert.ok(!B.run('a280', { seq: ubq, a280: '0.745' }).notes.some((x) => /you typed/.test(x)));
}
// Pen–strep is for culture medium: no "add to agar" note; ampicillin keeps it.
{
  const list = B.CALCS.find((c) => c.id === 'antibiotic').inputs[0].options;
  const pick = (re) => String(list.findIndex(([, l]) => re.test(l)));
  assert.ok(!B.run('antibiotic', { drug: pick(/Penicillin/) }).notes.some((x) => /agar/.test(x)));
  assert.ok(B.run('antibiotic', { drug: pick(/Ampicillin/) }).notes.some((x) => /agar/.test(x)));
}

console.log('ok');
