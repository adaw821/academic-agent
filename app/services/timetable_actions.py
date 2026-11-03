"""Agent timetable tools. Authenticated identity and attachment binding are server-owned."""
import asyncio
import base64
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from app.models.courses import Course
from app.services.timetable_entries import NewSchedule, ScheduleFields, create_entry, update_entry, delete_entry, owned_schedule
from app.services.teaching_calendar import TeachingCalendar


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ScheduleID(EmptyArgs):
    schedule_id: int = Field(strict=True, gt=0, description='query_timetable 返回的安排 id，不是 course_id')


class UpdateSchedule(ScheduleFields):
    schedule_id: int = Field(strict=True, gt=0)


class CalendarAction(TeachingCalendar):
    schedule_id: int = Field(strict=True, gt=0)


class ImageSupplement(EmptyArgs):
    source: Literal['attachment', 'mis'] = Field(description='attachment 使用本轮附加图片；mis 使用已保存的学校快照')


class CourseDetails(EmptyArgs):
    course_id: int = Field(strict=True, gt=0)
    course_name: str | None = Field(default=None, min_length=1, max_length=500)
    course_code: str | None = Field(default=None, max_length=20)
    teachers: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def changes_present(self):
        if not self.model_fields_set - {'course_id'}:
            raise ValueError('请提供要修改的课程字段')
        if 'course_name' in self.model_fields_set and not (self.course_name or '').strip():
            raise ValueError('课程名称不能为空')
        return self


READ_SCHEMAS = {'get_mis_timetable': (EmptyArgs, '查看学校课表获取进度、已保存快照的学期和课程明细；不打开浏览器，不包含登录凭据。')}
ACTION_SCHEMAS = {
    'open_mis_timetable': (EmptyArgs, '用户要求从学校获取课表时打开本机 MIS 窗口。用户需自行登录和选择学期；打开不等于保存成功。'),
    'save_mis_timetable': (EmptyArgs, '用户表示已登录、选好学期并明确要求保存时，保存当前学校窗口课表；返回的是进度，需后续查询确认成功。'),
    'cancel_mis_timetable': (EmptyArgs, '取消当前账号的学校课表获取，保留原快照。'),
    'supplement_timetable_image': (ImageSupplement, '用户明确要求识别图片补充课表时调用。attachment 使用本轮附件，mis 使用已保存学校截图；追加去重，不删除旧错误记录，不自动继承学期。'),
    'add_timetable_entry': (NewSchedule, '手动新增一个上课时段；已有课程按编号或名称关联。必须有课程名称、星期和明确的起止时间。'),
    'update_timetable_entry': (UpdateSchedule, '修改已查询到的一个安排的星期、起止时间和地点，保留学期规则。需要填写全部时间地点字段，未改变字段取自查询结果。'),
    'delete_timetable_entry': (ScheduleID, '用户明确要求删除一个已定位的上课时段时调用，仅删除安排，不删除课程资料。'),
    'set_timetable_calendar': (CalendarAction, '设置一个安排的学期日期、教学周和单双周；仅在用户明确要求清除规则时省略全部规则字段。'),
    'update_course_details': (CourseDetails, '修改已定位课程的名称、编号或教师；影响该课程所有上课时段，仅传用户明确要求更改的字段。'),
}


def get_mis_timetable(user_id):
    from app.services.mis_timetable import mis_capture, read_snapshot
    snapshot = read_snapshot(user_id)
    return dict(tool='get_mis_timetable', **mis_capture.status(user_id),
                snapshot={k: v for k, v in snapshot.items() if k in {'term', 'saved_at', 'courses'}} if snapshot else None)


def execute(db, user_id, name, args):
    if name in {'open_mis_timetable', 'save_mis_timetable', 'cancel_mis_timetable'}:
        from app.services.mis_timetable import mis_capture, enabled
        if not enabled() or not db.info.get('local_browser_allowed', False):
            raise HTTPException(403, '学校窗口工具仅支持已启用本地登录助手的本机请求')
        operation = {'open_mis_timetable': mis_capture.start, 'save_mis_timetable': mis_capture.request_capture,
                     'cancel_mis_timetable': mis_capture.cancel}[name]
        try:
            return operation(user_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
    if name == 'add_timetable_entry':
        return dict(create_entry(args, db, user_id), message='已新增上课安排。')
    if name == 'update_timetable_entry':
        fields = ScheduleFields.model_validate(args.model_dump(exclude={'schedule_id'}))
        return dict(update_entry(args.schedule_id, fields, db, user_id), message='已修改上课安排，原学期规则保留。')
    if name == 'delete_timetable_entry':
        return dict(delete_entry(args.schedule_id, db, user_id), message='已删除该上课时段，课程资料及其他时段保留。')
    if name == 'set_timetable_calendar':
        schedule = owned_schedule(db, user_id, args.schedule_id)
        for key, value in args.model_dump(exclude={'schedule_id'}).items():
            setattr(schedule, key, value)
        db.commit()
        return dict(schedule_id=schedule.id, message='学期与教学周规则已保存。')
    if name == 'update_course_details':
        course = db.scalar(select(Course).where(Course.id == args.course_id, Course.user_id == user_id).with_for_update())
        if course is None:
            raise HTTPException(404, '课程不存在')
        for key, value in args.model_dump(exclude={'course_id'}, exclude_unset=True).items():
            setattr(course, key, value.strip() if isinstance(value, str) else value)
        db.commit()
        return dict(course_id=course.id, message='课程资料已修改，对该课程所有上课时段生效。')
    if name == 'supplement_timetable_image':
        from app.services.timetable_service import timetable_service, TimetableImageError, TimetableParseError, TimetableProviderError
        if args.source == 'attachment':
            from app.services.timetable_attachments import read_image
            content = read_image(user_id, db.info.get('timetable_image_id'))
        else:
            from app.services.mis_timetable import read_snapshot
            snapshot = read_snapshot(user_id)
            if not snapshot:
                raise HTTPException(404, '尚未保存学校课表，请先获取并保存')
            content = base64.b64decode(snapshot['image'])
        try:
            result = asyncio.run(timetable_service(content, db, 'image/png', user_id))
        except (TimetableImageError, TimetableParseError) as exc:
            raise HTTPException(422, str(exc)) from None
        except TimetableProviderError as exc:
            raise HTTPException(502, str(exc)) from None
        if not isinstance(result, dict):
            raise HTTPException(422, '课表识别未完成，请核对已保存安排')
        return dict(count=len(result['timetables']), message='图片识别完成，已补充课表并跳过重复安排。旧的错误安排仍需修改或删除，学期规则需另行设置。')
    raise HTTPException(400, '未知课表操作')


def answer(data):
    if data['tool'] == 'get_mis_timetable':
        snapshot = data.get('snapshot')
        text = data.get('message') or '当前没有正在获取的学校课表。'
        if snapshot:
            text += f" 已保存学期：{snapshot['term']}；共 {len(snapshot['courses'])} 门课程。快照需识别补充后才能用于课表查询。"
        else:
            text += ' 尚未保存学校课表。'
    else:
        text = data.get('message') or f"学校窗口当前状态：{data.get('state', 'unknown')}。请稍后查询获取进度。"
    if data['tool'] == 'save_mis_timetable' and data.get('state') != 'saved':
        text += ' 保存尚未确认完成，请稍后查询学校课表获取进度。'
    return dict(answer=text, sources=[], retrieved_contexts=[], tool_results=[data])
