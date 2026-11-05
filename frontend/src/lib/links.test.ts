// SPDX-License-Identifier: Apache-2.0
import { expect, it } from "vitest";
import { webHref } from "./links";

it("only lets web addresses become links", () => {
  expect(webHref("https://jobs.example.com/1")).toBe("https://jobs.example.com/1");
  expect(webHref("http://example.com")).toBe("http://example.com/");
  for (const bad of ["javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "vbscript:x", "//evil.example", "", null, undefined]) {
    expect(webHref(bad)).toBeUndefined();
  }
});
