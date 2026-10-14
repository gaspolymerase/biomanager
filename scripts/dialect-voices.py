#!/usr/bin/env python3
"""Make BioManager's dialect narrators: a few candidates for each, to listen to and keep one.

    ~/.cache/biomanager-tts/venv/bin/python scripts/dialect-voices.py            # 3 candidates each
    … --count 5 --voices yue-a,minnan-a
    … keep yue-a=2 sichuan-a=1 shanghai-a=3 minnan-a=1

Each candidate is VoxCPM2 making a voice from zh-a's description, told to
speak the dialect, reading the voice's own line in the dialect's words; then
the candidate is copied the way scripts/qwen-voice.py will copy it, reading a
second line, so what you hear is what a video would get. Everything goes to
promo/out/dialect-voices/: the clips, and page.html (the page to listen on,
publishable as an artifact with clips/). `keep` copies the chosen candidate
to promo/voices/<name>.wav and adds it to voices.json, after which the
video scripts can use it as "qwen:<name>".

VoxCPM2 won a blind listening test of dialect TTS (scripts/dialect-bakeoff.py):
a described voice sounded right in Cantonese, Sichuanese, Shanghainese,
Hokkien and Shandong, and none did in Northeastern or Nanjing, so there is no
narrator for those two.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
VOICES = ROOT / "promo/voices"
OUT = ROOT / "promo/out/dialect-voices"

# name: (dialect, the voice's own line, the second line its copy reads), in the dialect's words
NARRATORS = {
    "yue-a": ("粤语", "大家好！今日想同大家分享一个超好用嘅实验室小工具，真系可以帮你慳返好多时间㗎。",
              "记低啲老鼠同埋试剂都好方便，打开就睇到今日要做咩，唔使再翻本簿仔。"),
    "sichuan-a": ("四川话", "大家好噻！今天想跟你们摆一个超好用的实验室小工具，硬是可以帮你省好多时间哦。",
                  "记耗子、管试剂都巴适得板，打开就晓得今天要做啥子，不消再去翻本本了。"),
    "shanghai-a": ("上海话", "大家好！今朝想搭侬分享一只老好用额实验室小工具，真额可以帮侬省交关辰光。",
                   "记老鼠、管试剂侪老方便额，一打开就晓得今朝要做啥，勿要再去翻簿子了。"),
    "minnan-a": ("闽南话", "逐家好！今仔日想欲共恁分享一个真好用的实验室小工具，实在会使共你省真济时间。",
                 "记鸟鼠、管试剂攏真方便，一下拍开就知影今仔日欲做啥，免閣去掀簿仔矣。"),
    "shandong-a": ("山东话", "大伙儿好！俺今儿个跟恁拉拉呱，说说一个怪好使的实验室小工具，真能帮恁省不老少时间嘞。",
                   "记耗子、管试剂都怪方便嘞，一打开就知道今儿个该干么，甭再去翻本子了，杠赛来！"),
}


def qwen_voice():
    spec = importlib.util.spec_from_file_location("qwen_voice", ROOT / "scripts/qwen-voice.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def describe(dialect: str) -> str:
    """zh-a's description, speaking the dialect instead of standard Mandarin."""
    zh = json.loads((VOICES / "voices.json").read_text(encoding="utf-8"))["zh-a"]["designed_from"]
    return zh.replace("，普通话标准", "").rstrip("。") + f"，说一口地道的{dialect}"


def for_page(wav: Path) -> str:
    """An AAC copy in .mp4, the audio type artifacts serve."""
    clip = OUT / "clips" / (wav.stem + ".mp4")
    clip.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["afconvert", "-f", "mp4f", "-d", "aac", str(wav), str(clip)], check=True, capture_output=True)
    return f"clips/{clip.name}"


def make(names: list[str], count: int):
    from mlx_audio.tts.utils import load_model

    qv = qwen_voice()
    model = load_model(qv.MODELS["voxcpm2"])
    rate = model.sample_rate
    (OUT / "wav").mkdir(parents=True, exist_ok=True)
    meta_file = OUT / "candidates.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    for name in names:
        dialect, line, second = NARRATORS[name]
        for k in range(1, count + 1):
            own, copy = OUT / "wav" / f"{name}-{k}.wav", OUT / "wav" / f"{name}-{k}-copy.wav"
            if copy.exists():
                continue
            t = time.time()
            parts = model.generate(text=line, instruct=describe(dialect))
            sf.write(own, np.concatenate([np.array(p.audio, dtype=np.float32).reshape(-1) for p in parts]), rate)
            sf.write(copy, qv.read(model, "voxcpm2", {"text": line}, own, second), rate)
            meta[f"{name}-{k}"] = {"name": name, "k": k, "dialect": dialect, "line": line, "second": second,
                                   "own": for_page(own), "copy": for_page(copy)}
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{name}-{k}: {time.time() - t:.0f} s", flush=True)
    page(meta)


def page(meta: dict):
    rows = {}
    for c in sorted(meta.values(), key=lambda c: (list(NARRATORS).index(c["name"]), c["k"])):
        rows.setdefault(c["name"], []).append(c)
    sections = []
    for name, cands in rows.items():
        dialect, line, second = NARRATORS[name]
        items = "".join(
            f'<div class="cand"><b>{name}={c["k"]}</b>'
            f'<span>自己那句</span><audio controls preload="none" src="{c["own"]}"></audio>'
            f'<span>复刻读新句</span><audio controls preload="none" src="{c["copy"]}"></audio></div>'
            for c in cands)
        sections.append(f'<section><h2>{dialect} · {name}</h2><p class="say">自己那句：{line}<br>新句：{second}</p>{items}</section>')
    html = PAGE.replace("<!--SECTIONS-->", "\n".join(sections))
    (OUT / "page.html").write_text(html, encoding="utf-8")
    print(f"{len(meta)} candidates → {OUT / 'page.html'}")


def keep(picks: list[str]):
    meta = json.loads((OUT / "candidates.json").read_text(encoding="utf-8"))
    voices_file = VOICES / "voices.json"
    voices = json.loads(voices_file.read_text(encoding="utf-8"))
    for pick in picks:
        name, _, k = pick.partition("=")
        c = meta.get(f"{name}-{k}")
        if not c:
            sys.exit(f"no candidate {pick!r}; page.html lists them as name=number")
        shutil.copyfile(OUT / "wav" / f"{name}-{k}.wav", VOICES / f"{name}.wav")
        voices[name] = {"language": "chinese", "model": "voxcpm2", "dialect": c["dialect"], "text": c["line"],
                        "designed_from": describe(c["dialect"])}
        print(f"kept {name} (candidate {k}): use it as qwen:{name}")
    voices_file.write_text(json.dumps(voices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


PAGE = """<title>BioManager 方言旁白</title>
<style>
:root { --bg: #f4f5f8; --card: #ffffff; --ink: #1b1d24; --muted: #646876; --line: #dfe2ea; --accent: #2f5bd3;
  --body: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #14151a; --card: #1e2027; --ink: #e9eaf0; --muted: #9a9eab; --line: #33363f; --accent: #7c9cf5; color-scheme: dark; } }
:root[data-theme="dark"] { --bg: #14151a; --card: #1e2027; --ink: #e9eaf0; --muted: #9a9eab; --line: #33363f; --accent: #7c9cf5; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.6 var(--body); }
main { max-width: 860px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 60px; }
h1 { font-size: 24px; margin: 0 0 6px; }
.lede { color: var(--muted); margin: 0 0 18px; max-width: 65ch; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; margin-bottom: 14px; }
h2 { font-size: 17px; margin: 0 0 4px; }
.say { color: var(--muted); font-size: 14px; margin: 0 0 8px; }
.cand { display: grid; grid-template-columns: 7em minmax(0, 1fr); gap: 4px 10px; align-items: center;
  padding: 10px 0; border-top: 1px solid var(--line); }
.cand b { grid-row: span 2; color: var(--accent); font-variant-numeric: tabular-nums; }
.cand span { display: none; }
audio { width: 100%; height: 36px; }
@media (min-width: 640px) { .cand { grid-template-columns: 7em 6em minmax(0, 1fr); }
  .cand span { display: block; color: var(--muted); font-size: 13px; } }
</style>
<main>
  <h1>BioManager 方言旁白</h1>
  <p class="lede">每种方言几个候选声音。先听“自己那句”，是这个声音照描述说的；再听“复刻读新句”，是视频里每句旁白会用的方式。挑两句都对味的，把它的编号（例如 yue-a=2）告诉 Claude。</p>
<!--SECTIONS-->
</main>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("keep", nargs="*", help="keep name=number … (the chosen candidates)")
    ap.add_argument("--voices", help="comma-separated; default: " + ",".join(NARRATORS))
    ap.add_argument("--count", type=int, default=3, help="candidates per voice (default 3)")
    args = ap.parse_args()
    if args.keep:
        if args.keep[0] != "keep":
            sys.exit("say: keep name=number …")
        return keep(args.keep[1:])
    names = args.voices.split(",") if args.voices else list(NARRATORS)
    for n in names:
        if n not in NARRATORS:
            sys.exit(f"no narrator {n!r}; there are " + ", ".join(NARRATORS))
    make(names, args.count)


if __name__ == "__main__":
    main()
