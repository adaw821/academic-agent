"""Conversation can exist without a project (default chat).

Also merges the two open heads (expand_course_teachers / email_verification).
- conversation.project_id becomes nullable (no-project conversations store NULL).
- conversation.user_id is added as the ownership anchor for no-project chats.
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a7c1d94b02"
down_revision = ("27ea591fb462", "c92e81f03a25")
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("conversation", "project_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("conversation", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_conversation_user_id_users", "conversation", "users", ["user_id"], ["id"]
    )
    op.create_index("ix_conversation_user_id", "conversation", ["user_id"])


def downgrade():
    op.drop_index("ix_conversation_user_id", table_name="conversation")
    op.drop_constraint("fk_conversation_user_id_users", "conversation", type_="foreignkey")
    op.drop_column("conversation", "user_id")
    # 还原 NOT NULL 前需确保没有无项目会话残留，否则迁移会失败。
    op.alter_column("conversation", "project_id", existing_type=sa.Integer(), nullable=False)
