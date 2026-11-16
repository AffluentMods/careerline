// SPDX-License-Identifier: Apache-2.0
/** Follow-ups for quiet applications. Mirrors backend/careerline/tracker/followups.py. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export interface FollowUpItem {
  application_id: string;
  title: string;
  company: string;
  stage: string;
  last_activity: string;
  days_quiet: number;
  follow_ups_sent: number;
  contact_name: string;
}

export interface FollowUpDraft {
  subject: string;
  body: string;
  to: string;
  number: number;
  method: "rules" | "ai";
  warnings: string[];
}

export const followUpKeys = { list: (days: number) => ["tracker", "follow-ups", days] as const };

export function useFollowUps(days = 10) {
  return useQuery({
    queryKey: followUpKeys.list(days),
    queryFn: () => api<FollowUpItem[]>(`/tracker/follow-ups?days=${days}`),
  });
}

export function useFollowUpMutations() {
  const qc = useQueryClient();
  return {
    draft: useMutation({
      mutationFn: ({ id, method }: { id: string; method: "rules" | "ai" }) =>
        api<FollowUpDraft>(`/tracker/applications/${id}/follow-up/draft`, { method: "POST", body: { method } }),
    }),
    sent: useMutation({
      mutationFn: ({ id, subject, body }: { id: string; subject: string; body: string }) =>
        api<{ follow_ups_sent: number }>(`/tracker/applications/${id}/follow-up/sent`, {
          method: "POST",
          body: { subject, body },
        }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["tracker"] }),
    }),
  };
}

/** A mailto: link that opens the draft in the user's own email app. */
export function mailtoLink(d: FollowUpDraft): string {
  const params = new URLSearchParams({ subject: d.subject, body: d.body });
  return `mailto:${encodeURIComponent(d.to)}?${params.toString().replace(/\+/g, "%20")}`;
}
