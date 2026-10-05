"""The lab's feature library (app/feature_library.py): elements kept by
sequence from annotated plasmids, and Detect features, which marks them on
another plasmid's map."""
from __future__ import annotations

import json
import random

from tests.base import AppTestCase, count, flash_text, one, row, rows, uniq

from app import feature_library as fl
from app.primer_records import reverse_complement


def bases(n: int, seed) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(n))


ITR = bases(40, 1)
WPRE = bases(60, 2)
FILLER = bases(200, 3)


def features(rid: int) -> list[dict]:
    return json.loads(one("select features_json from plasmids where id=?", rid) or "[]")


class CategoryTests(AppTestCase):

    def test_elements_sort_by_what_they_are(self):
        for name, ftype, want in (("5' ITR", "repeat_region", "viral"), ("WPRE", "misc_feature", "viral"),
                                  ("3' LTR (ΔU3)", "LTR", "viral"), ("RRE", "misc_feature", "viral"),
                                  ("AmpR", "CDS", "selection"), ("AmpR promoter", "promoter", "promoter"), ("CMV enhancer", "enhancer", "promoter"),
                                  ("hSyn promoter", "misc_feature", "promoter"), ("ori", "rep_origin", "origin"),
                                  ("bGH poly(A) signal", "polyA_signal", "terminator"), ("EGFP", "CDS", "coding"),
                                  ("loxP", "protein_bind", "other")):
            with self.subTest(name=name):
                self.assertEqual(fl.category_of(name, ftype), want)

    def test_detect_finds_both_strands_and_across_the_origin(self):
        class E:  # an entry, without the database
            def __init__(self, name, sequence):
                self.name, self.sequence, self.type, self.color, self.notes_json = name, sequence, "misc_feature", "", "{}"
        seq = WPRE[30:] + FILLER + reverse_complement(ITR) + FILLER[:50] + WPRE[:30]
        found = fl.detect([E("WPRE", WPRE), E("ITR", ITR)], seq, True, [])
        by_name = {f["name"]: f for f in found}
        self.assertEqual((by_name["WPRE"]["start"], by_name["WPRE"]["end"], by_name["WPRE"]["direction"]),
                         (len(seq) - 30, 29, 1))
        self.assertEqual((by_name["ITR"]["start"], by_name["ITR"]["direction"]), (230, -1))
        # Linear, the one across the end isn't there; one already marked isn't marked again.
        self.assertEqual([f["name"] for f in fl.detect([E("WPRE", WPRE)], seq, False, [])], [])
        marked = [{"name": "ITR", "start": 232, "end": 260, "direction": 1}]
        self.assertEqual([f["name"] for f in fl.detect([E("ITR", ITR)], seq, True, marked)], [])


SBOL = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://sbols.org/v1#" xmlns:so="http://purl.obolibrary.org/obo/SO_">
  <DnaComponent rdf:about="http://example.org/Part:loxP-001">
    <rdf:type rdf:resource="http://purl.obolibrary.org/obo/SO_0000057"/>
    <displayId>loxP-001</displayId><name>loxP-001</name>
    <description>Cre recombinase site</description>
    <dnaSequence><DnaSequence rdf:about="http://example.org/Part:loxP-001/seq">
      <nucleotides>ataacttcgtatagcatacattatacgaagttat</nucleotides>
    </DnaSequence></dnaSequence>
  </DnaComponent>
  <DnaComponent rdf:about="http://example.org/Part:T7-001">
    <rdf:type rdf:resource="http://purl.obolibrary.org/obo/SO_0000167"/>
    <displayId>T7_promoter-001</displayId><name>T7 promoter</name><description></description>
    <dnaSequence><DnaSequence rdf:about="http://example.org/Part:T7-001/seq">
      <nucleotides>taatacgactcactatagg</nucleotides>
    </DnaSequence></dnaSequence>
  </DnaComponent>
  <DnaComponent rdf:about="http://example.org/Part:empty">
    <displayId>no-sequence</displayId><name>Nameless</name>
  </DnaComponent>
</rdf:RDF>
"""


def supplement_zip(sbol: bytes = None) -> bytes:
    """A stand-in for Europe PMC's answer: a zip holding the supplement's
    own zip, which holds SBOL_files/labhost_All.xml."""
    import io
    import zipfile
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("SBOL_files/labhost_All.xml", sbol if sbol is not None else SBOL.encode())
        z.writestr("SBOL_files/labhost_Mammalian_Cells.xml", "<rdf:RDF/>")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("supp_gkv272_File005.pdf", b"%PDF-1.4 not the one")
        z.writestr("supp_gkv272_File009.zip", inner.getvalue())
    return outer.getvalue()


class FeaturePackTests(AppTestCase):
    """The common-features pack: read from the archive's supplement, never
    carried in the app (app/feature_pack.py)."""

    def test_the_sbol_in_the_supplement_becomes_elements(self):
        from app import feature_pack as pack
        found = pack.elements(pack.sbol_from_supplement(supplement_zip()))
        self.assertEqual([(e["name"], e["type"], e["sequence"]) for e in found], [
            ("loxP", "protein_bind", "ATAACTTCGTATAGCATACATTATACGAAGTTAT"),
            ("T7 promoter", "promoter", "TAATACGACTCACTATAGG")])
        self.assertEqual(found[0]["description"], "Cre recombinase site")

    def test_a_download_that_is_not_the_pack_is_said_plainly(self):
        from app import feature_pack as pack
        for raw in (b"", b"not a zip at all", supplement_zip(b"<rdf:RDF")):
            with self.subTest(raw=raw[:12]):
                with self.assertRaises(pack.PackUnavailable):
                    pack.elements(pack.sbol_from_supplement(raw)) if raw else pack.sbol_from_supplement(raw)

    def test_the_work_adds_the_elements_and_says_it_is_done(self):
        from unittest import mock
        from app import feature_pack as pack
        from app.db import SessionLocal
        with mock.patch.object(pack, "fetch_bytes", return_value=supplement_zip()):
            pack._run("alex")        # what the background thread does
        self.assertEqual(rows("select name, source_name from feature_library where source_name!='' order by name"),
                         [("T7 promoter", "GenoLIB"), ("loxP", "GenoLIB")])
        with SessionLocal() as s:
            state = pack.status(s)
        self.assertEqual((state["state"], state["added"], state["total"], state["by"]), ("done", 2, 2, "alex"))
        self.assertIn("2 from the pack", self.get_ok(self.a, "/plasmids/features"))
        # The variant number the library gives its entries is not a name a map should show.
        self.assertNotIn("loxP-001", self.get_ok(self.a, "/plasmids/features"))

    def test_a_download_that_fails_leaves_the_library_alone_and_says_why(self):
        from unittest import mock
        from app import feature_pack as pack
        before = count("feature_library")
        with mock.patch.object(pack, "fetch_bytes", side_effect=pack.PackUnavailable("no route to host")):
            pack._run("alex")
        self.assertEqual(count("feature_library"), before)
        page = self.get_ok(self.a, "/plasmids/features")
        self.assertIn("The last try did not finish: no route to host", page)
        self.assertIn("Add the common-features pack", page)   # and it can be tried again

    def test_the_button_starts_one_run_in_the_background_for_an_admin(self):
        from unittest import mock
        from app import feature_pack as pack
        from app.db import SessionLocal
        # The thread's work is stubbed out: the route must not wait for it.
        with mock.patch.object(pack, "_run") as work:
            r = self.post(self.a, "/plasmids/features/pack")
            self.assertFlash(r, "Downloading the common-features pack", "info")
            with SessionLocal() as s:
                self.assertEqual(pack.status(s)["state"], "running")
            # A second ask while it runs starts nothing more.
            self.assertFlash(self.post(self.a, "/plasmids/features/pack"), "already being downloaded", "info")
            self.assertEqual(work.call_count, 1)
            self.assertIn("Downloading…", self.get_ok(self.a, "/plasmids/features"))
        self.assertFlash(self.post(self.m, "/plasmids/features/pack"), "Admin access required.", "error")

    def test_a_run_that_stopped_without_finishing_does_not_block_the_next(self):
        from datetime import datetime, timedelta
        from app import feature_pack as pack
        from app.db import SessionLocal
        with SessionLocal() as s:
            pack._save_status(s, state="running", by="alex")
            s.commit()
        with SessionLocal() as s:
            stale = (datetime.utcnow() - pack.STALE - timedelta(minutes=1)).isoformat(timespec="seconds")
            s.execute(__import__("sqlalchemy").text(
                "update app_settings set value = :v where key = :k"),
                {"v": f'{{"state": "running", "by": "alex", "at": "{stale}"}}', "k": pack.STATUS_KEY})
            s.commit()
        with SessionLocal() as s:
            self.assertEqual(pack.status(s)["state"], "failed")


class LibraryRouteTests(AppTestCase):

    def setUp(self):
        # Elements of their own: the library keeps one entry per sequence.
        name = uniq("pAAV-src-")
        self.itr, self.wpre = bases(40, name + "itr"), bases(60, name + "wpre")
        gb = self.genbank(name, FILLER[:20] + self.itr + FILLER[20:80] + self.wpre + FILLER[80:120],
                          [("repeat_region", 21, 60, uniq("AAV2 ITR "), False), ("misc_feature", 121, 180, uniq("WPRE "), False),
                           ("misc_feature", 1, 20, "Feature 1", False)])
        self.source = self.make_plasmid(self.m, name=name)
        self.post(self.m, f"/plasmids/{self.source}/upload-sequence", {"sequence_text": gb})
        self.itr_name, self.wpre_name = [f["name"] for f in features(self.source)][:2]

    @staticmethod
    def genbank(name, sequence, feats):
        lines = [f"LOCUS       {name[:16]} {len(sequence)} bp    DNA     circular SYN 01-JAN-2026",
                 "FEATURES             Location/Qualifiers"]
        for ftype, start, end, label, rev in feats:
            loc = f"complement({start}..{end})" if rev else f"{start}..{end}"
            lines += [f"     {ftype:<16}{loc}", f'                     /label="{label}"']
        lines.append("ORIGIN")
        for i in range(0, len(sequence), 60):
            lines.append(f"{i + 1:>9} " + " ".join(sequence[i:i + 60][j:j + 10].lower() for j in range(0, 60, 10)).strip())
        lines.append("//")
        return "\n".join(lines) + "\n"

    def library(self):
        return {r[0]: r[1] for r in rows("select name, category from feature_library")}

    def test_add_to_library_keeps_named_features_once(self):
        r = self.post(self.m, f"/plasmids/{self.source}/features-to-library")
        self.assertFlash(r, "Added 2 elements to the feature library.", "success")
        self.assertEqual(self.library().get(self.itr_name), "viral")
        self.assertEqual(self.library().get(self.wpre_name), "viral")
        self.assertNotIn("Feature 1", self.library())
        self.assertFlash(self.post(self.m, f"/plasmids/{self.source}/features-to-library"),
                         "already has every named feature", "info")

    def test_detect_features_marks_them_on_another_plasmid_and_keeps_a_version(self):
        self.post(self.m, f"/plasmids/{self.source}/features-to-library")
        target = self.make_plasmid(self.m, sequence_text=FILLER[:90] + reverse_complement(self.wpre) + FILLER[90:] + self.itr)
        r = self.post(self.m, f"/plasmids/{target}/detect-features")
        self.assertFlash(r, "Marked 2 features from the library", "success")
        marked = {f["name"]: f for f in features(target)}
        self.assertEqual((marked[self.wpre_name]["start"], marked[self.wpre_name]["direction"]), (90, -1))
        self.assertEqual(marked[self.itr_name]["start"], 90 + 60 + 110)
        self.assertEqual(one("select how from plasmid_sequence_versions where plasmid_row_id=? order by id desc limit 1",
                             target), "detect")
        # Again: each is already marked.
        self.assertFlash(self.post(self.m, f"/plasmids/{target}/detect-features"), "already marked", "info")
        # Someone who may not edit it can't.
        self.assertFlash(self.post(self.o, f"/plasmids/{target}/detect-features"), "belongs to", "error")

    def test_a_big_library_is_searched_and_shown_a_shelf_at_a_time(self):
        from app import feature_library as fl
        from app.db import SessionLocal
        with SessionLocal() as s:
            known = fl.hashes(s)
            for i in range(120):
                fl.add(s, name=f"Filler {i}", ftype="misc_feature", sequence=bases(30, f"fill{i}"),
                       user="alex", known=known)
            fl.add(s, name=uniq("hSyn promoter "), ftype="promoter", sequence=bases(60, "hsyn"),
                   user="alex", known=known)
            s.commit()
        page = self.get_ok(self.m, "/plasmids/features")
        self.assertIn("search to narrow it", page)
        self.assertEqual(page.count("Filler "), 100)        # a shelf-full, not all 120
        found = self.get_ok(self.m, "/plasmids/features?q=hSyn")
        self.assertIn("hSyn promoter", found)
        self.assertNotIn("Filler ", found)

    def test_the_library_page_groups_elements_and_only_admins_collect(self):
        self.post(self.m, f"/plasmids/{self.source}/features-to-library")
        html = self.get_ok(self.m, "/plasmids/features")
        self.assertIn("Viral elements", html)
        self.assertIn(self.itr_name, html)
        self.assertNotIn("Collect from every plasmid", html)
        self.assertIn("Collect from every plasmid", self.get_ok(self.a, "/plasmids/features"))
        self.assertIn(self.m.post("/plasmids/features/collect").status_code, (302, 403))
        entry = one("select id from feature_library where name=?", self.itr_name)
        self.assertFlash(self.post(self.o, f"/plasmids/features/{entry}/delete"), "Only an admin", "error")
        self.assertFlash(self.post(self.m, f"/plasmids/features/{entry}/delete"), "Took", "success")
        self.assertIsNone(one("select id from feature_library where id=?", entry))

    def test_files_fill_the_library_without_making_plasmids(self):
        import io
        name = uniq("pRef-")
        itr, wpre = bases(40, name + "i"), bases(60, name + "w")
        gb = self.genbank(name, FILLER[:20] + itr + FILLER[20:80] + wpre + FILLER[80:120],
                          [("repeat_region", 21, 60, "AAV2 ITR ref", False), ("misc_feature", 121, 180, "WPRE ref", False)])
        plasmids_before = count("plasmids")
        r = self.post(self.m, "/plasmids/features/import",
                      {"files": [(io.BytesIO(gb.encode()), "pRef.gb"), (io.BytesIO(b"not a sequence"), "notes.txt")]},
                      content_type="multipart/form-data")
        self.assertFlash(r, "Added 2 elements to the feature library. Read from 1 file.", "success")
        self.assertIn("notes.txt", flash_text(r))
        self.assertEqual(count("plasmids"), plasmids_before)   # a file is not a plasmid
        self.assertEqual({n for n, in rows("select name from feature_library")} & {"AAV2 ITR ref", "WPRE ref"},
                         {"AAV2 ITR ref", "WPRE ref"})
        self.assertEqual(one("select sequence from feature_library where name=?", "WPRE ref"), wpre)
        # The same file again adds nothing.
        r = self.post(self.m, "/plasmids/features/import",
                      {"files": [(io.BytesIO(gb.encode()), "pRef.gb")]}, content_type="multipart/form-data")
        self.assertFlash(r, "already has every named feature", "info")
        self.assertFlash(self.post(self.m, "/plasmids/features/import", {}, content_type="multipart/form-data"),
                         "Choose the annotated files", "info")

    def test_the_plasmid_page_offers_detect_and_add(self):
        html = self.get_ok(self.m, f"/plasmid/{one('select plasmid_id from plasmids where id=?', self.source)}")
        self.assertIn("Detect features", html)
        self.assertIn("Add to library", html)


class PLannotateTests(AppTestCase):
    """Annotating with pLannotate, the separate program a lab may install
    (app/plannotate.py). The tool itself is never run in the tests."""

    SEQ = bases(400, "plann")

    def setUp(self):
        self.rid = self.make_plasmid(self.m, sequence_text=self.SEQ)

    def gbk(self, feats):
        lines = [f"LOCUS       plasmid {len(self.SEQ)} bp    DNA     circular SYN 01-JAN-2026",
                 "FEATURES             Location/Qualifiers"]
        for name, start, end in feats:
            lines += [f"     CDS             {start}..{end}", f'                     /label="{name}"']
        lines.append("ORIGIN")
        for i in range(0, len(self.SEQ), 60):
            lines.append(f"{i + 1:>9} " + self.SEQ[i:i + 60].lower())
        lines.append("//")
        return "\n".join(lines) + "\n"

    def features(self):
        return [f["name"] for f in json.loads(one("select features_json from plasmids where id=?", self.rid) or "[]")]

    def test_the_button_is_there_only_when_the_lab_has_set_it_up(self):
        from unittest import mock
        from app import plannotate
        page = f"/plasmid/{one('select plasmid_id from plasmids where id=?', self.rid)}"
        self.assertNotIn("Annotate with pLannotate", self.get_ok(self.m, page))
        self.assertFlash(self.post(self.m, f"/plasmids/{self.rid}/annotate"), "not set up on this server", "info")
        with mock.patch.dict("os.environ", {plannotate.ENV_VAR: "plannotate"}):
            self.assertIn("Annotate with pLannotate", self.get_ok(self.m, page))
            self.assertEqual(plannotate.command(), ["plannotate"])

    def test_what_it_finds_is_marked_and_kept_as_a_version(self):
        from unittest import mock
        from app import plannotate
        with mock.patch.dict("os.environ", {plannotate.ENV_VAR: "plannotate"}), \
                mock.patch.object(plannotate, "run_tool",
                                  return_value=self.gbk([("EGFP", 10, 60), ("AmpR", 100, 200)])):
            r = self.post(self.m, f"/plasmids/{self.rid}/annotate")
            self.assertFlash(r, "pLannotate marked 2 features: EGFP, AmpR.", "success")
            self.assertEqual(self.features(), ["EGFP", "AmpR"])
            self.assertEqual(one("select how from plasmid_sequence_versions where plasmid_row_id=? "
                                 "order by id desc limit 1", self.rid), "annotate")
            # What the map already marks is not marked twice.
            self.assertFlash(self.post(self.m, f"/plasmids/{self.rid}/annotate"), "found nothing", "info")
            self.assertEqual(self.features(), ["EGFP", "AmpR"])

    def test_a_tool_that_is_missing_or_slow_is_said_plainly(self):
        from unittest import mock
        from app import plannotate
        for problem in ("plannotate is not installed here", "it took longer than 45 seconds"):
            with mock.patch.dict("os.environ", {plannotate.ENV_VAR: "plannotate"}), \
                    mock.patch.object(plannotate, "run_tool", side_effect=plannotate.ToolFailed(problem)):
                self.assertFlash(self.post(self.m, f"/plasmids/{self.rid}/annotate"), problem, "error")
        self.assertEqual(self.features(), [])

    def test_the_command_is_run_as_a_list_and_never_through_a_shell(self):
        from unittest import mock
        from app import plannotate
        with mock.patch.dict("os.environ", {plannotate.ENV_VAR: "/opt/envs/pl/bin/plannotate --quiet"}):
            self.assertEqual(plannotate.command(), ["/opt/envs/pl/bin/plannotate", "--quiet"])
            with mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(stdout="", stderr="nothing written")
                with self.assertRaises(plannotate.ToolFailed):
                    plannotate.run_tool(self.SEQ, True)
                call = run.call_args
                self.assertEqual(call.args[0][:2], ["/opt/envs/pl/bin/plannotate", "--quiet"])
                self.assertIn("batch", call.args[0])
                self.assertNotIn("--linear", call.args[0])          # circular is its default
                self.assertNotIn("shell", call.kwargs)              # never through a shell
                self.assertEqual(call.kwargs["timeout"], plannotate.TIMEOUT)
            with mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(stdout="", stderr="")
                with self.assertRaises(plannotate.ToolFailed):
                    plannotate.run_tool(self.SEQ, False)
                self.assertIn("--linear", run.call_args.args[0])
        # Unbalanced quotes are not a command.
        with mock.patch.dict("os.environ", {plannotate.ENV_VAR: 'plannotate "oops'}):
            self.assertEqual(plannotate.command(), [])
            self.assertFalse(plannotate.available())
