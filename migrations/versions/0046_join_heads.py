"""Joins the two revisions made side by side after 0044: a litter's father
(0045_litter_sire) and seed lots (0045_seed_lots). Alembic refuses to
upgrade while there are two heads, so a new lab could not start. Nothing
changes in the database here; each of the two still adds its own column or
table, in either order.
"""
from __future__ import annotations

revision = "0046_join_heads"
down_revision = ("0045_litter_sire", "0045_seed_lots")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
