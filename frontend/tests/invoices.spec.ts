import { fileURLToPath } from "node:url"
import { expect, test } from "@playwright/test"

import {
  createCompany,
  deleteCompaniesByName,
  deleteCompanyByIco,
  deleteInvoicesByFilename,
  getInvoice,
  markInvoiceExported,
  randomIco,
  tryDeleteInvoice,
  uploadInvoice,
  waitForParsed,
} from "./utils/invoices"

test.describe("Invoice workflow", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/invoices")
  })

  test("invoices page is accessible", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Faktury" })).toBeVisible()
    await expect(page.getByTestId("upload-invoice-button")).toBeVisible()
  })

  test("companies page is accessible", async ({ page }) => {
    await page.goto("/companies")
    await expect(
      page.getByRole("heading", { name: "Naše firmy" }),
    ).toBeVisible()
    await expect(page.getByTestId("add-company-button")).toBeVisible()
  })

  test("queue page is accessible", async ({ page }) => {
    await page.goto("/queue")
    await expect(
      page.getByRole("heading", { name: "Fronta úloh" }),
    ).toBeVisible()
  })

  test("dashboard Plocha shows statistics and finds invoices", async ({
    page,
  }) => {
    test.slow()
    const marker = `PLOCHA${Date.now()}`
    const invoice = await uploadInvoice([
      `Faktura ${marker}`,
      "Dodavatel: Plocha Test s.r.o.",
      "Celkem 1210,00 Kc",
    ])
    await waitForParsed(invoice.id)

    await page.goto("/")
    // the renamed tab lands on the statistics overview
    await expect(page.getByText("Plocha").first()).toBeVisible()
    await expect(page.getByTestId("stats-chart")).toBeVisible()
    await expect(page.getByTestId("stats-card-needs_review")).toBeVisible()
    await page.getByTestId("stats-tab-week").click()
    await page.getByTestId("stats-tab-month").click()
    await expect(page.getByTestId("stats-chart")).toBeVisible()

    // the full-text box finds the uploaded document by its number
    await page.getByTestId("dashboard-search").fill(marker)
    const results = page.getByTestId("dashboard-search-result")
    await expect(results.first()).toBeVisible({ timeout: 15_000 })
    await expect(results.first()).toContainText(marker)
  })

  test("create company via UI", async ({ page }) => {
    const name = `E2E Firma ${randomIco()}`
    await page.goto("/companies")
    await page.getByTestId("add-company-button").click()
    await page.getByPlaceholder("Název firmy").fill(name)
    await page.getByPlaceholder("8 číslic").fill(randomIco())
    await page.getByPlaceholder("123456789/0100").fill("123456789/0100")
    await page.getByTestId("add-company-confirm-button").click()
    await expect(page.getByText(name)).toBeVisible()
  })

  test("full flow: upload → review → company → approve → export lock", async ({
    page,
  }) => {
    test.slow()
    // real LLM parsing of the uploaded PDF runs in the background
    test.setTimeout(150_000)

    // Seed: company + uploaded invoice (parsed in background on the server)
    const company = await createCompany(`E2E Dodavatel ${Date.now()}`)
    const invoice = await uploadInvoice([
      "FAKTURA E2E",
      "Dodavatel: E2E dodavatel s.r.o.",
      "Celkem 1210,00 Kc",
    ])

    // Open the detail right away – while the server parses in background,
    // the page must pick up the finished state on its own (no reload)
    const initial = await getInvoice(invoice.id)
    await page.goto(`/invoices/${invoice.id}`)
    if (initial.state === "uploaded" || initial.state === "parsing") {
      // approve stays locked until the document is parsed
      await expect(page.getByTestId("approve-button")).toBeDisabled()
      await expect(
        page.getByText(/zpřístupní po načtení dokladu/i),
      ).toBeVisible()
      await expect(page.getByTestId("recipient-alert")).toHaveCount(0)
    }
    await expect(page.getByTestId("approve-button")).toBeEnabled({
      timeout: 90_000,
    })
    await expect(page.getByTestId("state-badge")).toHaveText("Ke kontrole")

    // The original preview can be enlarged in a dialog
    await page.getByTestId("expand-preview-button").click()
    await expect(page.getByTestId("preview-dialog")).toBeVisible()
    await page.keyboard.press("Escape")
    await expect(page.getByTestId("preview-dialog")).toHaveCount(0)

    const parsed = await waitForParsed(invoice.id)
    expect(parsed.company_id).toBeNull()

    // The invoice waits in the queue with a reason (company not assigned)
    await page.goto("/queue")
    await expect(page.getByTestId(`queue-row-${invoice.id}`)).toBeVisible()

    // Detail: reason shown, approve is not possible without a recipient
    await page.goto(`/invoices/${invoice.id}`)
    await expect(page.getByTestId("queue-reasons-alert")).toContainText(
      "Zatím není vybrána firma příjemce",
    )
    await page.getByTestId("approve-button").click()
    await expect(
      page
        .locator("[data-sonner-toaster]")
        .getByText(/není vybrána vaše firma/i),
    ).toBeVisible({ timeout: 10_000 })

    // Assign the company → approve switches to export queue
    await page.getByTestId("company-select").click()
    await page.getByRole("option", { name: company.name }).click()
    await expect(page.getByTestId("state-badge")).toBeVisible()
    await page.getByTestId("approve-button").click()
    await expect(page.getByTestId("state-badge")).toHaveText("K exportu", {
      timeout: 10_000,
    })

    // Simulate the Pohoda connector reporting a successful import
    await markInvoiceExported(invoice.id)
    await page.reload()
    await expect(page.getByTestId("state-badge")).toHaveText("Exportováno")
    await expect(page.getByText("Faktura je uložena v systému")).toBeVisible()

    // Exported invoices cannot be deleted (API + UI)
    expect(await tryDeleteInvoice(invoice.id)).toBe(403)
    await page.getByTestId(`invoice-actions-menu-${invoice.id}`).click()
    await expect(page.getByTestId("delete-invoice-menu-item")).toHaveCount(0)
  })

  test("adds our company from the document when no recipient matches", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(300_000)

    const ico = randomIco()
    // the document names only a supplier – no recipient block to detect
    const invoice = await uploadInvoice([
      "FAKTURA PRIJEMCE",
      `Dodavatel: Účetní Téma ${randomIco()} s.r.o.`,
      `ICO: ${ico}`,
      "Celkem 4560,00 Kc",
    ])
    await page.goto(`/invoices/${invoice.id}`)
    await expect(page.getByTestId("state-badge")).toHaveText("Ke kontrole", {
      timeout: 180_000,
    })

    // no recipient detected on the document → no hint, but "add company" works
    await expect(page.getByTestId("recipient-alert")).toHaveCount(0)
    await page.getByTestId("add-company-for-invoice-button").click()
    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()
    await dialog.getByPlaceholder("Název firmy").fill(`Naše Firma ${ico}`)
    await dialog.getByPlaceholder("8 číslic").fill(randomIco())
    // the account is required – confirm is blocked empty
    await page.getByTestId("add-company-for-invoice-confirm").click()
    await expect(page.getByText("Číslo účtu je povinné")).toBeVisible()
    await dialog.getByPlaceholder("123456789/0100").fill("123456789/0100")

    await page.getByTestId("add-company-for-invoice-confirm").click()
    await expect(
      page.getByTestId("add-company-for-invoice-button"),
    ).toHaveCount(0)
    await expect(page.getByTestId("queue-reasons-alert")).not.toContainText(
      "Zatím není vybrána firma příjemce",
      { timeout: 10_000 },
    )
  })

  test("multi-rate invoice lists the VAT breakdown per rate", async ({
    page,
  }) => {
    test.slow()
    // two rates on one document – the rule parser must fill both lines
    const invoice = await uploadInvoice([
      `Faktura E2EDPH${Date.now()}`,
      "Dodavatel: DPH Varianta s.r.o.",
      "ICO: 47123737  DIC: CZ47123737",
      "Datum vystaveni: 2026-04-01  Splatnost: 2026-04-15",
      "Sazba DPH 12 % Zaklad DPH 1 000,00 Kc DPH 120,00 Kc",
      "Sazba DPH 21 % Zaklad DPH 2 000,00 Kc DPH 420,00 Kc",
      "Celkem k Uhrade 3 540,00 Kc",
    ])
    await waitForParsed(invoice.id)
    await page.goto(`/invoices/${invoice.id}`)
    await expect(page.getByTestId("state-badge")).toHaveText("Ke kontrole")

    const table = page.getByTestId("vat-items-table")
    await expect(table).toBeVisible()
    await expect(
      table.getByRole("cell", { name: "12%", exact: true }),
    ).toBeVisible()
    await expect(
      table.getByRole("cell", { name: "21%", exact: true }),
    ).toBeVisible()
    // rows plus the totals footer
    await expect(table.getByRole("row")).toHaveCount(4)
    // the lines add up to the total – no mismatch note
    await expect(page.getByText("Položky DPH nesouhlasí")).toHaveCount(0)
  })

  test("shows add-firm even without a parsed supplier (detail + queue)", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(150_000)

    const ico = randomIco()
    // no supplier data in the document at all
    const invoice = await uploadInvoice([
      "FAKTURA BEZ DODAVATELE",
      "Celkem 120,00 Kc",
    ])

    // Detail: the recipient hint cannot appear, but "add company" is visible
    await page.goto(`/invoices/${invoice.id}`)
    await expect(page.getByTestId("state-badge")).toHaveText("Ke kontrole", {
      timeout: 90_000,
    })
    await expect(page.getByTestId("recipient-alert")).toHaveCount(0)
    await page.getByTestId("add-company-for-invoice-button").click()
    const dialog = page.getByRole("dialog")
    await dialog
      .getByPlaceholder("Název firmy")
      .fill(`Bez Dodavatele ${ico} s.r.o.`)
    await dialog.getByPlaceholder("8 číslic").fill(ico)
    await dialog.getByPlaceholder("123456789/0100").fill("23456789/0100")
    // a second account with an IBAN (like invoices from Festo list)
    await dialog.getByTestId("add-bank-account-button").click()
    await dialog
      .getByTestId("extra-account-row")
      .getByPlaceholder("123456789/0100")
      .fill("0502-87654321/0100")
    await dialog
      .getByPlaceholder("CZ88 0300 0000 0000 0091 2923")
      .last()
      .fill("CZ65 0300 0000 0000 0091 2923")
    await page.getByTestId("add-company-for-invoice-confirm").click()

    // created + assigned → the affordances disappear
    await expect(
      page.getByTestId("add-company-for-invoice-button"),
    ).toHaveCount(0)
    await expect(page.getByText(`IČO: ${ico}`).first()).toBeVisible()

    // Queue: an invoice without a company carries the same button
    const queued = await uploadInvoice([
      "FAKTURA FRONTA FIRMA",
      "Dodavatel: Queue Firm Test Co",
      "Celkem 460,00 Kc",
    ])
    await page.goto("/queue")
    const row = page.getByTestId(`queue-row-${queued.id}`)
    await expect(row).toBeVisible({ timeout: 20_000 })
    // the button is present right away but disabled until the document is parsed
    await expect(
      row.getByTestId("add-company-for-invoice-button"),
    ).toBeVisible()
    await expect(row.getByTestId("add-company-for-invoice-button")).toBeEnabled(
      {
        timeout: 150_000,
      },
    )
    await row.getByTestId("add-company-for-invoice-button").click()
    const queueDialog = page.getByRole("dialog")
    const ico2 = randomIco()
    await queueDialog
      .getByPlaceholder("Název firmy")
      .fill(`Queue Firma ${ico2}`)
    await queueDialog.getByPlaceholder("8 číslic").fill(ico2)
    await queueDialog.getByPlaceholder("123456789/0100").fill("34567891/0300")
    await page.getByTestId("add-company-for-invoice-confirm").click()
    // the polling queue must drop the button once a company is assigned
    await expect(row.getByTestId("add-company-for-invoice-button")).toHaveCount(
      0,
      { timeout: 15_000 },
    )
  })

  test("real invoice PDF – recipient is picked and the invoice approved", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(180_000)

    // the real BNET invoice has a fixed filename (content-addressed uploads)
    // and a fixed IČO – start from a clean state so the run is repeatable
    await deleteInvoicesByFilename("364275_7926019675.pdf")
    await deleteCompanyByIco("06846904")
    const ours = await createCompany(`E2E Příjemce ${Date.now()}`)

    await page.getByTestId("upload-invoice-button").click()
    await page
      .getByTestId("invoice-file-input")
      .setInputFiles(
        fileURLToPath(
          new URL("../../test_data/364275_7926019675.pdf", import.meta.url),
        ),
      )
    const [uploadResponse] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/invoices/upload"), {
        timeout: 30_000,
      }),
      page.getByTestId("upload-invoice-confirm-button").click(),
    ])
    expect(uploadResponse.status()).toBe(201)
    await expect(page).toHaveURL(/\/invoices\/[0-9a-f-]{36}/, {
      timeout: 10_000,
    })
    // wait until the detail route has actually rendered (the list DOM can
    // still be visible for a moment after the URL changes)
    await expect(page.getByTestId("company-select")).toBeVisible({
      timeout: 15_000,
    })

    // the LLM parses the document (supplier IČO 06846904, FIO account
    // 2401384534/2010) – parsing with the real model takes up to ~1 minute
    await expect(page.getByTestId("state-badge")).toHaveText("Ke kontrole", {
      timeout: 120_000,
    })

    // pick our company as the recipient and approve for export
    await page.getByTestId("company-select").click()
    await page.getByRole("option", { name: ours.name }).click()
    await expect(
      page.getByTestId("add-company-for-invoice-button"),
    ).toHaveCount(0, { timeout: 20_000 })
    await page.getByTestId("approve-button").click()
    await expect(page.getByTestId("state-badge")).toHaveText("K exportu", {
      timeout: 15_000,
    })
  })

  test("Festo invoice – supplier accounts and VAT lines from a long document", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(480_000)

    // content-addressed uploads keep the original filename; stale Festo
    // companies from earlier runs must not affect the run
    await deleteInvoicesByFilename("FAKTURA_1333134828.PDF")
    await deleteCompanyByIco("00564737")
    await deleteCompaniesByName("Festo")

    await page.getByTestId("upload-invoice-button").click()
    await page
      .getByTestId("invoice-file-input")
      .setInputFiles(
        fileURLToPath(
          new URL("../../test_data/FAKTURA_1333134828.PDF", import.meta.url),
        ),
      )
    const [uploadResponse] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/invoices/upload"), {
        timeout: 30_000,
      }),
      page.getByTestId("upload-invoice-confirm-button").click(),
    ])
    expect(uploadResponse.status()).toBe(201)
    await expect(page).toHaveURL(/\/invoices\/[0-9a-f-]{36}/, {
      timeout: 10_000,
    })
    await expect(page.getByTestId("company-select")).toBeVisible({
      timeout: 15_000,
    })
    await expect(page.getByTestId("state-badge")).toHaveText("Ke kontrole", {
      // worst case: plain attempt (180s timeout) + guided retry (150s)
      timeout: 420_000,
    })

    // the EUR total carries the 21 % VAT line (60.22 + 12.65 = 72.87)
    const vatTable = page.getByTestId("vat-items-table")
    await expect(vatTable).toBeVisible()
    await expect(vatTable).toContainText("21%")
    // Festo lists several payment accounts – the edit form shows them all
    await expect(page.getByTestId("supplier-account-row").first()).toBeVisible({
      timeout: 10_000,
    })
    const account = page.getByTestId("supplier-account-row").first()
    const accountFilled =
      (await account.getByPlaceholder("123456789/0100").inputValue()) ||
      (await account
        .getByPlaceholder("CZ88 0300 0000 0000 0091 2923")
        .inputValue())
    expect(accountFilled).not.toBe("")
  })

  test("queue refreshes itself when a document is uploaded", async ({
    page,
  }) => {
    test.setTimeout(120_000)
    // open the queue first, then upload while it stays mounted
    await page.goto("/queue")
    const invoice = await uploadInvoice([
      "FAKTURA FRONTA",
      "Dodavatel: Fronta Test s.r.o.",
      "Celkem 100,00 Kc",
    ])
    // the already-open page must pick the new row up without a manual reload
    await expect(
      page.getByTestId(`queue-row-${invoice.id}`).first(),
    ).toBeVisible({
      timeout: 15_000,
    })
  })

  test("switches the UI language and remembers the choice", async ({
    page,
  }) => {
    await page.getByTestId("language-button").click()
    await page.getByTestId("language-en").click()
    await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible()
    // the choice is persisted, so it survives a reload
    await page.reload()
    await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible()
    await page.getByTestId("language-button").click()
    await page.getByTestId("language-de").click()
    await expect(
      page.getByRole("heading", { name: "Rechnungen" }),
    ).toBeVisible()
  })

  test.describe("stale session", () => {
    // no logged-in state – the page runs with just the expired token
    test.use({ storageState: { cookies: [], origins: [] } })

    test("stale session redirects to the login page", async ({ page }) => {
      await page.addInitScript(() => {
        // simulate a leftover token of an expired/deleted session (once, so the
        // redirect to /login is not undone by re-injecting the token)
        if (!sessionStorage.getItem("stale-injected")) {
          sessionStorage.setItem("stale-injected", "1")
          window.localStorage.setItem("access_token", "expired-session-token")
        }
      })
      await page.goto("/invoices")
      await expect(page).toHaveURL(/\/login/)
      await expect(page.getByTestId("email-input")).toBeVisible()
    })
  })
})
