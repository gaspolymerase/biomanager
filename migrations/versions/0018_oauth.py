"""Connectors (app/oauth.py): assistant apps that registered to connect,
their codes and connections, and the connection codes people make."""
from __future__ import annotations

from migrations.helpers import create_table

revision = "0018_oauth"
down_revision = "0017_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("oauth_clients", "oauth_codes", "oauth_grants", "oauth_link_codes"):
        create_table(name)


def downgrade() -> None:
    pass
