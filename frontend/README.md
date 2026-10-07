# Academic Agent 前端

Vue 3 + TypeScript + Vite 的中文学术工作台，与本仓库 FastAPI 接口对接。

## 启动

需要 Node.js 22+，并按根目录 `ENVIRONMENT.md` 启动后端。

```powershell
cd frontend
npm ci
npm run dev
```

打开 <http://127.0.0.1:5173>。开发服务器将 `/api` 请求转发到 `http://127.0.0.1:8000`，登录 Cookie 通过同源代理发送，无需修改后端 CORS。

后端地址不同时，复制 `.env.example` 为 `.env.local`，修改 `API_TARGET` 后重启 Vite。前端不需要也不应配置模型 API 密钥。

## 功能

- 登录、注册、退出及刷新后的登录状态恢复；会话过期时清空当前账号页面数据并重新登录。
- 创建、搜索、切换研究项目。
- 批量或拖拽上传 PDF、DOCX、PPTX、TXT，校验扩展名及空文件，自动轮询排队和解析状态。
- Markdown 文档问答、安全渲染、连续追问、导出 Markdown 对话。
- 勾选文档限定检索范围，调整检索比例（默认 40%），粘贴指定文档的原文进行追问。
- 点击答案引用，查看后端返回的原文片段。
- PNG / JPEG 课表识别、图片预览、按星期展示返回课程。
- 桌面三栏工作台及手机端导航、资料抽屉。

## 接口映射

| 场景 | 接口 |
| --- | --- |
| 注册 / 登录 / 当前用户 / 退出 | `POST /api/user/create`、`POST /api/user/login`、`GET /api/user/me`、`POST /api/user/logout` |
| 项目列表 / 新建 | `GET /api/projects`、`POST /api/projects` |
| 文档列表 / 上传 | `GET /api/projects/{id}/documents`、`POST /api/projects/{id}/documents`（表单字段 `files`） |
| 问答 | `POST /api/projects/{id}/ask` |
| 课表识别 | `POST /api/timetable`（表单字段 `file`） |

问答沿用后端返回的 `conversation_id`；新对话不传旧会话 ID。不勾选文档时省略 `doc_ids`，勾选时只发送已完成处理的文档。原文追问同时发送 `document_id` 和 `selected_text`，并检查它是否位于选定范围内。

## 验证与构建

```powershell
npm run build
npm test
```

`build` 执行 Vue/TypeScript 类型检查，并生成 `dist/`。浏览器测试默认使用本机 Microsoft Edge，覆盖登录、新建项目、文档范围、多轮问答、上传轮询、过期会话、项目切换竞态、课表响应、移动布局与 Markdown 注入防护。测试使用模拟 API，不写入真实数据库，也不验证模型回答质量。

没有 Edge 的环境可安装 Playwright Chromium 后执行：

```powershell
npx playwright install chromium
$env:PLAYWRIGHT_CHANNEL="chromium"
npm test
```

`npm run preview` 在 <http://127.0.0.1:4173> 预览构建结果，也提供本地 API 代理。正式部署时，将 `dist/` 配置为静态站点，并通过同源反向代理把 `/api` 转发到 FastAPI；Vite 开发服务器不作为正式生产服务。

## 当前边界

- 对话按项目保存在当前页面内存中。刷新或退出登录会清空前端记录；后端保存的历史仍可被本次会话继续引用，但当前没有历史会话列表接口，前端不承诺刷新恢复。需要留档时先导出对话。
- 新对话切换到一个新的后端会话，旧会话不会从数据库删除。
- 课表页展示本次识别响应；后端尚无课表列表读取接口，刷新后不恢复展示。
- 资料处理失败时可重新上传；当前后端没有删除、重试任务或文件正文读取接口，因此不提供这些操作。
- 引用详情只展示问答接口返回的片段，不是完整 PDF 阅读器。原文追问可直接使用引用片段，或手工粘贴文档原文。
- 文件处理和模型回答依赖后端、数据库及模型服务，可能耗时较长；上传成功不代表解析成功，需等待“已就绪”。
