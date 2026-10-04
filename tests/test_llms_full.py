"""llms-full.txt (scripts/llms-full.py): the whole English user guide as one
Markdown file for AI assistants, made when the website publishes."""
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

# tests.base first: it points the app at a throwaway database (and data folder).
from tests.base import AppTestCase  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("llms_full", ROOT / "scripts" / "llms-full.py")
llms_full = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(llms_full)


class LlmsFull(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = llms_full.build()

    def test_every_guide_page_is_in_it_in_order(self):
        positions = []
        for _group, pages in llms_full.guide_pages.PAGES:
            for slug, _en, _zh in pages:
                source = f"Source: {llms_full.guide_pages.url('en', slug)}\n"
                self.assertIn(source, self.text, slug)
                positions.append(self.text.index(source))
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Source: https://biomanager.org/deploy-with-ai.md", self.text)

    def test_no_html_is_left_in_the_guide(self):
        guide = self.text.split("Source: https://biomanager.org/deploy-with-ai.md")[0]
        self.assertIsNone(re.search(r"</?(p|div|span|b|i|a|li|td|th|kbd|code|small|figure|video|section)\b", guide))

    def test_links_are_absolute_and_tables_keep_their_cells(self):
        self.assertIn("[Lab setup](https://biomanager.org/guide/lab-setup)", self.text)
        self.assertNotIn("](guide/", self.text)
        self.assertNotIn(".html)", self.text)        # the sitemap's addresses, not the .html ones
        self.assertIn("| `Esc` | Close a menu or dialog |", self.text)

    def test_the_published_copy_is_up_to_date(self):
        published = (ROOT / "site" / "llms-full.txt").read_text(encoding="utf-8")
        self.assertEqual(published, self.text, "Run python scripts/llms-full.py after editing the guide")

    def test_a_note_s_title_is_set_off_from_its_text(self):
        self.assertIn("> **One litter date per cage** A cage holds one litter date", self.text)


if __name__ == "__main__":
    unittest.main()
