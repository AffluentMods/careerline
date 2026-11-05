// SPDX-License-Identifier: Apache-2.0
// Career Vault end to end: manual entries, resume import review, and a linked story.
// The resume fixture is fictional; regenerate it with:
//   cd backend/tests && ../.venv/Scripts/python -c "from resume_fixtures import make_docx; \
//   open('../../frontend/e2e/fixtures/casey-lin-resume.docx','wb').write(make_docx())"
import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PASSWORD = "e2e vault battery staple";
const FIXTURE = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "casey-lin-resume.docx");

async function reviewCount(page: Page) {
  return page.getByRole("navigation", { name: "Main" }).getByLabel(/to review/);
}

test("build a vault by hand, import a resume, approve drafts, write a story", async ({ page }) => {
  // The auth spec created the owner; registration is open on the e2e server.
  await page.goto("/register");
  await page.getByLabel("Name").fill("Riley Park");
  await page.getByLabel("Email").fill("riley@example.test");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Hi, Riley" })).toBeVisible();

  // --- add a role and an achievement by hand ------------------------------------------
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: /Career Vault/ }).click();
  await expect(page).toHaveURL(/\/vault\/experience$/);
  await expect(page.getByRole("heading", { name: "Start your vault" })).toBeVisible();

  await page.getByRole("button", { name: "Add role" }).click();
  await page.getByLabel("Official job title").fill("Network Engineer");
  await page.getByLabel("Employer (optional)").fill("Tailspin Networks");
  await page.getByLabel("Start (optional)").fill("2020-02");
  await expect(page.getByLabel(/I confirm this is accurate/)).toBeChecked();
  await page.getByRole("button", { name: "Add role" }).click();

  const role = page.locator("li.entry", { hasText: "Network Engineer" }).first();
  await expect(role.getByText("Verified")).toBeVisible();
  await expect(role.getByText("Tailspin Networks · Feb 2020 – Present")).toBeVisible();

  await role.getByRole("button", { name: "Add achievement" }).click();
  await role.getByLabel("Achievement", { exact: true }).fill("Migrated 12 branch offices to SD-WAN");
  await role.getByRole("button", { name: "Add achievement" }).click();
  await expect(role.getByText("Migrated 12 branch offices to SD-WAN")).toBeVisible();
  await expect(await reviewCount(page)).toHaveCount(0);

  // --- import a fictional resume and review the drafts --------------------------------
  await page.getByRole("link", { name: "Imports" }).click();
  await page.locator('input[type="file"]').setInputFiles(FIXTURE);
  await expect(page.getByRole("heading", { name: "Review casey-lin-resume.docx" })).toBeVisible();
  await expect(page.getByText("10 drafts found in experience, education, skills. 10 left to review.")).toBeVisible();
  await expect(page.getByText("What we read")).toBeVisible();
  await expect(await reviewCount(page)).toHaveText("10");

  // Approve a role and its first bullet.
  await page.getByLabel("Select IT Support Specialist").check();
  await page.getByLabel("Select Resolved 40 tickets per week on average").check();
  await page.getByRole("button", { name: "Approve selected (2)" }).click();
  await expect(page.getByText("8 left to review.", { exact: false })).toBeVisible();
  const approved = page.locator("li.entry", { hasText: "Resolved 40 tickets per week on average" });
  await expect(approved.getByText("Verified")).toBeVisible();

  // Discard one draft.
  await page.getByLabel("Select Ticketing").check();
  await page.getByRole("button", { name: "Discard selected" }).click();
  await expect(page.getByText("7 left to review.", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Select Ticketing")).toHaveCount(0);
  await expect(await reviewCount(page)).toHaveText("7");

  // Drafts never show as usable; the approved role shows up in Experience.
  await page.getByRole("link", { name: /^Experience/ }).click();
  const badge = (title: string) =>
    page.locator("li.entry", { hasText: title }).first().locator(".entry-title-row").first().locator(".badge");
  await expect(badge("IT Support Specialist")).toHaveText("Verified");
  await expect(badge("Desk Clerk")).toHaveText("Draft");

  // --- write a story linked to the hand-entered achievement ---------------------------
  await page.getByRole("link", { name: "Stories" }).click();
  await page.getByRole("button", { name: "Add story" }).click();
  await page.getByLabel("Title").fill("Branch network migration");
  await page.getByLabel("Situation (optional)").fill("Twelve branch offices ran on aging leased lines.");
  await page.getByLabel("Result (optional)").fill("All twelve moved to SD-WAN with no unplanned downtime.");
  await page.getByLabel("Tags (optional)").fill("networking, project delivery");
  await page.getByLabel(/Migrated 12 branch offices to SD-WAN/).check();
  await page.getByRole("button", { name: "Add story" }).click();

  const story = page.locator("li.story", { hasText: "Branch network migration" });
  await expect(story.getByText("Based on: Achievement: Migrated 12 branch offices to SD-WAN")).toBeVisible();
  await expect(story.getByText("project delivery")).toBeVisible();

  // Everything survives a reload (it's in the database, not just the page).
  await page.reload();
  await expect(page.locator("li.story", { hasText: "Branch network migration" })).toBeVisible();
  await expect(await reviewCount(page)).toHaveText("7");
});
