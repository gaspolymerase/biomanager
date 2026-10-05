"""Proposed changes (app/proposals.py): what an assistant proposed, waiting
for its person's approval; and lines waiting to be added to a notebook page
open in the live editor (notebook_pending_inserts)."""
from __future__ import annotations

from migrations.helpers import create_index, create_table

revision = "0017_proposals"
down_revision = "0016_whats_new"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table("proposals")
    create_table("proposal_changes")
    create_index("proposals", "ix_proposals_owner_username", ["owner_username"])
    create_index("proposals", "ix_proposals_status", ["status"])
    create_index("proposal_changes", "ix_proposal_changes_proposal_id_fk", ["proposal_id_fk"])
    create_table("notebook_pending_inserts")
    create_index("notebook_pending_inserts", "ix_notebook_pending_inserts_page_id_fk", ["page_id_fk"])


def downgrade() -> None:
    pass
