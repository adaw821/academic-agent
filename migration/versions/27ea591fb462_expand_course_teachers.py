"""Expand course teachers to match the model.

Revision ID: 27ea591fb462
Revises: 840667f998e4
"""

from alembic import op
import sqlalchemy as sa


revision = "27ea591fb462"
down_revision = "840667f998e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "courses", "teachers",
        existing_type=sa.String(length=50),
        type_=sa.String(length=100),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "courses", "teachers",
        existing_type=sa.String(length=100),
        type_=sa.String(length=50),
        existing_nullable=True,
    )
