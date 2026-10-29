/* The notebook's topics and favourites (templates/notebook.html): the
 * sidebar's tree of topics, a topic's own page of cards, and the home.
 *
 * - + beside Topics names a new topic in place. A topic's ••• (in the
 *   sidebar, or on its page) starts a page in it, makes a topic inside it,
 *   changes its name and icon, moves it into another topic, or deletes it.
 * - Drag a page onto a topic to move it there, or above or below a page to
 *   put it there (beside that page, under the page it sits in). Drag a topic
 *   onto another to put it inside, or near the top or bottom of one to put
 *   it above or below. On a topic's page, drag a card onto a topic, or
 *   (in Your order) before or after another card.
 * - Drag any page into Favorites to star it, and a favourite to reorder
 *   them; the star on a page or a card does the same.
 * - The arrow beside a topic or a page folds it, remembered in this browser.
 * - Share (on a topic's page, or in its •••) shares the topic with lab
 *   mates, a project group or the whole lab, to read or to edit: its pages
 *   and the topics inside it. They find it under Shared with me, where it
 *   is theirs to open but not to move.
 *
 * Every change goes to the server, then the page is drawn again from it.
 */
(function () {
  'use strict';

  var dataEl = document.getElementById('nb-data');
  var DATA = dataEl ? JSON.parse(dataEl.textContent || '{}') : {};
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function icon(name) { return '<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#' + name + '"></use></svg>'; }
  // A topic's icon: one of ours, an emoji or an SVG of its own (app/topic_icons.py).
  function topicIcon(value) {
    value = value || 'folder';
    if (value.indexOf('emoji:') === 0) return '<span class="ti-emoji" aria-hidden="true">' + esc(value.slice(6)) + '</span>';
    if (value.indexOf('svg:uploads/') === 0) return '<img class="ti-svg" src="/static/' + esc(value.slice(4)) + '" alt="">';
    return icon(value);
  }
  // The emoji offered first; the computer's own picker gives any other.
  var EMOJI = ['🧪', '🧫', '🧬', '🔬', '🧠', '🫀', '🫁', '🦴', '🩸', '💉', '💊', '🩺', '⚗️', '🧲', '🧊', '🌡️',
    '☢️', '☣️', '🐭', '🐁', '🐀', '🐟', '🐠', '🪰', '🪱', '🐸', '🐔', '🐣', '🌱', '🌿', '🌾', '🦠',
    '📈', '📊', '📋', '📌', '📁', '📒', '📚', '✏️', '💡', '🎯', '⭐', '🚀', '✅', '⚠️', '🔥', '❄️'];
  function iconChoices(value) {
    return { choices: DATA.folderIcons || [], value: value || 'folder', emoji: EMOJI, uploadUrl: '/notebook/api/topic-icons' };
  }
  function api(url, body) {
    return fetch(url, {
      method: 'POST', body: JSON.stringify(body || {}),
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || d.ok === false) throw new Error(d.error || ('HTTP ' + r.status));
        return d;
      });
    });
  }
  function post(url, fields) {
    var form = new FormData();
    Object.keys(fields || {}).forEach(function (k) { form.append(k, fields[k]); });
    return fetch(url, { method: 'POST', body: form, headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.json().catch(function () { return { ok: r.ok }; }); });
  }
  function toast(text, isError) {
    if (window.NotebookPage) window.NotebookPage.toast(esc(text), isError);
    else window.BiomanagerShell.toast(text, isError ? 'danger' : '');
  }
  function fail(err) { toast(err.message, true); }
  function reload() { window.location.reload(); }
  function go(url) { window.location.href = url; }

  // ------------------------------------------------------------ topics

  function topicEl(id) { return document.querySelector('.notebook-tab[data-tab-id="' + id + '"]'); }
  function topicName(id) {
    var el = topicEl(id);
    var label = el && el.querySelector(':scope > summary .tab-title');
    return label ? label.textContent.trim() : '';
  }
  // Is topic `id` the topic `outer`, or inside it at any depth?
  function within(id, outer) {
    var el = topicEl(outer);
    return String(id) === String(outer) || !!(el && el.querySelector('.notebook-tab[data-tab-id="' + id + '"]'));
  }

  var addBtn = document.getElementById('nb-topic-add');
  var addForm = document.getElementById('nb-topic-new');
  if (addBtn && addForm) {
    var addInput = addForm.querySelector('input');
    addBtn.addEventListener('click', function () {
      addForm.hidden = false;
      addInput.value = '';
      addInput.focus();
    });
    addForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var title = addInput.value.trim();
      if (!title) { addForm.hidden = true; return; }
      post('/notebook/tabs/create', { title: title }).then(function (d) { if (d.ok) go('/notebook?tab=' + d.id); });
    });
    addInput.addEventListener('keydown', function (e) { if (e.key === 'Escape') { addForm.hidden = true; addBtn.focus(); } });
    addInput.addEventListener('blur', function () { if (!addInput.value.trim()) addForm.hidden = true; });
  }

  function newPageIn(tabId) {
    if (window.NotebookNewPage) window.NotebookNewPage.open({ tab: tabId });
    else api('/notebook/api/pages/new', { tab_id: tabId }).then(function (d) { go(d.url); }).catch(fail);
  }

  function editTopic(tabId) {
    var el = topicEl(tabId);
    BioDialog.prompt(t('Name and icon'), topicName(tabId), {
      okLabel: t('Save'), icons: iconChoices(el && el.dataset.icon),
    }).then(function (answer) {
      if (!answer || !answer.value.trim()) return;
      api('/notebook/api/topics/' + tabId, { title: answer.value.trim(), icon: answer.icon }).then(reload).catch(fail);
    });
  }

  function newTopicIn(tabId) {
    BioDialog.prompt(t('New topic inside %(name)s', { name: topicName(tabId) }), '', {
      placeholder: t('Name the topic'), okLabel: t('Create'), icons: iconChoices('folder'),
    }).then(function (answer) {
      if (!answer || !answer.value.trim()) return;
      post('/notebook/tabs/create', { title: answer.value.trim(), parent_id: tabId }).then(function (d) {
        if (!d.ok) return;
        var done = answer.icon && answer.icon !== 'folder' ? api('/notebook/api/topics/' + d.id, { icon: answer.icon }) : Promise.resolve();
        done.then(function () { go('/notebook?tab=' + d.id); }).catch(fail);
      });
    });
  }

  function removeTopic(tabId) {
    var el = topicEl(tabId);
    var inside = el && el.querySelector('.notebook-subtopics .notebook-tab');
    var question = inside
      ? t('Delete the topic “%(name)s” and every page in it? The topics inside it stay, one level up.', { name: topicName(tabId) })
      : t('Delete the topic “%(name)s” and every page in it?', { name: topicName(tabId) });
    BioDialog.confirm(question, { danger: true, okLabel: t('Delete topic') }).then(function (ok) {
      if (ok) post('/notebook/tabs/' + tabId + '/delete', {}).then(function () { go('/notebook'); });
    });
  }

  function placeTopic(tabId, where) {
    return api('/notebook/api/topics/' + tabId + '/place', where).then(reload).catch(fail);
  }

  // A menu that floats over the page, under the button that opened it.
  function openAt(menu, button) {
    menu.hidden = false;
    var box = button.getBoundingClientRect();
    var w = menu.offsetWidth, h = menu.offsetHeight;
    var top = box.bottom + 4;
    if (top + h > window.innerHeight - 8) top = Math.max(8, box.top - h - 4);
    menu.style.top = top + 'px';
    menu.style.left = Math.max(8, Math.min(box.right - w, window.innerWidth - w - 8)) + 'px';
  }

  // Move to…: every topic it may go inside, and the top.
  var picker = null;
  function closePicker() { if (picker) { picker.remove(); picker = null; } }
  function movePicker(tabId, button) {
    closePicker();
    var rows = (DATA.tabs || []).map(function (id, i) {
      return { id: id, title: (DATA.tabTitles || [])[i] || '', indent: ((DATA.tabIndents || [])[i] || '').length };
    }).filter(function (r) {
      var el = topicEl(r.id);
      return !within(r.id, tabId) && !(el && el.classList.contains('is-inbox'));
    });
    var here = topicEl(tabId);
    var parent = here ? here.dataset.parentId : '';
    picker = document.createElement('div');
    picker.className = 'nb-head-menu nb-topic-menu nb-topic-pick';
    picker.setAttribute('role', 'menu');
    picker.innerHTML = '<div class="nb-head-list">' +
      '<button type="button" class="nb-head-item' + (parent ? '' : ' is-on') + '" data-pick="">' + icon('sitemap') + '<span>' + esc(t('At the top')) + '</span></button>' +
      rows.map(function (r) {
        return '<button type="button" class="nb-head-item' + (String(r.id) === parent ? ' is-on' : '') + '" data-pick="' + r.id + '" style="padding-left:' + (8 + 14 * r.indent) + 'px">' +
          topicIcon(topicEl(r.id) && topicEl(r.id).dataset.icon) + '<span>' + esc(r.title) + '</span></button>';
      }).join('') + '</div>';
    document.body.appendChild(picker);
    openAt(picker, button);
    picker.addEventListener('click', function (e) {
      var b = e.target.closest('[data-pick]');
      if (!b) return;
      closePicker();
      placeTopic(tabId, { parent_id: b.dataset.pick ? Number(b.dataset.pick) : null });
    });
  }

  // Share: who may open this topic's pages (lab_notebook.topic_shares_*).
  function shareTopic(tabId) {
    var base = '/notebook/api/topics/' + tabId + '/shares';
    var dialog = document.createElement('dialog');
    dialog.className = 'record-dialog record-dialog-narrow bio-dialog nb-share-dialog';
    dialog.setAttribute('aria-labelledby', 'nb-share-title');
    dialog.innerHTML = '<form method="dialog"><header class="record-dialog-head"><div><h3 id="nb-share-title">' +
      esc(t('Share %(name)s', { name: topicName(tabId) })) + '</h3></div></header>' +
      '<div class="record-dialog-body bio-dialog-body nb-share-body"><p class="nb-muted">' + esc(t('Loading…')) + '</p></div>' +
      '<footer class="record-dialog-foot"><button class="btn btn-primary" value="done">' + esc(t('Done')) + '</button></footer></form>';
    document.body.appendChild(dialog);
    var body = dialog.querySelector('.nb-share-body');
    var changed = false;
    dialog.addEventListener('close', function () { dialog.remove(); if (changed) reload(); });
    function roleSelect(attr, value) {
      return '<select ' + attr + '><option value="edit"' + (value === 'edit' ? ' selected' : '') + '>' + esc(t('can edit')) +
        '</option><option value="view"' + (value === 'view' ? ' selected' : '') + '>' + esc(t('can view')) + '</option></select>';
    }
    function draw() {
      fetch(base, { headers: { Accept: 'application/json' } }).then(function (r) { return r.json(); }).then(function (d) {
        var lab = d.shares.filter(function (x) { return x.username === '*'; })[0];
        var shared = d.shares.filter(function (x) { return x.username !== '*'; });
        var taken = {};
        shared.forEach(function (x) { taken[x.username] = true; });
        var people = d.people.filter(function (p) { return !taken[p.username]; }).map(function (p) {
          return '<option value="' + esc(p.username) + '">' + esc(p.name) + (p.guest ? ' ' + esc(t('(guest)')) : '') + '</option>';
        }).join('');
        var teams = (d.groups || []).filter(function (grp) { return !taken[grp.username]; }).map(function (grp) {
          return '<option value="' + esc(grp.username) + '">' + esc(grp.name) + '</option>';
        }).join('');
        var options = teams ? (people ? '<optgroup label="' + esc(t('People')) + '">' + people + '</optgroup>' : '') +
          '<optgroup label="' + esc(t('Project groups')) + '">' + teams + '</optgroup>' : people;
        body.innerHTML = '<p class="bio-dialog-text">' + esc(t('Its pages, and the topics inside it, open for them under Shared with me. New pages you add are shared too.')) + '</p>' +
          '<label class="nb-share-row">' + esc(t('Everyone in the lab')) + ' <select data-share-lab><option value="">' + esc(t('can’t see it')) + '</option>' +
          '<option value="view"' + (lab && lab.role === 'view' ? ' selected' : '') + '>' + esc(t('can view')) + '</option>' +
          '<option value="edit"' + (lab && lab.role === 'edit' ? ' selected' : '') + '>' + esc(t('can edit')) + '</option></select></label>' +
          '<div class="nb-share-row nb-share-add"><select data-share-who aria-label="' + esc(t('Person or project group')) + '">' +
          (options || '<option value="">' + esc(t('Everyone already has access')) + '</option>') + '</select>' +
          roleSelect('data-share-new-role aria-label="' + esc(t('Role')) + '"', 'view') +
          '<button type="button" class="btn" data-share-add>' + esc(t('Share')) + '</button></div>' +
          '<ul class="nb-share-list">' + (shared.length ? shared.map(function (x) {
            return '<li><span class="nb-share-who">' + (x.group ? icon('users') : icon('user')) + esc(x.name) + '</span>' +
              roleSelect('data-share-role="' + esc(x.username) + '" aria-label="' + esc(t('Role')) + '"', x.role) +
              '<button type="button" class="nb-share-remove" data-share-remove="' + esc(x.username) + '" aria-label="' + esc(t('Stop sharing')) + '">' + icon('x') + '</button></li>';
          }).join('') : '<li class="nb-muted">' + esc(t('Not shared with anyone yet.')) + '</li>') + '</ul>';
      }).catch(function (err) { body.innerHTML = '<p class="nb-warn">' + esc(err.message) + '</p>'; });
    }
    function send(url, payload, note) {
      return api(url, payload).then(function () { changed = true; if (note) toast(note); draw(); }).catch(fail);
    }
    body.addEventListener('change', function (e) {
      if (e.target.matches('[data-share-lab]')) {
        var role = e.target.value;
        send(role ? base : base + '/remove', role ? { username: '*', role: role } : { username: '*' });
      } else if (e.target.dataset.shareRole) {
        send(base, { username: e.target.dataset.shareRole, role: e.target.value });
      }
    });
    body.addEventListener('click', function (e) {
      var add = e.target.closest('[data-share-add]');
      var remove = e.target.closest('[data-share-remove]');
      if (add) {
        var who = body.querySelector('[data-share-who]').value;
        if (who) send(base, { username: who, role: body.querySelector('[data-share-new-role]').value }, t('Shared. They have been told.'));
      }
      if (remove) send(base + '/remove', { username: remove.dataset.shareRemove });
    });
    dialog.showModal();
    draw();
  }

  var menu = document.getElementById('nb-topic-menu');
  var menuTab = null, menuButton = null;
  function closeMenu() { if (menu) menu.hidden = true; menuTab = null; }
  document.addEventListener('click', function (e) {
    var plus = e.target.closest('[data-topic-new-page]');
    var dots = e.target.closest('[data-topic-menu]');
    var act = e.target.closest('[data-topic-do]');
    var sub = e.target.closest('[data-topic-new-sub]');
    var edit = e.target.closest('[data-topic-edit]');
    var share = e.target.closest('[data-topic-share]');
    if (picker && !picker.contains(e.target)) closePicker();
    if (share) { e.preventDefault(); shareTopic(share.dataset.topicShare); return; }
    if (plus) { e.preventDefault(); e.stopPropagation(); newPageIn(plus.dataset.topicNewPage); return; }
    if (sub) { e.preventDefault(); newTopicIn(sub.dataset.topicNewSub); return; }
    if (edit) { e.preventDefault(); editTopic(edit.dataset.topicEdit); return; }
    if (dots && menu) {
      e.preventDefault(); e.stopPropagation();
      if (menuTab === dots.dataset.topicMenu && !menu.hidden) { closeMenu(); return; }
      menuTab = dots.dataset.topicMenu;
      menuButton = dots;
      $$('[data-not-inbox]', menu).forEach(function (b) { b.hidden = dots.hasAttribute('data-inbox'); });
      openAt(menu, dots);
      return;
    }
    if (act && menuTab) {
      var tabId = menuTab;
      closeMenu();
      var what = act.dataset.topicDo;
      if (what === 'page') newPageIn(tabId);
      if (what === 'sub') newTopicIn(tabId);
      if (what === 'edit') editTopic(tabId);
      if (what === 'move') movePicker(tabId, menuButton);
      if (what === 'share') shareTopic(tabId);
      if (what === 'delete') removeTopic(tabId);
      return;
    }
    if (menu && !menu.hidden && !menu.contains(e.target)) closeMenu();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (menu && !menu.hidden) closeMenu();
    closePicker();
  });
  window.addEventListener('resize', function () { closeMenu(); closePicker(); });
  var head = document.querySelector('.nb-topic-title');
  var topicPage = document.getElementById('nb-topic');
  if (head && topicPage && topicPage.querySelector('[data-topic-edit]')) {
    head.addEventListener('dblclick', function () { editTopic(topicPage.dataset.topicId); });
  }

  // ------------------------------------------------------------ folding, remembered

  function remembered(key) {
    try { return JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (e) { return {}; }
  }
  function remember(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (err) { /* private window: not remembered */ }
  }
  // Topics: open as you left them (the open page's topic always is).
  var OPEN = 'nb-open-topics';
  var opened = remembered(OPEN);
  function folded(d) {
    var b = d.querySelector(':scope > summary > .nb-tab-fold');
    if (b) b.setAttribute('aria-expanded', String(d.open));
  }
  $$('.notebook-tab').forEach(function (d) { if (opened[d.dataset.tabId]) d.open = true; folded(d); });
  document.addEventListener('toggle', function (e) {
    var d = e.target;
    if (!d.classList || !d.classList.contains('notebook-tab')) return;
    folded(d);
    if (d.open) opened[d.dataset.tabId] = 1; else delete opened[d.dataset.tabId];
    remember(OPEN, opened);
  }, true);
  // A button inside the row doesn't fold it by itself, as the row does: the arrow does it here.
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.nb-tab-fold');
    if (!b) return;
    e.preventDefault();
    var d = b.closest('.notebook-tab');
    d.open = !d.open;
  });

  // Sub-pages: the arrow beside a page shows or hides them. The open
  // page's own line stays shown.
  var SHUT = 'nb-closed-subpages';
  var closed = remembered(SHUT);
  function paint(li, shut) {
    li.classList.toggle('is-collapsed', shut);
    var b = li.querySelector(':scope > .nb-subpages-toggle');
    if (b) b.setAttribute('aria-expanded', String(!shut));
  }
  $$('.notebook-page-item.has-children').forEach(function (li) {
    var holdsOpen = li.querySelector('.notebook-subpages .notebook-page-item.active');
    paint(li, !!closed[li.dataset.pageId] && !holdsOpen);
  });
  document.addEventListener('click', function (e) {
    var b = e.target.closest('.nb-subpages-toggle');
    if (!b) return;
    e.preventDefault();
    var li = b.closest('.notebook-page-item');
    var shut = !li.classList.contains('is-collapsed');
    paint(li, shut);
    if (shut) closed[li.dataset.pageId] = 1; else delete closed[li.dataset.pageId];
    remember(SHUT, closed);
  });

  // ------------------------------------------------------------ favourites

  var favBox = document.getElementById('nb-favorites');
  var favList = document.getElementById('nb-favorite-list');
  function favLine(id, title, kind) {
    var li = document.createElement('li');
    li.className = 'notebook-page-item';
    li.dataset.pageId = id;
    li.innerHTML = '<a href="/notebook?page=' + id + '">' + icon((DATA.kindIcons || {})[kind] || 'file').replace('class="icon"', 'class="icon notebook-page-icon"') +
      '<span class="page-title-label">' + esc(title || t('Untitled page')) + '</span></a>';
    return li;
  }
  function star(id, on, from) {
    return api('/notebook/api/pages/' + id + '/favorite', { on: on }).then(function () {
      $$('[data-star="' + id + '"]').forEach(function (b) {
        b.classList.toggle('is-on', on);
        b.setAttribute('aria-pressed', String(on));
      });
      if (favList) {
        var line = favList.querySelector('[data-page-id="' + id + '"]');
        if (!on && line) line.remove();
        if (on && !line) {
          var card = from && from.closest('.nb-card');
          var title = card ? card.querySelector('.nb-card-link b').textContent
            : (document.getElementById('page-title') || {}).value || (DATA.page && DATA.page.title);
          var kind = card ? card.dataset.kind : DATA.page && DATA.page.kind;
          favList.appendChild(favLine(id, title, kind));
        }
        favBox.classList.toggle('is-empty', !favList.children.length);
      }
      toast(on ? t('Added to Favorites.') : t('Taken out of Favorites.'));
    }).catch(fail);
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-star]');
    if (!b) return;
    e.preventDefault();
    e.stopPropagation();
    star(b.dataset.star, !b.classList.contains('is-on'), b);
  });

  // ------------------------------------------------------------ a topic's page

  var cards = document.getElementById('nb-cards');
  var VIEW = 'nb-cards-view';
  function setView(view) {
    if (!cards) return;
    cards.classList.toggle('is-list', view === 'list');
    $$('[data-cards-view]').forEach(function (b) {
      b.classList.toggle('is-active', b.dataset.cardsView === view);
      b.setAttribute('aria-pressed', String(b.dataset.cardsView === view));
    });
  }
  try { setView(localStorage.getItem(VIEW) === 'list' ? 'list' : 'cards'); } catch (e) { setView('cards'); }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-cards-view]');
    if (!b) return;
    setView(b.dataset.cardsView);
    try { localStorage.setItem(VIEW, b.dataset.cardsView); } catch (err) { /* private window: not remembered */ }
  });
  // A card's folder button moves it to the topic picked.
  document.addEventListener('change', function (e) {
    var sel = e.target.closest && e.target.closest('select[data-move-page]');
    if (!sel) return;
    api('/notebook/api/pages/' + sel.dataset.movePage + '/place', { tab_id: Number(sel.value), parent_page_id: null })
      .then(reload).catch(fail);
  });
  // When each card was last changed, in words ("today 14:05", "3 Oct").
  function times() {
    if (!window.NotebookPage) return;
    $$('time[data-when]').forEach(function (el) {
      var text = window.NotebookPage.timeText(el.getAttribute('datetime'));
      if (text) el.textContent = text;
    });
  }
  if (document.readyState === 'complete') times(); else window.addEventListener('load', times);

  // ------------------------------------------------------------ dragging

  var drag = null;      // { kind: page | topic | fav | ref | card, id, el }
  var mark = null;      // { el, cls }: where it would land, shown
  var listView = function () { return cards && cards.classList.contains('is-list'); };

  function unmark() { if (mark) mark.el.classList.remove(mark.cls); mark = null; }
  function show(el, zone) {
    var cls = zone === 'before' ? 'is-drop-before' : zone === 'after' ? 'is-drop-after' : 'is-drop';
    if (mark && mark.el === el && mark.cls === cls) return;
    unmark();
    el.classList.add(cls);
    mark = { el: el, cls: cls };
  }
  function half(e, el, across) {
    var box = el.getBoundingClientRect();
    return across ? (e.clientX < box.left + box.width / 2 ? 'before' : 'after')
      : (e.clientY < box.top + box.height / 2 ? 'before' : 'after');
  }
  function thirds(e, el) {
    var box = el.getBoundingClientRect();
    var y = (e.clientY - box.top) / box.height;
    return y < 0.28 ? 'before' : y > 0.72 ? 'after' : 'in';
  }
  function isPage() { return drag && (drag.kind === 'page' || drag.kind === 'card'); }

  // Where the thing dragged would land over this element, or null.
  function landing(e) {
    var x = e.target && e.target.nodeType === 1 ? e.target : e.target && e.target.parentNode;
    if (!x || !x.closest || !drag) return null;
    var inFavs = favBox && x.closest('#nb-favorites');
    if (inFavs) {
      var fav = x.closest('#nb-favorite-list > .notebook-page-item');
      if (drag.kind === 'fav') return fav && fav !== drag.el ? { el: fav, zone: half(e, fav), what: 'fav-order', id: fav.dataset.pageId } : null;
      return drag.kind === 'topic' ? null : { el: favBox, zone: 'in', what: 'fav-add' };
    }
    if (drag.kind === 'fav' || drag.kind === 'ref') return null;
    if (x.closest('.notebook-tab.is-shared')) return null;     // someone else's: not to move things into
    var summary = x.closest('.notebook-tab > summary');
    if (summary) {
      var d = summary.parentNode;
      if (drag.kind === 'topic') {
        if (within(d.dataset.tabId, drag.id)) return null;
        var zone = thirds(e, summary);
        if (zone === 'in' && d.classList.contains('is-inbox')) zone = half(e, summary);
        return { el: summary, zone: zone, what: 'topic', topic: d };
      }
      return { el: summary, zone: 'in', what: 'page-into', tab: d.dataset.tabId };
    }
    var item = x.closest('.notebook-tab .notebook-page-item');
    if (item) {
      if (!isPage() || item === drag.el || drag.el.contains(item)) return null;
      var row = item.querySelector(':scope > a');
      return { el: row, zone: half(e, row), what: 'page-beside', item: item };
    }
    if (drag.kind === 'topic' && x.closest('#nb-topics-head')) return { el: x.closest('#nb-topics-head'), zone: 'in', what: 'topic-top' };
    var tile = x.closest('[data-drop-topic]');
    if (tile) {
      if (isPage()) return { el: tile, zone: 'in', what: 'page-into', tab: tile.dataset.dropTopic };
      if (drag.kind === 'topic' && !within(tile.dataset.dropTopic, drag.id)) return { el: tile, zone: 'in', what: 'topic-into', tab: tile.dataset.dropTopic };
      return null;
    }
    var card = x.closest('.nb-card');
    if (card && drag.kind === 'card' && card !== drag.el && cards && cards.dataset.sort === 'order') {
      return { el: card, zone: half(e, card, !listView()), what: 'card-order', id: card.dataset.pageId };
    }
    return null;
  }

  function land(spot) {
    var id = drag.id;
    var side = spot.zone === 'before' ? 'before_id' : 'after_id';
    var body = {};
    if (spot.what === 'fav-add') return star(id, true).then(reload);
    if (spot.what === 'fav-order') {
      body[side] = Number(spot.id);
      return api('/notebook/api/favorites/' + id + '/place', body).then(reload).catch(fail);
    }
    if (spot.what === 'topic') {
      var d = spot.topic;
      if (spot.zone === 'in') return placeTopic(id, { parent_id: Number(d.dataset.tabId) });
      body.parent_id = d.dataset.parentId ? Number(d.dataset.parentId) : null;
      body[side] = Number(d.dataset.tabId);
      return placeTopic(id, body);
    }
    if (spot.what === 'topic-top') return placeTopic(id, { parent_id: null });
    if (spot.what === 'topic-into') return placeTopic(id, { parent_id: Number(spot.tab) });
    if (spot.what === 'page-into') body = { tab_id: Number(spot.tab), parent_page_id: null };
    if (spot.what === 'page-beside') {
      var up = spot.item.parentNode.closest('.notebook-page-item');
      body = { tab_id: Number(spot.item.dataset.tabId), parent_page_id: up ? Number(up.dataset.pageId) : null };
      body[side] = Number(spot.item.dataset.pageId);
    }
    if (spot.what === 'card-order') {
      body = { tab_id: Number(document.getElementById('nb-topic').dataset.topicId) };
      body[side] = Number(spot.id);
    }
    return api('/notebook/api/pages/' + id + '/place', body).then(reload).catch(fail);
  }

  document.addEventListener('dragstart', function (e) {
    var x = e.target && e.target.closest ? e.target : null;
    if (!x) return;
    var fav = x.closest('#nb-favorite-list > .notebook-page-item');
    var shared = x.closest('.notebook-tab.is-shared');
    var page = !shared && x.closest('.notebook-tab .notebook-page-item');
    var summary = !shared && x.closest('.notebook-tab > summary');
    var ref = x.closest('.notebook-sidebar .notebook-page-item');
    var card = x.closest('.nb-card');
    if (fav) drag = { kind: 'fav', id: fav.dataset.pageId, el: fav };
    else if (page) drag = { kind: 'page', id: page.dataset.pageId, el: page };
    else if (summary) drag = { kind: 'topic', id: summary.parentNode.dataset.tabId, el: summary.parentNode };
    else if (ref) drag = { kind: 'ref', id: ref.dataset.pageId, el: ref };
    else if (card && card.getAttribute('draggable') === 'true') drag = { kind: 'card', id: card.dataset.pageId, el: card };
    else return;
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', drag.el.textContent.trim().slice(0, 200)); } catch (err) { /* old browser */ }
    drag.el.classList.add('is-dragging');
    document.body.classList.add('nb-dragging', drag.kind === 'topic' ? 'nb-dragging-topic' : 'nb-dragging-page');
  });
  document.addEventListener('dragover', function (e) {
    if (!drag) return;
    var spot = landing(e);
    if (!spot) { unmark(); return; }
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    show(spot.el, spot.zone);
  });
  document.addEventListener('drop', function (e) {
    if (!drag) return;
    var spot = landing(e);
    unmark();
    if (!spot) return;
    e.preventDefault();
    land(spot);
  });
  document.addEventListener('dragend', function () {
    unmark();
    if (drag) drag.el.classList.remove('is-dragging');
    drag = null;
    document.body.classList.remove('nb-dragging', 'nb-dragging-topic', 'nb-dragging-page');
  });
})();
