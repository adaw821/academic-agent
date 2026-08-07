from fastapi import FastAPI

from app.db.database import Base, engine
from app.models.users import User
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule

from app.routers.timetable import router as timetable_router

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.include_router(
    timetable_router,
    prefix="/api"
)
