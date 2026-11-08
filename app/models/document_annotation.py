"""AI 写入文件内部的批注记录：一条记录对应文件里的一个批注锚点。

文件副本本身是“投影”：任何时候都从原文件重新生成并重放这里的所有记录，
因此删除记录就等于撤销该批注，不会破坏原文件。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class DocumentAnnotation(Base):
    __tablename__ = "document_annotation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"), nullable=False, index=True
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )

    # 来源消息与引用编号，便于回溯“这条批注是哪次回答加的”
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cite_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 单轮回答的指纹（问题 + 回答）：同一处原文被新一轮引用时覆盖旧批注
    turn_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 锚定原文（用子块原文，与前端高亮同一份文本）
    anchor_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 去重用的指纹：同一文档 + 同一段落原文只保留一条
    anchor_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 一句话结论
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # 页码提示（1 起），pdf/pptx 用来缩小搜索范围
    pages: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # docx_comment / pdf_highlight / pptx_note
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
