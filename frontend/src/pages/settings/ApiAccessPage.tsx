// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Button, Card, Checkbox, Field, Notice, SelectField } from "../../components/ui";
import { ApiError, errorMessage } from "../../lib/api";
import {
  connectionSnippets,
  describeScopes,
  EXPIRY_OPTIONS,
  mcpUrl,
  parseExpiry,
  SCOPE_INFO,
  useApiKeys,
  useCreateApiKey,
  useRevokeApiKey,
  useUpdateApiKeyScopes,
  type ApiKey,
  type CreatedApiKey,
  type Scope,
} from "../../lib/apiAccess";
import { useAuthStatus, useMe } from "../../lib/auth";
import { ConfirmIdentity } from "./DataPage";
import { SettingsNav } from "./SettingsNav";

export function ApiAccessPage() {
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  return (
    <div className="page">
      <header className="page-head">
        <h1>Settings</h1>
      </header>
      <SettingsNav />
      <p className="muted">
        Let your own AI agent (Claude, ChatGPT, or any MCP client) work in your account: read your search, add career
        facts and jobs, and propose changes. Facts it adds stay drafts until you verify them, and every change it proposes
        waits for your Confirm, unless you give the key full access.
      </p>
      {created ? <NewKeyCard created={created} onDone={() => setCreated(null)} /> : <CreateKeyCard onCreated={setCreated} />}
      <KeysCard />
    </div>
  );
}

function CreateKeyCard({ onCreated }: { onCreated: (k: CreatedApiKey) => void }) {
  const me = useMe();
  const status = useAuthStatus();
  const create = useCreateApiKey();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Scope[]>(["read", "draft", "propose"]);
  const [expiry, setExpiry] = useState("90");
  const [confirming, setConfirming] = useState(false);

  async function run() {
    try {
      const k = await create.mutateAsync({ name: name.trim(), scopes, expires_in_days: parseExpiry(expiry) });
      setConfirming(false);
      setName("");
      onCreated(k);
    } catch (err) {
      if (err instanceof ApiError && err.code === "reauth_required") setConfirming(true);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void run();
  }

  const reauthError = create.error instanceof ApiError && create.error.code === "reauth_required";
  return (
    <Card title="Create an API key" description="Give each agent its own key, so you can see what it did and revoke it on its own.">
      <form className="stack-sm" onSubmit={submit}>
        <Field
          label="Name"
          placeholder="e.g. Claude on my laptop"
          maxLength={100}
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <ScopePicker scopes={scopes} onChange={setScopes} />
        <SelectField label="Expires after" options={EXPIRY_OPTIONS} value={expiry} onChange={(e) => setExpiry(e.target.value)} />
        <AccessNote />
        {confirming ? null : (
          <div>
            <Button type="submit" busy={create.isPending} disabled={!name.trim() || scopes.length === 0}>
              Create key
            </Button>
          </div>
        )}
        {create.error && !reauthError ? <Notice>{errorMessage(create.error)}</Notice> : null}
      </form>
      {/* The password check is its own form, so it must sit outside the key form: a form nested in
          another makes the browser submit (and reload) the outer one instead. */}
      {confirming && me.data && status.data ? (
        <ConfirmIdentity
          user={me.data}
          status={status.data}
          action="create an API key"
          returnTo="/settings/api"
          onConfirmed={() => void run()}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </Card>
  );
}

/** What a key may do. Full access includes the rest, so they show as included while it's on. */
function ScopePicker({ scopes, onChange }: { scopes: Scope[]; onChange: (s: Scope[]) => void }) {
  const full = scopes.includes("full");
  function toggle(scope: Scope, on: boolean) {
    if (scope === "full") onChange(on ? ["full"] : ["read", "draft", "propose"]);
    else onChange(on ? [...scopes, scope] : scopes.filter((x) => x !== scope));
  }
  return (
    <fieldset className="check-group stack-sm" aria-label="What this key may do">
      <legend>What it may do</legend>
      {SCOPE_INFO.map((s) => (
        <Checkbox
          key={s.scope}
          label={s.label}
          hint={s.hint}
          checked={full || scopes.includes(s.scope)}
          disabled={full && s.scope !== "full"}
          onChange={(e) => toggle(s.scope, e.target.checked)}
        />
      ))}
      {full ? (
        <Notice tone="info">
          Anyone with this key can change or delete anything in your job search, and nothing waits for your Confirm.
          Your nightly backups are the way back from a mistake.
        </Notice>
      ) : null}
    </fieldset>
  );
}

function AccessNote() {
  return (
    <p className="small muted">
      No key can change your password, two-factor, sign-in sessions, or API keys, or export or import all your data.
      Without full access, a key also can&apos;t edit or delete anything, change settings, or send anything outside
      this server.
    </p>
  );
}

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="link-btn small"
      onClick={async () => {
        await navigator.clipboard?.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "Copied" : label}
    </button>
  );
}

function NewKeyCard({ created, onDone }: { created: CreatedApiKey; onDone: () => void }) {
  const url = mcpUrl();
  return (
    <Card title={`Your new key: ${created.name}`}>
      <div className="stack-sm">
        <Notice tone="success">
          Copy it now. This is the only time it&apos;s shown; CareerLine keeps only a fingerprint of it.
        </Notice>
        <div>
          <div className="row-between">
            <strong className="small">API key</strong>
            <CopyButton text={created.token} />
          </div>
          <code className="secret" aria-label="API key">
            {created.token}
          </code>
        </div>
        <div>
          <div className="row-between">
            <strong className="small">Server address (MCP)</strong>
            <CopyButton text={url} />
          </div>
          <code className="secret">{url}</code>
        </div>
        <h3 className="small">Connect your AI</h3>
        {connectionSnippets(url, created.token).map((s) => (
          <div key={s.label}>
            <div className="row-between">
              <span className="small">{s.label}</span>
              <CopyButton text={s.text} />
            </div>
            <pre className="secret snippet">{s.text}</pre>
          </div>
        ))}
        <p className="small muted">
          The agent must be able to reach this address. On a home network that means an AI app running on one of your own
          devices (or reaching the server over a VPN such as Tailscale).
        </p>
        <div>
          <Button variant="secondary" onClick={onDone}>
            Done
          </Button>
        </div>
      </div>
    </Card>
  );
}

function when(iso: string | null, fallback: string): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : fallback;
}

/** Change what an existing key may do. The key itself stays the same. */
function EditAccess({ k, onDone }: { k: ApiKey; onDone: () => void }) {
  const me = useMe();
  const status = useAuthStatus();
  const update = useUpdateApiKeyScopes();
  const [scopes, setScopes] = useState<Scope[]>(k.scopes);
  const [confirming, setConfirming] = useState(false);

  async function run() {
    try {
      await update.mutateAsync({ id: k.id, scopes });
      setConfirming(false);
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.code === "reauth_required") setConfirming(true);
    }
  }

  const reauthError = update.error instanceof ApiError && update.error.code === "reauth_required";
  return (
    <div className="stack-sm">
      <form
        className="stack-sm"
        aria-label={`Access for ${k.name}`}
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <ScopePicker scopes={scopes} onChange={setScopes} />
        <p className="small muted">The key stays the same, so apps already using it keep working.</p>
        {confirming ? null : (
          <div className="row">
            <Button type="submit" busy={update.isPending} disabled={scopes.length === 0}>
              Save access
            </Button>
            <Button type="button" variant="ghost" onClick={onDone}>
              Cancel
            </Button>
          </div>
        )}
        {update.error && !reauthError ? <Notice>{errorMessage(update.error)}</Notice> : null}
      </form>
      {/* Outside the form above: nested forms make the browser submit the outer one. */}
      {confirming && me.data && status.data ? (
        <ConfirmIdentity
          user={me.data}
          status={status.data}
          action="change what this key may do"
          returnTo="/settings/api"
          onConfirmed={() => void run()}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </div>
  );
}

function KeyRow({ k }: { k: ApiKey }) {
  const revoke = useRevokeApiKey();
  const [sure, setSure] = useState(false);
  const [editing, setEditing] = useState(false);
  return (
    <li className="session">
      <div className="stack-sm">
        <div>
          <strong>{k.name}</strong> <code className="small">{k.prefix}…</code>
          {k.scopes.includes("full") ? <span className="badge badge-warn">Full access</span> : null}
          {k.expired ? <span className="badge badge-warn">Expired</span> : null}
        </div>
        <span className="muted small">
          {describeScopes(k.scopes)} · created {when(k.created_at, "")} · last used {when(k.last_used_at, "never")} ·{" "}
          {k.expires_at ? `${k.expired ? "expired" : "expires"} ${when(k.expires_at, "")}` : "never expires"}
        </span>
        {k.conversation_id ? (
          <Link to={`/assistant/${k.conversation_id}`} className="small">
            {k.pending_proposals
              ? `${k.pending_proposals} proposed change${k.pending_proposals === 1 ? "" : "s"} waiting for you`
              : "See its proposed changes"}
          </Link>
        ) : null}
        {editing ? <EditAccess k={k} onDone={() => setEditing(false)} /> : null}
        {revoke.error ? <Notice>{errorMessage(revoke.error)}</Notice> : null}
      </div>
      {editing ? null : sure ? (
        <div className="row">
          <Button variant="danger" busy={revoke.isPending} onClick={() => revoke.mutate(k.id)}>
            Revoke
          </Button>
          <Button variant="ghost" onClick={() => setSure(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <div className="row">
          <Button variant="secondary" onClick={() => setEditing(true)} aria-label={`Change access for ${k.name}`}>
            Change access
          </Button>
          <Button variant="secondary" onClick={() => setSure(true)} aria-label={`Revoke ${k.name}`}>
            Revoke
          </Button>
        </div>
      )}
    </li>
  );
}

function KeysCard() {
  const keys = useApiKeys();
  return (
    <Card title="Your API keys" description="Revoking a key stops it at once. Changes it already proposed stay in the Assistant for you to decide.">
      {keys.isError ? <Notice>{errorMessage(keys.error)}</Notice> : null}
      {keys.data && keys.data.length === 0 ? <p className="muted small">No keys yet.</p> : null}
      {keys.data?.length ? (
        <ul className="session-list" aria-label="API keys">
          {keys.data.map((k) => (
            <KeyRow key={k.id} k={k} />
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
