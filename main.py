from app.config import load_config

load_config()

from fastapi import FastAPI
from app.services.startup import lifespan

from app.models.users import User
from app.models.courses import Course
from app.models.assignments import Assignment
from app.models.course_resources import CourseResource
from app.models.course_timetable import CourseSchedule
from app.models.projects import Project
from app.models.documents import Document
from app.models.document_parents import Parent
from app.models.document_child import Child

from app.routers.timetable_upload import router as timetable_router
from app.routers.rag_flow import router as rag_router
from app.routers.user_routers import router as user_router
from app.routers.assignments import router as assignments_router
from app.routers.course_sync import router as course_sync_router
from app.routers.library import router as library_router
from app.routers.school import router as school_router

app = FastAPI(lifespan=lifespan)
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
app.include_router(assignments_router, prefix="/api")
app.include_router(course_sync_router, prefix="/api")
app.include_router(library_router, prefix="/api")
app.include_router(school_router, prefix="/api")
from app.routers.school_account import router as school_account_router
app.include_router(school_account_router, prefix="/api")

from app.routers.annotations import router as annotations_router
app.include_router(annotations_router, prefix="/api")

from app.routers.mis_timetable import router as mis_timetable_router
app.include_router(mis_timetable_router, prefix="/api")
