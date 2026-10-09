// "@jordan" in a page, for someone in the lab, is a chip in their colour;
// hovering it shows who they are: name, what they do, admin or member (or
// a guest until when), email, project groups, and since when they're in
// the lab (/notebook/api/people/<username>). The text stays "@jordan" in
// the Markdown, as before: it is what tells them they were mentioned.

import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { escapeHtml, initials, personColor } from '../util.js';

const KEY = new PluginKey('peopleChips');
// As the server reads a mention (app/lab_notebook.py MENTION_RE).
const MENTION = /(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_.-]{0,79})/g;
const ROLE = { admin: 'Admin', member: 'Member', guest: 'Guest' };

let people = null;          // username -> display name, once loaded
let loading = null;
const cards = new Map();    // username -> Promise of the card

function loadPeople() {
  if (!loading) {
    loading = fetch('/notebook/api/people', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : { people: [] }))
      .then((data) => {
        people = new Map((data.people || []).map((p) => [p.username, p.name]));
        return people;
      })
      .catch(() => { people = new Map(); return people; });
  }
  return loading;
}

function decorate(doc) {
  if (!people || !people.size) return DecorationSet.empty;
  const found = [];
  doc.descendants((node, pos) => {
    if (!node.isText || node.marks.some((m) => m.type.name === 'code' || m.type.name === 'link')) return;
    MENTION.lastIndex = 0;
    let m;
    while ((m = MENTION.exec(node.text))) {
      const name = m[1].replace(/\.+$/, '');
      if (!people.has(name)) continue;
      const from = pos + m.index;
      found.push(Decoration.inline(from, from + 1 + name.length, {
        class: 'person-chip', 'data-person': name, title: people.get(name),
        style: `--person: ${personColor(name)}`,
      }));
    }
  });
  return DecorationSet.create(doc, found);
}

// ---------------------------------------------------------------- the card

let cardEl = null;
let hideTimer = null;

function card() {
  if (cardEl) return cardEl;
  cardEl = document.createElement('div');
  cardEl.className = 'person-card';
  cardEl.hidden = true;
  cardEl.addEventListener('mouseenter', () => clearTimeout(hideTimer));
  cardEl.addEventListener('mouseleave', hideSoon);
  document.body.appendChild(cardEl);
  return cardEl;
}

function hideSoon() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => { if (cardEl) cardEl.hidden = true; }, 250);
}

function monthYear(iso) {
  const d = new Date(`${iso}T12:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(document.documentElement.lang || undefined, { month: 'short', year: 'numeric' });
}

function render(p) {
  const color = personColor(p.username);
  const lines = [];
  if (p.title) lines.push(`<div class="person-card-title">${escapeHtml(p.title)}</div>`);
  const role = p.role === 'guest' && p.until ? `Guest until ${escapeHtml(p.until)}` : (ROLE[p.role] || 'Member');
  lines.push(`<div class="person-card-meta"><span class="person-card-role person-card-role-${escapeHtml(p.role)}">${role}</span>${p.since && p.role !== 'guest' ? ` · in the lab since ${escapeHtml(monthYear(p.since))}` : ''}</div>`);
  if (p.email) lines.push(`<a class="person-card-email" href="mailto:${escapeHtml(p.email)}">${escapeHtml(p.email)}</a>`);
  if (p.groups && p.groups.length) {
    lines.push(`<div class="person-card-groups">${p.groups.map((grp) => `<span class="person-card-group">${escapeHtml(grp.name)}${grp.lead ? ' · lead' : ''}</span>`).join('')}</div>`);
  }
  return `
    <div class="person-card-head">
      <span class="person-card-badge" style="background:${color}">${escapeHtml(initials(p.name))}</span>
      <span class="person-card-who"><b>${escapeHtml(p.name)}${p.me ? ' (you)' : ''}</b><small>@${escapeHtml(p.username)}</small></span>
    </div>
    ${lines.join('')}`;
}

function show(chip) {
  const name = chip.dataset.person;
  const box = card();
  clearTimeout(hideTimer);
  if (!cards.has(name)) {
    cards.set(name, fetch(`/notebook/api/people/${encodeURIComponent(name)}`, { credentials: 'same-origin' })
      .then((r) => r.json()).then((d) => (d.ok ? d.person : null)).catch(() => null));
  }
  box.innerHTML = `<div class="person-card-loading">${escapeHtml(people?.get(name) || name)}…</div>`;
  box.hidden = false;
  const place = () => {
    const r = chip.getBoundingClientRect();
    const b = box.getBoundingClientRect();
    const below = r.bottom + 8 + b.height < window.innerHeight;
    box.style.left = `${Math.max(8, Math.min(window.innerWidth - b.width - 8, r.left)) + window.scrollX}px`;
    box.style.top = `${(below ? r.bottom + 6 : r.top - b.height - 6) + window.scrollY}px`;
  };
  place();
  cards.get(name).then((p) => {
    if (box.hidden) return;
    box.innerHTML = p ? render(p) : '<div class="person-card-loading">Not found.</div>';
    place();
  });
}

export const PeopleChips = Extension.create({
  name: 'peopleChips',

  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: KEY,
        state: {
          init: (_config, state) => decorate(state.doc),
          apply: (tr, old) => (tr.docChanged || tr.getMeta(KEY) ? decorate(tr.doc) : old),
        },
        view: (view) => {
          if (!people) loadPeople().then(() => { if (!view.isDestroyed) view.dispatch(view.state.tr.setMeta(KEY, 'people')); });
          return {};
        },
        props: {
          decorations: (state) => KEY.getState(state),
          handleDOMEvents: {
            mouseover: (_view, event) => {
              const chip = event.target.closest && event.target.closest('.person-chip');
              if (chip) show(chip);
              return false;
            },
            mouseout: (_view, event) => {
              if (event.target.closest && event.target.closest('.person-chip')) hideSoon();
              return false;
            },
          },
        },
      }),
    ];
  },
});
