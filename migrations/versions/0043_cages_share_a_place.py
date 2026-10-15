"""A place in a rack may hold more than one cage: the unique index on
mouse_cages(rack_id_fk, rack_row, rack_col) from revision 0006 is dropped.
A cage is often set up in a place before the one there is ended, and the
lab must be able to record where its cages really are. The rack grid shows
the latest of them and marks the place with "!" (services.slot_cages).

placed_at is when a cage was last put in its place; a cage placed before
this revision has none, and counts from when it was made.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column, has_index

revision = "0043_cages_share_a_place"
down_revision = "0042_step_stimulation"
branch_labels = None
depends_on = None

INDEX = "uq_mouse_cages_place"


def upgrade() -> None:
    if has_index("mouse_cages", INDEX):
        op.drop_index(INDEX, table_name="mouse_cages")
    add_column("mouse_cages", sa.Column("placed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    pass
