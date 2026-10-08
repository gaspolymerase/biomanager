"""The notebook's own features (app/lab_notebook.py): sharing and who may
see or change a page, live-editing sync, version history, comments with
@mentions, tags and search, the daily log, protocols and the experiments
started from them, meeting rotations and action items, recipes, and
Markdown import and export."""
from __future__ import annotations

import base64
import io
import json
from datetime import date, timedelta

from tests.base import AppTestCase, TODAY, client_for, count, make_user, one, only_sqlite, rows, uniq  # first: points the app at a test database
from app import lab_notebook  # noqa: E402


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


class Notebook(AppTestCase):
    def post_json(self, client, url, body=None):
        return client.post(url, data=json.dumps(body or {}), content_type="application/json")

    def new_page(self, client, **body):
        r = self.post_json(client, "/notebook/api/pages/new", body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["page_id"]

    def save(self, client, page_id, headers=None, **fields):
        return client.post(f"/notebook/pages/{page_id}/update", data=fields, headers=headers or {})

    def share(self, owner_client, page_id, username, role="view"):
        r = self.post_json(owner_client, f"/notebook/api/pages/{page_id}/shares", {"username": username, "role": role})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))


class SharingTests(Notebook):
    def test_a_page_is_its_owners_until_shared(self):
        page = self.new_page(self.m, title=uniq("private"))
        other = client_for(make_user())
        self.assertEqual(other.get(f"/notebook/api/pages/{page}").status_code, 404)
        self.assertEqual(self.save(other, page, body="mine now").status_code, 404)
        self.assertNotIn("mine now", one("select body from notebook_pages where id=?", page) or "")

    def test_a_viewer_reads_and_comments_but_cannot_edit(self):
        viewer = make_user()
        page = self.new_page(self.m, title=uniq("shared"))
        self.share(self.m, page, viewer, "view")
        v = client_for(viewer)
        self.assertEqual(v.get(f"/notebook/api/pages/{page}").get_json()["page"]["role"], "view")
        self.assertEqual(self.save(v, page, body="changed").status_code, 403)
        r = self.post_json(v, f"/notebook/api/pages/{page}/comments", {"body": "Looks good"})
        self.assertEqual(r.status_code, 200)
        # Only the owner changes who it is shared with.
        r = self.post_json(v, f"/notebook/api/pages/{page}/shares", {"username": viewer, "role": "edit"})
        self.assertEqual(r.status_code, 403)

    def test_an_editor_saves_and_the_page_lists_under_shared_with_me(self):
        editor = make_user()
        title = uniq("together")
        page = self.new_page(self.m, title=title)
        self.share(self.m, page, editor, "edit")
        e = client_for(editor)
        self.assertEqual(self.save(e, page, body="Added by a lab mate").status_code, 200)
        self.assertEqual(one("select body from notebook_pages where id=?", page), "Added by a lab mate")
        html = e.get("/notebook").get_data(as_text=True)
        self.assertIn("Shared with me", html)
        self.assertIn(title, html)
        # They were told.
        self.assertEqual(count("notifications", "recipient_username=? and link like ?", editor, f"%page={page}%"), 1)

    def test_sharing_with_the_whole_lab_leaves_guests_out(self):
        page = self.new_page(self.m, title=uniq("lab-wide"))
        self.share(self.m, page, "*", "view")
        colleague = client_for(make_user())
        self.assertEqual(colleague.get(f"/notebook/api/pages/{page}").status_code, 200)
        guest = make_user()
        from app.db import SessionLocal
        from app.models import UserAccount
        from datetime import datetime
        with SessionLocal() as s:
            u = s.query(UserAccount).filter_by(username=guest).one()
            u.expires_at = datetime.utcnow() + timedelta(days=1)
            s.commit()
        self.assertEqual(client_for(guest).get(f"/notebook/api/pages/{page}").status_code, 404)

    def test_the_notebook_opens_a_shared_page_by_its_id(self):
        viewer = make_user()
        title = uniq("open me")
        page = self.new_page(self.m, title=title)
        self.share(self.m, page, viewer)
        html = client_for(viewer).get(f"/notebook?page={page}").get_data(as_text=True)
        self.assertIn(f'data-page-id="{page}"', html)
        self.assertIn("view only", html)

    def test_global_search_finds_shared_pages(self):
        viewer = make_user()
        word = uniq("zebrablot")
        page = self.new_page(self.m, title=word)
        self.share(self.m, page, viewer)
        r = client_for(viewer).get(f"/search?q={word}")
        self.assertIn(word, r.get_data(as_text=True))

    def test_deleting_a_page_takes_its_history_comments_and_shares(self):
        page = self.new_page(self.m, title=uniq("gone"))
        self.save(self.m, page, body="v1")
        self.share(self.m, page, make_user())
        self.post_json(self.m, f"/notebook/api/pages/{page}/comments", {"body": "note"})
        r = self.m.post(f"/notebook/pages/{page}/delete", headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"])
        for table in ("notebook_versions", "notebook_shares", "notebook_comments", "notebook_page_info"):
            self.assertEqual(count(table, "page_id_fk=?", page), 0, table)


def state_vector(clocks: dict) -> str:
    """A Yjs state vector for {client: clock}, as the editor sends it."""
    def varuint(n):
        out = bytearray()
        while True:
            byte, n = n & 0x7F, n >> 7
            out.append(byte | (0x80 if n else 0))
            if not n:
                return bytes(out)
    return b64(varuint(len(clocks)) + b"".join(varuint(c) + varuint(k) for c, k in clocks.items()))


class SyncTests(Notebook):
    def test_a_tab_that_fell_behind_does_not_save_an_older_text(self):
        page = self.new_page(self.m, title=uniq("race"))
        gen = self.m.get(f"/notebook/api/pages/{page}/sync?since=0&gen=-1&client=a").get_json()["gen"]
        live = {"X-Collab-Gen": str(gen), "X-Autosave": "1"}
        # The busy tab saves the newest text: its own edits (client 1) and another's (client 2).
        self.save(self.m, page, headers=live, body="- [ ] one\n- [ ] two\n- [ ] three",
                  collab_state=state_vector({1: 30, 2: 4}))
        # A background tab that has seen less saves late: kept out.
        r = self.save(self.m, page, headers=live, body="- [ ] one", collab_state=state_vector({1: 12, 2: 4}))
        self.assertTrue(r.get_json()["behind"])
        self.assertIn("three", one("select body from notebook_pages where id=?", page))
        # One that holds something the saved text lacks is saved.
        r = self.save(self.m, page, headers=live, body="- [ ] one\n- [ ] two\n- [ ] three\n- [ ] four",
                      collab_state=state_vector({1: 30, 2: 4, 7: 2}))
        self.assertNotIn("behind", r.get_json())
        self.assertIn("four", one("select body from notebook_pages where id=?", page))
        # An unreadable state is ignored rather than refused.
        from app import lab_notebook
        self.assertFalse(lab_notebook.behind(state_vector({1: 5}), "not base64!"))
        self.assertTrue(lab_notebook.behind(state_vector({1: 5, 2: 1}), state_vector({1: 5})))
        self.assertFalse(lab_notebook.behind(state_vector({1: 5}), state_vector({1: 5})))

    def test_editors_exchange_updates_and_the_first_state_is_seeded_once(self):
        editor = make_user()
        page = self.new_page(self.m, title=uniq("live"))
        self.share(self.m, page, editor, "edit")
        e = client_for(editor)
        first = self.m.get(f"/notebook/api/pages/{page}/sync?since=0&gen=-1&client=a").get_json()
        self.assertTrue(first["empty"])
        gen = first["gen"]
        r = self.post_json(self.m, f"/notebook/api/pages/{page}/sync", {"client": "a", "gen": gen, "init": True, "updates": [b64(b"state")]})
        self.assertTrue(r.get_json()["ok"])
        # A second editor seeding at the same moment is turned away.
        r = self.post_json(e, f"/notebook/api/pages/{page}/sync", {"client": "b", "gen": gen, "init": True, "updates": [b64(b"other")]})
        self.assertEqual(r.status_code, 409)
        got = e.get(f"/notebook/api/pages/{page}/sync?since=0&gen=-1&client=b").get_json()
        self.assertEqual([u["data"] for u in got["updates"]], [b64(b"state")])
        self.post_json(e, f"/notebook/api/pages/{page}/sync", {"client": "b", "gen": gen, "updates": [b64(b"typed")], "awareness": b64(b"cursor")})
        mine = self.m.get(f"/notebook/api/pages/{page}/sync?since={got['last']}&gen={gen}&client=a").get_json()
        self.assertEqual([u["data"] for u in mine["updates"]], [b64(b"typed")])
        self.assertEqual([p["username"] for p in mine["peers"]], [editor])

    def test_viewers_cannot_send_changes(self):
        viewer = make_user()
        page = self.new_page(self.m, title=uniq("look"))
        self.share(self.m, page, viewer)
        r = self.post_json(client_for(viewer), f"/notebook/api/pages/{page}/sync", {"client": "v", "gen": 0, "updates": [b64(b"x")]})
        self.assertEqual(r.status_code, 403)

    def test_a_save_from_outside_the_live_editor_starts_editors_again(self):
        page = self.new_page(self.m, title=uniq("reset"))
        self.post_json(self.m, f"/notebook/api/pages/{page}/sync", {"client": "a", "gen": 0, "init": True, "updates": [b64(b"s")]})
        # The live editor's own saves carry its generation…
        self.assertEqual(self.save(self.m, page, headers={"X-Collab-Gen": "0"}, body="from the editor").status_code, 200)
        self.assertEqual(lab_notebook_gen(page), 0)
        # …the plain-text fallback's do not: editing state is dropped.
        self.assertEqual(self.save(self.m, page, body="from the textarea").status_code, 200)
        self.assertEqual(lab_notebook_gen(page), 1)
        self.assertEqual(count("notebook_sync_updates", "page_id_fk=?", page), 0)
        # An editor still on the old state is told to start again.
        r = self.save(self.m, page, headers={"X-Collab-Gen": "0"}, body="stale")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(one("select body from notebook_pages where id=?", page), "from the textarea")
        pull = self.m.get(f"/notebook/api/pages/{page}/sync?since=5&gen=0&client=a").get_json()
        self.assertTrue(pull["reset"])

    def test_compaction_replaces_old_updates_with_one_state(self):
        page = self.new_page(self.m, title=uniq("compact"))
        for i in range(3):
            self.post_json(self.m, f"/notebook/api/pages/{page}/sync", {"client": "a", "gen": 0, "updates": [b64(bytes([i]))]})
        last = one("select max(id) from notebook_sync_updates where page_id_fk=?", page)
        r = self.post_json(self.m, f"/notebook/api/pages/{page}/sync/compact", {"client": "a", "gen": 0, "upto": last, "state": b64(b"all")})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual([r[0] for r in rows("select data from notebook_sync_updates where page_id_fk=?", page)], [b64(b"all")])


def lab_notebook_gen(page_id):
    return one("select collab_generation from notebook_page_info where page_id_fk=?", page_id)


class VersionTests(Notebook):
    def test_quick_edits_fold_into_one_version_and_another_person_starts_a_new_one(self):
        editor = make_user()
        page = self.new_page(self.m, title=uniq("hist"))
        self.share(self.m, page, editor, "edit")
        self.save(self.m, page, body="one")
        self.save(self.m, page, body="one two")
        self.assertEqual(count("notebook_versions", "page_id_fk=?", page), 1)
        self.save(client_for(editor), page, body="one two three")
        self.assertEqual(count("notebook_versions", "page_id_fk=?", page), 2)
        versions = self.m.get(f"/notebook/api/pages/{page}/versions").get_json()["versions"]
        self.assertEqual([v["saved_by"] for v in versions], [editor, self.member])

    def test_restoring_brings_back_the_text_and_keeps_what_was_there(self):
        page = self.new_page(self.m, title=uniq("restore"))
        self.save(self.m, page, body="original")
        first = self.post_json(self.m, f"/notebook/api/pages/{page}/versions", {"label": "before"}).get_json()["id"]
        self.save(self.m, page, body="rewritten")
        r = self.post_json(self.m, f"/notebook/api/versions/{first}/restore")
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(one("select body from notebook_pages where id=?", page), "original")
        bodies = [r[0] for r in rows("select body from notebook_versions where page_id_fk=? order by id", page)]
        self.assertIn("rewritten", bodies)
        self.assertEqual(one("select kind from notebook_versions where page_id_fk=? order by id desc limit 1", page), "restore")

    def test_versions_of_a_page_you_cannot_see_stay_hidden(self):
        page = self.new_page(self.m, title=uniq("secret"))
        self.save(self.m, page, body="secret text")
        vid = one("select id from notebook_versions where page_id_fk=?", page)
        self.assertEqual(client_for(make_user()).get(f"/notebook/api/versions/{vid}").status_code, 404)


class CommentTests(Notebook):
    def test_mentioning_someone_who_can_see_the_page_tells_them(self):
        mate = make_user()
        page = self.new_page(self.m, title=uniq("review"))
        self.share(self.m, page, mate)
        r = self.post_json(self.m, f"/notebook/api/pages/{page}/comments", {"body": f"@{mate} can you check the lot?", "quote": "lot 42"})
        self.assertEqual(r.get_json()["no_access"], [])
        self.assertEqual(count("notifications", "recipient_username=? and title like ?", mate, "%mentioned you%"), 1)
        threads = self.m.get(f"/notebook/api/pages/{page}/comments").get_json()["threads"]
        self.assertEqual(threads[0]["quote"], "lot 42")

    def test_mentioning_someone_without_access_names_them_instead(self):
        outsider = make_user()
        page = self.new_page(self.m, title=uniq("closed"))
        r = self.post_json(self.m, f"/notebook/api/pages/{page}/comments", {"body": f"@{outsider} look"})
        self.assertEqual(r.get_json()["no_access"], [outsider])
        self.assertEqual(count("notifications", "recipient_username=?", outsider), 0)

    def test_replies_resolve_and_the_owner_hears_of_comments(self):
        mate = make_user()
        page = self.new_page(self.m, title=uniq("thread"))
        self.share(self.m, page, mate)
        mc = client_for(mate)
        cid = self.post_json(mc, f"/notebook/api/pages/{page}/comments", {"body": "Why 37 °C?"}).get_json()["id"]
        self.assertEqual(count("notifications", "recipient_username=? and title like ?", self.member, "%commented%"), 1)
        self.post_json(self.m, f"/notebook/api/pages/{page}/comments", {"body": "Per the kit", "parent_id": cid})
        self.post_json(self.m, f"/notebook/api/comments/{cid}/resolve", {"resolved": True})
        thread = self.m.get(f"/notebook/api/pages/{page}/comments").get_json()["threads"][0]
        self.assertTrue(thread["resolved"])
        self.assertEqual([r["body"] for r in thread["replies"]], ["Per the kit"])
        # Someone else's comment is theirs to delete (or the page owner's).
        self.assertEqual(self.post_json(client_for(make_user()), f"/notebook/api/comments/{cid}/delete").status_code, 404)


class TagsAndSearchTests(Notebook):
    def test_tags_are_normalised_and_filter_the_search(self):
        page = self.new_page(self.m, title=uniq("tagged"))
        r = self.post_json(self.m, f"/notebook/api/pages/{page}/meta", {"tags": ["Western", " #western ", "Cell Culture"]})
        self.assertEqual(r.get_json()["page"]["tags"], ["western", "cell culture"])
        found = self.m.get("/notebook/api/search?tag=cell culture").get_json()["results"]
        self.assertIn(page, [x["id"] for x in found])

    def test_search_matches_every_word_and_shows_a_snippet(self):
        word = uniq("uniqword")
        page = self.new_page(self.m, title="Assay")
        self.save(self.m, page, body=f"The {word} sample was spun at 4 °C for 10 min.")
        found = self.m.get(f"/notebook/api/search?q={word} spun").get_json()["results"]
        self.assertEqual([x["id"] for x in found], [page])
        self.assertIn(word, found[0]["snippet"])
        self.assertEqual(self.m.get(f"/notebook/api/search?q={word} centrifuge").get_json()["results"], [])

    def test_search_filters_by_kind_and_status(self):
        page = self.new_page(self.m, starter="experiment", title=uniq("exp"))
        self.post_json(self.m, f"/notebook/api/pages/{page}/meta", {"action": "start"})
        ids = [x["id"] for x in self.m.get("/notebook/api/search?kind=experiment&status=running").get_json()["results"]]
        self.assertIn(page, ids)
        ids = [x["id"] for x in self.m.get("/notebook/api/search?kind=protocol").get_json()["results"]]
        self.assertNotIn(page, ids)


class PageKindTests(Notebook):
    def test_starting_and_finishing_an_experiment_stamps_the_times(self):
        page = self.new_page(self.m, starter="experiment", title=uniq("run"))
        info = self.m.get(f"/notebook/api/pages/{page}").get_json()["page"]
        self.assertEqual((info["kind"], info["status"], info["started_at"]), ("experiment", "planned", ""))
        started = self.post_json(self.m, f"/notebook/api/pages/{page}/meta", {"action": "start"}).get_json()["page"]
        self.assertEqual(started["status"], "running")
        self.assertTrue(started["started_at"])
        done = self.post_json(self.m, f"/notebook/api/pages/{page}/meta", {"action": "finish", "outcome": "failed"}).get_json()["page"]
        self.assertEqual(done["status"], "failed")
        self.assertTrue(done["finished_at"])

    def test_today_makes_one_daily_page_per_day(self):
        user = make_user()
        c = client_for(user)
        first = c.get("/notebook/today")
        second = c.get("/notebook/today")
        self.assertEqual(first.headers["Location"], second.headers["Location"])
        self.assertEqual(one("select count(*) from notebook_page_info i join notebook_pages p on p.id=i.page_id_fk "
                             "join notebook_tabs t on t.id=p.tab_id_fk where t.owner_username=? and i.kind='daily'", user), 1)
        self.assertEqual(one("select title from notebook_tabs where owner_username=?", user), "Daily log")

    def test_starters_fill_the_page_and_file_it_in_a_topic(self):
        user = make_user()
        c = client_for(user)
        page = self.new_page(c, starter="qpcr")
        body = one("select body from notebook_pages where id=?", page)
        self.assertIn("```qpcr", body)
        self.assertIn("```calc", body)
        self.assertEqual(one("select t.title from notebook_tabs t join notebook_pages p on p.tab_id_fk=t.id where p.id=?", page), "Experiments")

    def test_a_page_started_with_a_topic_open_goes_in_that_topic(self):
        user = make_user()
        c = client_for(user)
        self.new_page(c)                                         # makes the Inbox
        c.post("/notebook/tabs/create", data={"title": "SOPs"})
        sops = one("select id from notebook_tabs where owner_username=? and title='SOPs'", user)
        topic = lambda page: one("select t.title from notebook_tabs t join notebook_pages p on p.tab_id_fk=t.id "
                                 "where p.id=?", page)
        self.assertEqual(topic(self.new_page(c, starter="blank", open_tab_id=sops)), "SOPs")
        self.assertEqual(topic(self.new_page(c, starter="protocol", open_tab_id=sops)), "SOPs")
        # Experiments and meetings keep their own topics; someone else's topic is no place.
        self.assertEqual(topic(self.new_page(c, starter="experiment", open_tab_id=sops)), "Experiments")
        self.assertNotEqual(topic(self.new_page(self.m, starter="blank", open_tab_id=sops)), "SOPs")

    def test_a_page_started_with_no_topic_open_goes_in_the_inbox(self):
        user = make_user()
        c = client_for(user)
        c.post("/notebook/tabs/create", data={"title": "Photometry"})        # the first topic
        page = self.new_page(c, starter="blank")
        self.assertEqual(one("select t.title from notebook_tabs t join notebook_pages p on p.tab_id_fk=t.id "
                             "where p.id=?", page), "Inbox")

    def test_the_page_shows_times_on_the_lab_s_clock(self):
        from unittest import mock
        from app import lab
        page = self.new_page(self.m, starter="experiment")
        with mock.patch.object(lab, "clock_zone", return_value="America/New_York"):
            html = self.m.get(f"/notebook?page={page}").get_data(as_text=True)
        self.assertIn('"labZone": "America/New_York"', html)

    def test_a_new_page_s_title_box_starts_empty(self):
        page = self.new_page(self.m, starter="blank")
        html = self.m.get(f"/notebook?page={page}").get_data(as_text=True)
        self.assertIn('id="page-title" class="page-title" value="" placeholder="Untitled page"', html)

    def test_the_notebook_page_renders_every_kind(self):
        for starter in ("blank", "experiment", "protocol", "meeting", "seminar", "daily", "cloning", "western"):
            page = self.new_page(self.m, starter=starter)
            r = self.m.get(f"/notebook?page={page}")
            self.assertEqual(r.status_code, 200, starter)
            self.assertIn(f'data-page-id="{page}"', r.get_data(as_text=True))


class ProtocolTests(Notebook):
    def test_steps_become_a_checklist(self):
        body = "# PCR\n\n## Steps\n\n1. Thaw primers\n2. Mix 10 min\n   1. Nested\n\n```calc\n1. not a step\n```\n"
        out = lab_notebook.steps_as_checklist(body, "PCR")
        self.assertIn("- [ ] Thaw primers", out)
        self.assertIn("   - [ ] Nested", out)
        self.assertIn("### Steps", out)
        self.assertNotIn("# PCR", out.split("\n")[0])
        self.assertIn("1. not a step", out)

    def test_bullets_under_a_steps_heading_count_when_nothing_is_numbered(self):
        out = lab_notebook.steps_as_checklist("## Materials\n\n- Tris\n\n## Procedure\n\n- Lyse cells\n- Spin\n")
        self.assertIn("- Tris", out)
        self.assertIn("- [ ] Lyse cells", out)

    def test_an_experiment_from_a_protocol_follows_its_numbered_version(self):
        mate = make_user()
        protocol = self.new_page(self.m, starter="protocol", title=uniq("Miniprep"))
        self.save(self.m, protocol, body="## Steps\n\n1. Pellet cells\n2. Resuspend\n")
        self.share(self.m, protocol, "*", "view")
        # The first experiment numbers the protocol v1.
        c = client_for(mate)
        r = self.post_json(c, f"/notebook/api/pages/{protocol}/start-experiment", {"title": "Prep 1"})
        # A viewer cannot number it, so theirs follows the text as it is.
        self.assertEqual(r.status_code, 200)
        v = self.post_json(self.m, f"/notebook/api/pages/{protocol}/versions", {"release": True}).get_json()
        self.assertEqual(v["number"], 1)
        self.save(self.m, protocol, body="## Steps\n\n1. Pellet cells\n2. Resuspend in P1\n")
        exp = self.post_json(self.m, f"/notebook/api/pages/{protocol}/start-experiment", {}).get_json()["page_id"]
        body = one("select body from notebook_pages where id=?", exp)
        self.assertIn("- [ ] Resuspend", body)
        self.assertNotIn("in P1", body)  # v1, not the unnumbered edit
        info = self.m.get(f"/notebook/api/pages/{exp}").get_json()["page"]
        self.assertEqual((info["kind"], info["status"], info["protocol"]["version"]), ("experiment", "running", 1))
        listed = self.m.get(f"/notebook/api/pages/{protocol}/experiments").get_json()["experiments"]
        self.assertIn(exp, [x["id"] for x in listed])


class ProtocolLibraryTests(Notebook):
    """The protocol library: the lab's protocol pages and common protocols
    built in (app/notebook_protocols.py), inserted into a page or copied."""

    def test_the_library_lists_lab_protocols_and_the_built_in_ones(self):
        title = uniq("Perfusion v2")
        self.new_page(self.m, starter="protocol", title=title)
        lib = self.m.get("/notebook/api/protocols").get_json()
        self.assertIn(title, [p["title"] for p in lib["protocols"]])
        keys = [p["key"] for p in lib["presets"]]
        self.assertIn("hotshot", keys)
        self.assertTrue(all(p["title"] and p["category"] and p["summary"] for p in lib["presets"]))

    def test_every_built_in_protocol_has_steps_that_become_a_checklist(self):
        from app.notebook_protocols import PRESET_PROTOCOLS
        for key in PRESET_PROTOCOLS:
            text = self.m.get(f"/notebook/api/protocols/text?preset={key}").get_json()
            self.assertIn("(built in)", text["markdown"], key)
            self.assertIn("- [ ] ", text["markdown"], key)
            self.assertIn("### Steps", text["markdown"], key)

    def test_a_lab_protocol_is_inserted_at_its_numbered_version_with_a_link(self):
        title = uniq("Miniprep")
        protocol = self.new_page(self.m, starter="protocol", title=title)
        self.save(self.m, protocol, body="## Steps\n\n1. Pellet cells\n2. Resuspend\n")
        self.post_json(self.m, f"/notebook/api/pages/{protocol}/versions", {"release": True})
        self.save(self.m, protocol, body="## Steps\n\n1. Pellet cells\n2. Resuspend in P1\n")
        text = self.m.get(f"/notebook/api/protocols/text?page={protocol}").get_json()["markdown"]
        self.assertIn(f"[{title}](/notebook?page={protocol}) · v1", text)
        self.assertIn("- [ ] Resuspend", text)
        self.assertNotIn("in P1", text)

    def test_someone_elses_private_protocol_cannot_be_inserted(self):
        protocol = self.new_page(self.m, starter="protocol", title=uniq("Secret"))
        self.assertEqual(client_for(make_user()).get(f"/notebook/api/protocols/text?page={protocol}").status_code, 404)

    def test_copying_a_built_in_protocol_makes_an_editable_protocol_page(self):
        r = self.post_json(self.m, "/notebook/api/protocols/new", {"preset": "western"}).get_json()
        page = self.m.get(f"/notebook/api/pages/{r['page_id']}").get_json()["page"]
        self.assertEqual((page["kind"], page["title"], page["role"]), ("protocol", "Western blot", "owner"))
        self.assertIn("Ponceau", page["body"])
        tab = one("select t.title from notebook_pages p join notebook_tabs t on t.id=p.tab_id_fk where p.id=?", r["page_id"])
        self.assertEqual(tab, "Protocols")

    def test_a_new_blank_protocol_and_an_unknown_preset(self):
        blank = self.post_json(self.m, "/notebook/api/protocols/new", {}).get_json()
        self.assertIn("## Steps", self.m.get(f"/notebook/api/pages/{blank['page_id']}").get_json()["page"]["body"])
        self.assertEqual(self.post_json(self.m, "/notebook/api/protocols/new", {"preset": "nope"}).status_code, 404)
        self.assertEqual(self.m.get("/notebook/api/protocols/text?preset=nope").status_code, 404)

    def test_the_sidebar_opens_each_library_as_a_page(self):
        html = self.get_ok(self.m, "/notebook")
        for key in ("protocols", "recipes", "meetings"):
            self.assertIn(f'href="/notebook/{key}"', html)
            page = self.get_ok(self.m, f"/notebook/{key}")
            self.assertIn(f'data-library="{key}"', page)
            self.assertIn("notebook-library.js", page)
            self.assertIn('aria-current="page"', page)
        self.assertEqual(self.m.get("/notebook/other").status_code, 404)

    def test_a_built_in_protocol_reads_as_it_is_written(self):
        from app.notebook_protocols import PRESET_PROTOCOLS
        text = self.m.get("/notebook/api/protocols/text?preset=hotshot&as=written").get_json()
        self.assertEqual(text["markdown"], PRESET_PROTOCOLS["hotshot"]["body"])


class LibraryFolderTests(Notebook):
    """Folders in the protocol and recipe libraries: the lab's, so anyone
    makes one and files in it what they may edit; its maker or an admin
    renames or deletes it, and what was in it stays."""

    def folder(self, client, kind, name=None):
        r = self.post_json(client, "/notebook/api/folders", {"kind": kind, "name": name or uniq("Cloning")})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["id"]

    def file(self, client, kind, item_id, folder_id):
        return self.post_json(client, "/notebook/api/folders/file", {"kind": kind, "item_id": item_id, "folder_id": folder_id})

    def recipe(self, client, name=None):
        r = self.post_json(client, "/notebook/api/recipes", {"name": name or uniq("TBS"), "data": {
            "volume": 1, "volumeUnit": "L", "components": [{"name": "Tris", "conc": 50, "unit": "mM", "mw": 121.14}]}})
        return r.get_json()["id"]

    def test_a_protocol_is_filed_in_a_folder_the_lab_sees(self):
        folder = self.folder(self.m, "protocol")
        protocol = self.new_page(self.m, starter="protocol", title=uniq("Gibson"))
        self.assertEqual(self.file(self.m, "protocol", protocol, folder).status_code, 200)
        lib = self.m.get("/notebook/api/protocols").get_json()
        mine = next(p for p in lib["protocols"] if p["id"] == protocol)
        self.assertEqual((mine["folder_id"], mine["can_edit"]), (folder, True))
        self.assertIn(folder, [f["id"] for f in lib["folders"]])
        # A lab mate sees the folder, but not a protocol not shared with them.
        theirs = client_for(make_user()).get("/notebook/api/protocols").get_json()
        self.assertIn(folder, [f["id"] for f in theirs["folders"]])
        self.assertNotIn(protocol, [p["id"] for p in theirs["protocols"]])
        # The page knows its folder; taking it out leaves it in none.
        self.assertEqual(self.m.get(f"/notebook/api/pages/{protocol}").get_json()["page"]["folder_id"], folder)
        self.file(self.m, "protocol", protocol, None)
        self.assertIsNone(one("select folder_id from notebook_page_info where page_id_fk=?", protocol))

    def test_a_new_protocol_made_in_a_folder_goes_in_it(self):
        folder = self.folder(self.m, "protocol")
        r = self.post_json(self.m, "/notebook/api/protocols/new", {"preset": "hotshot", "folder_id": folder}).get_json()
        self.assertEqual(one("select folder_id from notebook_page_info where page_id_fk=?", r["page_id"]), folder)
        # A recipe folder is not a protocol's.
        other = self.folder(self.m, "recipe")
        self.assertEqual(self.post_json(self.m, "/notebook/api/protocols/new", {"folder_id": other}).status_code, 404)

    def test_only_who_may_edit_a_protocol_files_it(self):
        folder = self.folder(self.m, "protocol")
        protocol = self.new_page(self.m, starter="protocol", title=uniq("Shared SOP"))
        viewer, editor = make_user(), make_user()
        self.share(self.m, protocol, viewer, "view")
        self.share(self.m, protocol, editor, "edit")
        self.assertEqual(self.file(client_for(viewer), "protocol", protocol, folder).status_code, 403)
        listed = client_for(viewer).get("/notebook/api/protocols").get_json()["protocols"]
        self.assertFalse(next(p for p in listed if p["id"] == protocol)["can_edit"])
        self.assertEqual(self.file(client_for(editor), "protocol", protocol, folder).status_code, 200)
        self.assertEqual(self.file(client_for(make_user()), "protocol", protocol, folder).status_code, 404)
        note = self.new_page(self.m, title=uniq("not a protocol"))
        self.assertEqual(self.file(self.m, "protocol", note, folder).status_code, 400)

    def test_recipes_are_filed_by_whoever_saved_them(self):
        folder = self.folder(self.m, "recipe", uniq("Buffers"))
        rid = self.recipe(self.m)
        self.assertEqual(self.file(client_for(make_user()), "recipe", rid, folder).status_code, 403)
        self.assertEqual(self.file(self.m, "recipe", rid, folder).status_code, 200)
        lib = self.m.get("/notebook/api/recipes").get_json()
        self.assertEqual(next(r for r in lib["recipes"] if r["id"] == rid)["folder_id"], folder)
        # Saving its text keeps it where it is; a new one can start in a folder.
        self.post_json(self.m, "/notebook/api/recipes", {"id": rid, "name": "TBS, 10×", "data": {"components": []}})
        self.assertEqual(one("select folder_id from notebook_recipes where id=?", rid), folder)
        made = self.post_json(self.m, "/notebook/api/recipes", {"name": uniq("PBS-T"), "data": {"components": []}, "folder_id": folder}).get_json()["id"]
        self.assertEqual(one("select folder_id from notebook_recipes where id=?", made), folder)

    def test_a_folder_is_renamed_or_deleted_by_its_maker_and_its_contents_stay(self):
        name = uniq("Imaging")
        folder = self.folder(self.m, "recipe", name)
        rid = self.recipe(self.m)
        self.file(self.m, "recipe", rid, folder)
        # The same name twice is refused, whatever its case; a protocol folder may share it.
        self.assertEqual(self.post_json(self.m, "/notebook/api/folders", {"kind": "recipe", "name": name.upper()}).status_code, 400)
        self.folder(self.m, "protocol", name)
        other = client_for(make_user())
        self.assertEqual(self.post_json(other, f"/notebook/api/folders/{folder}", {"name": "Mine"}).status_code, 403)
        self.assertEqual(self.post_json(other, f"/notebook/api/folders/{folder}/delete").status_code, 403)
        renamed = uniq("Microscopy")
        self.assertEqual(self.post_json(self.m, f"/notebook/api/folders/{folder}", {"name": renamed}).get_json()["name"], renamed)
        # An admin may too.
        self.assertEqual(self.post_json(self.a, f"/notebook/api/folders/{folder}/delete").status_code, 200)
        self.assertEqual(count("notebook_folders", "id=?", folder), 0)
        self.assertIsNone(one("select folder_id from notebook_recipes where id=?", rid))

    def test_a_folder_needs_a_name_and_a_kind_and_guests_make_none(self):
        self.assertEqual(self.post_json(self.m, "/notebook/api/folders", {"kind": "protocol", "name": "  "}).status_code, 400)
        self.assertEqual(self.post_json(self.m, "/notebook/api/folders", {"kind": "plasmid", "name": "X"}).status_code, 400)
        guest = make_user()
        from app.db import SessionLocal
        from app.models import UserAccount
        from datetime import datetime
        with SessionLocal() as s:
            s.query(UserAccount).filter_by(username=guest).update({"expires_at": datetime.utcnow() + timedelta(days=30)})
            s.commit()
        self.assertEqual(self.post_json(client_for(guest), "/notebook/api/folders", {"kind": "protocol", "name": uniq("G")}).status_code, 403)


class MeetingTests(Notebook):
    def make_series(self, members, **extra):
        body = {"name": uniq("Lab meeting"), "members": members, "weekday": TODAY.weekday(), "time": "16:00", **extra}
        r = self.post_json(self.m, "/notebook/api/meetings", body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["series"]

    def test_notes_name_the_presenter_share_with_everyone_and_move_the_rotation_on(self):
        a, b = make_user(), make_user()
        series = self.make_series([a, b, self.member])
        self.assertEqual(series["next"]["presenter"], a)
        r = self.post_json(self.m, f"/notebook/api/meetings/{series['id']}/note")
        page = r.get_json()["page_id"]
        info = self.m.get(f"/notebook/api/pages/{page}").get_json()["page"]
        self.assertEqual((info["kind"], info["presenter"]), ("meeting", a))
        self.assertEqual(client_for(b).get(f"/notebook/api/pages/{page}").get_json()["page"]["role"], "edit")
        after = self.m.get("/notebook/api/meetings").get_json()["series"]
        self.assertEqual([s for s in after if s["id"] == series["id"]][0]["next"]["presenter"], b)

    def test_the_rotation_can_be_skipped_and_dates_follow_the_weekday(self):
        a, b = make_user(), make_user()
        series = self.make_series([a, b])
        self.assertEqual(series["upcoming"][0]["date"], TODAY.isoformat())
        self.assertEqual(series["upcoming"][1]["date"], (TODAY + timedelta(days=7)).isoformat())
        skipped = self.post_json(self.m, f"/notebook/api/meetings/{series['id']}/advance", {"step": 1}).get_json()["series"]
        self.assertEqual(skipped["next"]["presenter"], b)

    def test_meetings_go_on_the_calendar_and_open_their_notes(self):
        a = make_user()
        series = self.make_series([a, self.member])
        made = self.post_json(self.m, f"/notebook/api/meetings/{series['id']}/calendar", {"count": 2}).get_json()["made"]
        self.assertEqual(made, 2)
        again = self.post_json(self.m, f"/notebook/api/meetings/{series['id']}/calendar", {"count": 2}).get_json()["made"]
        self.assertEqual(again, 0)
        self.assertEqual(count("calendar_events", "title like ? and event_type='meeting'", series["name"] + "%"), 2)
        # The second meeting's notes (from its calendar event) name its own presenter.
        day = (TODAY + timedelta(days=7)).isoformat()
        r = self.m.get(f"/notebook/meetings/{series['id']}/open?date={day}")
        page = int(r.headers["Location"].split("page=")[1])
        self.assertEqual(self.m.get(f"/notebook/api/pages/{page}").get_json()["page"]["presenter"], self.member)
        # Opening it again finds the same page, and the rotation did not move.
        self.assertEqual(self.m.get(f"/notebook/meetings/{series['id']}/open?date={day}").headers["Location"], r.headers["Location"])
        current = [s for s in self.m.get("/notebook/api/meetings").get_json()["series"] if s["id"] == series["id"]][0]
        self.assertEqual(current["next"]["presenter"], a)

    def test_action_items_become_to_dos_once(self):
        a = make_user()
        page = self.new_page(self.m, starter="meeting", title=uniq("minutes"))
        self.save(self.m, page, body=f"## Action items\n\n- [ ] @{a} order primers, due {TODAY.isoformat()}\n- [ ] @nobody-here skip me\n- [x] @{a} already done\n")
        r = self.post_json(self.m, f"/notebook/api/pages/{page}/action-items").get_json()
        self.assertEqual([(m["username"], m["due"]) for m in r["made"]], [(a, TODAY.isoformat())])
        self.assertEqual(count("tasks", "owner=? and title like ?", a, "%order primers%"), 1)
        again = self.post_json(self.m, f"/notebook/api/pages/{page}/action-items").get_json()
        self.assertEqual((again["made"], again["skipped"]), ([], 1))


class RecipeAndMarkdownTests(Notebook):
    def test_recipes_are_saved_to_the_lab_library_beside_the_built_in_ones(self):
        name = uniq("HEPES buffer")
        r = self.post_json(self.m, "/notebook/api/recipes", {"name": name, "data": {"volume": 1, "volumeUnit": "L", "components": [{"name": "HEPES", "conc": 20, "unit": "mM", "mw": 238.3}]}})
        rid = r.get_json()["id"]
        lib = client_for(make_user()).get("/notebook/api/recipes").get_json()
        self.assertIn(name, [x["name"] for x in lib["recipes"]])
        self.assertIn("PBS, 10×", [x["name"] for x in lib["presets"]])
        # Someone else's recipe is not theirs to delete.
        self.assertEqual(self.post_json(client_for(make_user()), f"/notebook/api/recipes/{rid}/delete").status_code, 403)

    def test_export_writes_front_matter_and_import_reads_it_back(self):
        page = self.new_page(self.m, title="Cloning log")
        self.save(self.m, page, body="# Cloning log\n\nSome text")
        self.post_json(self.m, f"/notebook/api/pages/{page}/meta", {"tags": ["cloning"]})
        md = self.m.get(f"/notebook/api/pages/{page}/export.md").get_data(as_text=True)
        self.assertTrue(md.startswith("---\ntitle: \"Cloning log\""))
        self.assertIn('tags: ["cloning"]', md)
        r = self.m.post("/notebook/api/import", data={"file": (io.BytesIO(md.encode()), "log.md")},
                        content_type="multipart/form-data")
        new = r.get_json()["page_id"]
        self.assertEqual(one("select title from notebook_pages where id=?", new), "Cloning log")
        self.assertTrue(one("select body from notebook_pages where id=?", new).startswith("# Cloning log"))
        self.assertEqual(self.m.get(f"/notebook/api/pages/{new}").get_json()["page"]["tags"], ["cloning"])

    def test_the_order_lookup_answers(self):
        # It used to be registered on the wrong function and answered 500.
        r = self.m.get("/notebook/lookup/order/987654")
        self.assertEqual(r.status_code, 404)

    def test_a_template_is_fetched_whole(self):
        body = "x" * 400
        self.m.post("/notebook/templates/create", data={"title": uniq("tpl"), "body": body})
        tid = one("select id from notebook_templates where owner_username=? order by id desc limit 1", self.member)
        self.assertEqual(self.m.get(f"/notebook/templates/{tid}").get_json()["template"]["body"], body)


class NotificationSwitchTests(Notebook):
    def test_turning_notebook_notifications_off_in_settings_silences_them(self):
        mate = make_user()
        c = client_for(mate)
        r = c.post("/settings", data={"action": "notifications", "notify_lab": "1"})  # every box but Notebook
        self.assertIn(r.status_code, (200, 302))
        self.assertEqual(one("select notify_notebook from users where username=?", mate), 0)
        page = self.new_page(self.m, title=uniq("quiet"))
        self.share(self.m, page, mate)
        self.post_json(self.m, f"/notebook/api/pages/{page}/comments", {"body": f"@{mate} look"})
        self.assertEqual(count("notifications", "recipient_username=?", mate), 0)
        # On again: they hear of the next one, filed under Notebook.
        c.post("/settings", data={"action": "notifications", "notify_notebook": "1"})
        self.post_json(self.m, f"/notebook/api/pages/{page}/comments", {"body": f"@{mate} again"})
        self.assertEqual(count("notifications", "recipient_username=? and category='notebook'", mate), 1)

    def test_settings_shows_the_notebook_switch(self):
        html = self.m.get("/settings").get_data(as_text=True)
        self.assertIn('name="notify_notebook"', html)

    @only_sqlite
    def test_an_existing_database_gets_the_column_on_start(self):
        from app import services
        from app.db import engine
        with engine.begin() as con:
            con.exec_driver_sql("ALTER TABLE users DROP COLUMN notify_notebook")
        services.ensure_schema_updates()
        with engine.connect() as con:
            cols = [r[1] for r in con.exec_driver_sql("PRAGMA table_info(users)")]
        self.assertIn("notify_notebook", cols)


class MentionLinkTests(AppTestCase):
    """@mouse / @plasmid / @order chips open the record, not its list."""

    def test_a_mouse_mention_opens_that_mouse(self):
        row_id = self.make_mouse(self.m, self.member)
        number = one("select mouse_id from mice where id=?", row_id)
        r = self.m.get(f"/notebook/open/mouse/{number}")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith(f"#mouse-update-{row_id}"), r.headers["Location"])

    def test_a_plasmid_mention_opens_its_page(self):
        pid = self.make_plasmid(self.m)
        number = one("select plasmid_id from plasmids where id=?", pid)
        r = self.m.get(f"/notebook/open/plasmid/{number}")
        self.assertTrue(r.headers["Location"].endswith(f"/plasmid/{number}"), r.headers["Location"])

    def test_an_unknown_number_says_so(self):
        r = self.m.get("/notebook/open/mouse/987654", follow_redirects=True)
        self.assertIn("no mouse #987654", r.get_data(as_text=True))


class TemplateTests(Notebook):
    BODY = """## Samples

| Lane | Sample | µg |
| --- | --- | --- |
| 1 | Ladder | 5 |
| 2 | WT lysate | 30 |

- [x] Lyse 30 min on ice
- [ ] Run gel

![blot](/static/uploads/abc/blot.png)

```sheet
{"columns":[{"name":"Group","type":"text"},{"name":"OD","type":"number"}],"rows":[["WT","0.41"],["KO","0.12"]]}
```

```plate
{"format":96,"values":{"A1":"0.5"},"roles":{"A1":"standard"}}
```

## Observations

Ponceau even; lane 7 slightly low.

### Conclusion

p21 up about 6x at 10 uM.

## Next time

Use fresh ECL.
"""

    def template_from(self, client, page_id, **extra):
        r = client.post("/notebook/templates/create", data={"title": uniq("Western "), "from_page_id": str(page_id), **extra})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        return r.get_json()["template"]["id"]

    def test_a_taken_template_name_is_asked_about_then_replaced(self):
        name = uniq("Miniprep ")
        first = self.m.post("/notebook/templates/create", data={"title": name, "body": "one"}).get_json()
        again = self.m.post("/notebook/templates/create", data={"title": name.upper(), "body": "two"})
        self.assertEqual((again.status_code, again.get_json()["exists"]), (409, True))
        self.assertEqual(count("notebook_templates", "owner_username=? and lower(title)=lower(?)", self.member, name), 1)
        replaced = self.m.post("/notebook/templates/create", data={"title": name, "body": "two", "replace": "1"}).get_json()
        self.assertEqual(replaced["template"]["id"], first["template"]["id"])
        self.assertEqual(one("select body from notebook_templates where id=?", first["template"]["id"]), "two")
        # Someone else may have one of the same name.
        other = client_for(make_user())
        self.assertTrue(other.post("/notebook/templates/create", data={"title": name, "body": "x"}).get_json()["ok"])

    def test_structure_only_takes_a_page_s_length_even_with_unclosed_fences(self):
        import time
        from app import lab_notebook
        start = time.monotonic()
        lab_notebook.structure_only("\n".join("```a" for _ in range(20000)))
        self.assertLess(time.monotonic() - start, 1.0)
        kept = lab_notebook.structure_only("## Steps\n\n```calc\nx = 1\n```\n\n```a\nnever closed")
        self.assertIn("```calc", kept)
        self.assertIn("never closed", kept)

    def test_an_experiment_template_makes_experiments(self):
        page = self.new_page(self.m, starter="western")
        tid = self.template_from(self.m, page)
        self.assertEqual(one("select kind from notebook_templates where id=?", tid), "experiment")
        new = self.post_json(self.m, "/notebook/api/pages/new", {"template_id": tid}).get_json()["page_id"]
        self.assertEqual(one("select kind from notebook_page_info where page_id_fk=?", new), "experiment")

    def test_structure_only_keeps_the_steps_and_leaves_out_the_results(self):
        page = self.new_page(self.m, title=uniq("Blot "))
        self.save(self.m, page, body=self.BODY)
        tid = self.template_from(self.m, page, structure_only="1")
        body = one("select body from notebook_templates where id=?", tid)
        self.assertIn("| Lane | Sample | µg |", body)
        self.assertIn("| 1 |  |  |", body)
        self.assertNotIn("WT lysate", body)
        self.assertIn("- [ ] Lyse 30 min on ice", body)
        self.assertNotIn("[x]", body)
        self.assertNotIn("blot.png", body)
        sheet = json.loads(body.split("```sheet\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(sheet["rows"], [["WT", ""], ["KO", ""]])
        self.assertEqual(sheet["columns"][1]["name"], "OD")
        plate = json.loads(body.split("```plate\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual((plate["values"], plate["roles"]), ({}, {"A1": "standard"}))
        self.assertIn("## Observations", body)
        self.assertIn("### Conclusion", body)
        self.assertNotIn("Ponceau", body)
        self.assertNotIn("p21 up", body)
        self.assertIn("Use fresh ECL.", body)                       # a later section is kept
        # Without the tick, everything is kept as it was.
        full = one("select body from notebook_templates where id=?", self.template_from(self.m, page))
        self.assertIn("WT lysate", full)

    def test_a_lab_template_is_everyones_to_use_but_only_its_maker_s_to_delete(self):
        page = self.new_page(self.m, title=uniq("Miniprep "))
        mine = self.template_from(self.m, page, lab="1")
        private = self.template_from(self.m, page)
        other = client_for(make_user())
        listed = {t["id"]: t for t in other.get("/notebook/templates").get_json()["templates"]}
        self.assertIn(mine, listed)
        self.assertNotIn(private, listed)
        self.assertFalse(listed[mine]["can_delete"])
        self.assertTrue(self.post_json(other, "/notebook/api/pages/new", {"template_id": mine}).get_json()["ok"])
        self.assertEqual(self.post_json(other, "/notebook/api/pages/new", {"template_id": private}).status_code, 404)
        self.assertEqual(other.post(f"/notebook/templates/{mine}/delete").status_code, 404)
        self.assertEqual(one("select count(*) from notebook_templates where id=?", mine), 1)
