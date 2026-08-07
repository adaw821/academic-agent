from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

"""课程表，只存课程其他时间不存具体时间"""
class Course(Base):
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    ) 

    course_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    course_code: Mapped[str] = mapped_column(
        String(20),
        nullable=True
    )

    teachers: Mapped[str|None] = mapped_column(
        String(50),
        nullable=True
    )

    units: Mapped[float|None] = mapped_column(
        Float,
        nullable=True
    )

    timetable: Mapped[list["CourseSchedule"]] = relationship(
        back_populates="course"
    )
    