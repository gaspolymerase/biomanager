"""Baseline: the schema as it stood before Alembic.

Intentionally a no-op. Existing databases were built by
`Base.metadata.create_all()` plus the hand-written ALTERs in
startup.ensure_schema_updates(), so there is nothing to replay — this
revision exists only to give those databases a version to be stamped with:

    alembic stamp 0001_baseline

A brand-new database is still created by create_all() on first boot and
then stamped automatically (see app/services.py). Every schema change from
here on gets its own revision:

    alembic revision --autogenerate -m "add whatever"
    alembic upgrade head
"""
from __future__ import annotations

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
