"""Earlier versions of a plasmid's sequence, to look back at and restore.

Every way a sequence changes (a file, the map editor, the hand edit, Clear,
a restore) records the state it leaves, newest first on the plasmid's
Sequence tab. A plasmid that had a sequence before version history existed
gets that one recorded first ("baseline"), so the first change can be
undone too. The map editor saves after every edit, so one person's edits
within a few minutes of each other are one version, not hundreds; and only
the newest KEEP are kept.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select

from .models import PlasmidRecord, PlasmidSequenceVersion

KEEP = 50
EDITS_JOIN = timedelta(minutes=10)


def versions(session, p: PlasmidRecord) -> list[PlasmidSequenceVersion]:
    return list(session.scalars(select(PlasmidSequenceVersion)
                                .where(PlasmidSequenceVersion.plasmid_row_id == p.id)
                                .order_by(PlasmidSequenceVersion.saved_at.desc(), PlasmidSequenceVersion.id.desc())))


def _newest(session, p: PlasmidRecord) -> PlasmidSequenceVersion | None:
    found = versions(session, p)
    return found[0] if found else None


def _same(v: PlasmidSequenceVersion, p: PlasmidRecord) -> bool:
    return ((v.full_sequence or "") == (p.full_sequence or "") and bool(v.is_circular) == bool(p.is_circular)
            and (v.features_json or "[]") == (p.features_json or "[]"))


def before_change(session, p: PlasmidRecord) -> None:
    """Call before changing the sequence: a sequence from before version
    history is recorded as it was, so the change can be undone."""
    if not (p.full_sequence or "") or _newest(session, p) is not None:
        return
    session.add(PlasmidSequenceVersion(
        plasmid_row_id=p.id, saved_at=p.sequence_uploaded_at or p.updated_at or p.created_at or datetime.utcnow(),
        saved_by=p.updated_by or "", how="baseline", full_sequence=p.full_sequence or "",
        is_circular=bool(p.is_circular), features_json=p.features_json or "[]",
        sequence_format=p.sequence_format or ""))
    session.flush()


def record(session, p: PlasmidRecord, how: str, user: str, detail: str = "") -> None:
    """Call after changing the sequence: keep the state it now has."""
    if p.id is None:
        session.flush()
    now = datetime.utcnow()
    newest = _newest(session, p)
    if newest is not None and _same(newest, p) and how != "restore":
        return
    if (newest is not None and how == "editor" and newest.how == "editor" and newest.saved_by == user
            and now - newest.saved_at < EDITS_JOIN):
        newest.full_sequence, newest.is_circular = p.full_sequence or "", bool(p.is_circular)
        newest.features_json, newest.sequence_format = p.features_json or "[]", p.sequence_format or ""
        newest.saved_at = now
        return
    session.add(PlasmidSequenceVersion(
        plasmid_row_id=p.id, saved_at=now, saved_by=user, how=how, detail=detail[:200],
        full_sequence=p.full_sequence or "", is_circular=bool(p.is_circular),
        features_json=p.features_json or "[]", sequence_format=p.sequence_format or ""))
    session.flush()
    stale = [v.id for v in versions(session, p)[KEEP:]]
    if stale:
        session.execute(delete(PlasmidSequenceVersion).where(PlasmidSequenceVersion.id.in_(stale)))


def restore(session, p: PlasmidRecord, version: PlasmidSequenceVersion, user: str) -> None:
    """Put an earlier version back, as a new version of its own."""
    before_change(session, p)
    p.full_sequence, p.is_circular = version.full_sequence or "", bool(version.is_circular)
    p.features_json, p.sequence_format = version.features_json or "[]", version.sequence_format or ""
    p.sequence_uploaded_at = datetime.utcnow()
    record(session, p, "restore", user, detail=version.saved_at.isoformat(timespec="minutes"))
