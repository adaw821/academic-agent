# Academic Agent 测试指南

## 网页前端

前端代码独立放在 [`frontend/`](frontend/README.md)，使用 Vue 3、TypeScript 和 Vite。先启动本机 8000 端口的后端，再在另一个终端执行：

```powershell
cd frontend
npm ci
npm run dev
```

访问 http://127.0.0.1:5173 ，即可注册登录、创建项目、上传文档并提问。聊天记录暂存在页面内，刷新会清空。项目相关接口现在需要登录 Cookie；旧测试脚本需先登录并保留 Cookie。完整说明见前端 README。

本文档用于帮助测试人员在本地启动项目，并测试文档上传、Docling 解析、父子分片和向量入库效果。

首次搭建请先阅读 [本地环境安装与启动](ENVIRONMENT.md)，包含 Python、PostgreSQL、pgvector、迁移和独立 Ragas 环境。问答测试按 [问答功能测试指南](问答功能测试指南.md) 执行。

## 1. 当前功能

项目目前提供 5 个 HTTP 接口：

| 方法 | 地址 | 用途 |
| --- | --- | --- |
| `POST` | `/api/timetable` | 上传课表图片并识别 |
| `POST` | `/api/projects` | 创建文档项目 |
| `POST` | `/api/projects/{project_id}/documents` | 上传文档、解析、分片并生成向量 |
| `GET` | `/api/projects/{project_id}/documents/{document_id}` | 查询后台处理状态 |
| `POST` | `/api/projects/{project_id}/ask` | 检索并回答，返回答案、召回正文和来源 |

本文主要测试后两个接口。文档处理流程如下：

```text
TXT 直接按 UTF-8 读取；PDF 使用 Docling 结构化结果；DOCX/PPTX 经 Docling 解析为 Markdown
  -> 父分片（PDF：按小节，可跨页，最多 4000 字符含标题路径；PPTX：每页一个；其他：2000 字符，重叠 80）
  -> 子分片（PDF/PPTX：父片内按内容块合并，最多 800 字符含标题、无重叠；TXT：450 字符，重叠 50；其他：300 字符，重叠 30）
  -> BAAI/bge-m3 生成 1024 维向量
  -> PostgreSQL + pgvector
```

PDF 父片和每个子片携带完整标题路径（例如 `数据库 > 索引 > 联合索引`）。小节在下一个标题处结束，换页不结束；超过 4000 字符时优先沿内容块拆父片，这些父片共享文档内的 `section_id`。同名但位置不同的小节使用不同 ID。没有章节归属的正文按页兜底；父片和子片保留 Docling 内容项的来源页码。PPTX 继续按页切分，并重复较短的页首标题。二者跳过空白、纯标题和仅含图片占位符的内容。

标题层级以 Docling 识别结果为准；如完整标题路径本身已占满子片预算，上传会明确报错。正文项跨页时保留该项的所有来源页码。问答 `sources` 新增 `section_id`、`heading_path`、`page_numbers`（父片正文页码）和 `child_page_numbers`（命中子片正文页码），旧文档这些字段为空。检索仍按子片评分、按父片去重，不自动展开整个章节。

升级已有数据库后再启动新代码：`python -m alembic upgrade head`。新分片规则适用于后续上传；已有文档需要重新上传才能使用。

PDF/PPT 上传解析后还会提取 Docling 的图片节点，写入原上传文件旁的 `<文件存储名>.images/`，其中包含 PNG 和 `manifest.json`。入口为 `app/services/image_extraction.py` 的 `extract_document_images(document, document_id, output_dir)`，返回图片记录列表；无需新增数据库迁移。

每条记录包含 `image_id`、`source_ref`（Docling 节点引用）、`page_no`、`bbox`、`reading_order` 和相对输出目录的 `image_path`。`reading_order` 是遍历所有正文内容项（含图片子节点）的零起始序号，不是图片序号，也不是视觉模型校正后的顺序。坐标保留 Docling 的 `coord_origin`；`provenance` 保存全部来源位置及页面尺寸，顶层页码/坐标取第一个来源。`status=unavailable` 表示节点缺少可用像素或读取失败，不会伪造图片路径。图片留档发生在内置描述完成之后，`saved/filtered` 仅表示 PNG 留档状态，不表示是否调用过视觉模型。

PDF/PPT 图片识别使用 Docling 内置 `PictureDescriptionApiOptions`，在转换阶段调用 `glm-4.6v-flash` 并写入图片描述；沿用 `ZHIPU_API_KEY`。提示词要求有字完整转录、无字简短描述，直接输出 Markdown。描述随后进入现有父子分片。PDF `do_ocr=False`，仍提取原生文字层；图片描述不等于整页 OCR，不保证覆盖未被识别为图片的扫描内容。配置、测试方式和限制见 [Docling 图片描述实验](docs/docling-picture-description.md)。

## 2. 环境要求

- Git
- 64 位 Python（当前开发环境为 3.13.5）
- PostgreSQL（环境指南以 16 为例）
- PostgreSQL 的 `pgvector` 扩展
- ApiPost（用于调用接口，可选）
- 可访问 Hugging Face 的网络环境

首次处理文档时，`sentence-transformers` 会下载 `BAAI/bge-m3` 模型，Docling 也可能下载所需模型。第一次请求会比较慢，并需要数 GB 可用磁盘空间。后续请求会使用本地缓存。

## 3. 下载代码

```powershell
git clone https://github.com/adaw821/academic-agent.git
cd academic-agent
git checkout <开发者提供的分支名>
```

确认当前分支：

```powershell
git branch --show-current
```

输出应与开发者提供的待测分支一致；另用 `git rev-parse --short HEAD` 核对提交编号。上面命令中的占位符需要替换后再执行。

## 4. 创建 Python 虚拟环境

Windows PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

macOS/Linux：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

安装的主要依赖包括 FastAPI、SQLAlchemy、Alembic、PostgreSQL 驱动、pgvector、Docling、Sentence Transformers 和 BGE-M3 运行依赖。

性能埋点只使用 Python 标准库中的 `logging` 和 `time.perf_counter()`，无需额外依赖。问答已实现；Ragas 属于独立评测环境，按 [环境指南](ENVIRONMENT.md) 安装，不混入后端运行依赖。

## 5. 准备 PostgreSQL

先创建一个测试数据库，例如：

```sql
CREATE DATABASE academic_agent;
```

连接到 `academic_agent` 数据库后启用向量扩展：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

如果执行扩展命令时报错，需要先在 PostgreSQL 中安装 pgvector。注意：Python 包 `pgvector` 不能代替 PostgreSQL 服务端扩展。

## 6. 设置环境变量

请根据自己的用户名、密码、端口修改连接地址。

Windows PowerShell：

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:你的密码@127.0.0.1:5432/academic_agent"
$env:ZHIPU_API_KEY="你的智谱API密钥"
```

macOS/Linux：

```bash
export DATABASE_URL="postgresql+psycopg://postgres:你的密码@127.0.0.1:5432/academic_agent"
export ZHIPU_API_KEY="你的智谱API密钥"
```

`DATABASE_URL` 和 `ZHIPU_API_KEY` 都会在启动时读取。后者用于问答和课表识别；测试这两个功能必须使用有效密钥。当前不会自动读取 `.env` 文件。

不要把真实数据库密码或 API 密钥提交到 Git，也不要把它们放进要分享的 ApiPost 云端变量中。

## 7. 初始化数据库

在项目根目录执行：

```powershell
alembic upgrade head
```

然后用数据库客户端执行下面的 SQL，创建一个仅供本地测试使用的用户：

```sql
INSERT INTO users (username, email, phone, password_hash)
VALUES ('chunk_tester', 'chunk_tester@example.com', NULL, 'test-only')
RETURNING id;
```

记录返回的用户 `id`，后面创建项目时需要使用。该记录只用于本地分片测试，不要用于真实登录功能。

## 8. 启动服务

确保环境变量已经在当前终端中设置，然后运行：

```powershell
uvicorn main:app --reload
```

启动后访问：

- Swagger 文档：<http://127.0.0.1:8000/docs>
- ReDoc：<http://127.0.0.1:8000/redoc>
- OpenAPI 文件：<http://127.0.0.1:8000/openapi.json>

如果能够打开 `/docs`，说明服务已正常启动。

## 9. 导入 ApiPost

浏览器打开：

```text
http://127.0.0.1:8000/openapi.json
```

将内容保存为 `openapi.json`，然后在 ApiPost 中选择：

```text
导入项目 -> 手动导入 -> Swagger 3.0 -> 选择 openapi.json
```

也可以直接在 Swagger 页面测试，不强制使用 ApiPost。

## 10. 准备测试文件

建议在本地创建 `test_files` 目录。该目录已被 Git 忽略，不会上传到仓库。

至少准备以下文件：

1. 一份纯文本 `.txt`，包含多个标题和长段落；
2. 一份普通文字型 `.pdf`；
3. 一份含标题、列表和表格的 `.docx`；
4. 一份多页、含项目符号和表格的 `.pptx`；
5. 一份包含中英文混排、数字和专有名词的文档；
6. 一份空文件或不支持的文件，用于异常测试。

请不要只用扫描版 PDF。当前流程主要测试可解析文字；扫描件还会引入 OCR 质量变量。如果要测试扫描件，应单独记录为 OCR 测试。

为了判断是否丢失内容，建议在正常测试文档的开头、中间和末尾分别放置唯一标记：

```text
START-MARKER-2026
MIDDLE-MARKER-2026
END-MARKER-2026
```

## 11. ApiPost 测试步骤

### 11.1 创建项目

请求：

```text
POST http://127.0.0.1:8000/api/projects
```

Body 选择 `JSON`：

```json
{
  "user_id": 1,
  "name": "分片效果测试"
}
```

将 `user_id` 替换为初始化数据库时返回的 ID。请求成功后记录响应中的 `project_id`。

预期结果：HTTP 状态码为 `201`，并返回 `project_id`。

### 11.2 上传并分片文档

请求：

```text
POST http://127.0.0.1:8000/api/projects/{project_id}/documents
```

将 `{project_id}` 替换为上一步返回的数字。Body 选择 `form-data`：

| 参数名 | 类型 | 值 |
| --- | --- | --- |
| `files` | File | 选择一个或多个测试文件 |

支持的扩展名：

```text
.txt  .pdf  .docx  .pptx
```

不要手动设置 `Content-Type` 请求头，ApiPost 会自动生成带 boundary 的 `multipart/form-data`。

预期结果：HTTP 状态码为 `202`，`saved_files` 中出现成功保存并排队的文件，`failed_files` 中出现空文件或不支持的文件。随后调用文档状态接口，等 `status=completed` 才能检查分片和正式提问。

服务启动时会预加载 BGE-M3 向量模型、BGE reranker 和 GLM tokenizer，加载完成后才接受请求；加载失败会使启动失败。各项耗时见 `startup.embedding_model`、`startup.reranker`、`startup.tokenizer` 和 `startup.models`，完成事件为 `startup_models_ready`。请求继续复用原有缓存。每个 worker 进程独立加载，首次启动可能需要下载模型；Docling 解析器仍在首次文档处理时初始化。此步骤不执行推理预热，首次实际推理仍可能有额外初始化开销。

上传响应耗时与后台处理完成耗时应分开记录：

- 文件名和文件大小；
- 请求总耗时；
- HTTP 状态码；
- `saved_files` 和 `failed_files`；
- 服务端终端中的异常信息。

### 11.3 查看和保存性能日志

性能埋点自动以 UTF-8 追加到项目根目录的 `logs/performance.log`，不需要重定向终端。目录自动创建，已加入 Git 忽略规则。直接启动即可：

```powershell
uvicorn main:app --host 127.0.0.1 --port 8000
# 在另一个终端实时查看
Get-Content logs/performance.log -Wait -Tail 50
```

可在启动前设置环境变量 `PERFORMANCE_LOG_FILE` 自定义文件位置；相对路径按项目根目录解析。普通运行日志与异常堆栈仍输出到终端。正式测速建议关闭 `--reload`。

每个阶段产生 `pipeline_stage_started` 和 `pipeline_stage_finished`，完成记录包含 `stage`、`elapsed_ms`（毫秒）、`status`、`error_type`。`trace_id` 关联一次调用，`span_id` / `parent_span_id` 区分嵌套及重复阶段；同时记录时间、进程 ID 和可用的项目/文档 ID。上传和后台处理各有独立 trace，可通过文档 ID 关联后台记录。阶段为包含子阶段的墙钟耗时，不能直接相加；跳过的阶段不会产生记录，缓存模型仅首次加载计时。

| 阶段 | 内容 |
| --- | --- |
| `upload.total` / `upload.file_read` / `upload.file_save` / `upload.db_flush` / `upload.commit` | 上传总耗时、读文件、落盘、写入记录、提交 |
| `document.process` / `document.pipeline` / `document.file_read` / `document.processing_commit` / `document.final_commit` | 后台调用、处理流程、文件读取、状态提交、分片最终提交 |
| `chunk.*` / `image.*` | 文档转换、图片提取及识别、父子分片、向量化和数据库写入（按实际执行阶段记录） |
| `qa.total` / `qa.history_load` / `qa.query_rewrite` / `qa.translate` | 问答总耗时、历史读取、问题改写、翻译 |
| `qa.retrieve` / `qa.vector_search` / `qa.query_embedding` / `qa.bm25_search` / `qa.rrf_fuse` / `qa.rerank` | 检索、问题向量化、关键词检索、融合、重排 |
| `qa.answer` / `qa.answer_llm` / `qa.history_save` | 回答准备与生成、回答模型调用、历史保存 |

失败阶段仍记录耗时和异常类型，不写入问题、文档正文或模型响应。后台异常由原有逻辑处理，因此以 `document.pipeline` 的状态判断处理是否成功；`document.process` 包含失败恢复逻辑。日志持续追加，不自动轮转，可在服务停止后按需归档。

一次成功上传会产生以下几类关键事件：

```text
event=document_converter_initialized elapsed_ms=...
event=embedding_model_loaded model=BAAI/bge-m3 elapsed_ms=...
event=document_chunked document_id=... file_size_bytes=...
    parse_ms=... parent_split_ms=... child_split_ms=...
    embedding_ms=... parent_db_flush_ms=... total_ms=...
    parent_count=... child_count=...
event=document_upload_completed project_id=... total_ms=...
    file_save_ms=... commit_ms=... saved_count=... failed_count=...
```

失败时会出现：

```text
event=document_upload_failed project_id=... total_ms=...
    saved_count=... failed_count=...
```

上述失败事件与 Python 异常堆栈仍在终端，统一性能文件中可查对应失败阶段。日志不会主动记录文档正文、向量、数据库密码或 API 密钥。

### 11.4 日志字段含义

| 字段 | 含义 | 重点观察 |
| --- | --- | --- |
| `document_converter_initialized` | Docling 转换器首次初始化 | 只在当前进程第一次使用时出现 |
| `embedding_model_loaded` | BGE-M3 首次加载 | 只在当前进程第一次使用时出现，通常耗时较长 |
| `parse_ms` | Docling 转换并导出 Markdown 的耗时 | PDF、DOCX、PPTX 的主要解析指标 |
| `parent_split_ms` | 创建父分片的纯文本切分耗时 | 通常应远小于解析和向量化 |
| `child_split_ms` | 创建全部子分片的纯文本切分耗时 | 与父分片数量和文本长度相关 |
| `embedding_ms` | 按父片批量生成子片向量的累计耗时 | 当前最可能的性能瓶颈 |
| `parent_db_flush_ms` | 创建父记录并取得主键的累计耗时 | 可发现数据库连接或写入变慢 |
| `document_chunked.total_ms` | 单个文件从转换到创建分片对象的总耗时 | 不包含最终事务提交 |
| `file_save_ms` | 将本次请求中的文件写入磁盘的累计耗时 | 判断磁盘写入是否异常 |
| `commit_ms` | 上传请求提交文档记录的耗时 | 不包含后台分片和向量的事务提交 |
| `document_upload_completed.total_ms` | 上传保存并排队的总耗时 | 可与 ApiPost 响应耗时对照，不代表后台完成 |
| `parent_count` / `child_count` | 实际创建的父、子分片数 | 用于计算吞吐量和排查分片异常 |
| `saved_count` / `failed_count` | 本次请求成功和被拒绝的文件数 | 应与接口响应一致 |

注意：`document_chunked.total_ms` 不包含后台最终事务提交，也不包含排队时间；后台最终提交耗时见 `document.final_commit`。首次 Docling 转换也可能触发延迟初始化，因此第一次 `parse_ms` 通常不能代表稳定速度。后台失败看 `document_processing_failed` 日志和文档状态，HTTP 202 本身不代表解析成功。

### 11.5 性能测试方法

需要区分三种情况，不能混在一起计算平均值：

1. **首次安装冷启动**：本机还没有相关模型缓存，包含模型下载时间，只记录一次；
2. **进程冷启动**：模型已经下载到磁盘，但 Uvicorn 刚启动，记录第一次上传；
3. **热启动**：不重启 Uvicorn，继续上传相同规模文件，代表日常处理速度。

推荐步骤：

1. 启动服务，上传一个小文件完成预热；
2. 保持服务运行，对每个正式测试文件连续测试 3 次；
3. 每次使用新的测试项目或明确记录新的 `document_id`，避免混淆数据库结果；
4. 记录 3 次热启动耗时，并计算平均值、最快值和最慢值；
5. 重启 Uvicorn，再测试一次进程冷启动；
6. 使用 ApiPost 总耗时减去服务端 `total_ms`，观察客户端和传输额外开销；
7. 测试期间不要同时运行大型下载、模型训练或其他高负载程序。

建议测试矩阵：

| 文件类型 | 小文件 | 中文件 | 大文件 |
| --- | --- | --- | --- |
| TXT | 约 10 KB | 约 500 KB | 约 5 MB |
| PDF | 约 5 页 | 约 50 页 | 约 200 页 |
| DOCX | 约 5 页 | 约 50 页 | 约 200 页 |
| PPTX | 约 10 页 | 约 50 页 | 约 150 页 |

“大文件”只在测试机器资源允许时执行。如果单次请求超过 ApiPost、反向代理或服务端超时时间，应记录实际超时位置，不要反复无限等待。

可以根据日志计算两个辅助指标：

```text
向量化吞吐量 = child_count / (embedding_ms / 1000)
整体分片吞吐量 = child_count / (document_chunked.total_ms / 1000)
```

单位均为“子分片/秒”。不同电脑的绝对速度不可直接横向比较，应在同一台电脑、同一模型缓存状态和同一份测试文件上比较代码版本。

### 11.6 资源、并发和稳定性测试

无需安装监控软件，先使用 Windows 任务管理器、macOS 活动监视器或 Linux 系统监控工具观察：

- Python 进程 CPU 使用率；
- 内存峰值；
- 如果使用 GPU，记录显存峰值和 GPU 利用率；
- 上传文件所在磁盘的读写占用；
- PostgreSQL 进程的 CPU 和内存变化。

资源数据至少在“小文件热启动”和“大文件处理”时各记录一次。

并发测试只需做基础验证：

1. 保持同一个 Uvicorn 进程运行；
2. 从两个 ApiPost 标签页尽量同时上传两个中文件；
3. 再尝试同时上传三个中文件；
4. 记录每个请求的耗时、是否成功、CPU/内存峰值和终端异常；
5. 查询数据库，确认没有缺失、重复或只写入一半的文档记录。

后台解析和向量化在线程池中执行，仍有较高计算和内存开销。同一次上传登记的后台任务按序执行；跨请求可能并发。本轮只建立基线，不要求高并发压测。每轮预热、重复和并发测试都要查询状态并等待后台完成，不能仅以收到 202 为完成依据。

### 11.7 本轮必须提交的测试结果

测试人员最终应提供：

- `git rev-parse --short HEAD` 的输出；
- 测试电脑的操作系统、CPU、内存以及是否使用 GPU；
- Python、PostgreSQL 版本；
- 每个测试文件的类型、大小、页数或大致字数；
- 首次安装冷启动、进程冷启动和热启动耗时；
- 上述全部埋点字段；
- CPU、内存和显存峰值；
- 父子分片数量及长度统计 SQL 结果；
- 唯一标记完整性检查结果；
- 人工分片质量评分；
- 异常测试和并发测试结果；
- 失败请求对应的完整日志堆栈；
- 最终结论：通过、有条件通过或不通过。

## 12. 检查分片结果

### 12.1 统计每份文档的分片数量和长度

```sql
SELECT
    d.id AS document_id,
    d.filename,
    COUNT(DISTINCT p.id) AS parent_count,
    COUNT(c.id) AS child_count,
    MIN(LENGTH(c.content)) AS min_child_length,
    MAX(LENGTH(c.content)) AS max_child_length,
    ROUND(AVG(LENGTH(c.content)), 2) AS avg_child_length
FROM documents d
LEFT JOIN document_parent p ON p.document_id = d.id
LEFT JOIN document_child c ON c.parent_id = p.id
GROUP BY d.id, d.filename
ORDER BY d.id;
```

### 12.2 按顺序查看父分片

```sql
SELECT id, chunk_index, LENGTH(content) AS content_length, content
FROM document_parent
WHERE document_id = 你的文档ID
ORDER BY chunk_index;
```

### 12.3 按顺序查看子分片

```sql
SELECT
    p.chunk_index AS parent_index,
    c.chunk_index AS child_index,
    LENGTH(c.content) AS content_length,
    c.content
FROM document_parent p
JOIN document_child c ON c.parent_id = p.id
WHERE p.document_id = 你的文档ID
ORDER BY p.chunk_index, c.chunk_index;
```

### 12.4 检查唯一标记是否全部保留

```sql
SELECT
    d.filename,
    BOOL_OR(p.content LIKE '%START-MARKER-2026%') AS has_start,
    BOOL_OR(p.content LIKE '%MIDDLE-MARKER-2026%') AS has_middle,
    BOOL_OR(p.content LIKE '%END-MARKER-2026%') AS has_end
FROM documents d
JOIN document_parent p ON p.document_id = d.id
GROUP BY d.id, d.filename;
```

三个字段都应为 `true`。

### 12.5 检查空分片和向量

```sql
SELECT COUNT(*) AS bad_child_count
FROM document_child
WHERE BTRIM(content) = '' OR embedding IS NULL;
```

预期结果为 `0`。数据库字段类型已经限制向量维度为 1024；如果模型返回错误维度，入库会失败。

## 13. 人工评价标准

逐份查看父、子分片，并按下面的项目打分。每项 1～5 分，5 分最好。

| 项目 | 检查内容 |
| --- | --- |
| 解析完整性 | 开头、中间、末尾的内容是否都保留，是否存在整段缺失 |
| 语义完整性 | 标题是否和正文相连，完整句子是否被严重切断 |
| 表格质量 | 表头、行列和值是否仍能看懂 |
| 分片粒度 | 是否出现大量过短碎片，单个分片是否包含过多无关内容 |
| 顺序正确性 | PDF 页、PPT 页和段落顺序是否正确 |
| 特殊内容 | 中英文、数字、公式、项目符号和专有名词是否保留 |

重点记录以下问题：

- 标题与其正文被拆开；
- 句子在分片边界处失去关键主语或条件；
- 表格被打散后无法理解；
- 页眉、页脚重复进入大量分片；
- 出现乱码、空白块或只有几个字的碎片；
- 文档内容顺序错乱；
- 原文存在但数据库中完全找不到。

## 14. 异常测试

至少执行以下场景：

| 场景 | 预期结果 |
| --- | --- |
| 不存在的 `project_id` | 返回 `404` |
| 空文件 | 文件进入 `failed_files` |
| 上传 `.jpg`、`.zip` 等不支持文件 | 文件进入 `failed_files` |
| 同时上传多个合法文件 | 每个文件都出现在 `saved_files` |
| 文档无法被 Docling 解析 | 上传可能已返回 202；后台分片事务回滚，文档标记 failed，原文件保留 |
| 同一文件上传两次 | 当前实现会创建两份文档记录，应记录为现有行为 |

## 15. 测试报告模板

将下面内容复制一份，为每个文件填写：

```text
测试日期：
测试人员：
Git 分支：填写实际分支
Git 提交号：
操作系统：
CPU：
内存：
GPU及显存（没有则填无）：
Python 版本：
PostgreSQL 版本：

文件名：
文件类型：
文件大小：
总页数/大致字数：
上传耗时：
测试类型：首次安装冷启动 / 进程冷启动 / 热启动
document_converter_initialized elapsed_ms：
embedding_model_loaded elapsed_ms：
parse_ms：
parent_split_ms：
child_split_ms：
embedding_ms：
parent_db_flush_ms：
document_chunked total_ms：
file_save_ms：
commit_ms：
document_upload_completed total_ms：
向量化吞吐量（子分片/秒）：
CPU峰值：
内存峰值：
GPU及显存峰值：
父分片数量：
子分片数量：
子分片最短/最长/平均长度：

解析完整性（1-5）：
语义完整性（1-5）：
表格质量（1-5）：
分片粒度（1-5）：
顺序正确性（1-5）：
特殊内容（1-5）：

发现的问题：
问题所在页码或原文位置：
对应 parent_index / child_index：
服务端错误日志：
并发测试结果：
异常测试结果：
总体评价：通过 / 有条件通过 / 不通过
```

获取当前提交号：

```powershell
git rev-parse --short HEAD
```

## 16. 当前限制

- 已实现单轮问答，返回 `answer`、`retrieved_contexts` 和 `sources`；尚未实现多轮对话及 Ragas 评测脚本。
- `chunk_size` 和 `chunk_overlap` 由 LangChain 文本分割器按字符计算，不是按模型 token 计算。
- 文档状态为 `queued` → `processing` → `completed`，后台异常时为 `failed`。
- 上传保存后返回 202，后台任务不是持久化任务队列；服务重启可能中断处理，不会自动恢复。
- 当前没有文档删除接口；重复测试会持续增加数据库记录和本地上传文件。
- 当前没有自动清理测试数据的功能，请为测试使用独立数据库。

问答人工测试与后续评测见 [问答功能测试指南](问答功能测试指南.md)。

## LangGraph 工作流

问答与文档处理已使用 LangGraph StateGraph 编排，接口保持兼容。节点、条件分支、运行命令和事务边界见 [LangGraph 流程说明](docs/langgraph-workflows.md)。节点耗时继续写入 `logs/performance.log`。
