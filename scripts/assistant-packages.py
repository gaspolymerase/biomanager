#!/usr/bin/env python3
"""Write the packages that teach AI assistants to work with BioManager.

    python scripts/assistant-packages.py           # writes them
    python scripts/assistant-packages.py --check   # lists files out of date, exits 1 if any
    python scripts/assistant-packages.py --zips dist    # the downloads: BioManager-plugin-Claude.zip, -ChatGPT.zip, -Kimi.zip

A person adds the plugin once, in their assistant app; from then on "I
want to change something in BioManager" is enough. It carries a skill (how
to work with BioManager, in SKILL.md, which most assistant apps read), the
prompts as commands, and, in Claude Code, the connection to the lab's
own MCP address, asked for when the plugin is enabled. Elsewhere each lab
adds its own address (each lab's differs), from Help → Connect an AI
assistant.

One folder, plugins/biomanager/, with a manifest for each family of apps:

    .claude-plugin/plugin.json   Claude (chat, Cowork, Claude Code); Qwen Code converts it
    plugin.json                  Agent Plugins 1.0: ChatGPT and Codex, VS Code (GitHub Copilot)
    .cursor-plugin/plugin.json   Cursor
    kimi.plugin.json             Kimi Code, from its own download (it installs a zip by its link)

and three marketplaces that list it: .claude-plugin/marketplace.json
(Claude Code, VS Code, Qwen Code; Codex reads it too),
.agents/plugins/marketplace.json (Codex and ChatGPT's own) and
.cursor-plugin/marketplace.json (Cursor's, for its marketplace).

The words come from app/assistant_tools.py (INSTRUCTIONS and PROMPTS), the
same the MCP servers send, so they can't drift apart: run this after
changing them (tests/test_assistant_packages.py fails while a file is out
of date). A changed skill or command also needs a new VERSION, as apps
keep people on the version they have until it changes: the test says when,
and what CONTENT becomes. The plugin's README.md is written by hand.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.assistant_tools import INSTRUCTIONS, PROMPTS  # noqa: E402

PLUGIN = ROOT / "plugins" / "biomanager"
# The plugin's version, and a digest of the skill and commands it was set for.
VERSION = "1.5.0"
CONTENT = "e26652367f5e6779"
SITE = "https://biomanager.org"
REPO = "https://github.com/gaspolymerase/biomanager"

DESCRIPTION = ("Tell your assistant what you did in the lab, in your words or with a photo of your notes; it "
               "proposes the records in your lab's BioManager, and you approve them there.")

LONG_DESCRIPTION = (
    "BioManager keeps a lab's animals, stocks and notebook in one place: mice, zebrafish, flies, worms, plasmids, "
    "samples, reagents and experiments. It is open source, and each lab runs its own, on a desktop or a lab "
    "server.\n\nThis plugin teaches the assistant to work with your lab's BioManager. Tell it what you did, in your "
    "words or with a photo of your notes, and it looks up each record and sends one proposal, which you approve in "
    "BioManager; it never changes anything itself. It can also answer questions about your records, and plan with "
    "numbers BioManager works out from them: breeding, animals per group, random groups, a whole experiment.\n\n"
    "Connect your own BioManager too: Help → Connect an AI assistant in BioManager shows its address, which you add "
    "as a custom MCP server.")

SKILL_DESCRIPTION = (
    "Record and look up a lab's work in BioManager, the lab's database of animals (mice, zebrafish, flies, worms "
    "and other organisms), cages, litters, experiments, inventories and notebook. Use whenever the person mentions "
    "BioManager or their lab's records, or wants to log, record, update, change or look up animals, cages, "
    "litters, tanks, vials, experiments or what is due, even if they only say \"I want to change something in "
    "BioManager\". Not for working on BioManager's source code.")

# How to connect, said by the skill when the BioManager tools aren't in the
# conversation. The Connect page has each app's steps with the lab's address.
CONNECT = """\
- Every way in is on BioManager's **Help → Connect an AI assistant**, with this lab's address filled in \
(it ends in /api/v1/mcp) and one-click buttons for some apps.
- Claude (web, desktop, phone): Settings → Connectors → Add custom connector with that address; sign in with \
a connection code from the same page. Claude Code: this plugin asks for the address, then type /mcp, choose \
biomanager and press Allow in the browser.
- ChatGPT: Plugins → + → Add custom MCP server with that address (on the web; a Business, Enterprise or Edu \
workspace with developer mode). Codex: `codex mcp add biomanager --url <address>`, then \
`codex mcp login biomanager`.
- Cursor, VS Code, Cherry Studio: the buttons on the Connect page. Other apps: add the address as a \
streamable HTTP MCP server; it signs in with OAuth, or with a token from the same page.
- claude.ai and ChatGPT reach BioManager over the internet, so they need the lab's server to be on it; apps \
on the person's own computer (Claude Code, Codex, Cursor, VS Code, Cherry Studio) also reach a desktop app or \
a server on the lab network."""


def skill() -> str:
    return f"""---
name: biomanager
description: {json.dumps(SKILL_DESCRIPTION, ensure_ascii=False)}
---

# BioManager

{INSTRUCTIONS}

## When BioManager isn't connected

The tools above (lab_overview, resolve, propose_changes…) come from the lab's own BioManager. If they \
aren't available in this conversation, BioManager isn't connected yet: don't work without it, and don't \
ask for a token in the chat. Tell the person, briefly, how to connect:

{CONNECT}
"""


# Each prompt as a command: its slug, the hint for what follows it, and its
# text with that placeholder in it.
def commands(placeholder: str) -> dict[str, tuple[str, str, str]]:
    out = {}
    for name, (title, args, render) in PROMPTS.items():
        if not args:
            text, hint = render({}), ""
        elif args[0].get("required"):
            text, hint = render({args[0]["name"]: placeholder}), args[0]["description"]
        else:
            text, hint = render({}) + f"\n\n{placeholder}", args[0]["description"]
        out[name.replace("_", "-")] = (title, hint, text)
    return out


def command_file(title: str, hint: str, text: str) -> str:
    head = f"---\ndescription: {json.dumps(title, ensure_ascii=False)}\n"
    if hint:
        head += f"argument-hint: {json.dumps('[' + hint.lower() + ']', ensure_ascii=False)}\n"
    return head + "---\n\n" + text.rstrip() + "\n"


def plugin_json() -> dict:
    """Claude's manifest."""
    return {
        "name": "biomanager",
        "displayName": "BioManager",
        "version": VERSION,
        "description": DESCRIPTION,
        "author": {"name": "BioManager", "url": SITE},
        "homepage": f"{SITE}/guide/ai-assistants",
        "repository": REPO,
        "license": "MIT",
        "keywords": ["lab", "laboratory", "mouse colony", "zebrafish", "drosophila", "lab notebook",
                     "animal facility", "LIMS"],
        "icon": "./icon.svg",
        "documentationUrl": f"{SITE}/guide/ai-assistants",
        "supportUrl": f"{REPO}/issues",
        "privacyPolicyUrl": f"{SITE}/privacy",
        "termsOfServiceUrl": f"{SITE}/terms",
        "userConfig": {
            "mcp_address": {
                "type": "string",
                "title": "BioManager's MCP address",
                "description": "From Help → Connect an AI assistant in your BioManager. It ends in /api/v1/mcp.",
                "required": True,
            },
        },
        "mcpServers": {"biomanager": {"type": "http", "url": "${user_config.mcp_address}"}},
    }


def portable_json() -> dict:
    """Agent Plugins 1.0 (agent-plugins.org), with ChatGPT's and Codex's
    listing under extensions.com.openai. No MCP server: its address is each
    lab's own, and these apps can't ask for it when the plugin is added."""
    return {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "biomanager",
        "version": VERSION,
        "description": DESCRIPTION,
        "author": {"name": "BioManager", "url": SITE},
        "homepage": f"{SITE}/guide/ai-assistants",
        "repository": REPO,
        "license": "MIT",
        "keywords": plugin_json()["keywords"],
        "extensions": {"com.openai": {"interface": {
            "displayName": "BioManager",
            "shortDescription": "Lab records from your words",     # 30 at most, for the directory
            "longDescription": LONG_DESCRIPTION,
            "developerName": "BioManager",
            "category": "Education & Research",
            "websiteURL": SITE,
            "privacyPolicyURL": plugin_json()["privacyPolicyUrl"],
            "termsOfServiceURL": plugin_json()["termsOfServiceUrl"],
            "supportURL": f"{REPO}/issues",
            "defaultPrompt": "I want to record what I did in the lab today in BioManager.",
            "composerIcon": "./assets/icon.svg",
            "logo": "./assets/icon.svg",
        }}},
    }


def cursor_json() -> dict:
    return {key: plugin_json()[key] for key in ("name", "displayName", "version", "description", "author",
                                                "homepage", "repository", "license", "keywords")}


def kimi_json() -> dict:
    """Kimi Code's manifest (kimi.plugin.json): the skill and the commands,
    which it lists as /biomanager:<command>. No MCP server, as for ChatGPT:
    Kimi Code can't ask for the lab's address when the plugin is added."""
    return {
        "name": "biomanager",
        "version": VERSION,
        "description": DESCRIPTION,
        "keywords": plugin_json()["keywords"],
        "author": {"name": "BioManager"},
        "homepage": f"{SITE}/guide/ai-assistants",
        "license": "MIT",
        "skills": "./skills/",
        "commands": "./commands/",
        "interface": {
            "displayName": "BioManager",
            "shortDescription": "Lab records from your words",
            "longDescription": LONG_DESCRIPTION,
            "developerName": "BioManager",
            "websiteURL": SITE,
        },
    }


def marketplace_json() -> dict:
    return {
        "name": "biomanager",
        "owner": {"name": "BioManager", "url": SITE},
        "description": "BioManager, a lab's animals, stocks and notebook, for AI assistants.",
        "plugins": [{"name": "biomanager", "source": "./plugins/biomanager", "description": DESCRIPTION}],
    }


def codex_marketplace_json() -> dict:
    return {
        "name": "biomanager",
        "plugins": [{"name": "biomanager", "source": {"source": "local", "path": "./plugins/biomanager"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                     "category": "Productivity"}],
    }


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def files() -> dict[Path, str]:
    """Every generated file and what it should hold."""
    out = {
        ROOT / ".claude-plugin" / "marketplace.json": _json(marketplace_json()),
        ROOT / ".agents" / "plugins" / "marketplace.json": _json(codex_marketplace_json()),
        ROOT / ".cursor-plugin" / "marketplace.json": _json(marketplace_json()),
        PLUGIN / ".claude-plugin" / "plugin.json": _json(plugin_json()),
        PLUGIN / "plugin.json": _json(portable_json()),
        PLUGIN / ".cursor-plugin" / "plugin.json": _json(cursor_json()),
        PLUGIN / "kimi.plugin.json": _json(kimi_json()),
        PLUGIN / "skills" / "biomanager" / "SKILL.md": skill(),
        PLUGIN / "icon.svg": (ROOT / "app" / "static" / "icon.svg").read_text(encoding="utf-8"),
        PLUGIN / "assets" / "icon.svg": (ROOT / "app" / "static" / "icon.svg").read_text(encoding="utf-8"),
    }
    for slug, (title, hint, text) in commands("$ARGUMENTS").items():
        out[PLUGIN / "commands" / f"{slug}.md"] = command_file(title, hint, text)
    return out


def content_digest() -> str:
    """What people get from the plugin: the skill and the commands."""
    parts = [text for p, text in sorted(files().items()) if p.suffix == ".md" and p.name != "README.md"]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


def stale() -> list[Path]:
    return [p for p, text in files().items()
            if not p.exists() or p.read_text(encoding="utf-8") != text]


# What ChatGPT's plugin directory takes (platform.openai.com/plugins): a
# skills-only package, its manifest at the root. No MCP server: each lab's
# address is its own, added by each person as a custom MCP server.
OPENAI_FILES = ("plugin.json", "README.md", "assets/icon.svg", "skills/biomanager/SKILL.md")


def plugin_files() -> list[str]:
    """Everything in the plugin folder, as Claude's Add → Upload plugin takes it."""
    names = [str(p.relative_to(PLUGIN)) for p in files() if PLUGIN in p.parents]
    return sorted(names + ["README.md"])


def kimi_files() -> list[str]:
    """What Kimi Code takes: its manifest at the root, the skill and the commands."""
    return sorted(name for name in plugin_files()
                  if name in ("kimi.plugin.json", "README.md") or name.startswith(("skills/", "commands/")))


# The downloads each release carries (.github/workflows/release.yml): the
# plugin for Claude and the skills-only one for ChatGPT, which the website
# links to, and Kimi Code's, which its /plugins install takes by its link.
ZIPS = {"BioManager-plugin-Claude.zip": plugin_files, "BioManager-plugin-ChatGPT.zip": lambda: list(OPENAI_FILES),
        "BioManager-plugin-Kimi.zip": kimi_files}


def write_zips(folder: Path) -> list[Path]:
    import zipfile
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name, members in ZIPS.items():
        with zipfile.ZipFile(folder / name, "w", zipfile.ZIP_DEFLATED) as z:
            for member in members():
                z.write(PLUGIN / member, member)
        out.append(folder / name)
    return out


def main() -> None:
    if "--zips" in sys.argv:
        folder = Path(sys.argv[sys.argv.index("--zips") + 1]).resolve()
        for path in write_zips(folder):
            print(f"wrote {path}")
        return
    if "--check" in sys.argv:
        old = stale()
        for p in old:
            print(f"out of date: {p.relative_to(ROOT)}")
        sys.exit(1 if old else 0)
    for p, text in files().items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"wrote {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
