import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  // Pick up both the legacy tests/e2e/*.spec.{js,ts} and the new e2e/*.spec.{js,ts}
  testMatch: ['tests/e2e/**/*.spec.{js,ts}', 'e2e/**/*.spec.{js,ts}'],
  fullyParallel: true,
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:8081',
    trace: 'on-first-retry',
  },
  reporter: process.env.CI ? 'github' : 'list',
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
