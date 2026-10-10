"""A mouse cage's purposes are Breeder, Breeding and Experiment (see "The
mouse colony's words" in CLAUDE.md). Retired is no purpose any more (a
cage without living mice is simply not active), so retired cages lose it;
"Exp" and "Experiments" are written "Experiment". The colony's purpose
choices (Configure) drop Stock and Retired and gain any of the three they
lack. A Stock cage keeps its word until someone changes it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations.helpers import has_table

revision = "0029_cage_purposes"
down_revision = "0028_notebook_subpages"
branch_labels = None
depends_on = None

DEFAULTS = ("Breeder", "Breeding", "Experiment")


def upgrade() -> None:
    bind = op.get_bind()
    if has_table("mouse_cages"):
        bind.execute(sa.text("UPDATE mouse_cages SET purpose = 'Experiment' "
                             "WHERE lower(trim(purpose)) IN ('exp', 'experiments')"))
        bind.execute(sa.text("UPDATE mouse_cages SET purpose = '' WHERE lower(trim(purpose)) = 'retired'"))
    if has_table("dropdown_options"):
        bind.execute(sa.text("DELETE FROM dropdown_options WHERE field_name = 'purpose' "
                             "AND lower(trim(option_value)) IN ('exp', 'experiments', 'stock', 'retired')"))
        have = {row[0].strip().lower() for row in bind.execute(sa.text(
            "SELECT option_value FROM dropdown_options WHERE field_name = 'purpose'"))}
        for value in DEFAULTS:
            if value.lower() not in have:
                bind.execute(sa.text("INSERT INTO dropdown_options (field_name, option_value, created_at) "
                                         "VALUES ('purpose', :v, CURRENT_TIMESTAMP)"), {"v": value})


def downgrade() -> None:
    pass
