"""To-dos on the calendar's Board.

Two columns on `tasks`: `board_list`, the list a to-do is in on the Board
(empty: Inbox), and `board_position`, its place there. Every to-do from
before is in Inbox, in the order it was made.
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column

revision = "0051_task_board"
down_revision = "0050_notebook_topic_shares"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("tasks", sa.Column("board_list", sa.String(80), nullable=False, server_default=""))
    add_column("tasks", sa.Column("board_position", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    pass
