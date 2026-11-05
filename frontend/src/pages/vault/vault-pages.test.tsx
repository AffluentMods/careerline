// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/api";
import type { Entry, EntryInput, Profile } from "../../lib/vault";
import { EntryForm, StateBadge } from "./entries";
import { liveSelection, reviewOrder, usefulSuggestions } from "./imports";
import { parseTags } from "./StoriesTab";

function entry(id: string, over: Partial<Entry> = {}): Entry {
  return {
    id,
    kind: "achievement",
    parent_id: null,
    data: { text: id },
    evidence_note: "",
    verified: false,
    verified_at: null,
    usable: false,
    source: "import",
    import_id: "imp",
    created_at: "",
    updated_at: "",
    ...over,
  };
}

function wrap(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const noop = () => {};

describe("import review helpers", () => {
  it("orders children right after their parent with depth", () => {
    const order = reviewOrder([
      entry("role", { kind: "role" }),
      entry("skill", { kind: "skill" }),
      entry("bullet", { parent_id: "role" }),
    ]);
    expect(order.map((o) => [o.entry.id, o.depth])).toEqual([
      ["role", 0],
      ["bullet", 1],
      ["skill", 0],
    ]);
  });

  it("treats entries whose parent is outside the import as roots", () => {
    expect(reviewOrder([entry("a", { parent_id: "elsewhere" })])[0]?.depth).toBe(0);
  });

  it("drops selections that were approved or deleted", () => {
    const entries = [entry("a"), entry("b", { verified: true })];
    expect(liveSelection(new Set(["a", "b", "gone"]), entries)).toEqual(["a"]);
  });

  it("only suggests profile values that would change something", () => {
    const profile: Profile = {
      full_name: "Jordan Rivera",
      headline: "",
      location_text: "",
      summary: "",
      email: "",
      phone: "",
      address: "",
    };
    const out = usefulSuggestions({ full_name: "Jordan Rivera", headline: "Analyst", email: " " }, profile);
    expect(out).toEqual([{ key: "headline", value: "Analyst", replaces: "" }]);
  });
});

describe("stories", () => {
  it("parses tags", () => {
    expect(parseTags(" leadership, Leadership , ,incident response")).toEqual(["leadership", "incident response"]);
  });
});

describe("EntryForm", () => {
  it("submits a verified role with only the fields filled in", async () => {
    const onSubmit = vi.fn<(input: EntryInput) => Promise<void>>().mockResolvedValue();
    wrap(<EntryForm kind="role" entries={[]} submitLabel="Add role" onSubmit={onSubmit} onCancel={noop} />);
    await userEvent.type(screen.getByLabelText("Official job title"), "Security Analyst");
    await userEvent.type(screen.getByLabelText("Employer (optional)"), "Northwind Tools");
    await userEvent.type(screen.getByLabelText("Start (optional)"), "2021-03");
    expect(screen.getByLabelText(/I confirm this is accurate/)).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Add role" }));
    expect(onSubmit).toHaveBeenCalledWith({
      kind: "role",
      parent_id: null,
      data: { title: "Security Analyst", employer: "Northwind Tools", start: "2021-03" },
      evidence_note: "",
      verified: true,
    });
  });

  it("requires a parent role for a responsibility and can save it as a draft", async () => {
    const onSubmit = vi.fn<(input: EntryInput) => Promise<void>>().mockResolvedValue();
    const role = entry("r1", { kind: "role", data: { title: "Analyst", employer: "Northwind" }, verified: true });
    wrap(<EntryForm kind="responsibility" entries={[role]} submitLabel="Add" onSubmit={onSubmit} onCancel={noop} />);
    const parent = screen.getByLabelText("Belongs to");
    expect(parent).toBeRequired();
    await userEvent.selectOptions(parent, "r1");
    await userEvent.type(screen.getByLabelText("Responsibility"), "Triaged alerts");
    await userEvent.click(screen.getByLabelText(/I confirm this is accurate/));
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "responsibility", parent_id: "r1", verified: false }),
    );
  });

  it("shows the server's field error next to the field", async () => {
    const onSubmit = vi
      .fn<(input: EntryInput) => Promise<void>>()
      .mockRejectedValue(
        new ApiError(422, "invalid_request", "Some fields are invalid.", [
          { loc: ["body", "data", "url"], msg: "Value error, must be an https:// link" },
        ]),
      );
    wrap(<EntryForm kind="link" entries={[]} submitLabel="Add link" onSubmit={onSubmit} onCancel={noop} />);
    await userEvent.type(screen.getByLabelText("Label"), "Site");
    await userEvent.type(screen.getByLabelText("URL"), "https://example.com");
    await userEvent.click(screen.getByRole("button", { name: "Add link" }));
    expect(await screen.findByText("must be an https:// link")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Some fields are invalid.");
  });
});

describe("StateBadge", () => {
  it("labels drafts and blocked entries", () => {
    const { rerender } = render(<StateBadge entry={{ verified: false, usable: false }} />);
    expect(screen.getByText("Draft")).toBeInTheDocument();
    rerender(<StateBadge entry={{ verified: true, usable: false }} />);
    expect(screen.getByText("Parent is a draft")).toBeInTheDocument();
    rerender(<StateBadge entry={{ verified: true, usable: true }} />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });
});
