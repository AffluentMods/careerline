// SPDX-License-Identifier: Apache-2.0
/** Small fetch wrapper: same-origin cookies, CSRF header, typed errors. */

export interface FieldError {
  loc: string[];
  msg: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: FieldError[];

  constructor(status: number, code: string, message: string, fields: FieldError[] = []) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

type ErrorBody = { error?: { code?: string; message?: string; fields?: FieldError[] } } | null;

const CSRF_COOKIES = ["__Host-wp_csrf", "wp_csrf"];

export function readCsrfToken(cookieString: string = document.cookie): string | undefined {
  for (const part of cookieString.split(";")) {
    const [rawName, ...rest] = part.trim().split("=");
    if (rawName && CSRF_COOKIES.includes(rawName)) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export async function api<T>(path: string, options: { method?: Method; body?: unknown } = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = readCsrfToken() ?? "";

  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, "network_error", "Can't reach the server. Check that Waypoint is running.");
  }

  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data as ErrorBody)?.error;
    throw new ApiError(res.status, err?.code ?? "unknown", err?.message ?? "Something went wrong.", err?.fields);
  }
  return data as T;
}

/** Upload a file as the raw request body (no multipart). */
export async function apiUpload<T>(
  path: string,
  file: Blob,
  contentType: string,
  filename: string,
  tooLarge = "That file is too large. The limit is 5 MB.",
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": contentType,
        "X-CSRF-Token": readCsrfToken() ?? "",
        // Header values must be ASCII; the server decodes this.
        "X-Filename": encodeURIComponent(filename),
      },
      credentials: "same-origin",
      body: file,
    });
  } catch {
    throw new ApiError(0, "network_error", "Can't reach the server. Check that Waypoint is running.");
  }
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data as ErrorBody)?.error;
    const message =
      res.status === 413 ? tooLarge : (err?.message ?? "Something went wrong.");
    throw new ApiError(res.status, err?.code ?? "unknown", message);
  }
  return data as T;
}

/** POST that answers with a file (for example an export). Returns the file and its name. */
export async function apiDownload(path: string): Promise<{ blob: Blob; filename: string }> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method: "POST",
      headers: { "X-CSRF-Token": readCsrfToken() ?? "" },
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "network_error", "Can't reach the server. Check that Waypoint is running.");
  }
  if (!res.ok) {
    const data = (await res.json().catch(() => null)) as ErrorBody;
    throw new ApiError(res.status, data?.error?.code ?? "unknown", data?.error?.message ?? "Something went wrong.");
  }
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return { blob: await res.blob(), filename: match?.[1] ?? "download" };
}

/** Hand a blob to the browser as a download. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Something went wrong.";
}

/** Map validation errors to field names, using the last path segment after "data". */
export function fieldErrors(err: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!(err instanceof ApiError)) return out;
  for (const f of err.fields) {
    const path = f.loc.slice(f.loc[1] === "data" ? 2 : 1).join(".");
    if (path && !out[path]) out[path] = f.msg.replace(/^Value error, /, "");
  }
  return out;
}

// --- API types (mirror backend/waypoint/auth/schemas.py) ---------------------------

export interface User {
  id: string;
  email: string;
  display_name: string;
  is_owner: boolean;
  mfa_enabled: boolean;
  /** False for accounts that only sign in through single sign-on. */
  has_password: boolean;
}

export interface AuthStatus {
  has_users: boolean;
  registration_open: boolean;
  session: "none" | "mfa_pending" | "active";
  password_login: boolean;
  sso: { enabled: boolean; label: string };
  demo: boolean;
}

export interface LoginResult {
  status: "ok" | "mfa_required";
  user: User | null;
}

export interface MfaSetup {
  secret: string;
  otpauth_uri: string;
}

export interface SessionInfo {
  id: string;
  created_at: string;
  last_seen_at: string;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
}
