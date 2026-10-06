# Plan: AI assistants suggest records, people approve them

Status: built (steps 1 to 4 below), on a branch, not yet released: actions
for every animal database, experiments and the daily log; `/resolve`,
`/vocabulary`, `/due`, `/actions`; proposals with the Propose-only token and
Proposed changes; the MCP server in `mcp/` and Connect an AI assistant.
Members may make Propose-only tokens under the same Lab setup switch as
other tokens. Scope for the first round: every animal colony
(mice, zebrafish, flies and worms, any other organism), experiments, and the
notebook's daily log.

## In short

- BioManager gets no AI inside it. People keep using the assistant they
  already have (Claude, ChatGPT, Cursor, Cherry Studio or any app that speaks
  MCP); BioManager only learns to receive its suggestions safely.
- Someone tells their assistant, in their own words or with a photo of their
  notebook: "Litter in 102 yesterday, 7 pups; moved the two old breeders from
  88 to 91; on the TMX cohort I injected day 3 this morning." The assistant
  looks the records up and sends BioManager a **proposal**. It changes
  nothing yet.
- BioManager checks every change exactly as its own pages would, and shows
  the person **one summary of everything that will change**, in plain words,
  with any warnings at the top. They press **Approve** once (or **Discard**).
  There is no approving change by change: a wrong proposal is discarded and
  the assistant asked to send a corrected one.
- An approved proposal goes in as one batch: it is in Batch history, marked
  as coming from that assistant, and can be undone like any bulk action.
- Assistants get a new kind of token, **Propose only**: it can read and
  propose, never apply. Every proposal is approved by a person inside
  BioManager; assistants never apply changes themselves.
- The MCP server lives in this repository (`mcp/`) and is run from it; it is
  not published as a package for now.

## What will change for people

- **Settings → API tokens** offers a third kind of token, *Propose only*, and
  a **Connect an AI assistant** panel: it makes such a token and shows the
  setup to copy into an assistant app (on the desktop app, with its local
  address filled in).
- **Proposed changes** (from the bell, with a notification when one
  arrives): each pending proposal as a summary, for example

  > **From Claude, 10:42** · 5 changes
  > Mouse colony: litter recorded in cage 102 (7 pups, born 4 Oct);
  > 2 mice moved from cage 88 to 91 (#14, #15).
  > Experiment "TMX cohort 2": day 3 tamoxifen marked done for 6 mice.
  > Notebook: a note added to today's daily log.
  > ⚠ Cage 102 already had a litter dated 12 Sep; it will be replaced.
  > [Approve] [Discard] · Show each change

  "Show each change" lists before → after for every record, read only.
- **Batch history** shows approved proposals as "Proposal #12: <summary>
  (via Claude)", undoable as one.
- Lab setup gets one switch: whether members may make Propose-only tokens
  (as today for other tokens).

## Where we start from

- `/api/v1` (app/api.py): personal tokens, read or read-and-write, acting with
  their owner's exact permissions; an OpenAPI description at
  `/api/v1/openapi.json`; writes go through the pages' own checks; changes are
  labelled `API: <token name>` (`g.audit_batch`).
- It writes very little: change a mouse, record a weight, change a fly vial,
  add or change an inventory item, record an experiment readout. Litters,
  weaning, moves, deaths, new cages, tanks, crosses, flips, steps done and
  notebook notes have no write route.
- `audit.batch(...)` groups writes into a `BatchRecord` that `undo.py` can
  reverse (refusing when a record changed since). API writes are labelled but
  not grouped into batches today.
- The notebook is edited live by several people at once through Yjs (updates
  kept in `notebook_sync_updates`); the server holds no Yjs document of its
  own, so it cannot simply rewrite a page's text.
- The desktop app serves the same API on 127.0.0.1.

## Part 1: proposals in the API

### 1. Shared actions

Each action the assistant may propose is sent to the page itself, as the
form it would post (or to the API's own write, for a mouse's fields and
weights), so a proposal can never behave differently from the page. Rather
than moving each page's code into shared functions, the pages run as the
person inside one transaction that is rolled back for a preview and kept
for an approval (`app/contained.py`: every session joins it, so the pages'
commits become savepoints). The catalogue is `app/actions.py`: each
action's fields, target, summary line and the requests it makes; how it
works is in `docs/DEVELOPMENT.md` ("Proposed changes").

**Mouse colony** (app.py routes such as `cage_give_birth`, `cage_wean`,
`cage_wean_distribute`, the mouse and cage forms; done):
new mouse; change a mouse (any field the sheet edits, including genotype and
status); record a weight; new cage; change a cage (purpose, owner, rack and
position); litter born (cage, date); create the litter at genotyping
(father, mother, number of pups); wean (rows of sex, mice or count, and an
existing or new cage); move mice to a cage; mark dead or culled (date,
reason); a note on a mouse, cage or litter.

**Zebrafish** (`/zebrafish/...`; done): new tank; change a tank (line, count,
status); move a tank and return it; a cross (`/zebrafish/mate`); new or
changed clutch; new or changed fish row; a sacrifice entry (`/zebrafish/sac`);
a water reading.

**Flies and worms** (stock_routes.py, stock_service.py; done): new vial or
plate; change one; copy, collect, discard, restore, shifted, progeny done,
scored; mark a rack flipped; a frozen stock.

**Any other organism** (organism_routes.py, organism_service.py; done):
housing, animal, line, cross, cohort, reading and genotype saves; mark a
scheduled job done.

**Experiments** (experiments.py, experiment_steps.py; done): record a readout
(exists); mark a step done for some or all of its animals (date, dose given,
notes); add animals; change status; a note.

**Notebook** (done for the Log of a page; new pages are not proposable yet): add a note to today's daily log (made if it doesn't exist, as
`/notebook/today` does), or to a page the person may edit; make a new page
(a note, or an experiment page from a protocol). See step 5 for how text gets
into a live page.

Deleting anything, signing and changing who may see what are not proposable.

### 2. Finding records without guessing

`GET /api/v1/resolve?q=cage 88&kinds=cage,mouse` returns candidates across
every animal database, experiments and notebook pages, each with a stable
reference (`{"kind": "cage", "id": 412, "label": "Cage 88 · Rack A · B3"}`).
Proposals must use these references; an assistant that finds two matches
asks the person. `GET /api/v1/vocabulary` returns what this lab calls things:
strains and lines, genotype values, cage purposes, rack names, each
organism's own words (from its configuration), so free text maps to real
values. `GET /api/v1/due` returns Home's list (weanings, genotyping, flips,
scheduled jobs, experiment steps due), each with the change that records it
done where there is one. `GET /api/v1/actions` lists the catalogue.

### 3. Proposals

- `POST /api/v1/proposals`
  `{"summary": "...", "source": "Claude", "changes": [{"action": "litter_born",
  "target": {"kind": "cage", "id": 412}, "fields": {"date": "2026-10-04"}}],
  "replaces": 11}`.
  BioManager runs every change inside a transaction it then rolls back,
  collects each change's before/after values, warnings (a litter date
  replaced, pups younger than P18 at weaning, a dose that doesn't match the
  weight) and errors, writes the plain-language summary, and stores the
  proposal as *pending* (or *invalid*, with the errors, which the assistant
  can read and fix). `replaces` marks an earlier pending proposal superseded.
  Reply: the id, the summary, warnings, errors and the review link.
- `GET /api/v1/proposals/{id}`: status (*pending, approved, discarded,
  superseded, expired, invalid*), summary and changes.
- `POST /api/v1/proposals/{id}/discard`.
- No apply endpoint for tokens: approving happens only on the Proposed
  changes page, signed in.

### 4. Approving

- **Approve** re-runs every change for real inside one
  `audit.batch("mixed", "Proposal #12: <summary> (via Claude)")` and commits
  once. If any change now fails, or a record it touches changed after the
  preview, nothing is applied and the page says which record and why; the
  person discards it and asks for a fresh proposal.
- Only the person the token belongs to may approve or discard their
  proposals.
- Pending proposals expire after 14 days.

### 5. Notes into the notebook

The server can't edit a live Yjs page directly. Plan: a note to add is kept
as a *pending insert* on the page. The next editor that opens the page (or
the one already open, which polls for sync updates anyway) appends it to the
document as a paragraph block with a small "from Claude" label, through the
normal Yjs path, and marks it done; the server hands each insert to one
client only. Until then the page shows the pending note above its text, and
search finds it. A page nobody has ever edited live (no sync updates yet) is
written directly on the server. (Built so, without the pending note shown
above the text or found by search: an editor that opens the page adds it
within seconds.) Alternative, if this proves fragile: a Python
Yjs library (pycrdt) so the server appends to the document itself; heavier,
so second choice.

### 6. Storage, permissions and history

- New tables through an Alembic revision after the newest in
  `migrations/versions/`, using `migrations/helpers.py`:
  `proposals` (id, owner, token, source, summary, status, created, decided,
  expires, replaces, batch id once approved) and `proposal_changes`
  (proposal, order, action, target, fields JSON, preview JSON); and
  `notebook_pending_inserts` (page, text, label, proposal, claimed by,
  done at). `scripts/upgrade-check.py` with SQLite and `--postgres`.
- `api_tokens.scope` gains `propose`.
- A token can only propose changes its owner could make on the pages; a
  guest can't propose.
- Proposals are tracked in the change history as batches; the text the person
  gave their assistant is never sent or stored unless the assistant puts it in
  `summary`.

### 7. The Proposed changes page

Under the bell, phone-friendly: pending proposals newest first, each as its
summary (grouped by database, warnings first) with **Approve** and
**Discard**, and "Show each change" for the read-only before → after list.
Recent decisions below, linking to their batch. A notification when a
proposal arrives. English and Chinese like the rest of the app.

### 8. Tests

- Each action: the API's result matches the page's for the same input.
- Proposals: preview leaves the database untouched; approve applies
  everything as one batch, undo reverses it.
- Approval is refused, with the record named, when a record changed after the
  preview.
- Invalid proposals report the page's own messages.
- A propose-only token can't write directly, and no token can approve.
- Someone can't propose changes to records they can't edit.
- Expiry and superseding work.
- Notebook pending inserts are claimed by exactly one client.
- SQLite and PostgreSQL.

## Part 2: the MCP server (`mcp/`)

A small Python program in this repository, using the official MCP SDK,
speaking stdio. It holds no data: it calls `/api/v1` with the lab's address
and a token from its environment (`BIOMANAGER_URL`, `BIOMANAGER_TOKEN`). Run
from the repository:

    python -m pip install -r mcp/requirements.txt
    python mcp/biomanager_mcp.py   # or point the assistant app at this command

Tools:

| Tool | Does |
| --- | --- |
| `lab_overview` | The databases this lab has, and its vocabulary (from `/vocabulary`) |
| `resolve` | Find the records a phrase means, with references |
| `get`, `list` | Read records within the person's permissions |
| `whats_due` | Home's list of what is due (from `/due`) |
| `propose_changes` | Send a proposal; returns the summary and review link to show the person |
| `proposal_status`, `discard_proposal` | Follow a proposal up |

Prompts: "Log today's work from my notes", "Log from a photo of my notebook,
whiteboard or cage card" (the assistant app reads the photo), "Plan
tomorrow from what's due". They tell the assistant to resolve every record, to
ask rather than guess, to put everything in one proposal, and to end by
giving the person the review link.

Tool descriptions and argument schemas are generated from the API's endpoint
list and the action catalogue, so a new action appears in the MCP server
without editing it. Tests: each tool against a test server; one real run
through an MCP client before release.

`mcp/README.md` gives the setup for Claude Desktop, Cursor and Cherry Studio,
and the Connect an AI assistant panel shows the same with the person's token
and address filled in.

## Docs (in the same piece of work, per CLAUDE.md)

- A new guide page, "AI assistants" (English and Chinese): what it does, the
  Propose-only token, connecting an assistant, approving proposals.
- Updated: Scripts and the API and the API reference page; Settings in the
  guide; README (accounts and data); DEVELOPMENT.md (shared actions,
  proposals, notebook inserts, the MCP server); a Features card; `llms.txt`.

## Order of work

1. Shared actions and `/resolve`, `/vocabulary`, `/due` for the mouse colony,
   then zebrafish, flies and worms, and any organism.
2. Proposals: tables and migration, the endpoints, the Propose-only token,
   the Proposed changes page and notification, tests.
3. Experiments' actions, and the notebook's daily-log notes with pending
   inserts.
4. The MCP server, the Connect panel, docs; a release.

Each step is releasable on its own; step 2 is when people first see anything.
