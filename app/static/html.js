/* Text put into HTML a page script builds: & < > " ' as entities, so a name,
 * a note or anything typed shows as written and never as markup (in an
 * attribute too, whichever quotes it uses). null and undefined are empty.
 * Loaded first on every page (base.html, server_setup.html):
 *
 *   const esc = BioHtml.escape;
 *   el.innerHTML = `<span title="${esc(note)}">${esc(name)}</span>`;
 */
(function () {
  'use strict';
  const ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function escape(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ENTITIES[c]);
  }
  if (typeof window !== 'undefined') window.BioHtml = { escape };
  if (typeof module !== 'undefined' && module.exports) module.exports = { escape };
})();
