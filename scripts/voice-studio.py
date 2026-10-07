#!/usr/bin/env python3
"""A small page for reading text aloud with Qwen3-TTS, on this Mac.

    ~/.cache/biomanager-tts/venv/bin/python scripts/voice-studio.py

Opens http://127.0.0.1:7860 in the browser (--no-open doesn't). Type what to say and pick how
it should sound:

    我的声音     one of BioManager's own voices (promo/voices/, as the videos use)
    用一段录音   any clear 5–15 s recording, with the words spoken in it
    描述一个声音  a voice made from a description (Qwen3-TTS VoiceDesign)

Each result can be played, downloaded, or kept as a new voice in
promo/voices/, which scripts/qwen-voice.py and the video scripts can then
use as "qwen:<name>". Nothing leaves the computer; the models are
downloaded from Hugging Face the first time each is used (about 2.7 GB for
the voices, 2 GB more for describing one). Runs in the TTS environment
described in scripts/qwen-voice.py; Ctrl-C stops it.
"""
from __future__ import annotations

import base64
import io
import sys
import json
import re
import subprocess
import tempfile
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import load_model

ROOT = Path(__file__).resolve().parent.parent
VOICES = ROOT / "promo/voices"
BASE = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-6bit"
DESIGN = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-6bit"
PORT = 7860

models: dict = {}
lock = threading.Lock()          # one generation at a time: the model isn't shared safely
results: dict[str, dict] = {}    # id: {"wav": bytes, "text": str, "lang": str}


def model(name: str):
    if name not in models:
        models[name] = load_model(name)
    return models[name]


def saved_voices() -> dict:
    path = VOICES / "voices.json"
    return json.loads(path.read_text()) if path.exists() else {}


def as_wav(data: bytes, suffix: str, folder: Path) -> Path:
    """Any recording the Mac can read (wav, m4a, mp3, aiff…) as a mono 24 kHz wav."""
    src = folder / f"sample{suffix or '.wav'}"
    src.write_bytes(data)
    out = folder / "sample-24k.wav"
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(src), str(out)],
                   check=True, capture_output=True)
    return out


def speak(req: dict) -> dict:
    text = req.get("text", "").strip()
    if not text:
        raise ValueError("请先写下要读的话 / Type something to say first.")
    lang = req.get("lang") or "auto"
    speed = float(req.get("speed") or 1.0)
    with tempfile.TemporaryDirectory(prefix="voice-studio-") as tmp, lock:
        if req["mode"] == "saved":
            name = req["voice"]
            v = saved_voices()[name]
            m = model(BASE)
            parts = m.generate(text=text, ref_audio=str(VOICES / f"{name}.wav"), ref_text=v["text"],
                               lang_code=lang if lang != "auto" else v["language"], speed=speed)
        elif req["mode"] == "sample":
            if not req.get("sample") or not req.get("sample_text", "").strip():
                raise ValueError("需要一段录音和录音里说的原话 / A recording and its exact words are needed.")
            ref = as_wav(base64.b64decode(req["sample"]), Path(req.get("sample_name", "")).suffix, Path(tmp))
            m = model(BASE)
            parts = m.generate(text=text, ref_audio=str(ref), ref_text=req["sample_text"].strip(),
                               lang_code=lang, speed=speed)
        else:
            if not req.get("describe", "").strip():
                raise ValueError("请描述想要的声音 / Describe the voice first.")
            m = model(DESIGN)
            parts = m.generate(text=text, instruct=req["describe"].strip(), lang_code=lang, speed=speed)
        audio = np.concatenate([np.array(p.audio) for p in parts])
        buf = io.BytesIO()
        sf.write(buf, audio, m.sample_rate, format="WAV")
    rid = uuid.uuid4().hex[:10]
    results[rid] = {"wav": buf.getvalue(), "text": text,
                    "lang": lang if lang != "auto" else ("chinese" if re.search(r"[一-鿿]", text) else "english")}
    return {"id": rid, "seconds": round(len(audio) / m.sample_rate, 1)}


def keep(req: dict) -> dict:
    """Keep a result as a voice in promo/voices/: its clip, and the words it says."""
    name = re.sub(r"[^a-z0-9-]", "", req.get("name", "").lower())
    if not name:
        raise ValueError("名字只能用小写字母、数字和 - / Use lowercase letters, digits and -.")
    r = results[req["id"]]
    voices = saved_voices()
    if name in voices:
        raise ValueError(f"已经有叫 {name} 的声音了 / There is already a voice called {name}.")
    (VOICES / f"{name}.wav").write_bytes(r["wav"])
    voices[name] = {"language": r["lang"], "text": r["text"], "designed_from": req.get("describe", "")}
    (VOICES / "voices.json").write_text(json.dumps(voices, ensure_ascii=False, indent=2) + "\n")
    return {"name": name}


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Voice Studio</title>
<style>
:root { --bg:#f6f7f9; --card:#fff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --accent:#0f8a74; --soft:#e7f5f1; }
@media (prefers-color-scheme: dark) { :root { --bg:#111418; --card:#1a1f25; --ink:#e5e7eb; --muted:#9ca3af;
  --line:#2b323b; --accent:#34c3a3; --soft:#16302a; } }
* { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system, "PingFang SC", sans-serif; }
main { max-width:760px; margin:0 auto; padding:28px 16px 60px; }
h1 { font-size:22px; margin:0 0 4px; } .sub { color:var(--muted); margin:0 0 20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px; margin-bottom:14px; }
label { display:block; font-weight:600; margin:0 0 6px; } .hint { color:var(--muted); font-size:13px; font-weight:400; }
textarea, input[type=text], select { width:100%; font:inherit; color:inherit; background:var(--bg); border:1px solid var(--line);
  border-radius:10px; padding:10px 12px; } textarea { min-height:110px; resize:vertical; }
.tabs { display:flex; gap:6px; margin-bottom:14px; flex-wrap:wrap; }
.tabs button { border:1px solid var(--line); background:var(--bg); color:var(--ink); border-radius:999px; padding:7px 14px;
  font:inherit; cursor:pointer; } .tabs button.on { background:var(--accent); border-color:var(--accent); color:#fff; }
.row { display:flex; gap:14px; flex-wrap:wrap; align-items:end; } .row > div { flex:1; min-width:160px; }
.pane { display:none; } .pane.on { display:block; } .pane > * + * { margin-top:12px; }
.go { width:100%; margin-top:14px; padding:12px; border:0; border-radius:12px; background:var(--accent); color:#fff;
  font:600 16px/1 inherit; cursor:pointer; } .go:disabled { opacity:.55; cursor:wait; }
.err { color:#dc2626; margin-top:10px; white-space:pre-wrap; } .out { display:flex; flex-direction:column; gap:10px; }
.clip { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
.clip p { margin:0 0 8px; } .clip audio { width:100%; } .clip .acts { display:flex; gap:12px; margin-top:6px; font-size:14px; flex-wrap:wrap; }
.clip a, .clip button.link { color:var(--accent); background:none; border:0; padding:0; font:inherit; cursor:pointer; }
</style></head><body><main>
<h1>Voice Studio</h1><p class="sub">Qwen3-TTS，在这台 Mac 上运行，不联网 · runs on this Mac, nothing is uploaded</p>
<div class="card">
  <label for="text">要读的话 <span class="hint">· what to say (每行分开读 / each line read in turn)</span></label>
  <textarea id="text" placeholder="大家好！今天想跟你们分享一个超好用的实验室小工具。"></textarea>
  <div class="row" style="margin-top:12px">
    <div><label for="lang">语言 <span class="hint">· language</span></label>
      <select id="lang"><option value="auto">自动 / auto</option><option value="chinese">中文</option>
      <option value="english">English</option><option value="japanese">日本語</option><option value="korean">한국어</option>
      <option value="french">Français</option><option value="german">Deutsch</option><option value="spanish">Español</option></select></div>
    <div><label for="speed">语速 <span class="hint">· speed <b id="sv">1.0</b>×</span></label>
      <input id="speed" type="range" min="0.7" max="1.4" step="0.05" value="1" style="width:100%"></div>
  </div>
</div>
<div class="card">
  <div class="tabs"><button data-p="saved" class="on">我的声音</button><button data-p="sample">用一段录音</button>
    <button data-p="describe">描述一个声音</button></div>
  <div class="pane on" id="p-saved"><div><label for="voice">声音 <span class="hint">· promo/voices/</span></label>
    <select id="voice"></select></div></div>
  <div class="pane" id="p-sample">
    <div><label for="sample">录音 <span class="hint">· 5–15 秒、清楚、只有一个人说话 (wav, m4a, mp3…)</span></label>
      <input id="sample" type="file" accept="audio/*"></div>
    <div><label for="stext">录音里说的原话 <span class="hint">· the exact words in the recording</span></label>
      <input id="stext" type="text" placeholder="一字不差地写下录音里的话"></div>
  </div>
  <div class="pane" id="p-describe"><div><label for="desc">想要什么样的声音 <span class="hint">· 第一次用要下载约 2 GB</span></label>
    <textarea id="desc" style="min-height:80px" placeholder="二十多岁的年轻女生，声音明亮、有活力，语速稍快，带着笑意。"></textarea></div></div>
  <button class="go" id="go">读出来 · Speak</button><div class="err" id="err"></div>
</div>
<div class="out" id="out"></div>
</main><script>
let mode = 'saved';
const $ = id => document.getElementById(id);
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  mode = b.dataset.p; document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.pane').forEach(p => p.classList.toggle('on', p.id === 'p-' + mode)); });
$('speed').oninput = () => $('sv').textContent = (+$('speed').value).toFixed(2).replace(/0$/, '');
async function loadVoices(pick) { const v = await (await fetch('/voices')).json();
  $('voice').innerHTML = Object.entries(v).map(([k, x]) => `<option value="${k}">${k} · ${x.language}</option>`).join('');
  if (pick) $('voice').value = pick; }
loadVoices();
const file64 = f => new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result.split(',')[1]); fr.readAsDataURL(f); });
$('go').onclick = async () => {
  $('err').textContent = ''; const body = { mode, text: $('text').value, lang: $('lang').value, speed: $('speed').value,
    voice: $('voice').value, sample_text: $('stext').value, describe: $('desc').value };
  if (mode === 'sample' && $('sample').files[0]) { body.sample = await file64($('sample').files[0]); body.sample_name = $('sample').files[0].name; }
  $('go').disabled = true; $('go').textContent = '正在生成… (第一次要加载模型)';
  try { const r = await fetch('/speak', { method: 'POST', body: JSON.stringify(body) }); const j = await r.json();
    if (!r.ok) throw new Error(j.error); addClip(j, body); }
  catch (e) { $('err').textContent = e.message; }
  finally { $('go').disabled = false; $('go').textContent = '读出来 · Speak'; } };
function addClip(j, body) {
  const d = document.createElement('div'); d.className = 'clip';
  const who = mode === 'saved' ? body.voice : mode === 'sample' ? '录音的声音' : '描述的声音';
  d.innerHTML = `<p></p><audio controls src="/audio/${j.id}"></audio><div class="acts">
    <a href="/audio/${j.id}?download=1">下载 wav</a><button class="link">保存为我的声音…</button>
    <span class="hint">${who} · ${j.seconds} s</span></div>`;
  d.querySelector('p').textContent = body.text; $('out').prepend(d); d.querySelector('audio').play();
  d.querySelector('button').onclick = async () => {
    const name = prompt('给这个声音起个名字（小写字母、数字、-），以后视频里用 qwen:名字'); if (!name) return;
    const r = await fetch('/keep', { method: 'POST', body: JSON.stringify({ id: j.id, name, describe: body.describe }) });
    const k = await r.json(); if (!r.ok) return alert(k.error); alert('已保存为 ' + k.name); loadVoices(); };
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def send(self, code: int, body: bytes, kind: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/voices":
            self.send(200, json.dumps(saved_voices(), ensure_ascii=False).encode(), "application/json")
        elif self.path.startswith("/audio/"):
            rid = self.path.split("/")[2].split("?")[0]
            if rid not in results:
                return self.send(404, b"", "text/plain")
            extra = {"Content-Disposition": f'attachment; filename="voice-{rid}.wav"'} if "download" in self.path else {}
            self.send(200, results[rid]["wav"], "audio/wav", extra)
        else:
            self.send(404, b"", "text/plain")

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        try:
            out = speak(req) if self.path == "/speak" else keep(req) if self.path == "/keep" else None
            if out is None:
                return self.send(404, b"", "text/plain")
            self.send(200, json.dumps(out).encode(), "application/json")
        except Exception as e:  # noqa: BLE001 — shown on the page
            self.send(400, json.dumps({"error": str(e)}, ensure_ascii=False).encode(), "application/json")

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Voice Studio: http://127.0.0.1:{PORT}  (Ctrl-C to stop)", flush=True)
    if "--no-open" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
