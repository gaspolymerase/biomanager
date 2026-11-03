# The old `animals` table is dropped

The app's first versions had a generic `animals` table that samples,
calendar events and notebook pages could link to. Nothing has filled or
shown it since each database got its own records (mice, fish, organisms),
and samples name their source by kind and number. Revision 0055 drops it
and the three `animal_id_fk` columns, after copying any sample's linked
animal number into its source. The lab's maintainer chose dropping over
keeping an empty table, so the models say only what the app uses.

## Consequences

- On SQLite the three tables are rebuilt without the column by
  app/integrity.py's rebuild (`_rebuild_one` with `drop`), because a
  column still declared to reference a dropped table makes every insert
  fail while foreign keys are enforced, and Alembic's batch mode would
  lose AUTOINCREMENT's next id and the foreign keys checked at commit.
- `scripts/upgrade-check.py` lets a table in `RETIRED` disappear; any
  other table that does is still a failure.
- Undoing it is restoring the copy taken before the upgrade.
