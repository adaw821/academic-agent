# 作业查询接口

`GET /api/assignments`，使用项目现有登录接口设置的 `session_id` Cookie。此接口只查本地数据库，不访问学校、不调用模型、不刷新数据。

| 参数 | 含义 |
| --- | --- |
| course_id | 可选，本地课程 ID，正整数；不是学校的 11405 |
| due_from | 可选，YYYY-MM-DD，北京时间起始日，包含当天 |
| due_to | 可选，YYYY-MM-DD，北京时间截止日，包含当天 |
| status | 可选，submitted / pending / unknown |
| limit | 每页数量，默认 20，范围 1–100 |
| offset | 跳过数量，默认 0，非负整数 |

示例：查询本地课程 1 未提交的作业：

```http
GET /api/assignments?course_id=1&status=pending
```

示例：查询北京时间 2026-10-12 至 2026-10-18 的截止作业：

```http
GET /api/assignments?due_from=2026-10-12&due_to=2026-10-18
```

响应包含 `items`、筛选后分页前的 `total`、`limit`、`offset`、`timezone: Asia/Shanghai`。每项包含作业 ID、课程 ID/名称、题目、DDL、原始提交状态、标准化 status、来源链接和最后同步时间，时间以 +08:00 输出。

- 默认按 DDL 升序排列，相同 DDL 按作业 ID 排列，缺少 DDL 的排最后。
- 指定日期范围时不返回 DDL 为空的记录；due_to 通过次日零点之前实现全天包含。
- pending 包括明确未提交及未提交草稿；未识别或缺失的提交状态为 unknown，不把 Done 当作提交状态。
- 所有查询绑定当前登录用户，不接受 user_id 指定归属。查询他人或不存在的课程返回空列表，避免泄露课程存在性。
- 未登录、无效或过期会话返回 401；日期区间颠倒、非法状态和分页参数返回 422。
- 数据是上次同步副本；接口未判断资源是否已从学校移除，不代表实时完整状态。

验证：`python tests/test_assignment_queries.py`，使用隔离 SQLite、真实 Cookie 身份校验和 ASGI HTTP 请求，不写开发数据库。
