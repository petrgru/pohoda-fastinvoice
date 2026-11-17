import { expect, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { createUser } from "./utils/privateApi"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser } from "./utils/user"

test("Admin page is accessible and shows correct title", async ({ page }) => {
  await page.goto("/admin")
  await expect(page.getByRole("heading", { name: "Uživatelé" })).toBeVisible()
  await expect(
    page.getByText("Správa uživatelských účtů a oprávnění"),
  ).toBeVisible()
})

test("Add User button is visible", async ({ page }) => {
  await page.goto("/admin")
  await expect(page.getByTestId("add-user-button")).toBeVisible()
})

test.describe("Admin user management", () => {
  test("Create a new user successfully", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()
    const fullName = "Test User Admin"

    await page.getByTestId("add-user-button").click()

    await page.getByTestId("user-email-input").fill(email)
    await page.getByTestId("user-fullname-input").fill(fullName)
    await page.getByTestId("user-password-input").fill(password)
    await page.getByTestId("user-password-confirm-input").fill(password)

    await page.getByTestId("user-save-button").click()

    await expect(page.getByText("Uživatel vytvořen")).toBeVisible()

    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await expect(userRow).toBeVisible()
  })

  test("Create a superuser", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()

    await page.getByTestId("add-user-button").click()

    await page.getByTestId("user-email-input").fill(email)
    await page.getByTestId("user-password-input").fill(password)
    await page.getByTestId("user-password-confirm-input").fill(password)
    await page.getByLabel("Je administrátor?").check()
    await page.getByLabel("Je aktivní?").check()

    await page.getByTestId("user-save-button").click()

    await expect(page.getByText("Uživatel vytvořen")).toBeVisible()

    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await expect(userRow.getByText("Administrátor")).toBeVisible()
  })

  test("Edit a user successfully", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()
    const originalName = "Original Name"
    const updatedName = "Updated Name"

    await page.getByTestId("add-user-button").click()
    await page.getByTestId("user-email-input").fill(email)
    await page.getByTestId("user-fullname-input").fill(originalName)
    await page.getByTestId("user-password-input").fill(password)
    await page.getByTestId("user-password-confirm-input").fill(password)
    await page.getByTestId("user-save-button").click()

    await expect(page.getByText("Uživatel vytvořen")).toBeVisible()
    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await userRow.getByRole("button").click()

    await page.getByTestId("edit-user-item").click()

    await page.getByTestId("user-fullname-input").fill(updatedName)
    await page.getByTestId("user-save-button").click()

    await expect(page.getByText("Uživatel aktualizován")).toBeVisible()
    await expect(userRow.getByText(updatedName)).toBeVisible()
  })

  test("Delete a user successfully", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()

    await page.getByTestId("add-user-button").click()
    await page.getByTestId("user-email-input").fill(email)
    await page.getByTestId("user-password-input").fill(password)
    await page.getByTestId("user-password-confirm-input").fill(password)
    await page.getByTestId("user-save-button").click()

    await expect(page.getByText("Uživatel vytvořen")).toBeVisible()

    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await userRow.getByRole("button").click()

    await page.getByTestId("delete-user-item").click()

    await page.getByTestId("user-delete-confirm").click()

    await expect(page.getByText("Uživatel smazán")).toBeVisible()

    await expect(
      page.getByRole("row").filter({ hasText: email }),
    ).not.toBeVisible()
  })

  test("Cancel user creation", async ({ page }) => {
    await page.goto("/admin")

    await page.getByTestId("add-user-button").click()
    await page.getByTestId("user-email-input").fill("test@example.com")

    await page.getByTestId("user-cancel-button").click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
  })

  test("Email is required and must be valid", async ({ page }) => {
    await page.goto("/admin")

    await page.getByTestId("add-user-button").click()

    const emailInput = page.getByTestId("user-email-input")
    await emailInput.fill("invalid-email")
    await emailInput.blur()

    await expect(page.getByText("Zadejte platný e-mail")).toBeVisible()
  })

  test("Password must be at least 8 characters", async ({ page }) => {
    await page.goto("/admin")

    await page.getByTestId("add-user-button").click()

    await page.getByTestId("user-email-input").fill(randomEmail())
    await page.getByTestId("user-password-input").fill("short")
    await page.getByTestId("user-password-confirm-input").fill("short")
    await page.getByTestId("user-save-button").click()

    await expect(page.getByText("Heslo musí mít alespoň 8 znaků")).toBeVisible()
  })

  test("Passwords must match", async ({ page }) => {
    await page.goto("/admin")

    await page.getByTestId("add-user-button").click()

    await page.getByTestId("user-email-input").fill(randomEmail())
    await page.getByTestId("user-password-input").fill(randomPassword())
    const confirmInput = page.getByTestId("user-password-confirm-input")
    await confirmInput.fill("different12345")
    await confirmInput.blur()

    await expect(page.getByText("Hesla se neshodují")).toBeVisible()
  })
})

test.describe("Admin page access control", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Non-superuser cannot access admin page", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()

    await createUser({ email, password })
    await logInUser(page, email, password)

    await page.goto("/admin")

    await expect(
      page.getByRole("heading", { name: "Uživatelé" }),
    ).not.toBeVisible()
    await expect(page).not.toHaveURL(/\/admin/)
  })

  test("Superuser can access admin page", async ({ page }) => {
    await logInUser(page, firstSuperuser, firstSuperuserPassword)

    await page.goto("/admin")

    await expect(page.getByRole("heading", { name: "Uživatelé" })).toBeVisible()
  })
})
