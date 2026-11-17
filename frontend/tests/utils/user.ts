import { expect, type Page } from "@playwright/test"

export async function signUpNewUser(
  page: Page,
  name: string,
  email: string,
  password: string,
) {
  await page.goto("/signup")

  await page.getByTestId("full-name-input").fill(name)
  await page.getByTestId("email-input").fill(email)
  await page.getByTestId("password-input").fill(password)
  await page.getByTestId("confirm-password-input").fill(password)
  await page.getByTestId("signup-button").click()
  await page.goto("/login")
}

export async function logInUser(page: Page, email: string, password: string) {
  await page.goto("/login")

  await page.getByTestId("email-input").fill(email)
  await page.getByTestId("password-input").fill(password)
  await page.getByTestId("login-button").click()
  await page.waitForURL("/")
  await expect(page.getByTestId("user-menu")).toBeVisible()
}

export async function logOutUser(page: Page) {
  await page.getByTestId("user-menu").click()
  await page.getByTestId("logout-menu-item").click()
  await page.goto("/login")
}

export async function ensureOwnCompany(page: Page) {
  // a brand-new member is bounced to /companies until they own one –
  // create one through the app's own API with their session token
  await page.evaluate(async () => {
    const token = localStorage.getItem("access_token")
    const ico = `9${String(Date.now()).slice(-7)}`
    const response = await fetch("/api/v1/companies/", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ name: `Test s.r.o. ${ico}`, ico }),
    })
    if (!response.ok && response.status !== 409) {
      throw new Error(await response.text())
    }
  })
}
