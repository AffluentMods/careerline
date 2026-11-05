// SPDX-License-Identifier: Apache-2.0
/** Only plain web addresses become links. Anything else (javascript:, data:, odd schemes) doesn't. */
export function webHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}
