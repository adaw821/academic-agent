"""School connection, initial import and saved timetable retrieval."""
from datetime import date
from app.services.school_timetable_qa import query_timetable
from app.services.teaching_calendar import TeachingCalendar
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, SecretStr, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.models.course_resources import CourseResource
from app.models.users import User
from app.services.user_services import get_currentUser
from app.services.school_sessions import load_school_session, save_school_session, remove_school_session
from app.services.ispace import ISpaceClient, SchoolError, SchoolLoginRequired, activity_url
from app.services.school_sync import sync_course
from app.services.school_downloads import download_resource, download_status
from app.services.school_imports import import_resource
from app.services.durable_workflows import run_workflow

router = APIRouter(tags=["school"])


class SchoolConnection(BaseModel):
    session: SecretStr

    @field_validator("session", mode="before")
    @classmethod
    def validate_session(cls, value):
        import re
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,512}', value):
            # Avoid Pydantic's validation-error input field echoing a credential.
            raise HTTPException(422, "学校会话格式无效")
        return value


class ImportCourse(BaseModel):
    source_url: str = Field(min_length=1, max_length=500)


class ImportResource(BaseModel):
    project_id: int = Field(gt=0)


def school_call(user_id, callback, *, cookie=None):
    try:
        with ISpaceClient(cookie if cookie is not None else load_school_session(user_id)) as client:
            return callback(client)
    except SchoolLoginRequired:
        raise HTTPException(409, detail={"code": "school_login_required", "message": "学校会话缺失或已过期"}) from None
    except (SchoolError, ValueError):
        raise HTTPException(502, "学校访问或页面解析失败") from None


@router.put("/school/session")
def connect_school(request: SchoolConnection, user: User = Depends(get_currentUser)):
    cookie = request.session.get_secret_value()
    school_call(user.id, lambda client: client.discover_courses(), cookie=cookie)
    save_school_session(user.id, cookie)
    return {"configured": True, "validated": True}


@router.get("/school/session")
def school_status(user: User = Depends(get_currentUser)):
    try:
        load_school_session(user.id)
        configured = True
    except SchoolLoginRequired:
        configured = False
    return {"configured": configured, "validated": False}


@router.delete("/school/session", status_code=204)
def disconnect_school(user: User = Depends(get_currentUser)):
    remove_school_session(user.id)
    return Response(status_code=204)


@router.get("/school/courses")
def discover_courses(user: User = Depends(get_currentUser)):
    return school_call(user.id, lambda client: client.discover_courses())


@router.post("/courses/sync")
def import_course(request: ImportCourse, db: Session = Depends(get_db),
                  user: User = Depends(get_currentUser)):
    try:
        url = activity_url(request.source_url, '/course/view.php')
    except (SchoolError, ValueError):
        raise HTTPException(422, "请提供有效的学校课程链接") from None
    user_id = user.id
    db.rollback()
    snapshot = school_call(user_id, lambda client: client.collect_course(url))
    if snapshot.source_url != url:
        raise HTTPException(409, '学校来源不一致，未导入课程')
    try:
        return sync_course(db, user_id, snapshot)
    except Exception:
        db.rollback()
        raise HTTPException(500, "同步入库失败，已回滚") from None


@router.get("/courses")
def courses(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
            db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    table = Course.__table__
    where = table.c.user_id == user.id
    rows = db.execute(select(table).where(where).order_by(table.c.id).offset(offset).limit(limit)).mappings().all()
    return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset,
            "total": db.scalar(select(func.count()).select_from(table).where(where))}


@router.get("/courses/{course_id}/resources")
def course_resources(course_id: int, limit: int = Query(20, ge=1, le=100),
                     offset: int = Query(0, ge=0),
                     source_status: Literal['seen', 'not_seen', 'unknown'] | None = None,
                     assignment_id: int | None = Query(None, gt=0),
                     db: Session = Depends(get_db),
                     user: User = Depends(get_currentUser)):
    if not db.scalar(select(Course.id).where(Course.id == course_id, Course.user_id == user.id)):
        raise HTTPException(404, "课程不存在")
    table = CourseResource.__table__
    # Successful sync stamps the course and every observed resource together.
    # Absence is an observation, not proof that the school deleted the file.
    observed = case(
        (Course.last_synced_at.is_(None), 'unknown'),
        (table.c.last_synced_at == Course.last_synced_at, 'seen'),
        (table.c.last_synced_at < Course.last_synced_at, 'not_seen'),
        else_='unknown',
    )
    where = (table.c.course_id == course_id) & (Course.user_id == user.id)
    if source_status is not None:
        where &= observed == source_status
    if assignment_id is not None:
        where &= table.c.assignment_id == assignment_id
    rows = db.execute(select(table, observed.label('source_status'),
                             Course.last_synced_at.label('source_checked_at'))
                      .join(Course, Course.id == table.c.course_id)
                      .where(where).order_by(table.c.id).offset(offset).limit(limit)).mappings().all()
    # Local attachment paths are server implementation details, not public URLs.
    return {"items": [{key: value for key, value in row.items() if key != "storage_path"} for row in rows],
            "total": db.scalar(select(func.count()).select_from(table)
                               .join(Course, Course.id == table.c.course_id).where(where)),
            "limit": limit, "offset": offset}


@router.post("/courses/{course_id}/resources/{resource_id}/download")
def fetch_resource(course_id: int, resource_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_currentUser)):
    return download_resource(db, user.id, course_id, resource_id)


@router.get("/courses/{course_id}/resources/{resource_id}/download")
def resource_download_status(course_id: int, resource_id: int, db: Session = Depends(get_db),
                             user: User = Depends(get_currentUser)):
    return download_status(db, user.id, course_id, resource_id)


@router.post("/courses/{course_id}/resources/{resource_id}/import", status_code=202)
def import_course_resource(course_id: int, resource_id: int, request: ImportResource,
                           background_tasks: BackgroundTasks, response: Response,
                           db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    result = import_resource(db, user.id, course_id, resource_id, request.project_id)
    if result['reused']:
        response.status_code = 200
    else:
        background_tasks.add_task(run_workflow, result['job_id'])
    return result


@router.get("/timetable")
def timetable(course_id: int | None = Query(None, ge=1), on_date: date | None = None,
              limit: int = Query(20, ge=1, le=20), offset: int = Query(0, ge=0, le=10000),
              db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    if on_date is not None:
        return query_timetable(db, user.id, {'course_id': course_id, 'on_date': on_date, 'limit': limit, 'offset': offset})
    query = select(CourseSchedule, Course).join(Course, Course.id == CourseSchedule.course_id).where(Course.user_id == user.id)
    if course_id is not None:
        query = query.where(Course.id == course_id)
    weekday = case({name: index for index, name in enumerate(
        ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])}, value=CourseSchedule.day_of_week, else_=7)
    rows = db.execute(query.order_by(weekday, CourseSchedule.start_time, CourseSchedule.id)).all()
    items = [{"id": schedule.id, "course_id": course.id, "course_name": course.course_name,
              "course_code": course.course_code, "teachers": course.teachers, "units": course.units,
              "day_of_week": schedule.day_of_week, "start_time": schedule.start_time,
              "end_time": schedule.end_time, "location": schedule.location,
              **{key: getattr(schedule, key) for key in ("term_name", "term_start", "term_end", "start_week", "end_week", "week_pattern")}}
             for schedule, course in rows]
    return {"items": items, "total": len(items), "timezone": "Asia/Shanghai"}




@router.put('/timetable/{schedule_id}/calendar')
def set_teaching_calendar(schedule_id: int, request: TeachingCalendar,
                          db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    schedule = db.scalar(select(CourseSchedule).join(Course).where(
        CourseSchedule.id == schedule_id, Course.user_id == user.id).with_for_update())
    if schedule is None:
        raise HTTPException(404, '课表记录不存在')
    for key, value in request.model_dump().items():
        setattr(schedule, key, value)
    db.commit()
    return dict(schedule_id=schedule_id, **request.model_dump(mode='json'))
