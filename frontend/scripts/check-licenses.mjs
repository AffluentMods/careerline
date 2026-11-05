// SPDX-License-Identifier: Apache-2.0
// Fails if any installed npm package uses a license outside the allow-list.
// Usage: node scripts/check-licenses.mjs [--production]
import { readFileSync } from "node:fs";

const ALLOWED = new Set([
  "MIT", "MIT-0", "ISC", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "Apache-2.0", "MPL-2.0",
  "CC0-1.0", "BlueOak-1.0.0", "Unlicense", "Python-2.0", "CC-BY-4.0",
]);
const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url)));
const productionOnly = process.argv.includes("--production");

function ok(expr) {
  if (!expr) return false;
  const cleaned = expr.replace(/[()]/g, "");
  if (cleaned.includes(" OR ")) return cleaned.split(" OR ").some((p) => ok(p.trim()));
  if (cleaned.includes(" AND ")) return cleaned.split(" AND ").every((p) => ok(p.trim()));
  return ALLOWED.has(cleaned.trim());
}

const bad = [];
for (const [path, meta] of Object.entries(lock.packages)) {
  if (!path) continue;
  if (productionOnly && meta.dev) continue;
  const lic = typeof meta.license === "string" ? meta.license : meta.license?.type;
  if (!ok(lic)) bad.push(`${path.replace(/^.*node_modules\//, "")}@${meta.version}: ${lic ?? "UNKNOWN"}`);
}
if (bad.length) {
  console.error("Disallowed or unknown licenses:\n  " + bad.join("\n  "));
  process.exit(1);
}
console.log(`License check passed (${productionOnly ? "production" : "all"} packages).`);
