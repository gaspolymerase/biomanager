// Bottom-pinned floating toolbar for the TipTap editor.
// Centered horizontally, fixed to viewport bottom — follows scroll, always
// visible while the editor is on the page. Buttons reflect the active mark
// state (so [B] highlights when the caret is inside bold text).
//
// When the caret is inside a table, a secondary "table actions" row appears
// above the main toolbar with row/col add/remove buttons. The Insert menu
// lists everything in commands.js and inserts at the caret.

import { ICONS } from './icons.js';
import { ITEMS, pickFile, uploadFile, uploadImage } from './commands.js';
import { timers } from './timers.js';
import { ask, escapeHtml } from './util.js';
import { openColorMenu } from './rich-menus.js';
import { inColumns } from './extensions/RichBlocks.js';

function svgIcon(html) {
  return `<span class="editor-toolbar-icon">${html}</span>`;
}

let openMenuEl = null;

function closeInsertMenu() {
  if (openMenuEl && openMenuEl.parentNode) openMenuEl.parentNode.removeChild(openMenuEl);
  openMenuEl = null;
  document.removeEventListener('mousedown', onOutsideClick);
}

function onOutsideClick(event) {
  if (openMenuEl && !openMenuEl.contains(event.target) && !event.target.closest('[data-cmd-id="insert"]')) {
    closeInsertMenu();
  }
}

export function renderMenuItems(items) {
  const groups = {};
  items.forEach((item) => { (groups[item.group] = groups[item.group] || []).push(item); });
  return Object.keys(groups).map((g) => `<div class="insert-menu-label">${escapeHtml(g)}</div>${groups[g].map((item) => `
    <button type="button" class="insert-menu-item" data-id="${item.id}">
      <span class="insert-menu-icon">${ICONS[item.icon] || ICONS.insert}</span>
      <span class="insert-menu-text"><span class="insert-menu-title">${escapeHtml(item.label)}</span><span class="insert-menu-hint">${escapeHtml(item.hint || '')}</span></span>
    </button>`).join('')}`).join('');
}

function openInsertMenu(editor) {
  if (openMenuEl) { closeInsertMenu(); return; }
  const btn = document.querySelector('[data-cmd-id="insert"]');
  const rect = btn ? btn.getBoundingClientRect() : { left: 100, top: 400, width: 0 };
  const menu = document.createElement('div');
  menu.className = 'insert-menu insert-menu-main';
  menu.innerHTML = `<input class="insert-menu-search" placeholder="Search… (or type / in the page)" aria-label="Search insert menu"><div class="insert-menu-items">${renderMenuItems(ITEMS)}</div>`;
  document.body.appendChild(menu);
  const menuRect = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - menuRect.width - 8, rect.left - menuRect.width / 2 + rect.width / 2))}px`;
  menu.style.top = `${Math.max(8, rect.top - menuRect.height - 8)}px`;
  const search = menu.querySelector('.insert-menu-search');
  search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    menu.querySelectorAll('.insert-menu-item').forEach((b) => {
      const item = ITEMS.find((x) => x.id === b.dataset.id);
      b.hidden = q && !`${item.label} ${item.keywords || ''} ${item.group}`.toLowerCase().includes(q);
    });
    menu.querySelectorAll('.insert-menu-label').forEach((label) => {
      let n = label.nextElementSibling;
      let any = false;
      while (n && !n.classList.contains('insert-menu-label')) { if (!n.hidden) any = true; n = n.nextElementSibling; }
      label.hidden = !any;
    });
  });
  search.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeInsertMenu();
    if (event.key === 'Enter') {
      const first = [...menu.querySelectorAll('.insert-menu-item')].find((b) => !b.hidden);
      if (first) first.click();
    }
  });
  menu.addEventListener('click', (event) => {
    const b = event.target.closest('.insert-menu-item');
    if (!b) return;
    const item = ITEMS.find((x) => x.id === b.dataset.id);
    closeInsertMenu();
    if (item) item.run(editor);
  });
  openMenuEl = menu;
  setTimeout(() => { document.addEventListener('mousedown', onOutsideClick); search.focus(); }, 0);
}

export function createToolbar(editor, { extra = [] } = {}) {
  const toolbar = document.createElement('div');
  toolbar.className = 'editor-toolbar';

  // Secondary table-actions row, sits above main toolbar. Hidden when not in
  // a table. Position is computed relative to the main toolbar.
  const tableBar = document.createElement('div');
  tableBar.className = 'editor-toolbar editor-toolbar-secondary';
  tableBar.style.display = 'none';

  // One row: what has a menu of its own (a style, a kind of list) is one
  // button with that menu; the rest is in Insert and the "/" menu.
  const styles = [
    { id: 'p', icon: ICONS.text, label: 'Text', run: (e) => e.chain().focus().setParagraph().run(), isActive: (e) => e.isActive('paragraph') },
    { id: 'h1', icon: ICONS.h1, label: 'Heading 1', run: (e) => e.chain().focus().toggleHeading({ level: 1 }).run(), isActive: (e) => e.isActive('heading', { level: 1 }) },
    { id: 'h2', icon: ICONS.h2, label: 'Heading 2', run: (e) => e.chain().focus().toggleHeading({ level: 2 }).run(), isActive: (e) => e.isActive('heading', { level: 2 }) },
    { id: 'h3', icon: ICONS.h3, label: 'Heading 3', run: (e) => e.chain().focus().toggleHeading({ level: 3 }).run(), isActive: (e) => e.isActive('heading', { level: 3 }) },
    { id: 'quote', icon: ICONS.quote, label: 'Quote', run: (e) => e.chain().focus().toggleBlockquote().run(), isActive: (e) => e.isActive('blockquote') },
    { id: 'codeblock', icon: ICONS.code, label: 'Code block', run: (e) => e.chain().focus().toggleCodeBlock().run(), isActive: (e) => e.isActive('codeBlock') },
  ];
  const lists = [
    { id: 'bullet', icon: ICONS.bullet, label: 'Bulleted list', run: (e) => e.chain().focus().toggleBulletList().run(), isActive: (e) => e.isActive('bulletList') },
    { id: 'ordered', icon: ICONS.ordered, label: 'Numbered list', run: (e) => e.chain().focus().toggleOrderedList().run(), isActive: (e) => e.isActive('orderedList') },
    { id: 'task', icon: ICONS.task, label: 'Checklist', run: (e) => e.chain().focus().toggleTaskList().run(), isActive: (e) => e.isActive('taskList') },
  ];

  const groups = [
    [
      { id: 'style', icon: ICONS.text, label: 'Text style: heading, quote, code', choices: styles, renderAsDropdown: true },
      { id: 'lists', icon: ICONS.bullet, label: 'Lists and checklists', choices: lists, renderAsDropdown: true, isActive: (e) => lists.some((c) => c.isActive(e)) },
    ],
    [
      { id: 'bold', icon: ICONS.bold, label: 'Bold (Cmd-B)', exec: (e) => e.chain().focus().toggleBold().run(), isActive: (e) => e.isActive('bold') },
      { id: 'italic', icon: ICONS.italic, label: 'Italic (Cmd-I)', exec: (e) => e.chain().focus().toggleItalic().run(), isActive: (e) => e.isActive('italic') },
      { id: 'strike', icon: ICONS.strike, label: 'Strikethrough', exec: (e) => e.chain().focus().toggleStrike().run(), isActive: (e) => e.isActive('strike') },
      { id: 'color', icon: ICONS.palette, label: 'Colour and highlight', exec: (e) => openColorMenu(e, document.querySelector('[data-cmd-id="color"]')), isActive: (e) => e.isActive('tint') },
      {
        id: 'link',
        icon: ICONS.link,
        label: 'Link',
        exec: async (e) => {
          const previous = e.getAttributes('link')?.href || '';
          const url = await ask.prompt('Link', previous, { placeholder: 'https://… (leave blank to remove the link)', okLabel: 'Save link' });
          if (url === null) return;
          if (url === '') {
            e.chain().focus().extendMarkRange('link').unsetLink().run();
            return;
          }
          e.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
        },
        isActive: (e) => e.isActive('link'),
      },
    ],
    [
      {
        id: 'attach',
        icon: ICONS.attach,
        label: 'Picture or file',
        exec: async (e) => {
          const file = await pickFile('');
          if (!file) return;
          if (file.type && file.type.startsWith('image/')) uploadImage(e, file);
          else uploadFile(e, file);
        },
      },
      { id: 'timer', icon: ICONS.timer, label: 'Timer', exec: () => timers().ask() },
      { id: 'insert', icon: ICONS.insert, label: 'Insert… (or type /)', exec: () => openInsertMenu(editor), renderAsDropdown: true },
    ],
    [
      { id: 'undo', icon: ICONS.undo, label: 'Undo (Cmd-Z)', exec: (e) => e.chain().focus().undo().run(), enabled: (e) => e.can().undo() },
      { id: 'redo', icon: ICONS.redo, label: 'Redo (Cmd-Shift-Z)', exec: (e) => e.chain().focus().redo().run(), enabled: (e) => e.can().redo() },
    ],
  ];
  if (extra.length) groups.push(extra);

  const tableActions = [
    { id: 't-add-row', icon: ICONS.rowPlus, label: 'Add row below', exec: (e) => e.chain().focus().addRowAfter().run() },
    { id: 't-del-row', icon: ICONS.rowMinus, label: 'Delete row', exec: (e) => e.chain().focus().deleteRow().run() },
    { id: 't-add-col', icon: ICONS.colPlus, label: 'Add column right', exec: (e) => e.chain().focus().addColumnAfter().run() },
    { id: 't-del-col', icon: ICONS.colMinus, label: 'Delete column', exec: (e) => e.chain().focus().deleteColumn().run() },
    { id: 't-del-table', icon: ICONS.trash, label: 'Delete table', exec: (e) => e.chain().focus().deleteTable().run() },
  ];

  // Inside columns, a row like the table's: add a column, remove this one,
  // or put them back one under the other.
  const columnsBar = document.createElement('div');
  columnsBar.className = 'editor-toolbar editor-toolbar-secondary';
  columnsBar.style.display = 'none';
  const columnActions = [
    { id: 'c-add', icon: ICONS.colPlus, label: 'Add a column', exec: (e) => e.chain().focus().addColumn().run(), enabled: (e) => e.can().addColumn() },
    { id: 'c-del', icon: ICONS.colMinus, label: 'Remove this column (its contents too)', exec: (e) => e.chain().focus().removeColumn().run() },
    { id: 'c-unset', icon: ICONS.bullet, label: 'Undo columns: one under the other', exec: (e) => e.chain().focus().unsetColumns().run() },
  ];

  const allButtons = [];

  function openChoices(btn, cmd) {
    document.querySelectorAll('.toolbar-choices').forEach((m) => m.remove());
    const menu = document.createElement('div');
    menu.className = 'insert-menu toolbar-choices';
    menu.innerHTML = cmd.choices.map((c) => `
      <button type="button" class="insert-menu-item${c.isActive && c.isActive(editor) ? ' is-active' : ''}" data-id="${c.id}">
        <span class="insert-menu-icon">${c.icon}</span><span class="insert-menu-title">${escapeHtml(c.label)}</span>
      </button>`).join('');
    document.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    const m = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - m.width - 8, r.left))}px`;
    menu.style.top = `${Math.max(8, r.top - m.height - 8)}px`;
    menu.addEventListener('mousedown', (event) => event.preventDefault());
    const close = () => { menu.remove(); document.removeEventListener('mousedown', away); };
    const away = (event) => { if (!menu.contains(event.target) && !btn.contains(event.target)) close(); };
    setTimeout(() => document.addEventListener('mousedown', away), 0);
    menu.addEventListener('click', (event) => {
      const b = event.target.closest('.insert-menu-item');
      const choice = b && cmd.choices.find((c) => c.id === b.dataset.id);
      if (!choice) return;
      close();
      choice.run(editor);
    });
  }

  function makeButton(cmd, ed) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'editor-toolbar-btn';
    if (cmd.renderAsDropdown) btn.classList.add('editor-toolbar-btn-dropdown');
    btn.dataset.cmdId = cmd.id;
    btn.setAttribute('aria-label', cmd.label);
    btn.title = cmd.label;
    btn.innerHTML = cmd.renderAsDropdown ? svgIcon(cmd.icon) + svgIcon(ICONS.caretDown) : svgIcon(cmd.icon);
    btn.addEventListener('mousedown', (event) => event.preventDefault());
    btn.addEventListener('click', () => (cmd.choices ? openChoices(btn, cmd) : cmd.exec(ed)));
    btn._cmd = cmd;
    return btn;
  }

  groups.forEach((group, groupIndex) => {
    if (groupIndex > 0) {
      const sep = document.createElement('span');
      sep.className = 'editor-toolbar-sep';
      toolbar.appendChild(sep);
    }
    group.forEach((cmd) => {
      const btn = makeButton(cmd, editor);
      toolbar.appendChild(btn);
      allButtons.push(btn);
    });
  });

  const tableLabel = document.createElement('span');
  tableLabel.className = 'editor-toolbar-meta';
  tableLabel.textContent = 'Table';
  tableBar.appendChild(tableLabel);
  tableActions.forEach((cmd) => {
    const btn = makeButton(cmd, editor);
    tableBar.appendChild(btn);
    allButtons.push(btn);
  });

  const columnsLabel = document.createElement('span');
  columnsLabel.className = 'editor-toolbar-meta';
  columnsLabel.textContent = 'Columns';
  columnsBar.appendChild(columnsLabel);
  columnActions.forEach((cmd) => {
    const btn = makeButton(cmd, editor);
    columnsBar.appendChild(btn);
    allButtons.push(btn);
  });

  function updateState() {
    allButtons.forEach((btn) => {
      const cmd = btn._cmd;
      if (cmd.isActive) btn.classList.toggle('active', !!cmd.isActive(editor));
      if (cmd.choices) {
        const now = cmd.choices.find((c) => c.isActive && c.isActive(editor));
        const icon = (now || cmd).icon;
        if (btn._icon !== icon) { btn._icon = icon; btn.firstElementChild.innerHTML = icon; }
      }
      if (cmd.enabled) {
        try { btn.disabled = !cmd.enabled(editor); } catch (_e) { btn.disabled = false; }
      }
    });
    tableBar.style.display = editor.isActive('table') ? 'inline-flex' : 'none';
    columnsBar.style.display = !editor.isActive('table') && inColumns(editor) ? 'inline-flex' : 'none';
    const hidden = !editor.isEditable;
    toolbar.hidden = hidden;
    if (hidden) { tableBar.style.display = 'none'; columnsBar.style.display = 'none'; }
  }

  editor.on('selectionUpdate', updateState);
  editor.on('focus', updateState);
  editor.on('transaction', updateState);
  updateState();

  document.body.appendChild(toolbar);
  document.body.appendChild(tableBar);
  document.body.appendChild(columnsBar);

  // Centred over the page being written, not the window: centred on the
  // window it sat on the notebook's sidebar and covered its buttons.
  const column = document.querySelector('.notebook-main');
  const centre = () => {
    if (!column) return;
    const box = column.getBoundingClientRect();
    const x = `${Math.round(box.left + box.width / 2)}px`;
    toolbar.style.left = x;
    tableBar.style.left = x;
    columnsBar.style.left = x;
    // No wider than the page: a narrow window scrolls it sideways instead of
    // pushing it over the sidebar.
    const room = `${Math.max(240, Math.round(box.width - 32))}px`;
    toolbar.style.maxWidth = room;
    tableBar.style.maxWidth = room;
    columnsBar.style.maxWidth = room;
    tableBar.style.bottom = `${24 + toolbar.offsetHeight + 8}px`;
    columnsBar.style.bottom = tableBar.style.bottom;
  };
  centre();
  window.addEventListener('resize', centre);
  const watcher = column && 'ResizeObserver' in window ? new ResizeObserver(centre) : null;
  if (watcher) watcher.observe(column);

  return {
    el: toolbar,
    destroy() {
      window.removeEventListener('resize', centre);
      if (watcher) watcher.disconnect();
      editor.off('selectionUpdate', updateState);
      editor.off('focus', updateState);
      editor.off('transaction', updateState);
      closeInsertMenu();
      if (toolbar.parentNode) toolbar.parentNode.removeChild(toolbar);
      if (tableBar.parentNode) tableBar.parentNode.removeChild(tableBar);
      if (columnsBar.parentNode) columnsBar.parentNode.removeChild(columnsBar);
    },
  };
}
