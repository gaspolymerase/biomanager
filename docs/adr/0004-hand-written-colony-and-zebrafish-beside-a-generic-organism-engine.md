# The mouse colony and zebrafish stay hand-written beside the generic organism engine

The mouse colony and the zebrafish database have their own tables and pages
(`colony_routes.py`, `zebrafish_routes.py`). Every other species is an
organism module: a row in `organism_modules` describing its vocabulary,
identity mode, capabilities and schedule rules, served by one generic engine
(`app/organisms.py`, `app/organism_service.py`). The engine was generalised
from the mouse colony, and it has mouse and zebrafish presets so it can be
checked against the hand-written databases; a lab may run fish on it instead.
The hand-written databases are not ported onto the engine.

## Consequences

- A rule a lab relies on may exist in both forms (a cage's purposes, a tank's),
  and the same word can be configured differently in each; GLOSSARY.md is
  what the words mean.
- The hand-written databases keep their endpoint names, which pages link to
  by name (`tests/url_map.txt`).
