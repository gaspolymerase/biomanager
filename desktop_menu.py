"""The desktop app's menus.

On a Mac, the menu bar a Mac app has, built with AppKit after pywebview has
put up its two defaults: BioManager (About, Check for Updates, Settings,
Hide, Quit), File (tabs, export, print), Edit (the standard editing
commands, and BioManager's search), View (reload, zoom, appearance, full
screen), Go (back, forward, Home, and every database and function in the
sidebar), Window and Help. Edit and Window use the system's own actions,
so they behave as in any Mac app.

The Go menu lists what the sidebar shows the person signed in: each page
hands its sidebar links to `DesktopApi.set_nav` through pywebview's
JavaScript bridge (static/shell.js), and the menu is rebuilt from them.

On Windows and Linux, pywebview's own menus (which can't carry shortcuts)
give the same destinations and Check for Updates.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser

import desktop_updates as updates

_state = {"window": None, "nav": [], "go_menu": None, "zoom_items": None, "appearance_items": {},
          "auto_item": None, "local_url": ""}


def this_computer() -> None:
    """Back to this computer's own BioManager, from a lab another device
    holds that the window opened (app/devices.py: Open the lab in this window)."""
    updates.save_prefs(window_url="")
    if _state["local_url"]:
        _js(f"window.location.href = {json.dumps(_state['local_url'] + 'settings/devices')};")


class DesktopApi:
    """Called from the page (window.pywebview.api)."""

    calls = 0

    def set_nav(self, sections):
        """The sidebar's sections: [{"label", "links": [{"label", "url"}]}].
        Only paths on this app are kept."""
        DesktopApi.calls += 1
        clean = []
        for section in sections if isinstance(sections, list) else []:
            if not isinstance(section, dict) or not isinstance(section.get("links"), list):
                continue
            links = [{"label": str(l.get("label") or "")[:60], "url": str(l.get("url") or "")}
                     for l in (section.get("links") or []) if isinstance(l, dict)]
            links = [l for l in links if l["label"] and l["url"].startswith("/") and not l["url"].startswith("//")]
            if links:
                clean.append({"label": str(section.get("label") or "")[:40], "links": links})
        if clean != _state["nav"]:
            _state["nav"] = clean
            if sys.platform == "darwin":
                from PyObjCTools import AppHelper
                AppHelper.callAfter(_rebuild_go_menu)
        return True


# ---------------------------------------------------------------- helpers shared by both

def _js(code: str) -> None:
    window = _state["window"]
    if window is None:
        return
    if sys.platform == "darwin":
        view = _webview()
        if view is not None:
            view.evaluateJavaScript_completionHandler_(code, None)
            return
    threading.Thread(target=lambda: window.run_js(code), daemon=True).start()


def _go(path: str) -> None:
    _js(f"window.location.href = {json.dumps(path)};")


def new_tab() -> None:
    _js("if (window.BiomanagerTabs) { BiomanagerTabs.requestNewTab(); } window.location.href = '/';")


def close_tab() -> None:
    _js("if (window.BiomanagerTabs) BiomanagerTabs.closeCurrent();")


def step_tab(delta: int) -> None:
    _js(f"if (window.BiomanagerTabs) BiomanagerTabs.step({int(delta)});")


def search() -> None:
    _js("if (window.BiomanagerSearch) BiomanagerSearch.open();")


def export_sheet() -> None:
    _js("""(function () {
      const b = [...document.querySelectorAll('.dt-toolbar button, .dt-toolbar a')]
        .find((el) => el.offsetParent !== null && /^\\s*export\\b/i.test(el.textContent));
      if (b) b.click();
      else if (window.BioDialog) BioDialog.alert('This page has no sheet to export. Open a sheet, like Mice or Reagents, first.');
    })();""")


def check_for_updates(manual: bool) -> None:
    """In the background; the answer is shown on the main thread."""
    def run():
        answer = updates.check(manual=manual)
        if answer["state"] == "quiet":
            return
        if sys.platform == "darwin":
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_mac_update_alert, answer, manual)
        else:
            _plain_update_dialog(answer)
    threading.Thread(target=run, daemon=True).start()


def check_on_launch() -> None:
    def later():
        time.sleep(6)
        check_for_updates(manual=False)
    threading.Thread(target=later, daemon=True).start()


def _install(release: dict) -> None:
    """Download, check and stage the update, then quit so it takes over."""
    def run():
        try:
            message = updates.install_update(release)
        except (OSError, ValueError) as error:
            _say(f"The update wasn't installed: {error}", release)
            return
        _quit(message)
    threading.Thread(target=run, daemon=True).start()


def _say(message: str, release: dict | None = None) -> None:
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper
        AppHelper.callAfter(_mac_message, message, release)
    else:
        window = _state["window"]
        if window.create_confirmation_dialog("BioManager", message + ("\n\nDownload it instead?" if release else "")) and release:
            webbrowser.open(release["download"] or release["page"])


def _quit(message: str) -> None:
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper

        def done():
            import AppKit
            _mac_message(message, None)
            AppKit.NSApplication.sharedApplication().terminate_(None)
        AppHelper.callAfter(done)
    else:
        window = _state["window"]
        window.create_confirmation_dialog("BioManager", message)
        window.destroy()


def _plain_update_dialog(answer: dict) -> None:
    window = _state["window"]
    if answer["state"] == "newer":
        r = answer["release"]
        if updates.can_install(r):
            if window.create_confirmation_dialog(f"BioManager {r['version']} is available",
                                                 f"You have {answer['current']}. Install it and restart?\n\n{r['notes']}"):
                _install(r)
        elif window.create_confirmation_dialog(f"BioManager {r['version']} is available",
                                               f"You have {answer['current']}. Download it now?\n\n{r['notes']}"):
            webbrowser.open(r["download"] or r["page"])
    elif answer["state"] == "current":
        window.create_confirmation_dialog("BioManager is up to date", f"{answer['version']} is the latest version.")
    else:
        window.create_confirmation_dialog("Couldn't check for updates", answer["message"])


# ---------------------------------------------------------------- Windows and Linux

def plain_menus() -> list:
    """pywebview's cross-platform menus, without shortcuts."""
    from webview.menu import Menu, MenuAction, MenuSeparator
    return [
        Menu("File", [MenuAction("New Tab", new_tab), MenuAction("Close Tab", close_tab), MenuSeparator(),
                      MenuAction("Export This Sheet…", export_sheet),
                      MenuAction("Print…", lambda: _js("window.print();"))]),
        Menu("Go", [MenuAction("Back", lambda: _js("history.back();")),
                    MenuAction("Forward", lambda: _js("history.forward();")), MenuSeparator(),
                    MenuAction("Home", lambda: _go("/home")), MenuAction("Search…", search),
                    MenuAction("Settings", lambda: _go("/settings")), MenuSeparator(),
                    MenuAction("This Computer's BioManager", this_computer)]),
        Menu("Help", [MenuAction("BioManager User Guide", lambda: webbrowser.open(updates.GUIDE_URL)),
                      MenuAction("Keyboard Shortcuts", lambda: webbrowser.open(updates.GUIDE_URL + "#keys")),
                      MenuAction("Report a Problem…", lambda: webbrowser.open(updates.ISSUES_URL)),
                      MenuSeparator(), MenuAction("Check for Updates…", lambda: check_for_updates(True)),
                      MenuAction(f"About BioManager {updates.version()}",
                                 lambda: webbrowser.open(updates.RELEASES_PAGE))]),
    ]


# ---------------------------------------------------------------- macOS

_target = None


def _webview():
    try:
        from webview.platforms.cocoa import BrowserView
        instance = BrowserView.instances.get(_state["window"].uid)
        return instance.webview if instance else None
    except Exception:
        return None


def _nswindow():
    try:
        from webview.platforms.cocoa import BrowserView
        instance = BrowserView.instances.get(_state["window"].uid)
        return instance.window if instance else None
    except Exception:
        return None


def _target_object():
    """One Objective-C object every BioManager menu item calls; it runs the
    Python function registered under the item's tag."""
    global _target
    if _target is not None:
        return _target
    import AppKit
    import objc

    try:
        cls = objc.lookUpClass("BMMenuTarget")
    except objc.nosuchclass_error:
        class BMMenuTarget(AppKit.NSObject):
            handlers = {}

            def fire_(self, sender):
                handler = BMMenuTarget.handlers.get(sender.tag())
                if handler:
                    handler()

            def validateMenuItem_(self, item):
                return True
        cls = BMMenuTarget
    _target = cls.alloc().init()
    return _target


_next_tag = [1000]


def _item(menu, title: str, handler=None, key: str = "", mods=None, action: str | None = None):
    """A menu item: a Python `handler`, or a system `action` (a selector
    sent along the responder chain, like "copy:")."""
    import AppKit
    if action:
        item = menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
    else:
        item = menu.addItemWithTitle_action_keyEquivalent_(title, "fire:", key)
        target = _target_object()
        _next_tag[0] += 1
        item.setTag_(_next_tag[0])
        type(target).handlers[_next_tag[0]] = handler
        item.setTarget_(target)
    if mods is not None:
        item.setKeyEquivalentModifierMask_(mods)
    return item


def _submenu(main, title: str):
    import AppKit
    menu = AppKit.NSMenu.alloc().initWithTitle_(title)
    holder = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    holder.setSubmenu_(menu)
    main.addItem_(holder)
    return menu


def _sep(menu):
    import AppKit
    menu.addItem_(AppKit.NSMenuItem.separatorItem())


def _set_zoom(value: float) -> None:
    view = _webview()
    value = max(0.5, min(2.5, round(value, 2)))
    if view is not None and view.respondsToSelector_("setPageZoom:"):
        view.setPageZoom_(value)
    updates.save_prefs(zoom=value)


def _zoom_by(factor: float) -> None:
    _set_zoom(float(updates.load_prefs().get("zoom") or 1.0) * factor)


def _set_appearance(name: str) -> None:
    import AppKit
    app = AppKit.NSApplication.sharedApplication()
    names = {"light": "NSAppearanceNameAqua", "dark": "NSAppearanceNameDarkAqua"}
    app.setAppearance_(AppKit.NSAppearance.appearanceNamed_(names[name]) if name in names else None)
    for key, item in _state["appearance_items"].items():
        item.setState_(1 if key == name else 0)
    updates.save_prefs(appearance=name)


def _toggle_auto_check() -> None:
    on = not updates.load_prefs()["check_updates"]
    updates.save_prefs(check_updates=on)
    if _state["auto_item"] is not None:
        _state["auto_item"].setState_(1 if on else 0)


def _about() -> None:
    import AppKit
    app = AppKit.NSApplication.sharedApplication()
    credits = AppKit.NSAttributedString.alloc().initWithString_(
        "Your lab's animals, stocks and supplies, in one place.\nbiomanager.org")
    options = {"ApplicationName": "BioManager", "ApplicationVersion": updates.version(), "Version": "",
               "Credits": credits}
    icon = _icon()
    if icon is not None:
        options["ApplicationIcon"] = icon
    app.activateIgnoringOtherApps_(True)
    app.orderFrontStandardAboutPanelWithOptions_(options)


def _icon():
    import AppKit
    from pathlib import Path
    for path in (Path(getattr(sys, "_MEIPASS", ".")) / "app/static/icon-512.png",
                 Path(__file__).resolve().parent / "app/static/icon-512.png"):
        if path.exists():
            return AppKit.NSImage.alloc().initWithContentsOfFile_(str(path))
    return None


def _print() -> None:
    import AppKit
    view, window = _webview(), _nswindow()
    if view is None or not view.respondsToSelector_("printOperationWithPrintInfo:"):
        _js("window.print();")
        return
    info = AppKit.NSPrintInfo.sharedPrintInfo()
    info.setHorizontalPagination_(AppKit.NSPrintingPaginationModeFit if hasattr(AppKit, "NSPrintingPaginationModeFit") else 1)
    operation = view.printOperationWithPrintInfo_(info)
    operation.view().setFrame_(view.bounds())
    operation.setShowsPrintPanel_(True)
    operation.setShowsProgressPanel_(True)
    operation.runOperationModalForWindow_delegate_didRunSelector_contextInfo_(window, None, None, None)


def _mac_update_alert(answer: dict, manual: bool) -> None:
    import AppKit
    alert = AppKit.NSAlert.alloc().init()
    icon = _icon()
    if icon is not None:
        alert.setIcon_(icon)
    if answer["state"] == "newer":
        r = answer["release"]
        installable = updates.can_install(r)
        alert.setMessageText_(f"BioManager {r['version']} is available")
        alert.setInformativeText_(f"You have {answer['current']}. Your data stays where it is.\n\n{r['notes']}".strip())
        alert.addButtonWithTitle_("Install and Restart" if installable else "Download")
        alert.addButtonWithTitle_("Later")
        if not manual:
            alert.addButtonWithTitle_("Skip This Version")
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        choice = alert.runModal()
        if choice == AppKit.NSAlertFirstButtonReturn:
            if installable:
                _install(r)
            else:
                AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(r["download"] or r["page"]))
        elif choice == AppKit.NSAlertThirdButtonReturn:
            updates.save_prefs(skip_version=r["version"])
        return
    if answer["state"] == "current":
        alert.setMessageText_("BioManager is up to date")
        alert.setInformativeText_(f"{answer['version']} is the latest version.")
    else:
        alert.setMessageText_("Couldn't check for updates")
        alert.setInformativeText_(answer["message"])
    alert.addButtonWithTitle_("OK")
    alert.runModal()


def _mac_message(message: str, release: dict | None) -> None:
    import AppKit
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_("BioManager")
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_("OK")
    if release:
        alert.addButtonWithTitle_("Download Instead")
    if alert.runModal() == AppKit.NSAlertSecondButtonReturn and release:
        AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(release["download"] or release["page"]))


def _rebuild_go_menu() -> None:
    menu = _state["go_menu"]
    if menu is None:
        return
    import AppKit
    cmd, shift = AppKit.NSEventModifierFlagCommand, AppKit.NSEventModifierFlagShift
    menu.removeAllItems()
    view = _webview()
    _item(menu, "Back", lambda: view.goBack_(None) if view else None, "[")
    _item(menu, "Forward", lambda: view.goForward_(None) if view else None, "]")
    _sep(menu)
    _item(menu, "Home", lambda: _go("/home"), "h", cmd | shift)
    _item(menu, "Search…", search, "k")
    _item(menu, "This Computer's BioManager", this_computer)
    _sep(menu)
    # With Shift, ] and [ arrive as } and {, as Safari has them.
    _item(menu, "Next Tab", lambda: step_tab(1), "}", cmd | shift)
    _item(menu, "Previous Tab", lambda: step_tab(-1), "{", cmd | shift)
    number = 0
    for section in _state["nav"]:
        _sep(menu)
        header = menu.addItemWithTitle_action_keyEquivalent_(section["label"], None, "")
        header.setEnabled_(False)
        for link in section["links"]:
            if link["url"] in ("/home",):
                continue
            key = ""
            # ⌘1…⌘9: the databases, in the sidebar's order.
            if section["label"] == "Databases" and not link["url"].endswith("/new"):
                number += 1
                key = str(number) if number <= 9 else ""
            _item(menu, link["label"], (lambda url=link["url"]: _go(url)), key)


def install_mac(window) -> None:
    """Replace pywebview's default menu bar. Runs on the main thread, once
    the window is up."""
    import AppKit
    from PyObjCTools import AppHelper
    _state["window"] = window
    app = AppKit.NSApplication.sharedApplication()
    cmd = AppKit.NSEventModifierFlagCommand
    shift, option, control = (AppKit.NSEventModifierFlagShift, AppKit.NSEventModifierFlagOption,
                              AppKit.NSEventModifierFlagControl)
    # BioManager has tabs of its own; the system's window tabs (and the
    # "Show Tab Bar" items it adds to View) would be a second, confusing kind.
    AppKit.NSWindow.setAllowsAutomaticWindowTabbing_(False)
    main = AppKit.NSMenu.alloc().initWithTitle_("MainMenu")

    # BioManager
    m = _submenu(main, "BioManager")
    _item(m, "About BioManager", _about)
    _item(m, "Check for Updates…", lambda: check_for_updates(True))
    auto = _item(m, "Check for Updates Automatically", _toggle_auto_check)
    auto.setState_(1 if updates.load_prefs()["check_updates"] else 0)
    _state["auto_item"] = auto
    _sep(m)
    _item(m, "Settings…", lambda: _go("/settings"), ",")
    _sep(m)
    services = AppKit.NSMenu.alloc().initWithTitle_("Services")
    app.setServicesMenu_(services)
    m.addItemWithTitle_action_keyEquivalent_("Services", None, "").setSubmenu_(services)
    _sep(m)
    _item(m, "Hide BioManager", action="hide:", key="h")
    _item(m, "Hide Others", action="hideOtherApplications:", key="h", mods=cmd | option)
    _item(m, "Show All", action="unhideAllApplications:")
    _sep(m)
    _item(m, "Quit BioManager", action="terminate:", key="q")

    # File
    m = _submenu(main, "File")
    _item(m, "New Tab", new_tab, "t")
    _item(m, "Close Tab", close_tab, "w")
    _item(m, "Close Window", action="performClose:", key="w", mods=cmd | shift)
    _sep(m)
    _item(m, "Export This Sheet…", export_sheet, "e", cmd | shift)
    _item(m, "Print…", _print, "p")

    # Edit: the system's own commands, which the page's fields answer.
    m = _submenu(main, "Edit")
    _item(m, "Undo", action="undo:", key="z")
    _item(m, "Redo", action="redo:", key="z", mods=cmd | shift)
    _sep(m)
    _item(m, "Cut", action="cut:", key="x")
    _item(m, "Copy", action="copy:", key="c")
    _item(m, "Paste", action="paste:", key="v")
    _item(m, "Paste and Match Style", action="pasteAsPlainText:", key="v", mods=cmd | option | shift)
    _item(m, "Delete", action="delete:")
    _item(m, "Select All", action="selectAll:", key="a")
    _sep(m)
    _item(m, "Search BioManager…", search, "k")

    # View
    m = _submenu(main, "View")
    _item(m, "Reload Page", lambda: _webview().reload_(None) if _webview() else None, "r")
    _sep(m)
    _item(m, "Actual Size", lambda: _set_zoom(1.0), "0")
    _item(m, "Zoom In", lambda: _zoom_by(1.1), "=")
    _item(m, "Zoom Out", lambda: _zoom_by(1 / 1.1), "-")
    _sep(m)
    look = AppKit.NSMenu.alloc().initWithTitle_("Appearance")
    m.addItemWithTitle_action_keyEquivalent_("Appearance", None, "").setSubmenu_(look)
    for key, title in (("system", "Use System Setting"), ("light", "Light"), ("dark", "Dark")):
        _state["appearance_items"][key] = _item(look, title, (lambda k=key: _set_appearance(k)))
    _sep(m)
    _item(m, "Enter Full Screen", action="toggleFullScreen:", key="f", mods=cmd | control)

    # Go: filled from the sidebar
    go = _submenu(main, "Go")
    _state["go_menu"] = go

    # Window
    m = _submenu(main, "Window")
    _item(m, "Minimize", action="performMiniaturize:", key="m")
    _item(m, "Zoom", action="performZoom:")
    _sep(m)
    _item(m, "Bring All to Front", action="arrangeInFront:")
    app.setWindowsMenu_(m)

    # Help
    m = _submenu(main, "Help")
    _item(m, "BioManager User Guide", lambda: webbrowser.open(updates.GUIDE_URL))
    _item(m, "Keyboard Shortcuts", lambda: webbrowser.open(updates.GUIDE_URL + "#keys"))
    _item(m, "What's New", lambda: webbrowser.open(updates.RELEASES_PAGE))
    _sep(m)
    _item(m, "Report a Problem…", lambda: webbrowser.open(updates.ISSUES_URL))
    app.setHelpMenu_(m)

    app.setMainMenu_(main)
    _rebuild_go_menu()
    if os.environ.get("BIOMANAGER_MENU_DUMP"):
        # For checking the menus from outside the app (a build, a test run).
        threading.Timer(4, lambda: AppHelper.callAfter(_dump_menus, os.environ["BIOMANAGER_MENU_DUMP"])).start()
    prefs = updates.load_prefs()
    _set_appearance(prefs.get("appearance") or "system")
    if float(prefs.get("zoom") or 1.0) != 1.0:
        _set_zoom(float(prefs["zoom"]))


def _dump_menus(path: str) -> None:
    import AppKit

    def walk(menu, depth):
        out = []
        for item in menu.itemArray():
            if item.isSeparatorItem():
                out.append("  " * depth + "---")
                continue
            key = item.keyEquivalent() or ""
            mods = item.keyEquivalentModifierMask()
            marks = "".join(sym for flag, sym in ((AppKit.NSEventModifierFlagControl, "⌃"),
                                                  (AppKit.NSEventModifierFlagOption, "⌥"),
                                                  (AppKit.NSEventModifierFlagShift, "⇧"),
                                                  (AppKit.NSEventModifierFlagCommand, "⌘")) if key and mods & flag)
            state = " ✓" if item.state() == 1 else ""
            state += " (hidden)" if item.isHidden() else ""
            state += " (alternate)" if item.isAlternate() else ""
            out.append("  " * depth + f"{item.title()}{state}" + (f"  {marks}{key.upper()}" if key else ""))
            if item.submenu() is not None:
                out += walk(item.submenu(), depth + 1)
        return out

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(walk(AppKit.NSApplication.sharedApplication().mainMenu(), 0)) + "\n")
        f.write(f"# set_nav called {DesktopApi.calls} time(s); {sum(len(s['links']) for s in _state['nav'])} links\n")


def start(window) -> None:
    """pywebview's `func`: once the window is shown, put up the menus and
    check for updates (at most daily)."""
    _state["window"] = window
    window.events.shown.wait(15)
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper
        AppHelper.callAfter(install_mac, window)
    check_on_launch()
