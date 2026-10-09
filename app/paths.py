"""Path resolution for dev vs. frozen (PyInstaller) execution.

In dev (`python run.py`), data lives next to the source tree at `./data/`
and `app/static/uploads/`. In a frozen `.app`, those paths are inside a
read-only bundle, so we redirect writable state to a per-user folder:

  macOS:   ~/Library/Application Support/Biomanager/
  Windows: %APPDATA%/Biomanager/
  Linux:   ~/.local/share/Biomanager/

Read-only assets (templates, the built notebook JS/CSS) stay inside the
bundle and are resolved via sys._MEIPASS when frozen.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """Directory containing read-only app resources (templates, static files)."""
    if is_frozen():
        # PyInstaller unpacks data files under sys._MEIPASS.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def user_data_root() -> Path:
    """Writable per-user directory for DB and uploads."""
    if not is_frozen():
        return Path(__file__).resolve().parent.parent

    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Biomanager"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home())) / "Biomanager"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Biomanager"
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_dir() -> Path:
    """Directory for the SQLite database file.

    BIOMANAGER_DATA_DIR overrides everything, which is how the database gets
    moved off a cloud-synced folder without moving the code. Cloud sync does
    not honour SQLite's file locking and will eventually corrupt the file —
    see scripts/dbtool.py.
    """
    override = os.environ.get("BIOMANAGER_DATA_DIR", "").strip()
    if override:
        target = Path(override).expanduser()
    else:
        target = user_data_root() / "data"
    target.mkdir(parents=True, exist_ok=True)
    _owner_only(target)
    return target


def _owner_only(folder: Path) -> None:
    """The lab's database, uploads and keys are for the account that runs
    BioManager: other accounts on the same computer can't read them."""
    try:
        if os.name == "posix" and folder.stat().st_uid == os.getuid() and folder.stat().st_mode & 0o077:
            folder.chmod(0o700)
    except OSError:
        pass


# Folder names that indicate a cloud-sync client is managing this path.
SYNC_MARKERS = ("CloudStorage", "OneDrive", "Dropbox", "Google Drive",
                "GoogleDrive", "iCloud", "Box Sync", "Nextcloud")


def sync_risk(path: Path) -> str | None:
    """Return the sync provider managing `path`, if any."""
    for part in Path(path).resolve().parts:
        for marker in SYNC_MARKERS:
            if marker.lower() in part.lower():
                return marker
    return None


def uploads_dir() -> Path:
    """Directory for user-uploaded images and files.

    When frozen we need a writable location *and* it must be served at
    /static/uploads/ — so the Flask app adds a separate static route for
    this folder (see app.py).

    BIOMANAGER_UPLOADS_DIR puts them anywhere, which is how a server keeps
    them on the data volume, next to the database, rather than in the code."""
    override = os.environ.get("BIOMANAGER_UPLOADS_DIR", "").strip()
    if override:
        target = Path(override).expanduser()
    elif is_frozen():
        target = user_data_root() / "uploads"
    else:
        target = resource_root() / "app" / "static" / "uploads"
    target.mkdir(parents=True, exist_ok=True)
    return target


# Start a new lab (the desktop app's sign-in page, for a lab nobody can sign
# in to): the request is a file in the data folder, and the next start moves
# the lab aside, whole, before anything opens its database (desktop.py).
NEW_LAB_REQUEST = "start-new-lab"
# Restore a lab set aside: the request names the old-labs folder to bring
# back; the next start sets the present lab aside first, so the restore can
# itself be undone the same way.
RESTORE_REQUEST = "restore-lab"
OLD_LABS = "old-labs"
# What belongs to the computer rather than to the lab stays where it is.
COMPUTERS_OWN = {NEW_LAB_REQUEST, RESTORE_REQUEST, OLD_LABS, "desktop-prefs.json", "window"}


def ask_for_new_lab(wanted: bool = True) -> None:
    request = data_dir() / NEW_LAB_REQUEST
    if wanted:
        request.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    else:
        request.unlink(missing_ok=True)


def new_lab_asked_for() -> bool:
    return (data_dir() / NEW_LAB_REQUEST).exists()


def old_labs_dir() -> Path:
    return data_dir() / OLD_LABS


def old_labs() -> list[dict]:
    """The labs set aside on this computer, newest first: the folder, when,
    the lab's name and how many accounts it had (read from its database,
    which is only read), and its size."""
    import sqlite3
    found = []
    folder = old_labs_dir()
    for item in sorted(folder.iterdir(), reverse=True) if folder.is_dir() else []:
        if not item.is_dir():
            continue
        name, accounts = "", 0
        database = item / "biomanager.db"
        if database.is_file():
            try:
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
                    row = conn.execute("SELECT value FROM app_settings WHERE key='lab_name'").fetchone()
                    name = row[0] if row else ""
                    accounts = conn.execute("SELECT count(*) FROM users").fetchone()[0]
            except sqlite3.Error:
                pass
        size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
        found.append({"folder": item.name, "path": str(item), "name": name, "accounts": accounts, "size": size,
                      "has_lab": database.is_file()})
    return found


def ask_to_restore(folder: str | None) -> bool:
    """Ask the next start to bring back old-labs/<folder> (None: don't).
    Only a folder that is there."""
    request = data_dir() / RESTORE_REQUEST
    if folder is None:
        request.unlink(missing_ok=True)
        return True
    if not folder or "/" in folder or "\\" in folder or folder.startswith(".") or not (old_labs_dir() / folder).is_dir():
        return False
    request.write_text(folder, encoding="utf-8")
    return True


def restore_asked_for() -> str:
    request = data_dir() / RESTORE_REQUEST
    return request.read_text(encoding="utf-8").strip() if request.exists() else ""


def _set_aside_now(data: Path, uploads: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    target, n = old_labs_dir() / stamp, 2
    while target.exists():                      # two in one second: each its own folder
        target, n = old_labs_dir() / f"{stamp} ({n})", n + 1
    target.mkdir(parents=True)
    for item in sorted(data.iterdir()):
        if item.name not in COMPUTERS_OWN:
            shutil.move(str(item), str(target / item.name))
    if uploads.exists() and any(uploads.iterdir()):
        shutil.move(str(uploads), str(target / "uploads"))
    return target


def set_lab_aside() -> Path | None:
    """If a new lab was asked for, move this one (its database, keys, imports
    and uploaded files) into data/old-labs/<when>/ and return that folder.
    Nothing is deleted: putting the files back brings the lab back."""
    data = data_dir()
    restore = restore_asked_for()
    if restore and not (old_labs_dir() / restore).is_dir():
        # Gone since it was asked for: nothing is moved.
        (data / RESTORE_REQUEST).unlink(missing_ok=True)
        restore = ""
    if not (data / NEW_LAB_REQUEST).exists() and not restore:
        return None
    uploads = uploads_dir()
    try:
        target = _set_aside_now(data, uploads)
        if restore:
            # The lab set aside earlier comes back where this one was.
            back = old_labs_dir() / restore
            for item in sorted(back.iterdir()) if back.is_dir() else []:
                if item.name == "uploads":
                    uploads.mkdir(parents=True, exist_ok=True)
                    for f in item.iterdir():
                        shutil.move(str(f), str(uploads / f.name))
                    item.rmdir()
                else:
                    shutil.move(str(item), str(data / item.name))
            if back.is_dir() and not any(back.iterdir()):
                back.rmdir()
    except OSError as error:
        # Left asked for: the next start finishes the move, and nothing is lost meanwhile.
        print(f"BioManager: the lab could not be set aside: {error}", file=sys.stderr)
        return None
    (data / NEW_LAB_REQUEST).unlink(missing_ok=True)
    (data / RESTORE_REQUEST).unlink(missing_ok=True)
    return target
