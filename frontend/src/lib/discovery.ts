// SPDX-License-Identifier: Apache-2.0
/** Discovery client: job sources, postings, filters. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export interface FieldSpec {
  name: string;
  label: string;
  required?: boolean;
  hint?: string;
  type?: "text" | "number" | "checkbox" | "select";
  options?: string[];
  secret?: boolean;
}

export interface Adapter {
  kind: string;
  label: string;
  description: string;
  terms_url: string;
  config_fields: FieldSpec[];
  secret_fields: FieldSpec[];
}

export interface Source {
  id: string;
  kind: string;
  label: string;
  name: string;
  enabled: boolean;
  interval_hours: number;
  config: Record<string, unknown>;
  has_secret: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: "never" | "ok" | "error";
  last_error: string;
  last_found: number;
  last_new: number;
  pending: boolean;
}

export interface FilterResult {
  passed: boolean;
  reasons: string[];
  preferred: boolean;
  pay_vs_target: "above" | "within" | "below" | null;
  distance_miles: number | null;
}

export interface Score {
  score: number;
  method: "rules" | "ai";
  summary: string;
  stale: boolean;
}

export interface Posting {
  id: string;
  title: string;
  company: string;
  location: string;
  work_mode: "onsite" | "hybrid" | "remote" | null;
  pay_min_cents: number | null;
  pay_max_cents: number | null;
  pay_period: "hour" | "year" | null;
  currency: string;
  shifts: string[];
  clearance: string | null;
  employment_type: string;
  url: string;
  posted_at: string | null;
  closes_on: string | null;
  first_seen_at: string;
  last_seen_at: string;
  status: "new" | "saved" | "dismissed" | "tracked";
  application_id: string | null;
  sources: string[];
  filter: FilterResult;
  score: Score | null;
}

export interface ScoreItem {
  requirement: string;
  evidence: string[];
}

export interface ScoreDetail {
  score: number;
  method: "rules" | "ai";
  model: string;
  summary: string;
  matched: ScoreItem[];
  gaps: string[];
  red_flags: string[];
  stale: boolean;
  scored_at: string;
}

export interface PostingDetail extends Posting {
  description: string;
  sightings: { source_label: string; url: string; seen_at: string }[];
  score_detail: ScoreDetail | null;
}

export type View = "matches" | "filtered" | "saved" | "dismissed" | "all";

export interface PostingList {
  items: Posting[];
  counts: Record<View, number>;
}

export interface Filters {
  locations: string[];
  home_lat: number | null;
  home_lon: number | null;
  radius_miles: number | null;
  remote_ok: boolean;
  hybrid_ok: boolean;
  onsite_ok: boolean;
  min_hourly_cents: number | null;
  target_hourly_cents: number | null;
  hide_unpaid_postings: boolean;
  shifts_wanted: string[];
  shifts_excluded: string[];
  exclude_keywords: string[];
  require_keywords: string[];
  clearance_held: "none" | "public_trust" | "secret" | "top_secret";
  preferred_companies: string[];
  blocked_companies: string[];
}

export const SHIFT_OPTIONS: { value: string; label: string }[] = [
  { value: "day", label: "Day" },
  { value: "early", label: "Early" },
  { value: "evening", label: "Evening / swing" },
  { value: "night", label: "Night / overnight" },
  { value: "weekend", label: "Weekends" },
  { value: "rotating", label: "Rotating" },
  { value: "on_call", label: "On-call" },
];

export const CLEARANCE_LABEL: Record<string, string> = {
  none: "None",
  public_trust: "Public Trust",
  secret: "Secret",
  top_secret: "Top Secret",
};

export const MODE_LABEL: Record<string, string> = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" };

/** "a, b ,, c" -> ["a", "b", "c"] (deduplicated, case-insensitive). */
export function splitList(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(/[,\n]/)) {
    const t = part.trim();
    if (t && !seen.has(t.toLowerCase())) {
      seen.add(t.toLowerCase());
      out.push(t);
    }
  }
  return out;
}

export function scoreTone(score: number): "high" | "mid" | "low" {
  return score >= 75 ? "high" : score >= 50 ? "mid" : "low";
}

export const discoveryKeys = {
  all: ["discovery"] as const,
  adapters: ["discovery", "adapters"] as const,
  sources: ["discovery", "sources"] as const,
  postings: (view: View) => ["discovery", "postings", view] as const,
  posting: (id: string) => ["discovery", "posting", id] as const,
  filters: ["discovery", "filters"] as const,
};

export function useAdapters() {
  return useQuery({ queryKey: discoveryKeys.adapters, queryFn: () => api<Adapter[]>("/discovery/adapters"), staleTime: 3_600_000 });
}

export function useSources() {
  return useQuery({
    queryKey: discoveryKeys.sources,
    queryFn: () => api<Source[]>("/discovery/sources"),
    // Poll while a fetch is queued or running.
    refetchInterval: (q) => (q.state.data?.some((s) => s.pending) ? 3000 : false),
  });
}

export function usePostings(view: View) {
  return useQuery({ queryKey: discoveryKeys.postings(view), queryFn: () => api<PostingList>(`/discovery/postings?view=${view}`) });
}

export function usePosting(id: string) {
  return useQuery({ queryKey: discoveryKeys.posting(id), queryFn: () => api<PostingDetail>(`/discovery/postings/${id}`) });
}

export function useFilters() {
  return useQuery({ queryKey: discoveryKeys.filters, queryFn: () => api<Filters>("/discovery/filters") });
}

export function useDiscoveryMutations() {
  const qc = useQueryClient();
  const onSuccess = () => {
    void qc.invalidateQueries({ queryKey: discoveryKeys.all });
    void qc.invalidateQueries({ queryKey: ["tracker"] });
  };
  return {
    createSource: useMutation({
      mutationFn: (body: Partial<Source> & { kind: string; name: string; secret?: Record<string, unknown> | null }) =>
        api<Source>("/discovery/sources", { method: "POST", body }),
      onSuccess,
    }),
    updateSource: useMutation({
      mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
        api<Source>(`/discovery/sources/${id}`, { method: "PATCH", body }),
      onSuccess,
    }),
    deleteSource: useMutation({
      mutationFn: (id: string) => api<void>(`/discovery/sources/${id}`, { method: "DELETE" }),
      onSuccess,
    }),
    runSource: useMutation({
      mutationFn: (id: string) => api<Source>(`/discovery/sources/${id}/run`, { method: "POST" }),
      onSuccess,
    }),
    setStatus: useMutation({
      mutationFn: ({ id, status }: { id: string; status: "new" | "saved" | "dismissed" }) =>
        api<PostingDetail>(`/discovery/postings/${id}`, { method: "PATCH", body: { status } }),
      onSuccess,
    }),
    track: useMutation({
      mutationFn: (id: string) => api<PostingDetail>(`/discovery/postings/${id}/track`, { method: "POST" }),
      onSuccess,
    }),
    deletePosting: useMutation({
      mutationFn: (id: string) => api<void>(`/discovery/postings/${id}`, { method: "DELETE" }),
      onSuccess,
    }),
    importPosting: useMutation({
      mutationFn: (body: { url?: string; text?: string; title?: string; company?: string; location?: string }) =>
        api<PostingDetail>("/discovery/import", { method: "POST", body }),
      onSuccess,
    }),
    saveFilters: useMutation({
      mutationFn: (body: Filters) => api<Filters>("/discovery/filters", { method: "PUT", body }),
      onSuccess,
    }),
  };
}
