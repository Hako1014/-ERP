import { defineConfig } from '@playwright/test'

const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:5273'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  reporter: 'list',
  use: {
    baseURL,
    browserName: 'chromium',
    launchOptions: process.env.E2E_BROWSER_PATH || process.platform === 'win32'
      ? { executablePath: process.env.E2E_BROWSER_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' }
      : undefined,
    headless: true,
    trace: 'retain-on-failure',
  },
})
