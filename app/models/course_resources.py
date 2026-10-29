"""Resource metadata; downloaded file bytes stay outside the database."""
from datetime import datetime

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class CourseResource(Base):
    __tablename__ = "course_resources"
    __table_args__ = (
        UniqueConstraint("course_id", "source_key", name="uq_course_resources_source"),
        ForeignKeyConstraint(
            ["assignment_id", "course_id"], ["assignments.id", "assignments.course_id"],
            name="fk_course_resources_assignment_course",
        ),
        Index("ix_course_resources_assignment", "assignment_id", "course_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False)
    assignment_id: Mapped[int | None] = mapped_column(Integer)
    # Stable activity/file identity; use SHA-256 of the canonical URL when no ID exists.
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # e.g. lecture, assignment_prompt, template, student_submission, unknown.
    purpose: Mapped[str] = mapped_column(String(50), nullable=False, server_default="unknown")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(100))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
