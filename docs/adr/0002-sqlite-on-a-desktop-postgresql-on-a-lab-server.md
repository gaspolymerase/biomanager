# SQLite on a desktop, PostgreSQL on a lab server, from one code base

BioManager runs as a desktop app on one person's computer and as a shared
server for a whole lab, and both run the same code. The desktop keeps the lab
in a SQLite file it needs no service for; a lab server (the Docker stack in
`deploy/`) uses PostgreSQL, with a backup service that dumps, checks and
test-restores it. A lab moves from one to the other with
`scripts/migrate-to-postgres.py`.

## Consequences

- Every query and revision must work on both. SQLite-only SQL
  (`json_extract`, for example) is done in Python instead.
- CI runs the whole test suite on both databases, and the upgrade check on both.
