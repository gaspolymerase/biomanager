// Run by tests/test_notebook_blocks.py: BioHtml.escape (app/static/html.js), which every page
// script uses for text it puts into HTML.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const { escape } = createRequire(import.meta.url)('../../app/static/html.js');

assert.equal(escape(`"'><img src=x onerror="alert(1)">`), '&quot;&#39;&gt;&lt;img src=x onerror=&quot;alert(1)&quot;&gt;');
assert.equal(escape('Tom & Jerry <3'), 'Tom &amp; Jerry &lt;3');
assert.equal(escape('&amp;'), '&amp;amp;');                 // escaped once more, never left as markup
assert.equal(escape(null), '');
assert.equal(escape(undefined), '');
assert.equal(escape(0), '0');
assert.equal(escape('Cre/+; Ai14/+ · 种鱼'), 'Cre/+; Ai14/+ · 种鱼');
