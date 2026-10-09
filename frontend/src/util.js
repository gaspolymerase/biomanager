// Small helpers shared by the notebook's blocks and panels.

export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

// wrapped.flush() runs a call that is still waiting (and nothing if none
// is); wrapped.now() runs at once either way.
export function debounce(fn, delay) {
  let t = null;
  let lastArgs = [];
  const wrapped = (...args) => {
    lastArgs = args;
    clearTimeout(t);
    t = setTimeout(() => { t = null; fn(...lastArgs); }, delay);
  };
  wrapped.cancel = () => { clearTimeout(t); t = null; };
  wrapped.pending = () => t !== null;
  wrapped.flush = () => { if (t !== null) { clearTimeout(t); t = null; fn(...lastArgs); } };
  wrapped.now = (...args) => { clearTimeout(t); t = null; fn(...(args.length ? args : lastArgs)); };
  return wrapped;
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

export function toNumber(v) {
  if (v === null || v === undefined) return NaN;
  if (typeof v === 'number') return v;
  const s = String(v).trim().replace(/,/g, '').replace(/−/g, '-');
  if (s === '') return NaN;
  return Number(s);
}

export function isNum(v) {
  return Number.isFinite(toNumber(v));
}

// Up to `sig` significant figures, without trailing zeros or exponent noise.
export function fmt(n, sig = 4) {
  if (!Number.isFinite(n)) return '—';
  if (n === 0) return '0';
  const abs = Math.abs(n);
  if (abs >= 1e6 || abs < 1e-4) return n.toExponential(Math.max(0, sig - 1)).replace(/\.?0+e/, 'e');
  const digits = Math.max(0, sig - 1 - Math.floor(Math.log10(abs)));
  return Number(n.toFixed(Math.min(digits, 10))).toLocaleString(undefined, { maximumFractionDigits: Math.min(digits, 10) });
}

export function fmtP(p) {
  if (!Number.isFinite(p)) return '—';
  if (p < 0.0001) return '< 0.0001';
  return p < 0.001 ? p.toFixed(4) : p.toFixed(3);
}

export function stars(p) {
  if (!Number.isFinite(p)) return '';
  if (p < 0.0001) return '****';
  if (p < 0.001) return '***';
  if (p < 0.01) return '**';
  if (p < 0.05) return '*';
  return 'ns';
}

export function download(filename, content, type = 'text/plain') {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function bytesToBase64(bytes) {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// Parse pasted spreadsheet text (tab separated, as Excel and Sheets copy) or
// CSV. Returns an array of rows of strings.
export function parseDelimited(text) {
  const clean = String(text || '').replace(/\r\n?/g, '\n').replace(/\n+$/, '');
  if (!clean) return [];
  const delimiter = clean.includes('\t') ? '\t' : (clean.split('\n')[0].includes(';') && !clean.includes(',') ? ';' : ',');
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < clean.length; i++) {
    const c = clean[i];
    if (quoted) {
      if (c === '"' && clean[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"' && field === '') quoted = true;
    else if (c === delimiter) { row.push(field); field = ''; }
    else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else field += c;
  }
  row.push(field);
  rows.push(row);
  return rows;
}

export function toCsv(rows) {
  return rows.map((r) => r.map((v) => {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }).join(',')).join('\n') + '\n';
}

export function clock(date = new Date()) {
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

export function isoDate(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export async function api(url, { method = 'GET', body, form } = {}) {
  const opts = { method, headers: {} };
  if (form) {
    opts.body = form;
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const response = await fetch(url, opts);
  let data = null;
  try { data = await response.json(); } catch (_e) { data = null; }
  if (!response.ok || !data || data.ok === false) {
    const error = new Error((data && data.error) || `HTTP ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

// Open one of the app's pages in a BioManager tab (the browser's own
// location where there is no tab bar).
// Pass the editor to save the page being left first.
export async function openInApp(url, editor = null) {
  const leave = editor && editor.storage.pageLink && editor.storage.pageLink.beforeLeave;
  if (leave) await Promise.race([leave(), new Promise((done) => setTimeout(done, 2500))]).catch(() => {});
  if (window.BiomanagerTabs && window.BiomanagerTabs.open) window.BiomanagerTabs.open(url);
  else window.location.href = url;
}

// A stable colour per person, for cursors and avatars.
const PEOPLE_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#c98500', '#d55181', '#008300', '#4a3aa7', '#e34948'];
export function personColor(name) {
  let h = 0;
  for (const ch of String(name || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PEOPLE_COLORS[h % PEOPLE_COLORS.length];
}

/* The app's own dialogs (static/dialogs.js), with the browser's as a
   fallback for a page without them. Each returns a promise. */
export const ask = {
  confirm: (message, options) => (window.BioDialog ? window.BioDialog.confirm(message, options) : Promise.resolve(window.confirm(message))),
  prompt: (message, value, options) => (window.BioDialog ? window.BioDialog.prompt(message, value, options) : Promise.resolve(window.prompt(message, value))),
  alert: (message) => (window.BioDialog ? window.BioDialog.alert(message) : Promise.resolve(window.alert(message))),
};

export function initials(name) {
  const parts = String(name || '?').trim().split(/\s+/);
  return ((parts[0] || '?')[0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}
