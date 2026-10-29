"""Read-only course inventory and local content coverage, scoped to the user."""
from typing import Literal

from pydantic import Field
from sqlalchemy import func, select

from app.models.assignments import Assignment
from app.models.course_resources import CourseResource
from app.models.courses import Course
from app.services.course_matching import literal_course_name_match, semantic_course_matches
from app.services.school_sync_qa import CourseSyncQuery

TOOL_NAME = 'query_courses'
WARNING = ('结果仅来自当前账号已保存的课程、作业和教学资源元数据；未访问学校或读取文件正文。'
           '“本地无内容”仅指本地没有作业和教学资源记录，不证明学校课程为空，也不代表已导入知识库。'
           '未同步、采集不完整或仅保存课表都可能导致没有内容记录。')


class CourseQuery(CourseSyncQuery):
    course_name: str | None = Field(None, min_length=1, max_length=200,
        description='用户提及的课程名或缩写，原样保留；省略表示查询当前账号全部本地课程')
    content_state: Literal['all', 'no_synced_content', 'has_synced_content', 'not_synced', 'no_resources'] = Field(
        'all', description='all 全部；no_synced_content 本地无作业且无教学资源；has_synced_content 本地有记录；'
        'not_synced 从未成功同步；no_resources 没有本地教学资源。无记录不能证明学校为空')
    limit: int = Field(20, strict=True, ge=1, le=20)
    offset: int = Field(0, strict=True, ge=0, le=10000)


def query_courses(db, user_id, raw_args):
    args = CourseQuery.model_validate(raw_args)
    scope = [Course.user_id == user_id]
    if args.external_course_id:
        scope.append(Course.external_course_id == args.external_course_id)
    elif args.course_id is not None:
        scope.append(Course.id == args.course_id)
    elif args.course_name:
        owned = select(Course.id, Course.course_name).where(Course.user_id == user_id).order_by(Course.id)
        rows = db.execute(owned.where(func.lower(Course.course_name) == args.course_name.lower())).all()
        if not rows:
            rows = [c for c in db.execute(owned.where(Course.course_name.icontains(args.course_name, autoescape=True))).all()
                    if literal_course_name_match(args.course_name, c.course_name)]
        if rows:
            ids = [c.id for c in rows]
        else:
            candidates = [dict(id=c.id, name=c.course_name) for c in db.execute(owned.limit(201)).all()]
            db.rollback()
            matches, uncertain = semantic_course_matches(args.course_name, candidates)
            if uncertain:
                return dict(status='clarification_required', reason='ambiguous_course', courses=matches[:10], warning=WARNING)
            ids = [c['id'] for c in matches]
        scope.append(Course.id.in_(ids))

    # Aggregate each relation separately to avoid multiplying counts in a join.
    assignments = (select(Assignment.course_id, func.count().label('count'))
                   .group_by(Assignment.course_id).subquery())
    resources = (select(CourseResource.course_id, func.count().label('count'))
                 .where(CourseResource.purpose != 'student_submission')
                 .group_by(CourseResource.course_id).subquery())
    ac = func.coalesce(assignments.c.count, 0)
    rc = func.coalesce(resources.c.count, 0)
    joined = (Course.__table__.outerjoin(assignments, assignments.c.course_id == Course.id)
              .outerjoin(resources, resources.c.course_id == Course.id))
    filters = list(scope)
    if args.content_state == 'no_synced_content':
        filters.extend([ac == 0, rc == 0])
    elif args.content_state == 'has_synced_content':
        filters.append(ac + rc > 0)
    elif args.content_state == 'not_synced':
        filters.append(Course.last_synced_at.is_(None))
    elif args.content_state == 'no_resources':
        filters.append(rc == 0)
    total = db.scalar(select(func.count()).select_from(joined).where(*filters))
    # Report the name match separately from the content filter so zero rows are not
    # mistaken for "this course does not exist".
    matched_course_count = db.scalar(select(func.count()).select_from(Course).where(*scope))
    rows = db.execute(select(Course.id, Course.course_name, Course.external_course_id,
        Course.last_synced_at, Course.source_url.is_not(None).label('source_linked'),
        ac.label('assignment_count'), rc.label('resource_count')).select_from(joined).where(*filters)
        .order_by(Course.id).limit(args.limit).offset(args.offset)).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        item['course_name'] = item['course_name'][:200]
        item['sync_status'] = 'synced' if row['last_synced_at'] else 'not_synced'
        item['last_synced_at'] = row['last_synced_at'].isoformat() if row['last_synced_at'] else None
        item['local_content_state'] = 'has_records' if row['assignment_count'] + row['resource_count'] else 'no_records'
        items.append(item)
    return dict(status='ok', items=items, total=total, matched_course_count=matched_course_count,
                limit=args.limit, offset=args.offset, has_more=args.offset + len(items) < total, warning=WARNING)
