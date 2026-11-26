# Plan: BioManager beside cloud research agents

Status: proposed, 2026-11-24. Nothing built yet. Follows
`docs/plans/ai-assistants.md` and `docs/plans/remote-connector.md`.

## Why

Cloud research agents have arrived. ClawsGO Science (clawsgo.science) is
one. A frontier model (Claude Fable 5.2, Opus 5.5 or GPT 6.2) works in a
general Python sandbox for hours, unattended. It hands back a report,
figures and a LaTeX paper, and every step it took can be replayed. Its
demos are literature reviews, meta-analyses, writing, an scRNA benchmark
and GPU training. None starts from a plate reader's, qPCR machine's or
caliper's raw numbers, and none knows which animal was in which well. It is
paid by the token, and its privacy policy says task content goes to
Anthropic or OpenAI in the US (not used for training). This comes from its
website, billing terms and privacy policy, read on 2026-11-24. The product
itself was not tried, because signing up needs a +86 phone number.

A lab member with a plate reader's CSV will ask why they shouldn't hand it
to such an agent. BioManager's answer is the rule its assistants follow:
"BioManager computes; you talk". `plate_readout` and `derive` work the
numbers out on the experiment's own plate layout, and the person approves
them. Two things are missing before that answer holds up:

- **We have never measured it.** `benchmarks/assistant.py` has fifteen
  scenarios, and all of them turn words into records (litters, moves,
  weights, a reagent). None gives the assistant an export to analyse.
- **Our numbers have a shorter trail than theirs.** A readout from a plate
  keeps its value. For a ΔCt or blank-subtracted readout it also keeps the
  averaged inputs, in its note. It doesn't keep the export it came from, the
  layout as it was, the wells left out or the standard curve. The proposal
  knows its captures (`captures_json`), but the reading doesn't. Months
  later nobody can say how a value was worked out, or work it out again.

What BioManager has and a general agent lacks is still the point:

- the lab's records: which animal, group, dose, lot and day
  (`experiment_summary`);
- the same answer every time;
- nothing recorded until a person approves.

## In short

1. **Measure first.** Add analysis scenarios to `benchmarks/assistant.py`
   and run the same model two ways. One arm has BioManager's tools, as today.
   The other has a general sandbox instead of `plate_readout` and `derive`,
   the way a cloud agent works. **Wrong** has to stay at zero.
2. **A readout keeps its trail.** What a plate's values were worked out from
   (the export, the layout, the wells left out, the curve) is kept with the
   readings and shown on the experiment page. It can be worked out again
   without any assistant.
3. **Cloud agents connect like any assistant**, if they can. The remote
   connector already exists, so check before building anything.
4. **Say what leaves the lab** when an assistant reads it.

Not in this plan: AI inside BioManager, a sandbox of our own, writing
papers. General agents are better placed for those, and they run on the
same models.

## 1. The benchmark: analysis, with the tools and without

**Scenarios.** Each one makes an experiment through the app's pages, as the
benchmark does now, with groups, animals and a plate layout. Then it says
what a person would say:

| Scenario | Says | Right outcome |
| --- | --- | --- |
| elisa | "IL-6 ELISA from today, the file's in my captures" (8 × 12 grid, 7 standards) | Each animal's concentration off a 4PL curve, blank taken off |
| elisa-outlier | The same, with one replicate far from its pair | As above, and the person told about that well |
| over | The same, with one well reading OVER | That animal left out or asked about, never given a number |
| qpcr | A qPCR export (Well, Target, Ct): "fold change of Il6 against Actb, control is vehicle" | Fold change per animal against the vehicle group |
| caliper | "Tumours today: 1043 12.1 × 8.4, 1044 …" | Volume L × W² / 2 per mouse |
| which-layout | Two plate layouts on the experiment, and the person names neither | Asked |

**Truth comes from outside BioManager.** Each scenario starts from true
values (a concentration or a ΔΔCt per animal) and makes the export from
them: ODs through a known 4PL curve plus noise, Cts from known ΔCts. An
answer counts as correct when it is within the scenario's written tolerance
of the value that went in. The tools arm then can't score correct just
because it ran `app/plates.py`, and a fault in `plates.py` shows up.

**Two arms, one model:** `claude-opus-5-5` with `--via claude-code`, as in
the runs so far.

- **Tools:** the MCP tools as today, a Read and propose token, the skill.
- **Sandbox** (`--sandbox`): the same, but without `plate_readout` and
  `derive`. Instead the conversation gets a folder holding the export and
  may run Python there (Claude Code's Bash), as a cloud agent would. It
  still resolves records, reads the layout (`get`) and proposes values.
  Instructions unchanged.

**Counted** as now: correct, asked, missing and wrong (a value outside the
tolerance or on the wrong animal, recorded without a word). Also counted:

- **told:** whether the person heard about the far replicate or the OVER
  well;
- each conversation's tool calls and tokens.

That is six scenarios, three passes and two arms: 36 conversations. As in
the other scenarios, each proposal is approved and the readings compared.

**What each result means:**

- **The tools come out ahead on wrong or told.** The rule earns its place.
  Say so on the website with the numbers, as the import benchmark does.
- **The arms come out even.** The tools are worth their trail and the
  approval step, not their arithmetic. Item 2 matters more, and the website
  shouldn't claim accuracy.
- **The sandbox comes out ahead.** Something in `plates.py` or the tools'
  descriptions is wrong. Fix that before anything else in this plan.

## 2. A readout keeps its trail

- **Stored.** A new table, `reading_sources`, added in an Alembic revision
  after the newest, written with `migrations/helpers.py`. It holds:
  - the experiment, measure and day;
  - how the values were made: `plate`, `derive` or typed;
  - the capture or uploaded file;
  - the plate layout as it was;
  - the settings: target, reference gene, blank taken off, the curve and its
    fitted parameters, the wells left out;
  - each well's raw value and flags;
  - the proposal and the app's version.

  `experiment_readings` gets a `source_id` that may be empty: a reading
  typed in by hand has none.
- **Filled.** `plate_readout` returns its `experiment_reading` change with
  the source, and approving the proposal stores the source with the
  readings. The experiment page's own **Plate layout** import fills it the
  same way, so it makes no difference whether an assistant was involved.
- **Kept.** A capture file that a reading came from is kept as long as the
  reading. Check first what happens to capture files today and how
  `captures_json` is used once a proposal is approved.
- **Shown.** On the experiment page, a reading that came from a plate says
  so. It shows the file, the curve, the wells left out, and the plate itself
  with the flagged wells. **Work it out again** runs `plates.py` on the kept
  export and shows each value that would change, for example after a fix to
  `plates.py`. Changing them is an ordinary edit and goes into the change
  history.
- **Used.** `experiment_summary` returns how each measure was worked out.
  Write the methods can then say "IL-6 read off a four-parameter standard
  curve, blank wells subtracted, duplicate wells averaged" from the records,
  not from what the assistant remembers.

## 3. Cloud research agents as assistants

- If an agent can use remote MCP servers, there is nothing new to build. The
  lab server's connector (`/api/v1/mcp`, with OAuth or a bearer token) is
  the way in, with Read and propose, as for claude.ai. Any cloud app needs
  the lab server to have internet access before it can reach it.
- First, find out whether ClawsGO, or another agent of its kind, can add a
  custom MCP connector.
  - **If one can:** run it once on a demo lab with "analyse the ELISA in my
    captures for experiment X and record it". Note whether it calls
    `plate_readout` or works the numbers out in its own sandbox. Add what it
    needs to the guide's Connect an AI assistant.
  - **If none can:** stop there. No special integration for one product.
- Their unattended runs already fit the approval step. An overnight run can
  only propose, and the person approves in the morning. Pending proposals
  expire after 14 days.

## 4. What leaves the lab

The AI assistants guide page, in English and Chinese, gets a short section
saying:

- whatever a tool returns goes to the company behind the assistant app,
  and for a cloud agent also to the model providers it uses;
- the same goes for any capture photo or file the assistant reads;
- BioManager sends nothing on its own;
- a cloud app reaches a lab server only if the lab has turned on internet
  access;
- revoking the token under Settings cuts the assistant off.

## Order of work

1. **The benchmark:** the scenarios, `--sandbox`, the runs, and the results
   in `benchmarks/README.md`. Nothing changes for people.
2. **The readout trail:** the table and migration, `plate_readout` and the
   Plate layout import, the experiment page with Work it out again, and
   `experiment_summary`, with tests and docs. This is the first thing people
   see.
3. **The guide section** on what leaves the lab.
4. **The cloud agent check**, at any time. It needs nothing from 1–3.

## Tests (for 2)

- A plate import stores its source, whether it comes through the page or an
  approved proposal.
- Work it out again gives the same values, and shows the ones that change
  when `plates.py` would answer differently.
- A typed reading has no source.
- Undoing the proposal's batch removes the source along with the readings.
- Everything runs on SQLite and PostgreSQL, and `scripts/upgrade-check.py`
  passes.

## Docs (with 2, per CLAUDE.md)

- The guide's experiments page (the plate layout part), in both languages.
- `docs/DEVELOPMENT.md`, for the new table.
- README's experiments line, if it names readouts.
- The `experiment_summary` tool's description.
