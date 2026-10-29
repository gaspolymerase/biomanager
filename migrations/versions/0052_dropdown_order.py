"""The order of a dropdown's choices.

One column on `dropdown_options`: `position`, the place a choice has in its
column's dropdown, set by dragging it on the colony's Dropdowns tab. Every
choice from before is at 0, so they keep their A–Z order until a lab moves
one.
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column

revision = "0052_dropdown_order"
down_revision = "0051_task_board"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("dropdown_options", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    pass
