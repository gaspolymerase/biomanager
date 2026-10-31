# Plan: opening a big colony faster

Status: proposed, not started. Every number below was measured on the
current code (after the app.py round) with `scripts/load-test.py`'s lab
of 100,000 mice (5% alive, five years of history: 25,000 cages, 16,666
litters), on SQLite, signed in as the demo admin, in headless Chromium at
1440×900 on a 4-core machine. Times are medians of 3–5 runs. A lab of a
few thousand mice is several times faster; this is the stress case.

## What a person waits for today

| Step | Time | Where it goes |
| --- | --- | --- |
| Starting the app (desktop launch, server restart) | 2.1 s | 0.94 s of it is one start-up data migration |
| Home, after signing in | 1.6–1.8 s | 1.27 s is the server; the browser takes 0.35 s |
| The colony (Mice tab) | 1.7 s to usable, first paint at 0.65 s | the browser: 1.5 s of main thread |
| Cages tab | 2.9 s, first paint at 0.8 s | the browser: 2.5 s, of which style 0.9 s, layout 0.4 s |
| Breeders tab | 1.9 s | 274,000 DOM nodes, 5.3 MB of page |
| Litters tab | 1.2 s | |

The colony's sheets cost the server 0.2–0.4 s; that work was done in 2.0.x.
What is left is start-up, Home's server time, and the browser.

## Proposals, in the order to do them

### 1. Start-up: stop scanning every cage at each start

`services.migrate_cage_locations` (run by `init_database`) moves rack
positions once written as location text ("B-D7") into the rack fields.
It reads every cage not in a rack, at every start, to try its pattern on
the text. In this lab that is 25,000 cages, none of which has any location
text; it takes 0.94 s of the 2.1 s start.

- Ask SQL only for cages whose location text is not empty (a cage with no
  text can never match), so a lab that has migrated reads none.
- Measured: 0.94 s of start-up, every start. Effort: a line, plus a test
  that a cage with text still moves. Risk: none to what moves.

### 2. Home: read the agenda's mice as plain rows, and only what is shown

Home's server time is 1.27 s; `home_layouts._colony_items` is half of it.
It loads every living mouse past or near 30 weeks as an ORM object with its
litter, cage and rack (4,334 mice: 331 ms), and builds 5,715 agenda items,
3,960 of them "Past 30 w" for cages long past due, of which the page shows
none. The genotyping list (1,682 mice) is read the same way.

- Read those mice as plain rows with only the columns used (the sheets do
  this already, `app/plain_records.py`): the same 4,334 mice in 29 ms.
- Build an item only for what Home draws; count the rest in SQL.
- Then look at `welfare.home_view` (0.39 s: the experiment checks and
  `_mouse_path` 1,572 times).
- Expected: Home's server time from 1.27 s to about 0.4–0.5 s.

A finding that is not about speed: Home's **Due this week** counts every
colony item due by the week's end, the 3,960 cages long past 30 weeks
included, so in a lab this size it says about 5,700. It probably should
count what falls due this week and show what is overdue on its own.

### 3. The colony's sheets: draw the rows that are seen, then the rest

The Mice and Cages sheets draw a first page of 100 rows (`FIRST_PAGE`);
a 900-pixel window shows about 30. The rest are stubs, fetched when shown.

- Draw the first 40 and fetch the rest of the first page straight after,
  with the stubs-and-rows machinery the later pages already use.
- Measured (40 instead of 100): Mice ready in 1.23 s instead of 1.72
  (−29%), Cages in 1.74 s instead of 2.86 (−39%). Part of the work moves
  after the page is ready rather than going away, which is the point: the
  rows on screen are usable sooner.

### 4. The sheet's first frame: lay the table out once, not three times

`data-table.js` `_wireStickyParts` `measure` runs at the first frame. It
reads the toolbar's height (laying out the whole page), then removes
`is-fit` from the scroller and reads the table's width (laying the table
out again), then sets `is-fit` back (a third time before the first paint),
to decide how the header row sticks. `measure` alone is 288 ms of script on
Mice and 545 ms on Cages.

- Decide `is-fit` without taking the class off the live table (from the
  header row's width, or the scroller's `scrollWidth` while it scrolls),
  or once after the first paint.
- Measured (the class change left out, as an upper bound): Mice −0.21 s
  (−12%), Cages −0.37 s (−13%). With 3, Cages would be about 1.4 s.

### 5. Breeders: page its cards

The Breeders tab draws every Breeder cage's card at once: 1,855 cards,
each a table with its own header row (20,417 `<th>`), 274,000 nodes, 5.3 MB.

- Draw the first 100 cards, and the rest as the person scrolls or turns
  a page; search and the chips keep working over all of them (stubs, as
  the sheets do, or the server's search).
- Measured (the first 100 only): ready in 0.66 s instead of 1.92 (−66%),
  main thread 0.39 s instead of 1.46, first paint 0.55 s instead of 0.82.
- The most work of the five, and the biggest single page.

### Later, once the above is in

- The segmented controls' sliding marker (`shell.js` `boxOf`, 63 ms on
  Mice, 276 ms on Cages) and the phone bar (`initPhone`, 66 ms) read the
  layout during load and so pay for laying out the whole sheet first.
  Place the marker after the first paint.
- Style recalculation is 0.9 s on Cages. `tailwind.css` is 495 KB (about
  3,800 rule blocks); Chromium's selector statistics would show which
  selectors the sheet's cells pay for.

## How to check each

`scripts/load-test.py <folder> --reuse --browser` before and after (with
`CHROMIUM=` set to the browser's path), and the colony's existing tests.
A sheet's stubs are held to the drawn rows by `tests/test_speed.py`.
