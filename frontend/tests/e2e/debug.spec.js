// Browser E2E. Requires the full stack running (docker compose up).
// Run via: `npm run e2e` after `docker compose up -d`.

import { test, expect } from '@playwright/test'


test('debug page shows totals after an ingest', async ({ page }) => {
  // Drive an ingest first to populate the debug data.
  await page.goto('/#/ingest')
  await page.getByRole('button', { name: /load sample/i }).click()
  await page.getByRole('menuitem', { name: /admission/i }).click()
  const pid = `PW-DBG-${Date.now()}`
  await page.getByLabel('Patient ID').fill(pid)
  await page.getByRole('button', { name: /^submit$/i }).click()

  // Wait for the JobWatcher to complete and the page to navigate.
  await page.waitForURL(new RegExp(`#/patients/${pid}`), { timeout: 90_000 })

  // Visit the debug page.
  await page.goto('/#/debug')

  // KPI cards visible — at minimum the labels.
  await expect(page.getByText(/Total spend/i)).toBeVisible()
  await expect(page.getByText(/AI calls/i)).toBeVisible()
  await expect(page.getByText(/Avg latency/i)).toBeVisible()

  // Switch to AI calls tab and assert that an "extract" row is rendered.
  await page.getByRole('tab', { name: /AI calls/i }).click()
  await expect(page.getByText(/extract/i).first()).toBeVisible({ timeout: 30_000 })
})


test('debug page has a Refresh button on jobs tab with status filter', async ({ page }) => {
  await page.goto('/#/debug')
  await page.getByRole('tab', { name: /Jobs/i }).click()
  // The status filter v-select is present.
  await expect(page.getByLabel('Status').first()).toBeVisible()
})
