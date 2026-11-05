// SPDX-License-Identifier: Apache-2.0
import { expect, test } from "@playwright/test";
import { totp } from "./totp";

const PASSWORD = "e2e correct horse battery";

test("first run → enable MFA → sign out → sign in with TOTP", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/setup$/);

  await page.getByLabel("Name").fill("Test Owner");
  await page.getByLabel("Email").fill("owner@example.test");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create owner account" }).click();

  await expect(page).toHaveURL(/\/settings\/security/);
  await page.getByRole("button", { name: "Turn on" }).click();
  await page.getByLabel("Confirm your password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();

  const secret = (await page.getByLabel("Setup key").textContent())!.replace(/\s/g, "");
  await expect(page.getByAltText(/QR code/)).toBeVisible();
  await page.getByLabel("Authentication code").fill(totp(secret));
  await page.getByRole("button", { name: "Turn on" }).click();

  const codes = page.getByRole("list", { name: "Recovery codes" }).getByRole("listitem");
  await expect(codes).toHaveCount(10);
  await page.getByRole("button", { name: "I've saved them" }).click();
  await expect(page.getByText("On", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).first().click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email").fill("owner@example.test");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/login\/verify$/);
  // The current step was used to enable MFA, so use the next one (still within the window).
  await page.getByLabel("Authentication code").fill(totp(secret, 1));
  await page.getByRole("button", { name: "Verify" }).click();

  await expect(page.getByRole("heading", { name: "Hi, Test" })).toBeVisible();
});

test("API responses carry security headers", async ({ request }) => {
  const res = await request.get("/api/healthz");
  expect(res.headers()["content-security-policy"]).toContain("default-src 'none'");
  const html = await request.get("/");
  expect(html.headers()["content-security-policy"]).toContain("script-src 'self'");
  expect(html.headers()["x-frame-options"]).toBe("DENY");
});
