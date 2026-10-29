from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.courses import Course
from app.models.users import User
from app.services.user_services import get_currentUser
from app.services.ispace import ISpaceClient, SchoolError, SchoolLoginRequired, activity_url
from app.services.school_sessions import load_school_session
from app.services.school_sync import sync_course

router = APIRouter(tags=['courses'])


class CourseSyncResult(BaseModel):
    course_id: int
    assignments: int
    resources: int
    warnings: list[str]
    completed_at: datetime


@router.post('/courses/{course_id}/sync', response_model=CourseSyncResult)
def refresh_course(
    course_id: int = Path(ge=1, description='本地课程 ID'),
    db: Session = Depends(get_db),
    user: User = Depends(get_currentUser),
):
    user_id = user.id
    table = Course.__table__
    course = db.execute(select(table.c.source_url).where(table.c.id == course_id, table.c.user_id == user_id)).mappings().one_or_none()
    if course is None:
        raise HTTPException(404, '课程不存在')
    if not course['source_url']:
        raise HTTPException(409, '该课程尚未关联学校来源')
    try:
        url = activity_url(course['source_url'], '/course/view.php')
    except SchoolError:
        raise HTTPException(409, '课程来源链接无效，请重新关联学校课程') from None
    # End the read transaction before potentially slow school requests.
    db.rollback()
    try:
        cookie = load_school_session(user_id)
        with ISpaceClient(cookie) as client:
            snapshot = client.collect_course(url)
    except SchoolLoginRequired:
        raise HTTPException(409, detail={'code':'school_login_required', 'message':'学校会话缺失、已过期或无权访问，请更新当前用户的学校会话'}) from None
    except (SchoolError, ValueError):
        raise HTTPException(502, detail={'code':'school_sync_failed', 'message':'学校访问或页面解析失败，已有数据未更新'}) from None
    if snapshot.source_url != course['source_url']:
        raise HTTPException(409, '学校来源不一致，未更新数据')
    # Recheck ownership after collection, before writing. Lock protects against removal.
    db.execute(select(User.__table__.c.id).where(User.__table__.c.id == user_id).with_for_update()).scalar_one_or_none()
    existing = db.execute(select(table.c.id).where(table.c.id == course_id, table.c.user_id == user_id, table.c.source_url == snapshot.source_url).with_for_update()).scalar_one_or_none()
    if existing is None:
        db.rollback()
        raise HTTPException(409, '课程在同步期间发生变化，请重新查询')
    try:
        result = sync_course(db, user_id, snapshot)
    except Exception:
        db.rollback()
        raise HTTPException(500, '同步入库失败，已回滚') from None
    return dict(result, completed_at=datetime.now(timezone.utc))
