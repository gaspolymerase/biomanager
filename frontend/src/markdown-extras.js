// How the notebook's richer blocks are written in a page's Markdown, so a
// page stays readable (and mostly drawn) anywhere else:
//
//   > [!WARNING]                 a callout: GitHub's own alert syntax
//   > Fix with 4 % PFA in the hood.
//
//   <details>                    a toggle: GitHub draws it as one
//   <summary>Raw counts</summary>
//
//   …hidden until opened…
//
//   </details>
//
//   :::: columns                 side by side: Pandoc's fenced divs
//   ::: column
//   left
//   :::
//   ::: column
//   right
//   :::
//   ::::
//
//   [knocked down]{.red .bg-yellow}   coloured text: Pandoc's bracketed spans
//   ==look here==                      a yellow highlight, as Obsidian writes it
//
// These markdown-it plugins turn them into the HTML the editor's nodes and
// marks read (extensions/RichBlocks.js). They are plain functions so
// tests/js/markdown-extras.check.mjs can run them without a browser.

export const CALLOUTS = {
  note: { label: 'Note', icon: 'ℹ️' },
  tip: { label: 'Tip', icon: '💡' },
  important: { label: 'Important', icon: '❗' },
  warning: { label: 'Warning', icon: '⚠️' },
  caution: { label: 'Caution', icon: '⛔' },
};

// Text colours and highlights, by the class names a page's Markdown uses.
export const COLORS = ['gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red'];

const CALLOUT_RE = /^\[!(note|tip|important|warning|caution)\][ \t]*(?:\n|$)/i;

function callouts(md) {
  md.core.ruler.after('block', 'nb_callout', (state) => {
    const tokens = state.tokens;
    for (let i = 0; i < tokens.length; i++) {
      if (tokens[i].type !== 'blockquote_open') continue;
      const para = tokens[i + 1];
      const inline = tokens[i + 2];
      if (!para || para.type !== 'paragraph_open' || !inline || inline.type !== 'inline') continue;
      const m = CALLOUT_RE.exec(inline.content);
      if (!m) continue;
      const level = tokens[i].level;
      let close = i + 1;
      while (close < tokens.length && !(tokens[close].type === 'blockquote_close' && tokens[close].level === level)) close++;
      if (close >= tokens.length) continue;
      const open = tokens[i];
      open.type = 'nb_callout_open';
      open.tag = 'div';
      open.attrs = [['data-callout', m[1].toLowerCase()]];
      tokens[close].type = 'nb_callout_close';
      tokens[close].tag = 'div';
      inline.content = inline.content.slice(m[0].length);
      if (!inline.content.trim()) tokens.splice(i + 1, 3);    // the marker had a line of its own
    }
  });
}

function lineText(state, line) {
  return state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
}

// A block that runs from an opening line to its own closing one, counting
// the openers and closers of the same kind in between.
function containerRule(md, name, { opens, closes, isOpener, onOpen, onClose }) {
  md.block.ruler.before('fence', name, (state, startLine, endLine, silent) => {
    if (state.sCount[startLine] - state.blkIndent >= 4) return false;
    const first = lineText(state, startLine);
    const opening = opens(first);
    if (!opening) return false;
    let depth = 0;
    let closeLine = -1;
    for (let line = startLine + 1; line < endLine; line++) {
      const text = lineText(state, line);
      if (state.sCount[line] < state.blkIndent) break;
      if (isOpener(text)) depth++;
      else if (closes(text)) {
        if (depth === 0) { closeLine = line; break; }
        depth--;
      }
    }
    if (closeLine < 0) return false;
    if (silent) return true;
    const oldParent = state.parentType;
    const oldMax = state.lineMax;
    state.parentType = name;
    state.lineMax = closeLine;
    let bodyStart = onOpen(state, opening, startLine);
    if (bodyStart === undefined) bodyStart = startLine + 1;
    state.md.block.tokenize(state, bodyStart, closeLine);
    onClose(state, opening);
    state.parentType = oldParent;
    state.lineMax = oldMax;
    state.line = closeLine + 1;
    return true;
  }, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
}

const DETAILS_OPEN = /^<details(?:\s+open)?\s*>\s*$/i;
const DETAILS_CLOSE = /^<\/details>\s*$/i;
const SUMMARY = /^<summary>(.*)<\/summary>\s*$/i;

function toggles(md) {
  containerRule(md, 'nb_toggle', {
    opens: (text) => DETAILS_OPEN.test(text) && { open: /open/i.test(text) },
    isOpener: (text) => DETAILS_OPEN.test(text),
    closes: (text) => DETAILS_CLOSE.test(text),
    onOpen(state, _opening, startLine) {
      const open = state.push('nb_toggle_open', 'details', 1);
      open.attrs = [['data-toggle', '']];
      open.block = true;
      open.map = [startLine, state.lineMax];
      let line = startLine + 1;
      while (line < state.lineMax && !lineText(state, line).trim()) line++;
      const m = line < state.lineMax ? SUMMARY.exec(lineText(state, line)) : null;
      state.push('nb_toggle_summary_open', 'summary', 1);
      const inline = state.push('inline', '', 0);
      inline.content = m ? m[1].trim() : '';
      inline.map = [line, line + 1];
      inline.children = [];
      state.push('nb_toggle_summary_close', 'summary', -1);
      return m ? line + 1 : startLine + 1;
    },
    onClose(state) {
      state.push('nb_toggle_close', 'details', -1).block = true;
    },
  });
}

const DIV_OPEN = /^(:{3,})\s*(?:\{\s*\.?([\w-]+)\s*\}|([\w-]+))\s*$/;
const DIV_ANY_OPEN = /^:{3,}\s*[^:\s]/;
const DIV_CLOSE = /^:{3,}\s*$/;

function columns(md) {
  containerRule(md, 'nb_columns', {
    opens: (text) => {
      const m = DIV_OPEN.exec(text);
      const cls = m && (m[2] || m[3]);
      return cls === 'columns' || cls === 'column' ? { cls } : null;
    },
    isOpener: (text) => DIV_ANY_OPEN.test(text),
    closes: (text) => DIV_CLOSE.test(text),
    onOpen(state, opening, startLine) {
      const token = state.push(`nb_${opening.cls}_open`, 'div', 1);
      token.attrs = [[`data-${opening.cls}`, '']];
      token.block = true;
      token.map = [startLine, state.lineMax];
    },
    onClose(state, opening) {
      state.push(`nb_${opening.cls}_close`, 'div', -1).block = true;
    },
  });
}

// [text]{.red .bg-yellow}: every class must be a colour we know, or it is
// left alone (and may be a link after all).
function tintClasses(raw) {
  const out = { color: '', bg: '' };
  const parts = raw.trim().split(/\s+/);
  if (!parts.length || !parts[0]) return null;
  for (const part of parts) {
    const m = /^\.(bg-)?([a-z]+)$/.exec(part);
    if (!m || !COLORS.includes(m[2])) return null;
    if (m[1]) out.bg = m[2];
    else out.color = m[2];
  }
  return out;
}

function tints(md) {
  md.inline.ruler.before('link', 'nb_tint', (state, silent) => {
    const start = state.pos;
    if (state.src.charCodeAt(start) !== 0x5B /* [ */) return false;
    const labelEnd = state.md.helpers.parseLinkLabel(state, start, false);
    if (labelEnd < 0) return false;
    const rest = state.src.slice(labelEnd + 1, state.posMax);
    const m = /^\{([^{}\n]*)\}/.exec(rest);
    if (!m) return false;
    const tint = tintClasses(m[1]);
    if (!tint) return false;
    if (!silent) {
      const open = state.push('nb_tint_open', 'span', 1);
      open.attrs = [];
      if (tint.color) open.attrs.push(['data-color', tint.color]);
      if (tint.bg) open.attrs.push(['data-bg', tint.bg]);
      const oldMax = state.posMax;
      state.pos = start + 1;
      state.posMax = labelEnd;
      state.md.inline.tokenize(state);
      state.posMax = oldMax;
      state.push('nb_tint_close', 'span', -1);
    }
    state.pos = labelEnd + 1 + m[0].length;
    return true;
  });
  // ==highlight== (yellow), as Obsidian and some other editors write it.
  md.inline.ruler.before('emphasis', 'nb_highlight', (state, silent) => {
    const start = state.pos;
    const src = state.src;
    if (src.charCodeAt(start) !== 0x3D || src.charCodeAt(start + 1) !== 0x3D) return false;
    if (src[start + 2] === ' ' || src[start + 2] === '=') return false;
    const end = src.indexOf('==', start + 2);
    if (end < 0 || end >= state.posMax || src[end - 1] === ' ' || src.slice(start + 2, end).includes('\n')) return false;
    if (!silent) {
      state.push('nb_tint_open', 'span', 1).attrs = [['data-bg', 'yellow']];
      const oldMax = state.posMax;
      state.pos = start + 2;
      state.posMax = end;
      state.md.inline.tokenize(state);
      state.posMax = oldMax;
      state.push('nb_tint_close', 'span', -1);
    }
    state.pos = end + 2;
    return true;
  });
}

export function richMarkdown(md) {
  if (md.__nbRich) return;
  md.__nbRich = true;
  callouts(md);
  toggles(md);
  columns(md);
  tints(md);
}

// The Markdown a tint mark closes with: "]{.red .bg-yellow}".
export function tintSuffix(attrs) {
  const parts = [];
  if (attrs.color && COLORS.includes(attrs.color)) parts.push(`.${attrs.color}`);
  if (attrs.bg && COLORS.includes(attrs.bg)) parts.push(`.bg-${attrs.bg}`);
  return parts.length ? `]{${parts.join(' ')}}` : ']';
}
