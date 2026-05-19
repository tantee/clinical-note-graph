import { test, expect } from '@playwright/test'

// Smoke test for the graph scope/filter UX.
// Uses hash-mode URLs (createWebHashHistory) — see frontend/src/router.js.

test('patient Graph tab renders the canvas and toolbar', async ({ page }) => {
  await page.goto('/#/patient/HN-DEMO-1')

  // Switch to the Graph tab on the patient page.
  await page.getByRole('tab', { name: /Graph/i }).click()

  // Toolbar chip group is visible (we're in patient scope).
  await expect(page.getByText(/^All$/)).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText(/Latest encounter/i)).toBeVisible()
  await expect(page.getByText(/Pick…/)).toBeVisible()

  // Open the filter drawer.
  await page.getByRole('button', { name: /Filters/i }).click()
  await expect(page.locator('text=Node types')).toBeVisible()

  // Close drawer to clear the overlay.
  await page.keyboard.press('Escape')
})

test('encounter URL opens dialog with Graph tab', async ({ page }) => {
  // Visit patient page and grab the first encounter id from the Encounters tab.
  await page.goto('/#/patient/HN-DEMO-1')
  await page.getByRole('tab', { name: /Encounters/i }).click()
  const firstViewBtn = page.getByRole('button', { name: /^View$/i }).first()
  await firstViewBtn.click()

  // Dialog should now be open. Confirm the tab control is present.
  await expect(page.getByRole('tab', { name: /Detail/i })).toBeVisible()
  await expect(page.getByRole('tab', { name: /Graph/i })).toBeVisible()

  // Switch to Graph tab inside the dialog.
  await page.getByRole('tab', { name: /Graph/i }).last().click()

  // Encounter scope hides the chip group; we should see "Encounter scope · N encounter(s)".
  await expect(page.getByText(/Encounter scope/i)).toBeVisible({ timeout: 10_000 })

  // Close dialog.
  await page.getByRole('button', { name: /Close/i }).click()
})
