// Everything the notebook can insert, for the toolbar's Insert menu and the
// "/" menu typed at the start of a line. Items insert at the caret.

import { CALC_TYPES } from './blocks/index.js';
import { defaultCalc } from './blocks/calc.js';
import { defaultSheet } from './blocks/sheet.js';
import { stampTime } from './docops.js';
import { timers } from './timers.js';
import { api, ask, escapeHtml, isoDate, tr } from './util.js';
import { addSubPage, openColorMenu, openDatePicker, openPagePicker, openReminder, turnIntoPage } from './rich-menus.js';

export const SYMBOLS = [
  ['μ', 'micro'], ['°', 'degree'], ['℃', 'celsius'], ['±', 'plus-minus'], ['×', 'times'],
  ['÷', 'divide'], ['→', 'arrow right'], ['←', 'arrow left'], ['↑', 'up'], ['↓', 'down'], ['⇌', 'equilibrium'],
  ['Δ', 'delta'], ['α', 'alpha'], ['β', 'beta'], ['γ', 'gamma'], ['κ', 'kappa'], ['λ', 'lambda'],
  ['π', 'pi'], ['σ', 'sigma'], ['τ', 'tau'], ['Ω', 'ohm'], ['Å', 'angstrom'],
  ['∞', 'infinity'], ['≈', 'approx'], ['≤', 'less-eq'], ['≥', 'greater-eq'], ['≠', 'not-eq'],
  ['²', 'squared'], ['³', 'cubed'], ['⁻¹', 'inverse'], ['₂', 'sub 2'], ['₃', 'sub 3'], ['₄', 'sub 4'],
  ['✓', 'check'], ['✗', 'cross'], ['♀', 'female'], ['♂', 'male'], ['…', 'ellipsis'], ['—', 'em dash'],
];

function plateTable(rows, cols) {
  const letters = 'ABCDEFGHIJKLMNOP';
  const header = ['', ...Array.from({ length: cols }, (_, i) => `${i + 1}`)];
  return [
    `| ${header.join(' | ')} |`,
    `| ${header.map(() => '---').join(' | ')} |`,
    ...Array.from({ length: rows }, (_, r) => `| ${[letters[r], ...Array(cols).fill(' ')].join(' | ')} |`),
  ].join('\n');
}

export function pickFile(accept, { capture } = {}) {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    if (accept) input.accept = accept;
    if (capture) input.setAttribute('capture', capture);
    input.addEventListener('change', () => resolve(input.files && input.files[0]));
    input.click();
  });
}

export function formatBytes(n) {
  if (!n || n < 1024) return `${n || 0} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export async function uploadImage(editor, file, alt = '') {
  if (!file) return;
  const form = new FormData();
  form.append('image', file);
  try {
    const data = await api('/notebook/upload-image', { method: 'POST', form });
    const caption = alt || file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
    editor.chain().focus().setImage({ src: data.url, alt: caption, title: caption }).run();
  } catch (e) {
    ask.alert(`The picture could not be uploaded: ${e.message}`);
  }
}

export async function uploadFile(editor, file) {
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  try {
    const data = await api('/notebook/upload-file', { method: 'POST', form });
    editor.chain().focus().insertContent({ type: 'text', text: `${data.name} (${formatBytes(data.size)})`, marks: [{ type: 'link', attrs: { href: data.url } }] }).insertContent(' ').run();
  } catch (e) {
    ask.alert(`The file could not be uploaded: ${e.message}`);
  }
}

function md(markdown) {
  return (editor) => editor.chain().focus().insertContent(markdown).run();
}

function block(kind, value) {
  return (editor) => editor.chain().focus().insertLabBlock(kind, value).run();
}

// The caret, as something a menu can open beside.
function atCaret(editor) {
  const c = editor.view.coordsAtPos(editor.state.selection.from);
  return { getBoundingClientRect: () => ({ left: c.left, right: c.left, top: c.top, bottom: c.bottom }), contains: () => false };
}

export const ITEMS = [
  // ---- Basic: what any page is written with (keywords hold the Chinese, so "/标题" finds them too)
  { id: 'h1', group: 'Basic', icon: 'h1', label: 'Heading 1', hint: 'Or type # and a space', keywords: 'h1 title heading 标题 一级', run: (e) => e.chain().focus().toggleHeading({ level: 1 }).run() },
  { id: 'h2', group: 'Basic', icon: 'h2', label: 'Heading 2', hint: 'Or type ## and a space', keywords: 'h2 subtitle heading 标题 二级', run: (e) => e.chain().focus().toggleHeading({ level: 2 }).run() },
  { id: 'h3', group: 'Basic', icon: 'h3', label: 'Heading 3', hint: 'Or type ### and a space', keywords: 'h3 heading 标题 三级', run: (e) => e.chain().focus().toggleHeading({ level: 3 }).run() },
  { id: 'text', group: 'Basic', icon: 'text', label: 'Text', hint: 'Plain text', keywords: 'text paragraph plain 正文 文本', run: (e) => e.chain().focus().setParagraph().run() },
  { id: 'bullet', group: 'Basic', icon: 'bullet', label: 'Bulleted list', hint: 'Or type - and a space', keywords: 'bullet list unordered ul 列表 无序', run: (e) => e.chain().focus().toggleBulletList().run() },
  { id: 'numbered', group: 'Basic', icon: 'ordered', label: 'Numbered list', hint: 'Or type 1. and a space', keywords: 'numbered ordered list ol 列表 编号 有序', run: (e) => e.chain().focus().toggleOrderedList().run() },
  { id: 'todo', group: 'Basic', icon: 'task', label: 'Checklist', hint: 'Boxes to tick: to-dos', keywords: 'todo to-do checkbox checklist task 待办 清单 勾选', run: (e) => e.chain().focus().toggleTaskList().run() },
  { id: 'quote', group: 'Basic', icon: 'quote', label: 'Quote', hint: 'Or type > and a space', keywords: 'quote blockquote citation 引用', run: (e) => e.chain().focus().toggleBlockquote().run() },
  { id: 'divider', group: 'Basic', icon: 'divider', label: 'Divider', hint: 'A line across the page (or type ---)', keywords: 'divider line rule separator hr 分割线 分隔', run: (e) => e.chain().focus().setHorizontalRule().run() },
  { id: 'callout', group: 'Basic', icon: 'callout', label: 'Callout', hint: 'A coloured box: note, tip, warning…', keywords: 'callout note tip warning caution important box alert 提示 标注 注意 警告', run: (e) => e.chain().focus().setCallout('note').run() },
  { id: 'warning', group: 'Basic', icon: 'callout', label: 'Warning', hint: 'A callout for safety and things not to forget', keywords: 'warning caution danger safety hazard callout 警告 注意 安全', run: (e) => e.chain().focus().setCallout('warning').run() },
  { id: 'toggle', group: 'Basic', icon: 'toggle', label: 'Toggle', hint: 'A line that opens to show what it hides', keywords: 'toggle collapse collapsible details fold expand hide 折叠 展开 隐藏', run: (e) => e.chain().focus().setToggle().run() },
  { id: 'columns2', group: 'Basic', icon: 'columns', label: '2 columns', hint: 'Side by side, e.g. a gel and its notes', keywords: 'columns side by side layout two 分栏 两栏 并排', run: (e) => e.chain().focus().setColumns(2).run() },
  { id: 'columns3', group: 'Basic', icon: 'columns', label: '3 columns', hint: 'Three side by side', keywords: 'columns layout three 分栏 三栏 并排', run: (e) => e.chain().focus().setColumns(3).run() },
  { id: 'color', group: 'Basic', icon: 'palette', label: 'Colour and highlight', hint: 'Colour the selection, or this line', keywords: 'color colour highlight red yellow green blue mark 颜色 高亮 标记 红色', run: (e) => openColorMenu(e, atCaret(e)) },
  // Pages in pages, as Notion makes them: /page is a new page inside this one; or link one that exists ([[ too).
  { id: 'page', group: 'Basic', icon: 'page', label: 'Page', hint: 'A new page inside this one', keywords: 'page subpage sub-page new child nested 页面 子页面 新页面', run: (e) => addSubPage(e) },
  { id: 'page-link', group: 'Basic', icon: 'link', label: 'Link to page', hint: 'A page that exists (or type [[)', keywords: 'page link existing notebook mention reference 页面 链接 引用', run: (e) => openPagePicker(e) },
  { id: 'page-turn', group: 'Basic', icon: 'page', label: 'Turn into page', hint: 'This line becomes a page inside this one', keywords: 'page turn into convert subpage 页面 转换 子页面', run: (e) => turnIntoPage(e) },
  { id: 'toc', group: 'Basic', icon: 'toc', label: 'Table of contents', hint: 'This page’s headings, kept up to date', keywords: 'toc contents outline headings 目录 大纲', run: (e) => e.chain().focus().insertLabBlock('toc').run() },

  // ---- Data
  { id: 'sheet', group: 'Data', icon: 'table', label: 'Data sheet', hint: 'Enter data; plot it; t-test / ANOVA', keywords: 'table spreadsheet plot chart graph stats', run: block('sheet') },
  { id: 'sheet-xy', group: 'Data', icon: 'chart', label: 'X–Y plot', hint: 'A sheet set up for a scatter with a fitted line', keywords: 'scatter line regression standard curve', run: block('sheet', { ...defaultSheet(), columns: [{ name: 'x', type: 'number' }, { name: 'y', type: 'number' }], rows: [['', ''], ['', ''], ['', ''], ['', '']], chart: { type: 'scatter', x: 0, y: [1], group: -1, error: 'sem', fit: true, logY: false }, showStats: false, stats: { group: 0, value: 1, test: 'none', control: '' } }) },
  { id: 'plate', group: 'Data', icon: 'plate', label: 'Plate reader', hint: 'Heatmap, layout, standard curve', keywords: 'bca elisa absorbance 96 well plate', run: block('plate') },
  { id: 'experiment', group: 'Data', icon: 'flask', label: 'Colony experiment', hint: 'A mouse experiment’s manipulations and body weights', keywords: 'mouse experiment cohort injection dose treatment tamoxifen body weight colony', run: block('experiment') },
  { id: 'qpcr', group: 'Data', icon: 'chart', label: 'qPCR ΔΔCt', hint: 'Paste Ct values, get fold change', keywords: 'pcr ct cq ddct expression', run: block('qpcr') },
  // ---- Bench
  // The protocol library opens in the page's side panel (notebook-page.js), where one is picked and inserted.
  { id: 'protocol', group: 'Bench', icon: 'task', label: 'Protocol', hint: 'Insert a lab or common protocol', keywords: 'protocol method procedure sop steps library', run: () => window.dispatchEvent(new CustomEvent('nb:open-panel', { detail: { name: 'protocols' } })) },
  { id: 'recipe', group: 'Bench', icon: 'flask', label: 'Buffer recipe', hint: 'Masses and volumes for any volume', keywords: 'buffer media solution pbs recipe', run: block('recipe') },
  { id: 'formulation', group: 'Bench', icon: 'flask', label: 'Formulation', hint: 'Chemicals by mass: moles, equivalents, wt %', keywords: 'formulation reaction synthesis stoichiometry moles mmol equivalents equiv chemicals weigh mass batch 配方 摩尔', run: block('formulation') },
  ...Object.entries(CALC_TYPES).map(([type, label]) => ({
    id: `calc-${type}`, group: 'Bench', icon: 'calc', label, hint: 'Calculator', keywords: `calculator ${type}`, run: block('calc', defaultCalc(type)),
  })),
  { id: 'timer', group: 'Bench', icon: 'timer', label: 'Timer', hint: 'Start a countdown', keywords: 'countdown clock alarm', run: () => timers().ask() },
  { id: 'stamp', group: 'Bench', icon: 'clock', label: 'Time stamp', hint: 'Now, in bold (Ctrl+Shift+;)', keywords: 'time now clock stamp', run: (e) => stampTime(e) },
  { id: 'date', group: 'Bench', icon: 'calendar', label: "Today's date", hint: isoDate(), keywords: 'date today 今天 日期', run: (e) => e.chain().focus().insertContent(isoDate()).run() },
  { id: 'pick-date', group: 'Bench', icon: 'calendar', label: 'Date…', hint: 'Pick a day', keywords: 'date day pick calendar 日期', run: (e) => openDatePicker(e) },
  { id: 'reminder', group: 'Bench', icon: 'bell', label: 'Reminder', hint: 'A to-do in your calendar, linked here', keywords: 'reminder remind alarm todo later follow up 提醒 待办', run: (e) => openReminder(e) },
  { id: 'steps', group: 'Bench', icon: 'task', label: 'Protocol steps', hint: 'A checklist to run step by step', keywords: 'checklist task steps protocol', run: md('\n- [ ] Step one\n- [ ] Incubate 10 min\n- [ ] Step three\n') },
  // ---- Diagrams & maths
  { id: 'mermaid', group: 'Diagrams & maths', icon: 'flow', label: 'Flowchart', hint: 'Mermaid diagram', keywords: 'mermaid diagram flow graph', run: block('mermaid') },
  { id: 'sequence', group: 'Diagrams & maths', icon: 'flow', label: 'Sequence diagram', hint: 'Mermaid', keywords: 'mermaid sequence', run: block('mermaid', 'sequenceDiagram\n  Cell->>Nucleus: Signal\n  Nucleus-->>Cell: Transcription') },
  { id: 'gantt', group: 'Diagrams & maths', icon: 'flow', label: 'Timeline (Gantt)', hint: 'Plan an experiment over days', keywords: 'gantt timeline schedule plan', run: block('mermaid', `gantt\n  dateFormat YYYY-MM-DD\n  title Experiment plan\n  section Prep\n  Order reagents :a1, ${isoDate()}, 3d\n  section Run\n  Treatment :after a1, 7d\n  Harvest :2d`) },
  { id: 'mindmap', group: 'Diagrams & maths', icon: 'mindmap', label: 'Mind map', hint: 'From an indented list', keywords: 'mind map brainstorm ideas', run: block('mindmap') },
  { id: 'math', group: 'Diagrams & maths', icon: 'sigma', label: 'Equation', hint: 'LaTeX, on its own line', keywords: 'math latex formula equation katex', run: block('math') },
  { id: 'math-inline', group: 'Diagrams & maths', icon: 'sigma', label: 'Inline equation', hint: 'Or type $…$ in the text', keywords: 'math latex inline', run: (e) => { const latex = prompt('Equation (LaTeX):', 'x^2'); if (latex) e.chain().focus().insertContent({ type: 'mathInline', attrs: { latex } }).run(); } },
  // ---- Media
  { id: 'image', group: 'Pictures & files', icon: 'image', label: 'Picture', hint: 'Upload an image (or paste / drop one)', keywords: 'image photo picture gel blot', run: async (e) => uploadImage(e, await pickFile('image/*')) },
  { id: 'camera', group: 'Pictures & files', icon: 'camera', label: 'Take a photo', hint: 'On a phone or tablet', keywords: 'camera photo', run: async (e) => uploadImage(e, await pickFile('image/*', { capture: 'environment' })) },
  { id: 'file', group: 'Pictures & files', icon: 'attach', label: 'Attachment', hint: 'PDF, raw data, anything', keywords: 'file attach pdf upload', run: async (e) => uploadFile(e, await pickFile('')) },
  // ---- Text
  { id: 'table', group: 'Tables & text', icon: 'table', label: 'Table', hint: '3 × 3', keywords: 'table grid', run: (e) => e.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
  { id: 'materials', group: 'Tables & text', icon: 'table', label: 'Materials & lots', hint: 'Reagent · Vendor · Cat # · Lot', keywords: 'reagent lot inventory materials', run: md('\n| Reagent | Vendor | Cat # | Lot | Amount |\n| --- | --- | --- | --- | --- |\n|  |  |  |  |  |\n') },
  { id: 'mice', group: 'Tables & text', icon: 'table', label: 'Mouse list', hint: '@mouse · Genotype · Group', keywords: 'mouse animals cohort', run: md('\n| Mouse | Sex | Genotype | Group | Note |\n| --- | --- | --- | --- | --- |\n| @mouse  |  |  |  |  |\n') },
  { id: 'plate96', group: 'Tables & text', icon: 'plate', label: '96-well layout', hint: 'A text grid to plan a plate', keywords: 'plate layout 96', run: md(`\n${plateTable(8, 12)}\n`) },
  { id: 'code', group: 'Tables & text', icon: 'code', label: 'Code block', hint: 'Scripts, sequences, commands', keywords: 'code script sequence', run: (e) => e.chain().focus().toggleCodeBlock().run() },
  { id: 'symbol', group: 'Tables & text', icon: 'symbol', label: 'Symbol', hint: 'μ ° ± → Δ α β', keywords: 'symbol greek micro degree', run: (e) => openSymbolPicker(e) },
  { id: 'template', group: 'Tables & text', icon: 'template', label: 'From a template', hint: 'Insert a saved template here', keywords: 'template snippet', run: (e) => openTemplatePicker(e) },
];

export function filterItems(query) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return ITEMS;
  const hits = ITEMS.filter((i) => `${i.label} ${tr(i.label)} ${i.keywords || ''} ${i.group} ${tr(i.group)}`.toLowerCase().includes(q));
  // A name that starts with what was typed comes first ("/to": To-do, Toggle, Table of contents).
  const starts = (i) => {
    if (i.label.toLowerCase().startsWith(q) || tr(i.label).toLowerCase().startsWith(q)) return 0;
    return (i.keywords || '').toLowerCase().split(' ').some((w) => w.startsWith(q)) ? 1 : 2;
  };
  return hits.map((item, n) => ({ item, n })).sort((a, b) => starts(a.item) - starts(b.item) || a.n - b.n).map((x) => x.item);
}

function overlayMenu(html) {
  const overlay = document.createElement('div');
  overlay.className = 'insert-menu-overlay';
  overlay.innerHTML = `<div class="insert-menu">${html}</div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (event) => { if (event.target === overlay) overlay.remove(); });
  const onKey = (event) => { if (event.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
  return overlay;
}

export function openSymbolPicker(editor) {
  const overlay = overlayMenu(`<div class="insert-menu-label">Insert symbol</div><div class="insert-symbol-grid">${SYMBOLS.map(([s, label]) => `<button type="button" class="insert-symbol-btn" data-sym="${s}" title="${label}">${s}</button>`).join('')}</div>`);
  overlay.addEventListener('click', (event) => {
    const b = event.target.closest('.insert-symbol-btn');
    if (!b) return;
    editor.chain().focus().insertContent(b.dataset.sym).run();
    overlay.remove();
  });
}

export async function openTemplatePicker(editor) {
  const overlay = overlayMenu('<div class="insert-menu-label">Insert a template</div><div class="insert-templates-list">Loading…</div>');
  const list = overlay.querySelector('.insert-templates-list');
  try {
    const data = await api('/notebook/templates');
    if (!data.templates.length) {
      list.innerHTML = '<div class="insert-empty">No templates yet. Save a page as a template first.</div>';
      return;
    }
    list.innerHTML = data.templates.map((t) => `<button type="button" class="insert-template-row" data-id="${t.id}"><span class="insert-template-icon">${escapeHtml(t.icon || '📄')}</span><span><strong>${escapeHtml(t.title)}</strong><small>${escapeHtml(t.body_preview || '')}</small></span></button>`).join('');
    list.addEventListener('click', async (event) => {
      const row = event.target.closest('.insert-template-row');
      if (!row) return;
      const full = await api(`/notebook/templates/${row.dataset.id}`);
      editor.chain().focus().insertContent(full.template.body || '').run();
      overlay.remove();
    });
  } catch (_e) {
    list.innerHTML = '<div class="insert-empty">Could not load templates.</div>';
  }
}
