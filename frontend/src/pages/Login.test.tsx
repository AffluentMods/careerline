// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { LoginPage } from "./Login";

afterEach(() => vi.restoreAllMocks());

const STATUS = {
  has_users: true,
  registration_open: false,
  password_login: true,
  sso: { enabled: false, label: "single sign-on" },
  demo: false,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function renderLogin() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/login/verify" element={<p>verify page</p>} />
          <Route path="/" element={<p>home page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("shows the server's error message on bad credentials", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith("/auth/status")) return json({ ...STATUS, session: "none" });
    return json({ error: { code: "invalid_credentials", message: "Email or password is incorrect." } }, 401);
  });
  renderLogin();
  await userEvent.type(await screen.findByLabelText("Email"), "a@example.com");
  await userEvent.type(screen.getByLabelText("Password"), "wrong password!!");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
});

it("goes to the verification step when MFA is required", async () => {
  let session = "none";
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith("/auth/status")) return json({ ...STATUS, session });
    session = "mfa_pending";
    return json({ status: "mfa_required", user: null });
  });
  renderLogin();
  await userEvent.type(await screen.findByLabelText("Email"), "a@example.com");
  await userEvent.type(screen.getByLabelText("Password"), "right password!!");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("verify page")).toBeInTheDocument();
});

it("offers single sign-on and explains a failed attempt", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    json({ ...STATUS, session: "none", password_login: false, sso: { enabled: true, label: "Authelia" } }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/login?sso_error=no_account"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const link = await screen.findByRole("link", { name: "Sign in with Authelia" });
  expect(link).toHaveAttribute("href", "/api/v1/auth/oidc/start?next=%2F");
  expect(screen.getByRole("alert")).toHaveTextContent("No Waypoint account is linked");
  expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
});

it("shows unknown sign-on error codes generically, never verbatim", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    json({ ...STATUS, session: "none", sso: { enabled: true, label: "SSO" } }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/login?sso_error=%3Cb%3Ephish%3C%2Fb%3E"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Single sign-on didn't work. Try again.");
  expect(screen.queryByText(/phish/)).not.toBeInTheDocument();
});
