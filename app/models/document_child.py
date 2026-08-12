from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.db.database import Base
class Child(Base):
    """用来查找的小表"""
    __tablename__ = "document_child"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    parent_id: Mapped[int] = mapped_column(
        ForeignKey("document_parent.id"),
        nullable=False
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    parent: Mapped["Parent"] = relationship(
        back_populates="childs"
    )

    embedding: Mapped[list[float]] = mapped_column(
        Vector(1024),
        nullable=False,
    )
