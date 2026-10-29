"""Explicitly enabled school mutations; identity and destination come from the request."""
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class ResourceAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    course_id: int = Field(strict=True, gt=0)
    resource_id: int = Field(strict=True, gt=0)


class ReadReminderAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reminder_id: int = Field(strict=True, gt=0)


ACTION_SCHEMAS = {
    'download_school_resource': (ResourceAction, '用户明确要求下载一个已查询到 ID 的教学附件时调用，下载至服务器缓存。'),
    'import_school_resource': (ResourceAction, '用户明确要求将一个已下载附件导入当前项目知识库时调用，返回异步处理任务。'),
    'mark_school_reminder_read': (ReadReminderAction, '用户明确要求将指定提醒标记已读时调用，不改变作业提交状态。'),
}


def execute_action(db, user_id, project_id, plan):
    name = plan['name']
    args = ACTION_SCHEMAS[name][0].model_validate(plan['args'])
    if name == 'download_school_resource':
        from app.services.school_downloads import download_resource
        result = download_resource(db, user_id, args.course_id, args.resource_id)
    elif name == 'import_school_resource':
        from app.services.school_imports import import_resource
        if project_id is None:
            raise HTTPException(409, '请先选择知识库项目')
        result = import_resource(db, user_id, args.course_id, args.resource_id, project_id)
    else:
        from app.services.deadline_reminders import mark_reminder_read
        if mark_reminder_read(db, user_id, args.reminder_id) is None:
            raise HTTPException(404, '提醒不存在')
        db.commit()
        result = dict(reminder_id=args.reminder_id, read=True)
    return dict(result, tool=name)


def answer_action(data):
    if data['tool'] == 'import_school_resource':
        text = f"附件导入任务状态：{data['status']}；文档 ID：{data['document_id']}，任务 ID：{data['job_id']}。只有 completed 表示知识库处理完成。"
    elif data['tool'] == 'download_school_resource':
        text = '附件已下载至服务器缓存，可继续导入当前项目知识库。'
    else:
        text = '已将该提醒标记为已读；作业提交状态未改变。'
    return dict(answer=text, sources=[], retrieved_contexts=[], tool_results=[data])
