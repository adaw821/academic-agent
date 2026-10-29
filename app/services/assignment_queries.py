"""User-scoped assignment queries; no school requests or model calls."""
from datetime import datetime, time, timedelta, timezone
from sqlalchemy import case, func, select
from app.models.assignments import Assignment
from app.models.courses import Course

CHINA = timezone(timedelta(hours=8))
SUBMITTED = ('submitted for grading', 'submitted', '已提交', '已提交供评分')
PENDING = ('no submissions have been made yet', 'no attempt', 'new', 'draft (not submitted)', 'draft', '尚未提交', '未提交', '草稿（未提交）')


def list_assignments(db, user_id, *, course_id=None, due_from=None, due_to=None,
                     status=None, source_status=None, limit=20, offset=0):
    a, c = Assignment.__table__, Course.__table__
    normalized = func.lower(func.trim(func.coalesce(a.c.submission_status, '')))
    state = case((normalized.in_(SUBMITTED), 'submitted'),
                 (normalized.in_(PENDING), 'pending'), else_='unknown')
    filters = [c.c.user_id == user_id]
    if course_id is not None:
        filters.append(a.c.course_id == course_id)
    if due_from is not None:
        filters.append(a.c.due_at >= datetime.combine(due_from, time.min, CHINA))
    if due_to is not None:
        filters.append(a.c.due_at < datetime.combine(due_to + timedelta(days=1), time.min, CHINA))
    if source_status is not None:
        filters.append(a.c.source_status == source_status)
    if status is not None:
        filters.append(state == status)
    joined = a.join(c, a.c.course_id == c.c.id)
    total = db.scalar(select(func.count()).select_from(joined).where(*filters))
    rows = db.execute(select(a, c.c.course_name, state.label('status')).select_from(joined)
                      .where(*filters).order_by(a.c.due_at.asc().nulls_last(), a.c.id.asc())
                      .limit(limit).offset(offset)).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        for key in ('due_at', 'opens_at', 'last_synced_at'):
            value = item[key]
            if value is not None:
                # PostgreSQL returns aware values. SQLite test fixtures use China wall time.
                item[key] = value.replace(tzinfo=CHINA) if value.tzinfo is None else value.astimezone(CHINA)
        items.append(item)
    return dict(items=items, total=total, limit=limit, offset=offset, timezone='Asia/Shanghai')
