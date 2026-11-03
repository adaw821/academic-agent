"""User-owned manual corrections to the timetable shared with image imports and QA."""
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.courses import Course
from app.models.course_timetable import CourseSchedule



class ScheduleFields(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    day_of_week: Literal['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    start_time: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end_time: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    location: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def ordered_times(self):
        if self.end_time <= self.start_time:
            raise ValueError('结束时间必须晚于开始时间')
        return self


class NewSchedule(ScheduleFields):
    course_name: str = Field(min_length=1, max_length=500)
    course_code: str | None = Field(default=None, max_length=20)
    teachers: str | None = Field(default=None, max_length=100)


def owned_schedule(db, user_id, schedule_id):
    schedule = db.scalar(select(CourseSchedule).join(Course).where(
        CourseSchedule.id == schedule_id, Course.user_id == user_id).with_for_update())
    if schedule is None:
        raise HTTPException(404, '课表记录不存在')
    return schedule


def create_entry(request: NewSchedule, db: Session, user_id: int):
    # Reuse the current user's course; never alter shared course metadata here.
    code = request.course_code or None
    query = select(Course).where(Course.user_id == user_id)
    if code:
        query = query.where(Course.course_code == code)
    else:
        query = query.where(Course.course_name == request.course_name)
    course = db.scalar(query.order_by(Course.id))
    if course is None:
        course = Course(user_id=user_id, course_name=request.course_name,
                        course_code=code, teachers=request.teachers or None)
        db.add(course)
        db.flush()
    fields = request.model_dump(include=set(ScheduleFields.model_fields))
    duplicate = db.scalar(select(CourseSchedule).where(CourseSchedule.course_id == course.id,
        *(getattr(CourseSchedule, key) == value for key, value in fields.items())))
    if duplicate:
        raise HTTPException(409, '已有相同的上课安排，无需重复添加')
    schedule = CourseSchedule(course_id=course.id, **fields)
    db.add(schedule)
    db.commit()
    return {'id': schedule.id, 'course_id': course.id}


def update_entry(schedule_id: int, request: ScheduleFields,
                 db: Session, user_id: int):
    schedule = owned_schedule(db, user_id, schedule_id)
    for key, value in request.model_dump().items():
        setattr(schedule, key, value)
    db.commit()
    return {'id': schedule.id}


def delete_entry(schedule_id: int, db: Session, user_id: int):
    schedule = owned_schedule(db, user_id, schedule_id)
    db.delete(schedule)
    db.commit()
    # Keep the course and its assignments/resources; delete only this time slot.
    return {'deleted': True}
