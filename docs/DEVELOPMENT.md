# BioManager — developer and operator notes

How BioManager is built and how its internals behave. For what the app
does and how to use it, see the [README](../README.md). For running it on a
lab server, see [`deploy/README.md`](../deploy/README.md) and the
[runbook](../deploy/RUNBOOK.md).

## Stack

- Python
- Flask
- SQLAlchemy
- SQLite for local development
- PostgreSQL for a shared lab server (the test suite runs on both in CI)
- Tailwind CSS v4 for the interface (compiled, no CDN at runtime)

## Interface design

The interface follows Apple's macOS conventions: a translucent vibrant
sidebar, a Finder-style tab strip, a unified toolbar whose separator only
appears once content scrolls under it, 13px system type, AppKit control
metrics (28px buttons, 6px radii) and Apple's system colour palette — with
full dark mode.

Two Apple things are deliberately **not** used, because their licences do
not permit it outside Apple platforms:

- **SF Symbols** — licensed for Apple-platform apps only, not web.
- **Shipping SF Pro** — but the system font stack (`-apple-system`)
  resolves to SF on Apple devices, which is both correct and allowed.

### Icons

One sprite, `app/static/icons.svg`, built by `scripts/build-icons.py`:

```bash
python scripts/build-icons.py path/to/fontawesome-free-6.7.2
```

Referenced as `{{ icon('mouse') }}` in templates, which emits
`<svg class="icon"><use href="/static/icons.svg#mouse"></svg>` — one cached
request, inherits `currentColor`, no JavaScript, works offline in the
packaged app.

Three sources, all permissively licensed:

| Source | Licence | Covers |
| --- | --- | --- |
| [Font Awesome Free 6](https://fontawesome.com) | Icons CC BY 4.0 | the UI, plus `worm`, `mosquito`, `fish`, `frog`, `dna`, `vial`, `microscope`, `bacterium`, `virus`, `syringe` |
| [game-icons.net](https://game-icons.net) by Delapouite | CC BY 3.0 | `mouse` (their *rat*) and `fly`, vendored in `scripts/icon-sources/` |
| This project | — | `plasmid`, `petri`, `cage`, `tank`, `culture-vial` |

Font Awesome has no laboratory mouse and no plasmid. The plasmid and the
labware are drawn here; the mouse and the housefly are not, because both
collapse into a blob below about 24px unless ears, snout, tail — or
compound eyes and wings — are all resolved, which is more drawing than a
16px mark can carry. Hand-drawn versions were tried three times and thrown
away.

Add an icon by adding a name to `FROM_FONTAWESOME`, `FROM_SOURCES` or
`CUSTOM` in the build script and rebuilding. **Check any new icon at 15px**,
not just large — that is where they fail.

### App icon

`app/static/icon.svg` is laid out on Apple's macOS icon grid — an 824×824
rounded square inset in a 1024 canvas with a 185.4 corner radius — so it
sits correctly beside native apps in the Dock. The mark is a double helix,
the one symbol every organism in the app shares.

One drawing in `app/appearance.py` serves every size, from 28px beside a
heading to 512px on a desktop, so it is plain vector shapes: no SVG filters
(a blur or drop shadow is drawn at screen resolution by some browsers, Safari
among them, and turns the white edges to haze) and curves as Béziers, not
runs of short lines. `scripts/build-app-icon.py` re-renders the SVG and every
PNG, `.icns`, `.ico` and Android layer after a change, and the website's
copies (`site/assets/icon.svg`, `icon-192.png`, `apple-touch-icon.png`); it
renders with Quick Look on a Mac and with Chromium (Playwright) elsewhere.

## Interface

The UI is a collapsible icon rail plus a persistent **workspace tab strip**:
every page you open becomes a tab, **+** opens a new one on the person's
start page (Settings; Home if none), tabs survive navigation (they live in
`localStorage`), and they can be reordered by dragging, closed with middle
click, and switched with `Alt+1…9` / `Alt+←` / `Alt+→` (`Alt+W` closes,
`Cmd/Ctrl+B` collapses the rail, `Cmd/Ctrl+K` opens search).

The rail's lists are in `app/app.py`: `NAV_SECTIONS` (Workspace, with
Utilities, and the databases), `NAV_FOOTER` (Settings) and `NAV_MORE`, the
**More** menu at the rail's foot (Batch history, then the admin pages:
Lab setup, Colony overview, Audit log, Manage users, Guests, Racks &
boxes); **Help** holds the guide and Send feedback. `static/shell.js`
(`setupRailMenus`) moves each `.rail-pop` menu to `<body>` when it opens:
the rail's `backdrop-filter` makes it the containing block of anything
fixed inside it. Toasts (`BiomanagerShell.toast`) are a manual popover,
in the top layer, so they show in front of an open `<dialog>`; a refused
form's message is copied into the dialog `static/form-memory.js` reopens.

### Home

Classic's cards are `home_layouts.CARDS` (key, label, icon, wide at first,
the feature or flag it needs). A person's choice is the `home_cards:<user>`
setting — `order`, `hidden`, `wide`, and `seen` (the keys that existed when
they saved, so a card added in a later release joins at its place, shown
unless it is one of `OFF_AT_FIRST`). `get_cards`, `set_cards` and
`reset_cards` read and write it; `offered_cards` is what this lab can
show; `POST /home/cards` saves the Customize dialog
(`templates/home/_customize.html`). `home.html` captures each card with
`{% set %}` into a dict and draws them in the person's order;
`_home_extra_cards` loads the four off at first (to-dos, bookings, recent
pages, calculators). The calculators card puts the ones Utilities opened
last first (`biomanager:util:recent` in `localStorage`, up to eight).

### Utilities

`static/bench-calcs.js` is the arithmetic and its data (molecular weights,
buffer pKa, vessels, antibiotics, isotopes): each calculator in `CALCS` is
a description (`id`, `group`, `title`, `inputs` with units) and a
`compute(v)` that returns `{lines, table, warnings, notes, solved}`;
`run(id, raw)` reads the typed numbers (decimal commas too) and converts
each to its base unit first. It loads in Node too, and
`tests/js/bench-calcs.check.mjs` checks known answers.
`static/utilities-page.js` draws the list, the open calculator (from the
address hash) and the reference tables; the lab's chemicals come from the
page (`/utilities` passes them) ahead of the built-in list.

### Database addresses

A database's `key` is its address (`/inventory/<key>`, `/stocks/<key>`,
`/organisms/<key>`) and follows its name: the Configure routes and the setup
survey call `database_keys.rekey()` after a rename, which gives it the new
name's slug and keeps the old key in `database_aliases` (revision 0011).
Each service's `get_module()` falls back to those, so everything that finds
a database by key still finds it: routes (whose `_module_or_404` sends a
GET on to the current address; a POST from an older page just saves),
the API, `Experiment.db` places, import targets, labels. `rekey` rewrites
the stored exact matches (`Experiment.db`, an order's `stocked_as`, a
sample's `organism:<key>` source). Notebook `@<key> n` text is never
rewritten (signed pages can't change): `_mention_modules(with_old=True)`
knows the old keys, the page's `#nb-mention-types` lists them (`old`), and
backlinks search every key a database has had. New keys (`free_key`) avoid
reserved words, the @-words `mouse`, `plasmid` and `order`, and live and old
keys of every kind (inventories, stocks and organisms), so `@<key> n` means
one database. Names are unique the same way: `database_keys.name_clash`
(any case, built-in names included) is checked by each create and rename
route and by Lab setup. Organism codes keep the first key's stem
(`first_key`). Plasmids: the page is `/plasmid/<number>`; `/plasmids/<row
id>` redirects there, and the writes stay under `/plasmids/<row id>/…`.

### Small shared rules

- Box, rack and freezer lists sort with `positions.place_order()`: numbers
  as numbers and a leading minus (any dash) as a sign, so −80, −20, 4 °C.
- `positions.parse()` reads a plain number on a lettered box as its nth
  place along the rows (imports that mix "A1" and "3").
- Global search (`global_search`) orders plasmids, inventory records and
  pages by the name: equal, then starting with the words, then containing
  them, then the rest.
- A sheet cell takes a pasted block (`static/sheet.js`, `pasteBlock`): rows
  and columns as shown, each cell put in and saved as if typed. A value
  that isn't one of a `<select>`'s options moves on to the next cell (at
  most two), and a toast names the first and last rows filled.
- `static/data-table.js` keeps a sheet's sort in `localStorage`
  (`dt:<id>:sort`, restored before the first render) and its chip in the
  address (`?chip=<spec>`; `?scope=mine` picks the chip reading *Mine*).
- The page scrolls, not the sheet. `.data-table-card` is `overflow: clip`
  (not a scroller), so its `.dt-toolbar` sticks to the top of
  `.shell-scroll` and its `.dt-bottom-bar` to the bottom;
  `_wireStickyParts` puts their heights on the card as `--dt-toolbar-h`
  and `--dt-bottom-h` (the selection bar sits above the bottom bar). A
  table no wider than its card gets `.dt-scroll.is-fit` (`overflow:
  visible`), and its header row is CSS-sticky under the toolbar; a wider
  one scrolls sideways in `.dt-scroll`, and its header cells are moved
  down with `translateY` as the page scrolls.
- The bottom bar's **New** (`_sheet.html` `quick_add`) is a plain form
  POST to the database's create route with defaults; every one redirects
  back to the sheet. On submit `data-table.js` notes the rows' ids in
  `sessionStorage` (`dt:quick-add`); on the next load the row that wasn't
  there is put last, its page shown and its first editable cell
  focused. Records that need a name or a tank first (strains, fish, fish
  lines, plasmids, inventories with required columns) open their dialog
  instead.
- The dot before an ID (`id_cell(..., stage=)`, `.life-dot[data-stage]`)
  is blue/green/red for a living animal's age: `app/life_stage.py` has
  the bands (mouse, zebrafish; organism databases from those presets go by
  `preset_key`), `stock_service.unit_stage` the vial/plate rule (young
  before `ready_on`, old past the rack's flip interval since set-up or the
  rack's last flip). `stage="expired"` makes an expired inventory item's
  dot red. `sheet.js` only toggles `is-alive`, so the stage stays put and
  a dead animal's dot goes grey.
- A cell with `data-autosave-on="change"` saves when left, not while
  typed (the cage number); a refused value goes back to its `_was` copy.
- Ctrl+K and the unified `@` search (`/notebook/search/all`) rank a query
  that is a record number (`_record_number`: digits, no leading zero) by
  number; anything else by a code equal to it (lot, catalogue number),
  then names. The unified menu fills from each inventory in turn up to the
  limit, and offers a database whose key or label word starts with the
  query (`type: "database"`; picking it inserts `@<key> ` and keeps the
  menu open for its records).

### Styling

`frontend/src/tailwind.css` is the single source of truth: design tokens in
`@theme`, composable primitives as `@utility` (`btn`, `field`, `card`,
`badge`, …), and component classes for anything repeated or referenced by
JavaScript (`dt-*` for data tables, `cmdk-*` for the command palette,
`wtab*` for the tab strip, the `workbench` vocabulary for the dense module
pages). It compiles to `app/static/tailwind.css`:

```bash
cd frontend
npm install        # first time only
npm run build:css  # one-off build
npm run watch:css  # rebuild while editing templates
```

Rebuild after editing templates — Tailwind only emits the classes it finds in
`app/templates/**/*.html` and `app/static/*.js`.

Every page is on Tailwind; the old `styles.css` / `legacy.css` bridge has
been removed. Three pages embed third-party widgets that bring their own
stylesheet (Open Vector Editor on plasmid detail, TOAST UI on the calendar)
and the notebook editor's own CSS lives in `frontend/src/styles.css` and
`frontend/src/blocks.css`, built with `npm run build:notebook`. All three read the theme's colour tokens, so
they follow the app's palette and dark mode.

## Organism modules (configurable species databases)

Mouse colony and zebrafish are hand-written modules with their own tables.
Everything else is configurable: an **organism module** is a row in
`organism_modules` that describes a species, and one generic engine serves it.

A module declares:

- **Vocabulary** — cage/tank/vial/plate, strain/line/stock, litter/clutch/progeny.
  These are what the UI calls things, so a fly database says "vial" and "stock".
- **Identity mode** — individuals (mice), groups with a headcount (flies, worms),
  or hybrid (fish: groups that can resolve into named individuals).
- **Capabilities** — 19 switches covering crosses, cohorts, a nursery stage,
  genotyping, environment logs, cryo inventory, protocol/census, billing and more.
  See `app/organisms.py`.
- **Schedule rules** — `anchor date + offset`, optionally varying by rearing
  temperature. One rule covers "flip flies every 14 days at 25 °C, 28 at 18 °C".
  `complete_due()` takes the day it was done (never later than today) and
  moves a service anchor (`SERVICE_ANCHORS`, e.g. `last_serviced_on`) to it,
  unless two recurring rules on that kind of subject count from the same
  anchor: then the anchor stays and each counts from its own last
  completion in `recompute_due()`.
- **Custom fields** — typed per-module columns stored in each row's `attrs`
  JSON, which generate their own form inputs, table columns and validation.

Drosophila and C. elegans are seeded automatically on first run
(`organisms.AUTO_SEED_PRESETS`). Zebrafish and mouse exist as presets so the
engine can be checked against the hand-written modules.

Build a new one at **Add database** in the sidebar (`/organisms/new`): pick a
preset or a blank sheet, name the nouns, choose the capabilities, done — no
migration.

### Shape of the schema

| Table | Holds |
| --- | --- |
| `organism_modules` | one species database + its configuration |
| `organism_module_fields` | user-defined fields per module and entity |
| `organism_locations` | location tree: facility / room / system / rack / incubator |
| `organism_lines` | strains, lines, stocks |
| `organism_housing` | cages, tanks, vials, plates |
| `organisms` | the tracked unit — one animal, or a group with `count` |
| `organism_crosses` | matings and crosses |
| `organism_cohorts` | litters, clutches, progeny batches |
| `organism_events` | append-only lifecycle log |
| `organism_due` | materialised schedule items |
| `organism_measurements` | weights, water chemistry, temperatures |
| `organism_genotypes` | genotyping calls |
| `organism_preservation` | frozen lots, vials remaining, recovery tests |

Every row carries `module_id_fk`, and every relation is resolved through
`_ref()` in `app/organism_routes.py` so a reference can never cross modules.

## Access control

Who may change what lives in one place, `app/access.py`:

- **You manage your own colony.** A record whose `owner` is you is yours to
  edit or delete.
- **Shared cages are everyone's.** Any cage can be shared
  (`mouse_cages.is_shared`, `access.is_shared_cage()`). A cage that becomes
  a breeder cage (`access.STARTS_SHARED_PURPOSES`) starts shared and one
  that stops being one starts personal (a `set` event on
  `CageRecord.purpose` in `models.py`; revision 0013 set the flag on the
  breeder cages a lab already had, and 0015 cleared it on the other cages,
  where it meant nothing before). Turning it off or on, or giving the cage
  to someone else, is `access.can_set_sharing()` / `can_manage()` (owner or
  admin; animal care may also reassign), not everyone a shared cage lets
  edit. The whole lab can edit a shared cage and pick mice out of it. The
  cage sheet has a chip for each purpose its cages have
  (`cage_purpose_chips()`).
- **Unowned records stay open**, so records predating ownership don't lock
  anyone out.
- **Lab common** (`is_shared`) on inventory items and plasmids
  (`plasmids.is_shared`, revision 0010; `access.LAB_COMMON_RECORDS`) lets
  anyone edit the record; deleting it,
  changing its owner or making it personal again is `access.can_manage()`:
  its owner, an admin, or anyone while it is unowned.
- **Admins can do anything.**

- **Project groups** narrow any of these sharings to a group's members
  (below): `access.cage_shared_with()` is a shared cage this person may
  work in, and lab common checks `groups.record_shared_with()`.

Visibility is deliberately *not* restricted — a census with holes is not a
census. The **My colony / My groups / Shared / Everyone** switch on the
colony page is a view filter (`access.scopes_for()` offers My groups only to
someone in a group); edit rights are per record and don't change with it.

## Project groups

`app/groups.py`, the **Project groups** page (`/groups`, under More). Tables
`lab_groups` and `lab_group_members` (`lead`: may add and remove members;
making groups, renaming, deleting and naming leads is for admins), revision
0015. A record shared with a group keeps `is_shared` and names the group in
`share_group_id` (no foreign key; `groups.release()` clears it, and the
record's `is_shared`, when a group is deleted): `mouse_cages`, `plasmids`,
`inventory_items` and `tasks`. Breeding tanks and lab stock vials are shared
by their purpose, so `tanks.share_group_id` and `stock_units.share_group_id`
only narrow it (`share_group` in their dialogs; `"1"` is the lab). A
database for a group has an empty `private_to` and a `share_group_id`
(`lab.group_of()`): `lab.can_see()` and `in_sidebar()` take in its members,
`first_of_kind()` and the survey's lookups skip it, and
`lab.set_audience_for_new()` handles `audience=group:<id>` on the three New
database forms (and `lab_routes.audience` `to=group:<id>`). A notebook page
is shared with a group by a `notebook_shares` row whose username is
`group:<id>` (`groups.page_share_names()` in `role_for` and
`shared_page_ids`); a notebook template with `lab` on and a group is that
group's.

Forms send sharing as one value, `"0"` personal, `"1"` the lab, `"g<id>"` a
group: `groups.parse()`, `groups.differs()` and `groups.apply()` (which
refuses a group the person isn't in; admins: any), and the
`_sharing.html` macro draws the options (`groups_api` in every template).
Memberships are read once per request (`groups._cache`, `forget()` after a
change).

To-dos and events: `task_visible_clause()` (app.py) and
`lab_calendar.event_visible_clause()` are the person's own, the lab's
(`is_shared`, no group) and their groups'; that is what the calendar, Home
and the phone feed show. A personal one is its owner's alone, admins
included. `task_can_edit()` lets the lab or the group tick off and change a
shared to-do; deleting it, or changing whose it is, is its owner's or an
admin's (`task_can_manage()`). A shared event is changed by its owner or an
admin (`lab_calendar.event_can_edit()`). Events default to the lab's
(`calendar_events.is_shared` defaults true, and revision 0015 left every
existing event shared); to-dos to their owner's. Away soon lists someone's
shared to-dos, not their personal ones. A member's copy
of the lab (`lab_copy.member_view`) leaves out the databases of groups they
are not in, and keeps pages shared with their groups.

Admins get **Colony overview** (`/admin/colony`): every cage in the facility
grouped by who manages it, with occupancy, shared-cage pooling, idle-time
flags and a warning for living mice with no cage. That's the page for
reassigning animals when someone leaves.

Cage ownership is backfilled on first run from the mice each cage holds; a
cage whose mice disagree is left unowned rather than guessed at.

## Lab setup and personal databases

`app/lab.py` decides what the lab uses and who sees which database; the
pages are in `app/lab_routes.py`.

- **Switchable functions.** The hand-written databases (mouse colony,
  zebrafish, plasmids) and the calendar and notebook are on unless
  `feature:<key>` in `app_settings` is `off`. A switched-off function leaves
  the sidebar, home and search, and its URL prefixes are refused by a
  before-request hook (a flash and home for a page, 403 for a write); its
  data is untouched. Configurable databases use their own `enabled` flag.
- **The survey** (`/setup`, admins) sets those flags, enables, disables or
  creates the fly/worm and inventory databases by kind, and the member
  permissions, then stamps `lab_setup_done`. `landing_url()` sends an admin
  there until it is done, and anyone with no `users.welcomed_at` to the
  welcome tour (`/welcome`) once.
- **Personal databases.** `private_to` on `organism_modules`,
  `stock_modules` and `inventory_modules` names the one user a database is
  for; empty is the lab's. Each blueprint's `_module_or_404` 404s someone
  else's personal database (admins may open it). In a request, the services'
  `list_modules()` return the lab's databases plus the user's own, which is
  what the sidebar, home, search and the Databases page show; pass
  `everyone=True` for admin views. `first_of_kind()` only ever returns a lab
  database.
- **Member permissions:** `members_create_databases` (default on) and
  `members_share_databases` (default off; the test suite turns it on in
  `tests/base.py`, since most tests have members create shared databases).

## Orders and stock

- **Required columns:** `settings["required"]` lists form names (`vendor`,
  `attr_price`…) a new item must have; `None` (never chosen) falls back to
  the preset's list, so older orders inventories get name, vendor,
  catalogue number and quantity. `ModuleView.required` / `requirable` in
  `inventory_service.py`; `_item_from_form` refuses a new item missing one
  and an edit that empties one, but an old item that never had it still
  saves. An order always needs a name. CSV import is not held to it.
- **Remembered values:** `inventory_service.remembered()` gives each text
  column's earlier values (datalists `inv-rem-<column>`) and a fill map by
  name and catalogue number. Inventories that track a supplier lend each
  other name, vendor and catalogue number, never quantity.
- **Stock kinds:** `inventory_service.RESTOCK_KINDS` (reagents, antibodies,
  viruses) are what a received order can become (`STOCK_KINDS`), what
  offers **Order again**, and what Home's *Expiring & low stock* watches.
- **Plasmid columns:** field type `plasmid` (the Viruses preset's *Made
  from*; any inventory can add one in Configure) stores the plasmid's
  number as text. `_item_from_form` reads a number, `#42`, a name or a
  picked `42 · name` through `inventory_service.resolve_plasmid()` and
  keeps the number; one it can't find is kept as typed, with a note.
  `plasmid_links()` links the sheet's cells; `made_from_plasmid()` fills
  the plasmid page's *Made from this plasmid* card. Import matches it by
  `sheet_import.ATTR_ALIASES` like any preset column.
- **Order again:** a reagent, antibody or virus row links to
  `/inventory/<orders>?reorder=<key>:<id>`; `_reorder_payload()` builds the
  new-order dialog, taking quantity, price and grant from the last order
  of the same thing (by `stocked_as`, then catalogue number). An open order
  for it (`_same_thing()`: made by Order again from it, the same catalogue
  number, or the same name when it has none; status in the orders
  inventory's `open_statuses`) turns the row's cart into **On order**
  (`_open_orders_of()`) and puts an amber `_warn` hint in the dialog.
- **Positions and Stored at:** `apply_status()` frees the box cell of an
  item moving to `GONE_FROM_BOX` (used up, empty, discarded) and writes
  "was in Box · D7" into an empty location note; `_item_from_form` then
  doesn't put it back from the dialog's unchanged box fields.
  `inventory_racks.stored_at` (revision 0007) is where a box is kept; a
  `before_flush` listener in `inventory_service` (`insert=True`, so it runs
  before the audit listener and undo restores it) gives any item whose
  `rack_id_fk` changed that value in its *Stored at* column
  (`stored_at_field()`: key `storage_temp` or label "Stored at"), whichever
  path moved it. Saving a box with a new Stored at updates its items.
  `save_rack` with `count` > 1 makes numbered boxes (`_create_boxes`).
- **Primers:** the same listener fills a primer's `length`, `gc` and `tm`
  (`PRIMER_DERIVED`) from `attrs.sequence` in a `primers` inventory
  (`primer_numbers()`: SantaLucia 1998 nearest-neighbour stacks, 50 mM Na⁺
  entropy correction, 250 nM primer in excess). The sheet shows them read
  only and `_row_json` sends them back after a save. `add_primer_pair`
  makes *name*-F and *name*-R through `_item_from_form`, flushing between
  them so the second takes the next free cell.
- **Presets:** `inventory.PRESETS` (and `lab.INVENTORY_CHOICES` for the
  setup survey) include `primers` and `cell_lines`; Samples has number
  columns for what was measured (`SAMPLE_MEASURES`), which revision 0008
  adds to Samples databases made earlier unless a column of that key or
  name is there. The same revision adds `plasmids.concentration`
  and `plasmids.a260_280` (typed numbers, checked by `_plasmid_measure`).
- **Set field:** the selection bar's `action=field` (`bulk_fields()`: the
  built-in columns the inventory uses, every custom one but *source*, and
  notes) runs each ticked row through `_item_from_form` with that one
  column, inside the batch; a refused value leaves that row as it was and
  is named. Organisms do the same for custom fields (`bulk_animals`,
  `bulk_housing`, `_set_custom`), and their sheet edits custom cells in
  place, with `attr_<key>_was` so a stale row doesn't undo a later change
  (`read_attrs_checked` skips a field whose value equals its `_was`).
- **Received → stock:** `_offers_stock()` is true when a save moved an
  order to received and it is not stocked yet; autosave and board moves
  answer `offer_stock`, the dialog redirects with `?offer=<id>`.
  `order_to_reagents` redirects to the new record with `?open=<id>`. These
  one-shot parameters are removed from the address on load and from the
  referrer in `_back()`.

## Calendar repeats and bookings

`app/lab_calendar.py`. A repeating event is one `calendar_events` row and a
`calendar_repeats` row (`freq` daily, weekly, monthly or `nthweekday` —
every month on the weekday of the first date, "the fifth" read as the
last; `interval`; `until`; `skip`, the dates taken out), expanded by
`occurrences()` for the range on screen. *Change this one only* posts a new
event with `split_from: {event_id, date}`, which adds that date to the
series' `skip` in the same transaction. A new booking may carry
`repeat: {freq: daily | weekdays | weekly, until}`: `_repeated_slots()`
lists the slots (at most `MAX_REPEATED_BOOKINGS`), every one is checked
for a clash first, and each becomes an `equipment_bookings` row of its own.

## Copies of the lab on every computer

`app/lab_copy.py`. On a server, someone who may (admins; members when Lab
setup's `members_keep_copies` is on) makes a key per computer under
Settings (`lab_copy_keys`, only a SHA-256 kept). With `Authorization:
Bearer <key>` a computer gets `GET /api/lab-copy/snapshot` (the whole
database written to a SQLite file with the app's own metadata, encrypted
columns blanked; SHA-256 and row counts in `X-BioManager-*` headers; one
per key every 2 minutes), `/api/lab-copy/files` (uploads: path and size)
and `/api/lab-copy/files/<path>`. The snapshot is read in one transaction
(REPEATABLE READ on PostgreSQL, a read transaction on SQLite), so it is one
moment. A non-admin's copy is narrowed by `member_view()`: password hashes
blanked, `ADMIN_ONLY` tables emptied, `OWN_ROWS` kept for them only,
notebook pages they can't open (and `PAGE_ROWS` hanging off them) and
other people's personal databases removed; their files are those the copy
names (`uploads_named_in`). A test fails when a new table holding someone's
own rows isn't classified there. Guests, pending and disabled accounts may
not keep copies. Wrong keys are throttled per address (a right key is never
throttled); from the internet (guest access) the gate refuses them like any
request without a session. The desktop app (`LOCAL_SETUP`) stores the address and the key
(encrypted) in `app_settings`, and `start_background()` (from `desktop.py`)
fetches a copy when the last good one is over 20 hours old: checksum and
`PRAGMA integrity_check` first, the newest `lab_copy_keep` kept in
`<data>/lab-copies/<host>/db/`, uploads mirrored into `uploads/` (never
deleted). A snapshot loads into a new server with
`scripts/migrate-to-postgres.py` (`tests/test_lab_copy.py` proves it on
PostgreSQL).

## One master copy, many devices

`app/devices.py`, the Settings → Devices page. One database is the lab's
master copy and every other device works on it through the master's
address: nothing is ever merged. A server is the master unless an admin
hands the role to a desktop. A desktop becomes a master by sharing its lab
on the network (**Share this lab on the network**): `start_sharing()` runs
a second werkzeug server on `0.0.0.0` (port 5870, or the next free one,
kept in the desktop prefs as `lan_port`) wrapped in middleware that sets
`environ["biomanager.lan"]`. `via_network()` reads that mark and
`on_this_computer()` is "a desktop, not over the network": the desktop-only
routes (`server_setup`, `lab_copy`'s link form, the Devices page's own
buttons) check it, and `security.setup_code_required()` asks for the setup
code from the network as a server does. Links and keys made there use
`share_url()`.

A desktop linked by a copy key (`lab_copy.py`) says hello every minute
(`POST /api/devices/hello`, Bearer key) with its role (master, window or
copy), version and address, kept on its `lab_copy_keys` row
(`device_role`, `device_version`, `device_url`, `last_seen_at`, migration
0014). `linked_devices()` lists them for the master's admins and says why
one can't take over (not open in the last 10 minutes, another version, a
non-admin's key). **Open the lab in this window** saves the server's
address as the desktop prefs' `window_url`, which `desktop.py` opens
instead of the local app; Go → This Computer's BioManager clears it.

The hand-over is a phase in `app_settings` `devices:state`: `asked` (an
admin chose a desktop), `frozen` (that desktop took over and is taking its
last copy), `away` (the master is elsewhere: `url`, `label`) and
`receiving` (a returned database is loading). In `frozen`, `away` and
`receiving` `refuse_changes_while_read_only` refuses every write but
sign-in and the device APIs (409 JSON to autosave, a flash otherwise), and
`base.html` shows the `devices_banner`. The desktop's side is
`take_over()` (freeze, `lab_copy.pull()`, `install_copy()`, share, report
its address to `/api/devices/handover/done`; on failure
`/handover/abort`) and `give_back()` (freeze itself, `write_snapshot()`,
upload the files the other lacks to `/api/devices/return/files/<path>`,
`POST /api/devices/return` with the SHA-256, wait for hello to report the
master back). Both load a lab with `load_lab()`: one transaction with
foreign keys deferred, every table emptied and refilled from the SQLite
snapshot, PostgreSQL sequences moved past the new ids, and the device's own
settings kept (`LOCAL_SETTINGS`: `devices:`, `lab_copy_`, `telemetry:`,
`server_setup`). Before it, `keep_a_copy()` writes what the device had to
`<data>/backups/before-taking-over-…db` or `before-taking-back-…db`.
Sessions survive only for the same account and password, so people sign
in again on the new master. A lost desktop is written off with **Make this
the master again** (clears `away`); a desktop that gave the lab back can
reload its own lab from `own_backup` (`/settings/devices/own-again`).
`tests/test_devices.py` covers both directions on SQLite and PostgreSQL.

## Notifications

`app/notify.py`. A `before_flush` listener looks at dirty records (mice,
cages, tanks, fish, organisms, housing, vials, inventory items) and new
organism genotype calls, and notes who should hear what: an owner change,
a move to another cage/tank, a genotype recorded, an order status. A new
item in the orders inventory tells every active admin but the requester
(`_order_request`, category `orders`). An `@name` newly added to a
record's notes (`NOTE_FIELDS`: inventory items, plasmids, mice, cages,
tanks, vials, organisms, housing) tells that person with a link to the
record (`_mentions`, category `notebook`); `_resolve` checks at commit that
the name is still there and is someone. Notes are
turned into `notifications` rows in `before_commit`, grouped per recipient,
category and kind of change (twenty mice moved: one row listing them), never
to the actor, and dropped on rollback. `send()` respects the
`users.notify_<category>` switches and skips disabled accounts.

The header bell (`base.html`, `static/shell.js`) polls
`/notifications/count` every minute while the tab is visible and fetches
`/notifications/panel` when opened; `/notifications/<id>/open` marks one read
and redirects only to a path in this app. The daily "waiting for genotyping"
reminder is made on a user's first page of the day.

## The lab notebook

Pages are `notebook_pages` inside a person's `notebook_tabs` (topics);
everything added in the rebuild lives beside them, in its own tables
(`app/models.py`, below the lab calendar's), so existing databases need no
column changes. `app/lab_notebook.py` has the routes (`/notebook/api/…`) and
the rules; the editor is `frontend/src/` and the page around it is
`app/static/notebook-page.js` and `app/static/notebook.css`.

| Table | Holds |
| --- | --- |
| `notebook_page_info` | kind (note, experiment, protocol, meeting, seminar, daily), status, tags, start/finish, the protocol an experiment follows, a meeting's series and presenter, the live-editing generation |
| `notebook_shares` | who else may open a page, and whether to view or edit (`*` is the whole lab, guests excepted) |
| `notebook_versions` | the page's history: `auto` (one person's edits within 10 minutes, up to an hour, fold into one), `manual`, `release` (a protocol's v1, v2 …), `restore` |
| `notebook_sync_updates`, `notebook_presence` | live editing: Yjs updates and cursors (below) |
| `notebook_comments` | comments on a page or a quoted passage, and replies |
| `notebook_recipes` | the lab's buffer library (the built-in ones are `PRESET_RECIPES`) |
| `notebook_meeting_series` | a meeting's rotation (`members` in order, `next_index`), day and time |
| `notebook_templates` | a person's templates: title, Markdown, the page `kind` a page made from it gets, and `lab` (everyone may start from it; revision 0009) |

**Durations and clocks.** `frontend/src/durations.js` finds what gets a
step timer and leaves time points out (a list of times, "at 24 h", "48 h
samples"); `tests/js/durations.check.mjs` holds the cases; `timerLabel()` names a
timer from the words of its own sentence before the duration. The page shows
times in the lab's zone (`lab.clock_zone()`, `labZone` in `#nb-data`),
the clock the app writes "Started:" lines on. A new page goes in the topic
open (`open_tab_id`, sent only when a topic was chosen, `topicChosen`), else
in *Inbox*, unless it is an experiment or a meeting.

**Templates.** A name the person already uses answers 409 `exists`, and
the page asks before sending `replace=1`, which saves over it.
`Save as template` posts `from_page_id`, and with
`structure_only=1` the body goes through `lab_notebook.structure_only()`:
headings, text and table headers stay; ticks are cleared, a table's body
rows keep only their first cell, uploaded images and files go, and data
blocks keep their setup (`_empty_block`: a sheet's columns and first
cells, a plate's roles, a qPCR block's reference and control; results
emptied).

**Record links.** `@<type> <number>` in a page is a chip
(`frontend/src/extensions/MentionDecoration.js`), the `@` menu is
`MentionSuggestion.js`, and the words they know are `mentionTypes.js`:
`mouse`, `plasmid`, `order`, and every inventory key the page lists in
`#nb-mention-types` (`app._mention_modules()`: inventories the person can
see, except orders, which is `@order`). `/notebook/search|lookup|open|
backlinks/<type>/…` serve each kind; an inventory record's popover is
`{name, fields: [[label, value]…]}`; the chip shows `name` after the
number (`data-entity-name`, fetched once per record and cached).
`static/used-in.js` fills *Used in notebook
pages* on an inventory record's dialog (from the payload's `_number`) and
a plasmid's Storage tab.

**Colony experiments in a page.** The `experiment` block
(`frontend/src/blocks/experiment.js`) keeps only `{"id", "show",
"percent"}` and reads `/colony/experiments/<id>/notebook.json`; *Freeze a
copy* stores that payload in the block as `frozen`, so the page's
versions keep it. The payload, the plan and the records are
`app/experiment_steps.py`:

Experiments are on any database's animals (`app/experiments.py`):
`experiments.db` says which (`colony`, `zebrafish`, `stocks:<key>`,
`organisms:<key>`) and `experiments.readout` what is measured (JSON; blank
is the database's usual one). `experiments.Place` is what an experiment
needs to know of its database (nouns, housing, the kinds of manipulation
its animals get, the readouts that fit), and `subjects()` gives its
animals the same shape whatever they are. One page serves them all:
`templates/experiment.html`, drawn by `static/experiment-page.js` from
`/experiments/<id>/data.json`, and every change answers with that data.
Each database lists its experiments on its own **Experiments** tab: the
colony's in `colony.html`, the others through `templates/_experiments_tab.html`
with `tab_context()` (the zebrafish, stock and organism routes pass it as
`experiments_tab` when that tab is open). `Place.list_url` is that tab, so
the page's *All experiments* and a delete go back to it, and the old list
address `/experiments/in/<db>` redirects there. A new experiment can name
a group (`from_group`: a tank, rack or housing, from `candidates()`) whose
animals it starts with.

| Table | Holds |
| --- | --- |
| `experiment_subjects` | the animals of an experiment that isn't the colony's: a fish row, a vial or plate, an organism; its treatment group and how many there were at the start |
| `experiment_readings` | one readout per animal, readout and day (a mouse's body weight is a `mouse_weights` row instead) |
| `experiment_regimens` | saved regimens: an experiment's lines and days, per kind of animal, to plan the next one from (planning never records anything done) |
| `experiment_steps` | an experiment's manipulations: days (`"2–5"`, day 1 = the start date), kind, agent, dose, route, concentration, treatment group |
| `experiment_step_records` | one per step and day done: date, who, and per mouse the weight used and the amount and volume given (`mice`, JSON) |

A `reading` step (`weigh` in the first version) writes the readout when recorded.
A step may name the inventory item it uses (`reagent_item_id_fk`); a record
keeps that item as it was then (`reagent`: name, lot, expiry) and any sample
records the person chose to make with it (`samples`), made through
`inventory_routes._item_from_form` with the animal as their Source. Subjects
are `mouse`, `fish`, `clutch`, `unit`, `organism` or `cohort`.
`exp_stats.py` compares the groups on each day (Welch, ANOVA, χ²);
`/experiments/<id>/export.xlsx` and bench mode (`/experiments/<id>/bench`,
`static/experiment-bench.js`) are in `experiments.py`. The owner's
morning notification of what is due is `notify.daily_experiment_reminder`
(category `experiments`, `users.notify_experiments`). Undone days are
`auto` items on the calendar (`experiment_steps.calendar_items`). Which
notebook page is a person's for an experiment is the app setting
`experiment_notebook_page:<experiment>:<username>`.

**Signed pages** (`app/signatures.py`, table `record_signatures`): sign,
witness and amend events, each with the SHA-256 of the title and text at
that moment and (for a sign) the version it made. A page is locked while
its newest sign has no amend after it; then `page_payload` gives the
editor role `view` (`real_role` keeps the owner's sharing),
`load_page(need="edit")` answers 423, and so do the live-sync push and
`/notebook/pages/<id>/update`. Signing freezes live `experiment` blocks
into the text and resets live editing so open editors reload it.

**Who may do what** is `lab_notebook.role_for()`: `owner`, `edit`, `view` or
nothing. Viewers read and comment; editors also write; only the owner
shares, moves or deletes. Search, backlinks and the global search use
`accessible_filter()`, so a shared page is found wherever the owner's is.

**Live editing** needs no websocket: each open editor holds the page as a
Yjs document (`frontend/src/collab.js`), posts its updates (base64) to
`/notebook/api/pages/<id>/sync` and polls the same address for everyone
else's, every 1.2 s while someone else is on the page and every 4 s
alone. Yjs merges updates in any order, so two people typing in one
paragraph both keep their words. The first editor to open a page seeds it
from the saved Markdown (`init`, refused if someone else got there first).
Cursors travel as Yjs awareness updates in `notebook_presence`. When the
log grows past a few hundred updates, one editor replaces it with a single
snapshot (`/sync/compact`).

The Markdown in `notebook_pages.body` stays the source for search,
history and export: editors save it after their changes (with
`X-Collab-Gen`, and `collab_state`: the Yjs state vector of what the
editor held), and a version is credited to whoever last typed. The page
keeps the state its body was saved at (`notebook_page_info.body_state`,
revision 0012); a save whose state is strictly behind it
(`lab_notebook.behind`, e.g. a background tab that had not caught up) is
answered `behind` and not written. Anything
that replaces the text from outside the editor (restoring a version, the
plain-text fallback) bumps `collab_generation` and clears the log; open
editors are told to start again from the saved text.

**Blocks** (data sheets, recipes, calculators, plates, qPCR, diagrams,
equations) are one TipTap node, `labBlock` (`frontend/src/blocks/`). In
Markdown each is a fenced block named by its kind (```` ```sheet ````,
```` ```recipe ````, ```` ```mermaid ```` …) holding JSON or the source, so a
page reads anywhere and GitHub draws the diagrams and maths itself. In the
editor the data is one node attribute rather than text: Yjs merges text
character by character, which would splice two people's JSON, while an
attribute is replaced whole. Statistics (`blocks/stats.js`) and formula
columns (`blocks/formula.js`) are computed in the browser; formulas are
parsed by hand because the security policy forbids `eval`.

Mermaid and KaTeX are large and most pages use neither: `npm run
build:vendor` copies them to `app/static/notebook-build/vendor/`, and they
load the first time a page shows a diagram or an equation.

## Change history

Every create, edit and delete on a tracked table writes an `audit_log` row
with a field-level diff (`genotype: DBH-Cre → ∅`). This is done with a
SQLAlchemy `before_flush` listener in `app/audit.py`, not per-route calls, so
it covers the whole app including the organism engine, and the audit row is
written in the same transaction as the change it describes. Passwords and
tokens are redacted; high-churn tables are excluded. Admins read it at
`/audit`.

## Keeping the data safe

**Schema changes are Alembic revisions** (`migrations/versions/`,
`app/upgrade.py`). On start-up `services.init_database()` asks
`upgrade.plan()` what opening the database will change; if anything, it
copies an SQLite database first (`backups/before-upgrade-<from>-to-<to>-<time>.db`,
ten kept), then runs `create_all()`, the frozen pre-0.8 ALTERs
(`ensure_schema_updates`, which stop at revision `0002_v0_8_schema`), the
one-off data migrations, and `alembic upgrade head`. A new database is
stamped at head instead. Because create_all() runs first, a revision must
tolerate what it adds already being there: use `migrations/helpers.py`
(`create_table`, `add_column`). `scripts/upgrade-check.py` makes a demo
lab with every release tag, opens it with this code and checks the schema,
the row counts, the revision, the copy and the pages. With `--postgres
<url>` it does the same for a lab server: that release's own
`migrate-to-postgres.py` moves its demo lab into a new PostgreSQL database
on that server, and this code opens it (no copy there: the backup service
takes one before an update). `.github/workflows/upgrade-check.yml` runs
both on master and on tags.

**A SQLite file must not live in a cloud-synced folder.** OneDrive, Dropbox
and Google Drive do not honour SQLite's file locking: a sync mid-write, or
two machines with the folder open, corrupts the file outright. The app logs a
loud warning at startup if it detects this.

```bash
python scripts/dbtool.py check                      # location, integrity, sync risk
python scripts/dbtool.py backup                     # consistent snapshot, keeps 30
python scripts/dbtool.py relocate ~/BioManagerData  # move it somewhere local
python scripts/dbtool.py restore <file>
```

These are for a SQLite database on one machine. A server on PostgreSQL is
backed up by the backup service in `deploy/` (`deploy/backup/backup.sh`
runs without Docker too). `restore` checks the backup is a healthy
BioManager database and asks that the app be closed first (`--yes` skips
the question).

**The app won't start on a database it can't trust**: an SQLite file of 0
bytes (`services.refuse_emptied_database`: it would otherwise start a new
empty lab over it), or a database whose Alembic revision this version
doesn't know, i.e. one a newer version made (`upgrade.plan`).

**Failures get the app's own pages.** `OperationalError` (the database
down, locked, or its disk full) answers 503 with `error_plain.html`, which
needs nothing from the database; 404, 500, `StaleDataError`, `OverflowError`
and the integrity and data errors have handlers too, and each answers JSON
to a background save, a page script (`X-Requested-With: fetch`) or the API
(`_wants_json`). On SQLite, `lower()` is replaced with Python's, so
case-insensitive search folds every script as on PostgreSQL; typed search
text goes through `formutil.like_pattern` with `escape="\\"`, so `%` and
`_` are literal. `formutil.arg_int` reads numbers from the address.

`relocate` copies, verifies with an integrity check, and only then retires
the original — then prints the `BIOMANAGER_DATA_DIR` to export. Backups use
SQLite's backup API, so they are consistent even while the app is running.

Schema changes go through **Alembic** (`migrations/`). Existing databases are
stamped at `0001_baseline` automatically on boot:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

The old hand-written ALTERs in `services.ensure_schema_updates()` still run
for backwards compatibility, but new changes belong in a revision.

## Cage cards and QR labels

Printable, correctly-sized cards with a QR that opens the record — so someone
at the rack scans instead of walking back to type an ID.

- Mouse cages: **Cage cards** on the Cages view, or `/labels/cards/cages`
- Zebrafish tanks: **Tank labels**, or `/labels/cards/tanks`
- Organism modules: **Labels** on the Housing view, or `/labels/cards/<module>`
- Fly and worm stocks: **Labels**, or `/labels/cards/stocks/<key>`
- Inventories: **Labels** on the ticked rows, `/labels/cards/inventory/<key>?ids=…`

Each takes `ids=1,2,3` or the selection bar's repeated `selected_ids`.
`?stock=` picks what they print on (`labels.STOCKS`): `sheet`, or a label
printer's size, which prints one label a page (`@page { size }`, no
margin) with type sized by `labels.fit()`; the last choice for each kind is
kept in the session cookie. `?format=zpl&dpi=203|300` returns ZPL II
(`labels.to_zpl`: `^CI28` UTF-8, text through `^FH_` so `^ ~ _` are hex,
`^BQN` QR at the largest magnification that fits). For inventories the
page offers *On each label* (`f=` repeated, with `fields_set=1`; kept per
kind in the `label_fields` cookie) and *Two lines for long text* (`wrap`,
`label_wrap`): CSS line-clamp on the page, a two-line `^FB` title in ZPL.
An admin sets the lab's
Zebra (`app_settings.label_printer`, host or host:port, and
`label_printer_dpi`); `POST /labels/send` opens a socket to it on port
9100. `labels.printer_address` only accepts private, loopback, link-local
and Tailscale (100.64/10) addresses, so the server never sends to the
internet.

Cards are laid out in millimetres and print without any app chrome. QR
payloads are absolute URLs built from the incoming request, so a card printed
on the lab server scans to the lab server. Rendering uses `segno` (pure
Python, no image libraries), and cards still print without it — just without
the code.

## The public API

`app/api.py`: `/api/v1`, JSON, for scripts, instruments and other tools;
the reference page is `/api` and the spec `/api/v1/openapi.json`, both made
from `ENDPOINTS`, so a new endpoint goes there too.

- **Tokens** (`api_tokens`): made under Settings → API tokens (`api/_card.html`),
  `bmt_` and 40 random characters, shown once (`api/token.html`); only the
  SHA-256 and the first ten characters (`hint`) are kept. `scope` is `read`,
  `write` or `propose` (below); `expires_at` 30, 90, 365 days or never. Members make them only
  while Lab setup's `members_api_tokens` is on (default on); guests never.
  Admins see and revoke everyone's.
- **Signing in**: `app.load_current_user` hands `/api/v1…` to
  `api.authenticate()`, which reads only `Authorization: Bearer` and never
  the session cookie. So `security.cross_site_reason` skips those paths (a
  cross-site request can't carry the token), `lab_routes.remind_once_a_day`
  skips them, and `api._no_cookie` strips any Set-Cookie. A disabled
  account, a revoked or expired token, or members' tokens switched off: 401.
  `g.audit_batch = "API: <label>"` marks the change history.
- `_gate`: 401 without a token (with `WWW-Authenticate` pointing at the
  OAuth resource metadata, below), 403 when a read token tries a change, 429
  past `PER_MINUTE` (600) per token per worker (`_Rate`, in memory).
- **Lists** page by row id: `?limit` (100, at most 1000) and `?after`;
  the reply's `next` is the URL of the following page. Filters are in SQL.
- **Writes reuse the pages' code**, so their rules hold: `PATCH /mice/<id>`
  fills a form from `mouse_display_row`, overlays the fields sent, and calls
  `populate_mouse_from_form` (flashed refusals come back as `warnings`);
  inventory items go through `inventory_routes._item_from_form`, vials
  through `stock_routes._unit_from_form` (both copy only the fields sent),
  readouts through `experiments.set_reading`. Permission checks are the
  same functions (`can_edit_mouse`, `_can_edit`, `can_edit`,
  `access.can_edit_experiment`). A switched-off built-in database is a 404.

- **For assistants** (`app/lookup.py`): `/resolve?q=` finds the records a
  phrase means across every database the person can open ("cage 88" looks
  only at cages; a mouse by its number, a vial by its prefix and number),
  each with a reference `{"kind", "id"}` (the kinds are `actions.KINDS`);
  `/vocabulary` is the lab's own words; `/due` is Home's agenda
  (`home_layouts.build_agenda`), whose items carry `ref` and `propose`, the
  change that records each done; `/actions` is `actions.catalogue()`.

## Proposed changes: running the pages on someone's behalf

What an assistant proposes is a list of things a person does on the pages.
Rather than a second copy of each page's rules, every change is sent to the
page itself.

- `app/contained.py`: `transaction()` opens one connection and transaction;
  while it is set (`db.JOINED`, a ContextVar), every `SessionLocal()` is a
  `db.LabSession` joined to it with `join_transaction_mode="create_savepoint"`,
  so a page's `commit()` releases a savepoint and its `rollback()` undoes only
  its own part. SQLite gets its own engine (`db.contained_engine`) that
  leaves transactions to SQLAlchemy and sends `BEGIN` itself, without which
  savepoints don't nest. `run(app, username, method, path, data|json_body)`
  sends one request through `app.full_dispatch_request()` as that person
  (`contained.acting_user()`, read by `load_current_user`; for `/api/v1`
  it also stands in a write token, `contained.API_TOKEN`, with no rate
  limit), so every before_request check and the view's own permissions
  apply, and returns what the page said: its flashed messages (or its
  JSON) and whether it refused (a 4xx/5xx, an error flash, `ok: false`, a
  redirect to sign in). Leaving the block without `commit()` rolls it all
  back: a preview.
- `app/actions.py`: the catalogue, one `Action` per thing (`litter_born`,
  `experiment_step_done`, `notebook_note`,
  `wean`, `tank_new`, `stock_event`, `org_animal_change`…): its target kind,
  its fields (which also give the JSON Schema for the MCP server), a plain
  summary line, and `build`, which turns checked values into the `Call`s the
  page would send. `find()` turns a reference, or what the lab writes on a
  record (cage ID, `#14`, `FV12`), into the row, refusing one that could be
  several. `run(app, username, changes, apply=False)` runs a list in order
  inside one `contained.transaction()`, each change in its own savepoint
  (a failed one is undone before the next, so a preview reports each
  change's own problems), and collects the change-history rows each wrote
  (`records`, the before → after). With `apply=True` it first writes one
  `BatchRecord` ("mixed") and runs every page inside it: `audit.batch`
  called while another batch is open joins it (yielding a stand-in, no
  second row), so Batch history undoes the whole proposal as one; anything
  failing keeps nothing.

### Proposals (`app/proposals.py`)

- `proposals` and `proposal_changes` (revision `0017_proposals`): a proposal
  belongs to the person whose token sent it (`owner_username`), with its
  `source` ("Claude", or the token's name), the assistant's `summary`, a
  `status` (pending, approved, discarded, superseded, expired, invalid),
  `expires_at` (14 days), `replaces_id`, `batch_id_fk` once approved, and
  `audit_mark`, the change history's newest id when it was previewed. Each
  change keeps the request as sent, its summary, errors, warnings and the
  change-history rows the preview wrote (`records_json`). Both tables are
  left out of members' lab copies (`lab_copy.ADMIN_ONLY`).
- `create()` previews with `actions.run` in the owner's language
  (`i18n.language_for`; `contained.run(lang=…)` makes the pages answer in
  it too), stores the result (pending, or invalid with the reasons), marks
  `replaces` superseded and notifies the owner (`notify.send`, linking to
  `/proposals`). It commits the session's read before the preview starts,
  so SQLite's lock is not held across it.
- `approve()` refuses unless pending; refuses when a record the preview
  changed (not one it made) has a change-history row after `audit_mark`
  (`changed_since`, naming it); otherwise runs `actions.run(apply=True)`
  with `label()` / `description()` ("Proposal #12: … (via Claude)") and
  keeps it only if every change went through. `expire_old()` is applied
  lazily wherever proposals are read.
- API (`app/api.py`): scope `propose` (`SCOPES`) may read and call only
  `PROPOSING` (create, discard); a write token may propose too. There is
  no approve endpoint. `GET /proposals`, `GET /proposals/<id>` (with each
  change's request and records) are the owner's only.
- The page (`proposals.bp`, `templates/proposals.html`): pending proposals
  with their changes grouped by `AREAS`, warnings first, **Approve** /
  **Discard**, and **Show each change** (the stored records); recent
  decisions below. The account menu shows **Proposed changes**, with the
  pending count, once someone has any (`inject_user`).

### Notes into the notebook (`lab_notebook.add_note`)

`POST /notebook/api/notes` {text, time, via, page_id} adds `- **HH:MM** text`
to the end of a page's Log section (made if missing), on today's daily log
(`_daily_page`, shared with `/today`) unless a page is named. A page with
no live-editing updates in its current generation has nothing in Yjs to
lose, so its Markdown is changed directly (`append_log_line`, a version
recorded). One that has them gets a `notebook_pending_inserts` row
instead: `sync_pull` hands unclaimed rows (or rows claimed more than
`CLAIM_FOR` ago) to the editor that asks, which adds each with the Log
button's `appendLogLine` (so it travels through Yjs like typing) and posts
`/inserts/done`. Notebook rows aren't in the change history, so undoing an
approved proposal doesn't take a note back out.

### The tools (`app/assistant_tools.py`)

What an assistant gets, standard library only so both servers below share
it: `INSTRUCTIONS` (resolve every record, ask rather than guess, send one
proposal, give the review link), `PROMPTS`, and `TOOLS`, each a
description, an input JSON Schema and a function over `Api` (a small
urllib client of `/api/v1`): `lab_overview` → `/me` and `/vocabulary`,
`resolve`, `get`/`list` limited to the API's read paths (`READABLE`),
`whats_due`, `list_actions`, `propose_changes` → `POST /proposals`,
`proposal_status`, `discard_proposal`. `propose_changes`' description is
built from the lab's own `/actions` (`description(api, name)`).
`call_tool()` turns an `ApiError` or bad arguments into `{"error": …}` for
the assistant to read.

### The local MCP server (`mcp/`)

`mcp/biomanager_mcp.py` (the `mcp` SDK, version 2: `MCPServer`, stdio) is
started as a command by an assistant app that can't use an address
(Claude Desktop on the lab network); it is run from this repository, not
packaged (`mcp/requirements.txt`, `mcp/README.md`). `build_server()`
registers `TOOLS` and `PROMPTS`; it calls `/api/v1` with `BIOMANAGER_URL`
and `BIOMANAGER_TOKEN` and signs proposals `BIOMANAGER_ASSISTANT`.
`tests/test_mcp.py` runs the tools against the test app without the SDK.

### The MCP endpoint (`app/mcp_http.py`)

`POST /api/v1/mcp`: MCP's streamable HTTP, kept to what the clients need.
One JSON-RPC message (or a batch) per POST, answered with JSON;
notifications get 202; no session and no event stream (`GET` is 405,
from `api.unknown`). `initialize` echoes a version in `PROTOCOL_VERSIONS`
or offers the newest; `tools/list` adds titles and read-only hints;
`tools/call` returns the result as text and `structuredContent`. It sits
in `/api/v1`, so `_gate` checks the token and the rate; any scope may call
it (`api.mcp` is in `PROPOSING`), and each tool runs through
`InProcessApi`, the app's test client with the caller's own token, so a
tool can do nothing the API wouldn't for that token. Proposals are signed
with the token's label (the OAuth client's name, "Claude").

### Connectors: OAuth (`app/oauth.py`)

claude.ai, the Claude apps and ChatGPT add `/api/v1/mcp` as a custom
connector and sign in with OAuth 2.1; their servers make the calls, so
this needs the lab on the internet (`BIOMANAGER_PUBLIC_URL`, set by
`internet-access.sh`). Claude Code can use it too, with a loopback
redirect.

- Discovery: `/.well-known/oauth-protected-resource[/api/v1/mcp]` (RFC 9728;
  `resource` is `issuer()/api/v1/mcp`) and
  `/.well-known/oauth-authorization-server` (RFC 8414). The issuer is
  `request.url_root`, so from the internet it is the public address
  (Caddy's internet site sets `Host`), from inside the lab's own.
- `POST /oauth/register` (RFC 7591, JSON): https or loopback redirects;
  public clients (`none`) or a `bms_` secret (post or Basic). Clients
  unused for `UNUSED_CLIENT_FOR` go, unless a grant made with one can
  still be refreshed.
- `/oauth/authorize`: an unknown client or redirect is an error page (never
  a redirect); then `response_type=code`, PKCE `S256` and `resource` are
  required. Loopback redirects match on any port (`redirect_matches`).
  Signed in: a consent page naming the client and where it sends you back.
  From the internet nobody is signed in (the lab never shows its sign-in
  form there), so the page takes a **connection code** instead
  (`oauth_link_codes`: made on Connect an AI assistant, hashed, once, 10
  minutes, wrong guesses throttled by `link_throttle`). The person must be
  allowed to make tokens (`api.may_make_tokens`). Every answer carries
  `iss` (RFC 9207, which ChatGPT wants).
- `POST /oauth/token` (form-encoded): an `authorization_code` (`oauth_codes`,
  hashed, once, 5 minutes, PKCE and redirect checked) gives an access
  token that is an `api_tokens` row (scope `propose`, label the client's
  name, an hour) and a `bmr_` refresh token (`oauth_grants`, 90 days). A
  refresh rotates both into the same `api_tokens` row, so Settings lists
  one row per connection; revoking it ends the connection
  (`invalid_grant`). Errors are RFC 6749's JSON.
- The internet gate (`guests.OPEN_PREFIXES`) lets `/api/v1/`, `/oauth/`
  and `/.well-known/oauth-` through without a session: each answers only to
  a token, a code or with public metadata. `/oauth/token` and
  `/oauth/register` skip the cross-site check (no cookie is involved).
- **Settings → API tokens → Connect an AI assistant**
  (`oauth.connect_page`, `oauth/connect.html`): the connector address and
  **Get a connection code** when the lab is on the internet, and **Make a
  token** (scope `propose`), after which `api/token.html` shows the Claude
  Code command, the address-and-header block for Cursor and Cherry Studio,
  and the local server's setup.
- The four tables are admin-only in a lab copy (`lab_copy.ADMIN_ONLY`).

## Feedback and the usage report

`app/feedback.py`, for a pilot (the plan is `docs/PILOT.md`). **Feedback**
in the rail opens `/feedback?from=<the page>`; a note is a `feedback` row
(kind, text, the page's path, `app_version()`, a short platform string)
and each admin gets a notification. Admins see every note and mark it done;
members see their own. `issue_url()` builds a GitHub "new issue" link on
gaspolymerase/biomanager with the text, path, version and platform —
never the server's host or the person — which the person submits
themselves: nothing is sent by the server.

`/feedback/usage` (admins) is `usage()`: for each of the last eight weeks
(Monday to Sunday), the distinct lab accounts in the change history and
its rows per area (`AREAS`, by table-name prefix), notebook pages and
calendar events created; and totals now. `usage_text()` is the same as
plain text to paste into an email.

## Anonymous daily counts

`app/telemetry.py` (its docstring is the full account). Once a day an
installation posts one PostHog event to `HOST/i/v0/e/`:
`{"api_key", "event": "heartbeat", "distinct_id", "properties"}`. The
properties are `version`, `kind` (`desktop` under `LOCAL_SETUP`, else
`server`), `os` (`platform.system()`), `database` (the dialect),
`members` and `active_7_days` as ranges (`bucket()`: 0, 1, 2-5, 6-15,
16-50, 51+; active accounts, and lab accounts in the change history in the
last 7 days), `functions_on` (keys of `lab.FEATURES` that are on),
`databases_on` (enabled organism databases; stock databases by `kind`;
inventories by preset `kind`, anything else as `custom`), and
`$geoip_disable: true`, `$process_person_profile: false`. Nothing else:
never a name, a label, a key, a host or free text. A new property must keep
to that, and `tests/test_telemetry.py` checks names don't leak.

- **When**: `after_request` checks at most once an hour per process
  (`CHECK_EVERY`), then a daemon thread with the app context calls
  `send_if_due()`, which needs a key, no env switch, the lab switch on and
  the setup survey answered, and claims `telemetry:last_sent` with a
  conditional UPDATE so two gunicorn workers never both send. A failed
  post (5 s timeout, `urllib`) puts the old stamp back, logs at debug and
  is retried the next hour. Never under `TESTING` (the hook checks).
- **app_settings**: `telemetry:enabled` (`on`/`off`, default on; the first
  survey's checkbox, and **Switch on/off** on the Usage report,
  `POST /feedback/usage/heartbeat`), `telemetry:install_id` (a `uuid4`,
  made on first use; the Usage report's preview makes it too, so the JSON
  shown is exact), `telemetry:last_sent` (UTC ISO).
- **Environment**: `BIOMANAGER_TELEMETRY_KEY` (the project's public
  `phc_…` key; overrides `PROJECT_KEY`, BioManager's own project: a
  write-only key, safe in public code; with neither, nothing is ever
  sent), `BIOMANAGER_TELEMETRY=0` or `DO_NOT_TRACK=1`
  (off, whatever the admin chose; the page says "Off (set by the server)"),
  and `CI=true`, which GitHub Actions sets: the upgrade check opens every
  earlier release's database, and each would otherwise count as a lab.
  The Docker stack passes both switches from `deploy/.env`. A demo lab
  from `scripts/demo-data.py` is switched off in its own settings, so
  screenshots and the launch clips never count as a lab.

## Reminder emails

A daily digest of what is overdue or imminent: module schedule items (flips,
chunks, re-freezes), litters reaching weaning, breeders past 30 weeks, and
personal tasks. Configure by environment:

```bash
export BIOMANAGER_SMTP_HOST=smtp.example.edu
export BIOMANAGER_SMTP_PORT=587
export BIOMANAGER_SMTP_USER=biomanager@example.edu
export BIOMANAGER_SMTP_PASSWORD='an app password'
export BIOMANAGER_BASE_URL=http://lab-server:5055
```

```bash
scripts/send-reminders.py --dry-run     # print digests, send nothing
scripts/send-reminders.py               # send
```

Daily, via cron:

```
0 8 * * *  cd /path/to/Biomanager && .venv/bin/python scripts/send-reminders.py
```

With SMTP unconfigured the digests are logged rather than sent, so the job is
safe to schedule before a mail server exists. People with no email address on
their account are skipped. Settings shows the current delivery status.

## Batch operations

Two directions of the same idea: one specification applied to many records.

**Acting on records that exist** — tick rows in the mice table and a
selection bar rises from the bottom of the viewport:

- **Set** one field (owner, status, genotype, cage, note, date of death)
  across the selection
- **Add to experiment** with a shared treatment group — this is the "N mice
  under the same manipulation" case
- **Sac**, with a confirmation naming the count

Shift-click extends a range, the header checkbox selects everything
*visible* (never filtered-out rows), and each action applies only to records
you may edit, reporting how many were skipped rather than failing outright.

The bar is generic — `static/selection-bar.js` reads a markup contract, so
another table gets batch actions by adding `data-selection-scope`, row
checkboxes, and a form marked `data-selection-form`. No JavaScript changes.

**Creating records** — **Add many** on the colony page
(`/colony/mice/batch`) is a two-step flow: describe one mouse and say how
many, or upload a CSV, then check and edit an editable preview grid before
anything is written.

- Counts can be split by sex (`4 females, 2 males`) — the usual shape of a
  litter or an order.
- **IDs are assigned in ascending order** and shown in the preview
  (`IDs #25 – #30`). They are allocated again at save time, so a preview left
  open while someone else adds mice cannot collide.
- `new` in the cage field puts the whole batch in **one** freshly allocated
  cage; type numbers per row in the preview to split them.
- **Fill down** copies the first row's value into empty cells below — the
  usual fix after a CSV that only filled the first line.
- Tick **Skip** to leave a row out without deleting it.

CSV headers are matched loosely: `sex`, `dob`, `cage`, `litter` and `notes`
map onto the real columns, unknown columns are ignored, and `mouse_id` should
be left out entirely so IDs are assigned for you.

The older `/import/<entity>` endpoint still serves plasmid and order imports,
and numbers mice with one counter that skips every number used in the
database or earlier in the file, the same in the dry run (so a repeated ID
is reported per row); a real run is a batch.

**Numbers are handed out once.** `services.reserve_mouse_ids()` counts
above the highest mouse and above `app_settings.mouse_id_high`, the highest
ever handed out, so deleting the newest mouse or undoing an Add many never
gives its number again. Saves that take the next free number (New mouse,
Add many, New cage, litters, Wean and distribute, the CSV import) are
wrapped in `next_number_retried`: two people saving at once read the same
highest number, the database refuses the second, and the save is tried
again with a fresh one instead of reporting an ID the person never typed.
One cage per rack place is a unique index (`uq_mouse_cages_place`,
revision 0006); a swap on the rack grid moves the cages in steps.

**The Cages tab's three layouts.** `view_switch(..., cards_label=)` in
`_rack_grid.html` adds a Cards button; `rack-grid.js` shows one
`data-layout-panel` and fires `layout:change` on the scope. Each cage's
panel (`article[data-cage-card]` in its `tr.cage-detail`, drawn open; Close
all is remembered as `cages:open` in `localStorage`, and the row's arrow
or mice count opens or closes one) is also its card: while Cards is
shown, `colony.html` moves the articles into `[data-cage-cards]` (marked
`data-autosave-sheet`, so `sheet.js` saves their fields as before) and
back into their rows for the table, so nothing is rendered twice. A
panel's `.cage-card-head` shows only on a card. The cage's number is a
cell of its form (`cage_id`, with `cage_id_was`): `renumber_cage` refuses
a blank, `new` or another cage's number; mice follow by `cage_id_fk`.

> Previously this path was broken: `next_mouse_id()` was called per row, and
> because the session runs with `autoflush=False` the `max()` query could not
> see pending rows, so every row was handed the same ID and the import died
> on the unique index. Any CSV without explicit `mouse_id` values failed
> outright. The allocators now flush first, and batch paths reserve a
> contiguous block up front.

### Batches and undo

Every bulk action is recorded as a **batch** — one row in `batches` saying
who ran it, what it did and to how many records — and its audit entries
point back at it. **Batches** in the sidebar (`/batches`) lists them with
what undoing each would do.

Undo reverses the recorded changes, newest first:

| The batch | Undo does |
| --- | --- |
| created records | deletes them |
| edited records | puts every column back to its previous value |
| deleted records | re-inserts them from the stored snapshot |

It refuses in two cases, loudly rather than silently: a batch already undone,
and a record **changed again after the batch** — reverting then would discard
whoever's later edit. That second case offers *Undo anyway*. The undo is
itself recorded as a batch, so undoing an undo is a redo: the original
batch is then in force again (its `undone_at` cleared) and can be undone
again; the batch's own undos and redos don't count as later edits. A batch
is claimed with a conditional UPDATE before anything is reversed, so two
people pressing Undo together undo it once. Records the batch created are
reversed last (after the records that point at them are put back), since
in log order a redo's re-made cage comes after its mice's moves. Wean and
Wean and distribute are batches too.

This needed audit entries to carry a machine-readable diff, not just prose:
`audit_log.changes_json` holds `{"changes": {field: [before, after]}}` for an
edit and `{"snapshot": {...}}` for a delete. Parsing
`genotype: ∅ → C57BL/6` back into a value would have been guesswork.

**Stale rows.** A sheet row posts every cell. The mouse row also posts a
`<name>_was` copy of what each cell showed, and `populate_mouse_from_form`
writes a field only when `form_changed()` says it differs, so saving one
cell of a row opened before a colleague's edit keeps that edit.
`sheet.js` refreshes the copies after each save. A one-line cell can't hold
a line break (browsers drop them from an `<input>`), so a value sent back
equal to the stored one without its line breaks counts as unchanged
(`_keep_lines`).

Two ordering details worth knowing if you touch `app/audit.py`:

- **Inserts are logged in `after_flush`, not `before_flush`.** A new row has
  no primary key until the INSERT runs, so logging it earlier records
  `record_id = 0` and undo has nothing to find.
- **`audit.batch()` flushes on the way out**, so callers that commit after
  the block still get their audit rows attached to the batch.

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Build the stylesheet once: `cd frontend && npm install && npm run build:css && cd ..`.
4. Start the app with `python run.py`. On macOS port 5000 is taken by
   AirPlay/Control Center, so use `PORT=5055 python run.py` if the page does
   not load. `FLASK_DEBUG=1` turns on reloading and in-browser tracebacks;
   `run.py` refuses it on anything but a loopback address.
5. Open `http://127.0.0.1:5000` (or the port you set).
6. Register the first user, who becomes `admin`. The page asks for the
   **setup code** printed in the terminal when the app started (it is also
   in `data/setup-code`, which is deleted once the admin exists). The desktop
   app does not ask: only this machine can reach it.

The SQLite database is created automatically at `data/biomanager.db`. No
`SECRET_KEY` is needed: without one, a random key is made on first run and
kept in `data/secret_key` (readable by you only).

## Running the tests

```bash
scripts/test.sh
# which is:
.venv/bin/python -m unittest discover -s tests -t .
```

The suite in `tests/` uses only the standard library's `unittest` (it also
runs under pytest, if you have it). It starts the app once on a fresh
SQLite database in a temp folder — `data/` is never opened — and drives the
real routes with Flask's test client, as an admin and as members, so
permissions and validation are checked along with behaviour. One module per
area (`test_mice.py`, `test_plasmids.py`, `test_stocks.py`, …); shared
set-up and factories are in `tests/base.py`. Run one module, class or test
with `scripts/test.sh tests.test_mice` (or `tests.test_mice.SomeClass`).

The same suite runs on PostgreSQL, against an empty database it is allowed
to wipe (its schema is dropped first):

```bash
BIOMANAGER_TEST_DATABASE_URL=postgresql://localhost/biomanager_test scripts/test.sh
```

Tests of SQLite-only machinery (`app/integrity.py`) skip there, and the
SQLite → PostgreSQL migration tests only run there. CI runs both.

Every test makes its own uniquely named records and must not depend on
another test having run. A test marked `@unittest.expectedFailure`
documents a known bug; when the bug is fixed it shows up as an "unexpected
success" — remove the marker then. GitHub Actions runs the suite on every
push (`.github/workflows/tests.yml`).

### A big lab: the load test

`scripts/load-test.py` fills a demo lab with 100,000 mice (5% alive, the
rest dead on days spread over five years), their cages, litters and weights,
zebrafish, fly vials, 40,000 inventory items and notebook pages, then times
each main page as the demo admin. Run it after changing what a sheet loads:

```bash
.venv/bin/python scripts/load-test.py /tmp/bm-load
```

What keeps the sheets usable at that size:

- `colony_context` loads only the tab being opened (`active_view`), with
  `selectinload` for each row's cage, rack and litter rather than one query
  a row, and counts in SQL.
- What ended over `RECENT_DAYS` (90) ago is left out unless `?ended=all`:
  mice by date of death, cages with no living mouse and no recent death,
  litters over a year old with no living pup (colony), used-up or
  cancelled items and received orders (`inventory_routes._recent_items`,
  by `attrs.used_up_on` and `received_on`), discarded vials
  (`stock_routes`). The sheet says how many and links to them. Search,
  exports and the box grid still see everything.

At 100k mice on SQLite, the mouse and cage sheets take 2–3 s and stream
30–60 MB of rows (about 3 KB a row, a third of it whitespace). The next
step, if a lab needs it, is paging on the server instead of the sheet's
client-side pages.

## Languages

The app is in English and Simplified Chinese (`app/i18n.py`). Text is
written in English and wrapped, and the Chinese is looked up by the English:

- **Templates**: `{{ _('Litter born') }}`; a value goes in by name,
  `{{ _('Sign in with %(provider)s', provider=p.label) }}`; a sentence with
  markup in it is `{% trans name=value %}…{% endtrans %}`. A literal `%`
  inside `_()` is written `%%`. Don't wrap people's data (names, notes).
- **A label that arrives as a value** (from Python, or a built-in name a lab
  may have renamed): `{{ item.label|tr }}`, never `_(item.label)`, which
  would put a lab's text in unescaped and fail on a `%`. Its known values
  get catalog entries; anything a lab typed stays as typed.
- **Python** (flash messages, labels made in code): `gettext("…")` and
  `ngettext(singular, plural, n)`, imported from `app.i18n`. Not `_`: many
  modules use `_` as a throwaway name.
- **The same English, two meanings**: `pgettext("experiment", "active")` and
  `{{ status|tr("experiment") }}` look for a `"experiment::active"` entry
  first, so an experiment can be 进行中 while an account is 正常.
- **Dates**: `{{ day|date_format('%a %d %b') }}` and `i18n.strftime(day, "%b %d, %Y")`
  instead of `.strftime()` with month or weekday names: the same English
  patterns come out as a Chinese reader writes them (10月3日, 2026年10月3日 周六).
  `fmt_day` and `relative_day` already follow the language.
- **Notifications and emails** are written in their recipient's language,
  not the sender's: `notify.send(s, who, "%(who)s shared “%(title)s” with you",
  values={…})` translates the English title for each recipient
  (`i18n.language_for`: their choice in Settings, else the language their
  browser last showed). The message is translated only when
  `message_values` is given; otherwise it is kept as typed. Anything else
  written for someone else: `with i18n.using(lang): …`.
- **Page scripts**: `t("Saved")`, `t("%(n)s mice", {n: 3})` (`base.html`
  defines it before any page script).
- **The Chinese**: `app/translations/zh/<area>.json`, `{"English": "中文"}`,
  one file per area so translations of different pages don't collide; the
  scripts' words in `js-<area>.json` (sent to the browser). The words come
  from `docs/i18n-glossary.md`. A text with no entry shows in English.
- **Which language**: the person's choice in **Settings → Language**
  (kept per person, `language.<username>` in the settings table), else the
  language the browser or computer asks for first (`Accept-Language`); the
  sign-in page has a 中文 / English switch (`POST /language`).
  `session["lang"]` holds the choice for this browser.
- **Tests** (`tests/test_i18n.py`): every `_()` in a template and every
  `gettext("…")` in Python has its Chinese, a translation keeps the
  `%(name)s` values of its English, and the same English is never given two
  different Chinese. So adding text to a translated page means adding its
  Chinese too.
- **Kept in English**: what is stored (starter pages, default titles a
  record is saved with, audit text), the JSON API (`/api/v1` answers in
  English whoever asks; page scripts translate its labels for display), CSV
  and Excel headers, printed labels, logs and the usage report. A word a
  page script needs goes in a `js-*.json` file even when a server catalog
  already has it (only those reach the browser), with the same Chinese.

## The website

`site/` is the website, plain HTML and CSS with no build. Two hosts publish it
from master:

- **biomanager.org**: Cloudflare Pages, connected to this repository (output
  folder `site`, no build command). Baidu's crawler can read it; GitHub
  Pages answers Baidu with 403, so the domain lives here.
- **gaspolymerase.github.io/biomanager**: GitHub Pages, by
  `.github/workflows/pages.yml`. Every page's canonical address names
  biomanager.org, so search engines count the two as one site.

**Pages.** The front page (`index.html`: the opening, the feature stage, the
promises and where to go next), Features (`features.html`: each database,
cage cards, what works everywhere, the ways to run it), Run it for your lab
(`server.html`), the user guide (`guide.html` and `guide/`) and Download
(`download.html`: the files, phones, first steps and questions). Every page
has the same header, whose links go to those pages, the current one marked;
an old link to a section of the long front page (`/#download`, `/#tour`…)
goes on to the page it moved to.

**The user guide** is `guide.html` (its home, which the app opens through
`/guide`: a first-day path and a card for each area) and one page per topic
in `site/guide/` (and `site/zh/guide/`), in five groups: Get started,
Everyday skills, How to… (one task per page, numbered steps), Your databases,
Working as a lab, Data and help. Each page's own words sit between
`<!-- page -->` and `<!-- /page -->`: a title, a one-line `.summary`,
optional `.badges`, a clip (`video[data-clip-src]`, loaded when it scrolls
into view), the text, and its own Troubleshooting. Everything around them
(head, header, the grouped sidebar with search, "On this page", Previous /
Next, "Was this helpful?", whose No opens a GitHub issue naming the page) is
written by `scripts/guide-pages.py` from its `PAGES` list: to add a page, add
it there, make its file with the two markers, and run the script.
`--search` also rebuilds the search index (Pagefind, into `site/pagefind/`,
published with the site; it indexes only `data-pagefind-body`, the guide's
pages, and keeps English and Chinese apart). Run it after editing the guide.
An old link to a section of the one-page guide (`guide.html#mice`) goes on to
its page.

**For search engines and AI.** When the site publishes, `.github/workflows/pages.yml`
runs `scripts/llms-full.py`, which writes `site/llms-full.txt`: every English
guide page's own words as Markdown in `PAGES` order, then `deploy-with-ai.md`,
for AI assistants (`site/llms.txt` links it). It is committed too, since
Cloudflare Pages publishes `site/` as it is: run the script after editing the
guide (`tests/test_llms_full.py` fails while it is behind). After the deploy, `scripts/indexnow.py`
sends the pages that push changed to IndexNow (Bing, and through it ChatGPT
search and Copilot; Yandex, Seznam, Naver), proven ours by the key file
`site/<KEY>.txt`; a failure there leaves the site published. A new page also
goes in `site/sitemap.xml`.

**Addresses.** biomanager.org is served by Cloudflare Pages (DNS on Cloudflare),
which redirects `page.html` to `page` and, without `site/404.html`, would answer
every unknown address with the front page. So canonical links, language
alternates, `og:url`, the sitemap, IndexNow and `llms-full.txt` all use the
address without `.html` (`guide-pages.py`'s `url()`); GitHub Pages serves both
forms, so the site works on either. Links between pages may keep `.html`.

**Two languages.** `site/zh/` holds the Chinese pages, one for each English
page (`zh/index.html`, `zh/features.html`, `zh/download.html`,
`zh/guide.html`, `zh/server.html`), with the same
structure and the same ids, so `guide/mouse-colony.html` and `zh/guide/mouse-colony.html`
are the same section. A change to an English page goes into its Chinese page
in the same piece of work; the words come from `docs/i18n-glossary.md`.

- Each page names its other-language twin (`<link rel="alternate"
  hreflang>`), and `sitemap.xml` lists every page in both languages.
- The **中文 / English** link beside Download switches; `site.js` remembers
  the choice (`localStorage` `bm-lang`).
- An English page sends someone whose browser asks for Chinese first, and who
  has not picked a language, to its Chinese twin (an inline script in the
  head). The Chinese pages never redirect, so a crawler always reads the page
  it asked for.
- The Chinese pages carry `Content-Language` and `keywords` for Baidu, which
  still reads them.

**How it looks.** One stylesheet, `site/style.css`, for every page, light and
dark from the same tokens on `:root` (the app's teal, neutrals with a little
teal in them).

- **Type**: headings in Geist and small labels (eyebrows, the dock's names) in
  Geist Mono, self-hosted as Latin subsets in `site/assets/fonts/` (SIL OFL,
  `OFL.txt` beside them; about 25 KB and 20 KB), because mainland China can't
  reach a font CDN. Running text is the system font with each system's
  Chinese face; no Chinese webfont is sent. Big Chinese headings keep normal
  letter spacing (`:lang(zh)`).
- **The header** floats above the page and turns to glass once the page
  scrolls (`site.js` adds `.scrolled`).
- **The feature stage** (`#see` on the front page; dark on a dark page,
  white with pale glass on a light one) plays a short clip per tab of its
  dock: `site/assets/clips/<name>.webm` and `.mp4`, with a `.webp`
  poster. `scripts/site-clips.py` makes them from a fresh demo lab (through
  `scripts/feature-clips.py --plain`). Only the chosen clip loads; clips follow
  one another until someone picks a tab, and pause off screen. A tab's caption
  is in its `data-claim` and `data-more`.
- **Clips play by themselves** on the front page and Features, with no
  play button or controls (`bmClips` in `site.js`): Apple's browsers get
  the `.mp4` (they can stall on WebM), the others the smaller `.webm`; a
  clip the browser won't start on its own (Safari in Low Power Mode)
  starts at the visitor's first tap, click or key anywhere on the page,
  and the browser's own play button is hidden over them. With reduced
  motion the stage and the AI card stay still (the stage plays the tab
  someone picks); a Features window plays what was clicked.
- **Features** has no screenshots: each database and each thing that works
  everywhere is a glass and neon card (`.nc`) drawn in the page, with its own
  colours (`--c1`/`--c2` for the neon, `--a`/`--b` for the card on a dark
  page), a line drawing in inline SVG and a pane of glass with a little of
  the app's own words. On a dark page the card is dark and its drawing
  glows; on a light page it is white with a soft wash of its colour, a
  deeper drawing and white frosted glass. A card with `data-clip` opens that
  clip (from `site/assets/clips/`, made by `scripts/site-clips.py`) in a
  window with a link on to its part of the user guide; without scripts, or
  with ⌘-click, it is just that link. By day every band is white, parted by
  hairlines; on a dark page the header's glass turns dark over the stage and
  the cards (`.on-dark`, from `site.js`).
- **The AI assistants card** (`.nc-full.ai-card`, first under Features, the
  whole row): three small panes of glass (`.mg`), joined by thin arrows
  with Approve as a gate on the last: what you said (a prompt with its
  send button), one proposal, and your records (three glass rows). The
  glass is after Apple's Liquid Glass: clear and barely blurred, no grain,
  its light in the rim (a gradient border by mask, bright top left and
  bottom right) and a sheen across the top, over a soft glow of the
  card's colours. All HTML and CSS: beside the words on a wide screen,
  under them on a tablet, down the card on a phone. The send button
  presses, a dot walks the path, Approve lights as it passes and the first
  record row takes the change (CSS keyframes; still and lit with reduced
  motion). Under it its clip plays by itself while on screen, with no window to open: a
  `video[data-clip-src]`, which `site.js` loads and plays as the guide's
  clips. `data-clip-still` makes it a moving picture rather than a player:
  it loops with no controls, and a click on it stays on the page (the
  rest of the card is the link) and starts it if the browser held it back;
  asked for less motion, or not allowed to play, it stays on its poster.
  The clip, `assistant.*`, is made by `scripts/assistant-clip.py`: the
  assistant's window is drawn in a page, BioManager is a fresh demo lab
  where a token proposes and a real click approves, and each frame is a
  screenshot at twice the size with the zoom done by the browser, so words
  stay sharp.
- **The opening** (`.hero-glass`) is a band the height of the screen, light
  or dark with the page. Behind the headline is a double helix of frosted
  glass, worked out as a real helix in 3D: each backbone is wider and brighter
  where it comes towards you and thinner where it turns away, and where the
  two cross the near one passes over the far one. It is three layers of glass,
  back to front (the backbones behind the axis, the paler base pairs, the
  backbones in front), each a single `backdrop-filter` layer masked to its
  shapes, with a coloured core line under it that the glass frosts into a
  glow and bright edges over it; soft teal and blue light drifts underneath.
  One turn of every layer is drawn by `scripts/hero-helix.py` into
  `site/assets/helix/`, and the helix slides by one turn on a loop, which
  looks just like it turning. The product shot rises out of its lower edge.
  The header's **User guide** link is a bordered pill: more than the other
  links, less than Download.
- **Motion** is the hero's entrance and turning helix, the product shot flattening
  as the page scrolls, sections (`.rise`) rising in (driven by scrolling
  where the browser can, else by `site.js`), and each glass card's neon
  drawing itself in. Without that support, or with
  reduced motion, everything simply shows; reduced motion also stops the
  clips playing by themselves.
- **Icons** are `site/assets/icons.svg`, symbols copied from the app's
  `app/static/icons.svg` (plus phone, laptop, server and play, and Octicons'
  GitHub mark and star), coloured by `.ic-<colour>`.
- **Fresh copies.** Pages link `style.css`, `site.js` and `assets/icons.svg`
  as `…?v=<hash of the file>`, and `style.css` links the helix pictures the
  same way, so a browser never pairs a new page with yesterday's stylesheet.
  `scripts/site-stamp.py` writes the stamps (`scripts/guide-pages.py` and
  `scripts/hero-helix.py` run it); after editing any of those files by hand,
  run it, or `tests/test_site.py` fails.

## Desktop App

**Releasing.** Push a tag: `v1.0.0` builds every app, the server image and
bundle, checks each starts, and publishes the release (the website's
download buttons use `/releases/latest`). A tag with a hyphen, `v1.0.0-rc.1`,
is published as a *pre-release*: `/releases/latest` leaves it out (so the
website, the desktop app's update check and the server set-up stay on the
last release) and the image gets no `:latest` tag. Try the candidate, then
tag the release. A version's notes for the labs are
`docs/release-notes/<version>.md` (a candidate uses its release's), put
above the list of files on the release page. Its few-line summary goes in
`app/whats_new.py` `NOTES` (new, works differently, fixed):
`tests/test_whats_new.py` fails until the newest release notes have one.
Each person sees it once after the update (`users.whats_new_seen`, revision
0016; a new account is stamped when its welcome tour ends), and Help →
What's new opens it again. A server knows its version from the `VERSION`
file the release workflow writes into the image (`BIOMANAGER_VERSION`
build arg); a build from source says `server` and shows none.
The website names the version too (`<b data-version>` on the Download page
and the opening, English and Chinese; `site.js` brings it up to date from
GitHub where it can): change it with the notes, or `tests/test_whats_new.py`
fails.

**Signed builds.** The release workflow signs and notarises the Mac apps
(`scripts/sign-macos.sh`, entitlements in `desktop/entitlements.plist`) and
signs the Windows exe when these repository secrets exist; without them it
builds unsigned, as before:

| Secret | What |
| --- | --- |
| `MACOS_CERT_P12`, `MACOS_CERT_PASSWORD` | A "Developer ID Application" certificate with its key, exported from Keychain Access as .p12 and base64-encoded (`base64 -i cert.p12 \| pbcopy`), and the export password. Needs an Apple Developer Program membership. |
| `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` | The developer account's Apple ID, its team ID, and an app-specific password (appleid.apple.com → Sign-In and Security), for notarisation. |
| `WINDOWS_CERT_PFX`, `WINDOWS_CERT_PASSWORD` | A code-signing certificate (.pfx, base64) and its password. |

**Updating itself** (`desktop_updates.install_update`): the file for this
computer is downloaded, checked against the SHA-256 GitHub publishes in the
release's asset `digest`, staged beside the installed copy, and a small
script waits for the app's process to end, moves the old copy aside and the
new one into place, and opens it. A file the app downloads itself carries
no quarantine flag, so an update opens without the first-launch warning
even while the builds are unsigned.

`desktop.py` starts Flask on a free local port and opens it in a
pywebview window. `desktop_menu.py` builds the menus: on a Mac, the whole
menu bar through AppKit (installed on the main thread once the window is
shown, replacing pywebview's two defaults); elsewhere pywebview's own
menus. The Go menu is the sidebar: `static/shell.js` sends the page's
sidebar links to `DesktopApi.set_nav` over pywebview's JavaScript bridge,
which keeps only same-origin paths. `desktop_updates.py` is the version
(the `VERSION` file `Biomanager.spec` bundles from `BIOMANAGER_VERSION`;
from source, the latest tag + "+dev"), the update check against
`api.github.com/repos/gaspolymerase/biomanager/releases/latest` (which
leaves pre-releases out; `is_newer` puts `1.0.0-rc.1` before `1.0.0`), and
this computer's `desktop-prefs.json` (automatic check, skipped version,
appearance, zoom) in the data folder. Set `BIOMANAGER_MENU_DUMP=<file>` to
have a running app write its menu bar there, for checking a build.

**Windows 10** (`desktop_windows.py`). The Windows window is WebView2
through .NET Framework. Without WebView2 86+ or .NET 4.6.2+, pywebview
quietly uses Internet Explorer's engine, which can't run the app, so
`desktop.main()` first asks `missing()` (the same registry keys pywebview
reads) and without them calls `run_in_browser()`: a message offering
Microsoft's WebView2 bootstrapper, the app in the default browser, and a
second message that keeps the process (and Flask) alive until OK. A
`webview.start()` that raises on Windows ends there too, with the error in
the message. Before any of it, a frozen build's `unblock()` deletes the
`Zone.Identifier` stream from its own DLLs: Explorer marks every file
unpacked from a downloaded zip, and .NET won't load a marked assembly
(HRESULT 0x80131515). The release workflow's Windows job fails if
`missing()` finds anything lacking on the runner, which has both.

**Windows in Chinese: always name the encoding.** On Windows, Python opens
a text file without `encoding=` in the system code page: GBK on a Chinese
PC (Shift-JIS on a Japanese one, and so on), not UTF-8. From 0.10.2 to
1.0.5 `app/icons.py` read `icons.svg` (which has an em dash) that way at
start-up, so on Chinese Windows 10 and 11 the desktop app raised
`UnicodeDecodeError` and closed before any window opened. Users said it
"failed to install" (安装出错). The release check didn't see it: its runner is
English Windows, where cp1252 reads the same bytes without complaint. So:

- Every `open()`, `read_text()`, `write_text()` and `os.fdopen()` of text
  says `encoding="utf-8"`. `tests/test_desktop_windows.py` (`ChineseWindows`)
  fails on one in `app/` or the top-level `.py` files that doesn't.
- The built app runs in Python's UTF-8 mode (`("X utf8", None, "OPTION")` in
  `Biomanager.spec`; `X utf8_mode=1` is silently ignored). A test checks the
  spec still has it. It is the safety net, not the fix: code run from
  source, the tests and the server don't have it.
- The one exception is the update helper's `.cmd` (`desktop_updates.install_update`),
  written with `encoding="locale"`: cmd.exe reads a batch file in the system
  code page, and its paths may be Chinese.
- To try the app the way a Chinese PC runs it, on Linux:
  `sudo localedef -i zh_CN -f GBK zh_CN.GBK`, then run it, or the tests, with
  `LC_ALL=zh_CN.GBK`. `python -X warn_default_encoding` lists every file
  opened without an encoding.

Build a clickable native app (no terminal needed to launch):

```bash
./scripts/build-desktop.sh
open dist/BioManager.app
```

The script installs `pywebview` + `pyinstaller`, ensures the frontend bundle is built, and produces `dist/BioManager.app` (macOS) or `dist/BioManager/` (Windows/Linux). The app's SQLite database and uploads live in `~/Library/Application Support/Biomanager/` so rebuilds don't wipe your data. To skip the bundling step and just run a desktop window from source: `python desktop.py`.

## The phone apps

`android/` (Kotlin, Gradle) and `ios/` (SwiftUI, XcodeGen) are the same
small thing: a setup screen that asks for the lab server's address and
checks `/healthz` answers `ok`, then a web view of that server, plus a
native QR scanner. Links to other hosts open in the system browser.

- **iOS**: `ios/project.yml` generates the Xcode project (`cd ios &&
  xcodegen`; the `.xcodeproj` is not committed). `Scanner.swift` is
  VisionKit's `DataScannerViewController`. A page on the server can borrow
  it: `window.webkit.messageHandlers.bmScan.postMessage('scan')` opens the
  scanner and the code comes back as `window.bmScanned(text)`
  (`LabWebView.swift`, which only answers pages from the server's own
  host). Bench mode (`experiment-bench.js`) uses it for **Scan a card**,
  and in a browser falls back to `BarcodeDetector` on the camera when the
  browser has it. `.github/workflows/ios.yml` builds it for the simulator
  on a macOS runner and screenshots it against a demo server; shipping it
  needs an Apple developer account (`DEVELOPMENT_TEAM`).
- **Android**: `.github/workflows/android.yml` builds the APK, checks it in
  an emulator, and the release workflow publishes it.

## Shared Server Setup

**The supported way is the Docker stack in [`deploy/`](../deploy/README.md)**:
Caddy for HTTPS, the app under gunicorn, PostgreSQL 16, and a backup service
that dumps, checks, prunes, copies off-site with restic and test-restores
every week. `deploy/README.md` is the runbook: first start, moving a lab's
SQLite database over (`scripts/migrate-to-postgres.py`), backups, restoring
and updating.

The rest of this section is for running it without Docker.

Running BioManager for a whole lab means other people can send it requests,
so it runs differently from a laptop. What decides which requests to trust is
in `app/security.py`; its docstring explains each check.

```bash
pip install -r requirements.txt            # includes gunicorn
export DATABASE_URL='postgresql://USERNAME:PASSWORD@localhost:5432/biomanager'
export BIOMANAGER_PROXY_HOPS=1             # behind Caddy/nginx (below)
gunicorn -c gunicorn.conf.py wsgi:app      # never python run.py
```

`wsgi.py` sets `BIOMANAGER_ENV=production`, refuses to start with debug on,
and turns on Secure cookies — so the server **must be reached over HTTPS**.
gunicorn listens on `127.0.0.1:8000` only; put a reverse proxy in front for
TLS. With Caddy that is two lines:

```
lab-biomanager.example.edu {
    reverse_proxy 127.0.0.1:8000
}
```

With nginx, pass the headers the app reads:

```
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host $host;
client_max_body_size 64m;
```

Settings (environment variables, all optional):

| Variable | Default | Does |
| --- | --- | --- |
| `SECRET_KEY` | kept in the data folder | signs session cookies; 32+ characters in production |
| `BIOMANAGER_HTTPS` | on in production | Secure cookies; `0` only for a trusted plain-HTTP network |
| `BIOMANAGER_PROXY_HOPS` | `0` | proxies in front whose `X-Forwarded-*` headers to trust |
| `BIOMANAGER_TRUSTED_ORIGINS` | — | other origins allowed to post, comma separated |
| `BIOMANAGER_SESSION_DAYS` | `7` | idle days before a sign-in expires |
| `BIOMANAGER_MAX_UPLOAD_MB` | `64` | largest upload accepted |
| `BIOMANAGER_UPLOADS_DIR` | `app/static/uploads` | where uploads are kept; put it next to the database |
| `BIOMANAGER_TELEMETRY` | on | `0`: never send the anonymous daily counts (so does `DO_NOT_TRACK=1`) |
| `WEB_CONCURRENCY` | 1 on SQLite, 3 on Postgres | gunicorn worker processes |

`gunicorn.conf.py` loads the app once before forking (`preload_app`), so the
start-up schema updates and seeding run once, not in every worker at the
same moment. On first start the log prints the setup code for the first
admin account.

Keep the server off the open internet: on the campus network or VPN, or a
private network such as Tailscale.

## Multi-User Notes

- **The first account is the admin**, and on a server needs the setup code
  from the log, so nobody else on the network can claim it first.
- **Everyone after that waits for approval.** A sign-up is created as
  *awaiting approval*; admins get a notification and approve it in
  Settings → Manage users. `scripts/reset-password.py NAME --enable` does the
  same from the command line on SQLite.
- **Passwords are at least 12 characters.** Ten failed sign-ins in 15 minutes
  lock out that username and that address for the rest of the window.
- **Changing or resetting a password signs out every other session** of
  that account — the fix for a lost laptop. So does disabling it
  (`security.end_sessions`), and enabling it again doesn't bring them back.
- **Sign out ends that session for good.** Each session has an id
  (`session["sid"]`); Sign out records it in `app_settings`
  (`signed_out:<id>`, forgotten after the session lifetime), so a copy of the
  cookie no longer works.
- **Usernames are plain** (letters, digits, `.`, `-`, `_`, compared without
  case), so no two look alike. Sign-ups are limited to 5 an hour per address,
  wrong current passwords in Settings to 10 per 15 minutes, CSP reports to
  30 a minute per address.
- **API replies never set the session cookie** (`security._SessionInterface`).
- **The data folder is the running account's only** (mode 700).
- **Changes from other websites are refused.** Every POST is checked against
  the browser's `Sec-Fetch-Site`/`Origin` headers, so a malicious page cannot
  make a signed-in member's browser edit records. Sign out is a POST too.
- **Uploads need a login**, get unguessable names, and are served so that an
  uploaded HTML or SVG file downloads rather than running in the app.
- **Google Calendar tokens are encrypted in the database** with a key
  derived from the signing key. Tokens saved before are encrypted at the
  next start. Losing or changing the key only means reconnecting Google
  Calendar, which is why backups include the data folder's `secret_key`.

