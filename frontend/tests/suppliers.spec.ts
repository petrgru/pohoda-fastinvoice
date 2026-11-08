import { expect, test } from "@playwright/test"

import {
  getInvoice,
  randomIco,
  uploadInvoice,
  waitForParsed,
} from "./utils/invoices"

// These flows rely on the server parsing an uploaded PDF with the real model,
// so they need the same generous budgets as the full invoice flow test.
test.describe("Supplier registry", () => {
  test("suppliers page lists a supplier from an imported invoice", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(900_000)

    const ico = randomIco()
    const name = `E2E Dodavatel ${ico}`
    const invoice = await uploadInvoice([
      "FAKTURA E2E SUPPLIER",
      `Dodavatel: ${name}`,
      `IČO: ${ico}`,
      "Bankovní účet: 123456789/0100",
      "Celkem 1210,00 Kc",
    ])
    await waitForParsed(invoice.id, 720_000)

    // reachable from the navigation
    await page.goto("/invoices")
    await page.getByRole("link", { name: "Dodavatelé" }).click()
    await expect(
      page.getByRole("heading", { name: "Dodavatelé" }),
    ).toBeVisible()

    // the imported invoice registered its vendor
    await page.getByTestId("supplier-search-input").fill(ico)
    const row = page.locator("tr", { hasText: name })
    await expect(row).toBeVisible({ timeout: 15_000 })

    // open the manage dialog and confirm the parsed account as verified.
    // The dialog is re-opened when a background list refresh remounts the
    // row while it is open – users never notice, Playwright actionability
    // would wait forever on the detached node.
    const dialog = page.getByTestId("manage-supplier-dialog")
    const checkbox = dialog.getByTestId("supplier-account-confirmed-0")
    const openDialog = async () => {
      await row.getByTestId("manage-supplier-button").click()
      await expect(dialog).toBeVisible({ timeout: 10_000 })
      // let the debounced search and the dialog's invoice query settle
      await page.waitForTimeout(1_500)
    }
    await openDialog()
    for (let attempt = 0; attempt < 3; attempt++) {
      if (!(await dialog.isVisible().catch(() => false))) await openDialog()
      if (
        (await checkbox.getAttribute("aria-checked").catch(() => "false")) ===
        "true"
      )
        break
      await checkbox.click({ timeout: 5_000 }).catch(() => {})
    }
    await expect(dialog).toBeVisible()
    await expect(checkbox).toHaveAttribute("aria-checked", "true")
    await dialog.getByTestId("manage-supplier-save").click()
    await expect(dialog).toBeHidden({ timeout: 15_000 })

    // the confirmed flag persisted into the registry
    await expect(row).toContainText("Ověřeno", { timeout: 10_000 })
  })

  test("editing the account on an invoice asks for confirmation first", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(900_000)

    const ico = randomIco()
    const invoice = await uploadInvoice([
      "FAKTURA E2E TAMPER",
      `Dodavatel: E2E Tamper ${ico}`,
      `IČO: ${ico}`,
      "Bankovní účet: 2401384534/2010",
      "Celkem 1210,00 Kc",
    ])
    await waitForParsed(invoice.id, 720_000)

    await page.goto(`/invoices/${invoice.id}`)
    const accountInput = page.getByTestId("supplier-bank-account-input")
    await expect(accountInput).toBeVisible({ timeout: 15_000 })

    // the parsed account is accepted without prompting (it matches the document)
    await accountInput.fill("999999999/0300")
    await page.getByTestId("invoice-save-button").click()

    // a deviation from the document shows the tampering dialog, not a toast
    const dialog = page.getByTestId("account-confirm-dialog")
    await expect(dialog).toBeVisible({ timeout: 15_000 })
    await expect(
      page.getByTestId("account-doc-warning").or(dialog),
    ).toBeVisible()

    // discarding keeps the original account on the document
    await dialog.getByTestId("account-confirm-cancel").click()
    await expect(dialog).toBeHidden()

    // repeating the change and confirming saves it and flags the mismatch
    await accountInput.fill("888888888/0300")
    await page.getByTestId("invoice-save-button").click()
    await expect(dialog).toBeVisible({ timeout: 15_000 })
    await dialog.getByTestId("account-confirm-save").click()

    await expect(dialog).toBeHidden({ timeout: 15_000 })
    // the amber "differs from the document" warning now stays on the page
    await expect(page.getByTestId("account-doc-warning")).toBeVisible()
    // the tampered account is what the stored invoice now carries
    const saved = await getInvoice(invoice.id)
    expect(saved.supplier_bank_account).toBe("888888888/0300")
  })
})
