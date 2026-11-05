// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { scoreTone, splitList, type Posting } from "../../lib/discovery";
import { postingFacts } from "./JobsPage";
import { toPayload } from "./SourcesTab";

function posting(over: Partial<Posting>): Posting {
  return {
    id: "p",
    title: "Security Engineer",
    company: "Northwind Tools",
    location: "Springfield, ST",
    work_mode: "hybrid",
    pay_min_cents: 5000,
    pay_max_cents: 6500,
    pay_period: "hour",
    currency: "USD",
    shifts: ["day"],
    clearance: "secret",
    employment_type: "",
    url: "",
    posted_at: null,
    closes_on: null,
    first_seen_at: "",
    last_seen_at: "",
    status: "new",
    application_id: null,
    sources: [],
    filter: { passed: true, reasons: [], preferred: false, pay_vs_target: null, distance_miles: null },
    score: null,
    ...over,
  };
}

describe("discovery helpers", () => {
  it("splits comma lists without duplicates", () => {
    expect(splitList(' Tier 1 SOC, tier 1 soc ,, "commission only"\nSeattle')).toEqual([
      "Tier 1 SOC",
      '"commission only"',
      "Seattle",
    ]);
  });

  it("builds source payloads from form values", () => {
    const fields = [
      { name: "what", label: "Keywords", required: true },
      { name: "distance_km", label: "Distance", type: "number" as const },
      { name: "remote_only", label: "Remote", type: "checkbox" as const },
      { name: "where", label: "Where" },
    ];
    expect(toPayload(fields, { what: " security ", distance_km: "25", remote_only: true, where: "" })).toEqual({
      what: "security",
      distance_km: 25,
      remote_only: true,
    });
  });

  it("describes postings", () => {
    const facts = postingFacts(posting({}));
    expect(facts[0]).toBe("Northwind Tools");
    expect(facts).toContain("Hybrid");
    expect(facts).toContain("Day shift");
    expect(facts).toContain("Secret clearance");
    expect(facts.some((f) => /\/hr$/.test(f))).toBe(true);
    expect(postingFacts(posting({ company: "", work_mode: null, shifts: [], clearance: null, pay_min_cents: null, pay_max_cents: null }))).toEqual([
      "Springfield, ST",
    ]);
  });

  it("colors scores by band", () => {
    expect(scoreTone(80)).toBe("high");
    expect(scoreTone(60)).toBe("mid");
    expect(scoreTone(20)).toBe("low");
  });
});
