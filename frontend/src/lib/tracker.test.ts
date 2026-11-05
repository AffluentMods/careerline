// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import {
  addDays,
  agendaGroup,
  annualCents,
  attachmentType,
  centsToInput,
  daysBetween,
  formatDue,
  formatPay,
  groupByStage,
  hourlyCents,
  localToday,
  parseMoney,
  sortForBoard,
} from "./tracker";
import { summary } from "./tracker-fixtures";

describe("money", () => {
  it("parses user input into cents", () => {
    expect(parseMoney("52.50")).toBe(5250);
    expect(parseMoney("$110,000")).toBe(11_000_000);
    expect(parseMoney("52.5")).toBe(5250);
    expect(parseMoney(" ")).toBeNull();
    expect(parseMoney("12.345")).toBeNaN();
    expect(parseMoney("ten")).toBeNaN();
    expect(parseMoney("-5")).toBeNaN();
  });

  it("round-trips cents to an input value", () => {
    expect(centsToInput(11_000_000)).toBe("110000");
    expect(centsToInput(5250)).toBe("52.50");
    expect(centsToInput(null)).toBe("");
  });

  it("matches the server's annual and hourly maths", () => {
    expect(annualCents(5_000, "hour", 40)).toBe(10_400_000);
    expect(hourlyCents(10_000_000, "year", 40)).toBe(4_808);
    expect(hourlyCents(10_000_000, "year", 32)).toBe(6_010);
    expect(hourlyCents(5_000, "hour", 20)).toBe(5_000);
  });

  it("formats pay ranges", () => {
    const hourly = formatPay(summary({ pay_min_cents: 5_000, pay_max_cents: 6_500, pay_period: "hour" }));
    expect(hourly).toMatch(/50.*65.*\/hr$/);
    expect(formatPay(summary({}))).toBe("");
    expect(formatPay(summary({ pay_min_cents: 12_000_000, pay_max_cents: null, pay_period: "year" }))).toMatch(/\/yr$/);
  });
});

describe("dates", () => {
  it("uses the local calendar date", () => {
    expect(localToday(new Date(2026, 10, 4, 23, 30))).toBe("2026-11-04");
    expect(addDays("2026-11-28", 7)).toBe("2026-12-05");
    expect(daysBetween("2026-11-04", "2026-11-01")).toBe(-3);
  });

  it("describes due dates", () => {
    expect(formatDue({ due_on: "2026-11-04", due_time: "09:30:00" }, "2026-11-04")).toBe("Today 09:30");
    expect(formatDue({ due_on: "2026-11-05", due_time: null }, "2026-11-04")).toBe("Tomorrow");
    expect(formatDue({ due_on: "2026-11-01", due_time: null }, "2026-11-04")).toBe("3 days overdue");
    expect(formatDue({ due_on: "2026-11-08", due_time: null }, "2026-11-04")).toBe("In 4 days");
  });

  it("groups the agenda", () => {
    expect(agendaGroup("2026-11-01", "2026-11-04")).toBe("overdue");
    expect(agendaGroup("2026-11-04", "2026-11-04")).toBe("today");
    expect(agendaGroup("2026-11-11", "2026-11-04")).toBe("week");
    expect(agendaGroup("2026-11-12", "2026-11-04")).toBe("later");
  });
});

describe("board", () => {
  it("sorts by next due item, then most recent activity", () => {
    const apps = [
      summary({ id: "quiet-old", last_activity_at: "2026-10-01T00:00:00Z" }),
      summary({ id: "due-later", next_due: { due_on: "2026-11-20", due_time: null, title: "x", source: "reminder" } }),
      summary({ id: "quiet-new", last_activity_at: "2026-11-03T00:00:00Z" }),
      summary({ id: "due-soon", next_due: { due_on: "2026-11-05", due_time: "08:00:00", title: "x", source: "reminder" } }),
    ];
    expect(sortForBoard(apps).map((a) => a.id)).toEqual(["due-soon", "due-later", "quiet-new", "quiet-old"]);
  });

  it("groups by stage with every column present", () => {
    const groups = groupByStage([summary({ id: "a", stage: "applied" }), summary({ id: "b", stage: "rejected" })]);
    expect(groups.applied.map((a) => a.id)).toEqual(["a"]);
    expect(groups.rejected.map((a) => a.id)).toEqual(["b"]);
    expect(groups.saved).toEqual([]);
  });

  it("maps attachment file names to allowed types", () => {
    expect(attachmentType({ name: "Offer.PDF" })).toBe("application/pdf");
    expect(attachmentType({ name: "photo.jpeg" })).toBe("image/jpeg");
    expect(attachmentType({ name: "page.html" })).toBeNull();
    expect(attachmentType({ name: "logo.svg" })).toBeNull();
  });
});
