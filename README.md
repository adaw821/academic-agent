# Academic Agent

一个可在本机运行的中文学术助手，包含 **Vue 3 前端 + FastAPI 后端**。可以用自己的文档做带引用的问答，也可以让 AI 查询课程、作业、课表与提醒，并在授权后同步学校课程、下载附件或导入项目知识库。

当前学校适配对象为 **BNBU iSpace（Moodle）**，不是通用的任意学校教务系统。学校连接需要自己的有效登录会话；文档问答和查看已保存的课程数据不需要连接学校。

## 1. 功能概览

| 功能 | 可以做什么 |
| --- | --- |
| 账号与项目 | 注册、登录，创建和切换研究项目 |
| 文档知识库 | 上传 PDF、DOCX、PPTX、TXT；解析、分片、向量检索和重排 |
| 文档问答 | 连续追问、限定文档范围、原文追问、查看引用片段、导出 Markdown 对话 |
| 文档管理 | 查看解析正文、下载原文件、失败重试、重新处理和删除 |
| 历史与任务 | 恢复历史会话；查看、重试后台任务；后台问答支持恢复 |
| 学校事务 | 配置学校连接、发现课程、首次导入、刷新已有课程、查看作业与资源 |
| AI 学校助手 | 按语义和上下文选择课程、作业、课表、附件、提醒等工具；识别课程缩写与中英文别名 |
| AI 执行操作 | 开启对应开关后同步单门课程、下载学校附件、导入当前项目、标记提醒已读 |
| 课表 | 上传 PNG/JPEG 课表识别，查看已保存课表，配置学期、教学周及单双周 |
| 站内提醒 | 查看已生成的截止提醒、筛选未读、标记已读；生成提醒需要单独运行调度脚本 |

当前不支持自动提交作业、聊天中批量同步全部课程、外部消息推送，以及自动识别节假日或临时调课。课程资源元数据不等于附件已下载，也不等于文件已进入知识库。

## 2. 首次启动整个系统

需要 Git、64 位 Python、Node.js，以及 PostgreSQL + 数据库端 pgvector 扩展。当前开发环境使用 Python 3.13.5，前端按 Node.js 22+ 配置。Python 依赖包含固定版本，但不是完整的传递依赖锁文件；全新机器的安装仍需验证。

下面以 **Windows PowerShell** 为例。macOS/Linux 将 Python 路径换为 `.venv/bin/python`，环境变量使用 `export`。

### 2.1 下载代码与安装后端依赖

```powershell
git clone --branch codex/fullstack-usage-guide https://github.com/adaw821/academic-agent.git
cd academic-agent
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

直接使用虚拟环境解释器，无需激活环境。

### 2.2 准备数据库

已有 PostgreSQL 的用户可以创建 `academic_agent` 数据库并在其中启用 `vector` 扩展。**安装 Python 的 pgvector 包不能代替数据库扩展。**

如果使用已启动的 Docker Desktop（Linux 容器模式），可以运行：

```powershell
docker run --name academic-agent-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=local_dev_change_me -e POSTGRES_DB=academic_agent -p 127.0.0.1:5433:5432 -v academic-agent-pgdata:/var/lib/postgresql/data -d pgvector/pgvector:pg16
docker exec academic-agent-db pg_isready -U postgres -d academic_agent
```

等数据库报告可连接后，启用扩展：

```powershell
docker exec academic-agent-db psql -U postgres -d academic_agent -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

`local_dev_change_me` 是本机示例密码，请按需修改。以后启动这个容器用 `docker start academic-agent-db`，不用重复创建。数据保存在命名卷中。

### 2.3 配置并启动后端

在项目根目录的同一个终端运行：

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:local_dev_change_me@127.0.0.1:5433/academic_agent"
$env:ZHIPU_API_KEY="替换为自己的智谱API密钥"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

使用自己的 PostgreSQL 时，修改连接地址、端口、用户名和密码；密码里的特殊字符需要 URL 编码。每次新开终端都要设置环境变量，或自行配置持久化环境。

- 后端**不会自动读取 `.env` 文件**，不要只创建文件而不设置环境变量。
- 当前问答模型是 `glm-5.3`，课表识别模型是 `glm-4.6v`，名称写在相应服务源码中。密钥需有调用权限；当前没有用环境变量切换这两个模型的配置入口。
- 启动会预加载 `BAAI/bge-m3`、`BAAI/bge-reranker-v2-m3` 和 tokenizer。首次可能下载模型，需要可访问模型源，并留出数 GB 磁盘空间。等出现 `Application startup complete` 后再使用。
- 日常体验建议保持上面不带 `--reload` 的启动方式，避免反复加载模型；修改后端代码后手动重启。

就绪后可打开 [接口文档](http://127.0.0.1:8000/docs)。这是 API 调试页面，完整用户界面在前端。

### 2.4 启动前端

再开一个终端，进入项目的 `frontend` 目录：

```powershell
cd frontend
npm ci
npm run dev
```

打开 **[http://127.0.0.1:5173](http://127.0.0.1:5173)**。前端把 `/api` 请求代理到本机 `8000` 端口；统一使用 `127.0.0.1`，避免与 `localhost` 混用导致登录 Cookie 不一致。

后端地址不同，可以把 `frontend/.env.example` 复制为 `frontend/.env.local`，修改 `API_TARGET` 后重启前端。不要在前端配置模型密钥。

正常使用需要数据库、后端和前端都保持运行。后端与前端分别在各自终端按 `Ctrl+C` 停止。

## 3. 第一次使用：文档问答

1. 在网页注册账号并登录。邮箱或手机号至少填写一个；登录账号使用邮箱或手机号。
2. 创建研究项目，例如“数据库课程”，并进入该项目。
3. 上传 PDF、DOCX、PPTX 或 TXT，等文档状态变成 `completed`。
4. 输入“这份资料讲了什么？”或具体问题，点击答案引用查看原文片段。
5. 继续追问，或勾选文档限定检索范围；原文追问用于指定文档中的选区。

上传成功只表示进入处理流程，排队或处理中不等于已可检索。首次处理 PDF、Office 文件可能还需要下载解析/OCR 模型。

“历史与任务”可以恢复已有对话；开启“后台问答（支持恢复）”会创建可追踪任务。刷新后从后台任务入口找回结果，不需要反复重新提交。

文档菜单可查看解析正文、下载、重试失败解析、重新处理和删除。重新处理与删除会要求确认；处理中部分操作不可用。

## 4. 连接学校并同步课程

### 4.1 配置学校会话

1. 在浏览器打开并登录 [BNBU iSpace](https://ispace.bnbu.edu.cn/)。
2. 在学校页面按 `F12`，找到 **Application / 应用 → Cookies → 学校域名**。
3. 找到 `MoodleSession`，复制其 **Value**，不要复制整个 Cookie 请求头。
4. 回到本系统的“学校事务”，粘贴到学校会话输入框，点击“验证并保存连接”。

系统不收集学校密码。会话保存在后端本机 `.local/ispace-sessions/`，输入框提交后清空，不写入浏览器持久存储。会话是临时登录凭据，不要放进聊天、截图或 Git 仓库。

会话过期时，重新登录学校网站并更新会话值。解除连接会删除连接凭据，已同步的本地课程数据保留。

### 4.2 在页面中同步

在“学校事务”点击“从学校发现课程”，选择目标课程导入。也可以使用学校课程链接首次导入，或对已有本地课程执行刷新。

同步会保存课程、作业和资源元数据。附件下载与导入项目知识库是独立操作；查看对应项目的任务状态，等处理完成后再用于文档问答。

### 4.3 让 AI 帮你操作

在研究项目聊天区域打开 **学校助手**。需要修改数据时，再开启 **允许同步课程** 或 **允许下载、导入和已读操作**。不要同时限定文档或附带原文选区；指定文档时会走文档问答。

| 你可以这样说 | 系统如何处理 |
| --- | --- |
| “我有哪些课程？” | 查询当前账号的本地课程清单 |
| “有没有课程是没有内容的？” | 查看本地没有作业和教学资源记录的课程 |
| “哪些课还没同步过？” | 查询没有成功同步时间的课程 |
| “dbms 有内容吗？” | 结合实际课程列表识别缩写，再查看课程记录 |
| “这周有哪些没交的作业？” | 查询已同步的作业、截止日期和提交状态 |
| “明天几点上课？” | 查询已保存课表及学期规则 |
| “帮我同步 dbms” | 允许同步时，识别课程并执行单门同步 |
| “同步学校课程编号 11405” | 重新验证编号在当前学校账号下可见后同步 |
| “Database Management Systems（学校课程编号：11405）这个” | 在上一轮待同步候选的上下文中确认这门课程 |
| “看看有哪些未读提醒” | 查询已经生成的站内提醒 |
| “下载附件，本地资源 ID 是 12” | 开启操作权限后执行下载；ID 必须来自实际列表 |
| “把本地资源 ID 12 导入当前项目” | 开启操作权限后创建导入任务 |

编号只用于展示说法，请使用实际列表中的编号。**本地课程 ID 和学校课程编号不是同一种 ID。**

模型按语义、近期对话和工具定义选择路径，无需输入固定命令。缩写与中文名称会和实际课程列表匹配；多门匹配或判断不确定时让你选择。意图不清楚时先澄清，文档问题才转交文档问答。

本地没有目标课程时，允许同步后可以到学校发现并首次导入。发现列表不完整时，不会仅凭名称猜测唯一课程。当前每轮选择一个工具，不支持一轮里任意连续执行多个操作。

“本地无内容”不证明学校课程为空；“最近未发现”的作业不等于已下架或无需提交。课程内容、提交要求和最终截止时间以学校实际页面为准。

## 5. 课表与提醒

上传 PNG/JPEG 课表图片进行识别，在“我的课表”查看已保存记录。可以设置学期起止日期、教学周范围、每周或单双周，并按日期查询。没有学期配置的旧记录仅按星期匹配；不自动推断节假日、调课或停课。仅来自课表的课程不一定已关联 iSpace 来源。

提醒页展示已经生成的提醒。标记已读不会修改作业提交状态。目前只使用站内提醒，没有外部推送。

启动网页和 API 不会自动开启学校定时同步/提醒扫描。如需要，在另一个终端设置 `DATABASE_URL`，从根目录运行：

```powershell
# 将数字替换为自己的本地应用用户 ID，可在已登录的 GET /api/user/me 中查看
$appUserId = 1
.\.venv\Scripts\python.exe scripts/schedule_ispace.py --user-id $appUserId --interval-minutes 60 --deadline-reminders
```

该进程定期刷新这个账号已经导入的课程，并生成未来 24 小时内待交作业的站内提醒，需要有效学校会话。按 `Ctrl+C` 停止，加 `--once` 只运行一次；不要为同一账号重复启动多个调度进程。API 的文档/问答任务恢复线程与学校调度进程是两回事。

## 6. 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 前端打开但接口失败 | 确认后端加载完成、8000 端口可访问、前端 `API_TARGET` 正确 |
| 缺少 `DATABASE_URL` / `ZHIPU_API_KEY` | 在启动后端的同一个终端设置变量；后端不自动加载 `.env` |
| `type vector does not exist` | 在当前数据库启用 `vector` 扩展，再执行迁移 |
| 登录后又要求登录 | 会话可能过期；重新登录，不混用 `localhost` 与 `127.0.0.1` |
| 学校会话缺失或过期 | 重新登录学校网站，更新“学校事务”的 `MoodleSession` |
| 找不到学校课程 | 检查正式名称、连接和发现结果；复制实际候选的学校编号确认 |
| 课程有资源，但文档问答没有资料 | 同步只保存元数据；还需下载/导入项目并等待处理完成 |
| 问学校问题却进入文档问答 | 打开“学校助手”，取消文档范围或原文选区，确认运行最新后端 |
| 文档提示没有检索到资料 | 核对项目、文档状态、范围和解析正文；检索不到不等于原文没有答案 |
| 任务失败或刷新后找不到结果 | 从“历史与任务”检查状态、恢复会话或重试失败任务 |
| 没有截止提醒 | 检查提醒是否已生成、同步是否成功、调度脚本是否运行 |
| 启动或首次解析很慢 | 查看模型下载与日志；本地模型/OCR 在 CPU 上可能较慢 |

性能日志位于 `logs/performance.log`，普通异常在后端终端查看。详细环境说明见 [ENVIRONMENT.md](ENVIRONMENT.md)。

## 7. 开发与验证

```text
app/                  FastAPI 路由、数据模型、RAG 与学校工具
frontend/             Vue 3 + TypeScript + Vite 前端及浏览器测试
migration/            Alembic 数据库迁移
tests/                后端测试
scripts/              学校同步/调度与开发验证脚本
docs/                 接口、工作流与功能说明
requirements.txt      Python 依赖
```

前端类型检查、构建和浏览器测试：

```powershell
cd frontend
npm run build
npm test
```

浏览器测试默认使用 Microsoft Edge；未安装 Edge 时，按 [前端 README](frontend/README.md) 安装并选择 Playwright Chromium。测试模拟 API，不代表真实学校和模型联调验收。

在根目录运行学校助手后端测试的示例：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_school_qa.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_school_sync_qa.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_school_course_qa.py
```

构建结果在 `frontend/dist/`，`npm run preview` 可在本机预览。正式部署需自行配置 HTTPS、同源 `/api` 反向代理、数据库备份和凭据存储；Vite 开发服务器不用于正式托管。

本仓库包含前后端源码、依赖清单、迁移、测试和文档，不包含本地数据库、用户上传、学校会话、API 密钥、虚拟环境、模型缓存或 `node_modules`。

## 8. 更多说明

- [前端使用与接口映射](frontend/README.md)
- [环境安装详细说明](ENVIRONMENT.md)
- [模型工具选择与课程内容状态](docs/school-tool-routing.md)
- [课程首次导入与名称匹配](docs/school-first-import.md)
- [学校操作、作业状态与学期课表](docs/school-actions-calendar.md)
- [学校同步接口](docs/ispace-sync.md)
- [历史、文档管理与后台恢复](docs/backend-completion.md)
- [LangGraph 与图片处理流程](docs/langgraph-workflows.md)
- [问答测试指南](问答功能测试指南.md)

部分测试记录描述的是当时版本；当前接口以本分支源码和运行中的 `/docs` 为准。
