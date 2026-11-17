"""Primers & oligos (app/inventory.py preset "primers"): length, GC and Tm
follow the sequence however it is saved, and Add primer pair makes both."""
from __future__ import annotations

from tests.base import flashes, one, rows, uniq
from tests.test_inventory import InventoryCase, attrs_of, item, newest_batch
from app.inventory_service import primer_numbers, primer_tm  # noqa: E402


class PrimerMaths(InventoryCase):
    def test_length_gc_and_tm_from_a_sequence(self):
        got = primer_numbers("5'-agc gga taa caa ttt cac aca gga-3'")    # M13 reverse
        self.assertEqual((got["length"], got["gc"]), ("24", "41.7"))
        self.assertAlmostEqual(float(got["tm"]), 58.3, delta=0.2)
        # Degenerate or very short: length and GC, no Tm.
        self.assertNotIn("tm", primer_numbers("ACGTNACGTACGTT"))
        self.assertIsNone(primer_tm("ACGTAC"))
        self.assertEqual(primer_numbers(""), {})


class PrimerRecords(InventoryCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "primers")

    def test_saving_a_sequence_works_out_its_numbers_in_the_dialog_and_the_sheet(self):
        pid = self.make_item(self.m, self.key, uniq("T7 "), attr_sequence="TAATACGACTCACTATAGGG")
        self.assertEqual((attrs_of(pid)["length"], attrs_of(pid)["gc"]), ("20", "40.0"))
        r = self.autosave(self.m, f"/inventory/{self.key}/items/{pid}/update", {"attr_sequence": "GTAAAACGACGGCCAGT"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(attrs_of(pid)["length"], "17")
        self.assertEqual(r.get_json()["row"]["values"]["attr_length"], "17")   # the sheet shows it at once
        # Typing a Tm by hand doesn't stick while the sequence says otherwise.
        self.autosave(self.m, f"/inventory/{self.key}/items/{pid}/update", {"attr_tm": "99"})
        self.assertNotEqual(attrs_of(pid)["tm"], "99")

    def test_add_primer_pair_makes_both_linked_in_one_batch_side_by_side(self):
        box = self.make_rack(self.m, self.key, rows=3, cols=3)
        base = uniq("GAPDH qPCR ")
        r = self.post(self.m, f"/inventory/{self.key}/items/pair", data={
            "name": base, "forward": "GAAGGTGAAGGTCGGAGTCA", "reverse": "TTGAGGTCAATGAAGGGGTC",
            "category": "qPCR", "attr_target": "GAPDH", "rack_id": str(box)})
        self.assertFlash(r, f"Added {base}-F", "success")
        made = rows("select id, name from inventory_items where name like ? order by name", f"{base}-%")
        self.assertEqual([n for _i, n in made], [f"{base}-F", f"{base}-R"])
        fwd, rev = (attrs_of(i) for i, _n in made)
        self.assertEqual((fwd["direction"], fwd["pair"], rev["direction"], rev["pair"]),
                         ("forward", f"{base}-R", "reverse", f"{base}-F"))
        self.assertEqual((fwd["target"], fwd["length"]), ("GAPDH", "20"))
        cells = sorted((item(i)["rack_row"], item(i)["rack_col"]) for i, _n in made)
        self.assertEqual(cells, [(1, 1), (1, 2)])
        self.assertIn("primer pair", newest_batch(self.member)[1])

    def test_a_pair_without_both_sequences_makes_nothing(self):
        base = uniq("Actb ")
        r = self.post(self.m, f"/inventory/{self.key}/items/pair", data={"name": base, "forward": "ACGT"})
        self.assertFlash(r, "both sequences", "error")
        self.assertEqual(one("select count(*) from inventory_items where name like ?", f"{base}%"), 0)


class CellLines(InventoryCase):
    def test_a_cell_line_database_has_its_columns_and_a_vial_is_its_record(self):
        key = self.new_module(self.a, "cell_lines")
        html = self.get_ok(self.a, f"/inventory/{key}")
        for label in ("Passage", "Mycoplasma", "Cell line"):
            self.assertIn(label, html)
        self.assertIn("New vial", html)


def a_sequence(n: int = 22) -> str:
    import random
    return "".join(random.choice("ACGT") for _ in range(n))


class SameSequence(InventoryCase):
    """#163: a primer the lab already had made is pointed out, not refused,
    wherever its sequence is typed: the dialog as you type, on saving, the
    sheet, and Add primer pair."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "primers")

    def test_the_dialog_finds_it_as_you_type_whatever_the_case_and_spaces(self):
        seq = a_sequence()
        name = uniq("Actb-F ")
        pid = self.make_item(self.m, self.key, name, attr_sequence=seq)
        typed = "5'-" + " ".join(seq[i:i + 5] for i in range(0, len(seq), 5)).lower() + "-3'"
        got = self.m.get(f"/inventory/{self.key}/primers/same-sequence", query_string={"sequence": typed}).get_json()
        self.assertEqual([(p["id"], p["name"]) for p in got["primers"]], [(pid, name)])
        self.assertIn(f"open={pid}", got["primers"][0]["url"])
        # Not the record being edited, nor a sequence that differs by a base, nor a short one.
        self.assertEqual(self.m.get(f"/inventory/{self.key}/primers/same-sequence",
                                    query_string={"sequence": seq, "exclude": pid}).get_json()["primers"], [])
        self.assertEqual(self.m.get(f"/inventory/{self.key}/primers/same-sequence",
                                    query_string={"sequence": seq[:-1] + ("A" if seq[-1] != "A" else "C")}).get_json()["primers"], [])
        self.assertEqual(self.m.get(f"/inventory/{self.key}/primers/same-sequence",
                                    query_string={"sequence": seq[:6]}).get_json()["primers"], [])

    def test_saving_a_second_one_says_so_and_still_saves_it(self):
        seq = a_sequence()
        first = uniq("Gapdh-R ")
        self.make_item(self.m, self.key, first, attr_sequence=seq)
        second = uniq("Gapdh-R again ")
        r = self.post(self.m, f"/inventory/{self.key}/items/save", data={"id": "", "name": second, "attr_sequence": seq})
        self.assertFlash(r, "already has this sequence", "warning")
        self.assertFlash(r, first, "warning")
        self.assertEqual(one("select count(*) from inventory_items where name=?", second), 1)
        # Saving it again unchanged says nothing more.
        again = one("select id from inventory_items where name=?", second)
        r = self.post(self.m, f"/inventory/{self.key}/items/save", data={"id": str(again), "name": second, "attr_sequence": seq})
        self.assertFalse(any("already has this sequence" in text for _k, text in flashes(r)))

    def test_the_sheet_and_add_primer_pair_say_so_too(self):
        seq = a_sequence()
        first = uniq("Tubb3 ")
        self.make_item(self.m, self.key, first, attr_sequence=seq)
        other = self.make_item(self.m, self.key, uniq("Blank "))
        r = self.autosave(self.m, f"/inventory/{self.key}/items/{other}/update", {"attr_sequence": seq})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(any(first in n for n in r.get_json().get("notes", [])), r.get_json())
        base = uniq("Pair ")
        r = self.post(self.m, f"/inventory/{self.key}/items/pair",
                      data={"name": base, "forward": seq.lower(), "reverse": a_sequence()})
        self.assertFlash(r, f"{base}-F: The lab already has this sequence", "warning")
        self.assertFalse(any(f"{base}-R:" in text for _k, text in flashes(r)))
