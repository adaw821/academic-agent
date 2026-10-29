import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  globalSetup: './tests/server-setup.ts',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
