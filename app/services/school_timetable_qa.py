"""Read-only timetable lookup with optional term and teaching-week rules."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import case, func, literal, select
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.services.teaching_calendar import calendar_match

TOOL_NAME = 'query_timetable'
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
WARNING = '结果来自已保存的每周课表；有学期配置的记录按学期和单双周筛选；未配置记录仅按星期匹配，临时调课未纳入。未查到记录不等于当天无课。'


class TimetableQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    course_id: int | None = Field(None, strict=True, gt=0, description='仅使用用户明确提供的本地课程 ID')
    course_name: str | None = Field(None, min_length=1, max_length=200)
    weekday: Literal['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] | None = None
    on_date: date | None = Field(None, description='具体日期，按星期及已配置学期、教学周规则筛选，未纳入临时调课')
    relative_day: Literal['today', 'tomorrow'] | None = Field(None, description='北京时间今天或明天，由服务端换算')
    limit: int = Field(20, strict=True, ge=1, le=20)
    offset: int = Field(0, strict=True, ge=0, le=10000)

    @model_validator(mode='after')
    def valid_filters(self):
        if self.course_id is not None and self.course_name is not None:
            raise ValueError('课程 ID 与名称只能选一个')
        if sum(value is not None for value in (self.weekday, self.on_date, self.relative_day)) > 1:
            raise ValueError('星期、具体日期和相对日期只能选一个')
        return self


def query_timetable(db, user_id, raw_args):
    args = TimetableQuery.model_validate(raw_args)
    # The planner resolves relative dates once so durable replay keeps the same calendar day.
    if args.relative_day is not None:
        raise ValueError('relative day must be resolved before querying')
    weekday = DAYS[args.on_date.weekday()] if args.on_date else args.weekday
    data = dict(schedule_type='weekly_pattern', matched_date=args.on_date.isoformat() if args.on_date else None,
                matched_weekday=weekday, warning=WARNING)
    c, s = Course.__table__, CourseSchedule.__table__
    scope = [c.c.user_id == user_id]
    owned = select(c.c.id, c.c.course_name).where(*scope)
    if args.course_name:
        matches = db.execute(owned.where(func.lower(c.c.course_name) == args.course_name.lower()).limit(11)).all()
        if not matches:
            matches = db.execute(owned.where(c.c.course_name.icontains(args.course_name, autoescape=True)).order_by(c.c.id).limit(11)).all()
    elif args.course_id is not None:
        matches = db.execute(owned.where(c.c.id == args.course_id)).all()
    else:
        matches = None
    if matches is not None:
        if len(matches) != 1:
            return dict(data, status='clarification_required', reason='course_not_found' if not matches else 'ambiguous_course',
                        courses=[dict(id=m.id, name=m.course_name[:200]) for m in matches[:10]])
        scope.append(c.c.id == matches[0].id)
    aliases = {}
    for short, full, chinese in zip(DAYS, ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'), '一二三四五六日'):
        for label in (short.lower(), full.lower(), '周' + chinese, '星期' + chinese):
            aliases[label] = short
    aliases.update({'周天': 'Sun', '星期天': 'Sun'})
    normalized_day = case(aliases, value=func.lower(func.trim(s.c.day_of_week)), else_=None)
    joined = s.join(c, c.c.id == s.c.course_id)
    unknown = db.scalar(select(func.count()).select_from(joined).where(*scope, normalized_day.is_(None)))
    if weekday:
        scope.append(normalized_day == weekday)
    day_order = case({day: i for i, day in enumerate(DAYS)}, value=normalized_day, else_=7)
    start = func.trim(s.c.start_time)
    # Also order legacy H:MM entries correctly alongside the current HH:MM format.
    start_order = case((func.length(start) == 4, literal('0') + start), else_=start)
    rows = db.execute(select(s, c.c.course_name, c.c.course_code, c.c.teachers, normalized_day.label('weekday'))
        .select_from(joined).where(*scope).order_by(day_order, start_order, s.c.id)).mappings()
    items = []
    for row in rows:
        matches, week = calendar_match(row, args.on_date)
        if not matches:
            continue
        item = {key: row[key] for key in ('id', 'course_id', 'weekday')}
        for key in ('day_of_week', 'start_time', 'end_time', 'location', 'course_name', 'course_code', 'teachers'):
            item[key] = row[key][:200] if row[key] is not None else None
        item.update({key: row[key].isoformat() if isinstance(row[key], date) else row[key]
                     for key in ('term_name', 'term_start', 'term_end', 'start_week', 'end_week', 'week_pattern')})
        item.update(teaching_week=week, calendar_configured=row['term_start'] is not None)
        items.append(item)
    total = len(items)
    items = items[args.offset:args.offset + args.limit]
    return dict(data, status='ok', items=items, total=total, limit=args.limit, offset=args.offset,
                has_more=args.offset + len(items) < total, unrecognized_day_count=unknown)
