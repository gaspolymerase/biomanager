/* The notebook page around the editor: saving, the page's kind, status and
 * tags, and the side panels — sharing, comments, version history, search,
 * meetings, recipes, protocols. The editor itself is the bundle in notebook-build/
 * (frontend/src/main.js); without it the page falls back to a plain
 * Markdown textarea so nothing is ever locked away.
 */
(function () {
  'use strict';

  var dataEl = document.getElementById('nb-data');
  if (!dataEl) return;
  var DATA = JSON.parse(dataEl.textContent || '{}');
  var page = DATA.page;
  var me = DATA.me || {};
  var canEdit = !!page && (page.role === 'owner' || page.role === 'edit');
  var isOwner = !!page && page.role === 'owner';
  var nb = null; // the mounted editor (BiomanagerNotebook.mount)
  var people = null;

  // ------------------------------------------------------------ helpers

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function icon(name) { return '<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#' + name + '"></use></svg>'; }
  // Dates in the page's language; a label that arrives as a value with its
  // meaning here ("notebook::Note": a page, not a note on a record).
  var LOC = window.BM_LANG === 'zh' ? 'zh-CN' : [];
  function tc(ctx, s) { var k = ctx + '::' + s; var v = t(k); return v === k ? t(s) : v; }
  // Server times are UTC without a zone.
  function when(iso) {
    if (!iso) return null;
    return new Date(/Z|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
  }
  // Times on the lab's clock, not this browser's: "started 16:00" in the
  // header then says what the "Started: 16:00" line in the page says, for
  // everyone, wherever their computer thinks it is.
  var ZONE = (function (z) {
    try { if (z) new Intl.DateTimeFormat([], { timeZone: z }); return z || undefined; } catch (_) { return undefined; }
  })(DATA.labZone);
  function inLab(o) {
    o = Object.assign({}, o || {});
    if (ZONE) o.timeZone = ZONE;
    return o;
  }
  function timeText(iso) {
    var d = when(iso);
    if (!d || isNaN(d)) return '';
    var now = new Date();
    var day = function (x) { return x.toLocaleDateString('en-CA', inLab()); };
    var same = day(d) === day(now);
    var hm = d.toLocaleTimeString(LOC, inLab({ hour: '2-digit', minute: '2-digit' }));
    return same ? t('today') + ' ' + hm : d.toLocaleDateString(LOC, inLab({ day: 'numeric', month: 'short', year: day(d).slice(0, 4) === day(now).slice(0, 4) ? undefined : 'numeric' })) + ' ' + hm;
  }
  function api(url, opts) {
    opts = opts || {};
    var init = { method: opts.method || (opts.body || opts.form ? 'POST' : 'GET'), headers: { Accept: 'application/json' } };
    if (opts.form) init.body = opts.form;
    else if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    return fetch(url, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok || data.ok === false) {
          var err = new Error(data.error || ('HTTP ' + r.status));
          err.status = r.status; err.data = data;
          throw err;
        }
        return data;
      });
    });
  }
  var toastTimer = null;
  function toast(message, isError) {
    var el = $('#nb-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'nb-toast';
      el.className = 'nb-toast';
      el.setAttribute('role', 'status');
      document.body.appendChild(el);
    }
    el.innerHTML = message;
    el.classList.toggle('is-error', !!isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 4200);
  }
  function debounce(fn, ms) {
    var t = null;
    return function () {
      var args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(null, args); }, ms);
    };
  }
  function loadPeople() {
    if (people) return Promise.resolve(people);
    return api('/notebook/api/people').then(function (d) { people = d.people; return people; });
  }
  function initials(name) {
    var parts = String(name || '?').trim().split(/\s+/);
    return ((parts[0] || '?')[0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
  }
  var COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#c98500', '#d55181', '#008300', '#4a3aa7', '#e34948'];
  function colorFor(name) {
    var h = 0;
    for (var i = 0; i < String(name).length; i++) h = (h * 31 + String(name).charCodeAt(i)) >>> 0;
    return COLORS[h % COLORS.length];
  }
  function go(url) { window.location.href = url; }

  // ------------------------------------------------------------ sidebar

  var filter = $('#notebook-page-filter');
  if (filter) {
    filter.addEventListener('input', function () {
      var q = filter.value.trim().toLowerCase();
      $$('.notebook-page-item[data-page-title]').forEach(function (li) {
        li.style.display = !q || (li.dataset.pageTitle || '').indexOf(q) >= 0 ? '' : 'none';
      });
      if (q) $$('.notebook-tab').forEach(function (tab) { tab.setAttribute('open', ''); });
    });
  }
  var searchForm = $('#nb-search-form');
  if (searchForm) {
    searchForm.addEventListener('submit', function (event) {
      event.preventDefault();
      openPanel('search', { q: filter.value.trim() });
    });
  }
  $$('.nb-tag[data-tag]').forEach(function (b) {
    b.addEventListener('click', function () { openPanel('search', { tag: b.dataset.tag }); });
  });
  $$('[data-panel]').forEach(function (b) {
    b.addEventListener('click', function () { openPanel(b.dataset.panel); });
  });

  var sideToggle = $('#nb-side-toggle');
  if (sideToggle) sideToggle.addEventListener('click', function () {
    var open = $('#nb-shell').classList.toggle('nb-side-open');
    sideToggle.setAttribute('aria-expanded', String(open));
  });

  // ------------------------------------------------------------ new pages, templates, import

  var modal = $('#notebook-template-modal');
  function openNewPage() {
    if (!modal) return;
    modal.hidden = false;
    loadTemplates();
  }
  function closeNewPage() { if (modal) modal.hidden = true; }
  $$('[data-close-modal]').forEach(function (b) { b.addEventListener('click', closeNewPage); });
  var newBtn = $('#nb-new-page');
  if (newBtn) newBtn.addEventListener('click', openNewPage);

  $$('[data-starter]').forEach(function (b) {
    b.addEventListener('click', function () {
      var body = { starter: b.dataset.starter, open_tab_id: DATA.topicChosen ? DATA.selectedTab || '' : '' };
      api('/notebook/api/pages/new', { body: body }).then(function (d) { go(d.url); })
        .catch(function (e) { toast(t('Could not make the page: %(error)s', { error: esc(e.message) }), true); });
    });
  });

  function loadTemplates() {
    var list = $('#notebook-template-list');
    if (!list) return;
    list.innerHTML = '<div class="notebook-template-loading">' + t('Loading…') + '</div>';
    api('/notebook/templates').then(function (d) {
      if (!d.templates.length) {
        list.innerHTML = '<div class="notebook-template-empty">' + t('None yet. Use “Save as template” on any page, or make one below.') + '</div>';
        return;
      }
      list.innerHTML = d.templates.map(function (tpl) {
        var tags = (tpl.kind && tpl.kind !== 'note' ? '<span class="badge badge-quiet">' + esc(tc('notebook', tpl.kind_label)) + '</span>' : '') +
          (tpl.lab ? '<span class="badge badge-brand" title="' + (tpl.group ? t('Everyone in %(group)s can use it', { group: esc(tpl.group) }) : t('Everyone in the lab can use it')) + '">' +
            (tpl.group ? esc(tpl.group) : t('Lab')) + (tpl.mine ? '' : ' · ' + esc(tpl.owner_name)) + '</span>' : '');
        return '<div class="notebook-template-row"><button type="button" class="notebook-template-pick" data-template="' + tpl.id + '">' +
          '<span class="notebook-template-icon">' + (tpl.icon ? esc(tpl.icon) : icon('file')) + '</span>' +
          '<span class="notebook-template-meta"><span class="notebook-template-title">' + esc(tpl.title) + ' ' + tags + '</span>' +
          '<span class="notebook-template-preview">' + esc(tpl.body_preview || '') + '</span></span></button>' +
          (tpl.can_delete ? '<button type="button" class="notebook-template-del" title="' + t('Delete template') + '" aria-label="' + t('Delete template') + '" data-del-template="' + tpl.id + '">' + icon('trash') + '</button>' : '') + '</div>';
      }).join('');
    }).catch(function () { list.innerHTML = '<div class="notebook-template-empty">' + t('Could not load templates.') + '</div>'; });
  }
  if (modal) {
    modal.addEventListener('click', function (event) {
      var pick = event.target.closest('[data-template]');
      var del = event.target.closest('[data-del-template]');
      if (pick) {
        api('/notebook/api/pages/new', { body: { template_id: Number(pick.dataset.template), open_tab_id: DATA.topicChosen ? DATA.selectedTab || '' : '' } }).then(function (d) { go(d.url); });
      } else if (del) {
        BioDialog.confirm(t('Delete this template?'), { danger: true }).then(function (ok) {
          if (ok) fetch('/notebook/templates/' + del.dataset.delTemplate + '/delete', { method: 'POST' }).then(loadTemplates);
        });
      }
    });
    var tform = $('#notebook-template-form');
    if (tform) tform.addEventListener('submit', function (event) {
      event.preventDefault();
      saveTemplate(new FormData(tform)).then(function (d) { if (d && d.ok) { tform.reset(); loadTemplates(); } });
    });
  }

  // Save a template; one of the same name already yours is replaced only
  // when that is what was wanted.
  function saveTemplate(form) {
    var post = function () { return fetch('/notebook/templates/create', { method: 'POST', body: form }).then(function (r) { return r.json(); }); };
    return post().then(function (d) {
      if (!d.exists) return d;
      return BioDialog.confirm(d.error + ' ' + t('Replace it with this one?'), { okLabel: t('Replace it') }).then(function (ok) {
        if (!ok) return null;
        form.set('replace', '1');
        return post();
      });
    });
  }

  function importMarkdown() {
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = '.md,.markdown,.txt,text/markdown,text/plain';
    input.addEventListener('change', function () {
      if (!input.files[0]) return;
      var form = new FormData();
      form.append('file', input.files[0]);
      if (DATA.selectedTab) form.append('tab_id', DATA.selectedTab);
      api('/notebook/api/import', { form: form }).then(function (d) { go(d.url); })
        .catch(function (e) { toast(t('Could not import: %(error)s', { error: esc(e.message) }), true); });
    });
    input.click();
  }
  ['#nb-import', '#nb-import-2'].forEach(function (sel) { var b = $(sel); if (b) b.addEventListener('click', importMarkdown); });

  // ------------------------------------------------------------ drawer

  var drawer = $('#nb-drawer');
  var drawerBody = $('#nb-drawer-body');
  var drawerTitle = $('#nb-drawer-title');
  var currentPanel = null;
  function closePanel() {
    if (drawer) drawer.hidden = true;
    document.body.classList.remove('nb-drawer-open');
    currentPanel = null;
  }
  if ($('#nb-drawer-close')) $('#nb-drawer-close').addEventListener('click', closePanel);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && currentPanel && !document.querySelector('.nb-run')) closePanel();
  });
  function openPanel(name, opts) {
    if (!drawer) return;
    var panel = PANELS[name];
    if (!panel) return;
    currentPanel = name;
    drawer.hidden = false;
    drawer.dataset.panel = name;
    document.body.classList.add('nb-drawer-open');
    drawerTitle.textContent = t(panel.title);
    // A fresh box each time: a slow answer for a panel already closed
    // lands in a box no longer on the page, not in the one now open.
    var box = document.createElement('div');
    box.className = 'nb-panel';
    box.innerHTML = '<p class="nb-muted">' + t('Loading…') + '</p>';
    drawerBody.innerHTML = '';
    drawerBody.appendChild(box);
    panel.render(box, opts || {});
  }

  var PANELS = {};

  // ---- share
  PANELS.share = {
    title: 'Share',
    render: function (box) {
      Promise.all([api('/notebook/api/pages/' + page.id + '/shares'), loadPeople()]).then(function (res) {
        var d = res[0];
        var lab = d.shares.filter(function (s) { return s.username === '*'; })[0];
        var shared = d.shares.filter(function (s) { return s.username !== '*'; });
        var taken = {};
        shared.forEach(function (s) { taken[s.username] = true; });
        var options = d.people.filter(function (p) { return !taken[p.username]; })
          .map(function (p) { return '<option value="' + esc(p.username) + '">' + esc(p.name) + (p.guest ? ' ' + t('(guest)') : '') + '</option>'; }).join('');
        // Project groups the page can be shared with, as one choice each.
        var groupOptions = (d.groups || []).filter(function (grp) { return !taken[grp.username]; })
          .map(function (grp) { return '<option value="' + esc(grp.username) + '">' + esc(grp.name) + '</option>'; }).join('');
        if (groupOptions) options = (options ? '<optgroup label="' + t('People') + '">' + options + '</optgroup>' : '') +
          '<optgroup label="' + t('Project groups') + '">' + groupOptions + '</optgroup>';
        box.innerHTML =
          '<p class="nb-muted">' + t('People you share with see this page under <b>Shared with me</b>. Editors write in it with you, live. Share with a project group and everyone in it can.') + '</p>' +
          '<div class="nb-field-row"><label>' + t('Everyone in the lab') + ' <select id="nb-share-lab">' +
          '<option value="">' + t('can’t see it') + '</option><option value="view"' + (lab && lab.role === 'view' ? ' selected' : '') + '>' + t('can view') + '</option>' +
          '<option value="edit"' + (lab && lab.role === 'edit' ? ' selected' : '') + '>' + t('can edit') + '</option></select></label></div>' +
          '<form class="nb-field-row" id="nb-share-add"><select id="nb-share-who" aria-label="' + t('Person or project group') + '">' + (options || '<option value="">' + t('Everyone already has access') + '</option>') + '</select>' +
          '<select id="nb-share-role" aria-label="' + t('Role') + '"><option value="edit">' + t('can edit') + '</option><option value="view">' + t('can view') + '</option></select>' +
          '<button class="btn btn-primary" type="submit">' + t('Share') + '</button></form>' +
          '<ul class="nb-list">' + (shared.length ? shared.map(function (s) {
            var avatar = s.group ? '<span class="nb-avatar" style="background:#0d9488">' + icon('users') + '</span>'
              : '<span class="nb-avatar" style="background:' + colorFor(s.username) + '">' + esc(initials(s.name)) + '</span>';
            return '<li class="nb-list-row">' + avatar + '<span class="nb-grow">' + esc(s.name) + '</span>' +
              '<select data-share-role="' + esc(s.username) + '"><option value="edit"' + (s.role === 'edit' ? ' selected' : '') + '>' + t('can edit') + '</option><option value="view"' + (s.role === 'view' ? ' selected' : '') + '>' + t('can view') + '</option></select>' +
              '<button type="button" class="nb-icon-btn" data-share-remove="' + esc(s.username) + '" aria-label="' + t('Stop sharing') + '">' + icon('close') + '</button></li>';
          }).join('') : '<li class="nb-muted">' + t('Not shared with anyone yet.') + '</li>') + '</ul>';
        var refresh = function () { PANELS.share.render(box); setSharedLabel(true); };
        $('#nb-share-lab', box).addEventListener('change', function (e) {
          var role = e.target.value;
          (role ? api('/notebook/api/pages/' + page.id + '/shares', { body: { username: '*', role: role } })
            : api('/notebook/api/pages/' + page.id + '/shares/remove', { body: { username: '*' } })).then(refresh);
        });
        $('#nb-share-add', box).addEventListener('submit', function (e) {
          e.preventDefault();
          var who = $('#nb-share-who', box).value;
          if (!who) return;
          api('/notebook/api/pages/' + page.id + '/shares', { body: { username: who, role: $('#nb-share-role', box).value } })
            .then(function () { toast(t('Shared. They have been told.')); refresh(); })
            .catch(function (err) { toast(esc(err.message), true); });
        });
        box.onchange = function (e) {      // once per box, however often the panel redraws
          var u = e.target.dataset && e.target.dataset.shareRole;
          if (u) api('/notebook/api/pages/' + page.id + '/shares', { body: { username: u, role: e.target.value } });
        };
        box.onclick = function (e) {
          var b = e.target.closest('[data-share-remove]');
          if (b) api('/notebook/api/pages/' + page.id + '/shares/remove', { body: { username: b.dataset.shareRemove } }).then(refresh);
        };
      }).catch(function (e) { box.innerHTML = '<p class="nb-warn">' + esc(e.message) + '</p>'; });
    },
  };
  function setSharedLabel(shared) {
    var b = $('.nb-tool[data-panel="share"] span');
    if (b) b.textContent = shared ? t('Shared') : t('Share');
  }

  // ---- comments
  var commentThreads = [];
  function mentionize(text) {
    return esc(text).replace(/(^|\s)@([A-Za-z0-9][\w.-]*)/g, '$1<span class="nb-mention">@$2</span>').replace(/\n/g, '<br>');
  }
  function attachMentions(textarea) {
    var menu = document.createElement('div');
    menu.className = 'nb-mention-menu';
    menu.hidden = true;
    textarea.parentNode.insertBefore(menu, textarea.nextSibling);
    function word() {
      var before = textarea.value.slice(0, textarea.selectionStart);
      var m = /(^|\s)@([\w.-]*)$/.exec(before);
      return m ? m[2] : null;
    }
    textarea.addEventListener('input', function () {
      var w = word();
      if (w === null) { menu.hidden = true; return; }
      loadPeople().then(function (list) {
        var q = w.toLowerCase();
        var hits = list.filter(function (p) { return p.username !== me.username && (p.username.toLowerCase().indexOf(q) === 0 || p.name.toLowerCase().indexOf(q) >= 0); }).slice(0, 6);
        menu.innerHTML = hits.map(function (p) { return '<button type="button" data-user="' + esc(p.username) + '"><b>' + esc(p.name) + '</b> <small>@' + esc(p.username) + '</small></button>'; }).join('');
        menu.hidden = !hits.length;
      });
    });
    function pick(b) {
      var pos = textarea.selectionStart;
      var before = textarea.value.slice(0, pos).replace(/@([\w.-]*)$/, '@' + b.dataset.user + ' ');
      textarea.value = before + textarea.value.slice(pos);
      textarea.selectionStart = textarea.selectionEnd = before.length;
      menu.hidden = true;
      textarea.focus();
    }
    function highlight(step) {
      var items = $$('[data-user]', menu);
      if (!items.length) return;
      var at = items.findIndex(function (x) { return x.classList.contains('is-active'); });
      items.forEach(function (x) { x.classList.remove('is-active'); });
      items[(at + step + items.length) % items.length].classList.add('is-active');
    }
    // With the list open, the keyboard chooses from it: arrows move, Enter
    // or Tab picks (the first one when none is highlighted), Escape closes.
    // Enter used to add a new line and leave "@Sas" as plain text, so
    // nobody was told.
    textarea.addEventListener('keydown', function (e) {
      if (menu.hidden) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); highlight(e.key === 'ArrowDown' ? 1 : -1); }
      else if (e.key === 'Enter' || e.key === 'Tab') {
        var b = $('[data-user].is-active', menu) || $('[data-user]', menu);
        if (b) { e.preventDefault(); e.stopPropagation(); pick(b); }
      } else if (e.key === 'Escape') { e.preventDefault(); menu.hidden = true; }
    });
    menu.addEventListener('mousedown', function (e) { e.preventDefault(); });
    menu.addEventListener('click', function (e) {
      var b = e.target.closest('[data-user]');
      if (b) pick(b);
    });
    textarea.addEventListener('blur', function () { setTimeout(function () { menu.hidden = true; }, 150); });
  }
  function refreshHighlights() {
    if (!nb) return;
    nb.setQuotes(commentThreads.filter(function (t) { return !t.resolved && t.quote; }).map(function (t) { return { id: t.id, quote: t.quote }; }));
    var open = commentThreads.filter(function (t) { return !t.resolved; }).length;
    var badge = $('#nb-comment-count');
    var tool = $('.nb-tool[data-panel="comments"]');
    if (!badge && open && tool) { badge = document.createElement('b'); badge.className = 'nb-badge'; badge.id = 'nb-comment-count'; tool.appendChild(badge); }
    if (badge) { badge.textContent = open; badge.hidden = !open; }
  }
  function loadComments() {
    return api('/notebook/api/pages/' + page.id + '/comments').then(function (d) {
      commentThreads = d.threads;
      refreshHighlights();
      return d;
    });
  }
  function commentHtml(c, isReply) {
    return '<div class="nb-comment' + (isReply ? ' is-reply' : '') + '" id="comment-' + c.id + '">' +
      '<div class="nb-comment-head"><span class="nb-avatar" style="background:' + colorFor(c.author) + '">' + esc(initials(c.author_name)) + '</span>' +
      '<b>' + esc(c.author_name) + '</b><time>' + esc(timeText(c.created_at)) + (c.updated_at ? ' · ' + t('edited') : '') + '</time>' +
      (c.can_delete ? '<button type="button" class="nb-icon-btn" data-comment-delete="' + c.id + '" aria-label="' + t('Delete comment') + '">' + icon('trash') + '</button>' : '') + '</div>' +
      '<div class="nb-comment-body">' + mentionize(c.body) + '</div></div>';
  }
  PANELS.comments = {
    title: 'Comments',
    render: function (box, opts) {
      var selected = nb ? nb.selectionText() : '';
      loadComments().then(function () {
        var open = commentThreads.filter(function (t) { return !t.resolved; });
        var done = commentThreads.filter(function (t) { return t.resolved; });
        var thread = function (th) {
          return '<article class="nb-thread' + (th.resolved ? ' is-resolved' : '') + '" data-thread="' + th.id + '">' +
            (th.quote ? '<button type="button" class="nb-thread-quote" data-quote="' + th.id + '">“' + esc(th.quote) + '”</button>' : '') +
            commentHtml(th) + th.replies.map(function (r) { return commentHtml(r, true); }).join('') +
            '<form class="nb-reply" data-reply="' + th.id + '"><textarea rows="1" placeholder="' + t('Reply… @name to tell someone') + '"></textarea><button class="btn" type="submit">' + t('Reply') + '</button></form>' +
            '<button type="button" class="btn-link" data-resolve="' + th.id + '" data-to="' + (th.resolved ? '0' : '1') + '">' + (th.resolved ? t('Reopen') : t('Resolve')) + '</button></article>';
        };
        box.innerHTML =
          '<form class="nb-new-comment" id="nb-new-comment">' +
          (selected ? '<div class="nb-thread-quote is-static">“' + esc(selected.slice(0, 300)) + '”</div>' : '<p class="nb-muted">' + t('Select text in the page first to comment on that passage.') + '</p>') +
          '<textarea rows="3" placeholder="' + t('Comment… type @ to mention a lab mate') + '"></textarea>' +
          '<div class="nb-field-row"><span class="nb-grow"></span><button class="btn btn-primary" type="submit">' + t('Comment') + '</button></div></form>' +
          (open.length ? open.map(thread).join('') : '<p class="nb-muted">' + t('No open comments.') + '</p>') +
          (done.length ? '<details class="nb-resolved"><summary>' + t('%(n)s resolved', { n: done.length }) + '</summary>' + done.map(thread).join('') + '</details>' : '');
        $$('textarea', box).forEach(attachMentions);
        var form = $('#nb-new-comment', box);
        form.addEventListener('submit', function (e) {
          e.preventDefault();
          var text = $('textarea', form).value.trim();
          if (!text) return;
          api('/notebook/api/pages/' + page.id + '/comments', { body: { body: text, quote: selected.slice(0, 500) } }).then(afterPost);
        });
        if (opts.focus && commentThreads.length) {
          var target = $('#comment-' + opts.focus, box);
          if (target) { target.scrollIntoView({ block: 'center' }); target.classList.add('is-flash'); }
        } else {
          $('textarea', form).focus();
        }
        box.onsubmit = function (e) {
          var reply = e.target.closest('[data-reply]');
          if (!reply) return;
          e.preventDefault();
          var text = $('textarea', reply).value.trim();
          if (text) api('/notebook/api/pages/' + page.id + '/comments', { body: { body: text, parent_id: Number(reply.dataset.reply) } }).then(afterPost);
        };
        box.onclick = function (e) {
          var q = e.target.closest('[data-quote]');
          var r = e.target.closest('[data-resolve]');
          var del = e.target.closest('[data-comment-delete]');
          if (q && nb) nb.scrollToQuote(Number(q.dataset.quote));
          if (r) api('/notebook/api/comments/' + r.dataset.resolve + '/resolve', { body: { resolved: r.dataset.to === '1' } }).then(function () { PANELS.comments.render(box, {}); });
          if (del) BioDialog.confirm(t('Delete this comment?'), { danger: true }).then(function (ok) {
            if (ok) api('/notebook/api/comments/' + del.dataset.commentDelete + '/delete', { body: {} }).then(function () { PANELS.comments.render(box, {}); });
          });
        };
      }).catch(function (e) { box.innerHTML = '<p class="nb-warn">' + esc(e.message) + '</p>'; });

      function afterPost(d) {
        if (d.no_access && d.no_access.length) {
          var names = d.no_access_names.join(', ');
          if (isOwner) {
            BioDialog.confirm(t('%(names)s cannot see this page, so was not told. Let them view it?', { names: names }), { okLabel: t('Let them view it') }).then(function (ok) {
              if (!ok) return;
              Promise.all(d.no_access.map(function (u) { return api('/notebook/api/pages/' + page.id + '/shares', { body: { username: u, role: 'view' } }); }))
                .then(function () { toast(t('Shared with %(names)s.', { names: esc(names) })); });
            });
          } else {
            toast(t('%(names)s cannot see this page, so was not told. Ask %(owner)s to share it.', { names: esc(names), owner: esc(page.owner_name) }), true);
          }
        }
        PANELS.comments.render(box, {});
      }
    },
  };

  // ---- history
  function lineDiff(a, b) {
    var x = String(a || '').split('\n');
    var y = String(b || '').split('\n');
    if (x.length * y.length > 4e6) return null;
    var n = x.length, m = y.length;
    var dp = [];
    for (var i = 0; i <= n; i++) { dp.push(new Uint32Array(m + 1)); }
    for (i = n - 1; i >= 0; i--) for (var j = m - 1; j >= 0; j--) dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    var out = [];
    i = 0; j = 0;
    while (i < n && j < m) {
      if (x[i] === y[j]) { out.push([' ', x[i]]); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push(['-', x[i]]); i++; }
      else { out.push(['+', y[j]]); j++; }
    }
    while (i < n) out.push(['-', x[i++]]);
    while (j < m) out.push(['+', y[j++]]);
    return out;
  }
  function diffHtml(ops) {
    if (!ops) return '<p class="nb-muted">' + t('Too long to compare here.') + '</p>';
    var html = [];
    var skipped = 0;
    ops.forEach(function (op, idx) {
      var near = ops.slice(Math.max(0, idx - 2), idx + 3).some(function (o) { return o[0] !== ' '; });
      if (op[0] === ' ' && !near) { skipped++; return; }
      if (skipped) { html.push('<div class="nb-diff-skip">⋯ ' + t(skipped > 1 ? '%(n)s unchanged lines' : '%(n)s unchanged line', { n: skipped }) + '</div>'); skipped = 0; }
      html.push('<div class="nb-diff-' + (op[0] === '+' ? 'add' : op[0] === '-' ? 'del' : 'same') + '">' + esc(op[0] + ' ' + op[1]) + '</div>');
    });
    if (skipped) html.push('<div class="nb-diff-skip">⋯ ' + t(skipped > 1 ? '%(n)s unchanged lines' : '%(n)s unchanged line', { n: skipped }) + '</div>');
    var changes = ops.filter(function (o) { return o[0] !== ' '; }).length;
    return changes ? '<div class="nb-diff">' + html.join('') + '</div>' : '<p class="nb-muted">' + t('No differences from the page as it is now.') + '</p>';
  }
  // ---- sign: the page as a locked record (app/signatures.py). The
  // person's choice: nothing asks for it.
  PANELS.sign = {
    title: 'Signatures',
    render: function (box) {
      api('/notebook/api/pages/' + page.id + '/signatures').then(function (d) {
        var when = function (iso) { return new Date(iso).toLocaleString(LOC, inLab({ dateStyle: 'medium', timeStyle: 'short' })); };
        var label = { sign: t('Signed'), review: t('Reviewed'), witness: t('Witnessed'), amend: t('Opened to amend') };
        var list = d.entries.length ? '<ol class="nb-sig-list">' + d.entries.map(function (e) {
          return '<li class="nb-sig" data-action="' + esc(e.action) + '"><b>' + esc(label[e.action] || e.action) + '</b> ' + t('by %(name)s', { name: esc(e.name) }) +
            ' <span class="nb-muted">' + esc(when(e.at)) + '</span>' +
            (e.meaning ? '<div class="nb-sig-meaning">“' + esc(e.meaning) + '”</div>' : '') +
            (e.reason ? '<div class="nb-sig-meaning">' + t('Reason: %(reason)s', { reason: esc(e.reason) }) + '</div>' : '') +
            (e.sha256 && e.action !== 'amend' ? '<div class="nb-muted nb-sig-hash" title="' + t('SHA-256 of the title and text signed') + '">' +
              (e.matches ? t('✓ The page reads exactly as signed') : t('The page has changed since (amended)')) + ' · ' + esc(e.sha256.slice(0, 12)) + '…</div>' : '') +
            '</li>';
        }).join('') + '</ol>' : '';
        var identity = d.needs_password
          ? '<label>' + t('Your password') + ' <input type="password" id="nb-sig-secret" autocomplete="current-password" required></label>'
          : '<label>' + t('Type your user name (%(name)s)', { name: esc(d.me) }) + ' <input id="nb-sig-secret" autocomplete="off" required></label>';
        var mySign = d.entries.filter(function (e) { return e.action === 'sign'; }).pop();
        var forms = '';
        if (!d.locked && d.can_sign) {
          forms += '<form class="nb-sig-form" data-sig="sign"><p class="nb-muted">' + t('Signing makes this page the record of your work: its exact text is fingerprinted and kept, any live experiment in it is frozen as it is now, and the page is locked. Later changes are amendments with a reason. Whether to sign is your choice.') + '</p>' +
            '<label>' + t('What you are saying') + ' <select id="nb-sig-meaning"><option value="sign">' + esc(d.meanings.sign) + '</option>' +
            '<option value="review">' + esc(d.meanings.review) + '</option></select></label>' + identity +
            '<button type="submit" class="btn btn-primary">' + t('Sign and lock') + '</button></form>';
        }
        if (d.locked && !(mySign && mySign.username === d.me)) {
          forms += '<form class="nb-sig-form" data-sig="witness"><p class="nb-muted">' + t('Witness it: say you have read and understood it.') + '</p>' + identity +
            '<button type="submit" class="btn">' + t('Witness') + '</button></form>';
        }
        if (d.locked && d.can_amend) {
          forms += '<form class="nb-sig-form" data-sig="amend"><p class="nb-muted">' + t('Amend: open it again to change it. The reason stays in the record, and so do the signatures; sign it again when it is done.') + '</p>' +
            '<label>' + t('Why it is being amended') + ' <textarea id="nb-sig-reason" rows="2" required></textarea></label>' + identity +
            '<button type="submit" class="btn">' + t('Open to amend') + '</button></form>';
        }
        box.innerHTML = (d.locked ? '<p class="nb-sig-state is-locked">' + t('Signed and locked.') + '</p>' : (d.signed ? '<p class="nb-sig-state">' + t('Being amended: sign it again to lock it.') + '</p>' : '<p class="nb-sig-state">' + t('Not signed.') + '</p>')) +
          list + forms + '<p class="nb-error" id="nb-sig-error" hidden></p>';
        box.querySelectorAll('.nb-sig-form').forEach(function (form) {
          form.addEventListener('submit', function (ev) {
            ev.preventDefault();
            var action = form.dataset.sig;
            var secret = form.querySelector('#nb-sig-secret').value;
            var body = d.needs_password ? { password: secret } : { confirm: secret };
            body.action = action === 'sign' ? form.querySelector('#nb-sig-meaning').value : action;
            if (action === 'amend') body.reason = form.querySelector('#nb-sig-reason').value;
            var go = action === 'sign' ? flushed() : Promise.resolve();
            go.then(function () { return api('/notebook/api/pages/' + page.id + '/sign', { body: body }); })
              .then(function () { window.location.reload(); })
              .catch(function (err) {
                var el = box.querySelector('#nb-sig-error');
                el.textContent = err.message;
                el.hidden = false;
              });
          });
        });
      }).catch(function (err) { box.innerHTML = '<p class="nb-error">' + esc(err.message) + '</p>'; });
    },
  };

  PANELS.history = {
    title: 'Version history',
    render: function (box) {
      api('/notebook/api/pages/' + page.id + '/versions').then(function (d) {
        if (!d.versions.length) { box.innerHTML = '<p class="nb-muted">' + t('No versions yet: they are kept as the page is edited.') + '</p>'; return; }
        var lastDay = '';
        box.innerHTML = (canEdit ? '<div class="nb-field-row"><button type="button" class="btn" id="nb-save-version">' + icon('success') + ' ' + t('Save a named version') + '</button></div>' : '') +
          '<ul class="nb-list nb-versions">' + d.versions.map(function (v, i) {
            var day = (when(v.saved_at) || new Date()).toDateString();
            var head = day !== lastDay ? '<li class="nb-list-day">' + esc((when(v.saved_at) || new Date()).toLocaleDateString(LOC, inLab({ weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }))) + '</li>' : '';
            lastDay = day;
            var label = v.kind === 'release' ? '<b class="nb-pill">v' + v.number + '</b> ' : v.kind === 'manual' ? '<b class="nb-pill is-quiet">' + t('saved') + '</b> ' : v.kind === 'restore' ? '<b class="nb-pill is-quiet">' + t('restored') + '</b> ' : '';
            return head + '<li class="nb-list-row nb-version" data-version="' + v.id + '"><span class="nb-avatar" style="background:' + colorFor(v.saved_by) + '">' + esc(initials(v.saved_by_name)) + '</span>' +
              '<span class="nb-grow"><span>' + label + esc(v.label ? t(v.label) : (i === 0 ? tc('notebook', 'Latest') : t('Edits'))) + '</span><small>' + esc(v.saved_by_name) + ' · ' + esc((when(v.saved_at) || new Date()).toLocaleTimeString(LOC, inLab({ hour: '2-digit', minute: '2-digit' }))) + '</small></span></li>';
          }).join('') + '</ul><div id="nb-version-view"></div>';
        var save = $('#nb-save-version', box);
        if (save) save.addEventListener('click', function () { saveNamedVersion().then(function () { PANELS.history.render(box); }); });
        box.onclick = function (e) {
          var row = e.target.closest('[data-version]');
          if (row) showVersion(Number(row.dataset.version), box);
          var restore = e.target.closest('[data-restore]');
          if (restore) BioDialog.confirm(t('Put the page back as it was in this version? What is there now stays in the history.'), { okLabel: t('Restore') }).then(function (ok) {
            if (!ok) return;
            flushed().then(function () { return api('/notebook/api/versions/' + restore.dataset.restore + '/restore', { body: {} }); })
              .then(function () { window.location.reload(); })
              .catch(function (err) { toast(esc(err.message), true); });
          });
        };
      }).catch(function (e) { box.innerHTML = '<p class="nb-warn">' + esc(e.message) + '</p>'; });
    },
  };
  function showVersion(id, box) {
    var view = $('#nb-version-view', box);
    $$('.nb-version', box).forEach(function (r) { r.classList.toggle('is-active', Number(r.dataset.version) === id); });
    view.innerHTML = '<p class="nb-muted">' + t('Loading…') + '</p>';
    api('/notebook/api/versions/' + id).then(function (d) {
      var current = nb ? nb.getMarkdown() : ($('#initial-body') || {}).value;
      view.innerHTML = '<div class="nb-version-head"><b>' + esc(d.version.title) + '</b> · ' + esc(timeText(d.version.saved_at)) +
        (canEdit ? ' <button type="button" class="btn btn-primary" data-restore="' + id + '">' + t('Restore this version') + '</button>' : '') + '</div>' +
        '<div class="nb-seg"><button type="button" class="is-on" data-vmode="diff">' + t('Changes since') + '</button><button type="button" data-vmode="text">' + t('Text') + '</button></div>' +
        '<div class="nb-version-body">' + diffHtml(lineDiff(d.version.body, current)) + '</div>';
      view.onclick = function (e) {
        var m = e.target.closest('[data-vmode]');
        if (!m) return;
        $$('[data-vmode]', view).forEach(function (b) { b.classList.toggle('is-on', b === m); });
        $('.nb-version-body', view).innerHTML = m.dataset.vmode === 'diff' ? diffHtml(lineDiff(d.version.body, current)) : '<pre class="nb-version-text">' + esc(d.version.body) + '</pre>';
      };
    });
  }
  function saveNamedVersion() {
    return BioDialog.prompt(t('Name this version'), '', { placeholder: t('e.g. before re-analysis'), okLabel: t('Save version') }).then(function (label) {
      if (label === null) return undefined;
      return flushed()
        .then(function () { return api('/notebook/api/pages/' + page.id + '/versions', { body: { label: label } }); })
        .then(function () { toast(t('Version saved.')); });
    });
  }

  // ---- search
  PANELS.search = {
    title: 'Search',
    render: function (box, opts) {
      var kinds = DATA.kinds || {};
      var statuses = DATA.statuses || {};
      box.innerHTML = '<form class="nb-search" id="nb-search">' +
        '<input type="search" name="q" placeholder="' + t('Words in the title or text') + '" value="' + esc(opts.q || '') + '" aria-label="' + t('Search') + '">' +
        '<div class="nb-field-row">' +
        '<select name="kind" aria-label="' + t('Kind') + '"><option value="">' + t('Any kind') + '</option>' + Object.keys(kinds).map(function (k) { return '<option value="' + k + '">' + esc(tc('notebook', kinds[k])) + '</option>'; }).join('') + '</select>' +
        '<select name="status" aria-label="' + t('Status') + '"><option value="">' + t('Any status') + '</option>' + Object.keys(statuses).filter(Boolean).map(function (k) { return '<option value="' + k + '">' + esc(t(statuses[k])) + '</option>'; }).join('') + '</select>' +
        '<select name="whose" aria-label="' + t('Whose') + '"><option value="all">' + t('Mine & shared') + '</option><option value="mine">' + t('Mine') + '</option><option value="shared">' + t('Shared with me') + '</option></select></div>' +
        '<div class="nb-field-row"><select name="tag" aria-label="' + t('Tag') + '"><option value="">' + t('Any tag') + '</option></select>' +
        '<label>' + t('From') + ' <input type="date" name="from"></label><label>' + t('To') + ' <input type="date" name="to"></label></div></form>' +
        '<div id="nb-search-results"></div>';
      var form = $('#nb-search', box);
      api('/notebook/api/tags').then(function (d) {
        form.tag.innerHTML = '<option value="">' + t('Any tag') + '</option>' + d.tags.map(function (t) { return '<option value="' + esc(t.tag) + '"' + (t.tag === opts.tag ? ' selected' : '') + '>#' + esc(t.tag) + ' (' + t.count + ')</option>'; }).join('');
        run();
      });
      var run = debounce(function () {
        var params = new URLSearchParams(new FormData(form));
        var out = $('#nb-search-results', box);
        api('/notebook/api/search?' + params.toString()).then(function (d) {
          out.innerHTML = d.results.length ? '<p class="nb-muted">' + t(d.results.length === 1 ? '%(n)s page' : '%(n)s pages', { n: d.results.length + (d.results.length === 100 ? '+' : '') }) + '</p><ul class="nb-list">' + d.results.map(function (r) {
            return '<li><a class="nb-result" href="' + esc(r.url) + '"><b>' + esc(r.title) + '</b>' +
              '<small>' + esc(tc('notebook', kinds[r.kind] || 'Note')) + (r.status ? ' · ' + esc(t(statuses[r.status])) : '') + ' · ' + esc(r.mine ? t(r.tab) : t('by %(name)s', { name: r.owner_name })) + ' · ' + esc(r.entry_date || timeText(r.updated_at)) + '</small>' +
              (r.snippet ? '<span class="nb-snippet">' + esc(r.snippet) + '</span>' : '') +
              (r.tags.length ? '<span class="nb-result-tags">' + r.tags.map(function (t) { return '#' + esc(t); }).join(' ') + '</span>' : '') + '</a></li>';
          }).join('') + '</ul>' : '<p class="nb-muted">' + t('Nothing found.') + '</p>';
        }).catch(function (e) { out.innerHTML = '<p class="nb-warn">' + esc(e.message) + '</p>'; });
      }, 220);
      form.addEventListener('input', run);
      form.addEventListener('change', run);
      form.addEventListener('submit', function (e) { e.preventDefault(); run(); });
      form.q.focus();
    },
  };

  // ---- meetings
  PANELS.meetings = {
    title: 'Meetings',
    render: function (box) {
      api('/notebook/api/meetings').then(function (d) {
        var weekdays = d.weekdays;
        var card = function (s) {
          var next = s.next;
          return '<article class="nb-series" data-series="' + s.id + '">' +
            '<header><b>' + esc(s.name) + '</b><small>' + esc([s.weekday_name ? t(s.weekday_name + 's') : '', s.time, s.location].filter(Boolean).join(' · ')) + '</small></header>' +
            (next ? '<div class="nb-next"><span class="nb-avatar is-lg" style="background:' + colorFor(next.presenter) + '">' + esc(initials(next.name)) + '</span><div><small>' + t('Next to present') + (next.date ? ' · ' + esc(new Date(next.date + 'T12:00').toLocaleDateString(LOC, { weekday: 'short', day: 'numeric', month: 'short' })) : '') + '</small><b>' + esc(next.name) + '</b></div></div>' : '<p class="nb-muted">' + t('No one in the rotation yet.') + '</p>') +
            (s.upcoming.length > 1 ? '<ol class="nb-rotation">' + s.upcoming.slice(1).map(function (u) { return '<li><span>' + esc(u.name) + '</span><small>' + esc(u.date ? new Date(u.date + 'T12:00').toLocaleDateString(LOC, { day: 'numeric', month: 'short' }) : '') + '</small></li>'; }).join('') + '</ol>' : '') +
            '<div class="nb-field-row">' +
            '<button type="button" class="btn btn-primary" data-note="' + s.id + '">' + icon('note') + ' ' + t('Notes for the next meeting') + '</button>' +
            (s.can_edit ? '<button type="button" class="btn" data-skip="' + s.id + '" title="' + t('Move the rotation on one person') + '">' + t('Skip ›') + '</button><button type="button" class="btn" data-back="' + s.id + '" title="' + t('Move the rotation back one') + '">‹</button>' : '') + '</div>' +
            (s.can_edit ? '<div class="nb-field-row"><button type="button" class="btn-link" data-cal="' + s.id + '">' + t('Put the next meetings on the calendar') + '</button><button type="button" class="btn-link" data-edit="' + s.id + '">' + t('Edit') + '</button></div>' : '') +
            (s.notes.length ? '<details><summary>' + t('Past notes (%(n)s)', { n: s.notes.length }) + '</summary><ul class="nb-list">' + s.notes.map(function (n) { return '<li><a href="' + esc(n.url) + '">' + esc(n.title) + '</a></li>'; }).join('') + '</ul></details>' : '') +
            '</article>';
        };
        box.innerHTML = (d.series.length ? d.series.map(card).join('') : '<p class="nb-muted">' + t('No meetings yet. Set one up and the notebook keeps track of who presents next.') + '</p>') +
          '<button type="button" class="btn" id="nb-series-new">' + icon('plus') + ' ' + t('New meeting series') + '</button><div id="nb-series-form"></div>';
        var byId = {};
        d.series.forEach(function (s) { byId[s.id] = s; });
        $('#nb-series-new', box).addEventListener('click', function () { seriesForm(box, null, d.people, weekdays); });
        box.onclick = function (e) {
          var b = e.target.closest('button');
          if (!b) return;
          if (b.dataset.note) api('/notebook/api/meetings/' + b.dataset.note + '/note', { body: {} }).then(function (r) { go(r.url); });
          if (b.dataset.skip) api('/notebook/api/meetings/' + b.dataset.skip + '/advance', { body: { step: 1 } }).then(function () { PANELS.meetings.render(box); });
          if (b.dataset.back) api('/notebook/api/meetings/' + b.dataset.back + '/advance', { body: { step: -1 } }).then(function () { PANELS.meetings.render(box); });
          if (b.dataset.edit) seriesForm(box, byId[b.dataset.edit], d.people, weekdays);
          if (b.dataset.cal) {
            BioDialog.prompt(t('How many of the coming meetings?'), String(Math.max(4, byId[b.dataset.cal].members.length)), { okLabel: t('Add to the calendar') }).then(function (n) {
              if (!n) return;
              api('/notebook/api/meetings/' + b.dataset.cal + '/calendar', { body: { count: Number(n) } })
                .then(function (r) { toast(r.made ? t(r.made > 1 ? '%(n)s meetings added to the calendar, each naming its presenter.' : '%(n)s meeting added to the calendar, each naming its presenter.', { n: r.made }) : t('They are on the calendar already.')); })
                .catch(function (err) { toast(esc(err.message), true); });
            });
          }
        };
      }).catch(function (e) { box.innerHTML = '<p class="nb-warn">' + esc(e.message) + '</p>'; });
    },
  };
  function seriesForm(box, s, list, weekdays) {
    var holder = $('#nb-series-form', box);
    var members = s ? s.members.map(function (m) { return m.username; }) : [me.username];
    var fields = { name: s ? s.name : t('Lab meeting'), weekday: s && s.weekday !== null ? String(s.weekday) : '', time: s ? s.time : '', location: s ? s.location : '' };
    function draw() {
      // Keep what has been typed when the rotation list is redrawn.
      var old = $('form', holder);
      if (old) ['name', 'weekday', 'time', 'location'].forEach(function (k) { fields[k] = old.elements[k].value; });
      var names = {};
      list.forEach(function (p) { names[p.username] = p.name; });
      holder.innerHTML = '<form class="nb-series-form"><h4>' + (s ? t('Edit %(name)s', { name: esc(s.name) }) : t('New meeting series')) + '</h4>' +
        '<label>' + t('Name') + ' <input name="name" required value="' + esc(fields.name) + '"></label>' +
        '<div class="nb-field-row"><label>' + t('Every') + ' <select name="weekday"><option value="">' + t('— day —') + '</option>' + weekdays.map(function (w, i) { return '<option value="' + i + '"' + (fields.weekday === String(i) ? ' selected' : '') + '>' + esc(t(w)) + '</option>'; }).join('') + '</select></label>' +
        '<label>' + t('at') + ' <input type="time" name="time" value="' + esc(fields.time) + '"></label></div>' +
        '<label>' + t('Where') + ' <input name="location" value="' + esc(fields.location) + '" placeholder="' + t('Room, or a video link') + '"></label>' +
        '<div class="nb-label">' + t('Presenting order') + '</div><ol class="nb-order">' + members.map(function (u, i) {
          return '<li><span class="nb-grow">' + esc(names[u] || u) + '</span><button type="button" class="nb-icon-btn" data-up="' + i + '" aria-label="' + t('Earlier') + '">↑</button><button type="button" class="nb-icon-btn" data-down="' + i + '" aria-label="' + t('Later') + '">↓</button><button type="button" class="nb-icon-btn" data-out="' + i + '" aria-label="' + t('Remove') + '">×</button></li>';
        }).join('') + '</ol>' +
        '<div class="nb-field-row"><select id="nb-series-add"><option value="">' + t('Add someone…') + '</option>' + list.filter(function (p) { return members.indexOf(p.username) < 0; }).map(function (p) { return '<option value="' + esc(p.username) + '">' + esc(p.name) + '</option>'; }).join('') + '</select>' +
        '<button type="button" class="btn-link" id="nb-series-all">' + t('Add everyone') + '</button></div>' +
        '<div class="nb-field-row"><button class="btn btn-primary" type="submit">' + t('Save') + '</button><button type="button" class="btn" data-cancel>' + t('Cancel') + '</button>' +
        (s && (s.owner === me.username) ? '<span class="nb-grow"></span><button type="button" class="btn-link is-danger" data-delete-series>' + t('Delete') + '</button>' : '') + '</div></form>';
      var form = $('form', holder);
      form.scrollIntoView({ block: 'nearest' });
      $('#nb-series-add', holder).addEventListener('change', function (e) { if (e.target.value) { members.push(e.target.value); draw(); } });
      $('#nb-series-all', holder).addEventListener('click', function () {
        list.forEach(function (p) { if (!p.guest && members.indexOf(p.username) < 0) members.push(p.username); });
        draw();
      });
      form.onclick = function (e) {
        var b = e.target.closest('button');
        if (!b) return;
        var i;
        if (b.dataset.up !== undefined) { i = Number(b.dataset.up); if (i > 0) { members.splice(i - 1, 0, members.splice(i, 1)[0]); draw(); } }
        if (b.dataset.down !== undefined) { i = Number(b.dataset.down); if (i < members.length - 1) { members.splice(i + 1, 0, members.splice(i, 1)[0]); draw(); } }
        if (b.dataset.out !== undefined) { members.splice(Number(b.dataset.out), 1); draw(); }
        if (b.dataset.cancel !== undefined) holder.innerHTML = '';
        if (b.dataset.deleteSeries !== undefined) BioDialog.confirm(t('Delete %(name)s? Its notes stay.', { name: s.name }), { danger: true }).then(function (ok) {
          if (ok) api('/notebook/api/meetings/' + s.id + '/delete', { body: {} }).then(function () { PANELS.meetings.render(box); });
        });
      };
      form.onsubmit = function (e) {
        e.preventDefault();
        var el = form.elements;
        var body = { name: el.name.value, weekday: el.weekday.value, time: el.time.value, location: el.location.value, members: members };
        api(s ? '/notebook/api/meetings/' + s.id : '/notebook/api/meetings', { body: body }).then(function () { PANELS.meetings.render(box); })
          .catch(function (err) { toast(esc(err.message), true); });
      };
    }
    draw();
  }

  // ---- recipes
  PANELS.recipes = {
    title: 'Recipe library',
    render: function (box) {
      api('/notebook/api/recipes').then(function (d) {
        var insertable = !!(nb && canEdit);
        var row = function (r, mine) {
          var comps = (r.data.components || []).map(function (c) { return esc(c.name); }).join(', ');
          return '<li class="nb-list-row nb-recipe-row"><span class="nb-grow"><b>' + esc(r.name) + '</b><small>' + esc(r.data.volume || '') + ' ' + esc(r.data.volumeUnit || '') + ' · ' + comps + '</small></span>' +
            (insertable ? '<button type="button" class="btn" data-insert="' + esc(r.id) + '">' + tc('notebook', 'Insert') + '</button>' : '') +
            (mine && r.can_edit ? '<button type="button" class="nb-icon-btn" data-del-recipe="' + r.id + '" aria-label="' + t('Delete') + '">' + icon('trash') + '</button>' : '') + '</li>';
        };
        box.innerHTML = '<p class="nb-muted">' + (insertable ? t('Insert one into this page, then change the volume and every amount follows.') : t('Open a page you can edit to insert a recipe.')) + ' ' + t('Save your own from any recipe block.') + '</p>' +
          (d.recipes.length ? '<div class="notebook-section-label">' + t('Saved by the lab') + '</div><ul class="nb-list">' + d.recipes.map(function (r) { return row(r, true); }).join('') + '</ul>' : '') +
          '<div class="notebook-section-label">' + t('Common recipes') + '</div><ul class="nb-list">' + d.presets.map(function (r) { return row(r, false); }).join('') + '</ul>';
        var all = d.recipes.concat(d.presets);
        box.onclick = function (e) {
          var ins = e.target.closest('[data-insert]');
          var del = e.target.closest('[data-del-recipe]');
          if (ins && nb) {
            var r = all.filter(function (x) { return String(x.id) === ins.dataset.insert; })[0];
            var value = JSON.parse(JSON.stringify(r.data));
            value.name = value.name || r.name;
            nb.editor.chain().focus().insertLabBlock('recipe', value).run();
            toast(t('Inserted %(name)s.', { name: esc(r.name) }));
          }
          if (del) BioDialog.confirm(t('Delete this recipe from the library?'), { danger: true }).then(function (ok) {
            if (ok) api('/notebook/api/recipes/' + del.dataset.delRecipe + '/delete', { body: {} }).then(function () { PANELS.recipes.render(box); });
          });
        };
      }).catch(function (e) { box.innerHTML = '<p class="nb-warn">' + esc(e.message) + '</p>'; });
    },
  };

  // ---- protocols: the lab's protocol pages and the common ones built in
  PANELS.protocols = {
    title: 'Protocols',
    render: function (box) {
      api('/notebook/api/protocols').then(function (d) {
        var insertable = !!(nb && canEdit);
        var labRow = function (p) {
          var meta = [p.owner_name, p.version ? 'v' + p.version : t('not numbered yet')].filter(Boolean).join(' · ');
          return '<li class="nb-list-row" data-find="' + esc((p.title + ' ' + (p.tags || []).join(' ')).toLowerCase()) + '">' +
            '<span class="nb-grow"><b>' + esc(p.title) + '</b><small>' + esc(meta) + '</small></span>' +
            (insertable ? '<button type="button" class="btn" data-insert-page="' + p.id + '">' + tc('notebook', 'Insert') + '</button>' : '') +
            '<a class="btn btn-ghost" href="/notebook?page=' + p.id + '">' + t('Open') + '</a></li>';
        };
        var presetRow = function (p) {
          return '<li class="nb-list-row" data-find="' + esc((p.title + ' ' + p.category + ' ' + p.summary).toLowerCase()) + '">' +
            '<span class="nb-grow"><b>' + esc(p.title) + '</b><small>' + esc(p.summary) + '</small></span>' +
            (insertable ? '<button type="button" class="btn" data-insert-preset="' + esc(p.key) + '">' + tc('notebook', 'Insert') + '</button>' : '') +
            '<button type="button" class="btn btn-ghost" data-copy-preset="' + esc(p.key) + '" title="' + t('Save a copy as your own protocol to change') + '">' + t('Copy') + '</button></li>';
        };
        var groups = {};
        d.presets.forEach(function (p) { (groups[p.category] = groups[p.category] || []).push(p); });
        box.innerHTML =
          '<p class="nb-muted">' + (insertable
            ? t('Insert one into this page: its steps come in as a checklist you can run, with a link back to the protocol.')
            : t('Open a page you can edit to insert a protocol into it.')) + '</p>' +
          '<div class="nb-panel-actions"><input type="search" class="nb-panel-search" placeholder="' + t('Find a protocol…') + '" aria-label="' + t('Find a protocol') + '">' +
          '<button type="button" class="btn btn-primary" data-new-protocol>' + icon('plus') + ' ' + t('New protocol') + '</button></div>' +
          '<div class="notebook-section-label">' + t('Lab protocols') + '</div>' +
          (d.protocols.length ? '<ul class="nb-list">' + d.protocols.map(labRow).join('') + '</ul>'
            : '<p class="nb-muted">' + t('None yet. Write one with New protocol, or copy a common one below and make it yours.') + '</p>') +
          Object.keys(groups).map(function (cat) {
            return '<div class="notebook-section-label">' + esc(t(cat)) + '</div><ul class="nb-list">' + groups[cat].map(presetRow).join('') + '</ul>';
          }).join('');
        var search = box.querySelector('.nb-panel-search');
        search.addEventListener('input', function () {
          var q = search.value.trim().toLowerCase();
          box.querySelectorAll('[data-find]').forEach(function (row) { row.hidden = !!q && row.dataset.find.indexOf(q) < 0; });
        });
        box.onclick = function (e) {
          var target = e.target.closest('[data-insert-page],[data-insert-preset],[data-copy-preset],[data-new-protocol]');
          if (!target) return;
          if (target.hasAttribute('data-new-protocol') || target.dataset.copyPreset) {
            target.disabled = true;
            api('/notebook/api/protocols/new', { body: target.dataset.copyPreset ? { preset: target.dataset.copyPreset } : {} })
              .then(function (r) { window.location.href = r.url; })
              .catch(function (err) { target.disabled = false; toast(esc(err.message), true); });
            return;
          }
          if (!nb) return;
          var query = target.dataset.insertPage ? 'page=' + target.dataset.insertPage : 'preset=' + encodeURIComponent(target.dataset.insertPreset);
          api('/notebook/api/protocols/text?' + query).then(function (r) {
            nb.editor.chain().focus().insertContent(r.markdown).run();
            toast(t('Inserted %(name)s.', { name: esc(r.title) }));
          }).catch(function (err) { toast(esc(err.message), true); });
        };
      }).catch(function (e) { box.innerHTML = '<p class="nb-warn">' + esc(e.message) + '</p>'; });
    },
  };
  // The editor's /protocol command asks for this panel.
  window.addEventListener('nb:open-panel', function (e) { openPanel((e.detail && e.detail.name) || ''); });

  // ---- experiments that followed a protocol
  PANELS.experiments = {
    title: 'Experiments from this protocol',
    render: function (box) {
      api('/notebook/api/pages/' + page.id + '/experiments').then(function (d) {
        box.innerHTML = d.experiments.length ? '<ul class="nb-list">' + d.experiments.map(function (x) {
          return '<li><a class="nb-result" href="/notebook?page=' + x.id + '"><b>' + esc(x.title) + '</b><small>' + esc(x.owner_name) + (x.version ? ' · v' + x.version : '') + ' · ' + esc(timeText(x.started_at)) + (x.status ? ' · ' + esc(t((DATA.statuses || {})[x.status] || x.status)) : '') + '</small></a></li>';
        }).join('') + '</ul>' : '<p class="nb-muted">' + t('None yet. “Start an experiment from it” makes one with these steps as a checklist.') + '</p>';
      });
    },
  };

  if (!page) return;

  // ------------------------------------------------------------ the page

  var saveState = $('#page-saved-state');
  function setSaved(text, isError) {
    if (!saveState) return;
    saveState.textContent = text;
    saveState.classList.toggle('is-error', !!isError);
  }

  function post(fields, headers) {
    var body = new URLSearchParams();
    Object.keys(fields).forEach(function (k) { body.set(k, fields[k]); });
    var h = { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Autosave': '1' };
    Object.keys(headers || {}).forEach(function (k) { h[k] = headers[k]; });
    return fetch('/notebook/pages/' + page.id + '/update', { method: 'POST', headers: h, body: body.toString(), keepalive: true });
  }

  function saveField(field, value) {
    if (!canEdit) return;
    var f = {};
    f[field] = value;
    return post(f).then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok) setSaved(t('Saved %(time)s', { time: new Date().toLocaleTimeString(LOC, inLab({ hour: '2-digit', minute: '2-digit' })) }));
      else setSaved(t('Not saved'), true);
    }).catch(function () { setSaved(t('Offline — not saved yet'), true); });
  }

  // Wait for the editor's latest text to be saved before asking the server
  // to act on it (a version, a protocol's steps, action items).
  function flushed() {
    return nb ? Promise.resolve(nb.flush()) : Promise.resolve();
  }

  function saveBody(markdown, meta) {
    var headers = meta && meta.gen !== undefined ? { 'X-Collab-Gen': String(meta.gen) } : {};
    var fields = { body: markdown };
    if (meta && meta.state) fields.collab_state = meta.state;     // which edits this text holds
    return post(fields, headers).then(function (r) {
      if (r.status === 409) { restartEditor(); return; }
      return r.json().then(function (d) {
        if (d.ok) setSaved(t('Saved %(time)s', { time: new Date().toLocaleTimeString(LOC, inLab({ hour: '2-digit', minute: '2-digit' })) }));
        else setSaved(t('Not saved'), true);
      });
    }).catch(function () { setSaved(t('Offline — will save with the next change'), true); });
  }

  // Title, date, topic.
  var title = $('#page-title');
  // A title typed here and not yet saved is the page's title: the sync
  // poll (onMeta) brings the server's, which is older until the save lands,
  // and must not put it back (a title typed just before tabbing into the
  // page or opening Markdown was lost that way).
  var titleUnsaved = null;
  function saveTitleNow() {
    if (titleUnsaved === null) return Promise.resolve();
    var value = title.value;
    $$('.notebook-page-item[data-page-id="' + page.id + '"] .page-title-label').forEach(function (el) { el.textContent = value || t('Untitled page'); });
    document.title = (value || t('Untitled page')) + document.title.replace(/^[^·|—-]*/, ' ');
    if (window.BiomanagerTabs && window.BiomanagerTabs.retitle) window.BiomanagerTabs.retitle(value || t('Untitled page'));
    return saveField('title', value).then(function () {
      page.title = value;
      if (titleUnsaved === value) titleUnsaved = null;
    });
  }
  if (title && canEdit) {
    var saveTitle = debounce(saveTitleNow, 400);
    title.addEventListener('input', function () { titleUnsaved = title.value; saveTitle(); });
    title.addEventListener('change', saveTitleNow);     // leaving the box saves at once
    title.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); saveTitleNow(); if (nb) nb.editor.commands.focus('start'); }
    });
  }
  // A new page: the title is where you start. "Untitled page" is only the
  // box's placeholder (the page is saved under it), so typing starts clean.
  // A page still named as its starter named it ("New experiment") starts
  // there too, the name selected so typing replaces it.
  var starterTitle = title && (DATA.starterTitles || []).indexOf(title.value) >= 0 && title.value !== '';
  if (title && canEdit && (!title.value || starterTitle)) {
    var focusTitle = function () {
      var here = document.activeElement;
      var elsewhere = here && here !== document.body && here !== title && !here.closest('.ProseMirror');
      if (!document.querySelector('dialog[open]') && !elsewhere) {
        title.focus();
        if (starterTitle && title.value && titleUnsaved === null) title.select();
      }
    };
    setTimeout(focusTitle, 0);
    setTimeout(focusTitle, 400);     // after the editor has mounted
  }
  var dateInput = $('#page-entry-date');
  if (dateInput && canEdit) dateInput.addEventListener('change', function () { saveField('entry_date', dateInput.value); });
  var topic = $('#page-topic-select');
  if (topic) topic.addEventListener('change', function () {
    var f = new FormData();
    f.append('tab_id', topic.value);
    fetch('/notebook/pages/' + page.id + '/move', { method: 'POST', body: f }).then(function (r) { return r.json(); })
      .then(function (d) { if (d.ok) go('/notebook?page=' + d.page_id); });
  });

  // Kind, status, start / finish.
  function meta(body) {
    return api('/notebook/api/pages/' + page.id + '/meta', { body: body }).then(function (d) {
      page = Object.assign(page, d.page);
      drawStamps();
      return d;
    });
  }
  var kindSel = $('#nb-kind');
  if (kindSel) kindSel.addEventListener('change', function () { meta({ kind: kindSel.value }).then(function () { window.location.reload(); }); });
  var statusSel = $('#nb-status');
  if (statusSel) statusSel.addEventListener('change', function () {
    meta({ status: statusSel.value }).then(function () { statusSel.parentNode.dataset.status = statusSel.value; });
  });
  $$('[data-action]').forEach(function (b) {
    b.addEventListener('click', function () {
      var action = b.dataset.action;
      if (action === 'start') {
        meta({ action: 'start' }).then(function () { window.location.reload(); });
      } else if (action === 'finish') {
        meta({ action: 'finish', outcome: b.dataset.outcome }).then(function () { window.location.reload(); });
      } else if (action === 'start-experiment') {
        BioDialog.prompt(t('Name the experiment'), page.title + ' — ' + new Date().toLocaleDateString(LOC, inLab({ day: 'numeric', month: 'short', year: 'numeric' })), { okLabel: t('Start') }).then(function (name) {
          if (name === null) return;
          flushed().then(function () {
            return api('/notebook/api/pages/' + page.id + '/start-experiment', { body: { title: name } });
          }).then(function (d) { go(d.url); }).catch(function (e) { toast(esc(e.message), true); });
        });
      } else if (action === 'release') {
        flushed().then(function () {
          return api('/notebook/api/pages/' + page.id + '/versions', { body: { release: true } });
        }).then(function (d) {
          toast(t('Saved as v%(number)s. Experiments started from now on follow this version.', { number: d.number }));
          setTimeout(function () { window.location.reload(); }, 900);
        });
      } else if (action === 'action-items') {
        flushed().then(function () {
          return api('/notebook/api/pages/' + page.id + '/action-items', { body: {} });
        }).then(function (d) {
            if (!d.made.length) {
              toast(d.skipped ? t('Those action items were sent already.') : t('No open tasks name anyone. Write them like “- [ ] @name order primers, due 2026-10-01”.'));
              return;
            }
            toast(t(d.made.length > 1 ? 'Sent %(n)s to-dos: %(names)s.' : 'Sent %(n)s to-do: %(names)s.', { n: d.made.length, names: d.made.map(function (m) { return esc(m.name); }).join(', ') }));
        }).catch(function (e) { toast(esc(e.message), true); });
      }
    });
  });

  function drawStamps() {
    var box = $('#nb-stamps');
    if (!box) return;
    var parts = [];
    if (page.started_at) parts.push('<span title="' + esc(when(page.started_at).toLocaleString(LOC, inLab())) + '">' + icon('clock') + ' ' + t('started %(time)s', { time: esc(timeText(page.started_at)) }) + '</span>');
    if (page.finished_at) parts.push('<span title="' + esc(when(page.finished_at).toLocaleString(LOC, inLab())) + '">' + t('finished %(time)s', { time: esc(timeText(page.finished_at)) }) + '</span>');
    if (page.edited_by_name && page.edited_by !== me.username) parts.push('<span>' + t('last edited by %(name)s', { name: esc(page.edited_by_name) }) + '</span>');
    box.innerHTML = parts.length ? '<span class="meta-sep">·</span>' + parts.join(' · ') : '';
    var started = $('#nb-started');
    if (started && page.started_at) started.textContent = when(page.started_at).toLocaleString(LOC, inLab({ weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }));
  }
  drawStamps();

  // Tags.
  var tagsBox = $('#nb-tags');
  function drawTags() {
    if (!tagsBox) return;
    tagsBox.innerHTML = (page.tags || []).map(function (tag) {
      return '<span class="nb-tag is-chip">#' + esc(tag) + (canEdit ? '<button type="button" data-untag="' + esc(tag) + '" aria-label="' + t('Remove tag %(tag)s', { tag: esc(tag) }) + '">×</button>' : '') + '</span>';
    }).join('') + (canEdit ? '<input class="nb-tag-input" placeholder="' + ((page.tags || []).length ? t('+ tag') : t('Add tags…')) + '" aria-label="' + t('Add a tag') + '" list="nb-tag-list"><datalist id="nb-tag-list"></datalist>' : (page.tags || []).length ? '' : '<span class="nb-muted">' + t('no tags') + '</span>');
    var input = $('.nb-tag-input', tagsBox);
    if (!input) return;
    input.addEventListener('focus', function () {
      api('/notebook/api/tags').then(function (d) {
        $('#nb-tag-list', tagsBox).innerHTML = d.tags.map(function (t) { return '<option value="' + esc(t.tag) + '">'; }).join('');
      });
    }, { once: true });
    input.addEventListener('keydown', function (e) {
      if ((e.key === 'Enter' || e.key === ',') && input.value.trim()) {
        e.preventDefault();
        var next = (page.tags || []).concat(input.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean));
        meta({ tags: next }).then(function () { drawTags(); $('.nb-tag-input', tagsBox).focus(); });
      } else if (e.key === 'Backspace' && !input.value && (page.tags || []).length) {
        meta({ tags: page.tags.slice(0, -1) }).then(drawTags);
      }
    });
  }
  if (tagsBox) {
    tagsBox.addEventListener('click', function (e) {
      var b = e.target.closest('[data-untag]');
      if (b) meta({ tags: page.tags.filter(function (t) { return t !== b.dataset.untag; }) }).then(drawTags);
    });
    drawTags();
  }

  // More menu.
  var moreBtn = $('#nb-more-btn');
  var moreMenu = $('#nb-more-menu');
  if (moreBtn) {
    moreBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      moreMenu.hidden = !moreMenu.hidden;
      moreBtn.setAttribute('aria-expanded', String(!moreMenu.hidden));
    });
    document.addEventListener('click', function (e) { if (!moreMenu.hidden && !moreMenu.contains(e.target)) moreMenu.hidden = true; });
    moreMenu.addEventListener('click', function (e) {
      var b = e.target.closest('[data-more]');
      if (!b) return;
      moreMenu.hidden = true;
      var what = b.dataset.more;
      if (what === 'print') window.print();
      if (what === 'source') openSource();
      if (what === 'version') saveNamedVersion();
      if (what === 'template') {
        BioDialog.prompt(t('Save this page as a template named'), page.title || t('Untitled template'), {
          okLabel: t('Save template'),
          checks: [
            { name: 'structure_only', label: t('Structure only'), checked: true,
              hint: t('Keeps the headings, steps and table headers; leaves out the results, ticks, readings and pictures.') },
            { name: 'lab', label: t('Share it with the lab'), hint: t('Everyone can start a page from it.') },
          ],
        }).then(function (answer) {
          var name = answer && answer.value;
          if (!name) return;
          var f = new FormData();
          f.append('title', name);
          f.append('from_page_id', page.id);
          if (answer.checks.structure_only) f.append('structure_only', '1');
          if (answer.checks.lab) f.append('lab', '1');
          flushed().then(function () { return saveTemplate(f); })
            .then(function (d) { if (d && d.ok) toast(t('Saved as the template “%(name)s”.', { name: esc(name) })); });
        });
      }
      if (what === 'delete') {
        BioDialog.confirm(t('Delete “%(title)s”? Its history and comments go with it.', { title: page.title || t('Untitled page') }), { danger: true, okLabel: t('Delete page') }).then(function (ok) {
          if (!ok) return;
          fetch('/notebook/pages/' + page.id + '/delete', { method: 'POST', headers: { 'X-Requested-With': 'fetch' } })
            .then(function (r) { return r.json(); }).then(function (d) { go('/notebook?tab=' + d.tab_id); });
        });
      }
    });
  }

  // Markdown source.
  function openSource() {
    var md = nb ? nb.getMarkdown() : $('#initial-body').value;
    var overlay = document.createElement('div');
    overlay.className = 'notebook-modal';
    overlay.innerHTML = '<div class="notebook-modal-backdrop" data-x></div><div class="notebook-modal-card nb-source-card" role="dialog" aria-label="Markdown">' +
      '<header class="notebook-modal-head"><h3>Markdown</h3><button type="button" class="modal-close" data-x aria-label="' + t('Close') + '">' + icon('close') + '</button></header>' +
      '<div class="notebook-modal-body"><p class="nb-muted">' + t('The page as Markdown: sheets, recipes and diagrams are the fenced blocks.') + ' ' + (canEdit ? t('Edit and apply, or paste Markdown from elsewhere.') : '') + '</p>' +
      '<textarea class="nb-source" spellcheck="false"' + (canEdit ? '' : ' readonly') + '></textarea>' +
      '<div class="nb-field-row"><button type="button" class="btn" data-copy>' + t('Copy') + '</button><span class="nb-grow"></span>' + (canEdit ? '<button type="button" class="btn btn-primary" data-apply>' + t('Apply') + '</button>' : '') + '</div></div></div>';
    document.body.appendChild(overlay);
    var ta = $('textarea', overlay);
    ta.value = md;
    ta.focus();
    overlay.addEventListener('click', function (e) {
      if (e.target.closest('[data-x]')) overlay.remove();
      if (e.target.closest('[data-copy]')) { ta.select(); try { navigator.clipboard.writeText(ta.value); } catch (_e) { document.execCommand('copy'); } toast(t('Copied.')); }
      if (e.target.closest('[data-apply]')) {
        if (nb) nb.setContent(ta.value);
        else { $('.notebook-fallback').value = ta.value; saveBody(ta.value); }
        overlay.remove();
      }
    });
  }

  // Presence.
  function drawPeers(peers) {
    var box = $('#nb-presence');
    if (!box) return;
    // Your own other tabs are not company.
    peers = peers.filter(function (p) { return p.username !== me.username; });
    box.innerHTML = peers.map(function (p) {
      return '<span class="nb-avatar" style="background:' + colorFor(p.username) + '" title="' + esc(t('%(name)s is here', { name: p.name })) + '">' + esc(initials(p.name)) + '</span>';
    }).join('');
    box.title = peers.length ? t(peers.length > 1 ? '%(names)s are on this page' : '%(names)s is on this page', { names: peers.map(function (p) { return p.name; }).join(', ') }) : '';
  }

  // Daily log quick entry.
  var quick = $('#nb-quicklog');
  if (quick) quick.addEventListener('submit', function (e) {
    e.preventDefault();
    var input = $('#nb-quicklog-input');
    var text = input.value.trim();
    if (!text || !nb) return;
    nb.addLog(text);
    input.value = '';
    input.focus();
  });

  // Run mode button.
  var runBtn = $('#nb-run');
  function updateRun() { if (runBtn && nb) runBtn.hidden = !nb.hasSteps(); }
  if (runBtn) runBtn.addEventListener('click', function () { if (nb) nb.runMode(); });

  // ------------------------------------------------------------ the editor

  var mountEl = $('#editor-mount');
  var initialBody = $('#initial-body');

  function fallback() {
    var banner = document.createElement('div');
    banner.className = 'notebook-banner';
    banner.innerHTML = '<strong>' + t('The editor isn’t built yet.') + '</strong> ' + t('Run this once in a terminal, then refresh. Until then you can write in plain Markdown below.') + '<pre>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</pre>';
    mountEl.parentNode.insertBefore(banner, mountEl);
    var ta = document.createElement('textarea');
    ta.value = initialBody.value;
    ta.spellcheck = false;
    ta.className = 'notebook-fallback';
    ta.readOnly = !canEdit;
    mountEl.replaceWith(ta);
    // No X-Collab-Gen: the server starts live editors again from this text.
    ta.addEventListener('input', debounce(function () { saveBody(ta.value); }, 600));
  }

  function mount(body) {
    return window.BiomanagerNotebook.mount({
      element: mountEl,
      page: Object.assign({}, page, { body: body }),
      me: me,
      getTitle: function () { return title ? title.value : page.title; },
      onSave: saveBody,
      onPeers: drawPeers,
      onChange: debounce(updateRun, 400),
      onStatus: function (s) { if (s === 'offline') setSaved(t('Offline — changes will be sent when back'), true); },
      onMeta: function (m) {
        var shown = m.title === 'Untitled page' ? '' : m.title;
        if (m.title && title && titleUnsaved === null && document.activeElement !== title && title.value !== shown) title.value = shown;
      },
      onReset: restartEditor,
      onCommentOpen: function (id) { openPanel('comments', { focus: id }); },
    }).then(function (api_) {
      nb = api_;
      updateRun();
      if (page.open_comments || location.hash.indexOf('#comment-') === 0) loadComments().then(function () {
        var m = /^#comment-(\d+)/.exec(location.hash);
        if (m) openPanel('comments', { focus: Number(m[1]) });
      });
      return nb;
    });
  }

  var restarting = false;
  function restartEditor() {
    if (restarting) return;
    restarting = true;
    setSaved(t('Updating to the latest version…'));
    api('/notebook/api/pages/' + page.id).then(function (d) {
      page = Object.assign(page, d.page);
      if (nb) { try { nb.destroy(); } catch (_e) { /* already gone */ } nb = null; }
      mountEl.innerHTML = '';
      return mount(d.page.body);
    }).then(function () {
      restarting = false;
      setSaved(t('Up to date'));
      refreshHighlights();
    }).catch(function () { restarting = false; setSaved(t('Could not reload the page — refresh to continue'), true); });
  }

  window.addEventListener('DOMContentLoaded', function () {
    if (!mountEl || !initialBody) return;
    if (window.__notebookBundleMissing || !window.BiomanagerNotebook || typeof window.BiomanagerNotebook.mount !== 'function') {
      fallback();
      return;
    }
    mount(initialBody.value).catch(function (e) {
      console.error(e);
      toast(t('The live editor could not start (%(error)s). Showing the plain text instead.', { error: esc(e.message) }), true);
      mountEl.innerHTML = '';
      fallback();
    });
  });
})();
