# BioManager for AI assistants

[BioManager](https://biomanager.org) keeps a lab's animals, stocks and
supplies in one place: mice, zebrafish, flies, worms, plasmids, samples,
reagents, experiments and a lab notebook. It is open source, and
each lab runs its own, on a desktop or on a lab server.

This plugin lets your assistant (Claude, ChatGPT and Codex, Cursor, VS Code
with GitHub Copilot, and others that read skills) work with your lab's
BioManager. Tell it what you did, in your own words or with a photo of your
notebook, whiteboard or cage cards:

> Litter in cage 102 yesterday, 7 pups; moved the two old breeders from 88
> to 91; injected day 3 of the TMX cohort this morning.

It looks up each cage, mouse and experiment you mention, asks you when
something could be more than one record, and sends **one proposal**. You
read it in BioManager and press **Approve**, which makes every change at
once as one batch you can undo, or **Discard**. The assistant never
changes anything itself.

Once the plugin is added, you don't need to explain anything first: "I want
to change something in BioManager" is enough, and it shows you what's due
today and asks what you did.

## What it adds

- **A skill** that teaches the assistant how to work with BioManager: look
  up every record, never guess, send everything as one proposal, give you
  the link.
- **Nine commands**: *Log today's work from my notes*, *Log from a photo of
  my notebook, whiteboard or cage card*, *Turn my captures from the bench
  into records*, *Plan tomorrow from what's due*, *Review the lab this week*,
  *Design an experiment and set it up*, *Write the methods for an experiment*, *Draft the
  animal-use report for an annual review* and *Write a handover for someone
  leaving the lab*. The numbers in them (animals available, breeding odds,
  animals per group, group allocation, animals used per protocol) are worked
  out by BioManager, not by the assistant.
- **The connection to your BioManager**, in Claude Code: it asks for your
  BioManager's MCP address when the plugin is enabled.

## Connect your BioManager

Each lab's BioManager has its own address, shown in BioManager under
**Help → Connect an AI assistant**.

- **Claude on the web, the desktop app or a phone**: after adding the
  plugin, add BioManager as a custom connector (Settings → Connectors → Add
  custom connector) with that address, and sign in with a connection code
  from the same page. This needs your lab's server to be on the internet. On
  a Team or Enterprise plan, an owner can add it for the whole lab.
- **Claude Code**: enter the address when the plugin asks, then type `/mcp`,
  choose *biomanager*, and press **Allow** in the browser that opens.
- **ChatGPT** (on the web): Plugins → + → Add custom MCP server, with that
  address. **Codex**: `codex mcp add biomanager --url <address>`, then
  `codex mcp login biomanager`.
- **Cursor**, **VS Code** and **Cherry Studio**: the **Add to …** buttons on
  BioManager's Connect page.
- **Kimi Code**: type `/mcp-config` and add an HTTP server named
  *biomanager* with that address, then `/mcp-config login biomanager` and
  press **Allow** in the browser that opens.

The [AI assistants guide](https://biomanager.org/guide/ai-assistants) has
the details, including the BioManager desktop app and servers that are only
on the lab network.

## Privacy

The plugin itself collects nothing and sends nothing anywhere. It is
instructions for the assistant, plus, in Claude Code, the address of your
own BioManager. When you use it, the assistant reads your lab's records from
your BioManager, as you and with your permissions, and sends proposals back
to it. What the assistant sees is handled under your agreement with the
company that makes it (Anthropic for Claude, OpenAI for ChatGPT and Codex,
Moonshot AI for Kimi).
BioManager's makers never see your records. Revoke the connection at any
time under **Settings → AI assistant & tokens** in BioManager. The full
policy is at [biomanager.org/privacy](https://biomanager.org/privacy), and the
terms at [biomanager.org/terms](https://biomanager.org/terms).

## Licence

MIT, like BioManager. Source and issues:
[github.com/gaspolymerase/biomanager](https://github.com/gaspolymerase/biomanager).
