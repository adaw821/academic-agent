from datetime import date
from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

"""用来存课程时间"""
class CourseSchedule(Base):
    __tablename__="courses_schedule"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    course_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("courses.id"),
        nullable=False
    )

    course: Mapped["Course"] = relationship(
        back_populates="timetable"
    )

    day_of_week: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    start_time: Mapped[str] = mapped_column(
        String,
        nullable=False
    )
 
    end_time: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    location: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    term_name: Mapped[str | None] = mapped_column(String(100))
    term_start: Mapped[date | None] = mapped_column(Date)
    term_end: Mapped[date | None] = mapped_column(Date)
    start_week: Mapped[int | None] = mapped_column(Integer)
    end_week: Mapped[int | None] = mapped_column(Integer)
    week_pattern: Mapped[str] = mapped_column(String(10), nullable=False, default='all', server_default='all')
