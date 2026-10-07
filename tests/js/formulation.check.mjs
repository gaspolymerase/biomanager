// Run by tests/test_notebook_blocks.py: a Formulation's grams, moles and
// equivalents, and which pasted sheets are drawn as a line over time, in Node.
import assert from 'node:assert/strict';
import { compute, showMol } from '../../frontend/src/blocks/formulation.js';
import { looksLikeLog } from '../../frontend/src/blocks/sheet.js';

const close = (a, b, tol = 1e-9) => assert.ok(Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)), `${a} ≠ ${b}`);

// Methyl methacrylate weighed, AIBN at 0.01 equiv, DMF by mass; the basis is MMA.
const data = {
  basis: 0,
  components: [
    { name: 'MMA', mw: '100.12', mass: '10.012', unit: 'g', given: 'mass', density: '0.94' },
    { name: 'AIBN', mw: '164.21', eq: '0.01', given: 'eq', unit: 'mg' },
    { name: 'DMF', mw: '73.09', mass: '20', unit: 'g', given: 'mass' },
  ],
};
let { rows, total } = compute(data);
close(rows[0].mol, 0.1);
close(rows[0].eq, 1);
close(rows[0].mL, 10.012 / 0.94);
close(rows[1].mol, 0.001);
close(rows[1].grams, 0.16421);
close(rows[2].eq, (20 / 73.09) / 0.1);
close(total, 10.012 + 0.16421 + 20);
close(rows[2].wt, (20 / total) * 100);

// Purity: 98 % pure means more to weigh for the same moles, fewer moles from the same mass.
data.components[1].purity = '98';
close(compute(data).rows[1].grams, 0.16421 / 0.98);
data.components[2].purity = '50';
close(compute(data).rows[2].mol, 10 / 73.09);

// A row in equivalents follows the basis; without a basis mass it has none.
data.components[0].mass = '5.006';
close(compute(data).rows[1].mol, 0.0005);
data.components[0].mass = '';
assert.ok(Number.isNaN(compute(data).rows[1].grams));

// A mass with no molecular weight still counts in the total, with no moles.
({ rows, total } = compute({ basis: 0, components: [{ name: 'Filler', mass: '2', unit: 'kg', given: 'mass' }] }));
close(total, 2000);
assert.ok(Number.isNaN(rows[0].mol));

assert.equal(showMol(0.1), '100 mmol');
assert.equal(showMol(2.5e-6), '2.5 µmol');

// Logs: time down the first column, readings beside it.
const cols = (...names) => names.map((name) => ({ name, type: 'number' }));
const log = [['0', '25', '1.0'], ['1', '40', '1.2'], ['2', '60', '1.9'], ['3', '80', '2.4'], ['4', '80', '2.5']];
assert.ok(looksLikeLog(cols('Time (min)', 'T (°C)', 'P (bar)'), log));
assert.ok(looksLikeLog(cols('Run', 'T (°C)', 'P (bar)'), log), 'a steadily rising first column');
assert.ok(looksLikeLog(cols('时间', '温度'), [['10:00', '25'], ['10:05', '30'], ['10:10', '41'], ['10:15', '52'], ['10:20', '60']]));
// Groups to compare are not logs, whatever the header says.
const groups = [['Control', '1'], ['Control', '2'], ['Control', '3'], ['Treated', '5'], ['Treated', '6']];
assert.ok(!looksLikeLog(cols('Sample(s)', 'Value'), groups));
assert.ok(!looksLikeLog(cols('Dose', 'Response'), [['10', '1'], ['1', '2'], ['100', '3'], ['5', '4'], ['50', '5']]));
assert.ok(!looksLikeLog(cols('Time', 'Value'), log.slice(0, 4)), 'too short to be a log');
console.log('ok');
