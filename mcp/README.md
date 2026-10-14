# BioManager for AI assistants (MCP)

`biomanager_mcp.py` lets an AI assistant read your lab in BioManager and
**propose** changes: you tell it what you did, in your own words or with a
photo of your notes, and it sends one proposal that you approve, whole, on
**Proposed changes** in BioManager. It never changes anything itself.

It is a small MCP server: the assistant app starts it as a command and talks
to it over stdio. It holds no data; it calls your BioManager's API with a
token.

**Most apps don't need it.** BioManager answers MCP itself, at
`<your BioManager>/api/v1/mcp`, with the same tools:

- **Claude Code**, **Cursor**, **Cherry Studio** and other apps that take an
  MCP address: **Help → Connect an AI assistant** has a message to paste into
  the assistant's chat, with your address in it, so it sets itself up. Or,
  in Claude Code, add the address and sign in, with no token: run this, then
  type `/mcp`, choose *biomanager* and press **Allow** in the browser:

  ```bash
  claude mcp add --transport http --scope user biomanager https://your-server/api/v1/mcp
  ```

  An app that can't sign in takes the address and `Authorization: Bearer
  <token>`, from **Make a token** on the same page, which shows the setup.

- **Codex**: `codex mcp add biomanager --url <address>`, then
  `codex mcp login biomanager`. **Cursor**, **VS Code** and **Cherry Studio**:
  the **Add to …** buttons on the same page, with the address filled in.

- **claude.ai, the Claude apps and ChatGPT**, as a custom connector, when
  your lab's server is on the internet (`deploy/host/internet-access.sh`):
  the address `https://<server>:8443/api/v1/mcp`, signed in with OAuth and a
  one-time connection code from the same page. (ChatGPT, on the web:
  Plugins → + → Add custom MCP server.)

**The BioManager plugin** (`plugins/biomanager/`) is what an assistant
learns BioManager from: a skill and three commands, made from the same words
as these tools by `scripts/assistant-packages.py`. Add it once from the
`gaspolymerase/biomanager` marketplace (Claude: Customize → Plugins → Add
marketplace; Codex: `codex plugin marketplace add gaspolymerase/biomanager`),
and "I want to change something in BioManager" is enough to start.

Use this program for **Claude Desktop** on the lab network (Desktop's own
connectors go through the internet), or for an app that only starts
commands.

## 1. A token

In BioManager: **Settings → API tokens → Connect an AI assistant → Make a
token**. That makes a token that may *Read and propose* and shows the setup
below with your address and the token filled in. The token is shown once.

## 2. The program

From a copy of this repository (Python 3.10 or later):

```bash
python -m pip install -r mcp/requirements.txt
```

It needs three settings, given by the assistant app:

| | |
| --- | --- |
| `BIOMANAGER_URL` | Your BioManager's address. The desktop app's is on Settings → API tokens (`http://127.0.0.1:<port>`); the assistant app must run on that computer. |
| `BIOMANAGER_TOKEN` | The token from step 1. |
| `BIOMANAGER_ASSISTANT` | How its proposals are signed in BioManager, e.g. `Claude`. |

## 3. The assistant app

**Claude Desktop**: Settings → Developer → Edit Config, then add (with the
path to this folder; on Windows `"command": "py"` or the full path to
`python.exe`), and restart Claude:

```json
{
  "mcpServers": {
    "biomanager": {
      "command": "python",
      "args": ["/path/to/biomanager/mcp/biomanager_mcp.py"],
      "env": {
        "BIOMANAGER_URL": "https://your-server",
        "BIOMANAGER_TOKEN": "bmt_…",
        "BIOMANAGER_ASSISTANT": "Claude"
      }
    }
  }
}
```

Other apps that only start commands take the same command, arguments and
three settings.

## 4. Use it

Tell it, for example: *Log today's work: litter in cage 102 yesterday, 7
pups; moved the two old breeders from 88 to 91; injected day 3 of the TMX
cohort this morning.* It looks up each record, asks you when something could
be more than one, and replies with a link to the proposal. Open it in
BioManager (or from the bell), read the summary, and press **Approve** or
**Discard**.

It also comes with nine prompts: *Log today's work from my notes*, *Log
from a photo of my notebook, whiteboard or cage card*, *Turn my captures from
the bench into records*, *Plan tomorrow from what's due*, *Review the lab
this week*, *Design an experiment*, *Write the methods for an experiment*,
*Draft the animal-use report for an annual review* and *Write a handover for
someone leaving the lab*.

## Tools

| Tool | Does |
| --- | --- |
| `lab_overview` | This lab's databases and its own words (strains, statuses, racks, people, each database's fields), and today's date |
| `resolve` | The records a phrase means, each with the reference to use in a proposal |
| `get`, `list` | Read a record or a list from the API |
| `whats_due` | What is due, as Home lists it, with the change that records each done |
| `list_actions` | Everything a proposal may contain, briefly; with `names` (or an `area`), those actions in full, with each field's details |
| `propose_changes` | Send one proposal, each change with its evidence; returns each change's summary, warnings and errors, and the review link |
| `proposal_status`, `discard_proposal` | Follow a proposal up (sent back: the person's reason), or withdraw it |
| `list_captures`, `get_capture` | What the person wrote down or photographed at the bench, waiting to become records; a photo comes back as an image |
| `search`, `history`, `lineage` | Search records and notebook text; a record's change history; a plasmid's or mouse's family tree |
| `availability`, `breeding_plan`, `randomize`, `power_analysis` | Animals free by genotype, sex and age; matings to set up and by when; balanced random groups; animals per group. Worked out by BioManager, with their assumptions |
| `reorder_forecast`, `lab_checks`, `experiment_summary` | When stock runs out; the week's checks; an experiment's facts for its methods and its ARRIVE checklist |
| `animal_use`, `handover` | Animals per protocol, species and strain over a year (born, added, used, died or euthanized with reasons, breeding), with what each count is based on; everything one person looks after, by rack and box, and what is due on it |
| `verify_sequencing` | Sanger reads (an `.ab1` or FASTA uploaded as a capture, or the bases as text) checked against a plasmid's map: where each read lies, every mismatch, insertion and deletion with its quality, the features it falls in and the amino acid it changes, and a verdict for the clone |

What may be proposed comes from your BioManager (`/api/v1/actions`), so an
update to BioManager reaches the assistant without changing this program.
The tools themselves are `app/assistant_tools.py`, shared with `/api/v1/mcp`.
The user guide's *AI assistants* page has more.
