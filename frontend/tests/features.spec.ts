import { test, expect, type Page } from '@playwright/test'

const browserErrors = new WeakMap<Page, string[]>()
test.beforeEach(({ page }) => {
  const errors: string[] = []
  browserErrors.set(page, errors)
  page.on('pageerror', (error) => errors.push(error.message))
})
test.afterEach(({ page }) => {
  expect(browserErrors.get(page)).toEqual([])
})

const answer = {
  answer: '查询到一份待交作业。',
  conversation_id: 8,
  sources: [],
  retrieved_contexts: [],
}
const course = {
  id: 7,
  course_name: '数据库',
  source_url: 'https://ispace.bnbu.edu.cn/course/view.php?id=7',
  last_synced_at: '2026-10-26T02:00:00Z',
}
const document = { id: 10, filename: '讲义.txt', status: 'completed' }
const schedule = {
  id: 3,
  course_id: 7,
  course_name: '数据库',
  day_of_week: 'Mon',
  start_time: '09:00',
  end_time: '10:00',
  location: 'A101',
  teachers: '陈老师',
  term_name: null,
  term_start: null,
  term_end: null,
  start_week: null,
  end_week: null,
  week_pattern: 'all',
}
async function setup(page: Page, docs = true) {
  const savedSchedule = { ...schedule }
  const calls: { path: string; method: string; body: any }[] = []
  let removed = false,
    read = false,
    jobReads = 0,
    retried = false
  await page.route('**/api/**', async (route) => {
    const r = route.request(),
      url = new URL(r.url()),
      path = url.pathname,
      method = r.method()
    calls.push({ path, method, body: r.postData() ? r.postDataJSON() : null })
    let json: unknown
    if (path === '/api/user/me') json = { id: 1, username: '同学' }
    else if (path === '/api/user/logout') json = {}
    else if (path === '/api/projects')
      json = [
        { id: 1, name: '我的知识库' },
        { id: 2, name: '另一个项目' },
      ]
    else if (path.endsWith('/documents')) json = docs && !removed ? [document] : []
    else if (path.endsWith('/documents/10/content'))
      json = { content: '# 课程正文\n<script>window.hacked=true</script>已保存的全文' }
    else if (path.endsWith('/documents/10/file'))
      return route.fulfill({
        body: 'original',
        contentType: 'text/plain',
        headers: { 'Content-Disposition': 'attachment; filename=lecture.txt' },
      })
    else if (path.endsWith('/documents/10') && method === 'DELETE') {
      removed = true
      json = { cleanup_pending: false }
    } else if (path.endsWith('/documents/10/retry')) json = { job_id: 'doc', status: 'queued' }
    else if (path.endsWith('/conversations'))
      json = {
        items: [
          { id: 8, title: '上次的讨论', message_count: 2, updated_at: '2026-10-26T02:00:00Z' },
        ],
        total: 1,
      }
    else if (path.endsWith('/conversations/8/messages'))
      json = {
        items: [
          { id: 1, role: 'user', content: '上次的问题', turn_id: 'pair', response_data: null },
          {
            id: 2,
            role: 'assistant',
            content: '已恢复的历史回答',
            turn_id: 'pair',
            response_data: {
              sources: [{ index: 1, document_id: 10, parent_id: 3, filename: '讲义.txt' }],
              retrieved_contexts: ['原文证据'],
            },
          },
        ],
        total: 2,
      }
    else if (path.endsWith('/ask/jobs')) json = { job_id: 'job-1', status: 'queued' }
    else if (path.endsWith('/jobs/job-1'))
      json = {
        job_id: 'job-1',
        kind: 'qa',
        status: ++jobReads > 1 ? 'completed' : 'running',
        result: jobReads > 1 ? answer : null,
      }
    else if (path.endsWith('/jobs/job-failed/retry')) {
      retried = true
      json = { status: 'queued' }
    } else if (path.endsWith('/jobs'))
      json = {
        items: [
          {
            job_id: 'job-failed',
            kind: 'qa',
            status: retried ? 'queued' : 'failed',
            error_type: retried ? null : 'HTTPException',
          },
        ],
        total: 1,
      }
    else if (path.endsWith('/ask')) json = answer
    else if (path === '/api/school/session')
      json = { configured: method === 'PUT', validated: method === 'PUT' }
    else if (path === '/api/school/courses')
      json = {
        courses: [{ title: '数据库', source_url: course.source_url }],
        complete: false,
        warning: '动态课程列表不完整',
      }
    else if (path === '/api/courses') json = { items: [course], total: 1 }
    else if (path.endsWith('/sync')) json = { assignments: 1, resources: 1, warnings: [] }
    else if (path === '/api/courses/7/resources')
      json = {
        items: [
          {
            id: 9,
            title: 'Lab 1.pdf',
            resource_type: 'file',
            purpose: 'assignment_attachment',
            source_status: 'seen',
            source_url: 'javascript:alert(1)',
          },
        ],
        total: 1,
      }
    else if (path.endsWith('/resources/9/download')) json = { downloaded: true }
    else if (path.endsWith('/resources/9/import'))
      json = { document_id: 12, job_id: 'import', status: 'queued', reused: false }
    else if (path === '/api/assignments')
      json = {
        items: [
          {
            id: 4,
            title: 'Lab 1',
            course_id: 7,
            course_name: '数据库',
            status: 'pending',
            source_status: 'not_seen',
            due_at: '2026-10-27T15:59:00Z',
            last_synced_at: course.last_synced_at,
            source_url: course.source_url,
          },
        ],
        total: 1,
      }
    else if (path === '/api/assignment-reminders/5/read') {
      read = true
      json = { read: true }
    } else if (path === '/api/assignment-reminders')
      json = {
        items:
          read && url.searchParams.get('unread_only') === 'true'
            ? []
            : [
                {
                  id: 5,
                  title: 'Lab 1',
                  course_name: '数据库',
                  active: true,
                  due_at: '2026-10-27T15:59:00Z',
                  current_due_at: '2026-10-28T15:59:00Z',
                  read_at: read ? '2026-10-26T02:00:00Z' : null,
                },
              ],
        total: read ? 0 : 1,
        unread_count: read ? 0 : 1,
      }
    else if (path === '/api/timetable') json = { items: [savedSchedule], total: 1 }
    else if (path.endsWith('/calendar')) {
      Object.assign(savedSchedule, r.postDataJSON())
      json = savedSchedule
    } else return route.fulfill({ status: 404, json: { detail: '测试未匹配接口 ' + path } })
    return route.fulfill({ json })
  })
  await page.goto('/')
  await expect(page.locator('.research-toolbar h1')).toHaveText('我的知识库')
  return calls
}

test('无文档时可启用学校助手并选择可恢复问答，权限随请求发送', async ({ page }) => {
  const calls = await setup(page, false)
  await page.getByLabel('输入研究问题').fill('请同步数据库课程')
  await expect(page.getByRole('button', { name: '发送问题' })).toBeDisabled()
  await page.getByLabel('学校助手', { exact: true }).check()
  await page.getByLabel('允许同步课程').check()
  await page.getByLabel('允许下载、导入和已读操作').check()
  await page.getByLabel('后台问答（支持恢复）').check()
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.locator('.messages')).toContainText(answer.answer)
  expect(calls.find((c) => c.path.endsWith('/ask/jobs'))?.body).toMatchObject({
    use_school_tools: true,
    allow_school_sync: true,
    allow_school_actions: true,
  })
  expect(calls.some((c) => c.path.endsWith('/ask'))).toBe(false)
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page.getByLabel('学校助手', { exact: true })).not.toBeChecked()
})

test('恢复历史引用后继续会话，失败后台任务可以重试', async ({ page }) => {
  const calls = await setup(page)
  await page.getByRole('button', { name: '历史与任务', exact: true }).click()
  await page.getByRole('button', { name: /上次的讨论/ }).click()
  await expect(page.locator('.messages')).toContainText('已恢复的历史回答')
  await page.locator('.source-chips button').click()
  await expect(page.locator('.source-detail')).toContainText('原文证据')
  await page.getByLabel('输入研究问题').fill('继续解释')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.locator('.turn')).toHaveCount(2)
  await expect.poll(() => calls.find((c) => c.path.endsWith('/ask'))?.body.conversation_id).toBe(8)
  await page.getByRole('button', { name: '历史与任务', exact: true }).click()
  await page.getByRole('button', { name: '后台任务', exact: true }).click()
  await page.getByRole('button', { name: '重试任务' }).click()
  await expect(page.locator('.project-activity')).toContainText('排队中')
  expect(calls.some((c) => c.path.endsWith('/jobs/job-failed/retry'))).toBe(true)
})

test('文档正文安全渲染、原文件下载、重处理和确认删除', async ({ page }) => {
  const calls = await setup(page)
  await page.getByRole('button', { name: '查看正文' }).click()
  await expect(page.locator('.document-content')).toContainText('已保存的全文')
  await expect(page.locator('.document-content script')).toHaveCount(0)
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载原文件' }).click()
  expect((await download).suggestedFilename()).toBe('讲义.txt')
  await page.getByRole('button', { name: '重新处理', exact: true }).click()
  await page.getByRole('button', { name: '确认重新处理', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('处理队列')
  await page.getByRole('button', { name: '删除文档', exact: true }).click()
  expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(0)
  await page.getByRole('button', { name: '确认删除文档', exact: true }).click()
  await expect(page.locator('.document-row')).toHaveCount(0)
})

test('学校连接、发现与同步、下载和导入当前项目', async ({ page }) => {
  const calls = await setup(page)
  await page.getByRole('button', { name: '学校事务', exact: true }).click()
  await page.locator('.connection-card summary').click()
  await page.getByLabel('学校会话', { exact: true }).fill('test-session')
  await page.getByRole('button', { name: '验证并保存连接' }).click()
  await expect(page.getByLabel('学校会话', { exact: true })).toHaveValue('')
  await page.getByRole('button', { name: '从学校发现课程' }).click()
  await expect(page.locator('.school-page')).toContainText('动态课程列表不完整')
  await page.getByRole('button', { name: '同步到我的课程' }).click()
  await expect(page.locator('.feature-notice')).toContainText('同步完成')
  await page.getByRole('button', { name: '查看附件', exact: true }).click()
  await expect(page.locator('.resource-panel')).toContainText('Lab 1.pdf')
  await expect(page.locator('.resource-panel a')).toHaveCount(0)
  await page.getByRole('button', { name: '下载到服务器' }).click()
  await expect(page.locator('.resource-panel')).toContainText('已有缓存')
  await page.getByRole('button', { name: '导入知识库', exact: true }).click()
  await expect(page.locator('.resource-panel')).toContainText('导入状态：排队中')
  expect(calls.find((c) => c.path.endsWith('/resources/9/import'))?.body).toEqual({ project_id: 1 })
  expect(calls.find((c) => c.path === '/api/school/session' && c.method === 'PUT')?.body).toEqual({
    session: 'test-session',
  })
  await page.locator('.school-page').evaluate((element) => {
    element.scrollTop = 0
  })
  await page.screenshot({
    path: 'test-results/school-desktop.png',
    fullPage: true,
    animations: 'disabled',
  })
})

test('作业可见状态与提醒已读，不把历史提醒当作提交', async ({ page }) => {
  const calls = await setup(page)
  await page.getByRole('button', { name: '学校事务', exact: true }).click()
  await page.getByRole('button', { name: '作业', exact: true }).click()
  await expect(page.locator('.school-page')).toContainText('最近未发现')
  await page.getByLabel('学校可见状态').selectOption('not_seen')
  const filtered = page.waitForRequest(
    (r) => r.url().includes('/assignments?') && r.url().includes('source_status=not_seen'),
  )
  await page.getByRole('button', { name: '筛选作业' }).click()
  await filtered
  await page.getByRole('button', { name: '查看作业附件' }).click()
  await expect(page.locator('.resource-panel')).toContainText('Lab 1.pdf')
  await page.getByRole('button', { name: '站内提醒', exact: true }).click()
  await expect(page.locator('.school-page')).toContainText('最新作业截止时间')
  await page.getByRole('button', { name: '标记已读', exact: true }).click()
  await expect(page.locator('.school-page')).toContainText('该范围未读 0 条')
  expect(calls.some((c) => c.path.endsWith('/assignment-reminders/5/read'))).toBe(true)
})

test('保存课表学期及单双周、按日期查询，移动端无横向溢出', async ({ page }) => {
  const calls = await setup(page)
  await page.getByRole('button', { name: '我的课表', exact: true }).click()
  await expect(page.getByRole('region', { name: '已保存课表' })).toContainText('数据库')
  await page.getByRole('button', { name: '设置学期与单双周' }).click()
  await page.getByLabel('学期名称').fill('秋季')
  await page.getByLabel('学期开始', { exact: true }).fill('2026-09-01')
  await page.getByLabel('学期结束', { exact: true }).fill('2026-12-31')
  await page.getByLabel('起始教学周').fill('2')
  await page.getByLabel('结束教学周').fill('16')
  await page.getByLabel('周次规则').selectOption('even')
  await page.getByRole('button', { name: '保存学期规则' }).click()
  await expect(page.getByRole('status')).toContainText('学期规则已保存')
  await expect(page.locator('.saved-grid')).toContainText('秋季 · 双周')
  await expect(page.locator('.saved-grid')).toContainText('第 2–16 周')
  expect(calls.find((c) => c.path.endsWith('/calendar'))?.body).toMatchObject({
    term_start: '2026-09-01',
    start_week: 2,
    end_week: 16,
    week_pattern: 'even',
  })
  await page.getByLabel('按日期查看').fill('2026-10-26')
  const filtered = page.waitForRequest('**/api/timetable?on_date=2026-10-26&limit=20&offset=0')
  await page.getByRole('button', { name: '查询课表' }).click()
  await filtered
  await page.setViewportSize({ width: 390, height: 844 })
  await page.locator('.saved-timetable').scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({
    path: 'test-results/calendar-mobile.png',
    fullPage: true,
    animations: 'disabled',
  })
})

test('切换项目时丢弃迟到的历史会话响应', async ({ page }) => {
  await setup(page)
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/projects/1/conversations/8/messages?*', async (route) => {
    await gate
    await route.fulfill({
      json: {
        items: [
          { id: 1, role: 'user', content: '旧项目的问题', turn_id: 'old', response_data: null },
          {
            id: 2,
            role: 'assistant',
            content: '不应进入新项目的回答',
            turn_id: 'old',
            response_data: null,
          },
        ],
        total: 2,
      },
    })
  })
  await page.getByRole('button', { name: '历史与任务', exact: true }).click()
  const pending = page.waitForRequest('**/api/projects/1/conversations/8/messages?*')
  await page.getByRole('button', { name: /上次的讨论/ }).click()
  await pending
  await page.getByRole('button', { name: '另一个项目', exact: true }).click()
  const response = page.waitForResponse('**/api/projects/1/conversations/8/messages?*')
  release()
  await response
  await expect(page.locator('.research-toolbar h1')).toHaveText('另一个项目')
  await expect(page.locator('.turn')).toHaveCount(0)
})

test('跨消息分页恢复完整历史，不遗漏末页回答', async ({ page }) => {
  await setup(page)
  await page.route('**/api/projects/1/conversations/8/messages?*', (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'))
    return route.fulfill({
      json: {
        items:
          offset === 0
            ? [{ id: 1, role: 'user', content: '跨页的问题', turn_id: 'pair', response_data: null }]
            : [
                {
                  id: 2,
                  role: 'assistant',
                  content: '末页回答',
                  turn_id: 'pair',
                  response_data: null,
                },
              ],
        total: 2,
      },
    })
  })
  await page.getByRole('button', { name: '历史与任务', exact: true }).click()
  await page.getByRole('button', { name: /上次的讨论/ }).click()
  await expect(page.locator('.turn')).toHaveCount(1)
  await expect(page.locator('.turn')).toContainText('末页回答')
})

test('学校会话失效显示具体原因，应用会话过期清空学校数据', async ({ page }) => {
  await setup(page)
  await page.getByRole('button', { name: '学校事务', exact: true }).click()
  await page.route('**/api/courses/7/sync', (route) =>
    route.fulfill({
      status: 409,
      json: { detail: { code: 'school_login_required', message: '学校会话缺失或已过期' } },
    }),
  )
  await page.getByRole('button', { name: '同步课程', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('学校会话缺失或已过期')
  await page.route('**/api/courses?*', (route) =>
    route.fulfill({ status: 401, json: { detail: '登录已过期' } }),
  )
  await page.getByRole('button', { name: '刷新列表' }).click()
  await expect(page.getByRole('dialog')).toContainText('登录已过期')
  await expect(page.locator('.school-page')).toHaveCount(0)
})
