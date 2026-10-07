"""Columns a lab adds to the built-in databases.

The configurable databases — inventories, stock collections, any organism —
have taken custom columns from the start; the mouse colony, zebrafish and
plasmids never could, because their columns are real ones. Each of their
records gets an `attrs` JSON column of its own, holding only what the lab
added, the way an inventory item has always carried its own (issue #39).
The definitions live in `app_settings` under `custom_fields:<database>`, so
adding a column is a setting, not a schema change.
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column

revision = "0025_builtin_custom_fields"
down_revision = "0024_mouse_ear_tag"
branch_labels = None
depends_on = None

TABLES = ("mice", "fish", "tanks", "plasmids")


def upgrade() -> None:
    for table in TABLES:
        add_column(table, sa.Column("attrs", sa.Text(), server_default=""))


def downgrade() -> None:
    pass
