"""Add turn ID to messages.

Revision ID: c4e28f719b63
Revises: a913d64e8f20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e28f719b63"
down_revision: Union[str, Sequence[str], None] = "a913d64e8f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("message", sa.Column("turn_id", sa.String(length=32), nullable=True))
    op.create_index("ix_message_turn_id", "message", ["turn_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_message_turn_id", table_name="message")
    op.drop_column("message", "turn_id")
