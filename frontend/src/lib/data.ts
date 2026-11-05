// SPDX-License-Identifier: Apache-2.0
/** Export, import, re-authentication and backups. Mirrors backend/careerline/data/routes.py. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiDownload, apiUpload, saveBlob } from "./api";

export interface ImportResult {
  imported: Record<string, number>;
  notes: string[];
  from_version: string | null;
}

export interface BackupStatus {
  enabled: boolean;
  directory: string;
  interval_hours: number;
  keep: number;
  key_source: string;
  files: { name: string; size: number; created_at: string }[];
  last_job_status: string | null;
  last_job_error: string;
  last_job_at: string | null;
}

export const IMPORT_LIMIT_MB = 300;

/** Plain names for the counts an import reports, in reading order. */
export const TABLE_LABELS: Record<string, string> = {
  vault_entries: "vault entries",
  stories: "stories",
  applications: "applications",
  application_events: "timeline events",
  contacts: "contacts",
  reminders: "reminders",
  offers: "offers",
  attachments: "files",
  job_postings: "job postings",
  job_sources: "job sources",
  documents: "resumes and letters",
  conversations: "assistant conversations",
  interview_preps: "interview preps",
};

export function importSummary(r: ImportResult): string {
  const parts = Object.entries(TABLE_LABELS)
    .map(([table, label]) => [r.imported[table] ?? 0, label] as const)
    .filter(([n]) => n > 0)
    .map(([n, label]) => `${n} ${label}`);
  return parts.length ? parts.join(", ") : "nothing (the export was empty)";
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export async function reauthWithPassword(password: string): Promise<void> {
  await api("/auth/reauth", { method: "POST", body: { password } });
}

/** Single-sign-on accounts confirm by signing in again at the provider. */
export async function reauthWithSso(next: string): Promise<void> {
  const { url } = await api<{ url: string }>("/auth/oidc/start", {
    method: "POST",
    body: { purpose: "reauth", next },
  });
  window.location.assign(url);
}

export async function downloadExport(): Promise<void> {
  const { blob, filename } = await apiDownload("/data/export");
  saveBlob(blob, filename);
}

export function useImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) =>
      apiUpload<ImportResult>(
        "/data/import",
        file,
        "application/zip",
        file.name,
        `That file is too large to import (the limit is ${IMPORT_LIMIT_MB} MB).`,
      ),
    // Everything may have changed.
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useBackups(enabled: boolean) {
  return useQuery({
    queryKey: ["data", "backups"],
    queryFn: () => api<BackupStatus>("/data/backups"),
    enabled,
    refetchInterval: (q) => (q.state.data?.last_job_status === "queued" || q.state.data?.last_job_status === "running" ? 3000 : false),
  });
}

export function useBackupNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ queued: boolean }>("/data/backups", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["data", "backups"] }),
  });
}
