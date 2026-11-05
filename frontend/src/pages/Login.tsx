// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { AuthLayout } from "../components/AuthLayout";
import { Button, Field, FullPageLoader, Notice } from "../components/ui";
import { api, errorMessage, type AuthStatus, type LoginResult } from "../lib/api";
import { ssoErrorMessage, ssoStartPath, useAuthStatus, useRefreshAuth } from "../lib/auth";

/** "Sign in with …" button. A plain link: single sign-on is a full-page redirect. */
export function SsoButton({ status }: { status: AuthStatus }) {
  if (!status.sso?.enabled) return null;
  return (
    <a className="btn btn-secondary btn-block" href={ssoStartPath("/")}>
      Sign in with {status.sso.label}
    </a>
  );
}

export function LoginPage() {
  const status = useAuthStatus();
  const refresh = useRefreshAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status.isPending) return <FullPageLoader />;
  const s = status.data;
  if (s?.session === "active") return <Navigate to="/" replace />;
  if (s?.session === "mfa_pending") return <Navigate to="/login/verify" replace />;
  // First run: create the owner account, unless sign-in is single sign-on only.
  if (s && !s.has_users && s.password_login !== false) return <Navigate to="/setup" replace />;
  const ssoError = ssoErrorMessage(params.get("sso_error"));

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api<LoginResult>("/auth/login", { method: "POST", body: { email, password } });
      await refresh();
      navigate(res.status === "mfa_required" ? "/login/verify" : "/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Sign in"
      subtitle={s?.demo ? "This is a demo. Everything in it is made up." : "Welcome back."}
      footer={
        s?.registration_open ? (
          <span>
            New here? <Link to="/register">Create an account</Link>
          </span>
        ) : null
      }
    >
      <div className="stack">
        {ssoError ? <Notice>{ssoError}</Notice> : null}
        {s?.demo ? (
          <Notice tone="info">
            Demo account: <strong>demo@example.com</strong> with password <strong>explore-waypoint</strong>
          </Notice>
        ) : null}
        {s ? <SsoButton status={s} /> : null}
        {s?.sso?.enabled && s.password_login !== false ? <p className="divider-text muted small">or</p> : null}
        {s?.password_login !== false ? (
          <form className="stack" onSubmit={onSubmit} noValidate>
            {error ? <Notice>{error}</Notice> : null}
            <Field
              label="Email"
              type="email"
              name="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <Field
              label="Password"
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <Button type="submit" busy={busy} className="btn-block">
              Sign in
            </Button>
          </form>
        ) : null}
      </div>
    </AuthLayout>
  );
}
