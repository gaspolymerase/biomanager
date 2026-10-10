# BioManager: notes for Claude

How the app is built and tested is in `docs/DEVELOPMENT.md`; running a lab
server is in `deploy/RUNBOOK.md`.

This repository is public, and `deploy/` goes to every lab in the server
bundle: nothing here names a maintainer's own server, account, address or
institution. Our own server's runbook is kept outside the repository; a
change to `deploy/RUNBOOK.md` usually belongs in that copy too. The words to
keep out are the `PRIVATE_WORDS` secret (and a git-ignored `.private-words`
locally), which `tests/test_deploy.py` checks against.

## Changing the database's shape

Never add a column or table by hand-written ALTER at start-up any more
(`services.ensure_schema_updates` is frozen at 0.8). Make an Alembic
revision after the newest in `migrations/versions/`, using
`migrations/helpers.py` so it also runs where the change already exists,
and run `python scripts/upgrade-check.py` before a release (and with
`--postgres <url>` for lab servers; CI runs both): every earlier release's
database must still open with nothing lost.

## The docs follow the app

A change people will see or use is not finished until the pages that describe
it say so. In the same piece of work, update:

| Where | What it covers |
| --- | --- |
| `README.md` (this repo) | What each database and function does, features across the app, accounts, data and backups |
| `site/index.html` | The website's front page: the tour of each database, the feature cards, downloads |
| `site/guide/<page>.html` and `site/zh/guide/<page>.html` | The user guide: one page per area (a sheet, finding things, each database, calendar, notebook, working as a lab, phones, data), in both languages |
| `site/server.html`, `site/deploy-with-ai.md`, `site/llms.txt` | Only when running a server, deploying or the downloads change |
| `docs/DEVELOPMENT.md` | Internals: tables, modules, how things fit together |

The website is `site/`, served by GitHub Pages at
gaspolymerase.github.io/biomanager: pushing a change under `site/` to master
publishes it within a minute (`.github/workflows/pages.yml`), so push the
site with the app change it describes, never ahead of it. Releases are
published here only. The old gaspolymerase/biomanager-app repository is
archived: it redirects to the website and holds 0.10.1, which desktop apps
from before the move update to, and which looks here for every update after.

- Write for the people in the lab: what it does for them and where to find it,
  in plain words, as the surrounding text does. Name buttons as the app does.
- Put a change where a reader would look for it; don't add a "what's new"
  list. Fix text the change made wrong (a removed button, a renamed tab).
- A guide page is edited between its `<!-- page -->` and `<!-- /page -->`
  marks; everything around them is rebuilt. After editing one, run
  `python scripts/guide-pages.py --search` (the pages, then the Pagefind
  index) and `python scripts/llms-full.py` (`site/llms-full.txt`, which is
  committed and which `tests/test_llms_full.py` checks is current).
- Before publishing the website, check the edited pages still parse (every
  list and section closed).
- When a change alters a page that has a screenshot, retake the screenshots
  from a fresh demo lab (`scripts/demo-data.py`, then `scripts/screenshots.py`)
  and copy them to `docs/screenshots/` and `site/assets/screenshots/`.
- Internal changes with nothing to see (a refactor, a test, a fix that restores
  documented behaviour) need no docs.

## The mouse colony's words

What a cage's **Purpose** means, as the lab's maintainer set it. Don't
read them as synonyms or add meanings of your own:

- **Breeding** is a mating cage, mating now. It is the only cage with
  **Litter born**, **Genotyping** and **Wean**, and its litters are its
  pups (`services.is_breeding_cage`).
- **Breeder** is the lab's breeding stock: mice kept to be bred. Each
  person keeps part of the lab's stock, so a Breeder cage starts shared
  with the lab, and the **Breeders** tab lists these cages for anyone to
  **Pick** from (`services.is_breeder_cage`, `models._breeder_cages_start_shared`).
  It has no litter buttons.
- **Experiment** is a cage of mice in an experiment.
- There is no **Stock** and no **Retired** purpose. **Active** means the
  cage holds living mice, nothing else (`services.cage_is_active`); an
  empty cage is simply not active. **Retire** (a batch action) takes an
  empty cage out of its rack and clears its purpose.
- The bar above the cages is **All · Active ·** one button per purpose,
  and the purposes are the lab's own list: the colony's **Configure →
  Cage purposes**, the same list as its Settings tab's dropdown choices
  (`dropdown_options`, field `purpose`). A new lab starts with Breeder,
  Breeding and Experiment.

## Two rules the tests enforce

- **One string literal per `gettext()` and `ngettext()` call.** The
  translation check reads the first literal only, so a message split over
  two lines reads as untranslated however long the line has to be. Every
  English text needs its Chinese in `app/translations/zh/*.json`, and one
  English text may not have two different Chinese translations
  (`tests/test_i18n.py`).
- **Open text files with `encoding="utf-8"`** — `read_text`, `write_text`,
  `open`. A Chinese Windows machine defaults to GBK, where the app would
  read its own files wrongly; `tests/test_desktop_windows.py` checks every
  call.
