// A page's table of contents: its headings, kept up to date as the page
// changes, each one a link that scrolls to it. Nothing is stored but the
// block itself (```toc), so it can never go stale.

import { el } from '../util.js';

export function mountToc(body, { editor }) {
  const list = el('ol', { class: 'nb-toc' });
  body.appendChild(list);
  let timer = null;

  const draw = () => {
    timer = null;
    if (!editor || editor.isDestroyed) return;
    const heads = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === 'heading' && node.textContent.trim()) heads.push({ level: node.attrs.level, text: node.textContent.trim(), pos });
      return node.type.name !== 'labBlock';
    });
    list.innerHTML = '';
    if (!heads.length) {
      list.appendChild(el('li', { class: 'nb-toc-empty', text: 'Headings you add to the page are listed here.' }));
      return;
    }
    const top = Math.min(...heads.map((h) => h.level));
    heads.forEach((h) => {
      const link = el('a', { href: '#', class: `nb-toc-link nb-toc-l${h.level - top + 1}`, text: h.text });
      link.addEventListener('click', (event) => {
        event.preventDefault();
        const dom = editor.view.nodeDOM(h.pos);
        if (dom && dom.scrollIntoView) dom.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      const item = el('li');
      item.appendChild(link);
      list.appendChild(item);
    });
  };
  const later = () => { if (!timer) timer = setTimeout(draw, 300); };
  draw();
  editor?.on('update', later);
  return {
    update() {},
    destroy() {
      editor?.off('update', later);
      if (timer) clearTimeout(timer);
    },
  };
}
