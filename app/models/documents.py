from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

class Document(Base):
    __tablename__ = "documents"

    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
    )

    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    storage_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    project: Mapped["Project"] = relationship(
        back_populates="documents",
    )

    parents: Mapped[list["Parent"]] = relationship(
    back_populates="document"
)
