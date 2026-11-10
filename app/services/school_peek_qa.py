"""Read-only live peek: scrape one course's latest assignments and diff against
local, without any write. Pairs with sync, which the user confirms separately."""
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select

from app.models.assignments import Assignment
from app.models.courses import Course
from app.services.assignment_queries import CHINA
from app.services.course_matching import literal_course_name_match, semantic_course_matches

TOOL_NAME = 'peek_latest_assignments'
WARNING = ('本次实时读取学校课程页并与本地对比，未写入本地库，也未下载附件或同步资源；'
           '仅反映当前账号可见内容，可能不完整。要落地请另行确认同步。')
MAX_ITEMS = 20


class PeekQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    course_id: int | None = Field(None, strict=True, gt=0, description='仅使用用户提供或此前工具返回的本地课程 ID')
    course_name: str | None = Field(None, min_length=1, max_length=200,
        description='课程名或缩写，原样保留；实时读取针对单门已关联学校来源的本地课程')

    @model_validator(mode='after')
    def one_target(self):
        if self.course_id is not None and self.course_name is not None:
            raise ValueError('课程 ID 与名称只能选一个')
        return self


def _norm(dt):
    """Normalize a possibly-naive datetime to a comparable Beijing-time ISO string."""
    if dt is None:
        return None
    return (dt.replace(tzinfo=CHINA) if dt.tzinfo is None else dt.astimezone(CHINA)).isoformat()


def peek_latest_assignments(db, user_id, raw_args):
    from app.routers.school import school_call
    from app.services.ispace import activity_url

    args = PeekQuery.model_validate(raw_args)
    # Only courses already linked to a school source can be peeked without discovery.
    owned = select(Course.id, Course.course_name, Course.source_url).where(
        Course.user_id == user_id, Course.source_url.is_not(None)).order_by(Course.id)
    if args.course_id is not None:
        matches = db.execute(owned.where(Course.id == args.course_id)).all()
    elif args.course_name:
        matches = db.execute(owned.where(func.lower(Course.course_name) == args.course_name.lower())).all()
        if not matches:
            matches = [c for c in db.execute(owned.where(Course.course_name.icontains(args.course_name, autoescape=True))).all()
                       if literal_course_name_match(args.course_name, c.course_name)]
        if not matches:
            candidates = [dict(id=c.id, name=c.course_name) for c in db.execute(owned.limit(201)).all()]
            db.rollback()
            selected, _ = semantic_course_matches(args.course_name, candidates)
            if selected:
                matches = db.execute(owned.where(Course.id.in_([c['id'] for c in selected]))).all()
    else:
        return dict(tool=TOOL_NAME, status='clarification_required', reason='course_required', courses=[], warning=WARNING)
    if len(matches) != 1:
        return dict(tool=TOOL_NAME, status='clarification_required',
                    reason='course_not_found' if not matches else 'ambiguous_course',
                    courses=[dict(id=c.id, name=c.course_name[:200]) for c in matches[:10]], warning=WARNING)
    target = matches[0]
    # Capture the local snapshot before the slow school request, then end the read tx.
    local = {row.external_activity_id: row for row in db.execute(select(
        Assignment.external_activity_id, Assignment.title, Assignment.due_at).where(
        Assignment.course_id == target.id)).all()}
    db.rollback()
    url = activity_url(target.source_url, '/course/view.php')
    snapshot = school_call(user_id, lambda client: client.collect_assignments(url))
    if snapshot.source_url != target.source_url:
        raise HTTPException(409, '学校来源不一致，未对比')
    seen, new, changed = set(), [], []
    for item in snapshot.assignments:
        ext = item['external_activity_id']
        seen.add(ext)
        due, title = _norm(item.get('due_at')), (item.get('title') or '')[:200]
        if ext not in local:
            new.append(dict(external_activity_id=ext, title=title, due_at=due,
                            submission_status=item.get('submission_status')))
        else:
            row = local[ext]
            if _norm(row.due_at) != due or (row.title or '')[:200] != title:
                changed.append(dict(external_activity_id=ext, title=title, due_at=due,
                                    local_title=(row.title or '')[:200], local_due_at=_norm(row.due_at)))
    not_seen = [dict(external_activity_id=ext, title=(row.title or '')[:200])
                for ext, row in local.items() if ext not in seen]
    return dict(tool=TOOL_NAME, status='ok', course_id=target.id, course_name=target.course_name[:200],
                scraped_total=len(snapshot.assignments), local_total=len(local),
                new=new[:MAX_ITEMS], changed=changed[:MAX_ITEMS], not_seen=not_seen[:MAX_ITEMS],
                new_count=len(new), changed_count=len(changed), not_seen_count=len(not_seen), warning=WARNING)
