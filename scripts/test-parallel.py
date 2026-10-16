#!/usr/bin/env python3
"""Run the test suite in several processes at once.

    python scripts/test-parallel.py            # every test, one process a CPU (at most 4)
    python scripts/test-parallel.py -j 2       # two processes
    python scripts/test-parallel.py tests.test_mice tests.test_cages

Each process runs on its own database, as `python -m unittest` does on
one (tests/base.py): its own SQLite file in a temp folder, or, with
BIOMANAGER_TEST_DATABASE_URL, its own PostgreSQL database named after the
one given (biomanager_test, biomanager_test_2, …; created when missing,
which needs the CREATEDB right, and wiped as that one is). The test
classes are shared out as the processes ask for more, longest first by
how many tests they hold, so they finish together. The suite allows this:
no test relies on another having run, and tests/base.py makes each
process its own folders and database.

It prints each test class as it ends, then every failure and error with
its traceback, then `Ran N tests` and OK or FAILED as unittest does, and
exits 1 when anything failed. Standard library only.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def classes_of(suite) -> list[str]:
    """The ids of the test classes in a suite, with how many tests each holds."""
    out: dict[str, int] = {}

    def walk(s):
        for item in s:
            if isinstance(item, unittest.TestSuite):
                walk(item)
            elif isinstance(item, unittest.TestCase):
                cls = type(item)
                if cls.__name__ == "_FailedTest":
                    out[item.id()] = out.get(item.id(), 0) + 1   # a module that would not import
                else:
                    key = f"{cls.__module__}.{cls.__qualname__}"
                    out[key] = out.get(key, 0) + 1
    walk(suite)
    return sorted(out, key=lambda k: -out[k])


def database_for(worker: int, url: str) -> str:
    """Worker n's PostgreSQL database: the one given for the first, a
    sibling named after it for the others (created when missing)."""
    if not url or worker == 0:
        return url
    import psycopg
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    name = f"{parts.path.lstrip('/')}_{worker + 1}"
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as pg:
        if not pg.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            pg.execute(f'CREATE DATABASE "{name}"')
    return urlunsplit(parts._replace(path=f"/{name}"))


def work(worker: int, url: str, names, results) -> None:
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    if url:
        os.environ["BIOMANAGER_TEST_DATABASE_URL"] = database_for(worker, url)
    import tests.base  # noqa: F401  (this process's database, before any test module)
    loader = unittest.TestLoader()
    while True:
        name = names.get()
        if name is None:
            return
        start = time.perf_counter()
        result = unittest.TestResult()
        try:
            suite = loader.loadTestsFromName(name)
        except Exception as exc:   # a module that does not import
            results.put({"name": name, "run": 1, "failures": [], "errors": [(name, repr(exc))], "skipped": 0,
                         "expected": 0, "unexpected": [], "seconds": 0.0, "worker": worker})
            continue
        suite.run(result)
        results.put({
            "name": name, "run": result.testsRun, "worker": worker,
            "failures": [(t.id(), text) for t, text in result.failures],
            "errors": [(t.id() if hasattr(t, "id") else str(t), text) for t, text in result.errors],
            "skipped": len(result.skipped), "expected": len(result.expectedFailures),
            "unexpected": [t.id() for t in result.unexpectedSuccesses],
            "seconds": time.perf_counter() - start,
        })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-j", "--jobs", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("names", nargs="*", help="test modules, classes or tests (default: every test)")
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    # Discovering imports the test modules, which import the app: in a
    # process of its own, so this one stays clean for the workers.
    with mp.get_context("spawn").Pool(1) as pool:
        work_list = pool.apply(_discover, (args.names,))
    if not work_list:
        print("No tests found.")
        return 1
    url = os.environ.get("BIOMANAGER_TEST_DATABASE_URL", "").strip()
    jobs = max(1, min(args.jobs, len(work_list)))
    ctx = mp.get_context("spawn")
    names, results = ctx.Queue(), ctx.Queue()
    for name in work_list:
        names.put(name)
    for _ in range(jobs):
        names.put(None)
    start = time.perf_counter()
    workers = [ctx.Process(target=work, args=(n, url, names, results), daemon=True) for n in range(jobs)]
    for w in workers:
        w.start()
    print(f"{len(work_list)} test classes in {jobs} processes", flush=True)
    done, totals = [], {"run": 0, "skipped": 0, "expected": 0}
    failures, errors, unexpected = [], [], []
    while len(done) < len(work_list):
        try:
            r = results.get(timeout=5)
        except Exception:
            if not any(w.is_alive() for w in workers):
                print("A test process ended before its tests did.", flush=True)
                errors.append(("(test process)", "ended early; see above"))
                break
            continue
        done.append(r["name"])
        for key in totals:
            totals[key] += r[key]
        failures += r["failures"]
        errors += r["errors"]
        unexpected += r["unexpected"]
        bad = len(r["failures"]) + len(r["errors"]) + len(r["unexpected"])
        print(f"  [{len(done)}/{len(work_list)}] {r['name']}: {r['run']} tests, "
              f"{'FAILED ' + str(bad) if bad else 'ok'} ({r['seconds']:.1f} s)", flush=True)
    for w in workers:
        w.join(timeout=10)
    elapsed = time.perf_counter() - start
    for kind, items in (("FAIL", failures), ("ERROR", errors)):
        for test_id, text in items:
            print("=" * 70)
            print(f"{kind}: {test_id}")
            print("-" * 70)
            print(text)
    for test_id in unexpected:
        print(f"UNEXPECTED SUCCESS: {test_id}")
    print("-" * 70)
    print(f"Ran {totals['run']} tests in {elapsed:.3f}s")
    print()
    extra = [f"skipped={totals['skipped']}"] if totals["skipped"] else []
    if totals["expected"]:
        extra.append(f"expected failures={totals['expected']}")
    if failures or errors or unexpected:
        parts = [f"failures={len(failures)}"] if failures else []
        parts += [f"errors={len(errors)}"] if errors else []
        parts += [f"unexpected successes={len(unexpected)}"] if unexpected else []
        print(f"FAILED ({', '.join(parts + extra)})")
        return 1
    print("OK" + (f" ({', '.join(extra)})" if extra else ""))
    return 0


def _discover(names: list[str]) -> list[str]:
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    # Listing the tests imports them, and tests/base.py with them: on a
    # SQLite file of its own, not the PostgreSQL database a worker will use.
    os.environ.pop("BIOMANAGER_TEST_DATABASE_URL", None)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(names) if names else loader.discover("tests", top_level_dir=ROOT)
    return classes_of(suite)


if __name__ == "__main__":
    sys.exit(main())
