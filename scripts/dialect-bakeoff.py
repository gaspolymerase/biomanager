#!/usr/bin/env python3
"""Make the same lines in several Chinese dialects with several TTS systems, for a blind listening test.

    ~/.cache/biomanager-tts/venv/bin/python scripts/dialect-bakeoff.py
    … --systems voxcpm2-design,qwen-api --dialects 粤语,四川话
    … --list            # the systems, and which can run here

Every dialect in promo/dialect-bakeoff/lines.json says two lines: the shared
one in standard written Chinese (can the system turn it into the dialect by
itself?) and one written in the dialect's own words. Each system reads both,
the clips are levelled to the same loudness, and
promo/out/dialect-bakeoff/index.html is a page to listen to them blind:
the clips of a line come in a shuffled order as A, B, C…, each is scored
1–5 for how authentic and how natural it sounds, and 揭晓 shows which
system made which. Published as an artifact on claude.ai (artifact.html with
clips/), each listener's scores are kept in the page and the results table
averages everyone's; where that can't save, a listener copies their scores
and the organiser pastes them in.

Systems:
    qwen-clone      Qwen3-TTS Base copying promo/voices/zh-a.wav: what the videos use now
    qwen-design     Qwen3-TTS VoiceDesign, the dialect asked for in the voice description
    qwen-speaker    Qwen3-TTS CustomVoice's own dialect speaker (Eric, 四川话 only)
    voxcpm2-design  VoxCPM2, the dialect asked for in the voice description
    voxcpm2-clone   VoxCPM2 copying zh-a.wav, told which dialect to speak
    qwen-api        Alibaba Cloud's qwen3-tts-flash dialect voices (needs DASHSCOPE_API_KEY;
                    every line is sent to Alibaba Cloud)
    cosyvoice3      Fun-CosyVoice3-0.5B from its own checkout: COSYVOICE_DIR is the
                    FunAudioLLM/CosyVoice repository with the model in
                    pretrained_models/Fun-CosyVoice3-0.5B, COSYVOICE_PYTHON its interpreter

The local systems run with MLX in the TTS environment of scripts/qwen-voice.py;
their models come from Hugging Face the first time (Qwen3-TTS VoiceDesign and
CustomVoice 2.7 GB each, VoxCPM2 3.2 GB). Clips that already exist are kept,
so a run only makes what is missing; --again remakes them.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "promo/dialect-bakeoff"
NARRATOR = ROOT / "promo/voices/zh-a.wav"
OUT = ROOT / "promo/out/dialect-bakeoff"

QWEN_BASE = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-6bit"
QWEN_DESIGN = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-6bit"
QWEN_CUSTOM = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit"
VOXCPM2 = "mlx-community/VoxCPM2-8bit"
QWEN_API = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

# qwen3-tts-flash's dialect voices (Model Studio's Qwen-TTS voice list)
API_VOICES = {"粤语": "Kiki", "四川话": "Sunny", "上海话": "Jada", "闽南话": "Roy", "南京话": "Li"}
# what CosyVoice 3's instructions call each dialect
COSY_NAMES = {"粤语": "广东话"}

SYSTEMS = {
    "qwen-clone": "Qwen3-TTS · 复刻 zh-a（现在的旁白）",
    "qwen-design": "Qwen3-TTS · 描述音色",
    "qwen-speaker": "Qwen3-TTS · 自带方言音色",
    "voxcpm2-design": "VoxCPM2 · 描述音色",
    "voxcpm2-clone": "VoxCPM2 · 复刻 zh-a",
    "qwen-api": "Qwen3-TTS Flash API · 方言音色",
    "cosyvoice3": "CosyVoice 3 · 复刻 zh-a",
}


def unavailable(system: str) -> str | None:
    """Why a system can't run here, or None."""
    if system == "qwen-api" and not os.environ.get("DASHSCOPE_API_KEY"):
        return "set DASHSCOPE_API_KEY (Alibaba Cloud Model Studio, Beijing region)"
    if system == "cosyvoice3":
        repo, python = os.environ.get("COSYVOICE_DIR"), os.environ.get("COSYVOICE_PYTHON")
        if not (repo and python):
            return "set COSYVOICE_DIR and COSYVOICE_PYTHON (see the top of this script)"
        if not (Path(repo) / "pretrained_models/Fun-CosyVoice3-0.5B").exists():
            return f"no pretrained_models/Fun-CosyVoice3-0.5B in {repo}"
    return None


def can_say(system: str, dialect: str) -> bool:
    if system == "qwen-speaker":
        return dialect == "四川话"
    if system == "qwen-api":
        return dialect in API_VOICES
    return True


def narrator_text() -> str:
    voices = json.loads((ROOT / "promo/voices/voices.json").read_text(encoding="utf-8"))
    return voices["zh-a"]["text"]


def as_array(parts) -> np.ndarray:
    return np.concatenate([np.array(p.audio, dtype=np.float32).reshape(-1) for p in parts])


def mlx_jobs(system: str, jobs: list[dict], voice: str):
    """Yield (job, audio, sample_rate) for each job, with the system's model loaded once."""
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model

    name = {"qwen-clone": QWEN_BASE, "qwen-design": QWEN_DESIGN, "qwen-speaker": QWEN_CUSTOM,
            "voxcpm2-design": VOXCPM2, "voxcpm2-clone": VOXCPM2}[system]
    model = load_model(name)
    ref_text = narrator_text()
    try:
        for job in jobs:
            d, text, t = job["dialect"], job["text"], time.time()
            if system == "qwen-clone":
                parts = model.generate(text=text, ref_audio=str(NARRATOR), ref_text=ref_text, lang_code="chinese")
            elif system == "qwen-design":
                parts = model.generate_voice_design(text=text, instruct=f"{voice}，说一口地道的{d}",
                                                    language="chinese")
            elif system == "qwen-speaker":
                parts = model.generate_custom_voice(text=text, speaker="Eric", language="chinese")
            elif system == "voxcpm2-design":
                parts = model.generate(text=text, instruct=f"{voice}，说一口地道的{d}")
            else:
                parts = model.generate(text=text, ref_audio=str(NARRATOR), instruct=f"说一口地道的{d}")
            audio = as_array(parts)
            job["seconds_to_make"] = time.time() - t
            yield job, audio, model.sample_rate
    finally:
        del model
        gc.collect()
        mx.clear_cache()


def api_jobs(jobs: list[dict]):
    key = os.environ["DASHSCOPE_API_KEY"]
    for job in jobs:
        t = time.time()
        body = {"model": "qwen3-tts-flash",
                "input": {"text": job["text"], "voice": API_VOICES[job["dialect"]], "language_type": "Chinese"}}
        req = urllib.request.Request(QWEN_API, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            url = json.load(r)["output"]["audio"]["url"]
        with urllib.request.urlopen(url, timeout=120) as r, tempfile.NamedTemporaryFile(suffix=".wav") as f:
            f.write(r.read())
            f.flush()
            audio, rate = sf.read(f.name, dtype="float32", always_2d=True)
        job["seconds_to_make"] = time.time() - t
        yield job, audio.mean(axis=1), rate


COSY_RUNNER = r"""
import json, sys
repo, jobs_file, prompt_wav = sys.argv[1:4]
sys.path[:0] = [repo, repo + "/third_party/Matcha-TTS"]
import torchaudio
from cosyvoice.cli.cosyvoice import AutoModel
model = AutoModel(model_dir=repo + "/pretrained_models/Fun-CosyVoice3-0.5B")
for job in json.load(open(jobs_file, encoding="utf-8")):
    instruct = "You are a helpful assistant. 请用" + job["say_as"] + "表达。<|endofprompt|>"
    for i, part in enumerate(model.inference_instruct2(job["text"], instruct, prompt_wav, stream=False)):
        torchaudio.save(job["raw"] + (f".{i}" if i else ""), part["tts_speech"], model.sample_rate)
    print("cosyvoice3:", job["raw"], flush=True)
"""


def cosy_jobs(jobs: list[dict]):
    repo, python = os.environ["COSYVOICE_DIR"], os.environ["COSYVOICE_PYTHON"]
    with tempfile.TemporaryDirectory(prefix="cosy-") as tmp:
        for n, job in enumerate(jobs):
            job["say_as"] = COSY_NAMES.get(job["dialect"], job["dialect"])
            job["raw"] = f"{tmp}/{n}.wav"
        listing = Path(tmp, "jobs.json")
        listing.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
        runner = Path(tmp, "runner.py")
        runner.write_text(COSY_RUNNER, encoding="utf-8")
        t = time.time()
        subprocess.run([python, str(runner), repo, str(listing), str(NARRATOR)], check=True)
        each = (time.time() - t) / len(jobs)
        for job in jobs:
            raws = sorted(Path(tmp).glob(Path(job["raw"]).name + "*"))
            audio = [sf.read(p, dtype="float32", always_2d=True) for p in raws]
            job["seconds_to_make"] = each       # one load for every line, shared out
            yield job, np.concatenate([a.mean(axis=1) for a, _ in audio]), audio[0][1]


def level(audio: np.ndarray) -> np.ndarray:
    """The same loudness for every clip (RMS −20 dBFS, peaks under −1 dBFS), so louder doesn't sound better."""
    audio = audio - audio.mean()
    rms = float(np.sqrt(np.mean(audio ** 2))) or 1.0
    audio = audio * (10 ** (-20 / 20) / rms)
    peak = float(np.abs(audio).max()) or 1.0
    return audio * min(1.0, 10 ** (-1 / 20) / peak)


def save(job: dict, audio: np.ndarray, rate: int):
    wav = OUT / "wav" / f"{job['id']}.wav"
    clip = OUT / "clips" / f"{job['id']}.mp4"   # AAC in MP4, a type artifacts serve
    wav.parent.mkdir(parents=True, exist_ok=True)
    clip.parent.mkdir(parents=True, exist_ok=True)
    sf.write(wav, level(audio), rate)
    subprocess.run(["afconvert", "-f", "mp4f", "-d", "aac", str(wav), str(clip)],
                   check=True, capture_output=True)
    return {"id": job["id"], "dialect": job["dialect"], "line": job["line"], "system": job["system"],
            "file": f"clips/{clip.name}", "seconds": round(len(audio) / rate, 2)}


def build_page(lines: dict):
    meta_file = OUT / "clips.json"
    clips = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    clips = {k: v for k, v in clips.items() if (OUT / v["file"]).exists()}
    data = {
        "voice": lines["voice"], "shared": lines["shared"],
        "dialects": [{"name": d, "en": v["en"], "local": v["local"]} for d, v in lines["dialects"].items()],
        "systems": SYSTEMS,
        "clips": sorted(clips.values(), key=lambda c: c["id"]),
    }
    page = (HERE / "page.html").read_text(encoding="utf-8")
    page = page.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    # artifact.html is the page as published on claude.ai, which adds the document around it
    (OUT / "artifact.html").write_text(page, encoding="utf-8")
    (OUT / "index.html").write_text(
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n<body>\n'
        f"{page}</body>\n</html>\n", encoding="utf-8")
    return len(data["clips"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--systems", help="comma-separated; default: every one that can run here")
    ap.add_argument("--dialects", help="comma-separated; default: all in lines.json")
    ap.add_argument("--again", action="store_true", help="remake clips that already exist")
    ap.add_argument("--list", action="store_true", help="list the systems and stop")
    ap.add_argument("--page", action="store_true", help="only rebuild the page")
    args = ap.parse_args()

    if args.list:
        for s, label in SYSTEMS.items():
            why = unavailable(s)
            print(f"{s:15} {label}" + (f"\n{'':15} (not here: {why})" if why else ""))
        return

    lines = json.loads((HERE / "lines.json").read_text(encoding="utf-8"))
    if args.page:
        print(f"{build_page(lines)} clips → {OUT / 'index.html'}")
        return

    dialects = args.dialects.split(",") if args.dialects else list(lines["dialects"])
    if args.systems:
        systems = args.systems.split(",")
        for s in systems:
            if s not in SYSTEMS:
                sys.exit(f"no system {s!r}; --list shows them")
            if why := unavailable(s):
                sys.exit(f"{s}: {why}")
    else:
        systems = [s for s in SYSTEMS if not unavailable(s)]
        skipped = [s for s in SYSTEMS if s not in systems]
        if skipped:
            print("skipping " + ", ".join(skipped) + " (--list says why)")

    meta_file = OUT / "clips.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    for system in systems:
        jobs = []
        for d in dialects:
            if d not in lines["dialects"]:
                sys.exit(f"no dialect {d!r} in lines.json")
            if not can_say(system, d):
                continue
            en = lines["dialects"][d]["en"].lower()
            for line, text in (("shared", lines["shared"]), ("local", lines["dialects"][d]["local"])):
                cid = f"{en}-{line}-{system}"
                if args.again or not (OUT / "clips" / f"{cid}.mp4").exists():
                    jobs.append({"id": cid, "dialect": d, "line": line, "system": system, "text": text})
        if not jobs:
            continue
        print(f"{system}: {len(jobs)} clips", flush=True)
        if system == "qwen-api":
            made = api_jobs(jobs)
        elif system == "cosyvoice3":
            made = cosy_jobs(jobs)
        else:
            made = mlx_jobs(system, jobs, lines["voice"])
        for job, audio, rate in made:
            clip = save(job, audio, rate)
            clip["seconds_to_make"] = round(job["seconds_to_make"], 1)
            meta[clip["id"]] = clip
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {clip['id']}: {clip['seconds']} s of speech in {clip['seconds_to_make']} s", flush=True)
    n = build_page(lines)
    print(f"{n} clips → {OUT / 'index.html'}")


if __name__ == "__main__":
    main()
