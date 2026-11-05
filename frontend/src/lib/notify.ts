// SPDX-License-Identifier: Apache-2.0
/** Notification settings. Mirrors backend/careerline/notify/routes.py. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type Channel = "email" | "ntfy" | "webhook";
export type WebhookKind = "discord" | "slack" | "generic";

export interface NotifySettings {
  timezone: string;
  digest_enabled: boolean;
  digest_hour: number;
  matches_enabled: boolean;
  match_min_score: number;
  email_enabled: boolean;
  email_to: string;
  email_available: boolean;
  ntfy_enabled: boolean;
  ntfy_url: string;
  has_ntfy_token: boolean;
  webhook_enabled: boolean;
  webhook_kind: WebhookKind;
  webhook_hint: string;
  private_targets_allowed: boolean;
  demo: boolean;
}

/** What the form sends. Secret fields: undefined keeps what's stored, "" removes it. */
export interface NotifyInput {
  timezone: string;
  digest_enabled: boolean;
  digest_hour: number;
  matches_enabled: boolean;
  match_min_score: number;
  email_enabled: boolean;
  email_to?: string;
  ntfy_enabled: boolean;
  ntfy_url?: string;
  ntfy_token?: string;
  webhook_enabled: boolean;
  webhook_kind: WebhookKind;
  webhook_url?: string;
}

export interface LogEntry {
  id: string;
  kind: "digest" | "matches" | "test";
  channel: Channel;
  ok: boolean;
  title: string;
  detail: string;
  created_at: string;
}

export const WEBHOOK_KINDS: { value: WebhookKind; label: string; hint: string }[] = [
  { value: "discord", label: "Discord", hint: "Server settings, Integrations, Webhooks, Copy webhook URL." },
  { value: "slack", label: "Slack", hint: "An Incoming Webhook URL from your Slack app." },
  { value: "generic", label: "Other (JSON)", hint: "Any URL that accepts a JSON POST." },
];

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function hourLabel(h: number): string {
  return new Date(2000, 0, 1, h).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** Build the request: only send secret fields the user actually changed. */
export function toInput(
  form: Omit<NotifyInput, "email_to" | "ntfy_url" | "ntfy_token" | "webhook_url">,
  edits: { email_to?: string; ntfy_url?: string; ntfy_token?: string; webhook_url?: string },
): NotifyInput {
  const out: NotifyInput = { ...form };
  for (const key of ["email_to", "ntfy_url", "ntfy_token", "webhook_url"] as const) {
    const v = edits[key];
    if (v !== undefined) out[key] = v.trim();
  }
  return out;
}

export const notifyKeys = { settings: ["notify", "settings"] as const, log: ["notify", "log"] as const };

export function useNotifySettings() {
  return useQuery({ queryKey: notifyKeys.settings, queryFn: () => api<NotifySettings>("/notifications/settings") });
}

export function useNotifyLog() {
  return useQuery({ queryKey: notifyKeys.log, queryFn: () => api<LogEntry[]>("/notifications/log") });
}

export function useNotifyMutations() {
  const qc = useQueryClient();
  return {
    save: useMutation({
      mutationFn: (body: NotifyInput) => api<NotifySettings>("/notifications/settings", { method: "PUT", body }),
      onSuccess: (s) => qc.setQueryData(notifyKeys.settings, s),
    }),
    test: useMutation({
      mutationFn: (channel: Channel) =>
        api<{ ok: boolean; error: string | null }>("/notifications/test", { method: "POST", body: { channel } }),
      onSettled: () => qc.invalidateQueries({ queryKey: notifyKeys.log }),
    }),
  };
}
