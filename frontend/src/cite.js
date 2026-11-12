// A paper cited the way papers cite: its first author's surname and its
// year, "Wang et al., 2024", from a reading note's Authors and Year
// (lab_notebook.KIND_FIELDS). A citation in a page is a link to the paper's
// note (extensions/PageLinks.js, /cite) that shows these words.
//
// Authors come as the app keeps them, "Xiaoming Wang; Hua Li" (Zotero, a
// DOI, BibTeX and RIS all give this), or as people type them: "Wang X, Li Y",
// "Wang, Xiaoming", "Wang and Li".
//
//   1 author   (Wang, 2024)
//   2 authors  (Wang & Li, 2024)
//   3 or more  (Wang et al., 2024)
//
// A Chinese name is cited whole, as GB/T 7714 does: (王小明 等, 2024).

const CJK = /[㐀-鿿豈-﫿]/;
const PARTICLES = new Set(['van', 'von', 'de', 'der', 'den', 'del', 'della', 'di', 'da', 'du', 'dos', 'das', 'le', 'la', 'ten', 'ter', 'zu', 'af', 'bin', 'al', "van't"]);
const SUFFIX = /^(?:jr|sr|ii|iii|iv|v)\.?$/i;
const INITIALS = /^(?:[A-Z]\.?-?){1,3}$/;
const NOT_A_NAME = /^(?:…|\.\.\.|et\.? al\.?|others)$/i;

// The people named in an Authors field, in order.
export function splitAuthors(authors) {
  const text = String(authors || '').replace(/\s+/g, ' ').trim();
  if (!text) return [];
  let names;
  if (text.includes(';')) names = text.split(';');
  else {
    names = text.split(/\s*(?:,\s*)?\b(?:and)\b\s*|\s*&\s*|、|，/);
    // "Wang X, Li Y" lists people; "Wang, Xiaoming" is one person.
    if (names.length === 1) {
      const parts = text.split(',');
      if (parts.length > 1 && parts.every((p) => p.trim().includes(' '))) names = parts;
    } else {
      // "Wang X, Li Y and Chen Z": the part before "and" may list several.
      names = names.flatMap((n) => {
        const parts = n.split(',');
        return parts.length > 1 && parts.every((p) => p.trim().includes(' ')) ? parts : [n];
      });
    }
  }
  return names.map((n) => n.trim().replace(/,+$/, '')).filter((n) => n && !NOT_A_NAME.test(n));
}

// The name a citation gives one author: the surname, or a Chinese name whole.
export function surname(name) {
  const text = String(name || '').trim();
  if (!text) return '';
  if (CJK.test(text)) {
    if (text.includes(',')) {
      const [family, given] = text.split(',').map((s) => s.trim());
      return family + (CJK.test(given || '') ? given : '');
    }
    const words = text.split(/\s+/);
    // Zotero gives "小明 王" (first name, last name): the family name leads.
    return words.length > 1 ? words[words.length - 1] + words.slice(0, -1).join('') : text;
  }
  if (text.includes(',')) {
    const family = text.split(',')[0].trim();
    if (family && !SUFFIX.test(family)) return family;
  }
  const words = text.split(/\s+/).filter((w) => !SUFFIX.test(w.replace(/,$/, '')));
  if (words.length === 1) return words[0];
  // "Wang XM", "Smith J.": the surname first, initials after.
  if (INITIALS.test(words[words.length - 1]) && !INITIALS.test(words[0])) return words.slice(0, -1).filter((w) => !INITIALS.test(w)).join(' ');
  let at = words.length - 1;
  while (at > 1 && PARTICLES.has(words[at - 1].toLowerCase())) at -= 1;
  if (at === 1 && PARTICLES.has(words[0].toLowerCase()) && words.length > 2) at = 0;
  return words.slice(at).join(' ');
}

export function citeYear(year) {
  const found = String(year || '').match(/\d{4}/);
  return found ? found[0] : 'n.d.';
}

function shortTitle(title) {
  const words = String(title || '').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return 'Untitled page';
  if (CJK.test(title)) return title.length > 16 ? `${title.slice(0, 15).trim()}…` : title.trim();
  return words.length > 4 ? `${words.slice(0, 4).join(' ')}…` : words.join(' ');
}

// "Wang et al., 2024" for a reading note's fields; a note with no authors is
// cited by the start of its title.
export function citeLabel(fields = {}, title = '') {
  const names = splitAuthors(fields.authors).map(surname).filter(Boolean);
  const year = citeYear(fields.year);
  if (!names.length) return `${shortTitle(title)}, ${year}`;
  const first = names[0];
  if (names.length === 1) return `${first}, ${year}`;
  if (CJK.test(first)) return `${first} 等, ${year}`;
  if (names.length === 2) return `${first} & ${names[1]}, ${year}`;
  return `${first} et al., ${year}`;
}

// The words a citation shows and its Markdown keeps: "(Wang et al., 2024)".
export const citeText = (fields, title) => `(${citeLabel(fields, title)})`;
