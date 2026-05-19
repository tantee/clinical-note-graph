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
  // Vuetify's `v-btn :to="..."` renders as an <a> (role=link), not <button>.
  const firstViewBtn = page.getByRole('link', { name: /^View$/i }).first()
  await firstViewBtn.click()

  // Dialog should now be open. The dialog has its own Detail/Graph tabs;
  // the underlying patient page ALSO has a Graph tab, so scope queries to
  // the dialog by using .last() (most recently mounted).
  await expect(page.getByRole('tab', { name: /Detail/i })).toBeVisible()
  const dialogGraphTab = page.getByRole('tab', { name: /Graph/i }).last()
  await expect(dialogGraphTab).toBeVisible()

  // Switch to Graph tab inside the dialog.
  await dialogGraphTab.click()

  // Encounter scope hides the chip group; we should see "Encounter scope · N encounter(s)".
  await expect(page.getByText(/Encounter scope/i)).toBeVisible({ timeout: 10_000 })
  // (Dialog close not asserted — multiple "Close" labels in the DOM, e.g.,
  //  snackbar dismiss buttons — and Playwright tears down the page anyway.)
})
