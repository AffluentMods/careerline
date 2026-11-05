// SPDX-License-Identifier: Apache-2.0
/** Search analytics from the tracker. Mirrors backend/waypoint/insights. */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { localToday } from "./tracker";

export interface FunnelStep {
  stage: "applied" | "screening" | "interviewing" | "offer" | "accepted";
  count: number;
  rate_from_previous: number | null;
  rate_from_applied: number | null;
}

export interface SourceRow {
  source: string;
  applied: number;
  responded: number;
  positive: number;
  offers: number;
  response_rate: number | null;
  median_days_to_response: number | null;
}

export interface Insights {
  days: number | null;
  today: string;
  totals: {
    tracked: number;
    applied: number;
    responded: number;
    positive: number;
    interviews: number;
    offers: number;
    rejected: number;
    withdrawn: number;
    awaiting: number;
    quiet: number;
  };
  response_rate: number | null;
  positive_rate: number | null;
  median_days_to_response: number | null;
  measured_responses: number;
  funnel: FunnelStep[];
  sources: SourceRow[];
  weekly: { week_start: string; applied: number }[];
  response_times: { bucket: string; count: number }[];
  quiet_days: number;
}

export const RANGES = [
  { value: "", label: "All time" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
  { value: "365", label: "Last 12 months" },
];

export function useInsights(days: string) {
  const q = new URLSearchParams({ today: localToday(), ...(days ? { days } : {}) }).toString();
  return useQuery({
    queryKey: ["insights", days],
    queryFn: () => api<Insights>(`/insights?${q}`),
    // Refetching keeps the previous render on screen (no layout jump).
    placeholderData: keepPreviousData,
  });
}

export function percent(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}

export function days(n: number | null): string {
  if (n === null) return "—";
  const rounded = Math.round(n * 10) / 10;
  return `${rounded} day${rounded === 1 ? "" : "s"}`;
}

/** "Oct 5" from "2026-10-05", without time-zone shifts. */
export function shortDate(iso: string): string {
  const [y = 1970, m = 1, d = 1] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** A clean axis maximum and ticks for small counts: 0..max in 1, 2, 5, 10... steps. */
export function niceTicks(max: number, target = 4): number[] {
  if (max <= 0) return [0, 1];
  const rough = max / target;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= rough) ?? 10 * pow;
  const top = Math.ceil(max / Math.max(step, 1)) * Math.max(step, 1);
  const out: number[] = [];
  for (let v = 0; v <= top; v += Math.max(step, 1)) out.push(v);
  return out;
}
