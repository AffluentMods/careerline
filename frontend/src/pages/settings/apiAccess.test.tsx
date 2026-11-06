// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { connectionSnippets, mcpUrl, parseExpiry, type ApiKey } from "../../lib/apiAccess";
import { ApiAccessPage } from "./ApiAccessPage";

afterEach(() => vi.restoreAllMocks());

function json(body: unknown, status = 200) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

const USER = { id: "u1", email: "jordan@example.com", display_name: "Jordan", is_owner: true, mfa_enabled: true, has_password: true };
const STATUS = {
  has_users: true,
  registration_open: false,
  session: "active",
  password_login: true,
  sso: { enabled: false, label: "single sign-on" },
  demo: false,
};
const KEY: ApiKey = {
  id: "k1",
  name: "Claude",
  prefix: "cl_AbCdE",
  scopes: ["read", "propose"],
  created_at: "2026-11-01T10:00:00Z",
  last_used_at: null,
  expires_at: "2027-01-30T10:00:00Z",
  expired: false,
  conversation_id: "c9",
  pending_proposals: 2,
};
const SECRET = "cl_secretsecretsecretsecretsecretsecretsecret1";

type Call = { url: string; method: string; body: unknown };

function mockApi(handler: (url: string, method: string, body: unknown) => Response | undefined) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    const handled = handler(url, method, body);
    if (handled) return handled;
    if (url.endsWith("/auth/me")) return json(USER);
    if (url.endsWith("/auth/status")) return json(STATUS);
    return json({ error: { code: "not_found", message: "Not found." } }, 404);
  });
  return calls;
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ApiAccessPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("API access helpers", () => {
  it("builds connection snippets and parses expiry", () => {
    const url = mcpUrl("https://jobs.local:8080");
    expect(url).toBe("https://jobs.local:8080/api/mcp");
    const [claude, generic] = connectionSnippets(url, "cl_x");
    expect(claude?.text).toBe('claude mcp add --transport http careerline https://jobs.local:8080/api/mcp --header "Authorization: Bearer cl_x"');
    expect(JSON.parse(generic?.text ?? "{}").mcpServers.careerline).toEqual({
      type: "http",
      url,
      headers: { Authorization: "Bearer cl_x" },
    });
    expect(parseExpiry("never")).toBeNull();
    expect(parseExpiry("30")).toBe(30);
  });
});

describe("API access page", () => {
  it("lists keys with their proposals and revokes one after confirming", async () => {
    let keys = [KEY];
    const calls = mockApi((url, method) => {
      if (url.endsWith("/api-tokens") && method === "GET") return json(keys);
      if (url.endsWith("/api-tokens/k1") && method === "DELETE") {
        keys = [];
        return new Response(null, { status: 204 });
      }
      return undefined;
    });
    renderPage();

    const list = await screen.findByRole("list", { name: "API keys" });
    expect(within(list).getByText("Claude")).toBeInTheDocument();
    expect(within(list).getByText(/Can read, propose changes/)).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "2 proposed changes waiting for you" })).toHaveAttribute("href", "/assistant/c9");

    await userEvent.click(within(list).getByRole("button", { name: "Revoke Claude" }));
    await userEvent.click(within(list).getByRole("button", { name: "Revoke" }));
    expect(await screen.findByText("No keys yet.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/api-tokens/k1"))).toBe(true);
  });

  it("asks for the password, then shows the new key once with setup snippets", async () => {
    let reauthed = false;
    const calls = mockApi((url, method) => {
      if (url.endsWith("/api-tokens") && method === "GET") return json([]);
      if (url.endsWith("/auth/reauth") && method === "POST") {
        reauthed = true;
        return json({ until: "2026-11-05T12:10:00Z" });
      }
      if (url.endsWith("/api-tokens") && method === "POST") {
        if (!reauthed) return json({ error: { code: "reauth_required", message: "Confirm it's you first." } }, 403);
        return json({ ...KEY, id: "k2", name: "Laptop", scopes: ["read"], conversation_id: null, pending_proposals: 0, token: SECRET }, 201);
      }
      return undefined;
    });
    renderPage();

    await userEvent.type(await screen.findByLabelText("Name"), "Laptop");
    await userEvent.click(screen.getByRole("checkbox", { name: /Add drafts/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Propose changes/ }));
    await userEvent.selectOptions(screen.getByLabelText("Expires after"), "never");
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));

    const password = await screen.findByLabelText("Password");
    // A form inside the key form makes real browsers submit (and reload) the outer one.
    expect(password.closest("form")?.parentElement?.closest("form")).toBeNull();
    await userEvent.type(password, "correct horse battery staple");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(calls.filter((c) => c.method === "POST" && c.url.endsWith("/api-tokens"))).toHaveLength(2);

    expect(await screen.findByLabelText("API key")).toHaveTextContent(SECRET);
    expect(screen.getByText(/only time it's shown/)).toBeInTheDocument();
    expect(screen.getByText(/claude mcp add --transport http careerline/)).toHaveTextContent(`Bearer ${SECRET}`);
    const creates = calls.filter((c) => c.method === "POST" && c.url.endsWith("/api-tokens"));
    expect(creates.at(-1)?.body).toEqual({ name: "Laptop", scopes: ["read"], expires_in_days: null });

    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create key" })).toBeInTheDocument();
  });

  it("can't create a key with nothing allowed", async () => {
    mockApi((url, method) => (url.endsWith("/api-tokens") && method === "GET" ? json([]) : undefined));
    renderPage();
    await userEvent.type(await screen.findByLabelText("Name"), "x");
    for (const name of [/^Read/, /Add drafts/, /Propose changes/]) {
      await userEvent.click(screen.getByRole("checkbox", { name }));
    }
    expect(screen.getByRole("button", { name: "Create key" })).toBeDisabled();
  });
});
