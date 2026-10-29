from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.users import User
from app.schemas.assignments import AssignmentList
from app.services.assignment_queries import list_assignments
from app.services.user_services import get_currentUser
from app.services.deadline_reminders import list_deadline_reminders, mark_reminder_read

router = APIRouter(tags=['assignments'])


@router.get('/assignments', response_model=AssignmentList)
def query_assignments(
    course_id: int | None = Query(None, ge=1, description='本地课程 ID，不是学校课程 ID'),
    due_from: date | None = Query(None, description='北京时间起始日期，包含当天'),
    due_to: date | None = Query(None, description='北京时间截止日期，包含当天'),
    status: Literal['submitted', 'pending', 'unknown'] | None = None,
    source_status: Literal['available', 'not_seen', 'unknown'] | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_currentUser),
):
    if due_from and due_to and due_from > due_to:
        raise HTTPException(422, 'due_from 不能晚于 due_to')
    if due_to == date.max:
        raise HTTPException(422, 'due_to 必须早于 9999-12-31')
    return list_assignments(db, user.id, course_id=course_id, due_from=due_from,
                            due_to=due_to, status=status, source_status=source_status, limit=limit, offset=offset)


@router.get('/assignment-reminders')
def reminders(active_only: bool = True, unread_only: bool = False,
              limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
              db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    return list_deadline_reminders(db, user.id, active_only=active_only, unread_only=unread_only, limit=limit, offset=offset)


@router.post('/assignment-reminders/{reminder_id}/read')
def read_reminder(reminder_id: int, db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    if mark_reminder_read(db, user.id, reminder_id) is None:
        raise HTTPException(404, '提醒不存在')
    db.commit()
    return {'id': reminder_id, 'read': True}
