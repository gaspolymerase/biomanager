"""Who has a sheet open.

A new `page_presence` table: a page's address and a person, refreshed while
they have it open, so a sheet can show who else is looking at it. Nothing
in it lasts more than a minute; a database from before simply starts empty.
"""
from __future__ import annotations

from migrations.helpers import create_table

revision = "0049_page_presence"
down_revision = "0048_notebook_topics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("page_presence")


def downgrade() -> None:
    pass
