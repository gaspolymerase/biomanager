"""Files kept with a plasmid: sequencing traces, gel photos, datasheets."""
from __future__ import annotations

from migrations.helpers import create_index, create_table

revision = "0020_plasmid_files"
down_revision = "0019_feature_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("plasmid_files")
    create_index("plasmid_files", "ix_plasmid_files_plasmid_row_id", ["plasmid_row_id"])


def downgrade() -> None:
    pass
