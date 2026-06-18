# BNBU 学术助手（Academic Agent）

> 基于 RAG 的多模态 Agent 知识助手 · 面向 BNBU 学生的一站式学业辅助桌面平台

---

## 项目简介

**BNBU 学术助手**是一款专为 BNBU（北师港浸大）学生设计的 AI 智能桌面应用，旨在解决高校学生在日常学习中面临的痛点：

- 学校各类网页众多、信息分散，新生难以快速上手
- 课程评分标准（Rubric / Syllabus）不透明，难以提前规划学期
- 作业平台分散（iSpace / JupyterHub / AutoLab），容易遗漏 DDL
- 企业微信邮件杂乱，社团、学业、通知邮件混杂
- 复习阶段难以快速定位知识点，学习效率低下

本系统整合了**课表 OCR 识别、DDL 分钟级提醒、GPA 计算（sGPA/cGPA）、课程评价、邮件智能摘要、自定义作业平台管理、AI 对话**等核心功能，采用 **Spring Boot（业务后端）+ Python（AI/OCR 服务）+ Electron（桌面前端）** 三层架构。

---

## 目录

- [技术栈](#技术栈)
- [已实现功能](#已实现功能)
- [规划中功能](#规划中功能)
- [系统架构](#系统架构)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [API 文档](#api-文档)
- [数据库说明](#数据库说明)
- [开发说明](#开发说明)

---

## 技术栈

| 层次 | 技术 |
|------|------|
| 桌面前端 | Electron · Vue 3 · HTML / CSS / JavaScript |
| 业务后端 | Spring Boot 4.0 · MyBatis-Plus · JPA · MySQL |
| AI / OCR 服务 | Python · 百度 OCR API · FastAPI（规划中） |
| 向量数据库 | ChromaDB（规划中） |
| 缓存 / 队列 | Redis · Celery（规划中） |
| 工具库 | Lombok · RestTemplate |
| 构建工具 | Maven |

---

## 已实现功能

### 1. 课表 OCR 识别

- 用户上传 UIC Sections 截图，后端调用百度 OCR API 识别文字
- 自研状态机解析器提取课程信息：课程名、课程代码、教师、上课时间、教室
- 支持多时段课程（同一课程有多个上课时段）自动合并
- 支持合并行和逐行两种 OCR 输出格式的智能识别
- 解析结果可视化展示，用户可一键确认导入
- OCR 服务端口：`http://127.0.0.1:8766`

### 2. DDL 分钟级提醒

- 支持精确到**小时分钟**的截止时间（yyyy-MM-dd HH:mm），兼容旧版仅日期格式
- 三档提醒机制：截止前 30 分钟 / 10 分钟 / 5 分钟弹窗提醒
- 五级预警色彩：< 1 小时「紧急」红色脉冲 / < 24 小时「警告」橙色 / < 3 天「注意」黄色 / 正常绿色 / 已过期灰色
- **< 1 小时紧急悬浮窗**：白底红字持续悬浮，实时显示剩余分钟数
- 侧栏数字角标实时显示紧急 / 警告 DDL 数量
- 支持标记完成、删除操作
- 接口：`GET /api/ddl/list`、`POST /api/ddl/add`、`PUT /api/ddl/done/{id}`、`DELETE /api/ddl/{id}`、`GET /api/ddl/urgent`

### 3. 自定义平台 & DDL 抓取

- 用户可添加自定义作业提交平台（名称 + URL + 描述）
- 一键「提取 DDL」：根据平台类型自动生成示例截止日期
- BNBU 常用入口预设：iSpace、UIC MIS（选课）、学习资源中心 LRC、WebVPN、图书馆
- 接口：`GET /api/platform/list`、`POST /api/platform/add`、`DELETE /api/platform/{id}`、`POST /api/scrape/ddl`

### 4. GPA 计算器（sGPA / cGPA）

- 录入各课程名称、学分、百分制成绩，系统自动换算绩点并计算加权 GPA（4.0 制）
- 支持**学期 GPA（sGPA）**和**累计 GPA（cGPA）**双指标展示
- 支持目标 GPA 设定，系统自动计算差距和所需后续绩点
- 智能学习建议：根据当前 GPA 生成「优秀 / 良好 / 需改进」三档建议
- 成绩情景推演：展示不同后续成绩下的 GPA 变化
- 右侧滑出面板录入成绩，底部收缩箭头收起
- 绩点换算：≥90→4.0 / 85-89→3.7 / 80-84→3.3 / 75-79→3.0 / 70-74→2.7 / 65-69→2.3 / 60-64→2.0 / <60→0.0
- 接口：`GET /api/gpa/calc`、`POST /api/gpa/grade`、`DELETE /api/gpa/grade/{id}`

### 5. 课程评价

- 学生可为选修课程留下 1-5 星评分及文字评价
- 支持查看、删除已有评价，按时间倒序展示
- 接口：`GET /api/review/list`、`POST /api/review/add`、`DELETE /api/review/{id}`

### 6. 邮件智能摘要 & 分类

- 用户粘贴企业微信邮件（发件人 + 主题 + 正文），后端自动分类与摘要
- 分类：`academic`（学业）/ `club`（社团）/ `admin`（通知/行政）/ `other`
- 重要邮件（学业 + 行政类）自动标红
- AI 后端可用时调用 Claude 分类；不可用时使用关键词规则降级处理
- 接口：`GET /api/email/list`、`POST /api/email/process`、`DELETE /api/email/{id}`

### 7. AI 对话助手

- 集成聊天式 AI 对话界面，支持自然语言提问
- 支持拖拽上传文件（PDF、图片等）作为对话上下文
- 快捷提问入口，常用问题一键发送
- 打字机效果逐字输出回复

### 8. 主页聚合视图

- 默认主页整合**课程表 + 快捷入口 + 即将到期 DDL + 录入成绩**四合一
- 一键直达 iSpace、MIS、LRC、WebVPN 等 BNBU 常用网站
- DDL 区域仅展示最紧急的前 5 条，可一键跳转完整列表
- 点击「录入成绩」自动切换到 GPA 页面并打开右侧面板

### 9. Electron 桌面应用

- 所有功能封装为 Electron 桌面窗口（1200×800），本地运行，无需浏览器
- 使用 Vue 3 构建响应式布局：左侧导航 + 中间主内容区 + 右侧滑出面板
- 后端不可用时自动降级为本地 Mock 数据，保证界面正常展示
- 内置周期性轮询，紧急 DDL 桌面通知 + 红色悬浮窗
- 上课提醒弹窗：课前弹出课程名称、教室、上课时间
- 一键启动脚本 `start.bat`：自动启动 OCR 服务 + Electron 应用

---

## 规划中功能

> 以下功能已在需求分析与项目方案中明确，尚在开发中：

### 课程资料上传与知识库构建（RAG）

- 学生上传课程 PDF、讲义、Syllabus 等文档
- 系统异步完成：文本提取 → 分块（含重叠）→ Embedding → 写入 ChromaDB
- 进度通过 WebSocket 实时推送

### RAG 智能问答

- 基于个人知识库进行混合检索（BM25 关键词 + 向量语义）
- RRF 融合 + 重排模型精排，提升召回率
- 调用 Claude 生成带引用来源的回答（文档名 + 片段定位）
- SSE 流式逐字输出，低首字延迟

### Agent 工具调用

- 将检索、计算、DDL 查询等封装为 Function Calling 工具
- Agent 支持多步规划，自主决定工具调用顺序

### DDL 自动抓取

- 自动爬取校历与各作业平台（iSpace / JupyterHub / AutoLab）DDL
- 弹窗提醒选课 DDL、转专业 DDL 等关键时间节点

### 学期计划 & 复习规划

- 学期初安排总学期计划，与用户协商设置阶段性目标
- 学期末 / 期中生成复习计划，根据考纲定位知识点在 PPT 中的位置
- 内置复习进度追踪，完成阶段性目标后给予正向反馈

### 音视频多模态摄取

- 上传课程录音 / 视频，通过 ASR（Whisper / Claude）转写为带时间戳文本
- 支持「第 X 分钟讲了什么」类问答
- 大文件分片上传 + 断点续传

### LLM 工程化

- 令牌桶限流（Redis）+ 指数退避重试
- 语义缓存：语义相近问题直接命中缓存，节省 Token
- Token 成本统计，按用户 / 功能维度记录
- 模型降级策略（Claude 不可用时回退）

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                 Electron 桌面前端（本地）                       │
│    Vue 3 · 主页聚合 · 紧急悬浮窗 · 轮询通知 · 右侧面板           │
└──────────────────┬───────────────┬──────────────┬────────────┘
                   │               │              │
          ┌────────▼────────┐      │     ┌────────▼──────────────┐
          │  Spring Boot     │      │     │  Python OCR/AI 服务    │
          │  业务后端 :8080   │      │     │  百度 OCR :8766        │
          │  ScheduleCtrl    │      │     │  parse_sections()      │
          │  DdlCtrl         │      │     │  课程表智能解析          │
          │  GpaCtrl         │      │     │  FastAPI :5000（规划中）│
          │  ReviewCtrl      │      │     └───────────────────────┘
          │  EmailCtrl       │      │
          │  PlatformCtrl    │      │
          │  ScrapeCtrl      │      │
          └────────┬─────────┘      │
                   │                │
          ┌────────▼────────┐      │
          │     MySQL       │      │
          │  schedule_item  │      │
          │  ddl_item       │      │
          │  course_grade   │      │
          │  course_review  │      │
          │  email_summary  │      │
          │  submission_    │      │
          │    platform     │      │
          └─────────────────┘      │
```

---

## 项目结构

```
academic-agent-master/
├── pom.xml                                          # Maven 依赖配置
├── README.md                                        # 中文说明
├── README_EN.md                                     # English README
├── electron-app/
│   └── bnbu-assistant/
│       ├── main.js                                  # Electron 主进程
│       ├── launcher.js                              # 启动器（解决环境变量冲突）
│       ├── index.html                               # Vue 3 前端（所有页面合一）
│       ├── ocr_server.py                            # 百度 OCR 识别服务 + 课表解析器
│       ├── ocr_schedule.py                          # 课表解析辅助模块
│       ├── start.bat                                # 一键启动脚本
│       ├── start-browser.bat                        # 浏览器调试启动
│       ├── vendor/                                  # 本地前端依赖（离线可用）
│       │   ├── vue.global.prod.js                   #   Vue 3 生产版
│       │   ├── tailwind.css / tailwind.js           #   Tailwind CSS
│       │   ├── material-symbols.css / .woff2        #   Google Material Symbols 图标
│       │   └── tesseract/                          #   Tesseract.js WASM（备用 OCR）
│       └── assets/                                  # 应用图标等静态资源
├── src/
│   ├── main.py                                      # FastAPI AI 后端入口（规划中）
│   ├── prompt.py                                    # Prompt 模板（规划中）
│   └── main/
│       ├── java/org/example/academicagentbackend/
│       │   ├── AcademicAgentBackendApplication.java
│       │   ├── common/
│       │   │   └── Result.java                      # 统一响应包装 {code, message, data}
│       │   ├── controller/
│       │   │   └── ScheduleController.java         # POST /api/schedule/send
│       │   ├── mapper/
│       │   │   └── ScheduleItemMapper.java
│       │   ├── model/
│       │   │   ├── ScheduleItemEntity.java
│       │   │   ├── CourseInfoEntity.java
│       │   │   └── GradingSchemaEntity.java
│       │   └── service/
│       │       ├── ScheduleService.java
│       │       └── impl/ScheduleServiceImpl.java
│       └── resources/
│           └── application.yml
```

---

## 快速开始

> ⚠️ **注意：本项目目前仅支持本地运行，尚未部署至任何线上环境。**
> ⚠️ **推荐使用 Node.js v20 LTS 或 v22 LTS**。Node.js v24 等过新版本可能导致 Electron 28 二进制包下载失败。

### 前置要求

| 工具 | 版本要求 | 用途 |
|------|---------|------|
| Node.js | 20 LTS / 22 LTS（推荐） | 运行 Electron 前端 |
| Java | 17+ | 运行 Spring Boot 后端 |
| Maven | 3.6+ | 构建 Java 项目 |
| MySQL | 8.0+ | 数据持久化 |
| Python | 3.10+ | OCR 识别服务 |

### 1. 克隆项目

```bash
git clone <repository-url>
cd academic-agent-master
```

### 2. 初始化数据库

登录 MySQL 并执行以下 SQL：

```sql
CREATE DATABASE bnbu_assistant DEFAULT CHARACTER SET utf8mb4;
USE bnbu_assistant;

-- 课表
CREATE TABLE schedule_item (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  user_id     BIGINT,
  course_name VARCHAR(100),
  course_id   VARCHAR(50),
  day_of_week INT,
  start_time  VARCHAR(20),
  end_time    VARCHAR(20),
  location    VARCHAR(100)
);

-- DDL 任务（分钟精度）
CREATE TABLE ddl_item (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id       BIGINT,
  title         VARCHAR(200),
  due_date      VARCHAR(20),        -- yyyy-MM-dd HH:mm
  source        VARCHAR(20) DEFAULT 'user',
  done          INT DEFAULT 0,
  platform_name VARCHAR(100),
  course_name   VARCHAR(100)
);

-- 课程成绩（GPA 计算用）
CREATE TABLE course_grade (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id     BIGINT,
  course_name VARCHAR(100),
  credits     DOUBLE,
  score       DOUBLE,
  grade_point DOUBLE,
  semester    VARCHAR(30)
);

-- 课程评价
CREATE TABLE course_review (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id     BIGINT,
  course_name VARCHAR(100),
  rating      INT,
  comment     TEXT,
  review_date VARCHAR(20)
);

-- 邮件摘要
CREATE TABLE email_summary (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id       BIGINT,
  sender        VARCHAR(200),
  subject       VARCHAR(300),
  raw_content   TEXT,
  category      VARCHAR(20),
  summary       VARCHAR(500),
  important     INT DEFAULT 0,
  received_date VARCHAR(20)
);

-- 作业提交平台（用户自定义）
CREATE TABLE submission_platform (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id       BIGINT,
  platform_name VARCHAR(100),
  platform_url  VARCHAR(500),
  description   VARCHAR(200),
  icon          VARCHAR(50) DEFAULT 'fas fa-graduation-cap',
  sort_order    INT DEFAULT 99
);
```

### 3. 配置数据库连接

编辑 `src/main/resources/application.yml`：

```yaml
spring:
  datasource:
    url: jdbc:mysql://localhost:3306/bnbu_assistant?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai
    username: root
    password: your_password
```

### 4. 安装前端依赖

```bash
cd electron-app/bnbu-assistant
npm install
```

> ⚠️ 如果 `npm install` 后无法启动，报 `Electron failed to install correctly`，说明 Electron 二进制包（约 100MB）下载失败。请按下方 [🛠 Electron 下载失败的处理方案](#-electron-下载失败的处理方案) 操作。

### 5. 一键启动（推荐）

```bash
cd electron-app/bnbu-assistant
start.bat
```

`start.bat` 会自动：
1. 清理旧 OCR 服务进程
2. 启动百度 OCR 识别服务（端口 8766）
3. 启动 Electron 桌面应用

### 6. 手动分步启动

```bash
# 终端 1：启动 Spring Boot 后端
mvn spring-boot:run

# 终端 2：启动 OCR 服务
cd electron-app/bnbu-assistant
python ocr_server.py

# 终端 3：启动 Electron 前端
cd electron-app/bnbu-assistant
npx electron main.js
```

### 完整启动顺序

```
1. 启动 MySQL 数据库
2. mvn spring-boot:run                                        （Spring Boot :8080）
3. cd electron-app/bnbu-assistant && python ocr_server.py     （OCR 服务 :8766）
4. cd electron-app/bnbu-assistant && start.bat                （一键启动 2+3）
```

> 💡 前端有完整的降级策略：即使 Spring Boot 和 OCR 服务均未启动，Electron 前端也能用本地 Mock 数据正常演示所有界面。

---

### 🛠 Electron 下载失败的处理方案

`npm install` 过程中 Electron 的二进制包（约 100MB）可能因网络问题下载失败。**请按以下顺序尝试，优先使用前面的方案。**

#### 方案一：重试安装（最简单）

```bash
cd electron-app/bnbu-assistant
rmdir /s /q node_modules
npm install
```

如果第一次失败，在**同一个命令行窗口**中再执行一次 `npm install`——第二次通常能成功。

#### 方案二：使用国内镜像加速

```bash
cd electron-app/bnbu-assistant
set ELECTRON_MIRROR=https://registry.npmmirror.com/-/binary/electron/
npm install
```

#### 方案三：手动下载 Electron 二进制（最终兜底方案）

如果以上方案均失败，按以下步骤手动操作：

```bash
cd electron-app/bnbu-assistant

# 1. 先安装 npm 包（跳过二进制下载）
npm install electron --ignore-scripts

# 2. 手动下载 Electron 28.0.0（约 100MB，需等待下载完成）
powershell -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://npmmirror.com/mirrors/electron/v28.0.0/electron-v28.0.0-win32-x64.zip' -OutFile 'node_modules\electron\dist\electron.zip'"

# 3. 解压
powershell -ExecutionPolicy Bypass -Command "Expand-Archive -Path 'node_modules\electron\dist\electron.zip' -DestinationPath 'node_modules\electron\dist' -Force"

# 4. 验证：看到 electron.exe 即表示安装成功
dir node_modules\electron\dist\electron.exe
```

验证通过后，双击 `start.bat` 即可启动应用。

---

## API 文档

所有接口均返回统一格式：

```json
{ "code": 200, "message": "操作成功", "data": <T> }
```

### 课表解析

| 接口 | 说明 |
|------|------|
| `POST /api/schedule/send` | 上传课表图片（multipart/form-data，字段 `file`）|

### DDL 提醒

| 接口 | 说明 |
|------|------|
| `GET /api/ddl/list` | 查询所有 DDL（含 minutesLeft / hoursLeft / displayText / urgencyLevel） |
| `GET /api/ddl/urgent` | 仅查询 < 1 小时的紧急 DDL |
| `POST /api/ddl/add` | 新增 DDL，body: `{ title, dueDate("yyyy-MM-dd HH:mm"), source?, platformName?, courseName? }` |
| `PUT /api/ddl/done/{id}` | 标记完成 |
| `DELETE /api/ddl/{id}` | 删除 |

### GPA 计算

| 接口 | 说明 |
|------|------|
| `GET /api/gpa/calc` | 计算当前用户加权 GPA，返回绩点、学分、建议 |
| `POST /api/gpa/grade` | 录入成绩，body: `{ courseName, credits, score, semester }` |
| `DELETE /api/gpa/grade/{id}` | 删除一门课成绩 |

### 课程评价

| 接口 | 说明 |
|------|------|
| `GET /api/review/list` | 查询评价列表 |
| `POST /api/review/add` | 新增评价，body: `{ courseName, rating, comment }` |
| `DELETE /api/review/{id}` | 删除评价 |

### 邮件摘要

| 接口 | 说明 |
|------|------|
| `GET /api/email/list?category=` | 查询邮件列表（可按分类筛选）|
| `POST /api/email/process` | AI 处理邮件，body: `{ sender, subject, body }` |
| `DELETE /api/email/{id}` | 删除 |

### 平台管理

| 接口 | 说明 |
|------|------|
| `GET /api/platform/list` | 查询自定义平台列表 |
| `POST /api/platform/add` | 添加平台，body: `{ platformName, platformUrl, description?, icon? }` |
| `DELETE /api/platform/{id}` | 删除平台 |

### DDL 抓取

| 接口 | 说明 |
|------|------|
| `POST /api/scrape/ddl` | 从指定平台抓取 DDL，body: `{ platformName, platformUrl }`，返回 saved + items |

### OCR 识别服务

| 接口 | 说明 |
|------|------|
| `POST http://127.0.0.1:8766/ocr` | 上传图片进行 OCR 识别（multipart/form-data，字段 `image`）|
| `POST http://127.0.0.1:8766/ocr/sections` | 上传 Sections 截图识别课表 |

---

## 数据库说明

### `schedule_item` — 课表条目

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT | 主键，自增 |
| user_id | BIGINT | 用户 ID |
| course_name | VARCHAR | 课程名称 |
| course_id | VARCHAR | 课程编号 |
| day_of_week | INT | 星期（1-7） |
| start_time | VARCHAR | 上课开始时间 |
| end_time | VARCHAR | 上课结束时间 |
| location | VARCHAR | 上课地点 |

### `ddl_item` — DDL 任务（分钟精度）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT | 主键 |
| user_id | BIGINT | 用户 ID |
| title | VARCHAR | 任务标题 |
| due_date | VARCHAR | 截止时间 yyyy-MM-dd HH:mm |
| source | VARCHAR | user / calendar / platform / scrape |
| done | INT | 0=未完成 1=已完成 |
| platform_name | VARCHAR | 来源平台（平台抓取时记录） |
| course_name | VARCHAR | 关联课程名称 |

### `course_grade` — 课程成绩

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT | 主键 |
| user_id | BIGINT | 用户 ID |
| course_name | VARCHAR | 课程名称 |
| credits | DOUBLE | 学分 |
| score | DOUBLE | 百分制成绩 |
| grade_point | DOUBLE | 绩点（系统计算） |
| semester | VARCHAR | 学年学期 |

### `course_review` — 课程评价

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT | 主键 |
| user_id | BIGINT | 用户 ID |
| course_name | VARCHAR | 课程名称 |
| rating | INT | 1-5 分 |
| comment | TEXT | 评价文字 |
| review_date | VARCHAR | 评价日期 |

### `email_summary` — 邮件摘要

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT | 主键 |
| user_id | BIGINT | 用户 ID |
| sender | VARCHAR | 发件人 |
| subject | VARCHAR | 邮件主题 |
| raw_content | TEXT | 原始正文（截断 2000 字） |
| category | VARCHAR | academic / club / admin / other |
| summary | VARCHAR | AI 生成的一句话摘要 |
| important | INT | 0=普通 1=重要 |
| received_date | VARCHAR | 收到日期 |

### `submission_platform` — 自定义作业平台

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT | 主键 |
| user_id | BIGINT | 用户 ID |
| platform_name | VARCHAR | 平台名称 |
| platform_url | VARCHAR | 平台 URL |
| description | VARCHAR | 描述 |
| icon | VARCHAR | FontAwesome 图标类名 |
| sort_order | INT | 排序权重 |

---

## 开发说明

- **本地运行专用**：当前版本未配置任何线上部署，所有服务均在本地启动
- **双后端架构**：Java 负责业务逻辑与数据持久化；Python 负责 OCR 识别与 AI 编排，两者通过 HTTP 通信
- **统一响应格式**：`Result<T>` 包装，`code=200` 成功，`code=500` 失败
- **MyBatis-Plus**：所有 Mapper 继承 `BaseMapper`，提供开箱即用的 CRUD
- **前端降级策略**：所有接口调用包裹在 try/catch 中，后端不可用时自动使用本地 Mock 数据
- **当前用户**：暂时硬编码 `userId=1`，后续版本将实现用户登录系统
- **DDL 分钟精度**：支持 yyyy-MM-dd HH:mm 格式，三档提醒（30min / 10min / 5min），< 1 小时触发悬浮窗
- **GPA 双指标**：同时展示学期 GPA（sGPA）和累计 GPA（cGPA），支持目标追踪
- **OCR 智能解析**：自研状态机解析器，支持百度 OCR 的合并行和逐行两种输出格式
- **平台管理**：用户可手动添加作业提交平台，一键抓取 DDL（当前为模拟模式，真实实现需集成 Puppeteer）
- **CORS**：所有 Controller 已添加 `@CrossOrigin(origins = "*")`，支持 Electron 跨域访问
- **一键启动**：`start.bat` 自动清理旧进程 → 启动 OCR 服务 → 启动 Electron

---

## 开源协议

本项目为毕业设计作品，仅供学习交流使用。

---

> 更新时间：2026-06-18
