# 学校操作工具、教学周与作业可见状态

## 启用

先执行 `agent/Scripts/python.exe -m alembic upgrade head`，新迁移为
`a82c431d09be`。后续前端接入已完成；2026-10-26 已在当前本机数据库执行该迁移，核对 11 张业务表记录数未变。前端入口与使用方式见 `frontend/README.md`。

同步问答与 `/ask/jobs` 请求均可增加：

```json
{
  "query": "请下载课程 1 的附件 5",
  "use_school_tools": true,
  "allow_school_actions": true
}
```

`allow_school_actions` 默认关闭，与 `allow_school_sync` 独立。
开放 `download_school_resource`、`import_school_resource`、
`mark_school_reminder_read` 三项操作。每轮最多一个工具调用；目标不明确时先查询附件或提醒，
再在下一轮明确目标。下载与导入参数为本地 `course_id/resource_id`，已读参数为 `reminder_id`。
用户身份来自登录态，导入目标固定为当前问答项目，模型不能指定其他用户或项目。

下载复用服务器附件缓存；导入要求附件已下载，复用已有导入任务。
新导入返回 `queued` 与 `job_id/document_id`，启动时的持久化恢复 worker 会拾取处理；
仅 `completed` 表示知识库处理完成。已读操作可重复执行，不改变作业提交状态。
操作结果直接生成回答，避免模型把排队误报为成功。

持久化问答保存操作结果检查点。操作提交后、检查点保存前退出仍可能再次执行；
缓存复用、导入复用与已读幂等负责处理重放，不承诺恰好执行一次。

## 学期与单双周

`PUT /api/timetable/{schedule_id}/calendar` 完整替换该课表记录的校历配置：

```json
{
  "term_name": "2026 秋季",
  "term_start": "2026-09-01",
  "term_end": "2026-12-31",
  "start_week": 2,
  "end_week": 16,
  "week_pattern": "even"
}
```

`week_pattern` 为 `all/odd/even`。学期起止日期须同时填写；第 1 周是包含学期开始日的
周一至周日，此后周一递增。日期早于学期开始或晚于学期结束时不匹配。
教学周范围含首尾，单双周以教学周编号计算。提交 `{}` 可清除校历，恢复每周模式。
只能修改当前用户课程的课表记录。现有图片课表导入保持兼容，校历需通过此接口明确配置。

`GET /api/timetable?on_date=2026-09-07&limit=20&offset=0` 和 AI 课表工具均使用相同规则；
先筛选再分页，返回 `teaching_week/calendar_configured` 与校历字段。
不传日期的原课表接口仍返回全部每周安排，并附带校历字段。
旧记录未配置校历时仍按星期匹配，不把缺失配置解释成当天无课。
临时调课、节假日补课尚不自动推断。

## 作业可见状态

成功同步同一课程时，本次出现的作业设为 `available`，未出现的已有作业设为 `not_seen`；
旧数据在首次新同步前为 `unknown`。失败采集或事务失败不修改状态。
再次出现会恢复 `available`，沿用原作业 ID、附件关联和提醒历史。

`GET /api/assignments` 和 AI 作业查询均返回 `source_status`，支持同名过滤参数。
该字段与提交状态 `status` 独立。`not_seen` 可能源于隐藏、下架或可见范围变化，
不是学校已删除的确证；保留历史记录与最后实际采集时间，不硬删除。
未发现的作业不再生成有效截止提醒，历史提醒仍可查询。

按用户本轮选择，仅保留站内提醒，未接入外部推送，也没有新增外部发送配置。

## 验证

`tests/test_school_actions_calendar.py` 覆盖操作开关、身份/目标限制、已读幂等与跨用户隔离、
校历日期边界、课表修改权限和持久化重试检查点。
`tests/test_course_sync.py` 增加作业消失后保留、重现恢复测试。
学校与模型调用使用模拟响应；未进行真实学校访问或模型联调。
