"""Persist assignment deadline inbox reminders."""
from alembic import op
import sqlalchemy as sa

revision = 'e6a219db503c'
down_revision = 'd91e73b08422'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('deadline_reminders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('assignment_id', sa.Integer(), sa.ForeignKey('assignments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('assignment_id', 'due_at', name='uq_deadline_reminder_assignment_due'))


def downgrade():
    op.drop_table('deadline_reminders')
