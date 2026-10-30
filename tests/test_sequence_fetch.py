"""A plasmid's sequence by its Addgene number or GenBank accession
(app/sequence_fetch.py, app/sequence_fetch_routes.py). Nothing here reaches
the network: a fake opener stands in for NCBI and Addgene."""
from __future__ import annotations

import io
import os
import random
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from tests.base import AppTestCase, uniq

from app import sequence_fetch as sf


def genbank(name: str = "pUC19", length: int = 2686) -> str:
    seq = "".join(random.Random(4).choice("acgt") for _ in range(length))
    lines = [f"LOCUS       {name:<24}{length} bp    DNA     circular SYN 01-JAN-2026",
             "FEATURES             Location/Qualifiers",
             "     CDS             1626..2486", '                     /label="AmpR"',
             "     rep_origin      complement(867..1455)", '                     /label="ori"', "ORIGIN"]
    for i in range(0, length, 60):
        lines.append(f"{i + 1:>9} " + " ".join(seq[j:j + 10] for j in range(i, min(i + 60, length), 10)))
    return "\n".join(lines + ["//"])


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(pages: dict):
    """A stand-in for urlopen: the body for each address that starts with a key."""
    seen = []

    def open_(req, timeout=None):
        url = req.full_url
        seen.append(url)
        for prefix, body in pages.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return Reply(body.encode() if isinstance(body, str) else body)
        raise HTTPError(url, 404, "not found", {}, None)
    open_.seen = seen
    return open_


ADDGENE_PAGE = ('<a href="https://media.addgene.org/snapgene-media/v1/sequences/1/x/'
                'addgene-plasmid-11150-sequence-1.gbk">GenBank</a>')


class WhatWasTyped(unittest.TestCase):
    def test_addgene_and_ncbi(self):
        for raw, want in (("Addgene #11150", ("addgene", "11150")), ("addgene 11150", ("addgene", "11150")),
                          ("11150", ("addgene", "11150")), ("#11150", ("addgene", "11150")),
                          ("L09137", ("ncbi", "L09137")), ("nc_045512.2", ("ncbi", "NC_045512.2")),
                          ("NCBI MN908947.3", ("ncbi", "MN908947.3"))):
            self.assertEqual(sf.parse_ref(raw), want, raw)

    def test_what_is_neither(self):
        for raw in ("", "pUC19", "hello world"):
            with self.assertRaises(sf.FetchError):
                sf.parse_ref(raw)


class Fetching(unittest.TestCase):
    def test_ncbi(self):
        got = sf.fetch("L09137", opener=opener({sf.EFETCH: genbank()}))
        self.assertEqual((got["source"], got["ref"], got["label"]), ("ncbi", "L09137", "NCBI L09137"))
        self.assertTrue(got["text"].startswith("LOCUS"))

    def test_addgene_takes_the_genbank_file_its_page_links(self):
        o = opener({"https://www.addgene.org/11150/": ADDGENE_PAGE, "https://media.addgene.org/": genbank("pLKO")})
        got = sf.fetch("Addgene #11150", opener=o)
        self.assertEqual((got["source"], got["label"]), ("addgene", "Addgene #11150"))
        self.assertIn("pLKO", got["text"])
        self.assertTrue(got["file"].endswith(".gbk"))

    def test_failing_politely(self):
        cases = (
            ({}, "L09137", "not_found"),                                                 # 404
            ({sf.EFETCH: URLError("no route")}, "L09137", "offline"),
            ({sf.EFETCH: "Error: id not found"}, "L09137", "not_found"),
            ({"https://www.addgene.org/": "<html>no files</html>"}, "11150", "no_file"),
            ({sf.EFETCH: "LOCUS" + "a" * (sf.MOST + 10)}, "L09137", "too_big"),
        )
        for pages, raw, code in cases:
            with self.assertRaises(sf.FetchError) as caught:
                sf.fetch(raw, opener=opener(pages))
            self.assertEqual(caught.exception.code, code, raw)

    def test_switched_off_reaches_nothing(self):
        o = opener({sf.EFETCH: genbank()})
        with mock.patch.dict(os.environ, {"BIOMANAGER_SEQUENCE_FETCH": "0"}):
            with self.assertRaises(sf.FetchError) as caught:
                sf.fetch("L09137", opener=o)
        self.assertEqual(caught.exception.code, "switched_off")
        self.assertEqual(o.seen, [])


class ThroughThePage(AppTestCase):
    def test_found_and_described(self):
        with mock.patch.object(sf, "urlopen", opener({sf.EFETCH: genbank()})):
            j = self.m.get("/plasmids/fetch-sequence?ref=L09137").get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual((j["name"], j["length"], j["features"], j["circular"]), ("pUC19", 2686, 2, True))
        self.assertTrue(j["text"].startswith("LOCUS"))

    def test_offline_says_so(self):
        with mock.patch.object(sf, "urlopen", opener({sf.EFETCH: URLError("down")})):
            r = self.m.get("/plasmids/fetch-sequence?ref=L09137")
        self.assertEqual(r.status_code, 503)
        self.assertIn("couldn’t reach NCBI or Addgene", r.get_json()["error"])

    def test_the_fetched_text_makes_a_plasmid(self):
        # What the page does: the text goes in the paste box, and Create reads it.
        with mock.patch.object(sf, "urlopen", opener({sf.EFETCH: genbank("pFetched", 3000)})):
            text = self.m.get("/plasmids/fetch-sequence?ref=L09137").get_json()["text"]
        row = self.make_plasmid(self.m, name=uniq("fetched-"), sequence_text=text)
        from tests.base import one
        self.assertEqual(len(one("select full_sequence from plasmids where id=?", row)), 3000)

    def test_the_row_hides_when_switched_off(self):
        self.assertIn("data-seq-fetch", self.get_ok(self.m, "/plasmids"))
        with mock.patch.dict(os.environ, {"BIOMANAGER_SEQUENCE_FETCH": "0"}):
            self.assertNotIn("data-seq-fetch", self.get_ok(self.m, "/plasmids"))


if __name__ == "__main__":
    unittest.main()
