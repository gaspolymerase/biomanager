// Run by tests/test_notebook_blocks.py: how callouts, toggles, columns and
// colours are read from a page's Markdown (frontend/src/markdown-extras.js).
import assert from 'node:assert/strict';
import MarkdownIt from '../../frontend/node_modules/markdown-it/index.mjs';
import { richMarkdown, tintSuffix } from '../../frontend/src/markdown-extras.js';

const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
richMarkdown(md);
const html = (src) => md.render(src);

// GitHub's alerts are callouts; a plain quote stays a quote.
assert.match(html('> [!WARNING]\n> Use the **hood**.'), /<div data-callout="warning">\s*<p>Use the <strong>hood<\/strong>.<\/p>\s*<\/div>/);
assert.match(html('> [!note]\n> lower case too'), /data-callout="note"/);
assert.match(html('> just a quote'), /<blockquote>/);

// <details> is a toggle, its summary read as Markdown, nested ones counted.
const toggle = html('<details>\n<summary>Raw *counts*</summary>\n\n<details>\n<summary>inner</summary>\n\nx\n\n</details>\n\nafter\n\n</details>\nnext');
assert.match(toggle, /^<details data-toggle="">\s*<summary>Raw <em>counts<\/em><\/summary>\s*<details data-toggle="">\s*<summary>inner<\/summary>\s*<p>x<\/p>\s*<\/details>\s*<p>after<\/p>\s*<\/details>\s*<p>next<\/p>/);

// Pandoc's fenced divs are columns.
const cols = html(':::: columns\n::: column\nleft\n:::\n::: column\n- a\n- b\n:::\n::::');
assert.match(cols, /^<div data-columns="">\s*<div data-column="">\s*<p>left<\/p>\s*<\/div>\s*<div data-column="">\s*<ul>[\s\S]*<\/ul>\s*<\/div>\s*<\/div>/);

// Colours: only the ones the notebook has; anything else is left as it was.
assert.match(html('[late]{.red .bg-yellow}'), /<span data-color="red" data-bg="yellow">late<\/span>/);
assert.match(html('[x]{.nope}'), /\[x\]\{\.nope\}/);
assert.match(html('[a link](https://example.org)'), /<a href="https:\/\/example.org">a link<\/a>/);
assert.match(html('==look==' ), /<span data-bg="yellow">look<\/span>/);
assert.equal(tintSuffix({ color: 'red', bg: 'yellow' }), ']{.red .bg-yellow}');
assert.equal(tintSuffix({ color: 'evil" onclick', bg: '' }), ']');

// Still no raw HTML from a page.
assert.match(html('<script>alert(1)</script>'), /&lt;script&gt;/);
assert.match(html('<details><img src=x onerror=alert(1)></details>'), /&lt;details&gt;/);
console.log('ok');
