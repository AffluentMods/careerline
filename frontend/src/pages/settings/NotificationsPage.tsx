// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Button, Card, Checkbox, Field, FullPageLoader, Notice, SelectField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  browserTimeZone,
  hourLabel,
  toInput,
  useNotifyLog,
  useNotifyMutations,
  useNotifySettings,
  WEBHOOK_KINDS,
  type Channel,
  type NotifySettings,
  type WebhookKind,
} from "../../lib/notify";
import { SettingsNav } from "./SettingsNav";

export function NotificationsPage() {
  const settings = useNotifySettings();
  return (
    <div className="page">
      <header className="page-head">
        <h1>Settings</h1>
      </header>
      <SettingsNav />
      <p className="muted">
        A morning digest of what&apos;s due, and alerts when strong new job matches arrive. Sent only to the places
        you set here.
      </p>
      {settings.isPending ? <FullPageLoader /> : null}
      {settings.isError ? <Notice>{errorMessage(settings.error)}</Notice> : null}
      {settings.data ? <NotifyForm initial={settings.data} /> : null}
      <LogCard />
    </div>
  );
}

function supportedZones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
  } catch {
    return [];
  }
}

function zones(current: string): string[] {
  return Array.from(new Set([current, "UTC", ...supportedZones()]));
}

function NotifyForm({ initial }: { initial: NotifySettings }) {
  const m = useNotifyMutations();
  const fresh = !initial.digest_enabled && !initial.matches_enabled && initial.timezone === "UTC";
  const [form, setForm] = useState({
    timezone: fresh ? browserTimeZone() : initial.timezone,
    digest_enabled: initial.digest_enabled,
    digest_hour: initial.digest_hour,
    matches_enabled: initial.matches_enabled,
    match_min_score: initial.match_min_score,
    email_enabled: initial.email_enabled,
    ntfy_enabled: initial.ntfy_enabled,
    webhook_enabled: initial.webhook_enabled,
    webhook_kind: initial.webhook_kind,
  });
  const [edits, setEdits] = useState<{ email_to?: string; ntfy_url?: string; ntfy_token?: string; webhook_url?: string }>(
    {},
  );
  const [saved, setSaved] = useState(false);
  const [testResult, setTestResult] = useState<{ channel: Channel; ok: boolean; error: string | null } | null>(null);
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => {
    setForm({ ...form, [k]: v });
    setSaved(false);
  };
  const edit = (k: keyof typeof edits, v: string) => {
    setEdits({ ...edits, [k]: v });
    setSaved(false);
  };
  const kindHint = WEBHOOK_KINDS.find((k) => k.value === form.webhook_kind)?.hint;
  const noChannel = !form.email_enabled && !form.ntfy_enabled && !form.webhook_enabled;

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await m.save.mutateAsync(toInput(form, edits));
      setEdits({});
      setSaved(true);
    } catch {
      // shown below
    }
  }

  async function test(channel: Channel) {
    setTestResult(null);
    try {
      const r = await m.test.mutateAsync(channel);
      setTestResult({ channel, ...r });
    } catch (err) {
      setTestResult({ channel, ok: false, error: errorMessage(err) });
    }
  }

  const unsaved = Object.keys(edits).length > 0;

  return (
    <form className="stack" onSubmit={submit}>
      {initial.demo ? <Notice tone="info">Notifications are turned off in demo mode.</Notice> : null}
      <Card title="What to send">
        <div className="stack-sm">
          <Checkbox
            label="Daily digest"
            hint="Overdue and due-today reminders, follow-ups, and deadlines. Skipped on days with nothing due."
            checked={form.digest_enabled}
            onChange={(e) => set("digest_enabled", e.target.checked)}
          />
          {form.digest_enabled ? (
            <div className="row-fields">
              <SelectField
                label="Send at"
                value={String(form.digest_hour)}
                options={Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: hourLabel(h) }))}
                onChange={(e) => set("digest_hour", Number(e.target.value))}
              />
              <SelectField
                label="Time zone"
                value={form.timezone}
                options={zones(form.timezone).map((z) => ({ value: z, label: z.replaceAll("_", " ") }))}
                onChange={(e) => set("timezone", e.target.value)}
              />
            </div>
          ) : null}
          <Checkbox
            label="New job matches"
            hint="New postings that pass your filters and reach the score you choose. Checked every few minutes."
            checked={form.matches_enabled}
            onChange={(e) => set("matches_enabled", e.target.checked)}
          />
          {form.matches_enabled ? (
            <Field
              label="Minimum fit score"
              type="number"
              min={0}
              max={100}
              value={form.match_min_score}
              onChange={(e) => set("match_min_score", Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
            />
          ) : null}
        </div>
      </Card>

      <Card
        title="Where to send it"
        description={
          initial.private_targets_allowed
            ? undefined
            : "Addresses on a private network (like a home-lab ntfy) are blocked unless the server owner allows them."
        }
      >
        <div className="stack">
          <fieldset className="channel">
            <legend className="sr-only">Email</legend>
            <Checkbox
              label="Email"
              hint={initial.email_available ? undefined : "The server owner hasn't set up an email server (SMTP)."}
              disabled={!initial.email_available}
              checked={form.email_enabled}
              onChange={(e) => set("email_enabled", e.target.checked)}
            />
            {form.email_enabled ? (
              <div className="channel-body">
                <Field
                  label="Send to"
                  type="email"
                  autoComplete="email"
                  value={edits.email_to ?? initial.email_to}
                  onChange={(e) => edit("email_to", e.target.value)}
                />
                <TestButton channel="email" disabled={unsaved} onTest={test} busy={m.test.isPending} />
              </div>
            ) : null}
          </fieldset>

          <fieldset className="channel">
            <legend className="sr-only">ntfy</legend>
            <Checkbox
              label="ntfy push notifications"
              hint="Free phone notifications through ntfy.sh or your own ntfy server."
              checked={form.ntfy_enabled}
              onChange={(e) => set("ntfy_enabled", e.target.checked)}
            />
            {form.ntfy_enabled ? (
              <div className="channel-body">
                <Field
                  label="Topic address"
                  placeholder="https://ntfy.sh/your-private-topic"
                  hint="Pick a long, hard-to-guess topic name: anyone who knows it can read your notifications."
                  value={edits.ntfy_url ?? initial.ntfy_url}
                  onChange={(e) => edit("ntfy_url", e.target.value)}
                />
                <Field
                  label="Access token (optional)"
                  type="password"
                  autoComplete="off"
                  placeholder={initial.has_ntfy_token ? "Saved. Type to replace it." : ""}
                  value={edits.ntfy_token ?? ""}
                  onChange={(e) => edit("ntfy_token", e.target.value)}
                />
                {initial.has_ntfy_token && edits.ntfy_token === undefined ? (
                  <button type="button" className="link-btn small" onClick={() => edit("ntfy_token", "")}>
                    Remove saved token
                  </button>
                ) : null}
                <TestButton channel="ntfy" disabled={unsaved} onTest={test} busy={m.test.isPending} />
              </div>
            ) : null}
          </fieldset>

          <fieldset className="channel">
            <legend className="sr-only">Chat webhook</legend>
            <Checkbox
              label="Chat webhook"
              hint="Post to a Discord or Slack channel, or any JSON endpoint."
              checked={form.webhook_enabled}
              onChange={(e) => set("webhook_enabled", e.target.checked)}
            />
            {form.webhook_enabled ? (
              <div className="channel-body">
                <SelectField
                  label="Service"
                  value={form.webhook_kind}
                  options={WEBHOOK_KINDS.map((k) => ({ value: k.value, label: k.label }))}
                  onChange={(e) => set("webhook_kind", e.target.value as WebhookKind)}
                />
                <Field
                  label="Webhook address"
                  type="password"
                  autoComplete="off"
                  hint={kindHint}
                  placeholder={initial.webhook_hint ? `Saved (${initial.webhook_hint}). Paste to replace it.` : ""}
                  value={edits.webhook_url ?? ""}
                  onChange={(e) => edit("webhook_url", e.target.value)}
                />
                <TestButton channel="webhook" disabled={unsaved} onTest={test} busy={m.test.isPending} />
              </div>
            ) : null}
          </fieldset>
          {testResult ? (
            testResult.ok ? (
              <Notice tone="success">Test sent by {testResult.channel}. Check that it arrived.</Notice>
            ) : (
              <Notice>Test by {testResult.channel} failed: {testResult.error}</Notice>
            )
          ) : null}
        </div>
      </Card>

      {m.save.error ? <Notice>{errorMessage(m.save.error)}</Notice> : null}
      {saved ? <Notice tone="success">Saved.</Notice> : null}
      {(form.digest_enabled || form.matches_enabled) && noChannel ? (
        <Notice tone="info">Choose at least one place to send notifications.</Notice>
      ) : null}
      <div>
        <Button type="submit" busy={m.save.isPending} disabled={initial.demo && !noChannel}>
          Save notification settings
        </Button>
      </div>
    </form>
  );
}

function TestButton({
  channel,
  disabled,
  busy,
  onTest,
}: {
  channel: Channel;
  disabled: boolean;
  busy: boolean;
  onTest: (c: Channel) => void;
}) {
  return (
    <div className="row">
      <Button type="button" variant="secondary" busy={busy} disabled={disabled} onClick={() => onTest(channel)}>
        Send a test
      </Button>
      {disabled ? <span className="muted small">Save first, then test.</span> : null}
    </div>
  );
}

function LogCard() {
  const log = useNotifyLog();
  if (!log.data?.length) return null;
  return (
    <Card title="Recently sent">
      <ul className="files">
        {log.data.slice(0, 15).map((e) => (
          <li key={e.id} className="file">
            <span className={`badge ${e.ok ? "badge-ok" : "badge-warn"}`}>{e.ok ? "Sent" : "Failed"}</span>{" "}
            <span>{e.title}</span>{" "}
            <span className="muted small">
              by {e.channel} · {new Date(e.created_at).toLocaleString()}
              {e.detail ? ` · ${e.detail}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
