/* A field with suggestions, <input list="…"> and its <datalist>, shows
 * them in the app's own menu instead of the browser's: the same glass
 * menu in every browser, in dark mode, in a dialog and on a phone.
 *
 * Nothing to do in a template: write the input and its datalist as
 * before. Each such input on the page, or added to it later, is taken over
 * here; its datalist is read again each time the menu opens, so a script
 * may refill it. Picking a suggestion sets the value and sends input and
 * change, as typing it would, so a sheet saves it.
 *
 * Keys: typing filters, ↓/↑ move (in a sheet, Alt+↓ opens it, since ↓
 * alone moves down a row), Enter picks, Esc closes.
 */
(function () {
  'use strict';

  const MAX = 200;
  let pop = null;
  let field = null;
  let items = [];
  let active = -1;
  let choosing = false;

  function adopt(input) {
    const id = input.getAttribute('list');
    if (!id) return;
    input.dataset.comboList = id;
    input.removeAttribute('list');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('autocomplete', 'off');
  }

  function scan(node) {
    if (node.nodeType !== 1) return;
    if (node.matches('input[list]')) adopt(node);
    node.querySelectorAll('input[list]').forEach(adopt);
  }

  function choicesOf(input) {
    const list = document.getElementById(input.dataset.comboList);
    if (!list) return [];
    const seen = new Set();
    const out = [];
    for (const option of list.querySelectorAll('option')) {
      const value = option.value;
      if (!value || seen.has(value)) continue;
      seen.add(value);
      const label = option.label && option.label !== value ? option.label : '';
      out.push({ value, label });
    }
    return out;
  }

  // What the typed text matches: those starting with it first, then those
  // with it anywhere. A value that is already one of them shows them all.
  function match(choices, typed) {
    const q = typed.trim().toLowerCase();
    if (!q || choices.some((c) => c.value.toLowerCase() === q)) return choices.slice(0, MAX);
    const starts = [];
    const within = [];
    for (const c of choices) {
      const v = c.value.toLowerCase();
      const at = v.indexOf(q);
      if (at === 0) starts.push(c);
      else if (at > 0 || c.label.toLowerCase().includes(q)) within.push(c);
    }
    return starts.concat(within).slice(0, MAX);
  }

  function ensurePop() {
    if (pop) return pop;
    pop = document.createElement('div');
    pop.className = 'combo-pop';
    pop.id = 'combo-pop';
    pop.setAttribute('role', 'listbox');
    if ('popover' in pop) pop.popover = 'manual';
    // Keep the focus in the field while a suggestion is clicked.
    pop.addEventListener('pointerdown', (event) => event.preventDefault());
    pop.addEventListener('click', (event) => {
      const row = event.target.closest('.combo-opt');
      if (row) choose(Number(row.dataset.index));
    });
    pop.addEventListener('pointermove', (event) => {
      const row = event.target.closest('.combo-opt');
      if (row) highlight(Number(row.dataset.index), false);
    });
    return pop;
  }

  function render() {
    pop.textContent = '';
    items.forEach((c, i) => {
      const row = document.createElement('div');
      row.className = 'combo-opt';
      row.id = 'combo-opt-' + i;
      row.dataset.index = String(i);
      row.setAttribute('role', 'option');
      const value = document.createElement('span');
      value.textContent = c.value;
      row.appendChild(value);
      if (c.label) {
        const label = document.createElement('small');
        label.textContent = c.label;
        row.appendChild(label);
      }
      pop.appendChild(row);
    });
  }

  function place() {
    if (!field || !pop) return;
    const box = field.getBoundingClientRect();
    if (box.bottom < 0 || box.top > window.innerHeight) { close(); return; }
    pop.style.minWidth = Math.max(box.width, 180) + 'px';
    pop.style.left = Math.max(8, Math.min(box.left, window.innerWidth - pop.offsetWidth - 8)) + 'px';
    const below = window.innerHeight - box.bottom;
    const tall = pop.offsetHeight;
    pop.style.top = (below < tall + 12 && box.top > below ? box.top - tall - 4 : box.bottom + 4) + 'px';
  }

  function open(input) {
    items = match(choicesOf(input), input.value);
    // Nothing to offer, or only what is already written.
    if (!items.length || (items.length === 1 && items[0].value === input.value)) { close(); return; }
    if (field && field !== input) close();
    field = input;
    ensurePop();
    render();
    // In a dialog, in the dialog: the page behind a modal one can't be clicked.
    const home = input.closest('dialog') || document.body;
    if (pop.parentNode !== home) {
      if (pop.matches(':popover-open')) pop.hidePopover();
      home.appendChild(pop);
    }
    if (pop.popover && !pop.matches(':popover-open')) pop.showPopover();
    pop.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-controls', pop.id);
    const exact = items.findIndex((c) => c.value === input.value);
    highlight(exact, true);
    place();
  }

  function close() {
    if (!field) return;
    field.setAttribute('aria-expanded', 'false');
    field.removeAttribute('aria-activedescendant');
    field = null;
    active = -1;
    if (pop) {
      if (pop.popover && pop.matches(':popover-open')) pop.hidePopover();
      pop.hidden = true;
    }
  }

  function highlight(i, scroll) {
    const rows = pop.children;
    if (rows[active]) rows[active].classList.remove('is-active');
    active = i;
    if (!rows[i]) { field.removeAttribute('aria-activedescendant'); return; }
    rows[i].classList.add('is-active');
    field.setAttribute('aria-activedescendant', rows[i].id);
    if (!scroll) return;
    // Scroll the menu only, not the page or dialog under it.
    const row = rows[i];
    if (row.offsetTop < pop.scrollTop) pop.scrollTop = row.offsetTop - 5;
    else if (row.offsetTop + row.offsetHeight > pop.scrollTop + pop.clientHeight) {
      pop.scrollTop = row.offsetTop + row.offsetHeight - pop.clientHeight + 5;
    }
  }

  function choose(i) {
    const input = field;
    const c = items[i];
    if (!input || !c) return;
    close();
    choosing = true;
    input.value = c.value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    choosing = false;
  }

  const isCombo = (el) => el instanceof HTMLInputElement && el.dataset.comboList !== undefined;

  document.addEventListener('input', (event) => {
    if (!choosing && isCombo(event.target)) open(event.target);
  });
  document.addEventListener('pointerdown', (event) => {
    if (isCombo(event.target) && !event.target.disabled && !event.target.readOnly) {
      if (field === event.target) close();
      else open(event.target);
    } else if (field && !(pop && pop.contains(event.target))) {
      close();
    }
  });
  document.addEventListener('focusout', (event) => {
    if (event.target === field) close();
  });
  document.addEventListener('keydown', (event) => {
    const input = event.target;
    if (!isCombo(input)) return;
    const shown = field === input;
    const key = event.key;
    if (key === 'ArrowDown' || key === 'ArrowUp') {
      if (!shown) {
        // In a sheet ↓ moves down a row; Alt+↓ opens the suggestions.
        if (key === 'ArrowDown' && (event.altKey || !input.closest('.sheet-table'))) {
          event.preventDefault();
          open(input);
        }
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const step = key === 'ArrowDown' ? 1 : -1;
      highlight((active + step + items.length) % items.length, true);
    } else if (key === 'Enter' && shown && active >= 0) {
      event.preventDefault();
      event.stopPropagation();
      choose(active);
    } else if (key === 'Escape' && shown) {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (key === 'Tab') {
      close();
    }
  }, true);
  window.addEventListener('resize', place);
  document.addEventListener('scroll', (event) => {
    if (field && !(pop && pop.contains(event.target))) place();
  }, true);

  function start() {
    scan(document.body);
    new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'attributes') {
          if (record.target.matches('input[list]')) adopt(record.target);
        } else {
          record.addedNodes.forEach(scan);
        }
      }
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['list'] });
  }
  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);

  window.BioCombobox = { adopt, close };
})();
