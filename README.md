# Academic Agent

一个可在本机运行的中文学术助手，包含 **Vue 3 前端 + FastAPI 后端**。可以用自己的文档做带引用的问答，也可以让 AI 查询课程、作业、课表与提醒，并在授权后同步学校课程、下载附件、导入项目知识库，以及获取、补充和修改课表。

当前学校适配对象为 **BNBU iSpace（Moodle，课程与作业）** 和 **BNBU MIS（学校课表）**，不是通用的任意学校教务系统。访问学校需要自己的有效登录会话；文档问答、查看及手动维护已保存的课表不需要连接学校。

## 1. 功能概览

| 功能 | 可以做什么 |
| --- | --- |
| 账号与项目 | 注册、登录，创建和切换研究项目 |
| 文档知识库 | 上传 PDF、DOCX、PPTX、TXT；解析、分片、向量检索和重排 |
| 文档问答 | 连续追问、限定文档范围、原文追问、查看引用片段、导出 Markdown 对话 |
| 文档概述 | “Lab02 主要讲什么”“总结这份 PPT”等请求按文档顺序读取全部已入库正文；长文档逐段压缩，保留来源；同名文件需先在资料栏选择 |
| 文档管理 | 查看解析正文、下载原文件、失败重试、重新处理和删除 |
| 历史与任务 | 恢复历史会话；查看、重试后台任务；后台问答支持恢复 |
| 学校事务 | 配置学校连接、发现课程、首次导入、刷新已有课程、查看作业与资源 |
| AI 学校助手 | 按语义和上下文选择课程、作业、课表、附件、提醒等工具；识别课程缩写与中英文别名 |
| AI 执行操作 | 开启对应开关后同步单门课程、下载附件、导入项目、标记提醒已读，以及获取学校课表、识别图片补充、增删改安排和课程资料 |
| 课表 | 保存 MIS 学校快照；用 PNG/JPEG 图片补充；手动新增、修改时间地点和删除安排；配置学期、教学周及单双周 |
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

主 `requirements.txt` 已包含 Playwright 的 Python 依赖（通过 `requirements-local-login.txt` 引入）。使用本地学校登录或 MIS 课表获取功能时，还需准备浏览器：Windows 会优先使用已安装的 Microsoft Edge，也可安装 Chromium 作为备用。

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

上述 `.venv` 是新安装示例。本仓库当前开发环境若使用 `agent`，请将命令中的 `.venv` 换成 `agent`。安装依赖和启动后端必须使用同一个 Python 解释器，避免装到了系统 Python 而后端仍提示模块缺失。

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

- 后端和数据库迁移会自动读取项目根目录 `.env`，可参考 `.env.example`；终端已有环境变量优先，修改文件后需重启。
- 课表图片识别当前调用 `glm-ocr` 转录图片，再用 `glm-4.5-flash` 提取课程安排；名称写在 `app/services/timetable_service.py` 中。页面上传与 Agent 图片补充共用此流程，`ZHIPU_API_KEY` 需有对应调用权限。问答模型配置见 `app/services/answer_services.py`。
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

### 2.5 开启学校窗口与重启后端

在根目录 `.env` 中添加或修改 `ISPACE_LOCAL_LOGIN=1`。在原后端终端按 `Ctrl+C` 停止后，使用同一 Python 环境重启：

```powershell
$env:ISPACE_LOCAL_LOGIN = '1'
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

等出现 `Application startup complete` 后刷新前端。学校窗口需要桌面环境和单进程后端，不加 `--workers`；服务器部署保持此开关关闭。手动课表维护、图片识别和查看已有快照不依赖这个开关。

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

在研究项目聊天区域打开 **学校助手**。同步课程时开启 **允许同步课程**；执行课表获取、图片补充、增删改或其他写操作时开启 **允许下载、导入、已读和课表操作**。不要同时限定文档或附带原文选区；指定文档时会走文档问答。

| 你可以这样说 | 系统如何处理 |
| --- | --- |
| “我有哪些课程？” | 查询当前账号的本地课程清单 |
| “有没有课程是没有内容的？” | 查看本地没有作业和教学资源记录的课程 |
| “哪些课还没同步过？” | 查询没有成功同步时间的课程 |
| “dbms 有内容吗？” | 结合实际课程列表识别缩写，再查看课程记录 |
| “这周有哪些没交的作业？” | 查询已同步的作业、截止日期和提交状态 |
| “明天几点上课？” | 查询已保存课表及学期规则 |
| “帮我从学校获取课表” | 开启课表操作权限后打开 MIS 窗口，等待用户登录和选择学期 |
| “把已保存的学校课表图片识别进课表” | 识别学校快照并追加去重，原有安排保留 |
| 附加图片后说“识别这张图片补充课表” | 使用聊天中本轮上传的图片补充当前账号课表 |
| “把刚才查到的这条安排改到周三 14:00–15:30” | 依据实际查询记录定位并修改，缺少记录或有歧义时先查询或澄清 |
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

### 5.1 页面维护与图片补充

上传 PNG/JPEG 课表图片进行识别，在“我的课表”查看已保存记录。可以设置学期起止日期、教学周范围、每周或单双周，并按日期查询。没有学期配置的旧记录仅按星期匹配；不自动推断节假日、调课或停课。仅来自课表的课程不一定已关联 iSpace 来源。

“已保存课表”支持“手动新增安排”“修改时间地点”和“删除安排”。同一门课的不同上课时段分别维护；新增时按当前账号的课程编号或名称关联已有课程，并沿用其课程资料。删除需在页面确认，只删除所选时段，保留课程、作业和其他安排。手动修改不会清除已有学期规则，结果会用于课表查询和学校助手问答。

图片上传作为补充入口：识别后追加安排，相同课程、星期、起止时间和地点的记录会跳过，已有安排不会被整表覆盖。如果新图片修正了旧的错误时间或地点，请在已保存课表中修改或删除旧记录。学校 MIS 快照旁的“用此图片补充课表”可将保存的图片放入上传预览，点击“识别并保存课表”后才调用识别服务；也可上传另一张正确图片。识别不会自动继承 MIS 学期规则，保存后可自行设置。

### 5.2 在聊天中管理课表

在项目聊天中开启“学校助手”和“允许下载、导入、已读和课表操作”，即可让 Agent 调用课表工具。查询已保存安排或学校获取进度只需开启学校助手；修改数据和打开学校窗口需要操作开关。不要同时限定文档或附带原文选区。

- “帮我从学校获取课表”：打开本机 MIS 窗口；自行登录、选好学期后，再说“保存当前学期”。随后询问“学校课表保存好了吗”确认完成。学校窗口工具仍要求 `ISPACE_LOCAL_LOGIN=1` 和本机访问。
- “把已保存的学校课表图片识别进我的课表”：识别当前账号的 MIS 快照，追加到可编辑、可查询的课表。
- 点击聊天框“附加课表图片”，发送“识别这张图片补充课表”：附件只用于当前账号，成功识别后从输入框移除；PNG/JPEG 最大 10 MB。附件保存在 Git 忽略的 `.local/timetable-attachments/<用户ID>/`，按不可变 ID 绑定请求，后台重试不会换成另一张后来上传的图片。
- “新增数据库课，每周一 09:00–10:30，A101”：创建一个时段。也可修改时间地点、删除指定时段、设置学期与单双周，或修改课程名称、编号和教师（课程资料修改对该课程所有时段生效）。

当前学校工具每轮执行一个操作。修改或删除时若尚无准确安排 ID，会先查询并列出匹配项；下一轮指定对应安排继续。学校窗口的打开、保存、进度查询，以及快照识别补充也是分步执行，不能将“已打开”或“正在保存”理解为已入库。

| Agent 工具 | 作用 |
| --- | --- |
| `get_mis_timetable` | 读取获取进度、已保存学期和课程明细 |
| `open_mis_timetable` | 打开本机学校窗口，等待用户登录 |
| `save_mis_timetable` | 发起保存当前学期，需查询进度确认完成 |
| `cancel_mis_timetable` | 取消获取，保留之前的快照 |
| `supplement_timetable_image` | 识别本轮附件或已保存 MIS 图片，追加并去重 |
| `add_timetable_entry` | 新增一个上课时段 |
| `update_timetable_entry` | 修改星期、起止时间和地点 |
| `delete_timetable_entry` | 删除一个时段，保留课程资料 |
| `set_timetable_calendar` | 设置或明确要求后清除学期与教学周规则 |
| `update_course_details` | 修改课程名称、编号或教师，影响该课程全部时段 |

手动页面和 Agent 共用课表操作与字段校验；当前用户身份、图片附件与本机浏览器权限均由服务端绑定，模型不能指定其他用户或本地文件路径。

### 5.3 截止提醒

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
| 缺少 `DATABASE_URL` / `ZHIPU_API_KEY` | 在项目根目录 `.env` 填写，或在启动后端的终端设置环境变量 |
| `type vector does not exist` | 在当前数据库启用 `vector` 扩展，再执行迁移 |
| 登录后又要求登录 | 会话可能过期；重新登录，不混用 `localhost` 与 `127.0.0.1` |
| 学校会话缺失或过期 | 重新登录学校网站，更新“学校事务”的 `MoodleSession` |
| 打开学校浏览器失败（`ModuleNotFoundError`） | 用运行后端的 Python 执行 `-m pip install -r requirements-local-login.txt`，确认能导入 `playwright.sync_api` 后重启后端 |
| 没有“从 MIS 保存课表”按钮 | 设置 `ISPACE_LOCAL_LOGIN=1` 并重启后端、刷新网页 |
| 浏览器未安装或启动失败 | 使用后端 Python 执行 `-m playwright install chromium`；确认有桌面环境 |
| 学校快照已保存，但 AI 查不到课程安排 | 快照与可查询课表分开；点击“用此图片补充课表”再识别保存，或让 Agent 识别已保存学校图片 |
| 图片补充后仍看到旧的错误时间 | 图片按追加去重保存，不覆盖旧记录；手动或通过 Agent 修改、删除错误安排 |
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
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_school_timetable_actions.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_timetable_entries.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_timetable_upload.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_mis_timetable.py
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


### 本地学校登录助手

本地运行时，可点击“学校事务 → 连接学校”，在独立窗口完成学校登录。
助手验证学校访问成功后自动保存当前应用用户的会话并关闭窗口；不收集学校密码，
不复用日常浏览器的用户目录。支持取消、五分钟超时及手动粘贴兜底。

Windows 优先使用已安装的 Microsoft Edge；其他系统或 Edge 无法启动时使用 Chromium。
完整安装 `requirements.txt` 已包含浏览器控制库。旧环境只补装学校窗口依赖时，执行下面的最小升级命令（使用运行后端的 Python 环境）：

```powershell
python -m pip install -r requirements-local-login.txt
python -m playwright install chromium
```

使用以下命令替代原有后端启动命令，前端仍按原方式启动：

```powershell
$env:ISPACE_LOCAL_LOGIN = '1'
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

以上命令开启 `ISPACE_LOCAL_LOGIN=1`，后端绑定 `127.0.0.1:8000`。
助手要求有桌面环境、单进程运行；登录中不要重启后端。前后端都应只监听本机，
不要通过公网反向代理暴露启用了助手的服务。服务器部署保持该开关关闭，使用手动连接。
退出页面不会取消登录，重新进入可恢复进度；点击“取消连接”或“解除连接”会阻止待完成任务保存会话。
登录失效后重新连接即可，已有课程不会删除。已保存连接仅表示有凭据，实际有效性在访问学校时确认。

助手接口（均需应用登录，且启用本地模式）：`POST /api/school/login` 启动，
`GET /api/school/login` 查询当前用户进度，`DELETE /api/school/login` 取消。
接口不会返回学校 Cookie。学校 SSO/验证码仍由用户在学校页面完成。
实现使用 [Playwright 独立浏览器上下文](https://playwright.dev/python/docs/api/class-browsercontext)，
浏览器安装参见 [官方说明](https://playwright.dev/python/docs/browsers)。


### 邮箱验证码注册与密码重置

新注册需要邮箱验证码；已有账号仍可用原密码登录。“忘记密码？”可通过邮箱验证重设密码。启用前请执行新增迁移，并配置 QQ 邮箱 SMTP，详见 [邮箱配置与接口说明](docs/email-auth.md)。


### 图片识别容错

PDF/PPTX 中单张图片 OCR 异常会转交视觉模型；视觉识别失败时继续处理后续图片，保留正文及成功识别的图片内容，不采纳被截断或失败的描述。所有图片尝试完成后再判断失败阈值：默认失败至少 3 张且超过需识别图片的 50%，或累计失败至少 10 张，才将文档判为失败。主动跳过的装饰图不计入分母。

可通过 `IMAGE_FAILURE_MIN_COUNT`（默认 3）、`IMAGE_FAILURE_RATIO`（默认 0.5）、`IMAGE_FAILURE_MAX_COUNT`（默认 10）调整。原始文件转换失败、存储失败、没有可检索正文等仍正常报错。

允许范围内的失败不影响完成状态及问答，文档操作区显示失败图片的张数、页码和安全错误原因；超过阈值的失败同样提供图片明细。修复服务后可“重新处理”已完成的文档，或对失败任务“重试解析”。已有历史失败记录不会自动重新执行。


### MIS 课表离线保存

“我的课表”中的“从 MIS 保存课表”会打开独立学校窗口。登录后选中学期，等待课表完整显示，再回到应用点击“保存当前学期”。保存的是学校周课表 PNG 和课程明细；以后查看已保存版本无需登录 MIS。更新才需重新打开学校窗口登录。取消、超时或解析失败保留原快照。

本地助手要求 `ISPACE_LOCAL_LOGIN=1`、桌面环境和单进程后端（与 iSpace 登录助手一致）。学校网址固定为 `https://mis.bnbu.edu.cn/mis/student/tts/timetable.do`，不接收任意 URL。密码只在学校页面输入，不保存密码、Cookie 或浏览器用户目录。内置浏览器中用于检查页面的登录与应用助手的独立窗口不共享会话。

快照位于 Git 忽略的 `.local/mis-timetables/<应用用户ID>.json`，按用户隔离，图片接口需应用登录。仅保存最近一次选定学期；界面显示学期及保存时间。保存快照本身不会覆盖或写入用于课表问答的课程数据库；通过“用此图片补充课表”后识别保存，或 Agent 的 `supplement_timetable_image` 工具，才会追加到可编辑、可查询的课表记录。
