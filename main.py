from fastapi import FastAPI

from app.models.users import User
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.models.projects import Project
from app.models.documents import Document
from app.models.document_parents import Parent
from app.models.document_child import Child

from app.routers.timetable import router as timetable_router
from app.routers.upload import router as upload_router

app = FastAPI()
app.include_router(
    timetable_router,
    prefix="/api"
)
app.include_router(
    upload_router,
    prefix="/api",
)
