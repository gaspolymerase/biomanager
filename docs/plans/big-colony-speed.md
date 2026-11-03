# Plan: opening a big colony faster

Status: done. Items 1, 2 and 5 are in; 3 and 4 were tried and left out,
as measured below. Every number was measured on the code after the app.py
round with `scripts/load-test.py`'s lab
of 100,000 mice (5% alive, five years of history: 25,000 cages, 16,666
litters), on SQLite, signed in as the demo admin, in headless Chromium at
1440×900 on a 4-core machine. Times are medians of 3–5 runs. A lab of a
few thousand mice is several times faster; this is the stress case.

## What a person waited for before

| Step | Time | Where it goes |
| --- | --- | --- |
| Starting the app (desktop launch, server restart) | 2.1 s | 0.28 s of it is one start-up data migration |
| Home, after signing in | 1.6–1.8 s | 1.27 s is the server; the browser takes 0.35 s |
| The colony (Mice tab) | 1.7 s to usable, first paint at 0.65 s | the browser: 1.5 s of main thread |
| Cages tab | 2.9 s, first paint at 0.8 s | the browser: 2.5 s, of which style 0.9 s, layout 0.4 s |
| Breeders tab | 1.9 s | 274,000 DOM nodes, 5.3 MB of page |
| Litters tab | 1.2 s | |

The colony's sheets cost the server 0.2–0.4 s; that work was done in 2.0.x.
What is left is start-up, Home's server time, and the browser.

## What was done, in order

### 1. Start-up: stop scanning every cage at each start — done

`services.migrate_cage_locations` (run by `init_database`) moves rack
positions once written as location text ("B-D7") into the rack fields.
It read every cage not in a rack, at every start, to try its pattern on
the text: 25,000 cages here, none with any location text.

- Now it asks SQL only for cages with no rack and some location text (a
  cage with no text can never match), so a lab that has migrated reads
  none. `tests/test_rack_layout.py` (`PlacesWrittenAsText`) holds that a
  place still moves, a note stays, and the query names the location.
- Measured: the migration 285 ms → 5 ms, `init_database` 523 → 219 ms,
  every start. (The plan said 0.94 s: that was cProfile's figure, which
  inflates ORM-heavy code; timed plainly it was 0.28 s.)

### 2. Home: read the agenda's mice as plain rows — done in part

`home_layouts._colony_items` loaded every living mouse past or near
30 weeks as an ORM object with its litter, cage and rack (4,334 mice),
and the genotyping list (1,682 mice) the same way.

- Both now come from `plain_records.mice`, with only the columns used.
  The agenda is the same, item for item (compared as JSON before and
  after). Measured: `_colony_items` 611 → 287 ms; Home's server time
  1.19 s → 0.76 s.
- Not done: building only what Home draws. `build_agenda` also feeds the
  calendar (twice), the `/api/v1/due` lookup, the sidebar's peek and the
  task board, which read the whole agenda; cutting it for Home alone
  would need a second agenda.

Still open, not about speed: Home's **Due this week** counts every colony
item due by the week's end, the 3,960 cages long past 30 weeks included,
so in a lab this size it says about 5,700. It probably should count what
falls due this week and show what is overdue on its own.

### 3. The colony's sheets: draw 40 rows first, not 100 — left out

The idea: draw the first 40 rows and fetch the rest of the first page
straight after. The −29% / −39% first measured were wrong: the check took
the page as ready while 60 of its first 100 rows were still stubs. Timed
again until the first page was whole (medians of 5, usable / whole page):

| Sheet | 100 drawn | 40 drawn, then fetched |
| --- | --- | --- |
| Mice | 1.79 / 1.84 s | 1.04 / 1.51 s |
| Cages | 2.68 / 2.71 s | 1.97 / 2.80 s |
| Litters | 1.21 / 1.35 s | 1.09 / 1.38 s |

The rows on screen came sooner, but the whole first page no sooner (Cages
and Litters later), and the sheet sat dimmed while it filled. `FIRST_PAGE`
stays 100.

### 4. The sheet's first frame: `is-fit` — left out

`data-table.js` `measure` looked like 288 ms (Mice) and 545 ms (Cages) of
script. Most of that is the page's first layout, which has to happen
anyway and is charged to whichever script first reads the layout; leaving
the class change out (the upper bound) saved 0–41 ms (taking off a class
that isn't there costs nothing; the add and remove 7–8 ms on Mice, 35–41
ms on Cages). Not worth changing. The same goes for the "later" item on
`shell.js` `boxOf` and `initPhone`: they pay for the first layout too.

### 5. Breeders: page its cards — done

The Breeders tab drew every Breeder cage's card at once: 1,855 cards,
each a table with its own header row, 274,000 nodes.

- Past 200 cards (`colony_routes.BREEDERS_DRAWN_OVER`), the first 100 are
  drawn and the rest empty, each with what the search, **Alive only** and
  the count read; **Show 100 more** shows the next, and a card that comes
  into view (by search or Show more) is fetched from
  `/colony/breeders/cards`, drawn by the same template
  (`_breeder_cards.html`). The search still looks through every cage.
- Measured (medians of 5): load 1.60 s → 0.67 s, Chromium busy 1.19 s →
  0.44 s, first paint 0.73 s → 0.53 s, 274,000 → 23,000 nodes, 4.5 MB →
  0.7 MB of page.

### Afterwards: the Cages tab's style recalculation — done

Style recalculation was about 1 s of the Cages tab's 3 s in the browser.
Chromium's selector statistics showed the selectors are not the cost
(about 8% of it); the number of elements is. More than half the page
(8,400 of 14,700 elements) is the open cages' mice, of which a window
shows two or three. The cards in the table are now `content-visibility:
auto`, with a height worked out from their mice until first seen
(`docs/DEVELOPMENT.md`). Interleaved medians of 9: your colony's cages
load in 2.4 s instead of 3.3, the browser busy 2.0 s instead of 2.9; the
whole lab's cages, busy 2.2 s instead of 2.9, load unchanged (that page's
time is its download).

Two things found on the way: the load test's numbers included the What's
new dialog, which opens once after each update and restyles the whole
page (its admin had never pressed Got it); and Home's `status_experiment`
read its 1,532 mice as ORM objects: as plain rows, 110 ms to 50 ms.

### Afterwards: the other databases at scale

The load test filled only the colony, fish, fly vials, two inventories
and the notebook. It now also fills, in proportion to 100,000 mice,
5,000 worm strains, 10,000 plants, 5,000 plasmids (a fifth with a
sequence), 4,000 orders, 5,000 calendar events and 2,000 to-dos
(`fill_more`). Two pages had never been paged:

| Page | Before | After |
| --- | --- | --- |
| Plasmids (5,000) | 10.6 s to load, 2.7 s on the server, 36 MB | 1.4 s, 0.3 s, 3 MB |
| Plants (10,000; 3,328 listed) | 33 s, 4.3 s, 54 MB | 4.9 s, 3.2 s, 5.5 MB |

The plasmids' sheet draws its first page and makes the rest's stubs from
data; its Boxes grid and List come when first shown. The plants' sheet
lists the living and recently ended (the rest behind "Show them") and is
paged by drawing every row and reading the rest back, which is half its
server time; stubs made from data are the next step there. Worm strains
(0.9 s), orders (1.4 s) and the calendar's Board (0.4 s) were already
fine. On the fish, tanks, reagents and vials (1.4–1.8 s), the tank menus
of the fish dialog and the batch bar (3,338 tanks each) were tried and
cost nothing measurable: options in a closed menu are not laid out.

### Afterwards: a load test with a colony's ages

The load test gave its living mice litters born over five years, so 88%
of them were past 30 weeks and Home's agenda listed about 5,700 colony
items, 3,960 of them cages long past 30 weeks: no working colony keeps
its mice like that, and tuning Home for it would have tuned the test
data. The living mice now come from litters born in the last ten months,
most of them young: 519 of 4,921 (10.5%) are past 30 weeks, and the
agenda has 2,423 colony items (1,688 of them genotyping, as a third of
the living mice are labelled geno). Measured again on that lab:

| Page | Server | In Chromium |
| --- | --- | --- |
| Home | 0.45 s | 0.74 s to load |
| Mice / Cages / Litters | 0.13 / 0.22 / 0.30 s | 1.2 / 1.5 / 0.8 s |
| Fish / Tanks | 0.65 / 0.39 s | 1.8 / 1.4 s |
| Reagents / Samples / Orders | 0.84 / 0.38 / 0.30 s | 1.9 / 1.6 / 1.5 s |
| Fly vials / Worm strains | 0.30 / 0.23 s | 1.3 / 1.1 s |
| Plasmids | 0.27 s | 1.3 s |
| Plants (10,000) | 2.7 s | 4.8 s |

Home's server time was 0.45 s, of which the fly and worm schedules were
0.11 s, and the plants' sheet the slowest page left. Then:

| Page | Server before | After |
| --- | --- | --- |
| Plants (10,000; 3,323 listed) | 2.7 s (4.8 s to load) | 0.39 s (1.3 s); "Show them" 8.7 s to 0.89 s |
| Home | 0.45 s | 0.34 s |
| Reagents (with 4,000 orders) | 0.93 s | 0.37 s |

The plants' stubs are made from data; the fly and worm schedules read
only the vials that can fall due (the four of them on Home: 0.11 s to
8 ms); "Already on order" indexes the open orders instead of trying each
against every reagent. Left as they are: the fish (0.31 s) and tanks
(0.24 s), and Home's weaning list (0.04 s), shared with the calendar and
the cage sheet.

## How to check each

`scripts/load-test.py <folder> --reuse --browser` before and after (with
`CHROMIUM=` set to the browser's path), and the colony's existing tests.
A sheet's stubs are held to the drawn rows by `tests/test_speed.py`.
