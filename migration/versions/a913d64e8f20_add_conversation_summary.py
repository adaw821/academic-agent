"""Add conversation summary and covered message boundary.

Revision ID: a913d64e8f20
Revises: 7b2dc4ca892c
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a913d64e8f20"
down_revision: Union[str, Sequence[str], None] = "7b2dc4ca892c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversation", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "conversation",
        sa.Column("summarized_until_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation", "summarized_until_id")
    op.drop_column("conversation", "summary")
