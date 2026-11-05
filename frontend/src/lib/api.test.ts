// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, readCsrfToken } from "./api";

afterEach(() => vi.restoreAllMocks());

describe("readCsrfToken", () => {
  it("reads the __Host- cookie", () => {
    expect(readCsrfToken("a=1; __Host-cl_csrf=abc%3D; b=2")).toBe("abc=");
  });
  it("falls back to the plain cookie name", () => {
    expect(readCsrfToken("cl_csrf=xyz")).toBe("xyz");
  });
  it("ignores look-alike names", () => {
    expect(readCsrfToken("evil_cl_csrf=nope")).toBeUndefined();
  });
});

describe("api", () => {
  it("sends the CSRF header on POST but not GET", async () => {
    document.cookie = "cl_csrf=token123";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => new Response("{}", { status: 200 }));
    await api("/x");
    await api("/y", { method: "POST", body: { a: 1 } });
    const getHeaders = fetchMock.mock.calls[0]![1]!.headers as Record<string, string>;
    const postHeaders = fetchMock.mock.calls[1]![1]!.headers as Record<string, string>;
    expect(getHeaders["X-CSRF-Token"]).toBeUndefined();
    expect(postHeaders["X-CSRF-Token"]).toBe("token123");
    expect(postHeaders["Content-Type"]).toBe("application/json");
  });

  it("turns error bodies into ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "invalid_credentials", message: "Nope." } }), {
        status: 401,
      }),
    );
    await expect(api("/auth/login", { method: "POST", body: {} })).rejects.toMatchObject({
      status: 401,
      code: "invalid_credentials",
      message: "Nope.",
    });
  });

  it("reports network failures clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await api("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("network_error");
  });
});
