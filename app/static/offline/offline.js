/* The offline screens (app/static/offline/).
 *
 * They show the facility from this device's copy (the server's snapshot,
 * app/sync.py) and record what is done as a queue of changes, which goes to
 * the lab when its server answers again. What they record is shown at once,
 * marked "waiting", until the lab has it.
 *
 * Where the copy lives depends on who serves the screens:
 *   the desktop's relay   fetch /_relay/store, /_relay/store/queue, /_relay/sync (app/relay.py)
 *   a phone app           window.webkit.messageHandlers.bmStore (iPhone) or window.BMNative (Android),
 *                         answering through window.BMStoreReply(id, ok, value)
 */
(function () {
  "use strict";

  // ------------------------------------------------------------ the store
  var pending = {}, seq = 0;
  var nativePost = (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bmStore)
    ? function (m) { window.webkit.messageHandlers.bmStore.postMessage(m); }
    : (window.BMNative ? function (m) { window.BMNative.post(m); } : null);

  window.BMStoreReply = function (id, ok, value) {
    var p = pending[id];
    if (!p) return;
    delete pending[id];
    var v = typeof value === "string" ? JSON.parse(value || "null") : value;
    if (ok) p.resolve(v); else p.reject(new Error(String(v && v.error || v || "failed")));
  };

  function native(name, arg) {
    return new Promise(function (resolve, reject) {
      var id = ++seq;
      pending[id] = { resolve: resolve, reject: reject };
      nativePost(JSON.stringify({ id: id, name: name, arg: arg === undefined ? null : arg }));
    });
  }

  function json(url, options) {
    return fetch(url, options).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok && body && body.error) throw new Error(body.error);
        return body;
      });
    });
  }

  var store = nativePost ? {
    load: function () { return native("load"); },            // {snapshot, queue, status}
    add: function (op) { return native("add", op); },        // {ok, waiting}
    sync: function () { return native("sync"); },            // {ok, status}
    status: function () { return native("status"); },        // {online, store}
    strings: function () { return native("strings"); },      // {lang, words}
    backOnline: function () { return native("online"); }
  } : {
    load: function () { return json("/_relay/store"); },
    add: function (op) {
      return json("/_relay/store/queue", { method: "POST", headers: { "Content-Type": "application/json" },
                                          body: JSON.stringify(op) });
    },
    sync: function () { return json("/_relay/sync", { method: "POST" }); },
    status: function () { return json("/_relay/status"); },
    strings: function () { return json("/_offline/strings.json"); },
    backOnline: function () { location.href = from || "/"; }
  };

  // ------------------------------------------------------------ words
  var WORDS = {}, LANG = "en";
  function t(text, values) {
    var out = WORDS[text] || text;
    if (values) Object.keys(values).forEach(function (k) { out = out.split("%(" + k + ")s").join(values[k]); });
    return out;
  }
  function translatePage() {
    document.documentElement.lang = LANG === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-t]").forEach(function (el) {
      el.dataset.en = el.dataset.en || el.textContent.trim();
      el.textContent = t(el.dataset.en);
    });
    document.querySelectorAll("[data-t-placeholder]").forEach(function (el) {
      el.placeholder = t(el.dataset.tPlaceholder);
    });
    document.title = "BioManager · " + t("Offline");
  }

  // ------------------------------------------------------------ state
  var snap = null, queue = [], status = {}, view = null;
  var tab = "cages", from = new URLSearchParams(location.search).get("from") || "";
  var main = document.getElementById("main");

  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function today() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function uid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
    return Date.now().toString(16) + Math.random().toString(16).slice(2);
  }
  function tmp() { return "tmp:" + uid().slice(0, 12); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function byId(list, id) { return (list || []).find(function (r) { return r.id === id; }) || null; }
  function ref(kind, id) { return typeof id === "string" ? id : { kind: kind, id: id }; }
  function idOf(r) { return r && typeof r === "object" ? r.id : r; }

  // What the screens show: the copy, with what waits applied on top.
  function rebuild() {
    view = clone(snap || {});
    ["mice", "cages", "litters", "weights", "tasks", "tanks", "stock_units", "racks"].forEach(function (k) {
      view[k] = view[k] || [];
    });
    queue.forEach(function (op) { applyLocally(view, op); });
  }

  function mark(rec) { if (rec) rec._waiting = true; }

  // A cage's litters not yet weaned, oldest first (sync.cage_litters). A copy
  // taken before 1.9 has only the cage's one birth date.
  function littersOf(c) {
    if (c.litters) return c.litters;
    return c.date_give_birth ? [{ born: c.date_give_birth }] : [];
  }
  function setLitters(c, list) {
    list.sort(function (a, b) { return a.born < b.born ? -1 : a.born > b.born ? 1 : 0; });
    c.litters = list;
    c.date_give_birth = list.length ? list[0].born : null;   // the cage's date is its oldest
    mark(c);
  }
  function litterBorn(list, born) { return list.find(function (l) { return l.born === born; }) || null; }

  function applyLocally(v, op) {
    var f = op.fields || {}, target = idOf(op.target);
    if (op.action === "mouse_weight") {
      v.weights.unshift({ id: "w" + op.op, mouse: target, date: f.date || today(), grams: f.grams, _waiting: true });
    } else if (op.action === "mice_died") {
      (f.mice || []).forEach(function (m) {
        var rec = byId(v.mice, idOf(m));
        if (rec) { rec.status = "sac"; rec.date_of_death = f.date || today(); mark(rec); }
      });
    } else if (op.action === "mice_move") {
      (f.mice || []).forEach(function (m) {
        var rec = byId(v.mice, idOf(m));
        if (rec) { rec.cage = target; mark(rec); }
      });
    } else if (op.action === "note_add") {
      var kind = op.target && op.target.kind || "mouse";
      var rec = byId(kind === "cage" ? v.cages : v.mice, target);
      if (rec) {
        var key = kind === "cage" ? "notes" : "note";
        rec[key] = rec[key] ? rec[key] + "; " + f.text : f.text;
        mark(rec);
      }
    } else if (op.action === "litter_born") {
      var cage = byId(v.cages, target);
      if (cage) {
        var list = littersOf(cage).slice(), born = f.date || today();
        var fixed = f.another === false && (litterBorn(list, f.litter) || list[0]);
        if (litterBorn(list, born)) {
          // that day's litter is there already
        } else if (fixed) {
          list[list.indexOf(fixed)] = Object.assign({}, fixed, { born: born, _waiting: true });
        } else {
          list.push({ born: born, pups: f.pups || null, dam: f.mother || "", _waiting: true });
        }
        setLitters(cage, list);
      }
    } else if (op.action === "litter_genotyping") {
      var made = (op.creates || {}).mouse || [];
      var gcage = byId(v.cages, target);
      if (gcage) {
        var glist = littersOf(gcage).slice();
        var whose = litterBorn(glist, f.litter) || glist.find(function (l) { return !l.litter; });
        if (whose) { glist[glist.indexOf(whose)] = Object.assign({}, whose, { litter: ((op.creates || {}).litter || [])[0] || true }); setLitters(gcage, glist); }
      }
      made.forEach(function (id, i) {
        v.mice.push({ id: id, number: "", label: t("New pup %(n)s", { n: i + 1 }), sex: "", transgenes: [],
                      status: "geno", owner: "", note: "", cage: target, _waiting: true });
      });
    } else if (op.action === "wean") {
      var weaned = byId(v.cages, target);
      if (weaned) {
        var wlist = littersOf(weaned).slice();
        wlist.splice(Math.max(0, wlist.indexOf(litterBorn(wlist, f.litter))), 1);   // the one named, else the oldest
        setLitters(weaned, wlist);
      }
    } else if (op.action === "mouse_change") {
      var m = byId(v.mice, target);
      if (m) { Object.keys(f).forEach(function (k) { m[k] = f[k]; }); mark(m); }
    } else if (op.action === "task_done") {
      var task = byId(v.tasks, target);
      if (task) { task.done = true; mark(task); }
    } else if (op.action === "stock_event") {
      var unit = byId(v.stock_units, target);
      if (unit) { if (f.event === "discard") unit.active = false; unit._flipped = f.event === "copy"; mark(unit); }
    } else if (op.action === "tank_change") {
      var tank = byId(v.tanks, target);
      if (tank) { Object.keys(f).forEach(function (k) { tank[k] = f[k]; }); mark(tank); }
    }
  }

  // ------------------------------------------------------------ recording a change
  function record(action, target, fields, base, creates) {
    var op = { op: uid(), made_at: new Date().toISOString(), cursor: snap && snap.cursor, action: action,
               fields: fields || {}, base: base || {} };
    if (target !== undefined && target !== null) op.target = target;
    if (creates) op.creates = creates;
    return store.add(op).then(function (answer) {
      if (answer && answer.ok === false) throw new Error(answer.error || t("It couldn't be kept."));
      queue.push(op);
      rebuild();
      render();
    });
  }

  function saw(kind, rec, keys) {
    var out = {};
    if (!rec || typeof rec.id === "string") return out;
    var b = { v: rec.v };
    (keys || []).forEach(function (k) { b[k] = rec[k] === undefined ? null : rec[k]; });
    out[kind + ":" + rec.id] = b;
    return out;
  }

  // ------------------------------------------------------------ the bar
  function renderBar() {
    var fresh = document.getElementById("freshness");
    var taken = snap && snap.taken ? new Date(snap.taken) : null;
    if (taken && !isNaN(taken)) {
      var old = Date.now() - taken.getTime() > 24 * 3600 * 1000;
      var locale = LANG === "zh" ? "zh-CN" : undefined;
      var sameDay = taken.toDateString() === new Date().toDateString();
      fresh.textContent = t("Updated %(when)s", { when: sameDay
        ? taken.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
        : taken.toLocaleString(locale, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) });
      fresh.className = old ? "stale" : "muted";
      fresh.title = old ? t("This copy is more than a day old.") : "";
    } else {
      fresh.textContent = t("No copy yet");
    }
    var w = document.getElementById("waiting");
    w.hidden = !queue.length;
    w.textContent = t("%(n)s waiting", { n: queue.length });
    w.title = queue.length === 1 ? t("1 change waiting to reach the lab") : t("%(n)s changes waiting to reach the lab", { n: queue.length });
    document.querySelectorAll("#tabs [data-tab]").forEach(function (b) {
      b.setAttribute("aria-current", b.dataset.tab === tab ? "true" : "false");
      if (b.dataset.tab === "vials") b.hidden = !view.stock_units.length;
      if (b.dataset.tab === "tanks") b.hidden = !view.tanks.length;
    });
  }

  // ------------------------------------------------------------ screens
  function render() {
    renderBar();
    var hash = location.hash.replace(/^#/, "");
    var q = document.getElementById("find").value.trim();
    if (hash.indexOf("cage/") === 0) return renderCage(hash.slice(5));
    if (q) return renderFound(q);
    ({ cages: renderCages, todos: renderTodos, vials: renderVials, tanks: renderTanks, sent: renderSent }[tab] || renderCages)();
  }

  function living(cageId) {
    return view.mice.filter(function (m) { return m.cage === cageId && !m.date_of_death && m.status !== "sac"; });
  }
  function rackName(id) { var r = byId(view.racks, id); return r ? r.name : ""; }
  function cageLine(c) {
    var n = living(c.id).length;
    var bits = [c.purpose || t("No purpose"), c.owner, n === 1 ? t("1 mouse") : t("%(n)s mice", { n: n })];
    if (c.rack) bits.push(rackName(c.rack));
    return bits.filter(Boolean).join(" · ");
  }
  function cageItem(c) {
    var due = (view.weaning_due || []).some(function (d) { return d.cage === c.id; });
    return '<button class="item" data-open-cage="' + esc(c.id) + '"><span class="grow"><span class="title">' +
      esc(c.cage_id) + '</span><span class="sub">' + esc(cageLine(c)) + '</span></span>' +
      (c.breeding ? '<span class="tag">' + esc(t("Breeding")) + '</span>' : "") +
      (due ? '<span class="tag warn">' + esc(t("Wean due")) + '</span>' : "") +
      (c._waiting ? '<span class="tag wait">' + esc(t("waiting")) + '</span>' : "") + "</button>";
  }

  function renderCages() {
    var cages = view.cages.filter(function (c) { return living(c.id).length || c.breeding || c.date_give_birth; });
    if (!cages.length) { main.innerHTML = '<p class="empty">' + esc(t("No cages with mice in this copy.")) + "</p>"; return; }
    var groups = {};
    cages.forEach(function (c) { var k = rackName(c.rack) || t("Not in a rack"); (groups[k] = groups[k] || []).push(c); });
    main.innerHTML = Object.keys(groups).sort(function (a, b) { return a.localeCompare(b, undefined, { numeric: true }); }).map(function (k) {
      return "<h3>" + esc(k) + '</h3><div class="list">' + groups[k].map(cageItem).join("") + "</div>";
    }).join("");
  }

  function mouseLabel(m) { return m.number ? "#" + m.number : (m.label || t("New pup")); }

  function renderFound(q) {
    var low = q.toLowerCase().replace(/^#/, "");
    var link = q.match(/#cage-(\d+)/);
    if (link) { location.hash = "cage/" + link[1]; return; }
    var cages = view.cages.filter(function (c) {
      return c.cage_id.toLowerCase().indexOf(low) >= 0 || (c.card_id || "").toLowerCase().indexOf(low) >= 0;
    }).slice(0, 30);
    var mice = view.mice.filter(function (m) {
      return String(m.number) === low || (m.ear_tag || "").toLowerCase() === low;
    }).slice(0, 30);
    main.innerHTML = (cages.length ? "<h3>" + esc(t("Cages")) + '</h3><div class="list">' + cages.map(cageItem).join("") + "</div>" : "") +
      (mice.length ? "<h3>" + esc(t("Mice")) + '</h3><div class="list">' + mice.map(function (m) {
        var c = byId(view.cages, m.cage);
        return '<button class="item" data-open-cage="' + esc(m.cage) + '" data-hit="' + esc(m.id) + '"><span class="grow"><span class="title">' +
          esc(mouseLabel(m)) + '</span><span class="sub">' + esc([m.status, c ? t("cage %(cage)s", { cage: c.cage_id }) : ""].filter(Boolean).join(" · ")) +
          "</span></span></button>";
      }).join("") + "</div>" : "") +
      (!cages.length && !mice.length ? '<p class="empty">' + esc(t("Nothing in this copy matches “%(q)s”.", { q: q })) + "</p>" : "");
  }

  var hit = null;
  function renderCage(raw) {
    var id = /^\d+$/.test(raw) ? Number(raw) : raw;
    var c = byId(view.cages, id);
    if (!c) { main.innerHTML = '<button class="back" data-home>‹ ' + esc(t("Cages")) + '</button><p class="empty">' + esc(t("This cage isn't in this copy.")) + "</p>"; return; }
    var mice = living(c.id);
    var acts = ['<button class="btn small" data-act="cage-note">' + esc(t("Add a note")) + "</button>"];
    if (c.breeding) {
      acts.unshift('<button class="btn small" data-act="wean">' + esc(t("Wean")) + "</button>");
      acts.unshift('<button class="btn small" data-act="genotyping">' + esc(t("Genotyping")) + "</button>");
      acts.unshift('<button class="btn small" data-act="litter">' + esc(t("Litter born")) + "</button>");
    }
    main.innerHTML = '<button class="back" data-home>‹ ' + esc(t("Cages")) + "</button>" +
      '<section class="card"><h2>' + esc(t("Cage %(cage)s", { cage: c.cage_id })) +
      (c._waiting ? ' <span class="tag wait">' + esc(t("waiting")) + "</span>" : "") + "</h2>" +
      '<p class="muted">' + esc(cageLine(c)) + "</p>" +
      littersOf(c).map(function (l) {
        return '<p class="muted">' + esc(t("Litter born %(day)s", { day: l.born })) +
          (l.pups ? " · " + esc(t("%(n)s pups", { n: l.pups })) : "") +
          (l._waiting ? ' <span class="tag wait">' + esc(t("waiting")) + "</span>" : "") + "</p>";
      }).join("") +
      (c.notes ? "<p>" + esc(c.notes) + "</p>" : "") +
      (c.edit === false ? '<p class="muted">' + esc(t("Someone else's cage: the lab checks your changes when they arrive.")) + "</p>" : "") +
      '<div class="row">' + acts.join("") + "</div></section>" +
      '<section class="card">' + (mice.length ? mice.map(function (m) { return mouseRow(m, c); }).join("") :
        '<p class="empty">' + esc(t("No living mice in this cage.")) + "</p>") + "</section>";
    if (hit) {
      var el = main.querySelector('[data-mouse="' + CSS.escape(String(hit)) + '"]');
      if (el) { el.classList.add("hit"); el.scrollIntoView({ block: "center" }); }
      hit = null;
    }
  }

  function lastWeight(m) {
    var w = view.weights.find(function (x) { return x.mouse === m.id; });
    return w ? t("%(g)s g on %(day)s", { g: w.grams, day: w.date }) + (w._waiting ? " · " + t("waiting") : "") : "";
  }

  function mouseRow(m) {
    var genes = (m.transgenes || []).filter(Boolean).join("; ") || m.genotype || "";
    return '<div class="mouse" data-mouse="' + esc(m.id) + '"><div><strong>' + esc(mouseLabel(m)) + "</strong> " +
      '<span class="muted">' + esc([m.sex, genes, m.status].filter(Boolean).join(" · ")) + "</span>" +
      (m._waiting ? ' <span class="tag wait">' + esc(t("waiting")) + "</span>" : "") +
      (m.note ? '<div class="muted">' + esc(m.note) + "</div>" : "") + '</div><div class="muted">' + esc(lastWeight(m)) + "</div>" +
      '<div class="acts"><button class="btn small" data-act="weigh" data-id="' + esc(m.id) + '">' + esc(t("Record a weight")) + "</button>" +
      '<button class="btn small" data-act="died" data-id="' + esc(m.id) + '">' + esc(t("Died")) + "</button>" +
      '<button class="btn small" data-act="move" data-id="' + esc(m.id) + '">' + esc(t("Move")) + "</button>" +
      '<button class="btn small" data-act="mouse-note" data-id="' + esc(m.id) + '">' + esc(t("Note")) + "</button></div></div>";
  }

  function renderTodos() {
    var open = view.tasks.filter(function (x) { return !x.done || x._waiting; });
    main.innerHTML = open.length ? '<div class="list">' + open.map(function (x) {
      return '<label class="item"><input class="check" type="checkbox" data-task="' + esc(x.id) + '"' + (x.done ? " checked disabled" : "") +
        (x.edit === false ? " disabled" : "") + '><span class="grow"><span class="title">' + esc(x.title) + '</span><span class="sub">' +
        esc([x.due ? t("due %(day)s", { day: x.due }) : "", x.shared ? t("the lab's") : ""].filter(Boolean).join(" · ")) +
        "</span></span>" + (x._waiting ? '<span class="tag wait">' + esc(t("waiting")) + "</span>" : "") + "</label>";
    }).join("") + "</div>" : '<p class="empty">' + esc(t("No to-dos due in the next two weeks.")) + "</p>";
  }

  function renderVials() {
    var units = view.stock_units.filter(function (u) { return u.active; });
    main.innerHTML = '<div class="list">' + units.map(function (u) {
      return '<div class="item"><span class="grow"><span class="title">' + esc(t("No. %(n)s", { n: u.number })) + '</span><span class="sub">' +
        esc([u.genotype, u.purpose, u.owner].filter(Boolean).join(" · ")) + "</span></span>" +
        (u._flipped ? '<span class="tag wait">' + esc(t("flipped")) + "</span>" : "") +
        '<button class="btn small" data-act="flip" data-id="' + esc(u.id) + '">' + esc(t("Flipped")) + "</button>" +
        '<button class="btn small" data-act="discard" data-id="' + esc(u.id) + '">' + esc(t("Discard")) + "</button></div>";
    }).join("") + "</div>";
  }

  function renderTanks() {
    main.innerHTML = '<div class="list">' + view.tanks.filter(function (x) { return x.active; }).map(function (x) {
      return '<div class="item"><span class="grow"><span class="title">' + esc(x.tank_id) + '</span><span class="sub">' +
        esc([x.line, x.purpose, x.owner, x.notes].filter(Boolean).join(" · ")) + "</span></span>" +
        (x._waiting ? '<span class="tag wait">' + esc(t("waiting")) + "</span>" : "") +
        '<button class="btn small" data-act="tank-note" data-id="' + esc(x.id) + '">' + esc(t("Note")) + "</button></div>";
    }).join("") + "</div>";
  }

  function renderSent() {
    var refused = (status.refused || []).slice().reverse();
    main.innerHTML = "<h3>" + esc(t("Waiting to reach the lab")) + "</h3>" + (queue.length ? '<div class="list">' + queue.map(function (op) {
      return '<div class="item"><span class="grow"><span class="title">' + esc(t(ACTION_NAMES[op.action] || op.action)) +
        '</span><span class="sub">' + esc(new Date(op.made_at).toLocaleString()) + "</span></span></div>";
    }).join("") + "</div>" : '<p class="muted">' + esc(t("Nothing is waiting.")) + "</p>") +
      (status.held ? '<p class="note">' + esc(t("%(n)s changes need your decision: open Needs your decision once you are back online.", { n: status.held })) + "</p>" : "") +
      (refused.length ? "<h3>" + esc(t("Refused by the lab")) + '</h3><div class="list">' + refused.map(function (r) {
        return '<div class="item"><span class="grow"><span class="sub">' + esc(r.message) + "</span></span></div>";
      }).join("") + "</div>" : "");
  }

  var ACTION_NAMES = { mouse_weight: "Weight", mice_died: "Died", mice_move: "Moved", note_add: "Note",
                       litter_born: "Litter born", litter_genotyping: "Genotyping", wean: "Wean", task_done: "To-do done",
                       stock_event: "Flies and worms", tank_change: "Tank", mouse_change: "Mouse changed" };

  // ------------------------------------------------------------ asking for a value
  var dialog = document.getElementById("ask");
  function ask(title, fields, done) {
    document.getElementById("ask-title").textContent = title;
    var box = document.getElementById("ask-fields");
    box.innerHTML = fields.map(function (f, i) {
      var input = f.type === "select"
        ? '<select name="f' + i + '">' + f.options.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (o[0] === f.value ? " selected" : "") + ">" + esc(o[1]) + "</option>"; }).join("") + "</select>"
        : '<input name="f' + i + '" type="' + (f.type || "text") + '"' + (f.step ? ' step="' + f.step + '"' : "") +
          (f.list ? ' list="ask-list"' : "") + ' value="' + esc(f.value || "") + '"' + (f.required ? " required" : "") + ">";
      return "<label>" + esc(f.label) + input + "</label>";
    }).join("") + (fields.some(function (f) { return f.list; }) ? '<datalist id="ask-list">' +
      fields.find(function (f) { return f.list; }).list.map(function (o) { return '<option value="' + esc(o) + '">'; }).join("") + "</datalist>" : "");
    var error = document.getElementById("ask-error");
    error.hidden = true;
    var form = document.getElementById("ask-form");
    form.onsubmit = function (e) {
      e.preventDefault();
      var values = fields.map(function (_, i) { return form.elements["f" + i].value.trim(); });
      Promise.resolve().then(function () { return done(values); }).then(function () { dialog.close(); })
        .catch(function (err) { error.textContent = err.message; error.hidden = false; });
    };
    dialog.showModal();
    var first = box.querySelector("input, select");
    if (first) first.focus();
  }
  document.getElementById("ask-cancel").onclick = function () { dialog.close(); };

  function cageNow() { var id = location.hash.slice(6); return byId(view.cages, /^\d+$/.test(id) ? Number(id) : id); }
  function mouseById(raw) { var id = /^\d+$/.test(raw) ? Number(raw) : raw; return byId(view.mice, id); }

  // On a cage with two litters, Genotyping and Wean ask which (its birth date).
  function whichLitter(list, chosen) {
    return { label: t("Which litter"), type: "select", value: chosen && chosen.born,
             options: list.map(function (l) { return [l.born, t("Litter born %(day)s", { day: l.born })]; }) };
  }

  var ACTS = {
    weigh: function (id) {
      var m = mouseById(id);
      ask(t("Weigh %(mouse)s", { mouse: mouseLabel(m) }), [
        { label: t("Grams"), type: "number", step: "0.1", required: true },
        { label: t("Weighed on"), type: "date", value: today() }], function (v) {
        var g = parseFloat(v[0]);
        if (!(g > 0)) throw new Error(t("Type a weight in grams."));
        return record("mouse_weight", ref("mouse", m.id), { grams: g, date: v[1] || today() });
      });
    },
    died: function (id) {
      var m = mouseById(id);
      ask(t("%(mouse)s died or was culled", { mouse: mouseLabel(m) }), [
        { label: t("Died on"), type: "date", value: today() }, { label: t("Why (found dead, culled…)") }], function (v) {
        var fields = { mice: [ref("mouse", m.id)], date: v[0] || today() };
        if (v[1]) fields.reason = v[1];
        return record("mice_died", null, fields, saw("mouse", m, ["status"]));
      });
    },
    move: function (id) {
      var m = mouseById(id);
      ask(t("Move %(mouse)s", { mouse: mouseLabel(m) }), [
        { label: t("To cage"), required: true, list: view.cages.map(function (c) { return c.cage_id; }) }], function (v) {
        var dest = view.cages.find(function (c) { return c.cage_id.toLowerCase() === v[0].toLowerCase(); });
        if (!dest) throw new Error(t("There is no cage %(cage)s in this copy.", { cage: v[0] }));
        return record("mice_move", ref("cage", dest.id), { mice: [ref("mouse", m.id)] }, saw("mouse", m, ["cage"]));
      });
    },
    "mouse-note": function (id) {
      var m = mouseById(id);
      ask(t("Note on %(mouse)s", { mouse: mouseLabel(m) }), [{ label: t("What to add"), required: true }], function (v) {
        return record("note_add", ref("mouse", m.id), { text: v[0] });
      });
    },
    "cage-note": function () {
      var c = cageNow();
      ask(t("Note on cage %(cage)s", { cage: c.cage_id }), [{ label: t("What to add"), required: true }], function (v) {
        return record("note_add", ref("cage", c.id), { text: v[0] });
      });
    },
    litter: function () {
      var c = cageNow(), list = littersOf(c);
      var fields = [{ label: t("Born on"), type: "date", value: today() },
                    { label: t("How many pups"), type: "number" }, { label: t("Mother") }];
      // A litter not yet weaned: a second female's, or a wrong date? The
      // server refuses to guess, so the phone asks too, and which one to correct.
      if (list.length) {
        var options = [["another", t("Another litter")]].concat(list.map(function (l) {
          return [l.born, list.length > 1 ? t("Correct the litter born %(day)s", { day: l.born }) : t("Correct the date")];
        }));
        fields.push({ label: list.length > 1
                        ? t("This cage has litters born %(days)s, not yet weaned. This one is:", { days: list.map(function (l) { return l.born; }).join(LANG === "zh" ? "、" : ", ") })
                        : t("This cage has a litter born %(day)s, not yet weaned. This one is:", { day: list[0].born }),
                      type: "select", options: options });
      }
      ask(t("Litter born in cage %(cage)s", { cage: c.cage_id }), fields, function (v) {
        var f = { date: v[0] || today() };
        if (v[1]) {
          var n = parseInt(v[1], 10);
          if (!(n >= 1 && n <= 40)) throw new Error(t("Between 1 and 40 pups."));
          f.pups = n;
        }
        if (v[2]) f.mother = v[2];
        if (list.length && !litterBorn(list, f.date)) {
          f.another = v[3] === "another";
          if (!f.another && list.length > 1) f.litter = v[3];
        }
        return record("litter_born", ref("cage", c.id), f, saw("cage", c, ["date_give_birth"]));
      });
    },
    genotyping: function () {
      var c = cageNow(), list = littersOf(c);
      // Whose pups: the oldest litter not yet genotyped, unless chosen.
      var first = list.find(function (l) { return !l.litter; }) || list[0];
      // Its count and mother as recorded at Litter born, when there's no choosing.
      var known = list.length === 1 ? first : null;
      var fields = [{ label: t("How many pups"), type: "number", required: true, value: known && known.pups || "" },
                    { label: t("Father") }, { label: t("Mother"), value: known && known.dam || "" }];
      if (list.length > 1) fields.push(whichLitter(list, first));
      ask(t("Genotyping: the pups in cage %(cage)s", { cage: c.cage_id }), fields, function (v) {
        var n = parseInt(v[0], 10);
        if (!(n >= 1 && n <= 40)) throw new Error(t("Between 1 and 40 pups."));
        var pups = []; for (var i = 0; i < n; i++) pups.push(tmp());
        var fields = { pups: n };
        if (v[1]) fields.father = v[1];
        if (v[2]) fields.mother = v[2];
        if (list.length > 1) fields.litter = v[3];
        return record("litter_genotyping", ref("cage", c.id), fields, saw("cage", c, []), { litter: [tmp()], mouse: pups });
      });
    },
    wean: function () {
      var c = cageNow(), list = littersOf(c);
      ask(t("Wean cage %(cage)s", { cage: c.cage_id }), list.length > 1 ? [whichLitter(list, list[0])] : [], function (v) {
        return record("wean", ref("cage", c.id), list.length > 1 ? { litter: v[0] } : {}, saw("cage", c, []));
      });
    },
    flip: function (id) {
      var u = byId(view.stock_units, Number(id));
      return record("stock_event", ref("stock_unit", u.id), { event: "copy" }, saw("stock_unit", u, []));
    },
    discard: function (id) {
      var u = byId(view.stock_units, Number(id));
      ask(t("Discard No. %(n)s?", { n: u.number }), [], function () {
        return record("stock_event", ref("stock_unit", u.id), { event: "discard" }, saw("stock_unit", u, ["active"]));
      });
    },
    "tank-note": function (id) {
      var x = byId(view.tanks, Number(id));
      ask(t("Note on tank %(tank)s", { tank: x.tank_id }), [{ label: t("What to add"), required: true }], function (v) {
        var notes = x.notes ? x.notes + "; " + v[0] : v[0];
        return record("tank_change", ref("tank", x.id), { notes: notes }, saw("tank", x, ["notes"]));
      });
    }
  };

  // ------------------------------------------------------------ events
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-open-cage], [data-act], [data-home], [data-tab]");
    if (!el) return;
    if (el.dataset.tab) { tab = el.dataset.tab; document.getElementById("find").value = ""; location.hash = ""; render(); return; }
    if (el.hasAttribute("data-home")) { location.hash = ""; return; }
    if (el.dataset.openCage) {
      hit = el.dataset.hit || null;
      document.getElementById("find").value = "";
      location.hash = "cage/" + el.dataset.openCage;
      return;
    }
    var act = ACTS[el.dataset.act];
    if (act) Promise.resolve(act(el.dataset.id)).catch(function (err) { note(err.message); });
  });
  document.addEventListener("change", function (e) {
    var box = e.target.closest("[data-task]");
    if (!box || !box.checked) return;
    var raw = box.dataset.task, id = /^\d+$/.test(raw) ? Number(raw) : raw;
    record("task_done", ref("task", id), {}, {}).catch(function (err) { box.checked = false; note(err.message); });
  });
  document.getElementById("find").addEventListener("input", function () { location.hash = ""; render(); });
  // A card scanned in the phone app: its link names the cage (…#cage-12).
  window.bmScanned = function (value) {
    var link = String(value || "").match(/#cage-(\d+)/);
    if (link) { document.getElementById("find").value = ""; location.hash = "cage/" + link[1]; return; }
    document.getElementById("find").value = String(value || "");
    location.hash = "";
    render();
  };
  window.addEventListener("hashchange", render);

  // Back online: send what waits, then go back to the lab's own pages.
  var retry = document.getElementById("retry");
  retry.onclick = function () { check(true); };
  function check(byHand) {
    retry.disabled = true;
    return store.status().then(function (s) {
      if (!s.online) {
        if (byHand) note(t("The lab's server still can't be reached. Everything you do here is kept and sent later."));
        return;
      }
      note(t("Back online: sending what waits…"));
      return store.sync().then(function (answer) {
        status = answer.status || status;
        return store.load().then(function (data) {
          snap = data.snapshot; queue = data.queue || []; status = data.status || status;
          rebuild(); render();
          if (!queue.length) {
            var el = document.getElementById("online-note");
            el.hidden = false;
            el.innerHTML = esc(t("Back online, and the lab has everything done here.")) +
              (status.held ? " " + esc(t("%(n)s changes need your decision.", { n: status.held })) : "") +
              ' <a href="#" id="go-back">' + esc(t("Open the lab")) + "</a>";
            document.getElementById("go-back").onclick = function (e) { e.preventDefault(); store.backOnline(); };
          }
        });
      });
    }).catch(function () {}).then(function () { retry.disabled = false; });
  }
  function note(text) {
    var el = document.getElementById("online-note");
    el.hidden = false;
    el.textContent = text;
  }

  // ------------------------------------------------------------ start
  store.strings().catch(function () { return {}; }).then(function (s) {
    WORDS = (s && s.words) || {}; LANG = (s && s.lang) || "en";
    translatePage();
    return store.load();
  }).then(function (data) {
    snap = data.snapshot; queue = data.queue || []; status = data.status || {};
    rebuild();
    var link = from.match(/#cage-(\d+)/);
    if (link && !location.hash) location.hash = "cage/" + link[1];
    render();
    setInterval(function () { check(false); }, 15000);
  }).catch(function (err) {
    main.innerHTML = '<p class="empty">' + esc(t("This device has no copy of the lab yet.")) + " " + esc(err.message) + "</p>";
  });
})();
