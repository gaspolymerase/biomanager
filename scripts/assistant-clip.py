#!/usr/bin/env python3
"""Make the website's clip for AI assistants (site/assets/clips/assistant.*).

    python scripts/assistant-clip.py site/assets/clips
    python scripts/assistant-clip.py /tmp/out --base http://127.0.0.1:5077 --data /path/to/lab

Someone tells an assistant what they did; it finds each record, sends one
proposal, and they approve it in BioManager. The assistant's window is drawn
here (a plain one, not any real app's), and BioManager is the real app: a
fresh demo lab (scripts/demo-data.py), a Read and propose token, and a
proposal sent through /api/v1 as an assistant would, then approved on
Proposed changes with a real click.

Every frame is drawn on its own, at twice the size it is shown, with the
camera's zoom done by the browser (so words are drawn sharp at every zoom,
never enlarged afterwards), and encoded at a quality that keeps text crisp:
assistant.mp4 (H.264), assistant.webm (VP9) and assistant.webp (the poster).

Needs Playwright and a bundled ffmpeg, which are not app dependencies:
    pip install playwright imageio-ffmpeg pillow
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import imageio_ffmpeg
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FPS = 30
VIEW = (1280, 786)      # CSS pixels: the clip window's shape on the website
SCALE = 2               # drawn at twice that
FONTS = ROOT / "site/assets/fonts"

SAID = ("Log today's work: weaned the litter in 110. Weighed 6 and 7 from the TMX cohort, "
        "24.1 g and 23.8 g. Moved the two females from 112 into 106.")
STEPS = ["Found cage 110 · its litter is 19 days old",
         "Found mice 6 and 7 · cage 103",
         "Found the two females in 112 · #30 and #31",
         "Sent one proposal · 4 changes"]


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def ease_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


# --------------------------------------------------------------------------- the demo lab

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_demo_lab():
    data = Path(tempfile.mkdtemp(prefix="biomanager-assistant-clip-")) / "lab"
    subprocess.run([sys.executable, str(ROOT / "scripts/demo-data.py"), str(data)], check=True,
                   stdout=subprocess.DEVNULL)
    port = free_port()
    env = {**os.environ, "BIOMANAGER_DATA_DIR": str(data), "DATABASE_URL": f"sqlite:///{data / 'biomanager.db'}",
           "SECRET_KEY": "assistant-clip", "FLASK_DEBUG": "0", "PORT": str(port), "BIOMANAGER_TELEMETRY": "0"}
    server = subprocess.Popen([sys.executable, str(ROOT / "run.py")], env=env, cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(150):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.2)
    return f"http://127.0.0.1:{port}", data, server


def sign_in(page, base, password):
    page.goto(f"{base}/login")
    page.fill("input[name=username]", "alex")
    page.fill("input[name=password]", password)
    page.press("input[name=password]", "Enter")
    page.wait_for_load_state("networkidle")


def propose(page, base) -> dict:
    """What an assistant does: a Read and propose token, each record looked
    up with /resolve, and one proposal."""
    form = page.request.post(f"{base}/settings/api-tokens", headers={"Origin": base},
                             form={"label": "AI assistant", "scope": "propose", "expires": "30"})
    token = re.search(r"bmt_[A-Za-z0-9_\-]+", form.text())
    if not token:
        raise SystemExit("Couldn't make a token: " + form.text()[:300])
    auth = {"Authorization": f"Bearer {token.group(0)}"}

    def ref(q, kind):
        found = page.request.get(f"{base}/api/v1/resolve", params={"q": q}, headers=auth).json()["data"]
        hits = [m["ref"] for m in found if m["ref"]["kind"] == kind]
        if not hits:
            raise SystemExit(f"Nothing in the demo lab for {q!r}")
        return hits[0]

    females = page.request.get(f"{base}/api/v1/mice", params={"cage": "112"}, headers=auth).json()["data"]
    body = {"summary": "Today: weaned the litter in 110, weighed 6 and 7, and moved the two females from 112 "
                       "into 106.",
            "source": "AI assistant",
            "changes": [{"action": "wean", "target": ref("cage 110", "cage")},
                        {"action": "mouse_weight", "target": ref("mouse 6", "mouse"), "fields": {"grams": 24.1}},
                        {"action": "mouse_weight", "target": ref("mouse 7", "mouse"), "fields": {"grams": 23.8}},
                        {"action": "mice_move", "target": ref("cage 106", "cage"),
                         "fields": {"mice": [m["mouse_id"] for m in females][:2]}}]}
    r = page.request.post(f"{base}/api/v1/proposals", data=json.dumps(body),
                          headers={**auth, "Content-Type": "application/json"})
    out = r.json()
    if r.status != 201 or out.get("errors") or any(c.get("errors") for c in out.get("changes", [])):
        raise SystemExit("The proposal didn't go through: " + json.dumps(out)[:600])
    return out


# --------------------------------------------------------------------------- the assistant's window

CHAT_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face { font-family: Geist; src: url("{fonts}/Geist-latin.woff2") format("woff2"); font-weight: 100 900; }
@font-face { font-family: Geist Mono; src: url("{fonts}/GeistMono-latin.woff2") format("woff2"); font-weight: 100 900; }
* { box-sizing: border-box; }
html, body { margin: 0; width: 1280px; height: 786px; overflow: hidden; }
body { font: 17px/1.5 Geist, system-ui, sans-serif; color: #121a19; -webkit-font-smoothing: antialiased;
  background: radial-gradient(70% 90% at 20% 10%, #dff5ef, transparent 60%),
              radial-gradient(60% 80% at 90% 90%, #dde6ff, transparent 60%), #f4f7f6; }
body::before { content: ""; position: absolute; inset: 0;
  background-image: radial-gradient(rgba(16, 52, 46, 0.16) 1px, transparent 1px); background-size: 22px 22px;
  -webkit-mask-image: radial-gradient(80% 80% at 50% 50%, #000, transparent); }
#cam { position: absolute; inset: 0; transform-origin: 0 0; }
.win { position: absolute; left: 190px; top: 64px; width: 900px; height: 658px; border-radius: 22px;
  background: rgba(255, 255, 255, 0.72); border: 1px solid rgba(255, 255, 255, 0.95);
  box-shadow: 0 0 0 1px rgba(16, 52, 46, 0.07), 0 40px 90px rgba(10, 40, 35, 0.18), inset 0 1px 0 #fff;
  backdrop-filter: blur(20px) saturate(1.6); display: flex; flex-direction: column; overflow: hidden; }
.bar { display: flex; align-items: center; gap: 10px; height: 56px; padding: 0 20px; border-bottom: 1px solid rgba(16, 52, 46, 0.08); }
.dots { display: flex; gap: 8px; margin-right: 8px; } .dots i { width: 12px; height: 12px; border-radius: 50%; background: #d9dfdd; }
.av { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center; color: #fff;
  background: linear-gradient(135deg, #7aa7ff, #b79cff); box-shadow: 0 4px 10px rgba(122, 167, 255, 0.4); }
.av svg { width: 17px; height: 17px; }
.bar b { font-weight: 600; font-size: 16px; }
.conn { margin-left: auto; display: flex; align-items: center; gap: 8px; font-size: 13.5px; color: #4a5856;
  padding: 5px 11px; border-radius: 99px; background: rgba(16, 52, 46, 0.05); }
.conn i { width: 8px; height: 8px; border-radius: 50%; background: #2cc4a6; box-shadow: 0 0 8px #2cc4a6; }
.msgs { flex: 1; padding: 26px 30px 10px; display: flex; flex-direction: column; gap: 18px; justify-content: flex-end; }
.me { align-self: flex-end; max-width: 600px; padding: 13px 17px; border-radius: 18px 18px 6px 18px;
  background: #121a19; color: #f4f7f6; }
.it { display: flex; gap: 14px; align-items: flex-start; }
.it .body { flex: 1; }
.step { display: flex; align-items: center; gap: 10px; font: 500 14.5px/1.9 "Geist Mono", ui-monospace, monospace; color: #4a5856; }
.step .ic { width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; }
.step .ic.busy { border: 2px solid #c9d6d3; border-top-color: #2cc4a6; }
.step .ic.ok { background: #2cc4a6; color: #fff; }
.step .ic.ok svg { width: 11px; height: 11px; }
.reply { margin: 10px 0 0; }
.link { display: flex; align-items: center; gap: 14px; margin-top: 14px; width: 470px; padding: 14px 16px; border-radius: 14px;
  background: #fff; border: 1px solid rgba(16, 52, 46, 0.1); box-shadow: 0 8px 22px rgba(10, 40, 35, 0.08); }
.link img { width: 38px; height: 38px; border-radius: 10px; }
.link b { display: block; font-weight: 600; }
.link span { color: #5f6d6a; font-size: 14.5px; }
.link .go { margin-left: auto; color: #0f8f7c; font-weight: 600; font-size: 15px; }
.input { margin: 10px 22px 22px; min-height: 58px; display: flex; align-items: center; gap: 12px; padding: 10px 12px 10px 18px;
  border-radius: 16px; background: #fff; border: 1px solid rgba(16, 52, 46, 0.12); box-shadow: 0 2px 6px rgba(10, 40, 35, 0.05); }
.input .txt { flex: 1; } .input .ph { color: #8a9693; }
.input .caret { display: inline-block; width: 2px; height: 20px; margin-left: 1px; vertical-align: -4px; background: #121a19; }
.send { width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; background: #d9dfdd; color: #fff; }
.send.on { background: #121a19; }
.send svg { width: 18px; height: 18px; }
.hide { display: none !important; }
#cursor { position: absolute; left: 0; top: 0; width: 26px; height: 26px; z-index: 9; pointer-events: none;
  filter: drop-shadow(0 2px 3px rgba(0, 0, 0, 0.3)); }
</style></head><body><div id="cam">
<div class="win" id="win">
  <div class="bar"><span class="dots"><i></i><i></i><i></i></span>
    <span class="av"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l1.9 5.6L19.5 9.5l-5.6 1.9L12 17l-1.9-5.6L4.5 9.5l5.6-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/></svg></span>
    <b>Your AI assistant</b><span class="conn"><i></i>BioManager connected</span></div>
  <div class="msgs">
    <div class="me hide" id="me"></div>
    <div class="it hide" id="it"><span class="av"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l1.9 5.6L19.5 9.5l-5.6 1.9L12 17l-1.9-5.6L4.5 9.5l5.6-1.9z"/></svg></span>
      <div class="body"><div id="steps"></div>
        <p class="reply hide" id="reply">Done. I proposed 4 changes in BioManager for you to approve. Nothing changes until you do.</p>
        <div class="link hide" id="link"><img src="{icon}"><div><b>Proposal #{pid} · 4 changes</b><span>Waiting for you on Proposed changes</span></div><span class="go">Review →</span></div>
      </div></div>
  </div>
  <div class="input"><div class="txt" id="txt"><span class="ph">Tell it what you did…</span></div>
    <div class="send" id="send"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></div></div>
</div>
</div>
<svg id="cursor" viewBox="0 0 26 26"><path d="M5 3l15 8.5-6.4 1.6L10.6 20z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>
<script>
const SAID = {said}, STEPS = {steps};
const OK = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6.2l2.3 2.3 4.7-5"/></svg>';
const $ = id => document.getElementById(id);
window.frame = function (s) {
  // s: {typed, sent, steps: [state...], reply, link, cursor: [x, y, down], cam: [cx, cy, scale], fade}
  const t = $('txt');
  if (s.sent) t.innerHTML = '<span class="ph">Tell it what you did…</span>';
  else if (s.typed > 0) t.innerHTML = SAID.slice(0, s.typed).replace(/&/g, '&amp;').replace(/</g, '&lt;') + '<span class="caret"></span>';
  $('send').classList.toggle('on', !s.sent && s.typed > 0);
  $('me').classList.toggle('hide', !s.sent); $('me').textContent = SAID;
  $('it').classList.toggle('hide', !s.steps.length);
  $('steps').innerHTML = s.steps.map((st, i) => '<div class="step"><span class="ic ' + (st ? 'ok' : 'busy') + '"' +
      (st ? '' : ' style="transform:rotate(' + (s.spin || 0) + 'deg)"') + '>' + (st ? OK : '') + '</span>' + STEPS[i] + '</div>').join('');
  $('reply').classList.toggle('hide', !s.reply); $('link').classList.toggle('hide', !s.link);
  const [cx, cy, k] = s.cam;
  $('cam').style.transform = 'translate(' + (640 - cx * k) + 'px,' + (393 - cy * k) + 'px) scale(' + k + ')';
  const c = $('cursor');
  c.style.display = s.cursor ? 'block' : 'none';
  if (s.cursor) c.style.transform = 'translate(' + (s.cursor[0] - 5) + 'px,' + (s.cursor[1] - 3) + 'px) scale(' + (s.cursor[2] ? 0.85 : 1) + ')';
  document.body.style.opacity = s.fade == null ? 1 : s.fade;
};
window.where = id => { const r = $(id).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
</script></body></html>"""


def chat_frames(browser, pid: int, sink):
    """The assistant's window: typed, sent, the records found, the link."""
    ctx = browser.new_context(viewport={"width": VIEW[0], "height": VIEW[1]}, device_scale_factor=SCALE)
    page = ctx.new_page()
    tmp = Path(tempfile.mkdtemp(prefix="assistant-chat-"))
    doc = (CHAT_HTML.replace("{fonts}", FONTS.as_uri()).replace("{icon}", (ROOT / "app/static/icon-512.png").as_uri())
           .replace("{pid}", str(pid)).replace("{said}", json.dumps(SAID)).replace("{steps}", json.dumps(STEPS)))
    (tmp / "chat.html").write_text(doc, encoding="utf-8")
    page.goto((tmp / "chat.html").as_uri())
    page.evaluate("document.fonts.ready")

    # the timeline, in seconds
    type_from, type_cps = 0.7, 44.0
    type_to = type_from + len(SAID) / type_cps
    sent = type_to + 0.45
    step_at = [sent + 0.6 + 0.55 * i for i in range(len(STEPS))]
    step_done = [a + 0.45 for a in step_at]
    reply_at = step_done[-1] + 0.35
    link_at = reply_at + 0.45
    end = link_at + 1.9

    page.evaluate("frame({typed: 0, sent: false, steps: [], reply: false, link: false, cursor: null, cam: [640, 393, 1]})")
    send_xy = page.evaluate("where('send')")
    page.evaluate("frame({typed: 0, sent: true, steps: [1,1,1,1], reply: true, link: true, cursor: null, cam: [640, 393, 1]})")
    link_xy = page.evaluate("where('link')")

    n = int(end * FPS)
    for f in range(n):
        t = f / FPS
        typed = 0 if t < type_from else min(len(SAID), int((t - type_from) * type_cps))
        steps = [1 if t >= d else 0 for a, d in zip(step_at, step_done) if t >= a]
        # the camera: the whole window while typing, closer on the answer, then on the link
        k = 1 + 0.18 * ease((t - sent) / 1.2) + 0.12 * ease((t - link_at) / 1.0)
        cy = 393 + 70 * ease((t - sent) / 1.2) + 60 * ease((t - link_at) / 1.0)
        cx = 640 - 40 * ease((t - link_at) / 1.0)
        # the cursor: to Send, then to the link
        cursor = None
        if t < type_to + 0.1:
            cursor = None
        elif t < sent + 0.3:
            p = ease((t - type_to - 0.1) / 0.3)
            cursor = [send_xy[0] + 60 * (1 - p), send_xy[1] + 40 * (1 - p), sent <= t < sent + 0.12]
        elif t >= link_at + 0.6:
            p = ease((t - link_at - 0.6) / 0.8)
            cursor = [960 + (link_xy[0] + 120 - 960) * p, 700 + (link_xy[1] - 700) * p, t >= end - 0.35]
        state = {"typed": typed, "sent": t >= sent, "steps": steps, "reply": t >= reply_at, "link": t >= link_at,
                 "cursor": cursor, "cam": [cx, cy, k], "spin": (t * 360) % 360}
        page.evaluate(f"frame({json.dumps(state)})")
        sink(page.screenshot(type="png"))
    ctx.close()
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- BioManager

APP_OVERLAY = """
(() => {
  if (document.getElementById('__clip')) return;
  const css = document.createElement('style');
  css.textContent = `@font-face { font-family: Geist; src: url("${FONT}") format("woff2"); font-weight: 100 900; }
    body, button, input, select, textarea { font-family: Geist, system-ui, sans-serif !important; }
    body { transform-origin: 0 0; }
    #__clip { position: fixed; left: 0; top: 0; width: 26px; height: 26px; z-index: 2147483647; pointer-events: none;
      filter: drop-shadow(0 2px 3px rgba(0,0,0,.3)); }
    *, *::before, *::after { transition: none !important; animation: none !important; caret-color: transparent; }`;
  document.head.appendChild(css);
  const c = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  c.id = '__clip'; c.setAttribute('viewBox', '0 0 26 26');
  c.innerHTML = '<path d="M5 3l15 8.5-6.4 1.6L10.6 20z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>';
  document.documentElement.appendChild(c);
  window.__frame = (cx, cy, k, x, y, down) => {
    document.body.style.transform = 'translate(' + (640 - cx * k) + 'px,' + (393 - cy * k) + 'px) scale(' + k + ')';
    c.style.display = x == null ? 'none' : 'block';
    if (x != null) c.style.transform = 'translate(' + (x - 5) + 'px,' + (y - 3) + 'px) scale(' + (down ? 0.85 : 1) + ')';
  };
})();
"""


def app_frames(page, base, pid: int, sink):
    """Proposed changes: the proposal, read closer, approved with a click."""
    # the font travels in the page: a page from the lab may not load a file from this disk
    font = "data:font/woff2;base64," + base64.b64encode((FONTS / "Geist-latin.woff2").read_bytes()).decode()
    overlay = APP_OVERLAY.replace("${FONT}", font)
    page.set_viewport_size({"width": VIEW[0], "height": VIEW[1]})
    page.goto(f"{base}/proposals")
    page.wait_for_load_state("networkidle")
    page.add_style_tag(content="html { scroll-behavior: auto !important; }")
    page.evaluate(overlay)
    page.evaluate("document.fonts.ready")
    page.evaluate("window.scrollTo(0, 0)")

    def box(selector):
        page.evaluate("__frame(640, 393, 1, null, null, false)")
        r = page.locator(selector).first.bounding_box()
        return r["x"], r["y"], r["width"], r["height"]

    card = box(f"#proposal-{pid}")
    approve = box(f"#proposal-{pid} button.btn-primary")
    ccx, ccy = card[0] + card[2] / 2, min(card[1] + card[3] / 2, card[1] + 300)
    k1 = min(1.45, 1180 / card[2])
    ax, ay = approve[0] + approve[2] / 2, approve[1] + approve[3] / 2

    def on_screen(x, y, cx, cy, k):
        return 640 + (x - cx) * k, 393 + (y - cy) * k

    # the timeline
    zoom_in, read, to_button, click = 0.4, 1.4, 3.3, 4.2
    n = int(click * FPS)
    for f in range(n):
        t = f / FPS
        p = ease((t - zoom_in) / 1.0)
        k = 1 + (k1 - 1) * p
        cx, cy = 640 + (ccx - 640) * p, 393 + (ccy - 393) * p
        # read down the card, then settle on the buttons
        q = ease((t - read) / 1.6)
        cy = cy + (ay - 120 - ccy) * q * p
        cursor = None
        if t >= to_button - 0.9:
            sx, sy = on_screen(ax, ay, cx, cy, k)
            m = ease_out((t - (to_button - 0.9)) / 0.9)
            cursor = (sx + 220 * (1 - m), sy + 150 * (1 - m))
        page.evaluate(f"__frame({cx}, {cy}, {k}, {cursor[0] if cursor else 'null'}, {cursor[1] if cursor else 'null'}, "
                      f"{'true' if t > click - 0.15 else 'false'})")
        sink(page.screenshot(type="png"))
    last = (cx, cy, k, cursor)

    # the click: the real one, on the real button
    page.evaluate("__frame(640, 393, 1, null, null, false)")
    page.locator(f"#proposal-{pid} button.btn-primary").first.click()
    page.wait_for_load_state("networkidle")
    page.evaluate(overlay)
    page.evaluate("document.fonts.ready")
    page.evaluate("window.scrollTo(0, 0)")
    # then back out to the whole page: the notice, and the proposal under Recent
    cx0, cy0, k0, cur = last
    n = int(3.6 * FPS)
    for f in range(n):
        t = f / FPS
        p = ease(t / 1.3)
        k = k0 + (1 - k0) * p
        cx, cy = cx0 + (640 - cx0) * p, cy0 + (393 - cy0) * p
        drift = ease(t / 1.5)
        x, y = cur[0] + 60 * drift, cur[1] + 90 * drift
        page.evaluate(f"__frame({cx}, {cy}, {k}, {x}, {y}, {'true' if t < 0.12 else 'false'})")
        sink(page.screenshot(type="png"))


# --------------------------------------------------------------------------- encoding

class Frames:
    """Keeps every frame (as PNG bytes) for blending and encoding."""

    def __init__(self):
        self.items: list[bytes] = []

    def __call__(self, png: bytes):
        self.items.append(png)


def crossfade(a: list[bytes], b: list[bytes], frames: int) -> list[bytes]:
    out = a[:-frames]
    for i in range(frames):
        x = ease((i + 1) / (frames + 1))
        fa = Image.open(io.BytesIO(a[len(a) - frames + i])).convert("RGB")
        fb = Image.open(io.BytesIO(b[i])).convert("RGB")
        buf = io.BytesIO()
        Image.blend(fa, fb, x).save(buf, "PNG")
        out.append(buf.getvalue())
    return out + b[frames:]


def encode(frames: list[bytes], out_dir: Path, name: str):
    size = Image.open(io.BytesIO(frames[0])).size
    raw = b"".join(Image.open(io.BytesIO(f)).convert("RGB").tobytes() for f in frames)
    src = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-"]
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4, webm = out_dir / f"{name}.mp4", out_dir / f"{name}.webm"
    subprocess.run([FFMPEG, "-v", "error", "-y", *src, "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                    "-tune", "animation", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)],
                   input=raw, check=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", *src, "-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0",
                    "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-pix_fmt", "yuv420p", str(webm)],
                   input=raw, check=True)
    poster = Image.open(io.BytesIO(frames[int(len(frames) * 0.62)])).convert("RGB")
    poster.save(out_dir / f"{name}.webp", "WEBP", quality=88)
    return mp4, webm


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", nargs="?", default=str(ROOT / "site/assets/clips"))
    ap.add_argument("--base", help="a running demo lab to record in, instead of a fresh one")
    ap.add_argument("--data", help="that lab's data folder (for its demo-password)")
    ap.add_argument("--name", default="assistant")
    args = ap.parse_args()

    server = None
    if args.base:
        base, data = args.base.rstrip("/"), Path(args.data)
    else:
        base, data, server = start_demo_lab()
    try:
        password = (data / "demo-password").read_text().strip()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": VIEW[0], "height": VIEW[1]}, device_scale_factor=SCALE,
                                      color_scheme="light", locale="en-US",
                                      extra_http_headers={"Accept-Language": "en-US,en"})
            page = ctx.new_page()
            sign_in(page, base, password)
            proposal = propose(page, base)
            chat, app = Frames(), Frames()
            app_frames(page, base, proposal["id"], app)      # first, while the proposal is "just now"
            chat_frames(browser, proposal["id"], chat)
            browser.close()
        frames = crossfade(chat.items, app.items, int(0.5 * FPS))
        mp4, webm = encode(frames, Path(args.out), args.name)
        for f in (mp4, webm, Path(args.out) / f"{args.name}.webp"):
            print(f"wrote {f.relative_to(ROOT) if f.is_relative_to(ROOT) else f}: {f.stat().st_size // 1024} KB")
    finally:
        if server:
            server.terminate()
            shutil.rmtree(data.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
