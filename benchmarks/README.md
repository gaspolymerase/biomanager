# Benchmarks

Measurements of BioManager that anyone can run again and get the same
numbers. Each runs on a throwaway database of its own (as the tests do), so
no lab's data is ever opened.

## Import from Excel: messy sheets

`import_sheets.py` asks how much of a real lab's spreadsheet comes through
**Import from Excel** intact when the person accepts every column match the
app suggests and clicks Import.

```bash
.venv/bin/python benchmarks/import_sheets.py
```

It takes about ten minutes. `--per-mess 30` makes more sheets of each kind,
`--seed 7` a different set, and `--out <folder>` keeps every sheet it made
beside a `truth.json` of what each row really holds, so the data set can be
published with the results.

**How.** Each sheet is made from mouse or plasmid records whose true values
are known, then spoilt in one way a lab's sheet is spoilt (a *mess*), or in
four at once (the *kitchen sink*), or not at all (the *control*). The
messes:

| Kind | Messes |
| --- | --- |
| Headers | other names (DOB, Cage #, Vector), typos, Chinese headers, another column order |
| Layout | title lines above the header, blank rows, a TOTAL row, extra columns the database lacks |
| Cages and boxes | a cage number merged down its mice; written once with blanks below (filled down by eye) |
| Dates | 3/14/2026, 14/03/2026, 14.03.26, 14-Mar-26, real Excel date cells, Excel date numbers, all mixed; a 2062 typo |
| Values | male/female, ♂/♀, Breeding/exp/culled, owners by full or first name or someone not in the lab, stray spaces and capitals, a mouse number used twice, numbers stored as 1201.0, 123,4 for 123.4, concentrations with units |
| Files | CSV, semicolon CSV, TSV, CSV in Windows' encoding, CSV with a byte-order mark |

Every sheet is uploaded through the app's own pages and imported with the
match page's suggestions. Then each value is compared with the truth:

- **correct**: stored as it should be;
- **kept**: not stored as it should be, but the sheet's text is in the
  record's notes, or the preview warned about that value: nothing is lost,
  and the person was told;
- **blank**: left empty without a word;
- **wrong**: a different value without a word (the worst).

A row is *imported*, *refused* (the preview says why), or *lost*.

Results are written to `results/import-<version>-<commit>-seed<seed>.md`
(tables) and `.json` (the counts behind them); a commit marked `-dirty` had
changes to the app not yet committed.

### Results so far

Seed 1, 740 sheets, the importer before and after the fixes this benchmark
led to (`results/import-1.5.5+dev-3eb67cf-seed1.md` and
`results/import-1.5.5+dev-5cf6a57-seed1.md`):

| | Rows imported | Values correct | Kept, and told | Blank, silently | Wrong, silently |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mice, before | 100.0% | 95.9% | 3.8% | 0.3% | 0.0% |
| Mice, after | 100.0% | 99.8% | 0.2% | 0.0% | 0.0% |
| Plasmids, before | 94.7% | 98.0% | 0.7% | 1.3% | 0.0% |
| Plasmids, after | 100.0% | 99.9% | 0.1% | 0.0% | 0.0% |

What changed: a cage, rack, box or tank written once with blank cells
below offers **Its blank cells take the value above them** (filled-down
cages went from 91.6% to 100% correct, boxes from 82.9%); Chinese headers
are matched (5.2% and 0% to 100%), and a CSV from a Chinese Windows (GBK)
is read; a concentration written with `ng/µl` is read (90.7% to 100%).
What remains *kept* is what can't be known from the sheet: a person not in
the lab, a mouse number used twice, a birth date in 2062.
