/* A sheet's row forms, put on the page when they are needed.
 *
 * Each row of a sheet saves through a form of its own, outside the table,
 * that its cells join with form="…". Sent as part of the page, a thousand
 * such forms made Chromium (Chrome, Edge, the Windows app) look through
 * the whole page once for each form's fields as it loaded: seconds on a
 * long sheet. So a sheet sends them inside
 *
 *   <template data-row-forms> <form id="mouse-update-12" …> … </template>
 *
 * and a row's form joins the page, just before its template, when one of
 * its cells is touched or focused, or when a script asks for it:
 *
 *   BioRowForms.get('mouse-update-12')   // the form, on the page; null if none
 *
 * Use it wherever a row's form is looked up by id. Its cells are found as
 * before, by [form="…"], whether or not the form has joined yet.
 */
(function () {
  'use strict';

  function get(id) {
    if (!id) return null;
    const onPage = document.getElementById(id);
    if (onPage) return onPage;
    for (const template of document.querySelectorAll('template[data-row-forms]')) {
      const form = template.content.getElementById(id);
      if (form) {
        template.before(form);
        return form;
      }
    }
    return null;
  }

  // Before anything acts on a cell: a click, a tap, a key, focus.
  ['pointerdown', 'mousedown', 'touchstart', 'focusin', 'keydown', 'paste'].forEach((type) => {
    document.addEventListener(type, (event) => {
      const el = event.target;
      const id = el && el.getAttribute && el.getAttribute('form');
      if (id && !document.getElementById(id)) get(id);
    }, { capture: true, passive: true });
  });

  window.BioRowForms = { get };
})();
