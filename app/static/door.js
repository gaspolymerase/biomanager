/* The pages before signing in (templates/door/): show a password, the
   accounts this browser remembers, a guest code in four parts, waiting to be
   approved, the lab taking shape as it is named, and Copy. */
(function () {
  'use strict';

  const tr = (text, values) => (typeof window.t === 'function' ? window.t(text, values) : text);

  // Show / Hide a password.
  document.querySelectorAll('[data-peek]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = button.parentElement.querySelector('input');
      input.type = input.type === 'password' ? 'text' : 'password';
      button.textContent = input.type === 'password' ? tr('Show') : tr('Hide');
      input.focus();
    });
  });

  /* ------------------------------------------- the accounts kept here */

  // Written by Home after a sign-in with "Remember me on this computer"
  // (templates/base.html): names only, never a password.
  const KEY = 'biomanager:accounts';
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || '[]').filter((a) => a && a.u); } catch (_) { return []; } };
  const COLORS = ['#17A38F', '#7C5CC4', '#D9822B', '#2F7FD8', '#C2456B', '#4F8A3A'];
  const colorFor = (name) => COLORS[[...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % COLORS.length];
  const initialsOf = (name) => (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
  const chooser = document.querySelector('[data-accounts]');
  const signin = document.querySelector('[data-signin]');
  if (chooser && signin) {
    const accounts = load();
    const username = signin.querySelector('input[name="username"]');
    const usernameField = signin.querySelector('[data-username-field]');
    const who = signin.querySelector('[data-who]');
    const password = signin.querySelector('input[name="password"]');
    const showForm = () => { chooser.hidden = true; signin.hidden = false; };
    const pick = (a) => {
      showForm();
      username.value = a.u;
      usernameField.hidden = true;
      who.hidden = false;
      const avatar = who.querySelector('[data-who-avatar]');
      avatar.textContent = initialsOf(a.n || a.u);
      avatar.style.background = colorFor(a.u);
      who.querySelector('[data-who-name]').textContent = a.n || a.u;
      who.querySelector('[data-who-user]').textContent = a.u;
      const remember = signin.querySelector('input[name="remember"]');
      if (remember) remember.checked = true;
      password.focus();
    };
    // A form sent back with an error, or a name in the address, is not a fresh visit.
    const fresh = !username.value && !document.querySelector('.flash-error');
    if (accounts.length && fresh) {
      const list = chooser.querySelector('[data-account-list]');
      accounts.forEach((a) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'door-account';
        const avatar = document.createElement('span');
        avatar.className = 'door-avatar';
        avatar.style.background = colorFor(a.u);
        avatar.textContent = initialsOf(a.n || a.u);
        const text = document.createElement('span');
        const b = document.createElement('b'); b.textContent = a.n || a.u;
        const small = document.createElement('small'); small.textContent = a.u;
        text.append(b, small);
        const chev = document.createElement('span'); chev.textContent = '›'; chev.setAttribute('aria-hidden', 'true');
        button.append(avatar, text, chev);
        button.addEventListener('click', () => pick(a));
        list.appendChild(button);
      });
      chooser.hidden = false;
      signin.hidden = true;
      const title = document.querySelector('.door-title');
      if (title) title.textContent = tr('Welcome back');
    }
    chooser.querySelector('[data-account-other]').addEventListener('click', () => { showForm(); username.focus(); });
    chooser.querySelector('[data-account-forget]').addEventListener('click', () => {
      try { localStorage.removeItem(KEY); } catch (_) { /* storage off */ }
      showForm();
      username.focus();
    });
    who.querySelector('[data-who-change]').addEventListener('click', () => {
      username.value = '';
      usernameField.hidden = false;
      who.hidden = true;
      if (accounts.length) { chooser.hidden = false; signin.hidden = true; } else username.focus();
    });
  }

  /* --------------------------------------------------- a guest code */

  const code = document.querySelector('[data-code]');
  if (code) {
    const parts = [...code.querySelectorAll('input')];
    const clean = (s) => String(s || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    const spread = (text, from) => {
      let rest = clean(text);
      for (let k = from; k < parts.length && rest; k += 1) {
        parts[k].value = rest.slice(0, 4);
        rest = rest.slice(4);
        if (parts[k].value.length === 4 && parts[k + 1]) parts[k + 1].focus();
      }
    };
    parts.forEach((input, k) => {
      input.addEventListener('input', () => {
        const text = clean(input.value);
        if (text.length > 4) { spread(text, k); return; }
        input.value = text;
        if (text.length === 4 && parts[k + 1]) parts[k + 1].focus();
      });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Backspace' && !input.value && parts[k - 1]) parts[k - 1].focus();
      });
      input.addEventListener('paste', (e) => {
        const text = (e.clipboardData && e.clipboardData.getData('text')) || '';
        if (clean(text).length > 4) { e.preventDefault(); spread(text, k); }
      });
    });
    code.closest('form').addEventListener('submit', () => {
      code.closest('form').querySelector('[data-code-value]').value = parts.map((p) => clean(p.value)).join('-');
    });
  }

  /* ----------------------------------------------- waiting to be approved */

  const pending = document.querySelector('[data-poll]');
  if (pending) {
    const approved = document.querySelector('[data-approved]');
    const check = async () => {
      try {
        const res = await fetch(pending.dataset.poll, { headers: { Accept: 'application/json' } });
        const state = await res.json();
        if (state.approved) {
          pending.hidden = true;
          approved.hidden = false;
          const done = document.querySelector('[data-done-step]');
          if (done) done.classList.add('is-done');
          return;
        }
      } catch (_) { /* offline for a moment: ask again */ }
      setTimeout(check, 10000);
    };
    setTimeout(check, 4000);
  }

  /* ------------------------------------------- a lab taking shape */

  const labName = document.querySelector('[data-lab-name]');
  if (labName) {
    const name = document.querySelector('[data-forming-name]');
    const mono = document.querySelector('[data-forming-mono]');
    labName.addEventListener('input', () => {
      const text = labName.value.trim();
      name.textContent = text || name.dataset.empty;
      mono.textContent = text ? initialsOf(text) : '?';
      mono.classList.toggle('is-empty', !text);
    });
  }

  /* ------------------------------------------- the desktop app's own */

  // Inside the desktop app's window, a lab another device holds offers the
  // way back to this computer's own BioManager (desktop_menu.py).
  const desktop = () => window.pywebview && window.pywebview.api && window.pywebview.api.this_computer;
  const showDesktop = () => {
    if (!desktop()) return;
    document.querySelectorAll('[data-desktop-only]').forEach((el) => { el.hidden = false; });
  };
  showDesktop();
  window.addEventListener('pywebviewready', showDesktop);
  document.querySelectorAll('[data-this-computer]').forEach((button) => {
    button.addEventListener('click', () => { if (desktop()) window.pywebview.api.this_computer(); });
  });

  // Close the desktop app (desktop_menu.DesktopApi.quit); a browser can't.
  document.querySelectorAll('[data-close-app]').forEach((button) => {
    const ready = () => { if (window.pywebview && window.pywebview.api && window.pywebview.api.quit) button.hidden = false; };
    ready();
    window.addEventListener('pywebviewready', ready);
    button.addEventListener('click', () => window.pywebview.api.quit());
  });

  // A long upload: say so, and don't send it twice.
  const bringForm = document.querySelector('[data-bring-form]');
  if (bringForm) {
    bringForm.addEventListener('submit', () => {
      bringForm.querySelector('[data-bring-wait]').hidden = false;
      bringForm.querySelector('button[type=submit]').disabled = true;
    });
  }

  /* ------------------------------------------------------------- Copy */

  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', () => {
      const text = button.parentElement.querySelector('[data-copy-text]').textContent.trim();
      const done = (label) => { const was = button.textContent; button.textContent = label; setTimeout(() => { button.textContent = was; }, 1500); };
      try { navigator.clipboard.writeText(text).then(() => done(tr('Copied')), () => done(tr('Not copied'))); } catch (_) { done(tr('Not copied')); }
    });
  });
})();
