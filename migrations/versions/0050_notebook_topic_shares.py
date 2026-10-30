"""A notebook topic shared with lab mates.

A new `notebook_topic_shares` table: like a page's shares, but for a topic,
whose pages (and the topics inside it) the people named may then open.
Nothing is shared until someone shares a topic; a database from before
starts empty.
"""
from __future__ import annotations

from migrations.helpers import create_table

revision = "0050_notebook_topic_shares"
down_revision = "0049_page_presence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("notebook_topic_shares")


def downgrade() -> None:
    pass
