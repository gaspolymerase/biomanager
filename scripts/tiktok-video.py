#!/usr/bin/env python3
"""Vertical (9:16) videos for TikTok, Reels and Shorts.

    python scripts/tiktok-video.py          # every day of the launch calendar
    python scripts/tiktok-video.py 1 2      # or the days named

A day's video is made from its storyboard in promo/daily.json (the English
hook and lines, read aloud) and its footage, promo/out/<clip>/plain.mp4; a
day in promo/tiktok.json is made from what is written there instead. The
hook stays at the top of the screen with the day's title (posts.json) under
it, the footage fills the middle (each line shows its own part of the clip,
cropped to where things move and stretched to the line's length, on a
blurred copy of itself), the line is shown below a few words at a time, and
an end card gives the website. The voice is a Qwen voice, as in
explainer-video.py, over a quick beat made here. Writes
promo/out/tiktok/dayNN.mp4 and dayNN-cover.png.

TikTok runs six days behind the other platforms: day N goes out at 08:30
Beijing on promo/tiktok.json's "first" date + N - 1.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fc = _load("feature_clips", "feature-clips.py")
ex = _load("explainer_video", "explainer-video.py")
OUT = ROOT / "promo/out/tiktok"
W, H, SR = 1080, 1920, ex.SR
BOX_Y, BOX_H = 560, 860          # where the footage sits
CAPTION_Y = 1510                 # the middle of the caption line, under the footage, above TikTok's own text
GAP = 0.3
INK = (15, 23, 42)
MUTED = (148, 163, 184)
# Dark, as TikTok is, but each day in its own colour: a deep gradient and two soft glows.
TONES = [("#1e1b4b", "#0f3d3a", "#14b8a6", "#6366f1"), ("#172554", "#3b0764", "#3b82f6", "#a855f7"),
         ("#042f2e", "#172554", "#10b981", "#0ea5e9"), ("#3b0764", "#0c4a6e", "#d946ef", "#06b6d4"),
         ("#0f172a", "#064e3b", "#22c55e", "#3b82f6"), ("#4a044e", "#1e1b4b", "#ec4899", "#8b5cf6"),
         ("#422006", "#1e1b4b", "#f59e0b", "#6366f1")]


def backdrop(day: int) -> Image.Image:
    top, bottom, glow_a, glow_b = TONES[day % len(TONES)]
    img = fc.gradient((W, H), (top, bottom)).convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((-300, -260, 700, 640), fill=(*fc.hex_rgb(glow_a), 110))
    g.ellipse((480, 1300, 1480, 2260), fill=(*fc.hex_rgb(glow_b), 110))
    return Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(160)))
GREEN = (13, 148, 136)


def font(size):
    return fc.font(fc.FONT_EN, size)


def centred(draw, y, text, fnt, fill, stroke=0, stroke_fill=None):
    w = draw.textlength(text, font=fnt)
    draw.text(((W - w) / 2, y), text, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def balanced(draw, text, fnt, width):
    """Wrapped to as few lines as fit, with the words shared out evenly between them."""
    lines = fc.wrap(draw, text, fnt, width)
    words = text.split()
    if len(lines) < 2 or len(words) < 2:
        return lines
    best, limit = lines, width
    while limit > width * 0.4:
        limit -= 20
        trial = fc.wrap(draw, text, fnt, limit)
        if len(trial) > len(lines) or "" in trial:
            break
        best = trial
    return best


def background(video, path: Path):
    """The day's dark colours, with the hook at the top and the title on a green band under it."""
    img = backdrop(video.get("day", 0))
    d = ImageDraw.Draw(img)
    # As large as fits between TikTok's tabs (y 150) and the footage.
    for size in (78, 72, 66, 60, 56, 52):
        big, mark = font(size), font(int(size * 0.74))
        hook = (video["hook"] if isinstance(video["hook"], list) else balanced(d, video["hook"], big, 960))
        marks = balanced(d, video["mark"], mark, 940)
        height = len(hook) * size * 1.18 + 20 + len(marks) * (size * 0.74 + 30)
        if height <= BOX_Y - 20 - 160:
            break
    y = 160 + (BOX_Y - 20 - 160 - height) / 2
    for line in hook:
        centred(d, y, line, big, "white")
        y += size * 1.18
    y += 20
    for line in marks:
        tw = d.textlength(line, font=mark)
        x0 = (W - tw) / 2 - 24
        d.rounded_rectangle((x0, y, x0 + tw + 48, y + mark.size + 22), 18, fill=GREEN)
        d.text((x0 + 24, y + 6), line, font=mark, fill="white")
        y += mark.size + 30
    img.convert("RGB").save(path)


def caption(text, path: Path):
    img = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fnt = font(76)
    lines = fc.wrap(d, text, fnt, 860)
    y = 100 - len(lines) * 46
    for line in lines:
        centred(d, y, line, fnt, "white", 8, (15, 23, 42))
        y += 92
    img.save(path)


def end_card(end, day: int, path: Path):
    img = backdrop(day)
    d = ImageDraw.Draw(img)
    icon = Image.open(ROOT / "app/static/icon-512.png").convert("RGBA").resize((240, 240))
    img.alpha_composite(icon, ((W - 240) // 2, 520))
    centred(d, 820, end["title"], font(84), "white")
    site = font(70)
    sw = d.textlength(end["site"], font=site)
    d.rounded_rectangle(((W - sw) / 2 - 40, 960, (W + sw) / 2 + 40, 1070), 26, fill=GREEN)
    centred(d, 975, end["site"], site, "white")
    centred(d, 1140, end["follow"], font(46), MUTED)
    img.convert("RGB").save(path)


def beat(seconds: float, bpm: int = 122) -> np.ndarray:
    """A bright loop for short videos: four-on-the-floor kick, claps, sixteenth hats,
    an off-beat bass and a plucked arpeggio over Am – F – C – G."""
    step = 60 / bpm / 4                     # a sixteenth
    n = int((seconds + 1) * SR)
    left, right = np.zeros(n), np.zeros(n)
    hz = lambda m: 440 * 2 ** ((m - 69) / 12)
    rng = np.random.default_rng(5)
    chords = [[69, 72, 76], [65, 69, 72], [60, 64, 67], [67, 71, 74]]
    roots = [45, 41, 48, 43]

    def add(sound, at, pan=0.5, gain=1.0):
        i = int(at * SR)
        if i >= n:
            return
        e = min(n, i + len(sound))
        left[i:e] += sound[:e - i] * gain * (1 - pan) * 2
        right[i:e] += sound[:e - i] * gain * pan * 2

    t = np.arange(int(0.3 * SR)) / SR
    kick = np.sin(2 * np.pi * (50 + 110 * np.exp(-t * 35)) * t) * np.exp(-t * 11) * 0.5
    clap = rng.normal(0, 1, len(t)) * np.exp(-t * 22) * 0.16
    th = np.arange(int(0.04 * SR)) / SR
    hat = np.diff(rng.normal(0, 1, len(th) + 1)) * np.exp(-th * 120) * 0.05
    tb = np.arange(int(step * 1.8 * SR)) / SR
    tp = np.arange(int(step * 2.5 * SR)) / SR
    k = 0
    while k * step < seconds + 1:
        at, bar, pos = k * step, (k // 16) % 4, k % 16
        if pos % 4 == 0:
            add(kick, at)
        if pos in (4, 12):
            add(clap, at, 0.55)
        add(hat, at, 0.65, 1.0 if pos % 2 else 0.5)
        if pos % 4 == 2:                    # bass on the off-beat
            f = hz(roots[bar])
            add(np.tanh(2 * np.sin(2 * np.pi * f * tb)) * np.exp(-tb * 6) * 0.16, at)
        if pos % 2 == 0:                    # the arpeggio, up and down the chord
            m = chords[bar][[0, 1, 2, 1][(pos // 2) % 4]] + (12 if pos >= 8 else 0)
            f = hz(m)
            pluck = (np.sin(2 * np.pi * f * tp) + 0.4 * np.sin(4 * np.pi * f * tp)) * np.exp(-tp * 14) * 0.07
            add(pluck, at, 0.3 if pos % 4 else 0.7)
        k += 1
    mix = np.stack([left, right], 1)[: int(seconds * SR)]
    fade = np.minimum(1, np.minimum(np.arange(len(mix)) / (0.05 * SR), (len(mix) - np.arange(len(mix))) / (1.5 * SR)))
    return mix * fade[:, None]


def chunks(text: str, size: int = 3) -> list[str]:
    words = text.split()
    out = []
    while words:
        take = size
        # Don't leave one short word on its own at the end.
        if len(words) == size + 1:
            take = size + 1
        out.append(" ".join(words[:take]))
        words = words[take:]
    return out


def frames(source: Path, start: float, seconds: float, size=(480, 270), fps=6) -> np.ndarray:
    raw = subprocess.run([ex.FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(source),
                          "-vf", f"fps={fps},scale={size[0]}:{size[1]},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, size[1], size[0]).astype(np.int16)


def auto_crop(source: Path, start: float, end: float, frame=(1920, 1080)) -> list[int]:
    """Where the work happens in this stretch of footage (a typed cell, an opened panel,
    the pointer), zoomed in far enough that the app's text can be read on a phone."""
    fw, fh = frame
    shots = frames(source, start, end - start)
    k = fw / shots.shape[2]
    aspect = W / BOX_H
    moved = np.zeros(shots.shape[1:])
    for a, b in zip(shots, shots[1:]):
        step = np.abs(b - a) > 14
        if step.mean() < 0.25:          # a page change or a camera move says nothing about where to look
            moved += step
    ys, xs = np.nonzero(moved)
    if len(xs) < 40:
        cx, cy, w = fw / 2, fh / 2, 960
    else:
        weights = moved[ys, xs]
        def at(v, q):
            order = np.argsort(v)
            cum = np.cumsum(weights[order]) / weights.sum()
            return v[order][min(len(v) - 1, np.searchsorted(cum, q))] * k
        cx, cy = at(xs, 0.5), at(ys, 0.5)
        w = min(max((at(xs, 0.9) - at(xs, 0.1)) * 1.4, (at(ys, 0.9) - at(ys, 0.1)) * 1.4 * aspect, 640), 960)
    h = w / aspect
    x = min(max(cx - w / 2, 0), fw - w)
    y = min(max(cy - h / 2, 0), fh - h)
    return [int(x) // 2 * 2, int(y) // 2 * 2, int(w) // 2 * 2, int(h) // 2 * 2]


def from_day(n: int, board: dict, posts: dict) -> dict:
    """A day's video from its storyboard: the hook read first, then each line, with the
    footage shared out between them by how long each takes to say."""
    day = next(d for d in board["days"] if d["day"] == n)
    post = next(p for p in posts["posts"] if p["day"] == n)
    en = day["en"]
    says = [en["hook"], *en["lines"]]
    return {"day": n, "name": f"day{n:02d}", "source": f"promo/out/{day['clip']}/plain.mp4",
            "hook": en["hook"], "mark": post["title_en"],
            "lines": [{"say": s} for s in says]}


END = {"say": "It's free and open source. Find it at biomanager dot org.",
       "title": "Free & open source", "site": "biomanager.org", "follow": "Follow for a lab hack a day"}


def make(video: dict, voice: str, tmp: Path) -> Path:
    source = ROOT / video["source"]
    lines = video["lines"]
    video.setdefault("end", END)
    says = [l["say"] for l in lines] + [video["end"]["say"]]
    ex.prefetch([(voice, s) for s in says])
    audio = [ex.speak(s, voice, 0, tmp) for s in says]
    # Each line's footage lasts as long as the line, plus a breath.
    lead = 0.15
    spans, t = [], lead
    for a in audio:
        spans.append((t, len(a) / SR))
        t += len(a) / SR + GAP
    footage_end = spans[len(lines) - 1][0] + spans[len(lines) - 1][1] + GAP
    end_len = spans[-1][1] + 1.6
    total = footage_end + end_len

    # Lines without their own part of the footage share it out by how long each is said for.
    if any("from" not in line for line in lines):
        clip = ex.duration(source) - 0.1
        said = [spans[i][1] + GAP for i in range(len(lines))]
        at = 0.0
        for line, s_ in zip(lines, said):
            line["from"], line["to"] = at, at + clip * s_ / sum(said)
            at = line["to"]
    for line in lines:
        line.setdefault("crop", auto_crop(source, line["from"], line["to"]))
    # The footage: each part cropped, fitted to the box on a blurred copy, stretched to its line.
    parts = []
    for i, line in enumerate(lines):
        start, length = spans[i]
        seconds = (length + GAP + (lead if i == 0 else 0))
        x, y, w, h = line["crop"]
        speed = seconds / (line["to"] - line["from"])
        part = tmp / f"part{i}.mp4"
        subprocess.run([ex.FFMPEG, "-v", "error", "-y", "-ss", str(line["from"]), "-t", str(line["to"] - line["from"]),
                        "-i", str(source), "-filter_complex",
                        f"[0:v]setpts=PTS*{speed:.4f},crop={w}:{h}:{x}:{y},split[a][b];"
                        f"[a]scale={W}:{BOX_H}:force_original_aspect_ratio=increase,crop={W}:{BOX_H},boxblur=30:2[bg];"
                        f"[b]scale={W}:{BOX_H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p[v]",
                        "-map", "[v]", "-an", "-t", f"{seconds:.3f}", "-c:v", "libx264", "-crf", "16", str(part)],
                       check=True)
        parts.append(part)
    (tmp / "parts.txt").write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    footage = tmp / "footage.mp4"
    subprocess.run([ex.FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "parts.txt"),
                    "-c", "copy", str(footage)], check=True)

    # The sound: the voice, the music under it, a pop at each cut.
    # The beat drops back while someone speaks and comes up in the gaps.
    level = np.full(int(total * SR), 0.55)
    for start, length in spans:
        level[int(start * SR):int((start + length) * SR)] = 0.3
    level = np.convolve(level, np.ones(int(0.12 * SR)) / int(0.12 * SR), mode="same")
    mix = beat(total) * level[:, None]
    for (start, _), a in zip(spans, audio):
        ex.place(mix, a, start)
    for start, _ in spans[1:]:
        ex.place(mix, ex.pop(660) * 0.5, start - 0.12)
    mix *= min(1.0, 0.9 / max(1e-6, float(np.abs(mix).max())))
    (tmp / "mix.raw").write_bytes(mix.astype(np.float32).tobytes())

    # The captions, a few words at a time, shared out over each line by length.
    background(video, tmp / "bg.png")
    end_card(video["end"], video.get("day", 0), tmp / "end.png")
    overlays = []
    for i, (say, (start, length)) in enumerate(zip(says[:-1], spans)):
        bits = chunks(say)
        weights = [len(b) + 4 for b in bits]
        t = start
        for j, bit in enumerate(bits):
            d = length * weights[j] / sum(weights)
            png = tmp / f"cap{i}-{j}.png"
            caption(bit, png)
            overlays.append((png, t, t + d + (GAP if j == len(bits) - 1 else 0)))
            t += d
    # Stills last only as long as the video, or ffmpeg queues their frames without end.
    still = ["-loop", "1", "-framerate", "30", "-t", f"{total:.3f}", "-i"]
    inputs = [*still, str(tmp / "bg.png"), "-i", str(footage), *still, str(tmp / "end.png"),
              "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(tmp / "mix.raw")]
    graph = [f"[0:v][1:v]overlay=0:{BOX_Y}:eof_action=pass[v0]"]
    for k, (png, a, b) in enumerate(overlays):
        inputs += [*still, str(png)]
        graph.append(f"[v{k}][{4 + k}:v]overlay=0:{CAPTION_Y - 100}:enable='between(t,{a:.3f},{b:.3f})'[v{k + 1}]")
    n = len(overlays)
    graph.append(f"[2:v]format=rgba,fade=in:st={footage_end:.3f}:d=0.35:alpha=1[end]")
    graph.append(f"[v{n}][end]overlay=0:0:enable='gte(t,{footage_end:.3f})',format=yuv420p[out]")
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{video['name']}.mp4"
    subprocess.run([ex.FFMPEG, "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph),
                    "-map", "[out]", "-map", "3:a", "-r", "30", "-c:v", "libx264", "-crf", "20",
                    "-preset", "medium", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
                    "-movflags", "+faststart", str(out)], check=True)
    # The cover: the first frame with the hook and a caption-free picture.
    subprocess.run([ex.FFMPEG, "-v", "error", "-y", "-ss", "1.2", "-i", str(out), "-frames:v", "1",
                    str(OUT / f"{video['name']}-cover.png")], check=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("days", nargs="*", type=int)
    args = ap.parse_args()
    tiktok = json.loads((ROOT / "promo/tiktok.json").read_text(encoding="utf-8"))
    board = json.loads((ROOT / "promo/daily.json").read_text(encoding="utf-8"))
    posts = json.loads((ROOT / "promo/posts.json").read_text(encoding="utf-8"))
    own = {v["day"]: v for v in tiktok["videos"]}
    for n in args.days or [d["day"] for d in board["days"]]:
        video = own.get(n) or from_day(n, board, posts)
        video.setdefault("name", f"day{n:02d}")
        with tempfile.TemporaryDirectory(prefix="tiktok-") as tmp:
            out = make(video, tiktok["voice"], Path(tmp))
        print(f"day {n:2d}: {out.relative_to(ROOT)} ({ex.duration(out):.1f} s)", flush=True)


if __name__ == "__main__":
    main()
