"""Read-only assignment attachment lookup; never downloads or imports files."""
from urllib.parse import urlsplit, urlunsplit
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import case, func, select
from app.models.assignments import Assignment
from app.models.courses import Course
from app.models.course_resources import CourseResource
from app.models.documents import Document
from app.models.projects import Project
from app.models.workflow_run import WorkflowRun
from app.services.ispace import SchoolError, activity_url, safe_url
from app.services.school_downloads import resource_directory, saved_file

TOOL_NAME = 'query_assignment_resources'


class AssignmentResourceQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    assignment_id: int | None = Field(None, strict=True, gt=0, description='用户明确提供的本地作业 ID，不猜测学校活动 ID')
    assignment_name: str | None = Field(None, min_length=1, max_length=200, description='作业名称，例如 Lab 1；不是附件文件名')
    course_name: str | None = Field(None, min_length=1, max_length=200, description='可选课程名称，用于区分同名作业')
    limit: int = Field(20, strict=True, ge=1, le=20)
    offset: int = Field(0, strict=True, ge=0, le=10000)

    @model_validator(mode='after')
    def valid_target(self):
        if self.assignment_id is not None and self.assignment_name is not None:
            raise ValueError('作业 ID 与名称只能选一个')
        return self


def attachment_url(value):
    try:
        parsed = urlsplit(safe_url(value))
        if parsed.path.startswith('/pluginfile.php/'):
            # Do not expose arbitrary query parameters from stored source metadata.
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
        return activity_url(value, '/mod/resource/view.php')
    except (SchoolError, ValueError):
        return None


def query_assignment_resources(db, user_id, project_id, raw_args):
    args = AssignmentResourceQuery.model_validate(raw_args)
    if args.assignment_id is None and args.assignment_name is None:
        return dict(status='clarification_required', reason='assignment_required', assignments=[])
    a, c, r = Assignment.__table__, Course.__table__, CourseResource.__table__
    owned = select(a.c.id, a.c.title, a.c.course_id, c.c.course_name).join(c, c.c.id == a.c.course_id).where(c.c.user_id == user_id)
    if args.course_name:
        exact_courses = select(c.c.id).where(c.c.user_id == user_id, func.lower(c.c.course_name) == args.course_name.lower())
        exact_ids = list(db.scalars(exact_courses))
        owned = owned.where(c.c.id.in_(exact_ids)) if exact_ids else owned.where(c.c.course_name.icontains(args.course_name, autoescape=True))
    if args.assignment_id is not None:
        matches = db.execute(owned.where(a.c.id == args.assignment_id)).mappings().all()
    else:
        matches = db.execute(owned.where(func.lower(a.c.title) == args.assignment_name.lower()).limit(11)).mappings().all()
        if not matches:
            matches = db.execute(owned.where(a.c.title.icontains(args.assignment_name, autoescape=True)).order_by(a.c.id).limit(11)).mappings().all()
    if len(matches) != 1:
        return dict(status='clarification_required', reason='assignment_not_found' if not matches else 'ambiguous_assignment',
                    assignments=[dict(id=m['id'], title=m['title'][:200], course_id=m['course_id'],
                                      course_name=m['course_name'][:200]) for m in matches[:10]])
    target = matches[0]
    observed = case((c.c.last_synced_at.is_(None), 'unknown'),
                    (r.c.last_synced_at == c.c.last_synced_at, 'seen'),
                    (r.c.last_synced_at < c.c.last_synced_at, 'not_seen'), else_='unknown')
    scope = [c.c.user_id == user_id, r.c.course_id == target['course_id'], r.c.assignment_id == target['id'],
             r.c.resource_type.in_(['file', 'resource']), r.c.purpose != 'student_submission']
    joined = r.join(c, c.c.id == r.c.course_id)
    total = db.scalar(select(func.count()).select_from(joined).where(*scope))
    rows = db.execute(select(r, observed.label('source_status')).select_from(joined).where(*scope)
                      .order_by(r.c.id).limit(args.limit).offset(args.offset)).mappings().all()
    imports = {}
    if rows and project_id is not None:
        resource_id = WorkflowRun.request['school_resource_id'].as_integer()
        documents = db.execute(select(resource_id.label('resource_id'), Document.id, Document.status).select_from(Document)
            .join(WorkflowRun, WorkflowRun.document_id == Document.id).join(Project, Project.id == Document.project_id)
            .where(Project.user_id == user_id, Project.id == project_id, WorkflowRun.project_id == project_id,
                   WorkflowRun.kind == 'document', resource_id.in_([row['id'] for row in rows])).order_by(Document.id.desc()))
        for doc in documents:
            imports.setdefault(doc.resource_id, dict(document_id=doc.id, status=doc.status))
    items = []
    for row in rows:
        try:
            cached = saved_file(row, resource_directory(user_id, target['course_id'], row['id']))
            cache_status = 'downloaded' if cached else 'not_downloaded'
        except (HTTPException, OSError, ValueError):
            cache_status = 'unavailable'
        items.append(dict(resource_id=row['id'], title=row['title'][:200], resource_type=row['resource_type'],
                          purpose=row['purpose'], source_url=attachment_url(row['source_url']), source_status=row['source_status'],
                          cache_status=cache_status, project_document=imports.get(row['id'])))
    return dict(status='ok', assignment=dict(id=target['id'], title=target['title'][:200], course_id=target['course_id'],
                course_name=target['course_name'][:200]), items=items, total=total, limit=args.limit, offset=args.offset,
                has_more=args.offset + len(items) < total,
                coverage_warning='仅查询已建立关联的教学附件；没有记录不代表学校没有附件。当前工具未读取文件正文。')
