# 课程刷新接口

`POST /api/courses/{course_id}/sync`，使用项目登录的 `session_id` Cookie；无需请求体。

例如用户 1 的数据库课：`POST /api/courses/1/sync`。这里是本地课程 ID 1，不是学校课程 ID 11405。

服务端校验课程归属，从该用户的学校会话文件读取 MoodleSession，然后采集学校课程、作业详情及一层资源页面，成功后统一入库。重复刷新更新已有记录，不重复插入；不会向学校提交或修改作业。

## 学校会话配置

默认文件：`.local/ispace-sessions/{本地用户ID}.json`。用户 1 已从此前会话文件初始化；以后过期请更新 `.local/ispace-sessions/1.json`。接口不会再读取旧的 `.local/ispace-session.json`，其他用户也不会回退使用用户 1 的 Cookie。

文件格式：`{"MoodleSession":"自己的值"}`。可以设置服务端环境变量 `ISPACE_SESSION_DIR` 更换存放目录；所有会话文件应保留在 Git 外，仅本地服务可访问。现在也可通过 `PUT /api/school/session` 验证并保存当前用户的学校会话，支持查询配置状态和解除连接；首次课程导入可用 `POST /api/courses/sync`。见 [后端补齐说明](backend-completion.md)。前端尚未接入这些接口。

## 响应

成功返回 `course_id`、`assignments`、`resources`、`warnings`、`completed_at`。数量表示本次获取条目数，不是新增条目数。完成时间带 UTC 时区；作业查询接口仍返回北京时间。

- 401：项目未登录或登录过期。
- 404：课程不存在或属于其他用户。
- 409：课程未关联来源、学校会话缺失/失效/无权访问，或同步期间课程发生变化。学校会话问题返回 `detail.code=school_login_required`。
- 502：学校访问/解析失败；原数据不更新。
- 500：入库失败，事务回滚。

目前同步请求等待采集完成再返回，不是后台任务。未下载附件，不删除本次未出现的旧记录。刷新后调用 `GET /api/assignments?course_id=1` 查看结果。

验证：`python tests/test_course_sync.py`；使用真实项目 Cookie 校验、隔离 SQLite 和模拟学校响应。
