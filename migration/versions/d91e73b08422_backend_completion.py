"""Durable workflows, parsed document text and answer provenance."""
from alembic import op
import sqlalchemy as sa

revision = "d91e73b08422"
down_revision = "b83d602a194f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("parsed_text", sa.Text(), nullable=True))
    op.add_column("message", sa.Column("response_data", sa.JSON(), nullable=True))
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), unique=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("checkpoints", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("last_node", sa.String(50)),
        sa.Column("error", sa.String(100)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(32)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("project_id", "status", "lease_until"):
        op.create_index(f"ix_workflow_runs_{column}", "workflow_runs", [column])


def downgrade():
    op.drop_table("workflow_runs")
    op.drop_column("message", "response_data")
    op.drop_column("documents", "parsed_text")
