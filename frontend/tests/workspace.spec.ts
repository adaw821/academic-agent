import { test, expect, type Page } from '@playwright/test'

const projects = [
  { id: 1, name: '大语言模型文献研究' },
  { id: 2, name: '机器学习课程' },
]
const documents = [
  { id: 10, filename: 'Attention Is All You Need.pdf', status: 'completed' },
  { id: 11, filename: '研究笔记.txt', status: 'completed' },
]
const answer = {
  answer: '## 核心结论\n\n自注意力机制能够捕获序列依赖。[1]',
  conversation_id: 27,
  sources: [{ index: 1, document_id: 10, parent_id: 31, filename: documents[0].filename }],
  retrieved_contexts: ['Attention is all you need. The Transformer uses self-attention.'],
}

async function mockApi(page: Page, loggedIn = true) {
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname === '/api/user/me')
      return route.fulfill({
        status: loggedIn ? 200 : 401,
        json: loggedIn ? { id: 1, username: '研究者' } : { detail: '请先登录' },
      })
    if (pathname === '/api/user/login') {
      loggedIn = true
      return route.fulfill({ json: { user_id: 1, message: '登录成功' } })
    }
    if (pathname === '/api/user/logout') {
      loggedIn = false
      return route.fulfill({ json: { message: '已退出登录' } })
    }
    if (pathname === '/api/user/create')
      return route.fulfill({ status: 201, json: { id: 3, username: '新同学' } })
    if (pathname === '/api/projects' && route.request().method() === 'POST')
      return route.fulfill({ status: 201, json: { project_id: 3 } })
    if (pathname === '/api/projects') return route.fulfill({ json: projects })
    if (pathname.endsWith('/documents'))
      return route.fulfill({ json: pathname.includes('/1/') ? documents : [] })
    if (pathname.endsWith('/ask')) return route.fulfill({ json: answer })
    if (pathname === '/api/timetable' && route.request().method() === 'GET')
      return route.fulfill({ json: { items: [], total: 0 } })
    return route.fulfill({ status: 404, json: { detail: '未匹配的测试接口' } })
  })
}

test('登录、创建项目与退出时清空工作空间', async ({ page }) => {
  await mockApi(page, false)
  await page.goto('/')
  await expect(page.getByRole('button', { name: '发送问题' })).toBeDisabled()
  await page.getByRole('button', { name: '登录，开启研究' }).click()
  await page.getByLabel('邮箱或手机号').fill('student@example.com')
  await page.getByLabel('密码', { exact: true }).fill('password123')
  await page.getByRole('dialog').getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.project-item')).toHaveCount(2)
  await page.getByRole('button', { name: /新建研究项目/ }).click()
  await page.getByLabel('项目名称').fill('毕业论文')
  const createRequest = page.waitForRequest(
    (r) => r.url().endsWith('/api/projects') && r.method() === 'POST',
  )
  await page.getByRole('dialog').getByRole('button', { name: '创建项目', exact: true }).click()
  expect((await createRequest).postDataJSON()).toEqual({ name: '毕业论文' })
  await expect(page.getByRole('heading', { name: '毕业论文', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page.locator('.project-item')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '登录，开启研究' })).toBeVisible()
})

test('限定文档、多轮对话、引用原文与 Markdown 安全渲染', async ({ page }) => {
  await mockApi(page)
  await page.goto('/')
  await page.getByRole('checkbox', { name: `选择 ${documents[0].filename}` }).check()
  await page.getByRole('textbox', { name: '输入研究问题' }).fill('总结论文')
  const first = page.waitForRequest('**/api/projects/1/ask')
  await page.getByRole('button', { name: '发送问题' }).click()
  expect((await first).postDataJSON()).toEqual({
    query: '总结论文',
    conversation_id: null,
    candidate_k_percentage: 0.4,
    doc_ids: [10],
  })
  await expect(page.getByRole('heading', { name: '核心结论' })).toBeVisible()
  await page.locator('.source-chips button').click()
  await expect(page.locator('.source-detail')).toContainText('The Transformer uses self-attention.')
  await page.getByRole('button', { name: '基于这段原文追问' }).click()
  await page.getByRole('textbox', { name: '输入研究问题' }).fill('详细解释')
  const followup = page.waitForRequest('**/api/projects/1/ask')
  await page.getByRole('button', { name: '发送问题' }).click()
  expect((await followup).postDataJSON()).toMatchObject({
    conversation_id: 27,
    document_id: 10,
    selected_text: answer.retrieved_contexts[0],
  })
  await expect(page.locator('.turn')).toHaveCount(2)
  await page.route('**/api/projects/1/ask', (route) =>
    route.fulfill({
      json: {
        ...answer,
        answer:
          '<script>window.hacked=true</script><img src="x" onerror="window.hacked=true">安全文本',
      },
    }),
  )
  await page.getByRole('textbox', { name: '输入研究问题' }).fill('安全渲染')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.locator('.markdown').last()).toContainText('安全文本')
  expect(
    await page.evaluate(() => (window as unknown as { hacked?: boolean }).hacked),
  ).toBeUndefined()
  await expect(page.locator('.markdown script, .markdown [onerror]')).toHaveCount(0)
})

test('上传资料使用 files 字段并轮询至就绪', async ({ page }) => {
  await mockApi(page)
  let uploaded = false
  let reads = 0
  await page.route('**/api/projects/1/documents', async (route) => {
    if (route.request().method() === 'POST') {
      expect(route.request().postDataBuffer()?.toString()).toContain('name="files"')
      uploaded = true
      return route.fulfill({
        status: 202,
        json: {
          saved_files: [{ id: 12, filename: '测试资料.txt', status: 'queued' }],
          failed_files: [],
        },
      })
    }
    return route.fulfill({
      json: uploaded
        ? [{ id: 12, filename: '测试资料.txt', status: ++reads > 1 ? 'completed' : 'processing' }]
        : [],
    })
  })
  await page.goto('/')
  await expect(page.locator('.research-toolbar h1')).toHaveText(projects[0].name)
  await page.getByLabel('上传研究资料').setInputFiles({
    name: '测试资料.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('测试内容'),
  })
  await expect(page.locator('.document-row')).toContainText('解析中')
  await expect(page.locator('.document-row')).toContainText('已就绪', { timeout: 10000 })
  await page
    .getByLabel('上传研究资料')
    .setInputFiles({ name: '空文件.txt', mimeType: 'text/plain', buffer: Buffer.from('') })
  await expect(page.getByRole('alert')).toContainText('未上传：空文件.txt')
})

test('切换项目时丢弃旧文档响应', async ({ page }) => {
  await mockApi(page)
  let release: (() => void) | undefined
  const delayed = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/projects/1/documents', async (route) => {
    await delayed
    await route.fulfill({ json: documents })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '机器学习课程', exact: true }).click()
  release!()
  await expect(page.locator('.research-toolbar h1')).toHaveText('机器学习课程')
  await expect(page.locator('.document-row')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '发送问题' })).toBeDisabled()
})

test('会话过期清空资料并提示重新登录', async ({ page }) => {
  await mockApi(page)
  await page.goto('/')
  await expect(page.locator('.document-row')).toHaveCount(2)
  await page.route('**/api/projects/1/documents', (route) =>
    route.fulfill({ status: 401, json: { detail: '请先登录' } }),
  )
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog')).toContainText('登录已过期')
  await expect(page.locator('.document-row')).toHaveCount(0)
  await expect(page.locator('.project-item')).toHaveCount(0)
})

test('课表上传并显示按星期分组的真实响应字段', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/timetable', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { items: [], total: 0 } })
      : route.fulfill({
          json: {
            message: '课表解析成功',
            timetables: [
              {
                course_name: '机器学习',
                course_code: 'CS101',
                day_of_week: 'Mon',
                start_time: '09:00',
                end_time: '10:30',
                location: 'A101',
                teachers: '张老师',
                units: 3,
              },
            ],
          },
        }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: '我的课表', exact: true }).click()
  await page.getByLabel('上传课表图片').setInputFiles({
    name: 'timetable.png',
    mimeType: 'image/png',
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==',
      'base64',
    ),
  })
  await page.getByRole('button', { name: '识别并保存课表' }).click()
  await expect(page.locator('.week-grid section').first()).toContainText('机器学习')
  await expect(page.locator('.week-grid section').first()).toContainText('09:00 – 10:30')
})

test('课表手动新增、修正、取消删除及确认删除', async ({ page }) => {
  await mockApi(page)
  let rows: Record<string, unknown>[] = []
  await page.route('**/api/timetable', route => route.fulfill({ json: { items: rows, total: rows.length } }))
  await page.route('**/api/timetable/entries', route => {
    rows = [{ ...route.request().postDataJSON(), id: 10, course_id: 1, week_pattern: 'all' }]
    return route.fulfill({ status: 201, json: { id: 10, course_id: 1 } })
  })
  await page.route('**/api/timetable/entries/10', route => {
    if (route.request().method() === 'DELETE') rows = []
    else rows = [{ ...rows[0], ...route.request().postDataJSON() }]
    return route.fulfill({ json: { id: 10 } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '我的课表', exact: true }).click()
  await page.getByRole('button', { name: '手动新增安排' }).click()
  await page.getByLabel('课程名称', { exact: true }).fill('数据库')
  await page.getByLabel('上课地点', { exact: true }).fill('A101')
  await page.getByRole('button', { name: '保存上课安排' }).click()
  await expect(page.locator('.saved-grid')).toContainText('数据库')
  await page.getByRole('button', { name: '修改时间地点' }).click()
  await page.getByLabel('上课星期').selectOption('Wed')
  await page.getByLabel('上课地点', { exact: true }).fill('B202')
  await page.getByRole('button', { name: '保存上课安排' }).click()
  await expect(page.locator('.saved-grid')).toContainText('B202')
  await expect(page.locator('.saved-grid')).toContainText('Wed')
  await page.getByRole('button', { name: '删除安排', exact: true }).click()
  await page.getByRole('button', { name: '取消删除' }).click()
  await expect(page.locator('.saved-grid article')).toHaveCount(1)
  await page.getByRole('button', { name: '删除安排', exact: true }).click()
  await page.getByRole('button', { name: '确认删除安排' }).click()
  await expect(page.locator('.saved-grid article')).toHaveCount(0)
})

test('学校快照可放入图片补充入口，确认后才识别', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/timetable/mis', route => route.fulfill({ json: {
    local_login: true, snapshot: { term: '秋季', saved_at: '2026-09-01T00:00:00Z', courses: [] },
  } }))
  await page.route('**/api/timetable/mis/capture', route => route.fulfill({ json: { state: 'idle', message: '' } }))
  await page.route('**/api/timetable/mis/image*', route => route.fulfill({ contentType: 'image/png', body: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64') }))
  let uploaded = false
  await page.route('**/api/timetable', route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { items: [], total: 0 } })
    uploaded = true
    return route.fulfill({ json: { message: '课表解析成功', timetables: [] } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '我的课表', exact: true }).click()
  await page.getByRole('button', { name: '用此图片补充课表' }).click()
  await expect(page.getByAltText('待识别的课表预览')).toBeVisible()
  expect(uploaded).toBe(false)
  await page.getByRole('button', { name: '识别并保存课表' }).click()
  await expect.poll(() => uploaded).toBe(true)
})

test('聊天附加课表图片后通过学校操作工具补充', async ({ page }) => {
  await mockApi(page)
  const imageId = 'a'.repeat(32)
  await page.route('**/api/timetable/attachments', route => route.fulfill({ status: 201, json: { image_id: imageId } }))
  await page.route('**/api/projects/1/ask', route => {
    expect(route.request().postDataJSON()).toMatchObject({ timetable_image_id: imageId, use_school_tools: true, allow_school_actions: true })
    return route.fulfill({ json: { answer: '图片识别完成，已补充课表。', conversation_id: 1, sources: [], retrieved_contexts: [], tool_results: [{ tool: 'supplement_timetable_image', count: 1 }] } })
  })
  await page.goto('/')
  await page.getByLabel('学校助手', { exact: true }).check()
  await page.getByLabel('允许下载、导入、已读和课表操作').check()
  await page.getByLabel('附加课表图片', { exact: true }).setInputFiles({ name: '课表.png', mimeType: 'image/png', buffer: Buffer.from('png') })
  await expect(page.getByText('课表.png · 本轮课表附件')).toBeVisible()
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByText('图片识别完成，已补充课表。', { exact: true })).toBeVisible()
  await expect(page.getByText('课表.png · 本轮课表附件')).toHaveCount(0)
})

test('移动端导航、资料栏与布局不横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page)
  await page.goto('/')
  await page.getByRole('button', { name: '打开导航' }).click()
  await page.getByRole('button', { name: '机器学习课程', exact: true }).click()
  await page.getByRole('button', { name: '查看项目资料' }).click()
  await expect(page.locator('.documents-panel')).toBeVisible()
  await page.getByRole('button', { name: '关闭资料栏' }).click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/mobile-workspace.png', fullPage: true })
})

test('桌面工作台视觉检查且没有脚本错误', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await mockApi(page)
  await page.goto('/')
  await expect(page.locator('.document-row')).toHaveCount(2)
  await page.screenshot({ path: 'test-results/desktop-workspace.png', fullPage: true })
  expect(errors).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
