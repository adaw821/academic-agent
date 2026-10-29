"""Read-only AI view of the existing reminder inbox, with bounded output."""
from pydantic import BaseModel, ConfigDict, Field
from app.services.deadline_reminders import list_deadline_reminders

TOOL_NAME = 'query_deadline_reminders'


class ReminderQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    active_only: bool = Field(True, strict=True, description='默认仅查询仍有效的提醒；查历史提醒时设为 false')
    unread_only: bool = Field(False, strict=True, description='仅在用户要求未读提醒时设为 true；已读不代表已提交')
    limit: int = Field(20, strict=True, ge=1, le=20)
    offset: int = Field(0, strict=True, ge=0, le=10000)


def query_deadline_reminders(db, user_id, raw_args):
    args = ReminderQuery.model_validate(raw_args)
    result = list_deadline_reminders(db, user_id, **args.model_dump())
    items = []
    for row in result['items']:
        item = {key: row[key] for key in ('id', 'assignment_id', 'course_id', 'active')}
        item.update(title=row['title'][:200], course_name=row['course_name'][:200])
        for key in ('due_at', 'current_due_at', 'created_at', 'read_at', 'last_synced_at'):
            item[key] = row[key].isoformat() if row[key] is not None else None
        items.append(item)
    return dict(status='ok', items=items, total=result['total'], unread_count=result['unread_count'],
                limit=args.limit, offset=args.offset, has_more=args.offset + len(items) < result['total'],
                timezone=result['timezone'], warning=result['warning'],
                coverage_warning='仅查询已生成的站内提醒。未启用生成任务、未到提醒窗口或数据过旧都可能导致空列表，不能据此判断没有待交作业。')
