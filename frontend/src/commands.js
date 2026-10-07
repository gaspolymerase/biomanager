// Everything the notebook can insert, for the toolbar's Insert menu and the
// "/" menu typed at the start of a line. Items insert at the caret.

import { CALC_TYPES } from './blocks/index.js';
import { defaultCalc } from './blocks/calc.js';
import { defaultSheet } from './blocks/sheet.js';
import { stampTime } from './docops.js';
import { timers } from './timers.js';
import { api, ask, escapeHtml, isoDate } from './util.js';

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

export const ITEMS = [
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
  { id: 'date', group: 'Bench', icon: 'calendar', label: "Today's date", hint: isoDate(), keywords: 'date today', run: (e) => e.chain().focus().insertContent(isoDate()).run() },
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
  return ITEMS.filter((i) => `${i.label} ${i.keywords || ''} ${i.group}`.toLowerCase().includes(q));
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
