import { expect, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { createUser } from "./utils/privateApi.ts"
import { randomEmail, randomPassword } from "./utils/random"
import { ensureOwnCompany, logInUser, logOutUser } from "./utils/user"

const tabs = ["Můj profil", "Heslo", "Nebezpečná zóna"]

test("My profile tab is active by default", async ({ page }) => {
  await page.goto("/settings")
  await expect(page.getByRole("tab", { name: "Můj profil" })).toHaveAttribute(
    "aria-selected",
    "true",
  )
})

test("All tabs are visible", async ({ page }) => {
  await page.goto("/settings")
  for (const tab of tabs) {
    await expect(page.getByRole("tab", { name: tab })).toBeVisible()
  }
})

test.describe("Edit user profile", () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  let email: string
  let password: string

  test.beforeAll(async () => {
    email = randomEmail()
    password = randomPassword()
    await createUser({ email, password })
  })

  test.beforeEach(async ({ page }) => {
    await logInUser(page, email, password)
    await ensureOwnCompany(page) // silence the new-member onboarding redirect
    await page.goto("/settings")
    await page.getByRole("tab", { name: "Můj profil" }).click()
  })

  test("Edit user name with a valid name", async ({ page }) => {
    const updatedName = "Test User 2"

    await page.getByTestId("profile-edit-button").click()
    await page.getByTestId("profile-fullname-input").fill(updatedName)
    await page.getByTestId("profile-save-button").click()

    await expect(page.getByText("Uživatel aktualizován")).toBeVisible()
    await expect(
      page.locator("form").getByText(updatedName, { exact: true }),
    ).toBeVisible()
  })

  test("Edit user email with an invalid email shows error", async ({
    page,
  }) => {
    await page.getByTestId("profile-edit-button").click()
    await page.getByTestId("profile-email-input").fill("")
    await page.locator("body").click()

    await expect(page.getByText("Zadejte platný e-mail")).toBeVisible()
  })
})

test.describe("Edit user email", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Edit user email with a valid email", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()
    const updatedEmail = randomEmail()

    await createUser({ email, password })
    await logInUser(page, email, password)
    await ensureOwnCompany(page) // silence the new-member onboarding redirect
    await page.goto("/settings")
    await page.getByRole("tab", { name: "Můj profil" }).click()

    await page.getByTestId("profile-edit-button").click()
    await page.getByTestId("profile-email-input").fill(updatedEmail)
    await page.getByTestId("profile-save-button").click()

    await expect(page.getByText("Uživatel aktualizován")).toBeVisible()
    await expect(
      page.locator("form").getByText(updatedEmail, { exact: true }),
    ).toBeVisible()
  })
})

test.describe("Cancel edit actions", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Cancel edit action restores original name", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()
    const user = await createUser({ email, password })

    await logInUser(page, email, password)
    await ensureOwnCompany(page) // silence the new-member onboarding redirect
    await page.goto("/settings")
    await page.getByRole("tab", { name: "Můj profil" }).click()
    await page.getByTestId("profile-edit-button").click()
    await page.getByTestId("profile-fullname-input").fill("Test User")
    await page.getByTestId("profile-cancel-button").click()

    await expect(
      page.locator("form").getByText(user.full_name as string, { exact: true }),
    ).toBeVisible()
  })

  test("Cancel edit action restores original email", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()
    await createUser({ email, password })

    await logInUser(page, email, password)
    await ensureOwnCompany(page) // silence the new-member onboarding redirect
    await page.goto("/settings")
    await page.getByRole("tab", { name: "Můj profil" }).click()
    await page.getByTestId("profile-edit-button").click()
    await page.getByTestId("profile-email-input").fill(randomEmail())
    await page.getByTestId("profile-cancel-button").click()

    await expect(
      page.locator("form").getByText(email, { exact: true }),
    ).toBeVisible()
  })
})

test.describe("Change password", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Update password successfully", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()
    const newPassword = randomPassword()

    await createUser({ email, password })
    await logInUser(page, email, password)
    await ensureOwnCompany(page) // silence the new-member onboarding redirect
    await page.goto("/settings")
    await page.getByRole("tab", { name: "Heslo" }).click()
    await page.getByTestId("current-password-input").fill(password)
    await page.getByTestId("new-password-input").fill(newPassword)
    await page.getByTestId("confirm-password-input").fill(newPassword)
    await page.getByTestId("password-update-button").click()

    await expect(page.getByText("Heslo aktualizováno")).toBeVisible()

    await logOutUser(page)
    await logInUser(page, email, newPassword)
  })
})

test.describe("Change password validation", () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  let email: string
  let password: string

  test.beforeAll(async () => {
    email = randomEmail()
    password = randomPassword()
    await createUser({ email, password })
  })

  test.beforeEach(async ({ page }) => {
    await logInUser(page, email, password)
    await ensureOwnCompany(page) // silence the new-member onboarding redirect
    await page.goto("/settings")
    await page.getByRole("tab", { name: "Heslo" }).click()
  })

  test("Update password with weak passwords", async ({ page }) => {
    const weakPassword = "weak"

    await page.getByTestId("current-password-input").fill(password)
    await page.getByTestId("new-password-input").fill(weakPassword)
    await page.getByTestId("confirm-password-input").fill(weakPassword)
    await page.getByTestId("password-update-button").click()

    await expect(page.getByText("Heslo musí mít alespoň 8 znaků")).toBeVisible()
  })

  test("New password and confirmation password do not match", async ({
    page,
  }) => {
    await page.getByTestId("current-password-input").fill(password)
    await page.getByTestId("new-password-input").fill(randomPassword())
    await page.getByTestId("confirm-password-input").fill(randomPassword())
    await page.getByTestId("password-update-button").click()

    await expect(page.getByText("Hesla se neshodují")).toBeVisible()
  })

  test("Current password and new password are the same", async ({ page }) => {
    await page.getByTestId("current-password-input").fill(password)
    await page.getByTestId("new-password-input").fill(password)
    await page.getByTestId("confirm-password-input").fill(password)
    await page.getByTestId("password-update-button").click()

    await expect(
      page.getByText("Nové heslo nesmí být stejné jako současné"),
    ).toBeVisible()
  })
})

test("Appearance button is visible in sidebar", async ({ page }) => {
  await page.goto("/settings")
  await expect(page.getByTestId("theme-button")).toBeVisible()
})

test("User can switch between theme modes", async ({ page }) => {
  await page.goto("/settings")

  await page.getByTestId("theme-button").click()
  await page.getByTestId("dark-mode").click()
  await expect(page.locator("html")).toHaveClass(/dark/)

  await expect(page.getByTestId("dark-mode")).not.toBeVisible()

  await page.getByTestId("theme-button").click()
  await page.getByTestId("light-mode").click()
  await expect(page.locator("html")).toHaveClass(/light/)
})

test("Selected mode is preserved across sessions", async ({ page }) => {
  await page.goto("/settings")

  await page.getByTestId("theme-button").click()
  if (
    await page.evaluate(() =>
      document.documentElement.classList.contains("dark"),
    )
  ) {
    await page.getByTestId("light-mode").click()
    await page.getByTestId("theme-button").click()
  }

  const isLightMode = await page.evaluate(() =>
    document.documentElement.classList.contains("light"),
  )
  expect(isLightMode).toBe(true)

  await page.getByTestId("theme-button").click()
  await page.getByTestId("dark-mode").click()
  let isDarkMode = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  )
  expect(isDarkMode).toBe(true)

  await logOutUser(page)
  await logInUser(page, firstSuperuser, firstSuperuserPassword)

  isDarkMode = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  )
  expect(isDarkMode).toBe(true)
})
