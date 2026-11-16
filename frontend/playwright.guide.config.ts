// SPDX-License-Identifier: Apache-2.0
/**
 * Captures the screenshots for the in-app guide (Settings → Guide) from the demo account.
 * Run: npm run guide:screenshots   (writes public/guide/*.jpg; all data is fictional)
 */
import { defineConfig } from "@playwright/test";

const PORT = 8093;
const python = process.platform === "win32" ? "backend\\.venv\\Scripts\\python.exe" : "backend/.venv/bin/python";

export default defineConfig({
  testDir: "./guide",
  timeout: 120_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    channel: process.env.PW_CHANNEL ?? (process.platform === "win32" ? "msedge" : undefined),
    viewport: { width: 1280, height: 860 },
    colorScheme: "light",
  },
  webServer: {
    command: `${python} backend/scripts/run_local.py --demo --rebuild --fresh --port ${PORT} --pgdata .localdata/pg-guide`,
    cwd: "..",
    url: `http://localhost:${PORT}/api/healthz`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
