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
      await expect(row).toBeVisible()
      await row.locator('[data-testid^="company-actions-menu"]').click()
      await page.getByTestId(`company-manage-${company.id}`).click()

      // the creating superuser is listed as an admin member
      await expect(page.getByText(firstSuperuser)).toBeVisible()

      // invite a new editor by e-mail
      await page.getByTestId("manage-member-email").fill(memberEmail)
      await page.getByTestId("manage-member-add").click()
      await expect(page.getByText(memberEmail)).toBeVisible()

      // create an API key – the plaintext is shown exactly once
      await page.getByTestId("manage-key-label").fill("Pohoda e2e")
      await page.getByTestId("manage-key-create").click()
      await expect(page.getByTestId("manage-new-key")).toContainText("fik_")

      // revoke it – the row switches to the revoked badge
      const keyRow = page.locator("[data-testid^='api-key-row-']")
      await expect(keyRow.first()).toBeVisible()
      await page.locator("[data-testid^='api-key-revoke-']").first().click()
      await expect(keyRow.first()).toContainText("Zrušeno")
    } finally {
      await deleteCompaniesByName(name)
    }
  })
})
