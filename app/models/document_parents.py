from sqlalchemy import ForeignKey, Index, Integer, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base
class Parent(Base):
    """父片表"""
    __tablename__ = "document_parent"
    __table_args__ = (Index("ix_document_parent_document_section", "document_id", "section_id"),)

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

    # Section IDs are scoped to this document; legacy/non-PDF rows stay NULL.
    section_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading_path: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    page_numbers: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)

    document: Mapped["Document"] = relationship(
        back_populates="parents"
    )

    childs: Mapped[list["Child"]] = relationship(
        back_populates="parent"
    )
