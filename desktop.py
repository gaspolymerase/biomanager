"""Desktop entrypoint: serves the Flask app in a background thread and opens
it inside a native pywebview window.

Run from source:   python desktop.py
Bundled app:       double-click Biomanager.app (built via Biomanager.spec)
"""
from __future__ import annotations

import os
import socket
import sys

# Asked by ssh for a password (SSH_ASKPASS, set by app/server_setup.py when
# the server set-up signs in with one): print it and stop, before anything
# else starts. Windows has no shell script to do this, so the app does it.
if os.environ.get("BIOMANAGER_ASKPASS") == "1":
    # Straight to the pipe ssh gave it: the windowed app may have no sys.stdout.
    os.write(1, (os.environ.get("BIOMANAGER_SSH_PASSWORD", "") + "\n").encode("utf-8"))
    sys.exit(0)

import threading
import time
from pathlib import Path
from urllib.request import urlopen

import webview

# Start a new lab, if the sign-in page asked for one: this lab moves aside,
# whole, before anything opens its database (app/paths.py).
from app import paths as _paths  # noqa: E402
LAB_SET_ASIDE = _paths.set_lab_aside()

from app.app import app  # noqa: E402

# Only this machine can reach the desktop app (it binds 127.0.0.1 on a random
# port), so creating its first account does not need the server setup code.
# Sharing the lab on the network (app/devices.py) listens there as well, and
# marks those requests, which never get the desktop's own pages.
app.config["LOCAL_SETUP"] = True
# Said on the sign-in page until the new lab has its first account.
app.config["LAB_SET_ASIDE"] = str(LAB_SET_ASIDE or "")
# "Automatic" language in this window: the computer's (app/i18n.py).
from app import i18n  # noqa: E402
app.config["COMPUTER_LANGUAGE"] = i18n.system_language()


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _choose_port() -> int:
    """The same port as last time, when it is free. The window keeps its
    sign-in and tabs per address (http://127.0.0.1:<port>), so a new port
    each launch would start it afresh every time."""
    pinned = int(os.environ.get("BIOMANAGER_PORT") or 0)
    if pinned:
        return pinned
    import desktop_updates
    saved = int(desktop_updates.load_prefs().get("port") or 0)
    if saved and _port_free(saved):
        return saved
    port = _pick_free_port()
    desktop_updates.save_prefs(port=port)
    return port


def _run_flask(port: int) -> None:
    # threaded=True so concurrent requests (autosave + page navigation) don't
    # deadlock. use_reloader=False because the reloader spawns a child
    # process that webview can't follow.
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)


def _wait_until_ready(url: str, timeout_s: float = 8.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urlopen(url, timeout=0.5).read()
            return
        except Exception:
            time.sleep(0.1)


def main() -> int:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        # Before pywebview loads .NET, which refuses DLLs a downloaded zip
        # marked as from the internet (desktop_windows.py).
        import desktop_windows
        desktop_windows.unblock(Path(sys._MEIPASS))

    # BIOMANAGER_PORT pins the port, so the release workflow can check that a
    # freshly built app answers; otherwise last time's, or any free one.
    port = _choose_port()
    server_thread = threading.Thread(target=_run_flask, args=(port,), daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}/"
    _wait_until_ready(url)

    # Keep this computer's copy of the lab server fresh, if it is set up to
    # (Settings → Keep a copy of your lab server; app/lab_copy.py).
    from app import lab_copy
    lab_copy.start_background(app)
    # Share this computer's lab on the network again if it was shared, and
    # say hello to the lab it is linked to (app/devices.py).
    from app import devices
    devices.start_background(app)

    import desktop_menu
    desktop_menu._state["local_url"] = url
    # The lab another device holds, if this window opens it (Settings →
    # Devices → Open the lab in this window); else this computer's own.
    target = devices.window_url() or url
    # Windows 10 may lack what the window needs, and pywebview would then use
    # Internet Explorer's engine, which can't run the app: the web browser
    # instead (desktop_windows.py).
    if sys.platform == "win32":
        import desktop_windows
        reason = desktop_windows.missing()
        if reason:
            desktop_windows.run_in_browser(target, reason)
            return 0
    window = webview.create_window(
        "BioManager",
        target,
        width=1280,
        height=820,
        min_size=(960, 600),
        confirm_close=False,
        js_api=desktop_menu.DesktopApi(),
    )
    # gui=None lets pywebview pick the native backend (cocoa on macOS,
    # edgechromium on Windows, gtk/qt on Linux). On a Mac the full menu bar
    # replaces pywebview's once the window is up; elsewhere pywebview's own
    # menus carry the same destinations (desktop_menu.py). Either way the
    # app checks for a newer release, at most once a day.
    menus = [] if sys.platform == "darwin" else desktop_menu.plain_menus()
    # Not private mode (pywebview's default), which forgets the sign-in and
    # the open tabs at every launch. Windows and Linux keep that browser data
    # in the data folder; a Mac keeps it where WebKit keeps every app's.
    from app.paths import data_dir
    storage = data_dir() / "window"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.start(desktop_menu.start, (window,), debug=False, menu=menus,
                      private_mode=False, storage_path=str(storage))
    except Exception as error:
        if sys.platform != "win32":
            raise
        desktop_windows.run_in_browser(target, "failed", error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
