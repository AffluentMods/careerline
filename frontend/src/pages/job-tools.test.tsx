// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mailtoLink } from "../lib/followups";
import { SourcesTab } from "./jobs/SourcesTab";
import { DocumentPage } from "./materials/DocumentPage";
import { OverviewPage } from "./Overview";
import { FollowUpsList } from "./tracker/FollowUps";

afterEach(() => vi.restoreAllMocks());

function json(body: unknown, status = 200) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
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

function renderAt(path: string, routes: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>{routes}</Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const EMAIL_ADAPTER = {
  kind: "email_alerts",
  label: "Job alert emails (LinkedIn, Indeed, and more)",
  description: "Reads your job alert emails.",
  terms_url: "https://example.org",
  group: "inbox",
  min_interval_hours: 2,
  how_to: "1. Turn on alerts. 2. Create an app password.",
  config_fields: [
    { name: "host", label: "Mail server (IMAP)", required: true, default: "imap.gmail.com" },
    { name: "port", label: "Port", type: "number", default: 993 },
    { name: "sites", label: "Alert emails from", type: "multiselect", options: ["linkedin", "indeed"] },
  ],
  secret_fields: [
    { name: "username", label: "Email address", required: true },
    { name: "password", label: "App password", required: true, secret: true },
  ],
};
const REMOTIVE_ADAPTER = {
  kind: "remotive",
  label: "Remotive (remote jobs)",
  description: "Remote jobs.",
  terms_url: "https://example.org",
  group: "remote",
  min_interval_hours: 6,
  how_to: "",
  config_fields: [{ name: "keywords", label: "Keywords" }],
  secret_fields: [],
};

describe("job sources", () => {
  it("picks a source from grouped cards and fills in sensible defaults", async () => {
    const calls = mockApi((url, method) => {
      if (url.endsWith("/discovery/sources") && method === "GET") return json([]);
      if (url.endsWith("/discovery/adapters")) return json([EMAIL_ADAPTER, REMOTIVE_ADAPTER]);
      if (url.endsWith("/discovery/sources") && method === "POST") return json({ id: "s1" }, 201);
      return undefined;
    });
    renderAt("/", <Route path="/" element={<SourcesTab />} />);

    // With no sources yet, the picker opens by itself, grouped by kind.
    const inbox = await screen.findByRole("region", { name: /LinkedIn, Indeed/ });
    expect(within(screen.getByRole("region", { name: "Remote job boards" })).getByText("No key needed")).toBeInTheDocument();
    await userEvent.click(within(inbox).getByRole("button", { name: /Job alert emails/ }));

    expect(screen.getByText("Create an app password.")).toBeInTheDocument();
    expect(screen.getByLabelText("Mail server (IMAP)")).toHaveValue("imap.gmail.com");
    expect(screen.getByLabelText("Port")).toHaveValue(993);
    // A provider's minimum interval limits the choices.
    const every = screen.getByLabelText("Check every");
    expect(within(every).queryByRole("option", { name: "1 hours" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "Indeed" }));
    await userEvent.type(screen.getByLabelText("Email address"), "me@example.com");
    await userEvent.type(screen.getByLabelText("App password"), "app-pass");
    await userEvent.click(screen.getByRole("button", { name: "Add source" }));

    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toMatchObject({
      kind: "email_alerts",
      config: { host: "imap.gmail.com", port: 993, sites: ["linkedin"] },
      secret: { username: "me@example.com", password: "app-pass" },
    });
  });
});

const QUIET = {
  application_id: "a1",
  title: "Data Analyst",
  company: "Contoso",
  stage: "applied",
  last_activity: "2026-10-30T00:00:00Z",
  days_quiet: 16,
  follow_ups_sent: 0,
  contact_name: "Sam Ortiz",
};

describe("follow-ups", () => {
  it("drafts a follow-up, opens it in email, and marks it sent", async () => {
    const calls = mockApi((url, method) => {
      if (url.includes("/tracker/follow-ups")) return json([QUIET]);
      if (url.endsWith("/ai/settings")) return json({ provider: "none" });
      if (url.endsWith("/follow-up/draft"))
        return json({
          subject: "Following up: Data Analyst application",
          body: "Hi Sam,\n\nChecking in.\n\nJordan",
          to: "sam@contoso.example",
          number: 1,
          method: "rules",
          warnings: [],
        });
      if (url.endsWith("/follow-up/sent") && method === "POST") return json({ follow_ups_sent: 1 }, 201);
      return undefined;
    });
    renderAt("/", <Route path="/" element={<FollowUpsList />} />);

    expect(await screen.findByText(/No news for 16 days · Sam Ortiz/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Follow up" }));
    // No AI configured: only the plain draft is offered.
    expect(screen.queryByRole("button", { name: "Write it with AI" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Draft a short note" }));

    expect(await screen.findByLabelText("Subject")).toHaveValue("Following up: Data Analyst application");
    const open = screen.getByRole("link", { name: "Open in email" });
    expect(open.getAttribute("href")).toMatch(/^mailto:sam%40contoso\.example\?subject=Following%20up/);
    await userEvent.click(screen.getByRole("button", { name: "Mark as sent" }));
    expect(calls.find((c) => c.url.endsWith("/follow-up/sent"))?.body).toMatchObject({
      subject: "Following up: Data Analyst application",
    });
  });

  it("builds mailto links without plus signs for spaces", () => {
    const link = mailtoLink({ subject: "A b", body: "c d", to: "x@y.example", number: 1, method: "rules", warnings: [] });
    expect(link).toBe("mailto:x%40y.example?subject=A%20b&body=c%20d");
  });
});

describe("materials", () => {
  it("shows what screening software reads, and keyword coverage", async () => {
    const doc = {
      id: "d1",
      kind: "resume",
      title: "Resume for Analyst",
      is_master: false,
      posting_id: "p1",
      application_id: null,
      target_title: "Analyst",
      target_company: "Contoso",
      method: "rules",
      model: "",
      flagged: 0,
      stale: false,
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
      exported_at: null,
      content: {
        summary: null,
        experience: [],
        projects: [],
        education: [],
        certifications: [],
        skills: [],
        review: {
          summary: "Solid, but vague in places.",
          changes: [{ item_id: "i1", before: "Did reports", after: "Cut report time 40%", why: "Result first" }],
          discarded: [],
          missing: [
            { requirement: "Python", supported: true },
            { requirement: "Kubernetes", supported: false },
          ],
        },
      },
      sources: {},
    };
    mockApi((url) => {
      if (url.endsWith("/materials/documents/d1")) return json(doc);
      if (url.includes("/ats-check"))
        return json({
          format: "pdf",
          pages: 1,
          ok: false,
          flagged: 0,
          checks: [
            { id: "text", ok: true, label: "Text can be read", detail: "" },
            { id: "contact", ok: false, label: "Name and contact details come through", detail: "Not found as text: 555-0100" },
          ],
          keywords: { total: 4, covered: ["SQL", "Tableau"], could_add: ["Python"], gaps: ["Kubernetes"], score: 50 },
          text: "Jordan Rivera\nSUMMARY",
        });
      return undefined;
    });
    renderAt("/materials/d1", <Route path="/materials/:id" element={<DocumentPage />} />);

    expect(await screen.findByText("Solid, but vague in places.")).toBeInTheDocument();
    expect(screen.getByText(/Real gaps/).parentElement).toHaveTextContent("Kubernetes");
    await userEvent.click(screen.getByRole("button", { name: "Check the PDF" }));
    expect(await screen.findByText("Keyword match: 50%")).toBeInTheDocument();
    expect(screen.getByText(/In your vault but not on this page/).parentElement).toHaveTextContent("Python");
    expect(screen.getByText(/Not found as text: 555-0100/)).toBeInTheDocument();
  });
});

describe("today", () => {
  it("lists only the setup steps that are left, and the best new matches", async () => {
    mockApi((url) => {
      if (url.endsWith("/auth/me"))
        return json({ id: "u1", email: "a@b.c", display_name: "Jordan Rivera", is_owner: true, mfa_enabled: true, has_password: true });
      if (url.endsWith("/vault/summary"))
        return json({ kinds: { role: { total: 1, verified: 1 } }, stories: { total: 0, verified: 0 }, needs_review: 0 });
      if (url.endsWith("/discovery/sources")) return json([]);
      if (url.endsWith("/ai/settings")) return json({ provider: "none" });
      if (url.includes("/tracker/follow-ups")) return json([]);
      if (url.includes("/tracker/agenda")) return json([]);
      if (url.includes("/tracker/summary")) return json({ open_total: 0, stages: {} });
      if (url.includes("/discovery/postings"))
        return json({
          items: [
            {
              id: "p1",
              title: "Data Analyst",
              company: "Contoso",
              location: "Remote",
              work_mode: "remote",
              pay_min_cents: 9000000,
              pay_period: "year",
              currency: "USD",
              status: "new",
              score: { score: 82, method: "rules", summary: "", stale: false },
            },
          ],
          counts: { matches: 1 },
        });
      return undefined;
    });
    renderAt("/", <Route path="/" element={<OverviewPage />} />);

    expect(await screen.findByText("Hi, Jordan")).toBeInTheDocument();
    const setup = await screen.findByRole("region", { name: "Set up" });
    expect(within(setup).getByText(/Set up: 2 of 4 done/)).toBeInTheDocument();
    expect(within(setup).getByRole("link", { name: "Add sources" })).toHaveAttribute("href", "/jobs/sources");
    const match = await screen.findByRole("link", { name: /Data Analyst/ });
    expect(match).toHaveAttribute("href", "/jobs/postings/p1");
    expect(match).toHaveTextContent("Contoso · Remote · $90k");
  });
});
