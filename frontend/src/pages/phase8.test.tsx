// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatBytes, importSummary } from "../lib/data";
import { days, niceTicks, percent, shortDate, type Insights } from "../lib/insights";
import { toInput } from "../lib/notify";
import { InsightsPage } from "./insights/InsightsPage";
import { DataPage } from "./settings/DataPage";
import { NotificationsPage } from "./settings/NotificationsPage";

afterEach(() => vi.restoreAllMocks());

function json(body: unknown, status = 200) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

type Call = { url: string; method: string; body: unknown };

function mockApi(handler: (url: string, method: string, body: unknown) => Response | undefined) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    return handler(url, method, body) ?? json({ error: { code: "not_found", message: "Not found." } }, 404);
  });
  return calls;
}

const USER = {
  id: "u1",
  email: "jordan@example.com",
  display_name: "Jordan Rivera",
  is_owner: true,
  mfa_enabled: true,
  has_password: true,
};
const STATUS = {
  has_users: true,
  registration_open: false,
  session: "active",
  password_login: true,
  sso: { enabled: false, label: "single sign-on" },
  demo: false,
};

describe("helpers", () => {
  it("formats insight numbers", () => {
    expect(percent(null)).toBe("—");
    expect(percent(0.456)).toBe("46%");
    expect(days(1)).toBe("1 day");
    expect(days(3.25)).toBe("3.3 days");
    expect(niceTicks(0)).toEqual([0, 1]);
    expect(niceTicks(7)).toEqual([0, 2, 4, 6, 8]);
    expect(niceTicks(3)).toEqual([0, 1, 2, 3]);
    expect(shortDate("2026-10-05")).toMatch(/Oct/);
  });

  it("only sends notification secrets that were edited", () => {
    const form = {
      timezone: "UTC",
      digest_enabled: true,
      digest_hour: 8,
      matches_enabled: false,
      match_min_score: 80,
      email_enabled: false,
      ntfy_enabled: true,
      webhook_enabled: false,
      webhook_kind: "discord" as const,
    };
    expect(toInput(form, {})).not.toHaveProperty("ntfy_url");
    expect(toInput(form, { ntfy_token: "" })).toMatchObject({ ntfy_token: "" });
    expect(toInput(form, { ntfy_url: " https://ntfy.sh/x " })).toMatchObject({ ntfy_url: "https://ntfy.sh/x" });
  });

  it("summarizes imports and sizes", () => {
    expect(importSummary({ imported: { vault_entries: 3, attachments: 1, jobs: 0 }, notes: [], from_version: null })).toBe(
      "3 vault entries, 1 files",
    );
    expect(importSummary({ imported: {}, notes: [], from_version: null })).toMatch(/nothing/);
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

const INSIGHTS: Insights = {
  days: null,
  today: "2026-11-04",
  totals: {
    tracked: 6,
    applied: 5,
    responded: 3,
    positive: 2,
    interviews: 2,
    offers: 1,
    rejected: 1,
    withdrawn: 1,
    awaiting: 0,
    quiet: 1,
  },
  response_rate: 0.6,
  positive_rate: 0.4,
  median_days_to_response: 14,
  measured_responses: 3,
  funnel: [
    { stage: "applied", count: 5, rate_from_previous: null, rate_from_applied: 1 },
    { stage: "screening", count: 2, rate_from_previous: 0.4, rate_from_applied: 0.4 },
    { stage: "interviewing", count: 2, rate_from_previous: 1, rate_from_applied: 0.4 },
    { stage: "offer", count: 1, rate_from_previous: 0.5, rate_from_applied: 0.2 },
    { stage: "accepted", count: 0, rate_from_previous: 0, rate_from_applied: 0 },
  ],
  sources: [
    {
      source: "Job board <script>",
      applied: 3,
      responded: 1,
      positive: 0,
      offers: 0,
      response_rate: 0.333,
      median_days_to_response: 28,
    },
    { source: "Referral", applied: 2, responded: 2, positive: 2, offers: 1, response_rate: 1, median_days_to_response: 9.5 },
  ],
  weekly: Array.from({ length: 12 }, (_, i) => ({
    week_start: `2026-${i < 4 ? "08" : i < 8 ? "09" : "10"}-${String(1 + (i % 4) * 7).padStart(2, "0")}`,
    applied: i === 10 ? 3 : i % 3 === 0 ? 1 : 0,
  })),
  response_times: [
    { bucket: "Within a week", count: 1 },
    { bucket: "1–2 weeks", count: 1 },
    { bucket: "2–4 weeks", count: 0 },
    { bucket: "Over 4 weeks", count: 1 },
  ],
  quiet_days: 30,
};

describe("insights page", () => {
  it("shows the summary, charts with accessible names, and a table view", async () => {
    const calls = mockApi((url) => (url.includes("/insights") ? json(INSIGHTS) : undefined));
    wrap(<InsightsPage />);
    expect(await screen.findByText("60%")).toBeInTheDocument();
    expect(screen.getByText("14 days")).toBeInTheDocument();
    expect(screen.getByText(/1 application has had no answer/)).toBeInTheDocument();
    expect(screen.getByLabelText("Offer: 1, 50% of previous")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Applications per week, last 12 weeks" })).toBeInTheDocument();
    // Untrusted source names are text, never markup.
    expect(screen.getByText("Job board <script>")).toBeInTheDocument();

    const funnel = screen.getByRole("region", { name: "Stage funnel" });
    await userEvent.click(within(funnel).getByRole("button", { name: "Show table" }));
    expect(within(funnel).getByRole("table")).toHaveTextContent("Interviewing");

    await userEvent.selectOptions(screen.getByLabelText("Time range"), "90");
    expect(calls.some((c) => c.url.includes("days=90"))).toBe(true);
  });

  it("explains the empty state", async () => {
    mockApi((url) =>
      url.includes("/insights") ? json({ ...INSIGHTS, totals: { ...INSIGHTS.totals, applied: 0 } }) : undefined,
    );
    wrap(<InsightsPage />);
    expect(await screen.findByText(/Insights fill in as you move applications/)).toBeInTheDocument();
  });
});

describe("data page", () => {
  it("asks for the password again before exporting", async () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:x");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    let confirmed = false;
    const calls = mockApi((url, method) => {
      if (url.endsWith("/auth/me")) return json(USER);
      if (url.endsWith("/auth/status")) return json(STATUS);
      if (url.endsWith("/data/backups")) return json({ enabled: false, files: [] });
      if (url.endsWith("/auth/reauth") && method === "POST") {
        confirmed = true;
        return json({ until: "2026-11-04T12:10:00Z" });
      }
      if (url.endsWith("/data/export")) {
        if (!confirmed) return json({ error: { code: "reauth_required", message: "Confirm it's you first." } }, 403);
        // A string body: Node 22's Response rejects jsdom's Blob (Node 24 accepts it).
        return new Response("zip", {
          headers: { "content-disposition": 'attachment; filename="careerline-export-2026-11-04.zip"' },
        });
      }
      return undefined;
    });
    wrap(<DataPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Download export" }));
    const password = await screen.findByLabelText("Password");
    await userEvent.type(password, "correct horse battery staple");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Your export is downloading.")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.endsWith("/data/export"))).toHaveLength(2);
    expect(calls.find((c) => c.url.endsWith("/auth/reauth"))?.body).toEqual({
      password: "correct horse battery staple",
    });
    expect(screen.getByText(/Backups are off/)).toBeInTheDocument();
  });

  it("hides server backups from non-owners", async () => {
    mockApi((url) => {
      if (url.endsWith("/auth/me")) return json({ ...USER, is_owner: false });
      if (url.endsWith("/auth/status")) return json(STATUS);
      return undefined;
    });
    wrap(<DataPage />);
    expect(await screen.findByText("Export everything")).toBeInTheDocument();
    expect(screen.queryByText("Server backups")).not.toBeInTheDocument();
  });
});

describe("notifications page", () => {
  const SETTINGS = {
    timezone: "America/Chicago",
    digest_enabled: true,
    digest_hour: 8,
    matches_enabled: false,
    match_min_score: 80,
    email_enabled: false,
    email_to: "",
    email_available: false,
    ntfy_enabled: true,
    ntfy_url: "https://ntfy.sh/careerline-xyz",
    has_ntfy_token: true,
    webhook_enabled: false,
    webhook_kind: "discord",
    webhook_hint: "",
    private_targets_allowed: true,
    demo: false,
  };

  it("keeps saved secrets unless they are changed, and tests a channel", async () => {
    const calls = mockApi((url, method, body) => {
      if (url.endsWith("/notifications/settings") && method === "GET") return json(SETTINGS);
      if (url.endsWith("/notifications/settings") && method === "PUT") return json({ ...SETTINGS, ...(body as object) });
      if (url.endsWith("/notifications/log")) return json([]);
      if (url.endsWith("/notifications/test")) return json({ ok: false, error: "The server answered with status 500." });
      return undefined;
    });
    wrap(<NotificationsPage />);
    expect(await screen.findByDisplayValue("https://ntfy.sh/careerline-xyz")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Email/)).toBeDisabled(); // no SMTP on this server
    await userEvent.click(screen.getByRole("button", { name: "Save notification settings" }));
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.body).not.toHaveProperty("ntfy_token");
    expect(put?.body).not.toHaveProperty("ntfy_url");
    expect(put?.body).toMatchObject({ timezone: "America/Chicago", digest_enabled: true });

    await userEvent.click(screen.getByRole("button", { name: "Send a test" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Test by ntfy failed: The server answered with status 500.");
  });
});
