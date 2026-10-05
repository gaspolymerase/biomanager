"""Proposed changes (app/proposals.py): an assistant proposes through the
API, nothing changes, and its person approves (or discards) it, whole, on
Proposed changes."""
from __future__ import annotations

from tests.base import count, days_ago, execute, flashes, one, row, uniq
from tests.test_api import Base


def litter(cage: int, day: str) -> dict:
    return {"action": "litter_born", "target": {"kind": "cage", "id": cage}, "fields": {"date": day}}


class Proposing(Base):
    def propose(self, tok, *changes, **extra):
        return self.call("post", "/proposals", tok, json={"summary": "Litter in the breeder", "source": "Claude",
                                                         "changes": list(changes), **extra})

    def test_a_proposal_changes_nothing_and_tells_its_person(self):
        tok = self.token(self.m, scope="propose")
        cage = self.make_cage(self.m)
        notes = count("notifications", "recipient_username=?", self.member)
        r = self.propose(tok, litter(cage, days_ago(1)))
        self.assertEqual(r.status_code, 201, r.get_json())
        body = r.get_json()
        self.assertEqual(body["status"], "pending")
        self.assertIn("Litter born in cage", body["changes"][0]["summary"])
        self.assertIn(f"/proposals#proposal-{body['id']}", body["review_url"])
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))
        self.assertEqual(count("notifications", "recipient_username=?", self.member), notes + 1)
        full = self.call("get", f"/proposals/{body['id']}", tok).get_json()
        self.assertTrue(full["changes"][0]["records"])
        self.assertEqual(full["changes"][0]["request"], litter(cage, days_ago(1)))

    def test_a_propose_token_never_changes_anything_itself(self):
        tok = self.token(self.m, scope="propose")
        mouse = self.make_mouse(self.m, self.member, cage=uniq("C"))
        r = self.call("patch", f"/mice/{self.mouse_id(mouse)}", tok, json={"status": "sac"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("propose", r.get_json()["error"])
        # No token approves: there is nothing to call.
        pid = self.call("post", "/proposals", tok, json={"changes": [litter(self.make_cage(self.m), days_ago(1))]}
                        ).get_json()["id"]
        for scope in ("propose", "write"):
            r = self.call("post", f"/proposals/{pid}/approve", self.token(self.m, scope=scope))
            self.assertIn(r.status_code, (403, 404, 405))
        self.assertEqual(one("select status from proposals where id=?", pid), "pending")

    def test_connect_an_assistant_gives_the_setup_to_copy(self):
        self.assertIn("Connect an AI assistant", self.get_ok(self.m, "/settings"))
        r = self.m.post("/settings/api-tokens", data={"label": "AI assistant", "scope": "propose", "expires": "365"})
        page = r.get_data(as_text=True)
        self.assertIn('"mcpServers"', page)
        self.assertIn('"BIOMANAGER_URL": "http://localhost"', page)
        self.assertIn("claude mcp add biomanager", page)
        self.assertEqual(one("select scope from api_tokens order by id desc limit 1"), "propose")

    def test_a_read_token_cannot_propose(self):
        tok = self.token(self.m, scope="read")
        r = self.propose(tok, litter(self.make_cage(self.m), days_ago(1)))
        self.assertEqual(r.status_code, 403)

    def test_a_proposal_that_wont_go_through_says_why(self):
        tok = self.token(self.m, scope="propose")
        cage = self.make_cage(self.m)
        notes = count("notifications", "recipient_username=?", self.member)
        body = self.propose(tok, {"action": "litter_genotyping", "target": {"kind": "cage", "id": cage},
                                  "fields": {"pups": 0}}).get_json()
        self.assertEqual(body["status"], "invalid")
        self.assertIn("Pups must be a whole number", body["errors"][0])
        self.assertEqual(count("notifications", "recipient_username=?", self.member), notes)

    def test_someone_elses_records_are_refused_in_the_preview(self):
        tok = self.token(self.o, scope="propose")
        body = self.propose(tok, litter(self.make_cage(self.m), days_ago(1))).get_json()
        self.assertEqual(body["status"], "invalid")

    def test_a_corrected_proposal_replaces_the_first(self):
        tok = self.token(self.m, scope="propose")
        cage = self.make_cage(self.m)
        first = self.propose(tok, litter(cage, days_ago(1))).get_json()["id"]
        second = self.propose(tok, litter(cage, days_ago(2)), replaces=first).get_json()["id"]
        self.assertEqual(one("select status from proposals where id=?", first), "superseded")
        self.assertEqual(one("select status from proposals where id=?", second), "pending")
        listed = self.call("get", "/proposals?status=pending", tok).get_json()["data"]
        self.assertIn(second, [p["id"] for p in listed])
        self.assertNotIn(first, [p["id"] for p in listed])

    def test_discarded_through_the_api(self):
        tok = self.token(self.m, scope="propose")
        pid = self.propose(tok, litter(self.make_cage(self.m), days_ago(1))).get_json()["id"]
        r = self.call("post", f"/proposals/{pid}/discard", tok)
        self.assertEqual(r.get_json()["status"], "discarded")
        self.assertEqual(self.call("post", f"/proposals/{pid}/discard", tok).status_code, 409)
        other = self.token(self.o, scope="propose")
        self.assertEqual(self.call("get", f"/proposals/{pid}", other).status_code, 404)


class Approving(Base):
    def proposal(self, *changes, client=None) -> int:
        tok = self.token(client or self.m, scope="propose")
        r = self.call("post", "/proposals", tok, json={"summary": "From my notes", "source": "Claude",
                                                       "changes": list(changes)})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["id"]

    def test_approve_applies_everything_as_one_batch_that_undoes_as_one(self):
        cage = self.make_cage(self.m)
        mouse = self.make_mouse(self.m, self.member)
        pid = self.proposal(litter(cage, days_ago(3)),
                            {"action": "mice_move", "target": {"kind": "cage", "id": cage},
                             "fields": {"mice": [self.mouse_id(mouse)]}})
        page = self.get_ok(self.m, "/proposals")
        self.assertIn("From my notes", page)
        self.assertIn(f"/proposals/{pid}/approve", page)
        r = self.post(self.m, f"/proposals/{pid}/approve")
        self.assertIn(("success", f"Approved proposal #{pid}: it is in Batch history, where it can be undone as one."),
                      flashes(r))
        self.assertEqual(str(one("select date_give_birth from mouse_cages where id=?", cage))[:10], days_ago(3))
        self.assertEqual(one("select cage_id_fk from mice where id=?", mouse), cage)
        status, batch = row("select status, batch_id_fk from proposals where id=?", pid)
        self.assertEqual(status, "approved")
        self.assertEqual(one("select description from batches where id=?", batch),
                         f"Proposal #{pid}: From my notes (via Claude)")
        self.post(self.m, f"/batches/{batch}/undo")
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))
        self.assertNotEqual(one("select cage_id_fk from mice where id=?", mouse), cage)

    def test_only_its_person_approves_and_only_once(self):
        pid = self.proposal(litter(self.make_cage(self.m), days_ago(1)))
        r = self.post(self.o, f"/proposals/{pid}/approve")
        self.assertIn("no proposal", " ".join(t for _c, t in flashes(r)))
        self.assertEqual(one("select status from proposals where id=?", pid), "pending")
        self.post(self.m, f"/proposals/{pid}/approve")
        r = self.post(self.m, f"/proposals/{pid}/approve")
        self.assertIn("can't be approved", " ".join(t for _c, t in flashes(r)))

    def test_a_record_changed_after_the_preview_stops_it(self):
        cage = self.make_cage(self.m)
        pid = self.proposal(litter(cage, days_ago(1)))
        self.post(self.m, f"/colony/cages/{cage}/update", {"notes": "changed meanwhile"})
        r = self.post(self.m, f"/proposals/{pid}/approve")
        errors = [t for c, t in flashes(r) if c == "error"]
        self.assertTrue(errors and "changed after this proposal was made" in errors[0], flashes(r))
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))
        self.assertEqual(one("select status from proposals where id=?", pid), "pending")

    def test_one_failing_change_applies_nothing(self):
        cage, other = self.make_cage(self.m), self.make_cage(self.m)
        pid = self.proposal(litter(cage, days_ago(1)), litter(other, days_ago(1)))
        # The second cage is no longer the member's to change.
        execute("update mouse_cages set owner=? where id=?", self.other, other)
        r = self.post(self.m, f"/proposals/{pid}/approve")
        self.assertTrue([t for c, t in flashes(r) if c == "error"])
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))
        self.assertEqual(count("batches", "description like ?", f"Proposal #{pid}:%"), 0)

    def test_a_second_approve_finds_it_taken(self):
        pid = self.proposal(litter(self.make_cage(self.m), days_ago(1)))
        execute("update proposals set status='applying' where id=?", pid)
        r = self.post(self.m, f"/proposals/{pid}/approve")
        self.assertIn("being approved already", " ".join(t for _c, t in flashes(r)))
        self.assertEqual(count("batches", "description like ?", f"Proposal #{pid}:%"), 0)

    def test_expired_proposals_cannot_be_approved(self):
        pid = self.proposal(litter(self.make_cage(self.m), days_ago(1)))
        execute("update proposals set expires_at=? where id=?", "2000-01-01 00:00:00", pid)
        r = self.post(self.m, f"/proposals/{pid}/approve")
        self.assertIn("expired", " ".join(t for _c, t in flashes(r)))

    def test_discard_on_the_page_and_the_menu_entry(self):
        pid = self.proposal(litter(self.make_cage(self.m), days_ago(1)))
        self.assertIn("Proposed changes", self.get_ok(self.m, "/home"))
        self.post(self.m, f"/proposals/{pid}/discard")
        self.assertEqual(one("select status from proposals where id=?", pid), "discarded")
        self.assertNotIn(f"/proposals/{pid}/approve", self.get_ok(self.m, "/proposals"))
        self.assertIn("Nothing is waiting for you.", self.get_ok(self.a, "/proposals"))
