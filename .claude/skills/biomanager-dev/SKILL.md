---
name: biomanager-dev
description: Build, run, test, document and release BioManager itself. Use when changing this repository's code — a feature, a fix, a refactor, the website or the desktop and server builds — and when preparing a release. Covers running the app with a demo lab, the test suite, screenshots, the rules the tests enforce (translations, encoding, database revisions), what to update in the docs, and how a release is cut. Not for using BioManager to record a lab's work.
---

# Working on BioManager

A Flask app (`app/`), its own JavaScript and Tailwind (`app/static/`,
`frontend/`), a public website (`site/`), a desktop build and a server
bundle (`deploy/`). `CLAUDE.md` holds the rules; `docs/DEVELOPMENT.md` is
the long explanation of how everything fits together, and is the first
place to look for any module. This skill is the procedural part: the
commands, in the order a change needs them.

## Run it with a demo lab

Never develop against a real lab's data. Make a demo one and point the
server at it:

```bash
python scripts/demo-data.py /tmp/bm-demo          # a lab with mice, plasmids, a notebook…
BIOMANAGER_DATA_DIR=/tmp/bm-demo PORT=5466 python run.py
```

It prints the admin's name (`alex`) and writes the password to
`/tmp/bm-demo/demo-password`. `FLASK_DEBUG=1` reloads on edit. Rebuild the
demo (delete the folder and run the script again) whenever you change
`scripts/demo-data.py` or need a clean lab.

To see a change in a browser, drive it with Playwright; Chromium is
already installed, and Python needs its path:

```python
chromium.launch(executable_path="/opt/pw-browsers/chromium")   # Python
# Node: chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
```

Watch the console for page errors as well as looking at the screenshot: a
JavaScript error shows there and nowhere else.

## Tests

CI runs the whole suite on **SQLite and PostgreSQL**, on **Python 3.11**.
Both have to pass, so no 3.12-only syntax and no SQLite-only SQL
(`json_extract` is the usual trap — do that work in Python).

```bash
python scripts/test-parallel.py -j 4              # the whole suite, as CI runs it
python -m unittest tests.test_plasmids            # one file while working
BIOMANAGER_TEST_DATABASE_URL=postgresql://… python scripts/test-parallel.py   # the PostgreSQL run
```

The suite takes about half an hour in one process, so start it in the
background and carry on. `tests/base.py` gives each process its own
database and folders.

Two failures are expected in a container running as **root**:
`test_lab_key.ForTheBackups` checks a folder it cannot write. It passes in
CI. Anything else that fails is yours.

Don't edit files while a background suite runs: tests read the working
tree, and the results come out meaningless.

## The rules the tests enforce

- **One string literal per `gettext()` / `ngettext()`.** The check reads
  the first literal only, so a message split over two lines reads as
  untranslated however long the line has to be. Every English text needs
  its Chinese in `app/translations/zh/*.json`, and one English text may
  not have two different Chinese translations (`tests/test_i18n.py`).
  Page scripts (`t("…")`) read **`js*.json` only**, so a string used in
  both a template and JavaScript belongs in a `js-*.json` file.
- **Open text files with `encoding="utf-8"`** — `read_text`, `write_text`,
  `open`. A Chinese Windows machine defaults to GBK
  (`tests/test_desktop_windows.py`).
- **Text sizes come from the type scale** (`tests/test_design.py`): a
  `text-[14px]` or `text-[18px]` fails; use the nearest allowed size.
- **The database changes only through an Alembic revision** after the
  newest in `migrations/versions/`, using `migrations/helpers.py`. Then
  `python scripts/upgrade-check.py` (and `--postgres <url>`): every
  earlier release's database must still open with nothing lost.
- **Chinese guide pages**: a bold label needs a half-width space from the
  Chinese beside it, no space after Chinese punctuation, and must not
  start an indented line (`tests/test_site.py`).
- **`deploy/` ships to every lab**: nothing there names a maintainer's own
  server, account, address or institution (`tests/test_deploy.py`).
- **A route keeps its path and its endpoint name** (`tests/test_url_map.py`,
  against `tests/url_map.txt`). Pages link by endpoint name some 2,100
  times and the other tests ask for URLs, so a rename breaks pages no test
  opens. Adding or removing a route on purpose: `python scripts/url-map.py
  --write`, and say so in the commit. Moving a route's code: patch it in
  tests where the route now calls it from, or the stand-in is never called.

## Front-end

```bash
cd frontend && npm run build:css      # after editing frontend/src/tailwind.css or adding classes
```

Tailwind only emits classes it finds in `app/templates/**/*.html` and
`app/static/*.js`, so rebuild after adding one. The built
`app/static/tailwind.css` is committed.

## The docs follow the app

A change people see is not finished until the pages that describe it say
so, in the same piece of work. `CLAUDE.md` has the table of what goes
where: `README.md`, `site/index.html` and `site/features.html`, the guide
pages in both languages, `docs/DEVELOPMENT.md` for internals.

A guide page is edited between its `<!-- page -->` and `<!-- /page -->`
marks. After editing one:

```bash
python scripts/guide-pages.py --search    # rebuilds the pages, then the Pagefind index
python scripts/llms-full.py               # site/llms-full.txt, which a test checks is current
```

## Screenshots

When a change alters a page that has one, retake it from a **fresh** demo
lab, so no earlier experiment shows:

```bash
rm -rf /tmp/bm-demo && python scripts/demo-data.py /tmp/bm-demo
BIOMANAGER_DATA_DIR=/tmp/bm-demo PORT=5466 python run.py &
python scripts/screenshots.py http://127.0.0.1:5466 /tmp/bm-demo docs/screenshots plasmid-digest
```

Naming shots takes only those; with none it takes them all. A shot is
described in `DESKTOP` in `scripts/screenshots.py`, where a step can be a
selector to click, `("fill", selector, text)`, or `("run", script)`.
Convert each to WebP at 2000×1250 and copy it to **both**
`docs/screenshots/` and `site/assets/screenshots/`:

```python
from PIL import Image
im = Image.open("out/plasmid-digest.png").convert("RGB").resize((2000, 1250), Image.LANCZOS)
for d in ("docs/screenshots", "site/assets/screenshots"):
    im.save(f"{d}/plasmid-digest.webp", "WEBP", quality=82, method=6)
```

## Releasing

A release is a **tag**; the workflow builds and publishes everything.

1. Check what the last released version was — `git tag -l "v*"` and
   `docs/release-notes/` — because another session may have released
   since your branch started. Pick the next number.
2. Write `docs/release-notes/<version>.md`: **New**, **Works
   differently**, **Fixed**, then **Updating**, each line in English with
   its Chinese underneath, as the last release's file has it.
3. Add the short note the app shows after an update to `NOTES` in
   `app/whats_new.py` (newest first), with each line's Chinese in
   `app/translations/zh/whats_new.json`. A test fails when the newest
   release has no entry.
4. Name the version on the website: `site/index.html`,
   `site/download.html` and both under `site/zh/`. A test checks it
   matches the newest notes.
5. Merge to master, then tag:

```bash
git tag v<version> && git push origin v<version>
```

The release workflow builds the Mac (Apple silicon and Intel), Windows,
Linux and Android apps and the server bundle, checks each desktop build
starts, and publishes them. The website's download buttons point at
`releases/latest/download/<file>`, so **file names must not change**. A
tag with a hyphen (`v2.6.0-rc.1`) publishes as a pre-release.

If a build is cancelled rather than failed, look at GitHub's runner
images before assuming the code is wrong: a retired or browned-out runner
cancels the job.

## Conventions worth keeping

- Write for the people in the lab: what it does for them and where to
  find it, in plain words, naming buttons as the app does.
- A database's pages are in their own module (`app/colony_routes.py`,
  `app/plasmid_routes.py`, ... or a blueprint), which imports
  `app/flask_app.py` and `app/web.py` and **never `app.py`**. What every
  page needs goes in `app/web.py`; `docs/DEVELOPMENT.md` (Where the pages
  are) lists the modules.
- Keep the biology pure and the routes thin: `app/digest.py`,
  `app/cloning.py`, `app/mutagenesis.py`, `app/gateway.py` and
  `app/plasmid_compare.py` take sequences and return results, with no
  database, request or `gettext` in them; the routes turn a problem into
  one sentence in the reader's language.
- Stored plasmid features are **0-based with the end included**, and one
  crossing the origin keeps `start > end`.
- A comment says why, not what. Match the surrounding density.
