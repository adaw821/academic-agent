"""One school assignment per user-owned course; deadlines are assignment fields."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("course_id", "external_activity_id", name="uq_assignments_course_activity"),
        UniqueConstraint("id", "course_id", name="uq_assignments_id_course"),
        Index("ix_assignments_course_due", "course_id", "due_at"),
        Index("ix_assignments_due_at", "due_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False)
    # Moodle view.php?id identifies the course activity, not the internal assignment ID.
    external_activity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Unknown timezone/deadline stays null; raw text allows later correction.
    source_timezone: Mapped[str | None] = mapped_column(String(64))
    due_at_raw: Mapped[str | None] = mapped_column(Text)
    submission_status: Mapped[str | None] = mapped_column(String(100))
    grading_status: Mapped[str | None] = mapped_column(String(100))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    source_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown", server_default="unknown")
