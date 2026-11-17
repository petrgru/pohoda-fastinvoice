import { expect, test } from "@playwright/test"

import { firstSuperuser } from "./config.ts"
import { createCompany, deleteCompaniesByName } from "./utils/invoices"

test.describe("Company management", () => {
  const name = `E2E Správa ${Date.now()}`
  const memberEmail = `e2e-member-${Date.now()}@example.com`

  test("members and API keys can be managed from the company row", async ({
    page,
  }) => {
    const company = await createCompany(name)
    try {
      await page.goto("/companies")
      await page.getByTestId("company-search-input").fill(name)
      const row = page.locator("tr", { hasText: name })
      await expect(row).toBeVisible({ timeout: 15_000 })
      await row.locator('[data-testid^="company-actions-menu"]').click()
      await page.getByTestId(`company-manage-${company.id}`).click()

      // the creating superuser is listed as an admin member (scoped to the
      // member rows – the sidebar user menu shows the same e-mail)
      await expect(
        page
          .locator('[data-testid^="member-row-"]')
          .filter({ hasText: firstSuperuser }),
      ).toBeVisible()

      // invite a new editor by e-mail
      await page.getByTestId("manage-member-email").fill(memberEmail)
      await page.getByTestId("manage-member-add").click()
      await expect(page.getByText(memberEmail)).toBeVisible()

      // create an API key – the plaintext is shown exactly once
      await page.getByTestId("manage-key-label").fill("Pohoda e2e")
      await page.getByTestId("manage-key-create").click()
      await expect(page.getByTestId("manage-new-key")).toContainText("fik_")

      // only one key per company – a new key replaces (deletes) the old one
      const keyRows = page.locator("[data-testid^='api-key-row-']")
      await expect(keyRows).toHaveCount(1, { timeout: 15_000 })
      await page.getByTestId("manage-rotate-key").click()
      await page.getByTestId("manage-rotate-confirm").click()
      await expect(page.getByTestId("manage-new-key")).toContainText("fik_")
      await expect(keyRows).toHaveCount(1, { timeout: 15_000 })

      // revoking deletes the key for good – the create form comes back
      // (retry: right after the rotation the list may still show the old row)
      await expect(async () => {
        const buttons = page.locator("[data-testid^='api-key-revoke-']")
        if ((await buttons.count()) > 0) {
          await buttons.first().click()
        }
        await expect(keyRows).toHaveCount(0, { timeout: 2_000 })
      }).toPass({ timeout: 20_000 })
      await expect(page.getByTestId("manage-key-create")).toBeVisible()
    } finally {
      await deleteCompaniesByName(name)
    }
  })
})
