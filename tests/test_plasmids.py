"""Plasmids: permissions, sequences (parse, upload, edit, editor saves),
boxes on the shared storage grid, the one-off migration from free-text box
names, moves, Add many, and bulk actions."""
from __future__ import annotations

import io
import json
import re
import struct
import unittest

from tests.base import AppTestCase, GRID_NAMING, count, errors, execute, one, row, rows, uniq

from app import sequence_parser as sp
from app import services


GB = """LOCUS       pQA1        60 bp    DNA     circular SYN 01-JAN-2026
FEATURES             Location/Qualifiers
     promoter        1..20
                     /label="CMV"
     CDS             complement(21..50)
                     /label="GFPfrag"
     misc_feature    join(55..60,1..5)
                     /label="wrap"
ORIGIN
        1 atgcgtacgt tagcatgcat cgatcgatcg atcgatgcta gctagctagc atgcatgcrr
//
"""
NUMBERS_NAMING = {"naming_mode": "grid", "naming_rows": "numbers", "naming_cols": "numbers",
                  "naming_order": "row_col", "naming_separator": "-", "naming_start": "1"}


def plasmid(row_id: int, cols: str = "*"):
    return row(f"select {cols} from plasmids where id=?", row_id)


def by_name(name: str, cols: str = "id"):
    """Rows of the plasmids called `name`, in number order."""
    return rows(f"select {cols} from plasmids where name=? order by plasmid_id", name)


def newest_batch(actor: str):
    """(id, description, target_table, record_count, action) of `actor`'s newest batch."""
    return row("select id, description, target_table, record_count, action from batches "
               "where actor=? order by id desc limit 1", actor)


def grid_of(html: str) -> dict:
    m = re.search(r'<script type="application/json" data-rack-data>(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def gb_upload(text: str = GB, filename: str = "p.gb"):
    return {"file": (io.BytesIO(text.encode()), filename)}


def snapgene_bytes(sequence: bytes, circular: bool, features_xml: str) -> bytes:
    def chunk(kind: int, payload: bytes) -> bytes:
        return bytes([kind]) + struct.pack(">I", len(payload)) + payload
    return (chunk(0x09, b"SnapGene\x00\x01\x00\x0f\x00\x14")
            + chunk(0x00, bytes([1 if circular else 0]) + sequence)
            + chunk(0x0A, features_xml.encode()))


# ====================================================================== parser

def page(row_id) -> str:
    """The plasmid's page: /plasmid/<its number>."""
    return f"/plasmid/{one('select plasmid_id from plasmids where id=?', row_id)}"


class SequenceParserTests(AppTestCase):
    def test_clean_bases_keeps_every_iupac_ambiguity_code(self):
        self.assertEqual(sp.clean_bases("acgt uryk mswb dhvn 12 xz-*"), "ACGTURYKMSWBDHVN")

    def test_looks_like_bases_accepts_numbered_listing_and_refuses_prose(self):
        self.assertTrue(sp.looks_like_bases("1 acgtnnRY\n 11 ACGT"))
        self.assertFalse(sp.looks_like_bases("Dear colleague, here is the plasmid"))
        self.assertFalse(sp.looks_like_bases("  12 \n"))

    def test_genbank_origin_spanning_join_keeps_start_after_end(self):
        parsed = sp.parse_genbank(GB)
        feats = {f["name"]: f for f in parsed["features"]}
        self.assertEqual((parsed["name"], parsed["is_circular"], len(parsed["sequence"])), ("pQA1", True, 60))
        self.assertTrue(parsed["sequence"].endswith("RR"))
        self.assertEqual((feats["wrap"]["start"], feats["wrap"]["end"]), (54, 4))
        self.assertEqual((feats["CMV"]["start"], feats["CMV"]["end"], feats["CMV"]["direction"]), (0, 19, 1))
        self.assertEqual(feats["GFPfrag"]["direction"], -1)

    def test_forward_parts_span_their_outer_bounds(self):
        self.assertEqual(sp.span_of_parts([(2, 5), (9, 12)]), (2, 12))
        self.assertEqual(sp.span_of_parts([(50, 59), (0, 4)]), (50, 4))

    def test_fasta_keeps_ambiguity_codes_and_refuses_protein(self):
        self.assertEqual(sp.parse_fasta(">pX test\nATGCRYKMNN\nacgt\n")["sequence"], "ATGCRYKMNNACGT")
        self.assertIsNone(sp.parse_fasta(">prot\nMKLEPQFFLIVE\n"))

    def test_binary_or_non_snapgene_dna_file_is_not_a_sequence(self):
        self.assertIsNone(sp.parse_sequence_bytes(b"\x00\x01garbage", "x.gb"))
        self.assertIsNone(sp.parse_sequence_bytes(b"ACGTACGT", "looks-like-bases.dna"))
        self.assertIsNone(sp.parse_sequence_bytes(b"", "empty.fa"))

    def test_snapgene_file_parses_topology_and_origin_spanning_segment(self):
        xml = ('<Features><Feature name="wrapper" type="misc_feature" directionality="2">'
               '<Segment range="9-2" color="#ff0000"/></Feature></Features>')
        parsed = sp.parse_sequence_bytes(snapgene_bytes(b"ACGTRYACGT", True, xml), "p.dna")
        self.assertEqual((parsed["format"], parsed["sequence"], parsed["is_circular"]), ("snapgene", "ACGTRYACGT", True))
        f = parsed["features"][0]
        self.assertEqual((f["name"], f["start"], f["end"], f["direction"], f["color"]), ("wrapper", 8, 1, -1, "#ff0000"))


class ImportFidelityTests(AppTestCase):
    """What a sequence file says survives the import: its name, its topology,
    every qualifier, and which of several records was kept."""

    def test_a_name_run_into_the_length_keeps_both_and_the_topology(self):
        joined = GB.replace("LOCUS       pQA1        60 bp", "LOCUS       pQA1-a-long-name60 bp", 1)
        parsed = sp.parse_genbank(joined)
        self.assertEqual((parsed["name"], parsed["is_circular"], len(parsed["sequence"])), ("pQA1-a-long-name", True, 60))

    def test_a_byte_order_mark_or_lines_before_locus_still_read_as_genbank(self):
        for text in ("\ufeff" + GB, "# exported by our old tool\n\n" + GB):
            parsed = sp.parse_sequence_bytes(text.encode("utf-8"), "p.gb")
            self.assertEqual((parsed["format"], parsed["name"], parsed["is_circular"]), ("genbank", "pQA1", True))

    def test_several_fasta_records_keep_the_first_and_say_how_many(self):
        parsed = sp.parse_fasta(">insertA\nACGTACGTAC\n>insertB\nGGGGCCCCTT\n")
        self.assertEqual((parsed["name"], parsed["sequence"], parsed["records"]), ("insertA", "ACGTACGTAC", 2))
        self.assertEqual(sp.parse_fasta(">one\nACGT\n")["records"], 1)

    def test_every_genbank_qualifier_is_kept_in_full(self):
        # GenBank wraps long text at a space; the two lines join with one.
        first, rest = "from pEGFP-N1, see Cormack et al. 1996", " ".join(["linker"] * 70)
        note = f"{first} {rest}"
        gb = GB.replace('                     /label="GFPfrag"\n',
                        '                     /label="GFPfrag"\n'
                        f'                     /note="{first}\n'
                        f'                     {rest}"\n'
                        '                     /codon_start=1\n'
                        '                     /translation="MVSKGEELFT\n'
                        '                     GVVPILVELD"\n'
                        '                     /note="second note"\n', 1)
        frag = [f for f in sp.parse_genbank(gb)["features"] if f["name"] == "GFPfrag"][0]
        self.assertEqual(frag["notes"]["label"], ["GFPfrag"])
        self.assertEqual(frag["notes"]["note"], [note, "second note"])
        self.assertEqual(frag["notes"]["codon_start"], ["1"])
        self.assertEqual(frag["notes"]["translation"], ["MVSKGEELFTGVVPILVELD"])

    def test_snapgene_name_description_and_qualifiers_come_through(self):
        def chunk(kind: int, payload: bytes) -> bytes:
            return bytes([kind]) + struct.pack(">I", len(payload)) + payload
        features = ('<Features><Feature name="EGFP" type="CDS" directionality="1"><Segment range="1-6"/>'
                    '<Q name="note"><V text="&lt;html&gt;mEGFP, A206K&lt;/html&gt;"/></Q>'
                    '<Q name="codon_start"><V int="1"/></Q></Feature></Features>')
        notes = ("<Notes><CustomMapLabel>pCAG-GFP</CustomMapLabel><UseCustomMapLabel>1</UseCustomMapLabel>"
                 "<Description>&lt;html&gt;&lt;body&gt;CAG-driven GFP&lt;/body&gt;&lt;/html&gt;</Description></Notes>")
        raw = (chunk(0x09, b"SnapGene\x00\x01\x00\x0f\x00\x14") + chunk(0x00, b"\x01ACGTACGTAC")
               + chunk(0x0A, features.encode()) + chunk(0x06, notes.encode()))
        parsed = sp.parse_sequence_bytes(raw, "p.dna")
        self.assertEqual((parsed["name"], parsed["description"]), ("pCAG-GFP", "CAG-driven GFP"))
        self.assertEqual(parsed["features"][0]["notes"], {"note": ["mEGFP, A206K"], "codon_start": ["1"]})

    def test_a_snapgene_file_names_a_new_plasmid_and_fills_its_notes(self):
        def chunk(kind: int, payload: bytes) -> bytes:
            return bytes([kind]) + struct.pack(">I", len(payload)) + payload
        label = uniq("pSG")
        raw = (chunk(0x09, b"SnapGene\x00\x01\x00\x0f\x00\x14") + chunk(0x00, b"\x01ACGTACGTAC")
               + chunk(0x06, f"<Notes><CustomMapLabel>{label}</CustomMapLabel><Description>From Addgene</Description></Notes>".encode()))
        self.post(self.a, "/plasmids", data={"name": "", "sequence_file": (io.BytesIO(raw), "p.dna")},
                  content_type="multipart/form-data")
        self.assertEqual(by_name(label, "full_sequence, notes, sequence_format"), [("ACGTACGTAC", "From Addgene", "snapgene")])

    def test_uploading_several_records_says_only_the_first_was_kept(self):
        rid = self.make_plasmid(self.a, sequence_text="ACGTACGTACGT")
        r = self.post(self.a, f"/plasmids/{rid}/upload-sequence",
                      data={"file": (io.BytesIO(b">insertA\nACGTAC\n>insertB\nGGGGCC\n"), "two.fa")},
                      content_type="multipart/form-data")
        self.assertFlash(r, "The file holds 2 sequences; only the first, insertA, was kept.", "warning")
        self.assertEqual(plasmid(rid, "full_sequence")[0], "ACGTAC")

    def test_the_editor_cannot_store_digits_as_bases(self):
        rid = self.make_plasmid(self.a, sequence_text="ACGTACGTACGT")
        r = self.a.post(f"/plasmids/{rid}/sequence-save", json={"sequenceData": {"sequence": "ACGT 123 acgt"}})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(plasmid(rid, "full_sequence")[0], "ACGTACGTACGT")

    def test_a_long_qualifier_survives_an_editor_save(self):
        rid = self.make_plasmid(self.a, sequence_text="ACGTACGTACGT")
        long_note = "y" * 3000
        self.a.post(f"/plasmids/{rid}/sequence-save", json={"sequenceData": {"sequence": "ACGTACGTACGT", "features": [
            {"name": "f", "start": 0, "end": 3, "forward": True, "notes": {"note": [long_note]}}]}})
        stored = json.loads(plasmid(rid, "features_json")[0])
        self.assertEqual(stored[0]["notes"]["note"], [long_note])


# ====================================================================== create

class CreatePlasmidTests(AppTestCase):
    def test_genbank_upload_on_create_names_it_from_locus_and_keeps_features(self):
        backbone = uniq("bb")
        r = self.post(self.a, "/plasmids", data={"name": "", "backbone": backbone,
                                                 "sequence_file": (io.BytesIO(GB.encode()), "pqa1.gb")},
                      content_type="multipart/form-data")
        rid = one("select id from plasmids where backbone=?", backbone)
        name, seq, circ, feats_json, fmt = plasmid(rid, "name, full_sequence, is_circular, features_json, sequence_format")
        self.assertEqual((name, len(seq), bool(circ), fmt), ("pQA1", 60, True, "genbank"))
        wrap = [f for f in json.loads(feats_json) if f["name"] == "wrap"][0]
        self.assertEqual((wrap["start"], wrap["end"]), (54, 4))
        self.assertFlash(r, "GENBANK sequence (60 bp · 3 features)", "success")

    def test_pasted_fasta_keeps_iupac_letters(self):
        name = uniq("pFA")
        self.post(self.a, "/plasmids", data={"name": name, "sequence_text": ">pFA test\nATGCRYKMNNacgt\n"})
        self.assertEqual(by_name(name, "full_sequence, sequence_format"), [("ATGCRYKMNNACGT", "fasta")])

    def test_garbage_file_on_create_saves_plasmid_without_sequence_and_warns(self):
        name = uniq("BadSeq")
        r = self.post(self.a, "/plasmids", data={"name": name, "sequence_file": (io.BytesIO(b"\x00\x01junk"), "x.dna")},
                      content_type="multipart/form-data")
        self.assertEqual(by_name(name, "full_sequence"), [("",)])
        self.assertFlash(r, "x.dna is not a sequence file", "warning")

    def test_prose_pasted_as_sequence_is_not_mined_for_letters(self):
        name = uniq("BadText")
        r = self.post(self.a, "/plasmids", data={"name": name, "sequence_text": "Dear colleague, here is the plasmid"})
        self.assertEqual(by_name(name, "full_sequence"), [("",)])
        self.assertFlash(r, "not a sequence", "warning")

    def test_invalid_number_or_blank_name_creates_nothing(self):
        before = count("plasmids")
        r = self.post(self.a, "/plasmids", data={"plasmid_id": "0", "name": uniq("x")})
        self.assertFlash(r, "not a plasmid number", "error")
        r = self.post(self.a, "/plasmids", data={"plasmid_id": "abc", "name": uniq("x")})
        self.assertFlash(r, "not a plasmid number", "error")
        r = self.post(self.a, "/plasmids", data={"name": "   "})
        self.assertFlash(r, "Give the plasmid a name", "error")
        self.assertEqual(count("plasmids"), before)

    def test_taken_number_gets_the_next_free_one_and_says_so(self):
        taken = one("select plasmid_id from plasmids where id=?", self.make_plasmid(self.a))
        name = uniq("Racer")
        r = self.post(self.a, "/plasmids", data={"plasmid_id": str(taken), "name": name})
        got = by_name(name, "plasmid_id")[0][0]
        self.assertGreater(got, taken)
        self.assertFlash(r, f"Plasmid #{taken} was taken by the time you saved, so this one is #{got}", "warning")

    def test_create_into_a_taken_cell_leaves_it_in_the_box_unplaced(self):
        box = self.make_box(self.a)
        first = self.make_plasmid(self.a, box_id=box, position="B2")
        name = uniq("Clash")
        r = self.post(self.a, "/plasmids", data={"name": name, "box_id": str(box), "position": "b2"})
        self.assertEqual(plasmid(first, "box_row, box_col"), (1, 1))
        self.assertEqual(by_name(name, "box_id_fk, box_row, box_col"), [(box, None, None)])
        self.assertFlash(r, "already holds plasmid", "warning")

    def test_create_outside_the_box_is_reported(self):
        box = self.make_box(self.a, rows=4, cols=4)
        name = uniq("Far")
        r = self.post(self.a, "/plasmids", data={"name": name, "box_id": str(box), "position": "E1"})
        self.assertEqual(by_name(name, "box_id_fk, box_row"), [(box, None)])
        self.assertFlash(r, "outside the box", "warning")

    def test_moving_into_another_box_with_no_position_takes_its_next_free_cell(self):
        old_box, new_box = self.make_box(self.a), self.make_box(self.a)
        self.make_plasmid(self.a, box_id=new_box, position="A1")
        pid = self.make_plasmid(self.a, box_id=old_box, position="C3")
        self.post(self.a, f"/plasmids/{pid}/update", data={"box_id": str(new_box), "position": ""})
        self.assertEqual(plasmid(pid, "box_id_fk, box_row, box_col"), (new_box, 0, 1))

    def test_a_miniprep_s_concentration_and_purity_are_numbers_on_the_tube(self):
        pid = self.make_plasmid(self.a, concentration="412", a260_280="1,86")
        self.assertEqual(plasmid(pid, "concentration, a260_280"), ("412", "1.86"))
        r = self.a.post(f"/plasmids/{pid}/update", data={"concentration": "lots"}, headers={"X-Autosave": "1"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("is a number", r.get_json()["error"])
        self.assertEqual(plasmid(pid, "concentration"), ("412",))
        self.a.post(f"/plasmids/{pid}/update", data={"concentration": "388.5"}, headers={"X-Autosave": "1"})
        self.assertEqual(plasmid(pid, "concentration"), ("388.5",))


# ================================================================ permissions

class AddressTests(AppTestCase):
    def test_a_plasmid_s_page_is_at_its_number_and_old_addresses_lead_there(self):
        number = (one("select max(plasmid_id) from plasmids") or 0) + 500    # a number far from its row id
        rid = self.make_plasmid(self.m, plasmid_id=str(number))
        self.assertEqual(one("select plasmid_id from plasmids where id=?", rid), number)
        html = self.get_ok(self.m, f"/plasmid/{number}")
        self.assertIn(f"#{number}", html)
        r = self.m.get(f"/plasmids/{rid}")
        self.assertEqual((r.status_code, r.headers["Location"].split("?")[0][-len(f"/plasmid/{number}"):]),
                         (301, f"/plasmid/{number}"))
        self.assertIn(f'href="/plasmid/{number}"', self.get_ok(self.m, "/plasmids"))
        self.assertEqual(self.m.get("/plasmid/987654321").status_code, 302)   # not there: back to the list


class PermissionTests(AppTestCase):
    """The member owns a plasmid; `other` (a member too) may only read it."""

    def setUp(self):
        self.box = self.make_box(self.m)
        self.rid = self.make_plasmid(self.m, box_id=self.box, position="A1", sequence_text="ACGTACGTAC")
        self.pid = plasmid(self.rid, "plasmid_id")[0]
        self.snapshot = plasmid(self.rid, "name, full_sequence, box_id_fk, box_row, box_col, owner, resistance")

    def assertUntouched(self):
        self.assertEqual(plasmid(self.rid, "name, full_sequence, box_id_fk, box_row, box_col, owner, resistance"),
                         self.snapshot)

    def test_member_autosave_on_someone_elses_plasmid_is_403(self):
        r = self.autosave(self.o, f"/plasmids/{self.rid}/update", {"name": "hacked"})
        self.assertEqual(r.status_code, 403)
        self.assertIn(f"belongs to {self.member}", r.get_json()["error"])
        self.assertUntouched()

    def test_member_form_edit_on_someone_elses_plasmid_is_refused(self):
        r = self.post(self.o, f"/plasmids/{self.rid}/update", {"name": "hacked", "resistance": "Kan"})
        self.assertFlash(r, f"belongs to {self.member}", "error")
        self.assertUntouched()

    def test_member_cannot_delete_someone_elses_plasmid(self):
        r = self.post(self.o, f"/plasmids/{self.rid}/delete")
        self.assertFlash(r, "belongs to", "error")
        self.assertEqual(count("plasmids", "id=?", self.rid), 1)

    def test_a_lab_common_plasmid_is_anyones_to_edit_but_its_owners_to_give_away_or_delete(self):
        self.assertSaved(self.autosave(self.m, f"/plasmids/{self.rid}/update", {"is_shared": "1"}))
        self.assertSaved(self.autosave(self.o, f"/plasmids/{self.rid}/update", {"notes": "miniprep 2 in box B"}))
        self.assertEqual(plasmid(self.rid, "notes"), ("miniprep 2 in box B",))
        r = self.autosave(self.o, f"/plasmids/{self.rid}/update", {"owner": self.other})
        self.assertEqual(r.status_code, 403)
        self.assertIn("lab common", r.get_json()["error"])
        self.assertEqual(self.autosave(self.o, f"/plasmids/{self.rid}/update", {"is_shared": "0"}).status_code, 403)
        self.assertFlash(self.post(self.o, f"/plasmids/{self.rid}/delete"), "lab common", "error")
        self.assertEqual(count("plasmids", "id=?", self.rid), 1)
        self.assertEqual(plasmid(self.rid, "owner"), (self.member,))

    def test_member_cannot_move_someone_elses_plasmid(self):
        mine = self.make_box(self.o)
        r = self.o.post(f"/plasmids/{self.rid}/move", data={"box_id": str(mine), "box_row": "0", "box_col": "0"})
        self.assertEqual(r.status_code, 403)
        self.assertUntouched()

    def test_member_cannot_swap_own_plasmid_with_someone_elses(self):
        own = self.make_plasmid(self.o, box_id=self.box, position="B1")
        r = self.o.post(f"/plasmids/{own}/move", data={"box_id": str(self.box), "box_row": "0", "box_col": "0"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("may not move", r.get_json()["error"])
        self.assertEqual(plasmid(own, "box_row, box_col"), (1, 0))
        self.assertUntouched()

    def test_member_cannot_change_someone_elses_sequence(self):
        r = self.o.post(f"/plasmids/{self.rid}/sequence-save", json={"sequenceData": {"sequence": "GGGG"}})
        self.assertEqual(r.status_code, 403)
        for url, data in ((f"/plasmids/{self.rid}/upload-sequence", gb_upload()),
                          (f"/plasmids/{self.rid}/edit-sequence", {"sequence_text": "GGGG"}),
                          (f"/plasmids/{self.rid}/clear-sequence", {"confirm": "1"})):
            r = self.post(self.o, url, data, content_type="multipart/form-data")
            self.assertFlash(r, "belongs to", "error")
        self.assertUntouched()

    def test_member_may_duplicate_someone_elses_plasmid_and_owns_the_copy(self):
        r = self.post(self.o, f"/plasmids/{self.rid}/duplicate")
        copy = row("select plasmid_id, owner, full_sequence, box_id_fk, box_row from plasmids where name=?",
                   f"{self.snapshot[0]} (copy)")
        self.assertEqual(copy[1:], (self.other, "ACGTACGTAC", None, None))
        self.assertFlash(r, f"Duplicated plasmid #{self.pid} as #{copy[0]}", "success")
        self.assertUntouched()

    def test_owner_can_edit_and_delete_own_plasmid(self):
        self.assertSaved(self.autosave(self.m, f"/plasmids/{self.rid}/update", {"resistance": "Kan"}))
        self.assertEqual(plasmid(self.rid, "resistance")[0], "Kan")
        self.post(self.m, f"/plasmids/{self.rid}/delete")
        self.assertEqual(count("plasmids", "id=?", self.rid), 0)

    def test_admin_can_edit_and_move_anyones_plasmid(self):
        self.assertSaved(self.autosave(self.a, f"/plasmids/{self.rid}/update", {"notes": "admin was here"}))
        r = self.a.post(f"/plasmids/{self.rid}/move", data={"box_id": str(self.box), "box_row": "2", "box_col": "2"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(plasmid(self.rid, "notes, box_row, box_col"), ("admin was here", 2, 2))

    def test_unowned_plasmid_is_open_to_any_member(self):
        rid = self.make_plasmid(self.a, owner="")
        self.assertSaved(self.autosave(self.o, f"/plasmids/{rid}/update", {"notes": "anyone"}))

    def test_bulk_changes_own_plasmids_and_counts_the_skipped(self):
        own = self.make_plasmid(self.o)
        r = self.post(self.o, "/plasmids/bulk", {"action": "resistance", "value": "Hyg",
                                                  "selected_ids": [str(self.rid), str(own)]})
        self.assertEqual(plasmid(own, "resistance")[0], "Hyg")
        self.assertUntouched()
        self.assertFlash(r, "1 belong to someone else", "success")

    def test_pages_show_someone_elses_plasmid_read_only(self):
        sheet = self.get_ok(self.o, "/plasmids")
        tr = re.search(rf'<tr data-id="{self.rid}".*?</tr>', sheet, re.S).group(0)
        self.assertIn("sheet-lock", tr)
        item = [x for x in grid_of(sheet)["items"] if x["id"] == self.rid][0]
        self.assertTrue(item["locked"])
        detail = self.get_ok(self.o, page(self.rid))
        self.assertIn('data-locked="1"', detail)
        self.assertNotIn("Replace sequence", detail)
        self.assertNotIn("Save storage", detail)
        self.assertTrue(self.o.get(f"/plasmids/{self.rid}/sequence.json").get_json()["locked"])
        self.assertIn('data-locked="0"', self.get_ok(self.m, page(self.rid)))


# ================================================================ autosave

class AutosaveTests(AppTestCase):
    def setUp(self):
        self.box = self.make_box(self.a, rows=4, cols=4)
        self.rid = self.make_plasmid(self.a, box_id=self.box, position="A1")

    def test_autosave_answers_with_the_row_as_saved(self):
        name = uniq("renamed")
        r = self.autosave(self.a, f"/plasmids/{self.rid}/update", {"name": name, "backbone": "pUC19"})
        self.assertSaved(r)
        self.assertEqual(r.get_json()["row"]["values"]["name"], name)
        self.assertEqual(plasmid(self.rid, "name, backbone"), (name, "pUC19"))

    def test_blank_name_is_refused(self):
        r = self.autosave(self.a, f"/plasmids/{self.rid}/update", {"name": " "})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(plasmid(self.rid, "name")[0].strip())

    def test_missing_plasmid_is_404_json(self):
        gone = one("select coalesce(max(id), 0) + 1000 from plasmids")
        r = self.autosave(self.a, f"/plasmids/{gone}/update", {"name": "x"})
        self.assertEqual(r.status_code, 404)
        self.assertIs(r.get_json()["ok"], False)

    def test_moving_into_a_taken_cell_is_refused(self):
        self.make_plasmid(self.a, box_id=self.box, position="B2")
        r = self.autosave(self.a, f"/plasmids/{self.rid}/update", {
            "box_id": str(self.box), "position": "B2", "box_id_was": str(self.box), "position_was": "A1"})
        self.assertRefused(r)
        self.assertIn("already holds", r.get_json()["error"])
        self.assertEqual(plasmid(self.rid, "box_row, box_col"), (0, 0))

    def test_position_outside_the_box_or_without_a_box_is_refused(self):
        r = self.autosave(self.a, f"/plasmids/{self.rid}/update", {
            "box_id": str(self.box), "position": "Q99", "box_id_was": str(self.box), "position_was": "A1"})
        self.assertRefused(r)
        r = self.autosave(self.a, f"/plasmids/{self.rid}/update", {
            "box_id": "", "position": "A1", "box_id_was": str(self.box), "position_was": ""})
        self.assertRefused(r)
        self.assertIn("needs a box", r.get_json()["error"])
        self.assertEqual(plasmid(self.rid, "box_id_fk, box_row, box_col"), (self.box, 0, 0))

    def test_stale_row_keeps_the_position_the_grid_set(self):
        # The grid moved it to C3 while the sheet row still says A1.
        self.a.post(f"/plasmids/{self.rid}/move", data={"box_id": str(self.box), "box_row": "2", "box_col": "2"})
        r = self.autosave(self.a, f"/plasmids/{self.rid}/update", {
            "notes": "stale", "box_id": str(self.box), "position": "A1", "box_id_was": str(self.box), "position_was": "A1"})
        self.assertSaved(r)
        self.assertEqual(plasmid(self.rid, "notes, box_row, box_col"), ("stale", 2, 2))

    def test_a_box_that_is_gone_is_refused(self):
        gone = one("select coalesce(max(id), 0) + 1000 from plasmid_boxes")
        r = self.autosave(self.a, f"/plasmids/{self.rid}/update", {"box_id": str(gone), "box_id_was": str(self.box)})
        self.assertRefused(r)
        self.assertIn("no longer exists", r.get_json()["error"])

    def test_dialog_save_into_a_taken_cell_keeps_other_fields(self):
        self.make_plasmid(self.a, box_id=self.box, position="D4")
        r = self.post(self.a, f"/plasmids/{self.rid}/update", {"resistance": "Kan", "box_id": str(self.box), "position": "D4"})
        self.assertFlash(r, "not the box position", "error")
        self.assertEqual(plasmid(self.rid, "resistance, box_row, box_col"), ("Kan", 0, 0))


# ================================================================ sequences

class SequenceRouteTests(AppTestCase):
    def setUp(self):
        self.rid = self.make_plasmid(self.a, sequence_text="ACGTACGTACGT")

    def seq(self):
        return plasmid(self.rid, "full_sequence")[0]

    def test_sequence_save_refuses_empty_or_malformed_bodies(self):
        for kwargs in ({"json": {}}, {"json": {"sequenceData": {"sequence": "", "features": []}}},
                       {"data": "not json", "content_type": "text/plain"}):
            r = self.a.post(f"/plasmids/{self.rid}/sequence-save", **kwargs)
            self.assertEqual(r.status_code, 400, kwargs)
        self.assertEqual(self.seq(), "ACGTACGTACGT")

    def test_sequence_save_refuses_non_iupac_letters(self):
        r = self.a.post(f"/plasmids/{self.rid}/sequence-save", json={"sequenceData": {"sequence": "hello world"}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("IUPAC", r.get_json()["error"])
        self.assertEqual(self.seq(), "ACGTACGTACGT")

    def test_sequence_save_uppercases_drops_out_of_range_and_keeps_wrapping_features(self):
        r = self.a.post(f"/plasmids/{self.rid}/sequence-save", json={"sequenceData": {
            "sequence": "acgtRYKM acgt\nnnnn", "circular": True, "features": [
                {"name": "ok", "start": 1, "end": 4, "forward": True},
                {"name": "wrap", "start": 12, "end": 2, "forward": False},
                {"name": "too far", "start": 3, "end": 99}]}})
        self.assertEqual(r.get_json(), {"ok": True, "length": 16, "features": 2, "counts": {
            "features": 2, "primers": 0, "translations": 0, "parts": 0}})
        seq, feats = plasmid(self.rid, "full_sequence, features_json")
        self.assertEqual(seq, "ACGTRYKMACGTNNNN")
        self.assertEqual([(f["name"], f["start"], f["end"], f["direction"]) for f in json.loads(feats)],
                         [("ok", 1, 4, 1), ("wrap", 12, 2, -1)])

    def test_origin_spanning_feature_survives_upload_json_and_editor_save(self):
        self.post(self.a, f"/plasmids/{self.rid}/upload-sequence", gb_upload(), content_type="multipart/form-data")
        data = self.a.get(f"/plasmids/{self.rid}/sequence.json").get_json()
        wrap = [f for f in data["features"] if f["name"] == "wrap"][0]
        self.assertEqual((wrap["start"], wrap["end"], data["circular"]), (54, 4, True))
        # The editor posts back what it was given.
        r = self.a.post(f"/plasmids/{self.rid}/sequence-save", json={"sequenceData": {
            "sequence": data["sequence"], "circular": data["circular"], "features": data["features"]}})
        self.assertTrue(r.get_json()["ok"])
        stored = {f["name"]: f for f in json.loads(plasmid(self.rid, "features_json")[0])}
        self.assertEqual((stored["wrap"]["start"], stored["wrap"]["end"]), (54, 4))
        self.assertEqual(stored["GFPfrag"]["direction"], -1)
        self.assertEqual(len(stored), 3)

    def test_garbage_upload_is_refused_and_nothing_is_stored(self):
        r = self.post(self.a, f"/plasmids/{self.rid}/upload-sequence",
                      {"file": (io.BytesIO(b"\x00\x00\x01binary"), "x.gb")}, content_type="multipart/form-data")
        self.assertFlash(r, "not a sequence file", "warning")
        self.assertFlash(r, "The sequence was not changed")
        self.assertEqual(plasmid(self.rid, "full_sequence, sequence_format"), ("ACGTACGTACGT", "raw"))

    def test_prose_upload_is_refused(self):
        r = self.post(self.a, f"/plasmids/{self.rid}/upload-sequence",
                      {"file": (io.BytesIO(b"Meeting notes: bring the plasmids"), "notes.txt")},
                      content_type="multipart/form-data")
        self.assertFlash(r, "not a sequence file", "warning")
        self.assertEqual(self.seq(), "ACGTACGTACGT")

    def test_upload_with_nothing_chosen_is_a_notice(self):
        r = self.post(self.a, f"/plasmids/{self.rid}/upload-sequence", {})
        self.assertFlash(r, "Choose a file", "info")

    def test_genbank_and_fasta_uploads_replace_the_sequence(self):
        r = self.post(self.a, f"/plasmids/{self.rid}/upload-sequence", gb_upload(filename="p.gbk"),
                      content_type="multipart/form-data")
        self.assertFlash(r, "GENBANK · 60 bp · 3 features", "success")
        self.assertEqual(len(self.seq()), 60)
        r = self.post(self.a, f"/plasmids/{self.rid}/upload-sequence",
                      gb_upload(">pF\nACGTRYN\n", "p.fa"), content_type="multipart/form-data")
        self.assertFlash(r, "FASTA · 7 bp", "success")
        self.assertEqual(plasmid(self.rid, "full_sequence, is_circular, features_json"), ("ACGTRYN", 0, "[]"))

    def test_edit_sequence_strips_noise_and_drops_features_past_the_new_end(self):
        self.post(self.a, f"/plasmids/{self.rid}/upload-sequence", gb_upload(), content_type="multipart/form-data")
        r = self.post(self.a, f"/plasmids/{self.rid}/edit-sequence",
                      {"sequence_text": ">hdr\n1 acgtr yacgt\n11 acgtacgtac gtacgtacgt\n", "is_circular": "1"})
        self.assertEqual(self.seq(), "ACGTRYACGTACGTACGTACGTACGTACGT")
        names = [f["name"] for f in json.loads(plasmid(self.rid, "features_json")[0])]
        self.assertEqual(names, ["CMV"])
        self.assertFlash(r, "Dropped 2 feature(s)", "warning")

    def test_edit_sequence_to_nothing_is_refused(self):
        r = self.post(self.a, f"/plasmids/{self.rid}/edit-sequence", {"sequence_text": "  12 \n"})
        self.assertFlash(r, "Use Clear sequence", "error")
        self.assertEqual(self.seq(), "ACGTACGTACGT")

    def test_clear_sequence_needs_confirmation(self):
        r = self.post(self.a, f"/plasmids/{self.rid}/clear-sequence", {})
        self.assertFlash(r, "Confirm", "error")
        self.assertEqual(self.seq(), "ACGTACGTACGT")
        self.post(self.a, f"/plasmids/{self.rid}/clear-sequence", {"confirm": "1"})
        self.assertEqual(plasmid(self.rid, "full_sequence, features_json"), ("", "[]"))



class EditorAnnotationTests(AppTestCase):
    """What the plasmid editor saves: it sends each annotation group as an
    object keyed by id, and keeps primers, translations and parts as well as
    features. Saving used to accept only a list of features, so any edit in
    the editor erased every feature and nothing else was ever kept."""

    SEQ = "ATGGTGAGCAAGGGCGAGGAGCTGTTCACCGGGGTGGTGCCCATCCTGGTCGAGCTGGACGGCGACGTAAACGGCCACAAG"

    def setUp(self):
        self.rid = self.make_plasmid(self.a, sequence_text=self.SEQ)

    def save(self, **groups):
        return self.a.post(f"/plasmids/{self.rid}/sequence-save", json={"sequenceData": {
            "sequence": self.SEQ, "circular": True, "name": "pTest", **groups}})

    def stored(self):
        return json.loads(plasmid(self.rid, "features_json")[0])

    def test_features_sent_as_an_object_are_kept(self):
        r = self.save(features={"f1": {"id": "f1", "name": "CMV", "start": 0, "end": 20, "forward": True},
                                "f2": {"id": "f2", "name": "tag", "start": 30, "end": 40, "strand": -1}})
        self.assertEqual(r.get_json()["features"], 2)
        self.assertEqual([(f["name"], f["direction"]) for f in self.stored()], [("CMV", 1), ("tag", -1)])

    def test_primers_translations_and_parts_are_kept_and_come_back_by_group(self):
        r = self.save(
            features={"f1": {"name": "CDS1", "type": "CDS", "start": 0, "end": 29, "forward": True}},
            primers={"p1": {"name": "fwd", "start": 0, "end": 19, "forward": True}},
            translations={"t1": {"name": "mine", "start": 3, "end": 32, "forward": True,
                                 "translationType": "User Created"},
                          # The editor also lists its own translation of every CDS feature.
                          "t2": {"name": "CDS1", "start": 0, "end": 29, "translationType": "CDS Feature"}},
            parts={"x1": {"name": "insert", "start": 40, "end": 60, "forward": False}})
        self.assertEqual(r.get_json()["counts"], {"features": 1, "primers": 1, "translations": 1, "parts": 1})
        data = self.a.get(f"/plasmids/{self.rid}/sequence.json").get_json()
        self.assertEqual([f["name"] for f in data["features"]], ["CDS1"])
        self.assertEqual([(p["name"], p["start"], p["end"], p["forward"]) for p in data["primers"]],
                         [("fwd", 0, 19, True)])
        self.assertEqual([t["name"] for t in data["translations"]], ["mine"])
        self.assertEqual([(p["name"], p["strand"]) for p in data["parts"]], [("insert", -1)])
        # The detail page counts features only.
        self.assertIn("its 1 feature(s)", self.get_ok(self.a, page(self.rid)))

    def test_notes_and_joined_features_survive_a_round_trip(self):
        self.save(features=[{"name": "split", "start": 0, "end": 50, "forward": True,
                             "locations": [{"start": 0, "end": 10}, {"start": 40, "end": 50}],
                             "notes": {"gene": ["egfp"], "note": ["from pEGFP-N1"]}}])
        data = self.a.get(f"/plasmids/{self.rid}/sequence.json").get_json()
        split = data["features"][0]
        self.assertEqual(split["locations"], [{"start": 0, "end": 10}, {"start": 40, "end": 50}])
        self.assertEqual(split["notes"], {"gene": ["egfp"], "note": ["from pEGFP-N1"]})
        # And the editor posting back what it was given changes nothing.
        self.save(features=data["features"], primers=data["primers"])
        self.assertEqual(self.a.get(f"/plasmids/{self.rid}/sequence.json").get_json()["features"][0]["notes"],
                         {"gene": ["egfp"], "note": ["from pEGFP-N1"]})

    def test_renaming_in_the_editor_renames_the_plasmid(self):
        self.save(features=[])
        self.assertEqual(plasmid(self.rid, "name")[0], "pTest")

    def test_a_hand_edit_of_the_sequence_keeps_primers_and_parts(self):
        self.save(primers={"p": {"name": "fwd", "start": 0, "end": 9, "forward": True}},
                  parts={"x": {"name": "late", "start": 70, "end": 79, "forward": True}})
        self.post(self.a, f"/plasmids/{self.rid}/edit-sequence", {"sequence_text": self.SEQ[:40], "is_circular": "1"})
        self.assertEqual([(f.get("kind"), f["name"]) for f in self.stored()], [("primer", "fwd")])


# ================================================================ boxes

class BoxTests(AppTestCase):
    def test_new_box_is_saved_with_its_creator_and_opens(self):
        name = uniq("Box")
        r = self.a.post("/plasmids/boxes/save", data={"id": "", "name": name, "rows": "8", "cols": "10",
                                                      "location": "-80 freezer 2", **NUMBERS_NAMING})
        box = row("select id, rows, cols, location, created_by from plasmid_boxes where name=?", name)
        self.assertEqual(box[1:], (8, 10, "-80 freezer 2", self.admin))
        self.assertIn(f"box={box[0]}", r.headers["Location"])

    def test_duplicate_box_name_in_any_case_is_refused(self):
        name = uniq("Dup")
        self.make_box(self.a, name)
        r = self.post(self.m, "/plasmids/boxes/save", {"id": "", "name": name.upper(), "rows": "9", "cols": "9"})
        self.assertFlash(r, "already a box called", "error")
        self.assertEqual(count("plasmid_boxes", "lower(name)=lower(?)", name), 1)

    def test_blank_box_name_is_refused(self):
        before = count("plasmid_boxes")
        r = self.post(self.a, "/plasmids/boxes/save", {"id": "", "name": "  ", "rows": "9", "cols": "9"})
        self.assertFlash(r, "Give the box a name", "error")
        self.assertEqual(count("plasmid_boxes"), before)

    def test_box_cannot_shrink_past_a_plasmid(self):
        name = uniq("Shrink")
        box = self.make_box(self.a, name)
        pid = one("select plasmid_id from plasmids where id=?", self.make_plasmid(self.a, box_id=box, position="E5"))
        r = self.post(self.a, "/plasmids/boxes/save", {"id": str(box), "name": name, "rows": "3", "cols": "9"})
        self.assertFlash(r, "cannot shrink", "error")
        self.assertFlash(r, f"#{pid}", "error")
        self.assertEqual(one("select rows from plasmid_boxes where id=?", box), 9)

    def test_rename_follows_onto_its_plasmids(self):
        box = self.make_box(self.a)
        rid = self.make_plasmid(self.a, box_id=box, position="A1")
        new = uniq("Renamed")
        self.post(self.a, "/plasmids/boxes/save", {"id": str(box), "name": new, "rows": "10", "cols": "12", **GRID_NAMING})
        self.assertEqual(row("select name, rows, cols from plasmid_boxes where id=?", box), (new, 10, 12))
        self.assertEqual(plasmid(rid, "storage_box, box_row, box_col"), (new, 0, 0))

    def test_member_cannot_change_or_delete_someone_elses_box(self):
        name = uniq("AdminBox")
        box = self.make_box(self.a, name, rows=2, cols=2)
        r = self.post(self.m, "/plasmids/boxes/save", {"id": str(box), "name": name, "rows": "3", "cols": "3"})
        self.assertFlash(r, f"Only {self.admin} (who made it) or an admin", "error")
        r = self.post(self.m, f"/plasmids/boxes/{box}/delete")
        self.assertTrue(errors(r))
        self.assertEqual(row("select rows, cols from plasmid_boxes where id=?", box), (2, 2))

    def test_member_changes_and_deletes_own_box(self):
        name = uniq("Mine")
        box = self.make_box(self.m, name, rows=4, cols=4)
        self.post(self.m, "/plasmids/boxes/save", {"id": str(box), "name": name, "rows": "5", "cols": "6", **GRID_NAMING})
        self.assertEqual(row("select rows, cols, created_by from plasmid_boxes where id=?", box), (5, 6, self.member))
        self.post(self.m, f"/plasmids/boxes/{box}/delete")
        self.assertEqual(count("plasmid_boxes", "id=?", box), 0)

    def test_member_may_put_own_plasmids_in_anyones_box(self):
        box = self.make_box(self.a)
        rid = self.make_plasmid(self.m, box_id=box, position="C3")
        self.assertEqual(plasmid(rid, "box_id_fk, box_row, box_col, owner"), (box, 2, 2, self.member))

    def test_positions_follow_the_boxs_own_naming(self):
        box_name = uniq("Num")
        self.a.post("/plasmids/boxes/save", data={"id": "", "name": box_name, "rows": "8", "cols": "10", **NUMBERS_NAMING})
        box = one("select id from plasmid_boxes where name=?", box_name)
        rid = self.make_plasmid(self.a, box_id=box, position="2-3")
        self.assertEqual(plasmid(rid, "box_row, box_col"), (1, 2))
        bad = uniq("Bad")
        r = self.post(self.a, "/plasmids", {"name": bad, "box_id": str(box), "position": "B3"})
        self.assertFlash(r, "Positions there run 1-1–8-10", "warning")
        detail = self.get_ok(self.a, page(rid))
        self.assertIn(f'{box_name} · <span class="ident">2-3</span>', detail)
        r = self.autosave(self.a, f"/plasmids/{rid}/update", {
            "box_id": str(box), "position": "3-4", "box_id_was": str(box), "position_was": "2-3"})
        self.assertEqual(r.get_json()["row"]["values"]["position"], "3-4")

    def test_deleting_a_box_keeps_its_plasmids_and_undo_puts_them_back(self):
        name = uniq("Doomed")
        box = self.make_box(self.a, name)
        placed = [self.make_plasmid(self.a, box_id=box, position=p) for p in ("A1", "C4")]
        r = self.post(self.a, f"/plasmids/boxes/{box}/delete")
        self.assertFlash(r, "Its 2 plasmids are no longer in a box", "success")
        self.assertEqual(count("plasmid_boxes", "id=?", box), 0)
        self.assertEqual([plasmid(i, "box_id_fk, storage_box, box_row") for i in placed], [(None, "", None)] * 2)
        batch = newest_batch(self.admin)
        self.assertEqual(batch[1:3], (f"delete plasmid box {name}", "plasmid_boxes"))
        self.post(self.a, f"/batches/{batch[0]}/undo")
        self.assertEqual(one("select name from plasmid_boxes where id=?", box), name)
        self.assertEqual([plasmid(i, "box_id_fk, box_row, box_col") for i in placed], [(box, 0, 0), (box, 2, 3)])

    def test_grid_payload_shows_boxes_and_tiles_one_based(self):
        name = uniq("Grid")
        box = self.make_box(self.a, name, rows=5, cols=6, location="-20 shelf")
        rid = self.make_plasmid(self.a, box_id=box, position="D5")
        g = grid_of(self.get_ok(self.a, "/plasmids"))
        rack = [x for x in g["racks"] if x["id"] == box][0]
        self.assertEqual((rack["name"], rack["rows"], rack["cols"], rack["group"]), (name, 5, 6, "-20 shelf"))
        self.assertEqual(json.loads(rack["edit"]["data-record-payload"])["_count"], 1)
        item = [x for x in g["items"] if x["id"] == rid][0]
        self.assertEqual((item["rack"], item["row"], item["col"], item["locked"]), (box, 4, 5, False))


# ================================================================ migration

class LegacyBoxMigrationTests(AppTestCase):
    """Before boxes were a table, a plasmid's box was a typed name. Rows in
    that shape are written straight to the database, then the one-off
    migration is run again."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.big, cls.twin, cls.perm = uniq("Big box"), uniq("Twin box"), uniq("Old box")
        base = one("select coalesce(max(plasmid_id), 0) from plasmids") + 1000
        cls.pids = {}
        for i, (tag, box, r, c) in enumerate([("far", f" {cls.big} ", 11, 14), ("near", cls.big, 0, 0),
                                               ("twinA", cls.twin, 2, 2), ("twinB", cls.twin, 2, 2),
                                               ("loose", cls.twin, None, None), ("perm", cls.perm, 0, 0)]):
            pid = base + i
            cls.pids[tag] = pid
            execute("insert into plasmids (plasmid_id, name, backbone, insert_seq, resistance, owner, location, notes,"
                    " full_sequence, is_circular, features_json, sequence_format, storage_box, box_row, box_col,"
                    " created_at, updated_by) values (?,?,'','','',?,'','','',true,'','',?,?,?,'2026-01-01','')",
                    pid, uniq(tag), cls.member, box, r, c)
        execute("delete from app_settings where key=?", services.PLASMID_BOXES_FLAG)
        cls.result = services.migrate_plasmid_boxes()

    @staticmethod
    def at(pid, cols="box_id_fk, storage_box, box_row, box_col"):
        return row(f"select {cols} from plasmids where plasmid_id=?", pid)

    def box(self, name):
        return row("select id, rows, cols, created_by, naming from plasmid_boxes where name=?", name)

    def test_one_box_per_trimmed_name_grown_to_the_furthest_cell(self):
        self.assertEqual(count("plasmid_boxes", "name like ?", f"%{self.big}%"), 1)
        self.assertEqual(self.box(self.big)[1:3], (12, 15))
        self.assertEqual(self.box(self.twin)[1:3], (9, 9))
        self.assertEqual(json.loads(self.box(self.twin)[4])["rows"], "letters")

    def test_plasmids_are_linked_and_keep_their_cells(self):
        big = self.box(self.big)[0]
        self.assertEqual(self.at(self.pids["far"]), (big, self.big, 11, 14))
        self.assertEqual(self.at(self.pids["near"]), (big, self.big, 0, 0))

    def test_a_doubled_cell_keeps_the_lower_number_and_the_other_waits_unplaced(self):
        twin = self.box(self.twin)[0]
        self.assertEqual(self.at(self.pids["twinA"]), (twin, self.twin, 2, 2))
        self.assertEqual(self.at(self.pids["twinB"]), (twin, self.twin, None, None))
        self.assertGreaterEqual(self.result["unplaced_doubles"], 1)

    def test_a_plasmid_with_a_box_but_no_cell_is_linked_unplaced(self):
        self.assertEqual(self.at(self.pids["loose"])[:3], (self.box(self.twin)[0], self.twin, None))

    def test_migrated_boxes_have_no_creator_so_only_an_admin_may_change_them(self):
        box_id, *_rest, creator, _naming = self.box(self.perm)
        self.assertEqual(creator, "")
        r = self.post(self.m, "/plasmids/boxes/save", {"id": str(box_id), "name": uniq("Mine now"), "rows": "9", "cols": "9"})
        self.assertFlash(r, "Only an admin can change", "error")
        self.assertEqual(self.box(self.perm)[0], box_id)
        payload = [json.loads(x["edit"]["data-record-payload"]) for x in grid_of(self.get_ok(self.m, "/plasmids"))["racks"]
                   if x["id"] == box_id][0]
        self.assertTrue(payload["_locked"])
        self.post(self.a, "/plasmids/boxes/save", {"id": str(box_id), "name": self.perm, "rows": "10", "cols": "9", **GRID_NAMING})
        self.assertEqual(self.box(self.perm)[1], 10)

    def test_migration_runs_only_once(self):
        before = count("plasmid_boxes")
        self.assertIsNone(services.migrate_plasmid_boxes())
        self.assertEqual(count("plasmid_boxes"), before)


# ================================================================ moves

class MoveTests(AppTestCase):
    def setUp(self):
        self.box = self.make_box(self.a, rows=4, cols=4)
        self.p1 = self.make_plasmid(self.a, box_id=self.box, position="A1")
        self.p2 = self.make_plasmid(self.a, box_id=self.box, position="A2")

    def move(self, rid, **data):
        return self.a.post(f"/plasmids/{rid}/move", data=data)

    def test_move_to_a_free_cell(self):
        r = self.move(self.p1, box_id=str(self.box), box_row="3", box_col="3")
        self.assertEqual(r.get_json()["moved"][0]["position"], "D4")
        self.assertEqual(plasmid(self.p1, "box_row, box_col"), (3, 3))

    def test_move_onto_an_occupied_cell_swaps_and_reports_both(self):
        r = self.move(self.p1, box_id=str(self.box), box_row="0", box_col="1")
        self.assertEqual(sorted(m["id"] for m in r.get_json()["moved"]), sorted([self.p1, self.p2]))
        self.assertEqual([plasmid(i, "box_row, box_col") for i in (self.p1, self.p2)], [(0, 1), (0, 0)])

    def test_update_into_a_taken_cell_is_refused_not_swapped(self):
        r = self.autosave(self.a, f"/plasmids/{self.p1}/update", {
            "box_id": str(self.box), "position": "A2", "box_id_was": str(self.box), "position_was": "A1"})
        self.assertRefused(r)
        self.assertEqual([plasmid(i, "box_row, box_col") for i in (self.p1, self.p2)], [(0, 0), (0, 1)])

    def test_move_across_boxes_names_the_cell_in_the_new_box(self):
        name = uniq("Num")
        self.a.post("/plasmids/boxes/save", data={"id": "", "name": name, "rows": "3", "cols": "3", **NUMBERS_NAMING})
        other = one("select id from plasmid_boxes where name=?", name)
        r = self.move(self.p1, box_id=str(other), box_row="0", box_col="0")
        self.assertEqual(r.get_json()["moved"][0]["position"], "1-1")
        self.assertEqual(plasmid(self.p1, "box_id_fk, storage_box"), (other, name))

    def test_tray_drop_keeps_the_plasmid_in_its_box_without_a_cell(self):
        r = self.move(self.p1, box_id="", box_row="", box_col="")
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(plasmid(self.p1, "box_id_fk, box_row, box_col"), (self.box, None, None))

    def test_bad_moves_change_nothing(self):
        self.assertEqual(self.move(self.p1, box_id=str(self.box), box_row="4", box_col="0").status_code, 409)
        self.assertEqual(self.move(self.p1, box_id=str(self.box), box_row="-1", box_col="0").status_code, 409)
        self.assertEqual(self.move(self.p1, box_id=str(self.box), box_row="x", box_col="1").status_code, 400)
        gone_box = one("select max(id) + 1000 from plasmid_boxes")
        self.assertEqual(self.move(self.p1, box_id=str(gone_box), box_row="0", box_col="0").status_code, 404)
        gone = one("select max(id) + 1000 from plasmids")
        self.assertEqual(self.move(gone, box_id=str(self.box)).status_code, 404)
        self.assertEqual(plasmid(self.p1, "box_id_fk, box_row, box_col"), (self.box, 0, 0))


# ================================================================ add many

class AddManyTests(AppTestCase):
    def test_how_many_makes_consecutive_numbers_side_by_side_skipping_taken_cells(self):
        box = self.make_box(self.a)
        self.make_plasmid(self.a, box_id=box, position="C3")
        name = uniq("Batch")
        first = one("select coalesce(max(plasmid_id), 0) + 1 from plasmids")
        r = self.post(self.a, "/plasmids", {"name": name, "count": "5", "box_id": str(box), "position": "C2",
                                            "backbone": "pUC19", "resistance": "Amp", "plasmid_id": str(first)})
        got = by_name(name, "plasmid_id, backbone, resistance, box_row, box_col")
        self.assertEqual([x[0] for x in got], list(range(first, first + 5)))
        self.assertTrue(all(x[1:3] == ("pUC19", "Amp") for x in got))
        self.assertEqual([x[3:] for x in got], [(2, 1), (2, 3), (2, 4), (2, 5), (2, 6)])
        self.assertFlash(r, f"#{first}–#{first + 4}", "success")
        self.assertFlash(r, "(C2–C7)", "success")

    def test_add_many_is_one_batch_that_undo_removes(self):
        name = uniq("Undo")
        self.post(self.a, "/plasmids", {"name": name, "count": "4"})
        batch = newest_batch(self.admin)
        self.assertEqual(batch[2:], ("plasmids", 4, "create"))
        self.assertIn("×4", batch[1])
        self.post(self.a, f"/batches/{batch[0]}/undo")
        self.assertEqual(by_name(name), [])

    def test_what_does_not_fit_waits_in_the_box_and_is_reported(self):
        box = self.make_box(self.a, rows=2, cols=3)
        name = uniq("Crowd")
        r = self.post(self.a, "/plasmids", {"name": name, "count": "5", "box_id": str(box), "position": "B1"})
        got = by_name(name, "box_id_fk, box_row, box_col")
        self.assertEqual(got, [(box, 1, 0), (box, 1, 1), (box, 1, 2), (box, None, None), (box, None, None)])
        self.assertFlash(r, "had room for 3 of 5 from B1", "warning")

    def test_pasted_names_make_one_plasmid_each_in_the_first_free_cells(self):
        box = self.make_box(self.a)
        names = [uniq("pList") for _ in range(3)]
        self.post(self.a, "/plasmids", {"name": "", "names": f"{names[0]}\n\n  {names[1]}  \n{names[2]}\n",
                                        "count": "9", "box_id": str(box)})
        got = rows("select name, box_row, box_col from plasmids where box_id_fk=? order by plasmid_id", box)
        self.assertEqual(got, [(names[0], 0, 0), (names[1], 0, 1), (names[2], 0, 2)])

    def test_count_outside_one_to_fifty_is_refused(self):
        name = uniq("Too")
        for data in ({"name": name, "count": "51"}, {"name": name, "count": "0"},
                     {"names": "\n".join(f"{name}-{i}" for i in range(51))}):
            r = self.post(self.a, "/plasmids", data)
            self.assertFlash(r, "between 1 and 50", "error")
        self.assertEqual(count("plasmids", "name like ?", f"{name}%"), 0)

    def test_taken_run_of_numbers_moves_to_the_next_free_run(self):
        taken = one("select plasmid_id from plasmids where id=?", self.make_plasmid(self.a))
        name = uniq("Clash")
        r = self.post(self.a, "/plasmids", {"name": name, "count": "3", "plasmid_id": str(taken)})
        got = [x[0] for x in by_name(name, "plasmid_id")]
        self.assertGreater(got[0], taken)
        self.assertEqual(got, list(range(got[0], got[0] + 3)))
        self.assertFlash(r, "were taken", "warning")

    def test_every_plasmid_of_a_batch_gets_the_sequence(self):
        name = uniq("Seqs")
        self.post(self.a, "/plasmids", {"name": name, "count": "2", "sequence_text": ">x\nACGTRY\n"})
        self.assertEqual(by_name(name, "full_sequence"), [("ACGTRY",), ("ACGTRY",)])

    def test_older_form_naming_a_new_box_makes_a_nine_by_nine_box(self):
        box_name, name = uniq("Brand new box"), uniq("Legacy")
        self.post(self.a, "/plasmids", {"name": name, "storage_box": box_name, "position": "B2"})
        self.assertEqual(row("select rows, cols, created_by from plasmid_boxes where name=?", box_name), (9, 9, self.admin))
        self.assertEqual(by_name(name, "storage_box, box_row, box_col"), [(box_name, 1, 1)])


# ================================================================ bulk

class BulkTests(AppTestCase):
    def setUp(self):
        self.box = self.make_box(self.a)
        self.ids = [self.make_plasmid(self.a, box_id=self.box, position=p, resistance="Amp") for p in ("A1", "A2", "A3")]
        self.sel = [str(i) for i in self.ids]

    def bulk(self, **data):
        return self.post(self.a, "/plasmids/bulk", {"selected_ids": self.sel, **data})

    def test_bulk_owner_is_one_batch_and_undo_restores_owners(self):
        self.bulk(action="owner", value=self.member)
        self.assertEqual({plasmid(i, "owner")[0] for i in self.ids}, {self.member})
        batch = newest_batch(self.admin)
        self.assertEqual(batch[2:4], ("plasmids", 3))
        self.post(self.a, f"/batches/{batch[0]}/undo")
        self.assertEqual({plasmid(i, "owner")[0] for i in self.ids}, {self.admin})

    def test_bulk_resistance(self):
        r = self.bulk(action="resistance", value="Spec")
        self.assertEqual({plasmid(i, "resistance")[0] for i in self.ids}, {"Spec"})
        self.assertFlash(r, "Set resistance on 3 plasmids", "success")

    def test_bulk_move_into_a_small_box_reports_what_did_not_fit_and_undoes(self):
        tiny = self.make_box(self.a, rows=1, cols=2)
        r = self.bulk(action="move", box_id=str(tiny))
        self.assertEqual([plasmid(i, "box_id_fk, box_row, box_col") for i in self.ids],
                         [(tiny, 0, 0), (tiny, 0, 1), (tiny, None, None)])
        self.assertFlash(r, "1 did not fit", "success")
        self.post(self.a, f"/batches/{newest_batch(self.admin)[0]}/undo")
        self.assertEqual([plasmid(i, "box_id_fk, box_row, box_col") for i in self.ids],
                         [(self.box, 0, 0), (self.box, 0, 1), (self.box, 0, 2)])

    def test_bulk_move_to_no_box(self):
        self.bulk(action="move", box_id="")
        self.assertEqual({plasmid(i, "box_id_fk, storage_box, box_row") for i in self.ids}, {(None, "", None)})

    def test_bulk_delete_and_undo(self):
        self.bulk(action="delete")
        self.assertEqual(count("plasmids", f"id in ({','.join(self.sel)})"), 0)
        self.post(self.a, f"/batches/{newest_batch(self.admin)[0]}/undo")
        self.assertEqual([plasmid(i, "box_row, box_col") for i in self.ids], [(0, 0), (0, 1), (0, 2)])

    def test_bulk_with_nothing_ticked_or_unknown_action(self):
        r = self.post(self.a, "/plasmids/bulk", {"action": "owner", "value": "x"})
        self.assertFlash(r, "Tick the plasmids", "info")
        r = self.bulk(action="explode")
        self.assertFlash(r, "Unknown batch action", "error")
        self.assertEqual({plasmid(i, "resistance")[0] for i in self.ids}, {"Amp"})


# ================================================================ pages

class PageTests(AppTestCase):
    def test_sheet_and_detail_pages_render(self):
        box = self.make_box(self.a)
        rid = self.make_plasmid(self.a, box_id=box, position="B3", sequence_text=">x\nACGTNN\n")
        name = plasmid(rid, "name")[0]
        sheet = self.get_ok(self.m, "/plasmids")
        self.assertIn(f'href="{page(rid)}"', sheet)
        self.assertIn('id="plasmid-box-dialog"', sheet)
        detail = self.get_ok(self.a, page(rid))
        self.assertIn(name, detail)
        self.assertIn('<span class="ident">B3</span>', detail)
        self.assertIn("Replace sequence", detail)

    def test_missing_plasmid_page_redirects_to_the_sheet(self):
        gone = one("select coalesce(max(id), 0) + 1000 from plasmids")
        r = self.a.get(f"/plasmids/{gone}")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.a.get(f"/plasmids/{gone}/sequence.json").status_code, 404)


if __name__ == "__main__":
    unittest.main()
