from fastapi import FastAPI

from app.models.users import User
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.models.projects import Project
from app.models.documents import Document
from app.models.document_parents import Parent
from app.models.document_child import Child

from app.routers.timetable_upload import router as timetable_router
from app.routers.rag_flow import router as rag_router
from app.routers.user_routers import router as user_router

app = FastAPI()
app.include_router(
    timetable_router,
    prefix="/api"
)
app.include_router(
    rag_router,
    prefix="/api",
)

app.include_router(
    user_router,
    prefix="/api"
)
