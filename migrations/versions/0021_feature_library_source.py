"""Where a library element came from, when the lab's own maps did not give
it: the common-features pack (app/feature_pack.py)."""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column

revision = "0021_feature_library_source"
down_revision = "0020_plasmid_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("feature_library", sa.Column("source_name", sa.String(120), server_default=""))


def downgrade() -> None:
    pass
