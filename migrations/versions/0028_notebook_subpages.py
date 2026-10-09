"""Sub-pages in the notebook: a page made inside another (/page) keeps
which one, so the sidebar can list it under it. Every page has none at
first.
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column, create_index

revision = "0028_notebook_subpages"
down_revision = "0027_notebook_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("notebook_page_info", sa.Column("parent_page_id", sa.Integer(), nullable=True))
    create_index("notebook_page_info", "ix_notebook_page_info_parent_page_id", ["parent_page_id"])


def downgrade() -> None:
    pass
