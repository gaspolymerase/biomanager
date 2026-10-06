"""mcp/biomanager_mcp.py: the assistant's tools, over the real API (the MCP
SDK itself isn't needed: build_server only wraps these functions)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from tests.base import days_ago, one, uniq
from tests.test_api import Base

from app.app import app

_spec = importlib.util.spec_from_file_location(
    "biomanager_mcp", Path(__file__).resolve().parent.parent / "mcp" / "biomanager_mcp.py")
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)


class TestApi(bm.Api):
    """The same calls, sent to the test app instead of over HTTP."""

    def __init__(self, token: str):
        super().__init__("http://localhost", token)
        self.client = app.test_client()

    def request(self, method, path, query=None, body=None):
        r = self.client.open(f"/api/v1/{path.lstrip('/')}", method=method, query_string=query or {}, json=body,
                             headers={"Authorization": f"Bearer {self.token}"})
        data = r.get_json()
        if r.status_code >= 400:
            raise bm.ApiError((data or {}).get("error") or f"BioManager answered {r.status_code}.")
        return data


class Tools(Base):
    def test_from_overview_to_a_proposal(self):
        api = TestApi(self.token(self.m, scope="propose"))
        code = uniq("88")
        cage = self.make_cage(self.m, code)
        overview = bm.lab_overview(api)
        self.assertEqual(overview["you"]["username"], self.member)
        self.assertEqual(overview["lab"]["today"], days_ago(0))
        [hit] = bm.resolve(api, f"cage {code}")
        self.assertEqual(hit["ref"], {"kind": "cage", "id": cage})
        self.assertIn("litter_born", bm.describe_actions(bm.list_actions(api)))
        out = bm.propose_changes(api, "Litter yesterday", [
            {"action": "litter_born", "target": hit["ref"], "fields": {"date": days_ago(1)}}], source="Claude")
        self.assertEqual(out["status"], "pending")
        self.assertIn("review_url", out)
        self.assertIn("Nothing has changed yet", out["next"])
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))
        self.assertEqual(bm.proposal_status(api, out["id"])["status"], "pending")
        self.assertEqual(bm.discard_proposal(api, out["id"])["status"], "discarded")

    def test_reads_stay_within_the_api(self):
        api = TestApi(self.token(self.m, scope="read"))
        mouse = self.make_mouse(self.m, self.member, cage=uniq("C"))
        number = one("select mouse_id from mice where id=?", mouse)
        self.assertEqual(bm.get(api, f"mice/{number}")["mouse_id"], number)
        self.assertIn("data", bm.list_records(api, "mice", {"alive": "true"}, limit=5))
        for bad in ("../settings", "settings/api-tokens", "", "proposals/../../me"):
            with self.assertRaises(bm.ApiError):
                bm.get(api, bad)
        self.assertIsInstance(bm.whats_due(api, 3), list)

    def test_a_read_token_is_told_it_cannot_propose(self):
        api = TestApi(self.token(self.m, scope="read"))
        with self.assertRaises(bm.ApiError) as caught:
            bm.propose_changes(api, "x", [{"action": "cage_new", "fields": {}}])
        self.assertIn("only read", str(caught.exception))
