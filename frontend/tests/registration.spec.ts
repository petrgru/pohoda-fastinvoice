import { expect, test } from "@playwright/test"

import { deleteCompanyByIco, makePdfBytes, randomIco } from "./utils/invoices"

const apiBase = process.env.VITE_API_URL ?? "http://localhost:8000"

const apiLogin = async (email: string, password: string): Promise<string> => {
  const response = await fetch(`${apiBase}/api/v1/login/access-token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username: email, password }),
  })
  if (!response.ok) {
    throw new Error(`Login failed: ${response.status}`)
  }
  const data = (await response.json()) as { access_token: string }
  return data.access_token
}

test.describe("Registration and first company", () => {
  // start without the superuser session – the flow logs in as the new user
  test.use({ storageState: { cookies: [], origins: [] } })

  test("signup forces the first company and uploads land in it", async ({
    page,
  }) => {
    test.slow()
    const email = `e2e-uvod-${Date.now()}@example.com`
    const password = "e2e-SuperSecret-123"
    const ico = randomIco()
    const name = `E2E Naše Firma ${ico}`

    const register = await fetch(`${apiBase}/api/v1/users/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, full_name: "E2E Úvod" }),
    })
    expect(register.status).toBe(200)

    try {
      await page.goto("/login")
      await page.getByTestId("email-input").fill(email)
      await page.getByTestId("password-input").fill(password)
      await page.getByTestId("login-button").click()

      // onboarding: without a company the app leads straight to "Naše firmy"
      await expect(page).toHaveURL(/\/companies/, { timeout: 15_000 })
      await expect(page.getByTestId("companies-empty-hint")).toBeVisible()

      // other pages keep redirecting until the first company exists
      await page.goto("/invoices")
      await expect(page).toHaveURL(/\/companies/, { timeout: 10_000 })

      // add the own company through the dialog
      await page.getByTestId("add-company-button").click()
      await page.getByPlaceholder("Název firmy").fill(name)
      await page.getByPlaceholder("8 číslic").fill(ico)
      await page.getByPlaceholder("123456789/0100").fill("123456789/0100")
      await page.getByTestId("add-company-confirm-button").click()
      await expect(page.getByText(name)).toBeVisible()

      // from now on the invoices are reachable and a document is assigned
      // to the single company right away
      await page.goto("/invoices")
      await expect(page.getByTestId("upload-invoice-button")).toBeVisible()
      await page.getByTestId("upload-invoice-button").click()
      await page.getByTestId("invoice-file-input").setInputFiles({
        name: `e2e-uvod-${Date.now()}.pdf`,
        mimeType: "application/pdf",
        buffer: Buffer.from(
          makePdfBytes([
            `FAKTURA UVOD ${Date.now()}`,
            "Dodavatel: Uvodni Test s.r.o.",
            "Celkem 1210,00 Kc",
          ]),
        ),
      })
      // a member with one company does not pick the recipient
      await expect(page.getByTestId("upload-recipient-select")).toHaveCount(0)
      const [uploadResponse] = await Promise.all([
        page.waitForResponse((r) => r.url().includes("/invoices/upload"), {
          timeout: 30_000,
        }),
        page.getByTestId("upload-invoice-confirm-button").click(),
      ])
      expect(uploadResponse.status()).toBe(201)
      const created = await uploadResponse.json()
      expect(created.company_id).not.toBeNull()

      // the new user sees the invoice in their list
      await expect(page.getByTestId("state-badge").first()).toBeVisible({
        timeout: 10_000,
      })

      // clean up through the API of the fresh account
      const token = await apiLogin(email, password)
      const removed = await fetch(`${apiBase}/api/v1/invoices/${created.id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      })
      expect(removed.status).toBe(200)
    } finally {
      await deleteCompanyByIco(ico)
    }
  })
})
