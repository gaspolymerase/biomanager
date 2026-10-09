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
// Tris pH 8.06 at 25 °C: base : acid = 1.
assert.equal(value(B.run('buffer', { buffer: '0', ph: '8.06', temp: '25' }), 'Base : acid'), '1 : 1');
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
assert.equal(main(B.run('count', {}))[0], '1.06 × 10⁶ /mL');
// Doubling: 2e5 → 1.6e6 in 72 h is 3 doublings: 24 h.
assert.equal(main(B.run('doubling', {}))[0], '24 h');
// MOI 5 on 2e5 cells at 1e8/mL: 10 µL.
assert.equal(main(B.run('moi', {}))[0], '10 µL');
// Lentivirus: 1e5 cells, 12 % positive with 1 µL: 1.2 × 10⁷ TU/mL; with
// 1.4 % from 0.1 µL too, the two in range are averaged and 58 % is left out.
assert.equal(main(B.run('titer', { list: '1 12' }))[0], '1.2 × 10⁷ TU/mL');
assert.equal(main(B.run('titer', {}))[0], '1.3 × 10⁷ TU/mL');
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
// Sample size: d = 1, α 0.05, power 0.8 → 17 per group.
assert.equal(main(B.run('samplesize', { diff: '1', sd: '1' }))[0], '17');
// Numbers read as a lab types them.
assert.equal(B.num('2,5'), 2.5);
assert.equal(B.num('1,000'), 1000);
assert.equal(B.num('3e5'), 300000);
// From the re-test: no answer that isn't one.
assert.ok(B.run('dilution', { c1: '1', c1_unit: 'mM', c2: '10', c2_unit: 'mM', v2: '10', v2_unit: 'mL' }).error);
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

// ---- The redesign's tools.
// Every tool's every mode runs on what it opens with.
for (const tool of B.TOOLS) {
  for (const m of tool.modes) {
    const out = B.run(m.calc, m.example || {});
    assert.ok(!out.error && ((out.lines || []).length || out.table), `${tool.id}/${m.calc}: ${JSON.stringify(out).slice(0, 120)}`);
  }
}
// Every calculator belongs to a tool, and old addresses still open one.
for (const c of B.CALCS) assert.ok(B.TOOLS.some((x) => x.modes.some((m) => m.calc === c.id)), c.id);
assert.equal(B.locate('dilution').tool.id, 'dilute');
assert.equal(B.locate('xfold').tool.id, 'dilute');
assert.deepEqual([B.locate('titer').tool.id, B.locate('titer').mode], ['virus', 1]);
// Search finds what the testers typed, best first.
const first = (q) => (B.search(q)[0] || {}).id;
for (const [q, id] of [['c1v1', 'dilute'], ['amp', 'antibiotic'], ['carb', 'antibiotic'], ['lb', 'antibiotic'], ['sds', 'sdspage'],
  ['nanodorp', 'a260'], ['primers', 'primer'], ['delta-delta', 'ddct'], ['70% ethanol', 'dilute'], ['hemocytomter', 'count'],
  ['passage', 'split'], ['pen strep', 'antibiotic'], ['ng/ul to nm', 'copies'], ['centrifuge speed', 'rcf'], ['tamoxifen', 'dose'],
  ['稀释', 'dilute'], ['配溶液', 'make'], ['离心', 'rcf'], ['铺板', 'seed'], ['上样', 'loading'], ['amicon', 'concentrate']]) {
  assert.equal(first(q), id, `${q} → ${first(q)}`);
}
// A mg/mL stock to a µM final through the MW: 1 mg/mL of 50 kDa is 20 µM.
assert.equal(main(B.run('dilution', { c1: '1', c1_unit: 'mg/mL', c2: '10', c2_unit: 'µM', v2: '1', v2_unit: 'mL', mw: '50000' }))[0], '500 µL');
assert.ok(B.run('dilution', { c1: '1', c1_unit: 'mg/mL', c2: '10', c2_unit: 'µM', v2: '1' }).error);
// 1 L of 1 M Tris pH 8.0: 121.1 g of base and about 44.5 mL of 37 % HCl.
{
  const r = B.run('buffer', { buffer: '0', ph: '8', conc: '1', conc_unit: 'M', volume: '1', volume_unit: 'L' });
  assert.equal(value(r, 'Weigh'), '121.1 g Tris base');
  assert.equal(value(r, 'Titrate with about'), '44.54 mL 37 % HCl (12 M)');
}
// 37 % HCl is 12 M: 8.35 mL in 100 mL makes 1 M.
assert.equal(main(B.run('fromconc', {}))[0], '8.351 mL');
// A 3:1 ligation in 20 µL, and an unpipettable insert flagged.
assert.equal(B.run('ligation', { iconc: '25', vconc: '50' }).table.rows[4][1], '14.8');
assert.ok(B.run('ligation', { iconc: '200', vconc: '50' }).warnings.length);
// Six fragments at 0.1 pmol each and twice that: too much DNA for HiFi.
assert.ok(B.run('assembly', { list: 'v 5000 100\na 1000 100\nb 1000 100\nc 1000 100\nd 1000 100\ne 1000 100', vpmol: '0.1' }).warnings.some((w) => /pmol/.test(w)));
// ΔΔCt from triplicates: MYC up 6.96-fold.
assert.equal(main(B.run('ddcttable', {}))[0], '6.96-fold');
assert.ok(B.run('ddct', { tt: '36' }).warnings.length);
// A digest of 1 µg, two 20 U/µL enzymes, 50 µL: 0.5 µL each, 39 µL water.
assert.equal(main(B.run('digest', {}))[0], '39 µL');
// Normalising RNA: 1000 ng from 412 ng/µL is 2.4 µL.
assert.equal(B.run('normalise', {}).table.rows[0][1], '2.4');
// 12 mg at 90 % to 5 mg/mL: 2.16 mL. Two changes of 1 L for 5 mL: 1 : 201 each.
assert.equal(main(B.run('concentrate', {}))[0], '2.16 mL');
assert.equal(value(B.run('dialysis', {}), 'Each change dilutes'), '1 : 201');
// A T75 at 80 % in 3 days, doubling daily: 1.5 × 10⁶ cells.
assert.match(main(B.run('split', {}))[0], /^1.5 × 10⁶/);
// The dose series keeps 0.1 % DMSO; a 2 mL well takes 2 µL directly.
assert.match(B.run('doseseries', { well: '2000' }).lines[2].value, /^2 µL/);
assert.ok(B.run('doseseries', { top: '100' }).error);
// Lentivirus packaging 4:3:1 of 10 µg: 5, 3.75 and 1.25 µg.
assert.deepEqual(B.run('lentipack', { n: '1' }).table.rows.slice(0, 3).map((r) => r[1]), ['5 µg', '3.75 µg', '1.25 µg']);
// 20 plates of 25 mL (+10 %): 22 g LB agar; ampicillin 550 µL.
assert.equal(value(B.run('plates', { drug: '0' }), 'LB agar powder'), '22 g');
// Dosing sheet: 75 mg/kg of a 24.6 g mouse at 20 mg/mL is 92.25 µL.
assert.equal(B.run('dosesheet', {}).table.rows[0][3], '92.3 µL');

// ---- The lab's own tools: formulas read by hand, never run as code.
{
  const f = (src, vars = { a: 2, b: 3 }) => B.evalFormula(B.parseFormula(src, Object.keys(vars)), vars);
  assert.equal(f('a + b * 2'), 8);
  assert.equal(f('(a + b) * 2'), 10);
  assert.equal(f('a ^ b ^ 2'), 512);          // right to left, as on paper
  assert.equal(f('-a ^ 2'), -4);
  assert.equal(f('a × b ÷ 2 − 1'), 2);
  assert.equal(f('sqrt(a * 8) + min(a, b) + log10(1000)'), 9);
  assert.equal(f('1e3 * a + .5'), 2000.5);
  for (const bad of ['a +', '((a)', 'c + 1', 'foo(a)', 'pow(a)', 'a % b', 'a b', 'alert(1)', 'a.constructor', '', 'this']) {
    assert.throws(() => B.parseFormula(bad, ['a', 'b']), Error, bad);
  }
  const def = { id: 'labtest1', name: 'Glycerol stock', inputs: [{ name: 'culture', label: 'Culture', unit: 'µL', value: '500' },
    { name: 'want', label: 'Wanted', unit: '%', value: '15' }, { name: 'stock', label: 'Stock', unit: '%', value: '50' }],
  outputs: [{ label: 'Glycerol to add', formula: 'culture * want / (stock - want)', unit: 'µL', name: 'add' }, { label: 'In all', formula: 'culture + add', unit: 'µL' }] };
  B.useLabTools([def]);
  assert.equal(B.locate('labtest1').tool.group, 'lab');
  assert.equal(main(B.run('labtest1', {}))[0], '214.3 µL');
  assert.equal(value(B.run('labtest1', {}), 'In all'), '714.3 µL');
  assert.ok(B.run('labtest1', { stock: '15' }).warnings.length);            // ÷ 0
  assert.ok(B.run('labtest1', { culture: '' }).hint);
  assert.equal(first('glycerol stock'), 'labtest1');
  const made = B.labTool({ id: 'x', inputs: [{ name: '1x' }, { name: 'a' }, { name: 'a' }], outputs: [{ formula: 'a +' }, { formula: 'a', name: 'a' }] });
  assert.deepEqual(Object.keys(made.problems).sort(), ['in0', 'in2', 'out0', 'out1']);
  B.useLabTools([]);
  assert.equal(B.locate('labtest1'), null);
}

console.log('ok');
