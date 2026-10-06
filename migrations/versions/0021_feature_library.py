"""The lab's feature library (app/feature_library.py): named sequence
elements that Detect features finds on a plasmid's map."""
from __future__ import annotations

from migrations.helpers import create_index, create_table

revision = "0021_feature_library"
down_revision = "0020_plasmid_parents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("feature_library")
    create_index("feature_library", "ix_feature_library_category", ["category"])
    create_index("feature_library", "ix_feature_library_seq_hash", ["seq_hash"], unique=True)


def downgrade() -> None:
    pass
