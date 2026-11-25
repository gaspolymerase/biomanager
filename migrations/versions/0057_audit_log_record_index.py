"""An index for "was this record changed after that batch?" (#166).

Settings' History and the Batches page ask it of every bulk change they
list, so Undo can say whether reverting would throw a later edit away. The
log was indexed by table only, so each answer read every later entry of
that table: a lab that had imported 3000 primers waited 10–20 seconds for
Settings to open. Indexed by table, record and id, it is one lookup.
"""
from __future__ import annotations

from migrations.helpers import create_index

revision = "0057_audit_log_record_index"
down_revision = "0056_retire_animals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_index("audit_log", "ix_audit_log_record", ["table_name", "record_id", "id"])


def downgrade() -> None:
    pass
