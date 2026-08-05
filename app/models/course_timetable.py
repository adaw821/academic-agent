from sqlalchemy import ForeignKey, Integer, String
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
