"""Citing a paper in a notebook page (#143): (Smith et al., 2024) from a
paper note's authors and year (app/citations.py short_cite, reference),
what the editor asks for (pages/titles, cite-search), and the paper note
listing the pages that cite it."""
from __future__ import annotations

import unittest

from tests.base import client_for, make_user, uniq
from tests.test_notebook import Notebook

from app import citations  # noqa: E402  (after tests.base sets up the test database)


class ShortForms(unittest.TestCase):

    def test_one_two_and_more_authors(self):
        self.assertEqual(citations.short_cite("Jane Smith", "2024"), "Smith, 2024")
        self.assertEqual(citations.short_cite("Jane Smith; Kim Lee", "2024"), "Smith & Lee, 2024")
        self.assertEqual(citations.short_cite("Jane Smith; Kim Lee; Ana Diaz", "2024"), "Smith et al., 2024")
        self.assertEqual(citations.short_cite("Smith, Jane; Lee, Kim", "2024"), "Smith & Lee, 2024")

    def test_names_in_chinese_are_written_whole_and_no_author_gives_the_title(self):
        self.assertEqual(citations.short_cite("张三; 李四; 王五", "2022"), "张三 等, 2022")
        self.assertEqual(citations.short_cite("小明 王; Hua Li", "2022"), "王小明 等, 2022")  # as Zotero gives it
        self.assertEqual(citations.short_cite("王, 小明", "2022"), "王小明, 2022")
        self.assertEqual(citations.short_cite("", "2020", "Short title"), "Short title, 2020")
        self.assertEqual(citations.short_cite("", "2020", "One two three four five"), "One two three four…, 2020")
        self.assertEqual(citations.short_cite("Jane Smith", ""), "Smith, n.d.")
        self.assertEqual(citations.short_cite("Jane Smith", "Published 2021-03"), "Smith, 2021")

    def test_authors_as_people_type_them(self):
        self.assertEqual(citations.split_authors("Wang X, Li Y and Chen Z"), ["Wang X", "Li Y", "Chen Z"])
        self.assertEqual(citations.split_authors("Wang, Xiaoming"), ["Wang, Xiaoming"])
        self.assertEqual(citations.split_authors("Smith & Lee"), ["Smith", "Lee"])
        self.assertEqual(citations.split_authors("Jane Smith; Kim Lee; …"), ["Jane Smith", "Kim Lee"])
        self.assertEqual(citations.short_cite("Wang X, Li Y", "2024"), "Wang & Li, 2024")
        self.assertEqual(citations.short_cite("Wang XM", "2024"), "Wang, 2024")
        self.assertEqual(citations.surname("Ludwig van Beethoven"), "van Beethoven")
        self.assertEqual(citations.surname("Martin Luther King Jr."), "King")
        self.assertEqual(citations.surname("King, Martin Luther, Jr."), "King")
        self.assertEqual(citations.surname("Smith J."), "Smith")

    def test_the_reference_line(self):
        self.assertEqual(
            citations.reference({"authors": "Jane Smith; Kim Lee", "year": "2024", "journal": "Nature", "doi": "10.1/x"},
                                "A study."),
            "Jane Smith, Kim Lee (2024). A study. Nature. https://doi.org/10.1/x")
        self.assertEqual(citations.reference({"url": "https://example.org/p"}, "Untitled"), "Untitled. https://example.org/p")


class Citing(Notebook):

    def paper(self, client, title, authors, year):
        return self.new_page(client, kind="reading", title=title, properties=[
            {"key": "authors", "value": authors}, {"key": "year", "value": year}, {"key": "journal", "value": "Cell"}])

    def test_the_editor_reads_how_a_paper_is_cited(self):
        title = uniq("Reward prediction ")
        paper = self.paper(self.m, title, "Wolfram Schultz; Peter Dayan; Read Montague", "1997")
        note = self.new_page(self.m, title=uniq("Note"))
        got = self.m.get(f"/notebook/api/pages/titles?ids={paper},{note}").get_json()["pages"]
        self.assertEqual(got[str(paper)]["cite"], "Schultz et al., 1997")
        self.assertTrue(got[str(paper)]["ref"].startswith("Wolfram Schultz, Peter Dayan, Read Montague (1997). "))
        self.assertNotIn("cite", got[str(note)])

    def test_cite_a_paper_finds_papers_by_author_year_or_title(self):
        title = uniq("Orientation maps ")
        paper = self.paper(self.m, title, "Ming Xu; Jordan Lee", "2019")
        for q in ("xu", "2019", title.split()[1].lower()):
            found = self.m.get(f"/notebook/api/cite-search?q={q}").get_json()["papers"]
            self.assertIn(paper, [p["id"] for p in found], q)
        found = next(p for p in self.m.get("/notebook/api/cite-search?q=xu").get_json()["papers"] if p["id"] == paper)
        self.assertEqual(found["cite"], "Xu & Lee, 2019")
        # Someone else's paper, not shared: not offered.
        theirs = self.paper(client_for(make_user()), uniq("Theirs "), "Ming Xu", "2019")
        self.assertNotIn(theirs, [p["id"] for p in self.m.get("/notebook/api/cite-search?q=xu").get_json()["papers"]])

    def test_a_paper_lists_the_pages_that_cite_it(self):
        paper = self.paper(self.m, uniq("Cited "), "Jane Smith", "2024")
        note = self.new_page(self.m, title=uniq("Citing note"))
        self.save(self.m, note, body=f"As shown [(Smith, 2024)](/notebook?page={paper}&cite=1).\n\n```references\n```\n")
        linked = self.m.get(f"/notebook/api/pages/{paper}/linked-from").get_json()["pages"]
        self.assertIn(note, [p["id"] for p in linked])
