from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class AssignmentItem(BaseModel):
    id: int
    course_id: int
    course_name: str
    external_activity_id: str
    title: str
    description: str | None
    source_url: str
    opens_at: datetime | None
    due_at: datetime | None
    source_timezone: str | None
    due_at_raw: str | None
    status: Literal['submitted', 'pending', 'unknown']
    source_status: Literal["available", "not_seen", "unknown"]
    submission_status: str | None
    grading_status: str | None
    last_synced_at: datetime


class AssignmentList(BaseModel):
    items: list[AssignmentItem]
    total: int
    limit: int
    offset: int
    timezone: str
