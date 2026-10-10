"""Stock as a number, with a warning when it runs low (#58): an inventory
item can have a "Low at" level, in the same unit as its quantity. When the
quantity reads as a number and falls to that level, the item turns "low"
(app/inventory_service.py follow_stock_level). Every item has none at
first, so nothing changes until a lab sets one.
"""
from __future__ import annotations

import sqlalchemy as sa

from migrations.helpers import add_column

revision = "0031_low_stock"
down_revision = "0030_several_hazards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("inventory_items", sa.Column("low_at", sa.Float(), nullable=True))


def downgrade() -> None:
    pass
