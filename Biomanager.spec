# PyInstaller spec for BioManager desktop build.
#
# Build:   pyinstaller Biomanager.spec --clean --noconfirm
# Output:  dist/BioManager.app  (macOS)  /  dist/BioManager/  (Win/Linux)
#
# The spec bundles app/static + app/templates as read-only resources. SQLite
# DB and user uploads land in ~/Library/Application Support/Biomanager/ at
# runtime (see app/paths.py).

import os
import sys

# noinspection PyUnresolvedReferences
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

datas = [
    ("app/static", "app/static"),
    ("app/templates", "app/templates"),
    # Opening a database an older version made upgrades it (app/upgrade.py).
    ("migrations", "migrations"),
]

# The version the app shows in About and compares in Check for Updates
# (desktop_updates.version()), from the release tag.
import tempfile
_version_dir = tempfile.mkdtemp(prefix="biomanager-version-")
with open(os.path.join(_version_dir, "VERSION"), "w") as _f:
    _f.write(os.environ.get("BIOMANAGER_VERSION", "0.1.0"))
datas.append((os.path.join(_version_dir, "VERSION"), "."))

# pywebview backends + Flask use a few modules PyInstaller's static analysis
# can miss.
hiddenimports = (
    collect_submodules("webview")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + ["psycopg"]
    + collect_submodules("alembic")
    # migrations/ ships as files that Alembic runs (datas above), so the
    # analysis never sees what they import: env.py's logging.config, and
    # each revision's migrations.helpers.
    + ["json", "logging.config", "logging.handlers", "migrations", "migrations.helpers"]
)
if sys.platform.startswith("linux"):
    # On Linux the window is Qt WebEngine (pip install "pywebview[qt]"),
    # which pywebview imports only once it has picked a backend.
    hiddenimports += collect_submodules("qtpy") + [
        "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineCore", "PyQt6.QtWebChannel",
        "PyQt6.QtNetwork", "PyQt6.QtPrintSupport",
    ]


a = Analysis(
    ["desktop.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
# app/static/uploads/ holds the lab's own uploaded files on a machine that has
# run the app from source. It is data, not part of the app: never ship it.
_uploads = os.path.join("app", "static", "uploads")
a.datas = [entry for entry in a.datas if not entry[0].startswith(_uploads)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Python's UTF-8 mode, as on macOS and Linux. Without it a file opened with no
# encoding is read in the Windows code page: GBK on a Chinese PC, where the
# app's own UTF-8 files then fail to read and it never starts.
exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="BioManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="desktop/BioManager.ico" if sys.platform == "win32" else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BioManager",
)
app = BUNDLE(
    coll,
    name="BioManager.app",
    icon="desktop/BioManager.icns",
    bundle_identifier="org.biomanager.desktop",
    info_plist={
        "NSHighResolutionCapable": "True",
        "LSBackgroundOnly": "False",
        "CFBundleShortVersionString": os.environ.get("BIOMANAGER_VERSION", "0.1.0"),
        "CFBundleVersion": os.environ.get("BIOMANAGER_VERSION", "0.1.0"),
    },
)
