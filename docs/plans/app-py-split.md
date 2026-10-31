# app.py comes apart: the round, as done

`app/app.py` was 10,473 lines and held 159 routes, most of them belonging
to areas that already had a module. It is now 1,175 lines, holds no
database's pages, and nothing imports anything from it but the app
itself. Nothing a lab sees changed.

## Where things are now

| Module | What |
| --- | --- |
| `app/flask_app.py` | The Flask application object, and nothing else |
| `app/app.py` | The application around it: configuration, blueprints, signing a request in, error pages, what every template is given (navigation, filters), and its few pages about itself |
| `app/web.py` | What every page needs: `login_required` and the like, where someone lands, how a save answers, the lab's time, a person's badge, the databases' names, the app's icon |
| `app/text_cells.py` | A sheet's fields drawn as text until used |
| `app/colony_routes.py` | The mouse colony |
| `app/zebrafish_routes.py` | The zebrafish database (moved in the first round) |
| `app/plasmid_routes.py` | Plasmids |
| `app/calendar_routes.py` | The calendar's events and to-dos |
| `app/notebook_routes.py` | The notebook's tabs, pages and templates |
| `app/account_routes.py` | Signing in, Settings, the admin's pages |
| `app/home_routes.py` | `/` and Home |
| `app/records_routes.py` | Search, CSV import, Batch history, the change log |
| `app/utilities_routes.py` | Utilities |

Two helpers found better homes on the way: setting a lab's own column on
many records is `bulk.set_custom_column` (mice, plasmids and tanks all use
it), and reading what was typed as a record's number is
`formutil._record_number`.

## The rule that kept it safe

**The URL map did not change**: not a path, an endpoint name or a method.
Pages link by endpoint name some 2,100 times and the tests ask for URLs,
so a blueprint's renaming would have broken pages no test opens. Each area
keeps `@app.route` on the object from `flask_app.py`.
`tests/test_url_map.py` checks the app's map against `tests/url_map.txt`;
it went in first and every commit kept it identical. It stays: a route
added or removed on purpose updates it with `python scripts/url-map.py
--write`.

## How each move was checked

- What the moved code needs from `app.py`, found from the code, scope by
  scope; anything shared went to `web.py` or its natural module, never
  left for a module of pages to import from `app.py`.
- Nothing moved registers a hook or works at import (so when `app.py`
  imports it doesn't matter), checked each time.
- pyflakes: no undefined name in any module, and every import `app.py`
  still doesn't use accounted for.
- Tests that patch a function where a route no longer calls it from.
- The full suite after each commit.

## What it found

- **A test that would have passed with nothing tested.** `test_mice`
  simulated two saves taking the same mouse number by patching
  `app.app.next_mouse_id`. Once the route lived in `colony_routes.py`
  it called that module's own, the stand-in was never asked, the save got
  a fresh number at once, and every assertion still held. It patches
  `colony_routes` now and checks the stand-in was asked.
- **Paths with no test**: setting a lab's own column on many plasmids or
  tanks. One test now covers both, and fails if either call is wrong.
- **A route the round's own lists missed**: `/language` is declared with
  `@app.post`, not `@app.route`. The saved map holds every route however
  it is declared, so it moved with the sign-in page it belongs to.
- **31 comments and docstrings** named a function as `app.something` after
  it had moved, one of them since the zebrafish moved in the first round.
- **Five dead statements** pyflakes had flagged in `app.py` all along.
