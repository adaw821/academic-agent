from sqlalchemy import ForeignKey, Integer, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.db.database import Base
"""用来存会话"""

class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key = True
    )

    # 绑定项目时用于资料检索与归属；无项目的默认对话此列为空，归属改看 user_id。
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"),
        nullable=True,
        index=True
    )

    # 无项目对话的归属锚：项目对话也一并写入，便于统一校验。
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True
    )

    title: Mapped[str] = mapped_column(
    String(100),
    nullable=False,
    default="新对话",
)

    # 尚未压缩时为空；摘要不替代消息表中的完整聊天记录。
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # 摘要已覆盖到的消息 ID（包含该条），作为读取后续消息的边界。
    summarized_until_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
