// SPDX-License-Identifier: Apache-2.0
/** Tailored materials: generated resumes and cover letters with fact-check status. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type ItemStatus = "linked" | "flagged" | "approved";

export interface DocItem {
  id: string;
  text: string;
  fact_ids: string[];
  status: ItemStatus;
  reason: string;
}

export interface ResumeEntry {
  fact_id: string;
  title?: string;
  employer?: string;
  location?: string;
  name?: string;
  role?: string;
  url?: string;
  dates: string;
  bullets: DocItem[];
}

export interface ResumeContent {
  summary: DocItem | null;
  experience: ResumeEntry[];
  projects: ResumeEntry[];
  education: { fact_id: string; text: string }[];
  certifications: { fact_id: string; text: string }[];
  skills: { category: string; names: string[]; fact_ids: string[] }[];
}

export interface LetterContent {
  company: string;
  role: string;
  paragraphs: DocItem[];
}

export interface DocSummary {
  id: string;
  kind: "resume" | "cover_letter";
  title: string;
  is_master: boolean;
  posting_id: string | null;
  application_id: string | null;
  target_title: string;
  target_company: string;
  method: "rules" | "ai";
  model: string;
  flagged: number;
  stale: boolean;
  created_at: string;
  updated_at: string;
  exported_at: string | null;
}

export interface DocDetail extends DocSummary {
  content: ResumeContent | LetterContent;
  sources: Record<string, string>;
}

export interface DiffOp {
  op: "same" | "added" | "removed";
  text: string;
}

export interface GenerateInput {
  kind: "resume" | "cover_letter";
  method: "rules" | "ai";
  posting_id?: string;
  application_id?: string;
}

export function isResume(d: DocDetail): d is DocDetail & { content: ResumeContent } {
  return d.kind === "resume";
}

export function docItems(d: DocDetail): DocItem[] {
  if (!isResume(d)) return (d.content as LetterContent).paragraphs;
  const c = d.content;
  return [...(c.summary ? [c.summary] : []), ...c.experience.flatMap((e) => e.bullets), ...c.projects.flatMap((p) => p.bullets)];
}

export const materialsKeys = {
  all: ["materials"] as const,
  list: (filter: string) => ["materials", "list", filter] as const,
  doc: (id: string) => ["materials", "doc", id] as const,
  diff: (id: string) => ["materials", "diff", id] as const,
};

export function useDocuments(filter: { posting_id?: string; application_id?: string } = {}) {
  const q = new URLSearchParams(filter as Record<string, string>).toString();
  return useQuery({
    queryKey: materialsKeys.list(q),
    queryFn: () => api<DocSummary[]>(`/materials/documents${q ? `?${q}` : ""}`),
  });
}

export function useDocument(id: string) {
  return useQuery({ queryKey: materialsKeys.doc(id), queryFn: () => api<DocDetail>(`/materials/documents/${id}`) });
}

export function useDiff(id: string, enabled: boolean) {
  return useQuery({
    queryKey: materialsKeys.diff(id),
    queryFn: () => api<{ ops: DiffOp[]; added: number; removed: number }>(`/materials/documents/${id}/diff`),
    enabled,
  });
}

export function exportUrl(id: string, format: "pdf" | "docx"): string {
  return `/api/v1/materials/documents/${id}/export?format=${format}`;
}

export function useMaterialsMutations() {
  const qc = useQueryClient();
  const onSuccess = () => qc.invalidateQueries({ queryKey: materialsKeys.all });
  return {
    generate: useMutation({
      mutationFn: (body: GenerateInput) => api<DocDetail>("/materials/documents", { method: "POST", body }),
      onSuccess: () => {
        void onSuccess();
        void qc.invalidateQueries({ queryKey: ["ai", "usage"] });
      },
    }),
    patchItem: useMutation({
      mutationFn: ({ docId, itemId, action, text }: { docId: string; itemId: string; action: "approve" | "edit" | "remove"; text?: string }) =>
        api<DocDetail>(`/materials/documents/${docId}/items/${itemId}`, { method: "PATCH", body: { action, text: text ?? "" } }),
      onSuccess: (doc) => {
        qc.setQueryData(materialsKeys.doc(doc.id), doc);
        void qc.invalidateQueries({ queryKey: ["materials", "list"] });
        void qc.invalidateQueries({ queryKey: materialsKeys.diff(doc.id) });
      },
    }),
    rename: useMutation({
      mutationFn: ({ id, title }: { id: string; title: string }) =>
        api<DocDetail>(`/materials/documents/${id}`, { method: "PATCH", body: { title } }),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api<void>(`/materials/documents/${id}`, { method: "DELETE" }),
      onSuccess,
    }),
    attach: useMutation({
      mutationFn: ({ id, format, application_id }: { id: string; format: "pdf" | "docx"; application_id?: string }) =>
        api<{ attachment_id: string; application_id: string; filename: string }>(`/materials/documents/${id}/attach`, {
          method: "POST",
          body: { format, ...(application_id ? { application_id } : {}) },
        }),
      onSuccess: () => {
        void onSuccess();
        void qc.invalidateQueries({ queryKey: ["tracker"] });
      },
    }),
  };
}
