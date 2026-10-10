"""Find saved primers (GitHub #62): on a plasmid's page, a switch that looks
through every primer the lab has saved, whatever plasmid it names, for ones
that bind this sequence, so nobody orders a primer the lab already has.

The scan indexes the sequence's 15-base stretches once and asks
binding_sites only of the primers whose 3′ end is among them
(primer_records.TemplateIndex), so it must find exactly what asking
binding_sites of every primer would."""
from __future__ import annotations

import json
import random
import unittest

from tests.base import location, one, uniq
from tests.test_inventory import InventoryCase

from app import primer_records as pr


def random_dna(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("ACGT") for _ in range(n))


class IndexMatchesBruteForce(unittest.TestCase):
    """TemplateIndex.sites == binding_sites, primer by primer."""

    def primers_for(self, rng: random.Random, template: str, circular: bool) -> list[str]:
        n = len(template)
        out = []
        for _ in range(300):
            length = rng.randint(15, 32)
            start = rng.randrange(n)
            if not circular:
                start = rng.randrange(n - length)
            region = (template + template)[start:start + length]   # may cross the origin
            primer = region if rng.random() < 0.5 else pr.reverse_complement(region)
            roll = rng.random()
            if roll < 0.3:      # a restriction site or homology arm on the 5′ end
                primer = random_dna(rng, rng.randint(1, 25)) + primer
            elif roll < 0.4:    # one wrong base somewhere in the 5′ part
                i = rng.randrange(max(len(primer) - 15, 1))
                primer = primer[:i] + ("A" if primer[i] != "A" else "C") + primer[i + 1:]
            elif roll < 0.5:    # a 3′ mismatch: doesn't bind
                primer = primer[:-1] + ("G" if primer[-1] != "G" else "T")
            out.append(primer)
        out += [random_dna(rng, rng.randint(15, 30)) for _ in range(100)]   # unrelated
        # Across the origin, both strands, exactly.
        out += [template[-10:] + template[:12], pr.reverse_complement(template[-7:] + template[:16])]
        return out

    def test_same_sites_on_both_strands_with_tails_and_across_the_origin(self):
        rng = random.Random(62)
        for circular in (True, False):
            template = random_dna(rng, 3000)
            # A repeat, so some primers bind twice.
            template = template[:1000] + template[200:260] + template[1060:]
            index = pr.TemplateIndex(template, circular)
            found = tails = reverse = across = 0
            for primer in self.primers_for(rng, template, circular):
                expected = pr.binding_sites(template, circular, primer)
                self.assertEqual(index.sites(primer), expected, (circular, primer))
                found += bool(expected)
                tails += any(s["tail"] for s in expected)
                reverse += any(s["direction"] < 0 for s in expected)
                across += any(s["start"] > s["end"] for s in expected)
            # The comparison covered every case it is about.
            self.assertGreater(found, 150)
            self.assertGreater(tails, 50)
            self.assertGreater(reverse, 50)
            if circular:
                self.assertGreater(across, 1)
            else:
                self.assertEqual(across, 0)

    def test_short_primers_and_unclear_3_ends_are_passed_over(self):
        template = random_dna(random.Random(7), 500)
        index = pr.TemplateIndex(template, True)
        self.assertEqual(index.sites(template[100:112]), [])                 # under 15 bases
        self.assertEqual(index.sites(template[100:118] + "N" + template[119:125]), [])   # N in the 3′ end
        # N (or any IUPAC letter) in the 5′ part is a tail, as binding_sites says.
        primer = "NNRY" + template[100:122]
        site, = index.sites(primer)
        self.assertEqual((site["start"], site["end"], site["tail"]), (100, 121, 4))
        self.assertEqual(index.sites("5'-" + template[200:220].lower() + "-3'"),
                         pr.binding_sites(template, True, template[200:220]))
        self.assertEqual(pr.TemplateIndex("", True).sites(template[:20]), [])


class FindSavedPrimers(InventoryCase):
    """GET /plasmids/<id>/primer-sites, and the switch on the page."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rng = random.Random(6262)   # the same sequence every run
        cls.seq = random_dna(rng, 900)
        cls.name = uniq("pScan")
        cls.rid = cls.make_plasmid(cls.a, name=cls.name, sequence_text=cls.seq)
        cls.number = one("select plasmid_id from plasmids where id=?", cls.rid)
        # Pasted bases make a linear plasmid; the editor makes it circular.
        cls.a.post(f"/plasmids/{cls.rid}/sequence-save", json={"sequenceData": {
            "sequence": cls.seq, "circular": True, "name": cls.name}})
        # The lab's Primers database (the card's), and another the lab keeps.
        first = "select key from inventory_modules where kind='primers' and private_to='' " \
                "and share_group_id is null order by position, id limit 1"
        if one(first) is None:
            cls.new_module(cls.a, "primers")
        cls.lab_key = one(first)
        cls.oligos = cls.new_module(cls.a, "primers", uniq("Oligos "))
        assert cls.oligos != cls.lab_key
        s = cls.seq
        cls.ids = {
            # On the card already: its Plasmid column names this plasmid.
            "card": cls.make_item(cls.a, cls.lab_key, uniq("card-F"), attr_sequence=s[0:20],
                                  attr_template=str(cls.number)),
            "exact": cls.make_item(cls.a, cls.lab_key, uniq("exact-F"), attr_sequence=s[100:122]),
            "tail": cls.make_item(cls.a, cls.oligos, uniq("tail-F"), attr_sequence=cls.tail6(s[299]) + s[300:320]),
            "reverse": cls.make_item(cls.a, cls.oligos, uniq("rev-R"),
                                     attr_sequence=pr.reverse_complement(s[500:524])),
            "origin": cls.make_item(cls.a, cls.oligos, uniq("ori-F"), attr_sequence=s[-8:] + s[:14]),
            "none": cls.make_item(cls.a, cls.oligos, uniq("none-F"), attr_sequence="ACGTTGCA" * 3),
            # Names this plasmid, but in a database the card doesn't list.
            "linked": cls.make_item(cls.a, cls.oligos, uniq("link-F"), attr_sequence=s[700:720],
                                    attr_template=str(cls.number)),
        }

    @staticmethod
    def tail6(before: str) -> str:
        """A 6-base 5′ tail whose last base isn't the template's next one, so
        exactly 6 bases don't anneal."""
        return "GGATC" + ("A" if before != "A" else "C")

    def scan(self, client=None) -> dict:
        r = (client or self.a).get(f"/plasmids/{self.rid}/primer-sites")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def test_the_answer_lists_each_site_with_where_and_how_it_binds(self):
        j = self.scan()
        self.assertTrue(j["ok"])
        self.assertEqual((j["length"], j["circular"]), (900, True))
        hits = {h["id"]: h for h in j["hits"]}
        ids = self.ids
        self.assertNotIn(ids["none"], hits)
        self.assertNotIn(ids["card"], hits)          # the card lists it already
        self.assertGreaterEqual(j["listed"], 1)
        exact = hits[ids["exact"]]
        self.assertEqual((exact["start"], exact["end"], exact["strand"], exact["direction"]), (101, 122, 1, "forward"))
        self.assertEqual((exact["exact"], exact["tail"], exact["annealed"], exact["length"]), (True, 0, 22, 22))
        self.assertEqual(exact["where"], "101–122 →")
        self.assertIn(f"open={ids['exact']}", exact["url"])
        self.assertFalse(exact["linked"])
        tail = hits[ids["tail"]]
        self.assertEqual((tail["start"], tail["end"], tail["exact"], tail["tail"], tail["annealed"]), (301, 320, False, 6, 20))
        rev = hits[ids["reverse"]]
        self.assertEqual((rev["start"], rev["end"], rev["strand"], rev["direction"], rev["exact"]),
                         (501, 524, -1, "reverse", True))
        self.assertEqual(rev["where"], "501–524 ←")
        ori = hits[ids["origin"]]
        self.assertEqual((ori["start"], ori["end"]), (893, 14))   # across the origin
        self.assertTrue(hits[ids["linked"]]["linked"])
        self.assertEqual(j["primers"], len({h["id"] for h in j["hits"]}))
        self.assertEqual(j["sites"], len(j["hits"]))
        self.assertGreaterEqual(j["scanned"], len(ids))
        starts = [h["start"] for h in j["hits"]]
        self.assertEqual(starts, sorted(starts))

    def test_a_primer_in_someone_elses_own_database_is_not_found(self):
        mine = uniq("My oligos ")
        r = self.m.post("/inventory/new", data={"preset": "primers", "label": mine, "audience": "me"})
        key = location(r).split("?")[0].rsplit("/", 1)[1]
        self.assertEqual(one("select private_to from inventory_modules where key=?", key), self.member)
        private = self.make_item(self.m, key, uniq("secret-F"), attr_sequence=self.seq[400:421])
        self.assertIn(private, {h["id"] for h in self.scan(self.m)["hits"]})      # its owner finds it
        self.assertNotIn(private, {h["id"] for h in self.scan(self.o)["hits"]})   # nobody else does
        self.assertIn(self.ids["exact"], {h["id"] for h in self.scan(self.o)["hits"]})

    def test_the_page_has_the_switch_and_the_map_never_keeps_what_it_found(self):
        html = self.get_ok(self.a, f"/plasmid/{self.number}")
        self.assertIn("data-scan-switch", html)
        self.assertIn("Find saved primers", html)
        self.assertIn("Show on map", html)
        self.assertIn(f'data-scan-url="/plasmids/{self.rid}/primer-sites"', html)
        self.assertIn("primer-scan.js", html)
        zh = self.a.get(f"/plasmid/{self.number}", headers={"Accept-Language": "zh-CN"}).get_data(as_text=True)
        self.assertIn("查找已有引物", zh)
        # A plasmid without a sequence has nothing to look along.
        empty = self.make_plasmid(self.a, name=uniq("pEmpty"))
        number = one("select plasmid_id from plasmids where id=?", empty)
        self.assertNotIn("data-scan-switch", self.get_ok(self.a, f"/plasmid/{number}"))
        # A found primer drawn on the map comes back with a save; it isn't kept, nor made a record.
        before = one("select count(*) from inventory_items")
        r = self.a.post(f"/plasmids/{self.rid}/sequence-save", json={"sequenceData": {
            "sequence": self.seq, "circular": True, "name": self.name,
            "primers": {f"found-primer-{self.ids['exact']}-0": {
                "id": f"found-primer-{self.ids['exact']}-0", "name": "exact-F", "start": 100, "end": 121,
                "forward": True}}}})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(r.get_json()["primers_added"], 0)
        stored = json.loads(one("select features_json from plasmids where id=?", self.rid) or "[]")
        self.assertFalse([a for a in stored if a.get("kind") == "primer"])
        self.assertEqual(one("select count(*) from inventory_items"), before)

    def test_a_records_read_only_map_has_the_switch_too(self):
        key = self.new_module(self.a, "glycerol_stocks")
        stock = self.make_item(self.a, key, uniq("stock"), attr_plasmid=str(self.number))
        html = self.get_ok(self.a, f"/inventory/{key}/items/{stock}/sequence")
        self.assertIn("data-scan-switch", html)
        # No Primers card there, so the plasmid's own primers are found too.
        self.assertIn(f'data-scan-url="/plasmids/{self.rid}/primer-sites?all=1"', html)
        j = self.a.get(f"/plasmids/{self.rid}/primer-sites?all=1").get_json()
        card = [h for h in j["hits"] if h["id"] == self.ids["card"]]
        self.assertEqual([(h["start"], h["linked"]) for h in card], [(1, True)])
        self.assertEqual(j["listed"], 0)

    def test_an_unknown_plasmid_is_404(self):
        r = self.a.get("/plasmids/987654321/primer-sites")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
