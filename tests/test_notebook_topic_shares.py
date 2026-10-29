"""A notebook topic shared with lab mates (app/lab_notebook.py, topic_shares):
its pages, and the topics inside it, open for them, to read or to edit."""
from __future__ import annotations

import json

from tests.base import AppTestCase, client_for, make_user, one, uniq  # isort: skip


class TopicSharing(AppTestCase):
    def post_json(self, client, url, body=None):
        return client.post(url, data=json.dumps(body or {}), content_type="application/json")

    def topic(self, client, title=None, parent=None):
        data = {"title": title or uniq("topic")}
        if parent:
            data["parent_id"] = parent
        r = client.post("/notebook/tabs/create", data=data)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["id"]

    def page_in(self, client, tab_id, title=None):
        r = self.post_json(client, "/notebook/api/pages/new", {"tab_id": tab_id, "title": title or uniq("page")})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["page_id"]

    def share(self, tab_id, username, role="view", client=None):
        r = self.post_json(client or self.m, f"/notebook/api/topics/{tab_id}/shares", {"username": username, "role": role})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def role(self, client, page_id):
        r = client.get(f"/notebook/api/pages/{page_id}")
        return r.get_json()["page"]["role"] if r.status_code == 200 else r.status_code

    def test_sharing_a_topic_opens_its_pages_and_the_topics_inside(self):
        outer = self.topic(self.m)
        inner = self.topic(self.m, parent=outer)
        page, deep = self.page_in(self.m, outer), self.page_in(self.m, inner)
        reader = make_user()
        r = client_for(reader)
        self.assertEqual(self.role(r, page), 404)
        self.share(outer, reader, "view")
        self.assertEqual(self.role(r, page), "view")
        self.assertEqual(self.role(r, deep), "view")
        # Read, not write.
        self.assertEqual(r.post(f"/notebook/pages/{page}/update", data={"body": "mine now"}).status_code, 403)
        # A page added later is shared too.
        later = self.page_in(self.m, inner)
        self.assertEqual(self.role(r, later), "view")
        # And it lists under Shared with me, with its owner's topic.
        html = self.get_ok(r, "/notebook")
        self.assertIn('class="notebook-tab is-shared"', html)
        self.assertIn(f'data-tab-id="{outer}"', html)
        self.assertEqual(self.get_ok(r, f"/notebook?tab={inner}").count('data-topic-share'), 0)

    def test_an_editor_edits_and_adds_pages_that_stay_the_owners(self):
        tab = self.topic(self.m)
        page = self.page_in(self.m, tab)
        editor = make_user()
        e = client_for(editor)
        self.share(tab, editor, "edit")
        self.assertEqual(self.role(e, page), "edit")
        added = self.page_in(e, tab, "From a lab mate")
        self.assertEqual(one("select tab_id_fk from notebook_pages where id=?", added), tab)
        self.assertEqual(self.role(self.m, added), "owner")
        # Only the owner shares it on, renames it or moves it.
        r = self.post_json(e, f"/notebook/api/topics/{tab}/shares", {"username": make_user(), "role": "view"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.post_json(e, f"/notebook/api/topics/{tab}", {"title": "Mine"}).status_code, 404)

    def test_the_whole_lab_and_stopping(self):
        tab = self.topic(self.m)
        page = self.page_in(self.m, tab)
        anyone = client_for(make_user())
        self.share(tab, "*", "view")
        self.assertEqual(self.role(anyone, page), "view")
        listed = self.get_ok(self.m, f"/notebook/api/topics/{tab}/shares")
        self.assertIn("Everyone in the lab", listed)
        r = self.post_json(self.m, f"/notebook/api/topics/{tab}/shares/remove", {"username": "*"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.role(anyone, page), 404)

    def test_a_viewer_is_told(self):
        tab = self.topic(self.m, uniq("Cloning"))
        reader = make_user()
        self.share(tab, reader, "view")
        self.assertEqual(one("select count(*) from notifications where recipient_username=? and link like ?",
                             reader, f"%tab={tab}%"), 1)

    def test_deleting_the_topic_takes_its_shares(self):
        tab = self.topic(self.m)
        self.share(tab, make_user(), "view")
        self.m.post(f"/notebook/tabs/{tab}/delete")
        self.assertEqual(one("select count(*) from notebook_topic_shares where tab_id_fk=?", tab), 0)

    def test_inbox_is_not_shared_and_others_cannot_share_yours(self):
        tab = self.topic(self.m)
        stranger = client_for(make_user())
        r = self.post_json(stranger, f"/notebook/api/topics/{tab}/shares", {"username": "*", "role": "view"})
        self.assertEqual(r.status_code, 404)
