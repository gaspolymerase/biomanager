// A page's references: every paper it cites ((Smith et al., 2024)), in the
// order first cited, each as a reference list writes it and linked to its
// note. Worked out from the page as it is, like Contents, so nothing is
// stored but the block itself (```references) and it can never go stale.

import { el } from '../util.js';
import { paperInfo } from '../extensions/Citation.js';

const t = (s, v) => (typeof window !== 'undefined' && window.t ? window.t(s, v) : s);

export function mountReferences(body, { editor }) {
  const list = el('ol', { class: 'nb-refs' });
  body.appendChild(list);
  let timer = null;
  let drawn = '';

  const draw = () => {
    timer = null;
    if (!editor || editor.isDestroyed) return;
    const ids = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name === 'citation' && node.attrs.id && !ids.includes(String(node.attrs.id))) ids.push(String(node.attrs.id));
      return node.type.name !== 'labBlock';
    });
    const key = ids.join(',');
    if (key === drawn && list.childElementCount) return;
    drawn = key;
    if (!ids.length) {
      list.innerHTML = '';
      list.appendChild(el('li', { class: 'nb-refs-empty', text: t('Papers you cite on this page with /cite or [@ are listed here.') }));
      return;
    }
    Promise.all(ids.map((id) => paperInfo(id))).then((papers) => {
      if (drawn !== key) return;
      list.innerHTML = '';
      papers.forEach((paper, i) => {
        const item = el('li');
        const link = el('a', { href: `/notebook?page=${ids[i]}`, class: 'nb-refs-link',
                               text: paper ? (paper.ref || paper.title) : t('A paper note you cannot open, or one that was deleted') });
        item.appendChild(link);
        list.appendChild(item);
      });
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
