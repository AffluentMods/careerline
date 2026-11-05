// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Button, Card, Checkbox, Field, FullPageLoader, Notice, SelectField } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import {
  FEATURE_LABEL,
  formatMicros,
  useAiMutations,
  useAiSettings,
  useAiUsage,
  type AiProvider,
  type AiSettings,
} from "../../lib/ai";
import { centsToInput, parseMoney } from "../../lib/tracker";
import { SettingsNav } from "./SettingsNav";

const PROVIDERS: { value: AiProvider; label: string; hint: string }[] = [
  { value: "none", label: "Off", hint: "Keyword scoring and templates only. Nothing is sent anywhere." },
  { value: "anthropic", label: "Anthropic (Claude)", hint: "Uses your own Anthropic API key." },
  { value: "openai", label: "OpenAI-compatible API", hint: "Any service with an OpenAI-style /chat/completions endpoint." },
  { value: "ollama", label: "Ollama (local models)", hint: "Runs models on your own machine. Free and private." },
];

export function AiSettingsPage() {
  const settings = useAiSettings();
  return (
    <div className="page">
      <header className="page-head">
        <h1>Settings</h1>
      </header>
      <SettingsNav />
      {settings.isPending ? <FullPageLoader /> : null}
      {settings.isError ? <Notice>{errorMessage(settings.error)}</Notice> : null}
      {settings.data ? <AiForm initial={settings.data} /> : null}
      <UsageCard />
    </div>
  );
}

function AiForm({ initial }: { initial: AiSettings }) {
  const m = useAiMutations();
  const [provider, setProvider] = useState<AiProvider>(initial.provider);
  const [baseUrl, setBaseUrl] = useState(initial.base_url);
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState({
    scoring: initial.model_scoring,
    writing: initial.model_writing,
    chat: initial.model_chat,
  });
  const [redact, setRedact] = useState(initial.redact_pii);
  const [autoScore, setAutoScore] = useState(initial.auto_score);
  const [budget, setBudget] = useState(centsToInput(initial.monthly_budget_cents));
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const defaults = initial.defaults[provider] ?? { scoring: "", writing: "", chat: "" };
  const needsKey = provider === "anthropic" || provider === "openai";
  const needsUrl = provider === "openai" || provider === "ollama";

  async function submit(e: FormEvent) {
    e.preventDefault();
    const budgetCents = parseMoney(budget);
    if (Number.isNaN(budgetCents)) return setError("The budget must be a dollar amount, like 5 or 12.50.");
    setError(null);
    setSaved(false);
    try {
      await m.save.mutateAsync({
        provider,
        base_url: needsUrl ? baseUrl.trim() : "",
        api_key: apiKey ? apiKey : provider === "none" ? null : undefined,
        model_scoring: models.scoring.trim(),
        model_writing: models.writing.trim(),
        model_chat: models.chat.trim(),
        redact_pii: redact,
        auto_score: autoScore,
        monthly_budget_cents: budgetCents,
      });
      setApiKey("");
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <Card
        title="AI provider"
        description="AI is optional. Every AI feature uses only your verified vault facts, and job postings are always treated as untrusted data."
      >
        <SelectField
          label="Provider"
          value={provider}
          options={PROVIDERS.map((p) => ({ value: p.value, label: p.label }))}
          hint={PROVIDERS.find((p) => p.value === provider)?.hint}
          onChange={(e) => {
            const next = e.target.value as AiProvider;
            setProvider(next);
            setBaseUrl(next === provider ? baseUrl : (initial.default_base_urls[next] ?? ""));
          }}
        />
        {needsUrl ? (
          <Field
            label="API base URL"
            type="url"
            required
            value={baseUrl}
            placeholder={initial.default_base_urls[provider]}
            hint={
              initial.private_endpoints_allowed
                ? "Local and private-network addresses are allowed for you."
                : "Only public internet addresses are allowed on this server."
            }
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        ) : null}
        {needsKey ? (
          <Field
            label="API key"
            type="password"
            autoComplete="off"
            value={apiKey}
            required={provider === "anthropic" && !(initial.provider === "anthropic" && initial.has_api_key)}
            hint={
              initial.has_api_key && initial.provider === provider
                ? "A key is saved (encrypted). Leave blank to keep it."
                : "Stored encrypted and never shown again."
            }
            onChange={(e) => setApiKey(e.target.value)}
          />
        ) : null}
        {provider !== "none" ? (
          <div className="form-grid">
            <Field
              label="Model for scoring"
              value={models.scoring}
              placeholder={defaults.scoring}
              hint="A fast, inexpensive model"
              required={provider !== "anthropic"}
              onChange={(e) => setModels({ ...models, scoring: e.target.value })}
            />
            <Field
              label="Model for writing"
              value={models.writing}
              placeholder={defaults.writing}
              hint="Resumes and cover letters"
              required={provider !== "anthropic"}
              onChange={(e) => setModels({ ...models, writing: e.target.value })}
            />
            <Field
              label="Model for chat"
              value={models.chat}
              placeholder={defaults.chat}
              hint="Assistant and mock interviews"
              required={provider !== "anthropic"}
              onChange={(e) => setModels({ ...models, chat: e.target.value })}
            />
            <Field
              label="Monthly budget in USD (optional)"
              inputMode="decimal"
              value={budget}
              hint="AI calls stop when estimated spend reaches this"
              onChange={(e) => setBudget(e.target.value)}
            />
          </div>
        ) : null}
        {provider !== "none" ? (
          <div className="stack-sm">
            <Checkbox
              label="Remove emails, phone numbers, and street addresses before sending text to a hosted provider"
              checked={redact}
              onChange={(e) => setRedact(e.target.checked)}
            />
            <Checkbox
              label="Score new matching jobs with AI automatically"
              hint="Other jobs get a free keyword score. You can always rescore one job by hand."
              checked={autoScore}
              onChange={(e) => setAutoScore(e.target.checked)}
            />
          </div>
        ) : null}
      </Card>
      {error ? <Notice>{error}</Notice> : null}
      {saved ? <Notice tone="success">AI settings saved.</Notice> : null}
      {m.test.data ? (
        <Notice tone="success">
          Connected to {m.test.data.model} in {(m.test.data.duration_ms / 1000).toFixed(1)}s.
        </Notice>
      ) : null}
      {m.test.error ? <Notice>{errorMessage(m.test.error)}</Notice> : null}
      <div className="row">
        <Button type="submit" busy={m.save.isPending}>
          Save AI settings
        </Button>
        {initial.provider !== "none" ? (
          <Button type="button" variant="secondary" busy={m.test.isPending} onClick={() => m.test.mutate()}>
            Test connection
          </Button>
        ) : null}
      </div>
    </form>
  );
}

function UsageCard() {
  const usage = useAiUsage();
  if (!usage.data || usage.data.month_calls === 0) return null;
  const u = usage.data;
  return (
    <Card
      title="Usage this month"
      description={`${u.month_calls} calls, about ${formatMicros(u.month_cost_micros)}${
        u.unknown_cost_calls ? ` (plus ${u.unknown_cost_calls} calls with no known price)` : ""
      }. Estimates from published prices; your provider's bill is authoritative.`}
    >
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Feature</th>
              <th>Calls</th>
              <th>Tokens in / out</th>
              <th>Est. cost</th>
            </tr>
          </thead>
          <tbody>
            {u.by_feature.map((f) => (
              <tr key={f.feature}>
                <td data-label="Feature">{FEATURE_LABEL[f.feature] ?? f.feature}</td>
                <td data-label="Calls">{f.calls}</td>
                <td data-label="Tokens">
                  {f.input_tokens.toLocaleString()} / {f.output_tokens.toLocaleString()}
                </td>
                <td data-label="Est. cost">{formatMicros(f.cost_micros)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {u.recent.some((r) => r.status !== "ok") ? (
        <details>
          <summary className="small">Recent problems</summary>
          <ul className="files">
            {u.recent
              .filter((r) => r.status !== "ok")
              .slice(0, 8)
              .map((r, i) => (
                <li key={i} className="small">
                  {new Date(r.created_at).toLocaleString()} · {FEATURE_LABEL[r.feature] ?? r.feature} · {r.status}
                  {r.error ? ` — ${r.error}` : ""}
                </li>
              ))}
          </ul>
        </details>
      ) : null}
    </Card>
  );
}
