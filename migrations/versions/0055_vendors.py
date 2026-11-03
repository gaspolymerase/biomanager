"""The lab's vendors.

A `vendors` table: what the lab keeps about each vendor it orders from
(website, contact, email, phone, customer number, notes), for the Vendors
page (app/vendors.py). Records still name their vendor as text, as before;
nothing already stored changes.
"""
from __future__ import annotations

from migrations.helpers import create_table

revision = "0055_vendors"
down_revision = "0054_breeder_tanks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("vendors")


def downgrade() -> None:
    pass
