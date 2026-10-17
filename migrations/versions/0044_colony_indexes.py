"""Indexes for the colony's sheets. A colony keeps every mouse it ever had,
and its sheets list the living and what ended lately: without these, each
of them read every mouse (a hundred thousand in a large lab) to find the
few thousand it shows, and the counts above them did the same. The mice by
date of death, owner, cage and litter; the cages by when they were made;
the litters by date of birth.
"""
from __future__ import annotations

from migrations.helpers import create_index

revision = "0044_colony_indexes"
down_revision = "0043_cages_share_a_place"
branch_labels = None
depends_on = None

# As the models name them (index=True): ix_<table>_<column>.
INDEXES = {
    "mice": ["date_of_death", "owner", "cage_id_fk", "litter_id_fk"],
    "mouse_cages": ["created_at"],
    "litters": ["date_of_birth"],
}


def upgrade() -> None:
    for table, columns in INDEXES.items():
        for column in columns:
            create_index(table, f"ix_{table}_{column}", [column])


def downgrade() -> None:
    pass
