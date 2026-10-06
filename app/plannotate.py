"""Annotating a sequence with pLannotate, when a lab has installed it.

pLannotate (McGuffie & Barrick, *Nucleic Acids Res* 2021;49(W1):W516-W522,
GPL-3) aligns a plasmid against databases of parts and proteins, so it
finds an element that differs by a few bases -- a codon-optimised CDS, a
promoter one base off -- which the feature library's exact matching never
will. It is a separate program the lab installs:

    mamba create -n plannotate -c conda-forge -c bioconda plannotate
    mamba activate plannotate && plannotate setupdb      # ~250 MB

BioManager runs it as a tool (never imports it, so its GPL-3 does not
reach this code) and only when `BIOMANAGER_PLANNOTATE` names the command:

    BIOMANAGER_PLANNOTATE=plannotate                    # on PATH
    BIOMANAGER_PLANNOTATE=/opt/conda/envs/plannotate/bin/plannotate

**Why an environment variable and not a setting in Lab setup.** The value
is a command this server runs. Keeping it in the environment means whoever
installs the server decides it; were it a field in the app, anyone who got
into an admin account could turn "edit a setting" into "run what I like on
your server". An admin can use it, but only a person at the machine can
say what it is.

Nothing of pLannotate's is redistributed: its databases are a release
asset with no licence stated, and the lab downloads them itself with
`plannotate setupdb`.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

ENV_VAR = "BIOMANAGER_PLANNOTATE"
TIMEOUT = 45          # seconds; a lab server gives a request 60 (gunicorn.conf.py)
OUT_NAME = "plasmid"


class ToolFailed(Exception):
    """pLannotate is not there, took too long, or answered nothing."""


def command() -> list[str]:
    """The command to run, as a list, or [] when the lab has not set one."""
    raw = os.environ.get(ENV_VAR, "").strip()
    try:
        return shlex.split(raw) if raw else []
    except ValueError:                      # unbalanced quotes
        return []


def available() -> bool:
    return bool(command())


def run_tool(sequence: str, is_circular: bool, timeout: int = TIMEOUT) -> str:
    """Run pLannotate over the sequence and give back the GenBank it wrote.

    Everything goes through files in a folder of its own, which is thrown
    away afterwards; nothing is passed through a shell."""
    argv = command()
    if not argv:
        raise ToolFailed(f"{ENV_VAR} is not set")
    with tempfile.TemporaryDirectory(prefix="biomanager-plannotate-") as folder:
        here = Path(folder)
        (here / "in.fa").write_text(f">{OUT_NAME}\n{sequence}\n", encoding="utf-8")
        call = [*argv, "batch", "-i", str(here / "in.fa"), "-o", str(here), "-f", OUT_NAME]
        if not is_circular:
            call.append("--linear")
        try:
            done = subprocess.run(call, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
        except FileNotFoundError as problem:
            raise ToolFailed(f"{argv[0]} is not installed here") from problem
        except subprocess.TimeoutExpired as problem:
            raise ToolFailed(f"it took longer than {timeout} seconds") from problem
        written = sorted(here.glob(f"{OUT_NAME}*.gbk")) + sorted(here.glob(f"{OUT_NAME}*.gb"))
        if not written:
            said = (done.stderr or done.stdout or "").strip().splitlines()
            raise ToolFailed(said[-1][:200] if said else "it wrote no annotated sequence")
        return written[0].read_text(encoding="utf-8")


def annotations(genbank: str, length: int) -> list[dict]:
    """The features pLannotate found, in the shape features_json keeps."""
    from .sequence_parser import parse_genbank

    parsed = parse_genbank(genbank) or {}
    out = []
    for feature in parsed.get("features") or []:
        start, end = int(feature.get("start", -1)), int(feature.get("end", -1))
        if not (0 <= start < length and 0 <= end < length) or not (feature.get("name") or "").strip():
            continue
        out.append({"name": feature["name"][:120], "type": feature.get("type") or "misc_feature",
                    "start": start, "end": end, "direction": feature.get("direction", 1),
                    "color": feature.get("color") or "#cbd5e1",
                    "notes": feature.get("notes") if isinstance(feature.get("notes"), dict) else ""})
    return out


def find(sequence: str, is_circular: bool, existing: list[dict], timeout: int = TIMEOUT) -> list[dict]:
    """What pLannotate adds to a map: its features, minus the places this
    map already marks with the same name (feature_library's rule)."""
    from .feature_library import _overlaps

    sequence = (sequence or "").upper()
    found = annotations(run_tool(sequence, is_circular, timeout), len(sequence))
    named = {}
    for a in existing:
        if isinstance(a, dict) and str(a.get("start", "")).lstrip("-").isdigit():
            named.setdefault((a.get("name") or "").strip().lower(), []).append((int(a["start"]), int(a["end"])))
    keep = []
    for a in found:
        same = named.get(a["name"].strip().lower(), [])
        if any(_overlaps(a["start"], a["end"], s, e) for s, e in same):
            continue
        keep.append(a)
        named.setdefault(a["name"].strip().lower(), []).append((a["start"], a["end"]))
    return keep
