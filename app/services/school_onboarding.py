"""Credential lifecycle and resumable-by-retry initial school import."""
import re
from sqlalchemy import select
from app.db.database import SessionLocal
from app.models.users import User
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.services.school_crypto import seal, unseal
from app.services.school_auth import authenticate, fetch_timetable, SchoolAuthenticationError
from app.services.ispace import ISpaceClient, SchoolLoginRequired
from app.services.school_scheduler import user_sync_lock, SyncAlreadyRunning, owns_sync_lock


def update_state(user_id, state, message):
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user and user.school_credentials:
            user.school_sync_state, user.school_sync_message = state, message
            db.commit()


def read_credentials(user_id):
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.school_credentials:
            raise SchoolLoginRequired('请重新连接学校账号')
        if user.school_sync_state == 'authentication_required':
            raise SchoolLoginRequired('学校认证失败，请更新学校账密后重试')
        return unseal(user_id, user.school_credentials)


def persist_credentials(user_id, data):
    from app.services.school_sessions import save_school_session
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not user or not user.school_credentials:
            raise SchoolLoginRequired('学校连接已删除')
        user.school_credentials = seal(user_id, data)
        db.commit()
    save_school_session(user_id, data['cookie'], encrypted=True)


def ensure_session_locked(user_id):
    data = read_credentials(user_id)
    try:
        with ISpaceClient(data['cookie']) as client:
            client.discover_courses()
    except SchoolLoginRequired:
        try:
            data = authenticate(data['account'], data['password'])
        except SchoolAuthenticationError:
            update_state(user_id, 'authentication_required', '学校自动认证未完成，请更新账密；验证码或二次验证需另行处理。')
            raise SchoolLoginRequired('学校自动认证未完成，请更新学校账密') from None
        persist_credentials(user_id, data)
    return data


def ensure_session(user_id):
    if owns_sync_lock(user_id):
        return ensure_session_locked(user_id)['cookie']
    try:
        with user_sync_lock(user_id):
            return ensure_session_locked(user_id)['cookie']
    except SyncAlreadyRunning:
        raise SchoolLoginRequired('学校同步正在进行，请稍后重试') from None


def import_mis(db, user_id, snapshot):
    """Validate the complete snapshot before writing; repeated imports are additive/idempotent."""
    rows = []
    for item in snapshot['courses']:
        times, rooms = item['times'], item['rooms']
        if rooms and len(rooms) not in {1, len(times)}:
            raise ValueError('课程时间与教室无法对应，未导入课表')
        slots = []
        for index, value in enumerate(times):
            match = re.fullmatch(r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})', value.strip())
            if not match:
                raise ValueError('存在无法确定的上课时间，未导入课表')
            day, start, end = match.groups()
            normalized = []
            for raw in (start, end):
                hour, minute = map(int, raw.split(':'))
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    raise ValueError('上课时间无效')
                normalized.append(f'{hour:02}:{minute:02}')
            if normalized[0] >= normalized[1]:
                raise ValueError('上课时间范围无效')
            slots.append((day, *normalized, rooms[min(index, len(rooms)-1)] if rooms else ''))
        if len(item['course_code']) > 20 or len(item['teachers']) > 100:
            raise ValueError('课程明细超出字段限制')
        rows.append((item, slots))
    count = 0
    for item, slots in rows:
        course = db.scalar(select(Course).where(Course.user_id == user_id, Course.course_code == item['course_code'])) if item['course_code'] else None
        if course is None:
            matches = db.scalars(select(Course).where(Course.user_id == user_id, Course.course_name == item['course_name'])).all()
            course = matches[0] if len(matches) == 1 else None
        if course is None:
            course = Course(user_id=user_id, course_name=item['course_name'], course_code=item['course_code'], teachers=item['teachers'])
            db.add(course)
            db.flush()
        for day, start, end, room in slots:
            existing = db.scalar(select(CourseSchedule.id).where(
                CourseSchedule.course_id == course.id, CourseSchedule.term_name == snapshot['term'],
                CourseSchedule.day_of_week == day, CourseSchedule.start_time == start,
                CourseSchedule.end_time == end, CourseSchedule.location == room))
            if existing is None:
                db.add(CourseSchedule(course_id=course.id, day_of_week=day, start_time=start,
                                      end_time=end, location=room, term_name=snapshot['term']))
                db.flush()
                count += 1
    db.commit()
    return count


def sync_school(user_id):
    """Background arguments contain only local IDs, never credentials or browser state."""
    from app.services.school_sync import sync_course
    from app.services.mis_timetable import save_snapshot
    try:
        with user_sync_lock(user_id):
            data = ensure_session_locked(user_id)
            update_state(user_id, 'running', '正在同步学校课程和课表…')
            persist_credentials(user_id, data)
            failures = 0
            with ISpaceClient(data['cookie']) as client:
                found = client.discover_courses(current_only=True)
                for item in found['courses']:
                    try:
                        snapshot = client.collect_course(item['source_url'])
                        with SessionLocal() as db:
                            sync_course(db, user_id, snapshot)
                    except SchoolLoginRequired:
                        raise
                    except Exception:
                        failures += 1
            try:
                try:
                    snapshot, state = fetch_timetable(data['storage_state'])
                except SchoolLoginRequired:
                    try:
                        data = authenticate(data['account'], data['password'])
                    except SchoolAuthenticationError:
                        raise SchoolLoginRequired('MIS 自动认证未完成') from None
                    persist_credentials(user_id, data)
                    snapshot, state = fetch_timetable(data['storage_state'])
                with SessionLocal() as db:
                    import_mis(db, user_id, snapshot)
                save_snapshot(user_id, snapshot)
                data['storage_state'] = state
                persist_credentials(user_id, data)
            except SchoolLoginRequired:
                raise
            except Exception:
                failures += 1
            partial = failures or not found['complete']
            update_state(user_id, 'partial' if partial else 'completed',
                         '部分数据同步未完成，可重试；已导入数据保留。' if partial else '课程、作业和课表已同步；教学日期规则请核对。')
    except SyncAlreadyRunning:
        return
    except SchoolLoginRequired:
        update_state(user_id, 'authentication_required', '学校认证需要更新，请重新提交学校账密。')
    except Exception:
        update_state(user_id, 'failed', '学校同步未完成，可重试；已导入数据保留。')
