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
      // carries `icon`.
      var icons = o.kind === "prompt" && o.icons && o.icons.choices && o.icons.choices.length ? o.icons : null;
      if (icons) {
        var set = el("fieldset", "icon-picker record-wide");
        set.appendChild(el("legend", null, icons.label || t("Icon")));
        var grid = el("div", "icon-picker-grid");
        icons.choices.forEach(function (name, i) {
          var choice = el("label", "icon-picker-choice");
          choice.title = name;
          var radio = el("input");
          radio.type = "radio";
          radio.name = "bio-dialog-icon";
          radio.value = name;
          radio.setAttribute("aria-label", name);
          radio.checked = icons.value ? name === icons.value : i === 0;
          choice.appendChild(radio);
          choice.insertAdjacentHTML("beforeend", '<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#' + name.replace(/[^a-z0-9-]/g, "") + '"></use></svg>');
          grid.appendChild(choice);
        });
        set.appendChild(grid);
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
