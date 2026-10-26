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
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import webbrowser

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
