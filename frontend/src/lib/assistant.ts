// SPDX-License-Identifier: Apache-2.0
/** Assistant conversations and interview prep. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { localToday } from "./tracker";

export interface ChatItem {
  id: string;
  role: "user" | "assistant" | "action" | "tool";
  text: string;
  status?: "pending" | "confirmed" | "rejected" | "failed";
  result?: string;
  tool?: string;
}

export interface Conversation {
  id: string;
  title: string;
  /** "api": changes proposed through an API key. They're confirmed here but not chatted in. */
  source: "chat" | "api";
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatItem[];
  pending: number;
}

export interface Question {
  id: string;
  question: string;
  category: "behavioral" | "technical" | "situational" | "company";
  why: string;
  story_ids: string[];
  notes: string;
}

export interface Prep {
  id: string;
  posting_id: string | null;
  application_id: string | null;
  target_title: string;
  target_company: string;
  method: "rules" | "ai";
  questions: Question[];
  stories: Record<string, string>;
  created_at: string;
}

export interface MockTurn {
  role: "interviewer" | "candidate";
  text: string;
  feedback?: string;
  strengths?: string[];
  improvements?: string[];
  rating?: number;
  finished?: boolean;
}

export interface Mock {
  id: string;
  prep_id: string | null;
  target_title: string;
  target_company: string;
  max_questions: number;
  status: "active" | "finished";
  turns: MockTurn[];
  created_at: string;
}

export const SUGGESTIONS = [
  "What should I follow up on this week?",
  "Which of my saved jobs fit me best, and why?",
  "Draft a thank-you note for my most recent interview.",
  "Which of my applications have gone quiet for two weeks?",
];

export const assistantKeys = {
  conversations: ["assistant", "conversations"] as const,
  conversation: (id: string) => ["assistant", "conversation", id] as const,
  preps: (filter: string) => ["interview", "preps", filter] as const,
  prep: (id: string) => ["interview", "prep", id] as const,
  mocks: (prepId: string) => ["interview", "mocks", prepId] as const,
  mock: (id: string) => ["interview", "mock", id] as const,
};

export function useConversations() {
  return useQuery({ queryKey: assistantKeys.conversations, queryFn: () => api<Conversation[]>("/assistant/conversations") });
}

export function useConversation(id: string | undefined) {
  return useQuery({
    queryKey: assistantKeys.conversation(id ?? ""),
    queryFn: () => api<ConversationDetail>(`/assistant/conversations/${id}`),
    enabled: Boolean(id),
  });
}

export function useAssistantMutations() {
  const qc = useQueryClient();
  const store = (c: ConversationDetail) => {
    qc.setQueryData(assistantKeys.conversation(c.id), c);
    void qc.invalidateQueries({ queryKey: assistantKeys.conversations });
  };
  return {
    create: useMutation({
      mutationFn: () => api<ConversationDetail>("/assistant/conversations", { method: "POST" }),
      onSuccess: store,
    }),
    send: useMutation({
      mutationFn: ({ id, text }: { id: string; text: string }) =>
        api<ConversationDetail>(`/assistant/conversations/${id}/messages`, { method: "POST", body: { text, today: localToday() } }),
      onSuccess: (c) => {
        store(c);
        void qc.invalidateQueries({ queryKey: ["ai", "usage"] });
      },
    }),
    decide: useMutation({
      mutationFn: ({ actionId, confirm }: { actionId: string; confirm: boolean }) =>
        api<ConversationDetail>(`/assistant/actions/${actionId}`, { method: "POST", body: { confirm } }),
      onSuccess: (c) => {
        store(c);
        void qc.invalidateQueries({ queryKey: ["tracker"] });
        void qc.invalidateQueries({ queryKey: ["discovery"] });
      },
    }),
    remove: useMutation({
      mutationFn: (id: string) => api<void>(`/assistant/conversations/${id}`, { method: "DELETE" }),
      onSuccess: () => qc.invalidateQueries({ queryKey: assistantKeys.conversations }),
    }),
  };
}

export function usePreps(filter: { posting_id?: string; application_id?: string } = {}) {
  const q = new URLSearchParams(filter as Record<string, string>).toString();
  return useQuery({ queryKey: assistantKeys.preps(q), queryFn: () => api<Prep[]>(`/interview/preps${q ? `?${q}` : ""}`) });
}

export function usePrep(id: string) {
  return useQuery({ queryKey: assistantKeys.prep(id), queryFn: () => api<Prep>(`/interview/preps/${id}`) });
}

export function useMocks(prepId: string) {
  return useQuery({ queryKey: assistantKeys.mocks(prepId), queryFn: () => api<Mock[]>(`/interview/mocks?prep_id=${prepId}`) });
}

export function useMock(id: string) {
  return useQuery({ queryKey: assistantKeys.mock(id), queryFn: () => api<Mock>(`/interview/mocks/${id}`) });
}

export function useInterviewMutations() {
  const qc = useQueryClient();
  return {
    createPrep: useMutation({
      mutationFn: (body: { method: "rules" | "ai"; posting_id?: string; application_id?: string }) =>
        api<Prep>("/interview/preps", { method: "POST", body }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["interview"] }),
    }),
    patchQuestion: useMutation({
      mutationFn: ({ prepId, questionId, notes, story_ids }: { prepId: string; questionId: string; notes?: string; story_ids?: string[] }) =>
        api<Prep>(`/interview/preps/${prepId}/questions/${questionId}`, { method: "PATCH", body: { notes, story_ids } }),
      onSuccess: (p) => qc.setQueryData(assistantKeys.prep(p.id), p),
    }),
    deletePrep: useMutation({
      mutationFn: (id: string) => api<void>(`/interview/preps/${id}`, { method: "DELETE" }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["interview"] }),
    }),
    startMock: useMutation({
      mutationFn: (body: { prep_id: string; max_questions: number }) => api<Mock>("/interview/mocks", { method: "POST", body }),
      onSuccess: (m) => {
        qc.setQueryData(assistantKeys.mock(m.id), m);
        void qc.invalidateQueries({ queryKey: ["interview", "mocks"] });
      },
    }),
    answer: useMutation({
      mutationFn: ({ id, text }: { id: string; text: string }) =>
        api<Mock>(`/interview/mocks/${id}/answer`, { method: "POST", body: { text } }),
      onSuccess: (m) => qc.setQueryData(assistantKeys.mock(m.id), m),
    }),
  };
}

export const CATEGORY_LABEL: Record<Question["category"], string> = {
  behavioral: "Behavioral",
  technical: "Technical",
  situational: "Situational",
  company: "About the company",
};
