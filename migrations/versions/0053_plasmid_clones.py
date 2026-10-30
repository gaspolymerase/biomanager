"""Clones checked by sequencing.

A `plasmid_clones` table: one row per clone of a plasmid whose reads were
checked against its map on the Verify tab, with the verdict, the report,
the files its reads are kept in, and who accepted it as the plasmid and
when. Reads checked before this stay in the plasmid's files, as they were.
"""
from __future__ import annotations

from migrations.helpers import create_table

revision = "0053_plasmid_clones"
down_revision = "0052_dropdown_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("plasmid_clones")


def downgrade() -> None:
    pass
