# BNBU Academic Agent — Intelligent Learning Assistant

> A RAG-based Multimodal Agent Knowledge Assistant · All-in-one Academic Support Platform for BNBU Students

---

## Overview

**BNBU Academic Agent** is an AI-powered desktop application designed for BNBU (BNU-HKBU United International College) students. It addresses real pain points in campus life:

- Too many scattered university portals — overwhelming for freshmen
- Grading rubrics and syllabi are non-transparent, making semester planning hard
- Assignments spread across iSpace / JupyterHub / AutoLab — DDLs get missed
- Enterprise WeChat inboxes flooded with mixed messages
- Hard to locate the right knowledge point when revising for exams

The system integrates **timetable OCR recognition, minute-precision DDL alerts, GPA calculation (sGPA/cGPA), course reviews, email summarization, custom platform management, and AI chat** under a **Spring Boot (backend) + Python (AI/OCR service) + Electron (desktop frontend)** architecture.

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Implemented Features](#implemented-features)
- [Planned Features](#planned-features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [API Reference](#api-reference)
- [Database Schema](#database-schema)
- [Development Notes](#development-notes)

---

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| Desktop Frontend | Electron · Vue 3 · HTML / CSS / JavaScript |
| Business Backend | Spring Boot 4.0 · MyBatis-Plus · JPA · MySQL |
| AI / OCR Service | Python · Baidu OCR API · FastAPI (planned) |
| Vector Database | ChromaDB (planned) |
| Cache / Queue | Redis · Celery (planned) |
| Utilities | Lombok · RestTemplate |
| Build Tool | Maven |

---

## Implemented Features

### 1. Timetable OCR Recognition

- Upload UIC Sections screenshot; backend calls Baidu OCR API for text recognition
- Custom state-machine parser extracts: course name, course code, teacher, time slot, classroom
- Supports multi-slot courses (same course with multiple time slots) with automatic merging
- Smart recognition for both merged-line and per-line OCR output formats
- Visual display of parsed results; one-click confirmation to import
- OCR service endpoint: `http://127.0.0.1:8766`

### 2. Minute-Precision DDL Alerts

- Add deadlines with **hours and minutes** (yyyy-MM-dd HH:mm format); compatible with date-only entries
- Three-tier reminder system: 30 minutes / 10 minutes / 5 minutes before deadline
- Five urgency levels: red pulsing (< 1h), orange (< 24h), yellow (< 3d), green (safe), grey (expired)
- **< 1h urgent floating bar**: persistent white-background-red-text overlay showing live countdown
- Sidebar badge shows urgent + warning DDL counts
- Mark as done, delete operations
- Endpoints: `GET /api/ddl/list`, `POST /api/ddl/add`, `PUT /api/ddl/done/{id}`, `DELETE /api/ddl/{id}`, `GET /api/ddl/urgent`

### 3. Custom Platforms & DDL Scraping

- Add custom homework submission platforms (name + URL + description)
- One-click "Extract DDLs" — generates sample deadlines; future headless browser integration
- Built-in BNBU quick links: iSpace, UIC MIS, Learning Resource Center (LRC), WebVPN, Library
- Endpoints: `GET /api/platform/list`, `POST /api/platform/add`, `DELETE /api/platform/{id}`, `POST /api/scrape/ddl`

### 4. GPA Calculator (sGPA / cGPA)

- Enter course name, credits, and score (out of 100); system computes grade points and weighted GPA (4.0 scale)
- Dual indicators: **Semester GPA (sGPA)** and **Cumulative GPA (cGPA)**
- Target GPA setting with automatic gap calculation and required future GPA
- AI suggestions: Excellent / Good / Needs Improvement
- Grade scenario simulation: shows GPA changes under different future grades
- Right-side slide-out panel for grade entry with bottom collapse arrow
- Grade point scale: ≥90→4.0 / 85-89→3.7 / 80-84→3.3 / 75-79→3.0 / 70-74→2.7 / 65-69→2.3 / 60-64→2.0 / <60→0.0
- Endpoints: `GET /api/gpa/calc`, `POST /api/gpa/grade`, `DELETE /api/gpa/grade/{id}`

### 5. Course Reviews

- Leave 1–5 star ratings and text comments on courses
- View and delete reviews, sorted newest first
- Endpoints: `GET /api/review/list`, `POST /api/review/add`, `DELETE /api/review/{id}`

### 6. Email Summarization & Classification

- Paste Enterprise WeChat email content (sender + subject + body); AI auto-classifies and summarizes
- Categories: `academic` / `club` / `admin` / `other`
- Important emails (academic + admin) highlighted in red
- Falls back to keyword-based classification if AI backend is unavailable
- Filter by category, delete records
- Endpoints: `GET /api/email/list`, `POST /api/email/process`, `DELETE /api/email/{id}`

### 7. AI Chat Assistant

- Integrated chat-style AI interface for natural language queries
- Drag-and-drop file upload (PDF, images, etc.) as conversation context
- Quick-ask shortcuts for common questions
- Typewriter-effect streaming output

### 8. Home Dashboard

- Default landing page combines **timetable + quick links + top urgent DDLs + grade entry** in one view
- One-click access to BNBU services: iSpace, MIS, LRC, WebVPN
- Top 5 most urgent DDLs displayed; one-click jump to full list
- "Record Grades" button auto-switches to GPA page and opens right-side panel

### 9. Electron Desktop App

- All features packaged as a 1200×800 Electron window — no browser needed
- Vue 3 responsive three-column layout: left nav + main content + right slide-out panel
- Automatic fallback to local Mock data when backend is unavailable
- Periodic polling for urgent DDL desktop notifications + red floating bar
- Class reminder popup: shows course name, classroom, and time before class
- One-click startup via `start.bat`: auto-launches OCR service + Electron app

---

## Planned Features

### RAG Knowledge Base (Course Material Q&A)

- Upload course PDFs, lecture notes, syllabi
- Async pipeline: text extraction → chunking → Embedding → ChromaDB
- Real-time progress via WebSocket

### RAG-based Intelligent Q&A

- Hybrid retrieval (BM25 keyword + vector semantic search)
- RRF fusion + reranking for higher recall
- Claude-generated answers with source citations
- SSE streaming output, low time-to-first-token

### Agent Tool Calling

- Encapsulate retrieval, GPA calculation, DDL query as Function Calling tools
- Multi-step planning; supports stream interruption

### Automated DDL Scraping

- Auto-scrape academic calendar and platform (iSpace / JupyterHub / AutoLab) deadlines
- Pop-up alerts for course selection and transfer application DDLs

### Semester Plan & Revision Planner

- Plan full semester at the start; negotiate milestones with the user
- Generate revision plans for mid-term/finals; locate knowledge points in PPT slides
- Built-in progress tracker with positive reinforcement

### Audio/Video Multimodal Ingestion

- Upload lecture recordings; transcribe via ASR (Whisper / Claude) with timestamps
- Supports "What was discussed at minute X?" queries
- Large file chunked upload with resume support

### LLM Engineering

- Token bucket rate limiting (Redis) + exponential backoff
- Semantic caching to reduce cost and latency
- Per-user / per-feature token and cost tracking
- Model fallback strategy

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│              Electron Desktop Frontend (local)                 │
│   Vue 3 · Home Dashboard · Urgent Bar · Polling · Side Panel  │
└──────────────────┬───────────────┬──────────────┬────────────┘
                   │               │              │
          ┌────────▼────────┐      │     ┌────────▼──────────────┐
          │  Spring Boot     │      │     │  Python OCR/AI Service │
          │  Backend :8080   │      │     │  Baidu OCR :8766       │
          │  ScheduleCtrl    │      │     │  parse_sections()      │
          │  DdlCtrl         │      │     │  Smart timetable parse │
          │  GpaCtrl         │      │     │  FastAPI :5000 (planned)│
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

## Project Structure

```
academic-agent-master/
├── pom.xml                                          # Maven configuration
├── README.md                                        # Chinese README
├── README_EN.md                                     # English README
├── electron-app/
│   └── bnbu-assistant/
│       ├── main.js                                  # Electron main process
│       ├── launcher.js                              # Launcher (env variable fix)
│       ├── index.html                               # Vue 3 frontend (single-file SPA)
│       ├── ocr_server.py                            # Baidu OCR service + timetable parser
│       ├── ocr_schedule.py                          # Schedule parsing helper module
│       ├── start.bat                                # One-click startup script
│       ├── start-browser.bat                        # Browser debug launcher
│       ├── vendor/                                  # Local frontend dependencies (offline)
│       │   ├── vue.global.prod.js                   #   Vue 3 production build
│       │   ├── tailwind.css / tailwind.js           #   Tailwind CSS
│       │   ├── material-symbols.css / .woff2        #   Google Material Symbols icons
│       │   └── tesseract/                          #   Tesseract.js WASM (backup OCR)
│       └── assets/                                  # App icons and static resources
├── src/
│   ├── main.py                                      # FastAPI AI backend entry (planned)
│   ├── prompt.py                                    # Prompt templates (planned)
│   └── main/
│       ├── java/org/example/academicagentbackend/
│       │   ├── AcademicAgentBackendApplication.java
│       │   ├── common/
│       │   │   └── Result.java                      # Unified response wrapper
│       │   ├── controller/
│       │   │   └── ScheduleController.java          # POST /api/schedule/send
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

## Getting Started

> ⚠️ **This project currently runs locally only. No online deployment has been configured.**

### Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Node.js | 16+ | Run Electron frontend |
| Java | 17+ | Run Spring Boot backend |
| Maven | 3.6+ | Build Java project |
| MySQL | 8.0+ | Data persistence |
| Python | 3.10+ | OCR recognition service |

### 1. Clone the Repository

```bash
git clone <repository-url>
cd academic-agent-master
```

### 2. Initialize the Database

```sql
CREATE DATABASE bnbu_assistant DEFAULT CHARACTER SET utf8mb4;
USE bnbu_assistant;

CREATE TABLE schedule_item (
  id INT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT,
  course_name VARCHAR(100), course_id VARCHAR(50),
  day_of_week INT, start_time VARCHAR(20), end_time VARCHAR(20), location VARCHAR(100)
);
CREATE TABLE ddl_item (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT,
  title VARCHAR(200), due_date VARCHAR(20),
  source VARCHAR(20) DEFAULT 'user', done INT DEFAULT 0,
  platform_name VARCHAR(100), course_name VARCHAR(100)
);
CREATE TABLE course_grade (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT,
  course_name VARCHAR(100), credits DOUBLE, score DOUBLE,
  grade_point DOUBLE, semester VARCHAR(30)
);
CREATE TABLE course_review (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT,
  course_name VARCHAR(100), rating INT, comment TEXT, review_date VARCHAR(20)
);
CREATE TABLE email_summary (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT,
  sender VARCHAR(200), subject VARCHAR(300), raw_content TEXT,
  category VARCHAR(20), summary VARCHAR(500), important INT DEFAULT 0, received_date VARCHAR(20)
);
CREATE TABLE submission_platform (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT,
  platform_name VARCHAR(100), platform_url VARCHAR(500),
  description VARCHAR(200), icon VARCHAR(50) DEFAULT 'fas fa-graduation-cap',
  sort_order INT DEFAULT 99
);
```

### 3. Configure Database

Edit `src/main/resources/application.yml`:

```yaml
spring:
  datasource:
    url: jdbc:mysql://localhost:3306/bnbu_assistant?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai
    username: root
    password: your_password
```

### 4. Install Frontend Dependencies

```bash
cd electron-app/bnbu-assistant
npm install
```

### 5. One-Click Launch (Recommended)

```bash
cd electron-app/bnbu-assistant
start.bat
```

`start.bat` automatically:
1. Cleans up old OCR service processes
2. Starts Baidu OCR recognition service (port 8766)
3. Launches the Electron desktop application

### 6. Manual Step-by-Step Launch

```bash
# Terminal 1: Spring Boot backend
mvn spring-boot:run

# Terminal 2: OCR service
cd electron-app/bnbu-assistant
python ocr_server.py

# Terminal 3: Electron frontend
cd electron-app/bnbu-assistant
npx electron main.js
```

### Full Startup Order

```
1. Start MySQL
2. mvn spring-boot:run                                         (:8080)
3. cd electron-app/bnbu-assistant && python ocr_server.py     (:8766)
4. cd electron-app/bnbu-assistant && start.bat                (one-click 2+3)
```

> 💡 The frontend degrades gracefully — all UI pages work with local Mock data even without the backend running.

---

## API Reference

All responses follow: `{ "code": 200, "message": "...", "data": <T> }`

### Schedule Parsing

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/schedule/send` | POST | Upload timetable image (multipart, field: `file`) |

### DDL Alerts

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ddl/list` | GET | List all DDLs (with minutesLeft, hoursLeft, displayText, urgencyLevel) |
| `/api/ddl/urgent` | GET | List only < 1h urgent DDLs |
| `/api/ddl/add` | POST | Add DDL `{ title, dueDate("yyyy-MM-dd HH:mm"), source?, platformName?, courseName? }` |
| `/api/ddl/done/{id}` | PUT | Mark as done |
| `/api/ddl/{id}` | DELETE | Delete DDL |

### GPA Calculator

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/gpa/calc` | GET | Calculate weighted GPA |
| `/api/gpa/grade` | POST | Add/update grade `{ courseName, credits, score, semester }` |
| `/api/gpa/grade/{id}` | DELETE | Remove a grade record |

### Course Reviews

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/review/list` | GET | List course reviews |
| `/api/review/add` | POST | Add review `{ courseName, rating, comment }` |
| `/api/review/{id}` | DELETE | Delete review |

### Email Summarization

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/email/list` | GET | List emails `?category=academic\|club\|admin\|other` |
| `/api/email/process` | POST | Process email `{ sender, subject, body }` |
| `/api/email/{id}` | DELETE | Delete email record |

### Platform Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/platform/list` | GET | List custom platforms |
| `/api/platform/add` | POST | Add platform `{ platformName, platformUrl, description?, icon? }` |
| `/api/platform/{id}` | DELETE | Delete platform |

### DDL Scraping

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/scrape/ddl` | POST | Scrape DDLs `{ platformName, platformUrl }` → `{ saved, items }` |

### OCR Recognition Service

| Endpoint | Method | Description |
|----------|--------|-------------|
| `http://127.0.0.1:8766/ocr` | POST | Upload image for OCR (multipart, field: `image`) |
| `http://127.0.0.1:8766/ocr/sections` | POST | Upload Sections screenshot for timetable parsing |

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `schedule_item` | Parsed timetable entries |
| `ddl_item` | Deadline tasks (minute precision) |
| `course_grade` | Course grades for GPA calculation |
| `course_review` | Student course reviews |
| `email_summary` | AI-classified email summaries |
| `submission_platform` | Custom homework platforms |

---

## Development Notes

- **Local-only for now** — all services run on the developer's machine
- **Dual-backend architecture**: Java handles business logic & persistence; Python handles OCR recognition & AI orchestration via HTTP
- **Unified response format**: `Result<T>` wrapper — `code=200` success, `code=500` error
- **MyBatis-Plus**: All Mappers extend `BaseMapper` with out-of-the-box CRUD
- **Mock fallback**: Every frontend API call is wrapped in try/catch; UI works without the backend
- **Hardcoded userId=1** — multi-user login planned for future versions
- **DDL minute precision**: yyyy-MM-dd HH:mm format; three-tier reminders (30min / 10min / 5min); < 1h triggers persistent floating bar
- **Dual GPA indicators**: Semester GPA (sGPA) and Cumulative GPA (cGPA) displayed simultaneously with target tracking
- **Smart OCR parsing**: Custom state-machine parser handles both merged-line and per-line Baidu OCR output formats
- **Platform scraping**: Add custom platforms, one-click DDL extraction (mock mode; real scraping needs Puppeteer)
- **CORS**: All controllers annotated with `@CrossOrigin(origins = "*")` for Electron compatibility
- **One-click startup**: `start.bat` auto-cleans old processes → starts OCR service → launches Electron

---

## License

Final Year Project (FYP). For academic and learning purposes only.

---

> Last updated: 2026-06-16
