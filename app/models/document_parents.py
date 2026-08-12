from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base
class Parent(Base):
    """用来回答的大表"""
    __tablename__ = "document_parent"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"),
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

    document: Mapped["Document"] = relationship(
        back_populates="parents"
    )

    childs: Mapped[list["Child"]] = relationship(
        back_populates="parent"
    )
