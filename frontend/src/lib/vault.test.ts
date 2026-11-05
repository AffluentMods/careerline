// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { ApiError, fieldErrors } from "./api";
import {
  convertForm,
  entryDates,
  entryState,
  entryTitle,
  fromForm,
  parentOptions,
  toForm,
  uploadType,
  type Entry,
} from "./vault";

function entry(over: Partial<Entry>): Entry {
  return {
    id: "e1",
    kind: "role",
    parent_id: null,
    data: {},
    evidence_note: "",
    verified: true,
    verified_at: null,
    usable: true,
    source: "manual",
    import_id: null,
    created_at: "",
    updated_at: "",
    ...over,
  };
}

describe("form conversion", () => {
  it("round-trips role data and leaves out empty optional fields", () => {
    const data = { title: "Security Analyst", employer: "Northwind Tools", start: "2021-03", end: null };
    const values = toForm("role", data);
    expect(values.title).toBe("Security Analyst");
    expect(values.end).toBe("");
    expect(fromForm("role", values)).toEqual({
      title: "Security Analyst",
      employer: "Northwind Tools",
      start: "2021-03",
    });
  });

  it("trims whitespace", () => {
    expect(fromForm("skill", { name: "  Go  ", category: " " })).toEqual({ name: "Go" });
  });

  it("builds a metric only when it has a value", () => {
    expect(fromForm("achievement", { text: "Cut costs", "metric.value": "40", "metric.unit": "%" })).toEqual({
      text: "Cut costs",
      metric: { value: "40", unit: "%" },
    });
    expect(fromForm("achievement", { text: "Cut costs", "metric.unit": "%" })).toEqual({ text: "Cut costs" });
    expect(toForm("achievement", { text: "x", metric: { value: "3", unit: "x" } })["metric.value"]).toBe("3");
  });

  it("defaults required selects", () => {
    expect(toForm("certification", { name: "x" }).status).toBe("earned");
    expect(toForm("education", { institution: "x" }).status).toBe("completed");
  });

  it("carries the main text across a kind change", () => {
    expect(convertForm("achievement", "skill", { text: "Python" }).name).toBe("Python");
    expect(convertForm("role", "responsibility", { title: "Ran the help desk" }).text).toBe("Ran the help desk");
  });
});

describe("display helpers", () => {
  it("formats date ranges", () => {
    expect(entryDates(entry({ data: { start: "2021-03", end: null } }))).toBe("Mar 2021 – Present");
    expect(entryDates(entry({ data: { start: "2018", end: "2020-12" } }))).toBe("2018 – Dec 2020");
    expect(entryDates(entry({ kind: "education", data: { start: "2024", status: "in_progress" } }))).toBe(
      "2024 – In progress",
    );
    expect(entryDates(entry({ kind: "certification", data: { status: "in_progress", target_date: "2027" } }))).toBe(
      "Target 2027",
    );
    expect(entryDates(entry({ kind: "skill", data: { name: "Go" } }))).toBe("");
  });

  it("titles each kind", () => {
    const edu = entry({ kind: "education", data: { institution: "Example U", credential: "BS", field: "IS" } });
    expect(entryTitle(edu)).toBe("BS in IS");
    expect(entryTitle(entry({ kind: "achievement", data: { text: "Did it" } }))).toBe("Did it");
  });

  it("derives the review state", () => {
    expect(entryState({ verified: false, usable: false })).toBe("draft");
    expect(entryState({ verified: true, usable: false })).toBe("blocked");
    expect(entryState({ verified: true, usable: true })).toBe("verified");
  });

  it("offers only allowed parents and never the entry itself", () => {
    const all = [
      entry({ id: "r", kind: "role" }),
      entry({ id: "p", kind: "project" }),
      entry({ id: "s", kind: "skill" }),
    ];
    expect(parentOptions(all, "responsibility").map((e) => e.id)).toEqual(["r"]);
    expect(parentOptions(all, "achievement").map((e) => e.id)).toEqual(["r", "p"]);
    expect(parentOptions(all, "project", "p").map((e) => e.id)).toEqual(["r"]);
    expect(parentOptions(all, "skill")).toEqual([]);
  });
});

describe("uploads and errors", () => {
  it("maps file extensions to upload types", () => {
    expect(uploadType({ name: "CV.PDF", type: "" })).toBe("application/pdf");
    expect(uploadType({ name: "cv.docx", type: "" })).toContain("wordprocessingml");
    expect(uploadType({ name: "cv.doc", type: "application/msword" })).toBeNull();
  });

  it("maps server field errors to form field names", () => {
    const err = new ApiError(422, "invalid_request", "Some fields are invalid.", [
      { loc: ["body", "data", "start"], msg: "Value error, use YYYY or YYYY-MM" },
      { loc: ["body", "data", "metric", "value"], msg: "Field required" },
      { loc: ["body", "email"], msg: "Value error, not a valid email address" },
    ]);
    expect(fieldErrors(err)).toEqual({
      start: "use YYYY or YYYY-MM",
      "metric.value": "Field required",
      email: "not a valid email address",
    });
  });
});
