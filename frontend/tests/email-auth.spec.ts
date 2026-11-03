import { test, expect } from '@playwright/test'

test('邮箱验证码注册与忘记密码入口', async ({ page }) => {
  const calls: { path: string; body: any }[] = []
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/user/me') return route.fulfill({ status: 401, json: { detail: '请先登录' } })
    calls.push({ path, body: route.request().postDataJSON() })
    return route.fulfill({ json: { message: '请检查邮箱', retry_after: 60 } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '登录，开启研究' }).click()
  await page.getByRole('button', { name: '立即注册' }).click()
  await page.getByLabel('用户名', { exact: true }).fill('同学')
  await page.getByLabel('手机号（选填）').fill('13800138000')
  await page.getByLabel('邮箱', { exact: true }).fill('student@example.com')
  await page.getByRole('button', { name: '发送验证码', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '请检查邮箱' })).toBeVisible()
  expect(calls[0]).toEqual({
    path: '/api/user/email-code',
    body: { email: 'student@example.com', purpose: 'register' },
  })
  await page.getByLabel('密码', { exact: true }).fill('password123')
  await page.getByLabel('确认密码', { exact: true }).fill('password124')
  await page.getByLabel('邮箱验证码', { exact: true }).fill('123456')
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('两次输入的密码不一致')
  await page.getByLabel('确认密码', { exact: true }).fill('password123')
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '欢迎回到你的知识空间' })).toBeVisible()
  expect(calls.at(-1)?.body.code).toBe('123456')
  expect(calls.at(-1)?.body.phone).toBe('13800138000')
  await page.getByRole('button', { name: '忘记密码？' }).click()
  await expect(page.getByRole('heading', { name: '重置应用密码' })).toBeVisible()
  await expect(page.getByLabel('邮箱验证码', { exact: true })).toHaveValue('')
  await page.getByLabel('密码', { exact: true }).fill('newpassword123')
  await page.getByLabel('确认密码', { exact: true }).fill('newpassword123')
  await page.getByLabel('邮箱验证码', { exact: true }).fill('654321')
  await page.getByRole('button', { name: '重置密码', exact: true }).click()
  await expect(page.getByRole('heading', { name: '欢迎回到你的知识空间' })).toBeVisible()
  expect(calls.at(-1)).toEqual({
    path: '/api/user/reset-password',
    body: { email: 'student@example.com', password: 'newpassword123', code: '654321' },
  })
})
