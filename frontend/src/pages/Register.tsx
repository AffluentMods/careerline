// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router-dom";
import { AuthLayout } from "../components/AuthLayout";
import { Button, Field, FullPageLoader, Notice } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { useAuthStatus, useRefreshAuth } from "../lib/auth";
import { SsoButton } from "./Login";

const MIN_PASSWORD = 12;

export function RegisterPage({ firstRun = false }: { firstRun?: boolean }) {
  const status = useAuthStatus();
  const refresh = useRefreshAuth();
  // Where to go once the refreshed auth status shows we are signed in.
  const [afterSignup, setAfterSignup] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status.isPending) return <FullPageLoader />;
  if (status.data?.session === "active") return <Navigate to={afterSignup ?? "/"} replace />;
  if (status.data && !status.data.registration_open) return <Navigate to="/login" replace />;
  if (firstRun && status.data?.has_users) return <Navigate to="/register" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < MIN_PASSWORD) {
      setError(`Password must be at least ${MIN_PASSWORD} characters.`);
      return;
    }
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      await api("/auth/register", {
        method: "POST",
        body: { email, password, display_name: name },
      });
      setAfterSignup(firstRun ? "/settings/security?welcome=1" : "/");
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title={firstRun ? "Set up CareerLine" : "Create your account"}
      subtitle={
        firstRun
          ? "Create the owner account for this server. Registration closes after this unless you turn it on."
          : undefined
      }
      footer={
        firstRun ? null : (
          <span>
            Already have an account? <Link to="/login">Sign in</Link>
          </span>
        )
      }
    >
      <form className="stack" onSubmit={onSubmit} noValidate>
        {error ? <Notice>{error}</Notice> : null}
        <Field
          label="Name"
          name="name"
          autoComplete="name"
          required
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
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
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD}
          maxLength={256}
          hint={`At least ${MIN_PASSWORD} characters. A short phrase is easier to remember.`}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Field
          label="Confirm password"
          type="password"
          name="confirm"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        <Button type="submit" busy={busy} className="btn-block">
          {firstRun ? "Create owner account" : "Create account"}
        </Button>
        {firstRun && status.data?.sso?.enabled ? (
          <>
            <p className="divider-text muted small">or</p>
            <SsoButton status={status.data} />
          </>
        ) : null}
      </form>
    </AuthLayout>
  );
}
