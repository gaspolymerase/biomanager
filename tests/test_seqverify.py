"""Verify sequencing (app/seqverify.py): Sanger reads, from an ABI
chromatogram or text, checked against a plasmid's map, on the plasmid page,
over the API and as the assistant's verify_sequencing tool."""
from __future__ import annotations

import io
import json
import random
import struct
import time
import unittest

from tests.base import execute, one, row, uniq
from tests.test_api import Base

from app import seqverify as sv

# ---------------------------------------------------------------- a plasmid with known parts

_rng = random.Random(20261011)


def _random(n: int) -> str:
    return "".join(_rng.choice("ACGT") for _ in range(n))


def _orf(first: list[str], codons: int) -> str:
    """ATG, the codons given, random sense codons, then TAA: `codons` long."""
    sense = [c for c, aa in sv.CODE.items() if aa != "*"]
    body = first + [_rng.choice(sense) for _ in range(codons - 2 - len(first))]
    return "ATG" + "".join(body) + "TAA"


L = 3000
FWD = _orf(["CTG", "AAA", "GGC", "TGG"], 100)          # 1001–1300: M L K G W …
REV = _orf(["GAA", "TTC"], 100)                          # 2001–2300 on the bottom strand: M E F …
SEQ = _random(1000) + FWD + _random(700) + sv.reverse_complement(REV) + _random(700)
assert len(SEQ) == L
FEATURES = [
    {"name": "GeneF", "type": "CDS", "start": 1000, "end": 1299, "direction": 1, "notes": {}},
    {"name": "GeneR", "type": "CDS", "start": 2000, "end": 2299, "direction": -1, "notes": {"codon_start": ["1"]}},
    {"name": "ori", "type": "rep_origin", "start": 2900, "end": 99, "direction": 1, "notes": ""},
    {"name": "P1", "type": "primer_bind", "kind": "primer", "start": 1000, "end": 1019, "direction": 1},
]


def mutate(seq: str, changes: dict[int, str], deletions=(), insertions: dict[int, str] | None = None,
           start: int = 0, length: int = L) -> str:
    """`seq` (which starts at plasmid 0-based `start`) with base changes,
    deletions and insertions (after) at plasmid 0-based positions."""
    out = []
    for i, base in enumerate(seq):
        p = (start + i) % length
        if p in deletions:
            continue
        out.append(changes.get(p, base))
        if insertions and p in insertions:
            out.append(insertions[p])
    return "".join(out)


SPACING = 12          # trace samples between called bases in the made-up traces


def traces(bases: str, second: dict[int, str] | None = None, order: str = "GATC") -> list[tuple]:
    """ABIF entries for a made-up trace: a peak of 1000 for each called base
    every SPACING samples, and a second peak of 600 under the bases
    `second` names (index → the other base)."""
    size = SPACING * len(bases) + SPACING
    channels = {b: [5] * size for b in "ACGT"}
    for i, base in enumerate(bases):
        centre = SPACING * i + SPACING // 2
        for b, height in [(base, 1000), *(((second[i], 600),) if second and i in second else ())]:
            if b in channels:
                for dx in range(-3, 4):
                    channels[b][centre + dx] = max(channels[b][centre + dx], int(height * (1 - abs(dx) / 4)))
    out = [("FWO_", 1, 2, 1, 4, order.encode())]
    for n, b in zip(range(9, 13), order):
        out.append(("DATA", n, 4, 2, size, struct.pack(f">{size}h", *channels[b])))
    out.append(("PLOC", 2, 4, 2, len(bases), struct.pack(f">{len(bases)}h",
                                                          *(SPACING * i + SPACING // 2 for i in range(len(bases))))))
    return out


def abif(bases: str, quality: list[int], sample: str = "clone3_T7", number: int = 2, macbinary: bool = False,
         quality_tag: bool = True, trace: list[tuple] | None = None) -> bytes:
    """A small ABI chromatogram: the 128-byte header, the data, then the
    directory, as an ABI sequencer writes one."""
    entries = [("SMPL", 1, 18, 1, len(sample) + 1, bytes([len(sample)]) + sample.encode()),
               ("PBAS", number, 2, 1, len(bases), bases.encode()),
               ("LANE", 1, 4, 2, 1, struct.pack(">h", 7)),                      # small: in the offset field
               *(trace or [("DATA", 9, 4, 2, 4, struct.pack(">4h", 1, 2, 3, 4))])]
    if quality_tag:
        entries.insert(2, ("PCON", number, 2, 1, len(quality), bytes(quality)))
    blob, directory, offset = b"", b"", 128
    for tag, num, etype, esize, count, data in entries:
        if len(data) <= 4:
            directory += struct.pack(">4sihhii", tag.encode(), num, etype, esize, count, len(data)) + \
                data.ljust(4, b"\0") + struct.pack(">i", 0)
        else:
            directory += struct.pack(">4sihhiiii", tag.encode(), num, etype, esize, count, len(data), offset, 0)
            blob += data
            offset += len(data)
    header = (b"ABIF" + struct.pack(">h", 101) +
              struct.pack(">4sihhiiii", b"tdir", 1, 1023, 28, len(entries), 28 * len(entries), offset, 0)).ljust(128, b"\0")
    out = header + blob + directory
    return b"\0" * 128 + out if macbinary else out


def check(*reads: sv.Read, circular: bool = True, features=FEATURES) -> dict:
    return sv.verify(SEQ, circular, features, list(reads), name="pTest")


# ---------------------------------------------------------------- reading the files

class ReadingFiles(unittest.TestCase):
    def test_an_ab1_gives_its_called_bases_quality_and_name(self):
        bases = SEQ[400:900]
        quality = [_rng.randint(30, 60) for _ in bases]
        read = sv.read_abif(abif(bases, quality), "")
        self.assertEqual((read.bases, read.quality, read.source, read.name), (bases, quality, "ab1", "clone3_T7"))
        # The file name wins over the sample name; an old Mac's MacBinary header is skipped.
        self.assertEqual(sv.read_file("c3.ab1", abif(bases, quality, macbinary=True))[0].name, "c3")
        # Only a person's edit (PBAS1/PCON1): that is read.
        edited = sv.read_abif(abif(bases, quality, number=1), "x")
        self.assertEqual((edited.bases, edited.quality), (bases, quality))
        # Without quality the bases still come; a damaged directory, or none called, is said in words.
        self.assertIsNone(sv.read_abif(abif(bases, quality, quality_tag=False), "x").quality)
        broken = bytearray(abif(bases, quality))
        broken[26:30] = struct.pack(">i", 10 ** 8)              # the directory's offset, past the end
        with self.assertRaisesRegex(sv.ReadError, "directory is damaged"):
            sv.read_file("bad.ab1", bytes(broken))
        no_bases = abif(bases, quality).replace(b"PBAS", b"PBAX")
        with self.assertRaisesRegex(sv.ReadError, "without called bases"):
            sv.read_file("raw.ab1", no_bases)

    def test_text_reads_and_other_files(self):
        reads = sv.read_file("two.fa", b">r1 T7\nACGTACGTAC\nGTAC\n>r2\nNNACGT\n")
        self.assertEqual([(r.name, r.bases) for r in reads], [("r1", "ACGTACGTACGTAC"), ("r2", "NNACGT")])
        self.assertEqual(sv.read_file("c3.seq", b"\xef\xbb\xbfacgt acgt\n1 acgu\r\n")[0].bases, "ACGTACGTACGT")
        self.assertEqual(sv.read_file("c3.seq", b"acgt")[0].name, "c3")
        with self.assertRaisesRegex(sv.ReadError, "isn't a sequencing read BioManager can open"):
            sv.read_file("map.dna", b"\x09\x00\x00\x00\x0eSnapGene\x00\x01")
        with self.assertRaisesRegex(sv.ReadError, "SCF trace"):
            sv.read_file("c3.scf", b".scf\x00\x00\x00\x10")
        with self.assertRaisesRegex(sv.ReadError, "doesn't look like DNA"):
            sv.read_file("notes.txt", b"Dear lab, the clone looks fine to me.")
        reads, problems = sv.reads_from_uploads([("a.fa", b">a\nACGT"), ("b.zip", b"PK\x03\x04\x00\x00")], "ACGTTT")
        self.assertEqual([(r.name, r.extra["file"]) for r in reads], [("a", 0), ("Pasted read", None)])
        self.assertEqual(len(problems), 1)
        self.assertIn("b.zip", problems[0])

    def test_motts_algorithm_trims_the_poor_ends(self):
        quality = [2] * 30 + [40] * 500 + [3] * 40
        self.assertEqual(sv.mott_trim(quality), (30, 530))
        self.assertEqual(sv.mott_trim([3] * 50), (0, 0))
        # A text read loses only the Ns at its ends.
        self.assertEqual(sv.trim(sv.Read("t", "NNACGTNACGNN")), (2, 10))


# ---------------------------------------------------------------- placing reads and what they show

class Alignment(unittest.TestCase):
    def test_a_perfect_read(self):
        out = check(sv.Read("r", SEQ[900:1400]))
        read = out["reads"][0]
        self.assertEqual((read["strand"], read["start"], read["end"], read["identity"]), ("forward", 901, 1400, 100.0))
        self.assertEqual((out["status"], read["status"], read["differences"]), ("match", "match", []))
        self.assertEqual(out["features_covered"], ["GeneF"])
        self.assertEqual(out["verdict"], "Matches the map across 901–1,400, covering GeneF.")

    def test_substitutions_in_a_forward_cds(self):
        read = mutate(SEQ[900:1500], {1004: "C", 1011: "T", 1013: "A"}, start=900)
        out = check(sv.Read("r", read))
        diffs = {d["position"]: d for d in out["reads"][0]["differences"]}
        self.assertEqual(sorted(diffs), [1005, 1012, 1014])
        self.assertEqual((diffs[1005]["plasmid"], diffs[1005]["read"], diffs[1005]["kind"]), ("T", "C", "mismatch"))
        self.assertEqual(diffs[1005]["features"], ["GeneF"])        # not the primer: features only
        self.assertEqual((diffs[1005]["effect"]["kind"], diffs[1005]["effect"]["text"]), ("missense", "L2P"))
        self.assertEqual((diffs[1012]["effect"]["kind"], diffs[1012]["effect"]["text"]), ("silent", "silent, G4"))
        self.assertEqual((diffs[1014]["effect"]["kind"], diffs[1014]["effect"]["text"]), ("nonsense", "stop codon: W5*"))
        self.assertEqual(out["status"], "differences")
        self.assertIn("T→C at 1,005 in GeneF (L2P)", out["verdict"])

    def test_a_reverse_strand_read_and_a_reverse_cds(self):
        # GeneR's codon 2 is GAA (E) on the bottom strand: its first base is plasmid 2,297, C on the top strand.
        self.assertEqual(SEQ[2296], "C")
        region = mutate(SEQ[1900:2500], {2296: "G"}, start=1900)
        out = check(sv.Read("rev", sv.reverse_complement(region)))
        read = out["reads"][0]
        self.assertEqual((read["strand"], read["start"], read["end"]), ("reverse", 1901, 2500))
        (d,) = read["differences"]
        self.assertEqual((d["position"], d["plasmid"], d["read"]), (2297, "C", "G"))
        self.assertEqual((d["effect"]["feature"], d["effect"]["text"], d["effect"]["codon_from"], d["effect"]["codon_to"]),
                         ("GeneR", "E2Q", "GAA", "CAA"))
        # Where it is in the read as given (5′ of the reverse read is plasmid 2,500).
        self.assertEqual(d["read_position"], 2500 - 2297 + 1)

    def test_a_read_across_the_origin_with_a_deletion(self):
        p = next(i for i in range(40, 90) if SEQ[i - 1] != SEQ[i] != SEQ[i + 1])
        region = mutate(SEQ[2700:] + SEQ[:300], {}, deletions={p}, start=2700)
        out = check(sv.Read("ori", region))
        read = out["reads"][0]
        self.assertEqual((read["start"], read["end"], read["across_origin"]), (2701, 300, True))
        (d,) = read["differences"]
        self.assertEqual((d["kind"], d["position"], d["plasmid"], d["length"]), ("deletion", p + 1, SEQ[p], 1))
        self.assertEqual(d["features"], ["ori"])
        self.assertIn("across the origin", read["verdict"])
        self.assertEqual(out["covered"], [[2701, 300]])
        self.assertIn("ori", out["features_covered"])
        # The same read on a linear plasmid only matches up to the end.
        linear = check(sv.Read("ori", region), circular=False)["reads"][0]
        self.assertEqual(linear["across_origin"], False)
        self.assertIn("Only bases", linear["verdict"])

    def test_an_insertion_shifts_the_frame(self):
        x = next(b for b in "ACGT" if b not in (SEQ[1100], SEQ[1101]))
        out = check(sv.Read("ins", mutate(SEQ[950:1450], {}, insertions={1100: x}, start=950)))
        (d,) = out["reads"][0]["differences"]
        self.assertEqual((d["kind"], d["position"], d["read"]), ("insertion", 1101, x))
        self.assertEqual((d["effect"]["kind"], d["effect"]["codon"]), ("frameshift", 34))
        self.assertIn(f"{x} inserted after 1,101 in GeneF (frameshift at codon 34)", out["verdict"])
        # Three bases are in frame.
        three = check(sv.Read("ins3", mutate(SEQ[950:1450], {}, deletions={1100, 1101, 1102}, start=950)))
        (d,) = three["reads"][0]["differences"]
        self.assertEqual((d["kind"], d["length"], d["effect"]["kind"]), ("deletion", 3, "in_frame"))
        self.assertIn(d["position"], (1100, 1101, 1102, 1103))   # one deletion, wherever the bases allow it

    def test_low_quality_differences_are_marked_and_poor_reads_said(self):
        region = mutate(SEQ[500:1300], {1004: "C"}, start=500)
        quality = [45] * len(region)
        quality[1004 - 500] = 8
        out = check(sv.read_abif(abif(region, quality), "lowq"))
        (d,) = out["reads"][0]["differences"]
        self.assertEqual((d["quality"], d["confident"]), (8, False))
        self.assertEqual(out["status"], "match")
        self.assertIn("likely a sequencing error", out["verdict"])
        poor = check(sv.read_abif(abif(SEQ[500:900], [4] * 400), "poor"))
        self.assertEqual(poor["reads"][0]["status"], "poor")
        self.assertEqual(poor["status"], "no_match")

    def test_a_read_from_something_else_doesnt_match(self):
        out = check(sv.Read("junk", _random(800)))
        self.assertEqual((out["status"], out["reads"][0]["placed"]), ("no_match", False))
        self.assertEqual(out["verdict"], "The read doesn't match this plasmid.")

    def test_reads_together_make_the_clones_verdict(self):
        forward = sv.Read("F", mutate(SEQ[800:1600], {1004: "C"}, start=800))
        reverse = sv.Read("R", sv.reverse_complement(mutate(SEQ[950:1800], {1004: "C"}, start=950)))
        junk = sv.Read("junk", _random(600))
        out = check(forward, reverse, junk)
        (d,) = out["differences"]
        self.assertEqual((d["position"], d["reads"], sorted(d["strands"]), d["reads_covering"]),
                         (1005, ["F", "R"], ["forward", "reverse"], 2))
        self.assertEqual(out["covered"], [[801, 1800]])
        self.assertEqual(out["covered_bp"], 1000)
        self.assertTrue(out["verdict"].startswith("1 difference from the map: T→C at 1,005 in GeneF (L2P)."))
        self.assertIn("1 read doesn't match or was too poor to check.", out["verdict"])

    def test_a_difference_carries_its_trace(self):
        bases = mutate(SEQ[900:1500], {1004: "C"}, start=900)
        read = sv.read_abif(abif(bases, [40] * len(bases), trace=traces(bases)), "clone.ab1")
        self.assertEqual(sorted(read.extra["trace"]["channels"]), list("ACGT"))
        out = check(read)
        [d] = out["reads"][0]["differences"]
        picture = d["trace"]
        self.assertEqual(sorted(picture["channels"]), list("ACGT"))
        self.assertLessEqual(len(picture["channels"]["A"]), sv.TRACE_POINTS)
        [at] = [b for b in picture["bases"] if b["at"]]
        self.assertEqual(at["base"], "C")
        self.assertEqual(len(picture["bases"]), 2 * sv.TRACE_FLANK + 1)
        self.assertNotIn("mixed", out["reads"][0])
        # The words alone for the API and the assistants.
        lean = sv.without_traces(out)
        self.assertNotIn("trace", lean["differences"][0])
        self.assertTrue(lean["differences"][0]["has_trace"])
        # A text read has none.
        self.assertNotIn("trace", check(sv.Read("r", bases))["reads"][0]["differences"][0])

    def test_second_peaks_all_along_look_like_a_mix(self):
        bases = SEQ[900:1500]
        other = {i: "ACGT"[("ACGT".index(b) + 1) % 4] for i, b in enumerate(bases) if i % 6 == 0}
        read = sv.read_abif(abif(bases, [40] * len(bases), trace=traces(bases, other)), "mix.ab1")
        out = check(read)
        mixed = out["reads"][0]["mixed"]
        self.assertGreaterEqual(mixed["bases"], 90)
        self.assertIsNone(mixed["from_read"])
        self.assertIn("may be a mix of two clones", out["reads"][0]["verdict"])
        self.assertIn("mix.ab1 looks like a mix of two clones", out["verdict"])

    def test_two_sequences_from_one_place_are_an_indel_mixture(self):
        bases = SEQ[900:1500]
        other = {i: "ACGT"[("ACGT".index(bases[i]) + 2) % 4] for i in range(300, len(bases))}
        read = sv.read_abif(abif(bases, [40] * len(bases), trace=traces(bases, other)), "indel.ab1")
        out = check(read)
        mixed = out["reads"][0]["mixed"]
        self.assertEqual((mixed["from_read"], mixed["from_plasmid"]), (301, 1201))
        self.assertIn("From read base 301 (near 1,201 on the map)", out["reads"][0]["verdict"])

    def test_a_1kb_read_against_15kb_is_quick(self):
        plasmid = _random(15000)
        read = mutate(plasmid[7000:8000], {7100 + 9 * i: "A" if plasmid[7100 + 9 * i] != "A" else "C" for i in range(8)},
                      start=7000, length=15000)
        reads = [sv.Read("fast", sv.reverse_complement(read))]
        started = time.perf_counter()
        out = sv.verify(plasmid, True, [], reads)
        took = time.perf_counter() - started
        self.assertEqual((out["reads"][0]["start"], out["reads"][0]["end"]), (7001, 8000))
        self.assertEqual(len(out["differences"]), 8)
        self.assertLess(took, 2.0)


# ---------------------------------------------------------------- the page, the API and the tool

class Verifying(Base):
    def setUp(self):
        super().setUp()
        self.rid = self.make_plasmid(self.m, name=uniq("pVerify"))
        execute("update plasmids set full_sequence=?, is_circular=?, features_json=? where id=?", SEQ, True,
                json.dumps(FEATURES), self.rid)
        self.number = one("select plasmid_id from plasmids where id=?", self.rid)
        region = mutate(SEQ[900:1500], {1004: "C"}, start=900)
        self.ab1 = abif(region, [40] * len(region))

    def test_the_page_checks_reads_and_can_keep_them(self):
        # The page checks reads on its Verify tab (tests/test_clones.py); this route is the API's and the assistants'.
        html = self.get_ok(self.m, f"/plasmid/{self.number}")
        self.assertIn("data-verify-tab", html)
        self.assertIn("seq-verify.js", html)
        before = one("select count(*) from plasmid_files where plasmid_row_id=?", self.rid)
        r = self.m.post(f"/plasmids/{self.rid}/verify", data={"reads": (io.BytesIO(self.ab1), "clone3.ab1")},
                        content_type="multipart/form-data")
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["report"]["status"], "differences")
        self.assertEqual(j["report"]["differences"][0]["effect"]["text"], "L2P")
        self.assertEqual(one("select count(*) from plasmid_files where plasmid_row_id=?", self.rid), before)
        # Kept: the file, noted with what it showed; then offered to check again.
        r = self.m.post(f"/plasmids/{self.rid}/verify", data={"reads": (io.BytesIO(self.ab1), "clone3.ab1"),
                                                              "keep": "1", "reads_text": ">p\n" + SEQ[2000:2400]},
                        content_type="multipart/form-data")
        self.assertEqual(r.get_json()["kept"], ["clone3.ab1", "pasted-reads.fa"])
        kept_id = one("select max(id) from plasmid_files where plasmid_row_id=? and name='clone3.ab1'", self.rid)
        kind, notes = row("select kind, notes from plasmid_files where id=?", kept_id)
        self.assertEqual(kind, "trace")
        self.assertIn("Verified against the map", notes)
        self.assertIn("L2P", notes)
        again = self.m.post(f"/plasmids/{self.rid}/verify", data={"kept": str(kept_id)}).get_json()
        self.assertEqual(again["report"]["reads"][0]["name"], "clone3")
        # Nothing to read, or a file that isn't a read, is said.
        bad = self.m.post(f"/plasmids/{self.rid}/verify", data={"reads": (io.BytesIO(b"PK\x03\x04\x00"), "x.zip")},
                          content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("x.zip isn't a sequencing read", bad.get_json()["error"])
        zh = self.m.post(f"/plasmids/{self.rid}/verify", data={"reads_text": SEQ[900:1400]},
                         headers={"Accept-Language": "zh-CN"}).get_json()
        self.assertIn("与图谱一致", zh["report"]["verdict"])

    def test_someone_who_cant_edit_it_checks_but_doesnt_keep(self):
        execute("update plasmids set owner=?, is_shared=? where id=?", self.member, False, self.rid)
        self.assertNotIn('name="keep"', self.get_ok(self.o, f"/plasmid/{self.number}"))
        before = one("select count(*) from plasmid_files where plasmid_row_id=?", self.rid)
        j = self.o.post(f"/plasmids/{self.rid}/verify", data={"reads": (io.BytesIO(self.ab1), "c.ab1"), "keep": "1"},
                        content_type="multipart/form-data").get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["kept"], [])
        self.assertEqual(one("select count(*) from plasmid_files where plasmid_row_id=?", self.rid), before)

    def test_the_api_with_files_text_and_captures(self):
        tok = self.token(self.m, scope="read")
        updated = one("select updated_at from plasmids where id=?", self.rid)
        r = self.call("post", f"/plasmids/{self.number}/verify", tok,
                      data={"reads": (io.BytesIO(self.ab1), "clone3.ab1")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual((body["status"], body["plasmid"]["plasmid_id"]), ("differences", self.number))
        self.assertEqual(body["reads"][0]["strand"], "forward")
        r = self.call("post", f"/plasmids/{self.number}/verify", tok,
                      json={"reads": [{"name": "T7 read", "sequence": SEQ[100:700]}, SEQ[1000:1500]]})
        self.assertEqual([x["name"] for x in r.get_json()["reads"]], ["T7", "read-2"])
        self.assertEqual(r.get_json()["status"], "match")
        # A read uploaded as a capture (an .ab1 is a capture's file now).
        r = self.m.post("/captures/new", data={"text": "clone 3, T7", "file": (io.BytesIO(self.ab1), "clone3.ab1")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        capture = one("select max(id) from captures where owner_username=?", self.member)
        self.assertEqual(one("select file_type from captures where id=?", capture), "application/x-abi")
        got = self.call("get", f"/captures/{capture}", tok).get_json()
        self.assertIn("verify_sequencing", got["file_note"])
        self.assertNotIn("file_text", got)
        r = self.call("post", f"/plasmids/{self.number}/verify", tok, json={"captures": [capture]})
        self.assertEqual(r.get_json()["differences"][0]["text"], "T→C at 1,005 in GeneF (L2P)")
        self.assertEqual(self.call("post", f"/plasmids/{self.number}/verify", tok,
                                   json={"captures": [capture + 999]}).status_code, 404)
        self.assertEqual(self.call("post", f"/plasmids/{self.number}/verify", tok, json={}).status_code, 400)
        self.assertEqual(self.call("post", "/plasmids/99999999/verify", tok, json={"reads": ["ACGT"]}).status_code, 404)
        self.assertEqual(one("select updated_at from plasmids where id=?", self.rid), updated)   # nothing changed
        self.assertIn("/plasmids/{plasmid_id}/verify", json.dumps(self.call("get", "/openapi.json", tok).get_json()))

    def test_the_assistants_tool(self):
        tok = self.token(self.m, scope="propose", label="Claude")
        rpc = lambda method, params: self.bare.post(  # noqa: E731
            "/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"Authorization": f"Bearer {tok}"}).get_json()["result"]
        tools = {t["name"]: t for t in rpc("tools/list", {})["tools"]}
        self.assertTrue(tools["verify_sequencing"]["annotations"]["readOnlyHint"])
        self.assertEqual(tools["verify_sequencing"]["title"], "Verify sequencing")
        out = rpc("tools/call", {"name": "verify_sequencing", "arguments": {
            "plasmid": f"#{self.number}", "reads": [mutate(SEQ[900:1500], {1004: "C"}, start=900)]}})
        self.assertFalse(out["isError"])
        self.assertEqual(out["structuredContent"]["differences"][0]["effect"]["text"], "L2P")
        ref = {"kind": "plasmid", "id": self.rid}
        out = rpc("tools/call", {"name": "verify_sequencing", "arguments": {"ref": ref, "reads": [SEQ[0:500]]}})
        self.assertEqual(out["structuredContent"]["status"], "match")
        out = rpc("tools/call", {"name": "verify_sequencing", "arguments": {"plasmid": "14"}})
        self.assertTrue(out["isError"])
        # The local server offers it too, over the same API.
        from tests.test_mcp import TestApi, bm
        api = TestApi(tok)
        got = bm.verify_sequencing(api, {"plasmid": self.number, "reads": [SEQ[2000:2600]]})
        self.assertEqual(got["status"], "match")
        with open(bm.__file__, encoding="utf-8") as source:
            self.assertIn('@server.tool(name="verify_sequencing"', source.read())


if __name__ == "__main__":
    unittest.main()
