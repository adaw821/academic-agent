from fastapi import FastAPI

from app.db.database import Base, engine
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule

Base.metadata.create_all(bind=engine)

app = FastAPI()

