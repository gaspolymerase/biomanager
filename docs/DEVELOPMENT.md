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

### Where the pages are

`app/flask_app.py` holds the Flask application object and nothing else.
`app/app.py` builds the application around it (configuration, error
handlers, what every page is given to render with, the navigation) and
imports the modules that hold the pages: the colony's in
`app/colony_routes.py`, the zebrafish's in `app/zebrafish_routes.py`, most
others as blueprints. Some are still in `app.py` itself;
`docs/plans/app-py-split.md` moves them out. What a page needs whatever it
is a page of (who is signed in, how a save answers, how an edit or a
delete is written down, a person's badge) is in `app/web.py`, so a module
of pages imports that and `flask_app.py`, never `app.py`.

A route keeps its path and its endpoint name when its code moves:
`url_for` is called on them some 2,100 times, and the tests ask for URLs,
not endpoints, so a renamed one would break pages no test opens.
`tests/url_map.txt` holds every route the app answers and
`tests/test_url_map.py` fails on any difference; when a change really adds
or removes a route, `python scripts/url-map.py --write` updates it. A test
that patches a function patches it in the module the route calls it from:
patched anywhere else, the stand-in is never called and the test can pass
without testing anything.

## Interface design

The interface follows Apple's Liquid Glass design, on the frosted side as
macOS 27 is. Two layers:

- **Content** (sheets, cards, forms, the notebook page, the calendar grid)
  is opaque and calm.
- **Glass** is only for what you steer with: the sidebar, the top row of
  tabs, search and account, the page's buttons in its title row, menus,
  toasts. `glass` is a utility (`@utility glass` in `frontend/src/tailwind.css`):
  a clear fill (`--glass-fill`) over a light blur, lit like glass: a rim
  brightest at the top-left and bottom-right (`--glass-light`, drawn by its
  `::after`), an inner glow, and a sheen that follows the mouse
  (`--glass-x`/`--glass-y`, set by `static/shell.js`). `--glass-strong` is
  the frostier tint for raised and large surfaces. Glass doesn't bend what is
  behind it: Chromium can (`backdrop-filter: url(#svg)`), but WebKit, which
  the Mac app and Safari use, drops the whole backdrop filter then, so it
  isn't used.

The page scrolls *beneath* the two rows at the top (`.topstrip`, then
`.toolbar`), which float over it: `.shell-scroll` is padded at the top by
`--tabbar-h + --toolbar-h`, so a `sticky top-0` header stops just under
them. Once the page has scrolled, `.chrome-edge` softens what passes under
both rows (its colours still show through the glass), and the title, text
on no glass, gets a frosted patch of its own.

Motion is what makes it feel liquid. The selection in the tab strip and in
each segmented control (`.seg`) is one drop (`.wtab-drop`, `.seg-drop`)
that slides to the item picked, and on into the next page, which starts it
where the last page left it (`initDrops`, sessionStorage). Glass buttons
swell a little when pressed, and menus grow out of their button. Reduce
Motion turns all of it off.

Corners are concentric (cards 12px, controls inside them 8px, menus 14px
with 9px items), and anything floating in a bar is a capsule. Small controls
in the page keep rounded-rectangle corners, as on a Mac.

Reduce Transparency makes glass solid, and Increase Contrast makes it
solid with a firmer outline (`prefers-reduced-transparency`,
`prefers-contrast`). A window in the background shows its selection in
grey (`html.window-inactive`, from `static/shell.js`).

### The pieces, and the rules that keep them one

Everything is built from a few shared pieces in `frontend/src/tailwind.css`;
a page's own stylesheet keeps only what is truly its own. `tests/test_design.py`
reads the source for the rules marked *(tested)*.

- **Tokens.** Colours, the field look (`--field-bg`, `--field-border`,
  `--field-h` 30px, `-sm` 26, `-lg` 36), `--focus-ring`, `--scrim`, the
  dropdown `--chevron` and the empty-sheet `--empty-icon` are variables on
  `:root`, redefined for dark mode. Raw Tailwind tints used for pills
  (violet, pink, sky, teal) are remapped there too, so a `bg-violet-50
  text-violet-700` pill works in both themes.
- **Text colour** *(tested)*: words are 4.5:1 or more on what they sit on,
  in light and dark (`tests/test_design.py` `TextColours` reads the tokens):
  `muted` on the canvas, the recess and grey controls, `brand-700` as link
  text, `info-700` for blue words on their tint, `on-brand` on a solid
  accent (write `var(--color-on-brand, #fff)`: no utility uses it, so
  Tailwind leaves the variable out). The faint grey (`sand-400`) is for
  icons, separators and disabled fields, never for words; checked page by
  page with axe-core (`color-contrast`).
- **Type scale** *(tested)*: 10 (dense grids), 11, 12, 13 (body), 15, 17
  (dialog titles), 22, 28; 16 is a phone's fields, 32 and 40 display
  numbers. On a touch screen every field is 16px, `!important`, so iOS
  never zooms in.
- **Fields.** Every `<select>`, and every unclassed input and textarea, is a
  `field-box`: one height, fill, border, radius (8px) and focus ring; a
  select draws `--chevron` instead of the browser's arrow. Checkboxes and
  radios are drawn (16px, brand fill). A page stylesheet that sizes its own
  fields uses the same tokens.
- **Suggestions.** An `<input list>` and its `<datalist>` are written as
  usual; `static/combobox.js` takes each over (on the page or added later)
  and shows the choices in the app's menu, read from the datalist each time
  it opens. Typing filters; ↓/↑, Enter and Esc; in a sheet ↓ moves a row,
  so Alt+↓ opens it. A pick sends `input` and `change`, so a sheet saves it.
- **Segmented controls.** `.seg` with `.seg-item`s, or a `.dt-chips` group
  of `.dt-chip`s (filters, layout switches): the drop slides to the one that
  is `.is-active`, `aria-pressed`, `aria-current` or a checked radio inside a
  `label.seg-item`. A database's own tabs are `.seg.page-tabs` (a line, not
  a capsule) in a `.page-tabs-row`, with its counts (`.page-stats`) or its
  scope switch in `.page-tabs-side`. Help that used to sit on the page as a
  paragraph is behind an ⓘ (`details.info-tip[data-popover]`).
- **Dialogs** *(tested: a class and an accessible name)*: a `<dialog
  class="record-dialog">` (`-narrow` 460, default 640, `-wide` 880) with a
  `.record-dialog-head` (title, optional `.record-dialog-sub`, × as
  `.modal-close`), a `.record-dialog-body` that scrolls, and a
  `.record-dialog-foot`: Cancel, then the one `btn-primary`; Delete alone on
  the left. Below 640px it is a bottom sheet. A choice of formats or kinds
  inside one is a `.choice-list` or `.choice-cards`.
- **Asking** *(tested)*: never the browser's `alert`, `confirm` or `prompt`
  (the Mac app's web view answers them with no); `BioDialog.alert /
  confirm / prompt` (`static/dialogs.js`).
- **Menus and hover cards** float on `pop-surface` (frosted, 14px corners,
  `shadow-pop`); a row's hover is the brand fill with white text, in every
  menu. A `<details data-popover>` closes on a click outside, on Esc and
  when another opens (`initPopovers`).
- **Toasts**: one, `BiomanagerShell.toast(message, kind, {html})`, in the top
  layer, announced to screen readers, above a phone's tab bar.
- **Tooltips** from `data-tooltip` also show for a moment on a tap.
- **Purpose pills** take `data-tone` from the lab's purpose list
  (`cage_rows.purpose_tones`): Breeding brand, Breeder green, Experiment
  blue, the lab's own the next colours in order.
- **Scroll cues.** Tabs or a sheet wider than their box fade at the edge
  that hides more (`initScrollCues`, `data-clip`).
- **Empty** sheets and lists: a soft icon over one sentence (`.dt-empty`,
  `empty-state`), with at most one action.

**The design gallery**, `/design` (admins, by its address), draws
every piece above on one page as the app does (`templates/design_gallery.html`,
in English only, as a maintainer's tool): check a change there in light,
dark and at a phone's width before looking page by page.

### On a phone

Below 768px the tab strip and search field give way to a tab bar at the
bottom (`.phone-tabs` in `base.html`, `initPhone` in `static/shell.js`):
Home, Calendar and Notebook as the lab has them, **Databases** (a sheet of
the lab's databases, `#phone-dbs`) and **Scan**, with Search in a round
button beside it. It shrinks to its icons while the page scrolls down.
**Scan** asks the phone app for its scanner when there is one — the iPhone
app's `bmScan` handler (`"scan"`, answered through `window.bmScanned`, which
opens a card of this lab) or the Android app's `BioManagerApp.scan()`
(which opens the card itself) — else reads the code with the camera
(`BarcodeDetector`), else says to point the Camera app at the card. On
each page the bar tells the apps it has a Scan of its own
(`"page-has-scan"`, `BioManagerApp.pageHasScan()`), so they hide their
floating button for that page; a server from before this keeps it.
**☰** at the top left still brings in the whole sidebar.

### Type

Inter for the interface and JetBrains Mono for sequences and IDs, both
bundled in `app/static/fonts` (copied from `@fontsource-variable` by `npm run
build:fonts`, with their OFL licences), so the packaged app needs no
network. There is a file per script (Latin, Greek, Cyrillic…), and the
browser fetches only those a page uses. In cells, fields and IDs, Inter
tells l from I from 1 (`cv05`, `cv08`).

Chinese comes from each system's own font (`--font-cjk`: PingFang SC,
Microsoft YaHei UI, Noto Sans CJK SC); a Chinese font is 8–15 MB a weight.
When the page is in Chinese (`:lang(zh)`), the stack starts with "Inter
Text CJK", the same Inter without the quotes, ellipsis, em dash and middle
dot. Those then come from the Chinese font, full width. Chinese is set half
a point larger with more line height.

Settings → Appearance & language → **Font** lets a person pick the
computer's font instead (`appearance.get_font`, the `font:<user>` setting;
`base.html` then marks the page `data-font="system"`). **Text size** is the
window's zoom, which only the Mac app lets a page set
(`DesktopApi.text_size` / `set_text_size`, the same zoom as View → Zoom
In / Out); elsewhere the row names the browser's zoom keys. Zooming the page
in CSS was tried and left out: it makes the window-tall frame overflow.

The desktop app's page bridge: pywebview builds `window.pywebview.api` with
`new Function()` and sends answers back through `eval`, both refused by the
page's CSP (the desktop app doesn't log the report of that eval, from its
own window: `security._the_desktop_window_s_own`). `BiomanagerShell.desktopCall(name, …args)` (`static/shell.js`)
calls through pywebview's channel underneath, and an answer the page needs
comes back from Python by running the page's own function natively
(`desktop_menu._js`, e.g. `window.bmTextSize`). The Go menu's
`set_nav` goes this way too.

SF Symbols are not used: their licence covers Apple-platform apps only, not
the web.

### The Mac window

On a Mac the desktop window draws no title bar (`desktop_mac.py`, run as
pywebview's `before_show`). The page runs to the top edge, and the traffic
lights sit at the top of the sidebar, centred on the top row
(`ROW_CENTER`, which follows `--tabbar-h`). A script added before each
page draws marks it `<html class="mac-window">` (plus `is-fullscreen` in
full screen) so the page leaves room for the lights. The empty parts of the
two rows stand in for the title bar: `static/shell.js` posts `drag` or
`zoom` to the `bmWindow` message handler, and the window moves natively
(`performWindowDragWithEvent:`), or zooms or minimises as the person's
"Double-click a window's title bar" setting says. After the lights come
the window's tools (`.wtools` in `base.html`, shown under `html.mac-window`):
the sidebar, search, back and forward, as in Safari or Claude; search
replaces the top row's search box there, and the sidebar's own toggle is
hidden. Back and forward grey out by the Navigation API (`canGoBack`,
`canGoForward`), asked again after `pageshow` and on hover, since the web
view fills in what lies ahead a moment after a page loads. With the
sidebar collapsed the top row runs the window's width, the tabs after the
tools, and the sidebar starts below it. Windows and Linux keep their own
frames.

The sidebar is a pane of clear glass floating over the wallpaper, inset
`--rail-gap` from the window's edges. Collapsed (`body[data-rail="closed"]`,
the default), it shows icons only, each a circle and the current place a
raised pill, groups parted by a short hairline, and Settings and Help at
its foot, below `.rail-places`, which scrolls on its own and fades at its
ends. At its top is the lab's icon and name when an admin chose one
(`app/lab_icon.py`: the picture is kept in app_settings `lab_icon` as
`type;base64`, made a 160px PNG when Pillow is here, never an SVG; the
`lab_icon_version` key, read on every page, is its hash for the URL), else
the person's app icon. Hovering a place names it beside the
glass; hovering a database shows a look inside (`static/rail-peek.js`, from `/rail/peek` in `app/rail_peek.py`: the count from `home_layouts.count_tiles`, the due rows from `build_agenda` for its tracks, the person's own latest records).

macOS 26 draws its current look, and the larger lights (14 points, 23
apart, not 12 and 20), only for an app built with its SDK, judged by the
app's executable. That is PyInstaller's launcher, built with SDK 12, so
`scripts/build-desktop.sh` restamps it as SDK 26 with `vtool` and signs it
again. The window also has an empty unified toolbar: with SDK 26 the lights
can't be moved down to the top row without one. Run from source, the
window has the interpreter's look; to see the packaged one, run a copy of
the interpreter restamped the same way (`xcrun vtool -set-build-version
macos 11.0 26.0 -replace -output <copy> <python>`, then `codesign -s -`).

### Icons

One sprite, `app/static/icons.svg`, built by `scripts/build-icons.py`
from `lucide-static` (installed by `npm install` in `frontend/`):

```bash
python scripts/build-icons.py
```

Referenced as `{{ icon('mouse') }}` in templates, which emits
`<svg class="icon"><use href="/static/icons.svg#mouse"></svg>` — one cached
request, inherits `currentColor`, no JavaScript, works offline in the
packaged app. Within a page made from `base.html` it emits
`<use href="#i-mouse">` instead, and `icon_sprite_end()` puts a copy of
each symbol the page drew at its end (`icons.page_sprite`; the `i-` keeps
them apart from the page's own ids): Chromium loads and announces each
`<use>` of the file separately, a second of a long sheet's opening for its
rows' icons. Anything drawn outside a page (a template rendered for a
fragment or after the page) still points to the file; so do the icons
scripts write.

Every icon is a line drawing on a 24 grid, stroked in `currentColor`
(1.75, round ends); the fill is left to CSS (`svg.icon { fill: none }`), so
a toggle can fill one in (`.is-on`, a starred favourite). Two sources:

| Source | Licence | Covers |
| --- | --- | --- |
| [Lucide](https://lucide.dev) | ISC | the interface, plus `mouse` (Lucide's *rat*), `fish`, `worm`, `bird`, `rabbit`, `dna`, `virus`, `microscope`, `syringe`, `bone`, `brain` and the other objects |
| This project | — | `fly`, `plasmid`, `petri`, `cage`, `tank`, `culture-vial`, `antibody`, `cell`, `microtube`, `well-plate`, `blood`, `kidney`, `liver`, `stomach`, `intestine`, `lungs`, `gel`, `centrifuge` and the other lab and organ drawings, in Lucide's style |

Add an icon by adding a name to `FROM_LUCIDE` (our name → Lucide's) or
`CUSTOM` in the build script and rebuilding; the build stops on a name in
both or one Lucide lacks. Organs, procedures and more
organisms came in for the notebook's library folders, which offer the
biology ones (`FOLDER_ICONS` in `app/lab_notebook.py`). **Check any new icon at 15px**,
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
Utilities, and the databases) and `NAV_FOOTER` (Settings, which holds the
lab's pages too: see Settings below); **Help** holds the guide and Send
feedback. `static/shell.js`
(`setupRailMenus`) moves each `.rail-pop` menu to `<body>` when it opens:
the rail's `backdrop-filter` makes it the containing block of anything
fixed inside it. Toasts (`BiomanagerShell.toast`) are a manual popover,
in the top layer, so they show in front of an open `<dialog>`; a refused
form's message is copied into the dialog `static/form-memory.js` reopens.

### Settings

`templates/settings.html` is laid out like the Mac's System Settings: a
list of panes (`.settings-nav`) beside one pane at a time (`<section
data-pane>`). The address's `#hash` picks the pane; a hash naming something
inside a pane (`#api-tokens`, `#lab-copies`, `#language`) opens that pane and
scrolls to it, so links from other pages and redirects
(`url_for("settings", _anchor=…)`) keep working. Without JavaScript every
pane shows, stacked. On a phone the list and the pane take turns, with
**‹ Settings** to go back. Panes: **You**, the person's own (Profile,
with Sign-in under it, `#security`; Appearance & language, Notifications,
AI assistant & tokens, Your data) with icon tiles in the person's accent
colour; **Lab**, the lab's (Statistics, General, Databases, People &
access, Devices & copies, Version, History) in one slate
(`.set-ico.is-lab`). A setting is shown once, where it belongs: Batch
history is reached from History, the welcome tour from Help, the daily
email's cron line is under Reminder emails below, and the text size row
shows only in the Mac app, which can set it. In Notifications a category
in `notify.FOLLOWS` shares another's switch (a mouse picked from your
breeder cage follows Transfers), so Settings lists `notify.SHOWN`. `app/settings_lab.py`
gathers what the Lab panes show (`panes()`: with `configure`, every
sidebar database's own set-up page from `configure_links()`, listed in
Databases as Set up each database, and `summary`, the line under General,
Databases and People in the list of panes, as `pane_link(sub=)`); every
pane's head says whose its settings are (`.set-scope`: the person's, or
the lab's and who changes them); everyone sees them and only
admins get their forms (`/settings/lab` saves General, Databases, What
members may do and one other database at a time: `lab.apply_survey(...,
sections=)`). The pages they replace redirect to them: `/setup` once the lab
is set up (#general), `/admin/users` (#people), `/admin/colony` (#stats),
`/admin/racks/` (#racks), `/groups` (#groups); Batch history and the Audit
log stay as full pages, linked from History. People & access has tabs
(`[data-tabpane]`), and Project groups a page per group (`[data-subview]`,
`#group-<id>`), all picked by the hash.
Rows are `.set-row` in a `.set-group`, an on/off is
`input.set-switch`. Forms marked `data-autosave-form` post on change with
`X-Autosave: 1`, and the `settings` route answers `{"ok": true}` instead of a
redirect; the profile action only touches the fields the form sent, since
Profile and "When you sign in" are separate forms. The API tokens and lab
copy blocks are `api/_card.html` and `lab_copy/_cards.html`, included in
their panes.

### Home

Home has three views, tabs under the greeting (`templates/home.html`, a
`.page-tabs-row` form posting to `/home/layout`): **Overview**, the cards
below; **Timeline** (`home/_tracks.html`, `tracks_view`), every dated job
on one row of days, a row per kind of work, with 1, 2 or 4 weeks beside
the tabs; and **By place** (`home/_freezer.html`, `freezer_view`), the
racks with work in them and the jobs numbered in walking order. They are
saved under their first names, `classic`, `tracks` and `freezer`
(`home_layouts.LAYOUTS`, the `home_layout:<user>` setting), so a choice
made before the rename still holds. Customize shows on Overview only; on
the other two For you is one line (`.home-for-you`). Both read one agenda,
`build_agenda`; their styles are `static/home-layouts.css`, in the app's
type and tokens.

Overview opens with a bento of glass panels (`.home-bento`, rows of
`.home-panel` that wrap, `.is-2` twice as wide) above its cards: This week
(`week_view`: Monday to Sunday from an agenda built 7 days ahead on
Overview, a dot per kind of work a day (`_tone` of its track; overdue work
on today) and today's list, today's first), the colony's count, Needs a
look (`needs_look`, from `welfare.home_view`), For you, Your databases (a
tile per sidebar database with its `count_tiles` number) and the
notebook's latest pages. The databases and notebook panels always show,
so Customize does not offer those cards (`BENTO_FIXED`); For you and
Needs a look follow their cards' switches, and the grid below skips what
the bento shows. The title is the day as a mono kicker over the greeting
(`body:has(.home-bento)`).

Home is built from the lab's databases. `home_layouts.lab_databases` lists
the ones in this person's sidebar, in the lab's order (as the rail does,
leaving out the inventories that are tabs of Plasmids). `lab_cards` turns
them into the cards there are: `CARDS` (key, label, icon, wide at first,
the feature or flag it needs) plus a card of its own for each fly or worm
database (`stock:<key>`) and each animal database with a schedule
(`org:<key>`), ordered `TOP_CARDS`, then each database's cards in sidebar
order (`BUILTIN_CARDS`; `INVENTORY_CARDS` places Recent orders and
Expiring & low stock at the first such inventory), then the rest.
`count_tiles` makes the Counts card, a tile per database plus the
notebook; `shown_tiles` keeps the first `TILES_AT_FIRST` until the person
ticks their own. A person's choice is the `home_cards:<user>` setting —
`order`, `hidden`, `wide`, `tiles`, and `seen` (the keys that existed when
they saved, so a card added in a later release joins at its place, shown
unless it is one of `OFF_AT_FIRST`). The shared cards there were before
(`stocks`, `organisms`; `SPLIT_CARDS`) are read as every per-database
card that replaced them, at their place. `get_cards`, `set_cards` and
`reset_cards` read and write it; `offered_cards` is what this lab can
show; `POST /home/cards` saves the Customize dialog
(`templates/home/_customize.html`; the tiles only when it sends
`tiles=1`). `home.html` captures each card with `{% set %}` into a dict
and draws them in the person's order; `_home_extra_cards` loads the cards
off at first (to-dos, bookings, recent pages, calculators; Your databases
too, which repeats the sidebar and the Counts tiles) and Recent
plasmids. The calculators card puts the ones Utilities opened
last first (`biomanager:util:recent` in `localStorage`, up to eight).

**Animals that may need a look** (`app/welfare.py`, no schema change) is the
card `welfare`, first of the colony's (or zebrafish's) cards, offered when
the lab keeps either (`has_animals`). `welfare.checks()` is four
deterministic checks, each `{id, title, severity, count, items, fix}` as
`lab_checks` lists its own: `weight_loss` (a living mouse's latest weight at
least `loss_pct()` below its peak in the last `WINDOW_DAYS` = 30 days, two
weights at least, or below its first weight on or after the start of an
active or paused experiment; the app setting `welfare_weight_loss_pct`,
**Settings → General → Weight loss to flag**, 15 unless set, saved by
`lab._apply_rest`); `welfare_notes` (mice, tanks and fish whose `audit_log`
has a `create`, or an update touching `note`/`notes`, since `NOTES_DAYS` =
14 days ago, and mouse weights of those days with a note, read by
`welfare_words()`: `WORDS_EN` as whole words and `WORDS_ZH` as substrings,
any case, skipping a word right after no/not/without/non or 无/没有/未见/未,
with the first matching line); `not_weighed` (active colony experiments
whose readout is body weight: a mouse not left out, not weighed since
`max(last weight, start)` for more than `weigh_every()`, the longest gap
between the regimen's reading days while they last, else 7; any other
database's active experiment whose readout is body weight, by
`_others_not_weighed`: a fish, stock unit or organism, alive and not left
out, by its `ExperimentReading` body weights, its item a `label` in place
of `mouse`); and
`status_experiment` (`no_experiment`: status "experiment" in no
planned/active/paused experiment, checked once the lab has any colony
experiment; `other_status`: a non-blank other status in an active one,
with `propose` setting it to "experiment"). Items carry `ref`, the numbers
and a path `url`. `owner` narrows to a person's mice and the mice of their
experiments; `home_view()` uses it for a member and the lab's for
`access.is_care` (care, facility, admin), `?animals=mine|lab` switching,
`HOME_LIMIT` rows per check. UTC stamps (`changed_at`) go through
`day_start()`/`day_of()`; `weigh_date` is already the lab's day.

### Utilities

`static/bench-calcs.js` is the arithmetic and its data (molecular weights,
buffer pKa and how each is made, vessels, antibiotics and stocks,
concentrated reagents, ladders): each calculator in `CALCS` is an `id`,
`inputs` with units and a `compute(v)` that returns `{lines, table,
warnings, notes, solved}`; `run(id, raw)` reads the typed numbers (decimal
commas too) and converts each to its base unit first. What the page lists
is `TOOLS`, in `GROUPS`: a tool has a name, a line on what it gives, the
words people search for (English and Chinese) and one or more `modes`,
each running one calculator, with the `example` it opens on and the values
it can `solve` for. `search(q)` ranks tools by those words (whole word,
then start of word, then inside a word or one typo), and `locate(id)` turns
an address into a tool and mode, so an old calculator's `#dilution` still
opens Dilute. It loads in Node too, and `tests/js/bench-calcs.check.mjs`
checks known answers and the searches testers typed.
`static/utilities-page.js` draws the search, Pinned and Recent (kept in
this browser under `biomanager:util:`; Home's Calculators card reads
`recent`), the group cards, the open tool (from the address hash) and the
reference tables. The lab's chemicals come from the page (`/utilities`
passes them: its Chemicals databases through `lab_notebook.chemicals_search`,
each by name and abbreviation, then `chemical_references`) ahead of the
built-in list. The lab's centrifuge rotors (name, radius, top speed) are
one `app_settings` row, `utilities_rotors`, saved by `POST
/utilities/rotors` by anyone but a guest.

The lab's own tools are another row, `utilities_lab_tools`: each a name,
inputs (a formula name, label, unit, example) and answers (label, formula,
unit, an optional name later answers may use), with its `author`. `POST
/utilities/lab-tools` makes or changes one (its author or an admin; never
a guest) and `…/<id>/delete` removes it; the server keeps names and
formulas to a plain character set. On the page `useLabTools` turns each
into a calculator and a tool in the group `lab`, and `parseFormula` reads
a formula by hand (numbers, the tool's names, + − * / ^, brackets and a
fixed list of functions): nothing a lab types is ever run as code.

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
- What people number (cage, tank, litter, housing and cohort IDs, strain and
  line names) sorts with `positions.natural()` / `natural_sorted()`: runs of
  digits as whole numbers, the rest without case, so 2, 10, 101 and C-99
  before C-104. These columns are text, so lists are sorted in Python after
  the query (and `.limit()` after the sort); templates have `natural_sort`
  and `group_in_order` in place of Jinja's `sort` and `groupby`. A sheet's
  heading sort (`static/data-table.js`) compares text with `numeric: true`.
- `positions.parse()` reads a plain number on a lettered box as its nth
  place along the rows (imports that mix "A1" and "3").
- Global search (`global_search`) orders plasmids, inventory records and
  pages by the name: equal, then starting with the words, then containing
  them, then the rest.
- A sheet cell takes a pasted block (`static/sheet.js`, `pasteBlock`): rows
  and columns as shown, each cell put in and saved as if typed. A value
  that isn't one of a `<select>`'s options moves on to the next cell (at
  most two), and a toast names the first and last rows filled.
- A sheet card with `data-dt-views="<name>"` (every database's main sheet)
  gets saved views, filter chips and who is here; `data-dt-inspector` (with
  `mice` or `cages`, its rack drawn too) the Inspector. All three are
  `static/sheet-views.js`, on the sheet's controller (`BioSheets.sheet(el)`:
  `columns()`, `columnValues()`, `setFields()`, `state()`, `applyState()`).
  A filter chip is `{key, value, label}`, a column's `data-sort-key` that
  must read exactly that value (`_sortValue`), kept as `dt:<id>:fields`. A
  view is a name and a state (search, quick chip, filter chips, sort,
  hidden columns by key), each person's own per sheet name in
  `app_settings` (`views:<name>:<username>`, `app/sheet_views.py`, checked
  for shape by `clean()`). Who is here is `page_presence`, beaten every 30
  seconds by `POST /presence` and read back the same way; rows older than
  70 seconds are cleared; its faces go into the page header's
  `.toolbar-actions`. On such a card `layout()` lays the top out as in the
  design (`.dt-laid`): the toolbar's `.btn-primary` (New, with the key N)
  and a `[data-dt-show-as]` group (the mouse sheet's Table · Cages · Racks;
  `data-layout-pick="mouse-cages:grid"` sets the Cages tab's remembered
  layout) move to the end of the views row; the toolbar keeps the search,
  the chips and the filter chips, then `.dt-summary` ("Sorted by Mouse ↑ ·
  10 of 39", redrawn by wrapping the controller's `render()`), then its
  buttons as icons with their names as tooltips.
- A sheet's search (`data-table.js`, `_rowText`) reads a menu by its chosen
  option only; the row's `textContent` would hold every option, so a box
  or status name matched every row. Each row's text is kept while someone
  types a search (`_texts`), and forgotten on the search box's focus and
  on any edit, save or rack move in the sheet.
- Each row of a sheet saves through a form of its own, outside the table,
  that its cells join with `form="…"`. The forms come inside
  `<template data-row-forms>`, and `static/row-forms.js` (loaded in the
  head, before any page script) puts a row's form on the page, before its
  template, when one of its cells is pressed, focused, typed or pasted
  into. Anything that looks a row's form up by its id uses
  `BioRowForms.get(id)` (null if there is none); its cells are found by
  `[form="…"]` as before, whether or not the form has joined. Sent as
  part of the page, a thousand row forms made Chromium (Chrome, Edge, the
  Windows app) walk the whole page once for each form's fields as it
  loaded, up to two seconds on a sheet of a few hundred rows, and the
  square of that as the sheet grows. `_revealHashTarget` finds the row of
  a `#mouse-update-39` link by its cells.
- A long list of choices the same on every row (a fish row's **Tank**: every
  tank of the lab) is sent once, as `<template id="choices-<key>">`, and each
  row's `<select data-choices="<key>">` holds only its own choice until it
  is pressed, focused or typed in (`BiomanagerShell.fillChoices`,
  `static/shell.js`; `sheet.js`'s pasted blocks fill it before matching).
  On the fish sheet it was the tank list once a row: 130 KB a row in a lab
  of 3,000 tanks. An inventory row's **Kind**, **Status**, **Box** and each
  `select` column (`choices-inv-category`, `-inv-status`, `-inv-box`,
  `-inv-f-<key>`) work the same way; a `multiselect` cell (**Hazard**)
  holds its ticked choices as hidden fields and makes its checkboxes from
  `choices-inv-m-<key>` when it is opened (`buildMulti`). A source cell's
  ID box gets its colony's list (`inv-src-<n>`) when it is first focused or
  its colony changed, made from `/inventory/<key>/sources` (`source_ids`:
  each colony's IDs in the order of the page's menus), fetched once, the
  first time: a mouse colony's IDs were 3 MB of `<option>`s on the samples
  sheet, and then a fifth of its server time as JSON in the page.
- Rows are written without the indentation between their tags
  (`{% filter tight %}`, `app.py`), which was a third of a long inventory;
  the labels, links, titles and icons every row repeats are worked out
  once before the loop, and plain cells are written in the loop rather
  than by a macro call each.
- `sheet.js` listens on the sheet (`focusin`, `pointerdown`, `input`,
  `change`), not on each cell, and remembers a cell's value as it is
  first used (`data-previous`; `fillChoices` sets it before filling a
  menu). The box grid (`rack-grid.js`) is drawn when its panel is first
  shown (`layout:change`), not while the sheet loads behind it. An
  inventory's grid is not in its page at all (each tile carries its item's
  whole dialog, megabytes on a long inventory): the macro's `src`
  (`data-rack-src`) is `/inventory/<key>/grid` (`grid_data`, the page's
  `?type=` and `?ended=`), fetched then; the Boxes buttons above the sheet
  take their dialogs from `rack_edits`. The cage sheet's rack grid the
  same: its tiles come from `/colony/cages/grid` (`colony_cage_grid`, the
  page's `scope` and `ended`; `mouse_rack_payload(tiles=True)`), and the
  page carries the racks only, for its Racks buttons and Rack menus.
- Rows past a sheet's first page (100) are `display: none` from the
  stylesheet until `data-table.js` has drawn it
  (`.data-table-card.dt-held`, which the script takes off): the browser would
  otherwise style and lay out every row first. `render()` then touches only
  the rows whose state changes, and moves rows into a new order only when
  the order has changed (a sort, a search, a chip); a paged sheet's page
  the server drew is already in place and is not moved.
- The browser restyles a sheet as it loads only as far as a rule makes it.
  Three kinds of rule made it restyle every cell, over and over: a class
  that takes rows out of a `:not()` (`:not(.dt-ready)` was the hold above,
  so the class coming restyled the whole card; a class taken off is
  narrow), `tr:last-child td` (each row the page added restyled every cell
  of the table: a cell takes its row's rule from `--row-rule`, which only
  `tr:last-child` sets), and any rule pairing `[class]` with
  `::placeholder` (`.workbench`'s bare fields: any class changing on an
  element restyled all of it; their placeholder's colour is now their
  `--placeholder`). The mouse sheet's style work went from about 310 ms to
  130 ms. Chrome's trace with `disabled-by-default-devtools.timeline.invalidationTracking`
  says which change restyled what. `shell.js` and `data-table.js` read the
  page's scroll and sizes at the next frame, not as their scripts run,
  which laid the page out before it was drawn.
- A sheet of more than 300 rows (`PARK_OVER`) takes the rows it has not
  shown off the page (`_parkLater`), and `render()` puts each back when it
  is shown; it then stays. Chromium's autofill reads every field on a page
  as it loads (Chrome, Edge, the Windows app; not Safari or the Mac app),
  shown or not: on a sheet of a few thousand rows that took one to two
  minutes, and on a mouse sheet of 950 rows 20 s (5 s parked). Below about
  300 rows, taking them off and putting them back costs as much as it
  saves (it was 1,000 until 2.0.5). The rows leave in the window's `DOMContentLoaded`, after the
  document's, so every script that wires rows as the page loads has seen
  them, and before Chromium looks. Search, sort, export, print, the count
  and the column choices work on all of them (`this.rows`). Code that
  looks a row up later uses `BioSheets.row(id, el)` / `BioSheets.rows(el)`,
  which find a row off the page too, and `BioSheets.show(tr)` to turn the
  sheet to it first (a click on a row off the page reaches no listener on
  the document). `#mouse-update-39` and `#cage-12` links find their row
  either way. A sheet whose page scripts need every row on the page says
  `data-dt-keep-rows` (the cage sheet, when it is not paged). Until then nothing measures the
  page (a measure lays out every row): the toolbar's `is-tight` waits for
  its `ResizeObserver`. Once parked, the table holds only the page shown
  (and its detail rows): `render()` takes the others off, so the rows of
  pages turned past don't pile up in it, hidden, for every later search
  or sort to restyle.
- In Chromium a sheet's cells are drawn as text until used
  (`cell_text.text_cells_on`, a template global, by the browser's
  `Chrome/`; `?cells=text` or `?cells=fields` chooses either anywhere, and
  an inventory's rows URL carries it). A sheet's `<tbody>` wraps its rows in
  `{% filter as_text_cells(text_cells_on()) %}`: the inventories, the mice,
  the cages and the mice in each, litters, strains, fish, tanks, clutches,
  lines and the genotyping queue, vials, a stock's genotypes and frozen
  lots, plasmids. The filter (`app.py`) turns each `<td>` holding a single
  input or menu (spaces around it allowed) into a `<span class="cell-text">`:
  the text the field shows (a menu's chosen label, else its first; an
  input's placeholder when empty), the field's own markup in `data-field`,
  its value in `data-sort-value`, its name in `data-sort-for`, its row form
  in `data-form`, and the attributes styles and scripts read (`data-status`,
  `data-sex`, `data-scope`, the alive and dead values, `title`); a disabled
  or read-only field's span has no `tabindex`. Cells with more than one
  field (a source, a ticked-choices menu, a cage number beside its dot)
  stay as they are. `static/text-cells.js`, in every page's head so a
  page's own scripts can use it as they run, puts the field back when the
  span is focused (pressed, or reached with Tab), with the caret where it
  was pressed and a menu's list opened; a rack or box opens its position
  with it, as they are saved together. A page's scripts use
  `BioTextCells.field(root, name, form)` for a field (put back if it was
  text) and `BioTextCells.read(root, name, form)` for `{value, text}`
  without that; a field put back sends `text-cells:open`, which the colony
  page listens for to wire it as its fields were wired on load. `sheet.js`
  puts back a cell whose value a save changed (any sheet), reads text cells
  for the life dot, and opens a pasted-into cell (`editable`). A save sends
  the fields open in its row, and every update route changes only the
  fields it is sent (`if name in form`, `form_changed`), so the cells still
  drawn keep their values: a mouse's transgenes did not, and now keep one
  not sent. `_cellText` and `_rowText` read `data-sort-value`, so export,
  sort and search give what the fields would. A date's text is written in
  the browser's own date order as the page loads and with each
  `dt:rows-loaded`. Inventories open a fifth to a third sooner (418 samples,
  parked: 3.5–3.9 → 2.8–3.0 s), 956 mice 3.2 → 2.6 s, the lab's 734 cages
  5.8 → 4.5 s, vials 1.05 → 0.8 s; the page is 30–45% bigger, a tenth once
  compressed. Safari and the Mac app keep the fields: they never had
  autofill's wait, and the text cells are not tried there.
- An inventory of more than 500 listed items (`inventory_routes.LAZY_OVER`)
  is paged by the server. The page draws its first 100 rows (`FIRST_PAGE`,
  the sheet's first page, in its own order) and, for each other item, an
  entry in `<script type="application/json" data-dt-stubs>`
  (`_row_stubs`): its id and element id, its `data-*` (what the chips
  read), each sortable cell's value as the cell holds it (`k`: a box by
  its id, a status by its value) and the text a search looks in (`s`:
  what `_rowText` reads from a drawn row, its menus' shown choices
  included). `data-table.js` puts each in the sheet as an empty `<tr>`
  (a stub, `tr._stub`), so search, sort, the chips, the count and the
  pages work as before over every item; `_rowText` and `_sortValue` read
  a stub's entry. A page with stubs on it is drawn once their rows have
  come from `data-dt-rows-url` (`/inventory/<key>/rows?ids=…`, with the
  page's own `?type=` and `?ended=`, and `?rem=`: the columns that offer
  earlier values, which would otherwise take reading every item):
  `inventory/_sheet_rows.html` and `_row_forms.html`, the same templates
  the page draws its rows with, the forms going into a `<template
  data-row-forms>`. `_takeRows` swaps each stub for its row wherever the
  sheet keeps it, gives the row its column order, labels and hidden
  columns, and sends `dt:rows-loaded` (the samples sheet's Custom tag
  listens). Print fetches every row first (`_whenDrawn`, 500 a request,
  one after another). Export needs only the words: `_withTexts` asks
  `/rows?as=text` (three requests at a time, kept for the next export),
  which draws the rows with the same template and reads each cell as
  `_cellText` would (`app/cell_text.py`), sending `{id: [each cell's
  words]}`; a column moved on the page is found by its `data-dt-col`. Code that needs a row's cells uses
  `BioSheets.ready(tr)`, a Promise of the row, fetched if it is a stub
  (`?open=`, `#item-12`). A request that fails leaves the page as it was
  and says so; the next page turn, search or sort asks again.
- The mouse, cage and litter sheets are paged the same way past
  `LAZY_OVER` rows: the colony route draws the first `FIRST_PAGE` rows
  through `_mouse_rows.html` / `_cage_rows.html` / `_litter_rows.html` and
  their `_…_row_forms.html` (the page's and `/colony/mice/rows`'s,
  `/colony/cages/rows`'s, `/colony/litters/rows`'s), each other row as
  `app.mouse_stub` / `app.cage_stub` / `app.litter_stub`, built from the
  row's data (a stub carries its row form, so a `#mouse-update-N` link
  finds it). `tests/test_speed.py` holds `cage_stub` and `litter_stub` to
  `cell_text.row_stub` of the drawn row. The cage and litter rows
  addresses carry the page's `scope` and `ended`, and load only the rows
  asked for (`colony_context(only=…)`); a litter's pups are loaded with
  only the columns their cell shows (and, for someone who is not an
  admin, their cages: who may edit a litter is who may edit all its pups).
  Rows fetched are wired as the page's own (`dt:rows-loaded` in
  `colony.html`, which carries their detail rows as `details`).
  - A cage's detail row (its mice, data-detail-for) comes with its row
    and is kept in the sheet's `details` (`BioSheets.detail(tr)`, on the
    page or not). **Open all** / **Close all** reach every cage loaded
    (`BioSheets.rows`), and a cage fetched later opens or closes as the
    sheet is. The **Cards** layout fetches every cage first
    (`BioSheets.readyAll`, three requests at a time) and moves each
    detail row's card into its grid, and back. The cage tab works out
    which transgene columns the mouse sheet shows from the mice that can
    fill Transgene 3 or 4 only.
  - At 100,000 mice the litters (6,634) open in 3.2 s instead of 9.1, the
    cages (1,368 listed) in 3.1 s instead of 7.6 (26 MB of page to 3.8);
    their **Cards**, page and cards together, in 9.7 s instead of 10.4.
- What a long sheet costs the server, and what keeps it down:
  - Its records are read as plain rows, not ORM objects
    (`app/plain_records.py`): the mouse sheet's mice with their cages,
    racks and litters (`mice`), the litter sheet's litters with each pup's
    number and sex (`litters`; `full_pups` reads the rest, and the pups'
    cages, for the rows drawn only), an inventory's items (`inventory_items`,
    each with its box, and its `attrs_dict` decoded once). They carry the
    models' names (`access.can_edit` tells records apart by class name) and
    the columns the sheets read, so the rows, stubs and access rules read
    them as they read the models. They are for reading: every write loads
    its record through the ORM by id.
  - The mouse sheet's rows and their forms are written by
    `app/mouse_rows.py` (`mouse_rows_html`, `mouse_row_forms_html`), the
    same markup `_mouse_rows.html` wrote, character for character, and the
    text cells told what it already knows of each field (`app.text_cell`)
    rather than reading it back from the tag. A change to a mouse row's
    cells is made there. The cage sheet's rows the same: each cage's cells,
    its detail row and its mice's cells are written by `app/cage_rows.py`
    (`cage_rows_html`, from `_cage_rows.html`), as the template wrote them,
    and drawn as text cells by it; `_sheet.html`'s `id_cell` and
    `owner_cell` and `_sharing.html`'s `sharing_options`, which it writes
    as they do, are still the macros everywhere else. A page of cages is
    drawn in about a quarter of the time the template took.
  - Stubs (`mouse_stubs`, `cage_stubs`, `litter_stubs`, `_row_stubs`) work
    out what every row shares (the words for a sex, a status, a choice, an
    owner's badge, which column is which) once a sheet, and are written by
    `|stubs_json`: `|tojson`'s escaping without sorting thousands of rows'
    keys. A mouse row's cage cells are worked out once a cage
    (`services.mouse_display_row(cages=…)`), and `positions.natural()` and a
    rack's labels are cached.
  - Indexes for what the colony asks (`0044_colony_indexes`): a mouse's
    date of death, owner and cage, a cage's created date, a litter's date of
    birth, and `ix_mice_litter_pups`, which holds every column of a pup the
    litter sheet reads, so the database answers from it alone. The sheet
    reads the mice it lists without `ORDER BY mouse_id` and sorts them
    itself: asked for them in order, SQLite walked every mouse by its
    number. SQLite keeps 64 MB of pages in memory (`db.SQLITE_CACHE_KB`).
  - `app.py` sets the garbage collector to look for cycles once in 10,000
    new objects (`gc.set_threshold`): at Python's default, a long sheet's
    hundreds of thousands of objects were walked many times a page, a fifth
    of an inventory's time.
  - The cage sheet reads its cages as plain rows (`plain_records.cages`:
    every column, the rack, the mice with their litters, in the order they
    were added as the ORM gives them) and builds a cage's row in full
    (`cage_sheet_row`: each of its mice's rows, its litters to wean, who
    may edit what) only for the cages it draws, which it reads through the
    ORM with their litters born in them; each other cage gets what its
    stub, the chips and the counts read (`cage_stub_row`). The rack grid's
    tiles (`/colony/cages/grid`) are made from the same plain rows. The
    New cage dialog's next number reads every cage's in one pass
    (`services._FIRST_NUMBERS`, each one's first run of digits). The Breeders tab reads its cages, racks and
    mice as plain rows (`services.breeder_mice`), and its summary in one
    query (`breeder_summary`). The words every cage row or Breeders card
    says alike are put in the page's words once a draw (`app/cage_rows.py`,
    `w_…` in `colony.html`), and an owner's badge is
    made once a name for the request (`web.owner_badge_filter`): the short name
    the person chose (Settings → Profile, up to 5 letters; 3 or more make the
    round badge a pill, `data-len`), else two initials. The notebook's own
    badges (`notebook-page.js` `avatar`) read the same short names (`shorts`).
  - At 100,000 mice (`scripts/load-test.py`), the server's time against
    2.0.13's on the same machine: the mouse sheet (alex's 2,349 mice) 0.17 s
    (0.49), the litters (6,725) 0.4 s (2.4), the cages 0.5 s (1.15), the
    Breeders tab (1,602 cages) 0.43 s (1.07); an inventory of 20,000
    samples 0.7 s (1.85), of 20,000 reagents 0.7 s (1.4); the fish tab
    0.7 s (1.9); the tanks (3,337) 0.5 s (4.6).
- Vials are drawn whole by their page and paged after
  (`app/sheet_paging.py`): past `LAZY_OVER` rows, `page()` keeps the
  sheet's first page, makes each other row a stub with
  `cell_text.row_stub` (read from the row as drawn, as `_sortValue` and
  `_rowText` read it, so it needs no code per sheet), takes those rows'
  forms out of its `<template data-row-forms>`, and gives the sheet its
  rows address: `/stocks/<key>/units/rows`. That address builds the
  page's context with only the rows asked for, draws the page, and
  `rows_of` lifts them and their forms out. A stub carries its row form
  (`f`) for links. The server still draws every row once for the page;
  the browser builds only a page of them. At 100,000 mice: vials 3.8 s
  (4.5), a page turn 0.1–0.9 s.
- The tank sheet was paged that way, and drawing 3,337 tanks then reading
  them back took 4.6 s. Now `_zebrafish_context` draws its first page
  (`tank_sheet_rows`) and makes each other tank's stub from its data
  (`zf_tank_stubs`: its `data-*`, each field's value, a menu's shown
  choice, the owner's initials, the genotyping label, and in text cells
  an empty Pos's "–"; a lab's own columns are drawn with `custom_cell` and
  read with `row_stub`). `tests/test_speed.py` holds every stub to
  `cell_text.row_stub` of its drawn row, with and without the lab's own
  columns, in both ways of drawing cells and in Chinese.
  `/zebrafish/tanks/rows` draws only the tanks asked for
  (`_zebrafish_context(tank_ids=…)`). Each tank's fish are read as plain
  rows (its count of live fish, its fish rows); only a mating tank still
  out loads its fish in full, for its Returned dialog, and only the
  tanks drawn (and those) get their dialog's payload and delete prompt.
  The rack grid's tiles are made only for the grid (`?mode=grid`). The
  other tabs read no tank's fish at all. At 100,000 mice the tank sheet
  answers in 0.45–0.5 s.
- `static/data-table.js` keeps a sheet's sort in `localStorage`
  (`dt:<id>:sort`, restored before the first render) and its chip in the
  address (`?chip=<spec>`; `?scope=mine` picks the chip reading *Mine*).
- Its columns, per sheet in `localStorage`: `dt:<id>:hidden` (column
  numbers) and `dt:<id>:widths` (number → px), both by the column's number
  as the template draws it, and `dt:<id>:order`, the movable columns in the
  order a person dragged them into (header drag, or the arrows in the
  Columns menu), by key: `data-col-key`, else `data-sort-key`, else the
  heading's text. On load `_indexColumns` marks every cell with its drawn
  number (`data-dt-col`) and `_applyOrder` moves the cells themselves, so
  `sheet.js`'s pasted blocks, the CSV export and a phone's cards read a row
  in the order shown, while hidden columns and widths still find theirs by
  `data-dt-col`. A key the saved order lacks (a column added since) goes in
  after the column it follows in the template; one that has gone is
  dropped. Pinned columns (`.sheet-pin`), a `th[data-dt-fixed]` (an
  organism database's code), a header with a checkbox and one with no
  visible name don't move and can't be dropped among. The order a lab sees
  first is the template's: an inventory's `fields` list and the built-in
  databases' own columns in Configure (Move up / Move down renumber
  `field_<i>_*` in the order shown; an inventory's `col_sig` changes with
  it, so a sheet keyed by position starts fresh), and an organism
  database's `ModuleField.position` (`POST /organisms/<key>/field/<id>/move`,
  `organism_service.move_field`).
- The page scrolls, not the sheet. `.data-table-card` is `overflow: clip`
  (not a scroller), so its `.dt-toolbar` sticks to the top of
  `.shell-scroll` and its `.dt-bottom-bar` to the bottom;
  `_wireStickyParts` puts their heights where they are used, as
  `--dt-toolbar-h` on the table's header row and `--dt-bottom-h` on the
  selection bar (which sits above the bottom bar): set on the card, a new
  value restyled every row of the sheet. A
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
  instead. It is a plain button: the toolbar's **New** (which opens the
  record's form) is the page's one tinted action.
- The toolbar's rarer actions carry `data-dt-more` (Print in `tools()`,
  each **Import from Excel**): `data-table.js` (`_wireMore`) moves them
  into a **•••** menu before the toolbar's `.btn-primary`, so the toolbar
  keeps to one line. They stay inside the card, handlers and all; the
  menu is `position: fixed`, so a short sheet can't clip it.
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
been removed. Two pages embed third-party widgets that bring their own
stylesheet (TOAST UI on the calendar)
and the notebook editor's own CSS lives in `frontend/src/styles.css` and
`frontend/src/blocks.css`, built with `npm run build:notebook`. All three read the theme's colour tokens, so
they follow the app's palette and dark mode.

## Organism modules (configurable species databases)

Mouse colony and zebrafish are hand-written modules with their own tables:
the colony's pages are in `app/colony_routes.py`, the zebrafish database's
in `app/zebrafish_routes.py`. Both hang their routes on the application
object itself (`app/flask_app.py`) rather than a blueprint, so their
endpoints keep the names every page links to ("colony", "zebrafish_*").
`app.py` imports the zebrafish's module last: it still takes one template
filter from there.
Everything else is configurable: an **organism module** is a row in
`organism_modules` that describes a species, and one generic engine serves it.

A module declares:

- **Vocabulary** — cage/tank/vial/plate, strain/line/stock, litter/clutch/progeny.
  These are what the UI calls things, so a fly database says "vial" and "stock".
- **Identity mode** — individuals (mice), groups with a headcount (flies, worms),
  or hybrid (fish: groups that can resolve into named individuals).
- **Capabilities** — 20 switches covering crosses, cohorts, a nursery stage,
  genotyping, environment logs, cryo inventory, seed lots, protocol/census,
  billing and more. See `app/organisms.py`.
- **Schedule rules** — `anchor date + offset`, optionally varying by rearing
  temperature. One rule covers "flip flies every 14 days at 25 °C, 28 at 18 °C".
  `complete_due()` takes the day it was done (never later than today) and
  moves a service anchor (`SERVICE_ANCHORS`, e.g. `last_serviced_on`) to it,
  unless two recurring rules on that kind of subject count from the same
  anchor: then the anchor stays and each counts from its own last
  completion in `recompute_due()`.
- **Custom fields** — typed per-module columns stored in each row's `attrs`
  JSON, which generate their own form inputs, table columns and validation.
  A `plasmid` field holds a plasmid's number: the page suggests the lab's
  plasmids (`inventory_routes._plasmid_options`) and links each to its page
  (`inventory_service.plasmid_links`).
- **Its own words and lists**, in `settings` (Configure): `born_word` and
  `ended_word` for the two dates (`ModuleView.born_word`, `ended_word`,
  `cohort_born_word`; empty keeps Born and Died / removed), `stages` for a
  cohort, `location_kinds` for a rack or room, `seed_units`, and
  `stage_bands` `{young, old}` in days for the dot before an ID
  (`life_stage.for_module`, else the preset's bands). An empty `sexes`
  list stays empty (`create_module` defaults only a missing one) and the
  Sex column goes.

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
| `organism_seed_lots` | seed lots: amount left, storage, generation, germination, a selection plate's counts |

Every row carries `module_id_fk`, and every relation is resolved through
`_ref()` in `app/organism_routes.py` so a reference can never cross modules.

### Plants and seed lots

The **Plants** and **Field crops** presets (`plant`, `field_crop`) are
organism modules like any other, shaped on what plant labs' own tools keep
(Phytotracker, SeedUSoon, LabCollector's plant manager; BMS and Breedbase for
field breeding): no sexes, *Sown* and *Harvested / removed*, plant stages,
growth rooms, greenhouses, nurseries and fields as places, and the
`seed_bank` capability. A field is a location with rows and columns, its
plots housing units on that grid.

`OrgSeedLot` (`organism_seed_lots`, revision 0045) is seed kept to sow
again. Where it came from is a soft link, `source_kind` (organism / cohort
/ cross) and `source_id`, as the logs keep theirs; a cohort sown from it
points back by `OrgCohort.seed_lot_id_fk` (also soft: deleting the lot
clears it) and carries its `generation`. So a line's descent runs
lot → cohort → plant → lot, and `organism_service.SeedBook` (read once per
page by `seed_book()`) walks it back to the first lot.

- **Harvest** is `/organisms/<key>/seedlot/save` with a source: the lot
  takes the source's line, and its generation from `harvest_generation()`
  (selfed seed is `next_generation()` of the plant's cohort, T1 → T2;
  seed from a cross between two parents is F1). `end_source` ends the
  plants (`_end_with_harvest`: the database's status that says harvest,
  and the date) or collects the cross.
- **Sow** is `/organisms/<key>/cohort/save` with `seed_lot_id_fk`: the
  cohort takes the lot's line and generation unless given, and
  `seed_taken` comes off `amount_left` (`take_seed()`, refused beyond
  what is left; counted in seeds, it is also the cohort's initial count).
- `segregation(resistant, sensitive)` tests a selection plate against 3:1
  and 15:1 by χ² (1 df, p 0.05; all resistant from 30 seedlings up reads
  homozygous), for the Seeds sheet's Selection column.
- A seed lot is a schedule subject (`seedlot`, anchors `harvested_on` and
  `germination_tested_on`); one with nothing left drops off the schedule
  and the census. Labels print at `/labels/cards/seeds/<key>`; search and
  the assistant's lookup (`org_seedlot`) find lots; the change history
  tracks the table.

### Columns a lab adds to a built-in database

Inventories, stock collections and organism databases have taken custom
columns from the start; the mouse colony, zebrafish and plasmids could not,
because their columns are real ones, so a lab that weighs organs had nowhere
to put it but the notes. `app/custom_fields.py` gives all three what the
others have, by the same shapes: a definition in `app_settings` under
`custom_fields:<database>` (**Configure → Your own columns**, admins), and a
value in the record's own `attrs` JSON — `mice`, `tanks`, `fish` and
`plasmids` each gained one in revision 0025, the column an inventory item
has always had. Adding a column is a setting, not a migration.

`fields()` reads and `set_fields()` keeps them; `normalise()` drops a column
whose key is one the record already has (`RESERVED`), or the sheet would
have two columns answering to one name. `apply_form()` copies `attr_<key>`
from a submitted form, touching only the keys the form sent, so saving one
cell of a sheet row never blanks the rest. `values()` reads them back.
`_sheet.html`'s `custom_cell` draws one, as the inventory sheet's own
`field_cell` does for an inventory's; the types are the simple ones
(`FIELD_TYPES`), since *source* and *plasmid* describe what an inventory
record points at rather than anything a mouse carries. Removing a column
hides it and keeps what records hold in it, so putting it back shows the
values again.

A column of the lab's own goes everywhere a built-in one does:
**Set field** offers it (the bulk routes look for `attr_<key>` before their
own actions and hand it to `_bulk_custom_column`, one batch that Batch
history undoes); **Import from Excel** matches a spreadsheet column to it by
its own name (`custom_import_fields`, typed as the column is: a number
column takes a number, a choice column its choices); and search finds a
record by what one holds, with a LIKE over the record's `attrs` — one JSON
string, so the value is found whichever column holds it, the reach the notes
have always had.

### The order the databases sit in

The rail lists the built-in pages and then whatever the lab added, which is
the order they came into being rather than the order the work runs in. A lab
says otherwise on **All databases → Order in the sidebar** (an admin; rows
drag, or move with arrows, and the form posts the whole order, so no step
depends on which row moved — the same handling as Home's Customise, down to
its classes), or by dragging in the rail itself on any page
(`static/shell.js` `setupRailOrder`: the admin's Databases group carries
`data-rail-order`, each entry `data-db-key`; a drop posts `rail=1` and the
rail's keys, which `lab.arrange_within` puts in the places they held, so
what the rail doesn't show — Plasmids' tabs, others' own databases — stays
put): one list of keys
in `app_settings` under `databases:order` (`lab.database_order`,
`set_database_order`, `move_database`), applied by `lab.in_database_order`
where the rail's Databases group is assembled. The keys are the rail's own —
a built-in bare (`colony`), a module namespaced by its kind
(`inventory:samples`, `stock:drosophila`, `organism:worms`), so two
databases of different kinds may share a key. Anything the list does not
name keeps its place after the ones it does, so a new database joins the end
instead of landing in the middle of someone's arrangement. No migration: the
setting is one row.

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
  to someone else, is its owner's or an admin's (animal care may also
  reassign), not everyone a shared cage lets edit. The whole lab can edit a
  shared cage and pick mice out of it.
- **Giving a record away, or sharing it** is one rule for every database,
  in `app/stewardship.py`: `change_owner()` (with `may_give_away()`:
  `access.can_manage()`, or animal care on `access.CARE_RECORDS`; and the
  new owner one of `lab_members()`, or nobody), `batch_owner_refused()`
  for a batch's Set owner (a lab member, never blank), and
  `change_sharing()` (`can_manage()`, then `groups.apply`). Cages, tanks,
  fish lines, clutches, plasmids, inventory items, vials and frozen lots,
  and every organism record (`organism_routes._set_owner`) call it from
  their dialogs, sheet rows and batch actions; each answers with the
  refusal in words or None. Mice stay the colony's: anyone who may edit a
  mouse in a shared cage may take it (Pick), so `colony_routes.change_mouse` checks
  only that the new owner is in the lab. `tests/test_stewardship.py`.
- **What a cage's purpose does** (CLAUDE.md, "The mouse colony's words").
  *Breeding* is a mating cage: only it gets Litter born, Genotyping and
  Wean (`services.is_breeding_cage()`, the row's `can_breed` and
  `breeding`). *Breeder* is the lab's breeding stock: it starts shared
  (above) and the Breeders tab lists these cages to Pick from
  (`services.is_breeder_cage()`, `breeder_mice()`); the breeder summary
  counts the mice in both (`holds_breeders()`). *Active* is living mice
  only (`cage_is_active()`; the old `active_override` flag is ignored),
  and there is no Retired purpose: the Retire batch action clears it.
  The lab's purposes are the colony's `dropdown_options` rows with
  `field_name = 'purpose'` (`cage_purposes()`; a new lab gets
  `CAGE_PURPOSE_CHOICES`), edited on the Dropdowns tab or the colony's
  Configure page (`organisms.configure_builtin`, posting to the same
  `create_option` / `delete_option` with `back=configure`).
  The **Dropdowns** tab is `app/choices.py` and `static/choices.js`:
  `choices.columns()` lists each column with choices (`FIELDS`), its
  `dropdown_options` rows in `position` order (revision 0052; 0 until a
  lab drags them, then A–Z still breaks ties), the built-in ones the
  sheet always offers (`BUILT_IN`: the statuses, F/M/Unknown) as fixed
  rows, each choice's count of records (`USES`: column → model and
  field), and up to eight values the records hold that are no choice
  (`loose`). The routes answer `X-Requested-With: fetch` with JSON and the
  tab redraws itself from the page: `create_option` takes several lines,
  `update_option` with `also_records=1` and `delete_option` with
  `replace_with` rewrite the records through `bulk.run` (one audit batch,
  undone from Batch history), and `order_options` sets the positions.
  `choices.locked()` keeps Breeding and Breeder from being renamed. The bar above
  the cages is All, Active, then one chip per purpose
  (`cage_purpose_chips()`). Revision 0029 wrote "Exp" and "Experiments" as
  "Experiment", made Stock cages shared Breeder cages, cleared "Retired",
  and took Stock and Retired out of the choices.
- **A cage's litters** (`cage_litters`, `models.CageLitter`, revision 0037).
  A mating cage often holds two females, so a second litter comes before
  the first is weaned. Each birth is a row: `born`, `pups` and `dam` if
  given, `litter_id_fk` (the litter of mice Genotyping made of its pups),
  `weaned_on`. `mouse_cages.date_give_birth` stays, as the cage's oldest
  litter not yet weaned (`services.refresh_birth_date`, called wherever
  litters change), so the sheet's *Litter born* cell, the sync view, the
  exports and every other reader of one date read as before; typing in that
  cell corrects the oldest (`litters.set_born`, then `services.set_birth_date`;
  the row's `date_give_birth_was` keeps a stale row from writing it back),
  and only on a Breeding cage, like New cage's date; clearing it works on any. `open_litters()` are the ones
  not weaned, `waiting_litters()` those whose pups aren't all recorded
  dead. The rules live in `app/litters.py`, which the routes, `actions.py`
  (`_which_said`), Home's proposals and the cage sheet's answer (`can_breed`,
  `breeding`, `wean_due`) all read: `not_breeding()`, `pups()`, `choose()` /
  `default()`, `record_birth()`, `too_young()`, each returning its refusal
  or what was done, in words, for the route to flash.
  **Litter born** (`litters.record_birth`) on a cage with one open and a
  new date wants `litter_choice` *another* or *correct* (and `litter`, the
  birth date corrected, when there are two) and changes nothing without it;
  the same date again only adds the count or mother. **Genotyping** and
  **Wean** take `litter` (a birth date; `litters.choose`), defaulting to
  the oldest waiting (Genotyping: the oldest not yet genotyped);
  `mark_weaned(cage, cage_litter=…)` weans that one and the litters of mice
  born that day, and moves the cage's date on. `weaning_due` lists each
  open litter (an item's `cage_litter`, and `several` when its cage has
  two, which `weaning_title` shows as "(born 29 Sep)"); the calendar's
  genotyping items are one per open litter too, and Home's genotyping queue
  is one item per cage and litter. The table is audited, so undoing a wean
  undoes its litter. Revision 0037 made each cage's date its first litter,
  linked to the litter of its pups born that day.
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
`access.in_scope` decides each record in Python (a mouse's sharing is its
cage's); the mouse and cage sheets and the cage cards first ask the database
for `access.scope_superset` (own or unowned, or in a shared cage; …), a
superset that spares loading everyone's records, and
`tests/test_group_access.py` holds it to letting through all that in_scope
lists. The cage sheet counts the cages its 90-day rule leaves out over the
whole colony, from its mice's dates and statuses only.

### Uploaded files

Every upload (a notebook page's pictures and attachments, a plasmid's
files, a capture's photo or CSV) is saved by `services.save_uploaded_file`
(or `save_uploaded_image`) under a random name in the uploads folder and
served by one route, `/static/uploads/<path>` (`app/uploads.serve`, always
registered, wherever the folder is: `app/static/uploads`, a frozen app's
user-data folder, `BIOMANAGER_UPLOADS_DIR`). `security.guard_uploads` sends
anyone not signed in to sign in; then the file goes only to someone who may
see a record it belongs to, else 404 (`Cache-Control: no-store`; a file
served is `private, max-age=3600`).

What a file belongs to is the table `upload_owners` (`UploadOwner`,
revision `0038_upload_owners`): `name`, and either `source = "upload"` with
the `username` who uploaded it (the notebook editor's `/notebook/upload-image`
and `/notebook/upload-file`, so a picture shows before its page is saved),
or `source` = a table and `record_id` = a row whose text names
`uploads/<name>`. An `after_flush` hook (`uploads._note_names`) adds those
rows whenever any record naming a file is saved, through any page, the API
or a proposal: a page's body, its versions, a template, `plasmid_files.path`,
`captures.file_path`, an item's notes. The revision found what older
records named by reading every text column. Rows are never removed; a
record that went stops counting. `may_fetch` reads them by the indexed
name and asks of each record, as its page would (`_record_visible`): a
notebook page and the rows hanging off it (`page_id_fk`), `role_for` (so
shares, the lab's `*` but not for guests, project groups; admins don't
open others' private pages, nor their pictures); a capture, its owner; a
template, its owner, or the lab or their project group if shared; a
plasmid's file, while the plasmid exists (every plasmid is the lab's to
see); the lab copy's classes for the rest (`ADMIN_ONLY` admins, `OWN_ROWS`
the person, `OWN_OR_SHARED` owner or shared with them, a personal or
project group's database and its rows `lab.can_see`); any other record is
the lab's, for everyone signed in. A file no existing record names (never
named, or its records deleted) is its uploader's and the admins'. A
member's copy of the lab keeps the `upload_owners` rows of the records it
holds and their own uploads (`uploads.narrow_copy`). A saved record adds
its row only when whoever saves it may open the file already
(`uploads._may_carry`: a file this request uploaded, `note_fresh` from
`save_uploaded_file`; or `may_fetch` as above), so a name pasted into
one's own page opens nothing for them or its readers; outside a request
(start-up, scripts, the revision, a whole lab copied or moved) the records
are the lab's own and count.

## Project groups

`app/groups.py`, shown in Settings → People & access → Project groups
(`/groups` redirects there). Tables `lab_groups` and `lab_group_members`
(`lead`: may add and remove members and set the group's switches; making
groups, renaming, deleting and naming leads is for admins), revision 0015.

**What members may do in a group** (revision 0026): six `lab_groups`
switches, `groups.SWITCHES` (`may_edit_shared`, `may_change_records`,
`may_edit_each_other`, `may_edit_pages`, `may_tick_todos`, `may_share`),
and `lab_group_members.can_edit` (off: *Can only view*). `groups.member_may(
group, what)` is the one rule: an admin or a lead always, a member who can
edit when the switch is on, a viewer never. It is asked by
`groups.record_editable()` (shared cages and mice via
`access.cage_editable_shared`, plasmids, inventory, tanks, stock vials;
to-dos with `what="todos"`), `may_share_with()` and `choices()`,
`lab_notebook.role_for` (a group's edit share reads as view when
`pages` is off), `lab.opened()` (every POST into a group's database needs
`records`; each blueprint's `_module_or_404` calls it and keeps the module
in `g.current_module`) and `access._peer_editable` (`each_other`: a record
in that database whoever added it). `record_shared_with()` still decides
who *sees* something. Every switch starts as groups worked before 0026.
`/groups/<id>/switches` and `/groups/<id>/members/<who>/part` save them
(leads and admins; only an admin makes or unmakes a lead). A record shared with a group keeps `is_shared` and names the group in
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
`shared_page_ids`), and a topic by a `notebook_topic_shares` row the same
way; a notebook template with `lab` on and a group is that group's.

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

**Set a field…** (`static/selection-bar.js`, `anyField`) lists, when its
panel opens, every column of the ticked rows a person may edit (a cell's
field, or a text cell's `data-field`) beside the columns the sheet's batch
route sets (`[data-bulk-field]` options; a sheet without one gets a form
of the rows alone). A column of the route posts as before; any other is
set row by row: the cell takes the value and the row's form is saved as an
edit would save it (`BioSheet.save`, or the sheet's own `card.bioSave`,
as the mouse sheet's `saveFormInBackground`), each sending `X-Batch-Id`
from `POST /batch/start` so their audit rows join one batch
(`app/batch_edit.py`, a `before_app_request` that sets `g.audit_batch_id`
for its own opener's batch, open half an hour), closed with
`/batch/<id>/finish` and its count. A post that reloads the same page
opens where it was: `data-table.js` notes the scroll and each sheet's page
in sessionStorage (`biomanager:place`) as a POST form goes, and uses it if
the page comes back to the same address within two minutes.

The calendar's **Board** (`app/task_board.py`, `static/calendar-board.js`)
draws the same to-dos as cards: `tasks.board_list` is the list one is in
(empty: Inbox) and `board_position` its place there (revision 0051), the
to-do's own, so a shared to-do sits in one list for everyone. The lists'
names and order are each person's, in `app_settings` (`board:<username>`);
a list someone else put a shared to-do in shows after one's own. Done is
the to-dos ticked off in the last 14 days; moving a card into it sets
`done_at`, out of it clears it. `calendar_items` leaves out a to-do with no
day that is in a list (one with no day otherwise shows on today).
A new card's day may be written in its title: `task_board.when_in()`
reads one at the end (English: today, tomorrow, a weekday, next Fri, in
3 days, Nov 8, 8 Nov, 11/8, an ISO date, after on/by/due/for) or, in
Chinese, at either end (今天, 明天, 后天, 周五, 下周一, 11月8日), takes it
out of the title and sets `due_date`; `GET /calendar/board/when` gives
the box its hint, and `read_day: false` keeps the words. Inbox's column
also carries `calendar`: `home_layouts.build_agenda()` for the next 7 days
and anything overdue, less the calendar's own events (`_from_calendar`),
drawn under the cards as links to where each is done.
The calendar page's Month (`calendar-page.js`, `#cal[data-view]`) hides
the sidebar for the layer chips above the grid (`renderChips`, switching
the same `[data-layer-toggle]` boxes) and the day panel `#cal-dayside`
(`renderDay`; a click on a month cell picks its day, read from the
`monthGridHeader` template's `data-date`); `placeWells()` moves the
Meetings, Equipment and Protocols wells and the phone links between the
sidebar and that panel. Week and Day scroll to 08:00 once drawn
(`toWorkingHours`), and `markToday()` tints today's Due cell. `?protocol=`
opens a protocol timeline (`<id>`, `new`, or `start&template=<id>`).

**Settings → Statistics** (`settings_lab.statistics`) counts living mice,
active fish tanks and fly and worm vials per person (one animal at a time:
they are counted in different units), every rack, box and incubator with
how full it is (`admin_racks.CONTAINERS`; `rows × cols` places) and who may
change it (`admin_racks.assign`, autosaved), and what is coming up.

Cage ownership is backfilled on first run from the mice each cage holds; a
cage whose mice disagree is left unowned rather than guessed at.

## The way in

Every page before signing in extends `templates/door.html`: the lab on the
left (the BioManager mark, the lab's name and initials, the lab's shared
databases, on the helix `door.pattern()` draws once), the one thing to do
on the right. `app/door.py` gathers that (`door.context()`; from the
internet, the name only) and holds what is new around `/login` and
`/register` in `app.py`:

- **`/`** signed out is the sign-in (`door.signin`). Before any account it
  starts a lab instead: on a server `door/fresh.html` (no lab yet; the wrong
  address?); in the desktop app (`devices.on_this_computer()`)
  `door/first.html` asks whether the lab already has a BioManager.
- **Opening a lab** (`/open-lab`, desktop, before any account): the typed
  address is tried (https, then http) for `/healthz`, the lab's name read
  from its page title, and `window_url` saved in the desktop prefs, so the
  window opens it from then on.
- **Starting a lab**: `/start` keeps the name in the session
  (`NEW_LAB_NAME`), `/start/where` (desktop) chooses this computer or the
  server set-up, `/register` makes the admin, saves the name as `lab_name`,
  signs them in and sends them to the survey; the survey's first run ends
  on `/ready` (`lab_routes.py`) with the address people join at.
- **Joining**: `/register` keeps the username in the session (`PENDING`)
  and goes to `/joined`, which asks `/joined/status` until an admin
  approves.
- **Asking the admins** (`/ask`, open to the internet like `/guest`,
  `ask_throttle` per address): a notification to every admin, one English
  title per reason so each reads it in their own language.
- **Remembered accounts**: "Remember me on this computer" puts
  `remember_account` in the session; `base.html` moves it to
  `localStorage["biomanager:accounts"]` (names only) on the next page, and
  `static/door.js` offers them on the sign-in.

## A whole lab, moved or set aside

- **The lab file** (`app/lab_transfer.py`): a zip of `manifest.json`
  (format, version, schema revision, lab name, counts, the database's
  SHA-256), `lab.db` (`lab_copy.write_snapshot`, secrets blanked) and
  `uploads/`. Settings that are a machine's (`MACHINE_KEYS`: devices,
  lab copies, update checks, counts, sign-outs) are neither carried nor
  overwritten. `open_file` refuses anything else (and paths out of the
  zip, a bad checksum); `check` refuses another schema revision and lists
  values the target would refuse (`read_rows`, shared with
  `scripts/migrate-to-postgres.py`); `apply` replaces every table in one
  transaction (PostgreSQL: deferred keys, sequences past the old ids) and
  copies the files.
- **Taking one in** (`app/door.py`): `/lab/bring` (a form) and
  `/lab/import` (the desktop's push: the file as the body, the setup code
  and admin in headers, JSON back with a copy key for that admin). Only
  before any account; on a server with the setup code, throttled. Caddy
  lets these two paths take up to 16 GB.
- **The desktop's side** (`app/lab_move.py`): **Save the whole lab**
  (Downloads, or a download on a server), **Move this lab to a server**
  (`push`, then `devices.STATE` is AWAY with `moved`, the lab-copy link is
  set and `window_url` points at the server) and its way back
  (`/settings/devices/move/back` clears both; the local lab never
  changed).
- **Set aside and back** (`app/paths.py`): `start-new-lab` and
  `restore-lab` request files in the data folder, acted on by
  `set_lab_aside()` before the database opens (`desktop.py`); a restore
  sets the present lab aside first. `/restore-lab` lists `old_labs()`.
- **The window's way back**: `DesktopApi.this_computer` (a lab's sign-in
  shows *Use this computer's BioManager* inside the window), and, in the
  Windows browser fallback, `/lab-elsewhere` instead of the other lab.
- **A server starting over**: `deploy/backup/start-over.sh` renames the
  database and moves the files aside, `--undo STAMP` puts them back.

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
- **Stock kinds:** `inventory_service.RESTOCK_KINDS` (reagents, chemicals,
  antibodies, viruses) are what a received order can become (`STOCK_KINDS`), what
  offers **Order again**, and what Home's *Expiring & low stock* watches.
- **Several choices:** field type `multiselect` (Reagents' and Chemicals'
  *Hazard*, `inventory.HAZARDS`) keeps the ticked choices as one text,
  `"flammable, explosive precursor"`, so search, export and import read it
  as before. Its sheet cell is a `<details>` menu of checkboxes and the
  dialog a row of them; both also send `attr_<key>__several`, so a row with
  none ticked saves as empty (`inventory_routes.several()` orders them by
  the field's choices and keeps one that isn't among them). Migration 0030
  moved the Hazard field labs already had onto it.
- **Low at:** `inventory_items.low_at` (a nullable float, revision 0031)
  is the level, in the quantity's unit, at which an item is low. Its sheet
  column and dialog field show where `inventory_service.tracks_low()` holds
  (the *quantity* feature, and statuses with "in stock" and "low"). The
  quantity stays free text; `amount()` reads "12", "2.5", "2,5" (a decimal
  comma) or "1,000" as a number and anything else as none.
  `_item_from_form` calls `follow_stock_level()` when the quantity or the
  level changed and the same save didn't set the status by hand: at or
  below the level "in stock" turns "low", at 0 "empty" (or "used up",
  whichever the list has; through `apply_status`, so the box cell is
  freed), and above it again "in stock". Any other status (discarded, or
  one a lab renamed) and a quantity that isn't a number are left alone. So
  the sheet, the dialog, Set field and the API all follow it, and
  `_row_json` sends the new status and `low_at` back. With Low at, the
  sheet saves the quantity on change, not while typing. Leaving "in stock"
  this way sets a transient `_ran_low` on the item, which `notify._item`
  turns into one `orders`-category notice to its owner (not to whoever
  made the change), so each fall below the level tells them once.
  `attention_items()` carries the quantity and unit for Home.
- **Pack size:** `inventory_items.pack_size` (text, revision 0036) is what
  one of the item's unit holds, "10 mL" for an item kept in ampoules. Its
  sheet column (hidden until shown) and dialog field are where
  `has_pack_size()` holds (the *quantity* feature, not Orders);
  `_item_from_form` refuses one that `pack_size()` can't read as a number
  above 0 and a unit, and keeps it as `pack_size_text()` gives it ("10mL"
  → "10 mL"). It changes nothing on the page: `actions._used` reads it,
  so an amount used in its unit, or one of the same family (`UNITS`), comes
  off as part of one (2 mL of 10 mL ampoules is 0.2).
- **Renumber:** an item's # (`number`, given by `next_number()`, the
  highest + 1) follows the order items were made in, so after an import it
  follows the file. **Renumber** (the sheet's More menu, for an admin or
  the inventory's maker; `/inventory/<key>/renumber`) gives every item a new
  #, by a column (`inventory_renumber.natural()`: B2 before B10, empty last;
  ascending or descending) or `name_number`, the last number in each name
  ("B12" is #12), refused, naming the items, when a name has none, two have
  one number, or one is missing. The page previews each # now → new #; the
  POST is one `audit.batch`. `inventory_renumber.apply()` first moves the
  items to `-id` with a core UPDATE (not in the change history), then sets
  each new number as an ordinary change, so no two items share a number
  at any moment (there is no unique index on `(module_id_fk, number)`;
  this keeps it safe if one comes) and undo puts each back. In the same
  batch it rewrites what stores a number: an order's `stocked_as`, an
  order's notes "Reorder of <inventory> #n" (`_same_thing` reads them), a
  stock record's "From <order> #n", and the `number` in an experiment
  day's `samples`. "@<key> n" in notebook pages, templates, comments and
  pending inserts (every key the inventory has had; "@order n" for the
  first orders inventory) has no change history, so `follow_text()`
  rewrites it, resetting a page's live editors, and `undo.undo` calls
  `inventory_renumber.undone()` for the numbers it puts back. A signed,
  locked page and earlier versions keep the old numbers; the preview names
  the locked pages, and says that printed labels show the old # (their QR
  code holds the row id, so it still opens the item). The assistant's
  `inventory_renumber` posts to the same page.
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
- **Kept in:** `inventory_racks.place` (revision 0035, String(120), empty
  by default) is the fridge, freezer or cabinet a box is in. Nothing on
  the items changes when it does: `box_label()` ("Fridge 2 › Box A") and
  `where_label()` ("Fridge 2 › Box A · B3") in `inventory_service` put it
  first wherever an item's place is written (the sheet's Box menu, so its
  search and export see it; labels, the notebook popover, Home, the
  plasmid page, the was-in note, global search, which also matches items
  by their box's place, and `kept_in` in the API's item JSON). `box_order`
  sorts boxes by place, those without last; `_grid_payload` sends the place
  as each rack's `group`, so the grid's first picker is **Kept in**
  (shown only once a box has a place). `places_in_use()` fills the box
  dialog's datalist from every inventory the person sees and the plasmid
  boxes' freezers. Import from Excel finds a box by its name or by the
  label the sheet exports.
- **Primers:** the same listener fills a primer's `length`, `gc` and `tm`
  (`PRIMER_DERIVED`) from `attrs.sequence` in a `primers` inventory
  (`primer_numbers()`: SantaLucia 1998 nearest-neighbour stacks, 50 mM Na⁺
  entropy correction, 250 nM primer in excess). The sheet shows them read
  only and `_row_json` sends them back after a save. `add_primer_pair`
  makes *name*-F and *name*-R through `_item_from_form`, flushing between
  them so the second takes the next free cell.
- **Presets:** `inventory.PRESETS` (and `lab.INVENTORY_CHOICES` for the
  setup survey) include `chemicals`, `primers`, `glycerol_stocks` and `cell_lines`; Samples has number
  columns for what was measured (`SAMPLE_MEASURES`), which revision 0008
  adds to Samples databases made earlier unless a column of that key or
  name is there. Samples also has *Tissue*, *Genotype*, *Condition*,
  *Fixation* and *Orientation* (`SAMPLE_DETAILS`), which revision 0041 adds
  the same way, and gives the measured columns their types. The same revision adds `plasmids.concentration`
  and `plasmids.a260_280` (typed numbers, checked by `_plasmid_measure`).
- **Type tabs:** the *tabs* feature (Samples has it; revision 0041 adds it
  to Samples made earlier) puts a tab for each category above the sheet
  (`ModuleView.tabs`, the category choices). `?type=` picks one
  (`ModuleView.tab()`, any case; anything else is All): `module()` keeps
  that type's items, the columns used for it (`ModuleView.fields_for()`,
  `inventory.used_for()`), and the boxes holding it or nothing, each still
  with everything it holds so no taken cell looks free. A field's `types`
  (Configure's **Used for**, `_used_for()`: the list's spellings, a type
  renamed in the same save followed, all of them stored as none) names
  the categories it is for; none is every category, and so is an item with
  no category. The tab's columns change `col_sig`, so each tab keeps its own
  widths and hidden columns. Under All, a cell not used for the row's type
  and empty is a dash (`rows[].unused`). The item dialog wraps each field in
  `data-for-types` and hides and disables those not for the Type chosen, so
  they post nothing and keep their values; `_item_from_form` doesn't hold a
  required column to a type it isn't used for. `add_type` (the **+**,
  anyone who can open the database) appends a category; renaming and
  removing stay in Configure, as they relabel items. Samples' *Genotype*
  (`GENOTYPE_KEY`), empty, shows the source mouse's (`_mouse_links` reads
  `genotype` with the tag; `_source_genotype`) as an italic placeholder
  (`input.from-colony`) and as the row's `data-mouse-genotype` for search.
- **Split notes:** the selection bar's `action=split_notes` runs
  `inventory_service.split_notes()` on each ticked row: the notes split on
  `;` (an allele with no header after one, `Ai9/+`, joins the part before
  it, as a genotype's loci are written: `ALLELE`), and each `Header: value` part whose header is a field's label or key
  (or the category's label) goes into it, through `_note_value()` (a number
  or date that doesn't read as one stays); a field already holding another
  value, a source, person, link or plasmid field, and the rest stay in the
  notes, in order. An item with no category whose moved fields share exactly
  one type takes it. Headers naming no column are flashed. One batch.
- **Set field:** the selection bar's `action=field` (`bulk_fields()`: the
  built-in columns the inventory uses, every custom one and notes) runs
  each ticked row through `_item_from_form` with that one column, inside
  the batch; a refused value leaves that row as it was and is named. A
  *source* column is two answers — which colony and the ID in it — so the
  bar shows a second box for the colony and `_bulk_value` pairs them back
  into the `attr_<key>_kind` / `_ref` the item form posts; one mouse's
  whole harvest takes its source in one go. The sheet's own source cell
  (`field_cell`) is those same two boxes, autosaving like every other cell,
  with the colony's identifiers offered in the ID box (`inv-src-<n>`, the
  datalist the dialog uses) and the mouse it names one click away; it was
  the one cell that could only be changed by opening the record.
  `_mouse_links` reads each named mouse's `ear_tag` along with its row, so a
  sample shows what is written on the animal it came from, read fresh every
  render: a mouse re-tagged in the colony says the new mark on every one of
  its samples at once. It is not editable there — the tag belongs to the
  mouse, and a mouse with a dozen samples would otherwise have a dozen
  places to change it from, and a dozen ways to disagree.
  The sheet also shows it as a read-only *Custom tag* column after each
  source column, and follows the person's Columns choice on the mouse
  sheet: data-table.js keeps that by column number under
  `dt:<table id>:hidden`, `templates/_mouse_sheet.html` names the mouse
  sheet's id and columns (the cages' transgenes read it too), and a small
  script after the table marks the header `data-dt-off="1"` when the tag is
  hidden there. data-table.js keeps such a column hidden, out of the
  Columns menu and the export, without saving it, so the column numbers
  the Samples sheet saves stay those of its columns as drawn.
  The arrow that opens the mouse carries `data-mouse-peek="<mouse ID>"`
  (and `data-mouse-peek-tag`, dropped by that same script when the tag is
  hidden): `static/mouse-peek.js` shows the notebook's @mouse card over
  it, from the same `/notebook/lookup/mouse/<id>` and
  `/notebook/backlinks/mouse/<id>`, in `.mouse-peek` (the notebook's
  `.entity-popover` lives in the editor's own CSS, not on this page).
  The lookup sends the mouse's `age_days`/`age_weeks` from its litter's
  birth date (`calculate_age_fields`, as the colony sheet counts it).
  What the save would have said
  (a source naming no mouse in the colony) is flashed once, not per row. Organisms do the same for custom fields (`bulk_animals`,
  `bulk_housing`, `_set_custom`), and their sheet edits custom cells in
  place, with `attr_<key>_was` so a stale row doesn't undo a later change
  (`read_attrs_checked` skips a field whose value equals its `_was`).
- **Received → stock:** `_offers_stock()` is true when a save moved an
  order to received and it is not stocked yet; autosave and board moves
  answer `offer_stock`, the dialog redirects with `?offer=<id>`.
  `order_to_reagents` redirects to the new record with `?open=<id>`. These
  one-shot parameters are removed from the address on load and from the
  referrer in `_back()`.

## Plasmids and their sequences

A plasmid's sequence is `plasmids.full_sequence` (uppercase IUPAC, no
spaces), `is_circular` and `features_json`: every annotation the map holds,
features without a `kind` and primers, translations and parts with one
(`ANNOTATION_KINDS` in `app.py`). Coordinates are 0-based inclusive; one
crossing the origin keeps `start > end`.

- **Import** (`app/sequence_parser.py`, no BioPython): SnapGene `.dna`
  (packets: 0x00 sequence and topology, 0x0A features, 0x06 notes, whose
  `CustomMapLabel` names the plasmid and whose description fills an empty
  Notes), GenBank and FASTA, or pasted bases. Every qualifier is kept, as
  the editor's `notes` shape `{key: [values]}` (`qualifier_notes`, up to
  `NOTE_LIMIT` characters each), and a `/translation` rejoins without
  spaces. The LOCUS line's topology is read whatever its name does, and a
  name run into the length is split off once the sequence's length is
  known. A byte-order mark and lines before LOCUS are ignored. A file of
  several records keeps the first, and `records` says how many there were
  (`_several_records` warns).
- **Saves:** an edit on the Sequence tab (`edit.json`, below), a whole map
  as JSON (`/plasmids/<id>/sequence-save`, for scripts), the hand edit, a
  file, and Clear. Each refuses anything but IUPAC letters (`is_iupac`)
  and an empty sequence.
- **Versions** (`app/plasmid_versions.py`, `plasmid_sequence_versions`,
  revision 0019): each of those records the state it leaves (`record`, with
  `how`: upload, editor, hand, clear, restore); a sequence from before
  version history is kept as `baseline` before its first change
  (`before_change`). One person's map edits within `EDITS_JOIN` (10 min)
  are one version, and the newest `KEEP` (50) are kept. The Sequence tab
  lists them with **Restore** (`/plasmids/<id>/versions/<vid>/restore`),
  which makes the old state the newest version. Versions point at the
  plasmid's row id with no foreign key, so undoing a plasmid's delete
  brings them back.
- **Made from** (`app/plasmid_lineage.py`, `plasmid_parents`, revision
  0020): a child's parents, each a plasmid row (`parent_row_id`) or a
  label from outside the lab, with a `role` (backbone, insert, template,
  donor, other), a `method` and `details_json` (what an assembly used:
  enzymes, coordinates, primer ids). `add_parent` refuses a plasmid as its
  own parent and a loop; an empty Backbone or Insert takes the parent's
  name. The page shows Made from and Used to make (child plasmids, and
  inventory rows whose plasmid column names it) above the map, and
  `tree()` up to `TREE_DEPTH` generations each way on Properties. Row ids
  without foreign keys, as for versions. The assembly wizard records its
  product's parents through `add_parent`.
- **Primers** (`app/primer_records.py`): each save of the map gives every
  primer annotation a record in the lab's Primers database
  (`sync_from_map`, made with it only by someone who may add lab
  databases), with the plasmid's number in its `template` (**Plasmid**)
  column and status *to order*; the annotation keeps `inventory_id`.
  `save_primer` matches the plasmid plus sequence, then name, so repeated
  autosaves and the assembly wizard's primers never duplicate; taking a
  primer off the map leaves its record. `binding_sites` needs `MIN_ANCHOR`
  (15) 3′ bases to match and reports the 5′ tail. The plasmid's Primers
  card and `/plasmids/<id>/primers.csv`, and the Primers sheet's
  `/inventory/<key>/order-sheet.csv|txt` (ticked `selected_ids`), give
  `order_sheet` (Name, Sequence, Scale, Purification) and `order_lines`.
  Used to make leaves Primers databases out. **Find saved primers**
  (GitHub #62): `/plasmids/<id>/primer-sites` (`app.primer_sites`) checks
  every record of every Primers database the person may see
  (`saved_primers`: `list_modules` plus `lab.can_see`, so not someone
  else's own) against the plasmid. `TemplateIndex` maps each 15-base
  stretch of the top strand (across the origin when circular) to where it
  starts, looks up each primer's 3′ 15 bases and their reverse complement,
  and extends the hits with `_site`, the code `binding_sites` uses, so the
  sites are the same (tests/test_primer_scan.py checks them against
  `binding_sites` by brute force); 5,000 primers on 15 kb take about 10 ms
  of matching, the endpoint about 0.1–0.2 s. Primers under 15 bases or
  with other than ACGT in their 3′ 15 are passed over. The JSON's `hits`
  are one per site, 1-based, with `exact`, `tail`, `annealed` and
  `linked` (its Plasmid column names this one); records the Primers card
  lists are counted in `listed`, not repeated, unless `?all=1` (a
  record's read-only page, `inventory/sequence.html`). `static/primer-scan.js`
  lists them, each one's place a link that opens the Sequence tab on it
  (`BioSeqViewer.select`); the viewer already draws every lab primer that
  binds. `_clean_features` still drops any mark whose id starts
  `found-primer-` that a script sends, so none reaches `features_json` or
  `sync_from_map`.
- **Assembling one** (`app/cloning.py`, the pure functions; `app/cloning_routes.py`,
  the pages, at `/plasmids/assembly`): a tray of fragments taken from the
  lab's plasmids — a whole one, one feature, a region, or a piece a digest
  leaves — put together three ways. **Digest and ligate** cuts with the
  enzymes of `ENZYMES` (a recognition site and the two strands' cut
  offsets, as a catalogue writes them: `EcoRI G^AATTC`, `BsaI
  GGTCTC(1/5)`) and joins ends that fit; **Gibson / HiFi** joins fragments
  that already share 15–60 bases where they meet, and designs the primers
  that add that homology where they do not (`design_gibson_primers`, a 25
  base 5′ arm copied from the neighbour plus what anneals, sized to 60 °C
  by `inventory_service.primer_tm`); **In-Fusion** is the same overlap
  assembly told apart only by its shorter designed arm (`INFUSION_ARM`,
  15 bases), with the vector a cut fragment that gets no primer; **Golden
  Gate** takes the pieces a Type IIS enzyme releases (`released`: the ones
  its site has left) and refuses an overhang used twice, one that is
  another's reverse complement, or one that reads the same on both strands.
  A cut is `(at, overhang)` — the bond the top strand breaks at, and how
  far the other strand's nick is from it, signed — and a fragment keeps its
  top strand between two such nicks with each end's overhang written as the
  top strand reads it, which makes a product the fragments' sequences one
  after another and makes two ends fit when their words match. `flip` turns
  a fragment round, ends and features with it. Everything is pure: a
  problem comes back as `{kind, at, next, …}` and `status_text` turns it
  into the one sentence the page shows, which names the end that is wrong
  ("Fragment 2's 3′ end has no overlap with fragment 3") rather than
  saying the assembly failed. `/plasmids/assembly/preview` answers the tray
  with the junctions, the product's features and that sentence after every
  change, so none of the biology is in `static/assembly.js`, which draws
  the tray and a ring map of the product. The fragment picker asks
  `/plasmids/assembly/source/<n>` what can be taken off one plasmid: its
  features, and `cutters_of` — only the enzymes with a site in *that*
  plasmid, with how many and where, fewest sites first, since forty enzymes
  of which most do not cut is a list to read rather than a choice to make.
  Picking enzymes draws each piece on a ring of the plasmid it came off,
  the cuts marked, so you can see which part would come out before taking
  it. **Create** makes an ordinary
  plasmid: the fragments' features at their new coordinates
  (`carry_features`, which keeps the part of a feature a cut runs through
  and notes it), a `part` annotation per fragment so the map shows the
  joins, a version (`how="assembly"`), `add_parent` per fragment with the
  enzymes, coordinates and primer ids in `details_json`, and the library's
  elements marked if asked. A Gibson's primers go to the Primers database
  through `save_primer` against the plasmid each amplifies and are drawn on
  that plasmid's map where `binding_sites` says they bind (`how="primers"`);
  a map that is not yours to edit keeps the records but not the drawing.
- **Gateway** (`app/gateway.py` pure, `app/gateway_routes.py`,
  `static/gateway.js`), a method in the same wizard but its own panel
  because it recombines two whole plasmids, not a tray. att sites are read
  from each plasmid's own annotations (`att_sites`: features named
  `attB1`…`attR2`); `reaction_of` sees BP (a B molecule with a P) or LR (an
  L with an R). The insert donor's stretch between its two att sites (the
  gene) goes onto the other's backbone, each junction spliced at the core
  the two att sites of a number share (`_longest_common`, ≥ `MIN_CORE`), so
  it reads through with no seam whatever the exact arms in hand; the sites
  are relabelled by `REACTIONS` (B+P→L on the product and R on the
  byproduct; L+R→B and P). `recombine` returns the product, the byproduct
  and what the reaction removed (a ccdB cassette). `/plasmids/assembly/
  gateway/preview` answers two plasmid numbers with both products, and
  **Recombine** makes the entry or expression clone, `add_parent` for each
  parent (method `gateway`), version `how="assembly"`. `ATT_SEQUENCES` holds
  canonical att sites for the Feature library and the demo lab.
- **Files** (`plasmid_files`, revision 0022): what belongs with a plasmid
  — a sequencing read, a gel photo, a datasheet — saved by
  `services.save_uploaded_file` into the uploads folder, with `kind` from
  the name (`_file_kind`: trace, image, document). Anyone who may edit the
  plasmid adds them; whoever added one, or an admin, takes it off, and the
  file itself stays in uploads, as the notebook's do (the lab sees it at
  `/static/uploads/` while it is the plasmid's, the admins after:
  `app/uploads.py`). Export my data
  carries them under `plasmids/<n>-<name>-files/`. Row ids without foreign
  keys, as for versions.
- **Verify sequencing** (`app/seqverify.py`, pure; `static/seq-verify.js`
  draws its map strip and small traces, for the Verify tab too): Sanger
  reads checked against the map. `read_file` takes an
  ABI chromatogram (ABIF: the 128-byte header's root entry points at a
  directory of 28-byte entries, data of 4 bytes or less sitting in the
  offset field; PBAS2/PCON2, else PBAS1/PCON1; a MacBinary header is
  skipped) or FASTA/plain text, and refuses any other binary in words
  (`ReadError`). `trim` is Mott's algorithm on the quality (cutoff 0.05), or
  the Ns at a text read's ends. `Target` indexes every 12-base word of the
  plasmid once per call (the start repeated after the end when circular, so
  a read across the origin is one stretch); each strand of the read votes
  for diagonals with its own words (a word seen more than `MAX_HITS` times
  doesn't), the densest `SPAN` (60) window wins, and `_banded_local` is
  Smith–Waterman with affine gaps only in that band ± `BAND_PAD`: a 1 kb
  read on 15 kb takes about 20 ms. Each difference gets the plasmid
  position (1-based; an insertion is "after"), the read's quality there
  (`confident` at Q20 or more), the map's features (not primers or parts)
  and, in a CDS (`codon_start` honoured, joins followed, either strand),
  `_substitution_effect` with the mismatches of one codon taken together
  (the standard code, `CODE`) or a frameshift / in-frame indel. An .ab1's
  trace (`_abif_trace`: DATA9–12 in FWO_'s order, PLOC2 peaks, kept only
  when they line up with the bases) gives each difference a `trace`
  (`trace_window`: ±`TRACE_FLANK` bases, or the `flank` asked for, at most
  `TRACE_POINTS` samples a dye for the usual flank) and the read a `mixed` when `second_peaks` (another dye at
  `MIXED_RATIO` of the called one) marks `MIXED_SHARE` of its good bases,
  with `from_read`/`from_plasmid` when `mixed_from` finds them starting at
  one place; the API and the assistants get the report `without_traces`
  (`has_trace`). `verify`
  adds the clone: coverage runs, features covered whole or in part, the
  differences merged across reads (`reads`, `strands`, `reads_covering`)
  and one `verdict` sentence, in the page's language. Three ways in, none
  changing anything: `POST /plasmids/<id>/verify` (the page: chosen files,
  `reads_text`, or `kept` file ids; with `keep` and edit rights the chosen
  files become `plasmid_files` rows whose `notes` hold the verdict),
  `POST /api/v1/plasmids/<n>/verify` (in `COMPUTING`, so any token:
  multipart files, or JSON `reads` and `captures`), and the assistant's
  `verify_sequencing`, which sends capture ids (an `.ab1` capture is
  `application/x-abi`, kept as it is; `get_capture` only says what it is)
  or text reads.
- **The plasmid page's tabs.** Overview · Sequence · Digest · Verify ·
  Primers · Storage · Properties, each a `plasmid-tab-panel`; the app draws
  the plasmid itself (`static/plasmid-map.js`: `BioPlasmidMap.circular`
  for the maps, labelled on Overview, small in the List, with cuts and
  pieces on Digest; `BioPlasmidMap.gel` for the gel; colours from
  `static/plasmid.css`, the gel dark in both themes), and edits it on the
  Sequence tab; nothing third-party draws or edits a plasmid. Stored
  features are 0-based with the end included.
  - **Overview** (`static/plasmid-overview.js`): the labelled map from the
    page's own features, saved as SVG (computed styles written in) or a
    3× PNG; the plasmid's facts; a feature row opens Sequence on it
    (`BioSeqViewer.select`). **Compare with…** (`app/plasmid_compare.py`,
    `app/compare_routes.py`) reads this plasmid as a synthetic `Read`
    against the other's `seqverify.Target`, so the banded local aligner
    matches them whatever their origins or strand and reports each
    difference with the feature it hits and its protein effect
    (`check_read`, reshaped B→A); a net indel wider than the aligner's
    band shows as the aligned stretch ending early (for near-identical
    plasmids, every change is caught). `compare.json` and
    `compare/others.json` serve the Overview card, which draws both rings
    with the differences marked.
  - **From Addgene or NCBI** (`app/sequence_fetch.py`,
    `app/sequence_fetch_routes.py`, `templates/_sequence_fetch.html`,
    `static/sequence-fetch.js`), in New plasmid and Replace the sequence:
    `parse_ref` reads an Addgene number (`Addgene #11150`, a bare number)
    or an accession; `fetch` gets GenBank text from E-utilities' efetch
    (`gbwithparts`) or the first `.gbk` Addgene's sequences page links
    (Addgene's own full sequence ahead of the depositor's), at most `MOST`
    bytes. `fetch-sequence?ref=` answers with the text and what
    `parse_genbank` finds in it, and the page drops the text in the form's
    paste box, so creating or replacing reads it as pasted GenBank. Any
    failure is one `FetchError` code said politely (offline: download the
    file and upload it); `BIOMANAGER_SEQUENCE_FETCH=0` hides it and reaches
    nothing (`sequence_fetch_on()` in templates, passed through
    `deploy/compose.yaml`). Tests pass their own opener.
  - **Sequence** (`static/seq-viewer.js`, `app/sequence_routes.py`):
    `view.json` gives the bases, features, single cutters
    (`digest.enzyme_table`), the lab's primers (`lab_primers`:
    `primer_design.binding` with up to two mismatches, the 3′
    `ANCHOR` bases pairing), each CDS's protein (`protein.about`) and the
    ORFs of 100 codons or more (`protein.orfs`). Rows are laid out first
    (bases per row from the measured width of the mono font) and drawn as
    SVG when they scroll near (IntersectionObserver); a ResizeObserver lays
    them out again. Dragging picks a stretch; `primer-check` checks it as
    a primer (`primer_design.check`: Tm by the Primers database's nearest
    neighbours, GC, clamp, hairpin and self-dimer ΔG from the same table,
    sites) with the lab's same or nearby primers and a name
    (`_suggest_name`), and `primers/new` saves it through
    `primer_records.save_primer` and `derive_primer`.
  - **Editing** (`app/sequence_edit.py` pure, `edit.json` in
    `app/sequence_routes.py`, `static/seq-viewer.js`), on a plasmid the
    person may change (`access.can_edit`; the viewer's `data-edit`): one
    operation per request — `bases` (start, end, the new bases: typing over,
    an insertion when end = start − 1, a deletion when empty), `add_feature`,
    `edit_feature` / `delete_feature` (by `at`, the feature's place in the
    stored list, which `view.json` gives beside each feature), `topology`,
    and `state` (Undo: the whole state the previous edit's answer handed
    back as `before`, cleaned by `_clean_features`). Annotations move with a
    base edit by `mutagenesis.shift_features`, so a primer mark it touches
    goes. Each request carries `base`, the `state_hash` the page loaded,
    and is refused (409) when someone else has changed the plasmid since;
    each saved edit is `how="editor"`, joined with the same person's edits
    within `EDITS_JOIN` into one version, while Undo steps through every
    edit of the page's session. Keys: a base letter with bases picked opens
    Edit with it, Delete removes them, ⌘Z undoes. `templates/_seq_viewer.html`
    is the viewer for both the plasmid page and a record's read-only page.
  - **Mutagenesis** (`app/mutagenesis.py` pure, `app/mutagenesis_routes.py`,
    the Sequence tab's **Mutagenesis…**): a `Change` is `at` (0-based), `cut`
    bases and the `new` ones. `design_primers` makes Q5-style back-to-back
    primers on a circular plasmid: each anneals for `cloning.anneal_length`
    (TARGET_TM) from the change outwards; the new bases ride on the forward
    primer's 5′ end, or split over both from `SPLIT` bases. `shift_features`
    moves features after the change, stretches one it runs through, drops
    one it removes and any primer mark it touches; `effects` translates
    each coding feature before and after (`describe`: K80A, K80*, L3del,
    K2_L3insGS, …delins…, F101Qfs*151, reading on past the feature for a
    frameshift's stop). `codon_change` turns K80A into bases (`best_codon`:
    fewest changes, then `PREFERRED`); `silent_sites` tries one codon or two
    side by side (at most `MOST_CHANGED` bases) for sites of the non-Type IIS
    enzymes appearing or going, near the pick. `mutagenesis.json` designs,
    `silent-sites.json` lists, and `POST mutagenesis` makes the plasmid (the
    change marked as a `misc_difference`, version `how="mutagenesis"`, a
    `plasmid_parents` link with role `template` and method `mutagenesis`,
    the primers saved with category `mutagenesis`).
  - **Digest** (`app/digest.py` pure, `app/digest_routes.py`,
    `static/plasmid-digest.js`): `digest.json?enzymes=&agarose=&ladder=
    &methylated=&lane=` answers the cuts (`cut_positions`, the cut written
    as the top-strand base it follows), the pieces and the features wholly
    in each, the gel (`position`: log size over each agarose's range,
    bunching beyond it; `bands` merges pieces closer than `ONE_BAND`;
    brightness by mass; the uncut plasmid supercoiled at 0.68× and nicked
    at 1.7×), notes, and `suggest`: one or two enzymes of 1–3 cuts leaving
    2–4 pieces at least `WELL_APART`, scored for the freezer
    (`in_freezer`, the lab's Reagents names: EcoRI-HF counts), fewer
    enzymes and, when Made from names a backbone with a sequence
    (`_backbone`), a pattern unlike its own. Methylation: Dam's adenine in
    GATC and Dcm's inner cytosine in CCWGG, on either strand, block the
    sites of `DAM_BLOCKED` / `DCM_BLOCKED` enzymes they fall in. Kept
    lanes, agarose and ladder are per plasmid in localStorage; a lane may
    be `pcr:<bp>:<label>` (`BioDigest.addLane`, from the Primers tab).
  - **Verify** (`app/clone_routes.py`, `plasmid_clones`, revision 0053,
    `static/plasmid-verify.js`): reads are grouped by the clone their file
    names (`clone_of`: `C4_…`, `clone3-…`, `colony 2 …`), else the next
    number or the one typed, checked by `seqverify.verify` with a
    `TRACE_FLANK` of 20, kept as `plasmid_files` (notes: the clone) and
    remembered as a `PlasmidClone` (status, verdict, the report without
    traces, its file ids; a clone sequenced again is checked with all its
    reads). Opening one checks its kept files again, so its traces come
    back. **Accept** sets `accepted_at`; `verified()` is the newest
    accepted, shown in the header, on Overview and in the List. Someone
    who may only view the plasmid checks without keeping. The older
    `POST /plasmids/<id>/verify` stays for the API and the assistants.
  - **Primers** (`static/plasmid-primers.js`): `view.json`'s primers on a
    strip and in a list (exact, or with mismatches); an F and an R make
    `pcr.json` (`primer_design.pcr`: the nearest facing pair, tails
    included; what the backbone gives; anneal at the lower Tm − 3, extend
    at 1 kb/min), sent to the gel as a lane. `sequence_next` names the lab
    primers either side of the insert (the longest CDS that isn't a
    resistance gene).
- **One area, four tabs:** `PLASMID_TAB_KINDS` (primers, glycerol_stocks,
  viruses) are left out of the sidebar rail by `_inventory_module_links`
  and put in `g.plasmid_tabs`; `_plasmid_tab_strip` adds Plasmids itself,
  and `_plasmid_tabs.html` draws the strip on the Plasmids page and on
  those sheets (only where one of them is the page you are on). The rail
  marks Plasmids active on all of them, and names it `MOLECULAR_BIOLOGY`
  ("Molecular biology") whenever there is a tab beside it, unless the lab
  renamed Plasmids; the first tab keeps the database's own name.
- **Feature library** (`app/feature_library.py`, `feature_library`,
  revision 0021): named elements by sequence (as the feature reads 5′→3′),
  one per `seq_hash`, sorted into `CATEGORIES` by `category_of` (name and
  GenBank type; viral elements by name). Entries come only from the lab's
  own maps (`add_from_plasmid`, Add to library; `collect`, an admin's
  Collect from every plasmid), never built in, so a mistyped base can't
  label every map. `detect` finds exact matches of entries of at least
  `MIN_LENGTH` on both strands and across the origin, skipping a span
  already annotated or overlapping a feature of the same name;
  `/plasmids/<id>/detect-features` appends them and records a version
  (`how="detect"`). The library page is `/plasmids/features`;
  `/plasmids/features/import` reads elements straight from uploaded
  annotated files (`add_from_sequence`, which `add_from_plasmid` also
  uses), making no plasmid, so a lab fills the library from the maps it
  already downloads. The page shows the first 100 of each kind and
  searches by name, type or sequence (`?q=`), since a lab with the pack
  has a couple of thousand.
- **pLannotate** (`app/plannotate.py`): a lab that installs pLannotate
  (`mamba install -c bioconda plannotate`, then `plannotate setupdb`) and
  sets `BIOMANAGER_PLANNOTATE` to its command gets **Annotate with
  pLannotate** above the map, which finds elements by alignment — a
  codon-optimised CDS, a promoter a base off — where the library matches
  exactly. `run_tool` writes a FASTA into a folder of its own, runs
  `<command> batch -i … -o … -f plasmid` (plus `--linear` for a linear
  plasmid) with `subprocess.run`, a list and never a shell, and reads the
  `.gbk` it wrote back through `sequence_parser.parse_genbank`; `find`
  then drops what the map already marks with that name, as Detect features
  does, and the route records a version (`how="annotate"`). It is a tool
  this app runs, never imported, so its GPL-3 does not reach this code,
  and its databases stay the lab's own download. The command is an
  environment variable rather than a setting on purpose: it is a command
  this server runs, so whoever installs the server decides it, and getting
  into an admin account is not a way to run code on the machine. The
  timeout (45 s) sits under the 60 s a lab server allows a request.
- **The common-features pack** (`app/feature_pack.py`, with revision
  0023's `feature_library.source_name`): GenoLIB (Adames et al., *Nucleic
  Acids Res* 2015;43(10):4823-4832, doi:10.1093/nar/gkv272, CC BY 4.0),
  about 1,900 elements with their DNA. The app carries no copy: an
  admin's **Add the common-features pack** downloads the article's SBOL
  supplement from Europe PMC (a zip of zips, `labhost_All.xml`), so the
  lab obtains it from the archive rather than BioManager redistributing
  it. Worth knowing when reading that paper: its authors built the library
  by exporting a commercial program's published annotated files, which is
  why the page says to check anything you rely on and why each entry keeps
  `source_name`. The download takes about a minute, longer than a lab
  server allows a request (`gunicorn.conf.py`: `timeout = 60`), so `start`
  runs it in a thread and keeps its state in `app_settings`
  (`feature_pack_status`), where every worker can read it; the page shows
  "Downloading…" and refreshes itself. Adding two thousand elements uses
  `feature_library.add(known=…)`: one read of the library's hashes instead
  of a query and a flush each.
- **A record's plasmid, to read:** `/inventory/<key>/items/<id>/sequence`
  (`item_sequence`) mounts the same editor read only on the plasmid a
  record's plasmid column names — a virus's payload, a glycerol stock's
  plasmid — from `/plasmids/<id>/sequence.json`; the sheet's plasmid cell
  links to it when that plasmid has a sequence. Nothing there saves.
- **Chemicals:** a `chemicals` database keeps `abbreviation`, `cas`, `mw`,
  `formula`, `purity` and `density` in `attrs`. `lab_notebook.chemicals_search`
  (`/notebook/api/chemicals`, for the Formulation block) reads them from
  every Chemicals database the person sees and any other inventory with an
  `mw` column: name, those columns, lot and catalogue number, exact match
  first, then a start, in-stock bottles before used-up ones, then the
  built-in `chemical_references`. The `@` menu (`_mention_items`) also looks
  in `attrs`, through `formutil.attr_value_pattern` (a JSON value that
  starts with what was typed, so a column's name is not a match), and a
  chemical's popover lists those columns after its name. An order of one
  is a `reagent` (`STOCK_CATEGORY`).
- **Glycerol stocks:** a `glycerol_stocks` database's *Plasmid* column
  says which plasmid the bacteria carry. `made_from_plasmid(kinds=…)`
  finds them for the Storage tab's Glycerol stocks card (with the box,
  `rack_label`), and `NOT_MADE_FROM` keeps them, like primers, out of Made
  from this plasmid and Used to make.
- **Out:** `to_genbank` / `to_fasta` (`sequence_parser.py`) write what
  `parse_genbank` reads back the same: features and primers (as
  `primer_bind`) with every qualifier, a wrap across the origin as a join.
  `/plasmids/<id>/download.gb` and `.fa`, a `plasmids/<n>-<name>.gb` per
  sequence in Export my data, and the API's one-plasmid answer carries
  `annotations`.

## Calendar repeats and bookings

(The page: `templates/calendar.html` puts Back / Today / Forward, the view
switch and **New event** in the title row (`page_actions`), and
`static/calendar-page.js` writes the month or range into the title, a
week's as "October 2026 · Oct 25 – 31". Week and day are TOAST UI drawn
through its templates: `weekDayName` (the weekday and a date circle,
today's filled), `alldayTitle` "Due" with each item a pill and a ring
(`chip()`), `timegridDisplayPrimaryTime` and `timegridNowIndicatorLabel`
in 24-hour mono, `time` a block of title and hours (`timedBlock`), and the
theme's now line red. A click on any item, in every view, is the page's
own listener (the calendar's `clickEvent` came only on a double-click in
Month and never on a read-only item): `peek()` shows the quick look
(`.cal-peek`, fixed beside the item: kind, owner, the `#n` mice its title
names as links, its note, Mark done for a to-do, Open → `openOnce`). The
hover card is the List view's only. The small month bands the week on
show.)

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

### The phone feed and the Mac's Calendar

`lab_calendar.feed_items(s, user, scope)` is what a feed holds: `calendar_items`
read as its owner, for `mine` (their own things) or `lab`, from
`FEED_BACK_DAYS` ago to `FEED_AHEAD_DAYS` ahead, without connected
calendars. `/calendar/feed/<token>.ics` renders it (`render_ics`), and
`app/mac_calendar.py` writes it into the Mac's own Calendar, for a lab with
no server that Google could fetch the feed from.

`mac_calendar` runs in the desktop app on a Mac only (`offered()`:
`LOCAL_SETUP`, `devices.on_this_computer()`, and EventKit importable from
`pyobjc-framework-EventKit`). `EventKitStore` wraps EventKit; tests swap in
a fake with `use_store()`. The setting is one per lab, `mac_calendar` in
`app_settings`: `{user, scope, calendar, source, title, account, created,
last_ok, count, error}`. `update(app)` runs in an app and request context
of its own (its `g.user` is the calendar's owner, its `g.lang` theirs),
turns each item into an `Entry` (title, all day, start, end, notes: dates
for an all-day event, the last day included, else Unix seconds), reads the
events already in the calendar the same way, and `plan()` matches the two
as multisets: what is wanted and missing is added, the rest of what's
there is removed, one commit. No ids: EventKit's change when an account
syncs afresh. Events that started before the window are left alone. Since
everything else in the calendar is removed, `_start` takes only a new
calendar (`new:<source>`, `created`) or an existing one whose title starts
with BioManager (`cal:<id>`, `dedicated()`); a Mac can't add a calendar to a
Google account, and EventKit's refusal becomes the message telling the
person to make one on the web. **Stop** removes a calendar it created and
empties one it didn't. `start_background()` (from `desktop.py`) updates a
minute after start, every `UPDATE_EVERY`, and `SETTLE` seconds after any
request that changed something (`_after` sets `_nudge`). The packaged app
needs `NSCalendarsFullAccessUsageDescription` and
`NSCalendarsUsageDescription` in `Biomanager.spec`'s Info.plist (macOS
stops an app that asks without them) and
`com.apple.security.personal-information.calendars` in
`desktop/entitlements.plist` for the hardened runtime.

EventKit names the Mac's own account (`EKSourceTypeLocal`) "Default";
`EventKitStore._account` shows it as On My Mac, as the Mac's Calendar does.
A timed event goes in with UTC as its time zone: in the Mac's own zone,
macOS 14 hands back an event that runs across the night the clocks go back
an hour longer than it went in (15 doesn't), so `plan()` would replace it
at every update and the Mac would show it ending an hour late. Calendar
still shows it in the Mac's time. All-day events have no zone.

The stand-in can't show how EventKit keeps dates, so the real one is
checked on GitHub's Macs. `scripts/mac-calendar-check/live.py` runs
`TheStore` (an `Entry` of every kind, a multi-day event, Chinese, notes with
Windows line ends, two the same, the night the clocks change, read back by
a fresh store and matched by `plan()` with nothing to add or remove: else
every update would empty and refill the calendar) and `TheApp` (start in On
My Mac, an update with nothing new changes nothing, a deleted item and one
put there by hand go, the whole lab, stop) against the real calendars, in
Los Angeles, Shanghai and UTC (`.github/workflows/mac-calendar.yml`, on a
change to the code it uses). The release workflow's Mac jobs then run
`in-the-app.py` on the app just built: on a demo lab, through the app's own
`/calendar/mac`, it must fill a BioManager calendar in On My Mac with as
many events as it says and take it away on Stop. A CI Mac can't click
Allow, so `grant.py` writes the permission into macOS's TCC databases for
the app's bundle id and for whatever runs the step (`chain.sh`); GitHub's
Macs have SIP off, which lets it. The prompt itself is the one part left
untested.

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
PostgreSQL). The snapshot's `X-BioManager-Lab-Recipient` header is the lab
key's public half: `pull()` locks the copy (`….db.age`, with a `.json`
sidecar) and each mirrored file (`.age`, sizes in `uploads/.sizes.json`) to
it and to this computer's own key (`_recipients`), so a member's computer
reads its copy and nobody else's can; `open_copy`/`open_uploads` unlock.
A device that joined (below) takes its copy with its device token instead
of a copy key (`_authorised` returns its `LabDevice`; the rate limit is
`last_copy_at`).

## One master copy, many devices

`app/devices.py`, the Settings → Devices & copies → Devices page. One database is the lab's
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
non-admin's key). **Open the lab in this window** joins the lab as a device
(below) and sets the desktop prefs' `window_url` to `"relay"`; `desktop.py`
then opens the relay instead of the local app. The window never opens a
server's address itself: a move to a server (`lab_move._moved`, with the
device `door.take_in` makes for its admin), a give-back and an old
`window_url` all go through joining. Go → This Computer's BioManager clears
it; Go → Your Lab's BioManager opens the relay again.

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

## The lab's key

`app/lab_key.py`. Each lab has an age X25519 identity (pyrage), kept in
`lab_keys` (`secret` is `EncryptedText`, so it opens only with this
device's `SECRET_KEY`). The newest `active` row locks; every row is kept so
older files still open. `lab_transfer.build` locks a `.biomanager` file to
it (`age-encryption.org/v1` header), `open_file` tries the keys given (a
typed recovery key, the `X-BioManager-Lab-Key` header a desktop sends when
it moves a lab) and this device's own, and `door.take_in` adopts the key a
file opened with. `lab_keys`, `lab_devices` and `device_pairings` are
`MACHINE_TABLES`: kept across `load_lab` and `lab_transfer.apply`. The
recovery key is the `AGE-SECRET-KEY-1…` string (checksummed), shown under
Settings → Your data → Lab key after a password check (`lab_move.lab_key_page`,
which also adopts one). On a server with `BIOMANAGER_LABKEY_DIR`,
`export_if_asked()` writes `lab.pub` and `lab.key` (0600) into the `labkey`
volume at start-up; `deploy/backup/labkey.sh` locks each dump and tarball
with `age -R lab.pub` and opens them with `lab.key` or
`BIOMANAGER_RECOVERY_KEY`. The key is never in a backup.
`tests/test_lab_key.py`.

## Devices that join the lab

`app/lab_devices.py`. Every device is a `lab_devices` row: an app (desktop,
phone) with a `bmd_…` token (only its SHA-256 kept) sent as
`Authorization: Bearer`, or a browser sign-in (`kind="browser"`, keyed by
the session `sid`'s hash: `note_browser` at sign-in, `browser_ok` on every
request, so signing one out from another device ends that session). An app
joins with `POST /api/devices/join` (username and password, throttled), with
a code (`POST /api/devices/pair` gives an `XXXX-XXXX` code and a `bmp_`
secret; someone signed in approves it at `/settings/devices/approve`; the
app polls with `Authorization: Pair …` and gets its token once), or, for the
phone apps, from their signed-in page (`POST /api/devices/this-app`, the
app's User-Agent, same-origin). `authenticate()` (from
`app.load_current_user`) signs a token's requests in as its person and sets
`g.device`; `security.cross_site_reason` exempts `bmd_` bearer requests,
which no form can send. An admin's join answer carries the lab's key (for
their desktop's copies and hand-overs); a member's doesn't. Guests can't
join. Settings → Devices & copies lists `listing()`: your devices, and for
admins everyone's.

The desktop side is `app/desktop_join.py` (the token, encrypted, in the
desktop prefs `joined_lab`; the lab's key adopted inactive; the copy
settings pointed at the device) and `app/relay.py`: a threaded werkzeug
server on `127.0.0.1` (port kept as `relay_port`) that forwards each
request to the server with the token, keeps the server's cookies in its own
jar, rewrites `Location`, and refuses another Host (421) and cross-site
writes (403), the same check the server makes. `/logout` becomes
`/_relay/leave` (Sign this computer out of the lab?), which revokes the
device and forgets the lab. `tests/test_relay.py` runs it against a real
server thread.

The relay keeps its connections to the server open and uses them again
(`Relay._take` / `_keep`, at most `KEEP_AT_MOST`), with one TLS context
for all of them: a new connection for every request cost a TLS handshake,
two or three round trips to the server and some 20 ms of reading the
trusted certificates, for every page, script and autosave (20 requests to
an HTTPS server on the same computer: 960 ms before, 11 ms now). A kept
connection is used again only while it is open (`_closed`: an idle one has
nothing to read, so one that can be read from has ended) and for
`IDLE_REUSE` (30 s); an answer that closes its connection, or one not read
to its end, is not kept. A request that fails on a kept connection goes
again, once, on a new one: a server closes an idle connection only between
requests, so the first never reached it. A body up to `REPLAY_MAX` (2 MB)
is held for that; a larger one (an upload) always gets a new connection.
`KeptConnections` in `tests/test_relay.py` counts the connections a test
server is opened with.

## Working offline

The offline design: devices record what is done with no signal as a queue
of actions (`app/actions.py`), and the server replays them through the
pages' own routes. Nothing is merged; the lab keeps one master copy.

- **Versions.** `app/sync_clock.py`: every record a device keeps
  (`SYNCED_TABLES`: mice, cages, litters, weights, tasks, racks, vials,
  tanks) has `sync_version`, set in a `before_flush` hook from one lab-wide
  count (`sync_clock`, advanced by an UPDATE that PostgreSQL holds locked to
  commit, so numbers follow commit order). Deletions go to
  `sync_deletions`. The hook is off before Alembic has run
  (`services.init_database`).
- **The feed.** `app/sync.py`: `GET /api/sync/snapshot` (as the person may
  see it: the colony, living mice and those dead in the last 90 days, the
  last five weights, their to-dos for 14 days, vials of the stock databases
  they may open, tanks, dropdowns, weaning due) and `GET /api/sync/changes?since=`
  (rows above the cursor, rows they may no longer see as `gone`; `reset`
  past 20 000). A cage's row carries its litters not yet weaned
  (`sync.cage_litters`: `born`, `pups`, `dam`, `litter`, `wean_due`,
  `genotyping_due`), and `sync_clock` counts a change to a `cage_litters`
  row as one to its cage, so a litter born, corrected or weaned elsewhere
  reaches the devices. The offline screens ask which litter as the pages
  do, sending `litter` (a birth date).
- **The queue.** `POST /api/sync/push`: ops in order, each `{op, made_at,
  cursor, action, target, fields, base, creates}`. A `sync_ops` row claims
  the op id first, so a resend answers the stored result. `_check` compares
  `base` (`"mouse:12": {"v": …, field: …}`) with the record: same version
  fits; moved on with other fields changed merges; the same field changed
  to something else is held (`held_changes`), with per-action rules (both
  dead agree, a move to where it is is done, a second litter at genotyping
  is held, a weight that day by someone else is held, a wean whose pups
  moved is refused). Later ops about a held record, or naming its temporary
  ids, answer `waiting` and are sent again. Fitting ops run
  `actions.run(..., apply=True)` as their person, one batch each ("Done
  offline on …"); `creates` maps `tmp:` ids to the records made, by table
  and order. `task_done` is the one new action.
- **Deciding.** `/decisions` (Needs your decision, in the account menu once
  something is held; Home reminds after three days) and
  `POST /api/sync/held/<id>`: Keep mine replays the change as an ordinary
  edit, Keep theirs and Edit drop it. Refused ops of the last 30 days are
  listed there.
- **The screens.** `app/static/offline/` is one bundle for every device.
  It talks to a store through fetch (`/_relay/store`, `/_relay/store/queue`,
  `/_relay/sync`, `/_relay/status`) on the desktop, or
  `window.webkit.messageHandlers.bmStore` / `window.BMNative` answered with
  `window.BMStoreReply` in the phone apps. Its words are
  `app/translations/zh/js-offline.json`.
- **Stores.** The desktop: `app/offline_store.py` (snapshot, queue and
  state encrypted with this computer's secret, refreshed every 5 minutes by
  a thread `desktop_join.start_relay` starts); the relay redirects a page
  it can't fetch to `/_offline/` once there is a copy. iPhone:
  `ios/BioManager/OfflineStore.swift` (Keychain token, files with Data
  Protection, `NWPathMonitor`, `BGAppRefreshTask`). Android:
  `OfflineStore.java` (private storage, a network callback, a 15-minute
  timer); Gradle copies the bundle into the assets.

`tests/test_sync.py` covers the rules; `tests/test_relay.py` the desktop
going offline and back.

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
reminder is made on a user's first page of the day, with
`daily_experiment_reminder` and `daily_welfare_reminder` (category
`welfare`: the counts of `welfare.checks()` losing weight, not weighed and
notes, in Home's scope for that person; the status check isn't told).
`notify.wants()`/`set_wants()` read and save each category's switch: a
`users.notify_<category>` column, or for those in `SETTING_SWITCHES`
(`welfare`) the app setting `notify_<category>:<username>`, `off` when
silenced, so a new category needs no column.

## The lab notebook

Pages are `notebook_pages` inside a person's `notebook_tabs` (topics);
everything added in the rebuild lives beside them, in its own tables
(`app/models.py`, below the lab calendar's), so existing databases need no
column changes. `app/lab_notebook.py` has the routes (`/notebook/api/…`) and
the rules; the editor is `frontend/src/` and the page around it is
`app/static/notebook-page.js` and `app/static/notebook.css`.

One sidebar, as Notion has: the notebook's `{% block rail_mode %}compact`
puts `data-rail-mode` on `<html>`, so base.html's boot script starts the
app's sidebar folded to its icons (shell.js doesn't save that as your choice
when it is toggled there). On a window 768px or wider the notebook's list
(`#nb-sidebar`) is moved, by the inline script after it, into `.shell-main`
beside the scrolling page (`.is-docked`: absolute from the window's top to
the bottom, so it never scrolls), and the tabs, the title row and its
frosted band start after it (`--nb-side-w`). In the Mac window the list
runs under the traffic lights (padded by `--tabbar-h`), and the folded app
sidebar runs to the top too, square, with no rounded corner beside it. A sticky list in the scrolling page was tried:
Chrome measures a sticky top from inside the scroll box's padding (the two
rows' height), so it slid down on a short page. Its head is the notebook's
name (Home), the one New page (`#nb-new-page`, into the chosen topic) and
hide; then one search (typing filters the list, Enter opens the search
panel with tags); Today and the libraries are its pinned foot. A topic row
(`<summary>`) is a named group out of the Tab order: its arrow
(`.nb-tab-fold`, `aria-expanded`) is the button that folds it, since the row
holds a link and buttons of its own.

The page is the paper itself (`.notebook-editor` without a box; the page
column is `--color-surface` from the title row down) beside its list. Its path and tools (`.nb-page-where`, `.nb-page-tools`)
are written in the page, and `notebook-page.js` (`liftHeader`) moves them
into the window's title row before anything is wired; every lookup is by
id or across the document. While a side panel is open the list folds away,
and the formatting bar centres on the paper (`frontend/src/toolbar.js`).
The list also hides on its own (`[data-side-toggle]`, Ctrl+\ / ⌘\):
`nb-side` in localStorage, read by an inline script before the page draws
(`#nb-shell.is-side-closed`, and `body.nb-side-is-closed` for the buttons
in the lifted path). The title is a `<textarea>` that grows with what it
holds (`fitTitle`; a pasted line break becomes a space), 40px over the
editor's 30/24/20px headings and 16px text (`frontend/src/styles.css`), as
Notion sets them.

**Topics, favourites, the home.** Topics nest: `notebook_tabs.parent_tab_id`
(none: at the top) and `position` among the topics beside it, with an
`icon` (`FOLDER_ICONS`, as the library folders; revision 0048).
`lab_notebook.topic_tree()` lists one person's topics in the sidebar's
order, each after the one it sits in (`depth`, `path`, `topics` inside,
`pages` as `page_tree()`), and `topic_parents()` puts at the top a topic
whose parent has gone or that sits round a loop. *Inbox* (`is_inbox()`: so
named, at the top) never holds a topic and leaves out what a library lists
(`library_page_ids()`: protocols, reading and seminar pages, and meeting
notes of a series; one of no series stays, as no library lists it). The
sidebar and the topic's page are drawn from that tree
(`templates/notebook.html`: `topic_node`, recursive).
`/notebook?tab=<id>` is the topic's own page (`topic_view`: the topics in it,
its top-level pages as `page_cards()`, `?sort=edited|name|order`), and
`/notebook` with nothing asked for is the home (`home`: favourites, the
eight pages most recently changed that the person may open, the top-level
topics); neither opens a page any more. Deleting a topic moves the topics
in it up one level. A card (`page_card`) is a sketch of its page:
`page_shape()` reads its Markdown as `sheet` (a ```sheet fence or a
table), `steps` (two numbered or checklist lines) or `text`, and the card
draws a table's bands, a list's dots or lines (`.nb-thumb`); its kind as a
pill, its topic's icon (`tab_icon`) and its gist as the link's tooltip.
Home's header has the New page capsule (`data-new-page-kind`, and
`data-new-kind` protocol, reading, meeting) and its topics are chips; New
topic there names one in a dialog. While the sidebar is hidden, its button
and + are one glass capsule (`.nb-side-capsule`): in a page's path, else
placed in the title row above. **Fit the window** (`#nb-wide-btn`) puts
`.is-wide` on `#nb-shell` (localStorage `nb-wide`, set before paint), which
lifts the paper's 860px limit.

`app/static/notebook-topics.js` runs the sidebar's topics, favourites and
dragging, without the editor bundle (it borrows `window.NotebookPage`'s
toast and times when they are there): **+** names a new topic in place
(`/notebook/tabs/create`, with `parent_id` for **New topic inside**); the
topic menu (`#nb-topic-menu`, outside the sidebar, `position: fixed`, so a
topic's page opens it on a phone too) posts `/notebook/api/topics/<id>`
(name and icon) and `/notebook/api/topics/<id>/place` (`parent_id`,
`before_id`/`after_id`: it refuses a loop and Inbox), or deletes the topic.
A topic's icon (`notebook_tabs.icon`, `app/topic_icons.py`) is one of
`FOLDER_ICONS` by name, an emoji (`emoji:🧪`, as the system's emoji picker
types it; `is_emoji`) or an SVG of the person's own (`svg:uploads/ti_<hex>.svg`).
The icon picker (`BioDialog.prompt` with `icons.emoji` and `icons.uploadUrl`,
`app/static/dialogs.js`) has **Icons · Emoji · SVG**; an SVG goes to
`/notebook/api/topic-icons`, where `clean_svg` keeps only drawing elements
and attributes (no script, `on…`, `<a>`, `<image>`, `<foreignObject>`,
DOCTYPE, or `href`/`url()`/`@import` leaving the file) and saves it as an
upload: the topic's row names it, so backups, lab copies and
`app/uploads.py` treat it as any other file. Templates draw any of the
three with notebook.html's `ticon` macro (an `<img>` for an SVG, where
nothing in it runs), the move menu with `topicIcon` in
`notebook-topics.js`.
A page dragged in the sidebar, or a card in **Your order**, posts
`/notebook/api/pages/<id>/place` (`tab_id`, `before_id`/`after_id`,
`parent_page_id`): `move_page()` brings its sub-pages, then the topic's
pages are numbered again. Favourites are `notebook_favorites` (username,
page, `position`): `/notebook/api/pages/<id>/favorite` (`on`) and
`/notebook/api/favorites/<id>/place`; `favorite_ids()` lists only pages the
person may still open, and `delete_page_rows()` takes a page's stars with
it. Which topics are open is `nb-open-topics` in localStorage, the cards
or list `nb-cards-view`. **New page** (`#nb-new-form`,
`window.NotebookNewPage.open({kind, tab})`) asks for a title, topic, kind
and what to start from: the `STARTERS` of that kind, an empty page (`kind`
alone to `/notebook/api/pages/new`) and the person's templates of that
kind; **Save as template** is its own small form (name, who can use it,
structure only).

| Table | Holds |
| --- | --- |
| `notebook_page_info` | kind (note, experiment, protocol, reading, meeting, seminar, daily), status, tags, start/finish, the protocol an experiment follows, a meeting's series and presenter, the live-editing generation, and `parent_page_id`: the page a sub-page was made in (revision 0028) |
| `notebook_shares` | who else may open a page, and whether to view or edit (`*` is the whole lab, guests excepted) |
| `notebook_topic_shares` | the same for a topic: its pages, and the topics inside it at any depth (`lab_notebook.topic_shares`, worked out once a request), open to them; `role_for` takes the better of a page's own share and its topic's, `shared_page_ids` adds the topics' pages, and a member's copy (`lab_copy.member_view`) keeps them. Revision 0050 |
| `notebook_versions` | the page's history: `auto` (one person's edits within 10 minutes, up to an hour, fold into one), `manual`, `release` (a protocol's v1, v2 …), `restore` |
| `notebook_sync_updates`, `notebook_presence` | live editing: Yjs updates and cursors (below) |
| `notebook_comments` | comments on a page or a quoted passage, and replies |
| `notebook_recipes` | the lab's buffer library (the built-in ones are `PRESET_RECIPES`), and the folder each is in |
| `notebook_favorites` | the pages someone starred, in their order (`position`; revision 0048) |
| `notebook_folders` | the lab's folders in the protocol and recipe libraries (`kind`, name, icon); a protocol's folder is `notebook_page_info.folder_id` (revision 0027) |
| `notebook_meeting_series` | a meeting's rotation (`members` in order, `next_index`), day and time |
| `notebook_templates` | a person's templates: title, Markdown, the page `kind` a page made from it gets, and `lab` (everyone may start from it; revision 0009) |

**Libraries.** Protocols, Recipes and Reading in the notebook's sidebar
are pages (`/notebook/protocols`, `/notebook/recipes`, `/notebook/reading`,
`NOTEBOOK_LIBRARIES` in `app/app.py`): the notebook's page with no page open
and `static/notebook-library.js` in the main column. It reads the same
`/notebook/api/protocols` and `/notebook/api/recipes` the drawer and the
recipe block do (`window.NotebookPage`). Protocols lists the protocol
pages and the pages tagged protocol (`tagged_protocol`, `PROTOCOL_TAGS`),
each with `topic` (None when it is kept in Protocols, that is in the
owner's Inbox, which leaves it to the library) and `can_keep`;
`POST /notebook/api/protocols/<id>/keep` brings one in (its owner's
Inbox, kind protocol). A page made with kind protocol, from anywhere but
a page's sub-page, goes to that Inbox too (`page_new`), as
`protocol_new` does. The library also draws the calendar's timelines
from `/calendar/protocols`, each linking to `/calendar?protocol=`. Meetings is kept in the calendar:
`/notebook/meetings` redirects to `/calendar?meetings=all`, and
`static/calendar-meetings.js` draws the series (side panel, New event →
Meeting, a dialog for each: rotation, notes for the next meeting, put on
the calendar, past notes) from the same `/notebook/api/meetings` routes in
`app/lab_notebook.py`; `?meetings=new` or `=<id>` opens one. A meeting's
calendar event ends with `Notes: /notebook/meetings/<id>/open?date=`, which
the quick look turns into a link. A recipe opens in the recipe block's editor
(`BiomanagerNotebook.recipe`, with `library: true`: no ticks, no Save to /
Load from library), which saves as it changes. `?folder=`, `?recipe=` and
`?preset=` say where in a library you are. A folder's icon is one of
`FOLDER_ICONS` (empty stored for the plain folder), picked in
`BioDialog.prompt`'s `icons` option. Folders are the lab's: anyone
but a guest makes one; their maker or an admin renames or deletes one
(what was in it stays, in none); `/api/folders/file` files a protocol for
whoever may edit its page, a recipe for its owner or an admin. The
`/protocol` drawer and Load from library list by folder.

**A page's details and cover.** `notebook_pages.properties` (JSON, a
column from before 0.8) holds them: `[{"key", "value"}]` for a kind's
fields (`KIND_FIELDS`: a reading page's authors, journal, year, DOI, link
and Zotero link; a seminar's speaker, affiliation and link) and `cover`,
an uploaded picture's address, which any page may have. Entries from before
(`{"name", "type", "value"}`) are kept as they were and shown as rows.
`clean_properties` trims whatever is sent (`/update`, `/api/pages/new`):
text only, an empty field dropped, at most 40. The uploads hook owns a cover
for the page like any picture in it (its text names it). Search reads the
properties too, and its results carry the cover. `KIND_HINTS` says what
each kind adds, in the kind menu and New page; the template gets
`KIND_FIELDS` as `[key, label]` pairs, since `tojson` sorts a dict's keys.

**Reading notes.** `app/citations.py` reads what a person pastes into
**Fill in** or New page's **Paper** (`/notebook/api/cite`): CSL JSON, BibTeX
or RIS (what Zotero's Quick Copy or Export gives), a `zotero://select/…`
link, or a DOI, which alone is looked up at `api.crossref.org` (the only
host, 8 s; a failure is `CitationError`, said in words). A CSL JSON item's
zotero.org address becomes its `zotero://select/library/items/<KEY>` (or
`groups/<n>`) link. `/notebook/api/reading` lists the reading and seminar
pages the person may open with their fields, cover and the pictures in
their Markdown (`page_images`), which `notebook-library.js` draws as cards
or as one wall of pictures (`nb-reading-view` in localStorage), filtered in
the browser.

**Paper notes from Zotero** (`app/zotero.py`, `zotero_links`, revision
0047). A person's link holds a read-only Zotero API key (`EncryptedText`,
blanked in lab copies, `zotero_links` in `lab_copy.OWN_ROWS`), the library
(`users/<id>` or `groups/<id>`, from `/keys/current` and the user's
groups), a collection or none, the topic, the library `version` last read
and `seen`, the item keys a note was made for. A check (`zotero.check`)
asks `/<library>[/collections/<key>]/items/top?since=<version>`, 100 at a
time and at most 200 notes a check, skips notes, attachments and
annotations, and makes a Reading page per new item key (`fields()`: the
authors, not editors; the DOI from `DOI` or Zotero's Extra; the item's
Zotero tags as page tags; its abstract above the Paper notes starter). The
version moves on only once every page was read, and `seen` keeps a paper
from getting a second note when it changes or its note is deleted.
Connecting starts at the library's current version unless
`include_existing`. `notebook-page.js` posts `/notebook/api/zotero/check`
when a notebook page opens and the last check is over five minutes old
(`zotero.due` says the same on the server); **Check now** sends `now`.
`zotero.claim` takes each check for one request (it moves `checked_at` only
if nobody moved it since it was read, committed before Zotero is asked), so
two notebook tabs opened at once don't both make a paper's note. A new key
for the same library and collection carries on from the version it was at.
Tests replace `zotero._get` with `tests/test_notebook.FakeZotero`. The notebook's "Reading" is `notebook::Reading` in the
translations: plain "Reading" is a measurement's.

**Durations and clocks.** `frontend/src/durations.js` finds what gets a
step timer and leaves time points out (a list of times, "at 24 h", "48 h
samples"); `tests/js/durations.check.mjs` holds the cases; `timerLabel()` names a
timer from the words of its own sentence before the duration. The page shows
times in the lab's zone (`lab.clock_zone()`, `labZone` in `#nb-data`),
the clock the app writes "Started:" lines on.

**Topics are folders.** A page's kind never picks its topic:
`lab_notebook.folder_for()` puts every new page (blank, starter, template,
today's daily log, a protocol, an experiment from a protocol, a meeting
note, a colony experiment's **Add to notebook**) in the topic of the page it
was made from when that topic is the person's own, else the topic open
(`open_tab_id`, sent only when a topic was chosen, `topicChosen`), else
*Inbox*, which lists only what no library does (above). Changing the
kind (`/meta`) never moves a page. The page header
(`templates/notebook.html`) is a breadcrumb: the topics its topic sits in
(`tab_path`, each a link to its page), then the topic, which opens **Move to**
(`#nb-move-menu`, posting `/notebook/pages/<id>/move`, or
`/notebook/tabs/create` first for a new topic), then `parents_of()`; the
kind sits above the title (`#nb-kind-menu`, hints in `KIND_HINTS`).

**Sub-pages and links between pages.** `/page` posts
`/notebook/api/pages/new` with `parent_page_id` (a page the person can
open); the sub-page goes in that page's topic if it is theirs, else in their
own as above. The sidebar nests sub-pages under their parent within a topic
(`page_tree()`; a parent elsewhere or a loop shows flat), folded per browser
(`nb-closed-subpages` in localStorage). Moving a page moves
`with_subpages()` with it; a sub-page moved alone leaves its parent unless
the parent is in the topic it goes to. Deleting a parent leaves its
sub-pages as pages of their topic. In the editor
(`frontend/src/extensions/PageLinks.js`) a link to a page is a `pageLink`
inline atom, kept in the Markdown as `[Title](/notebook?page=12)`; it shows
the title from `/notebook/api/pages/titles?ids=` (pages the reader can
open; one they cannot is dimmed) and, when editable, writes it back into
the node so the Markdown carries the current title. Alone in its paragraph
it is drawn as a block (a decoration, `is-block`). `[[` (an input rule) and
**Link to page** open `openPagePicker`, which can also make a sub-page;
**Turn into page** makes the line's text a sub-page's title.
`/notebook/api/pages/<id>/linked-from` lists the pages the reader can open
whose body contains `/notebook?page=<id>)`. Every link in a page opens with
`openInApp()` (the app's pages, in a BioManager tab, after
`editor.storage.pageLink.beforeLeave` has pushed the live document and saved
the text) or in the browser (other sites). On `pagehide` the sync provider
also sends updates not yet pushed (`_sendOnLeave`, a keepalive request).

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

**Formulations.** The `formulation` block (`frontend/src/blocks/formulation.js`)
keeps each component's name, MW, purity, density, CAS number and lot as
picked, so a signed page needs nothing live; `compute()` gives grams,
moles, equivalents against the `basis` row (a row's `given` says whether
its mass or its equivalents were typed) and weight %. A component picked
from a database keeps `ref: "@<key> <n>"`: the page's Markdown then holds
the record link, so backlinks (*Used in notebook pages*) find it like a
typed `@` link. `structure_only` keeps the components and equivalents and
drops the ticks and lots. `tests/js/formulation.check.mjs` checks the
arithmetic, and the data sheet's `looksLikeLog()` (a pasted or imported
log over time is drawn as a line).

**Colony experiments in a page.** The `experiment` block
(`frontend/src/blocks/experiment.js`) keeps only `{"id", "show",
"percent", "measure"}` and reads `/colony/experiments/<id>/notebook.json`; *Freeze a
copy* stores that payload in the block as `frozen`, so the page's
versions keep it. The payload's `measures` are the experiment's other
measures, each with its `table` as the page has it; `measure` (a
`measure:<id>` key, none for the main readout) is the one the block shows,
switched by a button each (`readouts()`, `chosenReadout()`; a reader who
can't edit switches without saving). Every word goes through `tr()`, its
Chinese in `js-notebook.json`; the readout's, kinds' and status's names
arrive in English and are translated where they show, as on the
experiment page (`tc()` for `experiment::<status>`).
`tests/js/experiment-zh.check.mjs` checks each literal has its Chinese and
no English is shown outside `tr()`. A recorded day's reagent and lot (its record's
`reagent`) show under the manipulation, *(expired)* as on the experiment
page (`reagentUsed()`, `reagentText()`; `tests/js/experiment.check.mjs`).
The payload, the plan and the records are `app/experiment_steps.py`:

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
| `experiment_steps` | an experiment's manipulations: days (`"2–5"`, day 1 = the start date), kind, agent, dose, route, concentration, a stimulation's frequency, intensity, pulse_width and duration (`experiment_steps.STIM_FIELDS`, as typed; revision 0042), treatment group |
| `experiment_step_records` | one per step and day done: date, who, and per mouse the weight used and the amount and volume given (`mice`, JSON) |
| `plate_layouts` | a plate layout kept with an experiment: name, format (24/48/96/384), `wells` (JSON: each well's role, `sample` with its animal's key, `blank`, or `standard` with `conc`), `used_at` (the last read onto) |
| `plate_reads` | a plate read onto a layout and saved as readings: the layout as it was, the day and readout, the choices (`options`), every well's value as exported (`data`), each animal's value (`results`), and the file (`file_path`, an upload) |

A `reading` step (`weigh` in the first version) writes the readout when recorded.
A step may name the inventory item it uses (`reagent_item_id_fk`); a record
keeps that item as it was then (`reagent`: id, name, lot, expiry, and
`taken` once its amounts were taken off) and any sample records the person
chose to make with it (`samples`), made through
`inventory_routes._item_from_form` with the animal as their Source.
Every path that records a day goes through `experiment_steps._record`
(the page's record dialog and *Record manipulation*, bench mode, and the
`experiment_step_done` action, which posts to the same route), and
`reagent_used` picks what it kept: `reagent_id` when sent (blank: none),
else `default_reagent` (the step's item, else the one available item named
as the agent, `reagents_named`; with several, none, and the action refuses
asking which). A `lot` that isn't the item's switches to the item of the
same name with that lot, or keeps the lot as typed with no expiry. The
item used becomes the step's, unless a later day is recorded. A lot past
its expiry on `done_on` is a warning (`problems`, and `warnings` so an
action's change passes it on). `take_off` (opt-in) adds up the record's
volumes and amounts (`amounts_given`) and takes them off the item through
`actions._used`/`_left_after`, so units and pack sizes convert as in
`inventory_quantity_used`; it needs edit rights on the item, never runs
twice for a day, and undoing a day does not put it back. The action
checks a named `reagent` or `lot` before sending and refuses an unknown
lot in words. `assistant_compute.experiment_summary` lists each step's
lots and `expired_lots`; the handover's `next_steps` carry the planned
item and lot (`reagent`). Subjects
are `mouse`, `fish`, `clutch`, `unit`, `organism` or `cohort`.
`exp_stats.py` compares the groups on each day (Welch, ANOVA, χ²);
`/experiments/<id>/export.xlsx` and bench mode (`/experiments/<id>/bench`,
`static/experiment-bench.js`) are in `experiments.py`. The owner's
morning notification of what is due is `notify.daily_experiment_reminder`
(category `experiments`, `users.notify_experiments`). Undone days are
`auto` items on the calendar (`experiment_steps.calendar_items`). Which
notebook page is a person's for an experiment is the app setting
`experiment_notebook_page:<experiment>:<username>`.

**Derived readouts** (`app/derived.py`, no schema change). A readout can be a
formula over what was measured: `tumour_lw` (L × W² / 2) and `tumour_lwh`
(π/6 × L × W × H, mm³, mouse and organism only), `delta_ct`,
`delta_delta_ct` and `fold_change` (against the control group's mean ΔCt
on the same day, `per_group`), `percent_of_baseline` and
`blank_subtracted`. Each `Formula` names its inputs with units, a line of
arithmetic (`expression`) and a description; `check()` turns what was
given into numbers or a `DerivedError` in words (missing, below zero, not a
number, *Undetermined*, a Ct outside 0–50, an OD over 10, width over
length); the arithmetic is pure functions. The experiment keeps
`{"key": "derived", "formula", "control"}` in `experiments.readout`
(`readout_json`), and `readout_of` gives it the key `derived:<formula>`
(also each reading's `readout_key`), its unit, inputs and control; the page's
**Readout** select lists `derived_choices(place)` in an optgroup, and
**Control group** (`readout_control`) shows for a per-group one; both New
experiment forms take the same choice from `templates/_readout_choice.html`
(the `experiment_readouts` template global, `new_readout_choices(family)`),
posting `readout` and `readout_control`. Readings
take `{input: value}` instead of a number (the page's `/readings`, bench
mode, a reading step's `values`, `POST /api/v1/experiments/<id>/readings`
with `inputs`); `set_reading` → `_set_derived` stores the value with the
inputs as JSON in `experiment_readings.note` (`derived.note`), and
`readout_table` gives each row its `inputs` for the sheet's tooltips, the
dialog and the export's extra columns. ΔΔCt and fold change are worked out
again from the notes whenever they are read (`readings`), so a new control
animal or a changed group is followed, and stored afresh for the day on each
save (`_store_day`); a treated animal before any control that day, or the
last control taken out from under the others, is refused, and
`control_first` saves a whole day's controls first. `derive_day` is the
same arithmetic for an action's preview. `experiment_reading` takes
`inputs` for such a readout (refusing `values`, and the reverse), and its
summary shows each animal's inputs → value. `POST /api/v1/derive`
(`api.COMPUTING`; GET lists the formulas) is `derived.derive` over rows,
changing nothing, for the `derive` tool.

**Plate layouts** (`app/plates.py`, `static/plate-layout.js`, tables
`plate_layouts` and `plate_reads`, revision 0040). The experiment page's
**Plate layout** dialog (owners and admins, `access.can_edit_experiment`)
draws a layout on a plate (a rectangle dragged, a whole row or column,
**Fill in order**) and saves it with `POST /experiments/<id>/plates/layouts`
(`clean_layout`: wells on the plate, animals of the experiment, a standard's
`conc`); `GET …/plates.json` lists the layouts, the last read onto first
(*Same as last plate*), and the plates read. **Read a plate →** posts the
export (multipart: `layout_id`, `file` or `text`, `on`, `target`,
`reference`, `subtract_blank`, `standard_curve`, `curve`, `measure`, `skip`) to `…/plates/preview`,
which changes nothing, and **Save readings** to `…/plates/save`. Both run
`work_out` → `analyse`, the pure part: `read_export` reads the file with
`sheet_import.read_workbook` (a workbook's *Results* sheet first) and
`parse` finds a list with a well column (Well Position over Well's numbers)
and a value column (`Ct`/`Cq` make it `kind: ct`; a `Target` column splits
it by gene) or a grid (a header 1, 2, 3… with row letters before it; each
grid in a file is a read, named by the line above it), else headerless
`well value` lines. `mode_for(readout)` decides what a plate gives: `ct`
for ΔCt, ΔΔCt and fold change (each animal's mean Ct of the target and
reference genes, as `inputs`), `od` for `blank_subtracted` (needs blank
wells), `value` for any other number (the mean, less the blank's mean, or
read off a standard curve, `app/standard_curves.py`: `linear`, `loglog` or
`4pl`, as `curve` asks, else `fit()` picks 4PL for five or more
concentrations over a tenfold range and a line otherwise; the 4PL is a
pure-Python Levenberg–Marquardt on (a, d, ln c, ln b), at most
`MAX_STEPS`, refused as a `CurveError` in words when it doesn't settle,
puts the EC50 a hundredfold off the standards, has a slope outside
0.05–20 or r² under `MIN_R2`; `concentration()` is None past a plateau,
which the animal's row says, `below()` telling which side; the analysis
carries `curve` to draw beside the plate); body weight, a count and the
caliper formulas are refused in words. A Ct outside 0–50, an OD over 10
(in `od`), an *OVRFLW* or a word is `limit` or `no_value` and left out; a
replicate more than `CT_TOLERANCE` (0.5 cycles) or `VALUE_TOLERANCE` (20 %)
from its animal's median is `outlier`, flagged only; `skip` leaves wells out
by hand. ΔΔCt and fold change come from `experiments.derive_day`, so the
day's other readings count. `save` writes each value through
`experiments.set_reading` (derived inputs as the sheet takes them,
`control_first`), counts what it `replaces`, and keeps a `PlateRead`, all
inside one `audit.batch`, so Batch history's Undo takes the plate back
whole (the layout's `used_at` is set outside it, by a Core update, so a
later plate on the same layout doesn't block that Undo; a new measure is
kept before the batch, so an Undo never hides another plate's readings
of it); the
file is saved (`services.save_uploaded_file`) only once some animal has a
value, and `upload_owners` makes it the experiment's to see. For an
assistant, `POST /api/v1/experiments/<id>/plate` (`api.COMPUTING`) runs the
same with `layout` (id or name) or `wells` + `format`, and `text` or a
`capture` (its file), and answers the analysis with the `experiment_reading`
change to propose (`plate_readout` tool). Tested in
`tests/test_plate_layouts.py`.

**Other measures** (`experiments.measures_of`, no schema change). The
main readout is the one the plan, tests by day, the chart's default and
bench mode are about; a plate can fill another measure of the same
animals (**Readout** in the plate dialog: `measure` is blank or `main`,
a measure's key or name, or `new` with `measure_label` and
`measure_unit`, `plates.readout_from`). They are kept in
`experiments.readout` as `"measures": [{"id", "label", "unit"}]`
(`add_measure`; `keep_measures` carries them over a change of the main
readout), and their readings in `experiment_readings` with
`readout_key` `measure:<id>`. `readings`, `reading_inputs`,
`readout_table` and `set_reading` take an optional `readout` for one;
`readout_for` finds the main readout or a measure. The page data has
`measures`, each with its `table`; `experiment-page.js` draws a chip for
each beside the readout's (`view` `measure:<id>`), and the sheet, the
chart and the tests follow it (`VR()`, `VT()`), a typed cell posting
`measure` to `/readings`. Export adds a sheet per measure with values
and its tests under **Tests by day**; `experiment_summary` has
`readouts.other_measures` and ARRIVE item 6 names them as secondary;
`experiment_reading` and `POST /api/v1/experiments/<id>/readings` take
`measure`, and `plate_readout` takes `measure` (an existing one) and
`curve`.

A measure can be worked out from two Cts (`MEASURE_FORMULAS`: `delta_ct`,
`delta_delta_ct`, `fold_change`): its JSON entry has `formula` (and
`control` for the last two), chosen as **Kind** (`measure_kind`,
`measure_control`) when the plate dialog makes it, and `measure_readout`
gives it a derived readout's fields (`inputs`, `per_group`, `expression`)
under its own key, so `mode_for` asks for the target and reference genes,
`derive_day(…, readout)` and `_store_day` work out its ΔΔCt against its
own control, and Export adds the Ct columns. On the sheet its cell is
typed, not opened as a day: `typed_cts` takes "24.8 − 18.2" (target then
reference) as inputs, or, for ΔCt only, the ΔCt itself, kept with an
empty note. The ••• beside the measure shown (`#xp-measure`) posts
`…/measures/<id>/update` (`label`, `unit`, `kind`, `control`; `change_measure`
keeps the id, so the readings stay; a new control stores each day again;
a new `kind` goes through `rekind_readings` in one `audit.batch`: a number
clears the notes, ΔCt computes each from its inputs (an input-less value
kept as a typed ΔCt within `CT_MAX`), ΔΔCt and fold change `_store_day`
every day, refused while any reading has no inputs)
and `…/measures/<id>/delete`, which deletes its readings and takes it out
of the JSON (`drop_measure`) inside one `audit.batch`, so Undo puts back
both the experiments row and the readings. `last_measure` in the JSON
keeps a deleted measure's number from being given again (its plates keep
that key). The page data gives each measure its `count`. Both are the
`experiment_measure_change` action (`delete: true` to delete). Bench mode
lists each measure after the main readout (`data-reading` its key) and
posts `measure` to the same `/readings`. Tested in `tests/test_measures.py`.

**Planned experiments and changes from the plan** (`app/experiment_plan.py`,
revision 0033). Status `planned` comes before `active`: an assistant's
`experiment_new` makes one through `POST /experiments/in/<db>/design` (the
groups go in as Add does, `experiments.add_animals`, the steps as the
Regimen saves them, `_fill_step`), and nothing of it is due: `schedule()`
marks its days `planned`, and the calendar, Home's agenda (`lookup`) and the
morning reminder only read `active` experiments. **Start**
(`POST /experiments/<id>/start`, a person's button; `status_refusal` stops
the status field and `experiment_change` from doing it) sets day 1 (today
unless set), makes it active and keeps `snapshot()` in
`experiments.plan_json`: the groups and their animals, the main readout,
`readout_day`, the regimen and `planned_test` (a key of `TESTS`, the tests
`exp_stats` runs). From then on (`fixed()`), the routes that change the main
readout, its day, the test, the animals and their groups (the experiment's
own and the colony's older ones, and the mouse sheet's **Add to
experiment**), and leaving an animal out of the analysis
(`…/subjects/<key>/leave-out`; `left_out`, the reason, on `experiment_mice`
and `experiment_subjects`, which `exp_stats.by_day` and the chart skip)
compare before and after; a change with no `reason` is rolled back and
answered 409 `needs_reason`, which the page turns into a question
(`sendReason` in `experiment-page.js`, BioDialog) and sends again. Each is
an `experiment_deviations` row (what, before, after, each change in
`detail`, reason, who, when); more of the same kind by the same person
within ten minutes join the last one instead of asking again
(`CONTINUE_WITHIN`), except an animal left out. `page_data()` adds `plan`,
`deviations` and the Planned → done `comparison` to the page's data, the
export adds a "Plan and deviations" sheet, and `GET /api/v1/experiments/<id>`
has `plan`, `planned_test`, `readout_day` and `deviations`. An experiment
with no `plan_json` (all from before) never asks.

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

**Blocks** (data sheets, recipes, formulations, calculators, plates, qPCR,
diagrams, equations) are one TipTap node, `labBlock` (`frontend/src/blocks/`). In
Markdown each is a fenced block named by its kind (```` ```sheet ````,
```` ```recipe ````, ```` ```mermaid ```` …) holding JSON or the source, so a
page reads anywhere and GitHub draws the diagrams and maths itself. In the
editor the data is one node attribute rather than text: Yjs merges text
character by character, which would splice two people's JSON, while an
attribute is replaced whole. Statistics (`blocks/stats.js`) and formula
columns (`blocks/formula.js`) are computed in the browser; formulas are
parsed by hand because the security policy forbids `eval`.

**Rich text.** Callouts, toggles, columns and coloured text are TipTap
nodes and a mark in `frontend/src/extensions/RichBlocks.js` (`callout`,
`toggle` with its `toggleSummary`, `columns` of 2–4 `column`, the `tint`
mark with `color` and `bg`). How each is written in Markdown is in
`frontend/src/markdown-extras.js`. Its markdown-it rules turn these into
the HTML the nodes read:
- GitHub's alerts (`> [!WARNING]`);
- `<details>` with a `<summary>` line (nested ones counted; the rest of
  the page's HTML is still escaped);
- Pandoc's fenced divs (`:::: columns` / `::: column`);
- bracketed spans (`[text]{.red .bg-yellow}`, only the colours in `COLORS`);
- `==text==`.

`tests/js/markdown-extras.check.mjs` checks them. A toggle's open or
closed state is the reader's (the node view's), never saved. A table of
contents is a `toc` lab block (`blocks/toc.js`) drawn from the headings on
every change. Lab blocks' `mount()` now gets the `editor`.

`rich-menus.js` has the colour palette, the page picker
(`/notebook/api/search`, a link to `/notebook?page=<id>`), the date and
the reminder (a personal `task` posted to `/calendar/items`, then a link
to `/calendar?date=…`). The "/" menu's items are `commands.js` `ITEMS`;
keywords hold the Chinese, and the slash trigger takes any letters
(`\p{L}`).

`@name` for someone in the lab is decorated by
`extensions/PeopleChips.js` (the names from `/notebook/api/people`);
hovering shows `/notebook/api/people/<username>`: name, title, role, guest
until, since, and for a member also email and project groups.

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

- **Tokens** (`api_tokens`): made under Settings → AI assistant & tokens (`api/_card.html`),
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
  `_page` calls `_only_filters()`: a query parameter that isn't in the
  list's `ENDPOINTS` row (or `limit`, `after`) is a 400 naming the ones it
  takes, rather than a filter quietly ignored. `/experiments?status=` is
  one of `experiments.STATUSES` or `all`.
- **One record**: `/mice/<number>` has its weights and `experiments` (id,
  name, group, status, and `left_out` when set); `/plasmids/<number>` its
  box `position` (`plasmid_service.label`) and `glycerol_stocks` (the items
  of Glycerol stocks inventories naming it, `made_from_plasmid`), its
  `sequence`, `circular` and `annotations` only with `?sequence=1` (any
  plasmid has `has_sequence` and `length`); a litter's `cage` is where most
  of its living pups are and `cages` every one (a litter is in a cage only
  through its mice, as in `weaning_due`), and `/litters?cage=` lists those
  with a living pup in it; a cage's `litters` are the litters born in it not
  yet weaned (`services.cage_litter_json`: `born`, `pups`, `dam`, `litter`,
  `age_days`, `wean_due`, `genotyping_due`), `litter_born` the oldest's date;
  an item of `/due` about one carries it as `litter`, and its `propose` names
  it when the cage has two; `/experiments/<id>` is
  `experiments.payload` without `PAGE_ONLY` (the step kinds, regimens,
  reagents, sample inventories and tests the page offers as choices).
- **Writes reuse the pages' code**, so their rules hold: `PATCH /mice/<id>`
  fills a form from `mouse_display_row`, overlays the fields sent, and calls
  `colony_routes.change_mouse`, as the mouse sheet does: one set of rules (no date of
  birth or death in the future, an owner from the lab, a cohort's date of
  birth only once confirmed) whose refusal is a 400, and what it couldn't
  do while saving the rest (a private cage) comes back as `warnings`;
  a `version` that isn't the mouse's `sync_version` now is a 409, so a
  client that sends back what it read never overwrites a change since);
  inventory items go through `inventory_routes._item_from_form`, vials
  through `stock_routes._unit_from_form` (both copy only the fields sent),
  readouts through `experiments.set_reading`. Permission checks are the
  same functions (`can_edit_mouse`, `_can_edit`, `can_edit`,
  `access.can_edit_experiment`). A switched-off built-in database is a 404.
- **Racks**: `GET /racks` lists the colony's cage racks (`rack_json`:
  size, `naming`, example `positions`, how many cages); `PATCH
  /racks/<name>` takes `RACK_FIELDS` (`naming` merged over the rack's own,
  `rack_layout.json_naming`) through `_mouse_rack_from_form`. A change that
  renames or leaves out cages' places needs `cages`: `keep_names` or
  `keep_cells`; without it the answer is 409 with the `preview`
  (`rack_layout.public(plan)`), so a script can show it and send again.

- **For assistants** (`app/lookup.py`): `/resolve?q=` finds the records a
  phrase means across every database the person can open ("cage 88" looks
  only at cages; a mouse by its number, a vial by its prefix and number),
  each with a reference `{"kind", "id"}` (the kinds are `actions.KINDS`)
  and, for a kind read one at a time, the `path` GET reads it at
  (`lookup.path_of`: `cages/<code>`, `mice/<number>`, `plasmids/<number>`,
  `inventories/<key>/items/<number>`, `experiments/<id>`); `/path?kind=&id=`
  gives that path for a reference, which is how the `get` tool reads a
  `ref`. `/vocabulary` is the lab's own words (a mouse's statuses as the
  sheet offers them: built-in, the lab's dropdown, then any in use);
  `/due` is Home's agenda (`home_layouts.build_agenda`), whose items carry
  `ref` and `propose`, the change that records each done (an inventory
  item's `ref` and its inventory as `database`; the colony's age and
  genotyping items also `mice`); `/actions` is `actions.catalogue()`.

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
- Beyond the animals, the catalogue covers inventories, plasmids, the
  notebook and the calendar. Inventory items are written through the API's
  item writes (`_item_from_form`), so required columns, statuses and Low at
  behave as on the sheet. `inventory_quantity_used` takes an amount off a
  numeric quantity, and the status follows Low at; an amount in another unit
  ("40 mg", or `unit`) is converted within mass or volume (`UNITS`, powers
  of ten); an item kept in another unit (ampoules, vials) takes one through
  its pack size, and without one is refused with what would do. `order_received` marks an
  order received; with `to_stock` it saves the stock record that **Add to
  stock** would make (`_stock_from_order`, with `name`, `quantity`, `unit`
  and the rest given over it: `_restock`) with `from_order`, so the two are
  linked; its summary lists the new record's name, amount, lot and expiry,
  and its `warn` names an item of the same name already in that inventory.
  An `Action`'s optional `warn(s, target, values)` adds warnings of its own;
  `side_effects()` adds one for a record whose sharing changed though
  `shared` wasn't asked (a cage leaving Breeder is un-shared by
  `models._breeder_cages_start_shared`). `litter_born` keeps `pups` and
  `mother` on the cage's litter (`cage_litters`, below); on a cage with a
  litter not yet weaned and another date it needs `another` (true: a second
  litter, both kept; false: a correction, with `litter`, the birth date of
  the one corrected, when there are two) and is refused without it.
  `litter_genotyping` and `wean` take `litter` (a birth date, or the litter
  of mice its pups are) and otherwise act on the page's default, which their
  summary names when the cage has two. `sample_from_animal` fills a sample's Source with the mouse, tank
  or animal. Plasmids go through `/plasmids` and `/plasmids/<id>/update`,
  with a pasted sequence through upload-sequence. Glycerol stocks and primers
  are items in those inventories, and a primer pair goes through
  `/items/pair`. `notebook_page_new` makes the page through the notebook's
  Import, from a template or starter if given, starting with "Drafted by
  <source>" and never signed; with `folder` (kind protocol only) Import also
  files it in that folder of the protocol library (`folder_id` on the
  import form). `notebook_page_append` adds a section with the
  plain-text save and is refused while the page is open in the editor.
  `notebook_page_change` fixes a page instead of making a new one: its
  `title`, `kind`, `tags` and its text, all of it (`body`; a "Drafted by"
  line at the old text's top stays) or parts (`edits`, each `{find,
  replace}`, `find` there exactly once). It asks what the web editor asks
  (`_editable_page`: may edit, not signed and locked, not open in the
  editor), keeps the text as it was as a version of its own ("Before the
  change by <source>", `POST /notebook/api/pages/<id>/versions`) so History
  restores it, sets kind and tags through `/meta` and saves title and text
  through the plain-text save. The notebook keeps no change history, so its
  `shows` (an `Action`'s optional records of what the change history
  doesn't keep, added to `records`) gives the before → after: title, kind,
  tags and each part of the text that changes, by line (difflib).
  `protocol_folder_new` posts to `/notebook/api/folders` (kind protocol;
  a name a folder has already is refused) and `protocol_file` to
  `/notebook/api/folders/file` (`folder: ""` takes it out); the kind
  `protocol_folder` is a folder of the protocol library by name or id.
  `/api/v1/notebook/pages/<id>` reads a page the person may open, with its
  text, so an assistant edits from the page as it is now; resolve gives
  pages that path, and `_pages_titled` only finds pages the person may read.
  `inventory_renumber` posts to the inventory's **Renumber** page (below).
  `calendar_event_new` and `task_new` (a to-do: yours, or with `shared` the
  lab's; a day is optional) post to `/calendar/items` and `equipment_booking_new`
  to `/calendar/bookings`. The kinds `inventory_item`, `plasmid`, `equipment`,
  `notebook_page`, `protocol_folder` and `notebook_template` are found by `/resolve`, and `/vocabulary` lists
  each inventory's statuses, categories, columns and boxes, plasmid boxes,
  instruments and templates. `experiment_new` makes a **planned** experiment
  (*Planned experiments and changes from the plan*, above); no action starts
  one.

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
  it too), stores the result (pending, or invalid with the reasons), keeps
  `replaces` (one of the owner's) as `replaces_id` and marks it superseded
  when it is pending, sent back or invalid (`REPLACEABLE`), and notifies the
  owner (`notify.send`, linking to `/proposals`). `POST /proposals` answers
  with each change's records (`as_dict(records=True)`), so an assistant sees
  every before → after at once. It commits the session's read before the preview starts,
  so SQLite's lock is not held across it.
- `approve()` refuses unless pending; refuses when a record the preview
  changed (not one it made) has a change-history row after `audit_mark`
  (`changed_since`, naming it), or when a notebook page it changes (a
  `notebook_pages` record from `shows`; the notebook keeps no change
  history) was saved after the proposal was made (`_pages_changed_since`,
  by `updated_at`); otherwise runs `actions.run(apply=True)`
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
urllib client of `/api/v1`): `lab_overview` → `/me`, `/vocabulary` and
today's part of `/due` (`DUE_TODAY` at most; the person's own first, each
marked `mine`, as an admin's list is the whole lab's), with `INSTRUCTIONS` as
`how_to_work`: a tool result reaches the model in every app, while not
every app passes on a server's `instructions`, and its description is
written to be picked for a vague start ("I want to change something in
BioManager"), `resolve`, `get`/`list` limited to the API's read paths
(`READABLE`; `get` sends a query in its path, `plasmids/14?sequence=1`, as
query parameters, which every `Api` passes on), `whats_due`, `list_actions`, `propose_changes` → `POST /proposals`,
`proposal_status`, `discard_proposal`. `propose_changes`' description is
built from the lab's own `/actions` (`description(api, name)`), and names
the actions only, by area: assistant apps show only so much of a tool
(Claude Code cuts a description at about 4,000 characters and saves a long
reply to a file), and the whole catalogue with its help is some 86,000
characters. So `list_actions` comes in two steps (`actions_tool`): with
nothing given, every action briefly (area, title, target, fields with
`*` for required); with `names` or an `area`, those in full. An action
that doesn't exist is answered with the nearest
(`actions.nearest_actions`). `tests/test_mcp.py` keeps both within what
an app shows.
`call_tool()` turns an `ApiError` or bad arguments into `{"error": …}` for
the assistant to read.

**Evidence, Send back and Captures.** A proposed change may carry
`evidence` (`{quote, capture, where}` or a string; `proposals.evidence_of`),
kept in `request_json`, shown on Proposed changes and stripped by
`_runnable` before `actions.run`. The page lists one row a change: an icon
by its action (`ACTION_ICONS`, else its area's, `AREA_ICONS`) in a tile
tinted by its area (`AREA_TONES`, the `prop-*` classes), and its evidence
as a chip on the right (*You said*, or *Photo* / *Capture #n* and where),
the quote in its title and under **Show each change**. **Send back** (`proposals.send_back`,
`POST /proposals/<id>/send-back`) sets `sent_back` with `proposals.reason`;
`as_dict` then gives `reason` and a `next` telling the assistant to propose
again with `replaces`. Captures (`app/captures.py`, table `captures`,
revision `0032_assistant_inbox`) are what a person wrote or photographed at
the bench: text and one file (kept by `services.save_uploaded_file`, served
to its owner only by `/captures/<id>/file`, as `/static/uploads/` is:
`app/uploads.py`), with the record it was taken at
(`at_kind`, `at_id`, `at_label`; from Bench mode or a scanned card's link,
`from_card`). Status is waiting → used or dismissed. `GET
/api/v1/captures[/<id>]` serve them; `for_assistant` inlines a photo as
base64 (downscaled with Pillow when present, else refused over 5 MB) or a
text file's first 20,000 characters. `list_captures` and `get_capture` are
tools; a tool returning `_content` has those MCP content items appended
after the text by `mcp_http._call` (the local server uses
`mcp.types.ImageContent`). `propose_changes`' `captures` (and evidence's
capture ids) must be the owner's and are kept in `proposals.captures_json`;
`approve` marks them used with the proposal and batch
(`captures.mark_used`). Captures are in a member's lab copy as their own
rows only (`lab_copy.OWN_ROWS`).

**Looking up and working out (`app/assistant_compute.py`,
`lookup.search/history/record/mouse_lineage/plasmid_lineage`).** BioManager
computes; the model talks. `/search`, `/history`, `/lineage`,
`/availability`, `/reorder-forecast`, `/lab-checks`,
`/experiments/<id>/summary` and `/regimens` read; `/breeding-plan`,
`/randomize`, `/power`, `/derive` (derived readouts, `app/derived.py`;
the `derive` tool) and `/experiments/<id>/plate` (a plate on its layout,
`app/plates.py`; the `plate_readout` tool) are POSTs that change nothing (`api.COMPUTING`,
open to any token). Each runs as the token's person: `lookup.record` 404s a
record in a database they can't open, history shows only their own changes
unless they are an admin, and search is the ⌘K search
(`app.search_results`, which gives each match a `ref`). The genotype model
counts copies of the allele that matters at each autosomal, unlinked locus
(one to five loci, `MAX_LOCI`); a strain or background wanted (`is_strain`: the
`BACKGROUND` pattern, C57BL/6J, BALB/c…, or a background in the lab's
Strains, `lab_strains`) is no locus but both parents of it, odds 1, with
breeders whose genotype names it (and asking it het or wt is refused);
`breeding_plan` counts each mating's litters as `litters_per_mating`, or
`litters_by` (born in the window: the first `PAIRING_TO_BIRTH` days on,
then every `LITTER_INTERVAL`), else one; a Breeding cage holding a female
and a male of a cross is a mating under way (`to_set_up` is the rest),
and only breeders in no Breeding cage and no busy experiment count as
free; `clashes` names breeder kinds two listed crosses would both need;
litter sizes are the line's own mean when it has three
litters with pup counts, else the lab's, else 6; power is exact, from the
noncentral F distribution over `exp_stats.inc_beta` (two groups are the t² =
F case, planned with one SD; the experiment's Welch's test has practically
the same power then, as its assumptions say), and matches G*Power.
`availability` leaves out animals booked in a planned, active or paused
experiment (`BUSY`). A breeding plan's crosses say what a mate carries beyond
the loci asked (`also_carries`; those crosses come after the ones that don't)
and `males_short`, counting a male with up to two females.
`randomize` also returns `groups` in `experiment_new`'s shape, which takes
them and the `seed` (kept in the description) as they are. The pure parts (genotype model, binomial
matings, noncentral-F power, stratified minimisation, usage rates, the ARRIVE
builder) take plain values and are tested in
`tests/test_assistant_compute.py`. `lab_checks` takes its animal checks
(`weight_loss`, `welfare_notes`, `not_weighed`, `status_experiment`) from
`welfare.checks()` (Home's card, tested in `tests/test_welfare.py`), making
their `url`s whole; `weight_loss_pct` left out is the lab's setting. Its `summary` counts each record
once per severity (`_distinct`: by reference, as often as the check that has
it most), though an expired reagent is also an overdue job; `reorder_forecast`
names what it can't forecast (`without_enough_history`). The endpoints are
tested in `tests/test_api_compute.py`, and what an end-to-end run of the tools
found wanting in the reads in `tests/test_assistant_fixes_read.py`.
The prompts `weekly_review`, `design_experiment` and `write_methods` drive
them; `design_experiment` asks first, then proposes the plan it agreed with the
person: `experiment_new`, Breeding cages for the matings, `task_new` and the calendar.

**Write-ups (`app/assistant_reports.py`).** BioManager counts and lists;
the model writes. `GET /animal-use?start=&end=&protocol=` (the twelve months
to `end`, today, unless asked; `year_before`) counts mice, zebrafish and each
organism database the person may see (`_organism_entries`) per
protocol, species and strain: `born`, `added` (record made in the period,
born before it or undated), `new`, `used_in_experiments` (in an experiment
whose status isn't planned and which ran during the period), `died_or_euthanized`
with `died_by_reason`, and `breeding`; per row, per protocol (`by_species`) and
in `totals`, with `basis` (what each column is based on) and `flags` (no
protocol, no reason, no birth date, transfers). The schema has no protocol on a
mouse or a mouse strain, so a mouse's is the colony's own column (custom_fields)
whose key or label matches `PROTOCOL_COLUMN` (`protocol_columns`,
`protocol_of`), else its litter's mother's, else father's (`from_parents`);
a litter's pups without records (`total_pups` less its mice) are one entry
under the parents' protocol and strain, counted as born; an organism's is
its `protocol`, else its housing's, else its line's; a zebrafish row's is its tank's such column, else its line's
`iacuc_protocol`. A mouse's strain is the lab's Strain named in its genotype
(`strain_of`), else the genotype. A death's reason is read from the words of
the mouse's note or the sac log's reason (`REASONS`, in order), else its status
(`death_reason`). Fish deaths are `fish_sac_log` rows by `recorded_at` (the sac
date); a row's `born` adds back the fish sac'd from it by hand, which came off
its count. Breeding is a parent (by number, `lookup._first_number`) of a litter
born in the period, or a mouse now in a Breeder or Breeding cage: where a mouse
was isn't kept. The pure parts (`counted`, `tally`) take plain dicts. Anyone
may ask (the colony is the lab's); organism databases aren't counted and the
notes say so.
`GET /handover?username=&days=` is one person's records, their own or (an
admin) anyone's (`PermissionError` → 403): cages in their name or holding their
living mice, by rack and position (`yours`, `others_mice`), Breeding cages with
the due items whose `ref` is the cage, tanks, vials and frozen lots, inventory
items not used up (glycerol stocks counted apart), plasmids with their box
position, organism housing and animals, planned/active/paused experiments with
their next undone steps, the racks and boxes they made (`admin_racks.CONTAINERS`
by `created_by`), unsigned notebook pages (named only where `role_for` lets the
asker read them) and pending proposals (listed only to their owner), then
`lookup.due` filtered to their name. Databases come from `list_modules(everyone=True)`
filtered by `lab.can_see`, so an admin sees a member's own databases.
`owner_changes` maps each kind to the `*_change` action with an `owner` field
(`owner_actions()`, from the catalogue). The tools `animal_use` and `handover`
and the prompts `animal_use_report` and `handover_report` drive them; tested
in `tests/test_reports.py`.

### The plugin (`plugins/biomanager/`)

What an assistant app installs once to know BioManager: the skill
`skills/biomanager/SKILL.md` (`INSTRUCTIONS`, then how to connect) and
`commands/` (`PROMPTS`), with a manifest per family of apps:
`.claude-plugin/plugin.json` (Claude; it also asks Claude Code for the
lab's MCP address, `userConfig.mcp_address`, which chat and Cowork ignore),
`plugin.json` (Agent Plugins 1.0: ChatGPT, Codex, VS Code),
`.cursor-plugin/plugin.json` and `kimi.plugin.json` (Kimi Code: the skill
and the commands, no MCP server). `.claude-plugin/marketplace.json`,
`.agents/plugins/marketplace.json` and `.cursor-plugin/marketplace.json` at
the repository root list it, so `gaspolymerase/biomanager` is a marketplace
for Claude, Codex, VS Code and Qwen Code (Cursor takes plugins only from its
own marketplace, by submission). Everything but its README is
written by `scripts/assistant-packages.py`; `tests/test_assistant_packages.py`
fails while a file is out of date, and when the skill or a command changed
without a new `VERSION` (apps keep people on the version they have).
`claude plugin validate ./plugins/biomanager` checks it as Anthropic's
directory will. `--zips <folder>` writes the downloads every release carries
(`.github/workflows/release.yml`'s publish job runs it) and the Download page
links to: `BioManager-plugin-Claude.zip` (the whole folder, for **Add → Upload
plugin**) and `BioManager-plugin-ChatGPT.zip` (skills only, `OPENAI_FILES`, to
upload at platform.openai.com/plugins and share: each lab's MCP address is
its own, and ChatGPT's public directory takes no skills-only plugins for
now). `BioManager-plugin-Kimi.zip` (`kimi_files()`: `kimi.plugin.json`, the
skill and the commands) is for Kimi Code's `/plugins install <link>`, by its
`releases/latest/download/` link: Kimi Code reads only its own manifest, and
installing from the repository would fetch all of it, as it takes no
subfolder. The test keeps the ChatGPT listing within the directory's limits (a
30-character short description, a square logo).

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
- **Connect an AI assistant** (`oauth.connect_page`, `oauth/connect.html`;
  linked from Help for everyone but guests, from Settings → AI assistant & tokens and
  from an empty Proposed changes): how to add the plugin (`#plugin`,
  `PLUGIN_MARKETPLACE`); the connector address and **Get a
  connection code** when the lab is on the internet; for coding assistants
  a message to paste into their chat (`#agent-message`: the address, the
  Claude Code and Codex commands and Cursor's `mcp.json` entry, how to sign
  in, and to start with `lab_overview`), **Add to Cursor / VS Code / Cherry
  Studio** (`one_click_links`: each app's install link with the address in
  it), the Claude Code command that signs in with OAuth and a loopback
  redirect (no token), Codex's two, and **Make a token** (scope
  `propose`), after which `api/token.html` shows the address-and-header
  setup and the local server's.
- The four tables are admin-only in a lab copy (`lab_copy.ADMIN_ONLY`).

## Feedback and the usage report

`app/feedback.py`, for a pilot (the plan is `docs/PILOT.md`). **Feedback**
in the rail opens `/feedback?from=<the page>`; a note is a `feedback` row
(kind, text, the page's path, `app_version()`, a short platform string)
and each admin gets a notification. Admins see every note and mark it done;
members see their own. `issue_url()` builds a GitHub "new issue" link on
gaspolymerase/biomanager with the text, path, version and platform —
never the server's host or the person — which the person submits
themselves.

**Also send this to BioManager's makers** (`tell_makers`, with an optional
`contact`) sends the note at once as one PostHog event, `feedback`, through
`telemetry._post` to the same project and key as the daily counts:
`makers_payload()` is exactly what goes (kind, text, path, version, the
platform string, desktop or server, OS family, language, contact, the
installation's random id as `distinct_id`, `$geoip_disable`). It is offered
by `can_tell_makers()` (a project key, `telemetry.released()`, not
`telemetry.off_by_env()`; the lab's own counts switch doesn't hide it, as
the person chooses per note) and ticked by `tell_makers_by_default()` on a
desktop (`LOCAL_SETUP`) only. A sent note's id goes into `app_settings`
`feedback:sent_to_makers` (a JSON list, the last 2,000), so the page says
"Sent to BioManager's makers"; a failed send (offline) redirects with
`told=failed` and the note stays only in the lab. `scripts/feedback.py`
reads them back from PostHog with the personal key `scripts/installs.py`
uses.

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
  is retried the next hour. Never under `TESTING` (the hook checks), and
  never from a build being worked on (`released()`: a `+dev` version).
  Each dev run, upgrade check and screenshot pass makes its own database
  and so its own install id; before that guard, 230 of 234 counted "labs"
  were one developer's machine. A lab server whose image was built from a
  checkout has no VERSION file and reports `server`; it does send.
  `scripts/installs.py` lists every installation that has sent (first and
  last day, days seen, members, what is on), leaving out `+dev` builds and
  the ids in `~/.config/biomanager/own-installs` (testing, `--mine`); the
  ids in `~/.config/biomanager/author-installs` (the maintainer's own real
  use, `--author`) are kept but summed apart from everyone else's;
  `--csv` writes it out.
- **app_settings**: `telemetry:enabled` (`on`/`off`, default on; the first
  survey only says so, without a checkbox. Switched in Settings → General,
  `POST /settings/lab` with `section=heartbeat`, and with **Switch on/off**
  on the Usage report, `POST /feedback/usage/heartbeat`), `telemetry:install_id` (a `uuid4`,
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

## A lab server's updates

`app/server_updates.py` (its docstring is the full account). Only on a lab
server from a release (`applies()`: not `LOCAL_SETUP`, and
`telemetry.released()`); the desktop app has its own (`desktop_updates.py`),
and both compare versions with `app/releases.py` (`is_newer`,
`parse_version`, `summary`), which the server image carries while
`desktop_updates.py` stays beside the desktop app.

- **The check**: the same `after_request` pattern as the counts above (at
  most once an hour per process, a daemon thread), claiming
  `updates:last_check` with a conditional UPDATE so only one worker asks
  GitHub's `releases/latest` a day (User-Agent `BioManager/<version>`, no
  other data). The answer is kept in `updates:latest` (version, page, short
  notes, date); a newer version than this one notifies every active admin
  once (`updates:told`, claimed the same way; category `lab`, link
  `/settings#updates`). `updates:check` = `off` (the switch in Settings →
  Version → Updates, which also says the version to everyone and, on a
  server built from source, how to update it by hand) or `BIOMANAGER_UPDATE_CHECK=0` stops it;
  **Check now** (`POST /settings/updates`, `action=check`) always asks. The
  `updates:` keys are this machine's (`devices.LOCAL_SETTINGS`).
- **Update now** (`action=install`, admins): the app cannot restart its
  own server, so it writes `update-request` (who, which version; written to
  a temporary name, then renamed) into `/control`, which compose mounts from
  `deploy/control/`. `host/install.sh` makes that folder root's with mode
  1733 (others may add a file, not list it or touch root's), writes
  `updater` there (`can_update()` looks for it), and installs
  `biomanager-update.path`, whose `PathExists=` starts
  `biomanager-update.service` → `host/update.sh` as root. The script runs a
  copy of itself (the unpack replaces it), takes the request away, waits on
  `/run/lock/biomanager-stack.lock` (shared with `maintenance.sh`), and
  reads nothing from the request: it asks GitHub for the latest release,
  stops unless it is newer than `VERSION` (a candidate before its
  release, as `is_newer`), backs up, downloads `biomanager-server.tar.gz`
  and `biomanager-image-<arch>.tar.gz` and checks each against the asset's
  `digest`, loads the image with the new bundle's `load-image.sh`, copies the
  bundle over the deploy folder (keeping its owner), `docker compose up -d
  --build`, re-runs `install.sh` (so the units follow the version), and
  waits for the app to be healthy. Each step is written to
  `update-status.json` (`state`: running, done, failed, current; `step`:
  check, backup, download, image, restart; `detail` on failure) through a
  temporary file and a rename; ntfy hears the result. It refuses a checkout
  and a `.env` that pins `BIOMANAGER_IMAGE`.
- **The page** polls `GET /settings/updates/status` every few seconds while
  a request or a run is under way; while the server restarts the request
  fails and the page says so, and once the version it answers with differs
  from the one the page was drawn with, it reloads.
- **Tests**: `tests/test_server_updates.py` runs the check, the routes, and
  `update.sh` itself against stand-in `docker` and `curl` commands
  (`BIOMANAGER_UPDATE_TEST=1` lets it run without root), including a
  checksum that doesn't match and a release that isn't newer.

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

- **Set** one field (owner, status, strain, sex, genotype, cage, litter,
  date of birth, date of death, note, or a lab's own column) across the
  selection (`app.BULK_FIELDS`). Sex must be one of the sheet's choices; a
  date of birth re-dates a litter whose every mouse is selected and moves
  the rest into one new litter of that date, made inside the batch
- **Add to experiment** with a shared treatment group — this is the "N mice
  under the same manipulation" case
- **Sac**, with a confirmation naming the count

Shift-click extends a range, the header checkbox selects everything
*visible* (never filtered-out rows), and each action applies only to records
you may edit, reporting how many were skipped rather than failing outright.
That rule is `app/bulk.py`: `selected()` reads the ticked ids, `run()`
applies a database's own `apply(record)` to each record `may_edit` allows
inside one `audit.batch` (it returns None for changed, `bulk.UNCHANGED`,
`bulk.NOT_YOURS`, or a refusal in words) and counts them in a `Done`, and
`report()` flashes "Set owner: 3 tanks. 1 skipped — not yours to edit." with
`count()`'s noun in the page's language. Cages, tanks, fish, lines,
clutches, mice, a lab's own column, and the inventory and organism
databases run their batches through it (the last two keep their own
wording); plasmids, vials and Add to experiment, which act on the whole
selection at once, keep their own loops.

The bar is generic — `static/selection-bar.js` reads a markup contract, so
another table gets batch actions by adding `data-selection-scope`, row
checkboxes, and a form marked `data-selection-form`. No JavaScript changes.
`capsule()` turns the bar into one glass capsule: the count, a button per
form, `•••` and ×. A form that asks for something (a select or an input)
opens in `.selbar-pop`, a panel fixed above the bar (hung from `<body>`,
since the bar's backdrop filter would make it a containing block); one that
asks nothing submits through `requestSubmit`, its `data-confirm` asked
first. A button's name is `data-selbar-label`, else its submit button's
words, else for a bare Set or Move its control's `aria-label` ("Move to
rack"). The first three forms are on the bar, the rest and any
`btn-danger` one under `•••` (or `data-selbar-more`). Each gets a key:
`data-key`, else the first free initial of a word of its name (never N);
pressing it while rows are ticked runs it. `data-weigh-form` (the mouse
sheet's Weigh, `POST /colony/mice/bulk-weigh`, `grams_<id>` per mouse and
one `weigh_date`) fills a box per ticked mouse as it opens (`selbar:open`).

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

**The number and the mark are different things.** `mice.mouse_id` is the
identity — unique, never reused, what samples, cage cards, experiments and
`@mouse` all link by. `mice.ear_tag` (revision 0024, 40 characters, free
text, not unique; shown as *Custom tag*) is how the animal is marked: an
ear tag number, an ear punch such as RF or LB, a tail tattoo. It sits beside the ID in the sheet, in
the dialog, in Add many, in the export and in search
(`MouseRecord.ear_tag.ilike`), and a spreadsheet column called "custom
tag", "ear tag", "notch", "tattoo" or "tail tattoo" now maps to it — before it had anywhere to go, the
importer read such a column as the Mouse ID. Pointing at the number shows
the tag as well (`_sheet.html`'s `id_cell(aside=…)`), so hiding the column
under **Columns** loses nothing; the column stays because a tooltip cannot
be scanned down a sheet, sorted, printed or read on a phone at the rack.

**Strain** is `mice.strain` (revision 0039, 120 characters, free text): the
mouse's background, the column after the Custom tag. Its cell, the dialog
and Add many offer `datalist#options-strain`, the colony's dropdown choices
for the field `strain` (`MOUSE_PRESET_FIELDS`), which a new lab starts with
as `models.DEFAULT_MOUSE_STRAINS` and an older one gains in 0039 when it has
none. It is in the export, search, the phone sync row (`sync.mouse_row`) and
the importer, where a "Strain" or "Background" column now maps to it (one
called "Strain" went to the genotype before). The column moved the
transgenes along, so the sheet's id is `mice-v5` (`_mouse_sheet.html`).

**Numbers are handed out once.** `services.reserve_mouse_ids()` counts
above the highest mouse and above `app_settings.mouse_id_high`, the highest
ever handed out, so deleting the newest mouse or undoing an Add many never
gives its number again. Saves that take the next free number (New mouse,
Add many, New cage, litters, Wean and distribute, the CSV import) are
wrapped in `next_number_retried`: two people saving at once read the same
highest number, the database refuses the second, and the save is tried
again with a fresh one instead of reporting an ID the person never typed.
A rack place may hold more than one cage: a cage is often set up there
before the one there is ended. Revision 0043 dropped the unique index
revision 0006 had put on the place (`uq_mouse_cages_place`), and added
`mouse_cages.placed_at`, stamped whenever a cage's rack, row or column
changes, by any route (`models._stamp_cage_placements`, a `before_flush`;
off, like the sync clock, while the start-up steps before Alembic run).
`services.slot_order` puts a place's cages in the order they are shown:
those with living mice (`cage_is_active`) first, then the latest placed
(`placed_when`: `placed_at`, else `created_at` for a cage placed before
it, then the id). `slot_cages` groups cages by place and `crowded` says
whether a place earns its "!" (two or more active). `apply_cage_position`
no longer refuses a taken place; it flashes a warning naming the active
cages already there, which a `cage_change` or `cage_new` proposal's
preview shows as a warning. On the grid, `mouse_rack_payload` gives each
cage `live` and `placed`; `rack-grid.js` draws the first by the same
order and, with two or more live, a `.rack-stack-toggle` "!" whose
popover (`.rack-stack`) holds a tile for each. The Unplaced tray leaves
out items with `live` false (empty cages), with a line saying so. A drop on a taken place
(`place_cage`) swaps with the cage shown there; the others stay. Home's
By place view shows the same cage and a `.frz-crowd` "!". Fish tanks,
fly and worm racks and inventory boxes still take one record per place.

**A rack's new layout** (`app/rack_layout.py`). A cage, tank, vial, item or
plasmid keeps its place as a row and a column (0-based for tanks and
plasmids); its name comes from the rack's `rows`, `cols` and `naming`
(`positions.label`). So a save that changes those can rename the places of
what is in the rack, or leave some out, though nothing moved. `KINDS`
(`mouse_rack`, `fish_rack`, `inventory_box`, `stock_rack`, `plasmid_box`)
says, for each kind of rack, what sits in it and in which columns. `plan`
works out, for each thing placed, its name now and its name under each
choice: `keep_names` (to the cell the new layout calls by its name,
`Layout.cell`, which ignores the separator but not "37" counting along a
lettered rack) and `keep_cells` (same row and column); a thing that keeps
both its cell and its name is not listed, and one already outside the old
grid is left where it is. It also counts, per choice, what ends with no
position and the places left holding more than one live thing. `default`
is `keep_names` when the naming changed, else `keep_cells`. Every save
route (`save_mouse_rack` through `_mouse_rack_from_form`,
`zebrafish_update_rack`, `inventory_routes.save_rack`,
`stock_routes.save_rack`, `save_plasmid_box`) calls `check` before
changing anything: with something listed and no `cages=keep_names|keep_cells`
in the form, the save is refused (`refusal`). Otherwise the rack's change
and `apply` run inside `batch` (an `audit.batch` named by `describe`), so
Batch history undoes both together; what has no place left keeps its rack
with no row or column (Unplaced). `form_naming` keeps the rack's naming
when a form sends no `naming_mode`. The old "move them first" refusals on
shrinking a box or rack are gone: shrinking is the same question.
In the page, `rack_dialog(..., layout_kind=)` (`_rack_grid.html`) gives
the form `data-layout-preview` and a hidden `cages`, and a second dialog;
`rack-grid.js` (`setupLayoutCheck`) posts the form to
`/racks/<kind>/<id>/layout` (`rack_layout.preview`, for whoever may change
the rack) before an edit is sent, and shows the list and the two choices
when anything is listed. Racks of the other databases (`OrgLocation`) keep
their naming in `settings`, are not in the audit, and already unplace what
falls outside a smaller grid; they are not covered.

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
.venv/bin/python scripts/test-parallel.py         # -j N: N processes (at most 4 by default)
# or, in one process:
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

`scripts/test-parallel.py` shares the test classes out to up to four
processes as they ask for more (the ones with most tests first), each on
a database of its own, and reports as `unittest` does: the whole suite in
about 2½ minutes on SQLite and 4 on PostgreSQL, where one process takes
20 and 30. On PostgreSQL the processes after the first use databases named
after the one given (`biomanager_test_2`, …), made when missing, which
needs the CREATEDB right.

`benchmarks/` holds measurements rather than checks, on the same throwaway
database (`tests/base.py`). `benchmarks/import_sheets.py` makes mouse and
plasmid sheets with known values, spoils each the way labs' sheets are
spoilt (header names, title lines, merged or filled-down cages, date
styles, ♂/♀, encodings…), imports them through the real upload and match
pages with every suggestion accepted, and counts each value as correct,
kept (in the notes, or warned about), blank or wrong; results go to
`benchmarks/results/`. See `benchmarks/README.md`.

The same suite runs on PostgreSQL, against an empty database it is allowed
to wipe (its schema is dropped first):

```bash
BIOMANAGER_TEST_DATABASE_URL=postgresql://localhost/biomanager_test scripts/test.sh
```

Tests of SQLite-only machinery (`app/integrity.py`) skip there, and the
SQLite → PostgreSQL migration tests only run there. CI runs both.

Every test makes its own uniquely named records and must not depend on
another test having run, or on the ids a fresh database hands out (a
worker's database starts at 1: "Proposal #2" is a real proposal's name
there). A test marked `@unittest.expectedFailure`
documents a known bug; when the bug is fixed it shows up as an "unexpected
success" — remove the marker then. GitHub Actions runs the suite on every
pull request and every push to master, on SQLite and on PostgreSQL, with
`scripts/test-parallel.py -j 4` (`.github/workflows/tests.yml`; Actions →
tests → *Run workflow* tests a branch with no pull request).

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
  (`stock_routes`), and on the **Breeders** tab a Breeder cage with no
  living mouse and nothing since (made, or a mouse died) and a mouse that
  ended before then (`services.breeder_mice`, `recent`). The sheet says
  how many and links to them. Search, exports and the box grid still see
  everything; a breeder card's counts and search still go by all its mice.
  The tab's **Pick** buttons post through one form (`breeder-pick`), and
  its cards are `content-visibility: auto`.

What a person waits for is mostly the browser, not the server:
`--browser` also opens each page in Chromium and reports the time to its
load event, the main thread's busy time, its DOM nodes and JS heap.

```bash
.venv/bin/python scripts/load-test.py /tmp/bm-load --reuse --browser
```

What keeps the browser's side short is under *Small shared rules*: row
forms in a `<template>` (`row-forms.js`), long choice lists sent once
(`data-choices`), the rows past the first page hidden until the sheet is
drawn, a sheet of over 300 rows keeping the rows it has not shown
off the page (`_parkLater`), and an inventory of over 500 items paged by
the server (`LAZY_OVER`).

At 100k mice on SQLite (2.0.0), the server makes the mouse, cage and
litter sheets in 3–6 s, 20–25 MB each, and an inventory in 7 s, 60 MB.
In Chromium the mouse sheet then opens in 7.5 s (77 s before 2.0.0), the
tanks in 11 s (127 s), the fly vials in 6.6 s (97 s), the cages and
litters in about 10 s. After 2.0.0 the **Breeders** tab went from 40 s
to 2 s (16 MB to 5 MB), and an inventory of about 5,000 listed items
from 23–27 s to 11–12 s (6 KB of markup a row, from 13 KB) in 2.0.1, and
to under 3 s in 2.0.2, paged by the server: the server takes about 1 s
(it was 4 s) for a 6 MB page (33 MB), and turning a page, a sort, a
search or a chip takes about a second, its 100 rows fetched in under
0.1 s. Print, fetching every row, takes about 7 s; Export, fetching
only their words (2.0.7), 5 s the first time on one worker (it was 10 s)
and under half a second after, and the page is 3.8 MB, its box grid
fetched when shown. On the
mouse sheet (2,400 living mice) a page turn, search or sort takes under
a second, however many pages have been turned: before 2.0.2 each one
turned left its rows in the table and the next search took 3–5 s.

In a lab of 4,000 mice, in Chromium (before 2.0.0 → 2.0.0 → after):
mice 6.2 → 2.7 s, cages 8.0 → 3.1 s, litters 4.0 → 1.5 s, fish
31.5 → 3.2 s, reagents 5.2 → 2.6 → 1.8 s, samples 6.0 → 2.8 → 1.8 s,
Breeders 0.5 s. With rows taken off the page from 300 (2.0.5,
was 1,000): one person's 950 living mice 20 → 5 s, 500 fish 5–6 → 3 s;
sheets under 300 rows are unchanged.

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
  `fmt_day` (`app/days.py`) and `relative_day` already follow the language.
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
- **Which language**: the person's choice in **Settings → Appearance & language**
  (kept per person, `language.<username>` in the settings table), else the
  language the browser or computer asks for first (`Accept-Language`); the
  sign-in page has a 中文 / English switch (`POST /language`).
  `session["lang"]` holds the choice for this browser. Signing in keeps it
  (`security.start_session`), and with nothing chosen in Settings it becomes
  the person's (`load_current_user`). The desktop app's own window goes by
  the computer's language (`i18n.system_language`, set as
  `COMPUTER_LANGUAGE` by `desktop.py`) before the header: a Mac's web view
  reports English unless the app lists Chinese among its languages, which
  `Biomanager.spec` now does (`CFBundleLocalizations`).
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

**The Gitee copy.** `.github/workflows/gitee.yml` pushes master and the
version tags to gitee.com under the same owner and name on every push, for
people in China, where GitHub can be slow (the 知乎 and CSDN articles link
it). It needs the `GITEE_TOKEN` secret, a Gitee personal access token with
"projects"; without it the job does nothing. Release files stay on GitHub.

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
- **The clips are the app, replayed, not video** (as Raycast shows its
  app): `scripts/live-clips.py` runs each walk (the launch posts' walks in
  `promo/clips/`, plus the site's own census, litter and cage-card walks)
  in a fresh demo lab, driven by `scripts/feature-clips.py`'s Director, and
  records the pages with rrweb (`@rrweb/record`, a dev dependency in
  `frontend/`) instead of filming them. Each clip is
  `site/assets/clips/<name>.json` (segments: a desktop window or a phone,
  each with its size, length, zoom keyframes and rrweb events; a few hundred
  KB, about a tenth of that gzipped) and `<name>.webp`, the first frame at
  2×, which is the `<video>`'s poster in the page and all a browser
  without scripts shows. The recordings name the app's files as `%BM%/`;
  the stylesheets, fonts, `icons.svg` and pictures they load are copied to
  `site/assets/app/` (pictures the lab served, such as the app icon, to
  `site/assets/app/lab/`), and the replayer to
  `site/assets/vendor/rrweb-replay.js`. `bmClips.mount(video)` in `site.js`
  puts a player in the `<video>`'s place: it loads the replayer and the
  clip when one is to play, replays it in an iframe scaled to fit (light,
  whatever the visitor's theme), does the walk's zooms as a transform, plays
  a phone segment in a phone frame, and loops or says it ended. The words
  are drawn by the visitor's browser, so they are as sharp as the page on
  any screen. Rerun `live-clips.py` after a change people would see in a
  clip; a walk that no longer finds its button fails alone, by name.
- **The feature stage** (`#see` on the front page; dark on a dark page,
  white with pale glass on a light one) plays a short clip per tab of its
  dock. Only the chosen clip loads; clips follow one another until someone
  picks a tab, and pause off screen. A tab's caption is in its `data-claim`
  and `data-more`.
- **Clips play by themselves** on the front page, Features and the guide,
  with no play button or controls. With reduced motion the stage stays
  still (it plays the tab someone picks), a guide clip waits for a click,
  and a Features window plays what was clicked. The AI assistants card's
  clip is the one video (`scripts/assistant-clip.py`, which draws the
  assistant's window): Apple's browsers get its `.mp4` (they can stall on
  WebM), the others the smaller `.webm`; if the browser won't start it on
  its own (Safari in Low Power Mode) it starts at the visitor's first tap,
  click or key, and the browser's own play button is hidden over it.
- **Features** has no screenshots: each database and each thing that works
  everywhere is a glass and neon card (`.nc`) drawn in the page, with its own
  colours (`--c1`/`--c2` for the neon, `--a`/`--b` for the card on a dark
  page), a line drawing in inline SVG and a pane of glass with a little of
  the app's own words. On a dark page the card is dark and its drawing
  glows; on a light page it is white with a soft wash of its colour, a
  deeper drawing and white frosted glass. A card with `data-clip` opens that
  clip (from `site/assets/clips/`, made by `scripts/live-clips.py`) in a
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
  two cross the near one passes over the far one. Back to front: a shadow,
  the soft coloured cores, the far glass (both whole backbones) tinted teal
  or blue, the paler base pairs, then where a backbone comes in front a veil
  the colour of the page that hides the strand behind, its core, its glass
  and tint, each glass with bright edges (each strand's tint is kept inside
  its own outline, so it never spills onto the other where they cross); soft teal and blue light drifts underneath. Every layer is a
  plain picture with its blur drawn in, which the CSS only slides: no
  `backdrop-filter`, mask or CSS `filter`, which Safari draws on a long moving
  layer with gaps and straight-edged holes. One turn of every layer, with
  100px of room above and below for the blurs, is drawn by
  `scripts/hero-helix.py` into `site/assets/helix/`, and the helix slides by
  one turn on a loop, which looks just like it turning. The product shot rises out of its lower edge.
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
leaves pre-releases out; `is_newer`, in `app/releases.py` with a lab
server's check, puts `1.0.0-rc.1` before `1.0.0`), and this computer's
`desktop-prefs.json` (automatic check, skipped version, appearance, zoom) in
the data folder. Set `BIOMANAGER_MENU_DUMP=<file>` to
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

**Linux without the window's libraries** (`desktop_linux.py`). The Linux
window is Qt WebEngine, which loads the computer's own libEGL, Wayland
and OpenGL libraries; a server edition, WSL or a trimmed desktop may lack
one, and `webview.start()` then raises before any window shows. On Linux
`desktop.main()` catches that and calls `run_in_browser()`: the app in the
default browser, and a zenity or kdialog message whose OK ends the process;
with neither, it runs until stopped (Ctrl+C), and says so on stderr.
A display without working OpenGL (a VM, remote X, a virtual screen) is
worse: Qt aborts in C++ ("Could not initialize GLX"), past any except. So
`desktop_linux.software_rendering` draws the window in software
(`QT_XCB_GL_INTEGRATION=none`, `QT_QUICK_BACKEND=software` and Chromium's
`--disable-gpu`: without the first and last it still aborts, without the
second the window is black) when there is no graphics device (`/dev/dri`),
or when the window didn't appear last time: `window-starting` in the data
folder is written before `webview.start()` and removed on `events.shown`,
and once it was found the choice is kept (`software_window` in
`desktop-prefs.json`). Someone's own `QT_XCB_GL_INTEGRATION` wins. The
release workflow's Linux job runs `scripts/bare-linux-check.sh` on the
AppImage: Ubuntu 22.04 in Docker with none of those libraries, where it must
open in the browser and keep answering.

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
| `BIOMANAGER_UPDATE_CHECK` | on | `0`: never ask GitHub for a newer release (`app/server_updates.py`) |
| `BIOMANAGER_PLANNOTATE` | — | the pLannotate command, which turns on **Annotate with pLannotate** (`app/plannotate.py`) |
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
  Settings → People & access. `scripts/reset-password.py NAME --enable` does the
  same from the command line on SQLite.
- **Start a new lab** (`/start-new-lab`, linked from the sign-in page) is the
  desktop app's way out of a lab nobody can sign in to. Only the person at
  the computer gets it (`devices.on_this_computer`: `LOCAL_SETUP`, loopback,
  not the network listener); elsewhere it is a 404. It only writes the
  request file `data/start-new-lab`; `desktop.py` calls
  `paths.set_lab_aside()` before importing the app, so the database is moved
  before anything opens it: everything in the data folder except what is the
  computer's own (`desktop-prefs.json`, `window/`) goes to
  `data/old-labs/<YYYY-MM-DD HHMMSS>/`, with the uploads folder as `uploads/`.
  Nothing is deleted; a failed move leaves the request for the next start.
  `LAB_SET_ASIDE` carries the folder to the sign-in page until the new lab
  has an account. The page's **Close BioManager** calls
  `DesktopApi.quit` (desktop_menu.py).
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
- **Uploads need a login**, get unguessable names, go only to someone who
  may see a record they belong to (Access control → Uploaded files), and
  are served so that an uploaded HTML or SVG file downloads rather than
  running in the app.
- **Google Calendar tokens are encrypted in the database** with a key
  derived from the signing key. Tokens saved before are encrypted at the
  next start. Losing or changing the key only means reconnecting Google
  Calendar, which is why backups include the data folder's `secret_key`.

