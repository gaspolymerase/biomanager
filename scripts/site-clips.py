#!/usr/bin/env python3
"""Make the website's feature clips (site/assets/clips/) from a fresh demo lab.

    python scripts/site-clips.py                 # all seven
    python scripts/site-clips.py home plasmid    # just these

The front page's dark band plays one short clip per tab of its dock. Each is
recorded by scripts/feature-clips.py --plain (the launch posts' footage, from
its own demo lab), then cut down to the app's own area, sped up a little where
long (at most 1.6×, to about 12 s), and saved for the web: 1280 px wide, no
sound, as H.264 .mp4 and VP9 .webm, with a .webp of the first frame for the
poster.

Needs what feature-clips.py needs (Playwright, Pillow, imageio-ffmpeg). On a
computer without macOS's fonts, any bold font will do: the plain footage has
no words in it.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site/assets/clips"
CLIPS = ["home", "cards", "datasheet", "plasmid", "calendar", "sign", "phone"]
# Where the app sits in feature-clips.py's 1920×1080 plain frame: inside the
# window, below its title bar and above its rounded bottom corners. A phone-only
# clip keeps the whole phone.
DESKTOP_CROP = "1232:757:343:123"
PHONE_CROP = "1332:818:294:80"
PHONE_ONLY = {"phone"}
FALLBACK_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "C:/Windows/Fonts/arialbd.ttf"]


def load_feature_clips():
    spec = importlib.util.spec_from_file_location("feature_clips", ROOT / "scripts/feature-clips.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not Path(mod.FONT_EN[0]).exists():
        font = next((f for f in FALLBACK_FONTS if Path(f).exists()), None)
        if font:
            mod.FONT_EN = mod.FONT_ZH = (font, None)
    load = mod.load_clips

    def cards_desktop(d):
        # The cage cards walk, with its click on the Cage cards button that is
        # showing (the Cards view has one too, hidden in the Table view).
        d.goto("/colony?view=cages")
        d.start()
        d.click("a:has-text('Cage cards') >> visible=true", after=1.0)
        d.zoom(box=(244, 230, 380, 226), scale=2.0)
        d.move("text=Cage 101", 0.8)
        d.cover()
        d.wait(1.6)
        d.unzoom()
        d.move("select >> nth=0", 0.8)
        d.wait(1.0)

    def clips():
        found, prepare = load()
        if "cards" in found:
            found["cards"] = [("desktop", cards_desktop)] + found["cards"][1:]
        return found, prepare

    mod.load_clips = clips
    return mod


def duration(ffmpeg: str, path: Path) -> float:
    info = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = info.split("Duration: ", 1)[1].split(",", 1)[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def encode(ffmpeg: str, source: Path, name: str) -> None:
    speed = max(1.0, min(1.6, duration(ffmpeg, source) / 12.0))
    crop = PHONE_CROP if name in PHONE_ONLY else DESKTOP_CROP
    vf = f"crop={crop},setpts=PTS/{speed:.3f},fps=30,scale=1280:786:flags=lanczos"
    run = lambda *args: subprocess.run([ffmpeg, "-v", "error", "-y", *args], check=True)
    run("-i", str(source), "-an", "-vf", vf, "-c:v", "libx264", "-preset", "slow", "-crf", "28",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-tune", "animation", str(OUT / f"{name}.mp4"))
    run("-i", str(source), "-an", "-vf", vf, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "42",
        "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", str(OUT / f"{name}.webm"))
    run("-i", str(OUT / f"{name}.mp4"), "-frames:v", "1", "-c:v", "libwebp", "-quality", "80", str(OUT / f"{name}.webp"))
    sizes = ", ".join(f"{ext} {(OUT / f'{name}.{ext}').stat().st_size // 1024} KB" for ext in ("mp4", "webm", "webp"))
    print(f"{name}: ×{speed:.2f}, {sizes}", flush=True)


def main() -> None:
    wanted = sys.argv[1:] or CLIPS
    unknown = [c for c in wanted if c not in CLIPS]
    if unknown:
        sys.exit(f"No such site clip: {', '.join(unknown)}. They are: {', '.join(CLIPS)}")
    mod = load_feature_clips()
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="biomanager-site-clips-") as tmp:
        sys.argv = ["feature-clips.py", "--plain", tmp, *wanted]
        mod.main()
        for name in wanted:
            source = Path(tmp) / name / "plain.mp4"
            if source.exists():
                encode(mod.FFMPEG, source, name)
            else:
                print(f"{name}: not recorded", flush=True)


if __name__ == "__main__":
    main()
