# Import from Excel: messy-sheet benchmark

BioManager 1.5.1+dev (a06da7c), seed 1, 12 sheets per mess and 40 kitchen-sink sheets (4 messes each) per database; 740 sheets, 533 s.

Imported with every column as the match page suggests. *Kept*: not stored as it should be, but the sheet's text is in the notes or the preview warned; *blank* and *wrong*: silently.

## Mice

| Mess | Sheets | Rows imported | Fields correct | Kept (told) | Blank (silent) | Wrong (silent) | Extra columns kept |
|---|---:|---:|---:|---:|---:|---:|---:|
| all | 436 | 100.0% | 95.9% | 3.8% | 0.3% | 0.0% | 100% |
| control | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| header-synonyms | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| header-typos | 12 | 100.0% | 99.4% | 0.6% | 0.0% | 0.0% | – |
| headers-chinese | 12 | 100.0% | 5.2% | 94.8% | 0.0% | 0.0% | – |
| column-order | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| title-rows | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| blank-rows | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| total-row | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| extra-columns | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | 100% |
| merged-cages | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| filled-down-cages | 12 | 100.0% | 91.6% | 0.0% | 8.4% | 0.0% | – |
| dates-us | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| dates-eu | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| dates-eu-dots | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| dates-named | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| dates-excel-cells | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| dates-excel-serial | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| dates-mixed | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| date-typo-future | 12 | 100.0% | 99.4% | 0.6% | 0.0% | 0.0% | – |
| sex-words | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| sex-symbols | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| status-words | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| owner-full-names | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| owner-first-names | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| owner-unknown | 12 | 100.0% | 97.5% | 2.5% | 0.0% | 0.0% | – |
| padding-and-case | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| duplicate-ids | 12 | 100.0% | 99.6% | 0.4% | 0.0% | 0.0% | – |
| numbers-as-floats | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| csv | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| csv-semicolon | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| tsv | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| csv-windows-encoding | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| csv-bom | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| kitchen-sink | 40 | 100.0% | 93.0% | 6.0% | 1.0% | 0.0% | 100% |

By column (all sheets):

| Column | Correct | Kept | Blank | Wrong |
|---|---:|---:|---:|---:|
| cage | 94.0% | 3.6% | 2.4% | 0.0% |
| dob | 95.9% | 4.1% | 0.0% | 0.0% |
| ear_tag | 95.0% | 5.0% | 0.0% | 0.0% |
| genotype | 96.4% | 3.6% | 0.0% | 0.0% |
| mouse_id | 95.1% | 4.9% | 0.0% | 0.0% |
| note | 100.0% | 0.0% | 0.0% | 0.0% |
| owner | 95.4% | 4.6% | 0.0% | 0.0% |
| sex | 96.4% | 3.6% | 0.0% | 0.0% |
| status | 96.4% | 3.6% | 0.0% | 0.0% |

## Plasmids

| Mess | Sheets | Rows imported | Fields correct | Kept (told) | Blank (silent) | Wrong (silent) | Extra columns kept |
|---|---:|---:|---:|---:|---:|---:|---:|
| all | 304 | 94.7% | 98.0% | 0.7% | 1.3% | 0.0% | 100% |
| control | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| header-synonyms | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| header-typos | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| headers-chinese | 12 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | – |
| column-order | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| title-rows | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| blank-rows | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| total-row | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| extra-columns | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | 100% |
| filled-down-boxes | 12 | 100.0% | 82.9% | 0.0% | 17.1% | 0.0% | – |
| positions-styles | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| concentration-comma-decimal | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| concentration-with-unit | 12 | 100.0% | 90.7% | 9.3% | 0.0% | 0.0% | – |
| owner-full-names | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| owner-first-names | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| owner-unknown | 12 | 100.0% | 98.2% | 1.8% | 0.0% | 0.0% | – |
| padding | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| csv | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| csv-semicolon | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| tsv | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| csv-windows-encoding | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| csv-bom | 12 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | – |
| kitchen-sink | 40 | 88.6% | 92.8% | 1.4% | 5.8% | 0.0% | 100% |

By column (all sheets):

| Column | Correct | Kept | Blank | Wrong |
|---|---:|---:|---:|---:|
| a260_280 | 100.0% | 0.0% | 0.0% | 0.0% |
| backbone | 100.0% | 0.0% | 0.0% | 0.0% |
| box | 93.2% | 0.0% | 6.8% | 0.0% |
| concentration | 93.9% | 6.1% | 0.0% | 0.0% |
| insert | 100.0% | 0.0% | 0.0% | 0.0% |
| location | 100.0% | 0.0% | 0.0% | 0.0% |
| name | 100.0% | 0.0% | 0.0% | 0.0% |
| note | 100.0% | 0.0% | 0.0% | 0.0% |
| owner | 98.6% | 1.4% | 0.0% | 0.0% |
| plasmid_id | 100.0% | 0.0% | 0.0% | 0.0% |
| position | 93.2% | 0.0% | 6.8% | 0.0% |
| resistance | 100.0% | 0.0% | 0.0% | 0.0% |

## Examples of what went wrong

- **mice: header-typos**
  - row 2 owner: sheet 'cdiaz' → stored 'bench' (kept)
  - row 3 owner: sheet 'cdiaz' → stored 'bench' (kept)
  - row 4 owner: sheet 'dpatel' → stored 'bench' (kept)
  - row 5 owner: sheet 'cdiaz' → stored 'bench' (kept)
- **mice: headers-chinese**
  - row 2 mouse_id: sheet '37001' → stored 36025 (kept)
  - row 2 ear_tag: sheet 'L1' → stored '' (kept)
  - row 2 sex: sheet 'M' → stored '' (kept)
  - row 2 genotype: sheet 'fl/fl' → stored '' (kept)
  - row 2 ear_tag: sheet 'R1L1' → stored '' (kept)
  - row 2 sex: sheet 'F' → stored '' (kept)
- **mice: filled-down-cages**
  - row 3 cage: sheet '' → stored '' (blank)
  - row 4 cage: sheet '' → stored '' (blank)
  - row 5 cage: sheet '' → stored '' (blank)
  - row 7 cage: sheet '' → stored '' (blank)
  - row 3 cage: sheet '' → stored '' (blank)
  - row 5 cage: sheet '' → stored '' (blank)
- **mice: date-typo-future**
  - row 9 dob: sheet '2061-11-09' → stored None (kept)
  - row 9 dob: sheet '2061-09-02' → stored None (kept)
  - row 3 dob: sheet '2061-12-28' → stored None (kept)
  - row 22 dob: sheet '2061-08-07' → stored None (kept)
  - row 9 dob: sheet '2060-11-03' → stored None (kept)
  - row 8 dob: sheet '2062-06-17' → stored None (kept)
- **mice: owner-unknown**
  - row 3 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 5 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 11 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 13 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 5 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 10 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
- **mice: duplicate-ids**
  - row 22 mouse_id: sheet '313020' → stored 312033 (kept)
  - row 6 mouse_id: sheet '315004' → stored 313052 (kept)
  - row 7 mouse_id: sheet '317005' → stored 315024 (kept)
  - row 3 mouse_id: sheet '318001' → stored 317020 (kept)
  - row 8 mouse_id: sheet '320006' → stored 318046 (kept)
  - row 16 mouse_id: sheet '321014' → stored 320024 (kept)
- **mice: kitchen-sink**
  - row 4 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 13 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 7 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 6 dob: sheet '2062-01-01' → stored None (kept)
  - row 4 mouse_id: sheet '404002' → stored 399079 (kept)
  - row 13 dob: sheet '2062-01-11' → stored None (kept)
- **plasmids: headers-chinese**
  - import refused: Match a column, or give a value for every row, for: Name. Import from Excel Upload Match columns Check and import Match 
  - import refused: Match a column, or give a value for every row, for: Name. Import from Excel Upload Match columns Check and import Match 
  - import refused: Match a column, or give a value for every row, for: Name. Import from Excel Upload Match columns Check and import Match 
  - import refused: Match a column, or give a value for every row, for: Name. Import from Excel Upload Match columns Check and import Match 
  - import refused: Match a column, or give a value for every row, for: Name. Import from Excel Upload Match columns Check and import Match 
  - import refused: Match a column, or give a value for every row, for: Name. Import from Excel Upload Match columns Check and import Match 
- **plasmids: filled-down-boxes**
  - row 3 box: sheet '' → stored '' (blank)
  - row 3 position: sheet 'A2' → stored None (blank)
  - row 4 box: sheet '' → stored '' (blank)
  - row 4 position: sheet 'A3' → stored None (blank)
  - row 3 box: sheet '' → stored '' (blank)
  - row 3 position: sheet 'A2' → stored None (blank)
- **plasmids: concentration-with-unit**
  - row 2 concentration: sheet '152.6 ng/µl' → stored '' (kept)
  - row 3 concentration: sheet '2237.8 ng/µl' → stored '' (kept)
  - row 4 concentration: sheet '1204.5 ng/µl' → stored '' (kept)
  - row 5 concentration: sheet '299.0 ng/µl' → stored '' (kept)
  - row 2 concentration: sheet '1678.5 ng/µl' → stored '' (kept)
  - row 3 concentration: sheet '1448.9 ng/µl' → stored '' (kept)
- **plasmids: owner-unknown**
  - row 14 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 17 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 20 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 28 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 5 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
  - row 9 owner: sheet 'Dr. Smith' → stored 'bench' (kept)
- **plasmids: kitchen-sink**
  - row 3 box: sheet '' → stored '' (blank)
  - row 3 position: sheet 'A2' → stored None (blank)
  - row 4 box: sheet '' → stored '' (blank)
  - row 4 position: sheet 'A3' → stored None (blank)
  - row 4 concentration: sheet '2134.8 ng/µl' → stored '' (kept)
  - row 5 concentration: sheet '513.6 ng/µl' → stored '' (kept)
