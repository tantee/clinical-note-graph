// Browser E2E. Requires the full stack running (docker compose up).
// Run via: `npm run e2e` after `docker compose up -d`.

import { test, expect } from '@playwright/test'


test('ingest queues a job, JobWatcher completes, navigates to patient', async ({ page }) => {
  await page.goto('/#/ingest')
  await page.getByRole('button', { name: /load sample/i }).click()
  await page.getByRole('menuitem', { name: /admission/i }).click()

  // Unique patient ID so reruns stay isolated.
  const pid = `PW-${Date.now()}`
  // The v-autocomplete renders an input that we can type into.
  const patientInput = page.getByLabel('Patient ID')
  await patientInput.fill(pid)

  await page.getByRole('button', { name: /^submit$/i }).click()

  // JobWatcher card renders almost immediately.
  await expect(page.getByText(/ingest job/i)).toBeVisible()

  // On completion, the router navigates to /#/patients/{pid}.
  await page.waitForURL(new RegExp(`#/patients/${pid}`), { timeout: 90_000 })
  await expect(page.getByRole('heading', { name: new RegExp(pid) })).toBeVisible()
})


test('config patch round-trips', async ({ page }) => {
  await page.goto('/#/config')
  // The Model field on the AI provider card.
  const modelField = page.getByLabel('Model').first()
  await modelField.fill('gpt-4o-mini')
  await page.getByRole('button', { name: /save changes/i }).click()
  await expect(page.getByText(/configuration saved/i)).toBeVisible()
})
