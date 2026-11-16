// SPDX-License-Identifier: Apache-2.0
/** Screenshots for the in-app guide, from the demo account (fictional data). */
import { expect, test, type Page } from "@playwright/test";

const OUT = "public/guide";

async function shot(page: Page, name: string) {
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${OUT}/${name}.jpg`, type: "jpeg", quality: 78 });
}

/** Calls the API as the signed-in demo user (with the CSRF header the app sends). */
async function api(page: Page, method: string, path: string, body?: unknown): Promise<unknown> {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf = document.cookie
        .split(";")
        .map((c) => c.trim().split("="))
        .find(([k]) => k === "cl_csrf" || k === "__Host-cl_csrf")?.[1];
      const r = await fetch(`/api/v1${path}`, {
        method,
        headers: { "Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(csrf ?? "") },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return r.status === 204 ? null : r.json();
    },
    { method, path, body },
  );
}

test("guide screenshots", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("demo@example.com");
  await page.getByLabel("Password").fill("explore-careerline");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));

  await page.goto("/vault/experience");
  await shot(page, "vault");

  await page.goto("/jobs/sources");
  const picker = page.getByRole("heading", { name: "Where should CareerLine look for jobs?" });
  const add = page.getByRole("button", { name: "Add a source" });
  await expect(picker.or(add)).toBeVisible();
  if (await add.isVisible()) await add.click();
  await expect(picker).toBeVisible();
  await page.getByRole("heading", { name: "Where should CareerLine look for jobs?" }).scrollIntoViewIfNeeded();
  await shot(page, "sources");
  await page.getByRole("button", { name: /Job alert emails/ }).click();
  await page.getByRole("heading", { name: /New source: Job alert emails/ }).scrollIntoViewIfNeeded();
  await shot(page, "email-alerts");

  await page.goto("/jobs");
  await shot(page, "jobs");

  const postings = (await api(page, "GET", "/discovery/postings?view=all")) as { items: { id: string }[] };
  const top = postings.items[0]?.id;
  if (top) {
    await page.goto(`/jobs/postings/${top}`);
    await shot(page, "posting");
    const doc = (await api(page, "POST", "/materials/documents", { kind: "resume", posting_id: top })) as { id: string };
    await page.goto(`/materials/${doc.id}`);
    await page.getByRole("button", { name: /Check the PDF/ }).click();
    await expect(page.getByText("Keyword match", { exact: false })).toBeVisible();
    await shot(page, "document");
    await api(page, "POST", "/interview/preps", { method: "rules", posting_id: top });
  }

  await page.goto("/tracker/board");
  await shot(page, "tracker");

  await page.goto("/tracker/agenda");
  const follow = page.getByRole("button", { name: "Follow up", exact: true }).first();
  await expect(follow).toBeVisible();
  await follow.click();
  await page.getByRole("button", { name: "Draft a short note" }).click();
  await expect(page.getByLabel("Subject")).toBeVisible();
  await shot(page, "followups");

  await page.goto("/interview");
  const prep = page.locator("main a[href^='/interview/']").first();
  await expect(prep).toBeVisible();
  await prep.click();
  await expect(page.getByText(/question/i).first()).toBeVisible();
  await shot(page, "interview");

  await page.goto("/");
  await shot(page, "today");
});
