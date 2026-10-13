"""Samples by type: a tab for each type above the sheet, each showing the
columns used for it (Configure's Used for), the + that adds a type, the
Genotype a sample shows from its source mouse, and Split notes, which moves
"Tissue: Lung; Fixation: 4% PFA" out of the notes into those columns."""
from __future__ import annotations

import json
import re

from tests.base import *  # noqa: F401,F403
from tests.base import one, uniq
from tests.test_inventory import InventoryCase, attrs_of, item, module_id, settings_of


def headers(html: str) -> list[str]:
    """The sheet's column keys, as its sortable headers name them."""
    return re.findall(r'<th class="dt-sortable" data-sort-key="([^"]+)"', html)


def tab_links(html: str) -> list[str]:
    return re.findall(r'data-type-tab="([^"]+)"', html)


def sheet_ids(html: str) -> set[int]:
    return {int(n) for n in re.findall(r'<tr data-id="(\d+)"', html)}


class TypeTabsTest(InventoryCase):
    def setUp(self):
        super().setUp()
        self.key = self.new_module(self.a, "samples")

    def test_a_tab_per_type_with_its_own_columns(self):
        tissue = self.make_item(self.m, self.key, category="tissue", attr_tissue="Lung", attr_fixation="4% PFA")
        dna = self.make_item(self.m, self.key, category="DNA", attr_concentration="52.1")
        html = self.get_ok(self.m, f"/inventory/{self.key}")
        self.assertEqual(tab_links(html)[:5], ["tissue", "blood", "serum", "DNA", "RNA"])
        cols = headers(html)
        for key in ("attr_fixation", "attr_orientation", "attr_a260_280", "attr_concentration", "attr_tissue"):
            self.assertIn(key, cols)
        self.assertEqual(sheet_ids(html), {tissue, dna})

        html = self.get_ok(self.m, f"/inventory/{self.key}?type=tissue")
        self.assertEqual(sheet_ids(html), {tissue})
        cols = headers(html)
        self.assertIn("attr_fixation", cols)
        self.assertIn("attr_tissue", cols)
        self.assertIn("attr_genotype", cols)
        self.assertNotIn("attr_a260_280", cols)
        self.assertNotIn("attr_concentration", cols)
        self.assertRegex(html, r'aria-current="page"\s+data-type-tab="tissue"')

        # Any spelling of the type; a new sample from its tab is of that type.
        html = self.get_ok(self.m, f"/inventory/{self.key}?type=dna")
        self.assertEqual(sheet_ids(html), {dna})
        cols = headers(html)
        self.assertIn("attr_a260_280", cols)
        self.assertNotIn("attr_fixation", cols)
        self.assertIn('"category": "DNA"', html)

        # A type that isn't one is All.
        html = self.get_ok(self.m, f"/inventory/{self.key}?type=nonsense")
        self.assertEqual(sheet_ids(html), {tissue, dna})

    def test_all_greys_a_column_not_used_for_the_row(self):
        tissue = self.make_item(self.m, self.key, category="tissue")
        html = self.get_ok(self.m, f"/inventory/{self.key}")
        row = html[html.index(f'<tr data-id="{tissue}"'):]
        row = row[:row.index("</tr>")]
        self.assertNotIn('name="attr_a260_280"', row)
        self.assertIn('name="attr_fixation"', row)
        self.assertIn("Not used for tissue", row)

    def test_counts_and_boxes_follow_the_tab(self):
        names = {n: uniq(f"{n}Box") for n in ("tissue", "dna", "empty")}
        boxes = {n: self.make_rack(self.a, self.key, name=name) for n, name in names.items()}
        self.make_item(self.m, self.key, category="tissue", rack_id=str(boxes["tissue"]), position="A1")
        self.make_item(self.m, self.key, category="tissue")
        self.make_item(self.m, self.key, category="DNA", rack_id=str(boxes["dna"]), position="A1")
        html = self.get_ok(self.m, f"/inventory/{self.key}?type=tissue")
        self.assertRegex(html, r'data-type-tab="tissue">[^<]*<span class="dt-chip-count">2</span>')
        self.assertRegex(html, r'data-type-tab="DNA">[^<]*<span class="dt-chip-count">1</span>')
        self.assertRegex(html, r'aria-current="page"[^>]*>All|>All <span class="dt-chip-count">3</span>')
        # The boxes holding tissue, and the empty one; not the DNA box.
        self.assertIn(names["tissue"], html)
        self.assertIn(names["empty"], html)
        self.assertNotIn(names["dna"], html)
        self.assertIn(names["dna"], self.get_ok(self.m, f"/inventory/{self.key}"))

    def test_dialog_marks_columns_by_type(self):
        html = self.get_ok(self.m, f"/inventory/{self.key}")
        self.assertIn('data-for-types="dna|rna"', html)
        self.assertIn('data-for-types="tissue"', html)

    def test_a_column_for_another_type_is_not_required(self):
        settings = settings_of(self.key)
        settings["required"] = ["attr_a260_280"]
        from tests.base import execute
        execute("update inventory_modules set settings=? where key=?", json.dumps(settings), self.key)
        r = self.post(self.m, f"/inventory/{self.key}/items/save", data={"id": "", "name": uniq("S"), "category": "tissue"})
        self.assertNoErrors(r)
        r = self.post(self.m, f"/inventory/{self.key}/items/save", data={"id": "", "name": uniq("S"), "category": "DNA"})
        self.assertFlash(r, "260/280", "error")

    def test_without_the_feature_there_are_no_tabs(self):
        key = self.new_module(self.a, "reagents")
        html = self.get_ok(self.m, f"/inventory/{key}")
        self.assertNotIn("data-type-tabs", html)
        self.assertEqual(sheet_ids(self.get_ok(self.m, f"/inventory/{key}?type=buffer")), set())


class AddTypeTest(InventoryCase):
    def setUp(self):
        super().setUp()
        self.key = self.new_module(self.a, "samples")

    def test_anyone_can_add_a_type(self):
        r = self.m.post(f"/inventory/{self.key}/types/add", data={"name": "  slides "})
        self.assertIn("type=slides", r.headers["Location"])
        self.assertEqual(settings_of(self.key)["categories"][-1], "slides")
        html = self.get_ok(self.m, f"/inventory/{self.key}?type=slides")
        self.assertRegex(html, r'aria-current="page"\s+data-type-tab="slides"')
        # Its tab has the columns every type has, not those of some.
        cols = headers(html)
        self.assertIn("attr_genotype", cols)
        self.assertNotIn("attr_fixation", cols)

    def test_one_already_there_opens_its_tab(self):
        before = settings_of(self.key)["categories"]
        r = self.post(self.m, f"/inventory/{self.key}/types/add", data={"name": "dna"})
        self.assertFlash(r, "already")
        self.assertEqual(settings_of(self.key)["categories"], before)

    def test_needs_a_name_and_the_feature(self):
        r = self.post(self.m, f"/inventory/{self.key}/types/add", data={"name": " "})
        self.assertFlash(r, "Type the name", "error")
        key = self.new_module(self.a, "reagents")
        before = settings_of(key)["categories"]
        self.post(self.m, f"/inventory/{key}/types/add", data={"name": "slides"})
        self.assertEqual(settings_of(key)["categories"], before)


class UsedForTest(InventoryCase):
    def test_configure_keeps_used_for_and_follows_a_rename(self):
        key = self.new_module(self.a, "samples")
        html = self.get_ok(self.a, f"/inventory/{key}/configure")
        self.assertIn('value="DNA, RNA"', html)
        mv_settings = settings_of(key)
        mid = module_id(key)
        data = {"label": one("select label from inventory_modules where id=?", mid), "item_noun": "sample", "item_noun_plural": "samples",
                "category_label": "Type", "enabled": "1", "features": mv_settings["features"],
                "statuses_count": "0", "required_sent": "1"}
        cats = mv_settings["categories"]
        data["categories_count"] = str(len(cats))
        for i, c in enumerate(cats):
            data[f"categories_{i}_value"] = "frozen tissue" if c == "tissue" else c
            data[f"categories_{i}_was"] = c
        fields = mv_settings["fields"]
        data["field_count"] = str(len(fields))
        for i, f in enumerate(fields):
            data.update({f"field_{i}_key": f["key"], f"field_{i}_label": f["label"], f"field_{i}_type": f["type"],
                         f"field_{i}_options": ", ".join(f["options"]), f"field_{i}_width": str(f["width"]),
                         f"field_{i}_in_table": "1", f"field_{i}_types": ", ".join(f["types"])})
        # A type that isn't one is dropped; every type is the same as none.
        n = [f["key"] for f in fields].index("condition")
        data[f"field_{n}_types"] = "dna, nonsense"
        n = [f["key"] for f in fields].index("amount")
        data[f"field_{n}_types"] = ", ".join(cats)
        data["statuses_count"] = str(len(mv_settings["statuses"]))
        for i, st in enumerate(mv_settings["statuses"]):
            data[f"statuses_{i}_value"] = data[f"statuses_{i}_was"] = st
        self.post(self.a, f"/inventory/{key}/configure", data=data)
        saved = {f["key"]: f["types"] for f in settings_of(key)["fields"]}
        self.assertEqual(saved["fixation"], ["frozen tissue"])
        self.assertEqual(saved["condition"], ["DNA"])
        self.assertEqual(saved["amount"], [])
        self.assertEqual(saved["a260_280"], ["DNA", "RNA"])


class GenotypeFromMouseTest(InventoryCase):
    def test_an_empty_genotype_shows_the_source_mouses(self):
        from tests.base import execute
        key = self.new_module(self.a, "samples")
        mouse = self.make_mouse(self.m, self.member)
        mouse_id = str(one("select mouse_id from mice where id=?", mouse))
        execute("update mice set genotype=? where id=?", "Vglut2-Cre/+;Ai9/+", mouse)
        sample = self.make_item(self.m, key, category="tissue", attr_source_kind="mouse", attr_source_ref=mouse_id)
        own = self.make_item(self.m, key, category="tissue", attr_source_kind="mouse", attr_source_ref=mouse_id,
                             attr_genotype="WT")
        html = self.get_ok(self.m, f"/inventory/{key}?type=tissue")
        self.assertIn('placeholder="Vglut2-Cre/+;Ai9/+" class="from-colony"', html)
        self.assertIn('data-mouse-genotype="Vglut2-Cre/+;Ai9/+"', html)
        self.assertEqual(attrs_of(sample).get("genotype", ""), "")
        # A sample with its own genotype keeps it.
        row = html[html.index(f'<tr data-id="{own}"'):]
        row = row[:row.index("</tr>")]
        self.assertIn('value="WT"', row)
        self.assertNotIn("from-colony", row)
        # Re-genotyped in the colony: the sample follows.
        execute("update mice set genotype=? where id=?", "Vglut2-Cre/Cre", mouse)
        self.assertIn('placeholder="Vglut2-Cre/Cre"', self.get_ok(self.m, f"/inventory/{key}"))


class MigrationTest(InventoryCase):
    """Revision 0041 gives Samples made before it the tabs and the columns."""

    def run_migration(self):
        import importlib
        from unittest import mock
        from app.db import engine
        migration = importlib.import_module("migrations.versions.0041_sample_types")
        with engine.begin() as conn:
            with mock.patch.object(migration, "op", mock.Mock(get_bind=lambda: conn)), \
                    mock.patch.object(migration, "has_table", lambda name: True):
                migration.upgrade()

    def as_before(self, key: str, categories: list[str], extra: list[dict] = ()) -> None:
        """The database as Samples were made before 0041."""
        from tests.base import execute
        s = settings_of(key)
        s["features"] = ["storage", "sharing"]
        s["categories"] = categories
        s["fields"] = [{k: v for k, v in f.items() if k != "types"} for f in s["fields"]
                       if f["key"] not in ("tissue", "genotype", "condition", "fixation", "orientation")] + list(extra)
        execute("update inventory_modules set settings=? where key=?", json.dumps(s), key)

    def test_an_older_samples_database_gets_tabs_and_columns(self):
        key = self.new_module(self.a, "samples")
        self.as_before(key, ["tissue", "blood", "dna", "RNA"], [{"key": "organ", "label": "Tissue", "type": "text"}])
        self.run_migration()
        s = settings_of(key)
        self.assertIn("tabs", s["features"])
        fields = {f["key"]: f for f in s["fields"]}
        # The lab's spelling; protein and serum, which it hasn't, left out.
        self.assertEqual(fields["a260_280"]["types"], ["dna", "RNA"])
        self.assertEqual(fields["volume_ul"]["types"], ["blood", "dna", "RNA"])
        # Its own Tissue column stays, with no second one.
        self.assertNotIn("tissue", fields)
        self.assertEqual(fields["fixation"]["types"], ["tissue"])
        keys = [f["key"] for f in s["fields"]]
        self.assertEqual(keys[keys.index("collected_on") + 1:keys.index("collected_on") + 5],
                         ["genotype", "condition", "fixation", "orientation"])
        # Again: nothing changes.
        self.run_migration()
        self.assertEqual(settings_of(key), s)

    def test_types_a_lab_set_are_kept(self):
        key = self.new_module(self.a, "samples")
        from tests.base import execute
        s = settings_of(key)
        for f in s["fields"]:
            if f["key"] == "a260_280":
                f["types"] = ["DNA"]
        execute("update inventory_modules set settings=? where key=?", json.dumps(s), key)
        self.run_migration()
        self.assertEqual(next(f for f in settings_of(key)["fields"] if f["key"] == "a260_280")["types"], ["DNA"])


class SplitNotesTest(InventoryCase):
    def setUp(self):
        super().setUp()
        self.key = self.new_module(self.a, "samples")

    def split(self, client, *ids):
        return self.post(client, f"/inventory/{self.key}/items/bulk",
                         data={"action": "split_notes", "selected_ids": [str(i) for i in ids]})

    def test_the_old_databases_notes_go_into_columns(self):
        note = ("Tissue: Lung; Genotype: WT; Fixation: 4% pfa; Orientation: Sagittal; "
                "control 6OHDA acid 2w; Entered in old DB 2025-07-03")
        sid = self.make_item(self.m, self.key, notes=note)
        r = self.split(self.m, sid)
        self.assertFlash(r, "Moved what the notes said", "success")
        a = attrs_of(sid)
        self.assertEqual((a["tissue"], a["genotype"], a["fixation"], a["orientation"]),
                         ("Lung", "WT", "4% PFA", "sagittal"))
        self.assertEqual(item(sid)["notes"], "control 6OHDA acid 2w; Entered in old DB 2025-07-03")
        # Fixation is for tissue only, so it is tissue.
        self.assertEqual(item(sid)["category"], "tissue")
        # Undone from Batch history as one batch.
        from tests.test_inventory import newest_batch
        self.assertTrue(newest_batch(self.member)[1].startswith("split_notes"))

    def test_a_genotype_keeps_its_loci(self):
        sid = self.make_item(self.m, self.key, notes="Tissue: DRG; Genotype: Vglut2-Cre/+;Ai9/+; Fixation: 4% PFA; "
                                                      "Entered in old DB 2025-06-09; Line: Cre/+; +/-")
        self.split(self.m, sid)
        self.assertEqual(attrs_of(sid)["genotype"], "Vglut2-Cre/+;Ai9/+")
        # One that stays in the notes keeps its loci too.
        self.assertEqual(item(sid)["notes"], "Entered in old DB 2025-06-09; Line: Cre/+;+/-")

    def test_what_it_leaves(self):
        sid = self.make_item(self.m, self.key, category="DNA", attr_tissue="Liver",
                             notes="Tissue: Lung; Conc.: about 50; Collected: 2025-06-09; Box: B2; Time: 10:30")
        r = self.split(self.m, sid)
        a = attrs_of(sid)
        # A column holding something else, a number that isn't one, and a
        # header that names no column stay in the notes.
        self.assertEqual(a["tissue"], "Liver")
        self.assertEqual(a["collected_on"], "2025-06-09")
        self.assertEqual(item(sid)["notes"], "Tissue: Lung; Conc.: about 50; Box: B2; Time: 10:30")
        self.assertEqual(item(sid)["category"], "DNA")
        self.assertFlash(r, "“Box”, “Time”", "info")

    def test_nothing_to_split(self):
        sid = self.make_item(self.m, self.key, notes="just a note")
        r = self.split(self.m, sid)
        self.assertFlash(r, "nothing in its notes", "error")
        self.assertEqual(item(sid)["notes"], "just a note")

    def test_someone_elses_personal_sample_is_left_alone(self):
        sid = self.make_item(self.o, self.key, notes="Tissue: Lung")
        self.split(self.m, sid)
        self.assertEqual(item(sid)["notes"], "Tissue: Lung")
