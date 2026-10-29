from sqlalchemy import ForeignKey, Integer, Text, String, DateTime, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base
from datetime import datetime

"""用来存会话聊天信息"""
class Message(Base):
    __tablename__ = "message"

    response_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    conversation_id:Mapped[int] = mapped_column(
        ForeignKey("conversation.id"),
        nullable=False,
        index=True
    )

    # 同一轮问题和回答共用该 ID；已有历史消息允许为空。
    turn_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )

    #这里只存一条消息
    content:Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    role: Mapped[str] = mapped_column(
    String(20),
    nullable=False,
)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
