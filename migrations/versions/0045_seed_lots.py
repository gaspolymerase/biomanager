"""Seed lots for plant databases: seed harvested from plants, a sowing or a
cross, kept to sow again, with how much is left, where it is kept, its
generation, germination and a selection plate's counts. A sowing gains the
lot it came from and its generation (T2, F3). Every sowing made before
starts with neither, and nothing else changes.
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column, create_index, create_table

revision = "0045_seed_lots"
down_revision = "0044_colony_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("organism_seed_lots")
    add_column("organism_cohorts", sa.Column("seed_lot_id_fk", sa.Integer(), nullable=True))
    add_column("organism_cohorts", sa.Column("generation", sa.String(20), nullable=False, server_default=""))
    create_index("organism_cohorts", "ix_organism_cohorts_seed_lot_id_fk", ["seed_lot_id_fk"])


def downgrade() -> None:
    pass
