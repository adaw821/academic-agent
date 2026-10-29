# 后端补齐验证记录

日期：2026-10-11。功能与接口说明见 [backend-completion.md](backend-completion.md)。前端文件未修改。

## 数据库

本地 PostgreSQL 从 `b83d602a194f` 升级至 `d91e73b08422` 成功。
新增 `workflow_runs`、`documents.parsed_text`、`message.response_data`，没有删除原有数据。
独立迁移测试验证了升级、降级及旧文档/消息数据保留。

## 新增测试

- `tests/test_backend_completion.py`：最终 14 项在独立 PostgreSQL schema 中全部通过，测试 schema 已清理。
- 同一测试集的 13 项版本也在临时 SQLite 中全部通过；最后新增的删除提交失败恢复测试已在 PostgreSQL 验证。
- `tests/test_backend_schema.py`：1 项通过。

覆盖真实 Cookie 身份校验、跨用户隔离、历史分页与引用恢复、正文和原文件读取、路径边界、
文档及分片删除、删除事务失败时文件恢复、遗留文件清理、学校会话更新与解除、课程与课表查询、
过期租约恢复、并发任务领取、旧 worker 提交拦截、失败后的消息和分片回滚，以及 Docling 图片像素持久化。
学校网络与大模型推理使用模拟响应，数据库事务、HTTP 路由、文件操作和 Docling JSON 序列化使用真实实现。

## 原有回归

以下文件通过（按文件独立进程运行，避免旧测试的全局模块替换相互干扰）：

- 用户数据相关：`test_assignment_queries.py`、`test_course_sync.py`、`test_ispace.py`、`test_school_schema.py`、`test_timetable_upload.py`。
- 问答相关：`test_context_management.py`、`test_document_scope.py`、`test_selection_qa.py`、`test_rerank_routes.py`、`test_batch_qa.py`。
- 文档图片相关：`test_docling_images.py`、`test_image_enrichment.py`、`test_image_extraction.py`、`test_image_ocr.py`、`test_pdf_chunking.py`、`test_ppt_chunking.py`、`test_picture_api.py`、`test_routed_images.py`。
- 工程相关：`test_performance.py`、`test_startup.py`、`test_workflow_graphs.py`。

`test_image_extraction.py` 中旧的空描述测试原来没有进入 VLM 分支：未知类型、无 OCR 文本的图片按现行规则会跳过。
本次为该测试图片补上高置信度图表分类，使其实际验证 VLM 空响应报错；没有修改图片分流业务规则。修正后 14 项全部通过。

## 已确认的既有失败

`test_retrieval_percentage.py` 中 `ChildRerankTests` 仍按旧的双语加权分数、0.6 阈值及回退规则断言，
与当前分路重排、0.3 阈值实现不一致。测试报告显示 10 个失败、5 个错误（包含子测试），不能宣称整个测试库全绿。

验证方式：从 Git HEAD 读取修改前的 `rerank_results` 和 `chunk_filter`，与当前 AST 比较一致；
再将 HEAD 函数装入隔离测试进程，运行该类 16 项测试，复现同样的 10 个失败、5 个错误。
本次没有为了通过旧测试而回退检索算法。
详细记录保存在 `out/backend-baseline-retrieval.json` 和 `out/backend-regression-results.json`。

服务生命周期新增 1 项测试，验证恢复线程阻塞时仍可退出；`test_startup.py` 最终共 3 项通过。

## 此前隔离验证的运行边界

隔离测试未使用真实学校凭据，也未通过真实大模型进行完整问答质量验收。现有服务的恢复扫描会继续此前积压的文档任务。
核对期间本机 8000 端口的现有服务请求超时，因此新增功能的验证依据是隔离 HTTP 集成测试与真实 PostgreSQL，
不把既有服务进程视为已完成在线验收。

## 2026-10-25：真实 PostgreSQL 与模型联调

前端未改动。本轮使用已配置的本机 PostgreSQL 与智谱模型，所有测试数据放在随机命名的隔离 schema，
退出后删除该测试 schema。没有启动 API lifespan 或后台恢复扫描，没有迁移实际应用的 `public` schema。

### PostgreSQL 回归

设置 `BACKEND_TEST_POSTGRES=1`，分别运行以下文件，共 **41 项通过**：

| 文件 | 测试数 |
| --- | ---: |
| `tests/test_backend_completion.py` | 14 |
| `tests/test_school_qa.py` | 9 |
| `tests/test_school_resource_qa.py` | 6 |
| `tests/test_school_reminder_qa.py` | 6 |
| `tests/test_school_timetable_qa.py` | 6 |

这些回归使用真实 PostgreSQL，模型输出仍为模拟。真实模型调用由下面的独立验收脚本完成。

### 真实模型与文档链路

新增 `scripts/validate_backend_live.py`，使用应用已有的模型配置和真实 HTTP 路由。通过依赖覆盖将数据库、
文件目录和任务执行器指向隔离环境；不会替换模型、向量、检索或重排结果，也不读取学校凭据。

已验证：

- `glm-5.3` 面对四条自然语言问题，分别选择作业、附件、提醒、课表工具，并基于真实查询结果返回非空回答。
  每类只测一个案例，尚不代表复杂追问或大规模意图准确率验收。
- 在真实 PostgreSQL 上执行提醒表的升级 → 降级 → 升级；两个连接同时生成提醒，返回创建数 `[0, 1]`，无重复记录。
- 从临时学校缓存导入一份合成 TXT，经真实解析及 `BAAI/bge-m3` 向量化，产生 1 个可检索分片。
- 持久化问答任务调用实际检索、`BAAI/bge-reranker-v2-m3` 重排和真实模型，正确回答验收标识与提交文件名，返回 1 条文档来源。
- 测试 schema 均已清理。模型权重使用现有缓存，脚本设置离线权重加载，不下载新权重。

首次运行的文档计数检查误用了不存在的 `Child.document_id`，出现 `AttributeError`；已改为通过父分片关联文档，
补跑文档链路全部通过。此处修正的是验收脚本，并非应用业务代码。原始报告保留该失败记录：

- `.local/backend-live-validation.json`：四类真实模型工具测试通过，首次文档检查失败。
- `.local/backend-live-document.json`：修正后的文档链路、迁移及并发提醒检查通过。

报告只记录状态、数量、耗时和异常类型，不包含密钥、Cookie、业务内容或完整模型回答。`.local/` 不入 Git。

复跑前配置 `DATABASE_URL` 和 `ZHIPU_API_KEY`，并确保本地已缓存向量、重排及 tokenizer 模型：

```powershell
# 四类工具 + 文档链路；会产生真实模型调用费用。
.\agent\Scripts\python.exe scripts/validate_backend_live.py --document

# 只重跑迁移、并发提醒和文档链路。
.\agent\Scripts\python.exe scripts/validate_backend_live.py --document --skip-tools --report .local/backend-live-document.json
```

### 仍未覆盖

当前账号已配置的学校会话返回 `SchoolLoginRequired`；用户选择暂不更新，所以暂停真实学校采集与下载。
旧版独立会话文件未使用。此次导入的是合成的本地 TXT 缓存，不能称作“真实学校附件全链路通过”。
PDF/Office 转换、长文档和真实学校内容上的问答质量仍需另测。

2026-10-25 核对时，实际应用数据库停留在 `d91e73b08422`，尚未应用提醒迁移。
当日也没有启用常驻定时任务或验收已有服务进程，不替代此前记录的全量测试既有失败。

## 2026-10-26：应用库提醒迁移

已在当前配置的本机 `academic_agent` 数据库执行：

```powershell
.\agent\Scripts\python.exe -m alembic upgrade e6a219db503c
```

迁移前版本为 `d91e73b08422`，迁移后为 `e6a219db503c`。验证结果：

- `public.deadline_reminders` 表已创建，`(assignment_id, due_at)` 唯一约束及关联作业的级联删除外键均正确。
- 迁移前后核对用户、项目、文档、课程、作业、资源、会话、消息、工作流共 9 张原有业务表，记录数未变。
- 使用实际数据库调用当前账号的提醒查询服务，返回 `total=0/unread_count=0`；按现有时间与新鲜度规则计算，
  可生成提醒的作业数为 0。这不表示学校没有待交作业。
- 本次未生成提醒、未访问学校、未启动常驻任务。
- `http://127.0.0.1:8000/openapi.json` 连接失败（`ConnectError`），因此不宣称在线服务接口已经验收。

本地前后检查记录分别保存在 `.local/reminder-migration-before.json` 与 `.local/reminder-migration-after.json`。
当时尚需启动或更新实际服务进程，再验证在线接口；学校采集与下载保持暂停，待用户更新当前账号会话后继续。

### 同日服务启动后的在线验收

用户启动后端后，已通过真实 HTTP 连接 `127.0.0.1:8000` 验证：

- `/openapi.json` 返回 200；提醒查询/已读、问答、持久化问答、附件导入和课表路由均已加载，
  `SearchRequest` 包含 `use_school_tools`。
- 未登录访问当前用户、提醒、作业和课表接口均返回 401。
- 通过公开注册接口建立一次性验收账号，注册返回 201；登录、当前用户查询均返回 200。
- 登录后提醒、作业、课表、课程和项目列表均返回 200，且新账号得到空列表，没有读到已有账号的数据。
- 标记不存在的提醒返回 404；退出登录返回 200，退出后读取当前用户返回 401。
- 已注销并删除本次创建的临时账号与会话，没有访问学校或创建业务项目、文档。

检查记录保存在 `.local/backend-online-validation.json`。这次验证的是运行中服务的接口加载、鉴权和只读查询；
真实模型与文档链路的依据仍为上节隔离验收，不把空账号列表检查当成完整业务数据验收。

## 2026-10-26：更新会话后的真实学校附件验收

用户更新当前账号会话后，学校访问恢复。完整发现 15 门课程，并对当前课程执行真实同步，
本次快照包含 9 项作业、53 条资源。此处不代表 15 门课程均已同步，也不把历史资源记录计为本次发现。

选取真实 `Requirement.pdf`（344,471 字节、2 页扫描 PDF）完成下载、导入及持久化处理。
验收项目为 `School live validation 2026-10-26`（项目 10），文档 36；项目和文件保留供复查。

首次解析失败暴露了一个实际缺口：PDF 转换禁用 OCR，扫描页也没有进入后续图片识别，导出的正文为空。
修复仅在 PDF 转换配置中启用现有 RapidOCR，保留文字层及图片分流，未新增依赖或修改前端。
使用 `restart=true` 清除旧转换检查点后，由自动重载的后端重新处理同一文档，结果如下：

- 文档任务完成，正文 3,070 字符，4 个父片、7 个子片，保留第 1、2 页信息；本次处理约 205 秒。
- 对照渲染后的原页核对提交模板、两种文件名和评分规则；比较时解码 Markdown 转义与 HTML 实体，未改写存储正文。
- 用户明确授权仅此附件调用项目配置的智谱服务后，通过现有持久化问答入口提交问题，限定 `doc_ids=[36]`。
- 实际检索、重排及 GLM 回答完成；三项回答事实检查通过，3 条引用均来自文档 36，命中的子片页码均为第 1 页。
- 问答任务 `24247bb9f295424499f2e8997725d207`，会话 58，回答及来源已持久化。
- 相关回归：`tests/test_image_extraction.py` 14 项、`tests/test_pdf_chunking.py` 15 项全部通过。

本地证据为 `.local/school-live-validation.json` 和 `.local/school-live-answer.json`，不入 Git。
这次验收证明一份真实学校扫描附件可以完成“采集 → 下载 → 解析入库 → 带引用问答”。
仍有限制：OCR 在另一处部门描述句子中存在乱码，不能据此声称全文识别准确；更多课程、附件格式、
大文档及批量同步仍需分别验证。本次没有启用定时任务，也没有修改前端。

## 2026-10-26：对话同步单门课程

新增默认关闭的 `allow_school_sync` 开关，和 `use_school_tools` 同时开启后开放 `sync_school_course`。
复用现有课程同步接口，成功回答直接来自已提交的同步结果。接口示例和重试边界见
[backend-completion.md](backend-completion.md#对话同步单门课程2026-10-26)。

本轮设置 `BACKEND_TEST_POSTGRES=1`，在真实 PostgreSQL 的独立临时 schema 中运行：

- `tests/test_school_sync_qa.py`：10 项通过，覆盖默认只读、课程所有权、名称歧义、非法参数、
  未登录/项目越权、文档范围优先、学校会话失效、未关联课程，以及保存消息失败后的恢复。
- 既有学校工具回归：`test_school_qa.py` 9 项、`test_school_resource_qa.py` 6 项、
  `test_school_reminder_qa.py` 6 项、`test_school_timetable_qa.py` 6 项，均通过。

合计 37 项通过。学校采集及模型工具选择使用模拟响应，鉴权、问答图、同步入库与检查点使用实际实现。
本轮未通过新对话工具访问真实学校或调用真实模型，不能把上节附件验收当成本次新工具的自然语言实测。
前端和数据库结构未改动。
