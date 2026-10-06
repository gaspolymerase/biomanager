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

- **claude.ai, the Claude apps and ChatGPT**, as a custom connector, when
  your lab's server is on the internet (`deploy/host/internet-access.sh`):
  the address `https://<server>:8443/api/v1/mcp`, signed in with OAuth and a
  one-time connection code from the same page.

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
The tools themselves are `app/assistant_tools.py`, shared with `/api/v1/mcp`.
The user guide's *AI assistants* page has more.
