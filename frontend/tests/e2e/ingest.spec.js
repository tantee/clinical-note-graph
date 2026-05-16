// Browser E2E. Requires the full stack running (docker compose up).
// Run via: `npm run e2e` after `docker compose up -d`.

import { test, expect } from '@playwright/test'

test('ingest a sample admission and open the patient', async ({ page }) => {
  await page.goto('/#/ingest')
  await page.getByRole('button', { name: /load sample/i }).click()
  await page.getByRole('menuitem', { name: /admission/i }).click()

  // Make patientId unique per test run so re-runs stay isolated.
  const pid = `PW-${Date.now()}`
  await page.getByLabel('Patient ID').fill(pid)
  await page.getByRole('button', { name: /send to backend/i }).click()

  await expect(page.getByText('Open patient')).toBeVisible({ timeout: 60_000 })
  await page.getByRole('link', { name: /open patient/i }).click()

  await expect(page.getByRole('heading', { name: new RegExp(pid) })).toBeVisible()
  // Tabs render
  await page.getByRole('tab', { name: /timeline/i }).click()
  await expect(page.getByText(/admission/i)).toBeVisible()
  await page.getByRole('tab', { name: /notes/i }).click()
  await expect(page.getByText(/index.md/i)).toBeVisible()
})

test('config patch round-trips', async ({ page }) => {
  await page.goto('/#/config')
  await page.getByLabel('Model').fill('gpt-4o-mini')
  await page.getByRole('button', { name: /save changes/i }).click()
  await expect(page.getByText(/configuration saved/i)).toBeVisible()
})
