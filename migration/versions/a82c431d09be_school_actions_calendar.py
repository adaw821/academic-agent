"""Assignment visibility and teaching calendar."""
from alembic import op
import sqlalchemy as sa

revision = 'a82c431d09be'
down_revision = 'e6a219db503c'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('assignments', sa.Column('source_status', sa.String(20), nullable=False, server_default='unknown'))
    for name, typ in [('term_name', sa.String(100)), ('term_start', sa.Date()), ('term_end', sa.Date()),
                      ('start_week', sa.Integer()), ('end_week', sa.Integer())]:
        op.add_column('courses_schedule', sa.Column(name, typ))
    op.add_column('courses_schedule', sa.Column('week_pattern', sa.String(10), nullable=False, server_default='all'))


def downgrade():
    for name in ['week_pattern', 'end_week', 'start_week', 'term_end', 'term_start', 'term_name']:
        op.drop_column('courses_schedule', name)
    op.drop_column('assignments', 'source_status')
