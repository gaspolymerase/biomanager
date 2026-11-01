# One master copy; devices queue actions that the master replays

A lab has one database that is its master copy (a server, or a desktop an
admin made the master), and every other device works on it through the
master's address. Databases are never merged. A phone or a window with no
signal records what was done as a queue of actions (`app/actions.py`), and
the master replays each one through the pages' own rules as the person who
did it, so permissions and validation apply as if it were done there.

## Consequences

- An action carries the version of each record as the device saw it
  (`sync_version`). If the record has changed since in other fields, the
  action still applies; if the same field changed to something else, the
  action is held until someone chooses **Keep mine** or **Keep theirs**
  (`/decisions`); some actions are refused outright (a wean whose pups moved).
- Every record a device keeps carries a lab-wide `sync_version`, and
  deletions are kept in `sync_deletions`, so a device asks only for what
  changed.
