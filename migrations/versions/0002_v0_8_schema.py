"""The schema of BioManager 0.8, where the start-up ALTERs stop.

A no-op: the frozen ALTERs in startup.ensure_schema_updates() bring any
older database to this shape before Alembic runs. From here on every
schema change is a revision after this one (see migrations/helpers.py).
"""
from __future__ import annotations

revision = "0002_v0_8_schema"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
