/* An inventory's several-choice cells (a chemical's hazards), from inventory/module.html. */
// A several-choice cell (a chemical's hazards): its summary follows the
// ticked boxes, and an open menu closes when you click elsewhere.
// Until first opened it holds only the ticked values, as hidden fields its
// row sends; opening it makes the boxes from its list (choices-inv-m-…).
function buildMulti(cell) {
  if (!cell || cell.dataset.multiBuilt || !cell.dataset.multiChoices) return;
  cell.dataset.multiBuilt = '1';
  const menu = cell.querySelector('.multi-cell-menu');
  const several = menu.querySelector('input[name$="__several"]');
  const name = several.name.replace(/__several$/, '');
  const picked = Array.from(menu.querySelectorAll('[data-multi-picked]')).map((el) => el.value);
  const list = document.getElementById(`choices-${cell.dataset.multiChoices}`);
  const boxes = list ? list.content.cloneNode(true) : document.createDocumentFragment();
  const listed = new Set(Array.from(boxes.querySelectorAll('input')).map((b) => b.value));
  picked.filter((v) => !listed.has(v)).forEach((v) => {   // one the list no longer has
    const label = document.createElement('label');
    const box = label.appendChild(document.createElement('input'));
    box.type = 'checkbox';
    box.value = v;
    label.append(' ');
    label.appendChild(document.createElement('span')).textContent = v;
    boxes.appendChild(label);
  });
  boxes.querySelectorAll('input[type="checkbox"]').forEach((box) => {
    box.name = name;
    box.setAttribute('form', several.getAttribute('form'));
    box.dataset.autosave = '1';
    box.dataset.multi = '';
    box.checked = picked.includes(box.value);
    box.disabled = cell.hasAttribute('data-locked');
  });
  menu.querySelectorAll('[data-multi-picked]').forEach((el) => el.remove());
  menu.appendChild(boxes);
}
document.addEventListener('click', (event) => {
  const summary = event.target.closest && event.target.closest('[data-multi-cell] > summary');
  if (summary) buildMulti(summary.parentElement);
}, true);
document.addEventListener('change', (event) => {
  const box = event.target.closest('[data-multi-cell] input[type="checkbox"]');
  if (!box) return;
  const cell = box.closest('[data-multi-cell]');
  const names = [...cell.querySelectorAll('input[type="checkbox"]:checked')].map((b) => b.closest('label').textContent.trim());
  const summary = cell.querySelector('[data-multi-summary]');
  summary.textContent = names.length ? names.join(', ') : summary.dataset.emptyText;
});
document.addEventListener('click', (event) => {
  document.querySelectorAll('[data-multi-cell][open]').forEach((cell) => {
    if (!cell.contains(event.target)) cell.open = false;
  });
});
// The menu is fixed (the sheet's scrolling box would clip it): put it under
// its cell, or above when there's no room, and close it if the page scrolls.
document.addEventListener('toggle', (event) => {
  const cell = event.target;
  if (!cell.matches || !cell.matches('[data-multi-cell]') || !cell.open) return;
  buildMulti(cell);
  const menu = cell.querySelector('.multi-cell-menu');
  const box = cell.querySelector('summary').getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - menu.offsetWidth - 8))}px`;
  const below = box.bottom + 4;
  menu.style.top = `${below + menu.offsetHeight > window.innerHeight - 8 ? Math.max(8, box.top - menu.offsetHeight - 4) : below}px`;
}, true);
window.addEventListener('scroll', () => {
  document.querySelectorAll('[data-multi-cell][open]').forEach((cell) => { cell.open = false; });
}, true);
