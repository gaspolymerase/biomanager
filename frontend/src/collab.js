// Live editing over plain HTTP.
//
// Every open editor holds the page as a Yjs document. Changes are sent to
// /notebook/api/pages/<id>/sync as Yjs updates (base64) and fetched back by
// polling, so this works on the app's ordinary Flask server with no
// websocket. Yjs merges updates in any order, so two people typing in the
// same paragraph both keep what they wrote.
//
// Cursors travel the same way: the Yjs awareness state (name, colour,
// selection) rides along with each push, and peers' states come back with
// each poll.
//
// The server numbers its editing state with a generation. When the text is
// replaced from outside the editor (a version restored), the generation
// moves on and every editor starts again from the saved text (onReset).

import * as Y from 'yjs';
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate, removeAwarenessStates } from 'y-protocols/awareness';
import { base64ToBytes, bytesToBase64 } from './util.js';

const FAST = 1200;     // someone else is here
const SLOW = 4000;     // alone on the page
const HIDDEN = 15000;  // the tab is in the background
const COMPACT_AFTER = 400;

function randomId() {
  const bytes = new Uint8Array(9);
  crypto.getRandomValues(bytes);
  return bytesToBase64(bytes).replace(/[+/=]/g, '');
}

// Which edits this document holds (a Yjs state vector, base64), sent with a
// save of the page's Markdown: the server keeps the text that holds the most.
export function stateOf(doc) {
  return bytesToBase64(Y.encodeStateVector(doc));
}

export class HttpSyncProvider {
  constructor({ pageId, canEdit, user, onReset, onPeers, onStatus, onMeta }) {
    this.pageId = pageId;
    this.canEdit = canEdit;
    this.user = user;
    this.onReset = onReset || (() => {});
    this.onPeers = onPeers || (() => {});
    this.onStatus = onStatus || (() => {});
    this.onMeta = onMeta || (() => {});
    this.client = randomId();
    this.doc = new Y.Doc();
    this.awareness = new Awareness(this.doc);
    this.gen = -1;
    this.last = 0;
    this.pending = [];
    this.awarenessDirty = true;
    this.peers = [];
    this.peerClients = new Map(); // server client id -> Set of Yjs client ids
    this.sinceCompact = 0;
    this.stopped = false;
    this.pushing = null;
    this.timer = null;
    this.base = `/notebook/api/pages/${pageId}/sync`;
    // Lines the server handed this editor to add (an assistant's note on a
    // page that is open live): the editor adds them once it is ready.
    this.inserts = [];
    this.onInserts = null;

    this._onDocUpdate = (update, origin) => {
      if (origin === this) return;
      this.pending.push(update);
      this.sinceCompact += 1;
      this._schedulePush(250);
    };
    this._onAwareness = ({ added, updated, removed }, origin) => {
      if (origin !== 'local') return;
      const mine = this.doc.clientID;
      if ([...added, ...updated, ...removed].includes(mine)) {
        this.awarenessDirty = true;
        this._schedulePush(800);
      }
    };
    this._onVisibility = () => { if (!document.hidden) this._schedulePoll(50); };
    this._onUnload = () => this._leave();
  }

  // Fetch everything there is. Resolves with { empty } — empty means no one
  // has opened this page in the live editor yet, so the caller seeds it
  // from the saved markdown (seed()).
  async connect() {
    const first = await this._pull(true);
    this.doc.on('update', this._onDocUpdate);
    this.awareness.on('update', this._onAwareness);
    document.addEventListener('visibilitychange', this._onVisibility);
    window.addEventListener('pagehide', this._onUnload);
    return { empty: first.empty };
  }

  // Send the whole document as the first state. Resolves false when someone
  // else seeded it first; the caller then starts again (their state wins).
  async seed() {
    const state = Y.encodeStateAsUpdate(this.doc);
    this.pending = [];
    try {
      const r = await this._post(this.base, { client: this.client, gen: this.gen, init: true, updates: [bytesToBase64(state)] });
      if (r.status === 409) return false;
      const data = await r.json();
      if (data.last) this.last = Math.max(this.last, data.last);
      return true;
    } catch (_e) {
      return false;
    }
  }

  startLoop() {
    this._schedulePoll(FAST);
    this._schedulePush(0);
  }

  setUser(user) {
    this.user = user;
    this.awareness.setLocalStateField('user', user);
  }

  destroy() {
    this.stopped = true;
    clearTimeout(this.timer);
    clearTimeout(this.pushTimer);
    this.doc.off('update', this._onDocUpdate);
    this.awareness.off('update', this._onAwareness);
    document.removeEventListener('visibilitychange', this._onVisibility);
    window.removeEventListener('pagehide', this._onUnload);
    this._leave();
    this.awareness.destroy();
  }

  // Hand the waiting lines to the editor, then tell the server they are in.
  drainInserts() {
    if (!this.onInserts || !this.inserts.length || this.stopped) return;
    const batch = this.inserts;
    this.inserts = [];
    this.onInserts(batch);
    this._post(`/notebook/api/pages/${this.pageId}/inserts/done`, { client: this.client, ids: batch.map((i) => i.id) })
      .catch(() => {});
  }

  hasUnsent() {
    return this.pending.length > 0 || !!this.pushing;
  }

  // ---------------------------------------------------------------- internals

  _post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), keepalive: true });
  }

  _leave() {
    try {
      const body = JSON.stringify({ client: this.client, leaving: true });
      if (navigator.sendBeacon) navigator.sendBeacon(this.base, new Blob([body], { type: 'application/json' }));
    } catch (_e) { /* the presence row times out on its own */ }
  }

  _interval() {
    if (document.hidden) return HIDDEN;
    return this.peers.length ? FAST : SLOW;
  }

  _schedulePoll(delay) {
    if (this.stopped) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(async () => {
      try {
        await this._pull(false);
        this.onStatus('online');
      } catch (_e) {
        this.onStatus('offline');
      }
      this._schedulePoll(this._interval());
    }, delay ?? this._interval());
  }

  _schedulePush(delay) {
    if (this.stopped) return;
    clearTimeout(this.pushTimer);
    this.pushTimer = setTimeout(() => this._push(), delay);
  }

  async _pull(initial) {
    let more = true;
    let empty = false;
    while (more && !this.stopped) {
      const url = `${this.base}?since=${this.last}&gen=${this.gen}&client=${encodeURIComponent(this.client)}`;
      const r = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      if (data.reset && !initial && this.gen !== -1) {
        this.stopped = true;
        this.onReset(data.gen);
        return { empty: false };
      }
      this.gen = data.gen;
      empty = data.empty;
      if (data.updates.length) {
        Y.transact(this.doc, () => {
          for (const u of data.updates) Y.applyUpdate(this.doc, base64ToBytes(u.data), this);
        }, this);
        this.sinceCompact += data.updates.length;
      }
      this.last = data.last;
      more = data.more;
      if (data.inserts && data.inserts.length) {
        const known = new Set(this.inserts.map((i) => i.id));
        this.inserts.push(...data.inserts.filter((i) => !known.has(i.id)));
        this.drainInserts();
      }
      this._applyPeers(data.peers || []);
      this.onMeta({ title: data.title, updated_at: data.updated_at, role: data.role });
    }
    if (!initial && this.sinceCompact > COMPACT_AFTER) this._maybeCompact();
    return { empty };
  }

  _applyPeers(peers) {
    const seen = new Set();
    for (const peer of peers) {
      seen.add(peer.client);
      if (!peer.state) continue;
      const before = new Set(this.awareness.getStates().keys());
      try {
        applyAwarenessUpdate(this.awareness, base64ToBytes(peer.state), 'remote');
      } catch (_e) { continue; }
      const ids = this.peerClients.get(peer.client) || new Set();
      for (const id of this.awareness.getStates().keys()) {
        if (!before.has(id) && id !== this.doc.clientID) ids.add(id);
      }
      this.peerClients.set(peer.client, ids);
    }
    for (const [client, ids] of [...this.peerClients.entries()]) {
      if (!seen.has(client)) {
        removeAwarenessStates(this.awareness, [...ids], 'remote');
        this.peerClients.delete(client);
      }
    }
    // One entry per person, however many tabs they have open.
    const byUser = new Map();
    for (const peer of peers) if (!byUser.has(peer.username)) byUser.set(peer.username, peer);
    const changed = JSON.stringify([...byUser.keys()]) !== JSON.stringify(this.peers.map((p) => p.username));
    this.peers = [...byUser.values()];
    if (changed) this.onPeers(this.peers);
  }

  async _push() {
    if (this.stopped) return;
    if (this.pushing) { this._schedulePush(300); return; }
    const updates = this.canEdit ? this.pending.splice(0) : [];
    if (!this.canEdit) this.pending = [];
    if (!updates.length && !this.awarenessDirty) return;
    const body = { client: this.client, gen: this.gen, updates: [] };
    if (updates.length) body.updates = [bytesToBase64(Y.mergeUpdates(updates))];
    if (this.awarenessDirty) {
      body.awareness = bytesToBase64(encodeAwarenessUpdate(this.awareness, [this.doc.clientID]));
      this.awarenessDirty = false;
    }
    this.pushing = this._post(this.base, body).then(async (r) => {
      if (r.status === 409) {
        const data = await r.json().catch(() => ({}));
        if (data.reset) { this.stopped = true; this.onReset(data.gen); }
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      this.onStatus('online');
    }).catch(() => {
      // Keep what was not sent and try again.
      if (updates.length) this.pending.unshift(...updates);
      this.onStatus('offline');
      this._schedulePush(3000);
    }).finally(() => { this.pushing = null; });
    await this.pushing;
  }

  async _maybeCompact() {
    // Only the person whose id sorts first compacts, so two editors do not
    // both do it at once.
    const others = this.peers.map((p) => p.client);
    if (others.some((c) => c < this.client) || this.hasUnsent() || !this.canEdit) return;
    this.sinceCompact = 0;
    try {
      await this._post(`${this.base}/compact`, {
        client: this.client, gen: this.gen, upto: this.last,
        state: bytesToBase64(Y.encodeStateAsUpdate(this.doc)),
      });
    } catch (_e) { /* try again later */ }
  }
}
