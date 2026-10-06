#!/usr/bin/env python3
"""Record the launch posts' feature clips from a fresh demo lab.

    python scripts/feature-clips.py promo/out                 # every clip
    python scripts/feature-clips.py promo/out import cards    # just these
    python scripts/feature-clips.py --list

Each clip is a short scripted walk through one feature: a cursor you can
see, typing at a human pace, and a smooth zoom onto the part that matters,
in the style of Screen Studio or OpenScreen. It is rendered for each place
it is posted:

    <out>/<clip>/landscape-en.mp4   16:9 with an English title (X, LinkedIn, Facebook)
    <out>/<clip>/landscape-zh.mp4   16:9 with a Chinese title (Bilibili)
    <out>/<clip>/portrait-zh.mp4    3:4 with a Chinese title (Xiaohongshu)
    <out>/<clip>/clip.gif           960 px wide, for the README and GitHub
    <out>/<clip>/cover-en.png, cover-zh.png, cover-portrait.png
    <out>/<clip>/plain.mp4           with --plain: the footage alone, for the explainer

With no --base it makes its own demo lab (scripts/demo-data.py) in a
temporary folder and runs the app on a free port, so every recording starts
from the same made-up lab. The titles come from promo/posts.json.

Needs Playwright, Pillow and a bundled ffmpeg, none of them app dependencies:
    pip install playwright pillow imageio-ffmpeg && playwright install chromium
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FPS = 30
VIEW = (1280, 800)      # the browser window, in CSS pixels
PHONE = (390, 844)
SCALE = 2               # device pixels per CSS pixel in the recording

FONT_EN = ("/System/Library/Fonts/SFNS.ttf", None)
FONT_ZH = ("/System/Library/Fonts/STHeiti Medium.ttc", 0)
ICON = ROOT / "app/static/icon-512.png"

# One pastel gradient per clip, in turn: light enough for dark type, bright
# enough to stand out in a feed.
GRADIENTS = [
    ("#dbeafe", "#ede9fe"), ("#dcfce7", "#e0f2fe"), ("#fef3c7", "#fce7f3"),
    ("#e0e7ff", "#fae8ff"), ("#ccfbf1", "#e0e7ff"), ("#ffe4e6", "#fef9c3"),
]

# ---------------------------------------------------------------------------
# The cursor, drawn into the page so it is in every frame
# ---------------------------------------------------------------------------

CURSOR_JS = r"""
(() => {
  const KEY = '__clip_cursor';
  const start = JSON.parse(sessionStorage.getItem(KEY) || '[640,420]');
  const put = () => {
    if (document.getElementById('__cur') || !document.body) return;
    const c = document.createElement('div');
    c.id = '__cur';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M4 2.5v18.2l4.6-4.3 3.3 7.3 3.1-1.4-3.3-7.1h6.5z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position: 'fixed', left: 0, top: 0, zIndex: 2147483647, pointerEvents: 'none',
      transform: `translate(${start[0] - 4}px, ${start[1] - 2}px)`, filter: 'drop-shadow(0 1px 1.5px rgba(0,0,0,.35))'});
    document.documentElement.appendChild(c);
  };
  document.addEventListener('DOMContentLoaded', put);
  put();
  const follow = e => {
    put();
    const c = document.getElementById('__cur');
    if (c) c.style.transform = `translate(${e.clientX - 4}px, ${e.clientY - 2}px)`;
    sessionStorage.setItem(KEY, JSON.stringify([e.clientX, e.clientY]));
  };
  addEventListener('mousemove', follow, true);
  addEventListener('dragover', follow, true);
  addEventListener('mousedown', e => {
    const r = document.createElement('div');
    Object.assign(r.style, {position: 'fixed', left: e.clientX - 18 + 'px', top: e.clientY - 18 + 'px', width: '36px',
      height: '36px', borderRadius: '50%', background: 'rgba(0,122,255,.28)', zIndex: 2147483646,
      pointerEvents: 'none', transition: 'transform .45s ease-out, opacity .45s ease-out', transform: 'scale(.3)'});
    document.documentElement.appendChild(r);
    requestAnimationFrame(() => { r.style.transform = 'scale(1.3)'; r.style.opacity = '0'; });
    setTimeout(() => r.remove(), 500);
  }, true);
})();
"""


def ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


class Director:
    """Drives one page like a person would, and remembers where to zoom."""

    def __init__(self, page, base: str, size: tuple[int, int]):
        self.page, self.base, self.size = page, base, size
        self.frames: list[tuple[float, bytes]] = []
        self.zooms: list[tuple[float, tuple[float, float, float]]] = []  # (t, (cx, cy, scale))
        self.pos = (size[0] / 2, size[1] / 2)
        self.t0 = None
        self.cover_at = None
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._frame)

    # -- recording -----------------------------------------------------------
    def _frame(self, ev):
        self.frames.append((ev["metadata"]["timestamp"], base64.b64decode(ev["data"])))
        try:
            self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:  # noqa: BLE001 — the page may be navigating
            pass

    def start(self):
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92,
                                               "maxWidth": self.size[0] * SCALE, "maxHeight": self.size[1] * SCALE})
        self.t0 = time.time()
        self.zooms.append((0.0, (self.size[0] / 2, self.size[1] / 2, 1.0)))
        self.wait(0.6)

    def stop(self):
        self.wait(0.8)
        self.cdp.send("Page.stopScreencast")
        return time.time() - self.t0

    def now(self) -> float:
        return time.time() - self.t0

    # -- acting --------------------------------------------------------------
    def wait(self, seconds: float):
        self.page.wait_for_timeout(int(seconds * 1000))

    def goto(self, path: str, settle: float = 0.6):
        self.page.goto(self.base + path)
        self.page.wait_for_load_state("networkidle")
        self.page.evaluate("document.activeElement && document.activeElement.blur()")
        self.wait(settle)

    def loc(self, target):
        return self.page.locator(target).first if isinstance(target, str) else target

    def centre(self, target) -> tuple[float, float]:
        box = self.loc(target).bounding_box()
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def move(self, target, seconds: float = 0.7):
        loc = self.loc(target)
        loc.scroll_into_view_if_needed()
        x, y = self.centre(loc)
        x0, y0 = self.pos
        # Paced by the clock: each mouse call is a round trip to the browser.
        start = time.time()
        while (elapsed := time.time() - start) < seconds:
            k = ease(elapsed / seconds)
            self.page.mouse.move(x0 + (x - x0) * k, y0 + (y - y0) * k)
            self.page.wait_for_timeout(8)
        self.page.mouse.move(x, y)
        self.pos = (x, y)

    def click(self, target, after: float = 0.6, seconds: float = 0.7):
        self.move(target, seconds)
        self.wait(0.15)
        self.page.mouse.down()
        self.page.wait_for_timeout(70)
        self.page.mouse.up()
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        self.wait(after)

    def type(self, target, text: str, delay: int = 55, after: float = 0.4):
        self.click(target, after=0.2)
        self.page.keyboard.type(text, delay=delay)
        self.wait(after)

    def key(self, keys: str, after: float = 0.5):
        self.page.keyboard.press(keys)
        self.wait(after)

    def scroll(self, dy: float, seconds: float = 0.9, at=None):
        if at is not None:
            self.move(at, 0.4)
        start, done = time.time(), 0.0
        while done < dy if dy > 0 else done > dy:
            k = ease(min((time.time() - start) / seconds, 1.0))
            step = dy * k - done
            if step:
                self.page.mouse.wheel(0, step)
                done += step
            if k >= 1:
                break
            self.page.wait_for_timeout(8)
        self.wait(0.3)

    def zoom(self, target=None, scale: float = 1.6, box=None):
        """Zoom smoothly onto an element (or a box in CSS pixels), from now."""
        if box is None:
            b = self.loc(target).bounding_box()
            box = (b["x"], b["y"], b["width"], b["height"])
        x, y, w, h = box
        fit = min(self.size[0] / max(w * 1.1, 1), self.size[1] / max(h * 1.1, 1))
        self.zooms.append((self.now(), (x + w / 2, y + h / 2, max(1.0, min(scale, fit)))))

    def cover(self):
        """Use this moment for the cover images (else 1.5 s in)."""
        self.cover_at = self.now()

    def unzoom(self):
        self.zooms.append((self.now(), (self.size[0] / 2, self.size[1] / 2, 1.0)))


# ---------------------------------------------------------------------------
# The clips live in promo/clips/*.py. Each module has
#     CLIPS = {"name": [("desktop" | "phone", walk), ...]}   segments, in order
#     PREPARE = {"name": prepare}                             optional
# where walk(d) gets a Director on a signed-in page (it calls d.start() when
# the recording should begin, and may call d.cover() at the frame the cover
# images should show) and prepare(api, base) makes the records the
# clip needs first, through the app's own routes (api is Playwright's
# APIRequestContext, signed in as the demo admin).
# ---------------------------------------------------------------------------

def load_clips():
    import importlib.util
    clips, prepare = {}, {}
    for path in sorted((ROOT / "promo/clips").glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"clips_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        clips.update(getattr(mod, "CLIPS", {}))
        prepare.update(getattr(mod, "PREPARE", {}))
    return clips, prepare


# ---------------------------------------------------------------------------
# Rendering: resample to 30 fps, zoom, frame it on a gradient, encode
# ---------------------------------------------------------------------------

def hex_rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def font(which, size):
    path, index = which
    f = ImageFont.truetype(path, size, index=index or 0)
    if path.endswith("SFNS.ttf"):
        f.set_variation_by_name("Bold")
    return f


def gradient(size, colours) -> Image.Image:
    w, h = size
    a, b = hex_rgb(colours[0]), hex_rgb(colours[1])
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(0, w, 4):
            k = (x / w * 0.6 + y / h * 0.4)
            c = tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))
            for dx in range(4):
                if x + dx < w:
                    px[x + dx, y] = c
    return img


def wrap(draw, text, fnt, width):
    """Break a title into lines that fit; Chinese breaks between characters."""
    spaced = " " in text and not any("一" <= c <= "鿿" for c in text)
    words = text.split(" ") if spaced else list(text)
    joiner = " " if spaced else ""
    lines, line = [], ""
    for w in words:
        trial = (line + joiner + w) if line else w
        if draw.textlength(trial, font=fnt) <= width:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


class Layout:
    """The canvas for one render (gradient, title, bullets, brand) and where
    a desktop window or a phone sits on it."""

    def __init__(self, canvas, title, subtitle, lang, colours, bullets=(), plain=False):
        self.canvas = canvas
        cw, ch = canvas
        self.portrait = portrait = ch > cw
        face = FONT_ZH if lang == "zh" else FONT_EN
        title_font = font(face, int(cw * (0.066 if portrait else 0.034)))
        sub_font = font(face, int(cw * (0.036 if portrait else 0.018)))
        bg = gradient(canvas, colours)
        draw = ImageDraw.Draw(bg)
        lines = wrap(draw, title, title_font, cw * 0.86)
        lh = int(title_font.size * 1.28)
        top = int(ch * (0.06 if portrait else 0.045))
        for i, line in enumerate(lines):
            tw = draw.textlength(line, font=title_font)
            draw.text(((cw - tw) / 2, top + i * lh), line, font=title_font, fill=(17, 24, 39))
        y = top + len(lines) * lh
        if subtitle:
            tw = draw.textlength(subtitle, font=sub_font)
            draw.text(((cw - tw) / 2, y + int(sub_font.size * 0.2)), subtitle, font=sub_font, fill=(75, 85, 99))
            y += int(sub_font.size * 1.6)
        if plain:
            # Footage for a longer video: no words, and room below for its subtitles.
            bottom = ch - int(ch * 0.17)
        else:
            # The brand mark along the bottom.
            brand = "BioManager｜免费开源" if lang == "zh" else "BioManager · free and open source"
            bfont = font(face, int(cw * (0.032 if portrait else 0.016)))
            icon = Image.open(ICON).convert("RGBA").resize((int(bfont.size * 1.6),) * 2, Image.LANCZOS)
            bw = icon.width + 12 + draw.textlength(brand, font=bfont)
            bx, by = int((cw - bw) / 2), int(ch - bfont.size * (2.4 if portrait else 2.2))
            bg.paste(icon, (bx, by - int(bfont.size * 0.35)), icon)
            draw.text((bx + icon.width + 12, by), brand, font=bfont, fill=(55, 65, 81))
            bottom = by - int(ch * 0.03)
        # On the portrait canvas, up to three short points under the window.
        if portrait and bullets:
            pfont = font(face, int(cw * 0.04))
            plh = int(pfont.size * 1.6)
            block = plh * len(bullets)
            py = bottom - block
            indent = pfont.size * 1.5
            widest = max(indent + draw.textlength(b, font=pfont) for b in bullets)
            px = (cw - widest) / 2
            u = pfont.size
            for i, line in enumerate(bullets):
                ty = py + i * plh
                # A tick drawn as lines: the Chinese face has no ✓.
                draw.line([(px + u * 0.1, ty + u * 0.55), (px + u * 0.4, ty + u * 0.85), (px + u * 0.95, ty + u * 0.2)],
                          fill=(22, 163, 74), width=max(3, int(u * 0.13)), joint="curve")
                draw.text((px + indent, ty), line, font=pfont, fill=(31, 41, 55))
            bottom = py - int(ch * 0.025)
        self.area = (y + int(ch * 0.03), bottom)
        self.bg = bg
        self._placed = {}

    def place(self, source_size, phone):
        """(frame background, window size, where, mask) for this kind of segment."""
        key = (source_size, phone)
        if key in self._placed:
            return self._placed[key]
        cw, ch = self.canvas
        sw, sh = source_size
        chrome = 0 if phone else 0.035          # the title bar, as a share of the window's width
        max_w = cw * (0.92 if self.portrait else 0.86)
        max_h = self.area[1] - self.area[0]
        k = min(max_w / sw, max_h / (sh + sw * chrome))
        win = (int(sw * k), int(sh * k))
        bar = int(win[0] * chrome)
        bezel = int(win[0] * 0.035) if phone else 0
        outer = (win[0] + 2 * bezel, win[1] + bar + 2 * bezel)
        at = (int((cw - outer[0]) / 2), int(self.area[0] + (max_h - outer[1]) / 2))
        radius = int(outer[0] * (0.14 if phone else 0.012))
        frame = self.bg.convert("RGBA")
        shadow = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((at[0], at[1] + 16, at[0] + outer[0], at[1] + outer[1] + 16),
                                                 radius, fill=(30, 41, 59, 75))
        frame = Image.alpha_composite(frame, shadow.filter(ImageFilter.GaussianBlur(30)))
        body = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
        d = ImageDraw.Draw(body)
        d.rounded_rectangle((at[0], at[1], at[0] + outer[0], at[1] + outer[1]), radius,
                            fill=(17, 17, 17, 255) if phone else (236, 236, 238, 255))
        if not phone:
            # A Mac window's traffic lights.
            r = bar * 0.2
            for i, colour in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
                cx, cy = at[0] + bar * 0.55 + i * bar * 0.62, at[1] + bar / 2
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=colour + (255,))
        frame = Image.alpha_composite(frame, body).convert("RGB")
        inner_at = (at[0] + bezel, at[1] + bar + bezel)
        mask = Image.new("L", win, 0)
        inner_r = int(win[0] * 0.11) if phone else 0
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, *win), inner_r, fill=255)
        if not phone:
            # Round only the window's bottom corners; the bar has the top ones.
            mask = Image.new("L", win, 255)
            md = ImageDraw.Draw(mask)
            corner = Image.new("L", (radius * 2, radius * 2), 0)
            ImageDraw.Draw(corner).pieslice((0, 0, radius * 4, radius * 4), 180, 270, fill=255)
            for x0, y0, rot in ((0, win[1] - radius, 90), (win[0] - radius, win[1] - radius, 180)):
                md.rectangle((x0, y0, x0 + radius, y0 + radius), fill=0)
                piece = Image.new("L", (radius, radius), 0)
                ImageDraw.Draw(piece).pieslice((0, 0, radius * 2, radius * 2), 180, 270, fill=255)
                mask.paste(piece.rotate(rot), (x0, y0))
        self._placed[key] = (frame, win, inner_at, mask)
        return self._placed[key]


def zoom_at(zooms, t):
    """The (cx, cy, scale) at time t, easing 0.7 s from wherever it was into each keyframe."""
    state = zooms[0][1]
    for i in range(1, len(zooms)):
        tk, target = zooms[i]
        if t < tk:
            break
        start = zoom_at(zooms[:i], tk)
        k = ease((t - tk) / 0.7)
        state = tuple(start[j] + (target[j] - start[j]) * k for j in range(3))
    return state


def frames_at_fps(frames, t0, duration):
    frames = sorted(frames)
    i, out = 0, []
    for n in range(int(duration * FPS)):
        t = t0 + n / FPS
        while i + 1 < len(frames) and frames[i + 1][0] <= t:
            i += 1
        out.append(i)
    return frames, out


def render(segments, layout: Layout, path: Path):
    """segments: [(frames, zooms, source_size, phone, t0, duration, cover_at)], one after another."""
    cw, ch = layout.canvas
    proc = subprocess.Popen([FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{cw}x{ch}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                             "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
                            stdin=subprocess.PIPE)
    cover, written = None, 0
    cover_n = next((int(sum(sg[5] for sg in segments[:i]) * FPS + sg[6] * FPS)
                    for i, sg in enumerate(segments) if sg[6] is not None), int(1.5 * FPS))
    for frames, zooms, size, phone, t0, duration, _ in segments:
        bg, win_size, at, mask = layout.place(size, phone)
        frames, index = frames_at_fps(frames, t0, duration)
        decoded: dict[int, Image.Image] = {}
        for n, fi in enumerate(index):
            if fi not in decoded:
                decoded.clear()
                decoded[fi] = Image.open(io.BytesIO(frames[fi][1])).convert("RGB")
            src = decoded[fi]
            cx, cy, s = zoom_at(zooms, n / FPS)
            kx, ky = src.width / size[0], src.height / size[1]
            w, h = src.width / s, src.height / s
            left = min(max(cx * kx - w / 2, 0), src.width - w)
            top = min(max(cy * ky - h / 2, 0), src.height - h)
            win = src.resize(win_size, Image.LANCZOS, box=(left, top, left + w, top + h))
            frame = bg.copy()
            frame.paste(win, at, mask)
            written += 1
            if cover is None and written >= cover_n:
                cover = frame.copy()
            proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()
    return cover


def gif(mp4: Path, out: Path):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(mp4), "-vf",
                    "fps=15,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", str(out)], check=True)


# ---------------------------------------------------------------------------
# A demo lab to record in
# ---------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_demo_lab():
    data = Path(tempfile.mkdtemp(prefix="biomanager-clips-")) / "lab"
    subprocess.run([sys.executable, str(ROOT / "scripts/demo-data.py"), str(data)], check=True,
                   stdout=subprocess.DEVNULL)
    port = free_port()
    env = {**os.environ, "BIOMANAGER_DATA_DIR": str(data), "DATABASE_URL": f"sqlite:///{data / 'biomanager.db'}",
           "SECRET_KEY": "feature-clips", "FLASK_DEBUG": "0", "PORT": str(port), "BIOMANAGER_TELEMETRY": "0"}
    server = subprocess.Popen([sys.executable, str(ROOT / "run.py")], env=env, cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
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
    page.goto(f"{base}/home")
    # A fresh demo lab has never seen this release, so What's new opens over
    # the page and swallows the first click of every walk (app/whats_new.py).
    for _ in range(3):
        if not page.locator("dialog#whats-new[open]").count():
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    hide = page.get_by_role("button", name="Hide this list")
    if hide.count():
        hide.first.click()
        page.wait_for_load_state("networkidle")


def titles() -> dict:
    path = ROOT / "promo/posts.json"
    if not path.exists():
        return {}
    posts = json.loads(path.read_text())
    out = {}
    for p in posts["posts"]:   # a clip used twice (the tour) keeps its first day's title
        if p.get("clip"):
            out.setdefault(p["clip"], p)
    return out


def record(browser, base, password, walk, kind, prepare=None):
    size = PHONE if kind == "phone" else VIEW
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, device_scale_factor=SCALE,
                              is_mobile=kind == "phone", has_touch=False, color_scheme="light",
                              user_agent=None if kind != "phone" else
                              "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                              "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    ctx.add_init_script(CURSOR_JS if kind != "phone" else CURSOR_JS.replace("#111", "rgba(60,60,67,.55)"))
    page = ctx.new_page()
    sign_in(page, base, password)
    if prepare:
        prepare(ctx.request, base)
    d = Director(page, base, size)
    walk(d)
    if d.t0 is None:
        d.start()
    duration = d.stop()
    ctx.close()
    return (d.frames, d.zooms, size, kind == "phone", d.t0, duration, d.cover_at)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", nargs="?", default=str(ROOT / "promo/out"))
    ap.add_argument("clips", nargs="*")
    ap.add_argument("--base", help="a running demo lab to record in, instead of a fresh one")
    ap.add_argument("--data", help="that lab's data folder (for its demo-password)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--quick", action="store_true", help="only landscape-en.mp4, for checking a clip")
    ap.add_argument("--plain", action="store_true",
                    help="only plain.mp4: the footage with no words, for scripts/explainer-video.py")
    args = ap.parse_args()
    clips, prepares = load_clips()
    if args.list:
        print("\n".join(clips))
        return
    wanted = args.clips or list(clips)
    unknown = [c for c in wanted if c not in clips]
    if unknown:
        sys.exit(f"No such clip: {', '.join(unknown)}. --list shows them.")
    out = Path(args.out)
    server = None
    if args.base:
        base, data = args.base.rstrip("/"), Path(args.data)
    else:
        base, data, server = start_demo_lab()
    password = (data / "demo-password").read_text().strip()
    info = titles()
    order = list(clips)
    failed = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for name in wanted:
                print(f"{name}: recording", flush=True)
                try:
                    segments = [record(browser, base, password, walk, kind, prepares.get(name) if i == 0 else None)
                                for i, (kind, walk) in enumerate(clips[name])]
                except Exception as e:  # noqa: BLE001 — one broken clip shouldn't stop the rest
                    print(f"  FAILED: {e}", flush=True)
                    failed.append(name)
                    continue
                meta = info.get(name, {})
                colours = tuple(meta.get("gradient") or GRADIENTS[order.index(name) % len(GRADIENTS)])
                folder = out / name
                folder.mkdir(parents=True, exist_ok=True)
                renders = (("landscape-en", (1920, 1080), "en"),
                           ("landscape-zh", (1920, 1080), "zh"),
                           ("portrait-zh", (1080, 1440), "zh"))
                if args.plain:
                    render(segments, Layout((1920, 1080), "", "", "zh", colours, plain=True), folder / "plain.mp4")
                    print("  plain.mp4", flush=True)
                    continue
                for fname, canvas, lang in renders[:1] if args.quick else renders:
                    layout = Layout(canvas, meta.get(f"title_{lang}", name), meta.get(f"subtitle_{lang}", ""),
                                    lang, colours, meta.get("bullets_zh", ()) if lang == "zh" else ())
                    cover = render(segments, layout, folder / f"{fname}.mp4")
                    if cover is not None:
                        cover.save(folder / f"cover-{fname.replace('landscape-', '').replace('portrait-zh', 'portrait')}.png")
                    print(f"  {fname}.mp4", flush=True)
                if not args.quick:
                    gif(folder / "landscape-en.mp4", folder / "clip.gif")
                    print("  clip.gif", flush=True)
            browser.close()
    finally:
        if server:
            server.terminate()
            server.wait()
            shutil.rmtree(data.parent, ignore_errors=True)
    if failed:
        sys.exit(f"These clips failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
