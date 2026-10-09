"""The Mac window without a title bar.

The page runs to the window's top edge and the close, minimise and zoom
buttons sit over the top of the sidebar, as in Finder or Notes, beside the
row of tabs. The window keeps its title (the Window menu, Mission Control
and the Dock still show it); it just doesn't draw a bar for it.

- `prepare` runs as pywebview's `before_show`, on the main thread and before
  the first page loads: it hides the title bar, and adds a script that marks
  every page `<html class="mac-window">` before it draws, so the page leaves
  room for the buttons (frontend/src/tailwind.css). In full screen, where the
  buttons are gone, the mark is `mac-window is-fullscreen`.
- The buttons are moved down to the middle of the page's top row
  (`ROW_CENTER`), and moved back each time AppKit lays the title bar out
  again (a resize, leaving full screen).
- The empty part of the top row behaves as a title bar: static/shell.js
  posts "drag" when it is dragged and "zoom" when it is double-clicked, to the
  `bmWindow` message handler here, and the window moves with the mouse
  natively (`performWindowDragWithEvent:`), or zooms or minimises as the
  person's "Double-click a window's title bar" setting says.
"""
from __future__ import annotations

import objc

# Where the middle of the traffic lights goes, in points from the window's
# top: the middle of the page's top row (--tabbar-h in tailwind.css).
ROW_CENTER = 23

_SCRIPT = "document.documentElement.classList.add('mac-window'%s);"
_state: dict = {}


def prepare(window) -> None:
    """Hide the title bar of `window` (a pywebview Window) before it shows."""
    import AppKit
    from webview.platforms.cocoa import BrowserView

    browser = BrowserView.instances.get(window.uid)
    nswindow = window.native
    if browser is None or nswindow is None:
        return
    nswindow.setStyleMask_(nswindow.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)
    nswindow.setTitlebarAppearsTransparent_(True)
    nswindow.setTitleVisibility_(AppKit.NSWindowTitleHidden)
    # pywebview paints the title bar's container in the window colour (so a
    # normal title bar keeps its colour); here it would be a grey band over
    # the page's top row.
    container = nswindow.contentView().superview().subviews().lastObject()
    if container is not None and container.respondsToSelector_("setBackgroundColor:"):
        container.setBackgroundColor_(AppKit.NSColor.clearColor())

    controller = browser.webview.configuration().userContentController()
    _state.update(window=nswindow, webview=browser.webview, controller=controller)
    _mark_pages(fullscreen=False)
    handler = _WindowMessages.alloc().init()
    _state["handler"] = handler
    controller.addScriptMessageHandler_name_(handler, "bmWindow")

    # The latest mouse-down in the window: a drag asked for by the page
    # starts from it, as a title bar's would.
    mask = AppKit.NSEventMaskLeftMouseDown

    def remember(event):
        if event.window() is nswindow:
            _state["mouse_down"] = event
        return event

    _state["monitor"] = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, remember)

    center = AppKit.NSNotificationCenter.defaultCenter()
    queue = AppKit.NSOperationQueue.mainQueue()
    observers = []
    for name, fullscreen in ((AppKit.NSWindowDidEnterFullScreenNotification, True),
                             (AppKit.NSWindowDidExitFullScreenNotification, False)):
        observers.append(center.addObserverForName_object_queue_usingBlock_(
            name, nswindow, queue, (lambda note, on=fullscreen: _fullscreen(on))))
    for name in (AppKit.NSWindowDidResizeNotification, AppKit.NSWindowDidBecomeKeyNotification,
                 AppKit.NSWindowDidResignKeyNotification):
        observers.append(center.addObserverForName_object_queue_usingBlock_(
            name, nswindow, queue, lambda note: _place_buttons()))
    _state["observers"] = observers
    _place_buttons()


def _mark_pages(fullscreen: bool) -> None:
    """The script that marks each page as it starts, before it draws."""
    import WebKit
    controller = _state["controller"]
    controller.removeAllUserScripts()
    source = _SCRIPT % (", 'is-fullscreen'" if fullscreen else "")
    script = WebKit.WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
        source, WebKit.WKUserScriptInjectionTimeAtDocumentStart, True)
    controller.addUserScript_(script)


def _fullscreen(on: bool) -> None:
    _mark_pages(fullscreen=on)
    webview = _state.get("webview")
    if webview is not None:
        webview.evaluateJavaScript_completionHandler_(
            "document.documentElement.classList.toggle('is-fullscreen', %s);" % ("true" if on else "false"), None)
    if not on:
        _place_buttons()


def _place_buttons() -> None:
    """Centre the traffic lights on the page's top row. AppKit puts them back
    in a 28-point title bar whenever it lays the bar out, so this runs again
    after each resize and change of focus."""
    import AppKit
    nswindow = _state.get("window")
    if nswindow is None or nswindow.styleMask() & AppKit.NSWindowStyleMaskFullScreen:
        return
    close = nswindow.standardWindowButton_(AppKit.NSWindowCloseButton)
    if close is None or close.superview() is None or close.superview().superview() is None:
        return
    container = close.superview().superview()
    button = close.frame()
    # The container's height sets how far down the buttons sit: they keep
    # their place measured from its bottom edge.
    height = ROW_CENTER + button.size.height / 2 + button.origin.y
    frame = container.frame()
    if abs(frame.size.height - height) < 0.5:
        return
    frame.size.height = height
    frame.origin.y = nswindow.frame().size.height - height
    container.setFrame_(frame)


def _double_click_action(nswindow) -> None:
    import AppKit
    action = AppKit.NSUserDefaults.standardUserDefaults().stringForKey_("AppleActionOnDoubleClick")
    if action == "Minimize":
        nswindow.performMiniaturize_(None)
    elif action != "None":
        nswindow.performZoom_(None)


class _WindowMessages(objc.lookUpClass("NSObject")):
    """`window.webkit.messageHandlers.bmWindow.postMessage("drag" | "zoom")`."""

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        nswindow = _state.get("window")
        if nswindow is None:
            return
        kind = message.body()
        if kind == "drag" and _state.get("mouse_down") is not None:
            nswindow.performWindowDragWithEvent_(_state["mouse_down"])
        elif kind == "zoom":
            _double_click_action(nswindow)
