/* Set up a lab server (templates/server_setup.html, app/server_setup.py).
   Steps: where → connect → address → records → review → run. The page keeps
   the answers; the server checks them, shows the script and runs it. */
(function () {
  'use strict';
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const DATA = JSON.parse($('#ss-data').textContent || '{}');
  const STEPS = ['where', 'connect', 'address', 'records', 'review', 'run'];
  let current = 'where';
  let checked = null;          // the last connection check that passed, for these details
  let job = null;
  let logNext = 0;

  const target = () => ($('input[name="target"]:checked') || {}).value;
  const remote = () => target() !== 'this-computer';

  function keyPath() {
    const pick = $('#ss-key').value;
    return pick === '__other' ? $('#ss-key-other').value.trim() : pick;
  }

  function answers() {
    const t = target();
    const address = t === 'cloud-domain' ? $('#ss-domain').value.trim() : $('#ss-address').value.trim();
    return {
      target: t,
      host: $('#ss-host').value.trim(),
      user: $('#ss-user').value.trim(),
      port: $('#ss-port').value,
      key_path: keyPath(),
      address,
      acme_email: $('#ss-email').value.trim(),
      ts_authkey: $('#ss-authkey').value.trim(),
      ts_hostname: $('#ss-tsname').value.trim(),
      timezone: $('#ss-tz').value,
      bring_data: ($('input[name="bring"]:checked') || {}).value === '1',
    };
  }

  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then((r) => r.json().catch(() => ({ ok: false, error: t('The app gave no answer.') })));
  }

  // ---------------------------------------------------------------- steps

  function show(step) {
    current = step;
    $$('.ss-pane').forEach((p) => { p.hidden = p.dataset.pane !== step; });
    const index = STEPS.indexOf(step);
    $$('#ss-steps li').forEach((li, i) => {
      li.classList.toggle('is-current', i === index);
      li.classList.toggle('is-done', i < index);
    });
    $('#ss-back').hidden = index === 0 || step === 'run';
    $('#ss-next').hidden = step === 'run';
    $('#ss-next').textContent = step === 'review' ? t('Set it up') : t('Continue');
    if (step === 'connect') {
      $$('[data-remote]').forEach((el) => { el.hidden = !remote(); });
      $$('[data-local]').forEach((el) => { el.hidden = remote(); });
      $('#ss-check').textContent = remote() ? t('Test the connection') : t('Check Docker');
    }
    if (step === 'address') {
      $$('[data-for]').forEach((el) => { el.hidden = !el.dataset.for.split(' ').includes(target()); });
      if (!$('#ss-address').value) $('#ss-address').value = remote() ? $('#ss-host').value.trim() : (DATA.computer_address || '');
    }
    if (step === 'review') review();
    window.scrollTo(0, 0);
  }

  $('#ss-back').addEventListener('click', () => show(STEPS[Math.max(0, STEPS.indexOf(current) - 1)]));
  $('#ss-next').addEventListener('click', () => {
    if (current === 'connect' && !checked) {
      return result(false, remote() ? t('Test the connection first, so the set-up knows it can sign in.') : t('Check Docker first.'));
    }
    if (current === 'review') return start();
    show(STEPS[STEPS.indexOf(current) + 1]);
    return undefined;
  });
  $$('input[name="target"]').forEach((r) => r.addEventListener('change', () => { checked = null; result(null, ''); }));
  ['#ss-host', '#ss-user', '#ss-port', '#ss-key', '#ss-key-other'].forEach((sel) =>
    $(sel).addEventListener('input', () => { checked = null; result(null, ''); }));
  $('#ss-key').addEventListener('change', () => { $('#ss-key-other-wrap').hidden = $('#ss-key').value !== '__other'; });

  // ---------------------------------------------------------------- connect

  function result(ok, text) {
    const el = $('#ss-check-result');
    el.textContent = text || '';
    el.dataset.ok = ok === null ? '' : String(ok);
  }

  $('#ss-check').addEventListener('click', () => {
    result(null, remote() ? t('Signing in…') : t('Checking…'));
    $('#ss-facts').hidden = true;
    $('#ss-check').disabled = true;
    post('/server-setup/check', answers()).then((j) => {
      $('#ss-check').disabled = false;
      if (!j.ok) { checked = null; return result(false, j.error || t("Couldn't check.")); }
      const facts = [];
      facts.push(['ok', remote() ? t('Signed in: %(system)s', { system: j.system + (j.machine ? ` (${j.machine})` : '') }) : t('Docker Desktop is installed and running')]);
      if (remote()) {
        facts.push([j.docker ? 'ok' : 'todo', j.docker ? t('Docker is installed') : t('Docker isn\'t installed yet: the set-up installs it (Ubuntu or Debian)')]);
        facts.push([j.sudo ? 'ok' : 'bad', j.sudo ? t('This account can use sudo') : t('This account can\'t use sudo without a password: use one that can')]);
      }
      if (j.existing) facts.push(['bad', t('BioManager is already set up there: this would stop rather than overwrite it')]);
      $('#ss-facts').innerHTML = facts.map(([kind, text]) => `<li data-kind="${kind}">${escapeHtml(text)}</li>`).join('');
      $('#ss-facts').hidden = false;
      const blocked = facts.some(([kind]) => kind === 'bad');
      checked = blocked ? null : j;
      return result(!blocked, blocked ? t('Fix the red item, then test again.') : t('Ready.'));
    });
  });

  // ---------------------------------------------------------------- review

  function review() {
    const a = answers();
    const where = $(`input[name="target"][value="${a.target}"]`).closest('.ss-choice').querySelector('.ss-choice-title').firstChild.textContent;
    const rows = [
      [t('Where'), where],
      [t('Machine'), remote() ? `${a.user}@${a.host}${a.port !== '22' ? `:${a.port}` : ''}` : t('This computer (~/BioManagerServer)')],
      [t('Address'), a.target === 'cloud-tailscale' ? `${a.ts_hostname}.${t('<your tailnet>')}.ts.net` : a.address],
      [t('Certificate'), { 'cloud-tailscale': t('Tailscale’s, renewed by itself'), 'cloud-domain': t('Let’s Encrypt, renewed by itself') }[a.target] || t('BioManager’s own')],
      [t('Time zone'), a.timezone],
      [t('Records'), a.bring_data ? t('This app’s, moved in') : t('Starts empty')],
    ];
    $('#ss-summary').innerHTML = rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('');
    $('#ss-review-error').hidden = true;
    $('#ss-next').disabled = true;
    post('/server-setup/preview', a).then((j) => {
      if (!j.ok) {
        $('#ss-review-error').innerHTML = (j.errors || [j.error]).map(escapeHtml).join('<br>');
        $('#ss-review-error').hidden = false;
        return;
      }
      $('#ss-runs-on').textContent = j.runs_on;
      $('#ss-script').textContent = j.script;
      $('#ss-next').disabled = false;
    });
  }

  // ---------------------------------------------------------------- run

  function start() {
    $('#ss-next').disabled = true;
    post('/server-setup/start', answers()).then((j) => {
      $('#ss-next').disabled = false;
      if (!j.ok) {
        $('#ss-review-error').innerHTML = (j.errors || [j.error]).map(escapeHtml).join('<br>');
        $('#ss-review-error').hidden = false;
        return;
      }
      job = j.job;
      logNext = 0;
      $('#ss-log').textContent = '';
      $('#ss-done').hidden = true;
      $('#ss-failed').hidden = true;
      $('#ss-run-title').textContent = t('Setting up…');
      show('run');
      poll();
    });
  }

  function poll() {
    fetch(`/server-setup/job/${job}?since=${logNext}`).then((r) => r.json()).then((j) => {
      logNext = j.next;
      if (j.lines.length) {
        const log = $('#ss-log');
        log.textContent += `${j.lines.join('\n')}\n`;
        log.scrollTop = log.scrollHeight;
      }
      const running = j.status === 'running';
      $('#ss-progress').innerHTML = j.steps.map((s, i) => {
        const last = i === j.steps.length - 1;
        const state = last && running ? 'now' : last && j.status === 'failed' ? 'failed' : 'done';
        return `<li data-state="${state}">${escapeHtml(t(s))}</li>`;
      }).join('');
      if (running) return setTimeout(poll, 1200);
      if (j.status === 'done') finished(j); else failed(j);
      return undefined;
    }).catch(() => setTimeout(poll, 2500));
  }

  function finished(j) {
    const address = j.values.ADDRESS;
    $('#ss-run-title').textContent = t('Your lab server is ready');
    $('#ss-open').textContent = `https://${address}`;
    $('#ss-open').href = `https://${address}/`;
    $('#ss-register').textContent = `https://${address}/register`;
    $('#ss-register').href = `https://${address}/register`;
    $('#ss-code-box').hidden = !j.values.SETUP_CODE;
    $('#ss-code').textContent = j.values.SETUP_CODE || '';
    $('#ss-own-cert').hidden = j.values.REACHABLE !== 'yes-own-certificate';
    $('#ss-unreachable').hidden = j.values.REACHABLE !== 'no';
    $('#ss-ntfy-box').hidden = !j.values.NTFY_TOPIC;
    $('#ss-ntfy').textContent = j.values.NTFY_TOPIC || '';
    $('#ss-done').hidden = false;
  }

  function failed(j) {
    $('#ss-run-title').textContent = t('The set-up stopped');
    $('#ss-fail-text').textContent = j.error ? t(j.error) : '';
    $('#ss-failed').hidden = false;
    $('#ss-log-box').open = true;
  }

  $('#ss-retry').addEventListener('click', () => show('review'));
  $('#ss-copy').addEventListener('click', () => {
    const text = $('#ss-open').textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).catch(() => {});
    $('#ss-copy').textContent = t('Copied');
  });

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  show('where');
})();
