"""Encrypted school credentials and onboarding progress."""
from alembic import op
import sqlalchemy as sa

revision = 'da72c3019b80'
down_revision = 'f3a7c1d94b02'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('school_credentials', sa.String(), nullable=True))
    op.add_column('users', sa.Column('school_sync_state', sa.String(30), nullable=True))
    op.add_column('users', sa.Column('school_sync_message', sa.String(300), nullable=True))


def downgrade():
    for name in ('school_sync_message', 'school_sync_state', 'school_credentials'):
        op.drop_column('users', name)
