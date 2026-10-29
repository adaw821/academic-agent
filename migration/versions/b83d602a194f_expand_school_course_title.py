"""Allow full school course titles.
Revision ID: b83d602a194f
Revises: f72a913c604b
"""
from alembic import op
import sqlalchemy as sa
revision = 'b83d602a194f'
down_revision = 'f72a913c604b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('courses') as batch:
        batch.alter_column('course_name', existing_type=sa.String(50), type_=sa.String(500), existing_nullable=False)


def downgrade():
    # PostgreSQL refuses overlong existing values rather than silently truncating them.
    with op.batch_alter_table('courses') as batch:
        batch.alter_column('course_name', existing_type=sa.String(500), type_=sa.String(50), existing_nullable=False)
