// SPDX-License-Identifier: Apache-2.0
/** API keys for AI agents (MCP). Mirrors backend/careerline/api_access/routes.py. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type Scope = "read" | "draft" | "propose" | "full";

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: Scope[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  expired: boolean;
  conversation_id: string | null;
  pending_proposals: number;
}

export interface CreatedApiKey extends ApiKey {
  /** Shown once; the server keeps only a hash. */
  token: string;
}

export interface NewKeyInput {
  name: string;
  scopes: Scope[];
  expires_in_days: 7 | 30 | 90 | 365 | null;
}

export const SCOPE_INFO: { scope: Scope; label: string; hint: string }[] = [
  {
    scope: "read",
    label: "Read",
    hint: "Your vault, jobs, applications, and agenda. Never contact details.",
  },
  {
    scope: "draft",
    label: "Add drafts",
    hint: "Add career facts and stories as unverified drafts, and add jobs by link or text.",
  },
  {
    scope: "propose",
    label: "Propose changes",
    hint: "Reminders, stage moves, notes, tracking jobs, profile edits. Each waits for your Confirm.",
  },
  {
    scope: "full",
    label: "Full access",
    hint:
      "Everything above and more, with no confirmations: facts it adds count as verified, and it can edit or " +
      "delete anything, manage job sources, write materials, and change AI and notification settings.",
  },
];

/** "Full access", or the list of what a narrower key may do. */
export function describeScopes(scopes: Scope[]): string {
  if (scopes.includes("full")) return "Full access";
  const labels = SCOPE_INFO.filter((s) => scopes.includes(s.scope)).map((s) => s.label.toLowerCase());
  return `Can ${labels.join(", ")}`;
}

export const EXPIRY_OPTIONS: { value: string; label: string }[] = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "never", label: "Never" },
];

export function parseExpiry(value: string): NewKeyInput["expires_in_days"] {
  return value === "never" ? null : (Number(value) as 7 | 30 | 90 | 365);
}

/** The MCP endpoint as this browser reaches the server. */
export function mcpUrl(origin: string = window.location.origin): string {
  return `${origin}/api/mcp`;
}

/** Ready-to-paste setup for common MCP clients. */
export function connectionSnippets(url: string, token: string): { label: string; text: string }[] {
  const header = `Authorization: Bearer ${token}`;
  const json = JSON.stringify(
    { mcpServers: { careerline: { type: "http", url, headers: { Authorization: `Bearer ${token}` } } } },
    null,
    2,
  );
  return [
    { label: "Claude Code", text: `claude mcp add --transport http careerline ${url} --header "${header}"` },
    { label: "Other MCP clients (JSON config)", text: json },
  ];
}

export const apiKeyKeys = { all: ["api-keys"] as const };

export function useApiKeys() {
  return useQuery({ queryKey: apiKeyKeys.all, queryFn: () => api<ApiKey[]>("/api-tokens") });
}

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NewKeyInput) => api<CreatedApiKey>("/api-tokens", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: apiKeyKeys.all }),
  });
}

export function useUpdateApiKeyScopes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, scopes }: { id: string; scopes: Scope[] }) =>
      api<ApiKey>(`/api-tokens/${id}`, { method: "PATCH", body: { scopes } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: apiKeyKeys.all }),
  });
}

export function useRevokeApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api-tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: apiKeyKeys.all }),
  });
}
