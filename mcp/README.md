# BioManager for AI assistants (MCP)

`biomanager_mcp.py` lets an AI assistant read your lab in BioManager and
**propose** changes: you tell it what you did, in your own words or with a
photo of your notes, and it sends one proposal that you approve, whole, on
**Proposed changes** in BioManager. It never changes anything itself.

It is a small MCP server: the assistant app starts it as a command and talks
to it over stdio. It holds no data; it calls your BioManager's API with a
token.

## 1. A token

In BioManager: **Settings → API tokens → Connect an AI assistant**. That
makes a token that may *Read and propose* and shows the setup below with your
address and the token filled in. The token is shown once.

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

**Claude Code**:

```bash
claude mcp add biomanager --env BIOMANAGER_URL=https://your-server --env BIOMANAGER_TOKEN=bmt_… \
  --env BIOMANAGER_ASSISTANT=Claude -- python /path/to/biomanager/mcp/biomanager_mcp.py
```

**Cursor**: Settings → MCP → Add new MCP server, or the same `mcpServers`
block as Claude Desktop's in `~/.cursor/mcp.json`.

**Cherry Studio**: Settings → MCP Servers → Add server, type *stdio*,
command `python`, arguments the path to `biomanager_mcp.py`, and the three
settings as environment variables.

## 4. Use it

Tell it, for example: *Log today's work: litter in cage 102 yesterday, 7
pups; moved the two old breeders from 88 to 91; injected day 3 of the TMX
cohort this morning.* It looks up each record, asks you when something could
be more than one, and replies with a link to the proposal. Open it in
BioManager (or from the bell), read the summary, and press **Approve** or
**Discard**.

It also comes with three prompts: *Log today's work from my notes*, *Log from
a photo of my notebook, whiteboard or cage card*, and *Plan tomorrow from
what's due*.

## Tools

| Tool | Does |
| --- | --- |
| `lab_overview` | This lab's databases and its own words (strains, statuses, racks, people, each database's fields), and today's date |
| `resolve` | The records a phrase means, each with the reference to use in a proposal |
| `get`, `list` | Read a record or a list from the API |
| `whats_due` | What is due, as Home lists it, with the change that records each done |
| `list_actions` | Everything a proposal may contain, with its fields |
| `propose_changes` | Send one proposal; returns each change's summary, warnings and errors, and the review link |
| `proposal_status`, `discard_proposal` | Follow a proposal up, or withdraw it |

What may be proposed comes from your BioManager (`/api/v1/actions`), so an
update to BioManager reaches the assistant without changing this program.
The user guide's *AI assistants* page has more.
