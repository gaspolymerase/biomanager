"""The desktop app's relay: its window works on the lab's server through it.

A desktop that joined a lab (Open your lab, app/door.py) never opens the
server's pages itself. It runs this small server on 127.0.0.1 and its window
opens that; each request goes on to the lab's server with the device's
token (app/lab_devices.py), so the server knows which computer it is, signed
in as its person, and the answer comes back unchanged. The relay keeps the
server's cookies (its messages after a change, the language) in its own jar,
and points the server's own addresses (redirects) back at itself.

Only this computer's window is served: the Host must be the relay's own
address (a page elsewhere can't reach it by another name), and a change must
come from its own pages (the browser's Sec-Fetch-Site, else Origin or
Referer), the same check the server makes, since the relay adds the token
to whatever reaches it.

It keeps its connections to the server open and uses them again (_take,
_keep): each new one cost a TLS handshake, two or three round trips to the
server and the setting up of its certificates, for every page, script and
autosave. A kept one is checked before use (_closed: one the server has
closed reads as finished) and dropped after IDLE_REUSE seconds; a request
that fails on one goes again, once, on a new connection (a server closes
an idle connection only between requests, so the first never reached it).
A body over REPLAY_MAX (an upload) is not held to send twice: it always
gets a new connection.

When the server can't be reached, the window gets the offline screens
(app/static/offline/, at /_offline/) working on this computer's copy of the
facility and queueing what is done (app/offline_store.py); before there is a
copy, a page that says the server can't be reached (offline_page). Sign out
in the window becomes "Sign this computer out of the lab?", which revokes
the device.
"""
from __future__ import annotations

import http.client
import json
import mimetypes
import select
import socket
import ssl
import threading
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import quote, urlsplit

from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade"}
DROPPED_UP = HOP | {"host", "cookie", "authorization", "content-length"}
DROPPED_DOWN = HOP | {"set-cookie", "strict-transport-security", "content-length"}
CHUNK = 64 * 1024
IDLE_REUSE = 30.0             # seconds a kept connection may wait to be used again
KEEP_AT_MOST = 6              # kept connections, at most (the window asks for a few things at once)
REPLAY_MAX = 2 * 1024 * 1024  # a request body up to this is held, so it can go again
LOCAL = "/_relay/"
OFFLINE = "/_offline/"
BUNDLE = Path(__file__).resolve().parent / "static" / "offline"
STRINGS_ZH = Path(__file__).resolve().parent / "translations" / "zh" / "js-offline.json"


class _Headers:
    """What http.cookiejar asks of a response: its headers."""

    def __init__(self, msg):
        self._msg = msg

    def info(self):
        return self._msg


class Relay:
    def __init__(self, server: str, token, version: str = "", on_leave=None, offline=None, store=None):
        self.server = server.rstrip("/")
        split = urlsplit(self.server)
        self.https, self.netloc = split.scheme == "https", split.netloc
        self.token = token                  # a function: the device token now (it can change)
        self.version = version
        self.on_leave = on_leave            # called after the device signs out of the lab
        self.offline = offline              # a WSGI app for when the server is out of reach
        self.store = store                  # this computer's copy of the facility (app/offline_store.py)
        self.jar = CookieJar()
        self.base = ""                      # http://127.0.0.1:<port>, once serving
        # One TLS context for every connection: making one reads the trusted
        # certificates again, some 20 ms each time.
        self._context = ssl.create_default_context() if self.https else None
        self._idle: list[tuple[http.client.HTTPConnection, float]] = []   # (kept connection, since)
        self._lock = threading.Lock()

    # ------------------------------------------------------------ connections
    def _connect(self, timeout: float = 120) -> http.client.HTTPConnection:
        if self.https:
            return http.client.HTTPSConnection(self.netloc, timeout=timeout, context=self._context)
        return http.client.HTTPConnection(self.netloc, timeout=timeout)

    def _take(self) -> http.client.HTTPConnection | None:
        """A kept connection that is still open, or None."""
        now = time.monotonic()
        with self._lock:
            while self._idle:
                conn, since = self._idle.pop()
                if now - since <= IDLE_REUSE and not _closed(conn):
                    return conn
                conn.close()
        return None

    def close(self) -> None:
        """Closes its kept connections (the relay is stopping)."""
        with self._lock:
            kept, self._idle = self._idle, []
        for conn, _ in kept:
            conn.close()

    def _keep(self, conn: http.client.HTTPConnection) -> None:
        with self._lock:
            if len(self._idle) < KEEP_AT_MOST:
                self._idle.append((conn, time.monotonic()))
                return
        conn.close()

    # ------------------------------------------------------------ checks
    def _own_host(self, req: Request) -> bool:
        host = (req.host or "").lower()
        port = self.base.rsplit(":", 1)[-1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _cross_site(self, req: Request) -> bool:
        if req.method in ("GET", "HEAD", "OPTIONS"):
            return False
        site = req.headers.get("Sec-Fetch-Site", "")
        if site:
            return site not in ("same-origin", "none")
        for header in ("Origin", "Referer"):
            value = req.headers.get(header, "")
            if value and value != "null":
                return urlsplit(value).netloc.lower() != (req.host or "").lower()
        return False

    # ------------------------------------------------------------ serving
    def __call__(self, environ, start_response):
        req = Request(environ)
        if not self._own_host(req):
            return Response("Not here.", 421)(environ, start_response)
        if self._cross_site(req):
            return Response("This change came from another website, so it was refused.", 403)(environ, start_response)
        if req.path.startswith(LOCAL):
            return self._local(req)(environ, start_response)
        if req.path == OFFLINE[:-1] or req.path.startswith(OFFLINE):
            return self._bundle(req)(environ, start_response)
        if req.path == "/logout":
            return Response(status=303, headers={"Location": LOCAL + "leave"})(environ, start_response)
        try:
            return self._forward(req)(environ, start_response)
        except (OSError, http.client.HTTPException, ssl.SSLError):
            if self.offline is not None:
                return self.offline(environ, start_response)
            if self.store is not None and self.store.snapshot() is not None and req.method == "GET" \
                    and "text/html" in req.headers.get("Accept", ""):
                # The offline screens, on this computer's copy; they open what was asked for.
                return Response(status=303, headers={
                    "Location": OFFLINE + "?from=" + quote(req.full_path.rstrip("?"), safe="")})(environ, start_response)
            return offline_page(self, req)(environ, start_response)

    def _cookie_request(self, req: Request) -> urllib.request.Request:
        return urllib.request.Request(self.server + req.full_path.rstrip("?"))

    def _forward(self, req: Request) -> Response:
        path = req.full_path.rstrip("?") if req.query_string else req.path
        headers = {k: v for k, v in req.headers.items() if k.lower() not in DROPPED_UP}
        headers["Host"] = self.netloc
        headers["Authorization"] = f"Bearer {self.token()}"
        headers["X-BioManager-Version"] = self.version
        if "Origin" in headers:
            headers["Origin"] = self.server
        if "Referer" in headers:
            headers["Referer"] = headers["Referer"].replace(self.base, self.server, 1)
        cookie_req = self._cookie_request(req)
        self.jar.add_cookie_header(cookie_req)
        if cookie_req.has_header("Cookie"):
            headers["Cookie"] = cookie_req.get_header("Cookie")
        body = None
        replayable = True
        length = req.content_length
        if length:
            headers["Content-Length"] = str(length)
            replayable = length <= REPLAY_MAX
            body = req.get_data() if replayable else req.stream
        conn = self._take() if replayable else None
        reused = conn is not None
        if conn is None:
            conn = self._connect()
        try:
            conn.request(req.method, path, body=body, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException):
            conn.close()
            if not reused:
                raise
            # The server had closed it between requests: this one never reached it.
            conn = self._connect()
            conn.request(req.method, path, body=body, headers=headers)
            resp = conn.getresponse()
        self.jar.extract_cookies(_Headers(resp.msg), cookie_req)
        out = Response(status=resp.status)
        # Werkzeug starts every Response as text/plain; added beside the
        # server's own, WebKit (nosniff) took a stylesheet for plain text.
        out.headers.remove("Content-Type")
        for key, value in resp.getheaders():
            low = key.lower()
            if low in DROPPED_DOWN:
                continue
            if low == "location" and value.startswith(self.server):
                value = self.base + value[len(self.server):]
            out.headers.add(key, value)

        def stream():
            done = False
            try:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        done = True
                        break
                    yield chunk
            finally:
                # Read to its end and not closing: ready for the next request.
                if done and not resp.will_close:
                    self._keep(conn)
                else:
                    conn.close()
        out.response = stream()
        out.direct_passthrough = True
        return out

    # ------------------------------------------------------------ its own pages
    def _local(self, req: Request) -> Response:
        name = req.path[len(LOCAL):]
        if name == "leave":
            if req.method == "POST":
                try:
                    self._call("POST", "/api/devices/leave")
                except (OSError, http.client.HTTPException):
                    pass                     # out of reach: it is forgotten here anyway
                if self.on_leave:
                    target = self.on_leave()
                    return Response(status=303, headers={"Location": target})
                return Response("Signed out.", 200)
            return leave_page(self, req)
        if name == "status":
            try:
                status, _ = self._call("GET", "/healthz")
                ok = status == 200
            except (OSError, http.client.HTTPException):
                ok = False
            body = {"online": ok, **({"store": self.store.status()} if self.store is not None else {})}
            return _json(body)
        if self.store is None:
            return Response("Not found.", 404)
        if name == "store" and req.method == "GET":
            return _json({"snapshot": self.store.snapshot(), "queue": self.store.queue(),
                          "status": self.store.status()})
        if name == "store/queue" and req.method == "POST":
            try:
                waiting = self.store.add(json.loads(req.get_data(as_text=True) or "{}"))
            except ValueError as error:
                return _json({"ok": False, "error": str(error)}, 400)
            return _json({"ok": True, "waiting": waiting})
        if name == "sync" and req.method == "POST":
            return _json({"ok": True, "status": self.store.sync()})
        return Response("Not found.", 404)

    def _bundle(self, req: Request) -> Response:
        """The offline screens (app/static/offline/) and their words."""
        name = req.path[len(OFFLINE):] or "index.html"
        if name == "strings.json":
            from . import i18n
            zh = i18n.from_header(req.headers.get("Accept-Language")) == "zh"
            words = json.loads(STRINGS_ZH.read_text(encoding="utf-8")) if zh and STRINGS_ZH.is_file() else {}
            return _json({"lang": "zh" if zh else "en", "words": words})
        path = (BUNDLE / name).resolve()
        if BUNDLE not in path.parents or not path.is_file():
            return Response("Not found.", 404)
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        out = Response(path.read_bytes(), 200, mimetype=kind)
        out.headers["Cache-Control"] = "no-store"
        return out

    def _call(self, method: str, path: str) -> tuple[int, bytes]:
        conn = self._connect(timeout=15)
        try:
            conn.request(method, path, headers={"Authorization": f"Bearer {self.token()}", "Host": self.netloc,
                                                "Content-Length": "0", "X-BioManager-Version": self.version})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()


def _closed(conn: http.client.HTTPConnection) -> bool:
    """Whether a kept connection has been closed by the server: an idle one
    has nothing to read, so one that can be read from has ended."""
    sock = conn.sock
    if sock is None:
        return True
    try:
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return True
    return bool(readable)


# ---------------------------------------------------------------- pages of its own

def _json(body, status: int = 200) -> Response:
    return Response(json.dumps(body), status, mimetype="application/json")


_PAGE = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>BioManager</title>
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font:15px/1.5 -apple-system,"Segoe UI",system-ui,sans-serif;background:#f4f6f6;color:#17201f}}
@media (prefers-color-scheme:dark){{body{{background:#121817;color:#e4ecea}} .card{{background:#1a2221!important}}}}
.card{{max-width:440px;padding:28px 30px;border-radius:18px;background:#fff;box-shadow:0 10px 40px rgba(0,0,0,.08)}}
h1{{font-size:22px;margin:0 0 8px}} p{{margin:0 0 12px;color:#4a5654}} .row{{display:flex;gap:8px;margin-top:16px}}
button,a.btn{{font:inherit;padding:8px 16px;border-radius:999px;border:1px solid #c8d2d0;background:transparent;
color:inherit;text-decoration:none;cursor:pointer}} .primary{{background:#0f7a6b!important;border-color:#0f7a6b!important;color:#fff!important}}
</style></head><body><main class="card">{body}</main></body></html>"""


def _texts(req: Request) -> dict:
    from . import i18n
    zh = i18n.from_header(req.headers.get("Accept-Language")) == "zh"
    if zh:
        return {"lang": "zh-CN",
                "offline_title": "连不上实验室的服务器",
                "offline_body": "这台电脑现在连不上 {server}。请检查网络、VPN 或 Tailscale。连上后页面会自动回来。",
                "retry": "重试", "here": "打开这台电脑自己的 BioManager",
                "leave_title": "让这台电脑退出实验室？",
                "leave_body": "这台电脑将不再登录 {server}，需要时可以重新加入。这台电脑自己的 BioManager 不受影响。",
                "leave": "退出", "stay": "取消"}
    return {"lang": "en",
            "offline_title": "The lab's server can't be reached",
            "offline_body": "This computer can't reach {server} just now. Check the network, VPN or Tailscale. The page comes back by itself once it can.",
            "retry": "Try again", "here": "Open this computer's own BioManager",
            "leave_title": "Sign this computer out of the lab?",
            "leave_body": "This computer stops being signed in to {server}; it can join again whenever you like. This computer's own BioManager isn't touched.",
            "leave": "Sign out", "stay": "Cancel"}


def offline_page(relay: Relay, req: Request) -> Response:
    import html
    t = _texts(req)
    if req.method != "GET" or "text/html" not in req.headers.get("Accept", ""):
        return Response('{"ok": false, "offline": true}', 503, mimetype="application/json")
    server = html.escape(relay.netloc)
    body = (f"<h1>{t['offline_title']}</h1><p>{t['offline_body'].format(server=server)}</p>"
            f"<div class=\"row\"><a class=\"btn primary\" href=\"{html.escape(req.full_path)}\">{t['retry']}</a></div>"
            "<script>setInterval(function(){fetch('/_relay/status').then(function(r){return r.json()})"
            ".then(function(s){if(s.online)location.reload()}).catch(function(){})},5000)</script>")
    return Response(_PAGE.format(lang=t["lang"], body=body), 503, mimetype="text/html")


def leave_page(relay: Relay, req: Request) -> Response:
    import html
    t = _texts(req)
    body = (f"<h1>{t['leave_title']}</h1><p>{t['leave_body'].format(server=html.escape(relay.netloc))}</p>"
            f"<form method=\"post\" class=\"row\"><button class=\"primary\" type=\"submit\">{t['leave']}</button>"
            f"<a class=\"btn\" href=\"/\">{t['stay']}</a></form>")
    return Response(_PAGE.format(lang=t["lang"], body=body), 200, mimetype="text/html")


# ---------------------------------------------------------------- running it

_running: dict = {}


def free_port(preferred: int = 0) -> int:
    for port in ([preferred] if preferred else []) + [0]:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def start(relay: Relay, preferred_port: int = 0) -> str:
    """Serve `relay` on 127.0.0.1 (the same port as last time when free);
    returns its address. A relay already running is stopped first."""
    stop()
    port = free_port(preferred_port)
    relay.base = f"http://127.0.0.1:{port}"
    server = make_server("127.0.0.1", port, relay, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="biomanager-relay")
    thread.start()
    _running.update(server=server, relay=relay, base=relay.base)
    return relay.base


def stop() -> None:
    server = _running.pop("server", None)
    relay = _running.pop("relay", None)
    _running.clear()
    if server is not None:
        server.shutdown()
    if relay is not None:
        relay.close()


def address() -> str:
    return _running.get("base", "")
