"""Notes into the notebook's log (lab_notebook.add_note): written at once on
a page nobody has open live, or handed to the next live editor, once."""
from __future__ import annotations

from tests.base import count, one, uniq
from tests.test_notebook import Notebook, b64

from app import actions
from app.app import app


class NotesIntoTheLog(Notebook):
    def note(self, client, **body):
        r = self.post_json(client, "/notebook/api/notes", body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def test_a_note_lands_in_todays_daily_log(self):
        out = self.note(self.m, text="Injected TMX day 3", time="09:15", via="Claude")
        self.assertFalse(out["pending"])
        body = one("select body from notebook_pages where id=?", out["page_id"])
        self.assertIn("## Log\n\n- **09:15** Injected TMX day 3 _(via Claude)_", body)
        again = self.note(self.m, text="Weighed cohort 2", time="10:00")
        self.assertEqual(again["page_id"], out["page_id"])                  # the same daily log
        body = one("select body from notebook_pages where id=?", out["page_id"])
        self.assertIn("_(via Claude)_\n- **10:00** Weighed cohort 2", body)
        self.assertEqual(self.m.get("/notebook/today").headers["Location"].split("page=")[1], str(out["page_id"]))

    def test_a_page_open_live_gets_it_from_one_editor(self):
        page = self.new_page(self.m, title=uniq("live"))
        r = self.post_json(self.m, f"/notebook/api/pages/{page}/sync",
                           {"client": "first", "gen": 0, "init": True, "updates": [b64(b"\x00\x00")]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        out = self.note(self.m, text="Plate 3 contaminated", page_id=page, time="11:00")
        self.assertTrue(out["pending"])
        a = self.m.get(f"/notebook/api/pages/{page}/sync?since=0&gen=0&client=editor-a").get_json()
        self.assertEqual([(i["text"], i["time"]) for i in a["inserts"]], [("Plate 3 contaminated", "11:00")])
        b = self.m.get(f"/notebook/api/pages/{page}/sync?since=0&gen=0&client=editor-b").get_json()
        self.assertEqual(b["inserts"], [])                                  # one editor adds it
        self.post_json(self.m, f"/notebook/api/pages/{page}/inserts/done",
                       {"client": "editor-a", "ids": [a["inserts"][0]["id"]]})
        self.assertIsNotNone(one("select done_at from notebook_pending_inserts where page_id_fk=?", page))
        a = self.m.get(f"/notebook/api/pages/{page}/sync?since=0&gen=0&client=editor-a").get_json()
        self.assertEqual(a["inserts"], [])

    def test_only_on_pages_you_may_edit(self):
        page = self.new_page(self.m, title=uniq("mine"))
        r = self.post_json(self.o, "/notebook/api/notes", {"text": "x", "page_id": page})
        self.assertIn(r.status_code, (403, 404))
        self.assertEqual(self.post_json(self.m, "/notebook/api/notes", {"text": " "}).status_code, 400)
        self.assertEqual(self.post_json(self.m, "/notebook/api/notes", {"text": "x", "time": "25:00"}).status_code, 400)

    def test_as_a_proposal(self):
        before = count("notebook_pages")
        out = actions.run(app, self.member, [{"action": "notebook_note", "fields": {"text": "Genotyped litter L12"}}],
                          apply=True, description=uniq("P"), source="Claude")
        self.assertTrue(out.ok, [c.as_dict() for c in out.changes])
        self.assertIn("today's daily log", out.changes[0].summary)
        page = one("select p.id from notebook_pages p join notebook_tabs t on p.tab_id_fk=t.id "
                   "join notebook_page_info i on i.page_id_fk=p.id where t.owner_username=? and i.kind='daily'",
                   self.member)
        self.assertIn("Genotyped litter L12 _(via Claude)_", one("select body from notebook_pages where id=?", page))
        self.assertLessEqual(count("notebook_pages"), before + 1)
