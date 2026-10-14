# Benchmarks

Measurements of BioManager that anyone can run again and get the same
numbers. Each runs on a throwaway database of its own (as the tests do), so
no lab's data is ever opened.

## Import from Excel: messy sheets

`import_sheets.py` asks how much of a real lab's spreadsheet comes through
**Import from Excel** intact when the person accepts every column match the
app suggests and clicks Import.

```bash
.venv/bin/python benchmarks/import_sheets.py
```

It takes about ten minutes. `--per-mess 30` makes more sheets of each kind,
`--seed 7` a different set, and `--out <folder>` keeps every sheet it made
beside a `truth.json` of what each row really holds, so the data set can be
published with the results.

**How.** Each sheet is made from mouse or plasmid records whose true values
are known, then spoilt in one way a lab's sheet is spoilt (a *mess*), or in
four at once (the *kitchen sink*), or not at all (the *control*). The
messes:

| Kind | Messes |
| --- | --- |
| Headers | other names (DOB, Cage #, Vector), typos, Chinese headers, another column order |
| Layout | title lines above the header, blank rows, a TOTAL row, extra columns the database lacks |
| Cages and boxes | a cage number merged down its mice; written once with blanks below (filled down by eye) |
| Dates | 3/14/2026, 14/03/2026, 14.03.26, 14-Mar-26, real Excel date cells, Excel date numbers, all mixed; a 2062 typo |
| Values | male/female, ♂/♀, Breeding/exp/culled, owners by full or first name or someone not in the lab, stray spaces and capitals, a mouse number used twice, numbers stored as 1201.0, 123,4 for 123.4, concentrations with units |
| Files | CSV, semicolon CSV, TSV, CSV in Windows' encoding, CSV with a byte-order mark |

Every sheet is uploaded through the app's own pages and imported with the
match page's suggestions. Then each value is compared with the truth:

- **correct**: stored as it should be;
- **kept**: not stored as it should be, but the sheet's text is in the
  record's notes, or the preview warned about that value: nothing is lost,
  and the person was told;
- **blank**: left empty without a word;
- **wrong**: a different value without a word (the worst).

A row is *imported*, *refused* (the preview says why), or *lost*.

Results are written to `results/import-<version>-<commit>-seed<seed>.md`
(tables) and `.json` (the counts behind them); a commit marked `-dirty` had
changes to the app not yet committed.

### Results so far

Seed 1, 740 sheets, the importer before and after the fixes this benchmark
led to (`results/import-1.5.5+dev-3eb67cf-seed1.md` and
`results/import-1.5.5+dev-5cf6a57-seed1.md`):

| | Rows imported | Values correct | Kept, and told | Blank, silently | Wrong, silently |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mice, before | 100.0% | 95.9% | 3.8% | 0.3% | 0.0% |
| Mice, after | 100.0% | 99.8% | 0.2% | 0.0% | 0.0% |
| Plasmids, before | 94.7% | 98.0% | 0.7% | 1.3% | 0.0% |
| Plasmids, after | 100.0% | 99.9% | 0.1% | 0.0% | 0.0% |

What changed: a cage, rack, box or tank written once with blank cells
below offers **Its blank cells take the value above them** (filled-down
cages went from 91.6% to 100% correct, boxes from 82.9%); Chinese headers
are matched (5.2% and 0% to 100%), and a CSV from a Chinese Windows (GBK)
is read; a concentration written with `ng/µl` is read (90.7% to 100%).
What remains *kept* is what can't be known from the sheet: a person not in
the lab, a mouse number used twice, a birth date in 2062.

## AI assistants: from words to records

`assistant.py` asks how accurately an AI assistant, connected as a lab
member's would be, turns what they say into records.

```bash
.venv/bin/python -m pip install -r benchmarks/requirements.txt
.venv/bin/python benchmarks/assistant.py --repeat 3
```

It needs an Anthropic API key (`ANTHROPIC_API_KEY`, or a profile from
`ant auth login`), or, with `--via claude-code`, a signed-in `claude` CLI
instead: the same prompt and tools go through `claude -p`, with Claude
Code's own tools, settings and plugins left out (it still frames the
conversation a little, so compare runs made the same way). `--check` builds
every scenario without a model, and where a scenario has a right answer
written down, approves it and scores it, so a scenario no assistant could
get right shows there first.
`--model` picks the model (default `claude-opus-5-5`), `--context` what it
knows of BioManager besides the tools: the plugin's skill (default), only
the MCP server's instructions, or nothing.

**How.** Each conversation runs in a process of its own, on a fresh
throwaway database. Each scenario makes a small lab there through the app's
pages (as the tests do), then says one thing a person would ("Litter in cage 202
yesterday, 7 pups"; "Moved the breeder from 502 to 591", where two breeders
could be meant; "I want to change something in BioManager"). Eight are
harder: a weaning that sends the female pups to a new cage and the males to
another, past breeders of the same sex; three weights in one line; "the two
females" that only the records can pick out, beside "the male" in a cage of
two males, where it should ask; a litter born "on Sunday"; a death, a
weight and a move in one message; a litter date to correct, not a second
litter to add; 40 mg used from a reagent kept in grams. The assistant
gets exactly the tools an assistant app gets over MCP, as the person, with a
*Read and propose* token. When it asks something, the scenario answers as
the person would, or says they don't know. Its proposal is then approved, as
the person would press **Approve**, and the records are compared with the
truth: **correct**; **asked** (it asked rather than guessed, and changed
nothing wrongly: the right outcome when the words can't say); **missing**
(something said wasn't recorded); **wrong** (a record ended up wrong,
silently: the number that has to stay at zero).

Results go to `results/assistant-<version>-<commit>-<model>-<context>.md`
and `.json` (`-via-claude-code` and `--label` are added to the name), with
each conversation's tool calls and the tokens used. A model's runs vary, so
compare with `--repeat`.

### Results so far

Release 1.16.0, `claude-opus-5-5`, effort medium, through Claude Code
(`results/assistant-1.16.0+dev-7f3b6ee-*`): every counted conversation
ended **correct** or **asked**, and none **wrong**. (These runs came before each
conversation had a database of its own, so a pass's scenarios saw each
other's labs: in *unknown* the assistant offered the other scenarios'
cages as possible matches, and still asked.)

| Context | Runs | Correct | Asked | Missing | Wrong |
| --- | ---: | ---: | ---: | ---: | ---: |
| The skill | 4 × 7 | 20 | 8 | 0 | 0 |
| The server's instructions only | 1 × 7 | 5 | 2 | 0 | 0 |
| Nothing but the tools | 1 × 7 | 5 | 2 | 0 | 0 |

Each run gave the same outcome in each scenario, and the skill made no
difference: these seven say too little to tell the contexts apart. In the
`-x3` file all three passes shared one database: its second and third
passes found the first pass's cages and are not counted (4 **missing** in
litter and move, where the assistant rightly declined to record a litter
already there or to move mice into another person's cage).
After the change, `assistant-2.0.0+dev-f747cf8-…-isolation-check` ran
litter and move twice each: all four **correct**.

The eight harder scenarios on release 2.0.1, each conversation with a
database of its own (`results/assistant-2.0.1+dev-c19f06b-*-harder`):

| Context | correct | asked | missing | wrong |
| --- | ---: | ---: | ---: | ---: |
| The skill | 7 | 1 | 0 | 0 |
| Nothing but the tools | 6 | 1 | 1 | 0 |

The weaning, the three weights, "the two females", the Sunday litter, the
three changes in one message and the corrected date all came out right
either way, and "the male" in a cage of two was asked about. The
difference is the reagent, and it is not the assistant's doing: through
Claude Code, `propose_changes`' description is cut short after its 14th
action (the mice, then the fish), and `list_actions`' reply (some 86,000
characters) is saved to a file instead of shown (raising
`MAX_MCP_OUTPUT_TOKENS` to 150,000 shows the first 2 KB of it). So the
assistant never saw `inventory_quantity_used`: it guessed action names
until one was refused no longer. With the skill it then set the quantity to
0.96 g by hand (right, but BioManager didn't convert the 40 mg); with
nothing but the tools it gave up and proposed nothing (`-mcp-limit-150k`
is the same with the higher limit: right by hand again). An assistant app
that shows the whole catalogue wouldn't meet this; one that keeps tool
descriptions and replies short, as Claude Code does, sees only the mouse
actions.
