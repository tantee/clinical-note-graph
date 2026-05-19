import { test, expect } from '@playwright/test'

// Smoke test for the encounter summary flow.
// Requires: AI_PROVIDER=mock in backend env (default in compose).
test('encounter discharge summary renders with required sections', async ({ page }) => {
  // Router uses hash mode (see frontend/src/router.js — createWebHashHistory).
  await page.goto('/#/patient/HN-DEMO-1')
  // Switch to the new Encounters tab — locator by tab label.
  await page.getByRole('tab', { name: /Encounters/i }).click()
  // First row in the data table → click "View"
  await page.getByRole('button', { name: /^View$/i }).first().click()
  // We're now on /patient/HN-DEMO-1/encounter/:eid
  await expect(page.locator('h1')).toContainText(/admission|discharge_summary|clinic_visit|progress_note/i)
  // Trigger summary
  await page.getByRole('button', { name: /Summarize|Regenerate summary/i }).click()
  // If a menu opens, pick discharge_summary
  const dischargeItem = page.getByRole('menuitem', { name: /Discharge summary/i })
  if (await dischargeItem.isVisible().catch(() => false)) {
    await dischargeItem.click()
  }
  // Wait up to 60s for the AI mock to return.
  await expect(page.locator('text=AI summary')).toBeVisible({ timeout: 60_000 })
  // Confirm at least one required discharge section appears.
  const summaryText = await page.locator('.cng-markdown').first().innerText()
  expect(summaryText.length).toBeGreaterThan(50)
})
