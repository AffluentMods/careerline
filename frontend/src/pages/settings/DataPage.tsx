// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Button, Card, Field, Notice } from "../../components/ui";
import { ApiError, errorMessage, type AuthStatus, type User } from "../../lib/api";
import { useAuthStatus, useMe } from "../../lib/auth";
import {
  downloadExport,
  formatBytes,
  IMPORT_LIMIT_MB,
  importSummary,
  reauthWithPassword,
  reauthWithSso,
  useBackupNow,
  useBackups,
  useImport,
  type ImportResult,
} from "../../lib/data";
import { SettingsNav } from "./SettingsNav";

export function DataPage() {
  const me = useMe();
  const status = useAuthStatus();
  return (
    <div className="page">
      <header className="page-head">
        <h1>Settings</h1>
      </header>
      <SettingsNav />
      <p className="muted">Take all your data with you, bring it to another CareerLine server, and keep backups.</p>
      {me.data && status.data ? (
        <>
          <ExportCard user={me.data} status={status.data} />
          <ImportCard user={me.data} status={status.data} />
          {me.data.is_owner ? <BackupsCard /> : null}
        </>
      ) : null}
    </div>
  );
}

/** Ask for the password (or a fresh single sign-on) before a sensitive action. */
export function ConfirmIdentity({
  user,
  status,
  action,
  onConfirmed,
  onCancel,
  returnTo = "/settings/data",
}: {
  user: User;
  status: AuthStatus;
  action: string;
  onConfirmed: () => void;
  onCancel: () => void;
  /** Where single sign-on brings the user back to. */
  returnTo?: string;
}) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await reauthWithPassword(password);
      setPassword("");
      onConfirmed();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!user.has_password) {
    return (
      <div className="confirm-box" role="group" aria-label="Confirm it's you">
        <p>
          To {action}, confirm it&apos;s you by signing in again with {status.sso.label}. You&apos;ll come back here.
        </p>
        {error ? <Notice>{error}</Notice> : null}
        <div className="row">
          <Button
            busy={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await reauthWithSso(returnTo);
              } catch (err) {
                setError(errorMessage(err));
                setBusy(false);
              }
            }}
          >
            Continue with {status.sso.label}
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    );
  }
  return (
    <form className="confirm-box stack-sm" onSubmit={submit} aria-label="Confirm it's you">
      <p>To {action}, enter your password again.</p>
      {error ? <Notice>{error}</Notice> : null}
      <Field
        label="Password"
        type="password"
        autoComplete="current-password"
        required
        autoFocus
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <div className="row">
        <Button type="submit" busy={busy} disabled={!password}>
          Confirm
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function needsReauth(err: unknown): boolean {
  return err instanceof ApiError && err.code === "reauth_required";
}

function ExportCard({ user, status }: { user: User; status: AuthStatus }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function run() {
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await downloadExport();
      setConfirming(false);
      setDone(true);
    } catch (err) {
      if (needsReauth(err)) setConfirming(true);
      else setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Export everything"
      description="A .zip with all your data as JSON, plus your uploaded files. Contact details and files are decrypted, so keep the export somewhere safe."
    >
      <div className="stack-sm">
        <p className="small muted">
          Not included: your password and two-factor settings, AI provider keys, job source keys, and ntfy or webhook
          addresses. You&apos;d enter those again after importing.
        </p>
        {confirming ? (
          <ConfirmIdentity
            user={user}
            status={status}
            action="export your data"
            onConfirmed={() => void run()}
            onCancel={() => setConfirming(false)}
          />
        ) : (
          <div>
            <Button busy={busy} onClick={() => void run()}>
              Download export
            </Button>
          </div>
        )}
        {error ? <Notice>{error}</Notice> : null}
        {done ? <Notice tone="success">Your export is downloading.</Notice> : null}
      </div>
    </Card>
  );
}

function ImportCard({ user, status }: { user: User; status: AuthStatus }) {
  const imp = useImport();
  const [file, setFile] = useState<File | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);

  async function run() {
    if (!file) return;
    setResult(null);
    try {
      setResult(await imp.mutateAsync(file));
      setConfirming(false);
      setFile(null);
    } catch (err) {
      if (needsReauth(err)) setConfirming(true);
    }
  }

  return (
    <Card
      title="Import from a CareerLine export"
      description="Moves data from another CareerLine server, or restores your own export. It goes into this account, which must be empty."
    >
      <div className="stack-sm">
        <label className="file-pick">
          <span className="btn btn-secondary">Choose export (.zip)</span>
          <input
            type="file"
            accept=".zip,application/zip"
            className="sr-only"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setResult(null);
              imp.reset();
            }}
          />
          <span className="muted small">{file ? `${file.name} (${formatBytes(file.size)})` : `Up to ${IMPORT_LIMIT_MB} MB`}</span>
        </label>
        {file && !confirming ? (
          <div className="confirm-box">
            <p>
              Import <strong>{file.name}</strong> into this account? Everything in it is added as new records. Job
              sources come in turned off.
            </p>
            <div className="row">
              <Button busy={imp.isPending} onClick={() => void run()}>
                Import
              </Button>
              <Button variant="ghost" onClick={() => setFile(null)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : null}
        {confirming ? (
          <ConfirmIdentity
            user={user}
            status={status}
            action="import data"
            onConfirmed={() => void run()}
            onCancel={() => setConfirming(false)}
          />
        ) : null}
        {imp.error && !needsReauth(imp.error) ? <Notice>{errorMessage(imp.error)}</Notice> : null}
        {result ? (
          <Notice tone="success">
            Imported {importSummary(result)}.
            {result.notes.length ? (
              <ul className="reason-list">
                {result.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            ) : null}
          </Notice>
        ) : null}
      </div>
    </Card>
  );
}

function BackupsCard() {
  const backups = useBackups(true);
  const now = useBackupNow();
  const b = backups.data;
  return (
    <Card
      title="Server backups"
      description="Encrypted copies of the whole database, for every account. Only you (the owner) see this."
    >
      {backups.isError ? <Notice>{errorMessage(backups.error)}</Notice> : null}
      {b && !b.enabled ? (
        <p className="small">
          Backups are off. Set <code>CAREERLINE_BACKUP_DIR</code> to a folder (ideally on another disk) and restart.
          Running with <code>start.ps1</code> turns them on automatically.
        </p>
      ) : null}
      {b?.enabled ? (
        <div className="stack-sm">
          <p className="small">
            Every {b.interval_hours} hours to <code>{b.directory}</code>, keeping the newest {b.keep}. Encrypted with a
            key from <code>{b.key_source}</code>: keep your <code>.env</code> file backed up separately, or the
            backups can&apos;t be opened.
          </p>
          {b.last_job_status === "failed" ? <Notice>The last backup failed: {b.last_job_error}</Notice> : null}
          {b.files.length ? (
            <ul className="files">
              {b.files.map((f) => (
                <li key={f.name} className="file">
                  <code>{f.name}</code>{" "}
                  <span className="muted small">
                    {formatBytes(f.size)} · {new Date(f.created_at).toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">No backups yet.</p>
          )}
          <div className="row">
            <Button
              variant="secondary"
              busy={now.isPending || b.last_job_status === "queued" || b.last_job_status === "running"}
              onClick={() => now.mutate()}
            >
              Back up now
            </Button>
          </div>
          {now.error ? <Notice>{errorMessage(now.error)}</Notice> : null}
          <p className="muted small">
            To restore, see &ldquo;Backups and restore&rdquo; in the docs (<code>python -m careerline.data restore</code>).
          </p>
        </div>
      ) : null}
    </Card>
  );
}
