/* In-app dialogs in place of the browser's confirm(), prompt() and alert().
 *
 *   BioDialog.confirm('Delete V12?', { danger: true }).then((ok) => …)
 *   BioDialog.prompt('Name this version:', '').then((text) => …)   // null when cancelled
 *   BioDialog.prompt('Name', '', { checks: [{ name: 'lab', label: 'Share' }] })
 *     .then((answer) => …)   // { value, checks: { lab: true } }, or null
 *   BioDialog.prompt('Name', '', { icons: { choices: ['folder', 'dna'], value: 'dna' } })
 *     .then((answer) => …)   // { value, checks: {}, icon: 'dna' }, or null
 *     (the page loads static/icon-picker.css for the grid)
 *   BioDialog.alert('The picture could not be uploaded.')
 *
 * The browser's own dialogs look like the browser, not the app; they can't
 * say which button is the dangerous one; and embedded browsers (the
 * desktop app's web view, apps that open links in-app) often don't show
 * them at all: prompt() throws, confirm() answers "no" without asking, so
 * the button seems to do nothing. These are <dialog>s like the rest of the
 * app, and each call returns a promise.
 *
 * Loaded in <head> after actions.js, so every page script may use it.
 */
(function () {
  "use strict";

  var queue = Promise.resolve();

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function open(options) {
    // One at a time: a second question waits for the first to be answered.
    var run = queue.then(function () { return show(options); });
    queue = run.catch(function () {});
    return run;
  }

  function show(o) {
    return new Promise(function (resolve) {
      var dialog = el("dialog", "record-dialog record-dialog-narrow bio-dialog");
      dialog.setAttribute("aria-labelledby", "bio-dialog-title");
      var form = el("form");
      form.method = "dialog";
      var head = el("header", "record-dialog-head");
      var title = el("h3", null, o.title || (o.kind === "alert" ? "BioManager" : o.kind === "prompt" ? "" : t("Are you sure?")));
      title.id = "bio-dialog-title";
      head.appendChild(el("div")).appendChild(title);
      var body = el("div", "record-dialog-body bio-dialog-body");
      String(o.message || "").split(/\n{2,}/).forEach(function (para) {
        if (para.trim()) body.appendChild(el("p", "bio-dialog-text", para.trim()));
      });
      var input = null;
      if (o.kind === "prompt") {
        var label = el("label", "record-wide");
        input = el("input");
        input.name = "value";
        input.autocomplete = "off";
        input.value = o.value == null ? "" : String(o.value);
        if (o.placeholder) input.placeholder = o.placeholder;
        label.appendChild(input);
        body.appendChild(label);
      }
      // Ticks under a prompt ({ checks: [{ name, label, checked }] }): the
      // answer is then { value, checks: { name: true|false } }.
      var boxes = (o.kind === "prompt" && o.checks) || [];
      boxes.forEach(function (c) {
        var row = el("label", "record-wide bio-dialog-check");
        var box = el("input");
        box.type = "checkbox";
        box.name = c.name;
        box.checked = !!c.checked;
        row.appendChild(box);
        row.appendChild(el("span", null, c.label));
        if (c.hint) row.appendChild(el("small", "bio-dialog-hint", c.hint));
        body.appendChild(row);
      });
      // An icon to pick under a prompt ({ icons: { choices, value, label } }),
      // drawn as templates/_icon_picker.html draws one: the answer then
      // carries `icon`. With `emoji` (a list) and `uploadUrl`, it also offers
      // an emoji, the computer's own picker for any other, and an SVG of
      // one's own (a notebook topic's, app/topic_icons.py): "emoji:🧪",
      // "svg:uploads/…".
      var icons = o.kind === "prompt" && o.icons && o.icons.choices && o.icons.choices.length ? o.icons : null;
      if (icons) {
        var set = el("fieldset", "icon-picker record-wide");
        set.appendChild(el("legend", null, icons.label || t("Icon")));
        var chosen = icons.value || icons.choices[0];
        var iconNode = function (v) {
          if (v.indexOf("emoji:") === 0) return el("span", "icon-picker-emoji", v.slice(6));
          if (v.indexOf("svg:uploads/") === 0) {
            var img = el("img", "icon-picker-svg");
            img.src = "/static/" + v.slice(4);
            img.alt = "";
            return img;
          }
          var holder = el("span");
          holder.innerHTML = '<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#' + v.replace(/[^a-z0-9-]/g, "") + '"></use></svg>';
          return holder.firstChild;
        };
        var choiceEl = function (v) {
          var choice = el("label", "icon-picker-choice");
          var name = v.indexOf("emoji:") === 0 ? v.slice(6) : v.indexOf("svg:") === 0 ? t("Your SVG") : v;
          choice.title = name;
          var radio = el("input");
          radio.type = "radio";
          radio.name = "bio-dialog-icon";
          radio.value = v;
          radio.setAttribute("aria-label", name);
          radio.checked = v === chosen;
          choice.appendChild(radio);
          choice.appendChild(iconNode(v));
          return choice;
        };
        var gridOf = function (values) {
          var g = el("div", "icon-picker-grid");
          values.forEach(function (v) { g.appendChild(choiceEl(v)); });
          return g;
        };
        if (!chosen || icons.choices.indexOf(chosen) < 0 && chosen.indexOf(":") < 0) chosen = icons.choices[0];
        var builtIn = gridOf(icons.choices);
        if (!icons.emoji && !icons.uploadUrl) {
          set.appendChild(builtIn);
        } else {
          // Emoji: a few for the bench, and any other from the computer's own picker.
          var emojiValues = (icons.emoji || []).map(function (e) { return "emoji:" + e; });
          if (chosen.indexOf("emoji:") === 0 && emojiValues.indexOf(chosen) < 0) emojiValues.unshift(chosen);
          var emojiGrid = gridOf(emojiValues);
          var emojiPane = el("div", "icon-picker-pane");
          var own = el("label", "icon-picker-own");
          own.appendChild(el("span", null, t("Any emoji")));
          var ownInput = el("input");
          ownInput.type = "text";
          ownInput.maxLength = 16;
          ownInput.placeholder = "🙂";
          ownInput.setAttribute("autocomplete", "off");
          own.appendChild(ownInput);
          ownInput.addEventListener("input", function () {
            var v = ownInput.value.trim();
            if (!v) return;
            var value = "emoji:" + v;
            var radio = Array.prototype.find.call(emojiGrid.querySelectorAll("input"), function (r) { return r.value === value; });
            if (!radio) {
              var c = choiceEl(value);
              emojiGrid.insertBefore(c, emojiGrid.firstChild);
              radio = c.querySelector("input");
            }
            radio.checked = true;
          });
          var mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
          emojiPane.appendChild(emojiGrid);
          emojiPane.appendChild(own);
          emojiPane.appendChild(el("small", "icon-picker-hint", mac ? t("Control-Command-Space opens the Mac's own emoji.") : t("Windows key + . opens the emoji panel (Control-Command-Space on a Mac).")));
          // An SVG of one's own: cleaned on the server, then one of the choices.
          var svgPane = el("div", "icon-picker-pane");
          var svgGrid = gridOf(chosen.indexOf("svg:") === 0 ? [chosen] : []);
          var file = el("input");
          file.type = "file";
          file.accept = ".svg,image/svg+xml";
          file.hidden = true;
          var pick = el("button", "btn btn-sm", t("Upload an SVG…"));
          pick.type = "button";
          var problem = el("small", "icon-picker-error");
          pick.addEventListener("click", function () { file.click(); });
          file.addEventListener("change", function () {
            if (!file.files.length) return;
            var body = new FormData();
            body.append("svg", file.files[0]);
            pick.disabled = true;
            fetch(icons.uploadUrl, { method: "POST", body: body, headers: { "X-Requested-With": "fetch" } })
              .then(function (r) { return r.json(); })
              .then(function (d) {
                if (!d.ok) { problem.textContent = d.error || t("That SVG couldn't be used."); return; }
                problem.textContent = "";
                var c = choiceEl(d.icon);
                svgGrid.appendChild(c);
                c.querySelector("input").checked = true;
              })
              .catch(function () { problem.textContent = t("That SVG couldn't be used."); })
              .then(function () { pick.disabled = false; file.value = ""; });
          });
          svgPane.appendChild(svgGrid);
          svgPane.appendChild(pick);
          svgPane.appendChild(file);
          svgPane.appendChild(el("small", "icon-picker-hint", t("A drawing in an .svg file, up to 100 KB. Scripts and links in it are taken out.")));
          svgPane.appendChild(problem);
          // Icons · Emoji · SVG, starting on the kind already chosen.
          var panes = [["icons", t("Icons"), builtIn], ["emoji", t("Emoji"), emojiPane], ["svg", "SVG", svgPane]];
          if (!icons.uploadUrl) panes.pop();
          var bar = el("div", "seg seg-sm icon-picker-tabs");
          bar.setAttribute("role", "group");
          bar.setAttribute("aria-label", icons.label || t("Icon"));
          var start = chosen.indexOf("emoji:") === 0 ? "emoji" : chosen.indexOf("svg:") === 0 ? "svg" : "icons";
          var showPane = function (key) {
            panes.forEach(function (p) {
              p[2].hidden = p[0] !== key;
              p[3].classList.toggle("is-active", p[0] === key);
              p[3].setAttribute("aria-pressed", String(p[0] === key));
            });
          };
          panes.forEach(function (p) {
            var b = el("button", "seg-item", p[1]);
            b.type = "button";
            b.addEventListener("click", function () { showPane(p[0]); });
            p.push(b);
            bar.appendChild(b);
          });
          set.appendChild(bar);
          panes.forEach(function (p) { set.appendChild(p[2]); });
          showPane(start);
        }
        body.appendChild(set);
      }
      var foot = el("footer", "record-dialog-foot");
      var cancel = null;
      if (o.kind !== "alert") {
        cancel = el("button", "btn", o.cancelLabel || t("Cancel"));
        cancel.type = "button";
        foot.appendChild(cancel);
      }
      var ok = el("button", "btn " + (o.danger ? "btn-danger" : "btn-primary"), o.okLabel || t("OK"));
      ok.type = "submit";
      foot.appendChild(ok);
      if (title.textContent) form.appendChild(head);
      form.appendChild(body);
      form.appendChild(foot);
      dialog.appendChild(form);
      document.body.appendChild(dialog);

      var before = document.activeElement;
      var done = false;
      function finish(answer) {
        if (done) return;
        done = true;
        if (dialog.open) dialog.close();
        dialog.remove();
        if (before && typeof before.focus === "function" && document.contains(before)) {
          try { before.focus({ preventScroll: true }); } catch (_) {}
        }
        resolve(answer);
      }
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (o.kind === "prompt" && (boxes.length || icons)) {
          var checks = {};
          boxes.forEach(function (c) { checks[c.name] = form.elements[c.name].checked; });
          var picked = form.querySelector('input[name="bio-dialog-icon"]:checked');
          var answer = { value: input.value, checks: checks };
          if (icons) answer.icon = picked ? picked.value : null;
          finish(answer);
        } else finish(o.kind === "prompt" ? input.value : true);
      });
      if (cancel) cancel.addEventListener("click", function () { finish(o.kind === "prompt" ? null : false); });
      // Esc, or the dialog closed some other way, is "Cancel".
      dialog.addEventListener("close", function () { finish(o.kind === "prompt" ? null : o.kind === "alert" ? undefined : false); });

      dialog.showModal();
      if (input) { input.focus(); input.select(); }
      else (o.danger && cancel ? cancel : ok).focus();
    });
  }

  window.BioDialog = {
    confirm: function (message, options) {
      var o = Object.assign({ kind: "confirm", message: message }, options || {});
      // "Delete reagent #7?" gets a "Delete" button, not "OK".
      var verb = /^(Delete|Remove|Revoke|Retire|Discard|Disable|Clear|End|Stop|Disconnect|Cancel this)\b/.exec(String(message || ""));
      if (!o.okLabel && o.danger && verb) o.okLabel = t(verb[1] === "Cancel this" ? "Cancel it" : verb[1]);
      // The same, for a question asked in Chinese ("删除 V12？" gets 删除).
      var zhVerb = /^(删除|移除|撤销|清空|清除|丢弃|停用|结束|停止|断开)/.exec(String(message || ""));
      if (!o.okLabel && o.danger && zhVerb) o.okLabel = zhVerb[1];
      return open(o);
    },
    prompt: function (message, value, options) {
      return open(Object.assign({ kind: "prompt", title: message, value: value }, options || {}));
    },
    alert: function (message, options) {
      return open(Object.assign({ kind: "alert", message: message }, options || {}));
    },
    // A question that deletes, removes or ends something gets a red button.
    looksDestructive: function (text) {
      return /\b(delete|remove|revoke|retire|sac|end now|discard|disable|clear|cancel this)\b/i.test(text || "") ||
        /删除|移除|吊销|处死|立即结束|丢弃|停用|清空|清除|取消这/.test(text || "");
    },
  };
})();
