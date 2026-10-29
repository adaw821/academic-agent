# 本地环境安装与启动

供测试人员拉取代码后在自己的电脑上运行。先完成本文件，再按 [问答功能测试指南](问答功能测试指南.md) 测试。所有命令从项目根目录执行，除非特别标注。

## 1. 依赖分为三部分

| 类型 | 需要什么 | 安装方式 |
| --- | --- | --- |
| Python 后端 | FastAPI、SQLAlchemy、模型调用及文档处理库 | `requirements.txt` |
| 系统软件 | Git、64 位 Python、PostgreSQL、数据库端 pgvector 扩展 | 单独安装，或用 Docker 运行数据库 |
| 评测工具 | Ragas、HTTP 客户端、评分模型配置 | 单独评测虚拟环境 |

当前开发机虚拟环境记录的 Python 版本是 **3.13.5**。依赖清单保留项目原有固定版本，并按本地安装元数据补列直接导入的 `pydantic` 和 `langchain-core`；它不是完整的传递依赖锁文件，也尚未在全新机器验证安装。

## 2. 拉取代码与安装 Python 依赖

从开发者提供的仓库、分支拉取最新提交，不要复制他的 `agent` 虚拟环境或数据库目录。记录版本：

```powershell
git branch --show-current
git rev-parse --short HEAD
git status --short
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

直接使用虚拟环境解释器，不需要先激活环境。macOS/Linux 使用 `python3.13 -m venv .venv`，后续将 `.\.venv\Scripts\python.exe` 换为 `.venv/bin/python`。

如果固定版本无法下载，保留完整报错并核对软件源与 Python 版本，不要随意换成一组未经验证的新版本。

本轮增加 `rank-bm25`、`jieba`，老环境也要重新安装 `requirements.txt`。这两个依赖目前未锁版本；安装完成后执行 `.\.venv\Scripts\python.exe -m pip freeze > backend-environment.txt`，随测试报告保存实际版本。

## 3. PostgreSQL 与 pgvector：任选一种方式

`pip install pgvector` 只安装 Python 集成库，**不会安装 PostgreSQL 服务，也不会安装数据库端扩展**。每个测试数据库都必须启用 `vector` 扩展。参见 [pgvector 官方安装说明](https://github.com/pgvector/pgvector#installation)。

### 方式 A：用 Docker 运行带扩展的数据库

先安装并启动 Docker Desktop（Linux 容器模式），或可用的 Docker Engine。下面以 PostgreSQL 16 为本地测试环境，主机端口使用 5433，避免与已有的 5432 服务冲突：

```powershell
docker run --name academic-agent-test-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=local_test_change_me -e POSTGRES_DB=academic_agent -p 127.0.0.1:5433:5432 -v academic-agent-test-pgdata:/var/lib/postgresql/data -d pgvector/pgvector:pg16
docker logs academic-agent-test-db
docker exec academic-agent-test-db pg_isready -U postgres -d academic_agent
```

看到数据库就绪后，启用并检查扩展：

```powershell
docker exec academic-agent-test-db psql -U postgres -d academic_agent -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker exec academic-agent-test-db psql -U postgres -d academic_agent -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
```

`local_test_change_me` 仅为本机测试示例密码，可以自行替换，连接地址中的密码也要相应修改。容器与数据卷已存在时，不要重复执行 `docker run`，使用 `docker start academic-agent-test-db`。数据卷会保留数据，更改 `POSTGRES_PASSWORD` 参数不会修改已初始化数据库的密码。

### 方式 B：使用本机 PostgreSQL

安装 PostgreSQL 16 及匹配该版本的 pgvector 扩展，启动数据库服务。可从 [PostgreSQL 官方下载页](https://www.postgresql.org/download/) 获取数据库安装入口。

Windows 原生构建 pgvector 需要带 C++ 支持的 Visual Studio 构建工具；在管理员的 x64 Native Tools 命令提示符中按 [pgvector Windows 安装步骤](https://github.com/pgvector/pgvector#windows) 设置实际 `PGROOT` 并执行 `nmake`。普通 pip 无法替代这一步；不想编译可使用方式 A。其他系统的包管理器安装方式也见官方说明。

用 pgAdmin 或 psql 连接 `postgres` 数据库，单独执行：

```sql
CREATE DATABASE academic_agent;
```

再切换连接到 **academic_agent** 数据库执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT version();
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
```

如果提示扩展不存在，先安装数据库端 pgvector；如果提示权限不足，用有权限的数据库账号启用扩展。

## 4. 配置环境变量

在稍后启动服务的同一个 PowerShell 终端中设置：

```powershell
# Docker 示例；本机数据库请按实际端口、用户名和密码修改
$env:DATABASE_URL="postgresql+psycopg://postgres:local_test_change_me@127.0.0.1:5433/academic_agent"
$env:ZHIPU_API_KEY="填入可用的智谱API密钥"
```

macOS/Linux 使用 `export DATABASE_URL='...'` 和 `export ZHIPU_API_KEY='...'`。数据库密码中含 `@`、`/` 等 URL 特殊字符时，需要对密码部分进行 URL 编码。

代码直接读取环境变量，**不会自动加载 `.env` 文件**。不要把真实密钥写入代码或提交到仓库。

当前问答模型在 `app/services/answer_services.py` 中写为 `glm-5.3`，课表模型在 `app/services/timetable_service.py` 中写为 `glm-4.6v`。这些是当前源码值，不保证测试账号均有调用权限；请使用有相应权限的密钥。当前代码没有读取 `ZHIPU_CHAT_MODEL` 环境变量，设置它不会改变模型。

## 5. 初始化数据库表

必须先完成上一节的数据库端扩展启用，再运行：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

当前迁移包含 `vector(1024)` 列，但没有自动创建扩展。只运行迁移而不启用扩展，会出现 `type vector does not exist`。

当前已提供注册和登录 HTTP 接口，并通过 `user_sessions` 表校验登录状态。确认拿到了相应模型、路由和迁移文件，迁移已升级到交付版本的 head。不要用 SQL 插入 `password_hash='test-only'` 的用户：它没有有效密码哈希，无法正常登录；旧测试库有这种用户时，另注册一个测试账号。

## 6. 启动和检查

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

打开 [Swagger](http://127.0.0.1:8000/docs)，依次注册、登录、确认当前用户、创建项目、上传文档、查询状态、提问。使用 `POST /api/user/create` 注册（邮箱或手机号至少填写一个），使用 `POST /api/user/login` 登录，账号字段 `account` 填邮箱或手机号。登录返回 `session_id` Cookie；后续项目/文档/问答请求必须携带 Cookie。统一使用 `127.0.0.1`，不要混用 `localhost`，并用 `GET /api/user/me` 确认会话有效。当前会话有效期为 30 分钟，过期重新登录。

上传返回 **202**，等状态变成 `completed` 后再做内容测试；具体请求见 [问答功能测试指南](问答功能测试指南.md)。创建项目不再需要传 `user_id`，归属由登录用户确定。

服务启动时会预加载/下载向量模型 `BAAI/bge-m3`、重排模型 `BAAI/bge-reranker-v2-m3` 和 tokenizer，加载完成后才接受请求。非 TXT 文件解析还可能触发 Docling 模型下载，需要可访问相关模型源并留出数 GB 磁盘空间。记录下载/首次加载与后续请求耗时；不要把下载等待计入稳定推理延迟。GPU 不是运行的必需条件，CPU 耗时取决于机器、候选数量和输入长度；记录 GPU/CPU、内存及模型加载错误。

上传文件写入项目下的 `data/uploads`。后台任务在当前服务进程中执行；处理中不要改代码触发 reload，也不要关闭服务。正式性能测试去掉 `--reload`。

## 7. Ragas 单独安装

人工测试可以只安装后端依赖。做自动评测时，在第二个终端创建独立环境，避免改变后端已固定的依赖：

```powershell
py -3.13 -m venv .venv-ragas
.\.venv-ragas\Scripts\python.exe -m pip install --upgrade pip
.\.venv-ragas\Scripts\python.exe -m pip install ragas httpx
.\.venv-ragas\Scripts\python.exe -m pip check
.\.venv-ragas\Scripts\python.exe -m pip freeze > ragas-environment.txt
```

这里没有预设未经验证的 Ragas 固定版本。首次安装后保留生成的版本清单，复测时使用 `python -m pip install -r ragas-environment.txt` 重建对应环境。安装方式见 [Ragas 官方文档](https://docs.ragas.io/en/stable/getstarted/install/)。

本项目尚未提供 Ragas 评测脚本，安装后不会自动开始评分。测试人员需编写脚本调用本地 `/api/projects/{project_id}/ask`，保存 `answer`、`retrieved_contexts`，配合人工题目和参考答案评测；还要配置评分模型，所选指标需要嵌入模型时也需配置。具体记录与交付要求见测试指南第 8 节。

## 8. 常见故障

| 现象 | 检查 |
| --- | --- |
| `KeyError: DATABASE_URL` / `ZHIPU_API_KEY` | 是否在启动服务的同一终端设置变量 |
| 数据库连接被拒绝 | 服务是否启动；Docker 示例用主机端口 5433 |
| `extension vector is not available` | 数据库服务器尚未安装 pgvector |
| `type vector does not exist` | 是否在当前连接的数据库启用了扩展 |
| 模型调用 401/403 或模型不可用 | API 密钥、余额、账号权限及源码中的模型名 |
| 文档一直 queued/processing 或 failed | 查 Uvicorn 日志、模型下载状态，确认进程没有重启 |
| 项目/问答接口 HTTP 401 | 是否已登录并保留 `session_id` Cookie；会话是否过期 |
| 项目接口 HTTP 403 | 项目是否属于当前登录用户 |
| `ModuleNotFoundError: jieba` / `rank_bm25` | 用运行服务的同一个解释器重新安装 requirements.txt |
| `'user' is an invalid keyword argument for Document` | 确认交付代码已删除 Document 构造中的 `user=user` 并重启服务 |
| 问答提示没有资料 | 核对项目和 completed 状态；也可能候选被重排阈值全部过滤，不能直接断定库里没有答案 |

交付测试报告时记录 Python、PostgreSQL、pgvector 扩展版本、代码提交编号、依赖清单和模型名称。
