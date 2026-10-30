"""Set a field… on columns the server has no batch route for: each ticked
row saved through its own save, all of them one batch (app/batch_edit.py)."""
from __future__ import annotations

from tests.base import AUTOSAVE, AppTestCase, client_for, make_user, one, uniq  # isort: skip


class RowSavesAsOneBatch(AppTestCase):
    def start(self, client, what="Set Note"):
        r = client.post("/batch/start", json={"what": what, "table": "mice"})
        self.assertEqual(r.status_code, 200)
        return r.get_json()["id"]

    def save_note(self, client, mouse_row, note, batch_id=None):
        headers = dict(AUTOSAVE)
        if batch_id:
            headers["X-Batch-Id"] = str(batch_id)
        return client.post(f"/colony/mice/{mouse_row}/update", data={"note": note}, headers=headers)

    def test_the_rows_saved_with_its_number_join_the_batch(self):
        first = self.make_mouse(self.a, self.admin)
        second = self.make_mouse(self.a, self.admin)
        batch = self.start(self.a)
        note = uniq("checked ")
        for row in (first, second):
            self.assertEqual(self.save_note(self.a, row, note, batch).status_code, 200)
        self.assertEqual(self.a.post(f"/batch/{batch}/finish", json={"count": 2}).status_code, 200)
        self.assertEqual(one("select record_count from batches where id=?", batch), 2)
        self.assertEqual(one("select count(*) from audit_log where batch_id_fk=? and table_name='mice'", batch), 2)

    def test_someone_elses_batch_is_not_joined(self):
        batch = self.start(self.a)
        other = client_for(make_user())
        mine = self.make_mouse(other, "")
        self.save_note(other, mine, uniq("mine "), batch)
        self.assertEqual(one("select count(*) from audit_log where batch_id_fk=?", batch), 0)
        self.assertEqual(other.post(f"/batch/{batch}/finish", json={"count": 9}).status_code, 404)
