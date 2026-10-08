"""BioManager's releases on GitHub: which version is newer, and what a
release's notes say, in short. Used by the desktop app's own update check
(desktop_updates.py, beside the app) and a lab server's (server_updates.py),
whose image carries only this package."""
from __future__ import annotations

import re

REPO = "gaspolymerase/biomanager"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


def parse_version(text: str) -> tuple[int, ...] | None:
    """"v0.5.1" → (0, 5, 1); a "+dev" build counts as its tag."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _prerelease(text: str) -> tuple[int, ...] | None:
    """"1.0.0-rc.2" → (2,): a release candidate, before 1.0.0 itself."""
    m = re.match(r"^\s*v?\d+(?:\.\d+)*-[A-Za-z]*\.?(\d*)", text or "")
    return (int(m.group(1) or 0),) if m else None


def is_newer(latest: str, current: str) -> bool:
    """Whether `latest` comes after `current`. A release candidate
    (1.0.0-rc.1) comes before its release (1.0.0), so an app on a candidate
    is offered the release; installed apps never see candidates, which are
    published as pre-releases (GitHub's /releases/latest leaves them out)."""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    a, b = a + (0,) * (width - len(a)), b + (0,) * (width - len(b))
    if a != b:
        return a > b
    pa, pb = _prerelease(latest), _prerelease(current)
    if pa is None or pb is None:
        return pa is None and pb is not None      # the release after its candidate
    return pa > pb


def summary(notes: str, limit: int = 700) -> str:
    """Release notes as plain text for a dialog: what's new (the notes open
    with a table of downloads and end with how to install), no Markdown
    marks, short."""
    new = re.search(r"^\W*New in\b.*$", notes, flags=re.M)
    if new:
        notes = notes[new.start():]
        ends = [i for i in (notes.find("\nUpdate a lab server"), notes.find("\n**First launch")) if i > 0]
        notes = notes[:min(ends)] if ends else notes
    notes = "\n".join(line for line in notes.splitlines() if not line.lstrip().startswith("|"))
    text = re.sub(r"<!--.*?-->", "", notes, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"
