"""Persisted local inbox; fixed 24-hour horizon, no external message delivery."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, func, literal, select, update
from app.models.assignments import Assignment
from app.models.courses import Course
from app.models.deadline_reminder import DeadlineReminder
from app.models.users import User
from app.services.assignment_queries import CHINA, PENDING

WARNING = '提醒依据最近同步的本地数据；已读不代表已提交，请以学校系统为准。'


def eligible(user_id, now):
    a, c = Assignment.__table__, Course.__table__
    return and_(c.c.user_id == user_id, a.c.source_status != "not_seen",
        func.lower(func.trim(func.coalesce(a.c.submission_status, ''))).in_(PENDING),
        a.c.due_at > now, a.c.due_at <= now + timedelta(hours=24),
        c.c.last_synced_at >= now - timedelta(hours=24), c.c.last_synced_at <= now,
        a.c.last_synced_at == c.c.last_synced_at)


def generate_deadline_reminders(db, user_id):
    now = datetime.now(timezone.utc)
    a, c, r = Assignment.__table__, Course.__table__, DeadlineReminder.__table__
    # Match the user lock taken by school synchronization; uniqueness also guards replays.
    if db.scalar(select(User.__table__.c.id).where(User.__table__.c.id == user_id).with_for_update()) is None:
        raise ValueError('local user missing')
    dialect = db.get_bind().dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ValueError('unsupported database')
    candidates = select(a.c.id, a.c.due_at, literal(now)).select_from(a.join(c, a.c.course_id == c.c.id)).where(eligible(user_id, now))
    statement = insert(r).from_select(['assignment_id', 'due_at', 'created_at'], candidates).on_conflict_do_nothing(
        index_elements=['assignment_id', 'due_at']).returning(r.c.id)
    return len(db.execute(statement).all())


def list_deadline_reminders(db, user_id, *, active_only=True, unread_only=False, limit=20, offset=0):
    now = datetime.now(timezone.utc)
    a, c, r = Assignment.__table__, Course.__table__, DeadlineReminder.__table__
    joined = r.join(a, r.c.assignment_id == a.c.id).join(c, a.c.course_id == c.c.id)
    active = func.coalesce(and_(eligible(user_id, now), r.c.due_at == a.c.due_at), False)
    scope = [c.c.user_id == user_id]
    if active_only:
        scope.append(active)
    unread_count = db.scalar(select(func.count()).select_from(joined).where(*scope, r.c.read_at.is_(None)))
    if unread_only:
        scope.append(r.c.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(joined).where(*scope))
    rows = db.execute(select(r, a.c.title, a.c.course_id, a.c.due_at.label('current_due_at'),
                             a.c.last_synced_at, c.c.course_name, active.label('active')).select_from(joined)
                      .where(*scope).order_by(r.c.due_at, r.c.id).limit(limit).offset(offset)).mappings()
    items = []
    for row in rows:
        item = dict(row)
        for key in ('due_at', 'current_due_at', 'created_at', 'read_at', 'last_synced_at'):
            value = item[key]
            if value is not None:
                # Production PostgreSQL is aware; scheduler SQLite fixtures store UTC wall time.
                item[key] = (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(CHINA)
        items.append(item)
    return dict(items=items, total=total, unread_count=unread_count, limit=limit, offset=offset,
                timezone='Asia/Shanghai', warning=WARNING)


def mark_reminder_read(db, user_id, reminder_id):
    a, c, r = Assignment.__table__, Course.__table__, DeadlineReminder.__table__
    owned = select(a.c.id).join(c, a.c.course_id == c.c.id).where(c.c.user_id == user_id)
    return db.execute(update(r).where(r.c.id == reminder_id, r.c.assignment_id.in_(owned)).values(
        read_at=func.coalesce(r.c.read_at, datetime.now(timezone.utc))).returning(r.c.id)).scalar_one_or_none()
