"""A zebrafish tank of the lab's breeding stock is a breeder tank, as a
mouse cage of it is a Breeder cage (GLOSSARY.md). It was called a
"breeding" tank, which for mice means a mating cage; the fish's mating
tank is "mating" and stays so. Tanks already "breeding" become "breeder",
keeping who may edit them: a breeder tank is shared with the lab, or with
the project group it was shared with, as a breeding tank was.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations.helpers import has_table

revision = "0054_breeder_tanks"
down_revision = "0053_plasmid_clones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if has_table("tanks"):
        op.get_bind().execute(sa.text("UPDATE tanks SET purpose = 'breeder' WHERE lower(trim(purpose)) = 'breeding'"))


def downgrade() -> None:
    pass
