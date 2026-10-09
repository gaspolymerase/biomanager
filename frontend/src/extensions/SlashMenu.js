// "/" at the start of a line (or after a space) opens a menu of everything
// the Insert menu has: keep typing to filter ("/sheet", "/recipe",
// "/timer"), arrows to choose, Enter to insert.

import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { filterItems } from '../commands.js';
import { renderMenuItems } from '../toolbar.js';

// One or more words after the slash ("/mind map", "/分栏"); a query with a space
// that matches nothing closes the menu, so ordinary text is left alone.
const TRIGGER = /(?:^|\s)\/((?:[\p{L}\p{N}_-]+(?: [\p{L}\p{N}_-]+)*)?)$/u;

export const SlashMenu = Extension.create({
  name: 'slashMenu',

  addProseMirrorPlugins() {
    const editor = this.editor;
    let menu = null;
    let items = [];
    let active = 0;
    let range = null;
    let dismissedAt = null;

    const close = () => {
      if (menu) menu.remove();
      menu = null;
      range = null;
    };

    const choose = (item) => {
      if (!item || !range) return;
      const r = range;
      close();
      editor.chain().focus().deleteRange(r).run();
      item.run(editor);
    };

    const paint = () => {
      if (!menu) return;
      menu.innerHTML = items.length ? renderMenuItems(items.slice(0, 120)) : '<div class="insert-empty">Nothing matches</div>';
      menu.querySelectorAll('.insert-menu-item').forEach((b, i) => b.classList.toggle('is-active', i === active));
      menu.querySelector('.is-active')?.scrollIntoView({ block: 'nearest' });
    };

    const open = (view, query, from, to) => {
      items = filterItems(query);
      range = { from, to };
      if (!menu) {
        menu = document.createElement('div');
        menu.className = 'insert-menu insert-menu-slash';
        menu.addEventListener('mousedown', (e) => e.preventDefault());
        menu.addEventListener('click', (e) => {
          const b = e.target.closest('.insert-menu-item');
          if (b) choose(items.find((x) => x.id === b.dataset.id));
        });
        document.body.appendChild(menu);
        active = 0;
      }
      active = Math.min(active, Math.max(0, items.length - 1));
      paint();
      const coords = view.coordsAtPos(to);
      const h = menu.offsetHeight;
      const below = coords.bottom + 6;
      const top = below + h > window.innerHeight - 70 ? Math.max(8, coords.top - h - 6) : below;
      menu.style.left = `${Math.min(window.innerWidth - menu.offsetWidth - 8, coords.left)}px`;
      menu.style.top = `${top}px`;
    };

    return [new Plugin({
      key: new PluginKey('slashMenu'),
      view: () => ({
        update(view) {
          const { state } = view;
          const sel = state.selection;
          if (!editor.isEditable || !sel.empty || !sel.$from.parent.isTextblock || sel.$from.parent.type.name === 'codeBlock') { close(); return; }
          const before = sel.$from.parent.textBetween(0, sel.$from.parentOffset, undefined, '￼');
          const m = TRIGGER.exec(before);
          if (!m) { close(); return; }
          const from = sel.from - m[1].length - 1;
          if (from === dismissedAt || m[1].length > 30) { close(); return; }
          if (m[1].includes(' ') && !filterItems(m[1]).length) { close(); return; }
          dismissedAt = null;
          open(view, m[1], from, sel.from);
        },
        destroy: close,
      }),
      props: {
        handleKeyDown(_view, event) {
          if (!menu) return false;
          if (event.key === 'ArrowDown') { active = (active + 1) % Math.max(1, items.length); paint(); return true; }
          if (event.key === 'ArrowUp') { active = (active - 1 + items.length) % Math.max(1, items.length); paint(); return true; }
          if (event.key === 'Enter' || event.key === 'Tab') {
            if (!items.length) return false;
            event.preventDefault();
            choose(items[active]);
            return true;
          }
          if (event.key === 'Escape') { dismissedAt = range && range.from; close(); return true; }
          return false;
        },
      },
    })];
  },
});
