"""Email verification and delivery rate limits."""
from alembic import op
import sqlalchemy as sa

revision = 'c92e81f03a25'
down_revision = 'a82c431d09be'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('email_codes',
        sa.Column('email', sa.String(255), primary_key=True),
        sa.Column('purpose', sa.String(16), primary_key=True),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('expires', sa.BigInteger(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False))
    op.create_table('email_rates',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('until', sa.BigInteger(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False))


def downgrade():
    op.drop_table('email_rates')
    op.drop_table('email_codes')
