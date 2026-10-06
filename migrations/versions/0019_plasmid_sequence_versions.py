"""Earlier versions of a plasmid's sequence (app/plasmid_versions.py), so a
replaced or cleared sequence can be restored from the plasmid's page."""
from __future__ import annotations

from migrations.helpers import create_index, create_table

revision = "0019_plasmid_sequence_versions"
down_revision = "0018_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("plasmid_sequence_versions")
    create_index("plasmid_sequence_versions", "ix_plasmid_sequence_versions_plasmid_row_id", ["plasmid_row_id"])


def downgrade() -> None:
    pass
