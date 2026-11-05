// SPDX-License-Identifier: Apache-2.0
/** Tracker client: types, queries, and pure helpers (money, dates, board, agenda). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, apiUpload } from "./api";

// --- types (mirror backend/careerline/tracker/schemas.py) ----------------------------------

export const STAGES = [
  "saved",
  "applied",
  "screening",
  "interviewing",
  "offer",
  "accepted",
  "rejected",
  "withdrawn",
] as const;
export type Stage = (typeof STAGES)[number];
export const OPEN_STAGES: Stage[] = ["saved", "applied", "screening", "interviewing", "offer"];
export const CLOSED_STAGES: Stage[] = ["accepted", "rejected", "withdrawn"];

export const STAGE_LABEL: Record<Stage, string> = {
  saved: "Saved",
  applied: "Applied",
  screening: "Screening",
  interviewing: "Interviewing",
  offer: "Offer",
  accepted: "Accepted",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export type PayPeriod = "hour" | "year";
export type WorkMode = "onsite" | "hybrid" | "remote";
export type Priority = "low" | "normal" | "high";
export type EventKind = "stage_change" | "note" | "sent" | "interview" | "contact" | "other";
export type ContactRole = "recruiter" | "hiring_manager" | "interviewer" | "referrer" | "other";
export type ReferralStatus = "to_ask" | "asked" | "agreed" | "submitted" | "declined";
export type ReminderKind = "follow_up" | "deadline" | "interview" | "task";

export interface ApplicationFields {
  title: string;
  company: string;
  location: string;
  work_mode: WorkMode | null;
  job_url: string;
  source: string;
  pay_min_cents: number | null;
  pay_max_cents: number | null;
  pay_period: PayPeriod | null;
  currency: string;
  description: string;
  applied_on: string | null;
  deadline: string | null;
  priority: Priority;
}

export interface Application extends ApplicationFields {
  id: string;
  stage: Stage;
  stage_changed_at: string;
  created_at: string;
  updated_at: string;
}

export interface DueItem {
  due_on: string;
  due_time: string | null;
  title: string;
  source: string;
}

export interface ApplicationSummary {
  id: string;
  title: string;
  company: string;
  location: string;
  work_mode: WorkMode | null;
  pay_min_cents: number | null;
  pay_max_cents: number | null;
  pay_period: PayPeriod | null;
  currency: string;
  stage: Stage;
  stage_changed_at: string;
  applied_on: string | null;
  deadline: string | null;
  priority: Priority;
  updated_at: string;
  last_activity_at: string;
  next_due: DueItem | null;
  contact_count: number;
  has_offer: boolean;
}

export interface Attachment {
  id: string;
  application_id: string;
  event_id: string | null;
  filename: string;
  mime: string;
  size_bytes: number;
  created_at: string;
}

export interface EventData {
  materials?: string;
  interview_format?: "phone" | "video" | "onsite" | "other";
  with_whom?: string;
  channel?: "email" | "phone" | "message" | "in_person" | "other";
  from?: Stage | null;
  to?: Stage;
}

export interface TimelineEvent {
  id: string;
  application_id: string;
  kind: EventKind;
  occurred_at: string;
  title: string;
  body: string;
  data: EventData;
  attachments: Attachment[];
}

export interface ContactLink {
  application_id: string;
  role: ContactRole;
  title: string;
  company: string;
  stage: Stage;
}

export interface Contact {
  id: string;
  name: string;
  company: string;
  title: string;
  email: string;
  phone: string;
  linkedin_url: string;
  notes: string;
  links: ContactLink[];
}

export type ContactInput = Omit<Contact, "id" | "links">;

export interface Referral {
  id: string;
  application_id: string;
  contact_id: string;
  contact_name: string;
  status: ReferralStatus;
  asked_on: string | null;
  referred_on: string | null;
  follow_up_on: string | null;
  notes: string;
}

export interface Reminder {
  id: string;
  title: string;
  kind: ReminderKind;
  due_on: string;
  due_time: string | null;
  application_id: string | null;
  contact_id: string | null;
  notes: string;
  done_at: string | null;
}

export interface ReminderInput {
  title: string;
  kind: ReminderKind;
  due_on: string;
  due_time: string | null;
  application_id: string | null;
  contact_id?: string | null;
  notes?: string;
}

export interface AgendaItem {
  source: "reminder" | "application_deadline" | "offer_deadline" | "referral_follow_up";
  id: string;
  title: string;
  kind: string;
  due_on: string;
  due_time: string | null;
  application_id: string | null;
  application_title: string;
  company: string;
  contact_id: string | null;
  contact_name: string;
}

export interface OfferInput {
  currency: string;
  base_cents: number;
  base_period: PayPeriod;
  hours_per_week: number;
  bonus_cents: number | null;
  bonus_notes: string;
  equity_notes: string;
  pto_days: number | null;
  schedule: string;
  commute_minutes: number | null;
  growth_notes: string;
  benefits_notes: string;
  deadline: string | null;
}

export interface Offer extends OfferInput {
  id: string;
  application_id: string;
  application_title: string;
  company: string;
  stage: Stage;
  annual_cents: number;
  hourly_cents: number;
  total_annual_cents: number;
}

export interface ApplicationDetail {
  application: Application;
  events: TimelineEvent[];
  attachments: Attachment[];
  contacts: { contact: Contact; role: ContactRole }[];
  referrals: Referral[];
  reminders: Reminder[];
  offer: Offer | null;
}

export interface TrackerSummary {
  stages: Record<Stage, number>;
  open_total: number;
  overdue: number;
  due_today: number;
}

// --- dates ---------------------------------------------------------------------------------

/** Today's date in the browser's time zone, as YYYY-MM-DD. */
export function localToday(now: Date = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function addDays(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return localToday(new Date(y!, m! - 1, d! + days));
}

export function daysBetween(fromIso: string, toIso: string): number {
  const [y1, m1, d1] = fromIso.split("-").map(Number);
  const [y2, m2, d2] = toIso.split("-").map(Number);
  return Math.round((Date.UTC(y2!, m2! - 1, d2!) - Date.UTC(y1!, m1! - 1, d1!)) / 86_400_000);
}

export function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "";
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function formatDue(item: Pick<DueItem, "due_on" | "due_time">, today: string): string {
  const diff = daysBetween(today, item.due_on);
  const time = item.due_time ? ` ${item.due_time.slice(0, 5)}` : "";
  if (diff === 0) return `Today${time}`;
  if (diff === 1) return `Tomorrow${time}`;
  if (diff === -1) return `Yesterday${time}`;
  if (diff < 0) return `${-diff} days overdue`;
  if (diff < 7) return `In ${diff} days${time}`;
  return formatDate(item.due_on);
}

export type AgendaGroup = "overdue" | "today" | "week" | "later";

export function agendaGroup(dueOn: string, today: string): AgendaGroup {
  const diff = daysBetween(today, dueOn);
  if (diff < 0) return "overdue";
  if (diff === 0) return "today";
  if (diff <= 7) return "week";
  return "later";
}

// --- money ---------------------------------------------------------------------------------

/** "52.50", "$1,200", "110000" -> cents. Blank -> null. Invalid -> NaN. */
export function parseMoney(input: string): number | null {
  const cleaned = input.replace(/[\s,$€£]/g, "");
  if (!cleaned) return null;
  if (!/^\d+(\.\d{0,2})?$/.test(cleaned)) return Number.NaN;
  const [whole, frac = ""] = cleaned.split(".");
  return Number(whole) * 100 + Number(frac.padEnd(2, "0"));
}

export function centsToInput(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "";
  return cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2);
}

export function formatMoney(cents: number, currency = "USD", compact = false): string {
  const value = cents / 100;
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      notation: compact && value >= 10_000 ? "compact" : "standard",
      maximumFractionDigits: compact || cents % 100 === 0 ? 0 : 2,
      minimumFractionDigits: compact || cents % 100 === 0 ? 0 : 2,
    }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

export function formatPay(
  p: Pick<ApplicationSummary, "pay_min_cents" | "pay_max_cents" | "pay_period" | "currency">,
): string {
  const { pay_min_cents: lo, pay_max_cents: hi, pay_period: period, currency } = p;
  if (lo === null && hi === null) return "";
  const compact = period === "year";
  const suffix = period === "hour" ? "/hr" : period === "year" ? "/yr" : "";
  if (lo !== null && hi !== null && lo !== hi) {
    return `${formatMoney(lo, currency, compact)}–${formatMoney(hi, currency, compact)}${suffix}`;
  }
  return `${formatMoney((lo ?? hi)!, currency, compact)}${suffix}`;
}

export const WEEKS_PER_YEAR = 52;

export function annualCents(base: number, period: PayPeriod, hoursPerWeek: number): number {
  return period === "year" ? base : base * hoursPerWeek * WEEKS_PER_YEAR;
}

export function hourlyCents(base: number, period: PayPeriod, hoursPerWeek: number): number {
  if (period === "hour") return base;
  const hours = hoursPerWeek * WEEKS_PER_YEAR;
  return Math.floor((base + Math.floor(hours / 2)) / hours);
}

// --- board ----------------------------------------------------------------------------------

function dueKey(a: ApplicationSummary): string {
  return a.next_due ? `${a.next_due.due_on}T${a.next_due.due_time ?? "99:99"}` : "9999-12-31T99:99";
}

/** Next due item first (none last), then most recent activity. */
export function sortForBoard(apps: ApplicationSummary[]): ApplicationSummary[] {
  return [...apps].sort((a, b) => {
    const due = dueKey(a).localeCompare(dueKey(b));
    if (due !== 0) return due;
    return b.last_activity_at.localeCompare(a.last_activity_at);
  });
}

export function groupByStage(apps: ApplicationSummary[]): Record<Stage, ApplicationSummary[]> {
  const out = Object.fromEntries(STAGES.map((s) => [s, [] as ApplicationSummary[]])) as Record<
    Stage,
    ApplicationSummary[]
  >;
  for (const a of apps) out[a.stage].push(a);
  for (const s of STAGES) out[s] = sortForBoard(out[s]);
  return out;
}

export function daysInStage(stageChangedAt: string, now: Date = new Date()): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(stageChangedAt).getTime()) / 86_400_000));
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export const ATTACHMENT_TYPES: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  txt: "text/plain",
};

export function attachmentType(file: Pick<File, "name">): string | null {
  return ATTACHMENT_TYPES[file.name.toLowerCase().split(".").pop() ?? ""] ?? null;
}

// --- queries & mutations ----------------------------------------------------------------

export const trackerKeys = {
  all: ["tracker"] as const,
  applications: ["tracker", "applications"] as const,
  application: (id: string) => ["tracker", "applications", id] as const,
  contacts: ["tracker", "contacts"] as const,
  agenda: (today: string, days: number) => ["tracker", "agenda", today, days] as const,
  offers: ["tracker", "offers"] as const,
  summary: (today: string) => ["tracker", "summary", today] as const,
};

export function useApplications() {
  return useQuery({
    queryKey: trackerKeys.applications,
    queryFn: () => api<ApplicationSummary[]>("/tracker/applications"),
  });
}

export function useApplication(id: string) {
  return useQuery({
    queryKey: trackerKeys.application(id),
    queryFn: () => api<ApplicationDetail>(`/tracker/applications/${id}`),
  });
}

export function useContacts() {
  return useQuery({ queryKey: trackerKeys.contacts, queryFn: () => api<Contact[]>("/tracker/contacts") });
}

export function useAgenda(today: string, days = 60) {
  return useQuery({
    queryKey: trackerKeys.agenda(today, days),
    queryFn: () => api<AgendaItem[]>(`/tracker/agenda?today=${today}&days=${days}`),
  });
}

export function useOffers() {
  return useQuery({ queryKey: trackerKeys.offers, queryFn: () => api<Offer[]>("/tracker/offers") });
}

export function useTrackerSummary(today: string) {
  return useQuery({
    queryKey: trackerKeys.summary(today),
    queryFn: () => api<TrackerSummary>(`/tracker/summary?today=${today}`),
  });
}

export function useTrackerMutations() {
  const qc = useQueryClient();
  const onSuccess = () => qc.invalidateQueries({ queryKey: trackerKeys.all });
  const m = <TVars, TResult>(fn: (v: TVars) => Promise<TResult>) => ({ mutationFn: fn, onSuccess });
  return {
    createApplication: useMutation(
      m((body: Partial<ApplicationFields> & { title: string; stage?: Stage }) =>
        api<Application>("/tracker/applications", { method: "POST", body }),
      ),
    ),
    updateApplication: useMutation(
      m(({ id, body }: { id: string; body: Partial<ApplicationFields> }) =>
        api<Application>(`/tracker/applications/${id}`, { method: "PATCH", body }),
      ),
    ),
    moveStage: useMutation(
      m(({ id, stage, note }: { id: string; stage: Stage; note?: string }) =>
        api<Application>(`/tracker/applications/${id}/stage`, {
          method: "POST",
          body: { stage, note: note ?? "", today: localToday() },
        }),
      ),
    ),
    deleteApplication: useMutation(
      m((id: string) => api<void>(`/tracker/applications/${id}`, { method: "DELETE" })),
    ),
    addEvent: useMutation(
      m(({ applicationId, body }: { applicationId: string; body: Partial<TimelineEvent> & { kind: EventKind } }) =>
        api<TimelineEvent>(`/tracker/applications/${applicationId}/events`, { method: "POST", body }),
      ),
    ),
    deleteEvent: useMutation(m((id: string) => api<void>(`/tracker/events/${id}`, { method: "DELETE" }))),
    upload: useMutation(
      m(({ applicationId, file, eventId }: { applicationId: string; file: File; eventId?: string }) => {
        const type = attachmentType(file);
        if (!type) throw new ApiError(415, "unsupported_file", "Attach a PDF, Word, PNG, JPEG, or text file.");
        const q = eventId ? `?event_id=${eventId}` : "";
        return apiUpload<Attachment>(`/tracker/applications/${applicationId}/attachments${q}`, file, type, file.name);
      }),
    ),
    deleteAttachment: useMutation(
      m((id: string) => api<void>(`/tracker/attachments/${id}`, { method: "DELETE" })),
    ),
    createContact: useMutation(
      m((body: Partial<ContactInput> & { name: string }) => api<Contact>("/tracker/contacts", { method: "POST", body })),
    ),
    updateContact: useMutation(
      m(({ id, body }: { id: string; body: Partial<ContactInput> }) =>
        api<Contact>(`/tracker/contacts/${id}`, { method: "PATCH", body }),
      ),
    ),
    deleteContact: useMutation(m((id: string) => api<void>(`/tracker/contacts/${id}`, { method: "DELETE" }))),
    linkContact: useMutation(
      m(({ applicationId, contactId, role }: { applicationId: string; contactId: string; role: ContactRole }) =>
        api(`/tracker/applications/${applicationId}/contacts/${contactId}`, { method: "PUT", body: { role } }),
      ),
    ),
    unlinkContact: useMutation(
      m(({ applicationId, contactId }: { applicationId: string; contactId: string }) =>
        api<void>(`/tracker/applications/${applicationId}/contacts/${contactId}`, { method: "DELETE" }),
      ),
    ),
    addReferral: useMutation(
      m(({ applicationId, body }: { applicationId: string; body: Partial<Referral> & { contact_id: string } }) =>
        api<Referral>(`/tracker/applications/${applicationId}/referrals`, { method: "POST", body }),
      ),
    ),
    updateReferral: useMutation(
      m(({ id, body }: { id: string; body: Partial<Referral> }) =>
        api<Referral>(`/tracker/referrals/${id}`, { method: "PATCH", body }),
      ),
    ),
    deleteReferral: useMutation(m((id: string) => api<void>(`/tracker/referrals/${id}`, { method: "DELETE" }))),
    addReminder: useMutation(m((body: ReminderInput) => api<Reminder>("/tracker/reminders", { method: "POST", body }))),
    updateReminder: useMutation(
      m(({ id, body }: { id: string; body: Partial<ReminderInput> & { done?: boolean } }) =>
        api<Reminder>(`/tracker/reminders/${id}`, { method: "PATCH", body }),
      ),
    ),
    deleteReminder: useMutation(m((id: string) => api<void>(`/tracker/reminders/${id}`, { method: "DELETE" }))),
    saveOffer: useMutation(
      m(({ applicationId, body }: { applicationId: string; body: OfferInput }) =>
        api<Offer>(`/tracker/applications/${applicationId}/offer`, { method: "PUT", body }),
      ),
    ),
    deleteOffer: useMutation(
      m((applicationId: string) => api<void>(`/tracker/applications/${applicationId}/offer`, { method: "DELETE" })),
    ),
  };
}

export function downloadUrl(attachmentId: string): string {
  return `/api/v1/tracker/attachments/${attachmentId}/download`;
}
