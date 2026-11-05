// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatMicros } from "../../lib/ai";
import type { ConversationDetail } from "../../lib/assistant";
import { docItems, type DocDetail } from "../../lib/materials";
import { AssistantPage } from "./AssistantPage";

afterEach(() => vi.restoreAllMocks());

function json(body: unknown, status = 200) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

const NOW = "2026-11-04T12:00:00Z";

function conversation(over: Partial<ConversationDetail> = {}): ConversationDetail {
  return { id: "c1", title: "Follow-ups", source: "chat", created_at: NOW, updated_at: NOW, messages: [], pending: 0, ...over };
}

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/assistant/:id" element={<AssistantPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

type Handler = (url: string, init: RequestInit | undefined) => Response | undefined;

function mockApi(handler: Handler, provider = "anthropic") {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const handled = handler(url, init);
    if (handled) return handled;
    if (url.endsWith("/ai/settings")) return json({ provider });
    if (url.endsWith("/assistant/conversations") && method === "GET") return json([conversation()]);
    return json({ error: { code: "not_found", message: "Not found." } }, 404);
  });
  return calls;
}

describe("assistant page", () => {
  it("asks for a provider when AI is off", async () => {
    mockApi(() => undefined, "none");
    renderAt("/assistant");
    expect(await screen.findByText(/needs an AI provider/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New conversation" })).not.toBeInTheDocument();
  });

  it("shows proposed changes and sends the decision", async () => {
    let detail = conversation({
      pending: 1,
      messages: [
        { id: "m1", role: "user", text: "Remind me to follow up with Acme on Friday." },
        { id: "m2", role: "tool", text: "", tool: "list_applications" },
        { id: "a1", role: "action", text: "Create a reminder: Follow up with Acme on 2026-11-06", status: "pending", tool: "create_reminder" },
      ],
    });
    const calls = mockApi((url, init) => {
      if (url.endsWith("/assistant/conversations/c1")) return json(detail);
      if (url.endsWith("/assistant/actions/a1") && init?.method === "POST") {
        detail = conversation({
          messages: detail.messages.map((m) => (m.id === "a1" ? { ...m, status: "confirmed", result: "Reminder added." } : m)),
        });
        return json(detail);
      }
      return undefined;
    });
    renderAt("/assistant/c1");

    const card = (await screen.findByText(/Create a reminder/)).closest("li")!;
    expect(screen.getByText("Looked up list applications")).toBeInTheDocument();
    // Sending is blocked until the proposal is decided.
    expect(screen.getByLabelText("Message")).toBeDisabled();

    await userEvent.click(within(card).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Done: Reminder added.")).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/assistant/actions/a1"))?.body).toEqual({ confirm: true });
    expect(screen.getByLabelText("Message")).toBeEnabled();
  });

  it("sends a message with today's date and shows the reply", async () => {
    let detail = conversation();
    const calls = mockApi((url, init) => {
      if (url.endsWith("/assistant/conversations/c1")) return json(detail);
      if (url.endsWith("/assistant/conversations/c1/messages") && init?.method === "POST") {
        detail = conversation({
          messages: [
            { id: "m1", role: "user", text: "What is due?" },
            { id: "m2", role: "assistant", text: "Nothing is due this week." },
          ],
        });
        return json(detail);
      }
      return undefined;
    });
    renderAt("/assistant/c1");
    await userEvent.type(await screen.findByLabelText("Message"), "What is due?{Enter}");
    expect(await screen.findByText("Nothing is due this week.")).toBeInTheDocument();
    const sent = calls.find((c) => c.url.endsWith("/messages"))?.body as { text: string; today: string };
    expect(sent.text).toBe("What is due?");
    expect(sent.today).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("lets you decide API-key proposals even without an AI provider", async () => {
    const api = conversation({
      id: "k1",
      title: "Proposed by API key “Claude”",
      source: "api",
      pending: 1,
      messages: [{ id: "a1", role: "action", text: "Add a reminder: “Follow up”", status: "pending", tool: "create_reminder" }],
    });
    mockApi((url, init) => {
      if (url.endsWith("/assistant/conversations") && (init?.method ?? "GET") === "GET") return json([conversation(), api]);
      if (url.endsWith("/assistant/conversations/k1")) return json(api);
      return undefined;
    }, "none");
    renderAt("/assistant/k1");

    expect(await screen.findByText(/Add a reminder/)).toBeInTheDocument();
    expect(screen.getByText(/needs an AI provider/)).toBeInTheDocument();
    // Only the API conversation is listed, there's nothing to type into, and no new chats.
    const list = screen.getByRole("complementary", { name: "Conversations" });
    expect(within(list).getAllByRole("link").map((l) => l.textContent)).toEqual(["Proposed by API key “Claude”"]);
    expect(screen.queryByLabelText("Message")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New conversation" })).not.toBeInTheDocument();
    expect(screen.getByText(/proposed by an AI agent using one of your API keys/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled();
  });

  it("keeps the draft when sending fails", async () => {
    mockApi((url, init) => {
      if (url.endsWith("/assistant/conversations/c1")) return json(conversation());
      if (url.endsWith("/messages") && init?.method === "POST") {
        return json({ error: { code: "budget_reached", message: "This month's AI budget is used up." } }, 402);
      }
      return undefined;
    });
    renderAt("/assistant/c1");
    await userEvent.type(await screen.findByLabelText("Message"), "Hello{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("budget is used up");
    expect(screen.getByLabelText("Message")).toHaveValue("Hello");
  });
});

describe("AI and materials helpers", () => {
  it("formats costs in micros", () => {
    expect(formatMicros(null)).toBe("—");
    expect(formatMicros(0)).toBe("$0");
    expect(formatMicros(4_000)).toBe("< $0.01");
    expect(formatMicros(1_250_000)).toBe("$1.25");
  });

  it("lists every checkable item in a document", () => {
    const item = (id: string) => ({ id, text: id, fact_ids: [], status: "linked" as const, issues: [] });
    const resume = {
      kind: "resume",
      content: {
        summary: item("s"),
        experience: [{ id: "e", bullets: [item("b1"), item("b2")] }],
        projects: [{ id: "p", bullets: [item("b3")] }],
      },
    } as unknown as DocDetail;
    expect(docItems(resume).map((i) => i.id)).toEqual(["s", "b1", "b2", "b3"]);
    const letter = { kind: "cover_letter", content: { paragraphs: [item("p1"), item("p2")] } } as unknown as DocDetail;
    expect(docItems(letter).map((i) => i.id)).toEqual(["p1", "p2"]);
  });
});
