// Run by tests/test_notebook_blocks.py: the words a citation shows,
// "(Wang et al., 2024)", from a reading note's Authors and Year, in Node.
import assert from 'node:assert/strict';
import { citeLabel, citeText, splitAuthors, surname } from '../../frontend/src/cite.js';

// The authors as the app keeps them (Zotero, a DOI, BibTeX, RIS).
assert.deepEqual(splitAuthors('Xiaoming Wang; Hua Li; Zhen Chen'), ['Xiaoming Wang', 'Hua Li', 'Zhen Chen']);
assert.deepEqual(splitAuthors('Ann Lee; …'), ['Ann Lee']);
// As people type them.
assert.deepEqual(splitAuthors('Wang X, Li Y, Chen Z'), ['Wang X', 'Li Y', 'Chen Z']);
assert.deepEqual(splitAuthors('Wang, Xiaoming'), ['Wang, Xiaoming']);
assert.deepEqual(splitAuthors('Smith, John A.'), ['Smith, John A.']);
assert.deepEqual(splitAuthors('Wang, X. and Li, Y.'), ['Wang, X.', 'Li, Y.']);
assert.deepEqual(splitAuthors('Jane Doe & John Roe'), ['Jane Doe', 'John Roe']);
assert.deepEqual(splitAuthors('Wang X, Li Y and Chen Z'), ['Wang X', 'Li Y', 'Chen Z']);
assert.deepEqual(splitAuthors('Alexander Fleming'), ['Alexander Fleming']);
assert.deepEqual(splitAuthors('王小明、李华'), ['王小明', '李华']);
assert.deepEqual(splitAuthors(''), []);
assert.deepEqual(splitAuthors(undefined), []);

// A surname from each way of writing a name.
assert.equal(surname('Xiaoming Wang'), 'Wang');
assert.equal(surname('Wang, Xiaoming'), 'Wang');
assert.equal(surname('Wang XM'), 'Wang');
assert.equal(surname('Smith J.'), 'Smith');
assert.equal(surname('J. R. R. Tolkien'), 'Tolkien');
assert.equal(surname('Ludwig van Beethoven'), 'van Beethoven');
assert.equal(surname('Charles de la Fontaine'), 'de la Fontaine');
assert.equal(surname('Martin Luther King Jr.'), 'King');
assert.equal(surname('King, Martin Luther, Jr.'), 'King');
assert.equal(surname('Anne-Marie Dupont-Moreau'), 'Dupont-Moreau');
assert.equal(surname('Plato'), 'Plato');
// Chinese names whole, family name first (Zotero gives "小明 王").
assert.equal(surname('王小明'), '王小明');
assert.equal(surname('小明 王'), '王小明');
assert.equal(surname('王, 小明'), '王小明');

// The label.
assert.equal(citeLabel({ authors: 'Xiaoming Wang', year: '2024' }), 'Wang, 2024');
assert.equal(citeLabel({ authors: 'Xiaoming Wang; Hua Li', year: '2024' }), 'Wang & Li, 2024');
assert.equal(citeLabel({ authors: 'Xiaoming Wang; Hua Li; Zhen Chen', year: '2024' }), 'Wang et al., 2024');
assert.equal(citeLabel({ authors: 'Wang X, Li Y, Chen Z', year: '2023-05-01' }), 'Wang et al., 2023');
assert.equal(citeLabel({ authors: 'Xiaoming Wang', year: '' }), 'Wang, n.d.');
assert.equal(citeLabel({ authors: '王小明; 李华', year: '2024' }), '王小明 等, 2024');
assert.equal(citeLabel({ authors: '小明 王', year: '2024' }), '王小明, 2024');
// No authors: the start of the title.
assert.equal(citeLabel({ authors: '', year: '2021' }, 'Highly accurate protein structure prediction with AlphaFold'), 'Highly accurate protein structure…, 2021');
assert.equal(citeLabel({}, 'CRISPR screens'), 'CRISPR screens, n.d.');
assert.equal(citeLabel({}, ''), 'Untitled page, n.d.');
assert.equal(citeText({ authors: 'Jane Doe; John Roe; A. N. Other', year: '2019' }), '(Doe et al., 2019)');
console.log('ok');
