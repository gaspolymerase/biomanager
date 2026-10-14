"""The desktop joining a lab as a device and working through its relay
(app/desktop_join.py, app/relay.py), against a real server on this computer."""
from __future__ import annotations

import http.client
import logging
import threading
import time
from unittest import mock

from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

# tests.base first: it points the app at a throwaway database before anything
# imports the app. Importing app first would open data/biomanager.db.
from tests.base import AppTestCase, app, execute, make_user, one, uniq  # isort: skip

PASSWORD = "lab bench 42"
logging.getLogger("werkzeug").setLevel(logging.ERROR)       # each request, twice: the relay's and the server's


class LabServer:
    """The app served on a free port, as a lab's server would be."""

    def __enter__(self):
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()


def ask(base: str, method: str, path: str, headers: dict | None = None, body: bytes | None = None):
    host = base.split("//", 1)[1]
    conn = http.client.HTTPConnection(host, timeout=15)
    try:
        conn.request(method, path, body=body, headers={"Host": host, **(headers or {})})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


class Relay(AppTestCase):
    def setUp(self):
        super().setUp()
        from app import desktop_join
        self.addCleanup(desktop_join.forget)
        self.who = make_user(uniq("tech"))
        execute("update users set password_hash=? where username=?", generate_password_hash(PASSWORD), self.who)

    def test_the_desktop_joins_and_its_window_works_on_the_lab_through_the_relay(self):
        from app import desktop_join, devices
        with LabServer() as lab:
            result = desktop_join.join(lab.url, self.who, PASSWORD)
            self.assertTrue(result["ok"], result)
            self.assertEqual(devices.window_url(), "relay")
            self.assertEqual(one("select kind from lab_devices where public_id=?", result["device"]), "app")
            base = desktop_join.start_relay()
            self.assertTrue(base.startswith("http://127.0.0.1:"))
            status, _, page = ask(base, "GET", "/home", {"Accept": "text/html"})
            self.assertEqual(status, 200)
            self.assertIn(self.who, page)                      # signed in as its person, by the device's token
            # Each answer has the server's one Content-Type, so the stylesheet applies.
            host = base.split("//", 1)[1]
            conn = http.client.HTTPConnection(host, timeout=15)
            conn.request("GET", "/static/tailwind.css", headers={"Host": host})
            sheet = conn.getresponse()
            self.assertEqual(sheet.msg.get_all("Content-Type"), ["text/css; charset=utf-8"])
            conn.close()
            # A change from the window's own pages goes through; from another site it doesn't.
            cage = uniq("C")
            form = f"cage_id={cage}".encode()
            own = {"Content-Type": "application/x-www-form-urlencoded", "Origin": base}
            status, headers, _ = ask(base, "POST", "/colony/cages/create", own, form)
            self.assertIn(status, (200, 302, 303))
            self.assertEqual(one("select owner from mouse_cages where cage_id=?", cage), self.who)
            if "Location" in headers:
                self.assertFalse(headers["Location"].startswith(lab.url))   # back at the relay, never the server
            status, _, _ = ask(base, "POST", "/colony/cages/create",
                               {**own, "Origin": "https://elsewhere.example"}, f"cage_id={uniq('C')}".encode())
            self.assertEqual(status, 403)

    def test_only_this_computer_s_window_is_served(self):
        from app import desktop_join
        with LabServer() as lab:
            desktop_join.join(lab.url, self.who, PASSWORD)
            base = desktop_join.start_relay()
            port = base.rsplit(":", 1)[1]
            status, _, _ = ask(base, "GET", "/home", {"Host": f"attacker.example:{port}"})
            self.assertEqual(status, 421)

    def test_sign_out_signs_the_computer_out_of_the_lab(self):
        from app import desktop_join, devices
        with LabServer() as lab:
            joined = desktop_join.join(lab.url, self.who, PASSWORD)
            base = desktop_join.start_relay()
            status, headers, _ = ask(base, "GET", "/logout")
            self.assertEqual((status, headers["Location"]), (303, "/_relay/leave"))
            status, _, page = ask(base, "GET", "/_relay/leave", {"Accept": "text/html"})
            self.assertIn("Sign this computer out of the lab?", page)
            with mock.patch.dict("sys.modules", {"desktop_menu": mock.Mock(_state={"local_url": "http://127.0.0.1:1/"})}):
                status, headers, _ = ask(base, "POST", "/_relay/leave", {"Sec-Fetch-Site": "same-origin"})
            self.assertEqual((status, headers["Location"]), (303, "http://127.0.0.1:1/"))
            self.assertIsNotNone(one("select revoked_at from lab_devices where public_id=?", joined["device"]))
            self.assertIsNone(desktop_join.joined())
            self.assertEqual(devices.window_url(), "")

    def test_a_stylesheet_comes_through_as_one(self):
        # One Content-Type, the server's: a second (text/plain) beside it
        # made the Mac's window drop every stylesheet.
        from app import desktop_join
        with LabServer() as lab:
            desktop_join.join(lab.url, self.who, PASSWORD)
            base = desktop_join.start_relay()
            host = base.split("//", 1)[1]
            conn = http.client.HTTPConnection(host, timeout=15)
            try:
                conn.request("GET", "/static/tailwind.css", headers={"Host": host})
                resp = conn.getresponse()
                resp.read()
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.msg.get_all("Content-Type"), ["text/css; charset=utf-8"])
            finally:
                conn.close()

    def test_a_server_out_of_reach_says_so(self):
        from app import desktop_join
        with LabServer() as lab:
            desktop_join.join(lab.url, self.who, PASSWORD)
        base = desktop_join.start_relay()                     # the server has gone
        status, _, page = ask(base, "GET", "/home", {"Accept": "text/html"})
        self.assertEqual(status, 503)
        self.assertIn("The lab's server can't be reached", page)
        status, _, body = ask(base, "GET", "/_relay/status")
        import json
        self.assertEqual((status, json.loads(body)["online"], json.loads(body)["store"]["has_copy"]), (200, False, False))

    def test_a_wrong_password_joins_nothing(self):
        from app import desktop_join
        with LabServer() as lab:
            result = desktop_join.join(lab.url, self.who, "nope")
        self.assertFalse(result["ok"])
        self.assertIsNone(desktop_join.joined())


class OpenYourLab(AppTestCase):
    """Open your lab joins the lab; the window then opens the relay."""

    def setUp(self):
        super().setUp()
        from app import desktop_join
        self.addCleanup(desktop_join.forget)

    def test_joining_from_open_your_lab_opens_the_relay(self):
        from app import desktop_join, door
        who = make_user(uniq("tech"))
        execute("update users set password_hash=? where username=?", generate_password_hash(PASSWORD), who)
        with LabServer() as lab, mock.patch("app.devices.on_this_computer", return_value=True):
            c = app.test_client()
            found = {"url": lab.url, "name": "Rivera Lab", "host": lab.url.split("//")[1]}
            with mock.patch.object(door, "find_lab", return_value=found):
                html = c.get("/open-lab?address=" + found["host"]).get_data(as_text=True)
            self.assertIn("Join the lab", html)
            html = c.post("/open-lab", data={"action": "join", "username": who, "password": PASSWORD}).get_data(as_text=True)
            relay = desktop_join.start_relay()
            self.assertIn(relay, html)                         # the window goes to the relay, not the server
            self.assertNotIn(lab.url + "/", html)


class Offline(AppTestCase):
    """With the server out of reach, the window works on this computer's copy
    (app/offline_store.py) and what is done reaches the lab once it answers."""

    def setUp(self):
        super().setUp()
        from app import desktop_join
        self.addCleanup(desktop_join.forget)
        self.who = make_user(uniq("tech"))
        execute("update users set password_hash=? where username=?", generate_password_hash(PASSWORD), self.who)

    def test_offline_the_window_records_on_the_copy_and_sends_it_later(self):
        import json
        from app import desktop_join, relay
        lab = LabServer().__enter__()
        port = lab.server.server_port
        desktop_join.join(lab.url, self.who, PASSWORD)
        me = app.test_client()
        me.post("/login", data={"username": self.who, "password": PASSWORD})
        me.post("/colony/cages/create", data={"cage_id": uniq("C"), "purpose": "Breeding"})
        cage = one("select id from mouse_cages where owner=? order by id desc limit 1", self.who)
        base = desktop_join.start_relay()
        store = relay._running["relay"].store
        status = store.sync()
        self.assertTrue(status["has_copy"], status)
        self.assertIn(cage, [c["id"] for c in store.snapshot()["cages"]])
        lab.__exit__()                                                # the facility: no signal
        status_, headers, _ = ask(base, "GET", "/colony?view=cages", {"Accept": "text/html"})
        self.assertEqual(status_, 303)
        self.assertTrue(headers["Location"].startswith("/_offline/?from="))
        status_, _, page = ask(base, "GET", "/_offline/", {"Accept": "text/html"})
        self.assertIn('<script src="offline.js">', page)
        status_, _, words = ask(base, "GET", "/_offline/strings.json", {"Accept-Language": "zh-CN"})
        self.assertEqual(json.loads(words)["words"]["Offline"], "离线")
        op = {"op": "offline-1", "made_at": "2026-10-11T09:14:05Z", "action": "litter_born",
              "target": {"kind": "cage", "id": cage}, "fields": {"date": "2026-10-10"}}
        status_, _, body = ask(base, "POST", "/_relay/store/queue",
                               {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"},
                               json.dumps(op).encode())
        self.assertEqual(json.loads(body), {"ok": True, "waiting": 1})
        self.assertEqual(json.loads(ask(base, "GET", "/_relay/store")[2])["queue"][0]["op"], "offline-1")
        self.assertFalse(store.sync()["online"])                      # still out of reach: it waits
        self.assertEqual(len(store.queue()), 1)
        # Back in signal: the same address answers again.
        back = make_server("127.0.0.1", port, app, threaded=True)
        threading.Thread(target=back.serve_forever, daemon=True).start()
        self.addCleanup(back.shutdown)
        status = store.sync()
        self.assertTrue(status["online"], status)
        self.assertEqual(status["waiting"], 0)
        self.assertEqual(str(one("select date_give_birth from mouse_cages where id=?", cage))[:10], "2026-10-10")
        self.assertEqual([c["date_give_birth"] for c in store.snapshot()["cages"] if c["id"] == cage], ["2026-10-10"])

    def test_the_copy_is_unreadable_without_this_computer(self):
        from app import offline_store
        store = offline_store.Store("https://lab.example.org", lambda: "")
        store._write("snapshot", {"cages": [{"cage_id": "secret-cage"}]})
        self.assertNotIn("secret-cage", store._path("snapshot").read_text(encoding="utf-8"))
        self.assertEqual(store.snapshot()["cages"][0]["cage_id"], "secret-cage")
        store.forget()
        self.assertIsNone(store.snapshot())


class CountingServer:
    """A server that counts the connections it is opened with and answers
    each request with what it was asked ("GET /a" and the body). With
    close_after=True it closes a connection after each answer, as a server
    closes an idle one."""

    def __init__(self, close_after: bool = False, header_close: bool = False):
        import socketserver
        outer = self
        self.connections = 0
        self.seen: list[str] = []

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                outer.connections += 1
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    method, path, _ = line.decode().split(" ", 2)
                    length = 0
                    while (header := self.rfile.readline()) not in (b"\r\n", b"\n", b""):
                        name, _, value = header.decode().partition(":")
                        if name.strip().lower() == "content-length":
                            length = int(value)
                    body = self.rfile.read(length).decode() if length else ""
                    outer.seen.append(f"{method} {path} {body}".strip())
                    answer = f"{method} {path} {body}".strip().encode()
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                                     + (b"Connection: close\r\n" if header_close else b"")
                                     + f"Content-Length: {len(answer)}\r\n\r\n".encode() + answer)
                    self.wfile.flush()
                    if close_after or header_close:
                        return

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class KeptConnections(AppTestCase):
    """The relay keeps its connections to the server open and uses them
    again, rather than a new one (a TLS handshake) for every request."""

    def through(self, server):
        from werkzeug.test import Client
        from app.relay import Relay
        relay = Relay(server.url, lambda: "token")
        relay.base = "http://127.0.0.1:5999"
        self.addCleanup(relay.close)
        client = Client(relay)
        get = lambda path: client.get(path, base_url=relay.base).get_data(as_text=True)
        post = lambda path, body: client.post(path, base_url=relay.base, data=body,
                                              content_type="text/plain").get_data(as_text=True)
        return relay, get, post

    def test_one_connection_carries_request_after_request(self):
        with CountingServer() as server:
            _, get, post = self.through(server)
            self.assertEqual(get("/a"), "GET /a")
            self.assertEqual(post("/b", "x=1"), "POST /b x=1")
            self.assertEqual(get("/c"), "GET /c")
            self.assertEqual(server.connections, 1)

    def test_a_connection_the_server_closed_is_not_used_again(self):
        with CountingServer(close_after=True) as server:
            _, get, post = self.through(server)
            self.assertEqual(get("/a"), "GET /a")
            time.sleep(0.05)                       # the server's close reaches this side
            self.assertEqual(post("/b", "x=1"), "POST /b x=1")
            self.assertEqual(server.seen, ["GET /a", "POST /b x=1"])
            self.assertEqual(server.connections, 2)

    def test_a_request_that_finds_its_connection_gone_goes_again_once(self):
        # As if the server closed it the moment it was taken: it looked open.
        with CountingServer(close_after=True) as server, mock.patch("app.relay._closed", return_value=False):
            _, get, post = self.through(server)
            self.assertEqual(get("/a"), "GET /a")
            time.sleep(0.05)
            self.assertEqual(get("/b"), "GET /b")
            time.sleep(0.05)
            self.assertEqual(post("/c", "x=1"), "POST /c x=1")
            self.assertEqual(server.seen, ["GET /a", "GET /b", "POST /c x=1"])   # each once

    def test_an_answer_that_closes_its_connection_is_not_kept(self):
        with CountingServer(header_close=True) as server:
            relay, get, _ = self.through(server)
            self.assertEqual(get("/a"), "GET /a")
            self.assertEqual(relay._idle, [])
            self.assertEqual(get("/b"), "GET /b")
            self.assertEqual(server.connections, 2)

    def test_a_connection_idle_too_long_is_not_used_again(self):
        from app import relay as relay_module
        with CountingServer() as server:
            relay, get, _ = self.through(server)
            get("/a")
            with mock.patch.object(relay_module.time, "monotonic", return_value=time.monotonic() + relay_module.IDLE_REUSE + 1):
                get("/b")
            self.assertEqual(server.connections, 2)
