import { existsSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { expect, test } from "@playwright/test"

import { firstSuperuser } from "./config"

import {
  blockScannerSharing,
  createCompany,
  deleteCompaniesByName,
  deleteCompanyByIco,
  deleteInvoicesByFilename,
  fillMissingDates,
  getInvoice,
  makePdfBytes,
  markInvoiceExported,
  randomIco,
  tryDeleteInvoice,
  uploadInvoice,
  waitForParsed,
} from "./utils/invoices"

// GitHub-hosted runners cannot route to the LAN LLM cluster. A document
// that the rule extractor cannot cover (unlabelled identities, raster
// images) only parses when a real model answers; on hosted runners such
// tests would exercise a connection-refused fallback instead of parsing.
const requiresLlm = !!process.env.CI

test.describe("Invoice workflow", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/invoices")
  })

  test("invoices page is accessible", async ({ page }) => {
    // exact: the empty-state heading ("Žádné doklady") contains the title
    await expect(
      page.getByRole("heading", { name: "Doklady", exact: true }),
    ).toBeVisible()
    await expect(page.getByTestId("upload-invoice-button")).toBeVisible()
  })

  test("companies page is accessible", async ({ page }) => {
    await page.goto("/companies")
    await expect(
      page.getByRole("heading", { name: "Naše firmy", exact: true }),
    ).toBeVisible()
    await expect(page.getByTestId("add-company-button")).toBeVisible()
  })

  test("queue page is accessible", async ({ page }) => {
    await page.goto("/queue")
    await expect(
      page.getByRole("heading", { name: "Fronta úloh", exact: true }),
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

  test("company filter narrows the list and detail records the actors", async ({
    page,
  }) => {
    test.slow()
    const stamp = Date.now()
    const company = await createCompany(`FILTR ${stamp}`)
    const markerA = `FILTRA${stamp}`
    const markerB = `FILTRB${stamp}`
    const invoiceA = await uploadInvoice([
      `Faktura ${markerA}`,
      "Dodavatel: Filtr A s.r.o.",
      "Celkem 1210,00 Kc",
    ])
    const invoiceB = await uploadInvoice([
      `Faktura ${markerB}`,
      "Dodavatel: Filtr B s.r.o.",
      "Celkem 1210,00 Kc",
    ])
    await waitForParsed(invoiceA.id)
    await waitForParsed(invoiceB.id)

    // assign the company to B and approve it for export
    await page.goto(`/invoices/${invoiceB.id}`)
    await page.getByTestId("company-select").click()
    await page.getByRole("option", { name: company.name }).click()
    await page.getByTestId("approve-button").click()
    await expect(page.getByTestId("state-badge")).toHaveText("K exportu", {
      timeout: 10_000,
    })

    // detail: who uploaded it and who approved it (with the date)
    await expect(page.getByTestId("invoice-uploaded-by")).toContainText(
      firstSuperuser,
    )
    await expect(page.getByTestId("invoice-approved-by")).toContainText(
      firstSuperuser,
    )

    // the unapproved invoice shows the uploader and a pending note
    await page.goto(`/invoices/${invoiceA.id}`)
    await expect(page.getByTestId("invoice-uploaded-by")).toContainText(
      firstSuperuser,
    )
    await expect(page.getByTestId("invoice-approved-by")).toContainText(
      "čeká na schválení",
    )

    // list: the header switcher scopes the whole app
    await page.goto("/invoices")
    const switcher = page.getByTestId("active-company")
    await expect(switcher).toBeVisible()
    await expect(page.getByText(markerA)).toBeVisible()
    await expect(page.getByText(markerB)).toBeVisible()
    await switcher.click()
    await page.getByRole("option", { name: company.name }).click()
    await expect(page.getByText(markerB)).toBeVisible()
    await expect(page.getByText(markerA)).toHaveCount(0)

    // the task queue follows the active company (the unassigned invoice
    // waits in the queue, approved ones leave it anyway)
    await page.goto("/queue")
    await expect(page.getByTestId(`queue-row-${invoiceA.id}`)).toHaveCount(0)
    await switcher.click()
    await page.getByRole("option", { name: "Všechny firmy" }).click()
    await expect(page.getByTestId(`queue-row-${invoiceA.id}`)).toBeVisible()
    await switcher.click()
    await page.getByRole("option", { name: company.name }).click()

    // the choice survives a reload
    await page.goto("/invoices")
    await page.reload()
    await expect(page.getByTestId("active-company")).toBeVisible()
    await expect(page.getByText(markerB)).toBeVisible()
    await expect(page.getByText(markerA)).toHaveCount(0)

    // the upload dialog preselects the active company (switchable)
    const second = await createCompany(`DRUHA ${stamp}`)
    await page.getByTestId("upload-invoice-button").click()
    await expect(page.getByTestId("upload-recipient-select")).toContainText(
      company.name,
      { timeout: 10_000 },
    )
    await page.getByTestId("upload-recipient-select").click()
    await page.getByRole("option", { name: second.name }).click()
    await page.getByRole("button", { name: "Zrušit" }).click()
    await expect(page.getByTestId("upload-recipient-select")).toHaveCount(0)
    await deleteCompaniesByName(`DRUHA ${stamp}`)

    // clearing the switcher shows all invoices again
    await switcher.click()
    await page.getByRole("option", { name: "Všechny firmy" }).click()
    await expect(page.getByText(markerA)).toBeVisible()
    await expect(page.getByText(markerB)).toBeVisible()

    // DEVELOP=yes cascades the invoices away with the company
    await deleteCompaniesByName(`FILTR ${stamp}`)
  })

  test("missing IČ and DIČ can be picked from the document", async ({
    page,
  }) => {
    test.skip(requiresLlm, "the document needs the LLM, not the rule extractor")
    test.slow()
    const sellerIco = "47123788"
    const buyerIco = "27652375"
    const invoice = await uploadInvoice(
      [
        `Faktura IDENT${Date.now()}`,
        "ALFA SHOP s.r.o.",
        "Na Rohane 886/78, 170 00 Praha 1",
        "Odběratel: NAKUPUJICI a.s.",
        `IČ: ${buyerIco}`,
        `DIČ: CZ${buyerIco}`,
        `reference ${sellerIco}`,
        "Celkem 1210,00 Kc",
      ],
      // the document carries no usable seller identity – the picker test
      // asserts exactly that, so the helper must not invent one
      { supplierIdentity: false },
    )
    await waitForParsed(invoice.id)
    await page.goto(`/invoices/${invoice.id}`)

    // an unlabeled number in the document makes the picker available
    const icoInput = page.getByLabel("IČO")
    if ((await icoInput.inputValue()) !== "") {
      await icoInput.fill("")
    }
    await page.getByTestId("id-candidates-ico-button").click()
    const icoDialog = page.getByTestId("id-candidates-ico-dialog")
    await expect(icoDialog).toBeVisible()
    await expect(
      page.getByTestId(`id-candidate-ico-${sellerIco}`),
    ).toBeVisible()
    await expect(page.getByTestId(`id-candidate-ico-${buyerIco}`)).toBeVisible()
    await page.getByTestId(`id-candidate-ico-${sellerIco}`).click()
    await expect(icoInput).toHaveValue(sellerIco)

    // the buyer-block DIČ is offered too, tagged as the other party
    const dicInput = page.getByLabel("DIČ")
    if ((await dicInput.inputValue()) !== "") {
      await dicInput.fill("")
    }
    await page.getByTestId("id-candidates-dic-button").click()
    await expect(page.getByTestId("id-candidates-dic-dialog")).toBeVisible()
    await expect(
      page.getByTestId(`id-candidate-dic-CZ${buyerIco}`),
    ).toBeVisible()
    await page.getByTestId(`id-candidate-dic-CZ${buyerIco}`).click()
    await expect(dicInput).toHaveValue(`CZ${buyerIco}`)

    // saving persists the picked values
    await page.getByTestId("invoice-save-button").click()
    await expect(
      page.locator("[data-sonner-toaster]").getByText("Uloženo"),
    ).toBeVisible({ timeout: 10_000 })
    await page.reload()
    await expect(page.getByLabel("IČO")).toHaveValue(sellerIco)
  })

  test("create company via UI", async ({ page }) => {
    const name = `E2E Firma ${randomIco()}`
    await page.goto("/companies")
    await page.getByTestId("add-company-button").click()
    await page.getByPlaceholder("Název firmy").fill(name)
    await page.getByPlaceholder("8 číslic").fill(randomIco())
    await page.getByPlaceholder("123456789/0100").fill("123456789/0100")
    await page.getByTestId("add-company-confirm-button").click()
    // the list is sorted by name and paginated – find the new row via search
    await page.getByTestId("company-search-input").fill(name)
    await expect(page.locator("tr", { hasText: name })).toBeVisible({
      timeout: 15_000,
    })
  })

  test("full flow: upload → review → company → approve → export lock", async ({
    page,
  }) => {
    test.slow()
    // real LLM parsing of the uploaded PDF runs in the background
    test.setTimeout(150_000)

    // Seed: company + uploaded invoice (parsed in background on the server)
    // the number must be unique per run – invoices imported by earlier runs
    // stay in the database (exported ones cannot be deleted) and repeated
    // numbers would make this invoice a duplicate of one of them
    const stamp = Date.now()
    const company = await createCompany(`E2E Dodavatel ${stamp}`)
    const invoice = await uploadInvoice([
      `FAKTURA E2E${stamp}`,
      `Dodavatel: E2E dodavatel ${stamp} s.r.o.`,
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
    expect(parsed.recipient_company_id).toBeNull()

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
    await expect(page.getByText("Doklad je uložen v systému")).toBeVisible()

    // Exported invoices cannot be deleted (API + UI)
    expect(await tryDeleteInvoice(invoice.id)).toBe(403)
    await page.getByTestId(`invoice-actions-menu-${invoice.id}`).click()
    await expect(page.getByTestId("delete-invoice-menu-item")).toHaveCount(0)
  })

  test("adds our company from the document when no recipient matches", async ({
    page,
  }) => {
    test.slow()
    // parsing this minimal document needs the LLM, whose two attempts may
    // together wait up to LLM_STRUCTURED_TIMEOUT + LLM_TIMEOUT (330 s)
    test.setTimeout(400_000)

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
      timeout: 340_000,
    })

    // no recipient detected on the document → no hint, but "add company" works
    await expect(page.getByTestId("recipient-alert")).toHaveCount(0)
    await page.getByTestId("add-company-for-invoice-button").click()
    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()
    await dialog.getByPlaceholder("Název firmy").fill(`Naše Firma ${ico}`)
    await dialog.getByPlaceholder("8 číslic").fill(randomIco())
    // the account is optional – a malformed value blocks, an empty one saves
    await dialog.getByPlaceholder("123456789/0100").fill("ucet/1234")
    await page.getByTestId("add-company-for-invoice-confirm").click()
    await expect(page.getByText(/Zadejte účet ve formátu/)).toBeVisible()
    await dialog.getByPlaceholder("123456789/0100").fill("")
    await page.getByTestId("add-company-for-invoice-confirm").click()
    await expect(
      page.getByTestId("add-company-for-invoice-button"),
    ).toHaveCount(0)
    await expect(page.getByTestId("queue-reasons-alert")).not.toContainText(
      "Zatím není vybrána firma příjemce",
      { timeout: 10_000 },
    )
  })

  test("upload dialog assigns the recipient picked from our companies", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(420_000)

    // two own companies make the picker show; the document names none of them
    const first = await createCompany(`E2E Prijemce1 ${Date.now()}`)
    const second = await createCompany(`E2E Prijemce2 ${Date.now()}`)
    const filename = `faktura-picker-${Date.now()}.pdf`
    let invoiceId: string | null = null
    try {
      await page.goto("/invoices")
      await page.getByTestId("upload-invoice-button").click()
      await page.getByTestId("invoice-file-input").setInputFiles({
        name: filename,
        mimeType: "application/pdf",
        buffer: Buffer.from(
          makePdfBytes([
            "FAKTURA PICKR",
            `Dodavatel: Dodavatel Pickr ${randomIco()} s.r.o.`,
            "Prijemce: Firma Úplne Cizi 99999999 a.s.",
            "Celkem 1210,00 Kc",
          ]),
        ),
      })

      const picker = page.getByTestId("upload-recipient-select")
      await expect(picker).toBeVisible()
      await picker.click()
      await page.getByRole("option", { name: second.name }).click()
      await page.getByTestId("upload-invoice-confirm-button").click()
      await expect(page).toHaveURL(/\/invoices\/[0-9a-f-]{36}$/)
      invoiceId = page.url().split("/").pop() ?? null

      // the explicit choice wins even after the (slow, LLM) parsing finishes;
      // poll over the API – during the route transition the list page is still
      // mounted and its rows would make page-level locators ambiguous
      const deadline = Date.now() + 360_000
      let assigned = await getInvoice(invoiceId!)
      while (
        (assigned.state === "uploaded" || assigned.state === "parsing") &&
        Date.now() < deadline
      ) {
        await new Promise((resolve) => setTimeout(resolve, 1_000))
        assigned = await getInvoice(invoiceId!)
      }
      expect(assigned.recipient_company_id).toBe(second.id)
      await page.reload()
      await expect(page.getByTestId("state-badge")).toHaveText("Ke kontrole")
    } finally {
      if (invoiceId) {
        await tryDeleteInvoice(invoiceId)
      }
      await deleteCompanyByIco(first.ico)
      await deleteCompanyByIco(second.ico)
    }
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

    // an invoice waiting for review shows the lines as editable rows
    await expect(page.getByTestId("vat-items-editable")).toBeVisible()
    await expect(page.getByTestId("vat-row-rate-0")).toHaveValue("12%")
    await expect(page.getByTestId("vat-row-rate-1")).toHaveValue("21%")
    await expect(page.getByTestId("vat-row-base-0")).not.toHaveValue("")
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
    test.skip(
      !existsSync(
        fileURLToPath(
          new URL("../../test_data/364275_7926019675.pdf", import.meta.url),
        ),
      ),
      "the real invoice sample is not checked in",
    )
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
    // a real document may lack a parseable due date – approval requires all
    // three mandatory dates, so fill whatever the parser did not find
    await fillMissingDates(page.url().split("/").pop() ?? "")
    await page.getByTestId("approve-button").click()
    await expect(page.getByTestId("state-badge")).toHaveText("K exportu", {
      timeout: 15_000,
    })
  })

  test("Festo invoice – supplier accounts and VAT lines from a long document", async ({
    page,
  }) => {
    test.skip(
      !existsSync(
        fileURLToPath(
          new URL("../../test_data/FAKTURA_1333134828.PDF", import.meta.url),
        ),
      ),
      "the real invoice sample is not checked in",
    )
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
    const vatEditor = page.getByTestId("vat-items-editable")
    await expect(vatEditor).toBeVisible()
    const vatRates = vatEditor.getByTestId(/^vat-row-rate-/)
    await expect
      .poll(async () => {
        const values: string[] = []
        for (let i = 0; i < (await vatRates.count()); i += 1) {
          values.push(await vatRates.nth(i).inputValue())
        }
        return values.some((value) => value.replace(/\s/g, "") === "21%")
      })
      .toBe(true)
    // Festo lists several payment accounts – the edit form shows them all
    const accountsToggle = page.getByTestId("accounts-toggle")
    if ((await accountsToggle.getAttribute("aria-expanded")) === "false") {
      await accountsToggle.click()
    }
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

  test("photographing a document with the camera uploads it", async ({
    page,
  }) => {
    test.skip(requiresLlm, "the document needs the LLM, not the rule extractor")
    test.slow()
    test.setTimeout(300_000)
    await page.getByTestId("upload-invoice-button").click()
    const cameraButton = page.getByTestId("invoice-camera-button")
    await expect(cameraButton).toBeVisible({ timeout: 10_000 })
    await cameraButton.click()
    await expect(page.getByTestId("invoice-camera-preview")).toBeVisible({
      timeout: 15_000,
    })
    const capture = page.getByTestId("invoice-camera-capture")
    await expect(capture).toBeEnabled({ timeout: 15_000 })
    await capture.click()
    const confirm = page.getByTestId("upload-invoice-confirm-button")
    await expect(confirm).toBeEnabled({ timeout: 10_000 })
    await confirm.click()
    await expect(page).toHaveURL(/\/invoices\/[0-9a-f-]{36}$/, {
      timeout: 30_000,
    })
    const invoiceId = page.url().split("/").pop() ?? null
    expect(invoiceId).not.toBeNull()
    if (invoiceId) {
      // the noisy fake-camera frame fails parsing; clean the draft up
      await waitForParsed(invoiceId, 240_000).catch(() => null)
      await tryDeleteInvoice(invoiceId)
    }
  })

  test("scanning a document uploads a black-and-white image", async ({
    page,
  }) => {
    test.skip(requiresLlm, "the document needs the LLM, not the rule extractor")
    test.slow()
    test.setTimeout(300_000)
    await blockScannerSharing(page) // a real NAPS2 must not hijack this test
    await page.getByTestId("upload-invoice-button").click()
    const scanButton = page.getByTestId("invoice-scan-button")
    await expect(scanButton).toBeVisible({ timeout: 10_000 })
    await scanButton.click()
    await expect(page.getByTestId("invoice-camera-preview")).toBeVisible({
      timeout: 15_000,
    })
    // the trim checkbox is offered in the camera scan mode too, on by default
    const autoTrim = page.getByTestId("invoice-auto-trim")
    await expect(autoTrim).toBeChecked({ timeout: 10_000 })
    await autoTrim.click()
    await expect(autoTrim).not.toBeChecked()
    const capture = page.getByTestId("invoice-camera-capture")
    await expect(capture).toBeEnabled({ timeout: 15_000 })
    // in scan mode the button is labelled as a scanner action
    await expect(capture).toContainText("Naskenovat")
    await capture.click()
    const confirm = page.getByTestId("upload-invoice-confirm-button")
    await expect(confirm).toBeEnabled({ timeout: 10_000 })
    // a camera capture previews immediately
    await expect(page.getByTestId("invoice-upload-preview-image")).toBeVisible({
      timeout: 10_000,
    })
    // the preview must never push the upload buttons off the screen
    await expect(confirm).toBeInViewport()
    await confirm.click()
    await expect(page).toHaveURL(/\/invoices\/[0-9a-f-]{36}$/, {
      timeout: 30_000,
    })
    const invoiceId = page.url().split("/").pop() ?? null
    expect(invoiceId).not.toBeNull()
    if (invoiceId) {
      // the noisy fake-camera frame fails parsing; clean the draft up
      await waitForParsed(invoiceId, 240_000).catch(() => null)
      await tryDeleteInvoice(invoiceId)
    }
  })

  test("scanning through a shared NAPS2 profile uploads one PDF", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(300_000)
    // one small JPEG the fake scanner will "return"; pdf-lib needs real data
    const jpeg = await page.evaluate(
      () =>
        new Promise<string>((resolve) => {
          const canvas = document.createElement("canvas")
          canvas.width = 60
          canvas.height = 40
          const context = canvas.getContext("2d")
          if (context) {
            context.fillStyle = "#ffffff"
            context.fillRect(0, 0, 60, 40)
          }
          resolve(canvas.toDataURL("image/jpeg").split(",")[1] ?? "")
        }),
    )
    let servedPages = 0
    await blockScannerSharing(page) // block 9802+; the mock below wins on 9801
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "content-type",
      "Access-Control-Expose-Headers": "Location",
    }
    // the NAPS2 sharing service stubbed on its default port
    await page.route("http://localhost:9801/eSCL/**", (route) => {
      const request = route.request()
      if (request.method() === "OPTIONS") {
        return route.fulfill({ status: 204, headers: cors })
      }
      if (request.url().endsWith("/ScannerCapabilities")) {
        return route.fulfill({
          status: 200,
          headers: cors,
          body: `<?xml version="1.0"?><scan:ScannerCapabilities xmlns:scan="http://schemas.hp.com/imaging/escl/2011-05-03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"><pwg:MakeAndModel>E2E NAPS2</pwg:MakeAndModel></scan:ScannerCapabilities>`,
        })
      }
      if (request.url().endsWith("/ScanJobs")) {
        // stock NAPS2 only accepts its own namespace spelling (cyanfish/naps2#975),
        // so this also covers the client's retry with the other one
        const body = request.postData() ?? ""
        if (body.includes("escl/2011-05-03")) {
          return route.fulfill({ status: 400, headers: cors })
        }
        return route.fulfill({
          status: 201,
          headers: {
            ...cors,
            Location: "http://localhost:9801/eSCL/ScanJobs/e2e-job",
          },
        })
      }
      if (request.url().endsWith("/NextDocument")) {
        if (servedPages++ === 0) {
          return route.fulfill({
            status: 200,
            contentType: "image/jpeg",
            headers: cors,
            body: Buffer.from(jpeg, "base64"),
          })
        }
        return route.fulfill({ status: 404, headers: cors })
      }
      return route.fulfill({ status: 404, headers: cors })
    })

    await page.getByTestId("upload-invoice-button").click()
    await expect(page.getByTestId("invoice-scan-button")).toBeVisible({
      timeout: 10_000,
    })
    await page.getByTestId("invoice-scan-button").click()
    await expect(page.getByTestId("naps2-panel")).toBeVisible({
      timeout: 15_000,
    })
    await expect(page.getByTestId("scanner-profile-select")).toContainText(
      "E2E NAPS2",
    )
    // auto-trim of white margins is on by default
    await expect(page.getByTestId("invoice-auto-trim")).toBeChecked()
    await page.getByTestId("scanner-scan-button").click()
    const confirm = page.getByTestId("upload-invoice-confirm-button")
    await expect(confirm).toBeEnabled({ timeout: 30_000 })
    // the scanned page can be checked before it is uploaded
    await expect(page.getByTestId("invoice-upload-preview-image")).toBeVisible({
      timeout: 10_000,
    })
    // the preview must never push the upload buttons off the screen
    await expect(confirm).toBeInViewport()
    await confirm.click()
    await expect(page).toHaveURL(/\/invoices\/[0-9a-f-]{36}$/, {
      timeout: 30_000,
    })
    const invoiceId = page.url().split("/").pop() ?? null
    expect(invoiceId).not.toBeNull()
    if (invoiceId) {
      // the blank stub page fails parsing; clean the draft up
      await waitForParsed(invoiceId, 240_000).catch(() => null)
      await tryDeleteInvoice(invoiceId)
    }
  })

  test("a camera that stops on its own returns to the idle dialog", async ({
    page,
  }) => {
    test.skip(requiresLlm, "the document needs the LLM, not the rule extractor")
    test.slow()
    await blockScannerSharing(page) // a real NAPS2 must not hijack this test
    await page.getByTestId("upload-invoice-button").click()
    const scanButton = page.getByTestId("invoice-scan-button")
    await expect(scanButton).toBeVisible({ timeout: 10_000 })
    await scanButton.click()
    await expect(page.getByTestId("invoice-camera-preview")).toBeVisible({
      timeout: 15_000,
    })
    // simulate the OS ending the stream (sleep button, privacy switch, …)
    await page.evaluate(() => {
      const video = document.querySelector(
        "[data-testid='invoice-camera-preview'] video",
      ) as HTMLVideoElement | null
      video?.srcObject?.getTracks().forEach((track) => {
        track.stop()
      })
    })
    // no black screen left behind – the dialog falls back and tells the user
    await expect(page.getByTestId("invoice-camera-preview")).toBeHidden({
      timeout: 5_000,
    })
    await expect(page.getByText("Kamera byla ukončena")).toBeVisible()
    await page.keyboard.press("Escape")
  })

  test("deleting a document closes the dialog without offering the next one", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(420_000)
    const stamp = Date.now()
    const doomed = await uploadInvoice([
      `Faktura DELA${stamp}`,
      "Dodavatel: Mazani A s.r.o.",
      "Celkem 100,00 Kc",
    ])
    const survivor = await uploadInvoice([
      `Faktura DELB${stamp}`,
      "Dodavatel: Mazani B s.r.o.",
      "Celkem 200,00 Kc",
    ])
    try {
      await waitForParsed(doomed.id)
      await waitForParsed(survivor.id)
      await page.goto("/invoices")
      const doomedRow = page.locator("tr", { hasText: `DELA${stamp}` })
      await expect(doomedRow).toBeVisible()
      await doomedRow.locator('[data-testid^="invoice-actions-menu-"]').click()
      await page.getByTestId("delete-invoice-menu-item").click()
      await page.getByTestId("delete-invoice-confirm-button").click()
      await expect(doomedRow).toBeHidden({ timeout: 15_000 })
      // the dialog must not reappear on the neighbour row
      await expect(
        page.getByTestId("delete-invoice-confirm-button"),
      ).toBeHidden()
      await expect(
        page.locator("tr", { hasText: `DELB${stamp}` }),
      ).toBeVisible()
    } finally {
      await tryDeleteInvoice(doomed.id)
      await tryDeleteInvoice(survivor.id)
    }
  })

  test("the same document scanned twice is flagged as a duplicate", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(420_000)
    const stamp = Date.now()
    const company = await createCompany(`DUPTEST ${stamp}`)
    const lines = [
      `Faktura DUPR${stamp}`,
      "Dodavatel: Opakovany Dodavatel s.r.o.",
      "Celkem 1210,00 Kc",
    ]
    const first = await uploadInvoice(lines)
    const second = await uploadInvoice(lines)
    try {
      await waitForParsed(first.id)
      await waitForParsed(second.id)

      // both copies point at each other
      await page.goto(`/invoices/${second.id}`)
      await expect(page.getByTestId("duplicate-alert")).toBeVisible()
      await expect(
        page
          .getByTestId("duplicate-list")
          .getByTestId(`duplicate-item-${first.id}`),
      ).toBeVisible()

      // approve the first copy – nothing blocks it
      await page.goto(`/invoices/${first.id}`)
      await page.getByTestId("company-select").click()
      await page.getByRole("option", { name: company.name }).click()
      await page.getByTestId("approve-button").click()
      await expect(page.getByTestId("state-badge")).toHaveText("K exportu", {
        timeout: 10_000,
      })

      // approving the second copy needs an explicit decision
      await page.goto(`/invoices/${second.id}`)
      await page.getByTestId("company-select").click()
      await page.getByRole("option", { name: company.name }).click()
      await page.getByTestId("approve-button").click()
      await expect(page.getByTestId("duplicate-dialog")).toBeVisible()
      await expect(page.getByTestId("state-badge")).toHaveText("Ke kontrole")

      // keep the newer copy, drop the approved original
      await page.getByTestId("duplicate-dialog-approve").click()
      await expect(page.getByTestId("state-badge")).toHaveText("K exportu", {
        timeout: 10_000,
      })
      expect(await tryDeleteInvoice(first.id)).toBe(404)
    } finally {
      await tryDeleteInvoice(first.id)
      await tryDeleteInvoice(second.id)
      await deleteCompaniesByName(`DUPTEST ${stamp}`)
    }
  })

  test("a duplicate can be dropped without touching the approved original", async ({
    page,
  }) => {
    test.slow()
    test.setTimeout(420_000)
    const stamp = Date.now()
    const company = await createCompany(`DUPKEEP ${stamp}`)
    const lines = [
      `Faktura DUPK${stamp}`,
      "Dodavatel: Ponechany Dodavatel s.r.o.",
      "Celkem 1210,00 Kc",
    ]
    const original = await uploadInvoice(lines)
    const duplicate = await uploadInvoice(lines)
    try {
      await waitForParsed(original.id)
      await waitForParsed(duplicate.id)

      await page.goto(`/invoices/${original.id}`)
      await page.getByTestId("company-select").click()
      await page.getByRole("option", { name: company.name }).click()
      await page.getByTestId("approve-button").click()
      await expect(page.getByTestId("state-badge")).toHaveText("K exportu", {
        timeout: 10_000,
      })

      // the dialog's primary choice drops the record at hand only
      await page.goto(`/invoices/${duplicate.id}`)
      await page.getByTestId("company-select").click()
      await page.getByRole("option", { name: company.name }).click()
      await page.getByTestId("approve-button").click()
      await expect(page.getByTestId("duplicate-dialog")).toBeVisible()
      await page.getByTestId("duplicate-dialog-delete").click()
      await expect(page.getByTestId("upload-invoice-button")).toBeVisible({
        timeout: 10_000,
      })

      expect(await tryDeleteInvoice(duplicate.id)).toBe(404)
      expect((await getInvoice(original.id)).state).toBe("ready_for_export")
    } finally {
      await tryDeleteInvoice(original.id)
      await tryDeleteInvoice(duplicate.id)
      await deleteCompaniesByName(`DUPKEEP ${stamp}`)
    }
  })

  test("switches the UI language and remembers the choice", async ({
    page,
  }) => {
    await page.getByTestId("language-button").click()
    await page.getByTestId("language-en").click()
    await expect(
      page.getByRole("heading", { name: "Documents", exact: true }),
    ).toBeVisible()
    // the choice is persisted, so it survives a reload
    await page.reload()
    await expect(
      page.getByRole("heading", { name: "Documents", exact: true }),
    ).toBeVisible()
    await page.getByTestId("language-button").click()
    await page.getByTestId("language-de").click()
    await expect(
      page.getByRole("heading", { name: "Belege", exact: true }),
    ).toBeVisible()
  })

  test("invoice columns saved in the profile persist", async ({ page }) => {
    test.setTimeout(90_000)
    await page.goto("/invoices")
    const totalHeader = page.getByRole("columnheader", { name: "Celkem" })
    await expect(totalHeader).toBeVisible()

    // hide the total column through the picker (Escape closes the menu so
    // the table is not aria-hidden while the assertions run)
    await page.getByTestId("column-picker").click()
    await page.getByTestId("column-toggle-total_price").click()
    await page.keyboard.press("Escape")
    await expect
      .poll(async () => totalHeader.count(), { timeout: 15_000 })
      .toBe(0)

    // the preference is stored in the profile, so it survives a reload
    await page.reload()
    await expect
      .poll(async () => totalHeader.count(), { timeout: 15_000 })
      .toBe(0)

    // restore the default (all columns) for the following tests
    await page.getByTestId("column-picker").click()
    await page.getByTestId("column-toggle-total_price").click()
    await page.keyboard.press("Escape")
    await expect(totalHeader).toBeVisible({ timeout: 15_000 })
  })

  test("VAT rate lines are editable before the confirmation", async ({
    page,
  }) => {
    test.slow()
    const invoice = await uploadInvoice([
      `Faktura E2EDPHEDIT${Date.now()}`,
      "Dodavatel: DPH Edit s.r.o.",
      "ICO: 47123737  DIC: CZ47123737",
      "Datum vystaveni: 2026-04-01  Splatnost: 2026-04-15",
      "Sazba DPH 21 % Zaklad DPH 1 000,00 Kc DPH 210,00 Kc",
      "Celkem k Uhrade 1 210,00 Kc",
    ])
    try {
      await waitForParsed(invoice.id)
      await page.goto(`/invoices/${invoice.id}`)
      await expect(page.getByTestId("state-badge")).toHaveText("Ke kontrole")
      await expect(page.getByTestId("vat-items-editable")).toBeVisible()

      // a second rate lands one row below the parsed one
      await page.getByTestId("vat-row-add").click()
      await page.getByTestId("vat-row-base-1").fill("500")
      await page.getByTestId("vat-row-tax-1").fill("60")
      await page.getByTestId("invoice-save-button").click()
      await expect(
        page.locator("[data-sonner-toaster]").getByText("Uloženo"),
      ).toBeVisible({ timeout: 10_000 })
      await page.reload()
      await expect(page.getByTestId("vat-row-1")).toBeVisible()

      // removing the added line persists as well
      await page.getByTestId("vat-row-remove-1").click()
      await page.getByTestId("invoice-save-button").click()
      await expect(
        page.locator("[data-sonner-toaster]").getByText("Uloženo"),
      ).toBeVisible({ timeout: 10_000 })
      await page.reload()
      await expect(page.getByTestId("vat-row-1")).toHaveCount(0)
    } finally {
      await tryDeleteInvoice(invoice.id)
    }
  })

  test("uploading an invoice for another company warns before assigning", async ({
    page,
  }) => {
    test.skip(requiresLlm, "the document needs the LLM, not the rule extractor")
    test.slow()
    test.setTimeout(360_000)
    const ours = await createCompany(`E2E our-company-${Date.now()}`)
    const named = await createCompany(`E2E named-${Date.now()}`)
    let invoiceId: string | null = null
    try {
      const invoice = await uploadInvoice([
        `Faktura E2EMISMATCH${Date.now()}`,
        "Dodavatel: Dodavatel Jinde s.r.o.",
        `Prijemce: ${named.name} ${named.ico} a.s.`,
        "Celkem 420,00 Kc",
      ])
      invoiceId = invoice.id
      await waitForParsed(invoice.id)

      await page.goto(`/invoices/${invoice.id}`)
      // assign the wrong one of our companies on purpose
      await page.getByTestId("company-select").click()
      await page.getByRole("option", { name: ours.name }).click()

      const alert = page.getByTestId("recipient-mismatch-alert")
      await expect(alert).toBeVisible()
      await expect(alert).toContainText(named.name)

      // one click reassigns the invoice to the company named on the document
      await page.getByTestId("recipient-switch-button").click()
      await expect(alert).toBeHidden({ timeout: 15_000 })
      await expect(page.getByTestId("company-select")).toContainText(named.name)
    } finally {
      if (invoiceId) {
        await tryDeleteInvoice(invoiceId)
      }
      await deleteCompanyByIco(ours.ico)
      await deleteCompanyByIco(named.ico)
    }
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
