// SPDX-License-Identifier: Apache-2.0
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AuthStatus, type User } from "./api";

export const statusKey = ["auth", "status"] as const;
export const meKey = ["auth", "me"] as const;

export function useAuthStatus() {
  return useQuery({ queryKey: statusKey, queryFn: () => api<AuthStatus>("/auth/status") });
}

export function useMe(enabled = true) {
  return useQuery({ queryKey: meKey, queryFn: () => api<User>("/auth/me"), enabled });
}

/** Re-read auth state after login, logout, or MFA changes. */
export function useRefreshAuth() {
  const qc = useQueryClient();
  return async () => {
    await qc.invalidateQueries({ queryKey: ["auth"] });
  };
}

export function useSignOut() {
  const qc = useQueryClient();
  return async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      // Drop all cached user data and refetch what's on screen (which redirects to sign-in).
      await qc.resetQueries();
    }
  };
}

/** Where the browser goes to start single sign-on (a full-page navigation, not fetch). */
export function ssoStartPath(next = "/"): string {
  return `/api/v1/auth/oidc/start?next=${encodeURIComponent(next)}`;
}

/** Messages for the short codes the server puts in ?sso_error=. Unknown codes get a generic one. */
export const SSO_ERRORS: Record<string, string> = {
  not_configured: "Single sign-on isn't set up on this server.",
  flow_expired: "That sign-in took too long or started in another browser. Try again.",
  state_mismatch: "That sign-in didn't match the one this browser started. Try again.",
  provider_denied: "Sign-in was cancelled at your identity provider.",
  provider_unreachable: "Your identity provider couldn't be reached. Try again in a moment.",
  provider_insecure: "The identity provider's settings aren't secure (https is required). Ask the server owner.",
  provider_mismatch: "The identity provider's settings don't match this server. Ask the server owner.",
  token_failed: "Your identity provider didn't complete the sign-in. Try again.",
  bad_token: "The sign-in couldn't be verified, so it was refused. Try again.",
  no_account:
    "No Waypoint account is linked to that sign-in. Sign in with your password and link it in Settings, or ask the owner to allow new accounts.",
  email_required: "Your identity provider didn't share an email address, which a new account needs.",
  email_taken:
    "An account with that email already exists. Sign in with your password, then link single sign-on in Settings.",
  linked_elsewhere: "That sign-in is already linked to a different Waypoint account.",
  session_changed: "You signed out or switched accounts during sign-in. Try again.",
  inactive: "That account is turned off.",
};

export function ssoErrorMessage(code: string | null): string | null {
  if (!code) return null;
  return SSO_ERRORS[code] ?? "Single sign-on didn't work. Try again.";
}
