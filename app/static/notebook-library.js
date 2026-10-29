/* The notebook's libraries, each a page beside the sidebar
 * (/notebook/protocols, /notebook/recipes, /notebook/reading; /notebook/meetings
 * goes to the calendar, where meetings are kept):
 *
 * - Protocols and Recipes: the lab's folders, then what is in none, then
 *   the common ones built in. Search finds across all of them. A card
 *   opens the protocol's page, or the recipe in the recipe editor, to
 *   edit; a card is filed in a folder by dragging it onto one or with its
 *   folder button. ?folder=, ?recipe= and ?preset= are the place, so Back
 *   and a link come back to it.
 * - Reading: the papers and talks (reading and seminar pages), as cards
 *   with their covers or as every picture in them.
 *
 * notebook-page.js supplies the helpers (window.NotebookPage); the recipe
 * editor is the notebook bundle's (BiomanagerNotebook.recipe).
 */
(function () {
  'use strict';

  var root = document.getElementById('nb-library');
  var NP = window.NotebookPage;
  if (!root || !NP) return;
  var api = NP.api, esc = NP.esc, icon = NP.icon, toast = NP.toast;
  var KIND = root.dataset.library;
  var BASE = '/notebook/' + KIND;

  // ------------------------------------------------------------ helpers

  function place() {
    var q = new URLSearchParams(window.location.search);
    return { folder: Number(q.get('folder')) || null, recipe: q.get('recipe') || null, preset: q.get('preset') || null };
  }
  function link(params) {
    var q = new URLSearchParams();
    Object.keys(params || {}).forEach(function (k) { if (params[k]) q.set(k, params[k]); });
    var s = q.toString();
    return BASE + (s ? '?' + s : '');
  }
  function nav(params) {
    var url = link(params);
    if (url !== window.location.pathname + window.location.search) history.pushState(null, '', url);
    draw();
  }
  window.addEventListener('popstate', draw);

  function count(n, one, many) { return t(n === 1 ? one : many, { n: n }); }
  function matches(text, q) { return !q || String(text || '').toLowerCase().indexOf(q) >= 0; }

  // Enough Markdown to read a built-in protocol: headings, lists, bold, code.
  function inline(s) {
    return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  }
  function markdown(md) {
    var out = [], para = [], list = null;
    function endPara() { if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } }
    function endList() { if (list) { out.push('</' + list + '>'); list = null; } }
    String(md || '').split('\n').forEach(function (line) {
      var h = /^(#{1,6})\s+(.*)$/.exec(line);
      var ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      var ul = /^\s*[-*+]\s+(?:\[[ xX]\]\s+)?(.*)$/.exec(line);
      if (h) {
        endPara(); endList();
        var n = Math.min(h[1].length + 1, 6);
        out.push('<h' + n + '>' + inline(h[2]) + '</h' + n + '>');
      } else if (ol || ul) {
        endPara();
        var tag = ol ? 'ol' : 'ul';
        if (list !== tag) { endList(); out.push('<' + tag + '>'); list = tag; }
        out.push('<li>' + inline((ol || ul)[1]) + '</li>');
      } else if (!line.trim()) {
        endPara(); endList();
      } else {
        endList(); para.push(line.trim());
      }
    });
    endPara(); endList();
    return out.join('');
  }

  // The top of every view: where you are, then the view's own buttons.
  function head(crumbs, tools, blurb) {
    var home = { protocols: ['Protocols', 'protocol'], recipes: ['Recipes', 'flask'], meetings: ['Meetings', 'users'], reading: ['Reading', 'book-open'] }[KIND];
    var trail = [
      '<a href="' + BASE + '" data-nav="" data-drop-folder="">' + icon(home[1]) + ' ' + esc(NP.tc('notebook', home[0])) + '</a>',
    ].concat(crumbs.map(function (c) {
      return '<span class="nb-lib-sep" aria-hidden="true">›</span>' +
        (c.params ? '<a href="' + link(c.params) + '" data-nav="' + esc(JSON.stringify(c.params)) + '">' + (c.icon ? icon(c.icon) + ' ' : '') + esc(c.label) + '</a>'
          : '<span>' + (c.icon ? icon(c.icon) + ' ' : '') + '<span class="nb-lib-crumb-text">' + esc(c.label) + '</span></span>');
    }));
    return '<header class="nb-lib-head"><nav class="nb-lib-crumbs" aria-label="' + t('Where you are') + '">' + trail.join('') + '</nav>' +
      '<div class="nb-lib-tools">' + (tools || '') + '</div></header>' +
      (blurb ? '<p class="nb-lib-blurb">' + blurb + '</p>' : '');
  }
  function searchBox(placeholder, value) {
    return '<label class="nb-lib-search">' + icon('search') +
      '<input type="search" id="nb-lib-q" placeholder="' + esc(placeholder) + '" aria-label="' + esc(placeholder) + '" value="' + esc(value || '') + '" autocomplete="off"></label>';
  }
  function folderTiles(folders, items) {
    if (!folders.length) return '';
    return '<h2 class="nb-lib-label">' + t('Folders') + '</h2><div class="nb-lib-folders">' + folders.map(function (f) {
      var n = items.filter(function (x) { return x.folder_id === f.id; }).length;
      return '<a class="nb-lib-folder" href="' + link({ folder: f.id }) + '" data-nav="' + esc(JSON.stringify({ folder: f.id })) + '" data-drop-folder="' + f.id + '">' +
        icon(f.icon || 'folder') + '<span><b>' + esc(f.name) + '</b><small>' + count(n, KIND === 'protocols' ? '%(n)s protocol' : '%(n)s recipe', KIND === 'protocols' ? '%(n)s protocols' : '%(n)s recipes') + '</small></span></a>';
    }).join('') + '</div>';
  }
  // A card's folder button: a menu of the folders, for whoever may file it.
  function fileMenu(item, folders) {
    if (!item.can_edit || !folders.length) return '';
    return '<label class="nb-lib-file" title="' + t('Move to a folder') + '">' + icon('folder') +
      '<select data-file="' + esc(item.id) + '" aria-label="' + t('Folder') + '"><option value="">' + t('No folder') + '</option>' +
      folders.map(function (f) { return '<option value="' + f.id + '"' + (f.id === item.folder_id ? ' selected' : '') + '>' + esc(f.name) + '</option>'; }).join('') +
      '</select></label>';
  }
  function folderName(folders, id) {
    var f = folders.filter(function (x) { return x.id === id; })[0];
    return f ? f.name : '';
  }

  // Folder buttons shared by both libraries: a name and an icon to pick.
  function newFolder(kind, icons) {
    return BioDialog.prompt(t('New folder'), '', {
      placeholder: kind === 'protocol' ? t('e.g. Cloning') : t('e.g. Buffers'), okLabel: t('Create folder'),
      icons: { choices: icons, value: 'folder' },
    }).then(function (answer) {
      if (!answer || !answer.value.trim()) return null;
      return api('/notebook/api/folders', { body: { kind: kind, name: answer.value.trim(), icon: answer.icon } });
    });
  }
  function editFolder(folder, icons) {
    return BioDialog.prompt(t('Edit folder'), folder.name, {
      okLabel: t('Save'), icons: { choices: icons, value: folder.icon || 'folder' },
    }).then(function (answer) {
      if (!answer || !answer.value.trim()) return null;
      if (answer.value.trim() === folder.name && answer.icon === folder.icon) return null;
      return api('/notebook/api/folders/' + folder.id, { body: { name: answer.value.trim(), icon: answer.icon } });
    });
  }
  function deleteFolder(folder) {
    return BioDialog.confirm(t('Delete the folder %(name)s? What is in it stays, in no folder.', { name: folder.name }), { danger: true }).then(function (ok) {
      return ok ? api('/notebook/api/folders/' + folder.id + '/delete', { body: {} }) : null;
    });
  }
  function fileItem(kind, id, folderId, folders) {
    return api('/notebook/api/folders/file', { body: { kind: kind, item_id: id, folder_id: folderId || null } }).then(function () {
      toast(folderId ? t('Filed in %(folder)s.', { folder: esc(folderName(folders, folderId)) }) : t('Taken out of its folder.'));
    });
  }

  // Dragging a card onto a folder (or onto the library's name, to take it
  // out of its folder) files it.
  var dragging = null;
  root.addEventListener('dragstart', function (e) {
    var card = e.target.closest && e.target.closest('[data-item][draggable="true"]');
    if (!card) return;
    dragging = card.dataset.item;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', card.dataset.title || '');
    card.classList.add('is-dragging');
  });
  root.addEventListener('dragend', function () {
    dragging = null;
    root.querySelectorAll('.is-dragging, .is-drop').forEach(function (el) { el.classList.remove('is-dragging', 'is-drop'); });
  });
  root.addEventListener('dragover', function (e) {
    var target = dragging && e.target.closest('[data-drop-folder]');
    if (!target) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    target.classList.add('is-drop');
  });
  root.addEventListener('dragleave', function (e) {
    var target = e.target.closest && e.target.closest('[data-drop-folder]');
    if (target && !target.contains(e.relatedTarget)) target.classList.remove('is-drop');
  });
  root.addEventListener('drop', function (e) {
    var target = dragging && e.target.closest('[data-drop-folder]');
    if (!target) return;
    e.preventDefault();
    var id = dragging;
    dragging = null;
    if (view && view.drop) view.drop(id, Number(target.dataset.dropFolder) || null);
  });

  // In-page links (data-nav) move without reloading; a modified click still opens a tab.
  root.addEventListener('click', function (e) {
    var a = e.target.closest('a[data-nav]');
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    e.preventDefault();
    nav(a.dataset.nav ? JSON.parse(a.dataset.nav) : {});
  });

  var view = null;      // the library being shown: { drop(id, folder) }
  var editor = null;    // a mounted recipe editor, put away on leaving it
  var lastQuery = '';

  function draw() {
    if (editor) { try { editor.destroy(); } catch (_) { /* gone already */ } editor = null; }
    if (KIND === 'protocols') protocols();
    else if (KIND === 'recipes') recipes();
    else if (KIND === 'reading') reading();
  }

  function failed(e) {
    root.innerHTML = '<p class="nb-warn">' + esc(e.message) + '</p>';
  }

  // ------------------------------------------------------------ protocols

  function protocols() {
    var here = place();
    if (here.preset) return presetProtocol(here.preset);
    api('/notebook/api/protocols').then(function (d) {
      var folder = here.folder ? d.folders.filter(function (f) { return f.id === here.folder; })[0] : null;
      if (here.folder && !folder) { nav({}); return; }
      view = {
        drop: function (id, folderId) {
          fileItem('protocol', Number(id), folderId, d.folders).then(draw).catch(function (err) { toast(esc(err.message), true); });
        },
      };
      var tools = searchBox(t('Find a protocol…'), lastQuery) +
        (folder
          ? (folder.can_edit ? '<button type="button" class="btn" data-edit-folder>' + icon('edit') + ' ' + t('Edit folder') + '</button>' +
            '<button type="button" class="btn" data-delete-folder>' + icon('trash') + ' ' + t('Delete folder') + '</button>' : '')
          : '<button type="button" class="btn" data-new-folder>' + icon('folder-plus') + ' ' + t('New folder') + '</button>') +
        '<button type="button" class="btn btn-primary" data-new-protocol>' + icon('plus') + ' ' + t('New protocol') + '</button>';
      root.innerHTML = head(folder ? [{ label: folder.name, icon: folder.icon }] : [], tools,
        folder ? '' : t('Drag one onto a folder to file it. In a page, type /protocol to insert one.')) +
        '<div class="nb-lib-body" id="nb-lib-body"></div>';

      function card(p) {
        var meta = [p.owner_name, p.version ? 'v' + p.version : t('not numbered yet'), NP.timeText(p.updated_at)];
        if (lastQuery && p.folder_id) meta.unshift(folderName(d.folders, p.folder_id));
        return '<article class="nb-lib-card" data-item="' + p.id + '" data-title="' + esc(p.title) + '" draggable="' + (p.can_edit && d.folders.length ? 'true' : 'false') + '">' +
          '<a class="nb-lib-card-link" href="/notebook?page=' + p.id + '">' + icon('protocol') + '<b>' + esc(p.title) + '</b></a>' +
          (p.gist ? '<p>' + esc(p.gist) + '</p>' : '') +
          '<footer><small>' + esc(meta.filter(Boolean).join(' · ')) + (p.tags.length ? ' <span class="nb-lib-tags">' + p.tags.map(function (x) { return '#' + esc(x); }).join(' ') + '</span>' : '') + '</small>' +
          fileMenu(p, d.folders) + '</footer></article>';
      }
      function presetCard(p) {
        return '<article class="nb-lib-card is-builtin">' +
          '<a class="nb-lib-card-link" href="' + link({ preset: p.key }) + '" data-nav="' + esc(JSON.stringify({ preset: p.key })) + '">' + icon('protocol') + '<b>' + esc(p.title) + '</b></a>' +
          '<p>' + esc(p.summary) + '</p>' +
          '<footer><small>' + t('Built in') + '</small><button type="button" class="btn btn-ghost" data-copy-preset="' + esc(p.key) + '" title="' + t('Save a copy as your own protocol to change') + '">' + t('Copy') + '</button></footer></article>';
      }
      function cards(list) { return '<div class="nb-lib-cards">' + list.map(card).join('') + '</div>'; }

      function body() {
        var q = lastQuery.trim().toLowerCase();
        var box = document.getElementById('nb-lib-body');
        var html = '';
        if (q) {
          var found = d.protocols.filter(function (p) {
            return (!folder || p.folder_id === folder.id) && matches([p.title, p.owner_name, p.gist, p.tags.join(' '), folderName(d.folders, p.folder_id)].join(' '), q);
          });
          var builtIn = folder ? [] : d.presets.filter(function (p) { return matches([p.title, p.category, p.summary].join(' '), q); });
          html = (found.length ? '<h2 class="nb-lib-label">' + t('Lab protocols') + '</h2>' + cards(found) : '') +
            (builtIn.length ? '<h2 class="nb-lib-label">' + t('Common protocols') + '</h2><div class="nb-lib-cards">' + builtIn.map(presetCard).join('') + '</div>' : '') ||
            '<p class="nb-lib-empty">' + t('Nothing found.') + '</p>';
        } else if (folder) {
          var inside = d.protocols.filter(function (p) { return p.folder_id === folder.id; });
          html = inside.length ? cards(inside)
            : '<p class="nb-lib-empty">' + t('Nothing in this folder yet. Make a protocol here with New protocol, or drag one onto the folder.') + '</p>';
        } else {
          var known = {};
          d.folders.forEach(function (f) { known[f.id] = true; });
          var loose = d.protocols.filter(function (p) { return !known[p.folder_id]; });
          html = folderTiles(d.folders, d.protocols) +
            '<h2 class="nb-lib-label">' + (d.folders.length ? t('Not in a folder') : t('Lab protocols')) + '</h2>' +
            (loose.length ? cards(loose) : '<p class="nb-lib-empty">' + (d.protocols.length ? t('Everything is in a folder.') : t('None yet. Write one with New protocol, or copy a common one below and make it yours.')) + '</p>');
          var cats = [];
          var byCat = {};
          d.presets.forEach(function (p) {
            if (!byCat[p.category]) { byCat[p.category] = []; cats.push(p.category); }
            byCat[p.category].push(p);
          });
          html += '<h2 class="nb-lib-label is-section">' + t('Common protocols') + '</h2>' +
            '<p class="nb-lib-blurb">' + t('Starting points written for a typical lab. Copy one to make it your own, and check amounts, times and animal procedures against your lab’s approved protocols.') + '</p>' +
            cats.map(function (c) {
              return '<h3 class="nb-lib-sublabel">' + esc(t(c)) + '</h3><div class="nb-lib-cards">' + byCat[c].map(presetCard).join('') + '</div>';
            }).join('');
        }
        box.innerHTML = html;
      }
      body();

      var input = document.getElementById('nb-lib-q');
      input.addEventListener('input', NP.debounce(function () { lastQuery = input.value; body(); }, 120));
      root.onchange = function (e) {
        var sel = e.target.closest('select[data-file]');
        if (!sel) return;
        fileItem('protocol', Number(sel.dataset.file), Number(sel.value) || null, d.folders).then(draw)
          .catch(function (err) { toast(esc(err.message), true); draw(); });
      };
      root.onclick = function (e) {
        var b = e.target.closest('button');
        if (!b) return;
        if (b.hasAttribute('data-new-folder')) {
          newFolder('protocol', d.folder_icons).then(function (r) { if (r) draw(); }).catch(function (err) { toast(esc(err.message), true); });
        } else if (b.hasAttribute('data-edit-folder')) {
          editFolder(folder, d.folder_icons).then(function (r) { if (r) draw(); }).catch(function (err) { toast(esc(err.message), true); });
        } else if (b.hasAttribute('data-delete-folder')) {
          deleteFolder(folder).then(function (r) { if (r) nav({}); }).catch(function (err) { toast(esc(err.message), true); });
        } else if (b.hasAttribute('data-new-protocol')) {
          BioDialog.prompt(t('New protocol'), '', { placeholder: t('e.g. Western blot'), okLabel: t('Create') }).then(function (title) {
            if (title === null) return;
            api('/notebook/api/protocols/new', { body: { title: title.trim(), folder_id: folder ? folder.id : null } })
              .then(function (r) { window.location.href = r.url; })
              .catch(function (err) { toast(esc(err.message), true); });
          });
        } else if (b.dataset.copyPreset) {
          copyPreset(b, b.dataset.copyPreset, folder ? folder.id : null);
        }
      };
    }).catch(failed);
  }

  function copyPreset(button, key, folderId) {
    button.disabled = true;
    api('/notebook/api/protocols/new', { body: { preset: key, folder_id: folderId } })
      .then(function (r) { window.location.href = r.url; })
      .catch(function (err) { button.disabled = false; toast(esc(err.message), true); });
  }

  function presetProtocol(key) {
    view = null;
    api('/notebook/api/protocols/text?as=written&preset=' + encodeURIComponent(key)).then(function (r) {
      root.innerHTML = head([{ label: r.title }],
        '<button type="button" class="btn btn-primary" data-copy-preset="' + esc(key) + '">' + icon('plus') + ' ' + t('Copy to my protocols') + '</button>',
        t('Built in. Copy it to make it your own: then you can change it, number its versions and file it in a folder.')) +
        '<article class="nb-lib-read">' + markdown(r.markdown) + '</article>';
      root.onchange = null;
      root.onclick = function (e) {
        var b = e.target.closest('[data-copy-preset]');
        if (b) copyPreset(b, key, null);
      };
    }).catch(failed);
  }

  // ------------------------------------------------------------ recipes

  var BLANK_RECIPE = { volume: 1, volumeUnit: 'L', ph: '', notes: '', components: [{ name: '', conc: '', unit: 'mM', mw: '', stock: '', stockUnit: 'M' }] };

  function recipeLine(r) {
    var parts = (r.data.components || []).map(function (c) { return c.name; }).filter(Boolean);
    var size = [r.data.volume, r.data.volumeUnit].filter(function (x) { return x !== undefined && x !== ''; }).join(' ');
    return [size, r.data.ph ? 'pH ' + r.data.ph : '', parts.join(', ')].filter(Boolean).join(' · ');
  }

  function recipes() {
    var here = place();
    api('/notebook/api/recipes').then(function (d) {
      if (here.recipe) return recipe(d, here.recipe);
      var folder = here.folder ? d.folders.filter(function (f) { return f.id === here.folder; })[0] : null;
      if (here.folder && !folder) { nav({}); return; }
      view = {
        drop: function (id, folderId) {
          fileItem('recipe', Number(id), folderId, d.folders).then(draw).catch(function (err) { toast(esc(err.message), true); });
        },
      };
      var tools = searchBox(t('Find a recipe…'), lastQuery) +
        (folder
          ? (folder.can_edit ? '<button type="button" class="btn" data-edit-folder>' + icon('edit') + ' ' + t('Edit folder') + '</button>' +
            '<button type="button" class="btn" data-delete-folder>' + icon('trash') + ' ' + t('Delete folder') + '</button>' : '')
          : '<button type="button" class="btn" data-new-folder>' + icon('folder-plus') + ' ' + t('New folder') + '</button>') +
        '<button type="button" class="btn btn-primary" data-new-recipe>' + icon('plus') + ' ' + t('New recipe') + '</button>';
      root.innerHTML = head(folder ? [{ label: folder.name, icon: folder.icon }] : [], tools,
        folder ? '' : t('In a page, type /recipe to load one; change its volume and every amount follows.')) +
        '<div class="nb-lib-body" id="nb-lib-body"></div>';

      function card(r, builtIn) {
        var params = { recipe: String(r.id) };
        var meta = builtIn ? [t('Built in')] : [r.owner_name, NP.timeText(r.updated_at)];
        if (!builtIn && lastQuery && r.folder_id) meta.unshift(folderName(d.folders, r.folder_id));
        return '<article class="nb-lib-card' + (builtIn ? ' is-builtin' : '') + '" data-item="' + esc(r.id) + '" data-title="' + esc(r.name) + '" draggable="' + (!builtIn && r.can_edit && d.folders.length ? 'true' : 'false') + '">' +
          '<a class="nb-lib-card-link" href="' + link(params) + '" data-nav="' + esc(JSON.stringify(params)) + '">' + icon('flask') + '<b>' + esc(r.name) + '</b></a>' +
          '<p>' + esc(recipeLine(r)) + '</p>' +
          '<footer><small>' + esc(meta.filter(Boolean).join(' · ')) + '</small>' + (builtIn ? '' : fileMenu(r, d.folders)) + '</footer></article>';
      }
      function cards(list, builtIn) { return '<div class="nb-lib-cards">' + list.map(function (r) { return card(r, builtIn); }).join('') + '</div>'; }

      function body() {
        var q = lastQuery.trim().toLowerCase();
        var box = document.getElementById('nb-lib-body');
        var text = function (r) { return [r.name, r.owner_name, recipeLine(r), r.data.notes, folderName(d.folders, r.folder_id)].join(' '); };
        var html;
        if (q) {
          var found = d.recipes.filter(function (r) { return (!folder || r.folder_id === folder.id) && matches(text(r), q); });
          var builtIn = folder ? [] : d.presets.filter(function (r) { return matches(text(r), q); });
          html = (found.length ? '<h2 class="nb-lib-label">' + t('Saved by the lab') + '</h2>' + cards(found) : '') +
            (builtIn.length ? '<h2 class="nb-lib-label">' + t('Common recipes') + '</h2>' + cards(builtIn, true) : '') ||
            '<p class="nb-lib-empty">' + t('Nothing found.') + '</p>';
        } else if (folder) {
          var inside = d.recipes.filter(function (r) { return r.folder_id === folder.id; });
          html = inside.length ? cards(inside)
            : '<p class="nb-lib-empty">' + t('Nothing in this folder yet. Make a recipe here with New recipe, or drag one onto the folder.') + '</p>';
        } else {
          var known = {};
          d.folders.forEach(function (f) { known[f.id] = true; });
          var loose = d.recipes.filter(function (r) { return !known[r.folder_id]; });
          html = folderTiles(d.folders, d.recipes) +
            '<h2 class="nb-lib-label">' + (d.folders.length ? t('Not in a folder') : t('Saved by the lab')) + '</h2>' +
            (loose.length ? cards(loose) : '<p class="nb-lib-empty">' + (d.recipes.length ? t('Everything is in a folder.') : t('None yet. Make one with New recipe, or open a common one below and save a copy.')) + '</p>') +
            '<h2 class="nb-lib-label is-section">' + t('Common recipes') + '</h2>' + cards(d.presets, true);
        }
        box.innerHTML = html;
      }
      body();

      var input = document.getElementById('nb-lib-q');
      input.addEventListener('input', NP.debounce(function () { lastQuery = input.value; body(); }, 120));
      root.onchange = function (e) {
        var sel = e.target.closest('select[data-file]');
        if (!sel) return;
        fileItem('recipe', Number(sel.dataset.file), Number(sel.value) || null, d.folders).then(draw)
          .catch(function (err) { toast(esc(err.message), true); draw(); });
      };
      root.onclick = function (e) {
        var b = e.target.closest('button');
        if (!b) return;
        if (b.hasAttribute('data-new-folder')) {
          newFolder('recipe', d.folder_icons).then(function (r) { if (r) draw(); }).catch(function (err) { toast(esc(err.message), true); });
        } else if (b.hasAttribute('data-edit-folder')) {
          editFolder(folder, d.folder_icons).then(function (r) { if (r) draw(); }).catch(function (err) { toast(esc(err.message), true); });
        } else if (b.hasAttribute('data-delete-folder')) {
          deleteFolder(folder).then(function (r) { if (r) nav({}); }).catch(function (err) { toast(esc(err.message), true); });
        } else if (b.hasAttribute('data-new-recipe')) {
          BioDialog.prompt(t('New recipe'), '', { placeholder: t('e.g. 10× PBS'), okLabel: t('Create') }).then(function (name) {
            if (!name || !name.trim()) return;
            var data = JSON.parse(JSON.stringify(BLANK_RECIPE));
            data.name = name.trim();
            api('/notebook/api/recipes', { body: { name: name.trim(), data: data, folder_id: folder ? folder.id : null } })
              .then(function (r) { nav({ recipe: String(r.id) }); })
              .catch(function (err) { toast(esc(err.message), true); });
          });
        }
      };
    }).catch(failed);
  }

  // One recipe, in the recipe editor: the lab's own saves as it is changed
  // (by whoever saved it, or an admin); a built-in one can be copied.
  function recipe(d, id) {
    view = null;
    var r = d.recipes.concat(d.presets).filter(function (x) { return String(x.id) === String(id); })[0];
    if (!r) { nav({}); return; }
    var builtIn = String(r.id).indexOf('preset:') === 0;
    var editable = !builtIn && r.can_edit;
    var folder = builtIn ? null : d.folders.filter(function (f) { return f.id === r.folder_id; })[0];
    var crumbs = (folder ? [{ label: folder.name, icon: folder.icon, params: { folder: folder.id } }] : []).concat([{ label: r.name }]);
    var tools = '<span class="nb-lib-saved" id="nb-lib-saved" aria-live="polite"></span>' +
      (editable && d.folders.length ? '<label class="nb-folder-select">' + icon('folder') + '<select id="nb-recipe-folder" aria-label="' + t('Folder') + '"><option value="">' + t('No folder') + '</option>' +
        d.folders.map(function (f) { return '<option value="' + f.id + '"' + (f.id === r.folder_id ? ' selected' : '') + '>' + esc(f.name) + '</option>'; }).join('') + '</select></label>' : '') +
      (editable ? '' : '<button type="button" class="btn btn-primary" data-copy-recipe>' + icon('plus') + ' ' + t('Save a copy') + '</button>') +
      (editable ? '<button type="button" class="btn" data-delete-recipe>' + icon('trash') + ' ' + t('Delete') + '</button>' : '');
    var blurb = builtIn ? t('Built in. Save a copy to change it and file it in a folder.')
      : editable ? t('Changes are saved as you make them. Pages that loaded this recipe keep their own copy.')
        : t('Saved by %(name)s; they can change it. Save a copy to make your own.', { name: esc(r.owner_name) });
    root.innerHTML = head(crumbs, tools, blurb) + '<div class="nb-lib-recipe" id="nb-lib-recipe"></div>';

    var saved = document.getElementById('nb-lib-saved');
    var host = document.getElementById('nb-lib-recipe');
    var data = JSON.parse(JSON.stringify(r.data));
    var deleted = false;
    data.name = data.name || r.name;
    if (!window.BiomanagerNotebook || !window.BiomanagerNotebook.recipe) {
      host.innerHTML = '<p class="nb-warn">' + t('The recipe editor did not load. Reload the page to try again.') + '</p>';
    } else {
      editor = window.BiomanagerNotebook.recipe(host, {
        data: data, editable: editable, library: true,
        commit: function (next) {
          if (deleted) return;
          var name = String(next.name || '').trim() || r.name;
          next.components = (next.components || []).map(function (c) { var copy = Object.assign({}, c); delete copy.done; return copy; });
          saved.textContent = t('Saving…');
          api('/notebook/api/recipes', { body: { id: r.id, name: name, data: next } }).then(function () {
            r.name = name;
            r.data = next;
            var last = root.querySelector('.nb-lib-crumbs > span:last-child .nb-lib-crumb-text');
            if (last) last.textContent = name;
            saved.textContent = t('Saved');
          }).catch(function (err) { saved.textContent = ''; toast(esc(err.message), true); });
        },
      });
    }

    root.onchange = function (e) {
      if (e.target.id !== 'nb-recipe-folder') return;
      fileItem('recipe', r.id, Number(e.target.value) || null, d.folders).then(function () { r.folder_id = Number(e.target.value) || null; })
        .catch(function (err) { toast(esc(err.message), true); });
    };
    root.onclick = function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      if (b.hasAttribute('data-copy-recipe')) {
        b.disabled = true;
        var copy = JSON.parse(JSON.stringify(r.data));
        var name = builtIn ? r.name : t('%(name)s (copy)', { name: r.name });
        copy.name = name;
        api('/notebook/api/recipes', { body: { name: name, data: copy } })
          .then(function (x) { nav({ recipe: String(x.id) }); toast(t('Saved as your own. Change it here.')); })
          .catch(function (err) { b.disabled = false; toast(esc(err.message), true); });
      } else if (b.hasAttribute('data-delete-recipe')) {
        BioDialog.confirm(t('Delete this recipe from the library?'), { danger: true }).then(function (ok) {
          if (!ok) return;
          deleted = true;     // a change still waiting to be saved is not
          api('/notebook/api/recipes/' + r.id + '/delete', { body: {} })
            .then(function () { nav(folder ? { folder: folder.id } : {}); })
            .catch(function (err) { toast(esc(err.message), true); });
        });
      }
    };
  }



  // ------------------------------------------------------------ reading

  // Papers (reading notes) and talks (seminar notes): a card each with its
  // cover, or every picture in them, to find one by a figure remembered.
  // Search reads titles, authors, journals, DOIs, tags and picture captions.
  var READ_VIEW_KEY = 'nb-reading-view';
  function readingView() { try { return localStorage.getItem(READ_VIEW_KEY) === 'pictures' ? 'pictures' : 'cards'; } catch (_) { return 'cards'; } }
  function reading() {
    view = null;
    var state = { q: '', only: '', layout: readingView(), items: null };
    root.innerHTML = head([], searchBox(t('Title, author, journal, DOI or tag'), '') +
      '<div class="seg" role="group" aria-label="' + t('Show') + '">' +
      '<button type="button" class="seg-item is-active" data-only="">' + t('All') + '</button>' +
      '<button type="button" class="seg-item" data-only="reading">' + t('Papers') + '</button>' +
      '<button type="button" class="seg-item" data-only="seminar">' + t('Talks') + '</button></div>' +
      '<div class="seg" role="group" aria-label="' + t('Layout') + '">' +
      '<button type="button" class="seg-item" data-layout="cards" title="' + t('Cards') + '">' + icon('grid') + '</button>' +
      '<button type="button" class="seg-item" data-layout="pictures" title="' + t('Pictures') + '">' + icon('image') + '</button></div>' +
      '<button type="button" class="btn" data-zotero>' + icon('book-open') + ' Zotero</button>' +
      '<button type="button" class="btn btn-primary" data-new-page-kind="reading">' + icon('plus') + ' ' + t('Paper notes') + '</button>') +
      '<p class="nb-lib-blurb nb-zotero-line" id="nb-zotero-line"></p>' +
      '<div id="nb-read-out"><p class="nb-muted">' + t('Loading…') + '</p></div>';
    var out = document.getElementById('nb-read-out');
    function text(x) {
      var f = x.fields || {};
      return [x.title, f.authors, f.journal, f.year, f.doi, f.speaker, f.affiliation, (x.tags || []).join(' '),
        (x.images || []).map(function (i) { return i.alt; }).join(' ')].join(' ').toLowerCase();
    }
    function byline(x) {
      var f = x.fields || {};
      if (x.kind === 'seminar') return [f.speaker, f.affiliation].filter(Boolean).join(' · ');
      return f.authors || '';
    }
    function source(x) {
      var f = x.fields || {};
      return x.kind === 'seminar' ? [t('Talk'), x.entry_date].filter(Boolean).join(' · ') : [f.journal, f.year].filter(Boolean).join(' · ');
    }
    function card(x) {
      var pic = x.cover || (x.images[0] && x.images[0].src) || '';
      return '<a class="nb-read-card" href="' + esc(x.url) + '">' +
        '<span class="nb-read-cover' + (pic ? '' : ' is-empty') + '">' + (pic ? '<img src="' + esc(pic) + '" alt="" loading="lazy">' : icon(x.kind === 'seminar' ? 'person-chalkboard' : 'book-open')) + '</span>' +
        '<span class="nb-read-body"><b>' + esc(x.title) + '</b>' +
        (byline(x) ? '<span class="nb-read-by">' + esc(byline(x)) + '</span>' : '') +
        (source(x) ? '<small>' + esc(source(x)) + '</small>' : '') +
        (x.tags.length ? '<small class="nb-lib-tags">' + x.tags.map(function (g) { return '#' + esc(g); }).join(' ') + '</small>' : '') +
        (!x.mine ? '<small>' + esc(t('by %(name)s', { name: x.owner_name })) + '</small>' : '') + '</span></a>';
    }
    function pictures(list) {
      var pics = [];
      list.forEach(function (x) {
        var seen = {};
        (x.cover ? [{ src: x.cover, alt: '' }] : []).concat(x.images).forEach(function (img) {
          if (seen[img.src]) return;
          seen[img.src] = true;
          pics.push('<a class="nb-read-pic" href="' + esc(x.url) + '" title="' + esc(x.title) + '"><img src="' + esc(img.src) + '" alt="' + esc(img.alt) + '" loading="lazy">' +
            '<span>' + esc(img.alt || x.title) + '</span></a>');
        });
      });
      return pics.length ? '<div class="nb-read-pics">' + pics.join('') + '</div>'
        : '<p class="nb-lib-empty">' + t('No pictures yet. A cover or a picture pasted into a paper’s notes shows here.') + '</p>';
    }
    function paint() {
      root.querySelectorAll('[data-only]').forEach(function (b) { b.classList.toggle('is-active', b.dataset.only === state.only); });
      root.querySelectorAll('[data-layout]').forEach(function (b) { b.classList.toggle('is-active', b.dataset.layout === state.layout); });
      if (!state.items) return;
      var q = state.q.toLowerCase();
      var list = state.items.filter(function (x) { return (!state.only || x.kind === state.only) && matches(text(x), q); });
      if (!state.items.length) {
        out.innerHTML = '<p class="nb-lib-empty">' + t('No papers or talks yet. Paper notes and Seminar pages show here, yours and those shared with you.') + '</p>';
      } else if (!list.length) {
        out.innerHTML = '<p class="nb-lib-empty">' + t('Nothing matches.') + '</p>';
      } else {
        out.innerHTML = state.layout === 'pictures' ? pictures(list) : '<div class="nb-read-grid">' + list.map(card).join('') + '</div>';
      }
    }
    function load() {
      return api('/notebook/api/reading').then(function (d) { state.items = d.items; paint(); })
        .catch(function (e) { out.innerHTML = '<p class="nb-warn">' + esc(e.message) + '</p>'; });
    }
    load();
    // Where paper notes come from: Zotero, once connected (notebook-page.js).
    function zoteroLine(z) {
      var line = document.getElementById('nb-zotero-line');
      if (!line) return;
      line.innerHTML = z && z.connected
        ? icon('book-open') + ' ' + esc(t('New papers in Zotero (%(where)s) become notes here.', {
          where: [z.library_name, z.collection_name].filter(Boolean).join(' › ') })) +
          (z.error ? ' <span class="nb-warn">' + esc(z.error) + '</span>' : '')
        : t('Connect Zotero, and each paper you add there becomes a note here.');
    }
    zoteroLine(NP.zotero ? NP.zotero() : null);
    window.addEventListener('nb:zotero-state', function (e) { zoteroLine(e.detail); });
    window.addEventListener('nb:zotero-made', load);
    var search = document.getElementById('nb-lib-q');
    search.addEventListener('input', function () { state.q = search.value.trim(); paint(); });
    root.onchange = null;
    root.onclick = function (e) {
      var only = e.target.closest('[data-only]');
      var layout = e.target.closest('[data-layout]');
      if (only) { state.only = only.dataset.only; paint(); }
      if (layout) {
        state.layout = layout.dataset.layout;
        try { localStorage.setItem(READ_VIEW_KEY, state.layout); } catch (_) { /* not remembered */ }
        paint();
      }
    };
    paint();
  }

  draw();
})();
