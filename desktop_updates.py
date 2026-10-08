"""The desktop app's version, its preferences, and checking for a newer one.

A build knows its version from the VERSION file PyInstaller puts beside it
(Biomanager.spec writes it from BIOMANAGER_VERSION, which the release
workflow sets from the tag). Run from source, it asks git.

"Check for Updates…" (and, at most once a day, the app when it opens)
asks GitHub for the latest release on gaspolymerase/biomanager. That
request carries only the app's version in its User-Agent; turn the daily
check off with "Check for Updates Automatically". A newer release is
offered with its notes. "Install and Restart" downloads the file for this
computer, checks it against the SHA-256 GitHub publishes for it, and swaps
it in once the app has quit (install_update): the Mac app in its folder,
the Windows folder, or the Linux AppImage. The old one is kept beside it
until the new one has started. Where that isn't possible (run from
source, a folder it can't write to, the Linux .tar.gz) "Download" opens
the file in the browser instead.

Preferences (the automatic check, a skipped version, appearance and zoom)
are desktop-prefs.json in the data folder (app/paths.py), not in the lab's
database: they belong to this computer.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from app.releases import LATEST_API, RELEASES_PAGE, REPO, is_newer, parse_version, summary  # noqa: F401 (used by the menu and tests)

GUIDE_URL = "https://biomanager.org/guide.html"
ISSUES_URL = f"https://github.com/{REPO}/issues/new"
CHECK_EVERY = 24 * 3600
DEFAULT_PREFS = {"check_updates": True, "last_check": 0, "skip_version": "", "appearance": "system", "zoom": 1.0}

_ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------- version

def version() -> str:
    """"0.5.0" in a build; from source, the latest tag (and "+dev" past it)."""
    bundled = Path(getattr(sys, "_MEIPASS", _ROOT)) / "VERSION"
    try:
        text = bundled.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    try:
        out = subprocess.run(["git", "describe", "--tags", "--abbrev=0"], cwd=_ROOT, capture_output=True,
                             text=True, timeout=3)
        tag = out.stdout.strip()
        if out.returncode == 0 and tag:
            return tag.lstrip("v") + "+dev"
    except (OSError, subprocess.SubprocessError):
        pass
    return "dev"


# ---------------------------------------------------------------- preferences

def _prefs_path() -> Path:
    from app.paths import data_dir
    return data_dir() / "desktop-prefs.json"


def load_prefs() -> dict:
    try:
        stored = json.loads(_prefs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULT_PREFS, **(stored if isinstance(stored, dict) else {})}


def save_prefs(**changes) -> dict:
    prefs = {**load_prefs(), **changes}
    try:
        _prefs_path().write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    except OSError:
        pass
    return prefs


# ---------------------------------------------------------------- the check

def asset_for_this_computer(names: list[str]) -> str | None:
    """Which release file to download here."""
    system = sys.platform
    if system == "darwin":
        want = "BioManager-macOS-AppleSilicon.zip" if platform.machine() == "arm64" else "BioManager-macOS-Intel.zip"
    elif system.startswith("win"):
        want = "BioManager-Windows.zip"
    else:
        want = "BioManager-Linux.AppImage"
    return want if want in names else None


def fetch_latest(timeout: float = 8.0) -> dict:
    """The latest release: {"version", "notes", "page", "download"}.
    Raises OSError (no network, GitHub down) or ValueError (an odd answer)."""
    request = urllib.request.Request(LATEST_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"BioManager/{version()}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (a fixed https URL)
        data = json.loads(response.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None:
        raise ValueError("GitHub's answer had no version in it.")
    assets = {a.get("name"): a for a in data.get("assets") or [] if a.get("name")}
    wanted = asset_for_this_computer(list(assets))
    asset = assets.get(wanted) if wanted else None
    digest = str((asset or {}).get("digest") or "")
    return {"version": tag.lstrip("v"), "notes": summary(str(data.get("body") or "")),
            "page": str(data.get("html_url") or RELEASES_PAGE),
            "download": asset.get("browser_download_url") if asset else None,
            "asset": wanted, "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else ""}




def check(manual: bool, now: float | None = None, fetch=fetch_latest) -> dict:
    """What to tell the person:

    {"state": "newer", "release": {...}}   a newer version (not skipped, unless asked)
    {"state": "current", "version": "0.5.0"}
    {"state": "error", "message": "..."}  only when asked
    {"state": "quiet"}                     an automatic check with nothing to say
    """
    now = time.time() if now is None else now
    prefs = load_prefs()
    if not manual and (not prefs["check_updates"] or now - float(prefs["last_check"] or 0) < CHECK_EVERY):
        return {"state": "quiet"}
    current = version()
    try:
        release = fetch()
    except (OSError, ValueError) as error:
        return {"state": "error", "message": f"BioManager couldn't reach GitHub to check ({error})."} if manual \
            else {"state": "quiet"}
    save_prefs(last_check=now)
    if is_newer(release["version"], current) and (manual or release["version"] != prefs["skip_version"]):
        return {"state": "newer", "release": release, "current": current}
    return {"state": "current", "version": current} if manual else {"state": "quiet"}


# ---------------------------------------------------------------- installing

def installed_location() -> Path | None:
    """What an update replaces: the .app, the Windows folder, or the
    AppImage. None when run from source or from the Linux .tar.gz."""
    import os
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        app = next((p for p in exe.parents if p.suffix == ".app"), None)
        return app
    if sys.platform.startswith("win"):
        return exe.parent
    appimage = os.environ.get("APPIMAGE")
    return Path(appimage) if appimage else None


def can_install(release: dict) -> bool:
    import os
    where = installed_location()
    return bool(where and release.get("download") and os.access(where.parent, os.W_OK))


def _download(url: str, target: Path, sha256: str, progress=None) -> None:
    import hashlib
    request = urllib.request.Request(url, headers={"User-Agent": f"BioManager/{version()}"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=30) as response, open(target, "wb") as out:  # noqa: S310
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    if sha256 and digest.hexdigest() != sha256.lower():
        target.unlink(missing_ok=True)
        raise ValueError("The download doesn't match the checksum GitHub published for it, so it wasn't installed.")


def swap_script(platform_name: str, pid: int, old: Path, new: Path, keep: Path) -> tuple[str, str]:
    """(file name, script) that waits for this app to quit, moves the old one
    to `keep`, puts the new one in its place and starts it."""
    if platform_name.startswith("win"):
        return "biomanager-update.cmd", "\r\n".join([
            "@echo off",
            f":wait",
            f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
            f'move "{old}" "{keep}"',
            f'move "{new}" "{old}"',
            f'start "" "{old}\\BioManager.exe"',
            "",
        ])
    launch = f'open "{old}"' if platform_name == "darwin" else f'chmod +x "{old}" && nohup "{old}" >/dev/null 2>&1 &'
    return "biomanager-update.sh", "\n".join([
        "#!/bin/sh",
        f"while kill -0 {pid} 2>/dev/null; do sleep 0.5; done",
        f'mv "{old}" "{keep}" && mv "{new}" "{old}" && {launch}',
        "",
    ])


def install_update(release: dict, progress=None) -> str:
    """Download and check the new version, and leave a helper waiting to
    swap it in when this app quits. Returns what to tell the person; the
    caller then quits the app. Raises ValueError or OSError when it can't."""
    import os
    import shutil
    import subprocess
    import tempfile
    import zipfile
    where = installed_location()
    if not can_install(release):
        raise ValueError("This copy of BioManager can't update itself; download the new one instead.")
    work = Path(tempfile.mkdtemp(prefix="biomanager-update-"))
    download = work / release["asset"]
    _download(release["download"], download, release.get("sha256", ""), progress)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", str(download), str(work / "new")], check=True)
        new = next((work / "new").glob("*.app"))
        staged = where.parent / f".BioManager-{release['version']}.app"
        if staged.exists():
            shutil.rmtree(staged)
        shutil.move(str(new), str(staged))
        keep = Path(tempfile.gettempdir()) / f"BioManager-before-{stamp}.app"
    elif sys.platform.startswith("win"):
        with zipfile.ZipFile(download) as z:
            z.extractall(work / "new")
        new = next((work / "new").glob("*"))
        staged = where.parent / f"BioManager-{release['version']}"
        if staged.exists():
            shutil.rmtree(staged)
        shutil.move(str(new), str(staged))
        keep = where.parent / f"BioManager-before-{stamp}"
    else:
        staged = where.parent / f".BioManager-{release['version']}.AppImage"
        shutil.move(str(download), str(staged))
        os.chmod(staged, 0o755)
        keep = Path(tempfile.gettempdir()) / f"BioManager-before-{stamp}.AppImage"
    name, script = swap_script(sys.platform, os.getpid(), where, staged, keep)
    helper = work / name
    # cmd.exe reads a batch file in the system's code page, not UTF-8, and the
    # paths in it may not be ASCII (a user folder named 张伟).
    helper.write_text(script, encoding="locale")
    if sys.platform.startswith("win"):
        subprocess.Popen(["cmd", "/c", str(helper)], creationflags=0x00000008 | 0x00000200)  # detached, new group
    else:
        os.chmod(helper, 0o755)
        subprocess.Popen(["/bin/sh", str(helper)], start_new_session=True)
    return f"BioManager {release['version']} is ready. It opens as soon as this one quits."
