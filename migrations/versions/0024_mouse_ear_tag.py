"""What is written on the mouse itself: the ear tag (or notch, or tattoo).

The number BioManager gives a mouse stays its identity — unique, and what
every sample, cage card and experiment links by. This is the mark the lab
reads off the animal at the bench, which until now lived in the notes, so
finding the samples of "the one with the right-front punch" meant reading
every note to the end (issue #39).
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column

revision = "0024_mouse_ear_tag"
down_revision = "0023_feature_library_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("mice", sa.Column("ear_tag", sa.String(40), server_default=""))


def downgrade() -> None:
    pass
