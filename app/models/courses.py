from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

"""课程表，只存课程其他时间不存具体时间"""
class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("user_id", "source_url", name="uq_courses_user_source"),
    )

    # Nullable so existing timetable-only courses continue to work.
    source_url: Mapped[str | None] = mapped_column(String(500))
    external_course_id: Mapped[str | None] = mapped_column(String(100))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    ) 

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False
    )

    user: Mapped["User"]=relationship(
        back_populates="courses"
    )

    course_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )

    course_code: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True
    )

    teachers: Mapped[str|None] = mapped_column(
        String(100),
        nullable=True
    )

    units: Mapped[float|None] = mapped_column(
        Float,
        nullable=True
    )

    timetable: Mapped[list["CourseSchedule"]] = relationship(
        back_populates="course"
    )
