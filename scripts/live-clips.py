#!/usr/bin/env python3
"""Record the website's feature clips as the app's own pages, not as video.

    python scripts/live-clips.py                 # all of them
    python scripts/live-clips.py home plasmid    # just these

A clip on the website is the real app, replayed: each walk (the launch posts'
in promo/clips/, and the site's own below), driven by scripts/feature-clips.py's
Director in a fresh demo lab, is recorded with rrweb, which notes the page and
every change to it as it happens, and bmClips in site/site.js plays that back
in the visitor's browser. The words
are drawn by their browser at its own resolution, so they are as sharp as the
page around them on any screen and at any zoom, and a clip is a few hundred
kilobytes, not megabytes of video. Raycast's site shows its app the same way.

For each clip it writes, in site/assets/clips/:

    <clip>.json   the recording: one or more segments (a desktop window or a
                  phone), each with its size, length, zooms and rrweb events
    <clip>.webp   the first frame at twice the size, shown until it plays (and
                  where it can't)

and copies what the pages load from app/static (stylesheets, fonts, icons) to
site/assets/app/, which the recordings name in place of the lab's address,
and rrweb's replayer to site/assets/vendor/.

Needs what feature-clips.py needs (Playwright, Pillow, imageio-ffmpeg), and
`npm install` in frontend/ for rrweb's recorder.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import shutil
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site/assets/clips"
ASSETS = ROOT / "site/assets/app"
STATIC = ROOT / "app/static"
RECORDER = ROOT / "frontend/node_modules/@rrweb/record/dist/record.umd.min.cjs"
REPLAYER = ROOT / "frontend/node_modules/@rrweb/replay/dist/replay.umd.min.cjs"
VENDOR = ROOT / "site/assets/vendor/rrweb-replay.js"
# The front page's dock, in its order, then the Features page's cards.
CLIPS = ["home", "census", "litter", "assembly", "experiment", "calendar", "protocol", "datasheet", "links", "plasmid", "orders",
         "flies", "new-database", "cards", "import", "search", "looks", "phone"]
# A long walk plays faster, up to this, so it lasts about TARGET seconds.
MAX_SPEED, TARGET = 1.6, 12.0
# Where a recording names the app's files; site.js puts the site's
# assets/app/ there.
TOKEN = "%BM%/"

START = r"""
(() => {
  if (window.top !== window || !window.rrwebRecord) return;
  // The theme the page is drawn in, written where the replay can see it (the
  // replay can't know the recording's light or dark): looks turns dark.
  const dark = matchMedia('(prefers-color-scheme: dark)');
  const theme = () => {
    const root = document.documentElement;
    if (root && (!root.dataset.theme || root.dataset.bmClip)) {
      root.dataset.theme = dark.matches ? 'dark' : 'light';
      root.dataset.bmClip = '1';
    }
  };
  theme();
  document.addEventListener('DOMContentLoaded', theme);
  dark.addEventListener('change', theme);
  const record = window.rrwebRecord.record;
  record({
    emit: e => window.__bmEmit(e),
    inlineStylesheet: false,      // the stylesheets are site assets, loaded once
    inlineImages: false,          // pictures are copied to the site (lab_files)
    collectFonts: false,
    sampling: {mousemove: false, mouseInteraction: false, scroll: 16, input: 'last'},
    slimDOMOptions: {script: true, comment: true, headFavicon: true, headWhitespace: true,
                     headMetaSocial: true, headMetaRobots: true, headMetaHttpEquiv: true,
                     headMetaAuthorship: true, headMetaVerification: true},
    maskInputOptions: {password: true},
  });
  window.__bmSnap = () => record.takeFullSnapshot(true);
})();
"""


def load_site_clips():
    """scripts/feature-clips.py (the Director, the demo lab, the launch posts'
    walks), with the site's own walks: census and litter, and the cage-card
    walk with the button that is showing."""
    spec = importlib.util.spec_from_file_location("feature_clips", ROOT / "scripts/feature-clips.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    load = mod.load_clips

    def census(d):
        # The animals: every mouse in its sheet, the cages on their rack, and
        # (for an admin) everyone's cages on one page.
        d.goto("/colony")
        d.start()
        d.wait(1.0)
        d.scroll(320, at="th:has-text('Transgene 1')")
        d.wait(0.8)
        d.click("a:has-text('Cages') >> nth=0", after=0.8)
        d.click("[data-layout=grid] >> visible=true", after=1.8)
        d.cover()
        d.goto("/admin/colony", settle=1.0)
        d.scroll(360, seconds=1.2)
        d.wait(1.4)

    def breeding_cage(api, base):
        # Only a Breeding cage has Litter born, and the demo admin's own cages
        # are Breeders: make 111 a mating cage, as one would before a litter.
        import re
        html = api.get(f"{base}/colony?view=cages").text()
        row = re.search(r'/colony/cages/(\d+)/give-birth"\s+data-cage-label="111"', html).group(1)
        api.post(f"{base}/colony/cages/{row}/update", form={"purpose": "Breeding"}, headers={"X-Autosave": "1"})

    def litter(d):
        # Pups are born: one date, and BioManager works out weaning and
        # genotyping. The zoom is on the dialog, then on what it answers.
        d.goto("/colony?view=cages")
        d.start()
        d.wait(1.0)
        born = d.page.locator("[data-litter-born] >> visible=true").first
        d.move(born, 0.9)
        d.wait(0.5)
        born.click()
        d.wait(1.0)
        dialog = d.page.locator("#litter-born-modal").first
        d.zoom(dialog, scale=1.7)
        d.wait(1.0)
        d.move("#litter-born-modal input[name=date_give_birth]", 0.7)
        d.wait(1.0)
        d.cover()
        d.click("#litter-born-modal button:has-text('Record the litter')", after=2.0)
        d.unzoom()
        d.wait(0.6)
        # The answer: weaning is due three weeks out, worked out for you.
        said = d.page.locator(".flash-message").first
        if said.count() and said.is_visible():
            d.zoom(said, scale=1.9)
            d.wait(2.4)
            d.unzoom()
        d.wait(0.8)

    def cards_desktop(d):
        # The launch post's cage-card walk, with its click on the Cage cards
        # button that is showing (the Cards view has one too, hidden), over
        # the lab's cages: the demo admin's own are Breeders at the end.
        d.goto("/colony?view=cages&scope=all")
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
        found["census"] = [("desktop", census)]
        found["litter"] = [("desktop", litter)]
        prepare["litter"] = breeding_cage
        if "cards" in found:
            found["cards"] = [("desktop", cards_desktop)] + found["cards"][1:]
        return found, prepare

    mod.load_clips = clips
    return mod


def recorder(fc):
    class LiveDirector(fc.Director):
        """The Director, noting the page instead of filming it."""

        def __init__(self, page, base, size):
            super().__init__(page, base, size)
            self.poster = None
            self.start_ms = None

        def start(self):
            self.page.evaluate("document.activeElement && document.activeElement.blur()")
            self.poster = self.page.screenshot(type="png")
            self.start_ms = self.page.evaluate("Date.now()")
            self.page.evaluate("window.__bmSnap && window.__bmSnap()")
            self.t0 = time.time()
            self.zooms.append((0.0, (self.size[0] / 2, self.size[1] / 2, 1.0)))
            self.wait(0.6)

        def stop(self):
            self.wait(0.8)
            self.page.evaluate("1")      # the last events are through
            return time.time() - self.t0

    def record(browser, base, password, walk, kind, prepare=None):
        size = fc.PHONE if kind == "phone" else fc.VIEW
        ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, device_scale_factor=fc.SCALE,
                                  is_mobile=kind == "phone", has_touch=False, color_scheme="light",
                                  user_agent=None if kind != "phone" else
                                  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                                  "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
        events: list[dict] = []
        ctx.expose_binding("__bmEmit", lambda source, e: events.append(e))
        ctx.add_init_script(fc.CURSOR_JS if kind != "phone" else fc.CURSOR_JS.replace("#111", "rgba(60,60,67,.55)"))
        ctx.add_init_script(RECORDER.read_text(encoding="utf-8") + "\n;window.rrwebRecord = rrwebRecord;")
        ctx.add_init_script(START)
        page = ctx.new_page()
        fc.sign_in(page, base, password)
        if prepare:
            prepare(ctx.request, base)
        d = LiveDirector(page, base, size)
        walk(d)
        if d.t0 is None:
            d.start()
        duration = d.stop()
        lab_files(events, base, ctx.request)
        ctx.close()
        # From the snapshot taken at d.start(): Meta (4), then FullSnapshot (2).
        cut = next((i for i, e in enumerate(events) if e["type"] == 4 and e["timestamp"] >= d.start_ms), None)
        if cut is None:
            raise RuntimeError("rrweb recorded nothing from the start of the walk")
        # A mark at the end, so the replay lasts as long as the walk did.
        events.append({"type": 5, "data": {"tag": "end", "payload": {}}, "timestamp": int(d.start_ms + duration * 1000)})
        return {"kind": kind, "width": size[0], "height": size[1], "duration": round(duration, 2),
                "zooms": [[round(t, 3), [round(v, 1) for v in z[:2]] + [round(z[2], 3)]] for t, z in d.zooms],
                "events": events[cut:], "poster": d.poster}

    return record


def dark_by_theme(css: str) -> str:
    """The app is dark when the computer is (@media prefers-color-scheme,
    unless the page says data-theme=light). A replay can't be told the
    recording's light or dark, so the site's copies are dark when the page
    says data-theme=dark instead, which the recording does."""
    out, i = [], 0
    for m in re.finditer(r'@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{', css):
        if m.start() < i:
            continue
        j, depth = m.end(), 1
        while depth and j < len(css):
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        inner = css[m.end():j - 1]
        inner = re.sub(r':root:not\(\[data-theme=(["\']?)light\1\]\)', ':root[data-theme=dark]', inner)
        out += [css[i:m.start()], inner]
        i = j
    return "".join(out) + css[i:]


def lab_files(events: list[dict], base: str, request) -> None:
    """Point each picture the lab served (an app icon, an upload) at a copy
    in site/assets/app/lab/, fetched while still signed in."""
    fetched: dict[str, str] = {}

    def fix(attrs: dict) -> None:
        src = attrs.get("src")
        if not isinstance(src, str) or not src.startswith(base + "/") or src.startswith(base + "/static/"):
            return
        if src not in fetched:
            path = src[len(base) + 1:].split("#")[0]
            name = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.split("?")[0]).strip("-")
            r = request.get(src)
            if r.ok:
                dst = ASSETS / "lab" / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(r.body())
            fetched[src] = TOKEN + "lab/" + name
        attrs["src"] = fetched[src]

    def big_style(attrs: dict) -> None:
        # A stylesheet a page's script put in it (the plasmid editor's is
        # 1.5 MB) goes to a file of its own, shared by every snapshot.
        css = attrs.get("_cssText")
        if not isinstance(css, str):
            return
        css = attrs["_cssText"] = dark_by_theme(css)
        if len(css) < 50_000:
            return
        css = css.replace(base + "/static/", "../").replace(base + "/", "/")
        name = "style-" + hashlib.sha256(css.encode("utf-8")).hexdigest()[:12] + ".css"
        dst = ASSETS / "lab" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(css, encoding="utf-8")
        attrs["_cssText"] = f'@import url("{TOKEN}lab/{name}");'

    def walk(node: dict) -> None:
        if node.get("tagName") in ("img", "image", "source"):
            fix(node.get("attributes", {}))
        if node.get("tagName") == "style":
            big_style(node.get("attributes", {}))
        for child in node.get("childNodes", ()):
            walk(child)

    for e in events:
        data = e.get("data")
        if e["type"] == 2:
            walk(data["node"])
        elif e["type"] == 3 and data.get("source") == 0:
            for add in data.get("adds", ()):
                walk(add["node"])
            for change in data.get("attributes", ()):
                fix(change.get("attributes", {}))


def localise(text: str, base: str) -> tuple[str, set[str]]:
    """Name the app's files by TOKEN instead of the lab's address; return the
    files named (paths under app/static)."""
    text = text.replace(base + "/", "/")
    found = set(m.group(1) for m in re.finditer(r'/static/([A-Za-z0-9_./-]+)', text))
    text = re.sub(r'(?<![A-Za-z0-9_])/static/', TOKEN, text)
    return text, found


def copy_assets(paths: set[str]) -> None:
    """Copy app/static files to site/assets/app, with what their CSS names."""
    todo, seen = list(paths), set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        src = STATIC / rel
        if not src.is_file():
            continue
        dst = ASSETS / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".css":
            dst.write_text(dark_by_theme(src.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copyfile(src, dst)
        if src.suffix == ".css":
            for m in re.finditer(r'url\((["\']?)([^)"\']+)\1\)', src.read_text(encoding="utf-8")):
                url = m.group(2).split("?")[0].split("#")[0]
                if url.startswith(("data:", "http:", "https:", "/")):
                    continue
                todo.append(str((Path(rel).parent / url).as_posix()))
    # Every font, whichever a page asked for: a clip in Chinese needs others.
    for font in (STATIC / "fonts").glob("*.woff2"):
        dst = ASSETS / "fonts" / font.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(font, dst)


def vendor() -> None:
    """The replayer, for site.js to load: rrweb's own build, with its licence."""
    version = json.loads((REPLAYER.parent.parent / "package.json").read_text(encoding="utf-8"))["version"]
    code = REPLAYER.read_bytes().decode("utf-8")       # as it is: it has \r in strings
    code = re.sub(r"\n//# sourceMappingURL=\S+\s*$", "\n", code)
    VENDOR.parent.mkdir(parents=True, exist_ok=True)
    VENDOR.write_bytes((f"/*! @rrweb/replay {version} | MIT License, Copyright (c) 2018 Contributors "
                        f"(https://github.com/rrweb-io/rrweb) | copied by scripts/live-clips.py */\n" + code).encode("utf-8"))


def save(name: str, segments: list[dict], base: str) -> set[str]:
    total = sum(s["duration"] for s in segments)
    speed = round(max(1.0, min(MAX_SPEED, total / TARGET)), 3)
    poster = Image.open(io.BytesIO(segments[0].pop("poster"))).convert("RGB")
    for s in segments[1:]:
        s.pop("poster", None)
    poster.save(OUT / f"{name}.webp", "WEBP", quality=82, method=6)
    for old in (OUT / f"{name}.mp4", OUT / f"{name}.webm"):   # the video it replaces
        old.unlink(missing_ok=True)
    text = json.dumps({"v": 1, "speed": speed, "segments": segments}, separators=(",", ":"), ensure_ascii=False)
    text, files = localise(text, base)
    (OUT / f"{name}.json").write_text(text, encoding="utf-8")
    kb = lambda p: p.stat().st_size // 1024
    print(f"{name}: {total:.1f} s ×{speed}, json {kb(OUT / f'{name}.json')} KB, webp {kb(OUT / f'{name}.webp')} KB",
          flush=True)
    return files


def main() -> None:
    wanted = sys.argv[1:] or CLIPS
    unknown = [c for c in wanted if c not in CLIPS]
    if unknown:
        sys.exit(f"No such site clip: {', '.join(unknown)}. They are: {', '.join(CLIPS)}")
    if not RECORDER.exists():
        sys.exit("rrweb's recorder is missing: run npm install in frontend/.")
    fc = load_site_clips()
    from playwright.sync_api import sync_playwright
    clips, prepares = fc.load_clips()
    record = recorder(fc)
    OUT.mkdir(parents=True, exist_ok=True)
    base, data, server = fc.start_demo_lab()
    password = (data / "demo-password").read_text(encoding="utf-8").strip()
    files: set[str] = set()
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
                files |= save(name, segments, base)
            browser.close()
    finally:
        server.terminate()
        server.wait()
        shutil.rmtree(data.parent, ignore_errors=True)
    copy_assets(files)
    vendor()
    if failed:
        sys.exit(f"These clips failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
