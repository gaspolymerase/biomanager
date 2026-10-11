"""What's new: a short note, once, after BioManager is updated.

The first page someone opens after their lab's app or server moves to a new
version shows a small dialog: what is new in it, what works differently,
what was fixed, and a link to the full release notes. **Got it** keeps it
from showing again for that person (`users.whats_new_seen`); Help → What's
new opens it again.

The notes are written here, a few lines a version, alongside the release's
full notes in `docs/release-notes/<version>.md` (tests/test_whats_new.py
fails when the newest release has no entry). Someone who skipped a version
sees the notes of each one since the last they saw; someone who never saw
one sees only the current version's. A brand-new account sees the welcome
tour instead, and starts from the version it joined on.
"""
from __future__ import annotations

import re

from flask import Blueprint, g, has_request_context, jsonify
from markupsafe import Markup, escape

from .db import SessionLocal
from .models import UserAccount

bp = Blueprint("whats_new", __name__)

RELEASES_URL = "https://github.com/gaspolymerase/biomanager/releases/tag/v{version}"

# Newest first. Each line is plain text; **bold** names a button or a page.
# Each line's Chinese goes in app/translations/zh/whats_new.json (a test checks).
NOTES: dict[str, dict[str, list[str]]] = {
    "1.5.8": {
        "new": [
            "In the Mac app, beside the window's buttons: hide the sidebar, search everything, and go back and forward.",
        ],
        "changed": [
            "A collapsed sidebar shows each place's name under its icon, and keeps **Settings** and **Help** at its foot.",
        ],
    },
    "1.5.7": {
        "fixed": [
            "On macOS 26, the window's close, minimise and zoom buttons are the size other apps' are, and the app has the system's current look.",
            "With the sidebar collapsed, the window's buttons sit at the left of the top row, before your tabs, instead of hanging over the sidebar.",
        ],
    },
    "1.5.6": {
        "new": [
            "**Import from Excel** reads Chinese sheets: Chinese headers, 公/母 and 种鼠/处死, and a CSV saved on a Chinese Windows.",
        ],
        "changed": [
            "A cage, rack, box or tank written once with blank cells below: the match page offers **Its blank cells take the value above them**.",
            "On a Mac, the window's buttons sit square in its corner, and the welcome page's name on their line.",
        ],
        "fixed": [
            "A plasmid's concentration written with its unit (152.6 ng/µl) is read as the concentration, not put in the notes.",
        ],
    },
    "1.5.5": {
        "new": [
            "A reagent, chemical, antibody, virus or primer can have a **Low at** level: when its quantity falls to it, the status turns low and its owner is told; at 0 it is empty.",
            "Drag a sheet's column headers into the order you need; **Configure** sets the order the lab starts with.",
            "A plasmid's **Primers** card can **Find saved primers**: every primer the lab already has that binds it, where and which way, and **Show on map**.",
        ],
        "fixed": [
            "A lab server taking in a lab from the desktop app could stop with “app_settings: 12 read, 13 written” and change nothing.",
        ],
    },
    "1.5.4": {
        "fixed": [
            "In the desktop app, **Open the lab** opens your lab's server in the window; it did nothing, and a second press went back.",
        ],
    },
    "1.5.3": {
        "fixed": [
            "On a Mac, **Open your lab** finds a lab server at an https address; it said no BioManager answered there.",
        ],
    },
    "1.5.2": {
        "new": [
            "A chemical or reagent can have several hazards, including the controlled classes: drug precursor, explosive precursor, highly toxic (**Hazard**, ticked in a small menu).",
        ],
        "fixed": [
            "The notebook's **/** menu, **Insert** menu and toolbar are in Chinese, and find things by their Chinese names.",
            "Plasmid editor: **Export** and **Import** have the right icons, and on a Mac the sequence lines up with the ruler.",
        ],
    },
    "1.5.1": {
        "changed": [
            "In Chinese, a cage's purposes read alike: 种鼠笼 · 配种笼 · 实验笼 (Breeder, Breeding, Experiment).",
        ],
    },
    "1.5.0": {
        "changed": [
            "A mouse cage's purpose: **Breeding** is a mating cage, the only one with **Litter born**, **Genotyping** and **Wean**; **Breeder** is the lab's breeding stock, shared, listed under **Breeders**.",
            "No Stock or Retired any more: Stock cages are now Breeder cages, and a cage is **Active** while it holds living mice.",
            "Above the cages: **All**, **Active**, then a button for each purpose, which an admin sets under **Configure → Cage purposes**.",
        ],
    },
    "1.4.1": {
        "fixed": [
            "A lab server built from the source code sends its anonymous daily counts again; the **Usage report** shows what they hold.",
        ],
    },
    "1.4.0": {
        "new": [
            "A new look after Apple's Liquid Glass: the sidebar, tabs and page buttons float as glass over the page, and the selection slides to what you pick.",
            "On a Mac the window has no title bar: its buttons sit at the top of the sidebar, beside your tabs.",
            "Text is in Inter, which tells I, l and 1 apart; **Settings → Appearance & language** has **Font** and, in the Mac app, **Text size**.",
            "On a phone, a tab bar at the bottom: **Home**, **Calendar**, **Databases**, **Notebook** and **Scan**.",
            "The front page is the sign-in to your lab; **Remember me on this computer** offers your account by name next time.",
            "**Save the whole lab** makes one file a new server can take; the desktop app can **Move this lab to a server**.",
        ],
        "changed": [
            "The calendar's month is its title, its steering in the row at the top; a notebook page is a sheet of paper with its tools above it.",
            "A sheet's toolbar keeps to one line: **Print** and **Import from Excel** are under **•••**.",
        ],
        "fixed": [
            "The desktop app's **Go** menu lists your databases again.",
        ],
    },
    "1.3.0": {
        "new": [
            "**/page** in a notebook page makes a new page inside it; **Link to page** or typing **[[** links one that exists, and the sidebar nests them.",
            "Under a page's title, **Linked from** lists the pages that link to it.",
        ],
        "changed": [
            "Topics are folders: a new page goes in the topic it was made from or the one open, whatever its kind, and changing the kind never moves it.",
            "The top of a page reads like a path: click its topic to move it. The icon beside the title is its kind; click it to change the kind.",
            "Links to the app's own pages open in a BioManager tab.",
        ],
    },
    "1.2.9": {
        "fixed": [
            "**中文** works in the desktop app: the download was missing its Chinese, so every page stayed English whatever was chosen.",
        ],
    },
    "1.2.8": {
        "new": [
            "Notebook pages write as in Notion: type **/** for headings, **callouts**, **toggles**, **2 or 3 columns**, colours, a table of contents and **reminders**.",
            "**@** a colleague and hover their name to see who they are: their job, email and project groups.",
            "**Utilities** has a search that knows the bench's words, pinned tools, seven groups by task and new calculators; **Make a tool** adds the lab's own.",
        ],
        "changed": [
            "The notebook's toolbar fits in one row: **Style** and **Lists** each open a menu, and one button adds a picture or a file.",
        ],
        "fixed": [
            "A BCA standard curve averages replicate readings and flags ones more than 10 % apart.",
        ],
    },
    "1.2.7": {
        "new": [
            "**Protocols**, **Recipes** and **Meetings** in the notebook's sidebar open as pages of their own: search them, open one to change it, or add one.",
            "Sort protocols and recipes into the lab's folders, each with an icon you pick; **/protocol** in a page and **Load from library** list them by folder.",
        ],
        "changed": [
            "Icons that were cut off on the right, such as the people and cart icons, now show whole and centred.",
        ],
    },
    "1.2.6": {
        "new": [
            "A lab server tells its admins when a new BioManager is out, in a notification and in **Settings → Devices & copies → Updates**.",
            "**Update now** there backs up, installs the new version and restarts the lab's server; the page follows it and comes back on the new version.",
        ],
    },
    "1.2.5": {
        "new": [
            "**Start a new lab** on the desktop app's sign-in page sets aside a lab nobody can sign in to, deleting nothing, and starts an empty one whose first account is the admin.",
        ],
    },
    "1.2.4": {
        "new": [
            "A lab server can run on a NAS such as fnOS: when the NAS's own pages already use ports 80 and 443, BioManager takes free ones and gives its address with the port.",
            "**Set up a lab server** can sign in with a password instead of an SSH key, and asks for the password **sudo** needs when the account has one.",
        ],
    },
    "1.2.3": {
        "new": [
            "**Settings** is laid out like the Mac's: a list of panes beside the one you pick, each change saved as you make it, and a search box.",
            "The lab's pages are panes in Settings: **Statistics**, **General**, **Databases**, **People & access** and **History**. Everyone sees them; admins change them.",
            "In a project group, each person is a **Lead**, a **Member** or **Can only view**, and the group's switches say what its members may do.",
            "Home has a card for each of the lab's databases, in the sidebar's order.",
        ],
        "changed": [
            "The sidebar's **More** menu is gone: its pages are in **Settings**.",
            "The **Genotyping** dialog says what to type, and the rack position column is headed **Pos**.",
        ],
    },
    "1.2.2": {
        "fixed": [
            "The language picked on the sign-in page stays when you sign in, and on a Mac set to Chinese the desktop app shows Chinese.",
        ],
        "changed": [
            "**Settings → Language** changes as soon as you pick a language.",
        ],
    },
    "1.2.1": {
        "new": [
            "**Samples** show the source mouse's **Custom tag** as a column after **Source**; hide it on the **Mice** tab and it goes from Samples too.",
        ],
    },
    "1.2.0": {
        "new": [
            "**Chemicals**: a database for the lab's chemical list, by abbreviation, CAS number and molecular weight. Add it from **Add database**.",
            "**Formulation** in the notebook (type /formulation in a page): pick chemicals, type what you weigh and get the moles, or give equivalents and get the mass to weigh.",
            "**Columns of your own** now in the mouse colony, zebrafish and plasmids too, as in the other databases: **Configure → Your own columns**.",
            "**Custom tag** on every mouse (an ear tag, ear punch or tail tattoo; hide it under **Columns** if you don't use it), and **Card ID** on cages.",
            "Drag the databases in the sidebar into the order your lab works in (admins).",
            "A sample's **Source** is edited in the sheet, and **Set field** sets it on many samples at once.",
        ],
        "changed": [
            "A log pasted into a data sheet (time down the first column) is drawn as a line over time.",
            "A cage's mice show the same transgene columns as the **Mice** tab.",
        ],
    },
    "1.1.0": {
        "new": [
            "**AI assistants**: tell Claude, ChatGPT or Cursor what you did and it proposes the records; they wait "
            "for you on **Proposed changes**. **Help → Connect an AI assistant** sets it up.",
            "**Plasmids**: **Versions** you can restore, **Made from** and a family tree, a **Feature library** "
            "that marks elements on any map, **Assemble a plasmid**, and **Files** for reads and gel photos.",
            "**Glycerol stocks**: a database for the bacteria carrying each plasmid.",
            "The rest of BioManager is in Chinese: the other databases, experiments, the notebook and the messages.",
        ],
        "changed": [
            "**Primers**, **Glycerol stocks** and **Viruses** are tabs at the top of **Plasmids**, not separate "
            "entries in the sidebar.",
        ],
    },
    "1.0.6": {
        "new": [
            "**BioManager in Chinese (中文)**: choose it in **Settings → Language**, or 中文 / English on the sign-in "
            "page. The colony, sheets, calendar, Home and Settings are translated so far.",
        ],
        "fixed": [
            "On a Windows PC set to Chinese, Japanese or Korean, the desktop app starts again.",
        ],
    },
    "1.0.5": {
        "changed": [
            "BioManager's website is now **biomanager.org**: **Help → User guide** and the app's other links go "
            "there. The old address still works.",
        ],
    },
    "1.0.4": {
        "new": [
            "**What's new**: this note, once after each update. **Help → What's new** opens it again.",
        ],
        "changed": [
            "**Litter born** asks for the date of birth (today unless you change it) before recording the litter.",
            "Any shared cage now says **Shared**, not only breeder cages.",
        ],
        "fixed": [
            "In an open cage, the mouse table's header no longer covers the first mouse.",
            "On a Windows 10 PC without Microsoft Edge WebView2, the desktop app offers to download it and opens in the web browser meanwhile, instead of a blank window.",
        ],
    },
    "1.0.3": {
        "new": [
            "**Project groups** (More → Project groups): share cages, stock, databases, to-dos and notebook "
            "pages with the people on your project.",
            "**Who sees it** on calendar events and to-dos: only you, everyone in the lab, or one of your groups.",
            "**Any cage can be shared**, not only breeder cages, and the Cages tab has a chip for each purpose.",
            "**Settings → Devices**: open the lab's server in the desktop app, or share a desktop's lab on the "
            "lab's network.",
        ],
        "changed": [
            "Your own to-dos and events are yours alone, admins included. A shared event is changed only by "
            "whoever added it and admins.",
        ],
        "fixed": [
            "A sample from a mouse number too big for the database no longer stops the samples sheet opening.",
        ],
    },
}
HEADINGS = (("new", "New"), ("changed", "Works differently"), ("fixed", "Fixed"))


def parse(version: str | None) -> tuple[int, ...] | None:
    """(1, 0, 3) for "1.0.3", "v1.0.3", "1.0.3+dev" or "1.0.3-rc.1"; None for
    anything that names no release ("server", "")."""
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", (version or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def running() -> tuple[int, ...] | None:
    from .feedback import app_version
    return parse(app_version())


def _label(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def due(seen: str | None, now: tuple[int, ...] | None = None) -> list[dict]:
    """The notes someone who last saw `seen` has not seen yet, newest
    first: every version after it up to the running one, or only the running
    one when they have never seen any."""
    now = now if now is not None else running()
    if now is None:
        return []
    last = parse(seen)
    out = []
    for version, note in NOTES.items():
        v = parse(version)
        if v is None or v > now:
            continue
        if (last is None and v == now) or (last is not None and v > last):
            out.append({"version": version, "url": RELEASES_URL.format(version=version),
                        "sections": [(title, note.get(key, [])) for key, title in HEADINGS if note.get(key)]})
    return sorted(out, key=lambda n: parse(n["version"]), reverse=True)


def current_note(now: tuple[int, ...] | None = None) -> list[dict]:
    """The running version's note (for Help → What's new), or the newest one
    before it."""
    now = now if now is not None else running()
    if now is None:
        return []
    earlier = [v for v in NOTES if parse(v) is not None and parse(v) <= now]
    if not earlier:
        return []
    newest = max(earlier, key=parse)
    return [n for n in due(None, parse(newest)) if n["version"] == newest]


def stamp(user) -> None:
    """Mark the running version as seen by this person (a new account, or
    Got it)."""
    now = running()
    if now is not None and user is not None:
        user.whats_new_seen = _label(now)


def rich(text: str) -> Markup:
    """A note line in the page's language (its Chinese is in
    app/translations/zh/whats_new.json): escaped, with **bold** made bold."""
    from .i18n import translate_value
    return Markup(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", str(escape(translate_value(text)))))


@bp.app_context_processor
def inject():
    if not has_request_context():
        return {}
    user = g.get("user")
    # A new account sees the welcome tour first, and is stamped when it ends.
    notes = due(user.whats_new_seen) if user is not None and user.welcomed_at is not None else []
    return {"whats_new_due": notes, "whats_new_rich": rich,
            "whats_new_current": (lambda: current_note()) if user is not None else (lambda: [])}


@bp.post("/whats-new/seen")
def seen():
    """Got it: this person has read the notes up to the running version."""
    if g.get("user") is None:
        return jsonify({"ok": False}), 401
    with SessionLocal() as s:
        user = s.get(UserAccount, g.user.id)
        stamp(user)
        s.commit()
    return jsonify({"ok": True})
