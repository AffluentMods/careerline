// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from "@playwright/test";

const PORT = 8090;
const python = process.platform === "win32" ? "backend\\.venv\\Scripts\\python.exe" : "backend/.venv/bin/python";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  // Specs share one throwaway database: auth.spec must create the owner first.
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    // Uses an installed browser (Edge on Windows, else Playwright's Chromium).
    channel: process.env.PW_CHANNEL ?? (process.platform === "win32" ? "msedge" : undefined),
    trace: "retain-on-failure",
  },
  webServer: {
    // A separate, throwaway database that is wiped on every run.
    command: `${python} backend/scripts/run_local.py --rebuild --fresh --port ${PORT} --pgdata .localdata/pg-e2e`,
    cwd: "..",
    url: `http://localhost:${PORT}/api/healthz`,
    reuseExistingServer: false,
    // Lets vault.spec register a second (fictional) user after the owner exists.
    env: { CAREERLINE_ALLOW_REGISTRATION: "true" },
    timeout: 180_000,
  },
});
