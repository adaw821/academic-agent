# 学校课程、作业与资源表

- `courses` 复用现有课程表，通过 `user_id` 归属用户。新增 `source_url`、`external_course_id`、`last_synced_at`；原有课表记录可为空。
- `assignments` 每项作业一行，`course_id` 是本地课程主键；`external_activity_id` 才是学校链接中的活动 ID。DDL 存 `due_at`，同时保留 `opens_at`、题目描述、提交状态、评分状态、来源和同步时间。
- `course_resources` 存课件、题目、模板、页面等资源的元数据，文件本体存本地或对象存储，路径写入 `storage_path`。`assignment_id` 可空；同一资源如供多项作业使用，先作为课程级资源保存，不强行绑定单项作业。

## 同步约定

1. 所有查询先按 `courses.user_id` 限定当前用户；外键不代替 API 授权检查。
2. 按用户与规范化课程来源 URL 匹配课程；作业按课程与学校活动 ID 更新；资源按课程与稳定 `source_key` 更新，不重复插入。
3. `source_key` 使用稳定活动/文件标识；没有标识时取规范化来源 URL 的 SHA-256。来源 URL 不包含登录令牌。
4. 时间使用带时区值写入 PostgreSQL。确认学校账户时区后再解析；未确认时区的截止日期仅保留 `due_at_raw`，`due_at` 留空，不能猜时区。
5. `submission_status` 来自详情页，不用课程页面的 Done 推断是否提交。未知状态留空。
6. `purpose` 可用 `lecture`、`assignment_prompt`、`template`、`student_submission`、`unknown`；不要把学生提交文件当成老师题目。
7. 本次仅完成表结构和迁移，尚未实现同步接口，也未写入学校账号的真实数据。

## 建表

使用已配置 DATABASE_URL 的项目环境执行 `python -m alembic upgrade head`。
本次迁移版本为 `f72a913c604b`，前置版本为 `e63f2b908a14`。
验证：`python tests/test_school_schema.py`（隔离 SQLite，测试迁移、去重、跨课程关联限制及回滚）。
