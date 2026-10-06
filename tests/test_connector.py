"""The remote connector: /api/v1/mcp (app/mcp_http.py) and signing an
assistant app in with OAuth (app/oauth.py), as claude.ai and ChatGPT do."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from urllib.parse import parse_qs, urlparse

from tests.base import client_for, days_ago, execute, make_user, one, uniq
from tests.test_api import Base

CLAUDE = "https://claude.ai/api/mcp/auth_callback"
INTERNET = {"X-BioManager-Entry": "internet"}


def pkce():
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


class Endpoint(Base):
    def rpc(self, token, method, params=None, msg_id=1, **kw):
        body = {"jsonrpc": "2.0", "method": method, **({"id": msg_id} if msg_id is not None else {}),
                **({"params": params} if params is not None else {})}
        return self.bare.post("/api/v1/mcp", json=body, headers={"Authorization": f"Bearer {token}"}, **kw)

    def test_without_a_token_it_says_where_to_sign_in(self):
        r = self.bare.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["WWW-Authenticate"],
                         'Bearer resource_metadata="http://localhost/.well-known/oauth-protected-resource/api/v1/mcp"')
        meta = self.bare.get("/.well-known/oauth-protected-resource/api/v1/mcp").get_json()
        self.assertEqual(meta["resource"], "http://localhost/api/v1/mcp")
        self.assertEqual(meta["authorization_servers"], ["http://localhost"])
        server = self.bare.get("/.well-known/oauth-authorization-server").get_json()
        self.assertEqual(server["issuer"], "http://localhost")
        self.assertEqual(server["code_challenge_methods_supported"], ["S256"])
        self.assertTrue(server["authorization_response_iss_parameter_supported"])
        self.assertEqual(server["registration_endpoint"], "http://localhost/oauth/register")

    def test_initialize_tools_and_prompts(self):
        tok = self.token(self.m, scope="propose", label="Claude")
        init = self.rpc(tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "test", "version": "1"}}).get_json()["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["serverInfo"]["name"], "BioManager")
        self.assertIn("resolve", init["instructions"])
        newer = self.rpc(tok, "initialize", {"protocolVersion": "2099-01-01"}).get_json()["result"]
        self.assertEqual(newer["protocolVersion"], "2025-11-25")
        self.assertEqual(self.rpc(tok, "notifications/initialized", msg_id=None).status_code, 202)
        tools = {t["name"]: t for t in self.rpc(tok, "tools/list").get_json()["result"]["tools"]}
        self.assertEqual(set(tools), {"lab_overview", "resolve", "get", "list", "whats_due", "list_actions",
                                      "propose_changes", "proposal_status", "discard_proposal"})
        self.assertIn("litter_born", tools["propose_changes"]["description"])     # the lab's own actions
        self.assertTrue(tools["resolve"]["annotations"]["readOnlyHint"])
        self.assertEqual(tools["resolve"]["inputSchema"]["required"], ["q"])
        prompts = self.rpc(tok, "prompts/list").get_json()["result"]["prompts"]
        self.assertIn("log_today", [p["name"] for p in prompts])
        got = self.rpc(tok, "prompts/get", {"name": "log_today", "arguments": {"notes": "weaned 102"}}).get_json()
        self.assertIn("weaned 102", got["result"]["messages"][0]["content"]["text"])
        self.assertEqual(self.rpc(tok, "nonsense/method").get_json()["error"]["code"], -32601)
        self.assertEqual(self.rpc(tok, "tools/call", {"name": "rm_rf"}).get_json()["error"]["code"], -32602)
        bad = self.bare.post("/api/v1/mcp", data="{not json", content_type="application/json",
                             headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.bare.get("/api/v1/mcp", headers={"Authorization": f"Bearer {tok}"}).status_code, 405)

    def test_a_proposal_through_the_endpoint_is_signed_with_the_connection(self):
        tok = self.token(self.m, scope="propose", label="Claude")
        code = uniq("C")
        cage = self.make_cage(self.m, code)
        found = self.rpc(tok, "tools/call", {"name": "resolve", "arguments": {"q": f"cage {code}"}}).get_json()
        ref = found["result"]["structuredContent"]["matches"][0]["ref"]
        self.assertEqual(ref, {"kind": "cage", "id": cage})
        out = self.rpc(tok, "tools/call", {"name": "propose_changes", "arguments": {
            "summary": "litter", "changes": [{"action": "litter_born", "target": ref,
                                              "fields": {"date": days_ago(1)}}]}}).get_json()["result"]
        self.assertFalse(out["isError"])
        proposal = out["structuredContent"]
        self.assertEqual(proposal["status"], "pending")
        self.assertTrue(proposal["review_url"].startswith("http://localhost/proposals#"))
        self.assertEqual(one("select source from proposals where id=?", proposal["id"]), "Claude")
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))
        # A tool's refusal comes back as its result, for the assistant to read.
        err = self.rpc(tok, "tools/call", {"name": "get", "arguments": {"path": "../settings"}}).get_json()["result"]
        self.assertTrue(err["isError"])

    def test_a_read_token_reads_but_cannot_propose(self):
        tok = self.token(self.m, scope="read")
        self.assertEqual(self.rpc(tok, "tools/list").status_code, 200)
        out = self.rpc(tok, "tools/call", {"name": "propose_changes", "arguments": {
            "summary": "x", "changes": [{"action": "cage_new", "fields": {}}]}}).get_json()["result"]
        self.assertTrue(out["isError"])


class SigningIn(Base):
    def register(self, uris=(CLAUDE,), **extra):
        r = self.bare.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": list(uris), **extra})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()

    def authorize_params(self, client_id, challenge, redirect=CLAUDE, **extra):
        return {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": "s123",
                "code_challenge": challenge, "code_challenge_method": "S256",
                "resource": "http://localhost/api/v1/mcp", "scope": "propose", **extra}

    def allow(self, client, params, **form):
        r = client.post("/oauth/authorize", data={**params, "decision": "allow", **form})
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:400])
        return r.headers["Location"]

    def exchange(self, client_id, code, verifier, redirect=CLAUDE):
        return self.bare.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                   "redirect_uri": redirect, "client_id": client_id,
                                                   "code_verifier": verifier})

    def connect(self):
        client = self.register()
        verifier, challenge = pkce()
        params = self.authorize_params(client["client_id"], challenge)
        page = self.m.get("/oauth/authorize", query_string=params)
        self.assertEqual(page.status_code, 200)
        self.assertTrue("Claude wants to connect" in page.get_data(as_text=True))
        self.assertTrue("claude.ai" in page.get_data(as_text=True))
        back = urlparse(self.allow(self.m, params))
        q = parse_qs(back.query)
        self.assertEqual(f"{back.scheme}://{back.netloc}{back.path}", CLAUDE)
        self.assertEqual((q["state"], q["iss"]), (["s123"], ["http://localhost"]))
        r = self.exchange(client["client_id"], q["code"][0], verifier)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        return client, q["code"][0], verifier, r.get_json()

    def test_sign_in_use_refresh_and_end_the_connection(self):
        client, _code, _v, tokens = self.connect()
        self.assertEqual((tokens["token_type"], tokens["scope"], tokens["expires_in"]), ("Bearer", "propose", 3600))
        me = self.bare.get("/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}).get_json()
        self.assertEqual(me["username"], self.member)
        r = self.bare.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                           headers={"Authorization": f"Bearer {tokens['access_token']}"})
        self.assertEqual(r.status_code, 200)
        mouse = self.make_mouse(self.m, self.member, cage=uniq("C"))
        patch = self.bare.patch(f"/api/v1/mice/{self.mouse_id(mouse)}", json={"status": "sac"},
                                headers={"Authorization": f"Bearer {tokens['access_token']}"})
        self.assertEqual(patch.status_code, 403)                        # it proposes; it never changes
        # Refreshing changes both tokens; the old ones stop working.
        rows = one("select count(*) from api_tokens")
        r = self.bare.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client["client_id"],
                                                "refresh_token": tokens["refresh_token"]})
        fresh = r.get_json()
        self.assertEqual(one("select count(*) from api_tokens"), rows)   # one row per connection, renewed
        self.assertNotEqual(fresh["access_token"], tokens["access_token"])
        self.assertEqual(self.bare.get("/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
                                       ).status_code, 401)
        again = self.bare.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client["client_id"],
                                                    "refresh_token": tokens["refresh_token"]})
        self.assertEqual(again.get_json()["error"], "invalid_grant")
        # Revoked in Settings: the connection is over.
        token_id = one("select api_token_id_fk from oauth_grants order by id desc limit 1")
        self.m.post(f"/settings/api-tokens/{token_id}/revoke")
        ended = self.bare.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client["client_id"],
                                                    "refresh_token": fresh["refresh_token"]})
        self.assertEqual(ended.get_json()["error"], "invalid_grant")

    def test_a_code_works_once_and_only_with_its_verifier(self):
        client = self.register()
        verifier, challenge = pkce()
        params = self.authorize_params(client["client_id"], challenge)
        code = parse_qs(urlparse(self.allow(self.m, params)).query)["code"][0]
        self.assertEqual(self.exchange(client["client_id"], code, pkce()[0]).get_json()["error"], "invalid_grant")
        # The failed try used it up.
        self.assertEqual(self.exchange(client["client_id"], code, verifier).get_json()["error"], "invalid_grant")
        code = parse_qs(urlparse(self.allow(self.m, params)).query)["code"][0]
        self.assertEqual(self.exchange(client["client_id"], code, verifier).status_code, 200)
        self.assertEqual(self.exchange(client["client_id"], code, verifier).get_json()["error"], "invalid_grant")

    def test_refusals(self):
        client = self.register()
        _v, challenge = pkce()
        # A redirect it didn't register: no redirect at all, a page says so.
        r = self.m.get("/oauth/authorize", query_string=self.authorize_params(client["client_id"], challenge,
                                                                               redirect="https://evil.example/cb"))
        self.assertEqual(r.status_code, 400)
        # No PKCE: back to the app with the error and iss.
        r = self.m.get("/oauth/authorize", query_string=self.authorize_params(client["client_id"], ""))
        q = parse_qs(urlparse(r.headers["Location"]).query)
        self.assertEqual((q["error"], q["iss"]), (["invalid_request"], ["http://localhost"]))
        # Saying no.
        r = self.m.post("/oauth/authorize", data={**self.authorize_params(client["client_id"], challenge),
                                                  "decision": "deny"})
        self.assertEqual(parse_qs(urlparse(r.headers["Location"]).query)["error"], ["access_denied"])
        # Registration wants https or loopback redirects.
        bad = self.bare.post("/oauth/register", json={"client_name": "x", "redirect_uris": ["http://evil.example/cb"]})
        self.assertEqual(bad.status_code, 400)
        # Unknown client at the token endpoint.
        r = self.bare.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": "bmc_nope",
                                                "code": "x", "code_verifier": "y"})
        self.assertEqual(r.status_code, 401)
        # Not signed in, on the lab's network: sign in first, then come back.
        r = self.bare.get("/oauth/authorize", query_string=self.authorize_params(client["client_id"], challenge))
        self.assertTrue(r.headers["Location"].startswith("/login?next=/oauth/authorize"))

    def test_claude_code_on_any_loopback_port(self):
        client = self.register(uris=["http://localhost/callback", "http://127.0.0.1/callback"])
        verifier, challenge = pkce()
        params = self.authorize_params(client["client_id"], challenge, redirect="http://localhost:43117/callback")
        page = self.m.get("/oauth/authorize", query_string=params).get_data(as_text=True)
        self.assertTrue("a program on your own computer" in page)
        code = parse_qs(urlparse(self.allow(self.m, params)).query)["code"][0]
        self.assertEqual(self.exchange(client["client_id"], code, verifier,
                                       redirect="http://localhost:43117/callback").status_code, 200)

    def test_a_confidential_client_needs_its_secret(self):
        client = self.register(token_endpoint_auth_method="client_secret_post")
        verifier, challenge = pkce()
        params = self.authorize_params(client["client_id"], challenge)
        code = parse_qs(urlparse(self.allow(self.m, params)).query)["code"][0]
        self.assertEqual(self.exchange(client["client_id"], code, verifier).status_code, 401)
        code = parse_qs(urlparse(self.allow(self.m, params)).query)["code"][0]
        r = self.bare.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                "redirect_uri": CLAUDE, "code_verifier": verifier},
                           headers={"Authorization": "Basic " + base64.b64encode(
                               f"{client['client_id']}:{client['client_secret']}".encode()).decode()})
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_unused_clients_go_but_not_while_a_connection_can_be_renewed(self):
        connected, _code, _v, tokens = self.connect()
        idle = self.register()
        long_ago = days_ago(40)
        for c in (connected, idle):
            execute("update oauth_clients set created_at = ?, last_used_at = ? where client_id = ?",
                    long_ago, long_ago, c["client_id"])
        self.register()                                                 # registering tidies up
        self.assertEqual(one("select count(*) from oauth_clients where client_id = ?", idle["client_id"]), 0)
        r = self.bare.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": connected["client_id"],
                                                "refresh_token": tokens["refresh_token"]})
        self.assertEqual(r.status_code, 200, r.get_json())


class FromTheInternet(SigningIn):
    """claude.ai's servers and the person's browser come in through the
    lab's internet entrance, where no sign-in form is ever shown."""

    def test_metadata_registration_and_the_endpoint_get_past_the_gate(self):
        self.assertEqual(self.bare.get("/.well-known/oauth-authorization-server", headers=INTERNET).status_code, 200)
        r = self.bare.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CLAUDE]}, headers=INTERNET)
        self.assertEqual(r.status_code, 201)
        r = self.bare.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}, headers=INTERNET)
        self.assertEqual(r.status_code, 401)                            # not the guest page
        self.assertTrue(self.bare.get("/home", headers=INTERNET).headers["Location"].endswith("/guest"))

    def setUp(self):
        super().setUp()
        os.environ["BIOMANAGER_PUBLIC_URL"] = "http://localhost"

    def tearDown(self):
        os.environ.pop("BIOMANAGER_PUBLIC_URL", None)
        super().tearDown()

    def test_saying_yes_with_a_connection_code(self):
        client = self.register()
        verifier, challenge = pkce()
        params = self.authorize_params(client["client_id"], challenge)
        page = self.bare.get("/oauth/authorize", query_string=params, headers=INTERNET)
        self.assertEqual(page.status_code, 200)
        self.assertTrue('name="link_code"' in page.get_data(as_text=True))
        wrong = self.bare.post("/oauth/authorize", data={**params, "decision": "allow", "link_code": "AAAA-BBBB-CCCC"},
                               headers=INTERNET)
        self.assertEqual(wrong.status_code, 400)
        made = self.m.post("/settings/assistant/code").get_data(as_text=True)
        code = re.search(r'id="link-code">([A-Z0-9-]+)<', made).group(1)
        r = self.bare.post("/oauth/authorize", data={**params, "decision": "allow", "link_code": code.lower()},
                           headers=INTERNET)
        self.assertEqual(r.status_code, 302)
        auth_code = parse_qs(urlparse(r.headers["Location"]).query)["code"][0]
        tokens = self.exchange(client["client_id"], auth_code, verifier).get_json()
        me = self.bare.get("/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}", **INTERNET})
        self.assertEqual(me.get_json()["username"], self.member)
        # Used once.
        again = self.bare.post("/oauth/authorize", data={**params, "decision": "allow", "link_code": code},
                               headers=INTERNET)
        self.assertEqual(again.status_code, 400)


class ConnectPage(Base):
    def test_without_internet_access_it_says_so_and_offers_the_rest(self):
        page = self.get_ok(self.m, "/settings/assistant")
        self.assertTrue("isn&#39;t on the internet" in page or "isn't on the internet" in page)
        self.assertTrue('name="scope" value="propose"' in page)

    def test_on_the_internet_it_gives_the_address_and_a_code(self):
        os.environ["BIOMANAGER_PUBLIC_URL"] = "https://lab.example.org:8443"
        try:
            page = self.get_ok(self.m, "/settings/assistant")
            self.assertTrue("https://lab.example.org:8443/api/v1/mcp" in page)
            made = self.m.post("/settings/assistant/code").get_data(as_text=True)
            self.assertTrue(re.search(r'id="link-code">[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}<', made))
        finally:
            del os.environ["BIOMANAGER_PUBLIC_URL"]

    def test_a_coding_assistant_sets_itself_up_or_signs_in_without_a_token(self):
        page = self.get_ok(self.m, "/settings/assistant")
        command = "claude mcp add --transport http --scope user biomanager http://localhost/api/v1/mcp"
        self.assertTrue('id="agent-message"' in page)
        self.assertTrue("an MCP server at http://localhost/api/v1/mcp" in page)
        self.assertTrue(command in page)                                 # in the message, and on its own
        self.assertEqual(page.count(command), 2)
        self.assertTrue("lab_overview" in page)

    def test_it_is_found_from_help_and_from_an_empty_proposed_changes(self):
        for path in ("/settings", "/proposals"):
            page = self.get_ok(self.m, path)
            self.assertTrue('href="/settings/assistant"' in page, path)

    def test_a_guest_is_not_offered_it(self):
        guest = make_user(uniq("guest-visitor"))
        execute("update users set expires_at=? where username=?", "2099-01-01 00:00:00", guest)
        page = self.get_ok(client_for(guest), "/settings")
        self.assertFalse('href="/settings/assistant"' in page)
