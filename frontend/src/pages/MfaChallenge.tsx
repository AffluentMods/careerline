// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { AuthLayout } from "../components/AuthLayout";
import { Button, Field, FullPageLoader, Notice } from "../components/ui";
import { ApiError, api, errorMessage } from "../lib/api";
import { useAuthStatus, useRefreshAuth, useSignOut } from "../lib/auth";

export function MfaChallengePage() {
  const status = useAuthStatus();
  const refresh = useRefreshAuth();
  const signOut = useSignOut();
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status.isPending) return <FullPageLoader />;
  if (status.data?.session === "active") return <Navigate to="/" replace />;
  if (status.data?.session !== "mfa_pending") return <Navigate to="/login" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/mfa/verify", { method: "POST", body: { code } });
      await refresh();
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && (err.code === "mfa_locked" || err.code === "unauthenticated")) {
        await refresh();
        navigate("/login", { replace: true });
        return;
      }
      setError(errorMessage(err));
      setCode("");
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Two-step verification"
      subtitle={
        useRecovery
          ? "Enter one of the recovery codes you saved when you turned on two-factor authentication."
          : "Enter the 6-digit code from your authenticator app."
      }
      footer={
        <div className="row-between">
          <button
            type="button"
            className="link-btn"
            onClick={() => {
              setUseRecovery((v) => !v);
              setCode("");
              setError(null);
            }}
          >
            {useRecovery ? "Use authenticator code" : "Use a recovery code"}
          </button>
          <button type="button" className="link-btn" onClick={() => void signOut()}>
            Cancel
          </button>
        </div>
      }
    >
      <form className="stack" onSubmit={onSubmit} noValidate>
        {error ? <Notice>{error}</Notice> : null}
        {useRecovery ? (
          <Field
            key="recovery"
            label="Recovery code"
            name="code"
            autoComplete="off"
            spellCheck={false}
            placeholder="xxxx-xxxx-xxxx"
            required
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        ) : (
          <Field
            key="totp"
            label="Authentication code"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]*"
            maxLength={6}
            className="code-input"
            required
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          />
        )}
        <Button type="submit" busy={busy} className="btn-block" disabled={code.length < 6}>
          Verify
        </Button>
      </form>
    </AuthLayout>
  );
}
