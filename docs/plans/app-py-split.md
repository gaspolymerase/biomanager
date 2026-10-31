# Plan: app.py comes apart, round 2

Status: not started.

`app/app.py` is 10,473 lines and holds 159 routes. Most of them belong to
areas that already have their own module: the colony (51 routes), plasmids
(32), the notebook (24), the calendar (15). The first refactor round moved
zebrafish out this way and the file has grown back since. Nothing a lab
sees changes in this round; every later round gets cheaper.

## The rule that makes it safe

**The URL map does not change.** Not a path, not an endpoint name, not a
method. A route that moves keeps the name every page, every script and
every bookmark already uses.

That rules out Flask blueprints for this work: a blueprint renames
`colony` to `colony.index`, and the app has **2,100 `url_for` calls** on
these names (742 in templates, 1,358 in Python). The tests ask for URLs,
not endpoints, so they would not catch a missed one.

Instead each area keeps `@app.route` and moves to its own module, exactly
as `app/zebrafish_routes.py` already does. Phase 1 makes that pattern sound
(see below); after it, a module imports the Flask app and the shared
helpers from neutral modules, and app.py imports the area modules.

A snapshot test (`tests/test_url_map.py`) writes every rule, endpoint and
method to a file and fails on any difference. It goes in before anything
moves, so each phase has to prove it changed nothing.

## What moves where

| Area | Routes | Lines in app.py | Goes to |
| --- | --- | --- | --- |
| Colony: mice, cages, litters, strains, racks, experiments, batch actions | 51 | 3364–5951 | `app/colony_routes.py` |
| Plasmids: list, detail, boxes, feature library, lineage, import/export | 32 | 8595–10266 | `app/plasmid_routes.py` |
| Notebook: pages, folders, comments, favourites, libraries | 24 | 6744–6956, 7573–8161 | `app/notebook_routes.py` |
| Calendar: events, repeats, subscriptions, Google link, tasks | 15 | 5997–6692 | `app/calendar_routes.py` |
| Accounts: login, logout, register, settings, admin, start/restore a lab | 13 | 2669–3350 | `app/account_routes.py` |
| The rest: home, search, import, batches, audit, utilities, orders, samples | 14 | scattered | `app/misc_routes.py`, or the module each belongs to |

## Order of work

1. **Make the pattern sound.** `app = Flask(__name__)` moves to
   `app/flask_app.py`; the shared decorators and helpers (`login_required`,
   `admin_required`, `autosave_response`, `next_number_retried`,
   `log_delete`, `stamp_updated`) move to `app/web.py`. An area module then
   imports from those two and never from `app.py`, so the circular import
   and the "app.py imports it last" rule both go away.
   `zebrafish_routes.py` moves onto the new imports in the same step.
   Add `tests/test_url_map.py` first of all.
2. **Colony** — the biggest, and the one that pays back most.
3. **Plasmids** — the area just worked on, so it is fresh.
4. **Notebook and calendar.**
5. **Accounts and the rest**, and whatever is left that belongs in a module
   that already exists.
6. **Read what remains.** app.py should be the application itself: the
   Flask app, error handlers, context processors, template filters, the
   navigation, and nothing else. The route blocks above come to some
   7,500 lines, so about 2,900 are left.

Each phase is one commit that moves code and changes nothing else, with the
URL map snapshot unchanged and the full suite green.

## What stays in app.py

The app object's own setup: error handlers (403, 404, 500, and the
SQLAlchemy ones), `before_request` (`follow_lab_timezone`,
`load_current_user`), the context processors (`inject_icon`, `inject_user`,
`inject_appearance`, `inject_nav`, `inject_asset_version`), the template
filters, `NAV_SECTIONS` and the navigation helpers, and the asset-version
`after_request`.

## Risks

- **A helper used by two areas.** Moving it with one of them breaks the
  other. Each phase checks what the moved block calls, and anything shared
  goes to `app/web.py` instead.
- **Import order.** Phase 1 exists to remove this risk rather than carry it
  into five more modules.
- **A page nothing covers.** The URL map snapshot proves the routes are
  still there, not that they still work. Where a moved page has no test,
  the phase adds one.

## Done when

`app/app.py` is about 2,900 lines, every area is in its own module, the URL
map is byte-for-byte what it was at the start of the round, and the full
suite is green on SQLite and PostgreSQL. `docs/DEVELOPMENT.md` says where
each area's pages now live. No release note: a lab sees nothing change.
