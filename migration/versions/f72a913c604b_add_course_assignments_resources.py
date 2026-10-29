"""Add school course sources, assignments and resource metadata.

Revision ID: f72a913c604b
Revises: e63f2b908a14
"""
from alembic import op
import sqlalchemy as sa

revision = "f72a913c604b"
down_revision = "e63f2b908a14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("courses") as batch:
        batch.add_column(sa.Column("source_url", sa.String(500), nullable=True))
        batch.add_column(sa.Column("external_course_id", sa.String(100), nullable=True))
        batch.add_column(sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("uq_courses_user_source", ["user_id", "source_url"])
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("external_activity_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_timezone", sa.String(64), nullable=True),
        sa.Column("due_at_raw", sa.Text(), nullable=True),
        sa.Column("submission_status", sa.String(100), nullable=True),
        sa.Column("grading_status", sa.String(100), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("course_id", "external_activity_id", name="uq_assignments_course_activity"),
        sa.UniqueConstraint("id", "course_id", name="uq_assignments_id_course"),
    )
    op.create_index("ix_assignments_course_due", "assignments", ["course_id", "due_at"])
    op.create_index("ix_assignments_due_at", "assignments", ["due_at"])
    op.create_table(
        "course_resources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=True),
        sa.Column("source_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("purpose", sa.String(50), nullable=False, server_default="unknown"),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("course_id", "source_key", name="uq_course_resources_source"),
        sa.ForeignKeyConstraint(
            ["assignment_id", "course_id"], ["assignments.id", "assignments.course_id"],
            name="fk_course_resources_assignment_course",
        ),
    )
    op.create_index("ix_course_resources_assignment", "course_resources", ["assignment_id", "course_id"])


def downgrade() -> None:
    op.drop_table("course_resources")
    op.drop_table("assignments")
    with op.batch_alter_table("courses") as batch:
        batch.drop_constraint("uq_courses_user_source", type_="unique")
        batch.drop_column("last_synced_at")
        batch.drop_column("external_course_id")
        batch.drop_column("source_url")
