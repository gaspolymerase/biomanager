"""What each plasmid was made from (app/plasmid_lineage.py): its parents,
their roles, and how it was made, for the plasmid's family tree."""
from __future__ import annotations

from migrations.helpers import create_index, create_table

revision = "0018_plasmid_parents"
down_revision = "0017_plasmid_sequence_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("plasmid_parents")
    create_index("plasmid_parents", "ix_plasmid_parents_child_row_id", ["child_row_id"])
    create_index("plasmid_parents", "ix_plasmid_parents_parent_row_id", ["parent_row_id"])


def downgrade() -> None:
    pass
