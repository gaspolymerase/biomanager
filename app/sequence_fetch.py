"""A plasmid's sequence by its number: a GenBank accession at NCBI, or an
Addgene plasmid number.

- **NCBI**: E-utilities' efetch gives the record as GenBank, features and
  all (`gbwithparts`); no key is needed at a lab's volume.
- **Addgene**: its plasmid's sequences page links the public GenBank files;
  the full sequence Addgene made comes first, then the depositor's.

What comes back is GenBank text, which the plasmid's own upload path reads
(`sequence_parser`), so a fetched sequence is filled in exactly as a
downloaded file would be. Nothing here is needed to use BioManager: a server
with no internet says so politely and the person downloads the file
themselves, and `BIOMANAGER_SEQUENCE_FETCH=0` switches it off for a server
that must never reach out. The only sites reached are NCBI's and Addgene's.

`fetch(ref, opener=...)`: tests pass their own opener, so they never touch
the network.
"""
from __future__ import annotations

import os
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&rettype=gbwithparts&retmode=text&id="
ADDGENE_PAGE = "https://www.addgene.org/{number}/sequences/"
ADDGENE_FILE = re.compile(r"https://media\.addgene\.org/[^\s\"'<>]+?\.gbk", re.I)
WAIT = 15
MOST = 5_000_000            # bytes: a plasmid's record is far smaller; a genome is refused
AGENT = "BioManager (lab plasmid records)"

ACCESSION = re.compile(r"^[A-Z]{1,2}_?[A-Z]{0,4}\d{5,9}(?:\.\d+)?$")


class FetchError(Exception):
    """Why nothing came back, as a code the routes put in words."""

    def __init__(self, code: str, **details):
        super().__init__(code)
        self.code, self.details = code, details


def switched_off() -> bool:
    return (os.environ.get("BIOMANAGER_SEQUENCE_FETCH") or "").strip().lower() in ("0", "off", "false", "no")


def parse_ref(raw: str) -> tuple[str, str]:
    """("addgene", "11150") or ("ncbi", "L09137.2") from what was typed:
    "Addgene #11150", "addgene 11150", "#11150" or "11150" (a bare number is
    Addgene's), or an accession such as L09137, NC_045512.2, MN908947.3."""
    text = (raw or "").strip()
    lowered = text.lower()
    if "addgene" in lowered or re.fullmatch(r"#?\s*\d+", text):
        digits = re.search(r"\d+", text)
        if digits:
            return "addgene", digits.group(0)
        raise FetchError("unreadable")
    acc = re.sub(r"^(ncbi|genbank|nuccore)[\s:#]*", "", text, flags=re.I).strip().upper()
    if ACCESSION.fullmatch(acc):
        return "ncbi", acc
    raise FetchError("unreadable")


def _get(url: str, opener) -> str:
    req = Request(url, headers={"User-Agent": AGENT, "Accept": "text/plain, text/html, */*"})
    try:
        with opener(req, timeout=WAIT) as r:  # noqa: S310 (NCBI's or Addgene's address only)
            body = r.read(MOST + 1)
    except Exception as e:  # noqa: BLE001 — offline, refused, not found: told apart below
        code = getattr(e, "code", None)
        if code == 404 or code == 400:
            raise FetchError("not_found") from e
        raise FetchError("offline") from e
    if len(body) > MOST:
        raise FetchError("too_big")
    return body.decode("utf-8", errors="replace")


def _ncbi(accession: str, opener) -> str:
    text = _get(EFETCH + quote(accession), opener)
    if "LOCUS" not in text[:200]:
        raise FetchError("not_found")
    return text


def _addgene(number: str, opener) -> tuple[str, str]:
    page = _get(ADDGENE_PAGE.format(number=number), opener)
    files = ADDGENE_FILE.findall(page)
    if not files:
        raise FetchError("no_file")
    # Addgene's own full sequence first, then the depositor's, then any.
    def rank(url: str) -> int:
        lower = url.lower()
        return 0 if "addgene-plasmid" in lower and "partial" not in lower else 1
    chosen = sorted(dict.fromkeys(files), key=rank)[0]
    text = _get(chosen, opener)
    if "LOCUS" not in text[:200]:
        raise FetchError("no_file")
    return text, chosen


def fetch(raw: str, opener=None) -> dict:
    """The GenBank text for what was typed, where it came from, and a label
    for the plasmid's notes. Raises FetchError."""
    opener = opener or urlopen
    if switched_off():
        raise FetchError("switched_off")
    source, ref = parse_ref(raw)
    if source == "ncbi":
        text = _ncbi(ref, opener)
        return {"source": "ncbi", "ref": ref, "text": text, "label": f"NCBI {ref}",
                "url": f"https://www.ncbi.nlm.nih.gov/nuccore/{quote(ref)}"}
    text, file_url = _addgene(ref, opener)
    return {"source": "addgene", "ref": ref, "text": text, "label": f"Addgene #{ref}",
            "url": f"https://www.addgene.org/{ref}/", "file": file_url}
