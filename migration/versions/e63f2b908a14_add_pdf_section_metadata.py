"""Add PDF section identity, heading paths and source pages.

Revision ID: e63f2b908a14
Revises: c4e28f719b63
"""

from alembic import op
import sqlalchemy as sa


revision = "e63f2b908a14"
down_revision = "c4e28f719b63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_parent", sa.Column("section_id", sa.Integer(), nullable=True))
    op.add_column("document_parent", sa.Column("heading_path", sa.JSON(), nullable=True))
    op.add_column("document_parent", sa.Column("page_numbers", sa.JSON(), nullable=True))
    op.add_column("document_child", sa.Column("page_numbers", sa.JSON(), nullable=True))
    op.create_index("ix_document_parent_document_section", "document_parent", ["document_id", "section_id"])


def downgrade() -> None:
    op.drop_index("ix_document_parent_document_section", table_name="document_parent")
    op.drop_column("document_child", "page_numbers")
    op.drop_column("document_parent", "page_numbers")
    op.drop_column("document_parent", "heading_path")
    op.drop_column("document_parent", "section_id")
