"""When BioManager's window can't open on Linux: the web browser instead.

On Linux the window is Qt WebEngine, which needs the computer's own graphics
libraries (libEGL, Wayland's, a working OpenGL). A server edition, WSL or a
trimmed-down desktop may lack one, and pywebview then stops with an error
before any window shows. desktop.py catches that and calls
`run_in_browser`, as desktop_windows.py does on Windows: the app keeps
running and opens in the web browser.

Without a window there is nothing to close, so a dialog from the desktop
(zenity on GNOME, kdialog on KDE) says where the app is, and closing it
closes BioManager. Without either, the app runs until the terminal stops it
(Ctrl+C) or the person signs out.

A display without working OpenGL (a VM, remote X, a server's virtual
screen) is worse: Qt stops the whole process in C++ ("Could not initialize
GLX"), which no except can catch. `software_rendering` draws the window
without the graphics card instead (Qt's OpenGL and Chromium's GPU off), when
the computer has no graphics device, or when the window didn't appear last
time (a marker left before the window starts and taken away once it shows),
and from then on.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import webbrowser

# Qt's window without OpenGL, Chromium inside it without the GPU, and Qt's
# scene drawn in software: without the first two it still stops on a display
# with no working GLX, without the last the window comes up black.
SOFTWARE_ENV = {"QT_XCB_GL_INTEGRATION": "none", "QT_QUICK_BACKEND": "software"}
NO_GPU_FLAG = "--disable-gpu"


def has_graphics_device(dri: str = "/dev/dri") -> bool:
    try:
        return any(name.startswith(("card", "renderD")) for name in os.listdir(dri))
    except OSError:
        return False


def software_rendering(marker, prefs: dict, environ=os.environ, has_device=has_graphics_device) -> str | None:
    """Draw the window in software when it is needed: why, or None. Someone
    who set Qt's own variable keeps their choice."""
    if environ.get("QT_XCB_GL_INTEGRATION"):
        return None
    if prefs.get("software_window"):
        why = "chosen before"
    elif marker.exists():
        why = "the window didn't open last time"
    elif not has_device():
        why = "no graphics device"
    else:
        return None
    environ.update(SOFTWARE_ENV)
    flags = environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if NO_GPU_FLAG not in flags.split():
        environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " " + NO_GPU_FLAG).strip()
    return why


# What each desktop's dialog tool needs to show a message and wait for OK.
DIALOGS = (
    ("zenity", lambda text: ["zenity", "--info", "--title=BioManager", "--no-wrap", f"--text={text}"]),
    ("kdialog", lambda text: ["kdialog", "--title", "BioManager", "--msgbox", text]),
)


def message(error: BaseException | None, url: str) -> str:
    why = str(error or "").strip().splitlines()
    return ("BioManager's window couldn't open on this computer"
            + (f" ({why[-1][:300]})" if why else "") + ".\n\n"
            f"It is open in your web browser at {url}\n\n"
            "Leave this message open while you use it: OK closes BioManager.")


def _dialog(text: str, which=shutil.which, run=subprocess.run) -> bool:
    """Show `text` and wait for OK; False when there's no dialog tool."""
    for name, command in DIALOGS:
        if which(name):
            try:
                run(command(text), check=False)
                return True
            except OSError:
                continue
    return False


def run_in_browser(url: str, error: BaseException | None = None, dialog=_dialog, wait=None) -> None:
    """Open the app in the web browser and keep it running until the
    message is closed (or, with no dialog tool, until the process is stopped)."""
    text = message(error, url)
    print(text.replace("OK closes BioManager.", "Ctrl+C closes BioManager."), file=sys.stderr, flush=True)
    webbrowser.open(url)
    if not dialog(text):
        (wait or threading.Event().wait)()
