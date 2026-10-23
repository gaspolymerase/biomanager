"""A litter's father, beside its mother.

Litter born now asks for the father and the mother, so Genotyping can fill
both in from the litter it makes pups of (`cage_litters.sire`, as typed: a
mouse number or a note). Every litter recorded before starts with none.
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column

revision = "0045_litter_sire"
down_revision = "0044_colony_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("cage_litters", sa.Column("sire", sa.String(120), nullable=False, server_default=""))


def downgrade() -> None:
    pass
