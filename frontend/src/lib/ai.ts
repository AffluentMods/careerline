// SPDX-License-Identifier: Apache-2.0
/** AI provider settings, usage, and on-demand scoring. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { ScoreDetail } from "./discovery";

export type AiProvider = "none" | "anthropic" | "openai" | "ollama";

export interface AiSettings {
  provider: AiProvider;
  base_url: string;
  has_api_key: boolean;
  model_scoring: string;
  model_writing: string;
  model_chat: string;
  redact_pii: boolean;
  auto_score: boolean;
  monthly_budget_cents: number | null;
  defaults: Record<string, { scoring: string; writing: string; chat: string }>;
  default_base_urls: Record<string, string>;
  private_endpoints_allowed: boolean;
  month_spend_micros: number;
}

export interface AiSettingsInput {
  provider: AiProvider;
  base_url: string;
  api_key?: string | null;
  model_scoring: string;
  model_writing: string;
  model_chat: string;
  redact_pii: boolean;
  auto_score: boolean;
  monthly_budget_cents: number | null;
}

export interface AiUsage {
  month_cost_micros: number;
  month_calls: number;
  unknown_cost_calls: number;
  by_feature: { feature: string; calls: number; input_tokens: number; output_tokens: number; cost_micros: number }[];
  recent: {
    feature: string;
    model: string;
    status: string;
    error: string;
    input_tokens: number;
    output_tokens: number;
    cost_micros: number | null;
    duration_ms: number;
    created_at: string;
  }[];
}

export const FEATURE_LABEL: Record<string, string> = {
  score: "Fit scoring",
  test: "Connection test",
  resume: "Tailored resumes",
  cover_letter: "Cover letters",
  questions: "Interview questions",
  mock_interview: "Mock interviews",
  assistant: "Assistant",
};

export function formatMicros(micros: number | null): string {
  if (micros === null) return "—";
  const dollars = micros / 1_000_000;
  if (dollars === 0) return "$0";
  return dollars < 0.01 ? "< $0.01" : `$${dollars.toFixed(2)}`;
}

export const aiKeys = {
  settings: ["ai", "settings"] as const,
  usage: ["ai", "usage"] as const,
};

export function useAiSettings() {
  return useQuery({ queryKey: aiKeys.settings, queryFn: () => api<AiSettings>("/ai/settings") });
}

export function useAiUsage() {
  return useQuery({ queryKey: aiKeys.usage, queryFn: () => api<AiUsage>("/ai/usage") });
}

export function useAiMutations() {
  const qc = useQueryClient();
  return {
    save: useMutation({
      mutationFn: (body: AiSettingsInput) => api<AiSettings>("/ai/settings", { method: "PUT", body }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["ai"] }),
    }),
    test: useMutation({
      mutationFn: () => api<{ ok: boolean; model: string; duration_ms: number }>("/ai/settings/test", { method: "POST" }),
      onSettled: () => qc.invalidateQueries({ queryKey: aiKeys.usage }),
    }),
    score: useMutation({
      mutationFn: ({ postingId, method }: { postingId: string; method: "ai" | "rules" }) =>
        api<ScoreDetail>(`/ai/score/${postingId}`, { method: "POST", body: { method } }),
      onSuccess: () => {
        void qc.invalidateQueries({ queryKey: ["discovery"] });
        void qc.invalidateQueries({ queryKey: aiKeys.usage });
      },
    }),
  };
}
