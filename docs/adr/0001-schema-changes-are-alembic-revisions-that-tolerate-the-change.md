# Schema changes are Alembic revisions that tolerate the change already being there

Until 0.8 the app brought an older database up to date with hand-written
`ALTER`s at start-up (`services.ensure_schema_updates`). That code is frozen
at 0.8's shape (revision 0002 marks the point), and every change since is an
Alembic revision in `migrations/versions/`. Every start-up still runs
`create_all()`, and a new database is created at the newest shape and stamped,
so a revision can meet a database that already has its table or column: each
one is written with `migrations/helpers.py` and adds only what is missing.
Labs upgrade by opening their database with a newer version, from any release
back to 0.2, so `scripts/upgrade-check.py` (in CI, on SQLite and PostgreSQL)
upgrades a demo lab from every earlier release and fails if a row is lost.

## Consequences

- A revision never assumes the schema it starts from; data changes (an
  `UPDATE` of stored values) match case and spaces loosely, as 0029 and 0054 do.
- Downgrades are not supported: the app copies a SQLite database before
  upgrading it, and a lab server's backup service takes one, so undoing an
  upgrade is restoring that copy.
