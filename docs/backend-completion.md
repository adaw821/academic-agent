# 后端功能补齐（2026-10-11）

以下接口均以 `/api` 为前缀，使用现有登录 `session_id` Cookie。没有修改前端。
升级数据库后再启动服务：`agent/Scripts/python.exe -m alembic upgrade head`。
本次版本为 `d91e73b08422`；只新增任务表、文档正文与消息引用字段。

## 历史会话

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/projects/{id}/conversations?limit=20&offset=0` | 会话列表，按最近消息时间降序；返回标题、消息数和时间 |
| GET | `/projects/{id}/conversations/{cid}/messages?limit=100&offset=0` | 按消息 ID 升序分页读取完整历史 |

会话列表 limit 最大 100，消息 limit 最大 500。响应包含 `items/total/limit/offset`。
新会话标题取首问前 100 字。消息包含 `role/content/turn_id/created_at/response_data/selection`。
新回答的 `response_data` 保存引用及召回原文；选区问题展开为正文和 `selection`。
旧消息没有引用元数据时返回 null，不伪造出处。历史摘要不替代消息记录。
客户端取得 `conversation_id` 后继续通过原问答接口或新任务接口追问。

## 文档生命周期

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/projects/{id}/documents/{did}/content` | 读取完整解析 Markdown 正文 |
| GET | `/projects/{id}/documents/{did}/file` | 鉴权后下载原上传文件（PDF 可交给阅读器） |
| POST | `/projects/{id}/documents/{did}/retry` | 失败文档从成功检查点恢复 |
| POST | `/projects/{id}/documents/{did}/retry?restart=true` | 清除检查点，从原文件重新解析；已完成文档需显式指定 |
| DELETE | `/projects/{id}/documents/{did}` | 删除文档、父子分片、文档任务、原文件和图片目录 |

原文件路径必须位于对应项目上传目录，拒绝越界路径。下载使用附件响应与 no-store。
正文是实际解析输出，不是把有重叠的分片拼接后冒充全文；解析工具本身的识别限制仍存在。
升级前的文档没有完整正文时返回 409 `content_requires_reprocess`，可重新处理或下载原文件。
重新处理会先清除旧分片，处理完成前该文档暂不参与检索。

排队/处理中不能删除或再次重试；项目有未完成的持久化问答任务时，也不能删除或重处理文档。
删除先将文件移至上传根目录下的 `.trash`，数据库失败则还原；提交后物理清理。
物理清理暂时失败会返回 `cleanup_pending=true`，恢复扫描继续清理。
如果进程在删除中途退出，扫描会依据数据库中的文档是否存在，恢复文件或完成删除。
已保存的历史回答与引用原文保留，但其中引用的原文档可能已经删除。

## 持久化任务与恢复

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/projects/{id}/ask/jobs` | 与原 `/ask` 相同请求体，202 返回 `job_id/status` |
| GET | `/projects/{id}/jobs?limit=20&offset=0` | 查询项目任务，可找到旧文档对应任务 |
| GET | `/projects/{id}/jobs/{job_id}` | 状态、最后成功节点、尝试次数、错误类型和最终结果 |
| POST | `/projects/{id}/jobs/{job_id}/retry` | 重试失败问答；可加 `?restart=true` 重新读取历史并从头执行；文档使用文档重试接口 |

上传响应 `saved_files` 新增 `job_id`；旧字段不变。任务状态为 queued/running/completed/failed。
问答成功的 `result` 与原问答响应一致，含 `conversation_id/answer/sources/retrieved_contexts`。
原同步 `/ask` 保留；需要重启恢复保障的调用应使用 `/ask/jobs`。前端尚未切换到该接口。

两张 LangGraph 通过应用层节点包装器保存数据库检查点，不依赖内存或 pickle。
Docling 检查点使用公开 JSON 导出接口并嵌入图片像素；恢复时还原文档对象。
仅缓存成功节点，权限校验始终重新执行；最终分片/消息写入不缓存。
最终写入与任务完成状态同事务提交，旧 worker 的租约令牌失效后不能提交。
摘要更新在持久化问答中也随最终事务提交，避免失败后留下不可恢复的摘要边界。

启动后持续扫描排队任务和租约过期任务，每批最多 10 个，批次间隔 15 秒。
租约 120 秒、心跳 20 秒；多个服务进程通过条件更新争抢任务，避免重复领取。
升级前遗留的 queued/processing 文档会自动纳入任务表。
异常失败不会无限自动重试，修复原因后调用重试接口。成功任务释放检查点以减少存储。
如果中断发生在外部模型返回后、检查点保存前，该节点可能再次调用模型；数据库最终写入不会重复。
图片增强作为一个节点保存检查点，中途失败可能重新处理该节点内尚未持久化的图片。
不兼容的解析代码升级后应使用 `restart=true` 丢弃旧文档检查点。

## 学校连接、课程和课表

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| PUT | `/school/session` | `{"session":"MoodleSession 的值"}`；验证学校访问成功后保存当前用户凭据 |
| GET | `/school/session` | 只返回是否已配置；不返回会话值，也不宣称仍然有效 |
| DELETE | `/school/session` | 删除当前用户的学校凭据，不删除已同步数据 |
| GET | `/school/courses` | 从学校页面发现课程，保留 complete=false 和动态加载限制提示 |
| POST | `/courses/sync` | `{"source_url":"学校课程链接"}`；首次导入或更新该课程及作业资源 |
| GET | `/courses?limit=20&offset=0` | 当前用户本地课程列表 |
| GET | `/courses/{id}/resources?limit=20&offset=0` | 已采集资源元数据、内容与学校链接，不暴露本地文件路径 |
| GET | `/timetable?course_id=1` | 查询已保存课表；course_id 可省略，按周一至周日及开始时间排列 |

已有 `/courses/{id}/sync` 和 `/assignments` 继续可用。
所有学校凭据操作绑定当前登录用户，原有 `.local/ispace-sessions/{user_id}.json` 与
`ISPACE_SESSION_DIR` 仍兼容。写入使用原子替换；不接受其他用户 ID，不在响应中回显凭据。
这是会话连接 API，学校的交互式登录仍由用户完成，不采集学校密码或绕过学校认证。
课表与作业查询读取上次入库的数据。资源附件下载、Folder 展开和动态课程分页仍不在本次范围。

## 对话同步单门课程（2026-10-26）

> 2026-10-28 已扩展名称未匹配时的学校发现与首次导入；下节保留原版本记录，当前行为见 [AI 首次导入](school-first-import.md)。

`POST /api/projects/{id}/ask` 和持久化 `/ask/jobs` 支持同一请求体：

```json
{
  "query": "请同步 Database Systems 课程",
  "use_school_tools": true,
  "allow_school_sync": true
}
```

`allow_school_sync` 默认关闭；仅打开 `use_school_tools` 仍只有原有四类只读工具。
两个开关都开启时，模型可选择 `sync_school_course`，一次同步当前登录用户已关联的一门课程。
课程按本地 ID 或名称匹配；名称优先精确匹配，再作字面包含匹配。未指定、未找到或有歧义时返回
`tool_results[0].status=clarification_required`，不访问学校。模型不能指定用户 ID、学校 URL 或批量课程。
指定 `doc_ids` 或文档选区时沿用文档问答，不执行同步。

工具复用 `/courses/{id}/sync` 的学校会话、来源链接检查、采集后所有权复查及事务写入。
成功返回 `status=completed`、本次作业/资源数量、完成时间和采集警告；回答由这些结果直接生成，
不再额外调用回答模型。这里只刷新课程元数据，不下载附件、导入文档或提交作业。会话失效仍返回
409 `school_login_required`，学校访问失败返回 502；不得把失败响应理解为同步完成。

持久化问答保存同步结果检查点；后续消息保存失败时，普通重试复用结果。
同步事务与检查点不是一个事务：若进程在同步提交后、检查点保存前退出，恢复可能再次采集；
现有同步按来源标识更新，不重复创建同一作业/资源。不宣称同步恰好执行一次。
同步一旦提交，即使本轮问答后来失败，课程数据仍已更新；`restart=true` 会重新执行同步。
本次未接入前端。新增验证脚本为 `tests/test_school_sync_qa.py`，可使用下述 PostgreSQL 隔离方式运行。

## 验证

`agent/Scripts/python.exe tests/test_backend_completion.py` 在临时 SQLite 中验证真实 Cookie 鉴权、
历史引用、文件边界与删除、学校会话隔离、课表查询、问答/文档断点恢复和提交幂等性。
外部学校与模型调用使用模拟响应，Docling 的 JSON 与像素恢复使用真实库。

设置 `BACKEND_TEST_POSTGRES=1` 后运行同一命令，会使用 `DATABASE_URL` 中的 PostgreSQL，
为每个测试建立唯一临时 schema，并只清理该 schema，不写应用现有表。
