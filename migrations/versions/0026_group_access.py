"""What a project group's members may do in it.

Each group gets six switches (edit what is shared with it, add and change
records in its databases, edit each other's records there, edit its
notebook pages, tick off its to-dos, share their own things with it), and
each member a "can edit": off, they only see what is shared with the group.
Every one starts as groups worked before, so nothing changes until a lead
or an admin changes it (app/groups.py member_may).
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column

revision = "0026_group_access"
down_revision = "0025_builtin_custom_fields"
branch_labels = None
depends_on = None

SWITCHES = (("may_edit_shared", True), ("may_change_records", True), ("may_edit_each_other", False),
            ("may_edit_pages", True), ("may_tick_todos", True), ("may_share", True))


def upgrade() -> None:
    for name, on in SWITCHES:
        add_column("lab_groups", sa.Column(name, sa.Boolean(), nullable=False,
                                           server_default=sa.true() if on else sa.false()))
    add_column("lab_group_members", sa.Column("can_edit", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    pass
