"""Folders in the notebook's protocol and recipe libraries.

A lab-wide folder table, and the folder a protocol page (its page info)
or a saved recipe is filed in. Nothing is filed at first.
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column, create_index, create_table

revision = "0027_notebook_folders"
down_revision = "0026_group_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("notebook_folders")
    add_column("notebook_page_info", sa.Column("folder_id", sa.Integer(), nullable=True))
    create_index("notebook_page_info", "ix_notebook_page_info_folder_id", ["folder_id"])
    add_column("notebook_recipes", sa.Column("folder_id", sa.Integer(), nullable=True))
    create_index("notebook_recipes", "ix_notebook_recipes_folder_id", ["folder_id"])


def downgrade() -> None:
    pass
