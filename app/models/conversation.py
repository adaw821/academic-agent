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

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
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
