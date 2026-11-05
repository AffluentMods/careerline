// SPDX-License-Identifier: Apache-2.0
import { useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Card, Field, Notice } from "../components/ui";
import { api, errorMessage, type MfaSetup, type SessionInfo } from "../lib/api";
import { ssoErrorMessage, useAuthStatus, useMe, useRefreshAuth } from "../lib/auth";
import { SettingsNav } from "./settings/SettingsNav";

export function SecurityPage() {
  const [params] = useSearchParams();
  return (
    <div className="page">
      <header className="page-head">
        <h1>Settings</h1>
      </header>
      <SettingsNav />
      <p className="muted">Sign-in protection and active sessions for your account.</p>
      {params.get("welcome") ? (
        <Notice tone="success">
          Your account is ready. Next, turn on two-factor authentication below.
        </Notice>
      ) : null}
      {params.get("sso_error") ? <Notice>{ssoErrorMessage(params.get("sso_error"))}</Notice> : null}
      <MfaCard />
      <SsoCard />
      <SessionsCard />
      <PasswordCard />
    </div>
  );
}

// --- Two-factor ----------------------------------------------------------------------

type MfaStep = "idle" | "password" | "scan" | "codes";

function MfaCard() {
  const me = useMe();
  const refresh = useRefreshAuth();
  const [step, setStep] = useState<MfaStep>("idle");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [mode, setMode] = useState<"none" | "regen" | "disable">("none");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!setup) return;
    let live = true;
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 200, errorCorrectionLevel: "M" })
      .then((url) => live && setQr(url))
      .catch(() => live && setQr(null));
    return () => {
      live = false;
    };
  }, [setup]);

  function reset() {
    setStep("idle");
    setMode("none");
    setPassword("");
    setCode("");
    setSetup(null);
    setQr(null);
    setError(null);
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const enabled = me.data?.mfa_enabled ?? false;

  const startSetup = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      setSetup(await api<MfaSetup>("/auth/mfa/setup", { method: "POST", body: { password } }));
      setPassword("");
      setStep("scan");
    });
  };

  const confirmSetup = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const res = await api<{ recovery_codes: string[] }>("/auth/mfa/enable", { method: "POST", body: { code } });
      setCodes(res.recovery_codes);
      setStep("codes");
      setSetup(null);
      setCode("");
    });
  };

  const submitManage = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      if (mode === "disable") {
        await api("/auth/mfa/disable", { method: "POST", body: { password, code } });
        reset();
        await refresh();
      } else {
        const res = await api<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", {
          method: "POST",
          body: { password, code },
        });
        setCodes(res.recovery_codes);
        setMode("none");
        setPassword("");
        setCode("");
        setStep("codes");
      }
    });
  };

  return (
    <Card
      title="Two-factor authentication"
      description="Ask for a code from an authenticator app (such as Aegis, 2FAS, or 1Password) each time you sign in."
      actions={
        <span className={`badge ${enabled ? "badge-ok" : "badge-warn"}`}>{enabled ? "On" : "Off"}</span>
      }
    >
      {error ? <Notice>{error}</Notice> : null}

      {step === "codes" && codes ? (
        <RecoveryCodes
          codes={codes}
          onDone={async () => {
            setCodes(null);
            reset();
            await refresh();
          }}
        />
      ) : !enabled && step === "idle" ? (
        <Button onClick={() => setStep("password")}>Turn on</Button>
      ) : !enabled && step === "password" ? (
        <form className="stack narrow" onSubmit={startSetup}>
          <Field
            label="Confirm your password"
            type="password"
            autoComplete="current-password"
            required
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <div className="row">
            <Button type="submit" busy={busy}>
              Continue
            </Button>
            <Button type="button" variant="ghost" onClick={reset}>
              Cancel
            </Button>
          </div>
        </form>
      ) : !enabled && step === "scan" && setup ? (
        <form className="mfa-setup" onSubmit={confirmSetup}>
          <div className="qr-box">
            {qr ? <img src={qr} alt="QR code to add CareerLine to your authenticator app" width={200} height={200} /> : null}
          </div>
          <div className="stack">
            <ol className="plain-steps">
              <li>Scan the QR code with your authenticator app.</li>
              <li>
                Can't scan? Enter this key instead:
                <code className="secret" aria-label="Setup key">
                  {setup.secret.match(/.{1,4}/g)?.join(" ")}
                </code>
              </li>
              <li>Enter the 6-digit code the app shows.</li>
            </ol>
            <Field
              label="Authentication code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              className="code-input"
              required
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            />
            <div className="row">
              <Button type="submit" busy={busy} disabled={code.length !== 6}>
                Turn on
              </Button>
              <Button type="button" variant="ghost" onClick={reset}>
                Cancel
              </Button>
            </div>
          </div>
        </form>
      ) : enabled && mode === "none" ? (
        <div className="row">
          <Button variant="secondary" onClick={() => setMode("regen")}>
            New recovery codes
          </Button>
          <Button variant="danger" onClick={() => setMode("disable")}>
            Turn off
          </Button>
        </div>
      ) : enabled ? (
        <form className="stack narrow" onSubmit={submitManage}>
          <p className="muted">
            {mode === "disable"
              ? "Turning this off makes your account easier to break into."
              : "Your old recovery codes will stop working."}
          </p>
          <Field
            label="Password"
            type="password"
            autoComplete="current-password"
            required
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Field
            label="Authentication or recovery code"
            autoComplete="one-time-code"
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <div className="row">
            <Button type="submit" busy={busy} variant={mode === "disable" ? "danger" : "primary"}>
              {mode === "disable" ? "Turn off" : "Generate codes"}
            </Button>
            <Button type="button" variant="ghost" onClick={reset}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </Card>
  );
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const text = `CareerLine recovery codes\nEach code works once.\n\n${codes.join("\n")}\n`;

  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "careerline-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="stack">
      <Notice tone="info">
        Save these recovery codes somewhere safe, such as a password manager. Each one works once, and this is
        the only time they're shown.
      </Notice>
      <ul className="codes" aria-label="Recovery codes">
        {codes.map((c) => (
          <li key={c}>
            <code>{c}</code>
          </li>
        ))}
      </ul>
      <div className="row">
        <Button
          variant="secondary"
          onClick={() =>
            void navigator.clipboard?.writeText(text).then(
              () => setCopied(true),
              () => setCopied(false),
            )
          }
        >
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button variant="secondary" onClick={download}>
          Download
        </Button>
        <Button onClick={onDone}>I've saved them</Button>
      </div>
    </div>
  );
}

// --- Sessions --------------------------------------------------------------------------

function describeAgent(ua: string | null): string {
  if (!ua) return "Unknown device";
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Firefox\//.test(ua)
      ? "Firefox"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : "Browser";
  const os = /Windows/.test(ua)
    ? "Windows"
    : /Android/.test(ua)
      ? "Android"
      : /iPhone|iPad/.test(ua)
        ? "iOS"
        : /Mac OS X/.test(ua)
          ? "macOS"
          : /Linux/.test(ua)
            ? "Linux"
            : "";
  return os ? `${browser} on ${os}` : browser;
}

const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function SessionsCard() {
  const qc = useQueryClient();
  const sessions = useQuery({
    queryKey: ["auth", "sessions"],
    queryFn: () => api<SessionInfo[]>("/auth/sessions"),
  });
  const [error, setError] = useState<string | null>(null);

  async function revoke(s: SessionInfo) {
    setError(null);
    try {
      await api(`/auth/sessions/${encodeURIComponent(s.id)}`, { method: "DELETE" });
      if (s.current) {
        await qc.resetQueries();
      } else {
        await qc.invalidateQueries({ queryKey: ["auth", "sessions"] });
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card title="Active sessions" description="Devices currently signed in to your account.">
      {error ? <Notice>{error}</Notice> : null}
      {sessions.isPending ? (
        <p className="muted">Loading…</p>
      ) : sessions.isError ? (
        <Notice>{errorMessage(sessions.error)}</Notice>
      ) : (
        <ul className="session-list">
          {sessions.data.map((s) => (
            <li key={s.id} className="session">
              <div>
                <strong>{describeAgent(s.user_agent)}</strong>
                {s.current ? <span className="badge badge-ok">This device</span> : null}
                <p className="muted small">
                  {s.ip ?? "Unknown IP"} · last active {fmt.format(new Date(s.last_seen_at))}
                </p>
              </div>
              <Button variant="ghost" onClick={() => void revoke(s)}>
                {s.current ? "Sign out" : "Revoke"}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// --- Password ----------------------------------------------------------------------------

interface Identity {
  id: string;
  issuer: string;
  email: string;
  created_at: string;
  last_login_at: string | null;
}

function SsoCard() {
  const status = useAuthStatus();
  const me = useMe();
  const qc = useQueryClient();
  const enabled = Boolean(status.data?.sso?.enabled);
  const ids = useQuery({
    queryKey: ["auth", "identities"],
    queryFn: () => api<Identity[]>("/auth/oidc/identities"),
    enabled,
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!enabled || !status.data) return null;
  const label = status.data.sso.label;

  async function link() {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api<{ url: string }>("/auth/oidc/start", {
        method: "POST",
        body: { purpose: "link", next: "/settings/security" },
      });
      window.location.assign(url);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  async function unlink(id: string) {
    setError(null);
    try {
      await api(`/auth/oidc/identities/${id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["auth", "identities"] });
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card title="Single sign-on" description={`Sign in with ${label} instead of your password.`}>
      <div className="stack-sm">
        {error ? <Notice>{error}</Notice> : null}
        {ids.data?.length ? (
          <ul className="files">
            {ids.data.map((i) => (
              <li key={i.id} className="file">
                <span>{i.email || "Linked account"}</span>{" "}
                <span className="muted small">
                  {i.last_login_at ? `last used ${new Date(i.last_login_at).toLocaleDateString()}` : "not used yet"}
                </span>{" "}
                <button className="link-btn small" onClick={() => void unlink(i.id)}>
                  Unlink
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">No {label} account is linked yet.</p>
        )}
        {me.data && !me.data.has_password ? (
          <p className="small muted">This account signs in only with {label}. Set a password below to have a second way in.</p>
        ) : null}
        <div>
          <Button variant="secondary" busy={busy} onClick={() => void link()}>
            Link a {label} account
          </Button>
        </div>
      </div>
    </Card>
  );
}

function PasswordCard() {
  const me = useMe();
  const hasPassword = me.data?.has_password !== false;
  const qc = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setDone(false);
    if (next !== confirm) {
      setError("New passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      await api("/auth/password", {
        method: "POST",
        body: hasPassword ? { current_password: current, new_password: next } : { new_password: next },
      });
      if (!hasPassword) await qc.invalidateQueries({ queryKey: ["auth", "me"] });
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
      await qc.invalidateQueries({ queryKey: ["auth", "sessions"] });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title={hasPassword ? "Password" : "Set a password"}
      description={
        hasPassword
          ? "Changing your password signs out every other device."
          : "Optional. Lets you sign in without single sign-on. Works within 10 minutes of signing in."
      }
    >
      <form className="stack narrow" onSubmit={onSubmit}>
        {error ? <Notice>{error}</Notice> : null}
        {done ? <Notice tone="success">{hasPassword ? "Password changed." : "Password set."}</Notice> : null}
        {hasPassword ? (
          <Field
            label="Current password"
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        ) : null}
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          hint="At least 12 characters."
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        <Field
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        <div>
          <Button type="submit" busy={busy}>
            {hasPassword ? "Change password" : "Set password"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
