// SPDX-License-Identifier: Apache-2.0
// Tracker end to end: application → stages → follow-up → timeline file → referral → offers.
// Everyone and everything here is fictional.
import { expect, test } from "@playwright/test";

const PASSWORD = "e2e tracker battery staple";

function isoInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toLocaleDateString("en-CA"); // YYYY-MM-DD in local time
}

test("track an application from Saved to Offer and compare offers", async ({ page }) => {
  await page.goto("/register");
  await page.getByLabel("Name").fill("Casey Lin");
  await page.getByLabel("Email").fill("casey@example.test");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Hi, Casey" })).toBeVisible();

  // --- create an application ---------------------------------------------------------
  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("link", { name: /Tracker/ }).click();
  await expect(page).toHaveURL(/\/tracker\/board$/);
  await page.getByRole("button", { name: "Add application" }).click();
  await page.getByLabel("Job title").fill("Network Engineer");
  await page.getByLabel("Company (optional)").fill("Tailspin Networks");
  await page.getByLabel("Pay from (optional)").fill("50");
  await page.getByLabel("Pay to (optional)").fill("60");
  await page.getByLabel("Pay is per").selectOption("hour");
  await page.getByRole("button", { name: "Add application" }).click();
  await expect(page.getByRole("heading", { name: "Network Engineer" })).toBeVisible();
  await expect(page.getByText(/Tailspin Networks · \$50–\$60\/hr/)).toBeVisible();

  // --- move to Applied and accept the follow-up offer ----------------------------------
  await page.getByLabel("Move Network Engineer to").selectOption("applied");
  await expect(page.getByText("Moved “Network Engineer” to Applied.")).toBeVisible();
  await page.getByRole("button", { name: "Add reminder" }).click();
  const reminders = page.locator("section.card", { has: page.getByRole("heading", { name: "Reminders" }) });
  await expect(reminders.getByText("Follow up: Network Engineer")).toBeVisible();
  await expect(page.getByText("Moved to Applied")).toBeVisible(); // stage history in the timeline

  // --- timeline entry with an attachment, then download it ------------------------------
  await page.getByLabel("Add", { exact: true }).selectOption("sent");
  await page.getByLabel("What you sent").fill("Resume v3 + cover letter");
  await page.getByLabel("Title (optional)").fill("Applied online");
  await page.getByLabel("Attach a file (optional)").setInputFiles({
    name: "cover-note.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Fictional cover note for an e2e test."),
  });
  await page.getByRole("button", { name: "Add to timeline" }).click();
  const sent = page.locator("li.tl-item", { hasText: "Applied online" });
  await expect(sent.getByText("Resume v3 + cover letter")).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await sent.getByRole("link", { name: "cover-note.txt" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("cover-note.txt");

  // --- referral from a new contact ------------------------------------------------------
  await page.getByRole("button", { name: "Add referral" }).click();
  await page.getByLabel("Who's referring you").selectOption("new");
  await page.getByLabel("Name", { exact: true }).fill("Sam Example");
  await page.getByLabel("Status").selectOption("asked");
  await page.getByLabel("Follow up on (optional)").fill(isoInDays(3));
  await page.getByRole("button", { name: "Add referral" }).click();
  await expect(page.getByLabel("Referral status for Sam Example")).toHaveValue("asked");
  await expect(page.getByText("Sam Example").first()).toBeVisible();

  // --- the reminder shows up on Overview ---------------------------------------------------
  await nav.getByRole("link", { name: "Overview" }).click();
  const dueSoon = page.getByRole("region", { name: "Due soon" });
  await expect(dueSoon.getByText("Follow up: Network Engineer")).toBeVisible();
  await expect(dueSoon.getByText("Follow up on referral: Sam Example")).toBeVisible();

  // --- move to Offer and enter the offer -------------------------------------------------
  await nav.getByRole("link", { name: /Tracker/ }).click();
  await page.getByRole("link", { name: "Network Engineer" }).click();
  await page.getByLabel("Move Network Engineer to").selectOption("offer");
  await page.getByLabel("Base pay").fill("110000");
  await page.getByLabel("PTO days (optional)").fill("20");
  await page.getByRole("button", { name: "Save offer" }).click();
  await expect(page.getByText(/\$110,000\/yr · \$52\.88\/hr/)).toBeVisible();

  // --- a second offer, then compare -------------------------------------------------------
  await page.getByRole("link", { name: "← Tracker" }).click();
  await page.getByRole("button", { name: "Add application" }).click();
  await page.getByLabel("Job title").fill("Security Analyst");
  await page.getByLabel("Stage").selectOption("offer");
  await page.getByRole("button", { name: "Add application" }).click();
  await expect(page.getByRole("heading", { name: "Security Analyst" })).toBeVisible();
  await page.getByLabel("Base pay").fill("52");
  await page.getByLabel("Per", { exact: true }).selectOption("hour");
  await page.getByLabel("Commute, minutes one way (optional)").fill("15");
  await page.getByRole("button", { name: "Save offer" }).click();
  await page.getByRole("link", { name: "Compare offers" }).click();

  const table = page.locator("table.compare-table");
  await expect(table.getByRole("columnheader", { name: /Network Engineer/ })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: /Security Analyst/ })).toBeVisible();
  const yearly = table.getByRole("row", { name: /Yearly equivalent/ });
  await expect(yearly.locator("td.best")).toHaveText(/\$110,000/); // vs $108,160 at $52/hr
  const commute = table.getByRole("row", { name: /Commute/ });
  await expect(commute.locator("td.best")).toHaveCount(0); // only one offer has a commute
});
