"""An assistant fixing notebook pages rather than making new ones
(app/actions.py notebook_page_change), and filing protocols in the
library's folders (protocol_folder_new, protocol_file), as the pages'
own routes allow; and reading a page's text through /api/v1."""
from __future__ import annotations

from tests.base import *  # noqa: F401,F403
from tests.base import count, execute, one, row, rows, uniq
from tests.test_actions_more import Applies, errors_of, run
from tests.test_api import Base as ApiBase
from tests.test_notebook import Notebook, b64

STEPS = "## Steps\n\n1. Thaw the cells.\n2. Spin 5 min.\n3. Plate them."
TASKS = "## Steps\n\n- [ ] Thaw the cells.\n- [ ] Spin 5 min.\n- [ ] Plate them."


def page_ref(page: int) -> dict:
    return {"kind": "notebook_page", "id": page}


class PageCase(Applies, Notebook):
    def drafted(self, title=None, body=STEPS, **fields):
        """A page an assistant drafted, as notebook_page_new makes it."""
        title = title or uniq("Passaging ")
        self.apply({"action": "notebook_page_new", "fields": {"title": title, "body": body, **fields}},
                   source="Claude")
        return one("select id from notebook_pages where title=?", title)

    def body(self, page):
        return one("select body from notebook_pages where id=?", page)

    def info(self, page):
        return row("select kind, tags, folder_id from notebook_page_info where page_id_fk=?", page)


class ChangingAPage(PageCase):
    def test_the_whole_text_rewritten_as_steps_to_tick_and_the_old_one_kept(self):
        page = self.drafted(kind="protocol")
        before, versions = self.body(page), count("notebook_versions", "page_id_fk=?", page)
        change = {"action": "notebook_page_change", "target": page_ref(page), "fields": {"body": TASKS}}
        out = run(self.member, change, source="Claude")
        self.assertTrue(out.ok, out.errors)
        self.assertEqual(self.body(page), before)                              # a preview keeps nothing
        self.assertIn("text rewritten: 3 lines in, 3 out", out.changes[0].summary)
        shown = out.changes[0].records[0]
        self.assertEqual((shown["table"], shown["id"]), ("notebook_pages", page))
        self.assertEqual(shown["changes"]["changes"]["Text, line 5"],
                         ["1. Thaw the cells. ↵ 2. Spin 5 min. ↵ 3. Plate them.",
                          "- [ ] Thaw the cells. ↵ - [ ] Spin 5 min. ↵ - [ ] Plate them."])

        self.apply(change, source="Claude")
        # The line saying an assistant drafted it stays at its top.
        self.assertEqual(self.body(page), "_Drafted by Claude_\n\n" + TASKS + "\n")
        self.assertEqual(count("notebook_pages", "title=?", one("select title from notebook_pages where id=?", page)), 1)
        kept = rows("select kind, label, body from notebook_versions where page_id_fk=? order by id", page)
        self.assertEqual(len(kept), versions + 2)                  # the text before, and the text after
        self.assertIn(("manual", "Before the change by Claude", before), kept)
        self.assertEqual(kept[-1][2], self.body(page))

    def test_title_kind_and_tags_before_and_after(self):
        page = self.drafted(tags=["cells"])
        title = uniq("Passaging HEK ")
        out = self.apply({"action": "notebook_page_change", "target": page_ref(page),
                          "fields": {"title": f"  {title} ", "kind": "protocol", "tags": ["Cells", "tissue culture"]}})
        said = out.changes[0].summary
        self.assertIn(f"title → “{title}”", said)
        self.assertIn("kind note → protocol", said)
        self.assertIn("tags cells → cells, tissue culture", said)
        changes = out.changes[0].records[-1]["changes"]["changes"]
        self.assertEqual(changes["Kind"], ["note", "protocol"])
        self.assertEqual(changes["Tags"], ["cells", "cells, tissue culture"])
        self.assertEqual(one("select title from notebook_pages where id=?", page), title)
        self.assertEqual(self.info(page)[:2], ("protocol", ",cells,tissue culture,"))

    def test_parts_of_the_text_found_and_replaced(self):
        page = self.drafted()
        out = self.apply({"action": "notebook_page_change", "target": page_ref(page), "fields": {"edits": [
            {"find": "1. Thaw the cells.", "replace": "- [ ] Thaw the cells at 37 °C."},
            {"find": "Spin 5 min", "replace": "Spin 3 min"}]}})
        self.assertIn("text: 2 lines in, 2 out", out.changes[0].summary)
        self.assertIn("- [ ] Thaw the cells at 37 °C.\n2. Spin 3 min.\n3. Plate them.", self.body(page))
        out = run(self.member, {"action": "notebook_page_change", "target": page_ref(page),
                                "fields": {"edits": [{"find": "Centrifuge", "replace": "x"}]}})
        self.assertIn("“Centrifuge” is not on", errors_of(out))
        out = run(self.member, {"action": "notebook_page_change", "target": page_ref(page),
                                "fields": {"edits": [{"find": "the", "replace": "x"}]}})
        self.assertIn("is on", errors_of(out))
        self.assertIn("2 times", errors_of(out))
        out = run(self.member, {"action": "notebook_page_change", "target": page_ref(page),
                                "fields": {"body": "x", "edits": [{"find": "Plate", "replace": "Seed"}]}})
        self.assertIn("not both", errors_of(out))
        out = run(self.member, {"action": "notebook_page_change", "target": page_ref(page),
                                "fields": {"title": one("select title from notebook_pages where id=?", page)}})
        self.assertIn("nothing would change", errors_of(out))

    def test_by_its_title_and_only_a_page_you_may_read(self):
        title = uniq("Same title ")
        mine = self.drafted(title=title)
        theirs = self.new_page(self.o, title=title)            # another's page of the same title: not in the way
        self.apply({"action": "notebook_page_change", "target": title, "fields": {"tags": ["mine"]}})
        self.assertEqual(self.info(mine)[1], ",mine,")
        self.assertIsNone(one("select tags from notebook_page_info where page_id_fk=? and tags like '%mine%'", theirs))

    def test_refused_without_leave_to_edit_it(self):
        page = self.drafted()
        before = self.body(page)
        change = {"action": "notebook_page_change", "target": page_ref(page), "fields": {"body": "Mine now."}}
        self.assertIn("There is no notebook page", errors_of(run(self.other, change)))     # not shared
        self.share(self.m, page, self.other, "view")
        self.assertIn("to read, not to edit", errors_of(run(self.other, change)))
        self.share(self.m, page, self.other, "edit")
        self.apply(change, who=self.other)
        self.assertIn("Mine now.", self.body(page))
        self.assertNotEqual(self.body(page), before)

    def test_not_while_the_page_is_open_in_the_editor(self):
        page = self.drafted()
        r = self.post_json(self.m, f"/notebook/api/pages/{page}/sync",
                           {"client": "tab-1", "gen": 0, "init": True, "updates": [b64(b"\x00\x00")]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        out = run(self.member, {"action": "notebook_page_change", "target": page_ref(page), "fields": {"body": "x"}})
        self.assertIn("is open in the editor", errors_of(out))
        execute("delete from notebook_presence where page_id_fk=?", page)
        self.apply({"action": "notebook_page_change", "target": page_ref(page), "fields": {"body": "Later."}})

    def test_not_a_signed_page(self):
        page = self.drafted()
        execute("insert into record_signatures (page_id_fk, action, username, name, meaning, reason, content_sha256, signed_at) "
                "values (?, 'sign', ?, 'M', 'author', '', 'x', current_timestamp)", page, self.member)
        before = self.body(page)
        for fields in ({"body": "Changed."}, {"tags": ["x"]}):
            out = run(self.member, {"action": "notebook_page_change", "target": page_ref(page), "fields": fields})
            self.assertIn("is signed and locked", errors_of(out))
        self.assertEqual(self.body(page), before)


class ProtocolFolders(PageCase):
    def folder(self, name):
        return one("select id from notebook_folders where kind='protocol' and name=?", name)

    def test_a_folder_made_and_protocols_filed_in_it_in_one_proposal(self):
        name, title = uniq("Cell culture "), uniq("Thawing ")
        page = self.drafted(kind="protocol")
        out = self.apply({"action": "protocol_folder_new", "fields": {"name": name, "icon": "cell"}},
                         {"action": "protocol_file", "target": page_ref(page), "fields": {"folder": name}},
                         {"action": "notebook_page_new", "fields": {"title": title, "kind": "protocol",
                                                                    "folder": name.upper(), "body": TASKS}})
        folder = self.folder(name)
        self.assertEqual(row("select icon, created_by from notebook_folders where id=?", folder), ("cell", self.member))
        self.assertEqual(self.info(page)[2], folder)
        made = one("select id from notebook_pages where title=?", title)
        self.assertEqual(self.info(made)[::2], ("protocol", folder))
        self.assertIn(f"filed in “{name}”", out.changes[1].summary)
        self.assertIn(f"filed in “{name}”", out.changes[2].summary)
        # And out of it again.
        out = self.apply({"action": "protocol_file", "target": page_ref(page), "fields": {"folder": ""}})
        self.assertIn("taken out of its folder", out.changes[0].summary)
        self.assertIsNone(self.info(page)[2])

    def test_a_folder_name_taken_already(self):
        name = uniq("Cloning ")
        self.apply({"action": "protocol_folder_new", "fields": {"name": name}})
        out = run(self.other, {"action": "protocol_folder_new", "fields": {"name": f" {name.lower()} "}})
        self.assertIn("called", errors_of(out))
        self.assertIn("already", errors_of(out))
        out = run(self.member, {"action": "protocol_folder_new", "fields": {"name": name + " 2"}},
                  {"action": "protocol_folder_new", "fields": {"name": name + " 2"}})
        self.assertTrue(out.changes[0].ok)
        self.assertIn("already", errors_of(out))
        self.assertEqual(count("notebook_folders", "lower(name)=lower(?)", name), 1)
        out = run(self.member, {"action": "protocol_folder_new", "fields": {"name": uniq("x"), "icon": "rocket"}})
        self.assertIn("icon is one of", errors_of(out))

    def test_only_a_protocol_you_may_edit_is_filed(self):
        name = uniq("PCR ")
        self.apply({"action": "protocol_folder_new", "fields": {"name": name}})
        note = self.drafted()
        out = run(self.member, {"action": "protocol_file", "target": page_ref(note), "fields": {"folder": name}})
        self.assertIn("is not a protocol", errors_of(out))
        out = run(self.member, {"action": "notebook_page_new", "fields": {"title": "x", "folder": name}})
        self.assertIn("Only a protocol is filed in a folder", errors_of(out))
        protocol = self.drafted(kind="protocol")
        self.share(self.m, protocol, self.other, "view")
        out = run(self.other, {"action": "protocol_file", "target": page_ref(protocol), "fields": {"folder": name}})
        self.assertIn("to read, not to edit", errors_of(out))
        out = run(self.member, {"action": "protocol_file", "target": page_ref(protocol),
                                "fields": {"folder": uniq("Nowhere ")}})
        self.assertIn("There is no protocol folder", errors_of(out))
        self.assertIsNone(self.info(protocol)[2])


class ReadingAPage(ApiBase, PageCase):
    def test_resolve_finds_pages_and_folders_and_get_reads_the_text(self):
        title, name = uniq("Miniprep "), uniq("Plasmids ")
        page = self.drafted(title=title, kind="protocol")
        self.apply({"action": "protocol_folder_new", "fields": {"name": name}},
                   {"action": "protocol_file", "target": page_ref(page), "fields": {"folder": name}})
        tok = self.token(self.m, scope="propose")
        hits = self.call("get", "/resolve", tok, query_string={"q": title}).get_json()["data"]
        hit = next(h for h in hits if h["kind"] == "notebook_page")
        self.assertEqual((hit["ref"], hit["path"]), (page_ref(page), f"notebook/pages/{page}"))
        self.assertIn("protocol", hit["label"])
        hits = self.call("get", "/resolve", tok, query_string={"q": f"folder {name}"}).get_json()["data"]
        self.assertEqual([h["kind"] for h in hits], ["protocol_folder"])
        r = self.call("get", f"/{hit['path']}", tok)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        got = r.get_json()
        self.assertEqual(got["body"], self.body(page))
        self.assertEqual((got["kind"], got["folder"]["name"], got["can_edit"], got["locked"]),
                         ("protocol", name, True, False))
        path = self.call("get", "/path", tok, query_string={"kind": "notebook_page", "id": page}).get_json()
        self.assertEqual(path["path"], f"notebook/pages/{page}")
        # Another's page is not there to read.
        theirs = self.token(self.o, scope="propose")
        self.assertEqual(self.call("get", f"/notebook/pages/{page}", theirs).status_code, 404)
        self.assertEqual(self.call("get", "/path", theirs, query_string={"kind": "notebook_page", "id": page})
                         .status_code, 404)


class ApprovingAPageChange(ApiBase, PageCase):
    def proposal(self, *changes) -> int:
        tok = self.token(self.m, scope="propose")
        r = self.call("post", "/proposals", tok, json={"summary": "Steps to tick", "source": "Claude",
                                                       "changes": list(changes)})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["id"]

    def test_approved_it_changes_the_page_and_shows_before_and_after(self):
        page = self.drafted(kind="protocol")
        pid = self.proposal({"action": "notebook_page_change", "target": page_ref(page), "fields": {"body": TASKS}})
        html = self.get_ok(self.m, "/proposals")
        self.assertIn("Text, line 5: 1. Thaw the cells.", html)
        r = self.post(self.m, f"/proposals/{pid}/approve")
        self.assertEqual(one("select status from proposals where id=?", pid), "approved", r.get_data(as_text=True)[:300])
        self.assertIn("- [ ] Thaw the cells.", self.body(page))

    def test_a_page_saved_after_the_preview_stops_it(self):
        page = self.drafted()
        pid = self.proposal({"action": "notebook_page_change", "target": page_ref(page), "fields": {"body": TASKS}})
        self.save(self.m, page, body="Edited by hand meanwhile.")
        self.post(self.m, f"/proposals/{pid}/approve")
        self.assertEqual(one("select status from proposals where id=?", pid), "pending")
        self.assertEqual(self.body(page), "Edited by hand meanwhile.")
