// SPDX-License-Identifier: Apache-2.0
/** Career Vault client: types, queries, and the per-kind field definitions forms use. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, apiUpload } from "./api";

// --- types (mirror backend/careerline/vault/schemas.py) ----------------------------------

export type Kind =
  | "role"
  | "responsibility"
  | "achievement"
  | "project"
  | "certification"
  | "education"
  | "skill"
  | "link";

export type EntryData = Record<string, unknown>;

export interface Entry {
  id: string;
  kind: Kind;
  parent_id: string | null;
  data: EntryData;
  evidence_note: string;
  verified: boolean;
  verified_at: string | null;
  usable: boolean;
  /** "api": added by an AI agent through an API key (always starts as a draft). */
  source: "manual" | "import" | "api";
  import_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface EntryInput {
  kind: Kind;
  parent_id: string | null;
  data: EntryData;
  evidence_note: string;
  verified: boolean;
}

export interface Profile {
  full_name: string;
  headline: string;
  location_text: string;
  summary: string;
  email: string;
  phone: string;
  address: string;
}

export interface Story {
  id: string;
  title: string;
  situation: string;
  task: string;
  action: string;
  result: string;
  reflection: string;
  tags: string[];
  entry_ids: string[];
  verified: boolean;
  verified_at: string | null;
  created_at: string;
  updated_at: string;
}

export type StoryInput = Omit<Story, "id" | "verified_at" | "created_at" | "updated_at">;

export interface KindCount {
  total: number;
  verified: number;
}

export interface VaultSummary {
  kinds: Record<Kind, KindCount>;
  stories: KindCount;
  needs_review: number;
}

export interface ImportSummary {
  id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  stats: { kinds?: Partial<Record<Kind, number>>; sections?: string[]; skipped?: number; total?: number };
  drafts_remaining: number;
  created_at: string;
}

export interface ImportDetail extends ImportSummary {
  text: string;
  suggestions: Partial<Profile>;
  entries: Entry[];
}

// --- kind metadata ------------------------------------------------------------------------

export type FieldType = "text" | "textarea" | "date" | "select" | "url";

export interface FieldDef {
  name: string;
  label: string;
  type: FieldType;
  required?: boolean;
  options?: { value: string; label: string }[];
  hint?: string;
  maxLength?: number;
}

const DATE_HINT = "YYYY or YYYY-MM";

export const KIND_LABEL: Record<Kind, string> = {
  role: "Role",
  responsibility: "Responsibility",
  achievement: "Achievement",
  project: "Project",
  certification: "Certification",
  education: "Education",
  skill: "Skill",
  link: "Link",
};

export const ALLOWED_PARENTS: Record<Kind, (Kind | null)[]> = {
  role: [null],
  responsibility: ["role"],
  achievement: ["role", "project", "education", null],
  project: ["role", null],
  certification: [null],
  education: [null],
  skill: [null],
  link: [null],
};

export const FIELDS: Record<Kind, FieldDef[]> = {
  role: [
    { name: "title", label: "Official job title", type: "text", required: true, maxLength: 200 },
    { name: "employer", label: "Employer", type: "text", maxLength: 200 },
    { name: "location", label: "Location", type: "text", maxLength: 200 },
    {
      name: "employment_type",
      label: "Type",
      type: "select",
      options: [
        { value: "full_time", label: "Full-time" },
        { value: "part_time", label: "Part-time" },
        { value: "contract", label: "Contract" },
        { value: "internship", label: "Internship" },
        { value: "temporary", label: "Temporary" },
        { value: "volunteer", label: "Volunteer" },
      ],
    },
    { name: "start", label: "Start", type: "date", hint: DATE_HINT },
    { name: "end", label: "End", type: "date", hint: "Leave empty if this is your current role" },
    { name: "summary", label: "Summary", type: "textarea", maxLength: 4000 },
  ],
  responsibility: [{ name: "text", label: "Responsibility", type: "textarea", required: true, maxLength: 1000 }],
  achievement: [
    { name: "text", label: "Achievement", type: "textarea", required: true, maxLength: 1000 },
    { name: "metric.value", label: "Metric value", type: "text", hint: "For example 40", maxLength: 50 },
    { name: "metric.unit", label: "Unit", type: "text", hint: "For example %", maxLength: 50 },
    { name: "metric.context", label: "Measured how", type: "text", maxLength: 200 },
  ],
  project: [
    { name: "name", label: "Project name", type: "text", required: true, maxLength: 200 },
    { name: "your_role", label: "Your role", type: "text", maxLength: 200 },
    { name: "start", label: "Start", type: "date", hint: DATE_HINT },
    { name: "end", label: "End", type: "date", hint: "Leave empty if ongoing" },
    { name: "url", label: "Link", type: "url", hint: "https:// only", maxLength: 500 },
    { name: "description", label: "Description", type: "textarea", maxLength: 4000 },
  ],
  certification: [
    { name: "name", label: "Certification", type: "text", required: true, maxLength: 200 },
    { name: "issuer", label: "Issued by", type: "text", maxLength: 200 },
    {
      name: "status",
      label: "Status",
      type: "select",
      required: true,
      options: [
        { value: "earned", label: "Earned" },
        { value: "in_progress", label: "In progress" },
        { value: "expired", label: "Expired" },
      ],
    },
    { name: "earned_on", label: "Earned", type: "date", hint: DATE_HINT },
    { name: "expires_on", label: "Expires", type: "date", hint: DATE_HINT },
    { name: "target_date", label: "Target date", type: "date", hint: "If in progress" },
    { name: "credential_id", label: "Credential ID", type: "text", maxLength: 100 },
    { name: "url", label: "Verification link", type: "url", hint: "https:// only", maxLength: 500 },
  ],
  education: [
    { name: "institution", label: "Institution", type: "text", required: true, maxLength: 200 },
    { name: "credential", label: "Degree or credential", type: "text", maxLength: 200 },
    { name: "field", label: "Field of study", type: "text", maxLength: 200 },
    {
      name: "status",
      label: "Status",
      type: "select",
      required: true,
      options: [
        { value: "completed", label: "Completed" },
        { value: "in_progress", label: "In progress" },
      ],
    },
    { name: "start", label: "Start", type: "date", hint: DATE_HINT },
    { name: "end", label: "End", type: "date", hint: DATE_HINT },
  ],
  skill: [
    { name: "name", label: "Skill", type: "text", required: true, maxLength: 100 },
    { name: "category", label: "Category", type: "text", hint: "For example Languages or Tools", maxLength: 100 },
    {
      name: "level",
      label: "Level",
      type: "select",
      options: [
        { value: "familiar", label: "Familiar" },
        { value: "proficient", label: "Proficient" },
        { value: "expert", label: "Expert" },
      ],
    },
  ],
  link: [
    { name: "label", label: "Label", type: "text", required: true, maxLength: 100 },
    { name: "url", label: "URL", type: "url", required: true, hint: "https:// only", maxLength: 500 },
  ],
};

// --- pure helpers (unit tested) -----------------------------------------------------

export type FormValues = Record<string, string>;

function readPath(data: EntryData, path: string): unknown {
  const [head, tail] = path.split(".", 2);
  const value = data[head!];
  if (tail === undefined) return value;
  return value && typeof value === "object" ? (value as Record<string, unknown>)[tail] : undefined;
}

/** Entry data -> string form values for the kind's fields. */
export function toForm(kind: Kind, data: EntryData): FormValues {
  const values: FormValues = {};
  for (const f of FIELDS[kind]) {
    const v = readPath(data, f.name);
    values[f.name] = v === null || v === undefined ? "" : String(v);
  }
  if (kind === "certification" && !values.status) values.status = "earned";
  if (kind === "education" && !values.status) values.status = "completed";
  return values;
}

/** Form values -> entry data. Empty optional fields are left out so server defaults apply. */
export function fromForm(kind: Kind, values: FormValues): EntryData {
  const data: EntryData = {};
  for (const f of FIELDS[kind]) {
    const v = (values[f.name] ?? "").trim();
    if (!v) continue;
    const [head, tail] = f.name.split(".", 2);
    if (tail === undefined) {
      data[head!] = v;
    } else {
      const nested = (data[head!] as Record<string, string> | undefined) ?? {};
      nested[tail] = v;
      data[head!] = nested;
    }
  }
  // A metric needs a value; drop a half-filled one.
  if (kind === "achievement" && data.metric && !(data.metric as Record<string, string>).value) {
    delete data.metric;
  }
  return data;
}

/** Carry the main text across when a draft's kind is changed during review. */
export function convertForm(from: Kind, to: Kind, values: FormValues): FormValues {
  const primary = (k: Kind) => FIELDS[k][0]!.name;
  const next = toForm(to, {});
  next[primary(to)] = values[primary(from)] ?? "";
  return next;
}

export function entryTitle(e: Pick<Entry, "kind" | "data">): string {
  const d = e.data;
  const s = (k: string) => (typeof d[k] === "string" ? (d[k] as string) : "");
  switch (e.kind) {
    case "role":
      return s("title") || "Untitled role";
    case "project":
    case "certification":
    case "skill":
      return s("name");
    case "education":
      return s("credential") ? `${s("credential")}${s("field") ? ` in ${s("field")}` : ""}` : s("institution");
    case "link":
      return s("label");
    default:
      return s("text");
  }
}

export function entrySubtitle(e: Pick<Entry, "kind" | "data">): string {
  const d = e.data;
  const s = (k: string) => (typeof d[k] === "string" ? (d[k] as string) : "");
  switch (e.kind) {
    case "role":
      return [s("employer"), s("location")].filter(Boolean).join(" · ");
    case "project":
      return s("your_role");
    case "certification":
      return s("issuer");
    case "education":
      return s("credential") ? s("institution") : "";
    case "skill":
      return s("category");
    case "link":
      return s("url");
    default:
      return "";
  }
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatPartialDate(value: unknown): string {
  if (typeof value !== "string" || !value) return "";
  const [y, m] = value.split("-");
  return m ? `${MONTHS[Number(m) - 1] ?? m} ${y}` : (y ?? "");
}

export function entryDates(e: Pick<Entry, "kind" | "data">): string {
  const d = e.data;
  if (e.kind === "certification") {
    if (d.status === "in_progress") return d.target_date ? `Target ${formatPartialDate(d.target_date)}` : "In progress";
    const earned = formatPartialDate(d.earned_on);
    const expires = formatPartialDate(d.expires_on);
    return [earned && `Earned ${earned}`, expires && `expires ${expires}`].filter(Boolean).join(", ");
  }
  if (!("start" in d) && !("end" in d)) return "";
  const start = formatPartialDate(d.start);
  const end = formatPartialDate(d.end);
  const ongoing = e.kind === "education" && d.status === "in_progress" ? "In progress" : "Present";
  if (!start && !end) return "";
  if (!end) return start ? `${start} – ${ongoing}` : "";
  return start ? `${start} – ${end}` : end;
}

export type EntryState = "verified" | "draft" | "blocked";

/** Verified but unusable means a parent is still a draft. */
export function entryState(e: Pick<Entry, "verified" | "usable">): EntryState {
  if (!e.verified) return "draft";
  return e.usable ? "verified" : "blocked";
}

export function childrenOf(entries: Entry[]): Map<string, Entry[]> {
  const map = new Map<string, Entry[]>();
  for (const e of entries) {
    if (!e.parent_id) continue;
    const list = map.get(e.parent_id) ?? [];
    list.push(e);
    map.set(e.parent_id, list);
  }
  return map;
}

export function parentOptions(entries: Entry[], kind: Kind, selfId?: string): Entry[] {
  const allowed = ALLOWED_PARENTS[kind].filter((k): k is Kind => k !== null);
  return entries.filter((e) => allowed.includes(e.kind) && e.id !== selfId);
}

// --- queries & mutations ------------------------------------------------------------------

export const vaultKeys = {
  all: ["vault"] as const,
  entries: ["vault", "entries"] as const,
  summary: ["vault", "summary"] as const,
  profile: ["vault", "profile"] as const,
  stories: ["vault", "stories"] as const,
  imports: ["vault", "imports"] as const,
  import: (id: string) => ["vault", "imports", id] as const,
};

export function useEntries() {
  return useQuery({ queryKey: vaultKeys.entries, queryFn: () => api<Entry[]>("/vault/entries") });
}

export function useSummary() {
  return useQuery({ queryKey: vaultKeys.summary, queryFn: () => api<VaultSummary>("/vault/summary") });
}

export function useProfile() {
  return useQuery({ queryKey: vaultKeys.profile, queryFn: () => api<Profile>("/vault/profile") });
}

export function useStories() {
  return useQuery({ queryKey: vaultKeys.stories, queryFn: () => api<Story[]>("/vault/stories") });
}

export function useImports() {
  return useQuery({ queryKey: vaultKeys.imports, queryFn: () => api<ImportSummary[]>("/vault/imports") });
}

export function useImport(id: string) {
  return useQuery({ queryKey: vaultKeys.import(id), queryFn: () => api<ImportDetail>(`/vault/imports/${id}`) });
}

/** Any vault change can affect counts, drafts, and import progress, so refresh it all. */
function useInvalidateVault() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: vaultKeys.all });
}

export function useVaultMutations() {
  const invalidate = useInvalidateVault();
  const onSuccess = () => invalidate();
  return {
    create: useMutation({
      mutationFn: (body: EntryInput) => api<Entry>("/vault/entries", { method: "POST", body }),
      onSuccess,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: Partial<EntryInput> }) =>
        api<Entry>(`/vault/entries/${id}`, { method: "PATCH", body }),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api<void>(`/vault/entries/${id}`, { method: "DELETE" }),
      onSuccess,
    }),
    verify: useMutation({
      mutationFn: (body: { ids: string[]; verified: boolean }) =>
        api<{ updated: number }>("/vault/entries/verify", { method: "POST", body }),
      onSuccess,
    }),
    saveProfile: useMutation({
      mutationFn: (body: Profile) => api<Profile>("/vault/profile", { method: "PUT", body }),
      onSuccess,
    }),
    createStory: useMutation({
      mutationFn: (body: StoryInput) => api<Story>("/vault/stories", { method: "POST", body }),
      onSuccess,
    }),
    updateStory: useMutation({
      mutationFn: ({ id, body }: { id: string; body: Partial<StoryInput> }) =>
        api<Story>(`/vault/stories/${id}`, { method: "PATCH", body }),
      onSuccess,
    }),
    removeStory: useMutation({
      mutationFn: (id: string) => api<void>(`/vault/stories/${id}`, { method: "DELETE" }),
      onSuccess,
    }),
    upload: useMutation({
      mutationFn: (file: File) => {
        const type = uploadType(file);
        if (!type) throw new ApiError(415, "unsupported_file", "Upload a PDF or Word (.docx) file.");
        return apiUpload<ImportDetail>("/vault/imports", file, type, file.name);
      },
      onSuccess,
    }),
    removeImport: useMutation({
      mutationFn: ({ id, discardDrafts }: { id: string; discardDrafts: boolean }) =>
        api<void>(`/vault/imports/${id}?discard_drafts=${discardDrafts ? "true" : "false"}`, { method: "DELETE" }),
      onSuccess,
    }),
  };
}

export const UPLOAD_TYPES: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

export function uploadType(file: Pick<File, "name" | "type">): string | null {
  const ext = file.name.toLowerCase().split(".").pop() ?? "";
  return UPLOAD_TYPES[ext] ?? null;
}
