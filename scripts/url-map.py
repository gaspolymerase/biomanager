"""Every route the app answers, as one line each.

    python scripts/url-map.py            # print it
    python scripts/url-map.py --write    # save it to tests/url_map.txt

tests/test_url_map.py compares the saved file with the app's own map, so a
route that moves between modules has to keep its path, its endpoint name and
its methods. Pages, scripts and bookmarks link by those names: `url_for` is
called on them some 2,100 times across the templates and the app.

Run --write only when a change is meant to add or remove a route, and say so
in the commit; a line that changes for any other reason is a mistake.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAVED = ROOT / "tests" / "url_map.txt"
# Ignored on a route: the app only ever answers the rest.
UNINTERESTING = {"HEAD", "OPTIONS"}


def lines() -> list[str]:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("BIOMANAGER_DATA_DIR", tempfile.mkdtemp())
    from app.app import app

    out = []
    for rule in app.url_map.iter_rules():
        methods = sorted((rule.methods or set()) - UNINTERESTING)
        out.append(f"{rule.rule}\t{rule.endpoint}\t{' '.join(methods)}")
    return sorted(out)


def main() -> int:
    text = "\n".join(lines()) + "\n"
    if "--write" in sys.argv:
        SAVED.write_text(text, encoding="utf-8")
        print(f"wrote {SAVED.relative_to(ROOT)}: {text.count(chr(10))} routes")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
