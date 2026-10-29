"""Persist a complete course snapshot atomically; no network calls in transactions."""
from datetime import datetime, timezone
from sqlalchemy import select
from app.models.courses import Course
from app.models.assignments import Assignment
from app.models.course_resources import CourseResource
from app.models.users import User


def sync_course(db, user_id, snapshot):
    """Caller must supply the authenticated local user ID, never an LLM-selected ID."""
    courses, assignments, resources = Course.__table__, Assignment.__table__, CourseResource.__table__
    now = datetime.now(timezone.utc)
    warnings = list(snapshot.warnings)
    try:
        # Serializes syncs for this user on PostgreSQL and verifies the owner exists.
        if db.execute(select(User.__table__.c.id).where(User.__table__.c.id == user_id).with_for_update()).scalar_one_or_none() is None:
            raise ValueError('本地用户不存在')
        course_id = db.execute(select(courses.c.id).where(courses.c.user_id == user_id, courses.c.source_url == snapshot.source_url)).scalar_one_or_none()
        values = dict(course_name=snapshot.title, external_course_id=snapshot.external_course_id, last_synced_at=now)
        if course_id is None:
            course_id = db.execute(courses.insert().values(user_id=user_id, source_url=snapshot.source_url, **values).returning(courses.c.id)).scalar_one()
        else:
            db.execute(courses.update().where(courses.c.id == course_id).values(**values))
        db.execute(assignments.update().where(assignments.c.course_id == course_id).values(source_status="not_seen"))
        assignment_ids = {}
        for item in snapshot.assignments:
            condition = (assignments.c.course_id == course_id) & (assignments.c.external_activity_id == item['external_activity_id'])
            existing = db.execute(select(assignments.c.id).where(condition)).scalar_one_or_none()
            values = dict(item, last_synced_at=now, source_status="available")
            if existing is None:
                existing = db.execute(assignments.insert().values(course_id=course_id, **values)
                                      .returning(assignments.c.id)).scalar_one()
            else:
                db.execute(assignments.update().where(assignments.c.id == existing).values(**values))
            assignment_ids[item['external_activity_id']] = existing
        for item in snapshot.resources:
            condition = (resources.c.course_id == course_id) & (resources.c.source_key == item['source_key'])
            existing = db.execute(select(resources).where(condition)).mappings().one_or_none()
            values = dict(item, last_synced_at=now)
            references = values.pop('assignment_external_ids', None)
            if existing is not None:
                # Leave manual classification and attachment caches untouched.
                values.pop('purpose', None)
            if references is not None:
                if not isinstance(references, list) or any(not isinstance(ref, str) or ref not in assignment_ids for ref in references):
                    raise ValueError('附件引用了本次快照中不存在的作业')
                linked = {assignment_ids[ref] for ref in references}
                if len(linked) > 1:
                    warnings.append('发现多个作业共用的附件；不自动指定唯一作业，已有人工关联保留。')
                # Dedicated purpose identifies links this collector may later clear.
                current = existing if existing is not None else item
                managed = (current.get('purpose', 'unknown') == 'assignment_attachment' or
                           (current.get('purpose', 'unknown') == 'unknown' and current.get('assignment_id') is None))
                if managed:
                    assignment_id = next(iter(linked)) if len(linked) == 1 else None
                    values.update(assignment_id=assignment_id,
                                  purpose='assignment_attachment' if assignment_id is not None else 'unknown')
            if existing is None:
                db.execute(resources.insert().values(course_id=course_id, **values))
            else:
                db.execute(resources.update().where(resources.c.id == existing['id']).values(**values))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return dict(course_id=course_id, assignments=len(snapshot.assignments), resources=len(snapshot.resources),
                warnings=list(dict.fromkeys(warnings)))
