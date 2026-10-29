"""Single-course chat sync, with scoped school discovery for first import."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select

from app.models.courses import Course
from app.models.users import User
from app.services.ispace import SchoolError, activity_url
from app.services.course_matching import literal_course_name_match, semantic_course_matches

TOOL_NAME = 'sync_school_course'
SYNC_WARNING = '本次仅同步课程、作业和资源元数据；未下载附件、导入知识库或提交作业。采集结果可能不完整。'


class CourseSyncQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    course_id: int | None = Field(None, strict=True, gt=0, description='用户明确提供的本地课程 ID，不能猜测学校 ID')
    course_name: str | None = Field(None, min_length=1, max_length=200, description='保留用户原始课程名称或缩写，如 dbms；服务端结合真实列表识别别名。与学校编号同传时名称仅作标签，以编号为准')
    external_course_id: str | None = Field(None, strict=True, pattern=r'^[1-9][0-9]{0,99}$',
        description='仅用于用户明确选择工具返回的学校课程编号；与本地 course_id 不同。服务端会重新发现并验证该编号，不接受课程 URL')

    @field_validator('external_course_id', mode='before')
    @classmethod
    def normalize_school_id(cls, value):
        # JSON integers are a lossless alternative spelling; never coerce bool/float.
        return str(value) if type(value) is int else value

    @model_validator(mode='after')
    def valid_target(self):
        if self.course_id is not None and (self.course_name is not None or self.external_course_id is not None):
            raise ValueError('本地课程 ID 不能与名称或学校课程编号混用')
        return self


def discover_and_sync(db, user_id, args, data):
    from app.routers.school import school_call, import_course, ImportCourse

    # End local lookup before contacting school. Discovery always uses this user's session.
    db.rollback()
    found = school_call(user_id, lambda client: client.discover_courses())
    complete = found.get('complete') is True
    # Revalidate provenance, even though the discovery collector already canonicalizes URLs.
    courses = {}
    try:
        for row in found['courses']:
            url = activity_url(row['source_url'], '/course/view.php')
            ident = parse_qs(urlsplit(url).query)['id'][0]
            if url != row['source_url'] or ident != row['external_course_id'] or not isinstance(row['title'], str):
                raise ValueError('invalid discovered course')
            courses[ident] = dict(external_course_id=ident, name=row['title'], source_url=url)
    except (SchoolError, ValueError, KeyError, TypeError):
        raise HTTPException(502, '学校课程发现结果无效，未执行同步') from None
    candidates = list(courses.values())
    needs_confirmation = False
    if args.external_course_id:
        matches = [c for c in candidates if c['external_course_id'] == args.external_course_id]
    else:
        name = args.course_name.casefold()
        matches = [c for c in candidates if c['name'].casefold() == name]
        if not matches:
            matches = [c for c in candidates if literal_course_name_match(name, c['name'])]
        if not matches:
            matches, needs_confirmation = semantic_course_matches(args.course_name, candidates)
    data = dict(data, lookup_source='school', discovery_complete=complete)
    # A partial list cannot establish uniqueness by name. An explicitly selected,
    # rediscovered school ID is unambiguous even if unrelated courses are missing.
    if needs_confirmation or len(matches) != 1 or (not complete and not args.external_course_id):
        reason = ('course_match_confirmation' if needs_confirmation else
                  'ambiguous_school_course' if len(matches) > 1 else
                  'school_discovery_incomplete' if not complete else 'school_course_not_found')
        choices = matches or candidates
        return dict(data, status='clarification_required', reason=reason,
                    courses=[dict(external_course_id=c['external_course_id'], name=c['name'][:200]) for c in choices[:10]],
                    has_more=len(choices) > 10)
    target = matches[0]
    # Reuse initial import's credential, URL and transactional deduplication checks.
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(401, '账号已不可用，请重新登录')
    result = import_course(ImportCourse(source_url=target['source_url']), db=db, user=user)
    saved_name = db.scalar(select(Course.course_name).where(Course.id == result['course_id'], Course.user_id == user_id))
    warnings = list(result['warnings'])
    if not complete:
        warnings.append('学校课程发现列表不完整；本次仅同步你明确选择且已重新验证可见的学校课程。')
    return dict(data, status='completed', course_name=(saved_name or target['name'])[:200],
                course_id=result['course_id'], assignments=result['assignments'], resources=result['resources'],
                warnings=warnings, completed_at=datetime.now(timezone.utc).isoformat(), warning=SYNC_WARNING)


def sync_school_course(db, user_id, raw_args):
    from app.routers.course_sync import refresh_course

    args = CourseSyncQuery.model_validate(raw_args)
    data = dict(tool=TOOL_NAME, filters=args.model_dump(mode='json'))
    if args.external_course_id:
        return discover_and_sync(db, user_id, args, data)
    owned = select(Course.id, Course.course_name).where(Course.user_id == user_id).order_by(Course.id)
    needs_confirmation = False
    if args.course_id is not None:
        matches = db.execute(owned.where(Course.id == args.course_id)).all()
    elif args.course_name:
        matches = db.execute(owned.where(func.lower(Course.course_name) == args.course_name.lower()).limit(11)).all()
        if not matches:
            matches = [c for c in db.execute(owned.where(Course.course_name.icontains(args.course_name, autoescape=True))).all()
                       if literal_course_name_match(args.course_name, c.course_name)][:11]
        if not matches:
            candidates = [dict(id=c.id, name=c.course_name) for c in db.execute(owned.limit(201)).all()]
            # No DB transaction is held while waiting on model interpretation.
            db.rollback()
            selected, needs_confirmation = semantic_course_matches(args.course_name, candidates)
            if selected:
                matches = db.execute(owned.where(Course.id.in_([c['id'] for c in selected]))).all()
        if not matches:
            return discover_and_sync(db, user_id, args, data)
    else:
        return dict(data, status='clarification_required', reason='course_required', courses=[])
    if needs_confirmation or len(matches) != 1:
        return dict(data, status='clarification_required',
                    reason='course_match_confirmation' if needs_confirmation else 'ambiguous_course' if matches else 'course_not_found',
                    courses=[dict(id=c.id, name=c.course_name[:200]) for c in matches[:10]])
    target = matches[0]
    # Reuse session loading, URL validation, ownership recheck and atomic persistence.
    result = refresh_course(course_id=target.id, db=db, user=db.get(User, user_id))
    return dict(data, status='completed', course_name=target.course_name[:200],
                **{k: result[k] for k in ('course_id', 'assignments', 'resources', 'warnings')},
                completed_at=result['completed_at'].isoformat(), warning=SYNC_WARNING)


def answer_course_sync(data):
    # Report committed results directly; no second model can invent a write outcome.
    if data['status'] == 'clarification_required':
        reasons = {'course_required': '请指定要同步的一门课程名称或本地课程 ID。',
                   'course_match_confirmation': '课程缩写或别名尚不能确定唯一对应，请从以下候选中选择课程编号：',
                   'course_not_found': '没有找到你账号下匹配的课程，请确认课程名称或本地课程 ID。',
                   'ambiguous_course': '找到多门匹配课程，请指定其中一门的本地课程 ID：',
                   'ambiguous_school_course': '学校中找到多门匹配课程，请明确要同步的完整课程名或下列学校课程编号：',
                   'school_course_not_found': '本地与学校发现结果中没有找到匹配课程。请核对正式课程名称或在学校事务页检查连接；若下列候选包含目标，可回复“同步学校课程编号 …”。',
                   'school_discovery_incomplete': '学校课程发现列表不完整，暂不能确认名称是否唯一或课程是否不存在。若下列候选就是目标，请回复“同步学校课程编号 …”；否则请到学校事务页检查连接或导入课程。'}
        answer = reasons[data['reason']]
        if data['courses']:
            answer += '\n' + '\n'.join(
                f"- {c['name']}（学校课程编号：{c['external_course_id']}）" if 'external_course_id' in c
                else f"- {c['name']}（本地课程 ID：{c['id']}）" for c in data['courses'])
        if data.get('has_more'):
            answer += '\n这里只展示前 10 个候选，请提供更完整的课程名称。'
        answer += '\n尚未执行同步。'
    else:
        prefix = '已从学校同步到本地。' if data.get('lookup_source') == 'school' else '已同步。'
        answer = (f"课程“{data['course_name']}”（本地课程 ID：{data['course_id']}）{prefix}"
                  f"本次采集 {data['assignments']} 项作业、{data['resources']} 条资源。\n{SYNC_WARNING}")
        if data['warnings']:
            answer += '\n' + '\n'.join(data['warnings'])
    return dict(answer=answer, sources=[], retrieved_contexts=[], tool_results=[data])
