"""app/cell_text.py reads a row's cells as data-table.js's _cellText does
(an export of a sheet the server pages: /inventory/<key>/rows?as=text)."""
from __future__ import annotations

import unittest

from app.cell_text import cell_text, row_texts
from tests.base import *  # noqa: F401,F403


class ReadsACellAsThePageDoes(unittest.TestCase):

    def test_a_cell_drawn_as_text(self):
        self.assertEqual(cell_text('<span class="cell-text" tabindex="0" data-sort-value="Tris &amp; HCl">Tris &amp; HCl </span>'),
                         "Tris & HCl")
        # A date: its value, not the browser's way of writing it.
        self.assertEqual(cell_text('<span class="cell-text is-date" data-sort-value="2026-03-04">03/04/2026</span>'), "2026-03-04")
        # An empty field's placeholder is not its value.
        self.assertEqual(cell_text('<span class="cell-text is-empty is-placeholder" data-sort-value="">–</span>'), "")
        # A span that is not a text cell is read as any other markup.
        self.assertEqual(cell_text('<span class="pill">  in   use </span>'), "in use")

    def test_a_field(self):
        self.assertEqual(cell_text('<input name="vendor" value=" Acme &quot;Bio&quot; " data-autosave="1">'), 'Acme "Bio"')
        self.assertEqual(cell_text('<input type="checkbox" value="7"><input type="hidden" name="x" value="h">'
                                   '<input name="lot" value="L1">'), "L1")
        self.assertEqual(cell_text('<textarea name="notes">two\nlines </textarea>'), "two\nlines")
        # A menu: its chosen option's text, else its first.
        self.assertEqual(cell_text('<select name="status"><option value="a">Available</option>'
                                   '<option value="u" selected>Used up</option></select>'), "Used up")
        self.assertEqual(cell_text('<select name="category"><option value="">–</option><option value="t">tissue</option></select>'), "–")
        # The first field in the cell decides (a source: its colony, then the ID).
        self.assertEqual(cell_text('<span class="source-cell"><select name="k"><option value="mouse" selected>Mouse colony</option>'
                                   '</select><input name="r" value="42"></span>'), "Mouse colony")

    def test_a_cell_with_no_field(self):
        markup = ('<details class="multi-cell"><summary>flammable, toxic</summary><div class="multi-cell-menu">'
                  '<input type="hidden" name="h" value="flammable"></div></details>')
        self.assertEqual(cell_text(markup), "flammable, toxic")
        self.assertEqual(cell_text('<a href="/x"><svg class="icon"><use href="#i"></use></svg> 12&nbsp;mL\n</a>'), "12 mL")

    def test_rows_by_id(self):
        rows = ('<tr data-id="5" class="x"><td><input type="checkbox" value="5"></td><td>12</td>'
                '<td><input name="name" value="a"></td></tr><tr data-detail-for="5"><td>more</td></tr>'
                '<tr data-id="9"><td></td><td>13</td><td><input name="name" value="b"></td></tr>')
        self.assertEqual(row_texts(rows), {5: ["", "12", "a"], 9: ["", "13", "b"]})


class TheRowsAsWords(AppTestCase):

    def test_an_export_asks_for_words_only(self):
        from tests.test_inventory import InventoryCase
        key = InventoryCase.new_module(self.a, "reagents")
        vendor = uniq("Vendor")
        item = self.make_item(self.a, key, name=uniq("w"), vendor=vendor, status="in stock", received_on="2026-03-04")
        answer = self.a.get(f"/inventory/{key}/rows?ids={item}&as=text").get_json()
        self.assertEqual(answer["ids"], [item])
        words = answer["texts"][str(item)]
        self.assertIn(vendor, words)
        self.assertIn("2026-03-04", words)
        self.assertNotIn("rows", answer)
        # The same words, whichever way the page draws its cells.
        chrome = {"User-Agent": "Mozilla/5.0 Chrome/141.0.0.0 Safari/537.36"}
        self.assertEqual(self.a.get(f"/inventory/{key}/rows?ids={item}&as=text", headers=chrome).get_json(), answer)


if __name__ == "__main__":
    unittest.main()
