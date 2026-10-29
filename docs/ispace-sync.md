# iSpace 课程采集工具

## 已实现

- 固定访问 `https://ispace.bnbu.edu.cn`，使用用户提供的 MoodleSession。页面与文件使用 GET；动态课程使用固定只读查询方法的 Ajax POST。
- 指定课程链接，解析课程标题、学校活动 ID、作业名称、Opened/Due。
- 时间按用户确认的北京时间（Asia/Shanghai，UTC+8）解析，保存带时区时间。
- 逐项获取作业详情中的提交/评分状态；不把 Done 当作提交状态。
- 收集 File/Page/Folder 与 pluginfile 资源入口，展开一层 Page（例如 Lectures、Lab），读取 Folder 文件树及其子目录附件。过滤学生提交文件。
- 完整采集成功后再开启入库事务；按用户、课程、学校活动 ID 更新，重复同步不会新增重复作业，失败则回滚。
- 新课程独立建立学校来源记录；不会按名称猜测并合并已有课表课程。

## 首次运行

在项目根目录执行。应用数据库需已迁移到最新版本。

1. 在自己的浏览器正常登录 iSpace。
2. 开发者工具 → Application/应用 → Cookies → 学校域名，找到 `MoodleSession`。
3. 在项目根目录创建 `.local/ispace-session.json`，写入：

```json
{"MoodleSession": "在本地填入自己的值"}
```

`.local/` 已加入 Git 忽略。会话值等同登录凭据，不发到聊天或提交 Git。工具只在请求期间使用，不入库；过期时更新该文件。此工具不自动获取内置浏览器 Cookie，也不自动登录。

先预览，不写数据库：

```powershell
.\agent\Scripts\python.exe scripts/sync_ispace.py --session-file .local/ispace-session.json --course-url 'https://ispace.bnbu.edu.cn/course/view.php?id=11405'
```

确认结果后入库（将 `123` 换成你的项目本地用户 ID，并非学校学号）：

```powershell
.\agent\Scripts\python.exe scripts/sync_ispace.py --session-file .local/ispace-session.json --course-url 'https://ispace.bnbu.edu.cn/course/view.php?id=11405' --sync --user-id 123
```

发现课程目录：

```powershell
.\agent\Scripts\python.exe scripts/sync_ispace.py --session-file .local/ispace-session.json --list-courses
```

目录优先使用 Moodle 动态课程接口并跟随 `nextoffset` 分页。完整遍历后返回 `complete: true`；
无法使用动态接口或达到限制时返回已发现的课程及 `complete: false`，具体边界见第四步。

## Agent 后续接入

`ISpaceClient.collect_course(url)` 返回 `CourseSnapshot`；`sync_course(db, authenticated_user_id, snapshot)` 持久化并返回数量。同步调用应在服务端绑定已认证用户，不让模型或普通 HTTP 请求任意指定用户 ID。当前 CLI 的 `--user-id` 是本地开发入口，不是公开接口。

## 边界

- 课程同步收集可见资源链接、一层 Page 文本和 Folder 文件树附件；单个附件可通过下述接口下载，再导入项目进行解析问答。尚未同步公告或教师资料；课程发现完整性以返回的 `complete` 为准。
- 对未知日期格式、登录过期、无权访问、网络错误明确失败，不拿错误页覆盖数据。
- 未出现在最近一次成功采集中的资源保留，资源列表以 `source_status=not_seen` 标记；这不是已删除的证明。作业旧记录仍保留，尚未加入同类状态，不能把历史记录当作学校当前完整清单。
- CLI 不存登录凭据；资源按课程归档，作业说明中明确引用的唯一附件可自动关联，见第六步。不根据文件名猜测课件与作业关系。
- 新增课程标题长度迁移为 500，适配学校包含学期和教师姓名的长标题。

## 验证

`python tests/test_ispace.py`：页面字段、北京时间零点/中午、登录失败、跨站重定向、资源提取、重复同步、用户隔离、DDL 更新和事务回滚。
真实课程页面的结构已通过浏览器查看；HTTP 采集测试使用模拟响应，尚需用本地会话文件完成真实同步验收。

## 单个附件下载（第一步）

使用本地登录 `session_id` Cookie，先同步课程取得本地课程 ID 和资源 ID：

- `POST /api/courses/{course_id}/resources/{resource_id}/download`：将教学附件下载到服务器。
- `GET /api/courses/{course_id}/resources/{resource_id}/download`：查询本地附件是否存在，不访问学校。

POST 成功返回 `resource_id/downloaded/cached/size_bytes/content_type`；GET 没有 `cached` 字段，
尚未下载或本地文件丢失时 `downloaded=false`、大小和类型为 null。响应不暴露文件路径或学校凭据。
文件保存在已忽略的 `data/uploads/school/{user_id}/{course_id}/{resource_id}/`，数据库复用
`course_resources.storage_path/content_type`，无需新增迁移，不修改最后同步时间。

支持直接 `pluginfile.php` 文件链接、Moodle File 的同站重定向及唯一嵌入文件链接。
目前只接受 PDF、DOCX、PPTX、TXT，单文件最多 50 MiB，采用流式下载。
未登录返回 401；他人的或不存在的资源返回 404；Page/Folder/学生提交资源返回 422；
学校会话失效或无权限返回 409；学校请求失败、附件超限或格式不支持返回 502。
跨站跳转、学生提交文件路径和 HTML 错误页会被拒绝。

已有本地附件时直接复用；文件丢失可重新下载。当前没有强制刷新版本的参数。
正常下载失败或数据库提交失败会清理本次文件；进程被强制终止时可能留下未关联的临时文件，
本轮未加入孤儿文件扫描。下载成功仅表示已保存，不代表格式可被解析或已加入问答知识库。
学校原文件与本地缓存的更新检测留给后续实现；解析入库使用下面的独立接口。

`agent/Scripts/python.exe tests/test_school_downloads.py` 使用隔离数据库、真实 Cookie 鉴权和
模拟学校 HTTP 验证下载、复用、文件丢失重下、访问隔离及失败清理；尚未用真实学校会话验收附件下载。

## 将已下载附件导入项目知识库（第二步）

`POST /api/courses/{course_id}/resources/{resource_id}/import`

请求体：`{"project_id": 1}`。使用当前登录 Cookie，课程资源和目标项目必须都属于当前用户。
该接口只消费已下载附件，不访问学校；没有本地附件时返回 409，先调用下载接口。

首次导入返回 202，例如：

```json
{"document_id": 12, "job_id": "任务ID", "status": "queued", "reused": false}
```

服务将附件复制到项目上传目录，创建普通 Document 与持久化文档任务，再启动现有后台处理：
TXT 解码，PDF/DOCX/PPTX 使用现有 Docling 流程，后续父子分片、图片处理及向量写入均复用原实现。
引用文件名取学校资源标题并补齐实际扩展名；来源 URL、课程 ID、资源 ID 保存在任务 request 中。
不新增数据库迁移。202 只表示入队，`status=completed` 后才会被现有项目问答检索。

- 文档状态：`GET /api/projects/{project_id}/documents/{document_id}`。
- 任务状态与错误：`GET /api/projects/{project_id}/jobs/{job_id}`。
- 失败重试：`POST /api/projects/{project_id}/documents/{document_id}/retry`。
- 提问：`POST /api/projects/{project_id}/ask`，可传 `doc_ids: [document_id]` 限定文档。

相同资源导入同一项目返回 200、`reused=true`，复用文档与任务，并返回当前文档状态；
失败任务不会因重复导入而自动重试，使用上述重试接口。PostgreSQL 项目行锁串行化重复导入。
同一资源可以导入多个自有项目。项目文档是独立副本，删除不会影响学校缓存，删除后可以重新导入。
学校资源后续变化不会自动刷新项目副本。异常回滚会清理本次复制文件；提交结果无法确认时保留文件，
进程强制退出仍可能留下孤儿文件，后续清理扫描尚未实现。

`agent/Scripts/python.exe tests/test_school_imports.py` 使用临时 SQLite 验证权限、缺失附件、重复导入、
独立副本、回滚清理、真实 TXT 分片/正文入库与 BM25 召回，以及失败后的现有重试流程。
测试只模拟向量模型输出；不代表真实模型问答质量验收，也未在此测试中执行 PDF/Office 转换。

## Folder 资源展开（第三步）

现有课程同步接口自动展开从课程页、作业介绍或一层 Page 中发现的 Folder，无需新接口或迁移。
读取 Folder 正文的 `.foldertree`，提取包含折叠子目录在内的附件链接；文件树中的其他 Folder
活动链接也会继续展开，每个活动只访问一次，避免循环。结构依据
[Moodle 官方 Folder 模板](https://raw.githubusercontent.com/moodle/moodle/MOODLE_405_STABLE/mod/folder/templates/folder.mustache)。

附件以 `resource_type=file` 入库，出现在原课程资源列表中，可以直接调用前两步的下载和导入接口。
相同规范化 URL 去重，保留已有资源的稳定 ID；文件夹记录仍是 `folder`，不能直接作为文件下载。
不访问编辑、整包下载、外站和学生提交链接，不下载全部附件，不将页面导航链接当作文件树内容。

单次最多发现 100 个 Folder 活动、2000 个不同的文件树资源。超限、登录失效、请求失败或缺少
文件树结构时整个采集失败，现有同步接口不会用部分结果覆盖数据库。识别到的空文件树可以正常同步。
依赖 JavaScript 额外请求才出现的文件不在本轮支持范围；学校主题若改变文件树结构需适配。

`agent/Scripts/python.exe tests/test_ispace.py` 新增 6 项模拟 HTTP/HTML 测试，覆盖子目录、重复链接、
Page 内 Folder、循环、入库去重、数量上限、空目录及失败处理。未使用真实学校会话验收 Folder 页面。

## 动态课程发现与分页（第四步）

沿用 `GET /api/school/courses` 和 CLI 的 `--list-courses`，无需新接口或迁移。
读取 `/my/courses.php` 的 `M.cfg` JSON 中的 `sesskey`，不执行页面 JavaScript；随后只向学校固定
`/lib/ajax/service.php` 调用 `core_course_get_enrolled_courses_by_timeline_classification` 查询方法。
实现依据 [Moodle 官方课程 API](https://raw.githubusercontent.com/moodle/moodle/MOODLE_405_STABLE/course/externallib.php)
及 [Ajax 协议](https://raw.githubusercontent.com/moodle/moodle/MOODLE_405_STABLE/lib/ajax/service.php)。

- 使用 `allincludinghidden` 获取当前账号已选课程，包括用户自行在概览中隐藏的课程；不改变权限或课程状态。
- 每次请求 50 条，按服务端 `nextoffset` 继续，最多 21 次 Ajax 请求、1000 门课程。
- 空页且游标不再前进才表示遍历结束；短页或空页但游标前进时继续。返回 `complete=true, warning=null`。
- 重复页、游标异常、达到上限、无页面令牌、网络错误或不兼容响应返回 `complete=false` 和不完整原因。
- 完整结果以已选课程接口为准，不混入 HTML 的其他导航链接。部分结果合并已获取的动态课程及 HTML 课程，按课程 ID 去重。
- HTTP 401/403、登录跳转或明确的会话过期响应仍抛出学校登录错误，HTTP 接口返回 409；不能把过期会话验证为成功。

返回格式继续使用 `courses/complete/warning`，每门课程为 `external_course_id/title/source_url`。
标题移除 HTML 标记，链接由学校域名和课程 ID 构造；不信任接口返回的外站链接，不跟随 Ajax 重定向。
响应不包含 `sesskey`、Cookie 或上游错误原文。页面令牌仅用于本次调用，不写数据库。
`complete=true` 只表示本次已选课程查询遍历结束，不代表学校全部课程，也不提供并发课程变化的快照保证。

`agent/Scripts/python.exe tests/test_school_discovery.py` 包含 10 项模拟 HTTP 测试，覆盖动态分页、空页、
短页、去重、格式异常、部分结果、过期会话、重定向及上限；尚未用真实学校会话验收这一接口版本。

## 资源消失与重新出现状态（第五步）

沿用 `GET /api/courses/{course_id}/resources`，增加可选参数
`source_status=seen|not_seen|unknown`，省略时仍返回全部历史资源。
筛选在分页前执行，`total` 是筛选后的总数；非法状态返回 422，继续校验登录和课程归属。

每条资源新增以下字段：

| 字段 | 含义 |
| --- | --- |
| `source_status=seen` | 资源出现在课程最近一次成功采集中 |
| `source_status=not_seen` | 资源最后出现的时间早于课程最近成功同步时间 |
| `source_status=unknown` | 缺少课程同步时间，或资源时间晚于课程时间等无法可靠比较的情况 |
| `source_checked_at` | 课程最近成功同步时间；尚未同步时为 null |

既有 `last_synced_at` 仍是这条资源最后一次采集入库时间，不会因资源缺失而更新。
示例：`GET /api/courses/1/resources?source_status=not_seen&limit=20&offset=0`。

状态由现有课程与资源时间戳实时计算，不新增表字段或迁移，也没有永久删除标记。
成功同步的空资源列表会让旧资源变为 `not_seen`；资源重新出现后自动恢复 `seen` 并保留原 ID。
采集失败、会话过期或入库回滚不更新同步时间，因而不会误改观察状态。
这是最近一次成功采集范围内的观察结果：教师删除、隐藏、权限或页面范围变化都可能导致 `not_seen`，
不能据此断言资源已下架；也不把整个课程目录的完整性等同于课程内资源采集的完整性。

本步只为资源提供状态提示与筛选，不删除缓存、原资源记录或已导入项目的知识库文档，
不自动禁止旧缓存导入或撤销问答内容，也不记录首次消失时间和历次变化事件。

`agent/Scripts/python.exe tests/test_resource_status.py` 包含 5 项隔离 HTTP/SQLite 测试，覆盖资源消失与
重新出现、ID/缓存保留、空采集、筛选分页、失败保持原状态、未知时间及用户隔离。

## 作业与明确引用附件关联（第六步）

课程同步读取作业详情的 `#intro`，记录其中直接出现的 File 活动和文件链接对应的学校作业 ID。
采集快照的资源项新增临时字段 `assignment_external_ids`；入库时由当前用户、当前课程的本次作业
映射为本地 `assignment_id`，临时字段不写入资源表。课程页已出现的相同附件仍能补上关联。
资源表复用已有 `assignment_id/purpose`，无需数据库迁移。

- 仅被一个作业明确引用的附件自动设置 `assignment_id`，`purpose=assignment_attachment` 表示自动维护的附件关联，
  不代表已识别文件是题目、模板还是参考材料。
- 同一文件被多个作业引用时，不任意选择其中一个；移除已有自动关联并在同步 `warnings` 中提示。
  现有单个 `assignment_id` 无法表达多对多关系，本轮不扩表。
- 已有手动分类或手动关联保留。`assignment_attachment` 是自动维护标记，手动修正归属时应同时使用其他用途值。
- 资源仍出现在当前采集范围、但不再被作业直接引用时，清除其自动关联；转为另一个作业唯一引用时更新关联。
- 整条资源未出现在本次快照时保留历史关联，结合 `source_status=not_seen` 使用。旧调用方没有提供临时引用字段时也保留历史关联。
- Page/Folder 内间接链接、同名课件、学生提交文件不会据此自动归给作业；附件缓存及已导入项目文档保持独立。

资源列表新增可选的正整数 `assignment_id`（本地作业 ID）：

```http
GET /api/courses/1/resources?assignment_id=6&source_status=seen&limit=20&offset=0
```

响应沿用已有 `assignment_id/purpose` 字段，筛选在分页前执行；不属于该课程的作业 ID 返回空列表，
不泄露其他用户的作业存在性。资源列表本身仍要求当前用户拥有该课程。

`agent/Scripts/python.exe tests/test_assignment_resources.py` 包含 9 项隔离测试，覆盖明确引用采集、
去重、共享附件、学校 ID 到本地 ID 的转换、重复同步、引用移除、手动数据保护、事务回滚及查询筛选。
学校页面使用模拟响应，真实学校页面仍待验收。

## 对话查询作业（第七步）

现有 `POST /api/projects/{project_id}/ask` 和 `/ask/jobs` 增加可选请求字段
`use_school_tools`，默认 `false`。开启后，在项目和会话权限校验、历史加载之后，模型选择调用
学校只读工具（作业清单见本节、附件见第十步、提醒见第十一步、课表见第十二步）或继续原有文档检索。指定 `doc_ids` 或划线文档时仍走文档问答。
本步不改前端或数据库结构；现有页面需传入这个开关才能使用。

```json
{
  "query": "这周有哪些没交的作业？",
  "use_school_tools": true
}
```

继续同一对话时带上 `conversation_id`，并在每次请求中传入开关。路由模型读取最近 6 条消息
（每条最多 1000 字符）及最多 2000 字符的摘要，以辅助理解追问。

- 工具身份始终使用服务端当前登录用户，模型不能传入 `user_id`；查询范围是该用户的学校课程，
  并非项目中已导入的文档。课程 ID 是本地 ID，课程名称先精确匹配，再按字面子串匹配。
  多个候选返回澄清信息，未找到或不属于当前用户的课程统一返回未找到。
- 支持 `pending/submitted/unknown` 状态和截止日期区间；未知状态不归为未提交。
  今天、明天、本周、下周由服务端按北京时间解析，本周为周一至周日，绝对日期区间包含首尾两天。
  筛选截止日期时，不包含截止日期未知的记录。
- 每轮最多调用一次工具，每次最多 20 条；结果包含 `total/offset/has_more`。
  模型生成非法参数、未知工具、多工具调用或回答被截断时返回错误，不保存本轮消息。
- 回答沿用 `answer/sources/retrieved_contexts/conversation_id`，新增 `tool_results` 保存实际查询结果、
  筛选条件、查询时间及同步时间。学校工具不生成文档引用，`sources/retrieved_contexts` 为空；
  作业来源链接只保留合法的学校作业地址。结果同时保存到历史消息的 `response_data`。
- 数据来自最近一次本地同步，回答附带快照提示；零条结果不代表学校没有作业。
  本轮只查询，不会联网同步、下载题目、提交作业或设置提醒，也不同时执行文档检索。

持久化任务沿用现有查询、重试与恢复接口。已完成节点可以从检查点恢复，可能复用先前的查询快照；
失败任务需要重新查询时使用 `/api/projects/{project_id}/jobs/{job_id}/retry?restart=true`。
原有 HTTPException 重试策略仍可能清空检查点重新运行。已完成任务要刷新结果，应发起新的问答。

`agent/Scripts/python.exe tests/test_school_qa.py` 包含 9 项隔离测试，覆盖真实问答图、账号与课程隔离、
日期范围、课程名澄清、参数验证、文档分支兼容、结果保存和任务恢复。
测试使用临时 SQLite，模拟模型工具调用与回答；真实模型的工具选择能力和回答质量仍待联调验收。

## 定时更新已导入课程（第八步）

新增独立进程入口 `scripts/schedule_ispace.py`，不随 API 服务自动启动，不加载问答模型。
使用应用的 `DATABASE_URL`，并读取该账号通过 `PUT /api/school/session` 保存的本地学校会话：
默认 `.local/ispace-sessions/{user_id}.json`，可由 `ISPACE_SESSION_DIR` 改变目录。
它不读取第一节手工 CLI 的 `--session-file`，也不接受模型或公开 HTTP 请求指定用户身份。

在项目根目录执行，将 `123` 换成本地应用用户 ID：

```powershell
# 检查并更新一次到期课程；这条命令会实际采集和写入数据库。
.\agent\Scripts\python.exe scripts/schedule_ispace.py --user-id 123 --once

# 持续运行，默认每轮结束后等待 60 分钟；按 Ctrl+C 停止。
.\agent\Scripts\python.exe scripts/schedule_ispace.py --user-id 123 --interval-minutes 60
```

- 启动后立即检查，仅更新该用户已导入、带学校来源且距上次成功同步达到间隔的课程，
  从未成功同步的关联课程也会更新；课表中的无来源课程跳过。不会自动发现或导入新课程。
- 间隔允许 5 分钟至 7 天，按每轮完成后的固定等待计算；课程逐个处理，无并发采集或补跑积压批次。
  手动同步过的课程会暂时跳过。失败不更新成功同步时间，下一轮继续尝试。
- 每轮重新读取会话；会话缺失或过期时停止本轮，下一轮读取更新后的会话。单门课程失败则保留旧数据，
  继续其他课程。数据库批次失败也会等待下一轮，未知本地用户则退出。
- 网络采集不占用数据库事务。写入前重新检查归属、来源和同步时间；课程被删除、改来源或已被其他同步
  更新时返回 `skipped_changed`，不重建已删除课程或覆盖更新结果。
- 同一 `ISPACE_SESSION_DIR` 下，每个用户持有一个操作系统文件锁，覆盖执行和等待阶段。
  重复进程直接退出；不同用户可以各自运行。锁文件保留，锁随退出释放，不要在运行时删除锁文件。
  这是本机部署用锁，不提供多主机或不同会话目录之间的互斥。
- Ctrl+C / SIGTERM 请求停止后，当前课程采集返回时停止，不写入该次尚未保存的快照；已完成课程保留。
  进程退出或机器休眠期间不会同步；重新启动会依据数据库中的成功同步时间重新检查。

每轮向标准输出写一行 JSON，包含用户 ID、`courses_due` 和各课程结果。批次状态包括 `ok`、
`partial_failure`、`school_login_required`、`batch_failed`、`user_not_found`、`stopped`；
`ok` 且 `courses_due=0` 表示无需更新。输出不包含 Cookie、响应原文或 SQL 参数。
`--once` 成功退出码为 0，失败为 1；重复进程或参数错误为 2。可由部署环境使用 `--once` 调度，
但本实现不创建 Windows 计划任务、系统服务或开机启动项，也不提供前端定时设置或外部通知推送。
可选的站内 DDL 提醒见第九步。

`agent/Scripts/python.exe tests/test_school_scheduler.py` 包含 11 项测试，使用临时 SQLite 和模拟学校响应，
覆盖到期筛选、账号隔离、失败回滚、会话恢复、并发更新保护、停止、循环等待及 CLI 退出码；
另启动真实子进程验证本机文件锁互斥。真实学校会话和 PostgreSQL 并发行为仍待部署联调。

## 截止前 24 小时站内提醒（第九步）

新增 `deadline_reminders` 表。部署前执行迁移，再以可选参数开启提醒生成：

```powershell
.\agent\Scripts\python.exe -m alembic upgrade head
.\agent\Scripts\python.exe scripts/schedule_ispace.py --user-id 123 --interval-minutes 60 --deadline-reminders
```

也支持 `--once --deadline-reminders`，执行一次同步检查和提醒扫描。未传 `--deadline-reminders` 时
保留原定时同步行为。提醒在每轮同步检查结束后生成，即使没有到期课程需要同步，也会扫描已有数据；
会话失效、批次失败或停止时跳过。一门课程失败不阻止扫描其他满足条件的数据。
生成失败会回滚该次提醒写入并返回 `reminders.status=failed`，不会回滚已完成的课程同步，下一轮可重试。

生成条件固定为：当前用户的作业、明确的 `pending` 状态、截止时间在 `(当前时刻, 当前时刻+24小时]` 内，
且课程在最近 24 小时成功同步、作业也出现在该次同步中。未知状态、无截止时间、已过期、旧记录或未来的
异常同步时间不生成提醒。只依据本地副本，不代表已实时核对学校。扫描频率取决于运行间隔，并非准点推送；
进程未运行或长时间休眠可能错过截止前窗口，不补发已经过期的提醒。

数据库按 `(assignment_id, due_at)` 去重，多次扫描或重启不会重复生成同一截止时间的提醒。
截止时间变更后可生成新提醒；若改回曾提醒过的同一时间，复用原提醒及其已读状态。
生成结果在任务 JSON 中显示为 `reminders: {status: "ok", created: 数量}`。

已登录用户可调用：

```http
GET /api/assignment-reminders?active_only=true&unread_only=false&limit=20&offset=0
POST /api/assignment-reminders/1/read
```

列表只返回当前账号的数据，包含 `items/total/unread_count/limit/offset/timezone/warning`，时间输出为北京时间。
`unread_count` 按当前 `active_only` 范围统计，不随分页改变。默认只展示仍符合上述生成条件、且截止时间
仍与作业一致的有效提醒；已提交、DDL 变更、过期或数据不再新鲜时自动从默认列表隐藏。
用 `active_only=false` 可查历史，每条包含 `active`、生成时的 `due_at` 和最新的 `current_due_at`。
未知或取消的截止时间不会继续作为有效提醒。GET 不创建或标记提醒。

标记已读接口可重复调用，保留首次 `read_at`；其他账号的 ID 与不存在的 ID 统一返回 404。
已读不改变学校作业的提交状态。本轮仅交付持久化站内提醒、读取接口和生成任务，尚无前端提醒入口，
也不发送邮件、系统弹窗或聊天消息。AI 的作业查询工具暂不负责创建提醒。

`agent/Scripts/python.exe tests/test_deadline_reminders.py` 包含 9 项测试，覆盖日期边界、状态与新鲜度筛选、
账号隔离、去重、已读幂等、截止变化、分页、任务集成、事务回滚及迁移升降级。
使用临时 SQLite 和固定时钟；真实 PostgreSQL 并发与持续运行仍待部署验证。

## 对话查询作业附件（第十步）

沿用 `/api/projects/{project_id}/ask`、`/ask/jobs` 和 `use_school_tools=true`，新增只读模型工具
`query_assignment_resources`。没有新增公开接口、数据库迁移或自动下载流程。例如：

```json
{"query": "Database Systems 的 Lab 1 有哪些题目文件？已经导入当前项目了吗？", "use_school_tools": true}
```

模型可按 `assignment_name` 或用户明确提供的本地 `assignment_id` 查询，支持可选的 `course_name`、
`limit`（最多 20）和 `offset`。名称先不区分大小写精确匹配，再进行字面子串匹配；`%` 和 `_`
不作为通配符。仅在当前账号的课程内匹配，同名作业返回最多 10 个候选要求澄清；未提供作业时也请求澄清。
其他账号的作业 ID 与不存在的 ID 都返回未找到。用户和当前项目身份由服务器绑定，模型不能覆盖。

结果通过 `tool_results` 返回并保存到会话，包含作业信息、分页和以下附件字段：

| 字段 | 含义 |
| --- | --- |
| `resource_id/title/resource_type/purpose` | 已关联教学附件的标识与分类 |
| `source_url` | 校内文件或资源活动地址；无效链接为 null，移除任意查询参数 |
| `source_status` | 沿用最近同步观察状态 `seen/not_seen/unknown`，不表示已确认删除 |
| `cache_status` | `downloaded` 表示缓存文件存在，`not_downloaded` 表示未下载或文件已丢失，`unavailable` 表示路径或访问异常 |
| `project_document` | 当前项目中的导入文档 ID 与处理状态；没有记录时为 null |

只列出与目标作业明确关联的 `file/resource` 资源，排除 `student_submission`；未关联的同名课件不会自动算作附件。
学校缓存和项目文档是独立状态，即使缓存丢失，先前导入的项目副本仍可能存在；其他项目的导入记录不会混入。
`completed` 仅表示现有文档流程处理完成，不保证副本与学校最新版本一致。

本工具不会读取正文、执行下载、导入或学校请求，不暴露本地存储路径。附件标题和元数据不能作为题目正文依据；
解释文件内容仍使用文档问答。每轮仍至多调用一个学校工具，显式指定文档或选区时优先文档检索。
持久化任务恢复可能复用旧工具快照，刷新方式与第七步一致。无关联结果不代表学校没有文件。

`agent/Scripts/python.exe tests/test_school_resource_qa.py` 包含 6 项集成测试，覆盖真实问答图、关联范围、
账号及项目隔离、缓存丢失与异常、链接清理、名称澄清、分页、参数验证、结果持久化和失败恢复。
测试使用临时 SQLite，模拟模型输出；真实模型的工具选择与回答质量仍待联调。

## 对话查询站内截止提醒（第十一步）

沿用 `/api/projects/{project_id}/ask`、`/ask/jobs` 和 `use_school_tools=true`，增加只读工具
`query_deadline_reminders`，例如：

```json
{"query": "我有哪些未读的站内截止提醒？", "use_school_tools": true}
```

模型可传 `active_only`（默认 true）、`unread_only`（默认 false）、`limit`（最多 20）和 `offset`。
身份来自当前登录账号，查询范围是该账号的整个提醒箱；项目只作为会话容器，仍须验证项目权限。
工具复用第九步的提醒查询，不新增表或迁移；需要此前的提醒表迁移及生成任务才能拥有提醒数据。

- 明确问站内提醒、未读提醒或提醒历史时使用本工具；普通“有哪些快截止的作业”仍查询作业清单。
- 默认只显示有效提醒，查历史时可设置 `active_only=false`。历史项包含 `active`、原 `due_at`
  和最新同步的 `current_due_at`，不能把失效历史项当成当前催办。
- 返回 `total/unread_count/has_more` 和北京时间日期；未读总数按有效或历史筛选范围统计，不随分页改变。
  `read_at` 只是提醒已读状态，不代表作业已提交。
- 空提醒箱可能源于未启用生成任务、未进入提醒窗口或同步数据过旧，不证明没有待交作业，也不用于判断任务是否运行。
- 仅查询已生成记录，不生成新提醒、不自动标记已读、不设置推送；模型传入用户 ID、写入参数或未知工具会被拒绝。
- 结果经 `tool_results` 保存到会话与持久化任务；恢复可复用旧快照。刷新方式仍见第七步。
  显式文档范围、划线问答及每轮至多一个学校工具的规则不变。

`agent/Scripts/python.exe tests/test_school_reminder_qa.py` 包含 6 项集成测试，覆盖账号隔离、只读性、
有效及历史筛选、未读与分页语义、空列表、参数与写入工具拒绝、文档优先和任务恢复。
使用临时 SQLite 和固定时钟，模型输出为模拟；真实模型的意图选择和回答质量仍待联调。

## 对话查询每周课表（第十二步）

沿用 `/api/projects/{project_id}/ask`、`/ask/jobs` 和 `use_school_tools=true`，新增只读工具
`query_timetable`，例如：

```json
{"query": "明天有哪些课，几点上课、在哪上？", "use_school_tools": true}
```

工具读取当前账号已保存的 `courses_schedule`，不联网同步或重新识别课表。可按本地 `course_id`
或 `course_name` 过滤，同名课程要求澄清；其他账号的 ID 与不存在的 ID 都返回未找到。
支持以下互斥的日期筛选，不传时查询整周：

- `weekday`：`Mon` 至 `Sun`。
- `on_date`：具体日期，服务端换算成星期。
- `relative_day`：`today/tomorrow`，路由节点按北京时间换算并保存为具体日期，任务恢复不会随当天变化而重新解释。

返回课程、教师、星期、起止时间、地点及 `total/has_more`；每页最多 20 条。
按周一至周日、开始时间、记录 ID 排序；兼容旧记录中的英文完整星期、中文“周一/星期一”等写法，
以及 `9:00` 形式的时间排序。无法识别的星期在整周结果中放在最后，指定星期时不匹配，
通过 `unrecognized_day_count` 提示当前课程范围内此类记录的数量。

结果明确标记 `schedule_type=weekly_pattern`，`matched_date/matched_weekday` 仅说明匹配方式。
数据库没有学期、教学周、单双周、节假日或临时调课信息，所以不能确认具体某天实际开课，也不能将空结果断言为当天无课。
回答附带这一限制；工具不会修改课表或把课表识别结果说成学校实时校历。

沿用账号及项目鉴权、每轮至多一个学校工具、文档范围优先、结果持久化与任务恢复规则。本轮不新增数据库迁移。
`agent/Scripts/python.exe tests/test_school_timetable_qa.py` 包含 6 项集成测试，覆盖账号隔离、星期兼容与排序、
北京时间跨日、课程澄清、分页、未知星期、参数验证、文档优先及跨日任务恢复。
使用临时 SQLite 和模拟模型输出；真实模型的工具选择和回答质量仍待联调。
