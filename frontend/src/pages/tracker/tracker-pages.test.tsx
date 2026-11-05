// SPDX-License-Identifier: Apache-2.0
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AgendaItem, Offer } from "../../lib/tracker";
import { summary } from "../../lib/tracker-fixtures";
import { groupAgenda } from "./AgendaTab";
import { MoveMenu } from "./Board";
import { bestIds, groupByCurrency } from "./OffersTab";
import { sortApplications } from "./TrackerPage";

function offer(id: string, over: Partial<Offer> = {}): Offer {
  return {
    id,
    application_id: id,
    application_title: id,
    company: "",
    stage: "offer",
    currency: "USD",
    base_cents: 10_000_000,
    base_period: "year",
    hours_per_week: 40,
    bonus_cents: null,
    bonus_notes: "",
    equity_notes: "",
    pto_days: null,
    schedule: "",
    commute_minutes: null,
    growth_notes: "",
    benefits_notes: "",
    deadline: null,
    annual_cents: 10_000_000,
    hourly_cents: 4_808,
    total_annual_cents: 10_000_000,
    ...over,
  };
}

function item(id: string, due_on: string): AgendaItem {
  return {
    source: "reminder",
    id,
    title: id,
    kind: "task",
    due_on,
    due_time: null,
    application_id: null,
    application_title: "",
    company: "",
    contact_id: null,
    contact_name: "",
  };
}

describe("offer comparison", () => {
  const annual = { score: (o: Offer) => o.annual_cents, better: "high" as const };
  const commute = { score: (o: Offer) => o.commute_minutes, better: "low" as const };

  it("highlights the best value, including ties", () => {
    const offers = [offer("a"), offer("b", { annual_cents: 12_000_000 }), offer("c", { annual_cents: 12_000_000 })];
    expect([...bestIds(offers, annual)].sort()).toEqual(["b", "c"]);
  });

  it("prefers the lowest for commute and skips missing values", () => {
    const offers = [offer("a", { commute_minutes: 40 }), offer("b", { commute_minutes: 15 }), offer("c")];
    expect([...bestIds(offers, commute)]).toEqual(["b"]);
  });

  it("highlights nothing when there is nothing to compare", () => {
    expect(bestIds([offer("a")], annual).size).toBe(0);
    expect(bestIds([offer("a"), offer("b")], annual).size).toBe(0); // all equal
    expect(bestIds([offer("a"), offer("b")], { better: "high" }).size).toBe(0); // no score
  });

  it("never compares across currencies", () => {
    const groups = groupByCurrency([offer("a"), offer("b", { currency: "EUR" }), offer("c")]);
    expect(groups.map(([cur, list]) => [cur, list.map((o) => o.id)])).toEqual([
      ["USD", ["a", "c"]],
      ["EUR", ["b"]],
    ]);
  });
});

describe("agenda and list", () => {
  it("groups agenda items in order and drops empty groups", () => {
    const groups = groupAgenda([item("late", "2026-11-01"), item("now", "2026-11-04"), item("far", "2026-12-20")], "2026-11-04");
    expect(groups.map(([g, items]) => [g, items.map((i) => i.id)])).toEqual([
      ["overdue", ["late"]],
      ["today", ["now"]],
      ["later", ["far"]],
    ]);
  });

  it("sorts the list by stage order, not alphabetically", () => {
    const apps = [summary({ id: "o", stage: "offer" }), summary({ id: "s", stage: "saved" }), summary({ id: "i", stage: "interviewing" })];
    expect(sortApplications(apps, "stage", true).map((a) => a.id)).toEqual(["s", "i", "o"]);
    expect(sortApplications(apps, "stage", false).map((a) => a.id)).toEqual(["o", "i", "s"]);
  });
});

describe("MoveMenu", () => {
  it("moves a card without dragging", async () => {
    const onMove = vi.fn();
    render(<MoveMenu app={{ id: "a", title: "Security Engineer", stage: "saved" }} onMove={onMove} />);
    const menu = screen.getByLabelText("Move Security Engineer to");
    expect(menu).toHaveValue("saved");
    await userEvent.selectOptions(menu, "applied");
    expect(onMove).toHaveBeenCalledWith("applied");
  });
});
