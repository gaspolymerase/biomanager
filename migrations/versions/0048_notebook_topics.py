"""Topics inside topics, a topic's icon, and favourite pages.

A notebook topic can now sit in another (`notebook_tabs.parent_tab_id`;
none: at the top, where every topic was) and has an icon
(`notebook_tabs.icon`; empty: a folder). A starred page is a row of the new
`notebook_favorites`. Every topic made before stays at the top as a folder,
and nobody has favourites yet.
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column, create_table

revision = "0048_notebook_topics"
down_revision = "0047_zotero_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("notebook_tabs", sa.Column("parent_tab_id", sa.Integer(), nullable=True))
    add_column("notebook_tabs", sa.Column("icon", sa.String(40), nullable=False, server_default=""))
    create_table("notebook_favorites")


def downgrade() -> None:
    pass
