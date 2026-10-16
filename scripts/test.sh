#!/usr/bin/env bash
# Run the test suite (standard-library unittest; no extra packages).
#
#   scripts/test.sh                      # everything, in up to four processes
#   scripts/test.sh tests.test_mice      # one module (or Class, or Class.test)
#
# The tests run on a throwaway SQLite database in a temp folder; data/ is
# never opened. Everything runs through scripts/test-parallel.py, each
# process on its own database. Uses .venv/bin/python when there is one,
# else python3.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
if [ $# -gt 0 ]; then
  exec "$PY" -m unittest "$@"
fi
exec "$PY" scripts/test-parallel.py
