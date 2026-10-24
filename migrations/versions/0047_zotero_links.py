"""A person's Zotero library, read to make a paper note for each paper added
to it (app/zotero.py): a new table, zotero_links. Nothing else changes.
"""
from __future__ import annotations

from migrations.helpers import create_table

revision = "0047_zotero_links"
down_revision = "0046_litter_sire"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("zotero_links")


def downgrade() -> None:
    pass
